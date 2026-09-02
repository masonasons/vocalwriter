#!/usr/bin/env python3
"""Where the application's own data lives, running from source or frozen.

VocalWriter's binary, its dictionary and its voice bank are what actually make
the sound, and they belong to KAE Labs -- so they are not built into the
distributed program. It looks for them beside the executable instead, the way
an emulator looks for the software it runs. From a source checkout that is just
the project directory; from a build it is the folder the recipient drops their
own copy of VocalWriter 2.0 into.
"""
import os
import sys
import tempfile
import zipfile

#: what has to be present for the synthesiser to work at all
REQUIRED = (
    os.path.join('assets', 'VocalWriter.app', 'Contents', 'MacOS',
                 'VocalWriter'),
    os.path.join('assets', 'VocalWriter.app', 'Contents', 'Resources',
                 'VocalWriter.rsrc'),
    os.path.join('assets', 'GMSpeech.rsrc'),
    os.path.join('assets', 'EnglishLex'),
)


def frozen():
    return getattr(sys, 'frozen', False)


def _candidates():
    """Places to look, nearest first."""
    here = os.path.dirname(os.path.abspath(__file__))
    source_root = os.path.dirname(here)
    if frozen():
        exe = os.path.dirname(os.path.abspath(sys.executable))
        yield exe
        yield os.path.dirname(exe)          # exe in a bin/ subfolder
        # In a macOS bundle the executable is at Foo.app/Contents/MacOS, so
        # the assets belong beside the .app: three levels up from there.
        if exe.endswith(os.path.join('.app', 'Contents', 'MacOS')):
            yield os.path.dirname(os.path.dirname(os.path.dirname(exe)))
        bundled = getattr(sys, '_MEIPASS', None)
        if bundled:
            yield bundled
    else:
        yield source_root
    env = os.environ.get('VOCALWRITER_DATA')
    if env:
        yield env


#: a build carries the data as one archive rather than loose files
BUNDLE = 'assets_bundle.zip'


def _complete(base):
    return all(os.path.exists(os.path.join(base, r)) for r in REQUIRED)


def _unpacked():
    """Unpack the bundled archive once, and return where it went."""
    dest = os.path.join(tempfile.gettempdir(), 'vocalwriter-assets')
    if _complete(dest):
        return dest
    for base in _candidates():
        archive = os.path.join(base, BUNDLE)
        if os.path.isfile(archive):
            try:
                with zipfile.ZipFile(archive) as z:
                    z.extractall(dest)
            except (OSError, zipfile.BadZipFile):
                return None
            if _complete(dest):
                return dest
    return None


def data_root():
    """Where `assets/` is: loose files first, then whatever the build carries.

    Loose files win so that a copy placed beside the program overrides the
    built-in one.
    """
    first = None
    for base in _candidates():
        if first is None:
            first = base
        if _complete(base):
            return base
    return _unpacked() or first or os.getcwd()


def asset(*parts):
    return os.path.join(data_root(), *parts)


def bundled(*parts):
    """A file belonging to *this* program rather than to VocalWriter.

    The two are not in the same place and must not be looked for in the same
    place. VocalWriter's own files are data the program is pointed at, and in
    a build they are unpacked into a temporary folder; anything shipped as part
    of this program is inside the bundle. Resolving one as the other is how the
    phoneme palette came to be missing from the packaged build, leaving the
    picker offering a single entry.
    """
    if frozen():
        base = getattr(sys, '_MEIPASS', None) or os.path.dirname(
            os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def missing():
    """Which required files are absent, so the failure can be explained."""
    base = data_root()
    return [r for r in REQUIRED if not os.path.exists(os.path.join(base, r))]
