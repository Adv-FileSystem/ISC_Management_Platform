"""Decode an snappy_compress output with the independent pure-Python decoder and compare with the input.

The input is compared after pre() is applied to every byte. pre() must be the same rule as isc_pre in the
module's logic.hpp. For a module made without a logic file, add --original to compare with the input as is.

    python3 isc/operations/snappy_compress/check_output.py <input> <output> [--original]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snappy_raw  # noqa: E402


# [바꾸는 곳] logic.hpp 의 isc_pre 와 같은 규칙
def pre(b: int) -> int:
    return ord("#") if ord("0") <= b <= ord("9") else b


original, framed = Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes()
if "--original" not in sys.argv[3:]:
    original = original.translate(bytes(pre(b) for b in range(256)))
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
