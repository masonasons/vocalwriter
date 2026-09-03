#!/usr/bin/env python3
"""VocalWriter Studio -- build a line of singing, note by note.

    python -m app.studio

A note carries a *group* of phonemes and one pitch, because that is how singing
works: a syllable sits on a note, not one phoneme per note. "Add word" looks the
word up in VocalWriter's own dictionary and puts its phonemes on a single note,
which can then be edited, split or removed.

Everything is reachable from the keyboard and reads with a screen reader. The
list is a native control, editing happens in dialogs with ordinary labelled
fields rather than inside the list, and every action has a shortcut. Enter on a
row edits it, Delete removes it, and both a whole note and a single phoneme can
be heard on their own before committing to them.

Rendering costs several times the length of the audio, so the interface is
built to avoid it: previewing one note renders only that note, and repeating a
render that has already been done returns the cached audio at once.
"""
import math
import os
import sys
import tempfile

import wx
import wx.adv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.lists import ReportList                             # noqa: E402
from app import settings                                     # noqa: E402
from app import project                                      # noqa: E402
from app.engine import Engine                                # noqa: E402
from app.player import PLAYING, Player                        # noqa: E402
from ppc import paths, phonology, render                     # noqa: E402
from ppc.song import parse_pitch                             # noqa: E402

SHARP = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
COLUMNS = (('Phonemes', 165), ('Pitch', 55), ('Beats', 150), ('Word', 95),
           ('Bend', 90), ('Bar', 55))
TRACK_COLUMNS = (('Track', 150), ('Voice', 120), ('Volume', 70),
                 ('Pan', 90), ('State', 90))
DEFAULT_PITCH = 60
DEFAULT_BEATS = 0.5

#: Alt with the arrow keys nudges the selected note: up and down by a
#: semitone, right and left by a sixteenth note. A beat is a quarter note, so
#: a sixteenth is 0.25 beats; MIN_BEATS keeps a note from vanishing.
ID_ADD_WORD, ID_ADD_NOTE, ID_EDIT, ID_REMOVE = (wx.NewIdRef() for _ in range(4))
ID_UP, ID_DOWN, ID_LONGER, ID_SHORTER = (wx.NewIdRef() for _ in range(4))
ID_PLAY, ID_HEAR, ID_STOP, ID_KEYS = (wx.NewIdRef() for _ in range(4))
ID_ADD_REST, ID_IMPORT, ID_EXPORT = (wx.NewIdRef() for _ in range(3))
ID_AUTO_PREVIEW = wx.NewIdRef()
ID_EXPORT_TRACKS = wx.NewIdRef()
ID_BAR_REST, ID_GOTO_BAR, ID_METRONOME = (wx.NewIdRef() for _ in range(3))
ID_PLAY_PAUSE, ID_PLAY_HERE, ID_PANES = (wx.NewIdRef() for _ in range(3))
ID_TRACK_NEW, ID_TRACK_EDIT, ID_TRACK_REMOVE = (wx.NewIdRef()
                                                for _ in range(3))
ID_TRACK_MUTE, ID_TRACK_SOLO = (wx.NewIdRef() for _ in range(2))
ID_TRACK_UP, ID_TRACK_DOWN = (wx.NewIdRef() for _ in range(2))

SEMITONE = 1

#: A beat is a quarter note, so a sixteenth note is a quarter of a beat. This
#: is the step Alt with the left and right arrow keys moves a note by, and it
#: is the same step every time, wherever the note started.
#:
#: It used to round to the nearest step rather than add one, so that a note of
#: some other length would be pulled onto the grid. That was a mistake: the
#: distance travelled then depended on where the note already was, and since a
#: MIDI import rounds lengths to sixteenths, most notes in an imported song
#: moved by half of what the rest did. The step has to be the step.
SIXTEENTH = 0.25

#: A nudge will not take a note below a thirty-second note. The floor only
#: refuses; it never shortens the step to fit, which would make the last press
#: before it move by less than all the others.
MIN_BEATS = 0.125

#: What the lengths are called, so the Beats column can say "1.5, dotted
#: quarter" rather than leaving a number to be worked out.
NOTE_VALUES = {
    6.0: 'dotted whole', 4.0: 'whole', 3.0: 'dotted half', 2.0: 'half',
    1.5: 'dotted quarter', 1.0: 'quarter', 0.75: 'dotted eighth',
    0.5: 'eighth', 0.375: 'dotted sixteenth', 0.25: 'sixteenth',
    0.125: 'thirty-second',
}


def note_value(beats):
    """The name of a length, if it has one."""
    for length, name in NOTE_VALUES.items():
        if abs(beats - length) < 1e-6:
            return name
    return ''

#: The phoneme that is silence. A note holding nothing else is a rest: it is
#: not sung, it is the gap the phrases either side of it are placed around.
REST = '%'


def bend_text(value):
    """One end of a bend as something to put in a text field."""
    return '' if value is None else '%g' % round(value, 3)


def read_bend(text):
    """A bend field: a number, or None for blank and for anything unreadable."""
    text = (text or '').strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


#: What a file name may not contain, and what stands in for it. A track can
#: be called anything at all, including things Windows refuses outright.
BAD_IN_NAMES = '\\/:*?"<>|'


def file_name(text, fallback='track'):
    """A track's name, as much of it as a file can be called.

    Everything unusable becomes a hyphen rather than being dropped, so two
    tracks whose names differ only in punctuation still come out as two
    different files.
    """
    out = ''.join('-' if c in BAD_IN_NAMES or ord(c) < 32 else c
                  for c in text)
    return ' '.join(out.split()).strip(' .-') or fallback


#: How long the nudging has to stop for before a note is previewed. Long
#: enough that holding an arrow key does not queue up a render per step, short
#: enough that letting go is followed by the sound rather than a wait.
PREVIEW_WAIT = 180

#: MSAA: a focus event, and the list's items are its client children.
EVENT_OBJECT_FOCUS = 0x8005
OBJID_CLIENT = -4


def reannounce(listctrl, row):
    """Ask a screen reader to read a row again, after its contents changed.

    Changing the text of a list item tells a screen reader nothing: it speaks
    what is focused, when the focus arrives, so a note nudged up a semitone
    would otherwise change in silence. The status bar is no substitute --
    nothing reads that unless it is asked to.

    The event is raised by hand rather than by disturbing the control, because
    both ways of making the control raise a genuine one damage it: clearing
    and restoring the focused state moves the selection to the next row, and
    doing the same with the selected state stops the plain arrow keys moving
    between rows at all. Both were measured, and neither is worth a label.
    """
    if row < 0 or row >= listctrl.GetItemCount() or sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.user32.NotifyWinEvent(
            EVENT_OBJECT_FOCUS, listctrl.GetHandle(), OBJID_CLIENT, row + 1)
    except Exception:                       # never break editing over this
        pass


def stepped_length(beats, up):
    """A sixteenth note longer or shorter, or None if that is too short.

    Exactly one sixteenth, from wherever the note is now. Nothing is rounded and
    nothing is clamped: a step that would leave the note shorter than the floor
    is refused outright rather than made smaller to fit, because a press that
    moves less than the others is what made this feel unreliable.
    """
    want = beats + (SIXTEENTH if up else -SIXTEENTH)
    if want < MIN_BEATS - 1e-9:
        return None
    return round(want, 6)


def note_name(beats):
    """A length written the way it would be said: "an eighth note"."""
    name = note_value(beats)
    if not name:
        return ''
    return '%s %s note' % ('an' if name[0] in 'aeiou' else 'a', name)


def beat_count(n):
    said = note_name(n)
    return '%g beat%s%s' % (n, '' if n == 1 else 's',
                            ', %s' % said if said else '')


def length_text(beats):
    """A length for the Beats column: the number, and its name if it has one."""
    name = note_value(beats)
    return '%g, %s note' % (beats, name) if name else '%g' % beats


def pitch_name(midi):
    return '%s%d' % (SHARP[midi % 12], midi // 12 - 1)


class Named(wx.Accessible):
    """Gives one control the name and description a screen reader reads.

    Windows works out a control's name by looking for a static text just
    before it among its siblings, which means the name depends on the order
    things happen to have been created in and is silently wrong when they are
    not adjacent. Saying it outright removes the guesswork.

    The name is kept to a word or two because it is read out every time the
    control is reached, and anything longer is tiring to sit through. What the
    field wants goes in the description, which is read after it and can be
    turned off by anyone who does not want it.
    """

    def __init__(self, name, hint=''):
        wx.Accessible.__init__(self)
        self.name = name
        self.hint = hint

    def GetName(self, childId):
        return (wx.ACC_OK, self.name)

    def GetDescription(self, childId):
        if not self.hint:
            return (wx.ACC_NOT_SUPPORTED, '')
        return (wx.ACC_OK, self.hint)


#: Whether wx.Accessible can actually be constructed. It is implemented on
#: Windows only -- on Cocoa and GTK the class exists but its constructor raises
#: NotImplementedError, which took the whole window down on the first control
#: it reached. Nothing is lost by skipping it there: VoiceOver and Orca read
#: the control's name, and `SetName` supplies that on every platform. Probed
#: once, on first use, rather than at import.
_ACCESSIBLE = None


def set_accessible(ctrl, name, hint=''):
    """Name a control for a screen reader, where the toolkit allows it.

    Returns the accessible object so a caller can keep it alive and rename it
    later, or None where the platform has no such thing.
    """
    global _ACCESSIBLE
    if _ACCESSIBLE is None:
        try:
            Named('probe')
            _ACCESSIBLE = True
        except Exception:
            _ACCESSIBLE = False
    if not _ACCESSIBLE:
        return None
    named = Named(name, hint)
    ctrl.SetAccessible(named)
    return named


#: Ask an up-down control which edit box belongs to it (UDM_GETBUDDY).
UDM_GETBUDDY = 0x046A
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


def name_spin_buddy(ctrl, text):
    """Put a spin control's edit box after its caption in Z-order.

    A spin control on Windows is two windows, not one: the little arrows, and
    a separate edit box beside them that is what actually takes the focus and
    the typing. wx wraps only the arrows, so giving the control a name names
    the half nobody lands on -- the Tempo box announced itself as "Voice",
    which was the caption of the field before it, and the one in Add word had
    no name at all. The edit box is not a wx window and cannot be given a name
    directly, so it is moved to sit behind its own caption, which is where
    Windows looks.
    """
    if sys.platform != 'win32' or not isinstance(ctrl, wx.SpinCtrl):
        return
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.SendMessageW.restype = ctypes.c_void_p
        u32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                     ctypes.c_void_p, ctypes.c_void_p]
        buddy = u32.SendMessageW(ctrl.GetHandle(), UDM_GETBUDDY, None, None)
        if not buddy:
            return
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int,
                                     ctypes.c_uint]
        u32.SetWindowPos(buddy, ctypes.c_void_p(text.GetHandle()), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:                       # a name is not worth a crash
        pass


def labelled(parent, sizer, label, ctrl, proportion=0, hint=''):
    """A caption beside a control, and the same words to a screen reader.

    Both halves matter and they are not the same mechanism. `wx.Accessible`
    states the name outright. The static text is also moved to sit immediately
    before the control, because that is where Windows looks by default and it
    is the fallback if the first mechanism is unavailable.

    Getting this wrong is not merely a missing label. Every control here is
    built before its own caption, so what Windows found was the caption of the
    *previous* field: the Beats box announced itself as "Pitch", the bend box
    as "Beats", and the first field in each window had no name at all.
    """
    text = wx.StaticText(parent,
                         label='%s (%s)' % (label, hint) if hint else label)
    ctrl.MoveAfterInTabOrder(text)
    ctrl.SetName(label)
    set_accessible(ctrl, label, hint)
    name_spin_buddy(ctrl, text)
    box = wx.BoxSizer(wx.HORIZONTAL)
    box.Add(text, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
    box.Add(ctrl, proportion, wx.ALIGN_CENTER_VERTICAL)
    sizer.Add(box, 0, wx.EXPAND | wx.ALL, 4)
    return ctrl


def caption(parent, sizer, label, ctrl, proportion=1):
    """A caption above a control that fills the window, such as a list."""
    text = wx.StaticText(parent, label=label)
    ctrl.MoveAfterInTabOrder(text)
    ctrl.SetName(label)
    named = set_accessible(ctrl, label)
    # kept so the caption can be changed afterwards, and so the accessible
    # object stays referenced for as long as the control it names does
    ctrl.caption_text = text
    ctrl.caption_named = named
    sizer.Add(text, 0, wx.LEFT | wx.TOP, 8)
    sizer.Add(ctrl, proportion, wx.EXPAND | wx.ALL, 8)
    return ctrl


def relabel(ctrl, label):
    """Change a captioned control's label, and what is read out with it.

    Both have to move together. The notes list says which track it is showing,
    and a screen reader reads the name rather than the caption, so changing
    only the words on screen would leave it announcing the track you were on
    before.
    """
    text = getattr(ctrl, 'caption_text', None)
    if text is not None:
        text.SetLabel(label)
    named = getattr(ctrl, 'caption_named', None)
    if named is not None:
        named.name = label
    ctrl.SetName(label)


class Note(object):
    """One note: a pitch, a length, and the phonemes sung on it."""

    def __init__(self, phonemes=None, pitch=DEFAULT_PITCH, beats=DEFAULT_BEATS,
                 word='', bend=None):
        self.phonemes = list(phonemes or [])
        self.pitch = pitch
        self.beats = beats
        self.word = word
        #: [(where in the note, semitones)], `where` from 0 to 1. Attached to
        #: the note so that moving or retiming it takes its bend along.
        self.bend = [(float(a), float(v)) for a, v in (bend or ())]

    def text(self):
        return ' '.join(self.phonemes)

    def is_rest(self):
        return not self.phonemes or all(p == REST for p in self.phonemes)

    def label(self):
        """What the list shows: a rest reads as one, not as a per cent sign."""
        return '(rest)' if self.is_rest() else self.text()


class PhonemePicker(wx.Dialog):
    """Choose one phoneme, with its example word, and hear it first."""

    def __init__(self, parent, studio, pitch=DEFAULT_PITCH):
        wx.Dialog.__init__(self, parent, title='Insert phoneme')
        self.studio = studio
        self.pitch = pitch
        pal = studio.singable()
        self.symbols = [row[0] for row in pal]
        choices = ['%s   as in %s' % (s, e) if e else s for s, e in pal]

        outer = wx.BoxSizer(wx.VERTICAL)
        self.choice = wx.Choice(self, choices=choices, size=(300, -1))
        self.choice.SetSelection(0)
        labelled(self, outer, 'Phoneme', self.choice, 1)
        self.preview = wx.Button(self, label='&Preview')
        self.preview.Bind(wx.EVT_BUTTON, self.on_preview)
        outer.Add(self.preview, 0, wx.ALL, 6)
        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.choice.SetFocus()

    def chosen(self):
        return self.symbols[self.choice.GetSelection()]

    def on_preview(self, _evt):
        self.preview.Disable()
        self.studio.say('previewing %s' % self.chosen())
        self.studio.engine.send(
            'preview', lambda res: wx.CallAfter(self._done, res),
            phoneme=self.chosen(), pitch=self.pitch,
            program=self.studio.program())

    def _done(self, res):
        self.preview.Enable()
        if res:
            self.studio.play_file(res['path'])


class AddWordDialog(wx.Dialog):
    """Look a word up and lay it out over one note or several.

    A word of more than one syllable is worth more than one note -- that is
    what singing a word means -- so the phonemes are divided at the syllable
    boundaries and the division can be moved. VocalWriter's own scores do the
    same thing: their lyrics are typed with hyphens, "Dai-sy", one fragment per
    note, so a hyphen here sets the number of notes to start from.
    """

    def __init__(self, parent, studio, pitch=DEFAULT_PITCH,
                 beats=DEFAULT_BEATS):
        wx.Dialog.__init__(self, parent, title='Add word')
        self.studio = studio
        self.pitch = pitch
        self.phonemes = []
        self.rows = []

        outer = wx.BoxSizer(wx.VERTICAL)
        self.word = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER,
                                size=(220, -1))
        self.word.Bind(wx.EVT_TEXT_ENTER, self.on_look)
        labelled(self, outer, 'Word', self.word, 1,
                 hint='a hyphen marks where the notes divide, as in dai-sy')

        row = wx.BoxSizer(wx.HORIZONTAL)
        look = wx.Button(self, label='&Look up')
        look.Bind(wx.EVT_BUTTON, self.on_look)
        row.Add(look, 0, wx.ALL, 3)
        self.hear = wx.Button(self, label='&Preview')
        self.hear.Bind(wx.EVT_BUTTON, self.on_preview)
        self.hear.Disable()
        row.Add(self.hear, 0, wx.ALL, 3)
        outer.Add(row, 0)

        self.count = wx.SpinCtrl(self, min=1, max=1, initial=1, size=(80, -1))
        self.count.Bind(wx.EVT_SPINCTRL, self.on_count)
        # no hint here: a spin control's edit box takes its name from the
        # caption as written, so the name has to stand on its own
        labelled(self, outer, 'Notes to use', self.count)
        self.beats = wx.TextCtrl(self, value='%g' % beats, size=(80, -1),
                                 style=wx.TE_PROCESS_ENTER)
        self.beats.Bind(wx.EVT_TEXT_ENTER, self.on_beats)
        self.beats.Bind(wx.EVT_KILL_FOCUS, self.on_beats)
        labelled(self, outer, 'Beats', self.beats, hint='for each note')

        self.list = ReportList(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                size=(380, 140))
        for i, (head, width) in enumerate(COLUMNS[:3]):
            self.list.InsertColumn(i, head, width=width)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key)
        caption(self, outer, 'Notes for this word', self.list)

        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.ok = self.FindWindowById(wx.ID_OK)
        if self.ok:
            self.ok.Disable()
        self.SetSizerAndFit(outer)
        self.word.SetFocus()

    # -- looking the word up ----------------------------------------------

    def typed(self):
        """The word as the dictionary needs it, and how many notes the
        hyphens in it ask for."""
        raw = self.word.GetValue().strip()
        parts = [p for p in raw.split('-') if p]
        clean = ''.join(c for c in raw if c.isalpha() or c == "'")
        return clean, len(parts)

    def on_look(self, _evt):
        word, wanted = self.typed()
        if not word:
            self.studio.say('type a word first')
            return
        self.studio.say('looking up %s' % word)
        self.studio.engine.phonemes(
            [word], lambda res: wx.CallAfter(self._looked, word, wanted, res))

    def _looked(self, word, wanted, res):
        phones = (res or {}).get(word, [])
        if not phones:
            self.studio.say('no pronunciation for %s. Add a note and type its '
                            'phonemes instead.' % word)
            return
        self.phonemes = phones
        parts = phonology.syllabify(phones)
        self.count.SetRange(1, max(1, len(parts)))
        # one note per syllable unless the hyphens said otherwise -- that is
        # what singing a word of more than one syllable means, and the spin is
        # right here to collapse it back onto a single note
        self.count.SetValue(len(parts) if wanted <= 1
                            else min(wanted, len(parts)))
        self.build()
        self.hear.Enable()
        if self.ok:
            self.ok.Enable()
        self.studio.say('%s is %s, %d syllable%s'
                        % (word, ' '.join(phones), len(parts),
                           '' if len(parts) == 1 else 's'))
        self.count.SetFocus()

    def value_beats(self):
        try:
            return max(0.05, float(self.beats.GetValue()))
        except ValueError:
            return DEFAULT_BEATS

    def build(self):
        """Rebuild the notes from the word, the division and the length."""
        word, _ = self.typed()
        groups = phonology.regroup(self.phonemes, self.count.GetValue())
        b = self.value_beats()
        self.rows = [Note(g, self.pitch, b, word if i == 0 else '')
                     for i, g in enumerate(groups)]
        self.sync()

    def sync(self, select=0):
        self.list.DeleteAllItems()
        for i, n in enumerate(self.rows):
            self.list.InsertItem(i, n.label())
            self.list.SetItem(i, 1, pitch_name(n.pitch))
            self.list.SetItem(i, 2, '%g' % n.beats)
        if self.rows:
            i = min(max(select, 0), len(self.rows) - 1)
            self.list.Select(i)
            self.list.Focus(i)

    def refresh_row(self, i):
        n = self.rows[i]
        self.list.SetItem(i, 0, n.label())
        self.list.SetItem(i, 1, pitch_name(n.pitch))
        self.list.SetItem(i, 2, '%g' % n.beats)

    # -- adjusting ---------------------------------------------------------

    def on_count(self, _evt):
        if self.phonemes:
            self.build()
            self.studio.say(
                '%d note%s: %s' % (len(self.rows),
                                   '' if len(self.rows) == 1 else 's',
                                   ', '.join(n.text() for n in self.rows)))

    def on_beats(self, evt):
        evt.Skip()
        if not self.phonemes:
            return
        b = self.value_beats()
        for i, n in enumerate(self.rows):
            n.beats = b
            self.refresh_row(i)

    def on_key(self, evt):
        code = evt.GetKeyCode()
        i = self.list.GetFirstSelected()
        if evt.AltDown() and i >= 0 and code in (wx.WXK_UP, wx.WXK_DOWN,
                                                 wx.WXK_LEFT, wx.WXK_RIGHT):
            n = self.rows[i]
            if code in (wx.WXK_UP, wx.WXK_DOWN):
                step = SEMITONE if code == wx.WXK_UP else -SEMITONE
                n.pitch = max(0, min(127, n.pitch + step))
                self.studio.say(pitch_name(n.pitch))
            else:
                want = stepped_length(n.beats, code == wx.WXK_RIGHT)
                if want is None:
                    self.studio.say('%s, the shortest a nudge will make it'
                                    % beat_count(n.beats))
                    return
                n.beats = want
                self.studio.say(beat_count(n.beats))
            self.refresh_row(i)
            reannounce(self.list, i)
        elif code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and i >= 0:
            self.on_edit(None)
        else:
            evt.Skip()

    def on_edit(self, _evt):
        i = self.list.GetFirstSelected()
        if i < 0:
            return
        with NoteDialog(self, self.studio, self.rows[i]) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.rows[i] = dlg.result()
        self.sync(select=i)

    def on_preview(self, _evt):
        self.hear.Disable()
        self.studio.say('previewing %s'
                        % ', '.join(n.text() for n in self.rows))
        self.studio.engine.render(
            self.studio.song(self.rows), self.studio.preview_wav,
            lambda res: wx.CallAfter(self._heard, res))

    def _heard(self, res):
        self.hear.Enable()
        if res:
            self.studio.play_file(res['path'])

    def result(self):
        return list(self.rows)


class PointDialog(wx.Dialog):
    """One point on a bend: how far into the note, and how far from the pitch."""

    def __init__(self, parent, at=0.0, value=0.0):
        wx.Dialog.__init__(self, parent, title='Bend point')
        outer = wx.BoxSizer(wx.VERTICAL)
        self.at = wx.TextCtrl(self, value='%g' % round(at * 100, 3),
                              size=(90, -1))
        labelled(self, outer, 'Position', self.at,
                 hint='per cent through the note, 0 is the start')
        self.value = wx.TextCtrl(self, value='%g' % round(value, 3),
                                 size=(90, -1))
        labelled(self, outer, 'Semitones', self.value,
                 hint='above or below the written pitch')
        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.at.SetFocus()

    def result(self):
        def num(ctrl, fallback):
            try:
                return float(ctrl.GetValue())
            except ValueError:
                return fallback
        return (min(1.0, max(0.0, num(self.at, 0.0) / 100.0)),
                num(self.value, 0.0))


class BendDialog(wx.Dialog):
    """The whole shape of one note's bend, point by point.

    Two fields on the note itself cover a plain slide from one pitch to
    another. This is for the rest: a scoop that arrives late, a fall at the
    very end, or the curve read in from a MIDI file, which has a point every
    few milliseconds and would be flattened by anything simpler.
    """

    def __init__(self, parent, studio, note):
        wx.Dialog.__init__(self, parent, title='Pitch bend')
        self.studio = studio
        self.note = note
        self.points = list(note.bend)

        outer = wx.BoxSizer(wx.VERTICAL)
        self.list = ReportList(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                size=(340, 170))
        self.list.InsertColumn(0, 'Position', width=150)
        self.list.InsertColumn(1, 'Semitones', width=150)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key)
        caption(self, outer, 'Bend points', self.list)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (('&Add point...', self.on_add),
                               ('&Edit point...', self.on_edit),
                               ('&Remove point', self.on_remove),
                               ('&Preview note', self.on_preview)):
            b = wx.Button(self, label=label)
            b.Bind(wx.EVT_BUTTON, handler)
            row.Add(b, 0, wx.ALL, 3)
            if label.endswith('note'):
                self.hear = b
        outer.Add(row, 0)
        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.sync()
        self.list.SetFocus()

    def sync(self, select=0):
        self.points.sort(key=lambda pt: pt[0])
        self.list.DeleteAllItems()
        for i, (at, value) in enumerate(self.points):
            self.list.InsertItem(i, '%g per cent' % round(at * 100, 3))
            self.list.SetItem(i, 1, '%+g' % round(value, 3))
        if self.points:
            i = min(max(select, 0), len(self.points) - 1)
            self.list.Select(i)
            self.list.Focus(i)

    def on_key(self, evt):
        code = evt.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_edit(None)
        elif code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
            self.on_remove(None)
        else:
            evt.Skip()

    def on_add(self, _evt):
        with PointDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            at, value = dlg.result()
        self.points.append((at, value))
        self.points.sort(key=lambda pt: pt[0])
        self.sync(select=self.points.index((at, value)))
        self.studio.say('bend point at %g per cent, %+g semitones'
                        % (round(at * 100, 3), round(value, 3)))

    def on_edit(self, _evt):
        i = self.list.GetFirstSelected()
        if i < 0:
            self.studio.say('select a point first')
            return
        with PointDialog(self, *self.points[i]) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.points[i] = dlg.result()
        self.sync(select=i)

    def on_remove(self, _evt):
        i = self.list.GetFirstSelected()
        if i < 0:
            self.studio.say('select a point first')
            return
        self.points.pop(i)
        self.sync(select=min(i, len(self.points) - 1))
        self.studio.say('point removed, %d left' % len(self.points))

    def on_preview(self, _evt):
        self.hear.Disable()
        note = Note(self.note.phonemes, self.note.pitch, self.note.beats,
                    self.note.word, self.result())
        self.studio.engine.render(
            self.studio.song([note]), self.studio.preview_wav,
            lambda res: wx.CallAfter(self._heard, res))

    def _heard(self, res):
        self.hear.Enable()
        if res:
            self.studio.play_file(res['path'])

    def result(self):
        return sorted(self.points, key=lambda pt: pt[0])


class NoteDialog(wx.Dialog):
    """Edit one note: the phonemes sung on it, its pitch and its length."""

    def __init__(self, parent, studio, note=None):
        wx.Dialog.__init__(self, parent,
                           title='Edit note' if note else 'Add note')
        self.studio = studio
        self.note = note or Note()

        outer = wx.BoxSizer(wx.VERTICAL)
        self.phon = wx.TextCtrl(self, value=self.note.text(), size=(300, -1))
        labelled(self, outer, 'Phonemes', self.phon, 1,
                 hint='separated by spaces')

        row = wx.BoxSizer(wx.HORIZONTAL)
        ins = wx.Button(self, label='&Insert phoneme...')
        ins.Bind(wx.EVT_BUTTON, self.on_insert)
        row.Add(ins, 0, wx.ALL, 3)
        self.hear = wx.Button(self, label='Preview &note')
        self.hear.Bind(wx.EVT_BUTTON, self.on_preview)
        row.Add(self.hear, 0, wx.ALL, 3)
        outer.Add(row, 0)

        self.pitch = wx.TextCtrl(self, value=pitch_name(self.note.pitch),
                                 size=(90, -1))
        labelled(self, outer, 'Pitch', self.pitch,
                 hint='a name like C4, or a MIDI number')
        self.beats = wx.TextCtrl(self, value='%g' % self.note.beats,
                                 size=(90, -1))
        labelled(self, outer, 'Beats', self.beats)
        start, end = project.bend_ends(self.note.bend)
        self.bend_start = wx.TextCtrl(self, value=bend_text(start),
                                      size=(90, -1))
        labelled(self, outer, 'Bend at start', self.bend_start,
                 hint='semitones, blank for none')
        self.bend_end = wx.TextCtrl(self, value=bend_text(end), size=(90, -1))
        labelled(self, outer, 'Bend at end', self.bend_end,
                 hint='semitones, blank to hold the starting value')
        points = wx.Button(self, label='Bend p&oints...')
        points.Bind(wx.EVT_BUTTON, self.on_points)
        outer.Add(points, 0, wx.ALL, 6)
        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.phon.SetFocus()

    def on_insert(self, _evt):
        if not self.studio.singable():
            self.studio.say('the phoneme list has not loaded yet. Try again '
                            'in a moment, or type the phonemes in by hand.')
            self.studio.engine.send(
                'palette',
                lambda r: wx.CallAfter(self.studio._set_palette, r))
            return
        with PhonemePicker(self, self.studio, self.value_pitch()) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            sym = dlg.chosen()
        text = self.phon.GetValue().strip()
        self.phon.SetValue((text + ' ' + sym).strip())
        self.phon.SetInsertionPointEnd()
        self.studio.say('inserted %s' % sym)

    def on_preview(self, _evt):
        self.hear.Disable()
        note = self.result()
        self.studio.say('previewing %s' % (note.text() or 'nothing'))
        self.studio.engine.render(
            self.studio.song([note]), self.studio.preview_wav,
            lambda res: wx.CallAfter(self._done, res))

    def _done(self, res):
        self.hear.Enable()
        if res:
            self.studio.play_file(res['path'])

    def on_points(self, _evt):
        """Hand the whole curve over to be edited point by point."""
        note = self.result()
        with BendDialog(self, self.studio, note) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            note.bend = dlg.result()
        # the two fields have to catch up, or closing this window would write
        # its stale endpoints back over what was just edited
        start, end = project.bend_ends(note.bend)
        self.bend_start.ChangeValue(bend_text(start))
        self.bend_end.ChangeValue(bend_text(end))
        self.studio.say('bend has %d point%s, %s'
                        % (len(note.bend), '' if len(note.bend) == 1 else 's',
                           project.describe_bend(note.bend) or 'none'))

    def value_pitch(self):
        try:
            return parse_pitch(self.pitch.GetValue())
        except ValueError:
            return DEFAULT_PITCH

    def result(self):
        self.note.phonemes = self.phon.GetValue().split()
        self.note.pitch = self.value_pitch()
        try:
            self.note.beats = max(0.05, float(self.beats.GetValue()))
        except ValueError:
            pass
        self.note.bend = project.with_ends(
            self.note.bend, read_bend(self.bend_start.GetValue()),
            read_bend(self.bend_end.GetValue()))
        return self.note


class SongSettingsDialog(wx.Dialog):
    """Settings that belong to the whole song rather than to one part.

    The voice controls here are the song's: every part is sung with them
    unless it has been given its own in the track dialog. Setting the vibrato
    for a piece is one number in one place rather than the same number typed
    into every track.
    """

    def __init__(self, parent, bpm, sig, consonant_pct, voice, reverb,
                 anticipate=True):
        wx.Dialog.__init__(self, parent, title='Song settings')
        outer = wx.BoxSizer(wx.VERTICAL)
        self.tempo = wx.SpinCtrl(self, min=30, max=250, initial=int(bpm),
                                 size=(80, -1))
        labelled(self, outer, 'Tempo', self.tempo, hint='beats per minute')
        #: what to fall back on if the box is left saying something that is
        #: not a time signature: what the song had, rather than the default,
        #: because a mistyped 3/4 should not quietly become 4/4
        self.was_sig = tuple(sig)
        self.sig = wx.TextCtrl(self, value=project.format_sig(sig),
                               size=(60, -1))
        labelled(self, outer, 'Time signature', self.sig,
                 hint='as 4/4; it sets where the bars fall')
        self.consonants = wx.SpinCtrl(self, min=10, max=100,
                                      initial=int(consonant_pct),
                                      size=(80, -1))
        labelled(self, outer, 'Consonant length', self.consonants,
                 hint='100 is natural, 40 clips them hard against the vowels')

        # A note is heard where its vowel is, not where it starts, so a note
        # that opens with consonants sounds late -- and how late depends on
        # how the word is spelled, which is what makes a line drag unevenly.
        self.anticipate = wx.CheckBox(
            self, label='Consonants &before the beat')
        self.anticipate.SetValue(bool(anticipate))
        self.anticipate.SetToolTip(
            'The vowel lands on the beat and the consonants are sung into the '
            'end of the note before it, the way a singer does. Turn this off '
            'to hear a note start exactly where it is written, consonants '
            'and all.')
        outer.Add(self.anticipate, 0, wx.ALL, 6)

        # The engine's own reverb, which is two numbers in the application as
        # well. Nothing is heard until the wet is turned up, so a song that
        # says nothing about it sounds as it did before there was one.
        self.room = wx.SpinCtrl(self, min=0, max=100, initial=int(reverb[0]),
                                size=(80, -1))
        labelled(self, outer, 'Reverb room', self.room,
                 hint='how big the space is; VocalWriter used 40')
        self.wet = wx.SpinCtrl(self, min=0, max=100, initial=int(reverb[1]),
                               size=(80, -1))
        labelled(self, outer, 'Reverb amount', self.wet,
                 hint='per cent heard; 0 is none at all, VocalWriter used 24')

        # the engine's own voice controls, for every part that has not been
        # given its own
        self.voice_ctrls = {}
        values = voice or {}
        for key, _call, default, lo, hi, label, hint in render.VOICE_CONTROLS:
            spin = wx.SpinCtrl(self, min=lo, max=hi,
                               initial=int(values.get(key, default)),
                               size=(90, -1))
            labelled(self, outer, label, spin, hint=hint)
            self.voice_ctrls[key] = spin

        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.tempo.SetFocus()

    def result(self):
        """(tempo, signature, consonants, voice, reverb, on the beat)."""
        return (self.tempo.GetValue(),
                project.parse_sig(self.sig.GetValue(), self.was_sig),
                self.consonants.GetValue(),
                render.clean_voice(
                    dict((k, c.GetValue())
                         for k, c in self.voice_ctrls.items())),
                (self.room.GetValue(), self.wet.GetValue()),
                self.anticipate.GetValue())


class TrackDialog(wx.Dialog):
    """A track's name, voice, volume and pan, and how that voice is set up."""

    def __init__(self, parent, studio, track):
        wx.Dialog.__init__(self, parent, title='Track')
        self.studio = studio
        outer = wx.BoxSizer(wx.VERTICAL)

        self.name = wx.TextCtrl(self, value=track.name, size=(220, -1))
        labelled(self, outer, 'Name', self.name)

        # Every voice the bank holds, in its own order: the people's names
        # first, then the instruments, which sing too.
        names = studio.voice_names or ['Robert']
        self.voice = wx.Choice(self, choices=names, size=(180, -1))
        self.voice.SetSelection(min(max(0, studio.track_voice(track)),
                                    len(names) - 1))
        labelled(self, outer, 'Voice', self.voice)

        self.volume = wx.SpinCtrl(self, min=0, max=100, initial=track.volume,
                                  size=(80, -1))
        labelled(self, outer, 'Volume', self.volume, hint='per cent')
        self.pan = wx.SpinCtrl(self, min=-100, max=100, initial=track.pan,
                               size=(80, -1))
        labelled(self, outer, 'Pan', self.pan,
                 hint='-100 far left to 100 far right')

        # A part can keep its own consonant length. Off by default, because
        # the song's setting is the right answer for most parts and one number
        # in one place is easier to reason about than one per track.
        own = getattr(track, 'consonants', None)
        self.own_consonants = wx.CheckBox(
            self, label='&Consonant length just for this track')
        self.own_consonants.SetValue(own is not None)
        outer.Add(self.own_consonants, 0, wx.ALL, 6)
        self.consonants = wx.SpinCtrl(
            self, min=10, max=100,
            initial=int(round((own if own is not None else
                               studio.consonant_pct / 100.0) * 100)),
            size=(80, -1))
        labelled(self, outer, 'Consonant length', self.consonants,
                 hint='when the box above is ticked')
        self.consonants.Enable(own is not None)
        self.own_consonants.Bind(
            wx.EVT_CHECKBOX,
            lambda e: self.consonants.Enable(self.own_consonants.GetValue()))

        # A part may be sung in a different space from the rest -- a lead
        # dry in front of a choir in a hall. Parts that share a setting share
        # one reverberator, so they share its tail as well.
        own_reverb = getattr(track, 'reverb', None)
        self.own_reverb = wx.CheckBox(
            self, label='&Reverb just for this track')
        self.own_reverb.SetValue(own_reverb is not None)
        outer.Add(self.own_reverb, 0, wx.ALL, 6)
        shown = own_reverb if own_reverb is not None else studio.song_reverb
        self.room = wx.SpinCtrl(self, min=0, max=100, initial=int(shown[0]),
                                size=(80, -1))
        labelled(self, outer, 'Reverb room', self.room,
                 hint='when the box above is ticked')
        self.wet = wx.SpinCtrl(self, min=0, max=100, initial=int(shown[1]),
                               size=(80, -1))
        labelled(self, outer, 'Reverb amount', self.wet,
                 hint='per cent heard, when the box above is ticked')
        for spin in (self.room, self.wet):
            spin.Enable(own_reverb is not None)
        self.own_reverb.Bind(wx.EVT_CHECKBOX, self.on_own_reverb)

        # The engine's own voice controls. Like the consonant length, a
        # part follows the song's until it is given its own: the values shown
        # while the box is unticked are the song's, so ticking it starts from
        # what you were already hearing rather than from nothing.
        own_voice = getattr(track, 'voice', None)
        self.own_voice = wx.CheckBox(
            self, label='&Voice controls just for this track')
        self.own_voice.SetValue(own_voice is not None)
        outer.Add(self.own_voice, 0, wx.ALL, 6)
        self.voice_ctrls = {}
        values = own_voice if own_voice is not None else studio.song_voice
        for key, _call, default, lo, hi, label, hint in render.VOICE_CONTROLS:
            spin = wx.SpinCtrl(self, min=lo, max=hi,
                               initial=int((values or {}).get(key, default)),
                               size=(90, -1))
            labelled(self, outer, label, spin, hint=hint)
            spin.Enable(own_voice is not None)
            self.voice_ctrls[key] = spin
        self.own_voice.Bind(wx.EVT_CHECKBOX, self.on_own_voice)

        outer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.name.SetFocus()
        self.name.SetInsertionPointEnd()

    def result(self):
        """(name, voice, volume, pan). The voice is its place in the bank."""
        i = self.voice.GetSelection()
        return (self.name.GetValue().strip() or 'Voice', max(0, i),
                self.volume.GetValue(), self.pan.GetValue())

    def on_own_voice(self, _evt):
        for spin in self.voice_ctrls.values():
            spin.Enable(self.own_voice.GetValue())

    def on_own_reverb(self, _evt):
        for spin in (self.room, self.wet):
            spin.Enable(self.own_reverb.GetValue())

    def apply_voice(self, track):
        """Put the voice settings and the consonant length onto the track.

        Both are None when the part is following the song, which is not the
        same as holding a copy of what the song currently says: a part that
        follows keeps following when the song settings change.
        """
        track.voice = (render.clean_voice(
            dict((k, c.GetValue()) for k, c in self.voice_ctrls.items()))
            if self.own_voice.GetValue() else None)
        track.consonants = (self.consonants.GetValue() / 100.0
                            if self.own_consonants.GetValue() else None)
        track.reverb = ((self.room.GetValue(), self.wet.GetValue())
                        if self.own_reverb.GetValue() else None)


class Frame(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(self, None, title='VocalWriter Studio',
                          size=(720, 560))
        #: Every part of the song. There is always at least one: a song
        #: with no tracks has nowhere to put a note.
        self.tracks = [project.Track(name='Voice 1')]
        #: The song's tempo, its time signature, its consonant length as a
        #: percentage, and the engine's voice controls. All of it lives in the
        #: song settings dialog rather than the main window: it is set once for
        #: a piece and then left alone. A track can override the consonant
        #: length and the voice controls; nothing else here is per-part.
        self.bpm = 120
        self.sig = project.DEFAULT_SIG
        self.consonant_pct = 100
        self.song_voice = render.clean_voice(None)
        #: (room, wet) for the song, as whole percentages. Nothing at all
        #: until it is asked for: a song that says nothing about the reverb
        #: sounds exactly as it did before there was one.
        self.song_reverb = (0, 0)
        #: whether a note's consonants are sung before its beat, so that the
        #: vowel -- which is where a note is heard -- lands on it
        self.anticipate = True
        self.current = 0
        self._switching = False
        self.voice_names = ['Robert']
        self.palette = []
        self.wav = os.path.join(tempfile.gettempdir(), 'vw_studio.wav')
        self.preview_wav = os.path.join(tempfile.gettempdir(), 'vw_note.wav')
        self.sound = None
        self.player = Player()
        self.rendering = False
        self.path = None            # the project file, once it has one
        self.dirty = False

        #: program number -> the voice of the bank it picks, filled in when
        #: the engine answers. Only songs written before the whole bank was
        #: offered need it.
        self.program_map = {}
        #: how you like to work, kept between songs and between sittings
        self.settings = settings.load()
        #: the timer that waits for the nudging to stop before previewing
        self._preview_timer = None

        self.engine = Engine(on_error=self._engine_error)
        self._build()
        self._build_menu()
        # The engine answers in order, so ask for the cheap things first:
        # reading the palette is a file read, listing the voices runs sixteen
        # program changes through the interpreter.
        self.engine.ping(lambda r: wx.CallAfter(self._ready, r))
        self.engine.send('palette',
                         lambda r: wx.CallAfter(self._set_palette, r))
        self.engine.voices(lambda r: wx.CallAfter(self._set_voices, r))
        self.engine.send('program_voices',
                         lambda r: wx.CallAfter(self._set_program_map, r),
                         programs=list(range(128)))
        self.Bind(wx.EVT_CLOSE, self.on_close)

    #: The notes of whichever track is selected. Everything that edits notes
    #: goes through this, so adding, nudging, copying and the rest all act on
    #: the track you are looking at without having to know there are others.
    @property
    def notes(self):
        return self.tracks[self.current].notes

    @notes.setter
    def notes(self, value):
        self.tracks[self.current].notes = list(value)

    @property
    def track(self):
        return self.tracks[self.current]

    # -- interface ---------------------------------------------------------

    def _build(self):
        p = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        # Nothing sits above the lists any more. The voice belongs to a
        # track, and the tempo and the time signature to the song settings
        # dialog, so the window holds the song itself and nothing else: which
        # part, and what it sings. Tab reaches the two lists and stops.

        # The tracks come before the notes, so that Tab runs down the window
        # in the order the song is put together: which part, then what it
        # sings.
        self.tracks_list = ReportList(
            p, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 110))
        for i, (head, width) in enumerate(TRACK_COLUMNS):
            self.tracks_list.InsertColumn(i, head, width=width)
        self.tracks_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_track_edit)
        self.tracks_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_track_chosen)
        self.tracks_list.Bind(wx.EVT_KEY_DOWN, self.on_track_key)
        caption(p, outer, 'Tracks', self.tracks_list, 0)

        # not single-select: a phrase is copied, transposed or deleted as
        # a whole, and Shift with the arrow keys is how that is reached
        self.list = ReportList(p, style=wx.LC_REPORT)
        for i, (head, width) in enumerate(COLUMNS):
            self.list.InsertColumn(i, head, width=width)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key)
        caption(p, outer, 'Notes', self.list)

        self.messages = wx.TextCtrl(
            p, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
            size=(-1, 80))
        labelled(p, outer, 'Messages', self.messages, 1)

        p.SetSizer(outer)
        self.status = self.CreateStatusBar()
        self.sync_tracks(select=0)
        self.say('starting the engine')

    #: Menus rather than buttons: a screen reader announces an item's
    #: shortcut along with its name, so the keys are discoverable instead of
    #: having to be known in advance.
    def _build_menu(self):
        bar = wx.MenuBar()

        note = wx.Menu()
        note.Append(ID_ADD_WORD, 'Add &word...	Ctrl+W',
                    'Look a word up and spread it over one note or several')
        note.Append(ID_ADD_NOTE, 'Add &note...	Ctrl+N',
                    'Add a note and choose its phonemes')
        note.Append(ID_ADD_REST, 'Add &rest	Ctrl+R',
                    'Add a silent break of the same length')
        note.Append(ID_BAR_REST, 'Rest to the end of the &bar	Ctrl+Shift+R',
                    'Add a rest long enough to reach the next bar line')
        note.Append(ID_EDIT, '&Edit note...	Ctrl+E', 'Edit the selected note')
        note.AppendSeparator()
        # No tab, so no global accelerator: Ctrl+C belongs to whatever text
        # field has the focus. The notes list handles these keys itself.
        note.Append(wx.ID_CUT, 'Cu&t (Ctrl+X on a note)',
                    'Remove the selected notes and keep them to paste')
        note.Append(wx.ID_COPY, '&Copy (Ctrl+C on a note)',
                    'Keep the selected notes to paste')
        note.Append(wx.ID_PASTE, '&Paste (Ctrl+V on a note)',
                    'Put the kept notes in after the selection')
        note.Append(wx.ID_SELECTALL, 'Select &all (Ctrl+A on a note)',
                    'Select every note in the song')
        note.AppendSeparator()
        note.Append(ID_REMOVE, '&Remove note	Ctrl+D', 'Remove the selected note')
        note.AppendSeparator()
        note.Append(ID_UP, 'Transpose &up (Alt+Up on a note)',
                    'Up one semitone')
        note.Append(ID_DOWN, 'Transpose &down (Alt+Down on a note)',
                    'Down one semitone')
        note.Append(ID_LONGER, '&Longer	Alt+Right',
                    'One sixteenth note longer')
        note.Append(ID_SHORTER, '&Shorter	Alt+Left',
                    'One sixteenth note shorter')

        track = wx.Menu()
        track.Append(ID_TRACK_NEW, '&New track	Ctrl+T',
                     'Add another part, with its own voice')
        track.Append(ID_TRACK_EDIT, '&Track settings... (Enter on a track)',
                     'Name, voice, volume and pan')
        track.Append(ID_TRACK_REMOVE, '&Remove track (Delete on a track)',
                     'Remove the selected track and everything in it')
        track.AppendSeparator()
        # No tab, so no accelerator: S and M would be unusable in every text
        # field in the program. The tracks list handles them itself.
        track.Append(ID_TRACK_MUTE, '&Mute or unmute (M on a track)',
                     'Silence this track')
        track.Append(ID_TRACK_SOLO, '&Solo or unsolo (S on a track)',
                     'Hear only the soloed tracks')
        track.AppendSeparator()
        track.Append(ID_TRACK_UP, 'Move track &up (Ctrl+Up on a track)',
                     'Put this part earlier in the list')
        track.Append(ID_TRACK_DOWN, 'Move track &down (Ctrl+Down on a track)',
                     'Put this part later in the list')

        play = wx.Menu()
        # Space is handled by the notes list rather than registered as an
        # accelerator, or it could never be typed into a word again.
        self.mi_play = play.Append(ID_PLAY_PAUSE,
                                   'Play or &stop (Space)',
                                   'Play from the note the cursor is on, or '
                                   'stop if it is going')
        play.Append(ID_PLAY, 'Play from the &start	Ctrl+P',
                    'Sing every track from the beginning')
        play.Append(ID_HEAR, '&Hear note	Ctrl+H', 'Sing the selected note')
        play.Append(ID_STOP, '&Stop	Ctrl+.', 'Stop playing')
        play.AppendSeparator()
        self.mi_metronome = play.AppendCheckItem(
            ID_METRONOME, '&Metronome	Ctrl+M',
            'Tick along with Play. Never written into an exported file.')

        go = wx.Menu()
        go.Append(ID_GOTO_BAR, 'Go to &bar...	Ctrl+G',
                  'Jump to the first note in a bar')
        go.Append(ID_PANES, '&Tracks or notes	F6',
                  'Move between the tracks list and the notes list')

        f = wx.Menu()
        f.Append(wx.ID_NEW, '&New	Ctrl+Shift+N', 'Start an empty song')
        f.Append(wx.ID_OPEN, '&Open project...	Ctrl+O',
                 'Reopen a song saved earlier')
        f.Append(wx.ID_SAVE, '&Save project	Ctrl+S', 'Save the song')
        f.Append(wx.ID_SAVEAS, 'Save project &as...',
                 'Save the song under a new name')
        f.AppendSeparator()
        f.Append(ID_IMPORT, '&Import MIDI...	Ctrl+I',
                 'Take the notes from a MIDI file')
        f.Append(ID_EXPORT, '&Export WAV...	Ctrl+Shift+S',
                 'Write the whole song to one file')
        f.Append(ID_EXPORT_TRACKS, 'Export &tracks...	Ctrl+Shift+T',
                 'Write every track to a file of its own')
        f.AppendSeparator()
        # wx moves this into the application menu on macOS, where it becomes
        # Command+comma; elsewhere it stays here as Control+comma.
        f.Append(wx.ID_PREFERENCES, '&Song settings...\tCtrl+,',
                 'Tempo, time signature, consonants, reverb, voice controls')
        f.AppendSeparator()
        f.Append(wx.ID_EXIT, 'E&xit	Alt+F4')

        help_ = wx.Menu()
        help_.Append(ID_KEYS, '&Keys	F1', 'List the shortcuts in Messages')

        bar.Append(f, '&File')
        bar.Append(track, '&Track')
        bar.Append(note, '&Note')
        bar.Append(play, '&Play')
        bar.Append(go, '&Go')
        prefs = wx.Menu()
        self.mi_auto_preview = prefs.AppendCheckItem(
            ID_AUTO_PREVIEW, '&Preview notes as they change\tCtrl+Shift+P',
            'Hear a note whenever its pitch or length is nudged')
        self.mi_auto_preview.Check(bool(self.settings.get('auto_preview')))
        bar.Append(prefs, '&Settings')
        bar.Append(help_, '&Help')
        self.SetMenuBar(bar)

        for ident, handler in ((ID_ADD_WORD, self.on_add_word),
                               (ID_ADD_NOTE, self.on_add_note),
                               (ID_ADD_REST, self.on_add_rest),
                               (ID_BAR_REST, self.on_bar_rest),
                               (ID_GOTO_BAR, self.on_goto_bar),
                               (ID_EDIT, self.on_edit),
                               (wx.ID_CUT, self.on_cut),
                               (wx.ID_COPY, self.on_copy),
                               (wx.ID_PASTE, self.on_paste),
                               (wx.ID_SELECTALL, self.on_select_all),
                               (ID_REMOVE, self.on_remove),
                               (ID_UP, lambda e: self.nudge_pitch(SEMITONE)),
                               (ID_DOWN, lambda e: self.nudge_pitch(-SEMITONE)),
                               (ID_LONGER, lambda e: self.nudge_length(True)),
                               (ID_SHORTER, lambda e: self.nudge_length(False)),
                               (ID_METRONOME, self.on_metronome),
                               (ID_TRACK_NEW, self.on_track_new),
                               (ID_TRACK_EDIT, self.on_track_edit),
                               (ID_TRACK_REMOVE, self.on_track_remove),
                               (ID_TRACK_MUTE,
                                lambda e: self.toggle_track('mute')),
                               (ID_TRACK_SOLO,
                                lambda e: self.toggle_track('solo')),
                               (ID_TRACK_UP, lambda e: self.move_tracks(-1)),
                               (ID_TRACK_DOWN, lambda e: self.move_tracks(1)),
                               (ID_PANES, self.on_panes),
                               (ID_PLAY, self.on_play),
                               (ID_PLAY_PAUSE, self.on_play_stop),
                               (ID_HEAR, self.on_hear),
                               (ID_STOP, self.on_stop),
                               (ID_KEYS, self.on_keys),
                               (wx.ID_NEW, self.on_new),
                               (wx.ID_OPEN, self.on_open),
                               (wx.ID_SAVE, self.on_save),
                               (wx.ID_SAVEAS, self.on_save_as),
                               (ID_IMPORT, self.on_import),
                               (ID_EXPORT, self.on_export),
                               (ID_EXPORT_TRACKS, self.on_export_tracks),
                               (wx.ID_PREFERENCES, self.on_song_settings),
                               (ID_AUTO_PREVIEW, self.on_auto_preview),
                               (wx.ID_EXIT, lambda e: self.Close())):
            self.Bind(wx.EVT_MENU, handler, id=ident)

    def on_song_settings(self, _evt):
        dlg = SongSettingsDialog(self, self.bpm, self.signature(),
                                 self.consonant_pct, self.song_voice,
                                 self.song_reverb, self.anticipate)
        if dlg.ShowModal() == wx.ID_OK:
            was = (self.bpm, self.sig, self.consonant_pct, self.song_voice,
                   self.song_reverb, self.anticipate)
            (self.bpm, self.sig, self.consonant_pct, self.song_voice,
             self.song_reverb, self.anticipate) = dlg.result()
            if (self.bpm, self.sig, self.consonant_pct, self.song_voice,
                    self.song_reverb, self.anticipate) != was:
                self.touch()
            if self.sig != was[1]:
                # which bar a note falls in is read off the signature, so the
                # list is saying the wrong thing until it is built again
                self.sync(select=self.selection())
            self.say('%d beats per minute, %s, %g beats to the bar, '
                     'consonant length %d%%'
                     % (self.bpm, project.format_sig(self.sig),
                        project.bar_beats(self.sig), self.consonant_pct))
            if self.song_voice != was[3]:
                self.say('voice controls set for every part that has not been '
                         'given its own')
            if self.song_reverb != was[4]:
                self.say('reverb %s'
                         % ('off' if not self.song_reverb[1]
                            else 'room %d, %d%% heard'
                            % self.song_reverb))
            if self.anticipate != was[5]:
                self.say('consonants %s the beat'
                         % ('before' if self.anticipate else 'after'))
        dlg.Destroy()

    def on_auto_preview(self, _evt):
        """A setting of the program, not of the song: it is how you like to
        work, so it follows you from song to song and is saved at once rather
        than waiting for a dialog to be closed."""
        on = self.mi_auto_preview.IsChecked()
        self.settings['auto_preview'] = on
        kept = settings.save(self.settings)
        self.say('previewing notes as they change is %s%s'
                 % ('on' if on else 'off',
                    '' if kept else ', for this sitting only: the settings '
                    'file could not be written'))

    def preview_note(self, i):
        """Hear one note, once the nudging has stopped.

        A held arrow key sends a stream of changes and rendering every one of
        them would queue up a stream of little sounds, each already out of
        date. The last one is the one worth hearing, so the render waits for
        a moment of quiet first.
        """
        if not self.settings.get('auto_preview') or self.rendering:
            return
        if self._preview_timer is not None:
            self._preview_timer.Stop()
        self._preview_timer = wx.CallLater(PREVIEW_WAIT,
                                           self._preview_now, i)

    def _preview_now(self, i):
        self._preview_timer = None
        if not (0 <= i < len(self.notes)) or self.rendering:
            return
        note = self.notes[i]
        if not note.phonemes:             # a rest has nothing to hear
            return
        self.stop_audio()
        self.rendering = True
        self.engine.render(self.song([note]), self.preview_wav,
                           lambda res: wx.CallAfter(self._heard, res))

    def on_keys(self, _evt):
        for line in ('F6  move between the tracks list and the notes list',
                     'Ctrl+T  add a track. Each track has its own voice, '
                     'volume, pan and notes',
                     'Enter on a track  its name, voice, volume and pan',
                     'M on a track  mute it, S  solo it',
                     'Delete on a track  remove it',
                     'Ctrl+Up or Ctrl+Down on a track  reorder the parts',
                     'Ctrl+W  add word', 'Ctrl+N  add note',
                     'Ctrl+R  add a rest, a silent break',
                     'Ctrl+Shift+R  rest to the end of the bar',
                     'Ctrl+G  go to a bar',
                     'Ctrl+E  edit note', 'Ctrl+D or Delete  remove note',
                     'Ctrl+C, Ctrl+X, Ctrl+V  copy, cut and paste notes',
                     'Ctrl+A  select every note',
                     'Ctrl+Up or Ctrl+Down  move a note earlier or later',
                     'Ctrl+comma  song settings: tempo, time signature, '
                     'consonant length, the reverb, and the voice controls '
                     'every part follows unless it has its own',
                     'Ctrl+Shift+P  hear a note whenever it is nudged',
                     'Shift with the arrow keys selects more than one note',
                     'Alt+Up or Alt+Down  transpose a semitone',
                     'Alt+Right or Alt+Left  a sixteenth note longer or '
                     'shorter',
                     'Pitch bend is set per note, in the note editor',
                     'Space  play from the note the cursor is on, or stop '
                     'if it is playing',
                     'Ctrl+P  play every track from the start',
                     'Ctrl+H  hear the selected note',
                     'Ctrl+M  metronome on or off, for playing only',
                     'Ctrl+. stop',
                     'Ctrl+O open a project, Ctrl+S save it',
                     'Ctrl+I  import a MIDI file',
                     'Ctrl+Shift+S  export the whole song as one WAV',
                     'Ctrl+Shift+T  export every track to its own WAV',
                     'Enter on a note edits it'):
            self.say(line)

    def say(self, text):
        self.status.SetStatusText(text)
        self.messages.AppendText(text + '\n')

    def _ready(self, info):
        info = info or {}
        self.say('engine ready: %s, Python %s'
                 % (info.get('engine', 'unknown'), info.get('python', '')))

    def _set_program_map(self, picks):
        self.program_map = {p: v for p, v in enumerate(picks or [])
                            if v is not None}
        self.sync_tracks(select=self.current)

    def track_voice(self, track):
        """Which voice of the bank a part sings with.

        A part written down before the whole bank was offered carries a
        program number instead; the engine says what that program picks, and
        it is turned into a place in the bank the first time it is asked for,
        so nothing is lost and nothing has to be converted twice.
        """
        if getattr(track, 'voice_id', None) is None:
            track.voice_id = self.program_map.get(track.program, 0)
        return track.voice_id

    def _set_voices(self, names):
        self.voice_names = list(names) or ['Robert']
        self.sync_tracks(select=self.current)   # the Voice column can now say

    def voice_name(self, index):
        """What the voice at that place in the bank is called."""
        if 0 <= index < len(self.voice_names):
            return self.voice_names[index]
        return 'voice %d' % index

    def _set_palette(self, rows):
        self.palette = rows or []
        n = len(self.singable())
        self.say('%d phonemes available' % n)
        if not n:
            self.say('the phoneme list did not load. Phonemes can still be '
                     'typed into a note by hand.')

    def singable(self):
        """The palette minus silence: a rest is Ctrl+R, not a phoneme."""
        return [r for r in self.palette if r[0] != REST]

    def _engine_error(self, msg):
        wx.CallAfter(self.say, 'engine error: %s' % msg)

    # -- tracks ------------------------------------------------------------

    def track_state(self, t):
        """What the State column says: on, muted, soloed, or silenced by one.

        A track that is neither muted nor soloed is still silent while some
        other track is soloed, and that is worth saying outright -- otherwise
        a track reading "on" that cannot be heard looks like a fault.
        """
        bits = []
        if t.solo:
            bits.append('solo')
        if t.mute:
            bits.append('muted')
        if not bits and any(x.solo and not x.mute for x in self.tracks):
            bits.append('silent')
        return ', '.join(bits) or 'on'

    def refresh_track(self, i):
        t = self.tracks[i]
        self.tracks_list.SetItem(i, 0, t.name)
        self.tracks_list.SetItem(i, 1, self.voice_name(self.track_voice(t)))
        self.tracks_list.SetItem(i, 2, '%d%%' % t.volume)
        self.tracks_list.SetItem(i, 3, project.pan_text(t.pan))
        self.tracks_list.SetItem(i, 4, self.track_state(t))

    def sync_tracks(self, select=None):
        self._switching = True             # our own selecting is not a choice
        try:
            self.tracks_list.DeleteAllItems()
            for i, t in enumerate(self.tracks):
                self.tracks_list.InsertItem(i, t.name)
                self.refresh_track(i)
            i = min(max(0 if select is None else select, 0),
                    len(self.tracks) - 1)
            self.current = i
            self.tracks_list.Select(i)
            self.tracks_list.Focus(i)
        finally:
            self._switching = False
        relabel(self.list, 'Notes in %s' % self.track.name)

    def on_track_chosen(self, evt):
        """Moving down the tracks list changes what the notes list shows."""
        evt.Skip()
        # Not evt.GetIndex(): the selection event of the control used on
        # platforms where wx.ListCtrl is not accessible carries no row. The
        # list itself knows, and answers the same on both.
        i = self.tracks_list.GetFirstSelected()
        if self._switching or i == self.current or not (0 <= i
                                                        < len(self.tracks)):
            return
        self.tracks[self.current].cursor = max(0, self.selection())
        self.current = i
        t = self.track
        relabel(self.list, 'Notes in %s' % t.name)
        self.sync(select=t.cursor)
        self.say('%s, %s, %d note%s, %s'
                 % (t.name, self.voice_name(self.track_voice(t)),
                    len(t.notes),
                    '' if len(t.notes) == 1 else 's', self.track_state(t)))

    def on_track_key(self, evt):
        code = evt.GetKeyCode()
        plain = not (evt.ControlDown() or evt.AltDown() or evt.ShiftDown())
        if evt.ControlDown() and code in (wx.WXK_UP, wx.WXK_DOWN):
            self.move_tracks(-1 if code == wx.WXK_UP else 1)
        elif code == wx.WXK_SPACE and plain:
            self.on_play_stop(None)
        elif code == ord('S') and plain:
            self.toggle_track('solo')
        elif code == ord('M') and plain:
            self.toggle_track('mute')
        elif code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
            self.on_track_remove(None)
        elif code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_track_edit(None)       # not skipped: one dialog, not two
        else:
            evt.Skip()

    def track_at(self):
        i = self.tracks_list.GetFirstSelected()
        return i if 0 <= i < len(self.tracks) else self.current

    def toggle_track(self, field):
        """S and M on a track: solo it, or mute it.

        Every row is redrawn, not just this one, because soloing a track is a
        statement about all the others as well.
        """
        i = self.track_at()
        t = self.tracks[i]
        setattr(t, field, not getattr(t, field))
        self.touch()
        for k in range(len(self.tracks)):
            self.refresh_track(k)
        reannounce(self.tracks_list, i)
        self.say('%s %s%s' % (t.name, '' if getattr(t, field) else 'not ',
                              'soloed' if field == 'solo' else 'muted'))

    def track_name(self):
        """A name not already in use, so two tracks never read the same."""
        taken = {t.name for t in self.tracks}
        n = len(self.tracks) + 1
        while ('Voice %d' % n) in taken:
            n += 1
        return 'Voice %d' % n

    def on_track_new(self, _evt):
        t = project.Track(name=self.track_name(), program=self.track.program)
        self.tracks.insert(self.current + 1, t)
        self.touch()
        self.sync_tracks(select=self.current + 1)
        self.sync()
        self.tracks_list.SetFocus()
        self.say('added %s. Enter on a track sets its voice, volume and pan.'
                 % t.name)

    def on_track_edit(self, _evt):
        i = self.track_at()
        t = self.tracks[i]
        dlg = TrackDialog(self, self, t)
        if dlg.ShowModal() == wx.ID_OK:
            t.name, t.voice_id, t.volume, t.pan = dlg.result()
            dlg.apply_voice(t)
            self.touch()
            self.refresh_track(i)
            if i == self.current:
                relabel(self.list, 'Notes in %s' % t.name)
            reannounce(self.tracks_list, i)
            self.say('%s, %s, volume %d%%, %s, %s'
                     % (t.name, self.voice_name(self.track_voice(t)),
                        t.volume,
                        project.pan_text(t.pan),
                        'its own voice controls' if t.voice is not None
                        else "the song's voice controls"))
        dlg.Destroy()

    def on_track_remove(self, _evt):
        if len(self.tracks) == 1:
            self.say('a song needs at least one track')
            return
        i = self.track_at()
        t = self.tracks[i]
        if t.notes:
            answer = wx.MessageBox(
                'Remove %s and the %d note%s in it?'
                % (t.name, len(t.notes), '' if len(t.notes) == 1 else 's'),
                'VocalWriter Studio', wx.YES_NO | wx.ICON_QUESTION, self)
            if answer != wx.YES:
                self.say('kept %s' % t.name)
                return
        self.tracks.pop(i)
        self.touch()
        self.sync_tracks(select=min(i, len(self.tracks) - 1))
        self.sync(select=self.track.cursor)
        self.say('removed %s, %d track%s left'
                 % (t.name, len(self.tracks),
                    '' if len(self.tracks) == 1 else 's'))

    def move_tracks(self, delta):
        """Ctrl+Up and Ctrl+Down on a track: put the parts in another order."""
        i = self.track_at()
        j = i + delta
        if not (0 <= j < len(self.tracks)):
            self.say('%s is already %s' % (self.tracks[i].name,
                                           'first' if delta < 0 else 'last'))
            return
        self.tracks[i], self.tracks[j] = self.tracks[j], self.tracks[i]
        self.touch()
        self.sync_tracks(select=j)
        self.tracks_list.SetFocus()
        self.say('%s is now track %d of %d'
                 % (self.tracks[j].name, j + 1, len(self.tracks)))

    def on_panes(self, _evt):
        """F6: between the tracks and the notes, without hunting with Tab."""
        if self.list.HasFocus():
            self.tracks_list.SetFocus()
            self.say('tracks')
        else:
            self.list.SetFocus()
            self.say('notes in %s' % self.track.name)

    # -- the list ----------------------------------------------------------

    def selection(self):
        return self.list.GetFirstSelected()

    def selected(self):
        """Every selected row, in order."""
        out, i = [], self.list.GetFirstSelected()
        while i >= 0:
            out.append(i)
            i = self.list.GetNextSelected(i)
        return out

    def select_only(self, rows):
        for i in self.selected():
            self.list.Select(i, False)
        for i in rows:
            if 0 <= i < self.list.GetItemCount():
                self.list.Select(i)
        if rows:
            self.list.Focus(max(0, min(rows[0], self.list.GetItemCount() - 1)))

    def signature(self):
        """The time signature. Still a method, because everything asks for it
        as one from when it was read out of a box in the window."""
        return self.sig

    def position(self, index):
        """Where a note starts, in beats from the beginning."""
        return sum(n.beats for n in self.notes[:index])

    def bar_of(self, index):
        return project.bar_and_beat(self.position(index), self.signature())[0]

    def on_goto_bar(self, _evt):
        """Jump to a bar, which is how a long song is got about without
        stepping through every note in it."""
        if not self.notes:
            self.say('there are no notes yet')
            return
        last = self.bar_of(len(self.notes) - 1)
        dlg = wx.TextEntryDialog(self, 'Bar number, 1 to %d' % last,
                                 'Go to bar', '1')
        ok = dlg.ShowModal() == wx.ID_OK
        text = dlg.GetValue()
        dlg.Destroy()
        if not ok:
            return
        try:
            want = int(text.strip())
        except ValueError:
            self.say('%r is not a bar number' % text)
            return
        for i in range(len(self.notes)):
            if self.bar_of(i) >= want:
                self.select_only([i])
                self.list.SetFocus()
                self.say('bar %d, note %d of %d, %s'
                         % (self.bar_of(i), i + 1, len(self.notes),
                            self.notes[i].label()))
                return
        self.say('the song ends at bar %d' % last)

    def on_bar_rest(self, _evt):
        """Fill the rest of the bar with silence.

        This is what a time signature is for while a song is being written: it
        says where the bar line falls, so a phrase can be made to end on one
        without any counting.
        """
        rows = self.selected()
        at = (rows[-1] + 1) if rows else len(self.notes)
        span = project.bar_beats(self.signature())
        pos = self.position(at)
        left = span - (pos % span)
        if left < 1e-9 or abs(left - span) < 1e-9:
            left = span                     # already on a bar line: a whole bar
        near = self.notes[at - 1] if at else self.last()
        rest = Note([REST], near.pitch if near else DEFAULT_PITCH, left)
        self.notes.insert(at, rest)
        self.touch()
        self.sync(select=at)
        self.say('rest to the end of bar %s: %s'
                 % (project.bar_and_beat(pos, self.signature())[0],
                    beat_count(round(left, 6))))

    def bars(self):
        """Where each note starts, as "bar.beat".

        The engine is never told any of this -- it only knows lengths in beats
        -- but a song is easier to build when you can tell where the bar lines
        fall, especially without seeing them.
        """
        sig = self.signature()
        out, at = [], 0.0
        for n in self.notes:
            bar, beat = project.bar_and_beat(at, sig)
            out.append('%d:%g' % (bar, round(beat, 3)))
            at += n.beats
        return out

    def sync(self, select=None):
        self.list.DeleteAllItems()
        for i, n in enumerate(self.notes):
            self.list.InsertItem(i, n.label())
            self.list.SetItem(i, 1, pitch_name(n.pitch))
            self.list.SetItem(i, 2, length_text(n.beats))
            self.list.SetItem(i, 3, n.word)
            self.list.SetItem(i, 4, project.describe_bend(n.bend))
        for i, where in enumerate(self.bars()):
            self.list.SetItem(i, 5, where)
        if self.notes:
            i = min(max(0 if select is None else select, 0),
                    len(self.notes) - 1)
            self.select_only([i])

    def on_key(self, evt):
        code = evt.GetKeyCode()
        if evt.AltDown() and code in (wx.WXK_UP, wx.WXK_DOWN,
                                      wx.WXK_LEFT, wx.WXK_RIGHT):
            if code == wx.WXK_UP:
                self.nudge_pitch(SEMITONE)
            elif code == wx.WXK_DOWN:
                self.nudge_pitch(-SEMITONE)
            elif code == wx.WXK_RIGHT:
                self.nudge_length(True)
            else:
                self.nudge_length(False)
        elif evt.ControlDown() and code in (wx.WXK_UP, wx.WXK_DOWN):
            self.move_notes(-1 if code == wx.WXK_UP else 1)
        elif code == wx.WXK_SPACE and not (evt.ControlDown()
                                           or evt.AltDown()
                                           or evt.ShiftDown()):
            self.on_play_stop(None)
        elif evt.ControlDown() and code == ord('A'):
            self.on_select_all(None)
        elif evt.ControlDown() and code in (ord('C'), ord('X'), ord('V')):
            {ord('C'): self.on_copy, ord('X'): self.on_cut,
             ord('V'): self.on_paste}[code](None)
        elif code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
            self.on_remove(None)
        elif code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_edit(None)
        else:
            evt.Skip()

    def refresh_row(self, i):
        """Update one row in place, so the selection and focus do not move."""
        n = self.notes[i]
        self.list.SetItem(i, 0, n.label())
        self.list.SetItem(i, 1, pitch_name(n.pitch))
        self.list.SetItem(i, 2, length_text(n.beats))
        self.list.SetItem(i, 3, n.word)
        self.list.SetItem(i, 4, project.describe_bend(n.bend))
        # a length change moves every note after this one to a different beat
        for k, where in enumerate(self.bars()):
            self.list.SetItem(k, 5, where)

    def nudge_pitch(self, by):
        rows = self.selected()
        if not rows:
            self.say('select a note first')
            return
        for i in rows:
            n = self.notes[i]
            n.pitch = max(0, min(127, n.pitch + by))
            self.refresh_row(i)
        self.touch()
        if len(rows) == 1:
            self.say(pitch_name(self.notes[rows[0]].pitch))
        else:
            self.say('%d notes %s a semitone'
                     % (len(rows), 'up' if by > 0 else 'down'))
        reannounce(self.list, rows[0])
        self.preview_note(rows[0])

    def nudge_length(self, up):
        """Move the selected note a sixteenth note longer or shorter.

        It lands *on* the grid rather than adding a step to whatever was
        there, so the lengths stay musical. Adding a step is what made this
        feel unreliable: a half-beat note pushed down hit the floor at 0.25 --
        a quarter of a beat, not a half -- and pushing it back up from there
        gave 0.75, so the note never returned to where it started and the two
        keys did not undo each other.
        """
        rows = self.selected()
        if not rows:
            self.say('select a note first')
            return
        moved = 0
        for i in rows:
            n = self.notes[i]
            want = stepped_length(n.beats, up)
            if want is None:
                continue
            n.beats = want
            moved += 1
        if not moved:
            self.say('%s, the shortest a nudge will make it. Edit the note to '
                     'go shorter.' % beat_count(self.notes[rows[0]].beats))
            return
        self.touch()
        self.sync_lengths()
        self.preview_note(rows[0])
        if len(rows) == 1:
            self.say(beat_count(self.notes[rows[0]].beats))
        else:
            self.say('%d notes a sixteenth %s'
                     % (moved, 'longer' if up else 'shorter'))
        reannounce(self.list, rows[0])

    def sync_lengths(self):
        """Redraw every row: a length change moves the bars after it."""
        for i in range(len(self.notes)):
            self.refresh_row(i)

    def last(self):
        return self.notes[-1] if self.notes else None

    def on_select_all(self, _evt):
        if not self.notes:
            self.say('there are no notes yet')
            return
        self.select_only(list(range(len(self.notes))))
        self.say('all %d notes selected' % len(self.notes))

    def move_notes(self, delta):
        """Shift the selected notes one place earlier or later in the song.

        The selection moves with them, so a note can be walked into position
        with repeated presses rather than being cut and pasted.
        """
        rows = self.selected()
        if not rows:
            self.say('select a note first')
            return
        if (delta < 0 and rows[0] == 0) or \
           (delta > 0 and rows[-1] == len(self.notes) - 1):
            self.say('already at the %s' % ('beginning' if delta < 0 else 'end'))
            return
        for i in (rows if delta < 0 else reversed(rows)):
            self.notes[i], self.notes[i + delta] = \
                self.notes[i + delta], self.notes[i]
        moved = [i + delta for i in rows]
        self.touch()
        self.sync(select=moved[0])
        self.select_only(moved)
        if len(moved) == 1:
            self.say('%s is now note %d of %d'
                     % (self.notes[moved[0]].label(), moved[0] + 1,
                        len(self.notes)))
        else:
            self.say('%d notes moved %s' % (len(moved),
                                            'earlier' if delta < 0 else 'later'))
        reannounce(self.list, moved[0])

    # -- copy and paste ----------------------------------------------------
    #
    # Notes go to the system clipboard as text, so a phrase can be carried
    # between two copies of the program and so that what was copied can be
    # seen. A note kept only in memory would be lost the moment the window
    # closed, which is not what copying means anywhere else.

    def to_clipboard(self, text):
        if not wx.TheClipboard.Open():
            return False
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(text))
        finally:
            wx.TheClipboard.Close()
        return True

    def from_clipboard(self):
        if not wx.TheClipboard.Open():
            return ''
        try:
            data = wx.TextDataObject()
            if not wx.TheClipboard.GetData(data):
                return ''
            return data.GetText()
        finally:
            wx.TheClipboard.Close()

    def on_copy(self, _evt):
        rows = self.selected()
        if not rows:
            self.say('select a note first')
            return
        picked = [self.notes[i] for i in rows]
        if not self.to_clipboard(project.to_clipboard(picked)):
            self.say('the clipboard is busy, try again')
            return
        self.say('copied %s' % (picked[0].label() if len(picked) == 1
                                else '%d notes' % len(picked)))

    def on_cut(self, _evt):
        rows = self.selected()
        if not rows:
            self.say('select a note first')
            return
        self.on_copy(None)
        for i in reversed(rows):
            self.notes.pop(i)
        self.touch()
        self.sync(select=min(rows[0], len(self.notes) - 1))
        self.say('cut %d note%s' % (len(rows), '' if len(rows) == 1 else 's'))

    def on_paste(self, _evt):
        rows = project.from_clipboard(self.from_clipboard())
        if not rows:
            self.say('there are no notes on the clipboard')
            return
        here = self.selected()
        at = (here[-1] + 1) if here else len(self.notes)
        added = [Note(ph, pitch, beats, word, bend)
                 for ph, pitch, beats, word, bend in rows]
        self.notes[at:at] = added
        self.touch()
        self.sync(select=at)
        self.select_only(list(range(at, at + len(added))))
        self.say('pasted %d note%s: %s'
                 % (len(added), '' if len(added) == 1 else 's',
                    ', '.join(n.label() for n in added[:4])))

    def on_add_word(self, _evt):
        last = self.last()
        with AddWordDialog(self, self,
                           last.pitch if last else DEFAULT_PITCH,
                           last.beats if last else DEFAULT_BEATS) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            added = dlg.result()
        if not added:
            return
        at = len(self.notes)
        self.notes.extend(added)
        self.touch()
        self.sync(select=at)
        self.say('added %s over %d note%s: %s'
                 % (added[0].word or 'the word', len(added),
                    '' if len(added) == 1 else 's',
                    ', '.join(n.text() for n in added)))

    def on_add_note(self, _evt):
        last = self.last()
        seed = Note([], last.pitch if last else DEFAULT_PITCH,
                    last.beats if last else DEFAULT_BEATS)
        with NoteDialog(self, self, seed) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.notes.append(dlg.result())
        self.touch()
        self.sync(select=len(self.notes) - 1)
        self.say('added a note: %s' % (self.notes[-1].text() or 'empty'))

    def on_add_rest(self, _evt):
        """Insert a silence after the selected note, or at the end.

        A rest is a note nobody sings. It breaks the line into phrases: the
        engine stops at one and starts again after it, so the phrase before it
        is allowed to end instead of running into whatever follows.
        """
        i = self.selection()
        near = self.notes[i] if i >= 0 else self.last()
        rest = Note([REST], near.pitch if near else DEFAULT_PITCH,
                    near.beats if near else DEFAULT_BEATS)
        at = len(self.notes) if i < 0 else i + 1
        self.notes.insert(at, rest)
        self.touch()
        self.sync(select=at)
        self.say('added a rest of %s. Alt+Right and Alt+Left change it.'
                 % beat_count(rest.beats))

    def on_edit(self, _evt):
        i = self.selection()
        if i < 0:
            self.say('select a note first')
            return
        with NoteDialog(self, self, self.notes[i]) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.notes[i] = dlg.result()
        self.touch()
        self.sync(select=i)
        self.say('note %d is %s at %s' % (i + 1, self.notes[i].text(),
                                          pitch_name(self.notes[i].pitch)))

    def on_remove(self, _evt):
        rows = self.selected()
        if not rows:
            self.say('select a note first')
            return
        gone = [self.notes[i] for i in rows]
        for i in reversed(rows):
            self.notes.pop(i)
        self.touch()
        self.sync(select=min(rows[0], len(self.notes) - 1))
        self.say('removed %s' % (gone[0].label() if len(gone) == 1
                                 else '%d notes' % len(gone)))

    # -- audio -------------------------------------------------------------

    def program(self):
        """The voice of the track being worked on, for previews."""
        return self.track_voice(self.track)

    def song(self, notes=None, start=0.0, tracks=None):
        """The whole song as the engine wants it.

        Only the tracks that can be heard are sent, so mute and solo need no
        meaning at the far end -- what is not in the list is not sung. `notes`
        renders just those notes on the current track's voice, which is what
        hearing a single note does, and `tracks` says outright which parts to
        sing, which is what exporting them one at a time does.
        """
        if tracks is not None:
            parts = tracks
        elif notes is None:
            parts = project.audible(self.tracks)
        else:
            # Hearing one note should sound like the part it is in, so the
            # part is copied whole and given that one note. Copying only some
            # of it is what made a preview go on using the voice, the pan and
            # the level the track had before you changed them.
            t = self.track
            parts = [project.Track(
                name=t.name, program=t.program, volume=t.volume, pan=t.pan,
                voice=getattr(t, 'voice', None),
                consonants=getattr(t, 'consonants', None),
                reverb=getattr(t, 'reverb', None),
                voice_id=self.track_voice(t), notes=notes)]
        return {'bpm': float(self.bpm),
                'consonants': self.consonant_pct / 100.0,
                'start': round(float(start), 6),
                'reverb': {'room': self.song_reverb[0],
                           'wet': self.song_reverb[1]},
                'anticipate': bool(self.anticipate),
                'tracks': [self.part(t) for t in parts]}

    def part(self, t):
        """One track for the engine. Volume and pan go out as fractions.

        A part with no voice controls of its own is sung with the song's, so
        the resolving happens here and the engine is handed one set of numbers
        per track without having to know where they came from.
        """
        voice = getattr(t, 'voice', None)
        if voice is None:
            voice = self.song_voice
        own_reverb = getattr(t, 'reverb', None)
        return {'program': t.program,
                'voice_id': self.track_voice(t),
                'volume': t.volume / 100.0,
                'pan': t.pan / 100.0,
                'voice': dict(voice or {}),
                'reverb': (None if own_reverb is None
                           else {'room': own_reverb[0], 'wet': own_reverb[1]}),
                'consonants': getattr(t, 'consonants', None),
                'notes': [{'pitch': n.pitch, 'beats': n.beats,
                           'phonemes': n.phonemes or [REST]} for n in t.notes],
                'bends': [[round(at, 5), round(v, 4), bool(sl)]
                          for at, v, sl in project.timeline(t.notes)]}

    def playback(self, start=0.0):
        """The song as it is played: the metronome goes on here and nowhere
        else, so an exported file is only ever the singing."""
        song = self.song(start=start)
        if self.mi_metronome.IsChecked():
            song['metronome'] = {'bar': project.bar_beats(self.signature())}
        return song

    def on_metronome(self, _evt):
        if not self.mi_metronome.IsChecked():
            self.say('metronome off')
            return
        self.say('metronome on, %s, %g beats to the bar. It ticks along with '
                 'Play and is never written into an exported file.'
                 % (project.format_sig(self.signature()),
                    project.bar_beats(self.signature())))

    def on_hear(self, _evt):
        """Render only the selected note -- far quicker than the whole line."""
        i = self.selection()
        if i < 0:
            self.say('select a note first')
            return
        if self.rendering:
            self.say('already rendering')
            return
        self.stop_audio()
        self.rendering = True
        self.say('rendering one note')
        self.engine.render(self.song([self.notes[i]]), self.preview_wav,
                           lambda res: wx.CallAfter(self._heard, res))

    def _heard(self, res):
        self.rendering = False
        if res:
            self.say('note is %.2f seconds%s'
                     % (res.get('seconds', 0),
                        ' from cache' if res.get('cached') else ''))
            self.play_file(res['path'])

    def start_beats(self):
        """Where Space starts from: the note the cursor is on.

        Every track starts there, not just this one -- the cursor is a place in
        the song, and the parts have to stay together. Whatever is already
        sounding at that moment on another track is heard from the middle
        rather than being started again.
        """
        i = self.selection()
        return self.position(i) if i > 0 else 0.0

    def on_play_stop(self, _evt):
        """Space: play from the cursor, or stop if it is already going.

        Ctrl+Space still toggles a row's selection, which is what space does in
        a list; that is the one thing given up for this.
        """
        if self.rendering:
            self.say('still rendering')
            return
        if self.player.state() == PLAYING:
            self.player.stop()
            self.say('stopped')
            return
        self.start_playing(self.start_beats())

    def on_play(self, _evt):
        """Ctrl+P: from the top, wherever the cursor happens to be."""
        self.start_playing(0.0)

    def start_playing(self, start=0.0):
        if self.rendering:
            self.say('already rendering')
            return
        live = [t for t in project.audible(self.tracks) if t.notes]
        if not live:
            if any(t.notes for t in self.tracks):
                self.say('every track with notes in it is silent. '
                         'M unmutes a track, S turns solo off.')
            else:
                self.say('nothing to sing yet. Add a word or a note.')
            return
        total = max(sum(n.beats for n in t.notes) for t in live)
        if start >= total - 1e-9:
            start = 0.0                    # past the end: start again instead
        self.stop_audio()          # the file cannot be written while it plays
        self.rendering = True
        self.mi_play.Enable(False)
        bar, beat = project.bar_and_beat(start, self.signature())
        self.say('rendering %d track%s from bar %d beat %g, about %.1f seconds'
                 % (len(live), '' if len(live) == 1 else 's', bar,
                    round(beat, 2),
                    (total - start) * 60.0 / self.bpm))
        self.engine.render(self.playback(start), self.wav,
                           lambda res: wx.CallAfter(self._played, res))

    def _played(self, res):
        self.rendering = False
        self.mi_play.Enable(True)
        if not res:
            self.say('render failed')
            return
        loud = ''
        if (res.get('peak') or 0) > 1.0:
            # said out loud rather than left to be noticed: the song simply
            # getting quieter with no explanation is worse than the clipping
            loud = ', the tracks added up past full scale so the mix was ' \
                   'turned down'
        self.say('playing %.2f seconds%s%s'
                 % (res.get('seconds', 0),
                    ' from cache' if res.get('cached') else '', loud))
        self.play_file(res['path'])

    def play_file(self, path):
        """Play a WAV through the player, which can also pause it."""
        if not os.path.isfile(path):
            self.say('no audio file to play')
            return
        size = os.path.getsize(path)
        try:
            if self.player.play(path):
                self.say('playing %s, %d KB' % (os.path.basename(path),
                                                size // 1024))
                return
            # nothing native: wx will at least make a noise
            self.sound = wx.adv.Sound(path)
            if not self.sound.IsOk():
                raise RuntimeError('the file could not be opened')
            self.sound.Play(wx.adv.SOUND_ASYNC)
            self.say('playing %s, %d KB' % (os.path.basename(path),
                                            size // 1024))
        except Exception as exc:
            self.say('cannot play (%s). The file is at %s' % (exc, path))

    def stop_audio(self):
        try:
            self.player.stop()
        except Exception:
            pass

    def on_stop(self, _evt):
        self.stop_audio()
        self.say('stopped')

    def on_export(self, _evt):
        if not any(t.notes for t in project.audible(self.tracks)):
            self.say('nothing to export: no track that can be heard has any '
                     'notes in it')
            return
        with wx.FileDialog(self, 'Export audio',
                           wildcard='WAV files (*.wav)|*.wav',
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        self.say('rendering to %s' % os.path.basename(path))
        self.engine.render(
            self.song(), path,
            lambda res: wx.CallAfter(self.say, 'wrote %s, %.2f seconds'
                                     % (os.path.basename(path),
                                        (res or {}).get('seconds', 0))))

    def export_jobs(self, folder):
        """(track, where it goes) for each track, with no two files alike.

        Every track that has notes in it, muted or not. Mute and solo are how
        a song is listened to while it is being written; leaving a part out of
        an export because it happened to be muted at the time would be a
        nasty thing to discover later, in another program, with the song no
        longer in front of you.
        """
        base = (os.path.splitext(os.path.basename(self.path))[0]
                if self.path else 'song')
        jobs, taken = [], set()
        for t in self.tracks:
            if not t.notes:
                continue
            stem = '%s - %s' % (file_name(base, 'song'), file_name(t.name))
            name, k = stem, 1
            while name.lower() in taken:    # two tracks may share a name
                k += 1
                name = '%s %d' % (stem, k)
            taken.add(name.lower())
            jobs.append((t, os.path.join(folder, name + '.wav')))
        return jobs

    def on_export_tracks(self, _evt):
        """Every track as a file of its own, at its own volume and pan.

        They are stems, not separate performances: each one carries the volume
        and the pan it has in the song, so laying them on top of one another
        again gives back the mix.
        """
        if self.rendering:
            self.say('already rendering')
            return
        if not any(t.notes for t in self.tracks):
            self.say('nothing to export: no track has any notes in it')
            return
        with wx.DirDialog(self, 'Export each track into this folder',
                          style=wx.DD_DEFAULT_STYLE) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            folder = dlg.GetPath()
        jobs = self.export_jobs(folder)
        silent = [t.name for t in self.tracks
                  if t.notes and t not in project.audible(self.tracks)]
        self.rendering = True
        self.mi_play.Enable(False)
        self.say('exporting %d track%s into %s'
                 % (len(jobs), '' if len(jobs) == 1 else 's', folder))
        if silent:
            self.say('including %s, which %s silent in the song at the moment'
                     % (', '.join(silent), 'is' if len(silent) == 1
                        else 'are'))
        self._export_track(jobs, 0, [])

    def _export_track(self, jobs, i, written):
        """One track, then the next. They go one at a time because the engine
        does one at a time, and because each one is worth saying out loud."""
        if i >= len(jobs):
            self.rendering = False
            self.mi_play.Enable(True)
            failed = len(jobs) - len(written)
            self.say('exported %d of %d track%s%s'
                     % (len(written), len(jobs),
                        '' if len(jobs) == 1 else 's',
                        '' if not failed else ', %d failed' % failed))
            return
        _track, path = jobs[i]
        self.say('rendering %s, %d of %d'
                 % (os.path.basename(path), i + 1, len(jobs)))
        self.engine.render(
            self.song(tracks=[jobs[i][0]]), path,
            lambda res: wx.CallAfter(self._exported, jobs, i, written, res))

    def _exported(self, jobs, i, written, res):
        _track, path = jobs[i]
        if res:
            written.append(path)
            self.say('wrote %s, %.2f seconds'
                     % (os.path.basename(path), res.get('seconds', 0)))
        else:
            self.say('could not write %s' % os.path.basename(path))
        self._export_track(jobs, i + 1, written)

    # -- projects ----------------------------------------------------------

    def touch(self, dirty=True):
        """Note that the song has changed, and show it in the title."""
        self.dirty = dirty
        name = os.path.basename(self.path) if self.path else 'Untitled'
        self.SetTitle('%s%s - VocalWriter Studio'
                      % ('*' if dirty else '', name))

    def may_discard(self, what):
        """Ask before throwing away unsaved work. True to go ahead."""
        if not self.dirty or not any(t.notes for t in self.tracks):
            return True
        answer = wx.MessageBox(
            'This song has changes that have not been saved. %s anyway?'
            % what, 'VocalWriter Studio',
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
        return answer == wx.YES

    def take(self, bpm, tracks, path=None, sig=None, consonants=None,
             voice=None, reverb=None, anticipate=True):
        """Replace the song with what was loaded or imported.

        `tracks` are the dictionaries `project.load` gives back, whose rows
        become notes here -- the note is the editor's own idea, so the file
        reader has no business building one.
        """
        self.tracks = [project.Track(
            name=t['name'], program=t['program'], volume=t['volume'],
            pan=t['pan'], mute=t['mute'], solo=t['solo'],
            voice=t.get('voice'), consonants=t.get('consonants'),
            reverb=t.get('reverb'), voice_id=t.get('voice_id'),
            notes=[Note(ph, pitch, beats, word, bend)
                   for ph, pitch, beats, word, bend in t['rows']])
            for t in tracks]
        if not self.tracks:
            self.tracks = [project.Track(name='Voice 1')]
        self.bpm = max(30, min(250, int(round(bpm))))
        if sig:
            self.sig = project.parse_sig(project.format_sig(sig))
        if consonants:
            self.consonant_pct = max(10, min(100, int(round(consonants * 100))))
        self.song_voice = render.clean_voice(voice)
        self.song_reverb = tuple(reverb or (0, 0))
        self.anticipate = bool(anticipate)
        self.path = path
        self.sync_tracks(select=0)
        self.sync(select=0)
        self.touch(dirty=path is None)

    def on_new(self, _evt):
        if not self.may_discard('Start a new song'):
            return
        self.tracks = [project.Track(name='Voice 1',
                                     program=self.track.program)]
        self.path = None
        self.sync_tracks(select=0)
        self.sync()
        self.touch(dirty=False)
        self.say('new song')

    def on_open(self, _evt):
        if not self.may_discard('Open another project'):
            return
        with wx.FileDialog(self, 'Open project', wildcard=project.WILDCARD,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            (bpm, tracks, sig, consonants, voice, reverb,
             early) = project.load(path)
        except (OSError, ValueError) as exc:
            self.say('cannot open %s: %s' % (os.path.basename(path), exc))
            return
        self.take(bpm, tracks, path, sig, consonants, voice, reverb, early)
        self.say('opened %s: %d track%s, %d notes, %g bpm'
                 % (os.path.basename(path), len(self.tracks),
                    '' if len(self.tracks) == 1 else 's',
                    sum(len(t.notes) for t in self.tracks), bpm))

    def on_save(self, _evt):
        if self.path:
            return self.write_project(self.path)
        return self.on_save_as(None)

    def on_save_as(self, _evt):
        with wx.FileDialog(self, 'Save project', wildcard=project.WILDCARD,
                           defaultFile='song' + project.SUFFIX,
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        if not path.lower().endswith(project.SUFFIX):
            path += project.SUFFIX
        self.write_project(path)

    def write_project(self, path):
        try:
            project.save(path, self.bpm, self.tracks,
                         self.signature(), self.consonant_pct / 100.0,
                         self.song_voice, self.song_reverb, self.anticipate)
        except OSError as exc:
            self.say('could not save: %s' % exc)
            return
        self.path = path
        self.touch(dirty=False)
        self.say('saved %s: %d track%s, %d notes'
                 % (os.path.basename(path), len(self.tracks),
                    '' if len(self.tracks) == 1 else 's',
                    sum(len(t.notes) for t in self.tracks)))

    # -- MIDI --------------------------------------------------------------

    def on_import(self, _evt):
        if not self.may_discard('Import over it'):
            return
        with wx.FileDialog(self, 'Import MIDI',
                           wildcard=project.MIDI_WILDCARD,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            parts = project.midi_tracks(path)
        except (OSError, ValueError) as exc:
            self.say('cannot read %s: %s' % (os.path.basename(path), exc))
            return
        if not parts:
            self.say('%s has no notes in it' % os.path.basename(path))
            return
        names = [n for n, _count in parts]
        if len(parts) > 1:
            # Every part at once is the first choice, and the default: a file
            # with several parts in it is usually several parts of one song.
            labels = (['Every part, one track each']
                      + ['%s, %d notes' % (n, c) for n, c in parts])
            pick = wx.SingleChoiceDialog(self, 'What should be imported?',
                                         'Import MIDI', labels)
            ok = pick.ShowModal() == wx.ID_OK
            i = pick.GetSelection()
            pick.Destroy()
            if not ok:
                return
            if i > 0:
                names = [names[i - 1]]
        try:
            bpm, sig, got = project.from_midi_tracks(path, names)
        except (OSError, ValueError) as exc:
            self.say('cannot import: %s' % exc)
            return
        program = self.track.program
        self.take(bpm, [{'name': name, 'program': program, 'volume': 100,
                         'pan': 0, 'mute': False, 'solo': False, 'rows': rows}
                        for name, rows, _pending in got], sig=sig)
        self.say('imported %s: %d track%s, %d notes, %g bpm, %s'
                 % (os.path.basename(path), len(got),
                    '' if len(got) == 1 else 's',
                    sum(len(r) for _n, r, _p in got), round(bpm),
                    project.format_sig(sig)))
        if not any(pend for _n, _rows, pend in got) and not any(
                any(row[3] for row in rows) for _n, rows, _p in got):
            self.say('it carried no words, so every note sings %s. '
                     'Ctrl+W puts a word on one.' % project.DEFAULT_PHONEME)
        if len(got) > 1:
            self.say('every part is singing in %s. Enter on a track gives it '
                     'its own voice, volume and pan.'
                     % self.voice_name(self.program_map.get(program, 0)))
        # which track each word landed on, since a lookup comes back for the
        # whole file at once
        pending = [(k, word, indices)
                   for k, (_n, _rows, pend) in enumerate(got)
                   for word, indices in pend]
        if pending:
            words = sorted({w for _k, w, _ix in pending})
            self.say('looking up %d word%s' % (len(words),
                                               '' if len(words) == 1 else 's'))
            rows_by_track = [rows for _n, rows, _p in got]
            self.engine.phonemes(
                words,
                lambda res: wx.CallAfter(self._imported_words, rows_by_track,
                                         pending, res))

    def _imported_words(self, rows_by_track, pending, res):
        """Fill in the pronunciations for a MIDI that carried only lyrics."""
        found = res or {}
        rows = [list(r) for r in rows_by_track]
        got = 0
        for ti, word, indices in pending:
            phones = found.get(word) or []
            if ti >= len(rows):
                continue
            if not phones:
                # a word the dictionary does not know. The note still has a
                # pitch and a length, so it sings the default vowel rather
                # than falling silent, and the word stays on it to be seen.
                for i in indices:
                    if 0 <= i < len(rows[ti]) and not rows[ti][i][0]:
                        row = list(rows[ti][i])
                        row[0] = [project.DEFAULT_PHONEME]
                        rows[ti][i] = tuple(row)
                continue
            rows[ti] = project.fill(rows[ti], word, indices, phones)
            got += 1
        for ti, track in enumerate(self.tracks):
            if ti < len(rows):
                track.notes = [Note(ph, pitch, beats, word, bend)
                               for ph, pitch, beats, word, bend in rows[ti]]
        self.sync(select=0)
        self.touch()
        missing = len(pending) - got
        self.say('%d word%s pronounced%s'
                 % (got, '' if got == 1 else 's',
                    '' if not missing else
                    ', %d not in the dictionary -- those notes sing %s until '
                    'you say otherwise' % (missing, project.DEFAULT_PHONEME)))

    def on_close(self, evt):
        if evt.CanVeto() and not self.may_discard('Close'):
            evt.Veto()
            return
        self.stop_audio()
        self.engine.close()
        evt.Skip()


def main():
    missing = paths.missing()
    app = wx.App(False)
    if missing:
        nl = chr(10)
        wx.MessageBox(
            "VocalWriter Studio needs VocalWriter 2.0's own "
            "files, which are not part of this program." + nl + nl +
            'Put them in a folder named "assets" next to this '
            'application.' + nl + nl +
            'Missing:' + nl + '  ' + (nl + '  ').join(missing) +
            nl + nl + 'Looked in:' + nl + '  ' + paths.data_root(),
            'VocalWriter files not found', wx.OK | wx.ICON_INFORMATION)
        return
    Frame().Show()
    app.MainLoop()


if __name__ == '__main__':
    main()
