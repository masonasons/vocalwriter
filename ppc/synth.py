#!/usr/bin/env python3
"""Drive VocalWriter's own synthesiser under the PowerPC interpreter.

This is not a model of VocalWriter. It sets up the memory the engine expects,
then calls the engine's own functions -- `InitSharedTables`, `InitGlobals_Speech`,
`PgmChange_Speech`, `Speech_Note`, `SayFrame` -- so the samples that come out
are produced by the original code.

Memory layout the engine needs:

    _g_DataHandle  -> Handle -> the `ttvi` resource, from which SetTblAddr
                      carves every shared table and Make_F_Table builds the
                      frequency tables at startup.
    g              -> the synthesiser globals. g[0x2ec4] is _g_SpeechTbls,
                      g[0x2ec8 + chan*4] the per-channel contexts, g[0x2f90]
                      the mvox voice bank and g[0x2f94] the output buffer.
    mvox           -> GMSpeech's voice bank: a 128-entry program->voice map at
                      +0, and at +0x200 an array of offsets to the 336-byte
                      voice records, which have to be relocated to absolute
                      addresses the way the application does on load.
"""
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import paths                               # noqa: E402
from ppc.image import Machine                       # noqa: E402
from tools.machrsrc import ResourceFork             # noqa: E402

GMSPEECH = paths.asset('assets', 'GMSpeech.rsrc')

G_SPEECH_TBLS = 0x2ec4
G_CHANNELS = 0x2ec8
G_MISC = 0x2e9c
G_VOICEBANK = 0x2f90
G_OUTBUF = 0x2f94

VOICE_PTRS = 0x200          # offset of the voice pointer array inside mvox
GLOBALS_SIZE = 0x4000
CONTEXT_SIZE = 0x4000
OUT_SAMPLES = 1 << 21


class VocalWriter(object):
    def __init__(self, gmspeech=GMSPEECH):
        self.m = Machine()
        self._install_hooks()
        self._init_tables()
        self._init_globals(gmspeech)

    # -- setup -------------------------------------------------------------

    def _install_hooks(self):
        m = self.m

        def newptr(cpu):
            cpu.r[3] = m.alloc(max(cpu.r[3] & 0xFFFFFFFF, 16))

        # the allocator stub InitSharedTables calls for its 0x4800 block
        m.cpu.hooks[0xa63a0] = newptr

        # The only two library calls the synthesiser makes, identified from
        # their call sites: DoNote passes two floats and uses the result as a
        # frequency ratio (pow); Syllable_Duration passes one float and rounds
        # the result with fctiwz (floor).
        def _pow(cpu):
            try:
                cpu.f[1] = math.pow(cpu.f[1], cpu.f[2])
            except (ValueError, OverflowError):
                cpu.f[1] = 0.0

        def _floor(cpu):
            cpu.f[1] = math.floor(cpu.f[1])

        m.cpu.hooks[0xa64a0] = _pow
        m.cpu.hooks[0xa6540] = _floor

    def _init_tables(self):
        m = self.m
        addr, size = m.load_resource('ttvi', 2)
        self.ttvi, self.ttvi_size = addr, size
        m.mem.w32(m.globals_ptr('_g_DataHandle'), m.handle_to(addr))
        m.call('InitSharedTables')
        self.speech_tbls = m.mem.r32(m.globals_ptr('_g_SpeechTbls'))

    def _load_voices(self, path):
        data = ResourceFork.from_file(path).get('mvox', 1).data
        base = self.m.alloc(len(data), zero=False)
        self.m.mem.write(base, data)
        # relocate the voice pointer array: stored as offsets, used as pointers
        n = (0x360 - VOICE_PTRS) // 4
        for i in range(n):
            p = base + VOICE_PTRS + i * 4
            off = self.m.mem.r32(p)
            if 0 < off < len(data):
                self.m.mem.w32(p, base + off)
        return base

    def _init_globals(self, gmspeech):
        m = self.m
        self.g = m.alloc(GLOBALS_SIZE)
        self.ctx = m.alloc(CONTEXT_SIZE)
        self.outbuf = m.alloc(OUT_SAMPLES * 2)
        self.voices = self._load_voices(gmspeech)

        m.mem.w32(self.g + G_SPEECH_TBLS, self.speech_tbls)
        m.mem.w32(self.g + G_CHANNELS, self.ctx)
        m.mem.w32(self.g + G_VOICEBANK, self.voices)
        m.mem.w32(self.g + G_OUTBUF, self.outbuf)
        m.mem.w32(self.g + G_MISC, m.alloc(0x400))
        m.call('InitGlobals_Speech', self.g, 0)

    # -- use ---------------------------------------------------------------

    def program(self, prog):
        """Select a voice, the way a program change does."""
        self.m.call('PgmChange_Speech', self.g, 0, prog)

    def voice_name(self):
        idx = self.m.mem.r16(self.ctx + 0x1088)
        rec = self.m.mem.r32(self.m.mem.r32(self.ctx + 0xfcc) + idx * 4)
        n = self.m.mem.r8(rec)
        return self.m.mem.read(rec + 1, n).decode('mac-roman', 'replace')


if __name__ == '__main__':
    vw = VocalWriter()
    print('tables at 0x%x, globals 0x%x, context 0x%x'
          % (vw.speech_tbls, vw.g, vw.ctx))
    print('steps so far: %d' % vw.m.cpu.steps)
    for prog in (0, 4, 1):
        vw.program(prog)
        print('  program %-3d -> voice %r' % (prog, vw.voice_name()))
