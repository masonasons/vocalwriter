#!/usr/bin/env python3
"""Check a .trk parse against VocalWriter's own MIDI export of the same song.

The export is the only independent account of what a .trk contains, so this is
the regression test for the format work: parse the .trk cold, parse the .mid,
and require that every track's notes agree in count, pitch and duration.

    python tools/validate.py "assets/Demo Music/Daisy.trk" out/Daisy_export.mid

Notes are compared as sorted multisets. Simultaneous notes -- Crash's two-note
cymbal hit is one -- carry no inherent order, and the two files store them in
opposite orders, so comparing sequences directly would report a false failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smf import MidiFile          # noqa: E402
from trk import Song              # noqa: E402


def compare(trk_path, mid_path):
    song = Song.from_file(trk_path)
    midi = MidiFile.from_file(mid_path)
    by_name = {t.name: t for t in song.tracks if t.name}

    rows, ok = [], True
    for m in midi.tracks:
        if not m.name:
            continue
        t = by_name.get(m.name)
        if t is None:
            rows.append((m.name, 0, len(m.notes), False))
            ok = False
            continue
        got = sorted((n.pitch, n.duration) for n in t.notes())
        exp = sorted((n.pitch, n.duration) for n in m.notes)
        match = got == exp
        ok &= match
        rows.append((m.name, len(got), len(exp), match))
    return ok, rows


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ok, rows = compare(sys.argv[1], sys.argv[2])
    print('%-16s %-8s %-8s %s' % ('track', '.trk', '.mid', 'match'))
    for name, a, b, match in rows:
        print('%-16s %-8d %-8d %s' % (name, a, b, 'yes' if match else 'NO'))
    print()
    print('OK' if ok else 'MISMATCH')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
