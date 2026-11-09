"""graph_twohop 의 바이트 형식. 어댑터와 CPU 기준선이 같은 정의를 쓴다.

입력 isc_csr_pairs (리틀 엔디안):
    u32 V, u32 E, u32 P, u32 reserved, u32 offset[V+1], u32 index[E], u32 pair[P][2] = (src, dst), 정점 번호는 0부터
출력 isc_u32_vector: u32 count[P]
"""
from __future__ import annotations

import struct

HEADER = struct.Struct("<IIII")


def parse(data: bytes):
    if len(data) < HEADER.size + 4:
        raise ValueError(f"graph_twohop input needs a {HEADER.size}-byte header; received {len(data)}")
    v, e, p, _ = HEADER.unpack_from(data, 0)
    expected = HEADER.size + 4 * ((v + 1) + e + 2 * p)
    if v == 0 or p == 0 or len(data) != expected:
        raise ValueError(f"graph_twohop header says V={v}, E={e}, P={p} = {expected} bytes; received {len(data)}")
    pos = HEADER.size
    offset = struct.unpack_from(f"<{v + 1}I", data, pos); pos += 4 * (v + 1)
    index = struct.unpack_from(f"<{e}I", data, pos); pos += 4 * e
    flat = struct.unpack_from(f"<{2 * p}I", data, pos)
    pairs = list(zip(flat[0::2], flat[1::2]))
    if offset[0] != 0 or offset[-1] != e or any(b < a for a, b in zip(offset, offset[1:])):
        raise ValueError("graph_twohop offset must start at 0, end at E and never decrease")
    if any(x >= v for x in index) or any(s >= v or d >= v for s, d in pairs):
        raise ValueError("graph_twohop vertex id out of range")
    return v, e, offset, index, pairs
