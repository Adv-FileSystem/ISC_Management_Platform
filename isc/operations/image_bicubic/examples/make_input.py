#!/usr/bin/env python3
"""image_bicubic 입력 파일 만들기 (README 4.1).

    python3 isc/operations/image_bicubic/examples/make_input.py                          # 예시 영상 → datasets/pscen/product_512.bin
    python3 isc/operations/image_bicubic/examples/make_input.py --image 사진.jpg -o 경로   # 내 사진(PNG·JPEG 등)을 변환

[바꾸는 곳]       make_sample() — 예시 영상 (상품 사진 512×512). 또는 --image로 실제 사진을 읽음 (회색조로 변환)
[고치지 않는 곳]  encode()      — 입력 형식: u32 rows, u32 cols + u8 pixels[rows][cols] (리틀 엔디안, 8비트 회색조 1채널)
                                 크기 한계: 한 변 1~8192 화소, 출력은 입력보다 크지 않아야 함
"""
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

DEFAULT_OUTPUT = "datasets/pscen/product_512.bin"


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample() -> np.ndarray:
    rng = np.random.default_rng(7); h = w = 512
    a = np.full((h, w), 245.0)
    cv2.ellipse(a, (256, 280), (150, 110), 0, 0, 360, 90, -1)           # 상품 몸체
    cv2.rectangle(a, (200, 120), (312, 190), 140, -1)                   # 뚜껑
    cv2.putText(a, "ISC", (190, 300), cv2.FONT_HERSHEY_SIMPLEX, 2.5, 230, 6)   # 라벨
    return np.clip(a + rng.normal(0, 3, a.shape), 0, 255).astype(np.uint8)


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
    parser = argparse.ArgumentParser(description="image_bicubic 입력 파일 만들기")
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
