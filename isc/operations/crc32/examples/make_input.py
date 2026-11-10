#!/usr/bin/env python3
"""crc32 입력 파일 만들기 (README 4.1).

    python3 isc/operations/crc32/examples/make_input.py           # 예시 데이터 → datasets/crc32/backup.bin
    python3 isc/operations/crc32/examples/make_input.py -o 경로    # 저장 위치 바꾸기

실제 백업 파일은 변환할 필요가 없습니다. 그 파일을 바로 `isc run ... --input`에 넘기십시오.

[바꾸는 곳]       make_sample() — 예시 데이터
[고치지 않는 곳]  encode()      — 입력 형식 (원시 바이트 그대로)
"""
import argparse
import random
import zlib
from pathlib import Path

DEFAULT_OUTPUT = "datasets/crc32/backup.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> bytes:
    return random.Random(7).randbytes(256 * 1024)          # 256 KiB, 고정 시드


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(data: bytes) -> bytes:
    return data                                            # 어떤 바이트열이든 그대로


def main() -> None:
    parser = argparse.ArgumentParser(description="crc32 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    data = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(data))
    print(f"expected crc32 = {zlib.crc32(data):08x}")      # 4.5에서 비교할 기준값


if __name__ == "__main__":
    main()
