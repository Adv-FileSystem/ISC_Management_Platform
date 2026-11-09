"""mc_european 의 바이트 형식. 어댑터와 CPU 기준선이 같은 정의를 쓴다.

입력 isc_mc_options_f32 (리틀 엔디안):
    u32 count, u32 reserved, f32 option[count][8] = S, K, sigma, q, r, T, type(0 콜, 1 풋), reserved
출력 isc_f32_vector:
    f32 price[count]
"""
from __future__ import annotations

import struct

HEADER = struct.Struct("<II")
FIELDS = 8
RECORD = struct.Struct("<8f")
MAX_COUNT = 65536


def check(data: bytes) -> int:
    if len(data) < HEADER.size:
        raise ValueError(f"mc_european input needs an {HEADER.size}-byte header; received {len(data)}")
    count, _ = HEADER.unpack_from(data, 0)
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"mc_european count must be 1..{MAX_COUNT}, the header says {count}")
    expected = HEADER.size + count * RECORD.size
    if len(data) != expected:
        raise ValueError(f"mc_european header says {count} options = {expected} bytes; received {len(data)}")
    for i in range(count):
        s, k, sigma, q, r, t, kind, _ = RECORD.unpack_from(data, HEADER.size + i * RECORD.size)
        if s <= 0 or k <= 0 or sigma <= 0 or t <= 0 or kind not in (0.0, 1.0):
            raise ValueError(f"mc_european option {i}: S, K, sigma, T must be > 0 and type 0 or 1")
    return count


def options(data: bytes):
    count = check(data)
    return [RECORD.unpack_from(data, HEADER.size + i * RECORD.size) for i in range(count)]
