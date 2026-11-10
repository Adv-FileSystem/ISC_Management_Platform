#!/usr/bin/env python3
"""image_gaussian 결과 확인 (README 3).

FPGA 출력을 지금 쓰는 CPU 코드 OpenCV `cv2.GaussianBlur`(연산 폴더의 baseline.py, 3×3, BORDER_CONSTANT)와 비교합니다.
장치는 고정소수점 가중치를 써서 값이 조금 다를 수 있으므로, 화소마다 차이가 2 이하이면 PASS입니다(공식 testbench 허용오차).

    python3 isc/operations/image_gaussian/examples/check_output.py datasets/image_gaussian/ocr_denoise.emu.bin
    python3 isc/operations/image_gaussian/examples/check_output.py 출력파일 --input 입력파일 --sigma-milli 500 --png 폴더
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py)
from baseline import compute  # noqa: E402

DEFAULT_INPUT = "datasets/image_gaussian/ocr_scan_256.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="image_gaussian 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--sigma-milli", type=int, default=500, help="module.yaml의 sigma_milli와 같은 값")
    parser.add_argument("--png", metavar="DIR", help="입력·FPGA 결과를 PNG로 저장할 폴더")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    ref = compute(inp, {"sigma_milli": args.sigma_milli})
    d = np.abs(np.frombuffer(got[8:], np.uint8).astype(int) - np.frombuffer(ref[8:], np.uint8))
    print("header", got[:8] == ref[:8], "max_diff", d.max(), "over_2", int((d > 2).sum()))

    if args.png:
        import cv2
        rows, cols = struct.unpack("<II", got[:8])
        cv2.imwrite(f"{args.png}/input.png", np.frombuffer(inp[8:], np.uint8).reshape(rows, cols))
        cv2.imwrite(f"{args.png}/fpga.png", np.frombuffer(got[8:], np.uint8).reshape(rows, cols))
        print(f"PNG 저장: {args.png}/input.png, fpga.png")

    ok = got[:8] == ref[:8] and d.max() <= 2
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
