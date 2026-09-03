#!/usr/bin/env python3
"""Sing a VocalWriter MIDI export through VocalWriter's own synthesiser.

The application's export carries everything the engine needs: the notes, the
phonemes it derived for each syllable, the tempo map, the program changes and
the pitch bends (including the RPN 0 bend-range changes it makes mid-song).
This feeds all of that to the original PowerPC code.

    python -m ppc.midi out/Daisy_export.mid --track HAL -o out/daisy.wav

Two symbols in the export are palette names rather than the engine's internal
ones, and the tables say which is which:

    OH -> O    F1 550 / F2 920, a back rounded vowel; the export's OW has no
               formants of its own because it is a diphthong alias.
    DX -> DD   F1 200, manner 3, burst 13, and voiced at 50 dB where plain `d`
               is 0 -- the alveolar flap, as in "bet-ter".
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc.render import (BRIGHTNESS, SAMPLE_RATE, SAMPLES_PER_FRAME,  # noqa: E402
                        Note, Renderer, write_wav)
from ppc.phonology import is_nucleus, targets                  # noqa: E402
from tools.smf import MidiFile, split_phonemes                  # noqa: E402

PALETTE = {'OH': 'O', 'DX': 'DD'}

#: How far a coda consonant is cut for a given setting of the opening one.
#:
#: The two are tied together so that there is one control rather than two, and
#: the line is drawn through the two settings that are known to work: 1.0,
#: which is the natural length and the default, and 0.40, at which the codas
#: sit at 0.70. That second point is not a guess -- it is the setting of a
#: render the user picked out by ear.
CONSONANT_CODA = 0.5

#: How far the setting may be taken. Below this the consonants stop sounding
#: like consonants.
CONSONANT_MIN = 0.1


def consonant_factors(consonants):
    """(opening, closing) scale on the consonant lengths."""
    onset = max(CONSONANT_MIN, min(1.0, float(consonants)))
    return onset, 1.0 - (1.0 - onset) * CONSONANT_CODA


#: Longest run of score rendered as one piece, and how many notes of context
#: are rendered before it and discarded.
#:
#: Splitting is off. It is tempting -- wall time is set by the longest phrase,
#: so one 10-second phrase pins a whole render while the other cores idle --
#: but the engine carries far more state across a note boundary than the
#: filter memory. Cutting a phrase into 2.5 s pieces and rendering each on a
#: fresh engine produced audio differing from the correct render by *more than
#: the signal itself* (error rms 2835 against a signal of 2462), with the level
#: stepping 60 dB at the joins, and it is plainly audible as chopped-up speech.
#: Two notes of discarded lead-in did not fix it either, and cost 56% more CPU.
#: Speed has to come from the interpreter, which cannot change the output.
CHUNK_SECONDS = 1e9
LEAD_NOTES = 0

def syllable_lengths(syms, ms, consonants=1.0):
    """Split a note's time over its phonemes the way singing does.

    `Syllable_Duration` scales every phoneme in a syllable by the same factor
    to make it fill the note, so whatever is handed to it sets the proportions
    outright. Handing it the table's plain durations stretches the consonants
    along with the vowel: "Dai-" over a 947 ms note gives /d/ a 213 ms closure,
    which is 0.2 s of silence at the start of the word and is what made the
    phrasing sound lopsided.

    A held note lengthens the vowel and leaves the consonants alone, so once
    there is room to spare the consonants sit at the shortest length the tables
    allow and the nuclei divide everything that is left. Below that there is no
    room to spare and everything compresses together.

    The consonants used to stop at their *maximum* instead, which is what made
    the singing feel loose. Measured against VocalWriter's own render of the
    same score, the consonants that open a note run 57 ms there; that rule gave
    them 103 ms and this one 67. Since the vowel is where the beat is heard,
    the extra 46 ms put every note late by an amount that depended on how it
    was spelled -- nothing before a vowel, 80 ms before a /d/, 200 ms before
    "str-" -- so the pulse wandered from word to word. Note for note against
    the original the spread of that error falls from 61 ms to 51.

    `consonants` shortens them further, 1.0 being the natural length. It is one
    number because the two ends of a syllable do not want the same treatment:
    the consonant that opens a note delays the vowel and so delays the beat,
    while the one that closes it only shortens the vowel, so the opening is cut
    twice as hard. See CONSONANT_CODA.
    """
    t = targets()
    mx = [float(t.get(s, {}).get('max_ms') or 80) for s in syms]
    mn = [float(t.get(s, {}).get('min_ms') or 40) for s in syms]
    if sum(mn) <= 0:
        return list(mn)
    nuclei = [i for i, s in enumerate(syms) if is_nucleus(s, t)]
    if not nuclei:                       # no vowel: stretch the longest phoneme
        nuclei = [max(range(len(syms)), key=lambda i: mx[i] - mn[i])]
    first = nuclei[0]
    onset, coda = consonant_factors(consonants)
    #: the shortest each phoneme is allowed to be
    floor = [mn[i] if i in nuclei else mn[i] * (onset if i < first else coda)
             for i in range(len(syms))]
    # what the syllable wants: every vowel at full length, every consonant at
    # its shortest
    want = [mx[i] if i in nuclei else floor[i] for i in range(len(syms))]
    smin, total = sum(floor), sum(want)
    if ms <= smin:
        k = ms / smin if smin else 1.0
        return [v * k for v in floor]
    if ms <= total:
        k = (ms - smin) / (total - smin) if total > smin else 0.0
        return [b + k * (a - b) for a, b in zip(want, floor)]
    extra = (ms - total) / len(nuclei)
    out = list(want)
    for i in nuclei:
        out[i] += extra
    return out


FRAME_SECONDS = SAMPLES_PER_FRAME / float(SAMPLE_RATE)


def tempo_marks(midi):
    """[(tick, microseconds per quarter, seconds at that tick)]."""
    tempos = []
    for t in midi.tracks:
        if t.tempos:
            tempos = sorted(t.tempos)
            break
    if not tempos or tempos[0][0] != 0:
        tempos = [(0, 500000)] + tempos
    marks, elapsed = [], 0.0
    for i, (tick, us) in enumerate(tempos):
        if i:
            ptick, pus = tempos[i - 1]
            elapsed += (tick - ptick) / float(midi.division) * (pus / 1e6)
        marks.append((tick, us, elapsed))
    return marks


def make_clock(midi):
    marks = tempo_marks(midi)
    div = float(midi.division)

    def at(tick):
        lo = marks[0]
        for mk in marks:
            if mk[0] <= tick:
                lo = mk
            else:
                break
        return lo

    def seconds(tick):
        tick_, us, el = at(tick)
        return el + (tick - tick_) / div * (us / 1e6)

    def bpm(tick):
        return 60e6 / at(tick)[1]

    return seconds, bpm


def timeline(track, seconds):
    """Controller changes as [(seconds, kind, value)], in time order."""
    ev = [(seconds(t), 'bend', v) for t, v in sorted(track.bends)]
    ev += [(seconds(t), 'sens', v) for t, v in sorted(track.bend_range)]
    ev += [(seconds(t), 'program', v) for t, v in sorted(track.programs)]
    ev.sort(key=lambda e: (e[0], e[1] != 'sens'))   # sensitivity before bends
    return ev


def build_notes(track, midi, limit=None):
    """Notes plus explicit rests, with durations in beats."""
    div = float(midi.division)
    notes = sorted(track.notes, key=lambda n: n.tick)
    if limit:
        notes = notes[:limit]
    out, cursor = [], 0
    for n in notes:
        if n.tick > cursor:
            out.append(Note(n.pitch, (n.tick - cursor) / div, ['%'], velocity=1))
        syms = [PALETTE.get(s, s) for s in split_phonemes(n.phonemes)] \
            if n.phonemes else ['%']
        out.append(Note(n.pitch, max(n.duration, 1) / div, syms,
                        velocity=max(n.velocity, 1)))
        cursor = n.tick + n.duration
    return out, notes


def segment(raw, sec, gap=0.30):
    """Split notes into phrases at rests, returning [(start_seconds, notes)].

    Each phrase is rendered on a fresh engine, and what that is for is the
    timing: a rest is a silence of an exact length, and a continuous render
    has no way to hold one, so it compresses them and the song drifts off the
    beat.

    It used to be a workaround for something else as well -- a long render
    pinned to full scale after about twenty seconds -- but that was the
    engine's output buffer overrunning at 23.8 seconds and writing over its
    own state, not a filter running away. The buffer grows now.
    """
    groups, cur = [], []
    cursor = None
    for n in sorted(raw, key=lambda x: x.tick):
        if cur and sec(n.tick) - cursor >= gap:
            groups.append(cur)
            cur = []
        cur.append(n)
        cursor = sec(n.tick + n.duration)
    if cur:
        groups.append(cur)
    return groups


def _pick_track(midi, track_name):
    tracks = [t for t in midi.tracks if t.notes]
    if track_name:
        tracks = [t for t in tracks if t.name == track_name]
        if not tracks:
            raise SystemExit('no track named %r' % track_name)
    return tracks[0]


def _program_at(track, tick, default):
    p = default
    for t, v in sorted(track.programs):
        if t <= tick:
            p = v
        else:
            break
    return p


#: Extra time to hold the last note of a phrase past where the score ends it.
#:
#: Zero, and deliberately. VocalWriter's own render does go on sounding for a
#: median 0.34 s after the written note, but that is a note-off *decay* -- its
#: level falls from 616 to 188 across it -- and not a held note. Sustaining the
#: note instead keeps it at full level on whatever pitch its bend had reached
#: (158 Hz against the original's 120 on the opening phrase) and then cuts it
#: off abruptly, which is audibly worse than ending on time. The engine's own
#: decay after note-off is much shorter than the application's; the difference
#: is the stereo processing the application adds on top, which is not in the
#: synthesiser and cannot be recovered from it.
PHRASE_RELEASE = 0.0


def _phrase_notes(notes_in, midi, seconds, bpm, tail=True, consonants=1.0):
    """Notes for a run of the score, sung legato, with per-phoneme lengths."""
    div = float(midi.division)
    notes, starts = [], []
    for k, n in enumerate(notes_in):
        # Sing through to the next note rather than inserting a silent rest for
        # the small gaps the score leaves inside a phrase; the original holds
        # the line across them, and cutting to silence is what made it choppy.
        if k + 1 < len(notes_in):
            end = notes_in[k + 1].tick
        else:
            end = n.tick + n.duration
        ticks = max(end - n.tick, 1)
        beats = ticks / div
        ms = beats * 60000.0 / max(bpm(n.tick), 1e-6)
        syms = [PALETTE.get(x, x) for x in split_phonemes(n.phonemes)] \
            if n.phonemes else ['%']
        notes.append(Note(n.pitch, beats, syms, velocity=max(n.velocity, 1),
                          durations=syllable_lengths(syms, ms, consonants)))
        starts.append(n.tick)
    if tail:
        last = notes_in[-1]
        notes.append(Note(last.pitch, 0.35, ['%'], velocity=1))
        starts.append(last.tick + last.duration)
    return notes, starts


def _phrase_events(events, t0, t1):
    """Controller events for a run, rebased to its own start.

    Nothing past the last written note is applied. A phrase goes on sounding
    after that, but the bends that follow belong to the *next* phrase, and
    letting them reach a note still ringing bends it away underneath.
    """
    sub = [(e[0] - t0, e[1], e[2]) for e in events if t0 - 0.001 <= e[0] <= t1]
    head = []
    for kind in ('sens', 'bend'):
        prior = [e for e in events if e[0] < t0 and e[1] == kind]
        if prior:
            head.append((-1.0, kind, prior[-1][2]))
    head.sort(key=lambda e: e[1] != 'sens')
    return head + sub


def chunk_plan(groups, seconds, max_seconds=CHUNK_SECONDS, lead=LEAD_NOTES):
    """Cut the phrases into pieces small enough to spread over the cores.

    Wall time is set by the longest single piece, not by the total, so one
    10-second phrase pins a whole render while the other cores sit idle. Each
    piece is rendered with a few notes of preceding context whose audio is
    thrown away: that leaves the resonators and the amplitude ramp in the state
    a continuous render would have reached, so the joins are inaudible. The
    engine's own decay is on the order of milliseconds, far shorter than the
    lead-in, and every note re-derives its own controls.
    """
    plan = []
    for gi, grp in enumerate(groups):
        i = 0
        while i < len(grp):
            j = i
            t0 = seconds(grp[i].tick)
            while (j + 1 < len(grp) and
                   seconds(grp[j + 1].tick + grp[j + 1].duration) - t0 <= max_seconds):
                j += 1
            plan.append((grp[max(0, i - lead):i], grp[i:j + 1],
                         j + 1 >= len(grp)))
            i = j + 1
    return plan


def _render_chunk(job):
    """Render one chunk. Runs in a worker process, so it rebuilds its inputs."""
    path, track_name, default_program, brightness, ci, consonants = job
    midi = MidiFile.from_file(path)
    seconds, bpm = make_clock(midi)
    track = _pick_track(midi, track_name)
    raw = sorted(track.notes, key=lambda x: x.tick)
    lead, real, is_last = chunk_plan(segment(raw, seconds), seconds)[ci]
    run = list(lead) + list(real)
    notes, starts = _phrase_notes(run, midi, seconds, bpm, tail=is_last,
                                  consonants=consonants)
    t_lead = seconds(run[0].tick)
    t_real = seconds(real[0].tick)
    t1 = seconds(run[-1].tick + run[-1].duration)
    ev = _phrase_events(timeline(track, seconds), t_lead, t1)
    r = Renderer(program=_program_at(track, run[0].tick, default_program),
                 bpm=max(10, min(250, int(round(bpm(run[0].tick))))),
                 brightness=brightness)
    mark = []
    y = r.render_live(notes, starts, ev, bpm, verbose=False, mark=mark)
    cut = mark[len(lead)] if lead and len(mark) > len(lead) else 0
    return ci, t_real, y[cut:]


def render(path, track_name=None, program=None, limit=None, verbose=True,
           brightness=BRIGHTNESS, jobs=None, consonants=1.0):
    midi = MidiFile.from_file(path)
    seconds, bpm = make_clock(midi)
    track = _pick_track(midi, track_name)
    raw = sorted(track.notes, key=lambda x: x.tick)
    if limit:
        raw = raw[:limit]
    groups = segment(raw, seconds)
    plan = chunk_plan(groups, seconds)
    default_program = program
    if default_program is None:
        default_program = track.programs[0][1] if track.programs else 0

    if jobs is None:
        jobs = min(len(plan), os.cpu_count() or 1)
    if verbose:
        print('%s: track %r, %d notes, %d phrases -> %d chunks, %d workers'
              % (os.path.basename(path), track.name, len(raw), len(groups),
                 len(plan), jobs), flush=True)

    total = seconds(max(n.tick + n.duration for n in raw)) + 1.0
    out = np.zeros(int(total * SAMPLE_RATE) + SAMPLE_RATE, dtype=np.float32)
    todo = [(path, track.name, default_program, brightness, ci, consonants)
            for ci in range(len(plan))]

    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(_render_chunk, todo))
    else:
        results = [_render_chunk(j) for j in todo]

    for ci, t0, y in sorted(results):
        at = int(round(t0 * SAMPLE_RATE))
        end = min(at + len(y), len(out))
        out[at:end] += y[:end - at]
    if verbose:
        print('   %d chunks, longest %.2f s of audio'
              % (len(results), max(len(y) for _, _, y in results) / float(SAMPLE_RATE)),
              flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('midi')
    ap.add_argument('--track')
    ap.add_argument('--program', type=int)
    ap.add_argument('--limit', type=int, help='only the first N notes')
    ap.add_argument('-j', '--jobs', type=int,
                    help='parallel phrase renders (default: one per core)')
    ap.add_argument('--brightness', type=float, default=BRIGHTNESS,
                    help='scale on the radiation shelf (see ppc/render.py)')
    ap.add_argument('--consonants', type=float, default=1.0,
                    help='consonant length, 1.0 natural, 0.4 clipped')
    ap.add_argument('-o', '--out', default='out/ppc_midi.wav')
    a = ap.parse_args()
    y = render(a.midi, a.track, a.program, a.limit,
               brightness=a.brightness, jobs=a.jobs, consonants=a.consonants)
    write_wav(a.out, y)
    print('%.2f s at %d Hz -> %s' % (len(y) / float(SAMPLE_RATE), SAMPLE_RATE,
                                     a.out))


if __name__ == '__main__':
    main()
