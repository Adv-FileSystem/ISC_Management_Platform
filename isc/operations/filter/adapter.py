"""Platform-managed adapter for the filter profile: keeps the values the user's function accepts."""
from __future__ import annotations

ARG_INPUT, ARG_OUTPUT, ARG_ROW_COUNT, ARG_OUTPUT_COUNT = range(4)
COUNT_BYTES = 4


def execute(session, data: bytes, parameters: dict) -> bytes:
    if not data:
        return b""

    width = parameters["_types"]["in_bytes"]
    rows = len(data) // width
    input_buffer = session.buffer(len(data), arg=ARG_INPUT)
    output_buffer = session.buffer(len(data), arg=ARG_OUTPUT)
    count_buffer = session.buffer(COUNT_BYTES, arg=ARG_OUTPUT_COUNT)
    input_buffer.write(data)
    session.run(input_buffer, output_buffer, rows, count_buffer)

    produced = int.from_bytes(count_buffer.read(COUNT_BYTES), "little")
    if produced > rows:
        raise ValueError(f"filter produced {produced} rows from {rows} input rows")
    return output_buffer.read(produced * width)
