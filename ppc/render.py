#!/usr/bin/env python3
"""Render singing by running VocalWriter's own synthesiser.

Nothing here models the sound. It builds the memory the engine expects, hands
it notes, and lets the original PowerPC code fill the output buffer, so the
samples are the ones VocalWriter itself would produce.

The engine's shape, recovered from the binary:

  * A sequence is one packed block (see `SetSeqAddr`): a voice key, a count,
    then four parallel u16 arrays -- phoneme, control, spare, duration. Bit 0
    of the control word marks a phoneme that *begins a note*, and the sequence
    must be terminated by one, because `Syllable_Duration` scales a syllable by
    scanning forward to the next note-start and gives up if it never finds one.

  * `Syllable_Duration` rescales the nominal durations to fit the note:
    `scale = (frames << 16) / sum(nominal)`, `dur[i] = max(1, dur[i]*scale >> 16)`,
    padding the first phoneme until the total matches. So the durations only
    matter as ratios, and the phoneme table's milliseconds can go in directly.

  * `e_Fill_Next_Frame` is a state machine on ctx[0x1050]: 1 starts a phoneme,
    2 emits frames, 3 is done. When a syllable ends it sets ctx[0x10ba] and
    waits -- that is the engine asking for the next note, which is what drives
    the loop below.

  * `SayFrame` writes 220 sample pairs per frame, both channels identical, at
    44100 Hz -- the rate VocalWriter's own AIFF exports use. The glottal phase
    increment confirms it: `SayFrame` looks the pitch up in ctx[0xd0c] and
    shifts it by the octave into ctx[0xc64], and reading that as 7.25 fixed
    point against 44100 gives 110.2 Hz for MIDI 45 and 219.3 Hz for MIDI 57,
    within a tenth of a percent of equal temperament.
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc.synth import VocalWriter                              # noqa: E402
from tools.ttvi import durations, load, phoneme_order          # noqa: E402

SAMPLE_RATE = 44100
FRAMES_PER_SEC = 200.45
SAMPLES_PER_FRAME = 220

BPM = 120.0
TEMPO_SCALE = 1.0 / 240.0       # calibrated so frames/beat matches 200.45 fps

CTX_SEQ_COUNT = 0xffc
CTX_DURATIONS = 0xff8
CTX_VOICE_IDX = 0x1088
CTX_CURSOR = 0x100e
CTX_STATE = 0x1050
CTX_WAIT = 0x10ba
CTX_OUT_POS = 0x1080
CTX_AV = 0x316
#: the glide table the portamento control reads, as a pointer in the context
CTX_GLIDE_TBL = 0x1070
G_TEMPO_SCALE = 0x3274
G_FRAMES_PER_BEAT = 0x3268

MAX_FRAMES = 200000

#: Speech_PitchBend computes (sensitivity * value) >> 16, and the sensitivity
#: Speech_PBSens stores is in pitch units of 256 per octave -- 256 for a range
#: of 12 semitones. For a full-scale bend to reach the whole range the value
#: therefore has to span +/-65536, so MIDI's 14-bit bend is passed times 8.
#: Measured: full bend at a 12-semitone range lands on 439.9 Hz from A3, an
#: error of 0 cents, and stays within 3 cents across the range.
BEND_SCALE = 8

#: Scale on the radiation shelf `out = (2.5 - a)*x - a*x[n-1]`, where InitVoice
#: sets a = voice[0x110]/100 (about 1.03 for most voices). 1.0 is the engine's
#: own value; the knob exists because the application has a Brightness control
#: applied at render time that the MIDI export does not carry.
#:
#: It was briefly set to 0.83, fitted to the long-term average spectrum. That
#: fit was wrong: the long-term average is dominated by consonants, and the
#: consonants and the vowels are wrong in *opposite* directions -- comparing
#: frames that are the same note, time-aligned, the vowels are 10-13 dB *dark*
#: above 4 kHz while the unvoiced frames are ~6 dB bright. One shelf cannot
#: correct both, which is why its optimum looked so shallow. Measured against
#: aligned voiced frames, the engine's own 1.0 wins (8.00 dB rms against 8.85),
#: so no fudge is applied.
BRIGHTNESS = 1.0

#: The voice controls VocalWriter exposes, as
#: (key, engine call, default, low, high, label, hint).
#:
#: The defaults are the engine's own, read out of the context after
#: InitDefaultVoiceCntrls has run. A control is sent only when it differs from
#: its default, so a song that sets none of them sounds exactly as it did
#: before they existed -- which matters, because the values do not round-trip
#: perfectly: colour's default mix is 0.75 and the nearest the 0-127 control
#: can say is 95, or 0.748.
#:
#: Portamento reads a table through ctx[0x1070], which is a pointer the
#: application fills in `Synth_Startup` -- part of its audio startup, not of
#: the synthesis path -- so it used to point at a blank buffer here and the
#: control did nothing. The table is `_g_Time_Tbl`, which `InitSharedTables`
#: does load; see `_voice_controls` for where the two are joined up, and why
#: that happens after the defaults are set rather than before.
VOICE_CONTROLS = (
    ('color', 'Speech_Color', 95, 0, 127, 'Colour',
     'brighter voice as it rises'),
    ('vibrato', 'Speech_VibDepth', 31, 0, 127, 'Vibrato depth', '0 for none'),
    ('vibrato_rate', 'Speech_VibFreq', 47, 0, 127, 'Vibrato rate',
     'how fast it wavers'),
    ('chorus', 'Speech_Chorus', 0, 0, 127, 'Chorus', 'thickens the voice'),
    ('breath', 'Speech_Breath', 0, 0, 127, 'Breath', 'adds air to the tone'),
    ('detune', 'Speech_Detune', 0, -8192, 8191, 'Detune',
     'a shade sharp or flat'),
    ('portamento', 'Speech_Portamento', 0, 0, 127, 'Portamento',
     'glide between notes; 0 goes straight there'),
)

#: {key: default}
VOICE_DEFAULTS = dict((k, d) for k, _c, d, _lo, _hi, _l, _h in VOICE_CONTROLS)


def clean_voice(values):
    """A voice setting dictionary with only known keys, clamped to range."""
    out = {}
    for key, _call, default, lo, hi, _label, _hint in VOICE_CONTROLS:
        try:
            v = int((values or {}).get(key, default))
        except (TypeError, ValueError):
            v = default
        out[key] = max(lo, min(hi, v))
    return out


RAD_A = 0xcf4          # radiation shelf coefficient a
RAD_B = 0xcf0          # and 2 - a
G_TOTAL_VOL = 0xfbc    # the factor every voiced sample is scaled by


class Note(object):
    """One sung note: a pitch, a length in beats, and its phonemes."""

    def __init__(self, midi, beats, phonemes, velocity=100, durations=None):
        self.midi = int(midi)
        self.beats = float(beats)
        self.phonemes = list(phonemes)
        self.velocity = int(velocity)
        #: per-phoneme nominal lengths; Syllable_Duration only uses their
        #: ratios, so any consistent unit works
        self.durations = list(durations) if durations else None

    def __repr__(self):
        return 'Note(%d, %.3f, %r)' % (self.midi, self.beats, self.phonemes)


class Renderer(object):
    def __init__(self, program=0, bpm=BPM, brightness=BRIGHTNESS, voice=None):
        self.brightness = brightness
        self.voice = clean_voice(voice)
        self.blob = load()
        self.order = phoneme_order(self.blob)
        self.index = {n: i for i, n in enumerate(self.order)}
        self.nominal = {r[0]: r[1] for r in durations(self.blob)}
        self.program = program
        self.bpm = bpm

    # -- sequence ----------------------------------------------------------

    def _sequence(self, notes):
        """Pack the notes into the block `SetSeqAddr` expects.

        Returns the blob and, for each note, the index of the phoneme that
        starts it -- the engine consumes one note per note-start marker.
        """
        phon, ctrl, dur = [], [], []
        for note in notes:
            syms = [s for s in note.phonemes if s in self.index] or ['%']
            lens = note.durations or [self.nominal.get(s, 80) for s in syms]
            for k, s in enumerate(syms):
                phon.append(self.index[s])
                ctrl.append(1 if k == 0 else 0)     # bit 0: this begins a note
                dur.append(max(1, int(round(lens[k]))))
        # terminator: Syllable_Duration needs a following note-start to scale
        # the final syllable against, so every sequence ends with a marked rest
        phon.append(self.index['%'])
        ctrl.append(1)
        dur.append(max(1, int(self.nominal.get('%', 300))))

        n = len(phon)
        spare = [0] * n
        pack = lambda a: struct.pack('>%dH' % n, *a)
        blob = (struct.pack('>HH', 0, n) + pack(phon) + pack(ctrl)
                + pack(spare) + pack(dur))
        return blob, n

    # -- rendering ---------------------------------------------------------

    def _setup(self, notes, program=None):
        """Build the engine state a render runs on; returns the VocalWriter."""
        vw = VocalWriter()
        m = vw.m
        m.mem.wf32(vw.g + G_TEMPO_SCALE, TEMPO_SCALE)
        m.call('SetTempo', vw.g, int(self.bpm))

        blob, _n = self._sequence(notes)
        seq = m.alloc(len(blob), zero=False)
        m.mem.write(seq, blob)

        # PgmChange picks the voice; SetSeqAddr would override it from the
        # sequence's own voice key, so put it back afterwards
        vw.program(self.program if program is None else program)
        voice = m.mem.r16(vw.ctx + CTX_VOICE_IDX)
        m.call('SetSeqAddr', vw.ctx, seq)
        m.mem.w16(vw.ctx + CTX_VOICE_IDX, voice)

        for fn, args in (('InitSay', (vw.ctx,)),
                         ('Init_ControlBlocks', (vw.ctx,)),
                         ('Start_Speech', (vw.g, 0)),
                         ('Sing_Speech', (vw.g, 0, 1))):
            m.call(fn, *args)
        self._voice_controls(vw)
        return vw

    def _voice_controls(self, vw):
        """Controls that Start_Speech and PgmChange leave zeroed.

        Without the first, ctx[0x10bc]/[0x10c0] -- the weights that mix the two
        glottal wavetables -- are zero and every voiced sample is multiplied by
        nothing; without the second, ctx[0xfbc] is zero and the amplitude
        target collapses the same way. Either leaves the fricatives audible and
        the vowels silent.
        """
        m = vw.m
        m.call('InitDefaultVoiceCntrls', vw.ctx)
        # The glide table. `InitGlobals_Speech` copied g[0x2e9c] into
        # ctx[0x1070] long before there was a table there -- the application
        # fills that global in `Synth_Startup` -- so point the context at the
        # one `InitSharedTables` loaded.
        #
        # After the defaults on purpose. The engine's own default portamento
        # is read out of this same table, so wiring it any earlier would put a
        # glide on every note of every song ever rendered here. VocalWriter's
        # own renders do not glide -- measured against them, note change for
        # note change -- so the default stays what it has always been: nothing
        # in ctx[0x1060], and a note steps straight to its pitch.
        m.mem.w32(vw.ctx + CTX_GLIDE_TBL,
                  m.mem.r32(m.globals_ptr('_g_Time_Tbl')))
        m.call('Speech_Volume', vw.g, 0, 127)
        m.call('SetTotalVolume', vw.ctx)
        # Only what has been moved off its default, so an unset control is
        # left exactly as the engine had it rather than re-stated slightly
        # differently through a 0-127 knob.
        for key, call, default, _lo, _hi, _label, _hint in VOICE_CONTROLS:
            value = self.voice.get(key, default)
            if value != default:
                m.call(call, vw.g, 0, int(value) & 0xFFFFFFFF)
        if self.brightness is not None and m.mem.r16(vw.ctx + 0xcee):
            a0 = m.mem.rf32(vw.ctx + RAD_A)
            a = a0 * self.brightness
            m.mem.wf32(vw.ctx + RAD_A, a)
            m.mem.wf32(vw.ctx + RAD_B, 2.0 - a)
            # The shelf's DC gain is 2.5 - 2a, so tilting it also changes how
            # loud everything is -- by nearly four times at the calibrated
            # setting, which clips inside the engine. Compensate through the
            # engine's own volume so brightness only changes tone.
            dc0, dc = 2.5 - 2.0 * a0, 2.5 - 2.0 * a
            if dc > 1e-6 and dc0 > 1e-6:
                vol = m.mem.rf32(vw.ctx + G_TOTAL_VOL) * (dc0 / dc)
                m.mem.wf32(vw.ctx + G_TOTAL_VOL, vol)

    def _samples(self, vw):
        halfwords = vw.m.mem.r32(vw.ctx + CTX_OUT_POS)
        raw = np.frombuffer(vw.m.mem.read(vw.outbuf, halfwords * 2),
                            dtype='>i2').astype(np.float32)
        # SayFrame writes groups of four halfwords as [L0, L1, R0, R1] -- two
        # samples per channel, not two interleaved frames. Taking every second
        # halfword splices the left channel onto a copy of itself and destroys
        # the period, which sounds exactly like a voice with no pitch.
        return np.stack([raw[0::4], raw[1::4]], axis=1).ravel() / 32768.0

    def _note_call(self, vw, note):
        """Speech_Note(g, chan, note, ?, velocity, beats).

        The velocity is the *fifth* integer: it lands in ctx[0x1000], which
        DoNote turns into the note amplitude ctx[0x10c4], and SaveFrame copies
        into the frame as the factor the whole voiced branch is scaled by.
        """
        vw.m.call('Speech_Note', vw.g, 0, note.midi, 0, note.velocity,
                  floats=(note.beats,))

    def render(self, notes):
        vw = self._setup(notes)
        m = vw.m
        pending = list(notes)
        self._note_call(vw, pending.pop(0))
        r16 = lambda o: m.mem.r16(vw.ctx + o)
        for _ in range(MAX_FRAMES):
            m.call('e_Fill_Next_Frame', vw.ctx)
            m.call('SayFrame', vw.ctx)
            if r16(CTX_STATE) == 3:
                break
            if r16(CTX_WAIT):               # the engine is asking for a note
                if not pending:
                    break
                self._note_call(vw, pending.pop(0))
        return self._samples(vw)

    def render_live(self, notes, starts, events, bpm, verbose=False,
                    mark=None):
        """Render with the score's own tempo map, bends and program changes.

        The engine takes a note's length in beats and converts it with the
        tempo in force, so the tempo is set just before each note is handed
        over. Controller events are applied between frames, which is as fine as
        the engine's own control rate: one frame is 220 samples, ~5 ms.

        `mark` is a list the sample offset of each note is appended to, so a
        caller rendering lead-in context can find where the real audio starts.
        """
        vw = self._setup(notes)
        m = vw.m
        r16 = lambda o: m.mem.r16(vw.ctx + o)
        queue = list(zip(notes, starts))
        ev, ei = list(events), 0
        current_program = self.program

        def feed():
            note, tick = queue.pop(0)
            if mark is not None:
                # ctx[0x1080] counts halfwords, four per pair of mono samples
                mark.append(m.mem.r32(vw.ctx + CTX_OUT_POS) // 2)
            m.call('SetTempo', vw.g, max(10, min(250, int(round(bpm(tick))))))
            self._note_call(vw, note)

        feed()
        frames = 0
        while frames < MAX_FRAMES:
            now = frames * SAMPLES_PER_FRAME / float(SAMPLE_RATE)
            while ei < len(ev) and ev[ei][0] <= now:
                _t, kind, val = ev[ei]
                ei += 1
                if kind == 'bend':
                    m.call('Speech_PitchBend', vw.g, 0,
                           (val * BEND_SCALE) & 0xFFFFFFFF)
                elif kind == 'sens':
                    m.call('Speech_PBSens', vw.g, 0, val)
                elif kind == 'program' and val != current_program:
                    # Reloading the voice rebuilds the wavetables and filter
                    # coefficients but leaves the resonator delay lines holding
                    # the old voice's state, so only do it when the program
                    # actually changes. The export re-sends the program it is
                    # already on, and honouring that destabilises the cascade.
                    m.call('PgmChange_Speech', vw.g, 0, val)
                    self._voice_controls(vw)
                    current_program = val
            m.call('e_Fill_Next_Frame', vw.ctx)
            m.call('SayFrame', vw.ctx)
            frames += 1
            if verbose and frames % 400 == 0:
                print('   %6.1f s rendered, %d notes left'
                      % (frames * SAMPLES_PER_FRAME / float(SAMPLE_RATE),
                         len(queue)), flush=True)
            if r16(CTX_STATE) == 3:
                break
            if r16(CTX_WAIT):
                if not queue:
                    break
                feed()
        return self._samples(vw)


def write_wav(path, y, sr=SAMPLE_RATE):
    """Write samples to a WAV: one column is mono, two columns are stereo.

    Stereo is here for panning. Nothing the engine itself produces has two
    channels -- a voice is one signal -- but several voices placed at
    different points across the stereo field do, and the frames of a (n, 2)
    array are already left-then-right in memory, which is the order a WAV
    wants them in.
    """
    import wave
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    y = np.asarray(y, dtype=np.float32)
    pcm = (np.clip(y, -1.0, 1.0) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1 if y.ndim == 1 else y.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


DAISY = [(69, 1.5, 'd,EY'), (65, 1.5, 'z,IY'), (62, 1.5, 'd,EY'),
         (57, 1.5, 'z,IY'), (64, 0.75, 'g,I,v'), (65, 0.75, 'm,IY'),
         (67, 0.75, 'y,AO,r'), (64, 0.75, '%'), (62, 1.0, 'AE,n'),
         (64, 0.5, 's,er'), (65, 0.5, '%'), (62, 1.5, 'd,UW')]


if __name__ == '__main__':
    r = Renderer(program=0)
    tune = [Note(p, b, ph.split(',')) for p, b, ph in DAISY]
    y = r.render(tune)
    write_wav('out/ppc_sung.wav', y)
    print('%.2f s at %d Hz, %d notes -> out/ppc_sung.wav'
          % (len(y) / float(SAMPLE_RATE), SAMPLE_RATE, len(tune)))
