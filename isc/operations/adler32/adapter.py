"""Host adapter for the platform-managed Adler-32 operation."""
from __future__ import annotations


ARG_IN, ARG_CHECKSUM, ARG_INPUT_SIZE = range(3)


def execute(session, data: bytes, parameters: dict) -> bytes:
    """Return the Adler-32 value as four big-endian bytes."""
    bo_in = session.buffer(len(data), arg=ARG_IN)
    bo_checksum = session.buffer(4, arg=ARG_CHECKSUM)
    bo_in.write(data)

    # Adler-32 starts with s1=1 and s2=0.
    bo_checksum.write((1).to_bytes(4, "little"))
    session.run(bo_in, bo_checksum, len(data))

    value = int.from_bytes(bo_checksum.read(4), "little")
    return value.to_bytes(4, "big")
