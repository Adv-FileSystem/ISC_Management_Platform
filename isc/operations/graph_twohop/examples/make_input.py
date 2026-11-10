#!/usr/bin/env python3
"""graph_twohop 입력 파일 만들기 (README 4.1).

    python3 isc/operations/graph_twohop/examples/make_input.py           # 예시 데이터 → datasets/pscen/friends_1000_fof.bin
    python3 isc/operations/graph_twohop/examples/make_input.py -o 경로    # 저장 위치 바꾸기

[바꾸는 곳]       make_sample() — 친구 그래프(사람마다 친구 집합)와 추천 후보 쌍 목록
                                 (내 데이터: 같은 모양의 adj, pairs를 돌려주면 됨)
[고치지 않는 곳]  encode()      — 입력 형식: u32 V, u32 E, u32 P, u32 0 + u32 offset[V+1] + u32 index[E] (CSR)
                                 + (u32 src, u32 dst)[P] (리틀 엔디안)
"""
import argparse
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/pscen/friends_1000_fof.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample():
    rng = random.Random(11); V = 1000
    adj = [set() for _ in range(V)]
    for u in range(V):                      # 사람마다 무작위 친구 4명 (양방향)
        for _ in range(4):
            w = rng.randrange(V)
            if w != u: adj[u].add(w); adj[w].add(u)
    rng = random.Random(12); pairs = set()
    while len(pairs) < 250:                 # 친구의 친구(아직 친구 아님) = 추천 후보
        s = rng.randrange(V); f = rng.choice(sorted(adj[s])); d = rng.choice(sorted(adj[f]))
        if d != s and d not in adj[s]: pairs.add((s, d))
    while len(pairs) < 500:                 # 무작위 대조군
        s, d = rng.randrange(V), rng.randrange(V)
        if s != d and d not in adj[s]: pairs.add((s, d))
    pairs = sorted(pairs, key=lambda p: rng.random())
    return adj, pairs


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(adj: list, pairs: list) -> bytes:
    V = len(adj)
    off = [0]; idx = []
    for u in range(V): idx += sorted(adj[u]); off.append(len(idx))
    return (struct.pack("<IIII", V, len(idx), len(pairs), 0) + struct.pack(f"<{V+1}I", *off)
            + struct.pack(f"<{len(idx)}I", *idx) + b"".join(struct.pack("<II", s, d) for s, d in pairs))


def main() -> None:
    parser = argparse.ArgumentParser(description="graph_twohop 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    adj, pairs = make_sample()
    data = encode(adj, pairs)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"V={len(adj)} E={sum(len(a) for a in adj)} P={len(pairs)} bytes={len(data)}")


if __name__ == "__main__":
    main()
