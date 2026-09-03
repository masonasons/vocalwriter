#!/usr/bin/env python3
"""The dictionary under the PowerPC interpreter.

This is the reference, not the editor's lookup. `ppc/lexicon.py` does the same
job through the C engine, thousands of times faster and with the whole front
end wired: this one gives `OrthToPhon` five of its tables, so words whose
pronunciation depends on the suffix rules or the reduced vowels come out
differently. It is kept because it is what the analysis was done with and what
the other was checked against, and because it needs nothing but the original
binary.

Four operating system calls stand between the dictionary and the code that
reads it, and they are stubbed here: NewPtr, DisposePtr, SetFPos and FSRead.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import image                               # noqa: E402
from ppc import paths                               # noqa: E402
from ppc.cpu import Halt                            # noqa: E402
from ppc.image import Machine                       # noqa: E402
from ppc.lexicon import LEXICON, VOWELS, Words      # noqa: E402
from tools.ttvi import load as load_ttvi, phoneme_order  # noqa: E402

# import stubs the lexicon path calls
NEWPTR = 0xa64c0        # NewPtr(size) -> Ptr
DISPOSEPTR = 0xa62c0    # DisposePtr(p)
SETFPOS = 0xa65c0       # SetFPos(refNum, posMode, posOff) -> OSErr
FSREAD = 0xa65e0        # FSRead(refNum, long *count, void *buf) -> OSErr

PHON_OUT = 0x6df8       # ctx field holding the phoneme output buffer
WORD_MARK = 62          # opens a word
STRESS_MARK = 60        # immediately precedes the stressed vowel
MAX_PHONES = 64

LEX_CTX = 0x8000        # the struct OrthToPhon works in; >= 0x6e14 is used
SHARED_ALLOC = 0xa63a0  # the allocator InitSharedTables uses

#: The letter-to-sound tables live in the `ttvi` resource like everything else:
#: `InitSharedTables` -> `SetTblAddr` carves them out and leaves them in these
#: globals, and `Synth_Startup` copies them into the lexicon context. Without
#: them `EngToP` indexes from a null base, reads zeros, and never advances.
#:
#: Five of them. The C engine wires all of the front end's tables, which is
#: why the two disagree about some words: the suffix rules and the tables that
#: decide a reduced vowel are not here.
LTS_TABLES = (
    ('_g_hash', 0x6dc4),
    ('_g_rule', 0x6dc8),
    ('_g_kind', 0x6dcc),
    ('_g_Opcode_To_ASCII', 0x6db0),
    ('_g_phonTypeTbl', 0x6db4),
)
REFNUM = 42             # any non-zero file reference number will do
WORD_MAX = 16           # the token's character field, before its length at 0x10

FS_FROM_START = 1
FS_FROM_MARK = 3
EOF_ERR = -39           # eofErr, what FSRead returns past the end


class Lexicon(Words):
    def __init__(self, lexicon=LEXICON):
        self.data = open(lexicon, 'rb').read()
        self.pos = 0
        self.m = Machine()
        self.ctx = self.m.alloc(LEX_CTX)
        self.names = phoneme_order(load_ttvi())
        self.last_stress = None
        self._install()
        self._init_tables()

    # -- the four calls it needs from the operating system ------------------

    def _install(self):
        m = self.m

        def newptr(cpu):
            # Filled with a sentinel rather than zeroed: phoneme index 0 is a
            # real phoneme (IY), so a zero-filled buffer cannot be told apart
            # from one ending in IY. NewPtr does not promise zeroed memory
            # anyway -- NewPtrClear is the one that does.
            n = max(cpu.r[3] & 0xFFFFFFFF, 16)
            addr = m.alloc(n, zero=False)
            m.mem.write(addr, bytes([0xFF]) * n)
            cpu.r[3] = addr

        def disposeptr(cpu):
            cpu.r[3] = 0

        def setfpos(cpu):
            mode = cpu.r[4] & 0xFFFF
            off = cpu.r[5] & 0xFFFFFFFF
            if off & 0x80000000:
                off -= 1 << 32
            self.pos = off if mode == FS_FROM_START else self.pos + off
            cpu.r[3] = 0

        def fsread(cpu):
            cnt_p, buf = cpu.r[4], cpu.r[5]
            want = m.mem.r32(cnt_p)
            got = self.data[self.pos:self.pos + want]
            if got:
                m.mem.write(buf, got)
            self.pos += len(got)
            m.mem.w32(cnt_p, len(got))
            cpu.r[3] = 0 if len(got) == want else (EOF_ERR & 0xFFFFFFFF)

        for addr, fn in ((NEWPTR, newptr), (DISPOSEPTR, disposeptr),
                         (SETFPOS, setfpos), (FSREAD, fsread)):
            m.cpu.hooks[addr] = fn

    def _init_tables(self):
        """Carve the shared tables out of `ttvi` and hand the context its own."""
        m = self.m
        m.cpu.hooks[SHARED_ALLOC] = lambda cpu: cpu.r.__setitem__(
            3, m.alloc(max(cpu.r[3] & 0xFFFFFFFF, 16)))
        addr, _size = m.load_resource('ttvi', 2)
        m.mem.w32(m.globals_ptr('_g_DataHandle'), m.handle_to(addr))
        m.call('InitSharedTables')
        for name, off in LTS_TABLES:
            m.mem.w32(self.ctx + off, m.mem.r32(m.globals_ptr(name)))

    # -- allophones --------------------------------------------------------
    # -- use ---------------------------------------------------------------

    def _token(self, text):
        """The token struct OrthToPhon reads.

        Not a string: the copy loop indexes characters from offset 0 and takes
        the count from a u32 at offset 0x10, so the characters live in a fixed
        16-byte field with the length after it. It upper-cases as it copies,
        so case here does not matter.
        """
        b = text.encode('mac-roman', 'replace')[:WORD_MAX]
        p = self.m.alloc(0x20)
        self.m.mem.write(p, b)
        self.m.mem.w32(p + 0x10, len(b))
        return p

    def convert(self, word, max_steps=8000000):
        """Run OrthToPhon over one word. Returns its result code."""
        self.pos = 0
        wp = self._token(word)
        cpu = self.m.cpu
        cpu.r[1] = image.STACK_TOP - 0x1000
        self.m.mem.w32(cpu.r[1], image.STACK_TOP)
        cpu.r[3], cpu.r[4], cpu.r[5] = self.ctx, wp, REFNUM
        rc = cpu.call(self.m.funcs['OrthToPhon'], max_steps=max_steps)
        return rc - 0x10000 if rc & 0x8000 else rc

    def phonemes(self, word):
        """The phonemes for a word, as engine symbols, or None if unknown.

        Words the dictionary knows come back exactly as the application would
        pronounce them. Words it does not fall through to `EngToP`, the
        letter-to-sound rules, which needs tables this does not load yet and
        spins if they are absent -- hence the step limit, which turns that into
        a `None` rather than a hang.
        """
        try:
            self.convert(word)
        except Halt:
            return None
        buf = self.m.mem.r32(self.ctx + PHON_OUT)
        if not buf:
            return None
        out, stress = [], None
        for i in range(MAX_PHONES):
            v = self.m.mem.r16(buf + i * 2)
            if v == 0xFFFF:
                break
            if v == STRESS_MARK:
                stress = len(out)
            elif v == WORD_MARK:
                continue
            elif v < len(self.names):
                out.append(self.names[v])
        self.last_stress = stress
        return self.allophones(out) if out else None




if __name__ == '__main__':
    lex = Lexicon()
    print('lexicon %d bytes, context 0x%x' % (len(lex.data), lex.ctx))
    for w in (sys.argv[1:] or ['daisy', 'bicycle', 'marriage']):
        p = lex.phonemes(w)
        print('  %-14s %s' % (w, ' '.join(p) if p else '(not in the dictionary)'))
