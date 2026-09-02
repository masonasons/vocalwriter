#!/usr/bin/env python3
"""Compile the C core. Run once per interpreter:

    pypy -m ppc.build_core
    python -m ppc.build_core

The Python interpreter in `ppc/cpu.py` stays the reference implementation and
the fallback; this only makes the same decisions faster. `ppc/fastcpu.py`
prefers the compiled core when it is present and imports cleanly.
"""
import os
import sys

import cffi

HERE = os.path.dirname(os.path.abspath(__file__))

DECLS = """
typedef struct CPU CPU;
CPU *cpu_new(void);
void cpu_free(CPU *c);
void mem_write(CPU *c, uint32_t a, const char *buf, uint32_t n);
void mem_read(CPU *c, uint32_t a, char *buf, uint32_t n);
uint32_t mem_r8(CPU *c, uint32_t a);
uint32_t mem_r16(CPU *c, uint32_t a);
uint32_t mem_r32(CPU *c, uint32_t a);
void mem_w8(CPU *c, uint32_t a, uint32_t v);
void mem_w16(CPU *c, uint32_t a, uint32_t v);
void mem_w32(CPU *c, uint32_t a, uint32_t v);
double mem_rf32(CPU *c, uint32_t a);
void mem_wf32(CPU *c, uint32_t a, double v);
double mem_rf64(CPU *c, uint32_t a);
void mem_wf64(CPU *c, uint32_t a, double v);
void cpu_set_hooks(CPU *c, const uint32_t *addrs, int n);
uint32_t cpu_get_reg(CPU *c, int i);
void cpu_set_reg(CPU *c, int i, uint32_t v);
double cpu_get_f(CPU *c, int i);
void cpu_set_f(CPU *c, int i, double v);
uint32_t cpu_get_pc(CPU *c);
void cpu_set_pc(CPU *c, uint32_t v);
uint32_t cpu_get_lr(CPU *c);
void cpu_set_lr(CPU *c, uint32_t v);
uint64_t cpu_get_steps(CPU *c);
uint32_t cpu_overflows(CPU *c);
int cpu_err_op(CPU *c);
int cpu_err_xo(CPU *c);
int cpu_run(CPU *c, uint64_t max_steps);
"""


def build(verbose=True):
    ffi = cffi.FFI()
    ffi.cdef(DECLS)
    with open(os.path.join(HERE, 'core.c')) as fh:
        source = fh.read()
    ffi.set_source('ppc._ppccore', source)
    # The module name is a package path, and cffi resolves it against tmpdir,
    # so tmpdir has to be the directory `ppc` lives in -- not `ppc` itself, or
    # the extension is built into ppc/ppc/ where nothing looks for it.
    out = ffi.compile(tmpdir=os.path.dirname(HERE), verbose=verbose)
    return out


if __name__ == '__main__':
    print('building with', sys.executable)
    print('->', build())
