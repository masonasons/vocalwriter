#!/usr/bin/env python3
"""Which build this is, so that a bug report can name one.

A build carries a stamp written when it was packaged; a source checkout asks
git. Either way it comes back as something short enough to read out and exact
enough to check out again.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppc import paths                                        # noqa: E402

STAMP = 'build.txt'


def _stamped():
    """What the packager wrote, if this is a build."""
    try:
        with open(paths.bundled(STAMP), encoding='utf-8') as fh:
            return fh.read().strip()
    except OSError:
        return ''


def _from_git():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(
            ['git', '-C', root, 'log', '-1', '--format=%h %cd',
             '--date=format:%Y-%m-%d'],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


def describe():
    """The build, as something to read out in a bug report."""
    return _stamped() or _from_git() or 'unknown'


if __name__ == '__main__':
    print(describe())
