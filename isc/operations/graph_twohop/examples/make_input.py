#!/usr/bin/env python3
"""graph_twohop 입력 파일 만들기 (README 2.1) — 계좌·기기 사용 기록 + 점검할 계좌 쌍.

    python3 isc/operations/graph_twohop/examples/make_input.py           # 예시 데이터 → datasets/graph_twohop/accounts_devices.bin
    python3 isc/operations/graph_twohop/examples/make_input.py -o 경로    # 저장 위치 바꾸기

[바꾸는 곳]       make_sample() — 정점마다 연결된 정점 집합(adj)과 점검할 쌍 목록(pairs)
                                 (내 데이터: 같은 모양의 adj, pairs를 돌려주면 됨)
[고치지 않는 곳]  encode()      — 입력 형식: u32 V, u32 E, u32 P, u32 0 + u32 offset[V+1] + u32 index[E] (CSR)
                                 + (u32 src, u32 dst)[P] (리틀 엔디안)
"""
import argparse
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/graph_twohop/accounts_devices.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample():
    rng = random.Random(21)
    accounts, devices = 800, 1200                      # 정점 번호: 계좌 0~799, 기기 800~1999
    adj = [set() for _ in range(accounts + devices)]

    def use(account, device):                          # 계좌가 기기를 사용 (양방향 간선)
        adj[account].add(device); adj[device].add(account)

    rings = [list(range(r * 8, r * 8 + 8)) for r in range(5)]      # 의심 조직 5개 × 계좌 8개
    in_ring = {a for ring in rings for a in ring}
    for r, ring in enumerate(rings):
        pool = [accounts + r * 4 + k for k in range(4)]            # 조직마다 돌려 쓰는 기기 4대
        for a in ring:
            for d in rng.sample(pool, rng.choice([3, 4])):
                use(a, d)
    for a in range(accounts):                          # 일반 계좌: 자기 기기 1~2대 (가끔 우연히 겹침)
        if a not in in_ring:
            for _ in range(rng.choice([1, 2])):
                use(a, accounts + 20 + rng.randrange(devices - 20))

    pairs = set()
    while len(pairs) < 250:                            # 같은 조직 안의 계좌 쌍
        ring = rng.choice(rings); s, d = rng.sample(ring, 2); pairs.add((s, d))
    while len(pairs) < 500:                            # 무작위 계좌 쌍 (대조군)
        s, d = rng.sample(range(accounts), 2); pairs.add((s, d))
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
