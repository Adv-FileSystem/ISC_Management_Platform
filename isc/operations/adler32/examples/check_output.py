#!/usr/bin/env python3
"""adler32 결과 확인 (README 3).

FPGA 출력(4바이트, big-endian)을 지금 쓰는 CPU 코드 `zlib.adler32`의 결과와 비교합니다.

    python3 isc/operations/adler32/examples/check_output.py datasets/adler32/payload.adler.emu
    python3 isc/operations/adler32/examples/check_output.py 출력파일 --input 원본블록
"""
import argparse
import zlib

DEFAULT_INPUT = "datasets/adler32/payload.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="adler32 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 4바이트 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 블록 파일")
    args = parser.parse_args()

    data = open(args.input, "rb").read()
    fpga = int.from_bytes(open(args.output, "rb").read(), "big")     # 출력은 big-endian
    cpu = zlib.adler32(data)                                          # 지금 쓰는 CPU 코드

    print(f"fpga adler32 = {fpga:08x}")
    print(f"cpu  adler32 = {cpu:08x}  (zlib.adler32, {len(data)} bytes)")
    print("MATCH" if fpga == cpu else "MISMATCH")
    raise SystemExit(0 if fpga == cpu else 1)


if __name__ == "__main__":
    main()
