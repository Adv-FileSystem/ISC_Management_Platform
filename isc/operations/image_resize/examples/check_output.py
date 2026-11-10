#!/usr/bin/env python3
"""image_resize 결과 확인 (README 3).

FPGA 출력을 지금 쓰는 CPU 코드 OpenCV `cv2.resize(INTER_LINEAR)`(연산 폴더의 baseline.py)에 post() 를 적용한
값과 비교합니다. 장치는 고정소수점 보간이라 줄인 값이 OpenCV와 5까지 다를 수 있으므로, FPGA 화소가
"OpenCV 값 ±5 중 하나에 post() 를 적용한 값"이면 그 화소는 맞습니다. 모든 화소가 맞으면 PASS입니다.
post() 는 모듈의 logic.hpp 에 쓴 isc_post 와 같은 규칙이어야 합니다. 로직 파일 없이 만든 모듈은
--original 을 붙여 OpenCV 결과와 그대로(차이 5 이하) 비교합니다.
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

TOLERANCE = 5


# [바꾸는 곳] logic.hpp 의 isc_post 와 같은 규칙
def post(px: int) -> int:
    return 255 if px > 215 else px + 40


def main() -> None:
    parser = argparse.ArgumentParser(description="image_resize 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", required=True, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--png", metavar="DIR", help="FPGA 결과를 PNG로 저장할 폴더")
    parser.add_argument("--original", action="store_true", help="로직 파일 없이 만든 모듈: post() 없이 비교")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    out_rows, out_cols = struct.unpack("<II", got[:8])
    ref = compute(inp, {"out_rows": out_rows, "out_cols": out_cols})
    table = np.arange(256) if args.original else np.array([post(v) for v in range(256)])
    fpga = np.frombuffer(got[8:], np.uint8).astype(int)
    cpu = np.frombuffer(ref[8:], np.uint8).astype(int)
    allowed = np.zeros(len(cpu), bool)
    for step in range(-TOLERANCE, TOLERANCE + 1):
        allowed |= fpga == table[np.clip(cpu + step, 0, 255)]
    outside = int((~allowed).sum())
    print("size", out_rows, out_cols, "header", got[:8] == ref[:8], "outside_tolerance", outside)

    if args.png:
        import cv2
        name = Path(args.output).stem
        cv2.imwrite(f"{args.png}/{name}.png", np.frombuffer(got[8:], np.uint8).reshape(out_rows, out_cols))
        print(f"PNG 저장: {args.png}/{name}.png")

    ok = got[:8] == ref[:8] and outside == 0
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
