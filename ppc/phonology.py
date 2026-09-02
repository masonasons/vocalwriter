#!/usr/bin/env python3
"""Which phonemes are vowels, and where one syllable ends and the next begins.

This is what a word has to know about itself before it can be spread over more
than one note. It comes from VocalWriter's own phoneme tables rather than from
a list written out here, so it stays true to the engine that will sing it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.ttvi import load as load_ttvi, phoneme_targets     # noqa: E402

#: What counts as a syllable nucleus -- the phoneme a note's length is spent
#: on. The tables give every phoneme a manner: 0, 1 and 2 are the vowels, 3 the
#: consonants, 4 the sonorants. Manner alone is not enough in either direction.
#: `y` is manner 0 and is the glide in "yes", not a nucleus; `EL` is manner 4
#: and is the syllabic l of "bicycle", which is one. Their maximum durations
#: separate them cleanly: every vowel in the table allows at least 120 ms, `EL`
#: 160, while `y` stops at 75 and `l`, `r`, `w` and dark `LX` at 100 or less.
NUCLEUS_MANNERS = (0, 1, 2, 4)
NUCLEUS_MIN_MAX = 110

_TARGETS = None


def targets():
    global _TARGETS
    if _TARGETS is None:
        _TARGETS = phoneme_targets(load_ttvi())
    return _TARGETS


def is_nucleus(sym, tbl=None):
    row = (tbl or targets()).get(sym) or {}
    return (row.get('manner') in NUCLEUS_MANNERS
            and (row.get('max_ms') or 0) >= NUCLEUS_MIN_MAX)


#: Consonants that can follow another one at the start of a syllable: the
#: liquids and glides, as in "cra-", "blue", "twin", "few".
GLIDES = frozenset(('l', 'r', 'w', 'y', 'LX'))

#: What /s/ can be followed by, as in "spy", "sty", "sky", "small", "snow".
AFTER_S = frozenset(('p', 't', 'k', 'm', 'n', 'f'))


def legal_onset(syms):
    """Could this run of consonants begin an English syllable?

    One consonant always can. Two can when the second is a liquid or glide, or
    when the first is /s/. Three can when the first is /s/ and the other two
    could stand alone, which is what allows "str-" and "spl-".
    """
    if len(syms) <= 1:
        return True
    if len(syms) == 2:
        return syms[1] in GLIDES or (syms[0] == 's' and syms[1] in AFTER_S)
    if len(syms) == 3 and syms[0] == 's':
        return legal_onset(syms[1:])
    return False


def syllabify(phonemes):
    """Split a word's phonemes into syllables, one group per nucleus.

    The consonants between two vowels go to the *second* syllable as far as
    they legally can -- the maximum onset principle, which is what puts the
    /s/ of "bicycle" on "cy" rather than on "bi". Checked against the way
    VocalWriter's own scores divide their lyrics across notes, this agrees on
    "bi-cy-cle", "an-swer", "sty-lish", "mar-riage", "bet-ter", "do-ing" and
    "af-ford". It disagrees on "dai-sy" and "cra-zy", where the score keeps the
    /z/ with the first note; those splits were typed by hand by whoever wrote
    the song, which is the whole reason this is only a starting point and the
    editor lets the division be moved.

    A word with one vowel, or none, comes back as a single group.
    """
    syms = list(phonemes)
    tbl = targets()
    nuclei = [i for i, s in enumerate(syms) if is_nucleus(s, tbl)]
    if len(nuclei) < 2:
        return [syms] if syms else []

    cuts = []
    for a, b in zip(nuclei, nuclei[1:]):
        run = syms[a + 1:b]              # the consonants between two vowels
        take = 0                         # how many of them open the next one
        while take < len(run) and legal_onset(run[len(run) - take - 1:]):
            take += 1
        cuts.append(b - take)
    out, start = [], 0
    for c in cuts:
        out.append(syms[start:c])
        start = c
    out.append(syms[start:])
    return [g for g in out if g]


def regroup(phonemes, count):
    """Divide a word into exactly `count` notes, as evenly as it allows.

    Syllables are kept whole and shared out as evenly as they go, so asking a
    three-syllable word for two notes gives two syllables then one rather than
    breaking a syllable in half. Asking for more notes than there are syllables
    gives one per syllable.
    """
    parts = syllabify(phonemes)
    count = max(1, min(int(count), len(parts)))
    if count == len(parts):
        return parts
    out, at = [], 0
    for k in range(count):
        take = (len(parts) - at) // (count - k)
        group = []
        for p in parts[at:at + take]:
            group.extend(p)
        out.append(group)
        at += take
    return out
