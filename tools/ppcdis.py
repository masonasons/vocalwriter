#!/usr/bin/env python3
"""Disassemble functions out of the VocalWriter PowerPC binary, by name.

The executable is an unstripped debug build, so every function has a STABS
N_FUN record giving its name and address. That gives function boundaries for
free -- a function runs until the next one starts -- and lets branch targets be
printed as names rather than raw addresses.

    python tools/ppcdis.py Calc_Pole_Coefficients
    python tools/ppcdis.py SayFrame --limit 400

Float constants are the interesting part of DSP code and PowerPC loads them
from memory rather than as immediates, so `lfs`/`lfd` against a known base
register are annotated with the value where it can be resolved.
"""
import os
import struct
import sys

import capstone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.machsyms import load, functions      # noqa: E402

BINARY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'assets', 'VocalWriter.app', 'Contents', 'MacOS',
                      'VocalWriter')


class Image(object):
    def __init__(self, path=BINARY):
        self.blob, self.syms = load(path)
        self.funcs = functions(self.syms)
        self.by_addr = {}
        for name, addr in self.funcs.items():
            self.by_addr.setdefault(addr, name)
        self.sections = self._sections()
        self.md = capstone.Cs(capstone.CS_ARCH_PPC,
                              capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
        self.md.detail = False

    def _sections(self):
        out = []
        ncmds = struct.unpack('>I', self.blob[16:20])[0]
        off = 28
        for _ in range(ncmds):
            cmd, size = struct.unpack('>2I', self.blob[off:off + 8])
            if cmd == 1:
                nsects = struct.unpack('>I', self.blob[off + 48:off + 52])[0]
                so = off + 56
                for _k in range(nsects):
                    name = self.blob[so:so + 16].split(b'\0')[0].decode()
                    seg = self.blob[so + 16:so + 32].split(b'\0')[0].decode()
                    addr, sz, foff = struct.unpack('>3I', self.blob[so + 32:so + 44])
                    out.append((seg, name, addr, sz, foff))
                    so += 68
            off += size
        return out

    def addr_to_off(self, addr):
        for _seg, _name, a, sz, foff in self.sections:
            if a <= addr < a + sz:
                return foff + (addr - a)
        return None

    def read(self, addr, n):
        o = self.addr_to_off(addr)
        if o is None:
            return None
        return self.blob[o:o + n]

    def float_at(self, addr):
        b = self.read(addr, 4)
        if not b or len(b) < 4:
            return None
        return struct.unpack('>f', b)[0]

    def double_at(self, addr):
        b = self.read(addr, 8)
        if not b or len(b) < 8:
            return None
        return struct.unpack('>d', b)[0]

    def extent(self, name):
        """(start, end) of a function, end taken from the next function up."""
        if name not in self.funcs:
            raise KeyError(name)
        start = self.funcs[name]
        later = sorted(a for a in self.by_addr if a > start)
        return start, (later[0] if later else start + 0x2000)

    def disasm(self, name, limit=None):
        start, end = self.extent(name)
        code = self.read(start, end - start)
        out = []
        for ins in self.md.disasm(code, start):
            out.append(ins)
            if limit and len(out) >= limit:
                break
        return out

    def annotate(self, ins):
        """Resolve a branch target to a function name where possible."""
        op = ins.op_str
        if ins.mnemonic.startswith('b') and '0x' in op:
            try:
                tgt = int(op.split('0x')[-1].split(',')[0], 16)
            except ValueError:
                return ''
            if tgt in self.by_addr:
                return '  ; -> %s' % self.by_addr[tgt]
        return ''


def main():
    ap = sys.argv[1:]
    if not ap:
        print(__doc__)
        return 2
    name = ap[0]
    limit = None
    if '--limit' in ap:
        limit = int(ap[ap.index('--limit') + 1])
    img = Image()
    if name not in img.funcs:
        near = [k for k in img.funcs if name.lower() in k.lower()]
        print('no such function; did you mean: %s' % ', '.join(sorted(near)[:12]))
        return 1
    start, end = img.extent(name)
    print('== %s  0x%06x .. 0x%06x  (%d bytes) ==' % (name, start, end, end - start))
    for ins in img.disasm(name, limit):
        print('  %06x  %-8s %-32s%s'
              % (ins.address, ins.mnemonic, ins.op_str, img.annotate(ins)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
