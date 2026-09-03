#!/usr/bin/env python3
"""Settings that belong to the program rather than to a song.

A song carries its own tempo, its reverb and the rest, and those travel with
the file. What is here is how you like to work -- and that follows you from
song to song, so it lives in one small JSON file beside the operating system's
other per-user settings.

Nothing here changes a note or a sample. If the file is missing, unreadable,
or full of nonsense, the defaults stand and the program says nothing about it:
a preference is not worth an error message.
"""
import json
import os
import sys

APP = 'VocalWriter Studio'

DEFAULTS = {
    #: preview a note as it is nudged, so pitch and length can be heard
    #: rather than read
    'auto_preview': False,
}


def folder():
    """Where this platform keeps a program's per-user settings."""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, APP)
    if sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'Library',
                            'Application Support', APP)
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
        os.path.expanduser('~'), '.config')
    return os.path.join(base, 'vocalwriter-studio')


def path():
    return os.path.join(folder(), 'settings.json')


def load():
    """The settings, with anything missing filled in from the defaults."""
    out = dict(DEFAULTS)
    try:
        with open(path(), encoding='utf-8') as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return out
    if isinstance(doc, dict):
        for key, default in DEFAULTS.items():
            if key in doc and isinstance(doc[key], type(default)):
                out[key] = doc[key]
    return out


def save(values):
    """Write the settings. Returns whether it worked, for a caller that cares;
    a preference that cannot be written is not worth stopping for."""
    try:
        os.makedirs(folder(), exist_ok=True)
        with open(path(), 'w', encoding='utf-8') as fh:
            json.dump(dict(DEFAULTS, **values), fh, indent=1)
            fh.write('\n')
        return True
    except OSError:
        return False
