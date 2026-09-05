"""Bounded, independent snapshots of document edits."""
from copy import deepcopy
from functools import wraps


class History:
    def __init__(self, saved=None, limit=100):
        self.undo = []
        self.redo = []
        self.saved = deepcopy(saved)
        self.limit = limit

    def record(self, before, after, label):
        if before['document'] == after['document']:
            return False
        self.undo.append(deepcopy((before, after, label)))
        del self.undo[:-self.limit]
        self.redo.clear()
        return True

    def step(self, redo=False):
        source, target = (self.redo, self.undo) if redo else (self.undo, self.redo)
        if not source:
            return None
        entry = source.pop()
        target.append(entry)
        return deepcopy(entry[1 if redo else 0]), entry[2]


def undoable(label):
    def decorate(method):
        @wraps(method)
        def edit(self, *args, **kwargs):
            if not hasattr(self, '_history'):
                return method(self, *args, **kwargs)
            before = self._snapshot()
            result = method(self, *args, **kwargs)
            after = self._snapshot()
            changed = self._history.record(before, after, label)
            if changed or self.dirty != (after['document'] != self._history.saved):
                self._history_status()
            return result
        return edit
    return decorate
