"""ISC가 관리하는 bicubic 축소 연산 호스트 로직.

화소를 8개씩 64비트 워드로 묶는 형식은 공식 데모 호스트(test_resize.cpp)와 같다. 화소 수가 8의
배수가 아니면 마지막 워드를 0으로 채운다 (공식 호스트는 남는 화소를 쓰지 않는다).
"""
from __future__ import annotations

import struct

from bicubic_format import HEADER, check, padded

ARG_CONFIGS, ARG_SRC, ARG_DST = range(3)


def check_input(data: bytes, parameters: dict) -> None:
    rows, cols = check(data)
    out_rows, out_cols = int(parameters["out_rows"]), int(parameters["out_cols"])
    if out_rows > rows or out_cols > cols:
        raise ValueError(f"image_bicubic only downscales: {rows}x{cols} -> {out_rows}x{out_cols}")


def execute(session, data: bytes, parameters: dict) -> bytes:
    check_input(data, parameters)
    rows, cols = check(data)
    out_rows, out_cols = int(parameters["out_rows"]), int(parameters["out_cols"])
    pixels = data[HEADER.size:]
    src_bytes = padded(len(pixels))
    dst_bytes = padded(out_rows * out_cols)

    bo_cfg = session.buffer(32, arg=ARG_CONFIGS)
    bo_src = session.buffer(src_bytes, arg=ARG_SRC)
    bo_dst = session.buffer(dst_bytes, arg=ARG_DST)
    bo_cfg.write(struct.pack("<4I", cols, rows, out_cols, out_rows).ljust(32, b"\0"))
    bo_src.write(pixels.ljust(src_bytes, b"\0"))
    session.run(bo_cfg, bo_src, bo_dst)
    session.metric("rows", rows)
    session.metric("cols", cols)
    return HEADER.pack(out_rows, out_cols) + bo_dst.read(dst_bytes)[:out_rows * out_cols]
