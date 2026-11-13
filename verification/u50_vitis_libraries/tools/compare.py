#!/usr/bin/env python3
"""Compare a platform-run output with the operation's CPU reference (P path, step "hw = CPU").

    python3 tools/compare.py <operation> <input.bin> <output.bin> [--param name=value ...] [--other out2.bin]

- Exact operations (adler32, crc32, gemv_i32) must match the CPU reference byte for byte.
- Image operations use the tolerance of the Vitis Vision official testbench for that function:
  GaussianBlur 3x3: |diff| <= 2 for every pixel (analyzeDiff(diff, 2)); resize bilinear: <= 5.
- --other compares two device outputs byte for byte (hw vs hw_emu).
Prints one JSON line and exits 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pylib"))
sys.path.insert(0, str(PLATFORM_ROOT))

TOLERANCE = {"image_gaussian": 2, "image_resize": 5}
DEFAULTS = {"image_gaussian": {"sigma_milli": 500}, "image_resize": {}, "gemv_i32": {},
            "adler32": {}, "crc32": {}}


def baseline(operation: str):
    directory = PLATFORM_ROOT / "isc" / "operations" / operation
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location(f"cmp_{operation}", directory / "baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compute


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--other")
    args = parser.parse_args()

    params = dict(DEFAULTS.get(args.operation, {}))
    for item in args.param:
        key, value = item.split("=", 1)
        params[key] = int(value)
    data = Path(args.input).read_bytes()
    device = Path(args.output).read_bytes()
    expected = baseline(args.operation)(data, params)
    report = {"operation": args.operation, "input": Path(args.input).name, "input_bytes": len(data),
              "output": Path(args.output).name, "params": params}

    if args.operation in TOLERANCE:
        import numpy as np
        same_shape = device[:8] == expected[:8] and len(device) == len(expected)
        report["shape_match"] = same_shape
        if same_shape:
            diff = np.abs(np.frombuffer(device[8:], np.uint8).astype(int)
                          - np.frombuffer(expected[8:], np.uint8).astype(int))
            report.update(pixels=int(diff.size), max_abs_diff=int(diff.max()),
                          pixels_differing=int((diff > 0).sum()),
                          pixels_over_tolerance=int((diff > TOLERANCE[args.operation]).sum()),
                          tolerance=TOLERANCE[args.operation])
            ok = report["pixels_over_tolerance"] == 0
        else:
            ok = False
    else:
        ok = device == expected
        report["exact_match"] = ok
        if len(expected) <= 64:
            report["device_hex"], report["cpu_hex"] = device.hex(), expected.hex()

    if args.other:
        other = Path(args.other).read_bytes()
        report["other"] = Path(args.other).name
        report["device_outputs_identical"] = other == device
        ok = ok and other == device

    report["verdict"] = "PASS" if ok else "FAIL"
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
