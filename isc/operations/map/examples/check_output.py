#!/usr/bin/env python3
"""map 결과 확인 (README 3).

FPGA 출력을, 같은 변환식을 numpy float32로 계산한 결과와 비교합니다(곱셈을 float32로 해야 FPGA와 같은 반올림이 됨).
로직 파일(isc_map)의 변환식을 바꾸면 아래 [바꾸는 곳]의 convert()와 입출력 타입도 같이 바꿉니다.

    python3 isc/operations/map/examples/check_output.py datasets/map/result.emu.bin
    python3 isc/operations/map/examples/check_output.py 출력파일 --input 입력파일
"""
import argparse

import numpy as np

DEFAULT_INPUT = "datasets/map/temps.bin"
IN_DTYPE = "<f4"            # 입력 타입 (--type f32)
OUT_DTYPE = "u1"            # 출력 타입 (--out-type u8)


# ── [바꾸는 곳] 로직 파일의 isc_map과 같은 변환식 ──────────────────────
def convert(t):
    return 0 if t <= 0 else 255 if t >= 127.5 else int(np.float32(t) * np.float32(2.0))


def main() -> None:
    parser = argparse.ArgumentParser(description="map 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    args = parser.parse_args()

    values = np.frombuffer(open(args.input, "rb").read(), IN_DTYPE)
    expected = [convert(v) for v in values]
    got = np.frombuffer(open(args.output, "rb").read(), OUT_DTYPE).tolist()

    print(f"input={len(values)} output={len(got)} first5={list(zip(values[:5].tolist(), got[:5]))}")
    print("MATCH" if got == expected else "MISMATCH")
    raise SystemExit(0 if got == expected else 1)


if __name__ == "__main__":
    main()
