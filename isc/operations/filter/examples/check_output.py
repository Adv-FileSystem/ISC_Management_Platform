#!/usr/bin/env python3
"""filter 결과 확인 (README 3).

FPGA 출력을, 같은 조건을 Python으로 적용한 결과와 값·순서까지 비교합니다.
로직 파일(isc_filter)의 조건을 바꾸면 아래 [바꾸는 곳]의 keep()도 같은 조건으로 바꿉니다.

    python3 isc/operations/filter/examples/check_output.py datasets/filter/result.emu.bin
    python3 isc/operations/filter/examples/check_output.py 출력파일 --input 입력파일
"""
import argparse
import struct

DEFAULT_INPUT = "datasets/filter/amounts.bin"
VALUE_FORMAT = "I"          # 값 타입의 struct 코드: u8 B, u16 H, u32 I, u64 Q, i8 b, i16 h, i32 i, i64 q, f32 f


# ── [바꾸는 곳] 로직 파일의 isc_filter와 같은 조건 ─────────────────────
def keep(value) -> bool:
    return 1000 <= value < 5000


def main() -> None:
    parser = argparse.ArgumentParser(description="filter 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    args = parser.parse_args()

    size = struct.calcsize(VALUE_FORMAT)
    data = open(args.input, "rb").read()
    values = struct.unpack(f"<{len(data) // size}{VALUE_FORMAT}", data[:len(data) // size * size])
    expected = [v for v in values if keep(v)]
    out = open(args.output, "rb").read()
    got = list(struct.unpack(f"<{len(out) // size}{VALUE_FORMAT}", out))

    print(f"input={len(values)} kept={len(got)} first5={got[:5]}")
    print("MATCH" if got == expected else "MISMATCH")
    raise SystemExit(0 if got == expected else 1)


if __name__ == "__main__":
    main()
