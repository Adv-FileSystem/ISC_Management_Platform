#!/usr/bin/env python3
"""image_sobel 입력 파일 만들기 (README 4.1).

    python3 isc/operations/image_sobel/examples/make_input.py                          # 예시 영상 → datasets/pscen/part_cam_256.bin
    python3 isc/operations/image_sobel/examples/make_input.py --image 사진.jpg -o 경로   # 내 사진(PNG·JPEG 등)을 변환

[바꾸는 곳]       make_sample() — 예시 영상 (부품 카메라 영상 256×256). 또는 --image로 실제 사진을 읽음 (회색조로 변환)
[고치지 않는 곳]  encode()      — 입력 형식: u32 rows, u32 cols + u8 pixels[rows][cols] (리틀 엔디안, 8비트 회색조 1채널)
                                 크기 한계: 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920
"""
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

DEFAULT_OUTPUT = "datasets/pscen/part_cam_256.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> np.ndarray:
    rng = np.random.default_rng(9); h = w = 256
    a = np.full((h, w), 40, np.uint8)                                  # 컨베이어 바탕 (어두움)
    cv2.circle(a, (128, 128), 90, 190, -1)                             # 원형 부품
    cv2.circle(a, (128, 128), 30, 40, -1)                              # 가운데 구멍
    for k in range(6):                                                 # 볼트 구멍 6개
        t = k * np.pi / 3; cv2.circle(a, (int(128 + 60 * np.cos(t)), int(128 + 60 * np.sin(t))), 8, 40, -1)
    cv2.rectangle(a, (200, 120), (240, 136), 190, -1)                  # 돌출부
    return np.clip(a.astype(np.int16) + rng.normal(0, 4, a.shape), 0, 255).astype(np.uint8)


def read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"영상을 읽지 못했습니다: {path}")
    return image


# ── [고치지 않는 곳] 입력 형식 ──────────────────────────────────────
def encode(image: np.ndarray) -> bytes:
    image = np.ascontiguousarray(image, dtype=np.uint8)
    if image.ndim != 2:
        raise ValueError("회색조(2차원) 영상만 받습니다")
    rows, cols = image.shape
    return struct.pack("<II", rows, cols) + image.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="image_sobel 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--image", help="실제 사진 파일 (PNG, JPEG 등)")
    args = parser.parse_args()
    image = read_image(args.image) if args.image else make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(image))
    print("ok", *image.shape)


if __name__ == "__main__":
    main()
