"""Host adapter for the platform-managed SHA-256 operation."""
from __future__ import annotations


ARG_IN, ARG_DIGEST, ARG_INPUT_SIZE = range(3)


def execute(session, data: bytes, parameters: dict) -> bytes:
    """Return the 32-byte binary SHA-256 digest."""
    # XRT buffer objects cannot be zero-sized, while SHA-256(empty) is valid.
    bo_in = session.buffer(max(1, len(data)), arg=ARG_IN)
    bo_digest = session.buffer(32, arg=ARG_DIGEST)
    if data:
        bo_in.write(data)
    session.run(bo_in, bo_digest, len(data))
    return bo_digest.read(32)
