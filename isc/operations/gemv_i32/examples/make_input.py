#!/usr/bin/env python3
"""gemv_i32 입력 파일 만들기 (README 4.1).

    python3 isc/operations/gemv_i32/examples/make_input.py           # 예시 데이터 → datasets/pscen/credit_512x64.bin
    python3 isc/operations/gemv_i32/examples/make_input.py -o 경로    # 저장 위치 바꾸기

[바꾸는 곳]       make_sample() — 고객 특성 행렬 A, 가중치 x, 기본 점수 y, alpha, beta
                                 (내 데이터: numpy 배열로 읽어 같은 이름으로 돌려주면 됨. 예: np.load("features.npy"))
[고치지 않는 곳]  encode()      — 입력 형식: u32 m, u32 n, i32 alpha, i32 beta + i32 A[m][n](행 우선) + i32 x[n] + i32 y[m]
                                 (리틀 엔디안). 결과는 y_out = alpha·A·x + beta·y (int32)
"""
import argparse
import struct
from pathlib import Path

import numpy as np

DEFAULT_OUTPUT = "datasets/pscen/credit_512x64.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample():
    rng = np.random.default_rng(21); m, n = 512, 64          # 고객 512명, 특성 64개
    A = rng.integers(-124, 125, size=(m, n), dtype=np.int32)  # 정규화한 고객 특성 (정수 척도)
    x = rng.integers(-120, 123, size=n, dtype=np.int32)       # 특성별 가중치
    y = np.full(m, 600, dtype=np.int32)                       # 기본 점수 600
    alpha, beta = 1, 1                                        # 점수 = 1·A·x + 1·600
    return A, x, y, alpha, beta


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(A, x, y, alpha: int, beta: int) -> bytes:
    A, x, y = (np.asarray(v, dtype="<i4") for v in (A, x, y))
    m, n = A.shape
    if x.shape != (n,) or y.shape != (m,):
        raise ValueError(f"A가 {m}x{n}이면 x는 {n}개, y는 {m}개여야 합니다 (x {x.shape}, y {y.shape})")
    return struct.pack("<IIii", m, n, alpha, beta) + A.tobytes() + x.tobytes() + y.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="gemv_i32 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    A, x, y, alpha, beta = make_sample()
    data = encode(A, x, y, alpha, beta)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("m", A.shape[0], "n", A.shape[1], "bytes", len(data))


if __name__ == "__main__":
    main()
