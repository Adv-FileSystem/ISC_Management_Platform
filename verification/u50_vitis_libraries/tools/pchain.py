#!/usr/bin/env python3
"""Start platform (`isc run ... --target hw`) jobs without running the host out of memory.

Each hw link starts up to 8 Vivado out-of-context synthesis jobs (about 20 GB at its peak), and
the vendor builds share the same 94 GB host. This launcher starts the next job only when
- fewer than --max-running of its own jobs are running,
- at least --gap-min minutes passed since its last start (so two peaks do not overlap), and
- MemAvailable is at least --min-free-gb.

    python3 tools/pchain.py u50v_adler32:inputs/bytes_256k.bin u50v_crc32:inputs/bytes_256k.bin ...

Each job writes logs/platform/<module>_hw.log and outputs/<module>_<input>.hw.bin.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PLATFORM_ROOT = HERE.parent.parent
ENV_PREFIX = (
    "source /home/hun/dev/workspace/2025.1/Vitis/settings64.sh >/dev/null 2>&1; "
    "source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1; "
    "export VITIS_LIBRARIES=/home/hun/dev/workspace/vitis_libs_u50v; "
    f"export PYTHONPATH={HERE}/.pylib; "
)


def mem_available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1048576
    return 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs", nargs="+", help="module:input[:extra isc run args]")
    parser.add_argument("--max-running", type=int, default=2)
    parser.add_argument("--gap-min", type=float, default=20)
    parser.add_argument("--min-free-gb", type=float, default=35)
    args = parser.parse_args()

    pending = list(args.jobs)
    running: list[tuple[str, subprocess.Popen]] = []
    last_start = 0.0
    failures = 0
    while pending or running:
        for item in list(running):
            if item[1].poll() is not None:
                running.remove(item)
                failures += item[1].returncode != 0
                print(f"{time.strftime('%H:%M:%S')} done {item[0]} exit {item[1].returncode}", flush=True)
        ready = (pending and len(running) < args.max_running
                 and time.time() - last_start >= args.gap_min * 60
                 and mem_available_gb() >= args.min_free_gb)
        if ready:
            spec = pending.pop(0)
            module, input_path, *extra = spec.split(":")
            out = HERE / "outputs" / f"{module}_{Path(input_path).stem}.hw.bin"
            log = HERE / "logs" / "platform" / f"{module}_hw.log"
            command = (f"{ENV_PREFIX} cd {PLATFORM_ROOT} && /usr/bin/python3 -m isc run {module} "
                       f"--input {HERE / input_path} --target hw --output {out} --progress {' '.join(extra)}")
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a") as fh:
                fh.write(f"$ isc run {module} --input {input_path} --target hw {' '.join(extra)}\n")
                proc = subprocess.Popen(["bash", "-c", command], stdout=fh, stderr=subprocess.STDOUT)
            running.append((module, proc))
            last_start = time.time()
            print(f"{time.strftime('%H:%M:%S')} start {module} (free {mem_available_gb():.0f} GB)", flush=True)
        time.sleep(30)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
