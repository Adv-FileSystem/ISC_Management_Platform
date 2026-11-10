#!/usr/bin/env python3
"""CPU checks for the two Codec demos whose official hosts compare nothing (CO-1, CO-2).

    python3 tools/codec_check.py jpeg   <t0.jpg> <t0.yuv written by the jpegDec host>
    python3 tools/codec_check.py resize <t0.raw> <t0_resized.raw> --src 512x512 --dst 64x64

jpeg:   the host writes planar Y, U, V padded to whole MCUs. The Y plane is compared with the luma
        that libjpeg (through Pillow, decoder output colour space YCbCr) produces for the same file.
        Chroma is left out: libjpeg upsamples it, the kernel does not.
resize: the host reads the first src_w*src_h bytes of the raw file as an 8-bit image (as the
        official host does) and the output is compared with OpenCV INTER_CUBIC.
Both print max |diff|, mean |diff| and PSNR as one JSON line. PASS = PSNR >= 40 dB (no official
tolerance exists for these demos; 40 dB means the two images are visually identical).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pylib"))
import numpy as np  # noqa: E402

PSNR_PASS = 40.0


def stats(device: np.ndarray, cpu: np.ndarray) -> dict:
    diff = np.abs(device.astype(int) - cpu.astype(int))
    mse = float((diff ** 2).mean())
    psnr = math.inf if mse == 0 else 10 * math.log10(255 ** 2 / mse)
    return {"pixels": int(diff.size), "max_abs_diff": int(diff.max()), "mean_abs_diff": round(float(diff.mean()), 4),
            "pixels_differing": int((diff > 0).sum()), "psnr_db": round(psnr, 2) if psnr != math.inf else "inf",
            "verdict": "PASS" if psnr >= PSNR_PASS else "FAIL"}


def check_jpeg(jpg: Path, yuv: Path) -> dict:
    from PIL import Image
    image = Image.open(jpg)
    width, height = image.size
    sampling = image.layer[0][1:3] if hasattr(image, "layer") else (2, 2)   # (h, v) of the Y component
    mcu_w, mcu_h = 8 * sampling[0], 8 * sampling[1]
    pad_w, pad_h = -(-width // mcu_w) * mcu_w, -(-height // mcu_h) * mcu_h
    raw = np.frombuffer(yuv.read_bytes(), dtype=np.uint8)
    device_y = raw[:pad_w * pad_h].reshape(pad_h, pad_w)[:height, :width]
    image.draft("YCbCr", image.size)            # ask libjpeg for YCbCr output, no colour conversion
    cpu_y = np.asarray(image.convert("YCbCr"))[:, :, 0] if image.mode != "YCbCr" else np.asarray(image)[:, :, 0]
    report = {"check": "jpeg_y_plane", "file": jpg.name, "size": f"{width}x{height}",
              "y_sampling": list(sampling), "yuv_bytes": int(raw.size), "decoder_mode": image.mode}
    report.update(stats(device_y, cpu_y))
    return report


def check_resize(src: Path, dst: Path, src_size: str, dst_size: str) -> dict:
    import cv2
    sw, sh = (int(v) for v in src_size.split("x"))
    dw, dh = (int(v) for v in dst_size.split("x"))
    image = np.frombuffer(src.read_bytes()[:sw * sh], dtype=np.uint8).reshape(sh, sw)
    device = np.frombuffer(dst.read_bytes()[:dw * dh], dtype=np.uint8).reshape(dh, dw)
    cpu = cv2.resize(image, (dw, dh), interpolation=cv2.INTER_CUBIC)
    report = {"check": "resize_bicubic", "src": f"{sw}x{sh}", "dst": f"{dw}x{dh}", "reference": "cv2.INTER_CUBIC"}
    report.update(stats(device, cpu))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["jpeg", "resize"])
    parser.add_argument("source")
    parser.add_argument("device_output")
    parser.add_argument("--src", default="512x512")
    parser.add_argument("--dst", default="64x64")
    args = parser.parse_args()
    if args.kind == "jpeg":
        report = check_jpeg(Path(args.source), Path(args.device_output))
    else:
        report = check_resize(Path(args.source), Path(args.device_output), args.src, args.dst)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
