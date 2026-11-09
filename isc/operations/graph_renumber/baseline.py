"""CPU reference for graph_renumber: first-appearance numbering, the rule of the official host's
renumberClustersContiguously (graph/L2/tests/renumber/host/test_renumber.cpp), written independently."""
from __future__ import annotations

import struct

from renumber_format import HEADER, parse

PROBE_INPUT = HEADER.pack(4) + struct.pack("<4i", 3, 3, 1, 3)


def compute(data: bytes, parameters: dict) -> bytes:
    n, ids = parse(data)
    seen = {}
    new = [seen.setdefault(x, len(seen)) for x in ids]
    return struct.pack("<I", len(seen)) + struct.pack(f"<{n}i", *new)
