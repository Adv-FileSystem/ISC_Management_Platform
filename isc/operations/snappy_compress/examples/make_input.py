#!/usr/bin/env python3
"""snappy_compress 입력 파일 만들기 (README 4.1).

    python3 isc/operations/snappy_compress/examples/make_input.py           # 예시 → datasets/pscen/kafka_events_200k.bin
    python3 isc/operations/snappy_compress/examples/make_input.py -o 경로    # 저장 위치 바꾸기

실제 메시지 묶음 파일은 변환할 필요가 없습니다. 그 파일을 바로 `isc run ... --input`에 넘기십시오.

[바꾸는 곳]       make_sample() — 예시 데이터 (Kafka 클릭 이벤트 약 200 KB)
[고치지 않는 곳]  encode()      — 입력 형식 (원시 바이트 그대로)
"""
import argparse
import json
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/pscen/kafka_events_200k.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> bytes:
    rng = random.Random(8)
    out = bytearray()
    while len(out) < 200_000:
        ev = {"ts": 1762300000 + len(out) // 50, "user": f"u{rng.randrange(5000):05d}",
              "event": rng.choice(["view", "click", "cart", "buy"]), "item": rng.randrange(20000),
              "price": round(rng.uniform(1, 300), 2), "session": f"s{rng.randrange(100000):06x}"}
        body = json.dumps(ev, separators=(",", ":")).encode()
        out += struct.pack("<IQ", len(body), rng.getrandbits(64)) + body   # 메시지 머리말(길이, 키) + JSON 본문
    return bytes(out)


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(data: bytes) -> bytes:
    return data                                            # 어떤 바이트열이든 그대로


def main() -> None:
    parser = argparse.ArgumentParser(description="snappy_compress 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    data = encode(make_sample())
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(len(data), "bytes")


if __name__ == "__main__":
    main()
