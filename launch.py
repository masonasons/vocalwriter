#!/usr/bin/env python3
"""Entry point for the packaged application.

With no arguments, or with a song to open, this is the editor: see
app/studio.py. With anything else on the command line it is the command line
program instead -- rendering, importing, listing -- which is app/cli.py.

Both executables in a Windows build run this file. VocalWriterStudio is
windowed and vocalwriter is a console program, so that a script can wait for a
render and read what it said; which one was started makes no difference to
what the arguments mean.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import cli                                          # noqa: E402


def parse(heard):
    """The command line, and something to show for it when it is wrong.

    A windowed program with no console prints into nothing, so a mistyped
    option would otherwise be a program that simply does not start -- the
    worst thing it could do. When there is nowhere to print, whatever argparse
    would have said is caught and put in a dialog instead, which is a thing
    that can be read.
    """
    if heard:
        return cli.build_parser().parse_args()
    said, out, err = io.StringIO(), sys.stdout, sys.stderr
    sys.stdout = sys.stderr = said
    try:
        return cli.build_parser().parse_args()
    except SystemExit:
        import wx
        if not wx.GetApp():         # a dialog needs an application to sit in
            wx.App(False)
        wx.MessageBox(said.getvalue().strip() or 'that is not something '
                      'VocalWriter Studio can be asked for',
                      'VocalWriter Studio', wx.OK | wx.ICON_INFORMATION)
        raise
    finally:
        sys.stdout, sys.stderr = out, err


def main():
    # before anything can be printed or complained about, since a windowed
    # program on Windows has no console of its own to print into
    heard = cli.attach_console()
    args = parse(heard)
    if cli.wants_console(args):
        sys.exit(cli.run(args=args))
    from app.studio import main as editor
    editor(args.file)


if __name__ == '__main__':
    main()
