#!/usr/bin/env python3
"""The engine the editor asks for pronunciations and audio.

It used to be a process of its own, spoken to in JSON lines, because a render
under the PowerPC interpreter ran tens of millions of guest instructions and
took about as long as the music lasts -- something that cannot happen on the
thread drawing the window. The C engine renders a minute of singing in a few
hundredths of a second, so there is nothing left to put behind a process
boundary: this is imported and called.

What it holds is worth holding: the voice bank, the dictionary and the shared
tables are read once, and rendered audio is kept under a key made from the
song, so playing the same thing twice renders once.
"""
import hashlib
import json          # only for the cache key
import math
import os
import shutil
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import paths                                        # noqa: E402
from ppc.lexicon import open_lexicon                         # noqa: E402
from ppc.midi import syllable_lengths                        # noqa: E402
from ppc.phonology import is_nucleus, targets                # noqa: E402
from ppc.render import (SAMPLE_RATE, Note, Renderer,         # noqa: E402
                        write_wav)
from ppc.render import engine_name, open_engine              # noqa: E402
from tools.ttvi import load as load_ttvi, phoneme_order      # noqa: E402


PALETTE_FILE = paths.bundled('emu', 'phoneme_palette.json')

#: the palette was read out of the running application, and spells two
#: phonemes differently from the engine's own table
PALETTE_ALIAS = {'OH': 'O', 'DX': 'DD'}

#: What the application's own palette leaves out.
#:
#: `emu/phoneme_palette.json` was read out of VocalWriter's running interface
#: and holds the fifty phonemes it offers. Its engine has fifty-seven. The
#: other seven are real -- they have formants, manners and durations like any
#: other, and the dictionary and the letter-to-sound rules produce some of them
#: -- so leaving them out would mean a sound the engine can make that this
#: program cannot reach.
#:
#: The examples are observed rather than assumed: each one is a word the
#: dictionary or VocalWriter's own scores actually spell with that phoneme.
UNLISTED = [
    ('IX', 'rosES, beatEn'),        # lexicon: r OW z IX z, b IY t IX n
    ('Q',  'greaTer, heaTer'),      # letter-to-sound: g r EY Q ER
    ('DD', 'beTTer, whaT'),         # the scores' DX: b EH / DD ER, w UX DD
    ('TX', "iT, can'T"),            # the scores: IH TX, k AE n TX
    ('RX', 'a short r-coloured vowel'),
    ('QX', 'a longer Q'),
    ('%',  'silence, a rest'),
]

#: how long after the last note the file runs on, so its decay is not clipped
TAIL_SECONDS = 0.4

#: The metronome. This is the one sound here that is not VocalWriter's -- it
#: is a ruler held up against the singing, not part of it -- so it is mixed in
#: only when the song is played and never written into an exported file.
CLICK_HZ = 1000.0            # the beats
CLICK_ACCENT_HZ = 1600.0     # the first beat of each bar
CLICK_SECONDS = 0.035
CLICK_LEVEL = 0.22


def click(rate=SAMPLE_RATE, hz=CLICK_HZ, level=CLICK_LEVEL):
    """One tick: a tone that dies away immediately, so it reads as a tap."""
    n = int(rate * CLICK_SECONDS)
    t = np.arange(n, dtype=np.float32) / float(rate)
    return (level * np.sin(2 * np.pi * hz * t)
            * np.exp(-t * 45.0)).astype(np.float32)


def with_metronome(y, bpm, bar_beats, start=0.0, rate=SAMPLE_RATE):
    """The audio with a tick on every beat, accented at each bar line.

    `start` is the beat the audio itself begins on, so that playing from
    partway through a song still puts the ticks on the song's beats and the
    accents on its bar lines, rather than counting a fresh bar from wherever
    the cursor happened to be.
    """
    spb = 60.0 / max(bpm, 1e-6)
    plain, accent = click(rate), click(rate, CLICK_ACCENT_HZ,
                                       CLICK_LEVEL * 1.4)
    out = np.array(y, dtype=np.float32)
    n = out.shape[0]
    ticks = np.zeros(n, dtype=np.float32)
    beats = max(1, int(round(bar_beats)))             # keep 3/4, 6/8 and 7/8
    k = int(math.ceil(start - 1e-9))
    while True:
        at = int(round((k - start) * spb * rate))
        if at >= n:
            break
        tick = accent if (k % beats == 0) else plain
        end = min(at + len(tick), n)
        ticks[at:end] += tick[:end - at]
        k += 1
    out += ticks if out.ndim == 1 else ticks[:, None]
    peak = float(np.abs(out).max())
    if peak > 1.0:                        # the ticks must not push it into clip
        out /= peak
    return out

#: The bend range the engine is put into before any bend is sent. A song
#: carries its bend in semitones, so the range is ours to choose; twelve covers
#: anything a voice would do and keeps the 14-bit value comfortably fine --
#: one step is a fifth of a cent.
BEND_RANGE = 12.0


#: How often a bend in motion is sent to the engine. One frame is 220
#: samples, about 5 ms, which is as often as the engine can act on anything.
BEND_STEP = 0.005


def glide(points, step=BEND_STEP):
    """Fill in between the written points so a bend moves rather than jumps.

    A bend is written down as the places it passes through -- start here, end
    there -- but the engine is not given a curve, only individual values at
    individual moments. Two points a second apart would hold the first value
    for that whole second and then snap to the second one, which is a jump, not
    a slide. The values in between are worked out here, at the rate the engine
    can actually use them.

    Only where the point says to. A point carries a third field saying whether
    it slides into the next one or simply holds until then; filling in between
    every pair regardless is how a bend that finished on one note went on
    sliding into the next bend written anywhere later in the song.
    """
    pts = [(t, v, bool(sl)) for t, v, sl in _triples(points)]
    if len(pts) < 2:
        return [(t, v) for t, v, _sl in pts]
    out = []
    for (t0, v0, slides), (t1, v1, _) in zip(pts, pts[1:]):
        out.append((t0, v0))
        if not slides or v1 == v0 or t1 - t0 <= step:
            continue                        # a hold needs nothing in between
        n = int((t1 - t0) / step)
        for k in range(1, n):
            f = k * step / (t1 - t0)
            out.append((t0 + k * step, v0 + (v1 - v0) * f))
    out.append((pts[-1][0], pts[-1][1]))
    return out


def _triples(points):
    """Accept points with or without the slide flag; without it, they hold."""
    for pt in points:
        if len(pt) >= 3:
            yield pt[0], pt[1], pt[2]
        else:
            yield pt[0], pt[1], False


def bend_events(points, t0, t1):
    """Bend points for one phrase, in seconds from its own start.

    Everything is rebased on the phrase because each phrase is rendered on its
    own engine. Whatever bend was in force when the phrase began is carried in
    ahead of it, or a phrase starting partway through a slide would begin on
    the wrong pitch.
    """
    if not points:
        return []
    ev = [(-1.0, 'sens', int(BEND_RANGE))]
    before = [v for t, v, _sl in _triples(points) if t < t0]
    if before:
        ev.append((-1.0, 'bend', _raw(before[-1])))
    last = None
    for t, v in glide(points):
        if t0 <= t <= t1:
            raw = _raw(v)
            if raw != last:                 # sending the same value twice is
                ev.append((t - t0, 'bend', raw))   # work the engine need not do
                last = raw
    return ev


def _raw(semitones):
    """Semitones as the 14-bit number Speech_PitchBend is given."""
    v = int(round(semitones / BEND_RANGE * 8192))
    return max(-8192, min(8191, v))


def pan_gains(pan):
    """The left and right gains for a pan of -1 (hard left) to +1 (right).

    Constant power -- a track keeps its loudness as it moves across -- but
    normalised so that the middle is unity in both channels rather than the
    usual three decibels down. That way a track left in the middle sounds
    exactly as it did before there was any panning at all, and switching a
    song between one channel and two changes nothing you can hear.
    """
    theta = (max(-1.0, min(1.0, float(pan))) + 1.0) * (math.pi / 4.0)
    return (math.sqrt(2.0) * math.cos(theta),
            math.sqrt(2.0) * math.sin(theta))


#: VocalWriter's own defaults for a new song, from its reverb dialog.
DEFAULT_REVERB = (40, 24)

#: The most of a note that may be given to the note in front of it, so that a
#: note opening with consonants can start early enough for its vowel to land
#: on the beat. Half: take more than that and what is left is a grace note.
ANTICIPATE_MOST = 0.5


def onset_beats(note, bpm):
    """How long a note's opening consonants last, in beats.

    The phonemes before the first vowel: what stands between the note starting
    and the note being heard. `Syllable_Duration` scales a syllable to fill its
    note, and these durations already add up to the note, so their lengths are
    what they will be.
    """
    t = targets()
    ms = 0.0
    for sym, dur in zip(note.phonemes, note.durations or []):
        if is_nucleus(sym, t):
            break
        ms += dur
    return ms * bpm / 60000.0


def anticipate(notes, bpm, consonants, room):
    """Move every note's consonants in front of its beat, in place.

    A note is heard where its vowel is, not where it starts: "day" on beat two
    with a 60 ms /d/ in front of it is heard 60 ms after beat two, and how late
    depends on how the word is spelled -- nothing before a vowel, a little
    before /d/, a lot before "str-". That is what makes a line with hard onsets
    sound as though it is dragging, and it gets worse the longer the consonants
    are allowed to be.

    So a note that opens with consonants is started that much earlier and given
    that much more time, and the time is taken from the note in front of it,
    which is shortened by the same amount. Nothing moves: the vowels land where
    the notes were, the phrase is the same length, and the consonants are sung
    into the end of the note before -- which is what a singer does.

    `room` is how much silence there is before the phrase, since the first note
    has no note in front of it to borrow from. Returns how much earlier the
    phrase now starts.
    """
    def retime(note, beats):
        note.beats = beats
        note.durations = syllable_lengths(note.phonemes,
                                          beats * 60000.0 / bpm, consonants)

    lead = max(0.0, min(onset_beats(notes[0], bpm), room))
    if lead > 0:
        retime(notes[0], notes[0].beats + lead)
    for i in range(1, len(notes)):
        give = min(onset_beats(notes[i], bpm),
                   ANTICIPATE_MOST * notes[i - 1].beats)
        if give <= 0:
            continue
        retime(notes[i - 1], notes[i - 1].beats - give)
        retime(notes[i], notes[i].beats + give)
    return lead


def clean_reverb(values):
    """(room, wet) as whole percentages, clamped. None is no reverb at all.

    Two numbers, because they are the two the application has: the room scales
    the four delay lines and the wet is how much of the result is heard, the
    dry part being what is left.
    """
    if not values:
        return (0, 0)
    try:
        room = int(round(float(values.get('room', DEFAULT_REVERB[0]))))
        wet = int(round(float(values.get('wet', DEFAULT_REVERB[1]))))
    except (AttributeError, TypeError, ValueError):
        return (0, 0)
    return (max(0, min(100, room)), max(0, min(100, wet)))


def tracks_of(song):
    """A song's parts, whichever way it was written down.

    A song used to be one voice and one list of notes, and single notes are
    still asked for that way when one is previewed. Both shapes arrive here as
    a list of parts so that nothing further down has to know the difference.
    """
    parts = song.get('tracks')
    if parts:
        return parts
    return [{'program': song.get('program', 0),
             'notes': song.get('notes') or [],
             'bends': song.get('bends') or [],
             'velocity': song.get('velocity', 64)}]


def is_rest(phonemes):
    """A note nobody sings: no phonemes, or nothing but silence."""
    return not phonemes or all(p == '%' for p in phonemes)


def phrases(entries):
    """[(start in beats, [note...])] between the rests, and the total length.

    The rests themselves are dropped -- they are not sung, they are the gaps
    the phrases are placed around.
    """
    runs, cur, start, at = [], [], 0.0, 0.0
    for e in entries:
        beats = float(e.get('beats', 0.5))
        if is_rest(e.get('phonemes')):
            if cur:
                runs.append((start, cur))
                cur = []
        else:
            if not cur:
                start = at
            cur.append(e)
        at += beats
    if cur:
        runs.append((start, cur))
    return runs, at


class Engine(object):
    def __init__(self):
        self._lex = None
        self._voices = None
        self._palette = None
        #: key -> length in seconds. The audio itself lives in `cache_dir`
        #: under the key, never at the caller's path: callers reuse paths (the
        #: note preview always writes the same file), so an entry pointing at
        #: one would go on claiming a hit after a later render had overwritten
        #: it, and hand back the wrong audio.
        self._cache = {}
        self.cache_dir = os.path.join(tempfile.gettempdir(), 'vocalwriter-cache')

    @property
    def lex(self):
        if self._lex is None:
            self._lex = open_lexicon()
        return self._lex

    def ping(self):
        """What is running, for the window to say out loud."""
        return {'engine': engine_name(),
                'python': '.'.join(str(v) for v in sys.version_info[:3])}

    def phonemes(self, words):
        out = {}
        for w in words:
            clean = ''.join(c for c in w if c.isalpha() or c == "'")
            out[w] = (self.lex.phonemes(clean) or []) if clean else []
        return out

    def voices(self):
        """Every voice the bank holds, in its own order.

        Not the sixteen a program change reaches: the bank has 87, and the
        ones with instrument names sing lyrics as readily as the ones with
        people's names -- "special synthetic models of musical instruments
        with dynamic vocal tracts", as the manual has it. Five more sit in the
        bank with nothing in the program map pointing at them at all.
        """
        if self._voices is None:
            eng = open_engine()
            self._voices = [n for n in eng.voice_names() if n]
            eng.close()
        return self._voices

    def program_voices(self, programs):
        """Which voice each program number picks, for reading an old song."""
        eng = open_engine()
        out = [eng.program_voice(int(p)) for p in programs]
        eng.close()
        return out

    def palette(self):
        """Every phoneme the engine has, with an example word for each.

        The engine's own table is what decides the list, so nothing it can
        pronounce is missing. The application's palette supplies the example
        words for the ones it shows, and anything left over is filled in from
        UNLISTED and, failing that, listed bare rather than dropped.
        """
        if self._palette is None:
            examples = dict(UNLISTED)
            try:
                with open(PALETTE_FILE) as fh:
                    for sym, example in json.load(fh):
                        examples[PALETTE_ALIAS.get(sym, sym)] = example
            except (OSError, ValueError):
                pass
            order = phoneme_order(load_ttvi())
            self._palette = [[sym, examples.get(sym, '')] for sym in order]
            for sym, example in UNLISTED:    # in the table under another name
                if sym not in order:
                    self._palette.append([sym, example])
        return self._palette

    def preview(self, phoneme, pitch=60, program=0, beats=0.45, out=None):
        """Render one phoneme on its own, for the picker's Preview button."""
        song = {'bpm': 120, 'program': program,
                'notes': [{'pitch': pitch, 'beats': beats,
                           'phonemes': [phoneme]}]}
        if out is None:
            import tempfile
            out = os.path.join(tempfile.gettempdir(),
                               'vw_preview_%s_%d.wav'
                               % (phoneme.replace('%', 'rest'), pitch))
        return self.render(song, out)

    def render(self, song, out):
        # The metronome is mixed on afterwards and is deliberately left out of
        # the key, so switching it on and off never re-sings the song.
        metro = song.get('metronome') or None
        core = {k: v for k, v in song.items() if k != 'metronome'}
        # A render costs seconds, so never repeat one. The key covers
        # everything that changes the samples.
        key = hashlib.sha256(
            json.dumps(core, sort_keys=True).encode()).hexdigest()
        kept = os.path.join(self.cache_dir, key + '.wav')
        hit = self._cache.get(key)
        if hit is not None and os.path.isfile(kept):
            # A hit still has to deliver to the caller's filename, or saving a
            # song that has already been played writes nothing and reports
            # success.
            seconds, peak = hit
            return {'seconds': seconds, 'peak': peak, 'cached': True,
                    'path': self._deliver(kept, out, metro, core)}

        y, peak = self._samples(core)
        seconds = len(y) / float(SAMPLE_RATE)
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            write_wav(kept, y)
            self._cache[key] = (seconds, peak)
            where = self._deliver(kept, out, metro, core)
        except OSError:                      # no cache: still give the caller
            write_wav(out, self._ticked(y, metro, core))   # the audio anyway
            where = out
        return {'seconds': seconds, 'peak': peak, 'path': where,
                'cached': False}

    @staticmethod
    def _ticked(y, metro, song):
        if not metro:
            return y
        return with_metronome(y, float(song.get('bpm', 120)),
                              float(metro.get('bar', 4)),
                              float(song.get('start', 0.0)))

    def _samples(self, song):
        """Mix every track of a song, and say how loud the result came out.

        Returns (samples, peak). The samples are one column if every track sits
        in the middle with no reverb on it, and two otherwise, so a song that
        uses neither is the same file it always was.

        Reverb is a property of the song that a part may take over, like the
        consonant length. Parts are therefore grouped by the reverb they end up
        with, each group mixed and reverberated on its own, and the groups
        added together -- so two parts in the same room go through one
        reverberator and share its tail, and a part in a room of its own is not
        dragged into theirs.
        """
        bpm = float(song.get('bpm', 120))
        consonants = float(song.get('consonants', 1.0))
        start = float(song.get('start', 0.0))
        early = song.get('anticipate', True)
        tracks = tracks_of(song)
        if not any(t.get('notes') for t in tracks):
            raise ValueError('nothing to sing')
        song_reverb = clean_reverb(song.get('reverb'))
        groups = {}
        for t in tracks:
            y = self._track(t, bpm, consonants, start, early)
            vol = float(t.get('volume', 1.0))
            left, right = pan_gains(t.get('pan', 0.0))
            own = t.get('reverb')
            rev = song_reverb if own is None else clean_reverb(own)
            groups.setdefault(rev, []).append((y, vol * left, vol * right))
        parts = [p for group in groups.values() for p in group]
        n = max(len(y) for y, _l, _r in parts)
        stereo = (any(abs(float(t.get('pan', 0.0))) > 1e-6 for t in tracks)
                  or any(wet > 0 for _room, wet in groups))
        if stereo:
            mixes = []
            for rev, group in groups.items():
                mix = np.zeros((n, 2), dtype=np.float32)
                for y, gl, gr in group:
                    mix[:len(y), 0] += y * gl
                    mix[:len(y), 1] += y * gr
                mixes.append((rev, mix))
            # Several voices at once can add up past full scale. Turning the
            # mix down is a great deal better than clipping it -- and it has
            # to happen before the reverb, which works on 16-bit samples and
            # would clip whatever it was handed.
            peak = float(np.abs(sum(m for _r, m in mixes)).max()) if n else 0.0
            if peak > 1.0:
                for _rev, mix in mixes:
                    mix /= peak
            done = [self._reverberate(mix, rev) for rev, mix in mixes]
            # a reverb tail makes its group longer than the singing
            out = np.zeros((max(len(d) for d in done), 2), dtype=np.float32)
            for d in done:
                out[:len(d)] += d
            return out, peak
        out = np.zeros(n, dtype=np.float32)
        for y, gl, _gr in parts:          # in the middle both gains are the
            out[:len(y)] += y * gl        # volume, so one channel says it all
        peak = float(np.abs(out).max()) if n else 0.0
        if peak > 1.0:
            # the caller is told the number, so that it can say so rather than
            # leaving someone wondering why the song got quieter
            out /= peak
        return out, peak

    def _reverberate(self, mix, reverb):
        """One group's mix through the engine's own reverb.

        The reverberator works on the 16-bit samples the engine writes, 220
        frames at a time, so the mix goes to 16 bits and back. That is what the
        application does with its own sound buffers; going through floats
        instead would be a different reverb.

        The tail is what the last block leaves behind, so the mix is given a
        second of room to decay into and the silence trimmed back off.
        """
        room, wet = reverb
        if wet <= 0 or not len(mix):
            return mix
        eng = open_engine()
        try:
            if not eng.reverb(room / 100.0, wet / 100.0):
                return mix
            tail = np.zeros((SAMPLE_RATE, 2), dtype=np.float32)
            padded = np.concatenate([mix, tail])
            block = 220
            frames = (len(padded) // block) * block
            pcm = np.clip(padded[:frames], -1.0, 1.0)
            pcm = (pcm * 32767).astype('<i2').reshape(-1)
            eng.reverberate(pcm)
            wet_mix = pcm.reshape(-1, 2).astype(np.float32) / 32767.0
        finally:
            eng.close()
        # keep whatever of the tail is not silence, so a reverb longer than a
        # second is not cut off, and a short one does not pad the file
        loud = np.abs(wet_mix).max(axis=1) > (1.0 / 32767.0)
        last = int(np.nonzero(loud)[0][-1]) + 1 if loud.any() else len(mix)
        return wet_mix[:max(len(mix), min(last, len(wet_mix)))]

    def _track(self, track, bpm, consonants, start=0.0, early=True):
        """One track's audio, phrase by phrase, laid out on the beat.

        Rests are not sung. Each run of notes between them goes to its own
        engine and is placed at the time the score puts it, for two reasons.
        A single continuous render drifts -- a resonator state grows until,
        after something like twenty seconds, it reaches infinity and the output
        pins to full scale -- and it also has no way to hold a silence, because
        the engine only ever plays the next note it is handed. Cutting at a
        silence costs nothing: there is no sound there to cut.

        `start` is where playing begins, in beats. A phrase that is over by
        then is not rendered at all; one already under way is rendered whole
        and has its beginning cut off, so a note the cursor lands in the middle
        of is heard from the middle rather than being retriggered or lost.
        """
        spb = 60.0 / max(bpm, 1e-6)
        vel = int(track.get('velocity', 64))
        program = int(track.get('program', 0))
        voice_id = track.get('voice_id')
        voice = track.get('voice') or None
        # A part may set its own consonant length; without one it follows the
        # project's, which is what the setting in the song dialog is.
        own = track.get('consonants')
        if own is not None:
            try:
                consonants = float(own)
            except (TypeError, ValueError):
                pass
        # [(beat, semitones, slides into the next)], in the song's own time
        bends = sorted(_triples(track.get('bends') or []))
        runs, total = phrases(track.get('notes') or [])
        length = max(0.0, total - start) * spb + TAIL_SECONDS
        out = np.zeros(int(round(length * SAMPLE_RATE)), dtype=np.float32)
        was_over = 0.0                   # where the phrase before this ended
        for at, run in runs:
            notes = []
            for e in run:
                ph = e.get('phonemes') or ['%']
                beats = float(e.get('beats', 0.5))
                notes.append(Note(int(e['pitch']), beats, ph,
                                  velocity=int(e.get('velocity', vel)),
                                  durations=syllable_lengths(
                                      ph, beats * 60000.0 / bpm, consonants)))
            lead = (anticipate(notes, bpm, consonants, at - was_over)
                    if early else 0.0)
            was_over = at + sum(n.beats for n in notes) - lead
            # a marked rest to scale the last syllable against, and to let it
            # decay rather than being cut off
            notes.append(Note(notes[-1].midi, 0.4, ['%'], velocity=1))
            at -= lead                   # the first note's consonants
            span = sum(n.beats for n in notes)
            if at + span <= start:
                continue                 # over and done with before the cursor
            ev = bend_events([(t * spb, v, sl) for t, v, sl in bends],
                             at * spb, (at + span) * spb)
            # render_live is what applies the bends; with no events it produces
            # the same samples as render, checked against it
            y = Renderer(program=program, bpm=bpm, voice=voice,
                         voice_id=voice_id).render_live(
                notes, [0] * len(notes), ev, lambda _t: bpm)
            i = int(round((at - start) * spb * SAMPLE_RATE))
            if i < 0:                     # the phrase began before the cursor
                y = y[-i:]
                i = 0
            if not len(y):
                continue
            if i + len(y) > len(out):
                out = np.concatenate(
                    [out, np.zeros(i + len(y) - len(out), dtype=np.float32)])
            out[i:i + len(y)] += y
        return out

    @classmethod
    def _deliver(cls, kept, out, metro=None, song=None):
        """Put the audio where the caller asked, and say where it ended up.

        Windows refuses to write a file that something else has open, and the
        thing most likely to have this one open is the player that just played
        it. Rather than fail the whole render over that, the caller is handed
        the copy in the cache, which is the same audio.
        """
        try:
            if metro:
                with wave.open(kept) as w:
                    channels = w.getnchannels()
                    y = (np.frombuffer(w.readframes(w.getnframes()), '<i2')
                         .astype(np.float32) / 32768.0)
                if channels > 1:
                    y = y.reshape(-1, channels)
                write_wav(out, cls._ticked(y, metro, song or {}))
            elif os.path.abspath(kept) != os.path.abspath(out):
                shutil.copyfile(kept, out)
            return out
        except OSError:
            return kept


if __name__ == '__main__':
    # a quick look at the engine from the command line
    eng = Engine()
    print(engine_name())
    print('voices:', ', '.join(eng.voices()[:6]), '...')
    print('daisy:', eng.phonemes(['daisy']))
