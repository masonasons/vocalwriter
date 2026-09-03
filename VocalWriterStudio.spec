# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build.

    pyinstaller VocalWriterStudio.spec

Produces `dist/VocalWriterStudio/`, a folder to zip and send. One executable
serves as both the window and, re-run with --engine, the synthesis process.

VocalWriter's own files are included, so the build runs as it stands. They
remain KAE Labs' software. An `assets` folder placed beside the executable
still takes precedence over the built-in copy, so a recipient can substitute
their own.
"""
import os
import sys

block_cipher = None

# The compiled core is named for the interpreter that built it. Take only the
# one this interpreter can load -- a build for another runtime is dead weight
# and only confuses the picture if the right one fails to import.
import importlib.machinery
suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
core = [(os.path.join('ppc', f), 'ppc')
        for f in os.listdir('ppc')
        if f.startswith('_ppccore') and f.endswith(suffixes)]
if not core:
    raise SystemExit('build the core first: python -m ppc.build_core')

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
    binaries=core,
    datas=data,
    # _cffi_backend is cffi's runtime. Nothing imports it in Python source --
    # the compiled module needs it at load time -- so it has to be named here
    # or the core fails to import and everything silently falls back to the
    # Python interpreter, which is fifty times slower.
    hiddenimports=['ppc._ppccore', '_cffi_backend'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'capstone', 'PIL', 'tkinter',
              'pytest', 'setuptools'],
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
