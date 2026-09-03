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
                 consonants=None):
        self.name = name
        self.program = int(program)
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


def save(path, bpm, tracks, sig=DEFAULT_SIG, consonants=1.0, voice=None):
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
    """Read a project: (bpm, tracks, time signature, consonants, voice).

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
            clean_voice(doc.get('voice')))


def _read_track(doc, index=0):
    con = doc.get('consonants')
    try:
        con = None if con is None else float(con)
    except (TypeError, ValueError):
        con = None
    return {'name': doc.get('name') or ('Voice %d' % (index + 1)),
            'voice': (clean_voice(doc['voice']) if 'voice' in doc else None),
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

def midi_tracks(path):
    """The tracks worth importing, as [(name, note count)]."""
    midi = MidiFile.from_file(path)
    return [(t.name or 'untitled', len(t.notes))
            for t in midi.tracks if t.notes]


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


def from_midi(path, track_name=None, rest_beats=0.25, grid=0.25):
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
    """
    midi = MidiFile.from_file(path)
    tracks = [t for t in midi.tracks if t.notes]
    if not tracks:
        raise ValueError('this file has no notes in it')
    if track_name:
        match = [t for t in tracks if (t.name or 'untitled') == track_name]
        if not match:
            raise ValueError('no track named %r' % track_name)
        tracks = match
    track = tracks[0]

    div = float(midi.division or 480)
    curve = bend_curve(track)
    rows, cursor = [], None
    for n in sorted(track.notes, key=lambda x: x.tick):
        gap = 0.0 if cursor is None else (n.tick - cursor) / div
        if gap >= rest_beats:
            rows.append([['%'], n.pitch, quantise(gap, grid), '', []])
        beats = quantise(max(n.duration, 1) / div, grid)
        word = (n.text or '').strip()
        ph = ([PALETTE.get(x, x) for x in split_phonemes(n.phonemes)]
              if n.phonemes else [])
        span = max(n.duration, 1)
        rows.append([ph, n.pitch, beats, word,
                     _bend_over(curve, n.tick, n.tick + span)])
        cursor = n.tick + span

    pending = _pending_words(rows)
    return (_tempo(midi), [tuple(r) for r in rows], pending, _sig(midi))


def from_midi_tracks(path, names, rest_beats=0.25, grid=0.25):
    """Several parts of a MIDI file at once, one editor track for each.

    Returns (bpm, time signature, [(name, rows, pending)]). The tempo and the
    signature belong to the file rather than to any one part, so the first
    part's are the song's.
    """
    out, bpm, sig = [], 120.0, DEFAULT_SIG
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
