"""CPU reference for image_sobel: OpenCV Sobel 3x3, 8-bit saturated output, zero border.

Same call as the Vitis Vision official testbench (xf_sobel_tb.cpp): cv::Sobel(in, out, CV_8U, 1, 0, 3,
scale 1, delta 0, BORDER_CONSTANT) and the same with (0, 1); that testbench requires zero difference.
"""
from __future__ import annotations

from isc.operations.baseline import BaselineUnavailable
from sobel_format import HEADER, check

PROBE_INPUT = HEADER.pack(2, 3) + bytes([0, 50, 100, 150, 200, 250])


def compute(data: bytes, parameters: dict) -> bytes:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise BaselineUnavailable("the image_sobel CPU baseline needs `opencv-python` and `numpy`") from exc
    rows, cols = check(data)
    image = np.frombuffer(data, dtype=np.uint8, offset=HEADER.size).reshape(rows, cols)
    gx = cv2.Sobel(image, cv2.CV_8U, 1, 0, ksize=3, scale=1, delta=0, borderType=cv2.BORDER_CONSTANT)
    gy = cv2.Sobel(image, cv2.CV_8U, 0, 1, ksize=3, scale=1, delta=0, borderType=cv2.BORDER_CONSTANT)
    return HEADER.pack(rows, cols) + gx.tobytes() + gy.tobytes()
