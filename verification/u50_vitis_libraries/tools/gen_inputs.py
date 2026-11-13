#!/usr/bin/env python3
"""Deterministic inputs for the platform-track scenarios (fixed seeds, so anyone can regenerate them).

Writes into verification/u50_vitis_libraries/inputs/:
  bytes_1b.bin, bytes_3b.bin      edge cases: 1 byte, 3 bytes
  bytes_256k.bin                  256 KiB random (validated run: hw_emu reference + hw)
  bytes_64m.bin                   64 MiB random (hw only, checked against the CPU reference)
  text_8m.bin                     8 MiB of repeated English-like text (compressible)
  gemv_64x130.bin                 GEMV m=64 n=130 alpha=3 beta=-2 (validated run; n not a multiple of 4)
  gemv_1024x4096.bin              GEMV m=1024 n=4096 alpha=1 beta=1 (hw only, 16 MiB matrix)
  gray_128.bin, gray_512.bin      Vitis Vision sample images 128x128.png / 512x512.png as 8-bit gray
  gray_1080p.bin                  1920x1080 center crop of Vitis Vision 4k.jpg, 8-bit gray
  gray_4k.bin                     Vitis Vision 4k.jpg (3840x2160), 8-bit gray
Values for GEMV are in -124..124 like the library's own L1 gemv test (profile.json valueRange).
Image inputs need OpenCV for Python (verification/u50_vitis_libraries/.pylib).
"""
from __future__ import annotations

import random
import struct
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "inputs"
VISION_DATA = Path("/home/hun/dev/workspace/vitis_libs_u50v/vision/data")
sys.path.insert(0, str(OUT.parent / ".pylib"))


def gemv(path: Path, m: int, n: int, alpha: int, beta: int, seed: int) -> None:
    import numpy as np
    rng = np.random.default_rng(seed)
    a = rng.integers(-124, 125, (m, n), dtype=np.int32)
    x = rng.integers(-124, 125, n, dtype=np.int32)
    y = rng.integers(-124, 125, m, dtype=np.int32)
    path.write_bytes(struct.pack("<IIii", m, n, alpha, beta) + a.astype("<i4").tobytes()
                     + x.astype("<i4").tobytes() + y.astype("<i4").tobytes())


def gray(path: Path, image) -> None:
    path.write_bytes(struct.pack("<II", *image.shape) + image.tobytes())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261104)
    (OUT / "bytes_1b.bin").write_bytes(b"\x5a")
    (OUT / "bytes_3b.bin").write_bytes(b"abc")
    (OUT / "bytes_256k.bin").write_bytes(rng.randbytes(256 * 1024))
    (OUT / "bytes_64m.bin").write_bytes(rng.randbytes(64 * 1024 * 1024))
    words = ("in storage computing offloads data intensive work to the device near the data "
             "so that only results cross the bus ").split()
    text = " ".join(rng.choice(words) for _ in range(2_000_000)).encode()
    (OUT / "text_8m.bin").write_bytes(text[: 8 * 1024 * 1024])
    gemv(OUT / "gemv_64x130.bin", 64, 130, 3, -2, seed=1)
    gemv(OUT / "gemv_1024x4096.bin", 1024, 4096, 1, 1, seed=2)
    import cv2
    g128 = cv2.imread(str(VISION_DATA / "128x128.png"), cv2.IMREAD_GRAYSCALE)
    g512 = cv2.imread(str(VISION_DATA / "512x512.png"), cv2.IMREAD_GRAYSCALE)
    g4k = cv2.imread(str(VISION_DATA / "4k.jpg"), cv2.IMREAD_GRAYSCALE)
    gray(OUT / "gray_128.bin", g128)
    gray(OUT / "gray_512.bin", g512)
    gray(OUT / "gray_4k.bin", g4k)
    top, left = (g4k.shape[0] - 1080) // 2, (g4k.shape[1] - 1920) // 2
    gray(OUT / "gray_1080p.bin", g4k[top:top + 1080, left:left + 1920].copy())
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}\t{path.stat().st_size}")


if __name__ == "__main__":
    main()
