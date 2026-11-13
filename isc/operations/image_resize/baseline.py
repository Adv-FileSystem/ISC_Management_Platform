"""CPU reference for image_resize: OpenCV resize with INTER_LINEAR."""
from __future__ import annotations

from isc.operations.baseline import BaselineUnavailable
from resize_format import HEADER, check

PROBE_INPUT = HEADER.pack(2, 2) + bytes([0, 100, 200, 255])


def compute(data: bytes, parameters: dict) -> bytes:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise BaselineUnavailable("the image_resize CPU baseline needs `opencv-python` and `numpy`") from exc
    rows, cols = check(data)
    out_rows, out_cols = int(parameters["out_rows"]), int(parameters["out_cols"])
    image = np.frombuffer(data, dtype=np.uint8, offset=HEADER.size).reshape(rows, cols)
    out = cv2.resize(image, (out_cols, out_rows), interpolation=cv2.INTER_LINEAR)
    return HEADER.pack(out_rows, out_cols) + out.tobytes()
