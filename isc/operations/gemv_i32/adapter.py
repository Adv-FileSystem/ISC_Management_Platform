"""ISC가 관리하는 int32 GEMV 연산 호스트 로직.

열 수 n 을 커널 병렬 폭(4)의 배수로 0 을 채워 넓힌다. 0 열과 x 의 0 원소는 곱에 아무것도
더하지 않으므로 결과는 그대로다. 한 번의 커널 호출로 끝난다.
"""
from __future__ import annotations

import numpy as np

from gemv_format import HEADER, check, padded

# 커널 인자 인덱스 (operation.yaml의 kernel.arguments 순서와 같다)
ARG_A, ARG_X, ARG_Y, ARG_YOUT, ARG_M, ARG_N, ARG_ALPHA, ARG_BETA = range(8)


def check_input(data: bytes, parameters: dict) -> None:
    check(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    m, n, alpha, beta = check(data)
    n_pad = padded(n)
    offset = HEADER.size
    a = np.frombuffer(data, dtype="<i4", count=m * n, offset=offset).reshape(m, n)
    offset += 4 * m * n
    x = np.frombuffer(data, dtype="<i4", count=n, offset=offset)
    offset += 4 * n
    y = data[offset:offset + 4 * m]

    if n_pad != n:
        a = np.pad(a, ((0, 0), (0, n_pad - n)))
        x = np.pad(x, (0, n_pad - n))

    bo_a = session.buffer(4 * m * n_pad, arg=ARG_A)
    bo_x = session.buffer(4 * n_pad, arg=ARG_X)
    bo_y = session.buffer(4 * m, arg=ARG_Y)
    bo_out = session.buffer(4 * m, arg=ARG_YOUT)
    bo_a.write(np.ascontiguousarray(a, dtype="<i4").tobytes())
    bo_x.write(np.ascontiguousarray(x, dtype="<i4").tobytes())
    bo_y.write(y)
    session.run(bo_a, bo_x, bo_y, bo_out, m, n_pad, alpha, beta)

    session.metric("m", m)
    session.metric("n", n)
    return bo_out.read(4 * m)
