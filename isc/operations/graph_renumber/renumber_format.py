"""graph_renumber 의 바이트 형식: 입력 u32 N, i32 id[N] (0 <= id < N) / 출력 u32 clusters, i32 new_id[N]."""
from __future__ import annotations

import struct

HEADER = struct.Struct("<I")


def parse(data: bytes):
    if len(data) < HEADER.size + 4:
        raise ValueError(f"graph_renumber input needs a 4-byte count and ids; received {len(data)}")
    (n,) = HEADER.unpack_from(data, 0)
    if n == 0 or len(data) != HEADER.size + 4 * n:
        raise ValueError(f"graph_renumber header says N={n} = {HEADER.size + 4 * n} bytes; received {len(data)}")
    ids = struct.unpack_from(f"<{n}i", data, HEADER.size)
    if any(not 0 <= x < n for x in ids):
        raise ValueError("graph_renumber ids must satisfy 0 <= id < N (the kernel's map tables have N slots)")
    return n, ids
