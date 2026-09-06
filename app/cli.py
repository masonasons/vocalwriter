#!/usr/bin/env python3
"""VocalWriter Studio without the window: a song in, a WAV out.

    vocalwriter song.vws -o song.wav
    vocalwriter tune.mid -o tune.wav --voice Strings --tempo 96
    vocalwriter song.vws --tracks stems
    vocalwriter --list-voices

It is the same program the editor is. The song is built by `app.project`,
which is what the window builds its songs with too, and it is sung by the same
engine, so a file rendered here and the same file exported from the window are
the same audio to the sample.

A MIDI file can be given wherever a project can. It is imported exactly as
File > Import MIDI imports it, words looked up in VocalWriter's own dictionary
and all, so a batch of MIDI can be turned into singing without opening
anything.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import project                                      # noqa: E402
from app import version                                      # noqa: E402
from ppc import paths                                        # noqa: E402
from ppc.engine import Engine, engine_name                   # noqa: E402

NAME = 'vocalwriter'

EPILOG = """examples:
  vocalwriter song.vws -o song.wav          render a song
  vocalwriter tune.mid -o tune.wav          import a MIDI file and render it
  vocalwriter song.vws --tracks stems       one WAV per track, into stems
  vocalwriter tune.mid --save tune.vws      import, save, open it later
  vocalwriter song.vws -o q.wav --tempo 90 --voice Strings
  vocalwriter --list-voices                 every voice in the bank
  vocalwriter --pronounce daisy bicycle     what the dictionary says

With a file and none of --output, --tracks or --save, the editor opens with
that song in it. With no arguments at all, the editor opens empty."""


def build_parser():
    p = argparse.ArgumentParser(
        prog=NAME, add_help=True, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Sing a VocalWriter Studio project or a MIDI file.')
    p.add_argument('file', nargs='?', metavar='FILE',
                   help='a project (.vws) or a MIDI file to sing')

    out = p.add_argument_group('what to write')
    out.add_argument('-o', '--output', metavar='FILE',
                     help='render the whole song to this WAV file')
    out.add_argument('--tracks', metavar='FOLDER',
                     help='render every track to its own WAV in this folder')
    out.add_argument('--save', metavar='FILE',
                     help='write the song as a project (.vws), which is how a '
                          'MIDI file becomes one')

    how = p.add_argument_group('how to sing it',
                               'each of these overrides what the file says')
    how.add_argument('--track', metavar='NAME', action='append', default=[],
                     help='sing only this track, by name or by number; may be '
                          'given more than once')
    how.add_argument('--tempo', type=float, metavar='BPM')
    how.add_argument('--voice', metavar='NAME',
                     help='sing every track with this voice, by name or by '
                          'number in the bank (--list-voices says which)')
    how.add_argument('--consonants', type=float, metavar='PERCENT',
                     help='consonant length, 10 to 100')
    how.add_argument('--reverb', metavar='ROOM,WET',
                     help='room size and amount, each 0 to 100, as in 40,24')
    how.add_argument('--anticipate', dest='anticipate', action='store_true',
                     default=None,
                     help="sing a note's consonants before its beat, so the "
                          'vowel lands on it')
    how.add_argument('--no-anticipate', dest='anticipate',
                     action='store_false',
                     help='sing every note squarely on its beat')
    how.add_argument('--from', dest='start', type=float, default=0.0,
                     metavar='BEATS', help='start this many beats in')

    tell = p.add_argument_group('what it can tell you')
    tell.add_argument('--list-voices', action='store_true',
                      help='every voice in the bank, numbered')
    tell.add_argument('--list-tracks', action='store_true',
                      help='the tracks of FILE, numbered')
    tell.add_argument('--pronounce', nargs='+', metavar='WORD',
                      help="what VocalWriter's dictionary says a word is")
    tell.add_argument('--version', action='store_true',
                      help='which build this is, and which engine')
    p.add_argument('-q', '--quiet', action='store_true',
                   help='say nothing but what was asked for')
    return p


def wants_console(args):
    """Whether these arguments are a job to do rather than a window to open."""
    return bool(args.output or args.tracks or args.save or args.list_voices
                or args.list_tracks or args.pronounce or args.version)


# -- saying things ---------------------------------------------------------

def attach_console():
    """Windows: a windowed program starts with no console to print into.

    The editor is built windowed -- it is not a terminal program -- so running
    it with arguments would otherwise print into nothing at all. Borrowing the
    console of whatever started it is what makes "VocalWriterStudio.exe
    --help" answer. The shell does not wait for a windowed program, so the
    prompt comes back before the output does; vocalwriter.exe, built beside it
    as a console program, is the one to use in a script.

    True if there is somewhere to print. A console program, and anything run
    from source, has one already.
    """
    if os.name != 'nt' or not paths.frozen():
        return True
    try:
        if sys.stdout is not None and sys.stdout.fileno() >= 0:
            return True                 # vocalwriter.exe: built with one
    except Exception:                                        # noqa: BLE001
        pass                # a stream that cannot say is not a console
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):     # the parent's
            return False
        for stream in ('stdout', 'stderr'):
            try:
                setattr(sys, stream, open('CONOUT$', 'w', encoding='utf-8',
                                          errors='replace', buffering=1))
            except OSError:
                pass
        return True
    except Exception:                                        # noqa: BLE001
        return False           # printing is not worth failing to start over


class Say(object):
    """Progress on the error stream, answers on the output stream.

    Which matters for "vocalwriter --pronounce daisy > words.txt": the file
    gets the answer and the terminal gets the commentary.
    """

    def __init__(self, quiet=False):
        self.quiet = quiet

    def __call__(self, text):
        if not self.quiet:
            print(text, file=sys.stderr)

    @staticmethod
    def out(text):
        print(text)


def fail(message):
    print('%s: %s' % (NAME, message), file=sys.stderr)
    return 1


# -- reading what was asked -------------------------------------------------

def parse_reverb(text):
    parts = [p for p in text.replace(',', ' ').split() if p]
    if len(parts) != 2:
        raise ValueError('reverb wants a room size and an amount, as 40,24')
    try:
        room, wet = (int(round(float(p))) for p in parts)
    except ValueError:
        raise ValueError('reverb wants two numbers, as 40,24')
    for v in (room, wet):
        if not 0 <= v <= 100:
            raise ValueError('reverb runs from 0 to 100')
    return (room, wet)


def find_voice(name, names):
    """Which voice of the bank that is: a number, or a name spelled out.

    A name is matched however it is capitalised, in full first and then as the
    beginning of one, so "--voice trum" is Trumpet while "--voice Robert" is
    Robert rather than an ambiguity with Robert 2.
    """
    text = (name or '').strip()
    if text.isdigit():
        i = int(text)
        if not 0 <= i < len(names):
            raise ValueError('there are %d voices, numbered 0 to %d'
                             % (len(names), len(names) - 1))
        return i
    exact = [i for i, n in enumerate(names) if n.lower() == text.lower()]
    if exact:
        return exact[0]
    near = [i for i, n in enumerate(names)
            if n.lower().startswith(text.lower())]
    if len(near) == 1:
        return near[0]
    if near:
        raise ValueError('%r could be %s'
                         % (name, ', '.join(names[i] for i in near)))
    raise ValueError('no voice called %r; --list-voices says what there is'
                     % name)


def chosen_tracks(tracks, wanted):
    """The tracks named on the command line, in the song's own order."""
    if not wanted:
        return list(tracks)
    keep, unknown = [], []
    for want in wanted:
        text = want.strip()
        if text.isdigit() and 1 <= int(text) <= len(tracks):
            keep.append(tracks[int(text) - 1])
            continue
        match = [t for t in tracks if t.name.lower() == text.lower()]
        if not match:
            match = [t for t in tracks
                     if t.name.lower().startswith(text.lower())]
        if not match:
            unknown.append(want)
        keep.extend(match)
    if unknown:
        raise ValueError('no track called %s'
                         % ', '.join(repr(u) for u in unknown))
    return [t for t in tracks if t in keep]


# -- opening a song ---------------------------------------------------------

def is_midi(path):
    return os.path.splitext(path)[1].lower() in ('.mid', '.midi')


def open_song(path, eng, say):
    """A project or a MIDI file, as (bpm, tracks, signature, consonants,
    voice, reverb, anticipate).

    A MIDI file goes through the same import the window uses, words and all.
    """
    if not is_midi(path):
        bpm, docs, sig, consonants, voice, reverb, early = project.load(path)
        return (bpm, project.tracks_from(docs), sig, consonants, voice,
                reverb, early)

    names = [n for n, _count in project.midi_tracks(path)]
    if not names:
        raise ValueError('%s has no notes in it' % os.path.basename(path))
    bpm, sig, got = project.from_midi_tracks(path, names)
    pending = [(k, word, indices)
               for k, (_n, _rows, pend) in enumerate(got)
               for word, indices in pend]
    rows = [part for _n, part, _p in got]
    if pending:
        words = sorted({w for _k, w, _ix in pending})
        say('looking up %d word%s' % (len(words),
                                      '' if len(words) == 1 else 's'))
        rows, done = project.pronounce(rows, pending, eng.phonemes(words))
        short = len(pending) - done
        if short:
            say('%d word%s not in the dictionary; those notes sing %s'
                % (short, '' if short == 1 else 's', project.DEFAULT_PHONEME))
    docs = [{'name': name, 'program': 0, 'volume': 100, 'pan': 0,
             'mute': False, 'solo': False, 'rows': rows[k]}
            for k, (name, _rows, _p) in enumerate(got)]
    say('imported %d track%s, %d notes, %g bpm'
        % (len(docs), '' if len(docs) == 1 else 's',
           sum(len(r) for r in rows), round(bpm)))
    return (bpm, project.tracks_from(docs), sig, 1.0, None, (0, 0), True)


# -- the jobs ---------------------------------------------------------------

def render_to(eng, song, out, say):
    """One render, and what to say about how it came out."""
    res = eng.render(song, out)
    if not res:
        say('nothing was written to %s' % out)
        return False
    say('wrote %s, %.2f seconds, peak %.2f%s'
        % (res.get('path', out), res.get('seconds', 0), res.get('peak', 0),
           ' (from the cache)' if res.get('cached') else ''))
    if res.get('peak', 0) >= 1.0:
        say('it is at full scale and may be clipping: turn a track down, or '
            'turn the reverb down')
    if res.get('stopped_short'):
        say('the song was cut short: a phrase with no rest in it can only '
            'run so long')
    return True


def program_map_of(eng):
    """Which voice each old program number picks, for a song that names one."""
    return {p: v for p, v in enumerate(eng.program_voices(range(128)))
            if v is not None}


def run(argv=None, args=None):
    """Do what the arguments ask for. `args` is a parse already done, which
    is how the launcher avoids reading the command line twice."""
    if args is None:
        args = build_parser().parse_args(argv)
    say = Say(args.quiet)

    if args.version:
        Say.out('VocalWriter Studio %s' % version.describe())
        Say.out('engine: %s' % engine_name())
        Say.out('python: %s'
                % '.'.join(str(v) for v in sys.version_info[:3]))
        return 0

    absent = paths.missing()
    if absent:
        return fail("VocalWriter 2.0's own files are not here. Looked in %s, "
                    'and these are missing: %s'
                    % (paths.data_root(), ', '.join(absent)))

    if args.file and not os.path.isfile(args.file):
        return fail('there is no file called %s' % args.file)
    if not args.file and (args.output or args.tracks or args.save
                          or args.list_tracks):
        return fail('which file? Give a project or a MIDI file to work on')

    eng = Engine()

    if args.pronounce:
        for word, phones in sorted(eng.phonemes(args.pronounce).items()):
            Say.out('%s  %s' % (word, ' '.join(phones) if phones
                                else '(not in the dictionary)'))

    if args.list_voices:
        names = eng.voices()
        for i, name in enumerate(names):
            Say.out('%3d  %s' % (i, name))
        say('%d voices' % len(names))

    if not args.file:
        return 0

    try:
        bpm, tracks, sig, consonants, voice, reverb, early = open_song(
            args.file, eng, say)
    except (OSError, ValueError) as exc:
        return fail('cannot open %s: %s' % (args.file, exc))

    if args.list_tracks:
        program_map = program_map_of(eng)
        names = eng.voices()
        for i, t in enumerate(tracks, 1):
            pick = project.track_voice(t, program_map)
            Say.out('%2d  %-24s %-16s %3d%%  %s%s'
                    % (i, t.name,
                       names[pick] if pick < len(names) else 'voice %d' % pick,
                       t.volume, project.pan_text(t.pan),
                       '  muted' if t.mute else ''))
        say('%d track%s, %d notes, %g bpm, %s'
            % (len(tracks), '' if len(tracks) == 1 else 's',
               sum(len(t.notes) for t in tracks), round(bpm),
               project.format_sig(sig)))
        if not (args.output or args.tracks or args.save):
            return 0

    # what the command line says about the song, over what the file says
    if args.tempo is not None:
        bpm = args.tempo
    if args.consonants is not None:
        consonants = max(10.0, min(100.0, args.consonants)) / 100.0
    if args.reverb is not None:
        try:
            reverb = parse_reverb(args.reverb)
        except ValueError as exc:
            return fail(str(exc))
    if args.anticipate is not None:
        early = args.anticipate
    if args.voice is not None:
        try:
            pick = find_voice(args.voice, eng.voices())
        except ValueError as exc:
            return fail(str(exc))
        for t in tracks:
            t.voice_id = pick
        say('every track is singing in %s' % eng.voices()[pick])

    try:
        wanted = chosen_tracks(tracks, args.track)
    except ValueError as exc:
        return fail(str(exc))

    if args.save:
        try:
            project.save(args.save, bpm, wanted, sig, consonants, voice,
                         reverb, early)
        except OSError as exc:
            return fail('could not write %s: %s' % (args.save, exc))
        say('saved %s: %d track%s, %d notes'
            % (args.save, len(wanted), '' if len(wanted) == 1 else 's',
               sum(len(t.notes) for t in wanted)))

    if not (args.output or args.tracks):
        return 0

    program_map = program_map_of(eng)

    def song_for(parts):
        return project.song_dict(bpm, parts, consonants=consonants,
                                 voice=voice, reverb=reverb, anticipate=early,
                                 start=args.start, program_map=program_map)

    ok = True
    if args.output:
        heard = [t for t in project.audible(wanted) if t.notes]
        if not heard:
            return fail('nothing to render: no track that can be heard has '
                        'any notes in it')
        say('rendering %d track%s' % (len(heard),
                                      '' if len(heard) == 1 else 's'))
        ok = render_to(eng, song_for(heard), args.output, say) and ok

    if args.tracks:
        try:
            os.makedirs(args.tracks, exist_ok=True)
        except OSError as exc:
            return fail('cannot write into %s: %s' % (args.tracks, exc))
        base = os.path.splitext(os.path.basename(args.file))[0]
        jobs = project.export_jobs(wanted, args.tracks, base)
        if not jobs:
            return fail('nothing to render: no track has any notes in it')
        say('rendering %d track%s into %s'
            % (len(jobs), '' if len(jobs) == 1 else 's', args.tracks))
        for i, (t, out) in enumerate(jobs, 1):
            say('%d of %d: %s' % (i, len(jobs), t.name))
            ok = render_to(eng, song_for([t]), out, say) and ok

    return 0 if ok else 1


def main(argv=None):
    attach_console()
    try:
        return run(argv)
    except KeyboardInterrupt:
        return fail('stopped')


if __name__ == '__main__':
    sys.exit(main())
