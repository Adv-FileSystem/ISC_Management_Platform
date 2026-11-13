#!/usr/bin/env python3
"""graph_renumber 입력 파일 만들기 (README 2.1).

    python3 isc/operations/graph_renumber/examples/make_input.py           # 예시 데이터 → datasets/graph_renumber/members_5000.bin
    python3 isc/operations/graph_renumber/examples/make_input.py -o 경로    # 저장 위치 바꾸기

[바꾸는 곳]       make_sample() — 회원별 그룹 번호 목록 (내 데이터: 번호 목록을 돌려주면 됨)
[고치지 않는 곳]  encode()      — 입력 형식: u32 N + i32 ids[N] (리틀 엔디안). 번호는 0 이상 N 미만이어야 함
"""
import argparse
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/graph_renumber/members_5000.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> list:
    rng = random.Random(3); n = 5000
    groups = rng.sample(range(n), 300)          # 성긴 그룹 번호 300종 (0 <= id < N)
    return [rng.choice(groups) for _ in range(n)]


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(ids: list) -> bytes:
    n = len(ids)
    bad = [v for v in ids if not 0 <= v < n]
    if bad:
        raise ValueError(f"번호는 0 이상 {n} 미만이어야 합니다 (예: {bad[0]})")
    return struct.pack("<I", n) + struct.pack(f"<{n}i", *ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="graph_renumber 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    ids = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(ids))
    print("ids", len(ids), "distinct", len(set(ids)))


if __name__ == "__main__":
    main()
