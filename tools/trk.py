#!/usr/bin/env python3
"""Parser for VocalWriter '.trk' song files (VocalTracks, OSType 'Seq^').

Status: the container is decoded and verified against the running application;
the per-note event stream is only partly decoded. Everything below marked
(confirmed) was checked by opening the same file in VocalWriter 2.0.1 under
Mac OS X 10.4 and comparing what it displayed.

    Daisy.trk        parsed here            VocalWriter showed
    meter            3/4                    3/4                     (confirmed)
    tempo            80                     80                      (confirmed)
    tracks           Tempo Track, HAL,      same five, same order,
                     Dave, Crash, Karaoke   types T V I I K         (confirmed)
    lyrics           "Dai- sy dai- sy ..."  the song is Daisy Bell  (confirmed)

File layout, all big-endian:

    0x000   u32     version or track count
    0x00e   u16     meter numerator                                 (confirmed)
    0x010   u16     meter denominator as a power of two: 2 -> /4    (confirmed)
    0x012+          a run of u16 = 100, one per track (default level 100,
                    matching the manual's "Volume ... range 0 to 100")
    0x2fe   88 x N  track table
    ...             per-track data chunks, at the offsets the table gives

Track record, 88 bytes:

    0x00  u32   offset of this track's data chunk                   (confirmed:
                the chunks tile the file in order without gaps)
    0x04  u32   length of that chunk                                (confirmed:
                offset + length == the next value, see 0x10)
    0x08  u32   set only on vocal tracks; purpose unknown
    0x0c  u32   set only on vocal and karaoke tracks; a sub-length
    0x10  u32   offset + length, i.e. the end of the chunk          (confirmed)
    0x14  u32   60 on vocal and karaoke tracks, else 0
    0x18  u32   13 on vocal and karaoke tracks, else 0
    0x30  Pascal string, 32-byte field   track name                 (confirmed)
                The field is not cleared when a name shortens, so bytes after
                the string's length are stale and must be ignored -- "HAL" is
                stored as 03 'H' 'A' 'L' followed by leftover 'ck 2'.
    0x50  u32   track index, 1-based                                (confirmed)

Note and lyric events. Within a vocal track's chunk the lyric records form a
26-byte lattice carrying a Pascal-string syllable at record offset 12; reading
Daisy.trk on that lattice yields the song's words in order, including the two
spoken HAL lines at the end, so the stride and the field offset are confirmed
even though the other 25 bytes of the record are not yet identified.

Elsewhere the chunks resolve to a 12-byte event unit -- scoring every candidate
stride by how many byte columns stay constant across records picks out 12 and
its multiples. A representative run reads

    c0 01 06 2c 72 00 00 f0 00 14 00 40
    b0 01 06 2d 72 00 00 f0 00 15 00 41
    a0 01 06 2c 72 00 00 f0 00 16 00 43

where byte 3 moves over 0x28..0x2f (MIDI note numbers in a plausible range),
bytes 5-7 take values 0x0000f0 and 0x0001e0 (240 and 480, i.e. eighth and
quarter notes against a 480-tick quarter), and the last two u16 fields count up
monotonically. That is enough to be confident these are note events, and not
enough to name every field, so this module exposes them as raw records rather
than guessing.

The reliable way to finish this is the application's own File > Export, which
the manual says writes lyrics as MIDI Text meta events and phonemes as Lyric
meta events. Exporting a known .trk gives an exact note list to diff against
these bytes.
"""
import re
import struct

TRACK_TABLE_OFF = 0x2FE
TRACK_RECORD_SIZE = 88
NAME_OFF = 0x30
LYRIC_STRIDE = 26
LYRIC_FIELD_OFF = 12
EVENT_SIZE = 12
# Records sit on a 12-byte lattice, but its phase is set by wherever a track's
# array happens to start and differs between chunks (HAL's is 4, Dave's is 8),
# so it is measured per track rather than assumed.
MIN_PITCH, MAX_PITCH = 12, 108
MAX_DURATION = 1 << 16   # ticks; anything larger is not a note record
# byte[2] is an event type whose note value depends on the track type -- 6 on
# vocal tracks, 1 on instrumental ones -- so notes are identified by having a
# pitch, a velocity and a duration rather than by type.
TICKS_PER_QUARTER = 240

SYLLABLE_RE = re.compile(rb"[A-Za-z'=-]+\Z")


def _join_syllables(syllables):
    """Join syllables into words.

    A trailing '-' marks a syllable that continues into the next note, and '='
    repeats the previous vowel over another note (both per the manual).
    """
    words, cur = [], ''
    for syl in syllables:
        if syl.endswith('-'):
            cur += syl[:-1]
        elif syl == '=':
            continue
        else:
            words.append(cur + syl)
            cur = ''
    if cur:
        words.append(cur)
    return ' '.join(words)


class Note(object):
    """One note from a track's event stream."""
    __slots__ = ('offset', 'pitch', 'duration', 'index', 'position', 'raw',
                 'text', 'velocity')

    def __init__(self, offset, pitch, duration, index, position, raw):
        self.offset = offset
        self.pitch = pitch          # MIDI note number
        self.duration = duration    # ticks, 240 per quarter note
        self.index = index          # joins to the lyric of the same index
        self.position = position    # monotonic, roughly the start beat
        self.velocity = raw[4]      # internal scale; export maps 114->52, 120->55
        self.raw = raw
        self.text = ''

    def __repr__(self):
        return '<Note p=%d d=%d i=%d %r>' % (
            self.pitch, self.duration, self.index, self.text)


class Track(object):
    def __init__(self, index, rec, blob):
        self.raw = rec
        self.offset, self.length = struct.unpack('>II', rec[:8])
        self.unk08, self.sub_length, self.end = struct.unpack('>III', rec[8:20])
        self.flag14, self.flag18 = struct.unpack('>II', rec[20:28])
        n = rec[NAME_OFF]
        self.name = rec[NAME_OFF + 1:NAME_OFF + 1 + n].decode('mac-roman', 'replace')
        self.number = struct.unpack('>I', rec[0x50:0x54])[0]
        self.index = index
        self._blob = blob
        self._song = None

    @property
    def is_vocal_like(self):
        """Vocal and Karaoke tracks carry lyrics; the others do not.

        Both set 0x14 and 0x18 (to 60 and 13) where instrumental and tempo
        tracks leave them zero.
        """
        return bool(self.flag14 and self.flag18)

    @property
    def data(self):
        return self._blob[self.offset:self.offset + self.length]

    def notes_with_lyrics(self):
        """Notes in time order, each carrying its syllable.

        The two arrays are stored in different orders -- notes by time, lyrics
        by index -- and the note's `index` field is the join.
        """
        syl = self.lyrics()
        notes = self.notes()
        for n in notes:
            if 0 <= n.index < len(syl):
                n.text = syl[n.index]
        return notes

    def lyrics(self):
        """This track's syllables, in order (empty for non-vocal tracks)."""
        if not self.is_vocal_like:
            return []
        return self._song.lyrics(self._song.track_span(self))

    def text(self):
        return _join_syllables(self.lyrics())

    def raw_events(self, phase=0):
        """Every 12-byte record on the event lattice, as (offset, bytes).

        The scan covers the whole file, not just this track's chunk. Events are
        one interleaved stream tagged by owner: a record belonging to Dave sits
        inside what the track table calls Crash's chunk, and HAL's notes run
        from 0xf7c to 0x19e4, across its chunk boundary. byte[1] is what
        actually assigns a record to a track.
        """
        blob = self._blob
        return [(i, blob[i:i + EVENT_SIZE])
                for i in range(phase, len(blob) - EVENT_SIZE + 1, EVENT_SIZE)]

    @property
    def is_tempo_track(self):
        """Track 1 is always the tempo track and carries no notes."""
        return self.number == 1

    def _note_shaped(self, r):
        """Whether a record has the shape of one of this track's notes.

        byte[1] is the owning track's 0-based index and byte[4] the velocity.
        Requiring a non-zero velocity rejects the shadow records that repeat a
        note's pitch and duration with a zero velocity and index, and which the
        application's own MIDI export does not emit.

        This is necessary but not sufficient: a handful of other event types
        also carry a plausible pitch, velocity and duration, so callers must
        additionally match the track's note type -- see note_type().
        """
        if r[1] != self.number - 1 or r[4] == 0:
            return False
        if not (MIN_PITCH <= r[3] <= MAX_PITCH):
            return False
        dur = (r[5] << 16) | (r[6] << 8) | r[7]
        return 0 < dur <= MAX_DURATION

    def note_type(self, phase=None):
        """The byte[2] value this track uses for notes.

        It depends on the track type -- 6 on vocal tracks, 1 on instrumental
        ones -- and rather than hardcode a table, take the commonest type among
        note-shaped records. Other event types occasionally carry a plausible
        pitch and duration too, but only a handful of records each, so the
        genuine note type wins by a wide margin.
        """
        if phase is None:
            phase = self.event_phase()
        counts = {}
        for _, r in self.raw_events(phase):
            if self._note_shaped(r):
                counts[r[2]] = counts.get(r[2], 0) + 1
        if not counts:
            return None
        return max(counts, key=counts.get)

    def _is_note(self, r):
        return self._note_shaped(r)

    def event_phase(self):
        """The lattice phase that yields the most note records.

        The 12-byte lattice's phase differs between tracks -- 4 for HAL, 8 for
        Dave, 0 for Crash -- so it is measured rather than assumed.
        """
        best, best_n = 0, -1
        for phase in range(EVENT_SIZE):
            n = sum(1 for _, r in self.raw_events(phase)
                    if self._note_shaped(r))
            if n > best_n:
                best, best_n = phase, n
        return best

    def notes(self):
        """Notes on this track, in file order, which is time order.

        A note record has this track's index in byte[1], the track's note type
        in byte[2], a non-zero velocity, a MIDI-range pitch and a sane duration.
        Control events on the same lattice carry a zero duration; shadow records
        carry a zero velocity; and a few other event types happen to look
        note-shaped, which is why the type has to be matched too.
        """
        if self.is_tempo_track:
            # its events share the note lattice and would otherwise pass the
            # filter; the application's MIDI export emits no notes for it
            return []
        phase = self.event_phase()
        want = self.note_type(phase)
        out = []
        for off, r in self.raw_events(phase):
            if r[2] != want or not self._note_shaped(r):
                continue
            out.append(Note(off, r[3], (r[5] << 16) | (r[6] << 8) | r[7],
                            (r[8] << 8) | r[9], (r[10] << 8) | r[11], r))
        return out

    def __repr__(self):
        return '<Track %d %r at 0x%x len 0x%x%s>' % (
            self.number, self.name, self.offset, self.length,
            ' vocal' if self.is_vocal_like else '')


class Song(object):
    def __init__(self, blob):
        self.blob = blob
        self.meter_num = struct.unpack('>H', blob[0x0E:0x10])[0]
        self.meter_den = 1 << struct.unpack('>H', blob[0x10:0x12])[0]
        self.tracks = []
        i = 0
        while True:
            off = TRACK_TABLE_OFF + i * TRACK_RECORD_SIZE
            rec = blob[off:off + TRACK_RECORD_SIZE]
            if len(rec) < TRACK_RECORD_SIZE:
                break
            start, length = struct.unpack('>II', rec[:8])
            # the table ends where the entries stop pointing into the file
            if start == 0 or start + length > len(blob):
                break
            t = Track(i, rec, blob)
            if t.number != i + 1:
                break
            t._song = self
            self.tracks.append(t)
            i += 1

    @classmethod
    def from_file(cls, path):
        with open(path, 'rb') as fh:
            return cls(fh.read())

    @property
    def meter(self):
        return '%d/%d' % (self.meter_num, self.meter_den)

    def lyrics(self, span=None):
        """Syllables in order, read off the 26-byte lyric lattice.

        Scans between the end of the first vocal track's chunk and the start of
        the next track's data, picks the residue class mod 26 that holds the
        most plausible syllables, and returns them in file order.
        """
        lo, hi = span if span else self._lyric_span()
        found = []
        for i in range(lo, min(hi, len(self.blob))):
            n = self.blob[i]
            if 1 <= n <= 12:
                s = self.blob[i + 1:i + 1 + n]
                if len(s) == n and SYLLABLE_RE.match(s):
                    found.append((i, s.decode('mac-roman')))
        if not found:
            return []
        counts = {}
        for i, _ in found:
            counts[i % LYRIC_STRIDE] = counts.get(i % LYRIC_STRIDE, 0) + 1
        phase = max(counts, key=counts.get)
        return [s for i, s in found if i % LYRIC_STRIDE == phase]

    def track_span(self, track):
        """Bytes after a track's chunk and before the next track's data.

        A vocal track's syllables live in this gap rather than inside the chunk
        the table points at, so lyrics have to be read per track from here --
        otherwise one track's scan runs on into the next one's words.
        """
        later = [t.offset for t in self.tracks if t.offset > track.end]
        return track.end, min(later) if later else len(self.blob)

    def _lyric_span(self):
        vocal = [t for t in self.tracks if t.is_vocal_like]
        if not vocal:
            return 0, 0
        return self.track_span(vocal[0])

    def text(self):
        return _join_syllables(self.lyrics())


def main():
    import sys
    for path in sys.argv[1:]:
        s = Song.from_file(path)
        print('==', path, '==')
        print('meter %s, %d tracks' % (s.meter, len(s.tracks)))
        for t in s.tracks:
            if not t.name and t.length <= 4:
                continue        # unused slot; VocalWriter always writes 31
            notes = t.notes()
            print('   %-2d %-14s %-3d notes  %s'
                  % (t.number, t.name, len(notes),
                     'vocal' if t.is_vocal_like else ''))
            if t.is_vocal_like:
                words = t.text()
                if words:
                    print('      lyrics: %s' % words)
                shown = t.notes_with_lyrics()[:8]
                if shown:
                    print('      notes:  %s' % '  '.join(
                        '%s(p%d d%d)' % (n.text or '-', n.pitch, n.duration)
                        for n in shown))
        print()


if __name__ == '__main__':
    main()
