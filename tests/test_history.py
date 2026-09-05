import unittest
from app.history import History


def state(pitch, selected=0):
    return {'document': {'notes': [{'pitch': pitch, 'bend': [[0, 2]]}]},
            'selected': [selected]}


class HistoryTests(unittest.TestCase):
    def test_round_trip_and_snapshot_isolation(self):
        h = History()
        before, after = state(60), state(62, 1)
        h.record(before, after, 'transpose')
        after['document']['notes'][0]['bend'][0][1] = 99
        restored, label = h.step()
        self.assertEqual(restored, before)
        self.assertEqual(label, 'transpose')
        restored['document']['notes'][0]['pitch'] = 90
        self.assertEqual(h.step(True)[0], state(62, 1))
        self.assertEqual(h.step()[0], before)

    def test_noop_preserves_redo_but_new_edit_clears_it(self):
        h = History()
        h.record(state(60), state(62), 'pitch')
        h.step()
        self.assertFalse(h.record(state(60), state(60, 1), 'selection'))
        self.assertTrue(h.redo)
        h.record(state(60), state(64), 'pitch')
        self.assertIsNone(h.step(True))

    def test_saved_document_survives_branch_and_limit(self):
        h = History(state(60)['document'], limit=2)
        for pitch in range(60, 63):
            h.record(state(pitch), state(pitch + 1), 'pitch')
        self.assertEqual(len(h.undo), 2)
        self.assertEqual(h.step()[0], state(62))
        self.assertEqual(h.step()[0], state(61))
        self.assertIsNone(h.step())
        self.assertEqual(h.saved, state(60)['document'])


class EditorHistoryTests(unittest.TestCase):
    def test_note_edit_restores_selection_and_saved_status(self):
        from types import SimpleNamespace, MethodType
        from unittest.mock import Mock
        from app import studio, project
        frame = SimpleNamespace(
            bpm=120, sig=(4, 4), consonant_pct=100, song_voice={},
            song_reverb=(0, 0), anticipate=True, current=0, dirty=False,
            tracks=[project.Track(name='Lead', notes=[studio.Note(pitch=60)])],
            selected=lambda: [0], list=object(), refresh_row=Mock(),
            preview_note=Mock(), announce_note=Mock(), stop_audio=Mock(),
            _preview_timer=None, say=Mock(), sync=Mock(), sync_tracks=Mock(),
            select_only=Mock())
        frame.notes = frame.tracks[0].notes
        for name in ('_snapshot', '_history_status', '_restore_history'):
            setattr(frame, name, MethodType(getattr(studio.Frame, name), frame))
        def touch(dirty=True):
            frame.dirty = dirty
        frame.touch = touch
        frame._history = History(frame._snapshot()['document'])
        studio.Frame.nudge_pitch(frame, 1)
        self.assertTrue(frame.dirty)
        frame._restore_history()
        self.assertEqual(frame.tracks[0].notes[0].pitch, 60)
        self.assertFalse(frame.dirty)
        frame.select_only.assert_called_with([0])
        frame._restore_history(redo=True)
        self.assertEqual(frame.tracks[0].notes[0].pitch, 61)
        self.assertTrue(frame.dirty)
