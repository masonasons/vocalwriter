#!/usr/bin/env python3
"""A pronunciation for every word typed in, from VocalWriter's own dictionary.

Typing lyrics rather than importing them from the application's own MIDI
exports needs a pronunciation for every word, and the application carries one:
`EnglishLex`, a 440 KB compressed dictionary dated 1996, plus the code that
searches it and falls back to letter-to-sound rules for anything missing.

    Synth_ConvertWord(app, word, refNum)
        -> OrthToPhon(app[0xa8], word, refNum)
            -> InitToken
            -> LookUp -> SearchDict -> DecompressString
                      -> DoMorph          (prefixes and suffixes)
                      -> EngToP           (letter-to-sound fallback)
            -> TunePhons

That code runs here in the C engine, which is the same code lifted from the
PowerPC binary. `ppc/lexicon_ppc.py` runs it under the interpreter instead;
that one is the reference, and the editor does not use it.

What is this program's own is the allophony afterwards: the dictionary gives
back the phonemes it stores, and what the application sings has some of them
merged. See `Words`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import cengine                             # noqa: E402
from ppc import paths                               # noqa: E402
from tools.ttvi import load as load_ttvi, phoneme_order  # noqa: E402

LEXICON = paths.asset('assets', 'EnglishLex')

#: the engine's vowels, for deciding whether an l is syllabic
VOWELS = set('IY IH EH AE AA UX AO UH AX ER EY AY OY AW OW UW YU IR XR AR OR '
             'UR IX O'.split())


class Words(object):
    """What the two lookups have in common: the application's own allophony.

    The dictionary gives back the phonemes it stores; what the application
    sings has some of them merged -- an r-coloured vowel is one phoneme, a
    dark l is not the same symbol as a clear one -- and that merging is read
    off VocalWriter's own exports rather than guessed at.
    """

    #: `OrthToPhon` gives plain sequences; the application's own scores spell
    #: the same words with the single symbols the synthesiser actually wants.
    #: Derived by comparing every word of the Daisy export against what
    #: OrthToPhon returns for it -- see docs, and note this is not cosmetic:
    #: the r-coloured vowels carry a low F3 that "AA" followed by "r" does not.
    R_COLOURED = {('AA', 'r'): 'AR', ('AO', 'r'): 'OR', ('AX', 'r'): 'ER',
                  ('UX', 'r'): 'ER', ('EH', 'r'): 'XR', ('IH', 'r'): 'IR',
                  ('UH', 'r'): 'UR', ('IY', 'r'): 'IR', ('UW', 'r'): 'UR'}
    #: y+UW becomes YU only at the end of a word: the export spells "you" as
    #: YU but "you'll" as y UW LX.
    GLIDES = {('y', 'UW'): 'YU'}

    def allophones(self, phones):
        """Merge the sequences the application spells as single phonemes."""
        out, i = [], 0
        while i < len(phones):
            pair = (phones[i], phones[i + 1]) if i + 1 < len(phones) else None
            merged = self.R_COLOURED.get(pair)
            if merged is None and self.GLIDES.get(pair) and i + 2 == len(phones):
                merged = self.GLIDES[pair]
            if merged:
                out.append(merged)
                i += 2
                continue
            # a syllabic l: "bicycle" is k EL, not k AX l
            if (pair == ('AX', 'l')
                    and (i + 2 == len(phones) or phones[i + 2] not in VOWELS)):
                out.append('EL')
                i += 2
                continue
            # a dark l: syllable-final, i.e. not before a vowel
            if (phones[i] == 'l'
                    and (i + 1 == len(phones) or phones[i + 1] not in VOWELS)):
                out.append('LX')
                i += 1
                continue
            out.append(phones[i])
            i += 1
        return out


class CLexicon(Words):
    """The same dictionary, looked up by the C engine instead.

    `OrthToPhon` is the same lifted code either way, and it agrees with the
    interpreter word for word -- VocalWriterC checks that over the whole
    dictionary and over random syllables. What differs is that a word costs a
    few microseconds rather than the best part of a second, which matters when
    a line of lyrics is a dozen of them.

    The allophony afterwards is this program's own and stays in Python: the
    engine gives back what the dictionary says, and the merging of r-coloured
    vowels, dark and syllabic l and the YU glide is what the application's own
    exports show it doing with them.
    """

    def __init__(self, lexicon=LEXICON, editor=None):
        self.data = open(lexicon, 'rb').read()
        self.names = phoneme_order(load_ttvi())
        self.last_stress = None
        self.ed = editor or cengine.Editor()
        self.ed.lexicon(self.data)

    def syllables(self, word):
        """The word's syllables, each a list of phoneme names."""
        clean = ''.join(c for c in word if c.isalpha() or c == "'")
        if not clean:
            return []
        return [[self.names[c] for c in syl if c < len(self.names)]
                for syl in self.ed.word(clean)]

    def phonemes(self, word):
        out = [p for syl in self.syllables(word) for p in syl]
        return self.allophones(out) if out else None


def open_lexicon():
    """The dictionary, on the C engine.

    `Lexicon` below it -- the same lookup under the interpreter -- is kept as
    the reference the other was checked against, and is not used by the
    editor. It is also less complete: it wires five of the front end's tables
    where the C engine wires all of them, so the suffix rules and the reduced
    vowels come out differently on some words.
    """
    if os.environ.get('VOCALWRITER_ENGINE', '').lower() in (
            'interpreter', 'ppc', 'python'):
        from ppc.lexicon_ppc import Lexicon          # imported only if asked
        return Lexicon()
    return CLexicon()


if __name__ == '__main__':
    lex = open_lexicon()
    for w in (sys.argv[1:] or ['daisy', 'bicycle', 'marriage']):
        p = lex.phonemes(w)
        print('  %-14s %s' % (w, ' '.join(p) if p else '(not in the dictionary)'))
