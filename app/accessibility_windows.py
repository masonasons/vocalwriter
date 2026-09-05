"""UI Automation notifications for native Windows note lists."""
import ctypes
from functools import lru_cache
from uuid import UUID

#: Why the last announce() gave up, or '' if it did not. A bare False says
#: nothing about which of the five steps failed, which is no use when the only
#: place it fails is a machine you are not sitting at.
LAST_REASON = ''

#: Reasons that mean the session has no usable UI Automation, rather than that
#: the code is wrong. A build agent has no interactive desktop, so there is no
#: accessible object to wrap and no provider to make from it.
UNAVAILABLE = ('libraries', 'accessible', 'provider')


@lru_cache(maxsize=1)
def _libraries():
    ptr = ctypes.c_void_p
    hresult = ctypes.c_int32
    uia = ctypes.WinDLL('UIAutomationCore.dll')
    oleacc = ctypes.WinDLL('oleacc.dll')
    oleaut = ctypes.WinDLL('oleaut32.dll')
    oleacc.AccessibleObjectFromWindow.argtypes = [
        ptr, ctypes.c_uint32, ptr, ctypes.POINTER(ptr)]
    oleacc.AccessibleObjectFromWindow.restype = hresult
    uia.UiaProviderFromIAccessible.argtypes = [
        ptr, ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(ptr)]
    uia.UiaProviderFromIAccessible.restype = hresult
    uia.UiaRaiseNotificationEvent.argtypes = [
        ptr, ctypes.c_int, ctypes.c_int, ptr, ptr]
    uia.UiaRaiseNotificationEvent.restype = hresult
    oleaut.SysAllocString.argtypes = [ctypes.c_wchar_p]
    oleaut.SysAllocString.restype = ptr
    oleaut.SysFreeString.argtypes = [ptr]
    oleaut.SysFreeString.restype = None
    return uia, oleacc, oleaut


def _release(interface):
    """Release our COM reference (IUnknown's third vtable entry)."""
    if interface:
        vtable = ctypes.cast(
            interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(vtable[2])
        release(interface)


def announce(control, text):
    """Send only the new value, keeping the most recent repeated-key result.

    wx already initializes COM on its UI thread. Wrap the list's existing
    MSAA object in a complete UIA provider; a bare HWND host provider cannot
    raise events. All references acquired here belong to this call.

    False is not an error: the caller keeps its MSAA fallback. `LAST_REASON`
    says which step declined, so a failure on a machine one cannot attach a
    debugger to still names itself.
    """
    global LAST_REASON
    LAST_REASON = ''
    try:
        uia, oleacc, oleaut = _libraries()
    except (AttributeError, OSError) as exc:
        LAST_REASON = 'libraries: %s' % (exc,)
        return False  # Older Windows: caller retains its MSAA fallback.
    accessible = ctypes.c_void_p()
    provider = ctypes.c_void_p()
    message = activity = None
    iid = (ctypes.c_ubyte * 16).from_buffer_copy(
        UUID('618736e0-3c3d-11cf-810c-00aa00389b71').bytes_le)  # IAccessible
    try:
        result = oleacc.AccessibleObjectFromWindow(
            control.GetHandle(), 0xFFFFFFFC, ctypes.byref(iid),
            ctypes.byref(accessible))  # OBJID_CLIENT
        if result < 0 or not accessible:
            LAST_REASON = ('accessible: AccessibleObjectFromWindow 0x%08x'
                           % (result & 0xFFFFFFFF))
            return False
        result = uia.UiaProviderFromIAccessible(
            accessible, 0, 0, ctypes.byref(provider))  # CHILDID_SELF, default
        if result < 0 or not provider:
            LAST_REASON = ('provider: UiaProviderFromIAccessible 0x%08x'
                           % (result & 0xFFFFFFFF))
            return False
        message = oleaut.SysAllocString(text)
        activity = oleaut.SysAllocString('VocalWriter.NoteAdjustment')
        if not message or not activity:
            LAST_REASON = 'strings: SysAllocString returned null'
            return False
        # NotificationKind_Other, NotificationProcessing_ImportantMostRecent.
        # A held arrow key supersedes earlier values rather than queuing them.
        result = uia.UiaRaiseNotificationEvent(provider, 4, 1, message, activity)
        if result < 0:
            LAST_REASON = ('notify: UiaRaiseNotificationEvent 0x%08x'
                           % (result & 0xFFFFFFFF))
        return result >= 0
    finally:
        if activity:
            oleaut.SysFreeString(activity)
        if message:
            oleaut.SysFreeString(message)
        _release(provider)
        _release(accessible)
