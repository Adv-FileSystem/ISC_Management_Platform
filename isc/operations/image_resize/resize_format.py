"""image_resize 의 바이트 형식: u32 rows, u32 cols, u8 pixels[rows][cols] (리틀 엔디안 헤더)."""
from __future__ import annotations

import struct

HEADER = struct.Struct("<II")
MAX_IN = (2160, 3840)                # 커널 상수 HEIGHT, WIDTH
MAX_OUT = (1080, 1920)               # 커널 상수 NEWHEIGHT, NEWWIDTH
MAX_DOWNSCALE = 2                    # 커널 상수 MAXDOWNSCALE


def check(data: bytes) -> tuple[int, int]:
    if len(data) < HEADER.size + 1:
        raise ValueError(f"image input needs a {HEADER.size}-byte header and pixels; received {len(data)}")
    rows, cols = HEADER.unpack_from(data, 0)
    if not (1 <= rows <= MAX_IN[0] and 1 <= cols <= MAX_IN[1]):
        raise ValueError(f"input image must be at most {MAX_IN[0]}x{MAX_IN[1]}; the header says {rows}x{cols}")
    if len(data) != HEADER.size + rows * cols:
        raise ValueError(f"header says {rows}x{cols} = {rows * cols} pixels; received {len(data) - HEADER.size}")
    return rows, cols


def check_output_size(rows: int, cols: int, out_rows: int, out_cols: int) -> None:
    if not (1 <= out_rows <= MAX_OUT[0] and 1 <= out_cols <= MAX_OUT[1]):
        raise ValueError(f"output must be at most {MAX_OUT[0]}x{MAX_OUT[1]}; asked {out_rows}x{out_cols}")
    if rows > MAX_DOWNSCALE * out_rows or cols > MAX_DOWNSCALE * out_cols:
        raise ValueError(f"downscale beyond {MAX_DOWNSCALE}x is not built in: {rows}x{cols} -> {out_rows}x{out_cols}")
