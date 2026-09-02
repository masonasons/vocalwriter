#!/usr/bin/env python3
"""Reader for classic Mac OS resource forks.

VocalWriter keeps most of its data in resource forks rather than data forks:
GMBank and GMSpeech both have a zero-length data fork, so a naive copy off the
disk image silently produces empty files. On a non-HFS host the forks have to be
carried as sidecars -- assets/<name>.rsrc here.

Layout (Inside Macintosh: More Macintosh Toolbox, "Resource Manager"):

    header   16 bytes   data offset, map offset, data length, map length
    data     at dataOff each resource is a big-endian u32 length then its bytes
    map      at mapOff  a type list and, per type, a reference list; names live
                        in a separate name list, as Pascal strings

All integers are big-endian.
"""
import struct
from collections import OrderedDict


class Resource(object):
    __slots__ = ('type', 'id', 'name', 'attrs', 'data')

    def __init__(self, type_, id_, name, attrs, data):
        self.type = type_
        self.id = id_
        self.name = name
        self.attrs = attrs
        self.data = data

    def __repr__(self):
        return "<Resource %r id=%d name=%r %d bytes>" % (
            self.type, self.id, self.name, len(self.data))


class ResourceFork(object):
    def __init__(self, blob):
        self.blob = blob
        self.types = OrderedDict()
        self._parse()

    @classmethod
    def from_file(cls, path):
        with open(path, 'rb') as fh:
            return cls(fh.read())

    def _parse(self):
        b = self.blob
        if len(b) < 16:
            raise ValueError('too short to be a resource fork')
        data_off, map_off, data_len, map_len = struct.unpack('>4I', b[:16])
        if map_off + map_len > len(b) or data_off + data_len > len(b):
            raise ValueError('resource fork header points outside the file')

        m = b[map_off:map_off + map_len]
        type_list_off, name_list_off = struct.unpack('>HH', m[24:28])
        tl = m[type_list_off:]
        # both counts are stored as "one less than the real count"
        n_types = struct.unpack('>H', tl[:2])[0] + 1

        for i in range(n_types):
            entry = tl[2 + i * 8:10 + i * 8]
            type_, count, ref_off = struct.unpack('>4sHH', entry)
            count += 1
            type_ = type_.decode('mac-roman')
            out = []
            for j in range(count):
                ref = tl[ref_off + j * 12:ref_off + j * 12 + 12]
                res_id, name_off, packed = struct.unpack('>hHI', ref[:8])
                attrs, data_pos = packed >> 24, packed & 0x00FFFFFF

                name = ''
                if name_off != 0xFFFF:
                    p = map_off + name_list_off + name_off
                    name = b[p + 1:p + 1 + b[p]].decode('mac-roman', 'replace')

                p = data_off + data_pos
                size = struct.unpack('>I', b[p:p + 4])[0]
                out.append(Resource(type_, res_id, name, attrs,
                                    b[p + 4:p + 4 + size]))
            self.types[type_] = out

    def get(self, type_, id_=None):
        """Return one resource, by type and optionally id (default: the first)."""
        items = self.types.get(type_, [])
        if not items:
            raise KeyError('no %r resources' % (type_,))
        if id_ is None:
            return items[0]
        for r in items:
            if r.id == id_:
                return r
        raise KeyError('no %r resource with id %d' % (type_, id_))

    def summary(self):
        lines = []
        for t, items in sorted(self.types.items()):
            total = sum(len(r.data) for r in items)
            lines.append('%-6s x%-4d %8d bytes' % (repr(t), len(items), total))
        return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    for path in sys.argv[1:]:
        rf = ResourceFork.from_file(path)
        print('==', path, '==')
        print(rf.summary())
