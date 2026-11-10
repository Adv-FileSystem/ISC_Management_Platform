#!/usr/bin/env python3
"""image_resize 결과 확인 (README 3).

FPGA 출력을 지금 쓰는 CPU 코드 OpenCV `cv2.resize(INTER_LINEAR)`(연산 폴더의 baseline.py)와 비교합니다.
장치는 고정소수점 보간이라 값이 조금 다를 수 있으므로, 화소마다 차이가 5 이하이면 PASS입니다(공식 testbench 허용오차).
출력 크기는 출력 파일의 헤더에서 읽습니다.

    python3 isc/operations/image_resize/examples/check_output.py datasets/image_resize/train_preview.emu.bin --input datasets/image_resize/frame_384x216.bin
    python3 isc/operations/image_resize/examples/check_output.py datasets/image_resize/train_input.hw.bin --input datasets/image_resize/frame_1920x1080.bin --png 폴더
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py)
from baseline import compute  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="image_resize 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", required=True, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--png", metavar="DIR", help="FPGA 결과를 PNG로 저장할 폴더")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    out_rows, out_cols = struct.unpack("<II", got[:8])
    ref = compute(inp, {"out_rows": out_rows, "out_cols": out_cols})
    d = np.abs(np.frombuffer(got[8:], np.uint8).astype(int) - np.frombuffer(ref[8:], np.uint8))
    print("size", out_rows, out_cols, "header", got[:8] == ref[:8], "max_diff", d.max(), "over_5", int((d > 5).sum()))

    if args.png:
        import cv2
        name = Path(args.output).stem
        cv2.imwrite(f"{args.png}/{name}.png", np.frombuffer(got[8:], np.uint8).reshape(out_rows, out_cols))
        print(f"PNG 저장: {args.png}/{name}.png")

    ok = got[:8] == ref[:8] and d.max() <= 5
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
