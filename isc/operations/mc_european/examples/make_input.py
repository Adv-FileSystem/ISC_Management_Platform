#!/usr/bin/env python3
"""mc_european 입력 파일 만들기 (README 2.1).

    python3 isc/operations/mc_european/examples/make_input.py                 # 예시 옵션 8건 → datasets/mc_european/option_book_8.bin
    python3 isc/operations/mc_european/examples/make_input.py --csv book.csv  # 내 보유 옵션 CSV를 변환
    python3 isc/operations/mc_european/examples/make_input.py -o 경로          # 저장 위치 바꾸기

CSV 머리글: S,K,sigma,q,r,T,type   (type: 0 콜, 1 풋. 한 줄에 옵션 1건)

[바꾸는 곳]       make_sample() — 예시 옵션 목록. 또는 --csv로 실제 데이터를 읽음
[고치지 않는 곳]  encode()      — 입력 형식: u32 count, u32 0 + 옵션마다 f32 8개 (S, K, sigma, q, r, T, type, 0) (리틀 엔디안)
"""
import argparse
import csv
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/mc_european/option_book_8.bin"
FIELDS = ("S", "K", "sigma", "q", "r", "T", "type")


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> list:
    # S, K, sigma, q, r, T, type(0 콜, 1 풋). 6건은 공식 테스트 조건표의 조합, 2건(T=0.5, T=2)은 그 밖의 조합
    return [(100, 75, 0.11, 0.00, 0.01, 1, 0), (100, 100, 0.50, 0.00, 0.05, 1, 0), (100, 125, 1.20, 0.05, 0.15, 1, 0),
            (100, 100, 0.30, 0.02, 0.03, 0.5, 0), (100, 75, 0.11, 0.00, 0.01, 1, 1), (100, 100, 0.50, 0.00, 0.05, 1, 1),
            (100, 125, 1.20, 0.05, 0.15, 1, 1), (100, 110, 0.25, 0.01, 0.04, 2, 1)]


def read_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return [tuple(float(row[k]) for k in FIELDS) for row in csv.DictReader(f)]


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(book: list) -> bytes:
    return struct.pack("<II", len(book), 0) + b"".join(struct.pack("<8f", *o, 0.0) for o in book)


def main() -> None:
    parser = argparse.ArgumentParser(description="mc_european 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", help="보유 옵션 CSV (머리글: S,K,sigma,q,r,T,type)")
    args = parser.parse_args()
    book = read_csv(args.csv) if args.csv else make_sample()
    data = encode(book)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(len(book), "options,", len(data), "bytes")


if __name__ == "__main__":
    main()
