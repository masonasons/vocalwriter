#!/usr/bin/env python3
"""Reader for VocalWriter's speech-data blob: the 'ttvi' resource, id 2.

`InitSynth` runs `LoadSynthResource(&_g_DataHandle, 'ttvi', 2)`, and
`SetTblAddr` then carves the loaded handle into globals -- among them
`_g_SpeechTbls`, which `SetSpeechTblAddr` further carves into the synthesiser's
context pointers. So every table the engine uses lives in this one 91 KB
resource, addressed by a header of 46 big-endian u32 offsets at the start.

Two of those tables are identified beyond doubt because they satisfy the
resonator identity from `Calc_Pole_Coefficients` exactly:

    C = -(B/2)^2      i.e.  -exp(-2*pi*BW/fs) = -(2*exp(-pi*BW/fs) / 2)^2

and both are indexed **logarithmically**, twelve steps per octave, matching the
log-frequency domain the whole engine works in (see `e_HzToPitch`).

The phoneme tables are 57 entries long, and the engine's internal phoneme order
is stored in the blob itself as packed two-character codes -- which is how the
order below is known rather than guessed. It is *not* the same order as the
Phoneme Palette in the application binary.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ppc import paths                                        # noqa: E402

DEFAULT_RSRC = paths.asset('assets', 'VocalWriter.app', 'Contents',
                           'Resources', 'VocalWriter.rsrc')

N_SLOTS = 46

# Offsets confirmed by inspecting the data itself, not by trusting the
# slot->context mapping (which the disassembly leaves ambiguous by one).
COS_TABLE = 0xd9e        # cos term, indexed by log-frequency
B_TABLE = 0xf4e          # 2*exp(-pi*BW/fs)
C_TABLE = 0x202e         # -exp(-2*pi*BW/fs)
PHONEME_NAMES = 0x11302  # packed 2-char codes, the internal order
DURATION_1 = 0x1121e     # per-phoneme, milliseconds
DURATION_2 = 0x11290     # per-phoneme, milliseconds
N_PHONEMES = 57


def load(path=DEFAULT_RSRC):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.machrsrc import ResourceFork
    return ResourceFork.from_file(path).get('ttvi', 2).data


def slot_offsets(blob):
    """The 46 table offsets in the blob header."""
    return [struct.unpack('>I', blob[i * 4:i * 4 + 4])[0] for i in range(N_SLOTS)]


def u16_table(blob, off, n):
    return list(struct.unpack('>%dH' % n, blob[off:off + n * 2]))


def f32_table(blob, off, n):
    return list(struct.unpack('>%df' % n, blob[off:off + n * 4]))


def phoneme_order(blob):
    """The engine's internal phoneme order, from the blob's own name table."""
    out = []
    for v in u16_table(blob, PHONEME_NAMES, N_PHONEMES):
        # single-letter phonemes pack as 0x00xx, so the NUL must go too
        s = bytes([v >> 8, v & 0xFF]).decode('mac-roman', 'replace')
        s = s.replace(chr(0), '').strip()
        out.append(s or '.')
    return out


def durations(blob):
    """[(phoneme, duration_ms, secondary_ms)] in internal order."""
    names = phoneme_order(blob)
    d1 = u16_table(blob, DURATION_1, N_PHONEMES)
    d2 = u16_table(blob, DURATION_2, N_PHONEMES)
    return list(zip(names, d1, d2))


def check_resonator_identity(blob, n=200):
    """Verify C = -(B/2)^2 over the shipped coefficient tables."""
    b = f32_table(blob, B_TABLE, n)
    c = f32_table(blob, C_TABLE, n)
    worst = 0.0
    for bb, cc in zip(b, c):
        worst = max(worst, abs(cc - (-(bb / 2.0) ** 2)))
    return worst


def bandwidth_step(blob, n=64):
    """Steps per octave implied by the bandwidth table's log spacing."""
    import math
    b = f32_table(blob, B_TABLE, n)
    # B = 2*exp(-pi*BW/fs), so -ln(B/2) is proportional to BW
    lg = [-math.log(x / 2.0) for x in b if x > 0]
    ratios = [lg[i + 1] / lg[i] for i in range(min(len(lg), 40) - 1) if lg[i] > 0]
    r = sum(ratios) / len(ratios)
    return r, math.log(2.0) / math.log(r)


# _g_SpeechTbls is slot 35 of the outer header and points at a *second* header
# of 46 offsets, relative to itself. The articulation tables live under that.
SPEECH_TBLS = 0x480

# Slots within the speech sub-header, identified from their contents against the
# known phoneme order: F1/F2/F3 in Hz, their bandwidths, and a second target set
# the engine glides towards.
SUB_F1, SUB_F2, SUB_F3 = 16, 17, 18
SUB_B1, SUB_B2, SUB_B3 = 19, 20, 21
SUB_F1B, SUB_F2B, SUB_F3B = 23, 24, 25
SUB_B1B, SUB_B2B, SUB_B3B = 26, 27, 28
# Amplitude of voicing, in dB. Vowels 62-66, nasals 60, voiced fricatives 53,
# and exactly 0 for every unvoiced sound and for silence -- which is what makes
# it identifiable. Slot 29 is the same quantity at the second target.
SUB_AV, SUB_AV2 = 22, 29
SUB_MANNER = 11          # 0/1 vowel, 3 consonant, 4 liquid
SUB_BURST = 14           # non-zero only on stops: place-of-articulation index

INDIRECT = 0x8000        # bit 15: the value is an index, not a frequency
NO_TARGET = 0x7FFF       # silence, /h/ and the glottal stop carry this


def speech_slots(blob):
    base = SPEECH_TBLS
    return [base + struct.unpack('>I', blob[base + i * 4:base + i * 4 + 4])[0]
            for i in range(N_SLOTS)]


def _row(blob, addr):
    return list(struct.unpack('>%dH' % N_PHONEMES, blob[addr:addr + N_PHONEMES * 2]))


# Consonant loci. Sub-slot 6 gives, per phoneme, a byte offset into a locus
# data table (0xFFFF = none, i.e. the phoneme has no locus). Get_Locus reads it
# as `idx = (v >> 1) + ctrl*3` into a u16 array, so each formant gets a 3-value
# record (locus frequency in Hz, then two transition coefficients) and the three
# records sit consecutively.
#
# Sub-slots 3 and 4 hold two such tables: the same consonant's locus in
# different vowel contexts, which is what Get_Locus selects between using the
# neighbouring vowel's class. Table 4 carries the higher F2 loci (front-vowel
# context), table 3 the lower ones.
SUB_LOCUS_INDEX = 6
SUB_LOCUS_BACK = 3
SUB_LOCUS_FRONT = 4
NO_LOCUS = 0xFFFF


def loci(blob):
    """{phoneme: {'back': [(f,c1,c2) x3], 'front': [...]}} in Hz."""
    sl = speech_slots(blob)
    names = phoneme_order(blob)
    index = _row(blob, sl[SUB_LOCUS_INDEX])
    out = {}
    for i, name in enumerate(names):
        v = index[i]
        if v == NO_LOCUS or v >= INDIRECT:
            continue
        entry = {}
        for key, slot in (('back', SUB_LOCUS_BACK), ('front', SUB_LOCUS_FRONT)):
            base = sl[slot]
            recs = []
            for ctrl in range(3):
                o = base + v + ctrl * 6
                recs.append(list(struct.unpack('>3H', blob[o:o + 6])))
            entry[key] = recs
        out[name] = entry
    return out


def phoneme_targets(blob):
    """VocalWriter's own articulation table.

    Returns {phoneme: dict}. `formants` and `bandwidths` are in Hz; `formants2`
    is the second target the engine moves toward. A value of None means the
    stored entry had bit 15 set -- an index into a further table (the
    diphthongs and r-coloured vowels, which `Get_Diphthongs` resolves) -- or was
    the no-target marker used for silence, /h/ and the glottal stop.
    """
    sl = speech_slots(blob)
    names = phoneme_order(blob)
    rows = {k: _row(blob, sl[k]) for k in
            (SUB_F1, SUB_F2, SUB_F3, SUB_B1, SUB_B2, SUB_B3,
             SUB_F1B, SUB_F2B, SUB_F3B, SUB_B1B, SUB_B2B, SUB_B3B,
             SUB_AV, SUB_AV2, SUB_MANNER, SUB_BURST)}
    d1 = u16_table(blob, DURATION_1, N_PHONEMES)
    d2 = u16_table(blob, DURATION_2, N_PHONEMES)

    def val(v):
        return None if (v & INDIRECT or v == NO_TARGET) else v

    out = {}
    for i, name in enumerate(names):
        out[name] = {
            'formants': [val(rows[k][i]) for k in (SUB_F1, SUB_F2, SUB_F3)],
            'bandwidths': [val(rows[k][i]) for k in (SUB_B1, SUB_B2, SUB_B3)],
            'formants2': [val(rows[k][i]) for k in (SUB_F1B, SUB_F2B, SUB_F3B)],
            'bandwidths2': [val(rows[k][i]) for k in (SUB_B1B, SUB_B2B, SUB_B3B)],
            'av_db': rows[SUB_AV][i] if rows[SUB_AV][i] < INDIRECT else None,
            'av2_db': rows[SUB_AV2][i] if rows[SUB_AV2][i] < INDIRECT else None,
            'manner': rows[SUB_MANNER][i],
            'burst': rows[SUB_BURST][i],
            'max_ms': d1[i],
            'min_ms': d2[i],
        }
    return out


def main():
    blob = load()
    print('ttvi id 2: %d bytes, %d table slots' % (len(blob), N_SLOTS))
    print()
    print('resonator identity C = -(B/2)^2 holds to %.2e over the shipped tables'
          % check_resonator_identity(blob))
    r, per_octave = bandwidth_step(blob)
    print('bandwidth table spacing: ratio %.5f per step -> %.1f steps per octave'
          % (r, per_octave))
    print()
    names = phoneme_order(blob)
    print('internal phoneme order (%d):' % len(names))
    for k in range(0, len(names), 13):
        print('  %2d: %s' % (k, ' '.join('%-3s' % x for x in names[k:k + 13])))
    print()
    print('per-phoneme max/min duration (ms):')
    rows = durations(blob)
    for k in range(0, len(rows), 6):
        print('  ' + '   '.join('%-3s %3d/%-3d' % r for r in rows[k:k + 6]))
    print()
    t = phoneme_targets(blob)
    full = [k for k, v in t.items() if all(f is not None for f in v['formants'])]
    print('articulation targets: %d of %d phonemes fully direct' % (len(full), len(t)))
    print('%-4s %-18s %-16s %s' % ('ph', 'F1/F2/F3 Hz', 'B1/B2/B3', 'dur ms'))
    for name in phoneme_order(blob):
        v = t[name]
        f = '/'.join('-' if x is None else str(x) for x in v['formants'])
        b = '/'.join('-' if x is None else str(x) for x in v['bandwidths'])
        print('%-4s %-18s %-16s %d/%d' % (name, f, b, v['max_ms'], v['min_ms']))


if __name__ == '__main__':
    main()
