This is the build of the newest commit on `main`, replaced every time
something lands. It is not a version: for those, see the
[releases page](../../releases).

`Read Me.txt` in the zip is the guide: the keys, what a song is made of, the
voices, and what to do if something goes wrong.

It runs as it stands — VocalWriter 2.0.1's dictionary, voices and engine
tables are inside, and the synthesis is VocalWriter's own PowerPC code
recreated in C, which renders a minute of singing in about half a second.

**Windows** — `VocalWriterStudio-win64.zip`. Unzip it anywhere and run
`VocalWriterStudio.exe` from inside the folder, which has to stay together.
It is unsigned, so SmartScreen stops it the first time: "More info", then
"Run anyway".

**macOS** — `VocalWriterStudio-macos-arm64.zip`. Apple Silicon. Unzip and put
`VocalWriter Studio.app` where you like. It is signed only ad-hoc, not
notarised, so macOS refuses to open it until the download flag is cleared:

```
xattr -c "/Applications/VocalWriter Studio.app"
```

VocalWriter 2.0.1 and its data files are Copyright (c) 2005 KAE Labs, all
rights reserved, and are not covered by this project's MIT licence. See
[NOTICE](../../blob/main/NOTICE).
