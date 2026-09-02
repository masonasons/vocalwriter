#!/usr/bin/env python3
"""Symbol table reader for the (unstripped) VocalWriter PowerPC binary.

The shipped executable is a debug build: LC_SYMTAB carries 76,190 entries and a
400 KB string table, including STABS N_FUN records that give every function's
name, address and type signature. That is what makes reverse-engineering the
synthesiser tractable at all.
"""
import struct

N_FUN = 0x24
N_LSYM = 0x80
N_GSYM = 0x20
N_STSYM = 0x26


def load(path):
    with open(path, 'rb') as fh:
        blob = fh.read()
    ncmds = struct.unpack('>I', blob[16:20])[0]
    off, sym = 28, None
    for _ in range(ncmds):
        cmd, size = struct.unpack('>2I', blob[off:off + 8])
        if cmd == 2:
            sym = struct.unpack('>4I', blob[off + 8:off + 24])
        off += size
    if sym is None:
        raise ValueError('no LC_SYMTAB')
    symoff, nsyms, stroff, strsize = sym
    strs = blob[stroff:stroff + strsize]

    out = []
    for i in range(nsyms):
        p = symoff + i * 12
        strx, ntype, nsect, ndesc, value = struct.unpack('>IBBhI', blob[p:p + 12])
        if strx == 0 or strx >= strsize:
            continue
        end = strs.find(b'\0', strx)
        out.append((strs[strx:end].decode('utf-8', 'replace'),
                    ntype, nsect, ndesc, value))
    return blob, out


def functions(syms):
    """{name: address} from the STABS N_FUN records."""
    out = {}
    for nm, ntype, _, _, value in syms:
        if ntype == N_FUN and ':' in nm and value:
            out[nm.split(':', 1)[0]] = value
    return out


if __name__ == '__main__':
    import re
    import sys
    blob, syms = load(sys.argv[1])
    fns = functions(syms)
    pat = re.compile(sys.argv[2], re.I) if len(sys.argv) > 2 else None
    rows = sorted((v, k) for k, v in fns.items()
                  if not pat or pat.search(k))
    for a, n in rows:
        print('0x%06x  %s' % (a, n))
    print('-- %d of %d functions' % (len(rows), len(fns)))
