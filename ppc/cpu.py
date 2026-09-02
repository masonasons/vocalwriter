#!/usr/bin/env python3
"""A small user-mode PowerPC interpreter, enough to run VocalWriter's synthesiser.

Approximating VocalWriter does not converge on VocalWriter. Its synthesis core
is, however, self-contained arithmetic: 48 functions, 11,075 instructions, 65
distinct opcodes, and essentially no calls outside itself. Running that code
directly gives the original's output exactly, on any machine, with no emulator
and no Mac OS -- which is what a re-implementation is actually for.

Scope, deliberately narrow:

  * 32-bit big-endian PowerPC, user mode only.
  * The integer, branch and floating-point subset the synthesiser uses. There
    is no MMU, no supervisor state, no exceptions, no cache model.
  * Flat little-endian host memory holding big-endian guest words.

The condition register is modelled only as far as `cmpw`/`cmpwi` and the
conditional branches need: each of the eight CR fields keeps lt/gt/eq bits.
"""
import struct

# ---------------------------------------------------------------------------
# memory


class Memory(object):
    """Flat sparse memory, big-endian, page-backed.

    The synthesiser executes tens of millions of instructions per second of
    audio and nearly every one touches memory, so the access path matters more
    than anything else here. Two things keep it cheap: a one-entry page cache,
    since code and data accesses are overwhelmingly sequential within a page,
    and `struct` unpacking straight out of the page buffer instead of
    assembling values a byte at a time.
    """

    PAGE = 0x10000
    SHIFT = 16
    MASK = 0xFFFF

    _u16 = struct.Struct('>H').unpack_from
    _u32 = struct.Struct('>I').unpack_from
    _f32 = struct.Struct('>f').unpack_from
    _f64 = struct.Struct('>d').unpack_from
    _p16 = struct.Struct('>H').pack_into
    _p32 = struct.Struct('>I').pack_into
    _pf32 = struct.Struct('>f').pack_into
    _pf64 = struct.Struct('>d').pack_into

    def __init__(self):
        self.pages = {}
        self._cn = -1
        self._cp = None
        #: count of stfs saturations, as a health signal on a long render
        self.overflows = 0

    def _page(self, addr):
        """The page holding `addr`, allocating it on first touch."""
        pno = addr >> self.SHIFT
        if pno == self._cn:
            return self._cp
        p = self.pages.get(pno)
        if p is None:
            p = bytearray(self.PAGE)
            self.pages[pno] = p
        self._cn = pno
        self._cp = p
        return p

    # -- bulk ---------------------------------------------------------------

    def write(self, addr, data):
        n = len(data)
        pos = 0
        while pos < n:
            a = addr + pos
            off = a & self.MASK
            take = min(n - pos, self.PAGE - off)
            self._page(a)[off:off + take] = data[pos:pos + take]
            pos += take

    def read(self, addr, n):
        out = bytearray()
        pos = 0
        while pos < n:
            a = addr + pos
            off = a & self.MASK
            take = min(n - pos, self.PAGE - off)
            out += self._page(a)[off:off + take]
            pos += take
        return bytes(out)

    # -- scalars ------------------------------------------------------------
    #
    # Each takes the fast path when the access sits wholly inside one page,
    # which is all of them in practice; the fallback keeps unaligned accesses
    # that straddle a page boundary correct.

    def r8(self, a):
        return self._page(a)[a & self.MASK]

    def w8(self, a, v):
        self._page(a)[a & self.MASK] = v & 0xFF

    def r16(self, a):
        off = a & self.MASK
        if off <= self.PAGE - 2:
            return self._u16(self._page(a), off)[0]
        return (self.r8(a) << 8) | self.r8(a + 1)

    def w16(self, a, v):
        off = a & self.MASK
        if off <= self.PAGE - 2:
            self._p16(self._page(a), off, v & 0xFFFF)
        else:
            self.w8(a, (v >> 8) & 0xFF)
            self.w8(a + 1, v & 0xFF)

    def r32(self, a):
        off = a & self.MASK
        if off <= self.PAGE - 4:
            return self._u32(self._page(a), off)[0]
        return (self.r16(a) << 16) | self.r16(a + 2)

    def w32(self, a, v):
        off = a & self.MASK
        if off <= self.PAGE - 4:
            self._p32(self._page(a), off, v & 0xFFFFFFFF)
        else:
            self.w16(a, (v >> 16) & 0xFFFF)
            self.w16(a + 2, v & 0xFFFF)

    def rf32(self, a):
        off = a & self.MASK
        if off <= self.PAGE - 4:
            return self._f32(self._page(a), off)[0]
        return struct.unpack('>f', self.read(a, 4))[0]

    def wf32(self, a, v):
        # A double too large for single precision becomes an infinity on
        # PowerPC rather than trapping, so `stfs` must saturate here too.
        off = a & self.MASK
        try:
            if off <= self.PAGE - 4:
                self._pf32(self._page(a), off, v)
            else:
                self.write(a, struct.pack('>f', v))
        except OverflowError:
            self.overflows += 1
            inf = float('inf') if v > 0 else float('-inf')
            if off <= self.PAGE - 4:
                self._pf32(self._page(a), off, inf)
            else:
                self.write(a, struct.pack('>f', inf))

    def rf64(self, a):
        off = a & self.MASK
        if off <= self.PAGE - 8:
            return self._f64(self._page(a), off)[0]
        return struct.unpack('>d', self.read(a, 8))[0]

    def wf64(self, a, v):
        off = a & self.MASK
        if off <= self.PAGE - 8:
            self._pf64(self._page(a), off, v)
        else:
            self.write(a, struct.pack('>d', v))


# ---------------------------------------------------------------------------
# helpers

MASK32 = 0xFFFFFFFF

_U32 = struct.Struct('>I').unpack_from
_F32 = struct.Struct('>f')
_F64 = struct.Struct('>d')
_F64I = struct.Struct('>II')


def _to_int(v, trunc):
    """PowerPC float->word conversion, saturating out-of-range values."""
    if v != v:
        return -2 ** 31
    if v >= 2 ** 31 - 1:
        return 2 ** 31 - 1
    if v <= -2 ** 31:
        return -2 ** 31
    return int(v) if trunc else int(round(v))


def _as_int_pattern(iv):
    """An integer sitting in the low word of an FPR, as fctiwz leaves it.

    The hardware writes the converted word into the low half of the register
    and the code reads it back with stfd plus a load of that word, so keeping
    the value *as the register's bit pattern* reproduces it exactly -- and,
    unlike a side table of "this register holds an integer", it cannot go
    stale when the register is later reloaded with an ordinary float.
    """
    return _F64.unpack(_F64I.pack(0, iv & MASK32))[0]


def _single(v):
    """Round a double to single precision, saturating like the hardware."""
    try:
        return _F32.unpack(_F32.pack(v))[0]
    except OverflowError:
        return float('inf') if v > 0 else float('-inf')


def s32(v):
    v &= MASK32
    return v - 0x100000000 if v & 0x80000000 else v


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class Halt(Exception):
    """Raised when execution returns past the entry frame."""


class CPU(object):
    def __init__(self, mem):
        self.mem = mem
        self.r = [0] * 32
        self.f = [0.0] * 32
        self.cr = [[False, False, False] for _ in range(8)]   # lt, gt, eq
        self.lr = 0
        self.ctr = 0
        self.xer = 0
        self.pc = 0
        self.steps = 0
        #: address -> python callable(cpu), for libm and other imports
        self.hooks = {}
        self.trace = None
        #: instruction-fetch page cache, kept separate from Memory's own cache
        #: so that data accesses do not evict the code page on every step
        self._ipn = -1
        self._ipg = None


    # -- condition register ------------------------------------------------

    def _setcr(self, field, a, b):
        self.cr[field] = [a < b, a > b, a == b]

    def _cond(self, bo, bi):
        """Evaluate a branch condition (the BO/BI encoding)."""
        ctr_ok = True
        if not (bo & 0x04):
            self.ctr = (self.ctr - 1) & MASK32
            ctr_ok = (self.ctr != 0) if (bo & 0x02) == 0 else (self.ctr == 0)
        if bo & 0x10:
            return ctr_ok
        field, bit = bi >> 2, bi & 3
        val = self.cr[field][bit] if bit < 3 else False
        want = bool(bo & 0x08)
        return ctr_ok and (val == want)

    # -- execution ---------------------------------------------------------

    def call(self, addr, max_steps=200000000):
        """Run a function to completion; returns r3."""
        sentinel = 0xDEAD0000
        self.lr = sentinel
        self.pc = addr
        hooks = self.hooks
        step = self.step
        mem = self.mem
        r = self.r
        f = self.f
        # Hooks sit above the executable's own code, so one integer compare
        # rejects them for every real instruction and the dict is consulted
        # only where a stub actually lives.
        hookmin = min(hooks) if hooks else sentinel
        # `steps` is a lifetime counter for statistics; the runaway guard has
        # to count only this call, or a long render trips it simply by being
        # long rather than by looping.
        steps = self.steps
        n = 0
        while True:
            pc = self.pc
            if pc == sentinel:
                self.steps = steps
                return self.r[3]
            if pc >= hookmin:
                hook = hooks.get(pc)
                if hook is not None:
                    hook(self)
                    self.pc = self.lr
                    continue
            # A fast path for the instructions that dominate the profile:
            # loads and stores are more than half of everything executed, and
            # handling them here saves a method call apiece. Anything else
            # falls through to the full decoder with the word already fetched.
            pno = pc >> 16
            if pno != self._ipn:
                self._ipg = mem._page(pc)
                self._ipn = pno
            word = _U32(self._ipg, pc & 0xFFFF)[0]
            op = word >> 26
            if op == 32:                                   # lwz
                a = (word >> 16) & 31
                di = (word & 0xFFFF) - ((word & 0x8000) << 1)
                r[(word >> 21) & 31] = mem.r32(
                    ((r[a] if a else 0) + di) & MASK32)
                self.pc = pc + 4
            elif op == 40:                                 # lhz
                a = (word >> 16) & 31
                di = (word & 0xFFFF) - ((word & 0x8000) << 1)
                r[(word >> 21) & 31] = mem.r16(
                    ((r[a] if a else 0) + di) & MASK32)
                self.pc = pc + 4
            elif op == 48:                                 # lfs
                a = (word >> 16) & 31
                di = (word & 0xFFFF) - ((word & 0x8000) << 1)
                f[(word >> 21) & 31] = mem.rf32(
                    ((r[a] if a else 0) + di) & MASK32)
                self.pc = pc + 4
            elif op == 14:                                 # addi
                a = (word >> 16) & 31
                di = (word & 0xFFFF) - ((word & 0x8000) << 1)
                r[(word >> 21) & 31] = ((r[a] if a else 0) + di) & MASK32
                self.pc = pc + 4
            elif op == 52:                                 # stfs
                a = (word >> 16) & 31
                di = (word & 0xFFFF) - ((word & 0x8000) << 1)
                mem.wf32(((r[a] if a else 0) + di) & MASK32,
                         f[(word >> 21) & 31])
                self.pc = pc + 4
            elif op == 44:                                 # sth
                a = (word >> 16) & 31
                di = (word & 0xFFFF) - ((word & 0x8000) << 1)
                mem.w16(((r[a] if a else 0) + di) & MASK32,
                        r[(word >> 21) & 31] & 0xFFFF)
                self.pc = pc + 4
            else:
                step(word, pc)
            steps += 1
            n += 1
            if n > max_steps:
                self.steps = steps
                raise Halt('step limit at pc=0x%x' % pc)

    def step(self, word=None, pc=None):
        if pc is None:
            pc = self.pc
        if word is None:
            pno = pc >> 16
            if pno != self._ipn:
                self._ipg = self.mem._page(pc)
                self._ipn = pno
            word = _U32(self._ipg, pc & 0xFFFF)[0]
        nxt = pc + 4
        op = word >> 26
        r = self.r
        m = self.mem

        if op == 32:    # lwz
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = m.r32(((r[a] if a else 0) + di) & MASK32)
        elif op == 31:
            nxt = self._op31(word, nxt)
        elif op == 48:    # lfs
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            self.f[d] = m.rf32(((r[a] if a else 0) + di) & MASK32)
        elif op == 40:    # lhz
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = m.r16(((r[a] if a else 0) + di) & MASK32)
        elif op in (59, 63):
            self._fp(word, op)
        elif op == 14:      # addi
            d, a, si = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = ((r[a] if a else 0) + si) & MASK32
        elif op in (20, 21, 23):   # rlwimi / rlwinm / rlwnm
            s_, a = (word >> 21) & 31, (word >> 16) & 31
            sh = (word >> 11) & 31 if op != 23 else (r[(word >> 11) & 31] & 31)
            mb, me = (word >> 6) & 31, (word >> 1) & 31
            val = ((r[s_] << sh) | (r[s_] >> (32 - sh))) & MASK32 if sh else r[s_]
            mask = self._mask(mb, me)
            if op == 20:
                r[a] = ((val & mask) | (r[a] & ~mask)) & MASK32
            else:
                r[a] = val & mask
            if word & 1:
                self._setcr(0, s32(r[a]), 0)
        elif op == 52:    # stfs
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            m.wf32(((r[a] if a else 0) + di) & MASK32, self.f[s_])
        elif op == 16:    # bc / bcl
            bo, bi = (word >> 21) & 31, (word >> 16) & 31
            bd = word & 0xFFFC
            if bd & 0x8000:
                bd -= 0x10000
            take = self._cond(bo, bi)
            if word & 1:
                self.lr = nxt
            if take:
                nxt = (bd if (word & 2) else (self.pc + bd)) & MASK32
        elif op == 11:    # cmpi
            bf, a, si = (word >> 23) & 7, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            self._setcr(bf, s32(r[a]), si)
        elif op == 44:    # sth
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            m.w16(((r[a] if a else 0) + di) & MASK32, r[s_] & 0xFFFF)
        elif op == 36:    # stw
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            m.w32(((r[a] if a else 0) + di) & MASK32, r[s_])
        elif op == 15:    # addis
            d, a, si = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = ((r[a] if a else 0) + (si << 16)) & MASK32
        elif op == 18:    # b / bl
            li = word & 0x03FFFFFC
            if li & 0x02000000:
                li -= 0x04000000
            tgt = li if (word & 2) else (self.pc + li) & MASK32
            if word & 1:
                self.lr = nxt
            nxt = tgt & MASK32
        elif op == 19:    # bclr / bcctr
            xo = (word >> 1) & 0x3FF
            bo, bi = (word >> 21) & 31, (word >> 16) & 31
            if xo == 16:      # bclr
                take = self._cond(bo, bi)
                t = self.lr
                if word & 1:
                    self.lr = nxt
                if take:
                    nxt = t & MASK32
            elif xo == 528:   # bcctr
                take = self._cond(bo, bi)
                t = self.ctr
                if word & 1:
                    self.lr = nxt
                if take:
                    nxt = t & MASK32
            elif xo == 449:   # cror
                bt, ba, bb = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
                v = self._crbit(ba) or self._crbit(bb)
                self._setcrbit(bt, v)
            else:
                raise Halt('unhandled op19 xo=%d at 0x%x' % (xo, self.pc))
        elif op == 54:    # stfd
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            m.wf64(((r[a] if a else 0) + di) & MASK32, self.f[s_])
        elif op == 50:    # lfd
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            self.f[d] = m.rf64(((r[a] if a else 0) + di) & MASK32)
        elif op in (24, 25):   # ori / oris
            s_, a, ui = (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
            r[a] = (r[s_] | (ui << (16 if op == 25 else 0))) & MASK32
        elif op in (26, 27):   # xori / xoris
            s_, a, ui = (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
            r[a] = (r[s_] ^ (ui << (16 if op == 27 else 0))) & MASK32
        elif op == 10:    # cmpli
            bf, a, ui = (word >> 23) & 7, (word >> 16) & 31, word & 0xFFFF
            self._setcr(bf, r[a] & MASK32, ui)
        elif op == 37:    # stwu
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            ea = (r[a] + di) & MASK32
            m.w32(ea, r[s_])
            r[a] = ea
        elif op == 46:    # lmw
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            ea = ((r[a] if a else 0) + di) & MASK32
            for i in range(d, 32):
                r[i] = m.r32(ea)
                ea += 4
        elif op == 47:    # stmw
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            ea = ((r[a] if a else 0) + di) & MASK32
            for i in range(s_, 32):
                m.w32(ea, r[i])
                ea += 4
        elif op == 33:    # lwzu
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            ea = (r[a] + di) & MASK32
            r[d] = m.r32(ea)
            r[a] = ea
        elif op == 34:    # lbz
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = m.r8(((r[a] if a else 0) + di) & MASK32)
        elif op == 38:    # stb
            s_, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            m.w8(((r[a] if a else 0) + di) & MASK32, r[s_] & 0xFF)
        elif op == 42:    # lha
            d, a, di = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = s16(m.r16(((r[a] if a else 0) + di) & MASK32)) & MASK32
        elif op == 7:     # mulli
            d, a, si = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = (s32(r[a]) * si) & MASK32
        elif op == 8:     # subfic
            d, a, si = (word >> 21) & 31, (word >> 16) & 31, ((word & 0xFFFF) - ((word & 0x8000) << 1))
            r[d] = (si - s32(r[a])) & MASK32
        elif op in (28, 29):   # andi. / andis.
            s_, a, ui = (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
            r[a] = r[s_] & (ui << (16 if op == 29 else 0)) & MASK32
            self._setcr(0, s32(r[a]), 0)
        else:
            raise Halt('unhandled opcode %d at 0x%x' % (op, self.pc))

        self.pc = nxt & MASK32

    @staticmethod
    def _mask(mb, me):
        if mb <= me:
            return ((0xFFFFFFFF >> mb) & (0xFFFFFFFF << (31 - me))) & MASK32
        return (~(((0xFFFFFFFF >> (me + 1)) & (0xFFFFFFFF << (31 - mb + 1)))) ) & MASK32

    def _crbit(self, n):
        return self.cr[n >> 2][n & 3] if (n & 3) < 3 else False

    def _setcrbit(self, n, v):
        if (n & 3) < 3:
            self.cr[n >> 2][n & 3] = bool(v)

    def _op31(self, word, nxt):
        r = self.r
        m = self.mem
        xo = (word >> 1) & 0x3FF
        d, a, b = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
        rc = word & 1

        if xo == 266 or xo == 10:            # add / addc
            r[d] = (r[a] + r[b]) & MASK32
        elif xo == 40:                       # subf
            r[d] = (r[b] - r[a]) & MASK32
        elif xo == 8:                        # subfc
            r[d] = (r[b] - r[a]) & MASK32
        elif xo == 235 or xo == 235:         # mullw
            r[d] = (s32(r[a]) * s32(r[b])) & MASK32
        elif xo == 75:                       # mulhw
            r[d] = ((s32(r[a]) * s32(r[b])) >> 32) & MASK32
        elif xo == 11:                       # mulhwu
            r[d] = (((r[a] & MASK32) * (r[b] & MASK32)) >> 32) & MASK32
        elif xo == 491:                      # divw
            x, y = s32(r[a]), s32(r[b])
            q = 0 if y == 0 else int(x / y)
            r[d] = q & MASK32
        elif xo == 459:                      # divwu
            y = r[b] & MASK32
            r[d] = 0 if y == 0 else ((r[a] & MASK32) // y) & MASK32
        elif xo == 444:                      # or / mr
            r[a] = (r[d] | r[b]) & MASK32
        elif xo == 28:                       # and
            r[a] = (r[d] & r[b]) & MASK32
        elif xo == 316:                      # xor
            r[a] = (r[d] ^ r[b]) & MASK32
        elif xo == 476:                      # nand
            r[a] = (~(r[d] & r[b])) & MASK32
        elif xo == 124:                      # nor
            r[a] = (~(r[d] | r[b])) & MASK32
        elif xo == 104:                      # neg
            r[d] = (-s32(r[a])) & MASK32
        elif xo == 922:                      # extsh
            r[a] = s16(r[d]) & MASK32
        elif xo == 954:                      # extsb
            v = r[d] & 0xFF
            r[a] = (v - 0x100 if v & 0x80 else v) & MASK32
        elif xo == 24:                       # slw
            sh = r[b] & 63
            r[a] = 0 if sh > 31 else (r[d] << sh) & MASK32
        elif xo == 536:                      # srw
            sh = r[b] & 63
            r[a] = 0 if sh > 31 else (r[d] & MASK32) >> sh
        elif xo == 792:                      # sraw
            sh = r[b] & 63
            r[a] = (s32(r[d]) >> min(sh, 31)) & MASK32
        elif xo == 824:                      # srawi
            sh = b
            r[a] = (s32(r[d]) >> sh) & MASK32
        elif xo == 0:                        # cmpw
            self._setcr((word >> 23) & 7, s32(r[a]), s32(r[b]))
        elif xo == 32:                       # cmplw
            self._setcr((word >> 23) & 7, r[a] & MASK32, r[b] & MASK32)
        elif xo == 339:                      # mfspr
            spr = ((word >> 16) & 31) | (((word >> 11) & 31) << 5)
            r[d] = self.lr if spr == 8 else (self.ctr if spr == 9 else self.xer)
        elif xo == 467:                      # mtspr
            spr = ((word >> 16) & 31) | (((word >> 11) & 31) << 5)
            if spr == 8:
                self.lr = r[d]
            elif spr == 9:
                self.ctr = r[d]
            else:
                self.xer = r[d]
        elif xo == 23:                       # lwzx
            r[d] = m.r32((((r[a] if a else 0) + r[b])) & MASK32)
        elif xo == 279:                      # lhzx
            r[d] = m.r16((((r[a] if a else 0) + r[b])) & MASK32)
        elif xo == 87:                       # lbzx
            r[d] = m.r8((((r[a] if a else 0) + r[b])) & MASK32)
        elif xo == 151:                      # stwx
            m.w32((((r[a] if a else 0) + r[b])) & MASK32, r[d])
        elif xo == 407:                      # sthx
            m.w16((((r[a] if a else 0) + r[b])) & MASK32, r[d] & 0xFFFF)
        elif xo == 535:                      # lfsx
            self.f[d] = m.rf32((((r[a] if a else 0) + r[b])) & MASK32)
        elif xo == 663:                      # stfsx
            m.wf32((((r[a] if a else 0) + r[b])) & MASK32, self.f[d])
        elif xo == 598:                      # sync
            pass
        else:
            raise Halt('unhandled op31 xo=%d at 0x%x' % (xo, self.pc))

        if rc and xo in (444, 28, 316, 24, 536, 792, 824, 922, 266, 40):
            self._setcr(0, s32(r[a] if xo in (444, 28, 316, 24, 536, 792, 824, 922)
                               else r[d]), 0)
        return nxt

    def _fp(self, word, op):
        f = self.f
        d, a, b, c = ((word >> 21) & 31, (word >> 16) & 31,
                      (word >> 11) & 31, (word >> 6) & 31)
        xo = (word >> 1) & 0x1F
        xo10 = (word >> 1) & 0x3FF

        # Opcode 59 is the single-precision family (fadds, fmuls, fsubs,
        # fmadds ...): the arithmetic is done in double and the *result is
        # rounded to single*. Keeping the extra bits is not a harmless nicety
        # -- the synthesiser's resonators feed their own output back, so the
        # difference compounds, and a filter that is stable in single
        # precision can diverge when it is not rounded.
        if xo == 21:                                  # fadd(s)
            v = f[a] + f[b]
        elif xo == 20:                                # fsub(s)
            v = f[a] - f[b]
        elif xo == 25:                                # fmul(s)
            v = f[a] * f[c]
        elif xo == 18:                                # fdiv(s)
            v = f[a] / f[b] if f[b] else 0.0
        elif xo == 29:                                # fmadd(s)
            v = f[a] * f[c] + f[b]
        elif xo == 28:                                # fmsub(s)
            v = f[a] * f[c] - f[b]
        elif xo == 31:                                # fnmadd(s)
            v = -(f[a] * f[c] + f[b])
        elif xo == 30:                                # fnmsub(s)
            v = -(f[a] * f[c] - f[b])
        else:
            return self._fp_other(word, op, d, a, b, xo, xo10)
        f[d] = _single(v) if op == 59 else v

    def _fp_other(self, word, op, d, a, b, xo, xo10):
        f = self.f
        if xo10 == 72:                                # fmr
            f[d] = f[b]
        elif xo10 == 40:                              # fneg
            f[d] = -f[b]
        elif xo10 == 264:                             # fabs
            f[d] = abs(f[b])
        elif xo10 == 12:                              # frsp
            f[d] = _single(f[b])
        elif xo10 == 15:                              # fctiwz
            f[d] = _as_int_pattern(_to_int(f[b], trunc=True))
            return
        elif xo10 == 14:                              # fctiw
            f[d] = _as_int_pattern(_to_int(f[b], trunc=False))
            return
        elif xo10 in (0, 32):                         # fcmpu / fcmpo
            bf = (word >> 23) & 7
            x, y = f[a], f[b]
            self.cr[bf] = [x < y, x > y, x == y]
        else:
            raise Halt('unhandled fp op=%d xo=%d xo10=%d at 0x%x'
                       % (op, xo, xo10, self.pc))
