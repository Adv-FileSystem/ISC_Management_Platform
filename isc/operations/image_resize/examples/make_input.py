#!/usr/bin/env python3
"""image_resize 입력 파일 만들기 (README 2.1).

    python3 isc/operations/image_resize/examples/make_input.py                          # 예시 영상 2장 → datasets/image_resize/frame_384x216.bin, frame_1920x1080.bin
    python3 isc/operations/image_resize/examples/make_input.py --image 사진.jpg -o 경로   # 내 사진(PNG·JPEG 등)을 변환

[바꾸는 곳]       make_sample() — 예시 영상 (합성 장면). 또는 --image로 실제 사진을 읽음 (회색조로 변환)
[고치지 않는 곳]  encode()      — 입력 형식: u32 rows, u32 cols + u8 pixels[rows][cols] (리틀 엔디안, 8비트 회색조 1채널)
                                 크기 한계: 입력 최대 3840×2160. 축소는 2배까지 (rows ≤ 2·out_rows, cols ≤ 2·out_cols)
"""
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

SAMPLES = {"datasets/image_resize/frame_384x216.bin": (384, 216), "datasets/image_resize/frame_1920x1080.bin": (1920, 1080)}


# ── [바꾸는 곳] 데이터 ─────────────────────────────────────────────
def make_sample(w: int, h: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    a = (60 + 120 * xx / w + 40 * np.sin(yy / h * 6)).astype(np.float32)    # 배경 명암
    for _ in range(12):                                                    # 사물
        cx, cy, r = rng.integers(0, w), rng.integers(0, h), int(rng.integers(h // 20, h // 6))
        cv2.circle(a, (int(cx), int(cy)), r, float(rng.integers(0, 255)), -1)
    return np.clip(a + rng.normal(0, 6, a.shape), 0, 255).astype(np.uint8)


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


def write(path: str, image: np.ndarray) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(image))


def main() -> None:
    parser = argparse.ArgumentParser(description="image_resize 입력 파일 만들기")
    parser.add_argument("-o", "--output", help="--image와 함께: 저장 위치")
    parser.add_argument("--image", help="실제 사진 파일 (PNG, JPEG 등)")
    args = parser.parse_args()
    if args.image:
        if not args.output:
            parser.error("--image에는 -o가 필요합니다")
        image = read_image(args.image)
        write(args.output, image)
        print("ok", *image.shape)
        return
    for path, (w, h) in SAMPLES.items():
        write(path, make_sample(w, h))
    print("ok")


if __name__ == "__main__":
    main()
