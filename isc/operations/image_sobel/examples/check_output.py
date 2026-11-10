#!/usr/bin/env python3
"""image_sobel 결과 확인 (README 3).

FPGA 출력(헤더 + grad_x + grad_y)을 지금 쓰는 CPU 코드 OpenCV `cv2.Sobel`(연산 폴더의 baseline.py,
CV_8U, 3×3, BORDER_CONSTANT)과 바이트 비교합니다. 강한 윤곽(값 100 이상) 화소 수도 출력합니다.
--png 를 주면 두 기울기를 합친 윤곽 영상(edges.png)을 저장합니다.

    python3 isc/operations/image_sobel/examples/check_output.py datasets/image_sobel/part_outline.emu.bin
    python3 isc/operations/image_sobel/examples/check_output.py 출력파일 --input 입력파일 --png 폴더
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py)
from baseline import compute  # noqa: E402

DEFAULT_INPUT = "datasets/image_sobel/part_cam_256.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="image_sobel 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--png", metavar="DIR", help="윤곽 영상(edges.png)을 저장할 폴더")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    rows, cols = struct.unpack("<II", got[:8])
    n = rows * cols
    gx, gy = np.frombuffer(got, np.uint8, n, 8), np.frombuffer(got, np.uint8, n, 8 + n)
    print("size", rows, cols, "strong edges x", int((gx >= 100).sum()), "y", int((gy >= 100).sum()))

    if args.png:
        import cv2
        cv2.imwrite(f"{args.png}/edges.png", np.maximum(gx, gy).reshape(rows, cols))
        print(f"PNG 저장: {args.png}/edges.png")

    ok = got == compute(inp, {})
    print("MATCH" if ok else "MISMATCH")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
