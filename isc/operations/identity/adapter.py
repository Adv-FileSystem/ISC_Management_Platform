"""Platform-managed host adapter for the identity operation."""
from __future__ import annotations


ARG_IN, ARG_OUT, ARG_INPUT_SIZE, ARG_OUTPUT_SIZE = range(4)

# The kernel copies whole 32-byte words (see kernel/identity.cpp), so both device buffers
# are padded to a multiple of this; only the first `output_size` bytes are returned.
WORD_BYTES = 32


def execute(session, data: bytes, parameters: dict) -> bytes:
    if not data:
        return b""
    padded = -(-len(data) // WORD_BYTES) * WORD_BYTES
    bo_in = session.buffer(padded, arg=ARG_IN)
    bo_out = session.buffer(padded, arg=ARG_OUT)
    bo_size = session.buffer(4, arg=ARG_OUTPUT_SIZE)
    bo_in.write(data)
    if padded > len(data):
        bo_in.write(bytes(padded - len(data)), offset=len(data))
    session.run(bo_in, bo_out, len(data), bo_size)
    produced = int.from_bytes(bo_size.read(4), "little")
    if produced > len(data):
        raise RuntimeError(f"identity kernel reported {produced} bytes for a {len(data)}-byte input")
    return bo_out.read(padded)[:produced]
