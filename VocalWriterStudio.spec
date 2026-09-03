# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build.

    sh engine/build.sh              # the synthesiser
    pyinstaller VocalWriterStudio.spec

Produces `dist/VocalWriterStudio/`, a folder to zip and send.

The synthesis is VocalWriter's own code recreated in C (the `engine`
submodule, github.com/masonasons/VocalWriterC), loaded as a shared library.
It has to be built first; this refuses to package a program that cannot make
a sound.

VocalWriter's own data files are included where they are present, so the build
runs as it stands. They remain KAE Labs' software. An `assets` folder placed
beside the executable still takes precedence over the built-in copy, so a
recipient can substitute their own.
"""
import os
import sys

block_cipher = None

# The synthesiser. Everything the program does with sound goes through it,
# so a build without it is not worth making.
LIB = {'win32': 'libvocalwriter.dll',
       'darwin': 'libvocalwriter.dylib'}.get(sys.platform, 'libvocalwriter.so')
lib = None
for where in (os.path.join('lib', LIB),
              os.path.join('engine', 'build', LIB),
              os.path.join('..', 'VocalWriterC', 'build', LIB)):
    if os.path.isfile(where):
        lib = [(where, 'lib')]
        break
if lib is None:
    raise SystemExit(
        'the synthesiser is not built: run `sh engine/build.sh` first '
        '(git submodule update --init, if the submodule is empty)')

# The PowerPC interpreter is not in the build. It is what the C engine was
# checked against and it is still in the repository, but nothing here runs on
# it: it renders about a thousandth as fast.
core = []

# The four VocalWriter files that actually make the sound go in as one zip,
# not as loose files. One of them is a PowerPC Mach-O executable, and
# PyInstaller on macOS recognises that, tries to process and sign it as if it
# were part of this program, and fails. Inside an archive it is just data.
APP = 'assets/VocalWriter.app/Contents'
ASSETS = ('assets/EnglishLex', 'assets/GMSpeech.rsrc',
          APP + '/MacOS/VocalWriter', APP + '/Resources/VocalWriter.rsrc')

import zipfile
data = [('emu/phoneme_palette.json', 'emu')]
absent = [rel for rel in ASSETS if not os.path.isfile(rel)]
if absent:
    # A build without them is a real thing to want: it is what continuous
    # integration can make in public, and what anyone can build from a clean
    # checkout. `ppc/paths.py` looks for an `assets` folder beside the
    # executable, so the recipient supplies their own copy and the program
    # says as much on startup if they have not.
    print('*** building WITHOUT the VocalWriter files: %s'
          % ', '.join(absent))
else:
    os.makedirs('build', exist_ok=True)
    bundle = os.path.join('build', 'assets_bundle.zip')
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in ASSETS:
            z.write(rel, rel)
    data.append((bundle, '.'))

a = Analysis(
    ['launch.py'],
    pathex=[os.path.abspath('.')],
    binaries=core + lib,
    datas=data,
    # _cffi_backend is cffi's runtime. Nothing imports it in Python source --
    # cffi reaches for it when the library is opened -- so it has to be named
    # here or there is no synthesiser at all.
    hiddenimports=['_cffi_backend'],
    hookspath=[],
    runtime_hooks=[],
    # The interpreter and its compiled core are excluded by name. They
    # are still in the repository -- they are what the C engine was
    # checked against -- but a build has no use for something that
    # renders a thousand times slower, and PyInstaller picks the
    # extension module up from the source tree if it is not told.
    excludes=['matplotlib', 'scipy', 'capstone', 'PIL', 'tkinter',
              'pytest', 'setuptools',
              'ppc._ppccore', 'ppc.fastcpu', 'ppc.cpu', 'ppc.image',
              'ppc.synth', 'ppc.lexicon_ppc'],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VocalWriterStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a window, not a terminal
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='VocalWriterStudio',
)

# On macOS, wrap it as a bundle so it is an application rather than a folder
# with a unix executable in it: double-clickable, its own Dock entry and menu
# bar. `ppc/paths.py` already knows to look beside the .app for an `assets`
# folder, so a recipient drops VocalWriter 2.0's files next to it.
#
# No icon is set. VocalWriter's own App.icns is KAE Labs' artwork and does not
# belong in a program that is not theirs.
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='VocalWriter Studio.app',
        icon=None,
        bundle_identifier='com.github.vocalwriter-studio',
        info_plist={
            'CFBundleName': 'VocalWriter Studio',
            'CFBundleDisplayName': 'VocalWriter Studio',
            'CFBundleShortVersionString': '1.1',
            'CFBundleVersion': '1.1',
            'NSHighResolutionCapable': True,
            # It only ever plays audio it has rendered itself.
            'LSApplicationCategoryType': 'public.app-category.music',
            'NSHumanReadableCopyright':
                'Runs VocalWriter 2.0 (KAE Labs, 2005), which is not included.',
        },
    )
