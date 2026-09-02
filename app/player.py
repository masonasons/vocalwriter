#!/usr/bin/env python3
"""Playing a WAV file, and knowing whether it is still playing.

That second part is why this exists. `winsound` can start a sound and stop it,
but it cannot say whether one is still going, and without that a single key
cannot both start the song and stop it: after the song had finished, pressing
it again would stop nothing instead of playing. Windows' media control
interface will answer the question. It is spoken to by sending it strings,
which is as odd as it sounds, but it needs nothing installed and nothing
imported.

Anywhere else it falls back to starting and stopping a player process.
"""
import ctypes
import os
import subprocess
import sys

ALIAS = 'vocalwriterstudio'

STOPPED, PLAYING = 'stopped', 'playing'


class Player(object):
    """One sound at a time: play it, stop it, ask what it is doing."""

    def __init__(self):
        self.path = None
        self._proc = None
        self._opened = False

    # -- the Windows half --------------------------------------------------

    @staticmethod
    def _mci(command):
        """Send one command; returns (error code, reply)."""
        buf = ctypes.create_unicode_buffer(256)
        err = ctypes.windll.winmm.mciSendStringW(command, buf, 254, None)
        return err, buf.value

    def _close(self):
        if self._opened:
            self._mci('close %s' % ALIAS)
            self._opened = False

    def _open(self, path):
        self._close()
        # the quotes matter: a path with a space in it is otherwise read as
        # several arguments
        err, _ = self._mci('open "%s" type waveaudio alias %s' % (path, ALIAS))
        self._opened = not err
        return self._opened

    # -- what the window calls --------------------------------------------

    def play(self, path):
        """Start `path` from the beginning. True if it is playing."""
        self.path = path
        if not os.path.isfile(path):
            return False
        if sys.platform == 'win32':
            if not self._open(path):
                return False
            err, _ = self._mci('play %s from 0' % ALIAS)
            return not err
        self.stop()
        if sys.platform == 'darwin':
            self._proc = subprocess.Popen(['afplay', path])
            return True
        return False

    def stop(self):
        if sys.platform == 'win32':
            if self._opened:
                self._mci('stop %s' % ALIAS)
                self._close()
            return
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def state(self):
        """Whether a sound is playing, so one key can start and stop it.

        A file that has finished is closed here rather than left open. Windows
        will not let a file be written while it is open, so holding on to the
        last one played meant the next render could not overwrite it -- the
        song would not play again, and would not say why.
        """
        if sys.platform == 'win32':
            if not self._opened:
                return STOPPED
            err, mode = self._mci('status %s mode' % ALIAS)
            if err or mode != 'playing':
                self._close()
                return STOPPED
            return PLAYING
        if self._proc is not None and self._proc.poll() is None:
            return PLAYING
        return STOPPED
