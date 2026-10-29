"""CPU reference for Adler-32: zlib's, in the same four big-endian bytes."""
from __future__ import annotations

import zlib


def compute(data: bytes, parameters: dict) -> bytes:
    return (zlib.adler32(data) & 0xFFFFFFFF).to_bytes(4, "big")
