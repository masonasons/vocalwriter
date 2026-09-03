This is the build of the newest commit on `main`, replaced every time
something lands. It is not a version: for those, see the
[releases page](../../releases).

**These builds do not contain VocalWriter.** The synthesis is VocalWriter
2.0.1's own PowerPC code, and that code, its `EnglishLex` dictionary and its
`GMSpeech` voice bank belong to KAE Labs, 2005 — so they are not in this
repository and cannot be in anything built from it. You supply your own copy.
Put a folder named `assets` beside the program, holding at least:

```
assets/EnglishLex
assets/GMSpeech.rsrc
assets/VocalWriter.app/Contents/MacOS/VocalWriter
assets/VocalWriter.app/Contents/Resources/VocalWriter.rsrc
```

The program looks there on startup and names anything it cannot find, so you
will not be left wondering why it is silent.

**Windows** — `VocalWriterStudio-win64.zip`. Unzip it anywhere and run
`VocalWriterStudio.exe` from inside the folder, which has to stay together.
The `assets` folder goes beside the executable. It is unsigned, so SmartScreen
stops it the first time: "More info", then "Run anyway".

**macOS** — `VocalWriterStudio-macos-arm64.zip`. Apple Silicon. Unzip and put
`VocalWriter Studio.app` where you like, with the `assets` folder beside the
`.app` itself. It is signed only ad-hoc, not notarised, so macOS refuses to
open it until the download flag is cleared:

```
xattr -c "/Applications/VocalWriter Studio.app"
```
