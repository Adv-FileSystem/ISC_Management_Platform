#!/usr/bin/env python3
"""One memory-aware scheduler for every hw build on this host (vendor `make all` and platform `isc run --target hw`).

Why: a hw link starts up to 8 Vivado synthesis jobs (about 20 GB at its peak). On 2026-11-04 four
vendor builds plus two platform builds started together and the kernel's OOM killer stopped two
HLS runs (gqeJoin, dup_match). So the next build starts only when
  - fewer than --max-total hw builds are running (vendor + platform, counted from the process list),
  - MemAvailable >= --min-free-gb, and
  - --gap-min minutes passed since the previous start (peaks do not overlap).
Vendor builds get VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4" through the environment
(the Makefiles append to it with +=), which halves their synthesis peak.

Builds already running when the scheduler starts are adopted: it waits for them and records them.

    python3 tools/sched.py --platform u50v_crc32:inputs/bytes_256k.bin ... --vendor db_gqe_join,da_dup_match,...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vqueue  # noqa: E402
from pchain import ENV_PREFIX, HERE, PLATFORM_ROOT, mem_available_gb  # noqa: E402

VENDOR_LDFLAGS = "--vivado.synth.jobs 4 --vivado.impl.jobs 4"


def log(message: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')} {message}", flush=True)


def processes(pattern: str) -> list[tuple[int, str, str]]:
    """(pid, cwd, cmdline) of processes whose command line matches pattern."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if re.search(pattern, cmd):
                found.append((int(entry.name), os.readlink(entry / "cwd"), cmd))
        except OSError:
            continue
    return found


def vendor_running() -> dict[str, int]:
    """Running vendor builds as {job dir path: make pid} (one entry per build directory)."""
    running: dict[str, int] = {}
    for pid, cwd, _ in processes(r"^make all TARGET=hw"):
        running.setdefault(cwd, pid)
    return running


def platform_running() -> list[str]:
    return [cmd for _, _, cmd in processes(r"^/usr/bin/python3 -m isc run \S+ .*--target hw(\s|$)")]


def module_busy(module: str) -> bool:
    return bool(processes(rf"^/usr/bin/python3 -m isc run {re.escape(module)} "))


def build_verdict(job: dict, log_path: Path | None) -> int:
    root = Path(job["root"]) / job["dir"]
    text = log_path.read_text(errors="replace")[-4000:] if log_path and log_path.exists() else ""
    if re.search(r"make: \*\*\* .*Error", text):
        return 2
    return 0 if list(root.glob(f"build_dir.hw.{vqueue.PLATFORM}/*.xclbin")) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", nargs="*", default=[], help="module:input[:extra args]")
    parser.add_argument("--vendor", default="", help="comma-separated job ids (jobs.json)")
    parser.add_argument("--max-total", type=int, default=5)
    parser.add_argument("--min-free-gb", type=float, default=30)
    parser.add_argument("--gap-min", type=float, default=8)
    parser.add_argument("--no-adopt", action="store_true",
                        help="leave running vendor builds to the scheduler that started or adopted them")
    args = parser.parse_args()

    jobs = vqueue.load_jobs(vqueue.HERE / "jobs.json")
    by_dir = {str(Path(j["root"]) / j["dir"]): j for j in jobs.values()}
    platform_queue = list(args.platform)
    vendor_queue = [j for j in args.vendor.split(",") if j]

    # Adopt vendor builds that are already running (started by an earlier queue).
    adopted: dict[str, tuple[dict, float, Path | None]] = {}
    for cwd in ([] if args.no_adopt else vendor_running()):
        job = by_dir.get(cwd)
        if job is None:
            continue
        logs = sorted((vqueue.LOG_DIR / "build").glob(f"{job['id']}_*.log"))
        started = logs[-1].stat().st_ctime if logs else time.time()
        adopted[job["id"]] = (job, started, logs[-1] if logs else None)
        vendor_queue = [j for j in vendor_queue if j != job["id"]]
        log(f"adopt vendor {job['id']}")

    mine: dict[str, tuple[subprocess.Popen, dict, float, Path]] = {}
    last_start = time.time()           # whatever is running now started recently
    while platform_queue or vendor_queue or mine or adopted:
        running_dirs = vendor_running()
        for job_id, (job, started, log_path) in list(adopted.items()):
            if str(Path(job["root"]) / job["dir"]) not in running_dirs:
                code = build_verdict(job, log_path)
                vqueue.append("builds.jsonl", {
                    "id": job_id, "library": job.get("library"), "operation": job.get("operation"),
                    "phase": "build", "dir": job["dir"], "root": job["root"], "adopted": True,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
                    "finished_at": vqueue.now(), "seconds": round(time.time() - started, 1),
                    "exit_code": code, "log": str(log_path.relative_to(HERE)) if log_path else None})
                log(f"adopted vendor {job_id} finished: exit {code}")
                del adopted[job_id]
        for job_id, (proc, job, started, log_path) in list(mine.items()):
            if proc.poll() is not None:
                vqueue.append("builds.jsonl", {
                    "id": job_id, "library": job.get("library"), "operation": job.get("operation"),
                    "phase": "build", "dir": job["dir"], "root": job["root"],
                    "env": {"VPP_LDFLAGS": VENDOR_LDFLAGS},
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
                    "finished_at": vqueue.now(), "seconds": round(time.time() - started, 1),
                    "exit_code": proc.returncode, "log": str(log_path.relative_to(HERE))})
                log(f"vendor {job_id} finished: exit {proc.returncode}")
                del mine[job_id]

        total = len(vendor_running()) + len(platform_running())
        free = mem_available_gb()
        if (platform_queue or vendor_queue) and total < args.max_total and free >= args.min_free_gb \
                and time.time() - last_start >= args.gap_min * 60:
            started_one = False
            for spec in list(platform_queue):
                module, input_path, *extra = spec.split(":")
                if module_busy(module):
                    continue
                platform_queue.remove(spec)
                out = HERE / "outputs" / f"{module}_{Path(input_path).stem}.hw.bin"
                plog = HERE / "logs" / "platform" / f"{module}_hw.log"
                command = (f"{ENV_PREFIX} cd {PLATFORM_ROOT} && /usr/bin/python3 -m isc run {module} "
                           f"--input {HERE / input_path} --target hw --output {out} --progress {' '.join(extra)}")
                with open(plog, "a") as fh:
                    fh.write(f"$ isc run {module} --input {input_path} --target hw {' '.join(extra)}\n")
                    subprocess.Popen(["bash", "-c", command], stdout=fh, stderr=subprocess.STDOUT,
                                     start_new_session=True)
                log(f"start platform {module} (running {total}, free {free:.0f} GB)")
                started_one = True
                break
            if not started_one and vendor_queue:
                job = jobs[vendor_queue.pop(0)]
                stamp = time.strftime("%m%d_%H%M%S")
                vlog = vqueue.LOG_DIR / "build" / f"{job['id']}_{stamp}.log"
                command = f"export VPP_LDFLAGS='{VENDOR_LDFLAGS}'; " + vqueue.shell(job, "all")
                fh = open(vlog, "w")
                fh.write(f"$ {command}\n")
                fh.flush()
                proc = subprocess.Popen(["bash", "-c", command], stdout=fh, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                mine[job["id"]] = (proc, job, time.time(), vlog)
                log(f"start vendor {job['id']} (running {total}, free {free:.0f} GB)")
                started_one = True
            if started_one:
                last_start = time.time()
        time.sleep(30)
    log("all builds done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
