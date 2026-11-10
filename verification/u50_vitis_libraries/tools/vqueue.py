#!/usr/bin/env python3
"""Vendor reference-flow runner for the U50 Vitis Libraries verification.

build: runs `make all TARGET=hw PLATFORM=<u50>` for each job, several at once.
run:   runs `make run TARGET=hw PLATFORM=<u50>` for each job, one at a time, while
       holding the same device lock file the ISC platform uses
       (artifacts/locks/xrt-hw-0.lock), so vendor runs and `isc run` never share the card.

Every build and run appends one JSON line to results/builds.jsonl or results/runs.jsonl
with the command, start/end time, duration, exit code and log path.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # verification/u50_vitis_libraries
PLATFORM_ROOT = HERE.parent.parent                       # ISC_Management_Platform
VITIS_SETTINGS = "/home/hun/dev/workspace/2025.1/Vitis/settings64.sh"
XRT_SETUP = "/opt/xilinx/xrt/setup.sh"
PLATFORM = "xilinx_u50_gen3x16_xdma_5_202210_1"
DEVICE_LOCK = PLATFORM_ROOT / "artifacts" / "locks" / "xrt-hw-0.lock"
LOG_DIR = HERE / "logs"
RUN_TIMEOUT_S = 1800
RESULTS = HERE / "results"
_write_lock = threading.Lock()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load_jobs(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text())
    return {job["id"]: job for job in data["jobs"]}


def append(name: str, record: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with _write_lock, open(RESULTS / name, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def shell(job: dict, make_goal: str, prefix: str = "") -> str:
    extra = " ".join(job.get("make_vars", []))
    return (
        f"source {VITIS_SETTINGS} >/dev/null 2>&1; source {XRT_SETUP} >/dev/null 2>&1; "
        f"export PYTHONPATH={HERE}/.pylib; "
        f"cd {job['root']}/{job['dir']} && "
        f"{prefix}make {make_goal} TARGET=hw PLATFORM={PLATFORM} SHELL=/bin/bash {extra}"
    )


def execute(job: dict, phase: str, command: str) -> dict:
    stamp = time.strftime("%m%d_%H%M%S")
    log_path = LOG_DIR / phase / f"{job['id']}_{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started, t0 = now(), time.monotonic()
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"$ {command}\n")
        log.flush()
        proc = subprocess.run(["bash", "-c", command], stdout=log, stderr=subprocess.STDOUT)
    secs = round(time.monotonic() - t0, 1)
    tail = log_path.read_text(errors="replace")[-6000:]
    record = {
        "id": job["id"], "library": job.get("library"), "operation": job.get("operation"),
        "phase": phase, "dir": job["dir"], "root": job["root"], "command": command,
        "started_at": started, "finished_at": now(), "seconds": secs,
        "exit_code": proc.returncode, "log": str(log_path.relative_to(HERE)),
    }
    if phase == "run":
        record["verdict_lines"] = [
            line.strip() for line in tail.splitlines()
            if re.search(r"(?i)pass|fail|error|mismatch|correct", line)
        ][-12:]
    return record


def do_build(job: dict) -> dict:
    record = execute(job, "build", shell(job, "all"))
    append("builds.jsonl", record)
    print(f"[build] {job['id']}: exit {record['exit_code']} in {record['seconds']}s", flush=True)
    return record


def do_run(job: dict) -> dict:
    DEVICE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(DEVICE_LOCK, "a+") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        fd.seek(0)
        fd.truncate()
        # Same note shape as isc/lifecycle/devices.py, so `isc status` can name the holder.
        fd.write(json.dumps({"run_id": f"vqueue:{job['id']}", "pid": os.getpid(),
                             "since": now(), "purpose": "vendor reference run"}) + "\n")
        fd.flush()
        try:
            # A hung host must not keep the card (and the lock) forever.
            record = execute(job, "run", shell(job, "run", prefix=f"timeout -k 30 {RUN_TIMEOUT_S} "))
        finally:
            fd.seek(0)
            fd.truncate()
            fcntl.flock(fd, fcntl.LOCK_UN)
    append("runs.jsonl", record)
    print(f"[run] {job['id']}: exit {record['exit_code']} in {record['seconds']}s", flush=True)
    for line in record.get("verdict_lines", []):
        print("    ", line)
    return record


def do_cmd(run_id: str, cwd: str, command: str, note: str = "") -> dict:
    """Run one extra scenario step (any shell command) under the device lock, logged like a run."""
    job = {"id": run_id, "root": cwd, "dir": ".", "library": None, "operation": note or None}
    DEVICE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    full = (f"source {VITIS_SETTINGS} >/dev/null 2>&1; source {XRT_SETUP} >/dev/null 2>&1; "
            f"export PYTHONPATH={HERE}/.pylib; cd {cwd} && timeout -k 30 {RUN_TIMEOUT_S} bash -c {json.dumps(command)}")
    with open(DEVICE_LOCK, "a+") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        fd.seek(0)
        fd.truncate()
        fd.write(json.dumps({"run_id": f"vqueue:{run_id}", "pid": os.getpid(),
                             "since": now(), "purpose": "vendor reference run"}) + "\n")
        fd.flush()
        try:
            record = execute(job, "run", full)
        finally:
            fd.seek(0)
            fd.truncate()
            fcntl.flock(fd, fcntl.LOCK_UN)
    record["note"] = note
    append("runs.jsonl", record)
    print(f"[cmd] {run_id}: exit {record['exit_code']} in {record['seconds']}s", flush=True)
    for line in record.get("verdict_lines", []):
        print("    ", line)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["build", "run", "list", "cmd"])
    parser.add_argument("--id", help="cmd: record id")
    parser.add_argument("--cwd", help="cmd: working directory")
    parser.add_argument("--note", default="", help="cmd: what this step checks")
    parser.add_argument("--command", help="cmd: shell command")
    parser.add_argument("--jobs", default=str(HERE / "jobs.json"))
    parser.add_argument("--only", default="", help="comma-separated job ids, in order")
    parser.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args()

    if args.phase == "cmd":
        return 0 if do_cmd(args.id, args.cwd, args.command, args.note)["exit_code"] == 0 else 1
    jobs = load_jobs(Path(args.jobs))
    chosen = [jobs[i] for i in args.only.split(",") if i] if args.only else list(jobs.values())
    if args.phase == "list":
        for job in chosen:
            print(job["id"], job["dir"])
        return 0
    if args.phase == "build":
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            results = list(pool.map(do_build, chosen))
    else:
        results = [do_run(job) for job in chosen]
    return 0 if all(r["exit_code"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
