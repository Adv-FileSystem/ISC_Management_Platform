#!/usr/bin/env python3
"""aggregate 결과 확인 (README 3).

FPGA 출력 32바이트(count, sum, min, max)를, 같은 조건으로 Python에서 집계한 결과와 비교합니다.
로직 파일(isc_where)의 조건을 바꾸면 아래 [바꾸는 곳]의 selected()도 같은 조건으로 바꿉니다.

    python3 isc/operations/aggregate/examples/check_output.py datasets/aggregate/result.emu.bin
    python3 isc/operations/aggregate/examples/check_output.py 출력파일 --input 입력파일
"""
import argparse
import struct

DEFAULT_INPUT = "datasets/aggregate/tx.bin"
VALUE_FORMAT = "i"          # 입력 값 타입의 struct 코드: u8 B, u16 H, u32 I, u64 Q, i8 b, i16 h, i32 i, i64 q
RESULT_FORMAT = "<Qqqq"     # 부호 있는 입력이면 "<Qqqq", 부호 없는 입력이면 "<QQQQ"


# ── [바꾸는 곳] 로직 파일의 isc_where와 같은 조건 ──────────────────────
def selected(value) -> bool:
    return value < 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="aggregate 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 32바이트 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    args = parser.parse_args()

    size = struct.calcsize(VALUE_FORMAT)
    data = open(args.input, "rb").read()
    values = struct.unpack(f"<{len(data) // size}{VALUE_FORMAT}", data[:len(data) // size * size])
    sel = [v for v in values if selected(v)]
    expected = (len(sel), sum(sel), min(sel), max(sel)) if sel else (0, 0, 0, 0)
    got = struct.unpack(RESULT_FORMAT, open(args.output, "rb").read())

    print(f"count={got[0]} sum={got[1]} min={got[2]} max={got[3]}")
    print("MATCH" if got == expected else "MISMATCH")
    raise SystemExit(0 if got == expected else 1)


if __name__ == "__main__":
    main()
