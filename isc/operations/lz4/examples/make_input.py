#!/usr/bin/env python3
"""lz4 입력 파일 만들기 (README 4.1).

    python3 isc/operations/lz4/examples/make_input.py           # 예시 로그 → datasets/lz4/service.log
    python3 isc/operations/lz4/examples/make_input.py -o 경로    # 저장 위치 바꾸기

실제 로그 파일은 변환할 필요가 없습니다. 그 파일을 바로 `isc run ... --input`에 넘기십시오.

[바꾸는 곳]       make_sample() — 예시 데이터 (웹 서버 접근 로그 1,500줄)
[고치지 않는 곳]  encode()      — 입력 형식 (원시 바이트 그대로)
"""
import argparse
import random
from pathlib import Path

DEFAULT_OUTPUT = "datasets/lz4/service.log"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> str:
    rng = random.Random(1)
    lines = []
    for i in range(1500):
        level = rng.choice(["INFO", "INFO", "INFO", "WARN", "ERROR"])
        path = rng.choice(["/api/orders", "/api/users", "/health", "/api/cart"])
        lines.append(f"2026-11-05T10:{i // 60 % 60:02d}:{i % 60:02d}Z {level} GET {path} status=200 latency_ms={rng.randint(1, 900)}\n")
    return "".join(lines)


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(text: str) -> bytes:
    return text.encode()                                   # 어떤 바이트열이든 그대로


def main() -> None:
    parser = argparse.ArgumentParser(description="lz4 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(make_sample()))


if __name__ == "__main__":
    main()
