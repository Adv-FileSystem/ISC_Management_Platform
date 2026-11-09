"""CPU reference for graph_twohop: for each pair, walk src's neighbours and count dst among theirs.

Written independently of the kernel; it matched all 1000 pairs of the official golden
(graph/L2/tests/twoHop/data, 1-based ids) on 2026-11-05.
"""
from __future__ import annotations

import struct

from twohop_format import HEADER, parse

# 3 vertices, edges 0->1, 1->2, 0->2; pairs (0,2): one path 0->1->2
PROBE_INPUT = HEADER.pack(3, 3, 1, 0) + struct.pack("<4I", 0, 2, 3, 3) + struct.pack("<3I", 1, 2, 2) + struct.pack("<2I", 0, 2)


def compute(data: bytes, parameters: dict) -> bytes:
    v, e, offset, index, pairs = parse(data)
    counts = []
    for s, d in pairs:
        total = 0
        for x in index[offset[s]:offset[s + 1]]:
            total += index[offset[x]:offset[x + 1]].count(d)
        counts.append(total)
    return struct.pack(f"<{len(counts)}I", *counts)
