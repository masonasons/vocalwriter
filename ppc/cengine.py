#!/usr/bin/env python3
"""VocalWriter's synthesiser through the C recreation, instead of the interpreter.

The engine is the same engine either way: `ppc/cpu.py` runs KAE Labs' PowerPC
code instruction by instruction, and the VocalWriterC project is that same code
lifted into C and checked against it -- context field by context field after
every frame, and sample for sample against the AIFFs VocalWriter itself
exported. What changes here is only how long it takes. A minute of singing
takes the interpreter about a minute; it takes the C engine a few hundredths of
a second.

This is the same sequence of calls `ppc/render.py` has always made -- the
sequence block, the voice controls, a note when the engine asks for one, a
frame at a time -- through `vw_editor.h`, which exists for exactly this. The
interpreter stays as the reference the C engine is measured against
(`ppc/synth.py`, and VocalWriterC's own differential tests); it is no longer
what plays your song.

The library is looked for beside the program, then in a sibling VocalWriterC
checkout, and $VOCALWRITER_LIB overrides both.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import paths                                        # noqa: E402

#: what the shared library is called, per platform
LIB_NAMES = {'win32': 'libvocalwriter.dll',
             'darwin': 'libvocalwriter.dylib'}
LIB_NAME = LIB_NAMES.get(sys.platform, 'libvocalwriter.so')

CDEF = """
typedef struct vw_editor vw_editor;

vw_editor *vw_ed_open(const unsigned char *rsrc, size_t rsrc_len,
                      const unsigned char *gmspeech, size_t gmspeech_len);
void vw_ed_close(vw_editor *e);

void vw_ed_tempo_scale(vw_editor *e, float mul);
void vw_ed_tempo(vw_editor *e, int bpm);
void vw_ed_program(vw_editor *e, int program);
int vw_ed_sequence(vw_editor *e, const unsigned char *blob, size_t len);
void vw_ed_start(vw_editor *e);
void vw_ed_defaults(vw_editor *e, int glide);
void vw_ed_volume(vw_editor *e, int32_t value);
int vw_ed_control(vw_editor *e, const char *name, int32_t value);

void vw_ed_note(vw_editor *e, int key, int nextKey, int velocity, double beats);
int vw_ed_frames(vw_editor *e, int count);
int vw_ed_state(vw_editor *e);
int vw_ed_wants_note(vw_editor *e);
int32_t vw_ed_wave_index(vw_editor *e);
const int16_t *vw_ed_wave(vw_editor *e);

int vw_ed_hf_emph(vw_editor *e);
float vw_ed_emph_a(vw_editor *e);
float vw_ed_emph_b(vw_editor *e);
void vw_ed_set_emph(vw_editor *e, float a, float b);
float vw_ed_speech_volume(vw_editor *e);
void vw_ed_set_speech_volume(vw_editor *e, float v);

const char *vw_ed_voice_name(vw_editor *e);

int vw_ed_lexicon(vw_editor *e, const unsigned char *data, size_t len);
int vw_ed_word(vw_editor *e, const char *text, unsigned char *out);
const char *vw_ed_phoneme_name(int code);
"""

_ffi = None
_lib = None
#: why the library could not be loaded, for the caller to report
REASON = ''


def library_candidates():
    """Where to look, nearest first."""
    env = os.environ.get('VOCALWRITER_LIB')
    if env:
        yield env
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if paths.frozen():
        # a build carries it beside the executable's own files
        yield paths.bundled('lib', LIB_NAME)
        yield paths.bundled(LIB_NAME)
    yield os.path.join(root, 'lib', LIB_NAME)
    # the submodule, built in place: this is where a checkout of this
    # repository builds it (see engine/build.sh)
    yield os.path.join(root, 'engine', 'build', LIB_NAME)
    # a VocalWriterC checkout beside this one, which is how it is developed
    yield os.path.join(os.path.dirname(root), 'VocalWriterC', 'build', LIB_NAME)


def load():
    """The library, or None. The reason for a failure is left in REASON."""
    global _ffi, _lib, REASON
    if _lib is not None:
        return _lib
    try:
        import cffi
    except ImportError as exc:
        REASON = 'cffi is not installed: %s' % exc
        return None
    tried = []
    for path in library_candidates():
        if not path or not os.path.isfile(path):
            tried.append(path)
            continue
        ffi = cffi.FFI()
        ffi.cdef(CDEF)
        try:
            _lib = ffi.dlopen(path)
        except OSError as exc:
            REASON = 'cannot load %s: %s' % (path, exc)
            return None
        _ffi = ffi
        return _lib
    REASON = 'not found; looked in %s' % ', '.join(t for t in tried if t)
    return None


def available():
    return load() is not None


def describe():
    if load() is not None:
        return 'C engine'
    return 'PowerPC interpreter (%s)' % (REASON or 'no C engine')


class Editor(object):
    """One voice, its own context, driven a frame at a time.

    The same surface `ppc.synth.Editor` presents over the interpreter, so
    `ppc/render.py` does not know which engine it is talking to. Anything the
    engine is asked for by name -- a control, a field of the context -- is
    named the same in both.
    """

    #: the samples the engine writes are native halfwords here; under the
    #: interpreter they are big-endian, which is the only difference
    dtype = '<i2'

    def __init__(self, rsrc=None, gmspeech=None):
        lib = load()
        if lib is None:
            raise RuntimeError('the C engine is not available: %s' % REASON)
        self._lib = lib
        rsrc = rsrc or paths.asset('assets', 'VocalWriter.app', 'Contents',
                                   'Resources', 'VocalWriter.rsrc')
        gmspeech = gmspeech or paths.asset('assets', 'GMSpeech.rsrc')
        with open(rsrc, 'rb') as fh:
            self._rsrc = fh.read()
        with open(gmspeech, 'rb') as fh:
            self._gm = fh.read()
        self._e = lib.vw_ed_open(self._rsrc, len(self._rsrc),
                                 self._gm, len(self._gm))
        if self._e == _ffi.NULL:
            raise RuntimeError('the C engine would not start: check %s and %s'
                               % (rsrc, gmspeech))

    def close(self):
        if getattr(self, '_e', None):
            self._lib.vw_ed_close(self._e)
            self._e = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- setting up --------------------------------------------------------

    def tempo_scale(self, mul):
        self._lib.vw_ed_tempo_scale(self._e, mul)

    def tempo(self, bpm):
        self._lib.vw_ed_tempo(self._e, int(bpm))

    def program(self, prog):
        self._lib.vw_ed_program(self._e, int(prog))

    def sequence(self, blob):
        self._lib.vw_ed_sequence(self._e, blob, len(blob))

    def start(self):
        self._lib.vw_ed_start(self._e)

    def defaults(self, glide=True):
        self._lib.vw_ed_defaults(self._e, 1 if glide else 0)

    def volume(self, value):
        self._lib.vw_ed_volume(self._e, int(value))

    def control(self, name, value):
        if self._lib.vw_ed_control(self._e, name.encode('ascii'),
                                   int(value)) != 0:
            raise ValueError('no such control: %s' % name)

    # -- rendering ---------------------------------------------------------

    def note(self, key, next_key, velocity, beats):
        self._lib.vw_ed_note(self._e, int(key), int(next_key), int(velocity),
                             float(beats))

    def frames(self, count=1):
        return self._lib.vw_ed_frames(self._e, int(count))

    @property
    def state(self):
        return self._lib.vw_ed_state(self._e)

    @property
    def wants_note(self):
        return self._lib.vw_ed_wants_note(self._e)

    @property
    def wave_index(self):
        return self._lib.vw_ed_wave_index(self._e)

    def wave(self):
        """The engine's output halfwords, as they stand."""
        n = self._lib.vw_ed_wave_index(self._e)
        buf = _ffi.buffer(self._lib.vw_ed_wave(self._e), int(n) * 2)
        return np.frombuffer(buf, dtype=self.dtype).astype(np.float32)

    # -- the radiation shelf -----------------------------------------------

    @property
    def hf_emph(self):
        return self._lib.vw_ed_hf_emph(self._e)

    @property
    def emphasis(self):
        """(a, b): the shelf's two coefficients. `a` is the one to scale."""
        return (self._lib.vw_ed_emph_b(self._e),
                self._lib.vw_ed_emph_a(self._e))

    @emphasis.setter
    def emphasis(self, ab):
        a, b = ab
        # The context calls them the other way round from the driver: emphB is
        # the coefficient at +0xcf4, which is the one the shelf is tilted by.
        self._lib.vw_ed_set_emph(self._e, b, a)

    @property
    def speech_volume(self):
        return self._lib.vw_ed_speech_volume(self._e)

    @speech_volume.setter
    def speech_volume(self, v):
        self._lib.vw_ed_set_speech_volume(self._e, float(v))

    def voice_name(self):
        name = self._lib.vw_ed_voice_name(self._e)
        if name == _ffi.NULL:
            return ''
        return _ffi.string(name).decode('mac_roman', 'replace')

    # -- words -------------------------------------------------------------

    def lexicon(self, data):
        """Hand over `EnglishLex`, which words are looked up in."""
        self._lex = data                 # the engine reads it where it lies
        if self._lib.vw_ed_lexicon(self._e, data, len(data)) != 0:
            raise RuntimeError('the dictionary would not open')

    def word(self, text):
        """One word as syllables of phoneme codes, through the application's
        own dictionary search, suffix rules and letter-to-sound rules."""
        out = _ffi.new('unsigned char[]', 10 * 9)
        n = self._lib.vw_ed_word(self._e, text.encode('mac_roman', 'replace'),
                                 out)
        if n < 0:
            raise RuntimeError('no dictionary: call lexicon() first')
        return [[out[i * 9 + 1 + k] for k in range(out[i * 9])]
                for i in range(n)]
