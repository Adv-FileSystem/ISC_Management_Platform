"""데모 입력 생성 — 압축 가능 구간, 비압축 구간, 부분 블록을 모두 포함한다.

64KB 블록 2개(마지막은 부분 블록)가 되도록 크기를 잡아 멀티블록 경로와
부분 블록 경로를 한 번에 지나가게 한다.
"""
from __future__ import annotations

import random
from pathlib import Path

WORDS = ("isc", "module", "kernel", "buffer", "device", "stream", "compress",
         "record", "validate", "platform", "lifecycle", "artifact")
TARGET_BYTES = 68_298  # 65536 + 2762 -> 두 번째 블록이 부분 블록이 된다


def build() -> bytes:
    random.seed(20260828)
    text = bytearray()
    while len(text) < TARGET_BYTES - 12_288:
        line = " ".join(random.choice(WORDS) for _ in range(12))
        text += line.encode() + b"\n"
    text += bytes(random.getrandbits(8) for _ in range(4_096))  # 압축이 안 되는 구간
    text += b"A" * 8_192                                        # 아주 잘 압축되는 구간
    return bytes(text[:TARGET_BYTES])


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "datasets" / "sample.bin"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = build()
    destination.write_bytes(payload)
    print(f"{destination}: {len(payload)} bytes")
