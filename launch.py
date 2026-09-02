#!/usr/bin/env python3
"""Entry point for the packaged application.

One executable does both jobs: with --engine it is the synthesis process, and
without it, the window. See app/studio.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.studio import main                                  # noqa: E402

if __name__ == '__main__':
    main()
