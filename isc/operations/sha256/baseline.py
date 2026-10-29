"""CPU reference for SHA-256: hashlib's 32-byte binary digest."""
from __future__ import annotations

import hashlib


def compute(data: bytes, parameters: dict) -> bytes:
    return hashlib.sha256(data).digest()
