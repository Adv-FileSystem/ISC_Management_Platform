"""Platform-managed adapter for the map profile: converts every input value."""
from __future__ import annotations

ARG_INPUT, ARG_OUTPUT, ARG_ROW_COUNT = range(3)


def execute(session, data: bytes, parameters: dict) -> bytes:
    if not data:
        return b""

    types = parameters["_types"]
    rows = len(data) // types["in_bytes"]
    output_size = rows * types["out_bytes"]
    input_buffer = session.buffer(len(data), arg=ARG_INPUT)
    output_buffer = session.buffer(output_size, arg=ARG_OUTPUT)
    input_buffer.write(data)
    session.run(input_buffer, output_buffer, rows)
    return output_buffer.read(output_size)
