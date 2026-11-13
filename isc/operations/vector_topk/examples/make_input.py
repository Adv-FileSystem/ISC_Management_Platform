#!/usr/bin/env python3
"""vector_topk 입력 파일 만들기 (README 2.1).

    python3 isc/operations/vector_topk/examples/make_input.py           # 예시 데이터 → datasets/vector_topk/docs.bin
    python3 isc/operations/vector_topk/examples/make_input.py -o 경로    # 저장 위치 바꾸기

[바꾸는 곳]       make_sample() — 질의 임베딩 1개와 문서 임베딩 행렬 (int8)
                                 (내 데이터: 예: db = np.load("docs.npy").astype(np.int8))
[고치지 않는 곳]  encode()      — 입력 형식: u32 rows, u32 dim + i8 query[dim] + i8 db[rows][dim] (리틀 엔디안)
"""
import argparse
import struct
from pathlib import Path

import numpy as np

DEFAULT_OUTPUT = "datasets/vector_topk/docs.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample():
    rng = np.random.default_rng(42)
    rows, dim = 2000, 64
    db = rng.integers(-128, 128, size=(rows, dim), dtype=np.int8)
    query = db[123].copy()                                  # 123번 문서와 같은 질의 (정답 1위 = 123번)
    return query, db


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(query, db) -> bytes:
    query, db = np.asarray(query, dtype=np.int8), np.asarray(db, dtype=np.int8)
    rows, dim = db.shape
    if query.shape != (dim,):
        raise ValueError(f"질의 길이({query.shape})가 문서 차원({dim})과 다릅니다")
    return struct.pack("<II", rows, dim) + query.tobytes() + db.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="vector_topk 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    query, db = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(query, db))


if __name__ == "__main__":
    main()
