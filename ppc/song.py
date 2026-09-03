#!/usr/bin/env python3
"""Sing typed words: text in, audio out.

This joins the two halves. `ppc/lexicon.py` runs the application's own
dictionary and letter-to-sound rules to turn words into phonemes, and
`ppc/render.py` runs its synthesiser to turn phonemes and notes into samples.
Nothing about the pronunciation or the sound is invented here.

    python -m ppc.song "hello there" --pitches 67,64 -o out/hello.wav

A note takes one word. Splitting a long word over several notes needs syllable
boundaries, which the lexicon reports only as a stress position, so that waits
for the editor -- where a singer would place the split by hand anyway.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc.lexicon import open_lexicon                         # noqa: E402
from ppc.midi import syllable_lengths                        # noqa: E402
from ppc.render import (SAMPLE_RATE, Note, Renderer,         # noqa: E402
                        write_wav)

NOTE_NAMES = {'c': 0, 'd': 2, 'e': 4, 'f': 5, 'g': 7, 'a': 9, 'b': 11}


def parse_pitch(tok):
    """A MIDI number, or a name like C4 / F#3 / Bb5."""
    tok = tok.strip()
    if not tok:
        raise ValueError('empty pitch')
    if tok.lstrip('-').isdigit():
        return int(tok)
    step = NOTE_NAMES.get(tok[0].lower())
    if step is None:
        raise ValueError('bad pitch %r' % tok)
    i = 1
    while i < len(tok) and tok[i] in '#b':
        step += 1 if tok[i] == '#' else -1
        i += 1
    return 12 * (int(tok[i:]) + 1) + step


class Singer(object):
    """Turns words and notes into audio, reusing one loaded engine."""

    #: The application's own scores sit around 52-55; 100 clips.
    VELOCITY = 64

    def __init__(self, program=0, bpm=120, velocity=VELOCITY):
        self.lex = open_lexicon()
        self.program = program
        self.bpm = bpm
        self.velocity = velocity

    def phonemes(self, word):
        clean = ''.join(c for c in word if c.isalpha() or c == "'")
        return self.lex.phonemes(clean) if clean else None

    def notes(self, words, pitches, beats):
        out = []
        for k, w in enumerate(words):
            ph = self.phonemes(w) or ['%']
            b = beats[k] if k < len(beats) else beats[-1]
            ms = b * 60000.0 / self.bpm
            out.append(Note(pitches[k], b, ph, velocity=self.velocity,
                            durations=syllable_lengths(ph, ms)))
        # a rest, so the last note is scaled against a following note-start
        out.append(Note(pitches[-1], 0.4, ['%'], velocity=1))
        return out

    def sing(self, words, pitches, beats):
        return Renderer(program=self.program,
                        bpm=self.bpm).render(self.notes(words, pitches, beats))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('text')
    ap.add_argument('--pitches', default='60',
                    help='comma separated, MIDI numbers or names (C4, F#3)')
    ap.add_argument('--beats', default='0.5', help='comma separated')
    ap.add_argument('--bpm', type=float, default=120)
    ap.add_argument('--program', type=int, default=0)
    ap.add_argument('--velocity', type=int, default=Singer.VELOCITY)
    ap.add_argument('-o', '--out', default='out/sung.wav')
    a = ap.parse_args()

    words = a.text.split()
    pitches = [parse_pitch(t) for t in a.pitches.split(',')]
    beats = [float(t) for t in a.beats.split(',')]
    while len(pitches) < len(words):
        pitches.append(pitches[-1])

    s = Singer(program=a.program, bpm=a.bpm, velocity=a.velocity)
    for w in words:
        p = s.phonemes(w)
        print('  %-14s %s' % (w, ' '.join(p) if p else '(no pronunciation)'))
    y = s.sing(words, pitches, beats)
    write_wav(a.out, y)
    print('%.2f s at %d Hz -> %s' % (len(y) / float(SAMPLE_RATE), SAMPLE_RATE,
                                     a.out))


if __name__ == '__main__':
    main()
