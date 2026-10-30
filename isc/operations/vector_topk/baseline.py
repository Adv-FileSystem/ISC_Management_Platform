"""CPU reference for vector_topk: the same exact integer inner product, computed with numpy.

Written independently of the kernel and the adapter on purpose. It scores every row in one pass and
ranks with lexsort, where the kernel inserts into k sorted slots and the adapter merges chunks. Only
the byte format (topk_format) is shared, so a mistake in the format would not be caught here; the
format has its own tests.
"""
from __future__ import annotations

from isc.operations.baseline import BaselineUnavailable
from topk_format import HEADER, check, pack

BLOCK_ROWS = 1 << 16

# What `isc check` feeds the baseline to see that it runs (isc.operations.baseline.probe_input): a valid input
# of 3 rows and 4 dimensions. Without it the probe would send bytes this format rejects.
PROBE_INPUT = HEADER.pack(3, 4) + bytes([1, 2, 3, 4]) + bytes([1, 0, 0, 0]) \
    + bytes([0, 0, 0, 5]) + bytes([255, 255, 255, 255])


def compute(data: bytes, parameters: dict) -> bytes:
    try:
        import numpy as np
    except ImportError as exc:  # no other algorithm stands in for it
        raise BaselineUnavailable(
            "the vector_topk CPU baseline needs `numpy` (pip install numpy)") from exc

    rows, dim = check(data)
    k = int(parameters["k"])
    query = np.frombuffer(data, dtype=np.int8, count=dim, offset=HEADER.size).astype(np.int32)
    database = np.frombuffer(data, dtype=np.int8, count=rows * dim,
                             offset=HEADER.size + dim).reshape(rows, dim)

    scores = np.empty(rows, dtype=np.int32)
    for first in range(0, rows, BLOCK_ROWS):
        scores[first:first + BLOCK_ROWS] = database[first:first + BLOCK_ROWS].astype(np.int32) @ query

    ids = np.arange(rows, dtype=np.int64)
    ranked = np.lexsort((ids, -scores.astype(np.int64)))[:k]     # last key first: score, then id
    return pack([(int(index), int(scores[index])) for index in ranked], k)
