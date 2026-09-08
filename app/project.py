#!/usr/bin/env python3
"""Saving a song, reopening it, and reading one in from a MIDI file.

A project is plain JSON. It holds what the editor knows and nothing else -- the
notes, the tempo and the voice -- so it stays readable, stays small, and does
not go stale when the engine changes. The audio is not in it; that is what
Export WAV is for.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc.engine import DEFAULT_REVERB, clean_reverb  # noqa: E402
from ppc.render import VOICE_DEFAULTS, clean_voice   # noqa: E402
from ppc import phonology                                    # noqa: E402
from tools.smf import MidiFile, split_phonemes               # noqa: E402

SUFFIX = '.vws'
WILDCARD = 'VocalWriter Studio project (*.vws)|*.vws'
MIDI_WILDCARD = 'MIDI files (*.mid;*.midi)|*.mid;*.midi'

#: Bumped only if an older file would otherwise be read wrongly. Readers accept
#: anything they understand rather than demanding an exact match. Version 2
#: keeps the notes under `tracks` instead of at the top; a version 1 file is
#: read as a song with one track in it, which is what it is.
VERSION = 2

#: two symbols the exports spell differently from the engine's own table
PALETTE = {'OH': 'O', 'DX': 'DD'}

#: What a note sings when the file says nothing about what to sing. An
#: ordinary MIDI file has pitches and lengths and no words at all, and a note
#: with no phonemes in it is a rest -- so importing one used to give a song of
#: the right shape that made no sound whatever, and every note had to be
#: filled in by hand before anything could be heard. "AA" is the open vowel of
#: "father": something to sing the line on, and the obvious thing to replace.
DEFAULT_PHONEME = 'AA'

#: Pitch bend is kept as semitones, and attached to the note it happens on
#: rather than to the song. MIDI stores it as a 14-bit number whose meaning
#: depends on a bend-range setting that can itself change mid-song, which is
#: unreadable in a saved file and breaks the moment a note is moved. A note's
#: bend is [(where in the note, semitones)], `where` running 0 to 1, so it
#: survives the note being retimed, transposed or dragged elsewhere.
BEND_EPSILON = 0.01          # semitones below which a point is not worth keeping

#: The engine has no idea what a time signature is -- it is handed lengths in
#: beats and a tempo, and that is all. This is for the editor: it is what says
#: which bar a note falls in, so a song can be built to a shape rather than as
#: one long run of notes. It changes nothing about the sound.
DEFAULT_SIG = (4, 4)

#: The phoneme that is silence. A note holding nothing else is a rest: it is
#: not sung, it is the gap the phrases either side of it are placed around.
REST = '%'

#: What a note is before anything has been said about it: middle C, an eighth.
DEFAULT_PITCH = 60
DEFAULT_BEATS = 0.5

#: What a file name may not contain, and what stands in for it. A track can
#: be called anything at all, including things Windows refuses outright.
BAD_IN_NAMES = '\\/:*?"<>|'


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


def file_name(text, fallback='track'):
    """A track's name, as much of it as a file can be called.

    Everything unusable becomes a hyphen rather than being dropped, so two
    tracks whose names differ only in punctuation still come out as two
    different files.
    """
    out = ''.join('-' if c in BAD_IN_NAMES or ord(c) < 32 else c
                  for c in text)
    return ' '.join(out.split()).strip(' .-') or fallback


class Track(object):
    """One part of a song: its own voice, its own level, its own notes.

    Volume is a percentage and pan runs from -100 at the far left to +100 at
    the far right, because those are numbers you can say out loud and type
    back in. The engine is given fractions instead; the conversion happens on
    the way out.

    Mute and solo are kept with the track, and saved, because they are part of
    how a song is being worked on rather than a passing state of the window.
    """

    def __init__(self, name='', program=0, volume=100, pan=0,
                 mute=False, solo=False, notes=None, voice=None,
                 consonants=None, reverb=None, voice_id=None):
        self.name = name
        #: which program change this part used to be, kept for songs written
        #: down before voices were chosen out of the bank by name
        self.program = int(program)
        #: which voice of the bank sings this part. There are 87, and the ones
        #: with instrument names sing as readily as the ones with people's
        #: names. None means the program above still decides.
        self.voice_id = None if voice_id is None else int(voice_id)
        self.volume = int(volume)
        self.pan = int(pan)
        self.mute = bool(mute)
        self.solo = bool(solo)
        self.notes = list(notes or [])
        #: the engine's voice controls for this part -- colour, vibrato,
        #: chorus, breath, detune -- or None to follow the song's, which is
        #: what a part does unless it is given its own. See
        #: ppc.render.VOICE_CONTROLS.
        self.voice = None if voice is None else clean_voice(voice)
        #: (room, wet) for this part, or None to follow the song's. A part
        #: sung in a different space from the rest is a real thing to want --
        #: a lead dry in front of a choir in a hall -- and parts that share a
        #: setting share one reverberator and its tail.
        self.reverb = None if reverb is None else clean_reverb(
            reverb if isinstance(reverb, dict)
            else {'room': reverb[0], 'wet': reverb[1]})
        #: consonant length for this part, or None to follow the project's.
        #: A part sung fast wants shorter consonants than one held slowly, and
        #: that is a property of the part rather than of the song.
        self.consonants = None if consonants is None else float(consonants)
        #: which note was last selected here, so that moving between tracks
        #: comes back to where you were. Not saved: it is not part of the song.
        self.cursor = 0


def audible(tracks):
    """The tracks that will actually be heard.

    Solo wins: the moment any track is soloed, it and any other soloed track
    are the song and everything else is silent. Otherwise it is everything
    that has not been muted. A muted track that is also soloed stays silent --
    mute is the more definite statement of the two.
    """
    solo = [t for t in tracks if t.solo and not t.mute]
    if solo:
        return solo
    return [t for t in tracks if not t.mute]


def tracks_from(docs):
    """The dictionaries `load` and the MIDI reader give back, as tracks.

    The rows become notes here. Everything that opens a song goes through it,
    so the window and the command line end up with the same document.
    """
    tracks = [Track(name=t.get('name', ''), program=t.get('program', 0),
                    volume=t.get('volume', 100), pan=t.get('pan', 0),
                    mute=t.get('mute', False), solo=t.get('solo', False),
                    voice=t.get('voice'), consonants=t.get('consonants'),
                    reverb=t.get('reverb'), voice_id=t.get('voice_id'),
                    notes=[Note(ph, pitch, beats, word, bend)
                           for ph, pitch, beats, word, bend in t['rows']])
              for t in docs]
    return tracks or [Track(name='Voice 1')]


def track_voice(track, program_map=None):
    """Which voice of the bank a part sings with.

    A part written down before the whole bank was offered carries a program
    number instead; `program_map` says what that program picks, and it is
    turned into a place in the bank the first time it is asked for, so nothing
    is lost and nothing has to be converted twice.
    """
    if getattr(track, 'voice_id', None) is None:
        track.voice_id = (program_map or {}).get(track.program, 0)
    return track.voice_id


def part_dict(track, song_voice=None, program_map=None):
    """One track for the engine. Volume and pan go out as fractions.

    A part with no voice controls of its own is sung with the song's, so the
    resolving happens here and the engine is handed one set of numbers per
    track without having to know where they came from.
    """
    voice = getattr(track, 'voice', None)
    if voice is None:
        voice = song_voice
    own_reverb = getattr(track, 'reverb', None)
    return {'program': track.program,
            'voice_id': track_voice(track, program_map),
            'volume': track.volume / 100.0,
            'pan': track.pan / 100.0,
            'voice': dict(voice or {}),
            'reverb': (None if own_reverb is None
                       else {'room': own_reverb[0], 'wet': own_reverb[1]}),
            'consonants': getattr(track, 'consonants', None),
            'notes': [{'pitch': n.pitch, 'beats': n.beats,
                       'phonemes': n.phonemes or [REST]} for n in track.notes],
            'bends': [[round(at, 5), round(v, 4), bool(sl)]
                      for at, v, sl in timeline(track.notes)]}


def song_dict(bpm, tracks, consonants=1.0, voice=None, reverb=(0, 0),
              anticipate=True, start=0.0, program_map=None):
    """The whole song as the engine wants it.

    `tracks` are the parts to sing and nothing else -- mute and solo have no
    meaning at the far end, so whoever calls this decides what is audible.
    `consonants` is a fraction here, not a percentage.
    """
    reverb = tuple(reverb or (0, 0))
    return {'bpm': float(bpm),
            'consonants': float(consonants),
            'start': round(float(start), 6),
            'reverb': {'room': reverb[0], 'wet': reverb[1]},
            'anticipate': bool(anticipate),
            'tracks': [part_dict(t, voice, program_map) for t in tracks]}


def export_jobs(tracks, folder, base='song'):
    """(track, where it goes) for each track, with no two files alike.

    Every track that has notes in it, muted or not. Mute and solo are how a
    song is listened to while it is being written; leaving a part out of an
    export because it happened to be muted at the time would be a nasty thing
    to discover later, in another program, with the song no longer in front
    of you.
    """
    jobs, taken = [], set()
    for t in tracks:
        if not t.notes:
            continue
        stem = '%s - %s' % (file_name(base, 'song'), file_name(t.name))
        name, k = stem, 1
        while name.lower() in taken:        # two tracks may share a name
            k += 1
            name = '%s %d' % (stem, k)
        taken.add(name.lower())
        jobs.append((t, os.path.join(folder, name + '.wav')))
    return jobs


def pronounce(rows_by_track, pending, found):
    """Put the looked-up phonemes on the notes an import left blank.

    Returns (rows, words pronounced). A word the dictionary does not know
    leaves its note singing DEFAULT_PHONEME rather than falling silent: the
    note still has a pitch and a length, and the word stays on it to be seen.
    """
    found = found or {}
    rows = [list(r) for r in rows_by_track]
    got = 0
    for ti, word, indices in pending:
        if ti >= len(rows):
            continue
        phones = found.get(word) or []
        if not phones:
            for i in indices:
                if 0 <= i < len(rows[ti]) and not rows[ti][i][0]:
                    row = list(rows[ti][i])
                    row[0] = [DEFAULT_PHONEME]
                    rows[ti][i] = tuple(row)
            continue
        rows[ti] = fill(rows[ti], word, indices, phones)
        got += 1
    return rows, got


def pan_text(pan):
    """A pan as something to read out: "centre", "left 40", "right 100"."""
    pan = int(pan)
    if not pan:
        return 'centre'
    return ('right %d' if pan > 0 else 'left %d') % abs(pan)


def bar_beats(sig):
    """How many beats are in a bar, a beat being a quarter note.

    Six-eight is six eighth notes, which is three of this program's beats, so
    the denominator has to be taken into account rather than just the count.
    """
    num, den = sig
    return max(0.25, num * 4.0 / max(den, 1))


def bar_and_beat(position, sig):
    """Where a point in the song falls, as (bar, beat), both counting from 1.

    The beat is in the signature's own units, so beat 3 of a bar of 6/8 is the
    third eighth note, not the third quarter.
    """
    span = bar_beats(sig)
    bar = int(position // span)
    within = position - bar * span
    return bar + 1, within * sig[1] / 4.0 + 1


def format_sig(sig):
    return '%d/%d' % (int(sig[0]), int(sig[1]))


def parse_sig(text, fallback=DEFAULT_SIG):
    """Read "3/4". Anything unusable keeps what was there before."""
    try:
        num, den = str(text).replace(' ', '').split('/')
        num, den = int(num), int(den)
    except (ValueError, AttributeError):
        return tuple(fallback)
    if not (1 <= num <= 32) or den not in (1, 2, 4, 8, 16, 32):
        return tuple(fallback)
    return num, den


def save(path, bpm, tracks, sig=DEFAULT_SIG, consonants=1.0, voice=None,
         reverb=None, anticipate=True):
    """Write a project. `tracks` are Tracks whose notes have phonemes and a
    pitch, a length and a word. `voice` is the engine's voice controls for the
    song, which every track that has none of its own is sung with."""
    doc = {
        'format': 'vocalwriter-studio',
        'version': VERSION,
        'bpm': float(bpm),
        'time_signature': [int(sig[0]), int(sig[1])],
        'consonants': float(consonants),
        'tracks': [_track_doc(t) for t in tracks],
    }
    song_voice = _voice_doc(voice)
    if song_voice:
        doc['voice'] = song_voice
    if reverb and (reverb[0] or reverb[1]):
        doc['reverb'] = {'room': int(reverb[0]), 'wet': int(reverb[1])}
    if not anticipate:
        # written only when it is off, so a file that says nothing about it
        # sings on the beat -- which is what a file should do
        doc['anticipate'] = False
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1)
        fh.write('\n')


def _voice_doc(values):
    """Voice controls as they are written down: only the ones moved off the
    engine's own default, so a project that touches none of them reads the
    same as one saved before they existed."""
    return dict((k, int(v)) for k, v in (values or {}).items()
                if k in VOICE_DEFAULTS and int(v) != VOICE_DEFAULTS[k])


def _track_doc(t):
    doc = {'name': t.name, 'program': int(t.program),
           'voice_id': (None if getattr(t, 'voice_id', None) is None
                        else int(t.voice_id)),
           'volume': int(t.volume), 'pan': int(t.pan),
           'mute': bool(t.mute), 'solo': bool(t.solo),
           'notes': [_note_doc(n) for n in t.notes]}
    # A track with no voice controls of its own writes none at all and reads
    # back following the song. One that has them writes what it has moved,
    # which may be nothing -- an empty entry still says "its own", so a part
    # deliberately left at the defaults is not quietly signed up to whatever
    # the song does later.
    if getattr(t, 'voice', None) is not None:
        doc['voice'] = _voice_doc(t.voice)
    if getattr(t, 'reverb', None) is not None:
        doc['reverb'] = {'room': int(t.reverb[0]), 'wet': int(t.reverb[1])}
    if getattr(t, 'consonants', None) is not None:
        doc['consonants'] = float(t.consonants)
    return doc


def _note_doc(n):
    doc = {'phonemes': list(n.phonemes), 'pitch': int(n.pitch),
           'beats': float(n.beats), 'word': n.word}
    bend = list(getattr(n, 'bend', None) or ())
    if bend:
        doc['bend'] = [[round(float(at), 4), round(float(v), 4)]
                       for at, v in bend]
    return doc


def load(path):
    """Read a project: bpm, tracks, signature, consonants, voice, reverb and
    whether the consonants go before the beat.

    A track comes back as a dictionary whose `rows` are what the editor turns
    into notes, since the note itself belongs to the editor and not here.

    Anything missing falls back to something sensible rather than failing: a
    project that has lost its tempo is still worth opening. A file saved
    before there were tracks has its notes at the top level, and is read as
    the one track it describes.
    """
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not ('tracks' in doc or 'notes' in doc):
        raise ValueError('not a VocalWriter Studio project')
    parts = doc.get('tracks')
    if not parts:
        parts = [{'name': 'Voice 1', 'program': doc.get('program', 0),
                  'notes': doc.get('notes') or []}]
    tracks = [_read_track(part, i) for i, part in enumerate(parts)]
    sig = doc.get('time_signature') or DEFAULT_SIG
    try:
        sig = (int(sig[0]), int(sig[1]))
    except (TypeError, ValueError, IndexError):
        sig = DEFAULT_SIG
    try:
        consonants = float(doc.get('consonants', 1.0))
    except (TypeError, ValueError):
        consonants = 1.0
    return (float(doc.get('bpm', 120)), tracks, sig, consonants,
            clean_voice(doc.get('voice')), clean_reverb(doc.get('reverb')),
            bool(doc.get('anticipate', True)))


def _read_track(doc, index=0):
    con = doc.get('consonants')
    try:
        con = None if con is None else float(con)
    except (TypeError, ValueError):
        con = None
    return {'name': doc.get('name') or ('Voice %d' % (index + 1)),
            'voice_id': (_int(doc['voice_id'], 0)
                         if doc.get('voice_id') is not None else None),
            'voice': (clean_voice(doc['voice']) if 'voice' in doc else None),
            'reverb': (clean_reverb(doc['reverb']) if 'reverb' in doc
                       else None),
            'consonants': con,
            'program': _int(doc.get('program'), 0),
            'volume': max(0, min(100, _int(doc.get('volume'), 100))),
            'pan': max(-100, min(100, _int(doc.get('pan'), 0))),
            'mute': bool(doc.get('mute')),
            'solo': bool(doc.get('solo')),
            'rows': _rows(doc.get('notes') or [])}


def _int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _rows(entries):
    """Note dictionaries as the tuples the editor builds notes from."""
    rows = []
    for e in entries:
        try:
            rows.append((list(e.get('phonemes') or ['%']),
                         int(e.get('pitch', 60)),
                         float(e.get('beats', 0.5)),
                         e.get('word', ''),
                         [(float(a), float(v))
                          for a, v in e.get('bend') or []]))
        except (TypeError, ValueError, AttributeError):
            continue
    return rows


# -- the clipboard ---------------------------------------------------------

#: What copied notes are wrapped in. Notes go on the clipboard as text so they
#: survive between two copies of the program, and so that what is on the
#: clipboard can be looked at.
CLIP_KEY = 'vocalwriter-studio-notes'


def to_clipboard(notes):
    return json.dumps({CLIP_KEY: [_note_doc(n) for n in notes]}, indent=1)


def from_clipboard(text):
    """Rows from clipboard text, or [] if it is not ours."""
    try:
        doc = json.loads(text)
        entries = doc[CLIP_KEY]
    except (ValueError, TypeError, KeyError):
        return []
    return _rows(entries)


# -- MIDI ------------------------------------------------------------------

def _named_tracks(midi):
    """Every track with notes in it, each paired with a name of its own.

    The name is how a caller says which track it wants, so two tracks must
    never answer to the same one. MIDI files routinely leave every track
    unnamed -- and a file that names two of them "Piano" is ordinary -- so a
    repeated name is numbered here. Without this, asking for five parts of an
    unnamed file returns the first part five times, which is what the editor
    then shows: five tracks, all singing the same notes.
    """
    out, seen = [], {}
    for track in midi.tracks:
        if not track.notes:
            continue
        base = track.name or 'untitled'
        seen[base] = seen.get(base, 0) + 1
        name = base if seen[base] == 1 else '%s %d' % (base, seen[base])
        out.append((name, track))
    return out


def midi_tracks(path):
    """The tracks worth importing, as [(name, note count)]."""
    return [(name, len(track.notes))
            for name, track in _named_tracks(MidiFile.from_file(path))]


def _tempo(midi):
    for t in midi.tracks:
        if t.tempos:
            return 60e6 / sorted(t.tempos)[0][1]
    return 120.0


def quantise(beats, grid):
    """Round a length to the nearest `grid` of a beat, never below one grid.

    A performance recorded from a keyboard has lengths like 0.479 of a beat,
    which is a half note played a little short. Left alone those numbers make a
    song that cannot be edited -- every nudge would drag the note onto the grid
    anyway -- so they are rounded on the way in. `grid` of 0 leaves them be.
    """
    if not grid:
        return beats
    return max(grid, round(beats / grid) * grid)


#: The grids a length can be rounded onto. A sixteenth is the usual one, but
#: it cannot express a triplet: a third of a beat lands on a quarter of one,
#: which is a quarter of the note gone. A piece written in triplets -- a 12/8
#: ballad is the ordinary case -- loses that much from most of its notes, and
#: the part carrying the triplets then runs ahead of the rest of the song as
#: though its tempo were set too high. A twelfth of a beat holds both a
#: sixteenth (three twelfths) and a triplet eighth (four), so it is used for a
#: file whose own lengths sit on it.
SIXTEENTH_GRID = 0.25
TRIPLET_GRID = 1.0 / 12.0
#: How far off a grid a length may sit and still count as being on it. A
#: sequencer writes a note a tick or two short so the next one speaks, and
#: that much slop is well inside a sixty-fourth note; a human playing loosely
#: is not, and a loose performance is exactly what the coarser grid is for.
GRID_SLOP = 1.0 / 32.0


def off_grid(beats, grid):
    """How far a length sits from the nearest multiple of `grid`."""
    return abs(beats - quantise(beats, grid))


def choose_grid(lengths):
    """The grid to round `lengths` onto: a twelfth of a beat, or a sixteenth.

    The finer grid is taken only when the file asks for it -- when a real
    share of its lengths miss the sixteenth grid and land on the twelfth,
    which is what a triplet does and what a sloppy performance does not. A
    file that is already straight rounds exactly as it always has.
    """
    lengths = list(lengths)
    if not lengths:
        return SIXTEENTH_GRID
    triplets = [b for b in lengths
                if off_grid(b, SIXTEENTH_GRID) > GRID_SLOP
                and off_grid(b, TRIPLET_GRID) <= GRID_SLOP]
    return (TRIPLET_GRID if len(triplets) * 10 >= len(lengths)
            else SIXTEENTH_GRID)


def midi_grid(midi):
    """The grid for a whole file, so its parts cannot land on different ones."""
    div = float(midi.division or 480)
    return choose_grid(max(n.duration, 1) / div
                       for t in midi.tracks for n in t.notes)


def from_midi(path, track_name=None, rest_beats=0.25, grid=None):
    """Turn a MIDI track into editor notes.

    Returns (bpm, rows, pending), where a row is
    (phonemes, pitch, beats, word) and `pending` is [(word, [row index, ...])]
    -- words whose pronunciation the caller still has to look up.

    VocalWriter's own exports carry each note's phonemes as well as its lyric,
    so one of those comes back complete and ready to sing. Any other MIDI has
    only pitches, lengths and perhaps words. A lyric ending in a hyphen is half
    of a word, the way "count-" and "ry" are, so those are joined back together
    before being looked up and the pronunciation is divided over the notes they
    came from.

    Gaps between notes become rests, so the phrasing survives the trip.
    `rest_beats` is the shortest gap worth keeping; below it the note simply
    runs on to the next, which is how a legato line is written.

    A note that carries neither phonemes nor a word is given `DEFAULT_PHONEME`
    to sing, so an ordinary MIDI file arrives as a song that can be played.
    """
    midi = MidiFile.from_file(path)
    if grid is None:
        grid = midi_grid(midi)
    named = _named_tracks(midi)
    if not named:
        raise ValueError('this file has no notes in it')
    if track_name:
        match = [t for name, t in named if name == track_name]
        if not match:
            raise ValueError('no track named %r' % track_name)
        track = match[0]
    else:
        track = named[0][1]

    div = float(midi.division or 480)
    curve = bend_curve(track)
    # Counting from the start of the file rather than from the track's own
    # first note. A part that comes in late is written that way -- the pickup
    # in one part and not in the others is the arrangement -- and starting
    # every part at its own first note stacks them all on beat one, which puts
    # the parts out with each other and every note in the wrong bar.
    rows, cursor = [], 0
    for n in sorted(track.notes, key=lambda x: x.tick):
        gap = (n.tick - cursor) / div
        if gap >= rest_beats:
            rows.append([['%'], n.pitch, quantise(gap, grid), '', []])
        beats = quantise(max(n.duration, 1) / div, grid)
        word = (n.text or '').strip()
        if n.phonemes:
            ph = [PALETTE.get(x, x) for x in split_phonemes(n.phonemes)]
        elif word:
            ph = []                      # the lookup will fill it in
        else:
            ph = [DEFAULT_PHONEME]
        span = max(n.duration, 1)
        rows.append([ph, n.pitch, beats, word,
                     _bend_over(curve, n.tick, n.tick + span)])
        cursor = n.tick + span

    pending = _pending_words(rows)
    return (_tempo(midi), [tuple(r) for r in rows], pending, _sig(midi))


def from_midi_tracks(path, names, rest_beats=0.25, grid=None):
    """Several parts of a MIDI file at once, one editor track for each.

    Returns (bpm, time signature, [(name, rows, pending)]). The tempo and the
    signature belong to the file rather than to any one part, so the first
    part's are the song's. The grid does too: it is settled once, from every
    note in the file, or two parts of one song could be rounded onto different
    grids and drift apart.
    """
    out, bpm, sig = [], 120.0, DEFAULT_SIG
    if grid is None:
        grid = midi_grid(MidiFile.from_file(path))
    for k, name in enumerate(names):
        bpm, rows, pending, part_sig = from_midi(path, name, rest_beats, grid)
        if not k:
            sig = part_sig
        out.append((name, rows, pending))
    return bpm, sig, out


def _sig(midi):
    for t in midi.tracks:
        if getattr(t, 'time_sigs', None):
            _tick, num, den = sorted(t.time_sigs)[0]
            return (num, den)
    return DEFAULT_SIG


def bend_curve(track):
    """The track's pitch bend as [(tick, semitones)].

    MIDI's bend value is a fraction of a range that is itself set by a
    controller and can change mid-song -- VocalWriter's own exports change it
    three times. Resolving it here means everything downstream deals in
    semitones and nothing has to carry the range around.
    """
    ranges = sorted(getattr(track, 'bend_range', None) or []) or [(0, 2)]
    out = []
    for tick, value in sorted(getattr(track, 'bends', None) or []):
        span = ranges[0][1]
        for t, v in ranges:
            if t <= tick:
                span = v
            else:
                break
        out.append((tick, value / 8192.0 * span))
    return out


def _bend_over(curve, start, end):
    """The bend points inside one note, placed 0 to 1 across it.

    The value in force when the note begins is carried in as a point at 0, so a
    note that starts partway through a slide still starts on the right pitch.
    """
    span = float(max(end - start, 1))
    inside = [((t - start) / span, v) for t, v in curve if start <= t < end]
    before = [v for t, v in curve if t < start]
    held = before[-1] if before else 0.0
    if not inside and abs(held) < BEND_EPSILON:
        return []
    if not inside or inside[0][0] > 0.0:
        inside.insert(0, (0.0, held))
    return [(round(a, 4), round(v, 4)) for a, v in inside]


def timeline(notes):
    """Every note's bend gathered up, as (beat, semitones, slides).

    This is what the engine is given. It is derived from the notes each time
    rather than stored, so moving or retiming a note moves its bend with it.

    `slides` says what happens between this point and the next one: True to
    move smoothly from one to the other, False to stay put until the next one
    arrives and then change. A bend slides between the points *of one note*,
    which is what makes it a slide, and holds between notes, which is what
    makes it a note's own. Without that distinction a note bent from -4 up to 0
    went on sliding after it finished -- all the way down to the next bend
    written anywhere later in the song, dragging every note in between out of
    tune with it.

    A bend stays where it was left until something moves it, so a note with no
    bend of its own is given an explicit zero when the note before it ended
    bent. Without that, bending one note sharp would quietly sing everything
    after it sharp as well.
    """
    out, at, held = [], 0.0, 0.0
    for n in notes:
        points = list(getattr(n, 'bend', None) or ())
        if not points:
            if abs(held) >= BEND_EPSILON:
                out.append((at, 0.0, False))
                held = 0.0
        else:
            for k, (where, value) in enumerate(points):
                # the last point of a note has nothing of its own to slide to
                out.append((at + where * n.beats, value, k < len(points) - 1))
            held = points[-1][1]
        at += n.beats
    out.sort(key=lambda e: e[0])
    return out


def bend_ends(bend):
    """What a note's bend starts and ends on, or (None, None) if it has none."""
    if not bend:
        return (None, None)
    return (bend[0][1], bend[-1][1])


def with_ends(bend, start, end):
    """A curve with its endpoints set, keeping whatever was in between.

    Either end may be None, meaning leave that one as it is; both None clears
    the bend. Points between the ends are kept, so setting where a slide
    finishes does not throw away the shape of what it does on the way.
    """
    if start is None and end is None:
        return []
    middle = [(a, v) for a, v in (bend or ()) if 0.0 < a < 1.0]
    if start is None:
        start = (bend or [(0.0, 0.0)])[0][1]
    out = [(0.0, float(start))] + middle
    if end is not None:
        out.append((1.0, float(end)))
    elif bend and bend[-1][0] >= 1.0:
        out.append(bend[-1])
    return out


def describe_bend(bend):
    """How a note's bend reads in the list: blank, one value, or start to end.

    What matters about a bend is where it sets off and where it arrives, so
    that is what is shown, rather than how high or low it happens to go on the
    way. A bend that holds one value is shown as that value.
    """
    if not bend:
        return ''
    start, end = bend_ends(bend)
    if abs(end - start) >= BEND_EPSILON:
        return '%s to %s' % (_semis(start), _semis(end))
    # the ends agree, so say what it does in between rather than nothing: a
    # note that swoops up and comes back is not a note with no bend
    peak = max((v for _a, v in bend), key=lambda v: abs(v - start))
    if abs(peak - start) >= BEND_EPSILON:
        return '%s and back' % _semis(peak)
    return _semis(start) if abs(start) >= BEND_EPSILON else ''


def _semis(v):
    return '0' if abs(v) < BEND_EPSILON else '%+.3g' % v


def _pending_words(rows):
    """Which words still need a pronunciation, and which rows they cover.

    Lyrics broken across notes with hyphens are put back together, so "count-"
    followed by "ry" is looked up once, as "country".
    """
    pending, run, letters = [], [], ''
    for i, row in enumerate(rows):
        ph, word = row[0], row[3]
        if ph or not word:
            continue
        run.append(i)
        letters += word
        if word.endswith('-'):
            continue                       # the word carries on to the next note
        clean = ''.join(c for c in letters if c.isalpha() or c == "'")
        if clean:
            pending.append((clean, list(run)))
        run, letters = [], ''
    return pending


def fill(rows, word, indices, phonemes):
    """Put a looked-up pronunciation onto the notes its lyric was split over."""
    groups = phonology.regroup(phonemes, len(indices))
    while len(groups) < len(indices):      # more notes than syllables
        groups.append([])
    out = [list(r) for r in rows]
    for k, i in enumerate(indices):
        out[i][0] = groups[k] or ['%']
    return [tuple(r) for r in out]
