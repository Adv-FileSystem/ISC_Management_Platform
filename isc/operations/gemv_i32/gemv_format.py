"""gemv_i32 의 바이트 형식. 어댑터와 CPU 기준선이 같은 정의를 쓴다.

입력 isc_gemv_i32 (리틀 엔디안):
    u32 m, u32 n, i32 alpha, i32 beta, i32 A[m][n] (행 우선), i32 x[n], i32 y[m]
출력 isc_i32_vector:
    i32 y_out[m] = alpha * A x + beta * y   (int32 산술, 넘치면 2^32 로 감김)
"""
from __future__ import annotations

import struct

HEADER = struct.Struct("<IIii")      # m, n, alpha, beta
PAR = 4                              # 커널이 한 사이클에 곱하는 열 수; n 은 이 배수로 채운다
MAX_BYTES_PER_BANK = 256 * 1024 * 1024


def check(data: bytes) -> tuple[int, int, int, int]:
    """(m, n, alpha, beta) of a well-formed input; ValueError saying what is wrong otherwise."""
    if len(data) < HEADER.size:
        raise ValueError(f"gemv_i32 input needs a {HEADER.size}-byte header; received {len(data)}")
    m, n, alpha, beta = HEADER.unpack_from(data, 0)
    if m == 0 or n == 0:
        raise ValueError(f"gemv_i32 needs m >= 1 and n >= 1, the header says m={m}, n={n}")
    expected = HEADER.size + 4 * (m * n + n + m)
    if len(data) != expected:
        raise ValueError(f"gemv_i32 header says m={m}, n={n}, which is {expected} bytes; received {len(data)}")
    if m * padded(n) * 4 > MAX_BYTES_PER_BANK:
        raise ValueError(f"gemv_i32 matrix of {m}x{n} does not fit one 256 MiB HBM bank")
    return m, n, alpha, beta


def padded(n: int) -> int:
    return -(-n // PAR) * PAR
