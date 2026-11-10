#!/usr/bin/env python3
"""image_bicubic 결과 확인 (README 3).

(1) 정답 기준: FPGA 출력이 공식 커널 소스의 C 시뮬레이션 결과와 바이트까지 같은지
(2) 참고:     지금 쓰는 CPU 코드 cv2.resize(INTER_CUBIC)와 화소 값이 얼마나 다른지
              (bicubic 정의가 달라 같지 않은 것이 정상입니다. README 1.2)

    python3 isc/operations/image_bicubic/examples/check_output.py datasets/image_bicubic/train_256.emu.bin
    python3 isc/operations/image_bicubic/examples/check_output.py datasets/image_bicubic/train_256.hw.bin --png datasets/image_bicubic
"""
import argparse
import struct
import time

import cv2
import numpy as np

DEFAULT_INPUT = "datasets/image_bicubic/train_512.bin"
DEFAULT_CSIM = "datasets/image_bicubic/train_256.csim.raw"


def read_gray8(path: str) -> np.ndarray:
    """헤더(u32 rows, u32 cols) + u8 화소 파일을 2차원 배열로 읽음."""
    raw = open(path, "rb").read()
    rows, cols = struct.unpack("<II", raw[:8])
    return np.frombuffer(raw[8:], np.uint8).reshape(rows, cols)


def main() -> None:
    parser = argparse.ArgumentParser(description="image_bicubic 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--csim", default=DEFAULT_CSIM, help="C 시뮬레이션 출력 (헤더 없는 화소)")
    parser.add_argument("--png", metavar="DIR", help="입력·FPGA 결과·OpenCV 결과를 PNG로 저장할 폴더")
    args = parser.parse_args()

    source = read_gray8(args.input)
    fpga = read_gray8(args.output)
    rows, cols = fpga.shape
    csim = np.fromfile(args.csim, np.uint8).reshape(rows, cols)

    print(f"size: {source.shape[0]}x{source.shape[1]} -> {rows}x{cols}")
    print("(1) fpga vs 공식 C 시뮬레이션:", "MATCH" if np.array_equal(fpga, csim) else "MISMATCH")

    start = time.perf_counter()
    cpu = cv2.resize(source, (cols, rows), interpolation=cv2.INTER_CUBIC)     # 지금 쓰는 CPU 코드
    elapsed_ms = (time.perf_counter() - start) * 1000
    diff = np.abs(fpga.astype(int) - cpu.astype(int))
    print(f"(2) fpga vs cv2.resize INTER_CUBIC ({elapsed_ms:.3f} ms): "
          f"최대 차이 {diff.max()}, 평균 차이 {diff.mean():.2f}, 같은 화소 {(diff == 0).mean() * 100:.1f}%")

    if args.png:
        cv2.imwrite(f"{args.png}/input.png", source)
        cv2.imwrite(f"{args.png}/fpga.png", fpga)
        cv2.imwrite(f"{args.png}/opencv.png", cpu)
        print(f"PNG 저장: {args.png}/input.png, fpga.png, opencv.png")

    raise SystemExit(0 if np.array_equal(fpga, csim) else 1)


if __name__ == "__main__":
    main()
