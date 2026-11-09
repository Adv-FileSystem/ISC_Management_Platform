"""Pure-Python decoder for one raw Snappy block (format: varint length, then literal/copy tags).

Written from the format description (github.com/google/snappy/blob/main/format_description.txt), independent of the
FPGA kernel. Used to check device output; no compressor here.
"""
from __future__ import annotations


def decompress(block: bytes) -> bytes:
    pos, shift, length = 0, 0, 0
    while True:                                   # uncompressed length, little-endian varint
        b = block[pos]; pos += 1
        length |= (b & 0x7F) << shift
        if b < 0x80:
            break
        shift += 7
    out = bytearray()
    while pos < len(block):
        tag = block[pos]; pos += 1
        kind = tag & 3
        if kind == 0:                             # literal
            n = tag >> 2
            if n >= 60:
                extra = n - 59
                n = int.from_bytes(block[pos:pos + extra], "little"); pos += extra
            n += 1
            out += block[pos:pos + n]; pos += n
            continue
        if kind == 1:                             # copy, 1-byte offset
            n = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | block[pos]; pos += 1
        elif kind == 2:                           # copy, 2-byte offset
            n = (tag >> 2) + 1
            offset = int.from_bytes(block[pos:pos + 2], "little"); pos += 2
        else:                                     # copy, 4-byte offset
            n = (tag >> 2) + 1
            offset = int.from_bytes(block[pos:pos + 4], "little"); pos += 4
        if offset == 0 or offset > len(out):
            raise ValueError(f"snappy: bad copy offset {offset} at output {len(out)}")
        for _ in range(n):                        # byte by byte: copies may overlap themselves
            out.append(out[-offset])
    if len(out) != length:
        raise ValueError(f"snappy: preamble says {length} bytes, decoded {len(out)}")
    return bytes(out)


def decompress_framed(data: bytes) -> bytes:
    """Snappy framing format as written by the official Vitis host (chunk 0xff id, 0x00 compressed, 0x01 stored)."""
    pos, out = 0, bytearray()
    while pos < len(data):
        kind = data[pos]; size = int.from_bytes(data[pos + 1:pos + 4], "little"); pos += 4
        body = data[pos:pos + size]; pos += size
        if kind == 0x00:
            out += decompress(body[4:])
        elif kind == 0x01:
            out += body[4:]
    return bytes(out)
