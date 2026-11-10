#!/usr/bin/env python3
"""aggregate 입력 파일 만들기 (README 2.1).

    python3 isc/operations/aggregate/examples/make_input.py                          # 예시 데이터 → datasets/aggregate/tx.bin
    python3 isc/operations/aggregate/examples/make_input.py --csv 파일.csv --column 열이름   # 내 CSV의 한 열을 변환
    python3 isc/operations/aggregate/examples/make_input.py -o 경로                   # 저장 위치 바꾸기

[바꾸는 곳]       make_sample() — 예시 데이터. 또는 --csv로 실제 데이터를 읽음
[고치지 않는 곳]  encode()      — 입력 형식: i32 little-endian 값이 이어진 파일 (헤더 없음)
                                 로직 파일을 `isc template aggregate --type ...`으로 만들 때 고른 타입과 같아야 합니다.
"""
import argparse
import csv
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/aggregate/tx.bin"
VALUE_FORMAT = "i"          # struct 형식 문자 (i32). 다른 타입을 쓰면 함께 바꿈: u8 B, u16 H, u32 I, u64 Q, i8 b, i16 h, i32 i, i64 q, f32 f


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> list:
    rng = random.Random(21)
    return [rng.randint(-2000, 20000) for _ in range(10000)]   # 거래 10,000건, 음수 = 환불


def read_csv(path: str, column: str) -> list:
    convert = float if VALUE_FORMAT == "f" else int
    with open(path, newline="", encoding="utf-8") as f:
        return [convert(row[column]) for row in csv.DictReader(f) if row[column].strip()]


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(values: list) -> bytes:
    return struct.pack(f"<{len(values)}{VALUE_FORMAT}", *values)


def main() -> None:
    parser = argparse.ArgumentParser(description="aggregate 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", help="실제 데이터 CSV 파일 (머리글 줄 필요)")
    parser.add_argument("--column", help="--csv에서 읽을 열 이름")
    args = parser.parse_args()
    if args.csv and not args.column:
        parser.error("--csv에는 --column이 필요합니다")
    values = read_csv(args.csv, args.column) if args.csv else make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(values))
    print(len(values), "values ->", out)


if __name__ == "__main__":
    main()
