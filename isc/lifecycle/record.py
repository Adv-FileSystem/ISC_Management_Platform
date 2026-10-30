"""Run records: one JSON object per line in artifacts/records.jsonl.

Append-only and human-greppable on purpose -- a run record must survive the
process that produced it, and must not need a database to read back.
"""
from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from isc.lifecycle.workspace import Workspace

CSV_COLUMNS = [
    "run_id", "started_at", "module", "version", "target", "backend", "device",
    "status", "validation_passed", "input_bytes", "output_bytes",
    "total_ms", "kernel_ms", "host_to_device_ms", "device_to_host_ms", "throughput_mbps",
]


# Set for the reference run a parent run starts (isc/lifecycle.py): the child sees its
# parent's run id here, and says so when it reserves a device.
PARENT_RUN_ENV = "ISC_PARENT_RUN_ID"


def new_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def append(workspace: Workspace, record: dict[str, Any]) -> Path:
    path = workspace.records_path
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str, ensure_ascii=False)
    # Single write of one line: concurrent runs interleave records, never
    # corrupt each other's.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def read_all(workspace: Workspace, module_id: str | None = None,
             limit: int | None = None) -> list[dict[str, Any]]:
    path = workspace.records_path
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if module_id and record.get("module") != module_id:
            continue
        records.append(record)
    records.reverse()  # newest first
    return records[:limit] if limit else records


def get(workspace: Workspace, run_id: str) -> dict[str, Any] | None:
    for record in read_all(workspace):
        if record.get("run_id") == run_id:
            return record
    return None


def to_csv(records: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        flat = dict(record)
        flat.update(record.get("metrics") or {})
        validation = record.get("validation") or {}
        flat["validation_passed"] = validation.get("passed")
        device = record.get("device") or {}
        flat["device"] = device.get("name")
        writer.writerow(flat)
    return buffer.getvalue()
