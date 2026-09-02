#!/usr/bin/env python3
"""Load VocalWriter's Mach-O into interpreter memory and call its functions.

The synthesiser needs three things in memory before it will run: the
executable's own segments (code, constants, initialised data), the `ttvi`
resource that every table is carved out of, and a heap for the context structs
the engine allocates at startup.

Nothing here emulates Mac OS. The synthesis routines are pure arithmetic on
memory the caller provides, so the loader only has to place bytes and hand out
addresses.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import paths                               # noqa: E402
from ppc.cpu import CPU, Memory                     # noqa: E402

try:                                                 # the compiled core, if built
    from ppc import fastcpu
    FAST = fastcpu.AVAILABLE
except ImportError:
    fastcpu = None
    FAST = False
from tools.machrsrc import ResourceFork             # noqa: E402
from tools.machsyms import functions, load          # noqa: E402

BINARY = paths.asset('assets', 'VocalWriter.app', 'Contents', 'MacOS',
                     'VocalWriter')
RSRC = paths.asset('assets', 'VocalWriter.app', 'Contents', 'Resources',
                   'VocalWriter.rsrc')

HEAP_BASE = 0x20000000
STACK_TOP = 0x30000000


class Machine(object):
    def __init__(self, binary=BINARY, rsrc=RSRC):
        self.blob, self.syms = load(binary)
        self.funcs = functions(self.syms)
        # The C core and the Python interpreter make the same decisions; the
        # compiled one is preferred purely for speed and falls back silently.
        if FAST:
            self.cpu = fastcpu.CPU()
            self.mem = self.cpu.mem
        else:
            self.mem = Memory()
            self.cpu = CPU(self.mem)
        self._load_segments()
        self.heap = HEAP_BASE
        self._install_stack()
        self.rsrc = ResourceFork.from_file(rsrc) if rsrc else None

    # -- loading -----------------------------------------------------------

    def _load_segments(self):
        ncmds = struct.unpack('>I', self.blob[16:20])[0]
        off = 28
        self.sections = []
        for _ in range(ncmds):
            cmd, size = struct.unpack('>2I', self.blob[off:off + 8])
            if cmd == 1:
                name = self.blob[off + 8:off + 24].split(b'\0')[0].decode()
                vmaddr, vmsize, fileoff, filesize = struct.unpack(
                    '>4I', self.blob[off + 24:off + 40])
                if name != '__PAGEZERO' and filesize:
                    self.mem.write(vmaddr,
                                   self.blob[fileoff:fileoff + filesize])
                nsects = struct.unpack('>I', self.blob[off + 48:off + 52])[0]
                so = off + 56
                for _k in range(nsects):
                    sn = self.blob[so:so + 16].split(b'\0')[0].decode()
                    addr, sz, fo = struct.unpack('>3I', self.blob[so + 32:so + 44])
                    self.sections.append((name, sn, addr, sz, fo))
                    so += 68
            off += size

    def _install_stack(self):
        self.cpu.r[1] = STACK_TOP - 0x1000
        # a plausible back-chain so prologues that walk it do not fault
        self.mem.w32(self.cpu.r[1], STACK_TOP)

    # -- allocation --------------------------------------------------------

    def alloc(self, size, zero=True):
        addr = self.heap
        self.heap = (self.heap + size + 0xF) & ~0xF
        if zero:
            self.mem.write(addr, bytes(size))
        return addr

    def load_resource(self, kind, rid):
        """Place a resource in memory; returns its address."""
        data = self.rsrc.get(kind, rid).data
        addr = self.alloc(len(data), zero=False)
        self.mem.write(addr, data)
        return addr, len(data)

    def handle_to(self, addr):
        """A Mac Handle: a pointer to a pointer."""
        h = self.alloc(4)
        self.mem.w32(h, addr)
        return h

    # -- calling -----------------------------------------------------------

    def addr(self, name):
        return self.funcs[name]

    def call(self, name_or_addr, *args, **kw):
        """Call a function with integer arguments in r3..r10."""
        a = (self.funcs[name_or_addr] if isinstance(name_or_addr, str)
             else name_or_addr)
        cpu = self.cpu
        cpu.r[1] = STACK_TOP - 0x1000
        self.mem.w32(cpu.r[1], STACK_TOP)
        for i, v in enumerate(args):
            cpu.r[3 + i] = v & 0xFFFFFFFF
        for i, v in enumerate(kw.get('floats', ())):
            cpu.f[1 + i] = v
        return cpu.call(a)

    def globals_ptr(self, name):
        """Address of a named global (they live in __common/__data)."""
        for nm, ntype, nsect, ndesc, value in self.syms:
            if nm == name and value:
                return value
        raise KeyError(name)


if __name__ == '__main__':
    m = Machine()
    print('loaded %d functions; heap at 0x%x' % (len(m.funcs), HEAP_BASE))
    print('_g_SpeechTbls at 0x%x' % m.globals_ptr('_g_SpeechTbls'))
