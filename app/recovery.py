#!/usr/bin/env python3
"""What the song was, in case the program does not get to say goodbye.

The synthesiser runs in this process. That is what makes it fast, and it means
a fault in the engine ends the program rather than one process of it -- so the
song has to be somewhere other than in memory. A copy is written beside the
settings whenever the song changes and a moment has passed, and deleted on the
way out; anything left behind next time is a song the program did not finish
saying goodbye to, and is offered back.

It is not a substitute for saving. It is the difference between losing a
session and losing nothing.
"""
import json
import os
import time

from app import project
from app import settings

NOTES = 'recovery.vws'
MARK = 'recovery.json'


def _path(name):
    return os.path.join(settings.folder(), name)


def write(bpm, tracks, sig, consonants, voice, reverb, anticipate, path=None):
    """Keep a copy of the song. Quiet about failure: a recovery file that
    cannot be written is not a reason to interrupt anyone."""
    try:
        os.makedirs(settings.folder(), exist_ok=True)
        project.save(_path(NOTES), bpm, tracks, sig, consonants, voice,
                     reverb, anticipate)
        with open(_path(MARK), 'w', encoding='utf-8') as fh:
            json.dump({'path': path, 'when': time.time(),
                       'notes': sum(len(t.notes) for t in tracks)}, fh)
            fh.write('\n')
        return True
    except (OSError, ValueError, TypeError):
        return False


def waiting():
    """(the file, what it says about itself) if a song was left behind."""
    try:
        with open(_path(MARK), encoding='utf-8') as fh:
            mark = json.load(fh)
    except (OSError, ValueError):
        return None
    if not os.path.isfile(_path(NOTES)):
        return None
    return _path(NOTES), mark if isinstance(mark, dict) else {}


def clear():
    """Nothing to recover: the program is closing in the ordinary way."""
    for name in (NOTES, MARK):
        try:
            os.remove(_path(name))
        except OSError:
            pass
