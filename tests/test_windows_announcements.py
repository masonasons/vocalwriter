import ctypes
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import accessibility, accessibility_windows as windows, studio


class WindowsAnnouncements(unittest.TestCase):
    def setUp(self):
        self.uia, self.oleacc, self.oleaut = Mock(), Mock(), Mock()
        self.control = Mock()
        self.control.GetHandle.return_value = 0x123456789

        def accessible(hwnd, object_id, iid, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 101
            return 0

        def provider(accessible, child, flags, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 202
            return 0

        self.oleacc.AccessibleObjectFromWindow.side_effect = accessible
        self.uia.UiaProviderFromIAccessible.side_effect = provider
        self.uia.UiaRaiseNotificationEvent.return_value = 0
        self.oleaut.SysAllocString.side_effect = [303, 404]
        libraries = patch.object(windows, '_libraries', return_value=(
            self.uia, self.oleacc, self.oleaut))
        release = patch.object(windows, '_release')
        libraries.start()
        self.release = release.start()
        self.addCleanup(libraries.stop)
        self.addCleanup(release.stop)

    def test_windows_dispatch_sends_only_value_and_releases_resources(self):
        with patch.object(accessibility.sys, 'platform', 'win32'):
            self.assertTrue(accessibility.announce(self.control, 'C4'))
        args = self.uia.UiaRaiseNotificationEvent.call_args.args
        self.assertEqual(args[0].value, 202)
        self.assertEqual(args[1:], (4, 1, 303, 404))
        self.assertEqual(self.oleaut.SysAllocString.call_args_list[0].args, ('C4',))
        self.assertEqual(
            [call.args[0] for call in self.oleaut.SysFreeString.call_args_list],
            [404, 303])
        self.assertEqual([call.args[0].value for call in self.release.call_args_list],
                         [202, 101])

    def test_provider_failure_releases_accessible_and_skips_notification(self):
        self.uia.UiaProviderFromIAccessible.side_effect = None
        self.uia.UiaProviderFromIAccessible.return_value = -2147467259
        self.assertFalse(windows.announce(self.control, 'quarter note'))
        self.uia.UiaRaiseNotificationEvent.assert_not_called()
        self.assertEqual(self.release.call_args.args[0].value, 101)

    def test_failed_notification_falls_back_to_row(self):
        self.uia.UiaRaiseNotificationEvent.return_value = -2147467259
        frame = SimpleNamespace(say=Mock())
        with patch.object(accessibility.sys, 'platform', 'win32'), \
                patch.object(studio, 'reannounce') as fallback:
            studio.Frame.announce_note(frame, 'one bar', self.control, 0)
        fallback.assert_called_once_with(self.control, 0)
        self.assertEqual(self.oleaut.SysFreeString.call_count, 2)

    def test_missing_api_uses_fallback(self):
        with patch.object(windows, '_libraries', side_effect=AttributeError):
            self.assertFalse(windows.announce(self.control, 'C4'))


@unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows UI Automation')
class NativeWindowsAnnouncement(unittest.TestCase):
    def test_notification_from_real_note_list(self):
        """The real thing, on a real list, where there is a desktop for it.

        UI Automation needs an interactive desktop: a build agent has none, so
        there is no accessible object to wrap and no provider to make from it,
        and the test skips rather than failing the build for it. Production
        code is unaffected either way -- announce() returning False is the
        signal to use the MSAA fallback, which is what happens there.

        Anything past that point is a real fault and still fails, naming the
        call and its HRESULT instead of only "False is not true".
        """
        import wx
        from app.lists import ReportList
        app = wx.App.Get() or wx.App(False)
        frame = wx.Frame(None, title='Accessibility test')
        try:
            notes = ReportList(frame)
            notes.InsertColumn(0, 'Pitch')
            notes.InsertItem(0, 'C4')
            frame.Show()
            app.Yield()
            for text in ('C4', 'one bar one beat'):
                if windows.announce(notes, text):
                    continue
                reason = windows.LAST_REASON
                if reason.startswith(windows.UNAVAILABLE):
                    self.skipTest('no UI Automation in this session: %s' % reason)
                self.fail('announce(%r) failed -- %s' % (text, reason or 'no reason'))
        finally:
            frame.Destroy()
            app.Yield()


if __name__ == '__main__':
    unittest.main()
