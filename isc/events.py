"""Run events: what a long job is doing right now, as an append-only file anyone can read.

One JSONL file per run (`artifacts/events/<run_id>.jsonl`), one event per line, flushed and
fsynced as it is written -- the discipline of records.jsonl, for the same reason: it has to
survive the process that wrote it. Nothing here knows a vendor, a dashboard or HTTP; the
consumers (`isc status`, `isc watch`) read the file and nothing else.

Schema v1. Fields are only ever added; a reader ignores types and fields it does not know.

    every event   v, seq, ts, run_id, type
    run           state=started:  kind, module, target, backend, pid, host[, parent_run_id]
                  state=finished: status[, error]
    stage         stage, state=running|succeeded|failed|skipped[, duration_s][, detail]
    progress      stage, step[, current, total, unit, eta_s, message]
    heartbeat     stage
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from isc.devices import process_alive
from isc.record import PARENT_RUN_ENV
from isc.workspace import Workspace

SCHEMA = 1
HEARTBEAT_SECONDS = 30.0
LOST_AFTER_SECONDS = 3 * HEARTBEAT_SECONDS   # a run silent for this long, whose process is gone, is lost


class Sink:
    def write(self, event: dict[str, Any]) -> None:
        raise NotImplementedError


class MemorySink(Sink):
    """For tests: keeps the events instead of writing them."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class NullSink(Sink):
    def write(self, event: dict[str, Any]) -> None:
        pass


class FileSink(Sink):
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, default=str, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


class RunEvents:
    """The events of one run. Writing is serialised, so a heartbeat thread and the job's own
    thread can both write; and it can never fail the job it describes."""

    def __init__(self, run_id: str, sink: Sink, *, kind: str, module: str = "", target: str = "",
                 backend: str = "", parent_run_id: str | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 heartbeat_s: float = HEARTBEAT_SECONDS,
                 now: Callable[[], float] = time.time) -> None:
        self.run_id = run_id
        self._sink = sink
        self._on_event = on_event
        self._now = now
        self._heartbeat_s = heartbeat_s
        self._lock = threading.Lock()
        self._seq = 0
        self._broken = False
        self._finished = False
        self._running: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        started: dict[str, Any] = {
            "state": "started", "kind": kind, "module": module, "target": target,
            "backend": backend, "pid": os.getpid(), "host": socket.gethostname(),
        }
        if parent_run_id:
            started["parent_run_id"] = parent_run_id
        self._emit("run", **started)

    # -- the job's side -------------------------------------------------------
    def stage(self, name: str, **detail: Any) -> "StageHandle":
        """Begin a stage. `with events.stage("build") as stage:` ends it for you."""
        return StageHandle(self, name, detail)

    def skip(self, name: str, **detail: Any) -> None:
        """A stage that did not run at all (it is still named, so a reader sees why)."""
        fields: dict[str, Any] = {"stage": name, "state": "skipped"}
        if detail:
            fields["detail"] = detail
        self._emit("stage", **fields)

    def finish(self, status: str, error: str = "") -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._stop.set()
        fields: dict[str, Any] = {"state": "finished", "status": status}
        if error:
            fields["error"] = error
        self._emit("run", **fields)

    # -- internals ------------------------------------------------------------
    def _begin(self, name: str, detail: dict[str, Any]) -> None:
        with self._lock:
            self._running.append(name)
        self._ensure_heartbeat()
        fields: dict[str, Any] = {"stage": name, "state": "running"}
        if detail:
            fields["detail"] = detail
        self._emit("stage", **fields)

    def _end(self, name: str, state: str, duration_s: float, detail: dict[str, Any]) -> None:
        with self._lock:
            if name in self._running:
                self._running.remove(name)
        fields: dict[str, Any] = {"stage": name, "state": state, "duration_s": duration_s}
        if detail:
            fields["detail"] = detail
        self._emit("stage", **fields)

    def _ensure_heartbeat(self) -> None:
        if self._thread is not None or self._heartbeat_s <= 0:
            return
        self._thread = threading.Thread(target=self._beat, name="isc-heartbeat", daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        while not self._stop.wait(self._heartbeat_s):
            with self._lock:
                stage = self._running[-1] if self._running else None
            if stage is not None and not self._stop.is_set():
                self._emit("heartbeat", stage=stage)

    def _emit(self, type_: str, **fields: Any) -> None:
        with self._lock:
            self._seq += 1
            event = {
                "v": SCHEMA, "seq": self._seq,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self._now())),
                "run_id": self.run_id, "type": type_, **fields,
            }
            if not self._broken:
                try:
                    self._sink.write(event)
                except Exception as exc:    # losing progress must never lose the result
                    self._broken = True
                    print(f"warning: run events are not being recorded: {exc}", file=sys.stderr)
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception:
                    self._on_event = None


class StageHandle:
    def __init__(self, events: RunEvents, name: str, detail: dict[str, Any]) -> None:
        self._events = events
        self.name = name
        self._started = time.monotonic()
        self._ended = False
        events._begin(name, detail)

    def progress(self, step: str, *, current: int | None = None, total: int | None = None,
                 unit: str = "", eta_s: float | None = None, message: str = "") -> None:
        fields: dict[str, Any] = {"stage": self.name, "step": step}
        if current is not None:
            fields["current"] = current
        if total is not None:
            fields["total"] = total
        if unit:
            fields["unit"] = unit
        if eta_s is not None:
            fields["eta_s"] = round(eta_s)
        if message:
            fields["message"] = message
        self._events._emit("progress", **fields)

    def succeed(self, **detail: Any) -> None:
        self._end("succeeded", detail)

    def fail(self, error: str = "", **detail: Any) -> None:
        self._end("failed", {"error": error, **detail} if error else detail)

    def _end(self, state: str, detail: dict[str, Any]) -> None:
        if self._ended:
            return
        self._ended = True
        self._events._end(self.name, state, round(time.monotonic() - self._started, 3), detail)

    def __enter__(self) -> "StageHandle":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if exc_type is None:
            self.succeed()
        else:
            self.fail(f"{exc_type.__name__}: {exc}")
        return False


def events_path(workspace: Workspace, run_id: str) -> Path:
    return workspace.events_dir / f"{run_id}.jsonl"


def start_run(workspace: Workspace, run_id: str, *, kind: str, module: str = "",
              target: str = "", backend: str = "", sink: Sink | None = None,
              on_event: Callable[[dict[str, Any]], None] | None = None,
              **options: Any) -> RunEvents:
    """Open the events of one run: the file under artifacts/events/ unless a sink is given.

    A run another run started (the reference run `isc run` spawns) finds its parent's id in the
    environment; it is written as `parent_run_id`.
    """
    return RunEvents(
        run_id, sink if sink is not None else FileSink(events_path(workspace, run_id)),
        kind=kind, module=module, target=target, backend=backend,
        parent_run_id=os.environ.get(PARENT_RUN_ENV) or None, on_event=on_event, **options,
    )


def silent() -> RunEvents:
    """Events that go nowhere: for code handed a stream when there is no run to report on."""
    return RunEvents("", NullSink(), kind="", heartbeat_s=0)


# -- describing ---------------------------------------------------------------
def format_duration(seconds: float) -> str:
    """0.4s, 36m30s, 1h05m: short, for people."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    whole = int(seconds)
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def format_clock(seconds: float) -> str:
    """0:23:10, the way a table of elapsed times is read."""
    whole = int(seconds)
    return f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def describe_event(event: dict[str, Any]) -> str | None:
    """One human-readable line for an event; None for the ones not worth a line."""
    kind = event.get("type")
    if kind == "run" and event.get("state") == "started":
        parent = f" (started by {event['parent_run_id']})" if event.get("parent_run_id") else ""
        return (f"run {event.get('run_id', '')}: {event.get('kind', '')} "
                f"{event.get('module', '')} [{event.get('target', '')}]{parent}")
    if kind == "run":
        error = f" -- {event['error']}" if event.get("error") else ""
        return f"finished: {event.get('status', '?')}{error}"
    if kind == "stage":
        stage, state = event.get("stage", ""), event.get("state", "")
        detail = event.get("detail") or {}
        if state == "running":
            return f"  {stage}: running"
        if state == "succeeded":
            return f"  {stage}: done in {format_duration(float(event.get('duration_s') or 0.0))}"
        if state == "failed":
            return f"  {stage}: FAILED" + (f" -- {detail['error']}" if detail.get("error") else "")
        if state == "skipped":
            return f"  {stage}: skipped" + (f" ({detail['reason']})" if detail.get("reason") else "")
        return None
    if kind == "progress":
        line = f"  {event.get('stage', '')}: {event.get('step', '')}"
        if event.get("current") is not None and event.get("total") is not None:
            line += f" {event['current']}/{event['total']}"
        if event.get("message"):
            line += f" ({event['message']})"
        if event.get("eta_s"):
            line += f", ~{format_duration(float(event['eta_s']))} left"
        return line
    return None


# -- reading ------------------------------------------------------------------
@dataclass(frozen=True)
class RunSummary:
    run_id: str
    kind: str
    module: str
    target: str
    state: str            # running | succeeded | failed | cancelled | lost
    stage: str            # the stage running now, "" when none
    progress: str         # the last progress, e.g. "synth 40/114"; "" when none
    started_at: str
    elapsed_s: float
    error: str
    pid: int
    parent_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def read_events(path: Path) -> list[dict[str, Any]]:
    """Every complete event in the file.

    The last line may still be being written (no newline yet), and a line may not be JSON;
    neither is an error for a reader.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.split("\n")
    lines.pop()                      # "" after the final newline, or a line not finished yet
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _epoch(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()


def _progress_text(event: dict[str, Any]) -> str:
    step = str(event.get("step", "")).split(".", 1)[-1]
    if event.get("current") is not None and event.get("total") is not None:
        return f"{step} {event['current']}/{event['total']}"
    return f"{step} {event.get('message', '')}".strip()


def summarize(events: list[dict[str, Any]], *, now: float | None = None,
              alive: Callable[[int], bool] = process_alive) -> RunSummary | None:
    """Where a run is, from its events alone. None when the start event is missing."""
    started = next((e for e in events if e.get("type") == "run" and e.get("state") == "started"), None)
    if started is None:
        return None
    finished = next((e for e in reversed(events)
                     if e.get("type") == "run" and e.get("state") == "finished"), None)
    now = time.time() if now is None else now

    stage, progress = "", ""
    for event in events:
        if event.get("type") == "stage":
            if event.get("state") == "running":
                stage = str(event.get("stage", ""))
            elif event.get("stage") == stage:
                stage = ""
        elif event.get("type") == "progress":
            progress = _progress_text(event)

    pid = int(started.get("pid") or 0)
    if finished is not None:
        state, elapsed = str(finished.get("status", "failed")), _epoch(finished["ts"]) - _epoch(started["ts"])
        stage = ""
    else:
        silent_for = now - _epoch(events[-1]["ts"])
        # `pid` can only be judged on the host that wrote the events.
        gone = started.get("host") == socket.gethostname() and not alive(pid)
        state = "lost" if silent_for > LOST_AFTER_SECONDS and gone else "running"
        elapsed = now - _epoch(started["ts"])

    return RunSummary(
        run_id=str(started.get("run_id", "")), kind=str(started.get("kind", "")),
        module=str(started.get("module", "")), target=str(started.get("target", "")),
        state=state, stage=stage, progress=progress, started_at=str(started["ts"]),
        elapsed_s=max(elapsed, 0.0), error=str(finished.get("error", "")) if finished else "",
        pid=pid, parent_run_id=str(started.get("parent_run_id", "")),
    )


def list_runs(workspace: Workspace, limit: int = 20, *, now: float | None = None,
              alive: Callable[[int], bool] = process_alive) -> list[RunSummary]:
    """The most recent runs, newest first. A run id starts with its timestamp, so the
    file names sort by time."""
    if not workspace.events_dir.is_dir():
        return []
    summaries: list[RunSummary] = []
    for path in sorted(workspace.events_dir.glob("*.jsonl"), reverse=True):
        summary = summarize(read_events(path), now=now, alive=alive)
        if summary is not None:
            summaries.append(summary)
        if len(summaries) >= limit:
            break
    return summaries


def follow(path: Path, *, poll_s: float = 0.5, now: Callable[[], float] = time.time,
           sleep: Callable[[float], None] = time.sleep,
           alive: Callable[[int], bool] = process_alive) -> Iterator[dict[str, Any]]:
    """Events as they arrive, until the run finishes or is found lost."""
    seen = 0
    while True:
        events = read_events(path)
        for event in events[seen:]:
            yield event
        seen = len(events)
        summary = summarize(events, now=now(), alive=alive)
        if summary is not None and summary.state != "running":
            return
        sleep(poll_s)
