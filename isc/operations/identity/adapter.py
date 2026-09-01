"""Platform-managed host adapter for the identity operation."""
from __future__ import annotations


ARG_IN, ARG_OUT, ARG_INPUT_SIZE, ARG_OUTPUT_SIZE = range(4)


def execute(session, data: bytes, parameters: dict) -> bytes:
    if not data:
        return b""
    bo_in = session.buffer(len(data), arg=ARG_IN)
    bo_out = session.buffer(len(data), arg=ARG_OUT)
    bo_size = session.buffer(4, arg=ARG_OUTPUT_SIZE)
    bo_in.write(data)
    session.run(bo_in, bo_out, len(data), bo_size)
    produced = int.from_bytes(bo_size.read(4), "little")
    return bo_out.read(produced)
