# VocalWriter 2.0, singing again

**VocalWriter 2.0.1** (KAE Labs, 2005) is a Mac OS X singing synthesiser built
on what its authors trademarked **Resonant Articulatory Synthesis** — a
mathematical model of the human vocal tract. It shipped as a PowerPC-only
binary, so no Mac built since 2006 can run it and Rosetta-for-PowerPC died with
Mac OS X 10.7 in 2011.

It sings here with no Mac, no emulator and no VM: **its own PowerPC synthesis
code is executed directly**, by a small interpreter written for the purpose. The
samples are the ones VocalWriter itself computes, not an imitation of them.

That is the second attempt. The first was a careful re-implementation of the
algorithm — Klatt-style cascade/parallel formant synthesis driven by the
original's own tables — and it was not close enough. The engine turned out to be
a better target than the algorithm: 48 functions, about 11,000 instructions, 65
distinct opcodes, and essentially no calls outside itself. Running that is both
less work and exactly faithful.

```
ppc/      the engine as this program drives it
            cengine.py the C synthesiser, which is what renders
            render.py  notes in, samples out
            engine.py  what the editor asks: pronunciations and audio
            midi.py    sings a VocalWriter MIDI export
            cpu.py     32-bit big-endian PowerPC, user mode -- the reference
            image.py   loads the Mach-O and calls into it
            synth.py   builds the memory the engine expects
app/      VocalWriter Studio, the editor
engine/   the synthesiser in C, as a submodule (VocalWriterC)
assets/   VocalWriter 2.0.1's data files (see below)
tools/    format parsers (machrsrc, machsyms, ttvi, smf, trk) and ppcdis
docs/     ENGINE.md -- what the engine does; FORMATS.md -- the file formats
```

and two directories you make yourself:

```
emu/out/  reference audio rendered by VocalWriter itself, for comparison
out/      rendered audio
```

## Builds

Every commit on `main` is built for Windows and for Apple Silicon, and the
newest pair is at [releases/latest](../../releases/latest). Those builds carry
none of VocalWriter -- see below -- so they need a copy of the files put beside
them; the tagged releases on the [releases page](../../releases) are the ones
built with the files in place, which run as they stand.

## What is here

VocalWriter 2.0.1's own data files are in this repository, so a clone runs:

```
assets/EnglishLex                              the dictionary
assets/GMSpeech.rsrc                           the voices
assets/GMBank.rsrc                             the wavetables under them
assets/VocalWriter.app/.../VocalWriter.rsrc    the engine's tables
assets/VocalWriter.app/.../MacOS/VocalWriter   the PowerPC binary
```

They are the work of KAE Labs, 2005, and are still theirs -- not covered by
this project's licence. See [NOTICE](NOTICE). Everything else of the
application -- the demo songs, the tutorial, the manual -- is not needed to
run this and is not here.

The synthesis itself is in the `engine` submodule
([VocalWriterC](https://github.com/masonasons/VocalWriterC)): VocalWriter's
own PowerPC code, lifted into C function by function and checked against the
original until every sample agrees. Build it once:

```bash
git submodule update --init
sh engine/build.sh
```

That produces `engine/build/libvocalwriter.<dll|dylib|so>`, which the editor
loads. Without it there is no sound and the program says so.

The PowerPC interpreter in `ppc/` is still here and is still the reference the
C engine is measured against -- it is what the analysis was done with, and
what VocalWriterC's differential tests compare against, field by field after
every frame. Nothing in the editor runs on it: it renders about a thousandth
as fast. `VOCALWRITER_ENGINE=interpreter` asks for it, for comparing the two.

## The application

```bash
python -m app.studio        # wxPython, so CPython -- see below
```

VocalWriter Studio builds a song note by note. A note carries a *group* of
phonemes and one pitch, because that is how singing works: a syllable sits on a
note, not one phoneme per note. "Add word" looks a word up in VocalWriter's own
dictionary and can spread it over as many notes as it has syllables.

**It is built to be used without seeing it.** Everything is on the menus with a
shortcut on each item, so a screen reader announces the key along with the name;
editing happens in dialogs with ordinary labelled fields rather than inside the
list; and every control states its own accessible name rather than leaving
Windows to guess it from whatever static text happens to precede it, which is
wrong more often than not. Nothing is conveyed by colour or position alone.

A song is made of **tracks**, each with its own voice, volume, pan and notes.
There are **87 voices**, and the ones with instrument names sing as readily as
the ones with people's names: "special synthetic models of musical instruments
with dynamic vocal tracts", as VocalWriter's manual puts it, so Strings and
Trumpet sing the lyrics too. Five of them -- Miles, Ellen1, Kae1, Sonny1 and
Ed1 -- sit in the bank with nothing in its program map pointing at them, and
are reachable here because a voice is chosen by its place in the bank rather
than by a program change. The instrument voices come out about three times as
loud as Robert; the track's volume is the place to fix that.

| | |
|---|---|
| F6 | between the tracks list and the notes list |
| Ctrl+T | add a track |
| Enter on a track | its name, voice, volume, pan and voice controls |
| M / S on a track | mute / solo |
| Delete on a track | remove it |
| Ctrl+Up / Ctrl+Down | reorder the parts |

| | |
|---|---|
| Ctrl+W / Ctrl+N / Ctrl+R | add a word / a note / a rest |
| Ctrl+Shift+R | a rest to the end of the bar |
| Ctrl+E, or Enter on a note | edit it, including its pitch bend |
| Ctrl+D, or Delete | remove it |
| Alt+Up / Alt+Down | transpose a semitone |
| Alt+Right / Alt+Left | a sixteenth note longer or shorter |
| Ctrl+Z / Ctrl+Shift+Z | undo / redo |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+A | copy, cut, paste, select all |
| Ctrl+Up / Ctrl+Down | move a note earlier or later |
| Ctrl+G | go to a bar |
| Ctrl+, | song settings: tempo, signature, consonants, reverb, voice controls |
| Ctrl+Shift+P | hear a note whenever it is nudged |
| Space | play from the cursor, or stop |
| Ctrl+P / Ctrl+H / Ctrl+M / Ctrl+. | play from the start / hear one note / metronome / stop |
| Ctrl+O / Ctrl+S | open and save a project |
| Ctrl+I | import a MIDI file |
| Ctrl+Shift+S / Ctrl+Shift+T | export one WAV / one WAV per track |
| F1 | list the keys in Messages |

Undo and redo cover the last 100 edits to notes, tracks, and song settings.
The Edit menu names the next action to undo or redo. Opening, importing, or
starting a new song clears history; saving preserves it. On macOS, use
Command+Z and Command+Shift+Z.

On macOS, Hear Note uses **Option+H**, leaving Command+H available to hide
the app. Play from the start uses **Command+P**.

The picker inside the note editor offers **all 56** phonemes the engine can
pronounce -- the application's own palette shows fifty, and the seven it leaves
out are real, with formants and durations like any other, so they are here too.
The fifty-seventh is silence, which is a rest rather than a phoneme.

What belongs to the whole song -- the tempo, the time signature, how long the
consonants are, the reverb, and the engine's own voice controls -- is set once
in the song settings and then left alone, which is why it is behind Ctrl+comma
rather than in the window. A part follows the song's consonant length, reverb
and voice controls until it is given its own in the track dialog, so setting
the vibrato for a piece is one number in one place rather than the same number
typed into every track. Parts that share a reverb setting go through one
reverberator and share its tail; a part with its own is in a room of its own,
which is how a dry lead sits in front of a choir in a hall.

**A note is heard where its vowel is, not where it starts.** "Straw" on beat
three, with `str-` in front of the vowel, arrives 139 ms after beat three;
"day" arrives 47 ms after; a word beginning with a vowel arrives on time. That
is what makes a line with hard onsets drag, unevenly, in a way that gets worse
the longer the consonants are allowed to be -- and it is why *Consonants before
the beat*, in the song settings, is on. It gives a note that opens with
consonants that much more time and takes it from the note in front, so the
consonants are sung into the end of the previous note and the vowel lands where
the note is written. Measured on the same three words: 47 ms and 139 ms late
become 7 ms and 5 ms.

How you like to work, as opposed to what the song is, lives on the **Settings**
menu and is kept between songs -- at the moment that is one thing, *Preview
notes as they change* (Ctrl+Shift+P), which plays a note whenever its pitch or
length is nudged, once the nudging stops.

The voice controls are the engine's: colour, vibrato depth and rate, chorus,
breath, detune and portamento -- the glide between notes. Which of them do
anything was settled by measuring rather than by reading their names. Nothing
is sent to the engine unless it has been moved off the engine's own default, so
a song that sets none of them renders exactly as it did before they existed.

A MIDI file from anywhere else arrives singing: a note that carries no words
of its own is given **AA**, the open vowel of "father", so the line can be
played and listened to before a single word is typed. Where the file does
carry words, they are looked up -- and note that every program except
VocalWriter writes the sung word in the MIDI Lyric event, which is what that
event is for, while VocalWriter writes the word as a Text event and puts the
phonemes it derived in the Lyric event beside it. Both are read.

Pitch bend belongs to the note and is written as the points it passes through,
so it survives the note being retimed, transposed or moved. Projects are plain
JSON (`.vws`); a MIDI import brings in the notes, the lyrics, the tempo, the
time signature and the bends, and VocalWriter's own exports carry their
phonemes as well, so one of those comes back ready to sing.

Exporting tracks separately gives stems, not separate performances: each file
carries the volume and the pan it has in the song, so laying them back on top of
one another reproduces the mix -- measured, and within one 16-bit step.

It used to run as two processes, because a render under the interpreter took
about as long as the music lasts and nothing that slow can happen on the thread
drawing a window. **The C engine renders a minute of singing in about half a
second**, so the process boundary and its JSON protocol are gone: the engine is
imported and called. It still answers on a worker thread, because half a second
of a frozen window is still a stutter, and finished renders are still cached,
so playing the same thing twice renders once.

```
app/studio.py   the window: notes, phonemes, voices, play
app/engine.py   the engine on a worker thread, answering in callbacks
ppc/engine.py   pronunciations and audio, with the render cache
ppc/cengine.py  the C synthesiser, loaded through cffi
ppc/song.py     words + notes -> audio, without a window
ppc/lexicon.py  words -> phonemes
```

## Singing

Needs Python with NumPy (SciPy and Capstone only for the analysis and
disassembly helpers):

```bash
python3 -m venv .venv && ./.venv/bin/python -m pip install numpy scipy capstone
```

```bash
./.venv/bin/python -m ppc.midi out/Daisy_export.mid --track HAL -o out/HAL_ppc.wav
```

Phrases render in parallel, one process each (`-j` to choose how many). That
mattered when a phrase took a minute; it is now about a tenth of a second.

The export carries everything the engine needs — the notes, the phonemes
VocalWriter derived for each syllable, the tempo map, the program changes and
the pitch bends — so nothing about the performance has to be guessed.

## How it runs

The synthesiser is driven the way the application drives it, by calling its own
functions. `SetSeqAddr` takes one packed block: a voice key, a count, then four
parallel `u16` arrays — phoneme, control, spare, duration. Bit 0 of the control
word marks a phoneme that *begins a note*, and the sequence must end with one,
because `Syllable_Duration` scales a syllable by scanning forward to the next
note-start and gives up if it never finds one.

`e_Fill_Next_Frame` is then a state machine: state 1 starts a phoneme, state 2
emits frames, state 3 is done. When a syllable ends it sets a flag and waits —
that is the engine asking for the next note, and it is what drives the render
loop. `SayFrame` writes 220 sample pairs per frame at 44100 Hz.

`Syllable_Duration` scales every phoneme of a syllable by the same factor to
fill the note, so whatever nominal lengths it is given set the proportions
outright. Handing it the phoneme table's plain durations stretches consonants
along with the vowel — "Dai-" over a 947 ms note gives /d/ a 213 ms closure,
a fifth of a second of silence before the word starts. The tables carry both a
maximum and a minimum per phoneme for exactly this reason: below the syllable's
natural length everything compresses together, and above it the consonants stop
at their maximum while the nucleus holds the rest, which is what a singer does.
Notes are also sung through the small gaps the score leaves inside a phrase
rather than cut to silence.

Several controls have to be set that `Start_Speech` leaves zeroed, and each one
fails silently and characteristically:

* `InitDefaultVoiceCntrls` — without it the two glottal wavetables are mixed
  with weights of zero, so every voiced sample is multiplied by nothing and only
  the fricatives are audible.
* `Speech_Volume` + `SetTotalVolume` — without them the amplitude target
  collapses the same way.
* `Speech_Note`'s velocity is its **fifth** integer argument; put it in the
  fourth and the note amplitude is zero, with the same symptom.

## What is checked

Against VocalWriter's own render of the same track:

| | this | VocalWriter |
|---|---|---|
| pitch, frame by frame vs the original audio | median **10 cents**, 89% within 50 | — |
| vowel spectrum below 4 kHz (time-aligned) | within **1.5 dB** | — |
| vowel spectrum above 4 kHz | **-11 dB** (see Known limits) | — |
| level | rms 2518, peak 14615 | rms 2104, peak 12956 |
| render time for 60 s of song | **0.5 s** | — |

Pitch is exact by construction: the phase increment reads as 8.24 fixed point at
44100 Hz and lands on equal temperament to within a tenth of a percent.

## Known limits

**Above 4 kHz there is a flat ~11 dB deficit, and it is not in the engine.**
Comparing frames that are the same note, time-aligned by cross-correlation
(767 frames, median correlation 0.89), the vowel spectrum matches the original
to within about 1.5 dB up to 4 kHz and then drops by 10-12.5 dB, flat all the
way to 18 kHz. Flat above a corner is an additive component that is missing,
not a filter rolling off.

It is not reachable from inside the synthesiser. Ruled out by measurement: the
radiation shelf (1.2 overshoots to +22 dB before it fills 4-6 kHz, and wrecks
the midrange on the way), the voice colour control, breath/aspiration
(`Speech_Breath` adds 400-4000 Hz and barely touches 6-13 kHz), the six fixed
resonators (all present and stable, out to 4988 Hz), the source wavetable's
band limit (harmonic 47, well above the deficit at these pitches), and the
parallel branch, whose amplitudes the engine's own tables set to zero for
vowels. The application applies stereo processing the engine does not -- its
two channels differ where a raw engine render's do not -- and that output stage
is the remaining candidate.

Note that the long-term average spectrum says the opposite, +3 to +6 dB *too
bright*. Both are true: the average is dominated by consonants, which are about
6 dB bright in tilt, while the vowels are dark. Opposite errors, so no single
tone control corrects them -- which is why fitting one produced a shallow,
misleading optimum.

**Long renders used to drift, and it was not drift.** A continuous render of
more than about twenty seconds grew until the output pinned to full scale and
then collapsed, which read like a filter state running away -- and it was not
the bends, the coefficients or the program changes, and a fresh engine was
always fine, so it went down as unsolved and the renderer split songs into
phrases at rests to avoid it.

It was the output buffer. `SayFrame` writes 440 halfwords wherever `waveIndex`
points, with nothing of its own to stop it, and the buffer held 23.8 seconds:
at 23.8 seconds the engine began writing over whatever came next, which
included its own state. Under the interpreter that corrupted the render -- the
level pinned, then fell to a twelfth, and a 35-second phrase ran on for 542
seconds of nothing. In the C engine it is real memory, so it ended the program
instead. The buffer grows now, and two minutes in one phrase holds a flat
level from beginning to end (peak 0.958 to 0.968 across every ten-second
window). Phrases are still split at rests, for the timing: a rest is a silence
of an exact length, and a continuous render compresses it.

**Speed.** A 60-second song renders in about half a second, cold. The
interpreter it replaced runs at roughly a tenth of real time in one process --
85 seconds of singing took 8 seconds of CPU with the compiled core, and far
longer before that -- which is what the phrase-by-phrase machinery and the
render cache were built around. Both are still here: rests still have to be
exact, and a song still gets played over and over.

Cutting phrases into smaller pieces to spread them over more cores does not
work, and that has not changed: the engine carries much more state across a
note boundary than the filter memory, and rendering 2.5-second pieces on fresh
engines produced audio differing from the correct render by more than the
signal itself, with 60 dB steps at the joins and plainly audible chopping.
Discarded lead-in context did not fix it and cost 56% more CPU. `CHUNK_SECONDS`
in `ppc/midi.py` keeps the machinery but leaves splitting off.

## How the reference audio was made

`emu/out/` holds VocalWriter's own renders, which is what everything above is
measured against. They were made once, under Mac OS X 10.4 (Tiger) PowerPC in
QEMU, using the application's **File > Play-to-Disk** — offline rendering, so
unaffected by emulation speed. Capturing the guest's live audio does not work:
an emulated G4 cannot synthesise speech in real time and roughly 60% of it comes
out as gaps. That harness has been removed now that it has served its purpose;
the VM is no longer needed to render anything.

## What the formats turned out to be

Full detail in [`docs/FORMATS.md`](docs/FORMATS.md). The short version:

- **`GMSpeech`** holds all 87 voices in **30 KB**, because nothing is sampled —
  each voice is a name, a sex flag, five (formant, bandwidth) pairs and two
  source-spectrum tables. Female voices carry systematically higher formants
  than male ones, exactly as a shorter vocal tract predicts. Measuring the
  rendered audio confirms the numbers are real: the sung vocal's strongest
  peaks (2455, 2821 Hz) match the stored set for `Abe`/`Ed1` (2500, 2800 Hz)
  to within 2%, and every close match is male.
- **`.trk`** songs are a track table of 88-byte records pointing at data chunks.
  Parsing `Daisy.trk` cold gives meter 3/4 and tracks `Tempo Track, HAL, Dave,
  Crash, Karaoke` — which is precisely what the application shows when it opens
  the same file.
- **Lyrics** sit on a 26-byte lattice in the gap after each vocal track's chunk.
- **Notes** are 12-byte records tagged with their owning track. Parsed straight
  from the bytes, they reproduce the application's own MIDI export exactly, on
  every track of both songs exported — 8 tracks, 656 notes, same pitches and
  durations.
- **Phonemes** encode symbol length in case: every uppercase symbol is two
  letters, every lowercase one is one, so `swIYt` splits to `s w IY t` with no
  dictionary. The 44 symbols are ARPABET plus allophones.

The note decode is checked against the application itself. `File > Export`
writes a MIDI file with the notes, lyrics and phonemes in it; `validate.py`
parses the `.trk` cold and requires the two to agree:

```
$ python tools/validate.py "assets/Demo Music/Daisy.trk" out/Daisy_export.mid
track            .trk     .mid     match
HAL              59       59       yes
Dave             12       12       yes
Crash            2        2        yes
Karaoke          59       59       yes

OK

$ python tools/validate.py "assets/Demo Music/Acappella.trk" out/Acappella_export.mid
track            .trk     .mid     match
Vocals 1         149      149      yes
Vocals 2         145      145      yes
Vocals 3         134      134      yes
Karaoke          96       96       yes

OK
```

```
$ python tools/trk.py "assets/Demo Music/Daisy.trk"
meter 3/4, 31 tracks
   2  HAL            off=0xc6c    len=0xbc8    vocal
      lyrics: Daisy daisy give me your answer do I'm half crazy all for the
      love of you It won't be a stylish marrage I can't afford a carriage but
      you'll look sweet on the seat of a bicycle built Dave What are you doing
      I'm much better now
```

The demo song is HAL 9000 singing Daisy Bell — the song the IBM 704 sang in
1961, and the one HAL sings while being shut down.

Three traps in the note stream, all of which cost time:

- **The track table is not a data boundary.** Events are one interleaved stream
  and `byte[1]` names the owning track. A record owned by Dave sits inside what
  the table calls Crash's chunk.
- **The 12-byte lattice's phase differs per track** (4, 8, 0 here), so measure it.
- **Shadow records** repeat a vocal note's pitch and duration with velocity 0.
  They are not notes; the app's export omits them.
- **Shape is not enough.** A few other event types also look note-shaped, so the
  record's type must match the track's note type too — found by census rather
  than hardcoded, since it differs by track type (6 vocal, 1 instrumental).

## Licence

The work in this repository -- the interpreter, the format parsers, the
analysis and the editor -- is under the MIT licence; see [LICENSE](LICENSE)
and [NOTICE](NOTICE).

That covers none of VocalWriter. **VocalWriter 2.0.1 is Copyright (c) 2005 KAE
Labs, all rights reserved**, as are its dictionary, its voice bank, its demo
songs and its manual. VocalWriter, VocalTracks and Resonant Articulatory
Synthesis are their trademarks. This is an interoperability and preservation
project for software that no current machine has been able to run since Rosetta
for PowerPC was withdrawn in 2011. It is not affiliated with or endorsed by KAE
Labs.

## Not done

- **Absolute note timing.** Durations are exact, but a note's start tick is not
  recoverable from its record. The position field is phrase-relative and resets
  along with the lyric index, so reconstructing absolute time needs the phrase
  structure, which is undecoded. The parser therefore reports pitch, duration,
  velocity, lyric and phoneme, and does not emit timed MIDI.
- **`EnglishLex` body.** The header is decoded: 26,213 words and 27 cumulative
  per-letter counts that land on that total exactly, differencing to a textbook
  English letter distribution (S 2837, C 2524, X 13, Z 59). The entries
  themselves are bit-packed at ~16.8 bytes each and still opaque; that likely
  needs the disassembly. It matters little in practice, since the MIDI export
  hands over the app's own computed phonemes.
- **A modern re-implementation** of the RAS engine. The voice data is small and
  structured enough to make this genuinely feasible.
