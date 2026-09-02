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
os.makedirs('build', exist_ok=True)
bundle = os.path.join('build', 'assets_bundle.zip')
with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
    for rel in ASSETS:
        z.write(rel, rel)

data = [('emu/phoneme_palette.json', 'emu'), (bundle, '.')]

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
