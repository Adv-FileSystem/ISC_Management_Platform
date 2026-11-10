#!/usr/bin/env python3
"""Run each vendor job on the card as soon as its hw build has succeeded.

Watches results/builds.jsonl; for every job id in --only whose latest build record has exit code 0
and that has no run record yet, calls vqueue's do_run (device lock, results/runs.jsonl).
Stops when every listed job has either been run or has a failed build.

    python3 tools/autorun.py --only db_gqe_join,graph_twohop,...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vqueue  # noqa: E402


def latest(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                found[record["id"]] = record
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", required=True)
    parser.add_argument("--poll", type=int, default=60)
    args = parser.parse_args()

    wanted = [item for item in args.only.split(",") if item]
    jobs = vqueue.load_jobs(vqueue.HERE / "jobs.json")
    done: set[str] = set(latest(vqueue.RESULTS / "runs.jsonl"))
    while True:
        builds = latest(vqueue.RESULTS / "builds.jsonl")
        for job_id in wanted:
            build = builds.get(job_id)
            if job_id in done or build is None or build["exit_code"] != 0:
                continue
            print(time.strftime("%H:%M:%S"), "run", job_id, flush=True)
            vqueue.do_run(jobs[job_id])
            done.add(job_id)
        finished = [j for j in wanted if j in done or (j in builds and builds[j]["exit_code"] != 0)]
        if len(finished) == len(wanted):
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
