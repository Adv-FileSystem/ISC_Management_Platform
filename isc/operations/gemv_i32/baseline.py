"""CPU reference for gemv_i32: numpy int64 product, then wrapped to int32 like the device.

Independent of the kernel: one matrix product in 64-bit, no padding, no streams.
"""
from __future__ import annotations

from isc.operations.baseline import BaselineUnavailable
from gemv_format import HEADER, check

# 2x3 matrix, alpha 2, beta -1: a valid input for `isc check`'s baseline probe.
PROBE_INPUT = HEADER.pack(2, 3, 2, -1) + b"".join(
    v.to_bytes(4, "little", signed=True) for v in [1, 2, 3, 4, 5, 6, 1, 1, 1, 10, 20])


def compute(data: bytes, parameters: dict) -> bytes:
    try:
        import numpy as np
    except ImportError as exc:
        raise BaselineUnavailable("the gemv_i32 CPU baseline needs `numpy`") from exc

    m, n, alpha, beta = check(data)
    offset = HEADER.size
    a = np.frombuffer(data, dtype="<i4", count=m * n, offset=offset).reshape(m, n).astype(np.int64)
    offset += 4 * m * n
    x = np.frombuffer(data, dtype="<i4", count=n, offset=offset).astype(np.int64)
    offset += 4 * n
    y = np.frombuffer(data, dtype="<i4", count=m, offset=offset).astype(np.int64)
    result = alpha * (a @ x) + beta * y
    return (result & 0xFFFFFFFF).astype(np.uint32).view("<i4").tobytes()
