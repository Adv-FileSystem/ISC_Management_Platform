#!/usr/bin/env python3
"""sha256 입력 파일 만들기 (README 2.1).

    python3 isc/operations/sha256/examples/make_input.py           # 예시 데이터 → datasets/sha256/release.bin
    python3 isc/operations/sha256/examples/make_input.py -o 경로    # 저장 위치 바꾸기

실제 배포 파일은 변환할 필요가 없습니다. 그 파일을 바로 `isc run ... --input`에 넘기십시오.

[바꾸는 곳]       make_sample() — 예시 데이터
[고치지 않는 곳]  encode()      — 입력 형식 (원시 바이트 그대로)
"""
import argparse
import random
from pathlib import Path

DEFAULT_OUTPUT = "datasets/sha256/release.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> bytes:
    return random.Random(3).randbytes(100_000)             # 100,000바이트, 고정 시드


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(data: bytes) -> bytes:
    return data                                            # 어떤 바이트열이든 그대로


def main() -> None:
    parser = argparse.ArgumentParser(description="sha256 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(make_sample()))


if __name__ == "__main__":
    main()
