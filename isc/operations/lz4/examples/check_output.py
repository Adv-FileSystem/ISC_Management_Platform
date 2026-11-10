#!/usr/bin/env python3
"""lz4 결과 확인 (README 3).

FPGA 출력(플랫폼 블록 형식)을 블록마다 풀어 원본과 같은지 확인하고 압축률을 출력합니다.
출력은 표준 .lz4 프레임이 아니므로 `lz4 -d`로 풀 수 없습니다. 이 스크립트가 복원기 역할도 합니다(--restore).

    python3 isc/operations/lz4/examples/check_output.py datasets/lz4/service.log.isclz4.emu
    python3 isc/operations/lz4/examples/check_output.py 출력파일 --input 원본로그 --restore 복원파일

블록 형식: 블록마다 12바이트 헤더(little-endian u32 raw_size, u32 stored_size, u32 flags) + 페이로드.
flags=0이면 표준 LZ4 블록, flags=1이면 원본 그대로.
"""
import argparse
import struct

import lz4.block

DEFAULT_INPUT = "datasets/lz4/service.log"


def restore(framed: bytes) -> tuple[bytes, int]:
    out, pos, blocks = bytearray(), 0, 0
    while pos < len(framed):
        raw_size, stored_size, flags = struct.unpack_from("<III", framed, pos)
        pos += 12
        payload = framed[pos:pos + stored_size]
        pos += stored_size
        out += payload if flags == 1 else lz4.block.decompress(payload, uncompressed_size=raw_size)
        blocks += 1
    return bytes(out), blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="lz4 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 원본 파일")
    parser.add_argument("--restore", metavar="FILE", help="풀어 낸 원본을 이 파일로 저장")
    args = parser.parse_args()

    framed = open(args.output, "rb").read()
    original = open(args.input, "rb").read()
    restored, blocks = restore(framed)
    if args.restore:
        open(args.restore, "wb").write(restored)

    print(f"blocks={blocks} original={len(original)} compressed={len(framed)} "
          f"ratio={len(original) / max(len(framed), 1):.2f}")
    print("ROUNDTRIP OK" if restored == original else "ROUNDTRIP MISMATCH")
    raise SystemExit(0 if restored == original else 1)


if __name__ == "__main__":
    main()
