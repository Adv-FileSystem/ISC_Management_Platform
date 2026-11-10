#!/usr/bin/env python3
"""sha256 결과 확인 (README 3).

FPGA 출력(32바이트 이진 다이제스트)을 지금 쓰는 CPU 코드 `hashlib.sha256`의 결과, 또는 공개된 다이제스트와 비교합니다.

    python3 isc/operations/sha256/examples/check_output.py datasets/sha256/release.sha256.emu
    python3 isc/operations/sha256/examples/check_output.py 출력파일 --input 배포파일
    python3 isc/operations/sha256/examples/check_output.py 출력파일 --expected 공개된16진수다이제스트
"""
import argparse
import hashlib

DEFAULT_INPUT = "datasets/sha256/release.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="sha256 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 32바이트 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--expected", help="공개된 다이제스트(16진수). 주면 --input 대신 이 값과 비교")
    args = parser.parse_args()

    fpga = open(args.output, "rb").read().hex()
    if args.expected:
        expected, source = args.expected.strip().lower(), "공개된 다이제스트"
    else:
        expected, source = hashlib.sha256(open(args.input, "rb").read()).hexdigest(), "hashlib.sha256"   # 지금 쓰는 CPU 코드

    print(f"fpga sha256 = {fpga}")
    print(f"기준 sha256 = {expected}  ({source})")
    print("MATCH" if fpga == expected else "MISMATCH")
    raise SystemExit(0 if fpga == expected else 1)


if __name__ == "__main__":
    main()
