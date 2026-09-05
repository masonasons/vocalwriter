"""Run with: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import studio


class NoteAnnouncements(unittest.TestCase):
    def test_lengths_follow_time_signature(self):
        cases = [
            (.125, (4, 4), 'thirty-second note'),
            (.5, (4, 4), 'eighth note'),
            (.75, (4, 4), 'dotted eighth note'),
            (1, (4, 4), 'quarter note'),
            (4, (4, 4), 'one bar'),
            (5, (4, 4), 'one bar one beat'),
            (8, (4, 4), '2 bars'),
            (3, (3, 4), 'one bar'),
            (4, (3, 4), 'one bar one beat'),
            (3.5, (6, 8), 'one bar one beat'),
            (1.25, (4, 4), '1.25 beats'),
        ]
        for beats, sig, expected in cases:
            with self.subTest(beats=beats, sig=sig):
                self.assertEqual(studio.spoken_length(beats, sig), expected)

    def editor(self, pitch=59, beats=.75):
        return SimpleNamespace(
            notes=[studio.Note(pitch=pitch, beats=beats)],
            selected=lambda: [0], signature=lambda: (4, 4),
            list=object(), touch=Mock(), refresh_row=Mock(),
            sync_lengths=Mock(), preview_note=Mock(), announce_note=Mock())

    def test_pitch_shortcut_announces_result(self):
        frame = self.editor()
        studio.Frame.nudge_pitch(frame, 1)
        self.assertEqual(frame.notes[0].pitch, 60)
        frame.announce_note.assert_called_once_with('C4', frame.list, 0)

    def test_length_shortcut_announces_result(self):
        frame = self.editor()
        studio.Frame.nudge_length(frame, True)
        self.assertEqual(frame.notes[0].beats, 1)
        frame.announce_note.assert_called_once_with('quarter note', frame.list, 0)

    def test_shortening_announces_result(self):
        frame = self.editor()
        studio.Frame.nudge_length(frame, False)
        frame.announce_note.assert_called_once_with('eighth note', frame.list, 0)

    def test_floor_does_not_change_note(self):
        frame = self.editor(beats=.125)
        studio.Frame.nudge_length(frame, False)
        self.assertEqual(frame.notes[0].beats, .125)
        frame.touch.assert_not_called()
        self.assertIn('shortest', frame.announce_note.call_args.args[0])

    def test_native_announcement_does_not_refocus_row(self):
        frame = SimpleNamespace(say=Mock())
        control = object()
        with patch.object(studio, 'announce', return_value=True) as announce, \
                patch.object(studio, 'reannounce') as refocus:
            studio.Frame.announce_note(frame, 'C4', control, 0)
            announce.assert_called_once_with(control, 'C4')
            refocus.assert_not_called()


if __name__ == '__main__':
    unittest.main()
