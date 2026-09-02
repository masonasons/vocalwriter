# VocalWriter 2.0 file formats

Reverse-engineering notes for **VocalWriter 2.0.1** (KAE Labs, 2005) — a
PowerPC-only Mac OS X singing synthesiser. The synthesis method is trademarked
**Resonant Articulatory Synthesis (RAS)** and models the vocal tract directly.

Everything here is either **confirmed** — checked against the application
running under Mac OS X 10.4 in QEMU — or explicitly marked as inferred. Nothing
is presented as known when it is a guess.

---

## The application

| | |
|---|---|
| Bundle | `VocalWriter.app`, `CFBundleShortVersionString` 2.0.1 |
| Binary | Mach-O 32-bit **PowerPC only** (`feedface`, cputype 0x12) — no Intel slice |
| Requires | PowerPC G4/G5, Mac OS X 10.3–10.4, per the manual |
| Signature | `APPL` / `sMus` |

The PowerPC-only binary is the whole reason this needs emulation: Rosetta for
PowerPC was removed in Mac OS X 10.7 (2011), so no Mac since can run it.

### Document types

| OSType | Meaning |
|---|---|
| `Seq^` | VocalWriter Tracks file (`.trk`) |
| `Midi` | Standard MIDI file |
| `dEng` | English Lexicon |
| `i16~` | GM Wavetable (`GMBank`) |
| `i08~` | Parametric Voices (`GMSpeech`) |

---

## Resource forks matter

`GMBank` and `GMSpeech` have a **zero-length data fork**. All their content
lives in the resource fork, so a naive file copy off the disk image produces two
empty files and a synthesiser with no voices and no instruments.

| File | Resource | Size | Contents |
|---|---|---|---|
| `GMSpeech` | `mvox` id 1 "Data" | 30,096 | all 87 singing/instrument voices |
| `GMBank` | `mwav` id 1 | 3,482,450 | General MIDI wavetable samples |
| `GMBank` | `mdef` id 1 | 30,752 | GM instrument definitions |
| `VocalWriter.rsrc` | `data` id 1–3 | 277,696 | embedded engine data |
| `VocalWriter.rsrc` | `ttvi` id 2 | 91,108 | unidentified |

`tools/machrsrc.py` reads the standard classic-Mac resource fork layout.

---

## `mvox` — the voice bank (confirmed structure)

30,096 bytes, big-endian throughout:

```
0x000   128 x u16   program number -> voice index (not GM, see below)
0x100   608 bytes   not yet identified
0x360   87 x 336    voice records
```

`864 + 87*336 == 30096` exactly, which is what fixes the header size and record
count beyond doubt.

The table at `0x000` is program-indexed but is **not** a General MIDI map:
entries 0–4 point at Robert, Sarah, Tracy, Andy and Abe — the singing voices —
where GM program 0 would be Acoustic Grand Piano. It looks like VocalWriter's
own instrument numbering. The exact numbering is unconfirmed.

**30 KB for 87 voices** is only possible because nothing is sampled — each voice
is a few hundred bytes of formant and source parameters.

### Voice record (336 bytes)

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | Pascal string, 16-byte field | voice name — **confirmed** |
| 0x10 | u16 | 0 = male, 1 = female — **confirmed** (see below) |
| 0x12 | u16 | unidentified, 0–200 |
| 0x14 | u16 | unidentified, 0–40 |
| 0x16 | u16 | level: 128, 192, or 16 for the breath voices |
| 0x18 | 5 × (u16 Hz, u16 Hz) | five (formant frequency, bandwidth) pairs — **confirmed** |
| 0x2c | u16, u16 | 330, 400 on every voice |
| 0x30–0x4d | mixed | gains and signed offsets |
| 0x4e | ~45 × u16 | decaying spectral table A (13818 … 0) |
| 0xae | ~45 × u16 | decaying spectral table B (13818 … 0), falls faster |
| 0x110+ | | further parameters, largely zero |

The sex flag and the formant block corroborate each other. Female voices carry
systematically higher formants, exactly as a shorter vocal tract predicts:

| Voice | sex | formants (Hz) |
|---|---|---|
| Robert | male | 3600, 4200, 3200, 3750, 5000 |
| Sarah | female | 4500, 5500, 4700, 5550, 6500 |
| Abe | male | 2500, 3200, 2800, 3250, 4800 |

Independent confirmation, measured rather than eyeballed: taking the long-term
average spectrum of the rendered `Daisy.trk` vocal (11–50 s, the sung stretch)
and picking its strongest peaks above 2.4 kHz gives

```
measured:  2455, 2821, 4888, 4931, 4985 Hz
```

Scoring all twelve singing voices by mean distance from their stored formants
ranks `Abe` and `Ed1` first at 94 Hz mean error — their stored set is
2500, 2800, 3200, 3250, 4800 Hz, so the two lowest peaks land within 2%. Every
voice in the top six is male, which is what HAL should be. The stored values are
therefore frequencies in Hz, and they are the frequencies the synthesiser
actually produces.

(An earlier guess here was that the voice was `Robert`, from an eyeballed
~4200 Hz band. The measurement puts him third at 264 Hz mean error; the sung
voice in `Daisy.trk` is `Abe` or `Ed1`, which share a formant set.)

The first twelve voices are `Robert`, `Sarah`, `Tracy`, `Andy`, `Abe`, `Miles`,
`Ellen1`, `Kae1`, `Sonny1`, `Ed1`, `Male Brth`, `Female Brth`; the remaining 75
are instrument timbres (`Cellos`, `BagPipes`, `PanFlute`, …).

Both spectral tables begin 13818, 5827 and fall monotonically to zero. Their
exact role is **not** confirmed. The shapes are consistent with two excitation
source spectra — a voiced glottal source and a faster-rolling noise source —
which would match the manual's description of speech having several excitation
sources (glottis, tongue, teeth, lips), but that is inference, not fact.

---

## `.trk` — song files

Container and note events both **confirmed** against the running application:
parsed output matches the app's own MIDI export exactly.

```
0x000   u32     version or track count
0x00e   u16     meter numerator            (confirmed: 3 for Daisy.trk)
0x010   u16     meter denominator, log2    (confirmed: 2 -> /4, app shows 3/4)
0x012+  u16 x N default track levels, all 100
0x2fe   88 x 31 track table  (VocalWriter always writes 31 slots)
...             per-track data chunks at the offsets the table gives
```

### Track record (88 bytes)

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | u32 | offset of the track's data chunk — **confirmed** |
| 0x04 | u32 | length of that chunk — **confirmed** |
| 0x08 | u32 | set only on vocal tracks; unknown |
| 0x0c | u32 | set on vocal and karaoke tracks; a sub-length |
| 0x10 | u32 | offset + length (chunk end) — **confirmed** |
| 0x14 | u32 | 60 on vocal/karaoke tracks, else 0 |
| 0x18 | u32 | 13 on vocal/karaoke tracks, else 0 |
| 0x30 | Pascal string, 32-byte field | track name — **confirmed** |
| 0x50 | u32 | 1-based track index — **confirmed** |

The name field is **not cleared when a name shortens**, so bytes past the length
are stale. `HAL` is stored as `03 'H' 'A' 'L'` followed by leftover `ck 2` from
a previous, longer name. Reading the field as a C string gives `HALck 2`.

Validation — parsing `Daisy.trk` cold and then opening it in the application:

| | parsed | application showed |
|---|---|---|
| meter | 3/4 | 3/4 |
| tempo | — | 80 |
| tracks | Tempo Track, HAL, Dave, Crash, Karaoke | same five, same order, types T V I I K |

### Lyrics — confirmed

A vocal track's syllables live in the gap **after** its chunk and before the
next track's data, on a **26-byte lattice** with a Pascal-string syllable at
record offset 12. Reading `Daisy.trk` on that lattice gives, in order:

> Dai- sy dai- sy give me your an- swer do I'm half cra- zy all for the love of
> you It won't be a sty- lish mar- rage I can't af- ford a car- riage but you'll
> look sweet on the seat of a bi- cy- cle built … Dave What are you do- ing I'm
> much bet- ter now

That is Daisy Bell — the song the IBM 704 sang in 1961 and HAL 9000 sings in
*2001* — followed by the two HAL lines. The tracks are named `HAL`, `Dave` and
`Crash`. Getting the words out in the right order, including the spoken coda,
confirms both the stride and the field offset. (`marrage` is a typo in KAE Labs'
original file.)

Per the manual, `-` splits a syllable across notes and `=` repeats a vowel over
another note; every note must contain a vowel.

### Note events — confirmed

Decoded by exporting songs from the running application via `File > Export` and
diffing the resulting MIDI against the raw bytes. **Every track of both exported
songs reproduces the export exactly** — same note count, same pitches, same
durations:

| song | track | from `.trk` | from MIDI | identical |
|---|---|---|---|---|
| Daisy | HAL | 59 | 59 | yes |
| Daisy | Dave | 12 | 12 | yes |
| Daisy | Crash | 2 | 2 | yes |
| Daisy | Karaoke | 59 | 59 | yes |
| Acappella | Vocals 1 | 149 | 149 | yes |
| Acappella | Vocals 2 | 145 | 145 | yes |
| Acappella | Vocals 3 | 134 | 134 | yes |
| Acappella | Karaoke | 96 | 96 | yes |

Acappella is the more demanding case: four vocal tracks, and the one that
exposed the need to match the note type as well as the record shape.

Events are **12-byte records on a lattice**. A note record:

| Offset | Type | Meaning |
|---|---|---|
| 0 | u8 | varies unpredictably; looks like a stale pointer byte |
| 1 | u8 | **owning track, 0-based** — 1 for HAL, 4 for Karaoke |
| 2 | u8 | event type: **6 on vocal tracks, 1 on instrumental** |
| 3 | u8 | **MIDI pitch** |
| 4 | u8 | **velocity**, internal scale (export maps 114 → 52, 120 → 55) |
| 5–7 | u24 | **duration in ticks, 240 per quarter note** |
| 8–9 | u16 | **lyric index** — joins this note to its syllable |
| 10–11 | u16 | monotonic position; usually tracks the start beat but can hold 0xFFFF |

Three things are easy to get wrong here:

**The track table is not a data boundary.** Events are one interleaved stream
and `byte[1]` is what assigns a record to a track — a record owned by Dave sits
inside what the table calls Crash's chunk, and HAL's notes run from `0xf7c` to
`0x19e4`, straight across its own chunk's end. Parse by owner, not by extent.

**The lattice phase differs per track** — 4 for HAL, 8 for Dave, 0 for Crash —
so it has to be measured, not assumed.

**Type-8 shadow records.** Alongside each vocal note sits a record repeating its
pitch and duration with velocity 0 and index 0. These are not notes and the
application's own export does not emit them; requiring a non-zero velocity is
what rejects them. Ordinary control events are easier — they carry pitch 0.

### Other events on the same lattice

Every record carries its owning track in `byte[1]` and a type in `byte[2]`.
Counts for `Daisy.trk`:

| track | type | count | what |
|---|---|---|---|
| HAL (vocal) | 0x06 | 59 | notes |
| HAL | 0x08 | 15 | shadow records (velocity 0) |
| HAL | 0x20 | 169 | continuous control |
| Dave (instrumental) | 0x01 | 13 | notes |
| Dave | 0x21 | 672 | continuous control |
| Karaoke | 0x06 / 0x20 | 59 / 169 | as HAL |

So the note type is **0x01 on instrumental tracks and 0x06 on vocal ones**, and
the continuous-control type likewise **0x21 / 0x20**. Control records hold a
value in `byte[4]` and, unlike notes, a **zero duration**.

Shape alone is not quite enough, though. Across `Acappella.trk` a handful of
other event types (0x22, 0x2f, 0x3d) also carry a plausible pitch, velocity and
duration — five records in total, which is exactly the margin by which a
shape-only filter overcounted that song. The parser therefore also requires the
record's type to equal the track's note type, and finds that type by taking the
commonest one among note-shaped records rather than hardcoding a table: on
Vocals 1 the census reads `6: 149, 34: 2, 61: 2, 47: 1`, so the genuine type
wins by a wide margin.

The control values are written densely and move smoothly: HAL's run
`49, 48, 46, 44, 42, 41, 39, 38, 41, 43, 46, 48, 51, …` over a 0–127 range,
Dave's ramps `64, 66, 68, 70, …` over 64–255. The manual documents a dozen
control types per track (Volume, Pitch-Bend, Brightness, Chorus, Vib Depth, Vib
Freq, Portamento, Breath, Noise, …); which of them these two streams carry is
**not** established here.

### Timing — not recovered

Note **durations** are exact, but a note's **absolute start tick is not
recoverable** from its record alone. `byte[10..11]` is a position counter that
is monotonic within a phrase but resets between phrases, in step with the lyric
index: HAL's spoken lines carry indices 48–58 and their own position origin,
the song carries 0–47 and a different one. Against real ticks the field lands a
beat low in the spoken section and a beat high in the sung one, so it is
phrase-relative, and reconstructing absolute time needs the phrase structure,
which is still undecoded.

Consequently the parser exposes pitch, duration, velocity, lyric and phoneme —
all verified — but does **not** attempt to emit timed MIDI.

Notes are stored in time order while lyrics are stored in index order, which is
why the two arrays appear rotated relative to each other: HAL's note records
begin with lyric indices 48–58 (the spoken lines, which happen first) before
0–47 (the song). `byte[8..9]` is the join.

### Phonemes — confirmed

The export writes each note's phonemes as a MIDI **Lyric** meta event and its
text as a **Text** meta event, both at the note's tick, exactly as the manual
says. The phoneme strings use case to encode symbol length:

> **every uppercase symbol is exactly two letters, every lowercase symbol
> exactly one**

so a string tokenises with no dictionary at all. Verified across every phoneme
in `Daisy.trk`: all uppercase runs are of even length.

```
swIYt   -> s w IY t          kAEnTX -> k AE n TX
dEYv    -> d EY v            fORDX  -> f OR DX
IHNG    -> IH NG             wUXDX  -> w UX DX
```

The 44-symbol inventory is ARPABET plus allophones:

```
AA AE AO AR AW AX AY CH DH DX EH EL ER EY IH IY JH LX
NG OR OW SH TX UH UW UX XR YU  +  b d f g h k l m n r s t v w y z
```

`DX` is the alveolar flap, `TX` an unreleased t, `LX` a dark l, `EL` a syllabic
l, `AX`/`UX` schwas. The manual's discussion of a "t" between vowels becoming a
tongue flap shows up directly in the data: *"What are"* is `w UX DX` + `AR`.

## `EnglishLex` — header decoded, body not

440,772 bytes. The header is now understood; the entry body is not.

```
0x00  u32    0
0x04  u32    0x00010000        version 1.0
0x08  u32    2
0x0c  u32    26213             word count
0x10  27 x u32                 cumulative word index per initial letter
0x7c  ...                      packed entries, to end of file
```

The 27 values at `0x10` ascend and **end exactly on the word count**, so they are
per-letter boundaries rather than byte offsets. Differencing them gives the
words per initial letter, and the result is unmistakably English:

| | | | | | | | |
|---|---|---|---|---|---|---|---|
| A 1750 | B 1421 | C 2524 | D 1534 | E 1112 | F 1085 | G 833 | H 912 |
| I 1247 | J 278 | K 200 | L 795 | M 1459 | N 612 | O 709 | P 2075 |
| Q 112 | R 1350 | S 2837 | T 1321 | U 776 | V 467 | W 622 | X 13 |
| Y 106 | Z 59 | | | | | | |

S, C and P largest; X and Z tiny. That the counts land on 26,213 to the word is
what confirms the reading.

The body averages ~16.8 bytes per entry, uses all 256 byte values, and is
bit-packed — entries are not recoverable by inspection. There is visible
structure: leading bytes ascend across consecutive entries (`c6 … c6 … c7 … c8
…`, and within the `c8` group the next byte runs `32, 37, 38, 3a, 3b, 43`),
consistent with a sorted key holding the word itself. Finishing it likely needs
the PowerPC disassembly.

**In practice this rarely matters.** The application computes pronunciations
itself and its MIDI export writes them out as Lyric meta events, so the phonemes
for any song are obtainable without cracking the lexicon at all — see above.

## Tools

| Path | Does |
|---|---|
| `tools/machrsrc.py` | classic Mac resource fork reader |
| `tools/mvox.py` | voice bank parser; `python tools/mvox.py assets/GMSpeech.rsrc` |
| `tools/trk.py` | song parser: tracks, notes, lyrics; `python tools/trk.py "assets/Demo Music/Daisy.trk"` |
| `tools/smf.py` | MIDI reader for the app's export, plus `split_phonemes()` |
| `tools/validate.py` | checks a `.trk` parse against the app's MIDI export |
