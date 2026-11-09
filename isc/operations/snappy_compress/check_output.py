"""Decode an snappy_compress output with the independent pure-Python decoder and compare with the input.

    python3 isc/operations/snappy_compress/check_output.py <input> <output>
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snappy_raw  # noqa: E402

original, framed = Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes()
pos, out, blocks = 0, bytearray(), 0
while pos < len(framed):
    raw, stored, flags = struct.unpack_from("<III", framed, pos); pos += 12
    payload = framed[pos:pos + stored]; pos += stored
    out += payload if flags == 1 else snappy_raw.decompress(payload)
    blocks += 1
ok = bytes(out) == original
print(f"blocks={blocks} original={len(original)} compressed={len(framed)} ratio={len(original) / len(framed):.2f}")
print("ROUNDTRIP OK" if ok else "ROUNDTRIP MISMATCH")
sys.exit(0 if ok else 1)
