"""Platform-managed adapter for the aggregate profile: count, sum, min and max in 32 bytes."""
from __future__ import annotations

ARG_INPUT, ARG_RESULT, ARG_ROW_COUNT = range(3)
RESULT_BYTES = 32  # little-endian u64 x 4: count, sum, min, max


def execute(session, data: bytes, parameters: dict) -> bytes:
    if not data:
        return bytes(RESULT_BYTES)

    rows = len(data) // parameters["_types"]["in_bytes"]
    input_buffer = session.buffer(len(data), arg=ARG_INPUT)
    result_buffer = session.buffer(RESULT_BYTES, arg=ARG_RESULT)
    input_buffer.write(data)
    session.run(input_buffer, result_buffer, rows)
    return result_buffer.read(RESULT_BYTES)
