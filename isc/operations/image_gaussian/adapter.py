"""ISC가 관리하는 3x3 Gaussian 연산 호스트 로직. 영상 한 장을 커널 한 번에 처리한다."""
from __future__ import annotations

import struct

from gaussian_format import HEADER, check

ARG_IN, ARG_OUT, ARG_ROWS, ARG_COLS, ARG_SIGMA = range(5)


def check_input(data: bytes, parameters: dict) -> None:
    check(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    rows, cols = check(data)
    pixels = rows * cols
    sigma_bits = struct.unpack("<I", struct.pack("<f", int(parameters["sigma_milli"]) / 1000.0))[0]
    bo_in = session.buffer(pixels, arg=ARG_IN)
    bo_out = session.buffer(pixels, arg=ARG_OUT)
    bo_in.write(data[HEADER.size:])
    session.run(bo_in, bo_out, rows, cols, sigma_bits)
    session.metric("pixels", pixels)
    return HEADER.pack(rows, cols) + bo_out.read(pixels)
