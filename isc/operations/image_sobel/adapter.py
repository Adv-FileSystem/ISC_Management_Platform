"""ISC가 관리하는 Sobel 3x3 연산 호스트 로직. 영상 한 장을 커널 한 번에 처리한다."""
from __future__ import annotations

from sobel_format import HEADER, check

ARG_IN, ARG_GX, ARG_GY, ARG_ROWS, ARG_COLS = range(5)


def check_input(data: bytes, parameters: dict) -> None:
    check(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    rows, cols = check(data)
    pixels = rows * cols
    bo_in = session.buffer(pixels, arg=ARG_IN)
    bo_gx = session.buffer(pixels, arg=ARG_GX)
    bo_gy = session.buffer(pixels, arg=ARG_GY)
    bo_in.write(data[HEADER.size:])
    session.run(bo_in, bo_gx, bo_gy, rows, cols)
    session.metric("pixels", pixels)
    return HEADER.pack(rows, cols) + bo_gx.read(pixels) + bo_gy.read(pixels)
