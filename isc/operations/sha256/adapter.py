"""Host adapter for the platform-managed SHA-256 operation."""
from __future__ import annotations


ARG_IN, ARG_DIGEST, ARG_INPUT_SIZE = range(3)


def execute(session, data: bytes, parameters: dict) -> bytes:
    """Return the 32-byte binary SHA-256 digest."""
    # The kernel reads whole 32-bit words, so pad the buffer to a multiple of 4
    # bytes; the padding bytes are not hashed. This also keeps the buffer
    # non-empty for SHA-256(empty), since XRT buffer objects cannot be zero-sized.
    padded = data + bytes(-len(data) % 4) if data else bytes(4)
    bo_in = session.buffer(len(padded), arg=ARG_IN)
    bo_digest = session.buffer(32, arg=ARG_DIGEST)
    bo_in.write(padded)
    session.run(bo_in, bo_digest, len(data))
    return bo_digest.read(32)
