"""ISC가 관리하는 bilinear resize 연산 호스트 로직. 영상 한 장을 커널 한 번에 처리한다."""
from __future__ import annotations

from resize_format import HEADER, check, check_output_size

ARG_IN, ARG_OUT, ARG_ROWS_IN, ARG_COLS_IN, ARG_ROWS_OUT, ARG_COLS_OUT = range(6)


def check_input(data: bytes, parameters: dict) -> None:
    rows, cols = check(data)
    check_output_size(rows, cols, int(parameters["out_rows"]), int(parameters["out_cols"]))


def execute(session, data: bytes, parameters: dict) -> bytes:
    rows, cols = check(data)
    out_rows, out_cols = int(parameters["out_rows"]), int(parameters["out_cols"])
    check_output_size(rows, cols, out_rows, out_cols)
    bo_in = session.buffer(rows * cols, arg=ARG_IN)
    bo_out = session.buffer(out_rows * out_cols, arg=ARG_OUT)
    bo_in.write(data[HEADER.size:])
    session.run(bo_in, bo_out, rows, cols, out_rows, out_cols)
    session.metric("pixels_in", rows * cols)
    session.metric("pixels_out", out_rows * out_cols)
    return HEADER.pack(out_rows, out_cols) + bo_out.read(out_rows * out_cols)
