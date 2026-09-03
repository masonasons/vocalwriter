# VocalWriter Studio

A program for writing songs and having them sung. The singing is VocalWriter
2.0.1's own synthesiser -- KAE Labs, 2005, for the PowerPC Macintosh -- so what
you hear is what that program made, running on a machine it was never built
for.

It is meant to be used without seeing it. Everything is on a menu with its key
beside it, editing happens in ordinary labelled dialogs rather than inside the
list, and every field says its own name, so a screen reader has something to
read at every point. Nothing is said with colour or position alone.

## Starting it

**Windows.** Unzip anywhere and run `VocalWriterStudio.exe` from inside the
folder, which has to stay together. It is not signed, so the first run brings
up SmartScreen: choose "More info", then "Run anyway".

**macOS.** Apple Silicon. Unzip and put `VocalWriter Studio.app` where you
like. It is signed only ad-hoc, so macOS will refuse to open it until the
download flag is cleared:

    xattr -c "/Applications/VocalWriter Studio.app"

Everything it needs is inside. There is nothing to install and nothing to buy.

When it starts it says what it found, in the Messages box at the bottom:

    engine ready: C engine, Python 3.13.15, 87 voices
    this is build a7f689a 2026-09-03

That second line is worth reading out if you ever report a problem.

## The shape of a song

A song is a set of **tracks**. Each track has its own voice, its own volume
and pan, and its own notes; F6 moves between the list of tracks and the list
of notes.

A **note** carries a pitch, a length, and a *group* of phonemes -- because
that is how singing works. A syllable sits on a note; it is not one sound per
note. "Add word" (Ctrl+W) looks a word up in VocalWriter's own dictionary and
can spread it over as many notes as it has syllables.

A **rest** is a note with nothing to sing. Rests matter more than they look:
they are how a phrase ends, and the program renders each phrase separately so
that a silence is exactly as long as it says it is.

## The keys

Tracks:

| | |
|---|---|
| F6 | between the tracks list and the notes list |
| Ctrl+T | add a track |
| Enter on a track | its name, voice, volume, pan, reverb and voice controls |
| M / S on a track | mute / solo |
| Delete on a track | remove it |
| Ctrl+Up / Ctrl+Down | reorder the parts |

Notes:

| | |
|---|---|
| Ctrl+W | add a word, looked up in the dictionary |
| Ctrl+N / Ctrl+R | add a note / a rest |
| Ctrl+Shift+R | a rest to the end of the bar |
| Ctrl+E, or Enter | edit a note, including its pitch bend |
| Ctrl+D, or Delete | remove it |
| Alt+Up / Alt+Down | transpose a semitone |
| Alt+Right / Alt+Left | a sixteenth note longer or shorter |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+A | copy, cut, paste, select all |
| Ctrl+Up / Ctrl+Down | move a note earlier or later |
| Ctrl+G | go to a bar |
| Shift with the arrows | select more than one note |

Hearing it:

| | |
|---|---|
| Space | play from the note the cursor is on, or stop |
| Ctrl+P | play from the beginning |
| Ctrl+H | hear the selected note on its own |
| Ctrl+M | the metronome, which is never written into a file |
| Ctrl+. | stop |

Everything else:

| | |
|---|---|
| Ctrl+, | song settings |
| Ctrl+Shift+P | hear a note whenever it is nudged |
| Ctrl+O / Ctrl+S / Ctrl+Shift+S | open, save, save as |
| Ctrl+I | import a MIDI file |
| Ctrl+Shift+S / Ctrl+Shift+T | export one WAV / one WAV per track |
| F1 | read the keys out into Messages |

## Song settings (Ctrl+comma)

What belongs to the whole song rather than to one part: the tempo, the time
signature, how long the consonants are, the reverb, and the engine's own voice
controls. A track can take any of the last two over for itself in the track
dialog; until it does, it follows the song.

**Consonant length** is a percentage. 100 is their natural length; lower
clips them against the vowels, which tightens a fast line.

**Consonants before the beat** should stay on. A note is heard where its vowel
is, not where it starts, so a word beginning with "str-" arrives about 140
milliseconds after its beat and a word beginning with a vowel arrives on time
-- which is what makes a line drag unevenly. With this on, a note that opens
with consonants starts early enough for its vowel to land where the note is
written, and the consonants are sung into the end of the note before it, the
way a singer does.

**Reverb** is the application's own: a room size and how much of it is heard.
VocalWriter's own default was 40 and 24. Parts that share a setting go through
one reverberator and share its tail; a part given its own is in a room of its
own, which is how a dry lead sits in front of a choir in a hall.

**The voice controls** -- colour, vibrato depth and rate, chorus, breath,
detune and portamento -- are the engine's. Portamento is the glide between
notes: 0 steps straight there, and VocalWriter's own default was about 40.

## The voices

There are **87**. The ones with people's names (Robert, Sarah, Tracy, Andy,
Abe, Webster, Richard, Desmond and the rest) are the natural voices. The ones
with instrument names are the same synthesiser with a recorded instrument in
place of the vocal cords -- VocalWriter's manual calls them "special synthetic
models of musical instruments with dynamic vocal tracts" -- and they sing the
words just as readily. Strings sings. Trumpet sings.

Some of them sit where their sample sits rather than where the note is
written: Piano sounds an octave below the note, and Calliope is higher still.
That is the voice, not a fault; VocalWriter does the same with them.

The instrument voices come out of the engine far louder than the natural
ones -- as much as fifty times over full scale -- so each voice is measured
once and held inside full scale. Turning a track's volume down works the way
you would expect: it goes into the engine, before anything can clip.

## Words, and MIDI

**Typed words** go through VocalWriter's own dictionary, its suffix rules and,
for anything it does not know, its letter-to-sound rules. What comes back is
phonemes on notes, which you can then edit: the note editor's picker offers
all 56 the engine can pronounce, with an example word for each.

**Importing a MIDI file** (Ctrl+I) brings in the notes, the lengths, the
tempo, the time signature and the pitch bends. If the file has words in it
they are looked up. If it has none -- which is most files -- every note is
given `AA`, the open vowel of "father", so the line can be played and listened
to before a word is typed. VocalWriter's own MIDI exports carry the phonemes
they chose, so one of those comes back ready to sing.

**Exporting** writes 44100 Hz WAV. "Export tracks" writes one file per track,
each carrying the volume and pan it has in the song, so laying them back on
top of one another gives the mix.

## When something goes wrong

The song is kept. A copy is written a couple of seconds after it stops
changing, and if the program ever closes without being asked to, the next
start offers it back. That is not a reason to stop saving, but it means a
crash costs a moment rather than an evening.

Two things make a problem report answerable:

- **the build line** the program prints when it starts;
- **the song**, which after a crash is sitting in `recovery.vws`, beside the
  settings: `%APPDATA%\VocalWriter Studio` on Windows,
  `~/Library/Application Support/VocalWriter Studio` on a Mac.

## What this is made of

The editor is ours. The synthesis is VocalWriter's own PowerPC code,
recreated in C function by function and checked against the original until
every sample agrees -- against a PowerPC interpreter running the real binary,
and against the audio VocalWriter itself exported on a Macintosh, which it
matches exactly.

VocalWriter 2.0.1 and the data files in this program -- the dictionary, the
voices, the wavetables and the engine's tables -- are Copyright (c) 2005 KAE
Labs, all rights reserved, and are not covered by this project's licence. The
rest is MIT. See NOTICE in the source repository:

    https://github.com/masonasons/vocalwriter
