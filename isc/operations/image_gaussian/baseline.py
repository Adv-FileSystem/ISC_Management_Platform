"""CPU reference for image_gaussian: OpenCV GaussianBlur, 3x3, zero border (BORDER_CONSTANT)."""
from __future__ import annotations

from isc.operations.baseline import BaselineUnavailable
from gaussian_format import HEADER, check

PROBE_INPUT = HEADER.pack(2, 3) + bytes([0, 50, 100, 150, 200, 250])


def compute(data: bytes, parameters: dict) -> bytes:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise BaselineUnavailable("the image_gaussian CPU baseline needs `opencv-python` and `numpy`") from exc
    rows, cols = check(data)
    sigma = int(parameters["sigma_milli"]) / 1000.0
    image = np.frombuffer(data, dtype=np.uint8, offset=HEADER.size).reshape(rows, cols)
    out = cv2.GaussianBlur(image, (3, 3), sigma, sigmaY=sigma, borderType=cv2.BORDER_CONSTANT)
    return HEADER.pack(rows, cols) + out.tobytes()
