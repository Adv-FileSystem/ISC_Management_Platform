#!/usr/bin/env python3
"""crc32 결과 확인 (README 3).

FPGA 출력(4바이트, big-endian)을 지금 쓰는 CPU 코드 `zlib.crc32`의 결과와 비교합니다.

    python3 isc/operations/crc32/examples/check_output.py datasets/crc32/part-0001.crc.emu   # hw_emu 출력
    python3 isc/operations/crc32/examples/check_output.py datasets/crc32/part-0001.crc.hw    # U50 출력
    python3 isc/operations/crc32/examples/check_output.py 출력파일 --input 원본파일           # 내 파일
"""
import argparse
import time
import zlib

DEFAULT_INPUT = "datasets/crc32/part-0001.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="crc32 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 4바이트 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 원본 파일")
    args = parser.parse_args()

    data = open(args.input, "rb").read()
    fpga = int.from_bytes(open(args.output, "rb").read(), "big")

    start = time.perf_counter()
    cpu = zlib.crc32(data)                                  # 지금 쓰는 CPU 코드
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"fpga crc32 = {fpga:08x}")
    print(f"cpu  crc32 = {cpu:08x}  (zlib.crc32, {len(data)} bytes, {elapsed_ms:.3f} ms)")
    print("MATCH" if fpga == cpu else "MISMATCH")
    raise SystemExit(0 if fpga == cpu else 1)


if __name__ == "__main__":
    main()
