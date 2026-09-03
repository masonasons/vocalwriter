#!/usr/bin/env python3
"""The engine, in this process, answering on a worker thread.

There used to be a second process here, spoken to in JSON lines. That was not
architecture for its own sake: a render under the PowerPC interpreter ran tens
of millions of guest instructions and took roughly as long as the music lasts,
and nothing that slow can happen on the thread that draws the window. The C
engine renders a minute of singing in a few hundredths of a second, so the
process boundary bought nothing and cost a serialisation of every request.

The thread stays. A render is fast, not instant -- a long song is still a
fraction of a second, and a fraction of a second of a frozen window is the
difference between an interface that answers and one that stutters. Requests
go on a queue, they are answered in the order they were asked, and the answer
arrives in a callback; whoever asked hands it back to the window with
`wx.CallAfter`, as it always did.
"""
import os
import queue
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ppc.engine import Engine as Synthesiser                 # noqa: E402


class Engine(object):
    """What the window asks; the same surface the engine process had."""

    def __init__(self, on_error=None):
        self.on_error = on_error
        self._q = queue.Queue()
        self._eng = None
        self._lock = threading.Lock()
        self._closing = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # -- plumbing ----------------------------------------------------------

    @property
    def engine(self):
        """Built on the worker thread, the first time something is asked.

        Opening it reads the voice bank and the tables, which is quick but not
        free, and doing it here means the window is already up and saying so
        rather than waiting on it.
        """
        if self._eng is None:
            self._eng = Synthesiser()
        return self._eng

    def _run(self):
        while True:
            job = self._q.get()
            if job is None:
                break
            op, kw, callback = job
            try:
                result = getattr(self.engine, op)(**kw)
            except Exception as exc:                  # noqa: BLE001
                # A failed request still has to come back. Dropping the
                # callback left whatever asked for it waiting for an answer
                # that would never arrive: one failed render and the window
                # said "already rendering" to everything afterwards, for good.
                if self.on_error:
                    self.on_error('%s: %s' % (type(exc).__name__, exc))
                if callback:
                    callback(None)
                continue
            if callback:
                callback(result)

    def send(self, op, callback=None, **kw):
        self._q.put((op, kw, callback))

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
        if self._closing:
            return
        self._closing = True
        self._q.put(None)
        self._thread.join(timeout=5)
