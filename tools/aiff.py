#!/usr/bin/env python3
"""Minimal AIFF reader for VocalWriter's Play-to-Disk output.

Python's `aifc` module was removed in 3.13, and the format is small enough to
read directly. VocalWriter always writes 44.1 kHz 16-bit stereo.

The only fiddly part is the sample rate, which AIFF stores as an 80-bit IEEE
extended float -- a 15-bit biased exponent, a 1-bit sign and an explicit 64-bit
mantissa (unlike IEEE single/double, the leading bit is not implied).
"""
import struct

import numpy as np


def _extended_to_float(b):
    exponent = struct.unpack('>H', b[:2])[0]
    mantissa = struct.unpack('>Q', b[2:10])[0]
    sign = -1.0 if exponent & 0x8000 else 1.0
    exponent &= 0x7FFF
    if exponent == 0 and mantissa == 0:
        return 0.0
    return sign * mantissa * 2.0 ** (exponent - 16383 - 63)


def read(path):
    """Return (samples, sample_rate).

    samples is float32 in -1..1, shaped (frames,) for mono or (frames, ch).
    """
    with open(path, 'rb') as fh:
        blob = fh.read()
    if blob[:4] != b'FORM' or blob[8:12] not in (b'AIFF', b'AIFC'):
        raise ValueError('not an AIFF file: %s' % path)

    channels = bits = frames = None
    rate = 0.0
    data = None
    pos = 12
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack('>I', blob[pos + 4:pos + 8])[0]
        body = blob[pos + 8:pos + 8 + size]
        if cid == b'COMM':
            channels, frames, bits = struct.unpack('>hIh', body[:8])
            rate = _extended_to_float(body[8:18])
        elif cid == b'SSND':
            offset = struct.unpack('>I', body[:4])[0]
            data = body[8 + offset:]
        pos += 8 + size + (size & 1)      # chunks are word-aligned

    if data is None or channels is None:
        raise ValueError('AIFF missing COMM or SSND: %s' % path)
    if bits != 16:
        raise ValueError('only 16-bit AIFF is supported, got %d' % bits)

    want = frames * channels * 2
    pcm = np.frombuffer(data[:want], dtype='>i2').astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    return pcm, rate


def read_mono(path):
    y, sr = read(path)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return y, sr


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        y, sr = read(p)
        print('%s: %s @ %.0f Hz, %.2f s, peak %.3f'
              % (p, 'x'.join(str(d) for d in y.shape), sr,
                 y.shape[0] / sr, float(abs(y).max())))
