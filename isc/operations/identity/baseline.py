"""CPU reference for the identity operation: an actual copy.

`bytes(data)` looks right and is wrong here. CPython returns the *same* object
for an immutable bytes input, so it measures nothing -- and identity exists in
this catalog precisely to price pure movement against pure PCIe transfer. A
baseline that does no work would make that comparison meaningless.
"""
from __future__ import annotations


def compute(data: bytes, parameters: dict) -> bytes:
    return memoryview(data).tobytes()
