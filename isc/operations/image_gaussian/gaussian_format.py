"""image_gaussian 의 바이트 형식: u32 rows, u32 cols, u8 pixels[rows][cols] (리틀 엔디안 헤더)."""
from __future__ import annotations

import struct

HEADER = struct.Struct("<II")
MAX_ROWS, MAX_COLS = 1080, 1920      # 커널 상수 HEIGHT, WIDTH


def check(data: bytes) -> tuple[int, int]:
    if len(data) < HEADER.size + 1:
        raise ValueError(f"image input needs a {HEADER.size}-byte header and pixels; received {len(data)}")
    rows, cols = HEADER.unpack_from(data, 0)
    if not (1 <= rows <= MAX_ROWS and 1 <= cols <= MAX_COLS):
        raise ValueError(f"image must be 1..{MAX_ROWS} rows x 1..{MAX_COLS} cols; the header says {rows}x{cols}")
    if len(data) != HEADER.size + rows * cols:
        raise ValueError(f"header says {rows}x{cols} = {rows * cols} pixels; received {len(data) - HEADER.size}")
    return rows, cols
