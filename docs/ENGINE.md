# VocalWriter's synthesiser, from the binary

Everything here is read out of the shipped PowerPC executable, not inferred from
listening. It supersedes the guessed parameters of the first, approximate cut.

## The binary is a debug build

`LC_SYMTAB` carries **76,190 symbols** and a 400 KB string table, including
STABS `N_FUN` records. Every function has its name, address and type signature.
1,249 named functions; the speech synthesiser is 161 of them between `0x08e000`
and `0x0a6000`.

That is the whole reason this is tractable. `tools/machsyms.py` reads the table
and `tools/ppcdis.py` disassembles any function by name, using the next
function's address as the boundary.

```
python tools/ppcdis.py Calc_Pole_Coefficients
python tools/machsyms.py <binary> "Formant|Glot|Phon"
```

## The engine's shape

| Function | Does |
|---|---|
| `e_Fill_Next_Frame` | per-frame control update — calls `DoNote`, `Syllable_Duration`, `StartNewPhon`, `Init_Ctrls_for_New_Phon`, `Interpolate_Pitch`, `Interpolate_Formants`, `SaveFrame` |
| `SayFrame` | renders one frame of audio; calls `Calc_Pole_Coefficients` ×3, `Calc_Zero_Coefficients` ×1, `Set_SndFreq`, `InitSampOsc` |
| `InitFixedFormants` | builds the voice's six fixed resonators |
| `Calc_Pole_Coefficients` / `Calc_Zero_Coefficients` | resonator / anti-resonator coefficients |
| `MakePulse`, `InvDFT`, `InitSampleGlott` | the glottal source, built by **inverse DFT** |
| `GetTarget`, `Get_Locus`, `Head_Rules`, `Tail_Rules`, `Fill_Phon_Targets`, `Get_Diphthongs`, `Adjust_Colored_Target` | articulation: phoneme targets and coarticulation |
| `OrthToPhon`, `SearchDict`, `DecompressString`, `LookUp`, `DoMorph` + ~40 `Do_*_Morph` | letter-to-sound: dictionary plus a full English morphology engine |

So it is a **frame-based Klatt-family synthesiser**: control frames set targets,
`SayFrame` renders samples between them.

## `Calc_Pole_Coefficients` — exact

```
Calc_Pole_Coefficients(ctx, float *A, float *B, float *C, short freq, short bw)

    bw   = min(bw, 1225)              # hard ceiling
    bw   = max(bw, ctx->[0xcc0])      # per-voice floor
    freq = max(freq, 256)

    i  = (bw - 50) / 5                # bandwidth quantised to 5 Hz from 50
    *C = ctx->tbl_0xd08[i]
    *B = ctx->tbl_0xd04[i] * ctx->tbl_0xd00[freq - 256]
    *A = 1.0 - *B - *C
```

This is the standard Klatt two-pole section, `y[n] = A·x[n] + B·y[n-1] +
C·y[n-2]`, entirely table-driven: `tbl_0xd08` is `-exp(-2πB/fs)`, `tbl_0xd04` is
`2·exp(-πB/fs)`, `tbl_0xd00` is the cosine term. Bandwidth is **quantised to
5 Hz steps** and clamped to `[voice floor, 1225]`.

## Frequencies are not in Hz

Every formant frequency passes through **`e_HzToPitch`** before reaching
`Calc_Pole_Coefficients`, so the `freq` argument is a **logarithmic pitch unit,
256 per octave** (≈21.33 per semitone), not hertz:

```
e_HzToPitch(hz):
    hz <= 99    -> m = hz << 3, base = 0x000
    hz <= 199   -> m = hz << 2, base = 0x100
    hz <= 399   -> m = hz << 1, base = 0x200
    hz <= 799   -> m = hz,      base = 0x300
    hz <= 1599  -> m = hz >> 1, base = 0x400
    hz <= 3199  -> m = hz >> 2, base = 0x500
    hz <= 6399  -> m = hz >> 3, base = 0x600
    else        -> m = hz >> 4, base = 0x700

    t      = (2621*m - 1048400) >> 11      # m is normalised into one octave
    return ctx->tbl_0xfdc[t] + base        # 512-entry log table
```

Normalise into a single octave, take a linear index, look up the log, add
256 per octave. The `freq >= 256` clamp in `Calc_Pole_Coefficients` is exactly
one octave above the bottom of this scale — the cosine table is indexed by
**log frequency**, not linear frequency.

## The voice's fixed formants — exact

`InitVoice` copies the `mvox` record straight into the synth context, which
**confirms the mvox layout independently of any pattern-matching**:

| mvox offset | via | context |
|---|---|---|
| 0x18 | `e_HzToPitch` | 0xcc2 (formant 1 freq) |
| 0x1a | — | 0xcc4 (formant 1 bandwidth) |
| 0x1c | `e_HzToPitch` | 0xcc6 |
| 0x1e | — | 0xcc8 |
| 0x20 | `e_HzToPitch` | 0xcd8 |
| 0x22 | — | 0xcda |
| 0x24 | `e_HzToPitch` | 0xcdc |
| 0x26 | — | 0xcde |
| 0x28 | `e_HzToPitch` | 0xce0 |
| 0x2a | — | 0xce2 |
| 0x2c | `e_HzToPitch` | 0xce4 — **nasal pole** |
| 0x2e | — | 0xcea — **nasal zero** |
| 0x30 | — | 0xce6 — nasal bandwidth, also the anti-resonator's |

Bandwidths are passed through unchanged; only frequencies are converted.

`InitFixedFormants` then builds **six** fixed pole sections from
`0xcc2/0xcc4`, `0xcc6/0xcc8`, `0xcd8/0xcda`, `0xcdc/0xcde`, `0xce0/0xce2` and
`0xce4/0xce6`, writing `(A,B,C)` triples to `0xd40`, `0xd4c`, `0xd58`, `0xd64`,
`0xd70` and `0xd88`.

**The upper three are attenuated.** The sections built from `0xcd8`, `0xcdc` and
`0xce0` have their `A` coefficient multiplied by **0.4** (a double at
`0xab1d8`). That is the real answer to the 2–3 kHz hump that the first,
listening-based first cut was trying to tune away by hand.

`0xce6` doubles as the bandwidth of the anti-resonator
(`Calc_Zero_Coefficients`) in `SayFrame` — so the sixth section and the zero are
a **nasal pole/zero pair**, the standard Klatt arrangement. That explains why
mvox 0x2c and 0x2e hold 330 and 400 on every single voice: they are the nasal
resonance and antiresonance, which do not vary by speaker.

## The sample loop — cascade, confirmed

The main loop runs `0x951b0`–`0x96334`. Frame parameters are linearly
interpolated across **128 samples** (nine value/increment pairs at 0xd94–0xdd8,
counters bounded at 126/127). Each filter section is

```
    y = A*x + B*y1 + C*y2        # A,B,C from Calc_Pole_Coefficients
    y2 = y1;  y1 = y;  x = y     # running signal passes to the next section
```

with per-section state in consecutive float pairs from 0xdf0 upward, so the
topology is a **cascade** — the signal is threaded through section after section
in one chain, not summed in parallel.

## Articulated formants

`SayFrame` calls `Calc_Pole_Coefficients` three times with bandwidths read from
the current frame at offsets `0x1a`, `0x1c`, `0x1e` — so **three formants are
articulated with per-frame bandwidths**, on top of the six fixed sections, plus
one zero.

## The glottal source — exact

`ResetVoice` calls `InitVoice` then `InvDFT`, so the source is rebuilt whenever
the voice changes. `InvDFT` (0x9ce08) reads **48 int16 harmonic amplitudes** from
`voice+0x4c` and another 48 from `voice+0xac`, scales both by `voice[0x12]`, and
sums them into two 256-point buffers:

```
for h in 0..47:
    amp = tbl[h] * scale
    phase = 0
    for n in 0..255:
        buf[n] += sin_table[phase] * amp
        phase = (phase + h) mod 256
```

which is a sine-only inverse DFT:

    wave[n] = sum_h tbl[h] * sin(2*pi*h*n/256)

Sine-only, so every harmonic has zero phase. Reconstructing it from the shipped
voices gives textbook glottal pulses: fast rise, peak
around n=30, long fall, sharp return.

**This settles what the two mvox tables are.** They were previously described
here as "consistent with source spectra ... inference, not fact". They are the
harmonic spectra of the two glottal sources, and the offsets are 0x4c and 0xac
(not 0x4e/0xae — harmonic 0 is a zero DC term). Table B is a band-limited
variant of table A, truncated above a per-voice harmonic: 27 for Robert, 22 for
Abe, 46 for Sarah.

## Where the tables live

`Synth_Startup` copies `_g_SpeechTbls` into the synth globals;
`InitGlobals_Speech` passes it to `SetSpeechTblAddr`, which walks a header of
**46 u32 offsets** and resolves each with `GetThePtr(base, slot) = base + *slot`
into a context pointer. The interesting slots:

| slot | context | what |
|---|---|---|
| 32 | 0xd00 | cosine table, indexed by log-frequency |
| 33 | 0xd04 | `2·exp(-πB/fs)` |
| 34 | 0xd08 | `-exp(-2πB/fs)` |
| 35 | 0xc60 | sine table used by `InvDFT` |
| 40 | 0xfdc | the 512-entry log table used by `e_HzToPitch` |
| 42 | 0xfd4 | **per-phoneme records** |

### The blob is the `ttvi` resource

`InitSynth` runs `LoadSynthResource(&_g_DataHandle, 'ttvi', 2)` — so the whole
speech data set is **`ttvi` id 2, 91,108 bytes**, addressed by a header of 46
big-endian u32 offsets. (`SetTblAddr` dereferences the Mac handle, so the
offsets are relative to the start of the resource.) `tools/ttvi.py` reads it.

Two tables are identified beyond doubt because the shipped data satisfies the
resonator identity from `Calc_Pole_Coefficients` **to 8.25e-08**:

    C = -(B/2)^2        i.e.  -exp(-2πBW/fs) = -( 2exp(-πBW/fs) / 2 )^2

| table | offset |
|---|---|
| cosine term, indexed by log-frequency | `0xd9e` |
| `2·exp(-πBW/fs)` | `0xf4e` |
| `-exp(-2πBW/fs)` | `0x202e` |

The bandwidth table is spaced **logarithmically at exactly 12.0 steps per
octave** (measured ratio 1.05946 = 2^(1/12)) — bandwidth is quantised in
semitones, matching the log-frequency domain the rest of the engine works in.

### The internal phoneme order

The blob stores its own phoneme names as packed two-character codes at
`0x11302`, so the order is read, not guessed. **57 phonemes**, and it is *not*
the Phoneme Palette order from the application binary:

```
IY IH EH AE AA UX AO UH AX ER EY AY OY AW OW UW YU IR XR AR OR UR IX %
RX LX EL EN w y r l h m n NG f v TH DH s z SH ZH p b t d k g CH JH TX
Q QX DD O
```

`%` is silence and `Q`/`QX` glottal stops. Every per-phoneme table in the blob
is 57 entries long and indexed in this order.

### Per-phoneme durations — real data

`0x1121e` and `0x11290` hold two 57-entry u16 tables, in **milliseconds**, and
they read exactly as a phonetician would expect:

| | |
|---|---|
| diphthongs longest | OY 260, AW 260, AY 250 |
| tense vowels | AA 240, AO 240, AE 230 |
| reduced vowels shortest | AX 120, IX 120 |
| stops | p 85, b 80, t 85, d 80 |
| glottal stop | Q 20 |
| silence | % 305 |

This replaces the invented duration model of the first cut outright.

**The frequency tables are not stored — they are computed.** `InitSharedTables` allocates
0x4800 bytes, `SetTblAddr` carves that block into the globals, and
`Make_F_Table` *computes* the frequency tables at startup by replicating a seed
table across octaves (nested loops of 32 × 12, walking backwards). That is why
searching the resource fork and the `data` resources for float tables found
nothing — they only exist at run time.

## The articulation table — found

`_g_SpeechTbls` is **slot 35** of the outer header, at `ttvi + 0x480`, and holds
its own 46-offset header. Under it, sixteen 57-entry tables carry the phoneme
parameters. Identified against the known phoneme order:

| sub-slot | contents |
|---|---|
| 16, 17, 18 | **F1, F2, F3 targets, in Hz** |
| 19, 20, 21 | **B1, B2, B3 bandwidths, in Hz** |
| 23, 24, 25 | a **second target set** the engine glides toward |

The data is unmistakably real:

```
IY  340/2120/2900     r   350/1025/1380     b  200/1100/2150
AA  750/1200/2660     w   300/ 600/2240     p  350/1100/2150
ER  450/1270/1550     y   250/2080/2560     d  200/1600/2600
                      l   350/ 800/2950     t  350/1600/2600
```

ER and /r/ carry the low F3 that r-colouring requires (1550, 1380); the
voiced/voiceless stop pairs differ *only* in F1 (b 200 vs p 350, d 200 vs t
350), which is exactly how voicing is modelled; nasals get wide bandwidths
(m/n 130/280/360). Silence, `h` and `QX` store 0x7FFF — no target at all.

The outer slots are all named by the binary's own symbols, which is how they
were identified rather than guessed: `_g_CosTbl` (0xd9e), `_g_BcoeffTbl`
(0xf4e), `_g_CcoeffTbl` (0x202e), `_g_maxDurTbl` (0x1121e), `_g_minDurTbl`
(0x11290), `_g_Opcode_To_ASCII` (0x11302), `_g_SpeechTbls` (0x480), plus the
letter-to-sound rule tables (`_g_dashruletab`, `_g_atruletab`, `_g_SuffixTab`,
`_g_RulesData`, …).

### The indirect entries

38 of 57 phonemes have fully direct targets. The rest store a value with **bit
15 set**, whose low bits index a further table in steps of 4: F1 indices 0–44
for the diphthongs and r-coloured vowels, F2 indices 48–108, F3 from 116. Those
are the context-dependent targets `Get_Diphthongs` and `Get_Locus` resolve. The
table they index has not been located yet.

### A warning about measuring "improvement"

Substituting the real table *raised* the vowel-formant error against the
original's own render, from 97 Hz to 176 Hz. That is not a regression — it
exposes a circular metric. The 97 Hz figure came from targets that had been
*measured from that very render*, so they minimise that error by construction.
The shipped table gives what the engine aims at; the original's output then
moves away from those targets through coarticulation (`Get_Locus`,
`Head_Rules`, `Tail_Rules`), which this re-implementation does not yet do. The
fix is to implement coarticulation, not to go back to fitted numbers.

## Topology: cascade *and* parallel

The five fixed sections are **not** all in one chain, and getting this wrong is
what kept the re-implementation sounding wrong no matter how accurate the data
became.

In `SayFrame`'s sample loop, the sections built from `0xd40` and `0xd4c` write
their result back into the running signal (local `0xb0`) — a cascade. The
sections at `0xd58`, `0xd64` and `0xd70` do not:

```
    if (ctx[0xe48] > threshold) {          ; gated on its own amplitude
        y  = A_local * local_0xb4          ; a *different* input
        y += ctx[0xd5c] * ctx[0xe28]
        y += ctx[0xd60] * ctx[0xe2c]
        local_0x9c = y                     ; a separate accumulator
    }
```

Different input, separate output, and gated on an amplitude that can be zero —
that is a **parallel branch**, the standard Klatt arrangement where the cascade
carries voiced sounds and the parallel branch carries fricatives and bursts.
Those three are also exactly the sections `InitFixedFormants` scales by 0.4.

So the real structure is:

| branch | sections |
|---|---|
| cascade | 3 articulated formants, then the voice's fixed formants 1 and 2, plus the nasal pole/zero |
| parallel | the voice's fixed formants 3, 4 and 5, each amplitude-gated, gain ×0.4 |

Cascading all five instead stacks Abe's 2500/2800/3200/3250 Hz poles on top of
one another. Measured against the original's own render of the same track, with
both spectra normalised to **equal total power** over 150-5000 Hz (peak
normalisation flatters whichever signal has the taller peak and is not a fair
comparison):

| band | cascade-all | correct split |
|---|---|---|
| 150-800 Hz | −16.5 dB | **−0.1** |
| 800-2000 Hz | −4.6 | +5.2 |
| 2000-3500 Hz | +18.6 | +15.4 |
| 3500-5000 Hz | −46.2 | +8.9 |
| **RMS 150-5000 Hz** | **30.4 dB** | **12.0 dB** (9.5 with the branch levelled) |

The low band goes from 16.5 dB down to essentially exact, and overall error
falls by a factor of 2.5.

### The amplitudes

`SayFrame` gates each parallel section on its own amplitude
(`if ctx[0xe48] > threshold`). Two amplitude tables *are* recovered — sub-slots
22 and 29, **AV, the amplitude of voicing in dB**, at the first and second
target. It is graded rather than binary, which is how it identifies itself:

| | AV (dB) |
|---|---|
| vowels | 62-66 |
| liquids | 64 |
| nasals | 60 |
| voiced fricatives | 53 |
| unvoiced fricatives, all stops, silence | **0** |

That replaces the hand-written voiced/unvoiced set the engine used before.

### The parallel gate, traced but not found

`SayFrame` reads the gate from the control frame:

```
if (frame[0x10] == 0)  ctx[0xe48] = 0;              ; section off
else {  ctx[0xe48] = (float)frame[0x10] / K;
        A_scaled  = ctx[0xd58] * ctx[0xe48];  }     ; A coefficient x amplitude
```

and `SaveFrame` fills those fields:

```
frame[0x10] = e_LogToLin(ctx, ctx[0x31e])
frame[0x12] = e_LogToLin(ctx, ctx[0x320])
frame[0x14] = e_LogToLin(ctx, ctx[0x322])
```

So the parallel amplitudes are **dB values** (hence `e_LogToLin`), interpolated
per frame from control values at `ctx[0x31e]` onward — Klatt's A2-A6. The
per-phoneme table that seeds them has not been located.

Its magnitude *can* be measured, though. Sweeping the branch level against the
original's own render:

| level | RMS | 150-800 | 800-2k | 2-3.5k | 3.5-5k |
|---|---|---|---|---|---|
| 0.0 (off) | 28.4 dB | +1.3 | +8.1 | +6.7 | **−46.8** |
| 0.15 | **9.5** | +1.2 | +7.7 | +10.2 | −4.9 |
| 1.0 (ungated) | 12.0 | −0.1 | +5.2 | **+15.4** | +8.9 |

Switching the branch off collapses 3.5-5 kHz by 47 dB — the voice's top formant
(4800 Hz for Abe) lives there, so the branch is genuinely required. Running it
ungated puts 15 dB too much at 2-3.5 kHz. The best fit is near **0.15, about
−16 dB**, which measures roughly what the missing table must contain without
deriving it.

### Why there is no amplitude table to find

Tracing further explains the failed search. The engine runs **15 control
channels**, held at `ctx + 0x308 + i*2` — anchored by the bandwidths, which
`SaveFrame` reads from `0x30e/0x310/0x312` and which are known to be controls
3, 4, 5. That makes the parallel amplitudes at `0x31e`–`0x324` **controls
11–14**.

Speech sub-slot 2 is the control-to-class map:

```
control   0  1  2 | 3  4  5 | 6 | 7  8 | 9 10 11 12 13 14
class     0  0  0 | 1  1  1 | 2 | 3  3 | 4  4  4  4  4  4
          formants   bandwidths  AV    ?    amplitude block
```

And the per-phoneme target tables are **two sets of seven** — sub-slots 16–22
(first target) and 23–29 (second). Seven tables, covering controls 0–6 only.

So controls 11–14 have **no per-phoneme target table at all**. Their values are
produced by the rule layer — `Head_Rules`, `Tail_Rules`, `Insert_Burst`,
`Get_Locus` — writing into the 36-byte control records at `ctx+0xec`, each of
which carries a pointer (`rec+0x1c`) into a breakpoint list that
`Interpolate_Formants` walks. The amplitudes are *generated*, not looked up.

That reframes `PARALLEL_GAIN`: it is standing in for rule-driven behaviour, not
for a constant table that was merely mislaid.

### `ctx->0xfd4` is a flags table, not formant data

`AdjustGain` tests it with `clrlwi r0, r0, 0x1f` — bit 0. It is
`_g_phonFlags2`: 57 u32 at `0x1113a`, one per phoneme, 38 distinct values. The
bits group exactly as the articulation table does, which cross-checks both:

| flags | phonemes |
|---|---|
| `0x2040003d` | AY OY AW OW UW IR XR AR OR — the diphthongs and r-coloured vowels |
| `0x0020003d` | IH EH AE IX — precisely the four whose stored F2 is indirect |
| `0x0000003d` | AA UX AO UH AX ER O — plain monophthongs |
| `0x020001b6` | w r — glides |
| `0x00000020` | % — silence |

Bit `0x20000000` marks a diphthong and `0x00200000` an indirect F2, matching the
bit-15 indirections in the target tables independently.

`engine.PARALLEL_GAIN` holds the level as an explicitly labelled stand-in — the one
number in the engine not read out of the original. It should be deleted as soon
as the real per-phoneme amplitudes are found.

Two other things were tested and ruled out as causes of the 2-3.5 kHz excess:
driving the branch from the noise source instead of the voiced one (within
0.1 dB — so the drive signal is not the issue), and articulating only two
formants instead of three for a voice whose fixed formants start low
(11.99 → 11.53 dB — marginal).

## The control layer is generative, not tabular

This is the decisive structural finding, and it settles what is left to do.

`Get_Diphthongs` does not look up a diphthong's target. It installs a
**breakpoint script** into the control record:

```
rec = ctx + 0xec + ctrl*36
rec[0x1c] = ctx[0x3f0]        ; pointer to a time/value breakpoint list
```

and `Fill_Phon_Targets` sets that pointer to **`ctx + 0x326`** — a scratch area
*inside the context*, not a static table. `Interpolate_Formants` then walks the
list, reading `(time, value)` pairs and advancing `rec[0x1c]` by 2 each step,
until the sentinel time `0x1996` (6550).

So the scripts are **built at run time**. The same is true of the parallel
amplitudes (controls 11-14), which have no per-phoneme table either.

That is why the search for a diphthong target table and an amplitude table both
failed: neither exists. What exists is:

| kind | where |
|---|---|
| static tables | formant/bandwidth targets, AV, durations, phoneme flags, classes, coefficient and log tables — all found |
| generated at run time | diphthong and r-coloured glides, consonant loci, parallel amplitudes, burst insertion |

The generating code is `Get_Locus`, `Head_Rules`, `Tail_Rules`, `Insert_Burst`
and `Get_Diphthongs` — together roughly 2,000 PowerPC instructions. Reading
their first sections shows the shape: `Get_Locus` applies only to controls 0-2
(the formants), requires one side of the boundary to be class 3 (consonant) and
the other not, and branches on bit 18 of the phoneme flags word.

**The data-extraction phase is therefore finished.** Everything in VocalWriter
that is a table has been located and read. Everything still missing is
algorithm, and closing the gap means porting those five functions rather than
finding more data.

## The voiced source is a crossfade, and Brightness controls it

`SayFrame` does not pick one of the voice's two glottal spectra. It mixes them:

```
    i      = ctx[0xc6c] >> 16                  ; 16.16 phase accumulator
    source = bufA[i] * ctx[0x10bc]             ; table A, from voice+0x4c
           + bufB[i] * ctx[0x10c0]             ; table B, from voice+0xac
```

and `Speech_Color` — the control the manual calls **Brightness** — sets the two
weights:

```
    w = colour / 127, clamped to [0, 1]
    ctx[0x10bc] = w
    ctx[0x10c0] = 1.0 - w
```

Table A falls at about **−6.3 dB/octave** and table B at **−13.2**, so
Brightness crossfades between a bright source and a dark one. That is why each
voice ships two spectra, and it settles a question left open earlier.

`InitDefaultVoiceCntrls` stores **0.75** (`lis r0, 0x3f40`) as the default, so a
voice starts bright. Songs automate it: HAL's track in `Daisy.trk` carries 169
continuous control events running 38–51 early on, i.e. w ≈ 0.30–0.40 — and
sweeping brightness against the original's own render puts the best fit at
0.25–0.39, matching. Driving that automation from the `.trk` rather than a
static value is the obvious next step.

Immediately after the mix, a **second oscillator** reads `bufB` on its own phase
accumulator (`ctx[0xc70]`), gated on `ctx[0xcca]` and `ctx[0x45e]`, and is added
in — the manual's **Chorus**, which "doubles the voicing for the natural
voices".

Measured effect of getting the source right, against the original:

| brightness | RMS | 150-800 | 800-2k | 2-3.5k | 3.5-5k |
|---|---|---|---|---|---|
| 1.00 (table A alone) | 9.5 dB | +1.2 | +7.7 | +10.2 | −4.9 |
| 0.39 (manual's default 50/127) | 8.1 | +1.3 | +3.2 | +7.2 | −5.0 |
| 0.25 | **7.7** | +1.3 | **+1.0** | +6.4 | −4.8 |

## The radiation post-filter — the piece that was actually missing

At the end of `SayFrame`'s sample loop, after the cascade and parallel branches
are summed:

```
    if (ctx[0xcee]) {
        t = ctx[0xcf0]*x - ctx[0xe58]*ctx[0xcf4]    ; one-zero
        ctx[0xe58] = x                              ; keep x[n-1]
        x = t + x*0.5
    }
```

`InitVoice` sets it from **mvox offset 0x110**, a field nothing else uses:

```
    a = voice[0x110] / 100.0        ; Abe 0.80, Robert 1.03, Sarah 0.97
    ctx[0xcf4] = a
    ctx[0xcf0] = 2.0 - a
    ctx[0xcee] = (voice[0x110] > 0) ; the stage is skipped when zero
```

That is a differentiator — the **radiation characteristic at the lips**, which
converts glottal flow to pressure and adds roughly +6 dB/octave. Leaving it out
made everything above ~3 kHz too dark.

**It validates itself.** With the stage in place, the application's own default
Brightness of 0.75 becomes the best-fitting value; without it, matching the
original required forcing Brightness down to 0.25 to compensate. A missing
stage was being papered over by a wrong control setting, and restoring it puts
both right at once.

| | before | with radiation |
|---|---|---|
| 150-800 Hz | +1.4 | +1.5 |
| 800-2000 Hz | +2.6 | **-1.7** |
| 2000-3500 Hz | +7.7 | **-0.6** |
| 3500-5000 Hz | +1.5 | **+0.9** |
| RMS | 6.8 dB | **5.4 dB** |

### What the control layer is *not* responsible for

Before porting the rule layer, it was worth testing whether it was the
bottleneck at all. Driving this engine's filter chain with the **original's own
measured F1/F2/F3 tracks**, instead of its own articulation, scored **7.02 dB**
against the original — no better than its normal render at 6.75 dB.

So perfect formant trajectories buy nothing here. `Get_Locus`, `Head_Rules`,
`Tail_Rules` and the breakpoint machinery were not what stood between this and
the original; the signal chain was. That test cost minutes and saved porting
~2,000 instructions to no effect.

Consonant loci were implemented anyway (they are correct and cheap -- /d/ at
1700 Hz before a back vowel, 2050 before a front one) but they move the
trajectory correlation by 0.002, because consonants occupy little of the time.

### A caution about the trajectory metric

F2 trajectory correlation looked like the metric to optimise, at 0.38. But
measuring the **original against its own nominal targets** gives 0.444 -- so
0.38 is close to the ceiling this measurement can show, and most of the gap is
LPC tracking noise and coarticulation, not error. F1's ceiling is 0.814 against
a measured 0.47, so only F1 had real headroom.

## Still to recover

- `SayFrame`'s sample loop: the filter topology (cascade order, where the noise
  and burst sources enter) and the frame rate.
- **The rule layer**: `Get_Locus`, `Head_Rules`, `Tail_Rules`, `Insert_Burst`,
  `Get_Diphthongs` (~2,000 instructions). Porting these would deliver the
  consonant loci, the diphthong and r-coloured glides, and the parallel
  amplitudes together, and would let `engine.PARALLEL_GAIN` be deleted.
- `Syllable_Duration` and `Interpolate_Pitch` — the duration and pitch
  contours, which the approximate cut substituted with its own logic.
- Where the noise and burst sources enter `SayFrame`'s cascade.
- What selects between glottal source A and B at run time, and what
  `InitSampleGlott` (a *sampled* source, selected by voice fields 0x118/0x122)
  is used for alongside the synthesised one.
- `DecompressString` / `SearchDict`, which would open `EnglishLex`.
