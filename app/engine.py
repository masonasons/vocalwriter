#!/usr/bin/env python3
"""Talk to the engine process from the interface.

Requests go out on a worker thread and come back through a callback, so a
render -- tens of millions of guest instructions -- never blocks the window.
The engine is started under PyPy when it can be found, because it runs the
interpreter about two and a half times faster, and wxPython cannot itself run
under PyPy.
"""
import json
import os
import queue
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: Homebrew installs PyPy as pypy3.11; scoop installs it as pypy.
PYPY_NAMES = ('pypy3.11', 'pypy3.10', 'pypy3', 'pypy')

#: searched as well as PATH, which a GUI launched from Finder may not inherit
EXTRA_DIRS = ('/opt/homebrew/bin', '/usr/local/bin')


def have_compiled_core():
    try:
        from ppc import fastcpu
        return fastcpu.AVAILABLE
    except Exception:
        return False


def find_interpreter():
    """The interpreter to run the engine in.

    With the compiled core there is nothing to gain from a second runtime --
    CPython drives it faster than PyPy did -- so the interpreter already
    running is used, which is also what makes a frozen build work: there, the
    executable re-runs itself in engine mode. PyPy is only sought when the core
    has not been built, where it is still worth about two and a half times.
    """
    if getattr(sys, 'frozen', False) or have_compiled_core():
        return sys.executable, False
    ext = '.exe' if os.name == 'nt' else ''
    dirs = os.environ.get('PATH', '').split(os.pathsep) + list(EXTRA_DIRS)
    for name in PYPY_NAMES:
        for d in dirs:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p, True
    return sys.executable, False


class Engine(object):
    def __init__(self, on_error=None):
        self.exe, self.is_pypy = find_interpreter()
        self.on_error = on_error
        self._next = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._q = queue.Queue()
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONUNBUFFERED='1')
        # A frozen build has no separate Python to invoke, so the executable
        # runs itself with a flag that its entry point turns into the engine.
        cmd = ([self.exe, '--engine'] if getattr(sys, 'frozen', False)
               else [self.exe, '-m', 'ppc.server'])
        self.proc = subprocess.Popen(
            cmd, cwd=ROOT if os.path.isdir(ROOT) else None, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._write, daemon=True).start()

    # -- plumbing ----------------------------------------------------------

    def _write(self):
        while True:
            line = self._q.get()
            if line is None:
                break
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                break

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                cb = self._pending.pop(msg.get('id'), None)
            if msg.get('ok'):
                if cb:
                    cb(msg.get('result'))
                continue
            # A failed request still has to come back. Dropping the callback
            # here left whatever asked for it waiting for an answer that would
            # never arrive: one failed render and the window said "already
            # rendering" to everything afterwards, for good.
            if self.on_error:
                self.on_error(msg.get('error', 'engine error'))
            if cb:
                cb(None)

    def send(self, op, callback=None, **kw):
        with self._lock:
            self._next += 1
            rid = self._next
            if callback:
                self._pending[rid] = callback
        self._q.put(json.dumps(dict(kw, op=op, id=rid)) + '\n')
        return rid

    # -- operations --------------------------------------------------------

    def ping(self, cb):
        self.send('ping', cb)

    def phonemes(self, words, cb):
        self.send('phonemes', cb, words=list(words))

    def voices(self, cb):
        self.send('voices', cb)

    def render(self, song, out, cb):
        self.send('render', cb, song=song, out=out)

    def close(self):
        try:
            self.send('quit')
            self._q.put(None)
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
