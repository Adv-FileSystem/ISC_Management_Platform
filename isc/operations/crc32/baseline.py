"""CPU reference for CRC-32: zlib's, in the same four big-endian bytes."""
from __future__ import annotations

import zlib


def compute(data: bytes, parameters: dict) -> bytes:
    return (zlib.crc32(data) & 0xFFFFFFFF).to_bytes(4, "big")
