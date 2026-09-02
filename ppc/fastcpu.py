#!/usr/bin/env python3
"""The compiled core behind the same interface as `ppc/cpu.py`.

`ppc/cpu.py` remains the reference: it is what was debugged against
VocalWriter's own output, and it is the fallback wherever the extension has not
been built. This module presents identical `Memory` and `CPU` objects backed by
`ppc/core.c`, so nothing above it has to know which is running.

Build it with `python -m ppc.build_core` (or `pypy -m ppc.build_core`), once per
interpreter.
"""
import sys

from ppc.cpu import Halt                                     # noqa: F401

try:
    from ppc._ppccore import ffi, lib
    AVAILABLE = True
    REASON = ''
except Exception as _exc:                                    # not built here
    ffi = lib = None
    AVAILABLE = False
    REASON = '%s: %s' % (type(_exc).__name__, _exc)

SENTINEL = 0xDEAD0000


class Memory(object):
    """The C page table, addressed the way `ppc.cpu.Memory` is."""

    def __init__(self, core):
        self._c = core

    def write(self, addr, data):
        b = bytes(data)
        lib.mem_write(self._c, addr, b, len(b))

    def read(self, addr, n):
        buf = ffi.new('char[]', n)
        lib.mem_read(self._c, addr, buf, n)
        return ffi.buffer(buf, n)[:]

    def r8(self, a):
        return lib.mem_r8(self._c, a)

    def w8(self, a, v):
        lib.mem_w8(self._c, a, v & 0xFF)

    def r16(self, a):
        return lib.mem_r16(self._c, a)

    def w16(self, a, v):
        lib.mem_w16(self._c, a, v & 0xFFFF)

    def r32(self, a):
        return lib.mem_r32(self._c, a)

    def w32(self, a, v):
        lib.mem_w32(self._c, a, v & 0xFFFFFFFF)

    def rf32(self, a):
        return lib.mem_rf32(self._c, a)

    def wf32(self, a, v):
        lib.mem_wf32(self._c, a, v)

    def rf64(self, a):
        return lib.mem_rf64(self._c, a)

    def wf64(self, a, v):
        lib.mem_wf64(self._c, a, v)

    @property
    def overflows(self):
        return lib.cpu_overflows(self._c)


class _Regs(object):
    """r[] and f[] as ordinary mutable sequences, backed by the C struct."""

    def __init__(self, core, getter, setter):
        self._c, self._get, self._set = core, getter, setter

    def __getitem__(self, i):
        return self._get(self._c, i)

    def __setitem__(self, i, v):
        self._set(self._c, i, v)

    def __len__(self):
        return 32


class CPU(object):
    def __init__(self, mem=None):
        if not AVAILABLE:
            raise ImportError('the compiled core is not built here')
        self._c = lib.cpu_new()
        self.mem = Memory(self._c) if mem is None else mem
        self.r = _Regs(self._c, lib.cpu_get_reg,
                       lambda c, i, v: lib.cpu_set_reg(c, i, v & 0xFFFFFFFF))
        self.f = _Regs(self._c, lib.cpu_get_f, lib.cpu_set_f)
        self.hooks = {}
        self._hooked = None

    # -- the few registers callers touch by name ---------------------------

    @property
    def pc(self):
        return lib.cpu_get_pc(self._c)

    @pc.setter
    def pc(self, v):
        lib.cpu_set_pc(self._c, v & 0xFFFFFFFF)

    @property
    def lr(self):
        return lib.cpu_get_lr(self._c)

    @lr.setter
    def lr(self, v):
        lib.cpu_set_lr(self._c, v & 0xFFFFFFFF)

    @property
    def steps(self):
        return lib.cpu_get_steps(self._c)

    # -- execution ---------------------------------------------------------

    def _sync_hooks(self):
        keys = tuple(sorted(self.hooks))
        if keys == self._hooked:
            return
        arr = ffi.new('uint32_t[]', list(keys) or [0])
        lib.cpu_set_hooks(self._c, arr, len(keys))
        self._hooked = keys

    def call(self, addr, max_steps=200000000):
        """Run until the function returns; hooks come back out to Python."""
        self._sync_hooks()
        self.lr = SENTINEL
        self.pc = addr
        while True:
            status = lib.cpu_run(self._c, max_steps)
            if status == 0:
                return self.r[3]
            if status == 1:
                self.hooks[self.pc](self)
                self.pc = self.lr
                continue
            if status == 2:
                raise Halt('step limit at pc=0x%x' % self.pc)
            raise Halt('unhandled opcode %d (xo=%d) at 0x%x'
                       % (lib.cpu_err_op(self._c), lib.cpu_err_xo(self._c),
                          self.pc))

    def __del__(self):
        try:
            if getattr(self, '_c', None) is not None and lib is not None:
                lib.cpu_free(self._c)
                self._c = None
        except Exception:
            pass


def describe():
    if AVAILABLE:
        return 'compiled core (%s)' % sys.platform
    return 'pure-Python interpreter (%s)' % (REASON or 'not built')
