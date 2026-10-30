"""A backend's BuildProgress as run events, and how long each build step usually takes.

The backend says "step X started / is at 40 of 114 / completed". Here that becomes `progress`
events on the build stage and, when a step completes, one line in artifacts/build_durations.jsonl.
The next build of the same (operation, target) reads the last few of those lines to say how long
the step it is in is likely to take: `eta_s`. It is an estimate and is named as one -- with no
history there is no `eta_s` at all, and no percentage is ever made up.

Nothing here may fail a build: watching is a courtesy to whoever waits for it.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Callable

from isc.backends.base import BuildProgress
from isc.lifecycle.workspace import Workspace

HISTORY_FILE = "build_durations.jsonl"
RECENT = 5


def history_path(workspace: Workspace) -> Path:
    return workspace.artifacts_dir / HISTORY_FILE


def recent_durations(workspace: Workspace, operation: str, target: str, step: str,
                     limit: int = RECENT) -> list[float]:
    """Seconds the last `limit` builds of this (operation, target) spent in `step`, oldest first."""
    try:
        lines = history_path(workspace).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found: list[float] = []
    for line in lines:
        try:
            entry = json.loads(line)
            if (entry["operation"], entry["target"], entry["step"]) == (operation, target, step):
                found.append(float(entry["seconds"]))
        except (ValueError, KeyError, TypeError):
            continue
    return found[-limit:]


def expected_seconds(workspace: Workspace, operation: str, target: str, step: str) -> float | None:
    durations = recent_durations(workspace, operation, target, step)
    return statistics.median(durations) if durations else None


def _seconds_of_day(clock: str) -> int:
    hours, minutes, seconds = (int(part) for part in clock.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _took(began: tuple[float, str], now: float, clock: str) -> float:
    """How long a step took. The tool's own HH:MM:SS stamps are better than the times the lines
    reached us: Vivado's first output arrives in one burst, seen up to ~20 s late on a real
    hw_emu build, which timed a 19 s step at 0.02 s. The stamps carry no date, hence the wrap."""
    started_at, started_clock = began
    if started_clock and clock:
        return float((_seconds_of_day(clock) - _seconds_of_day(started_clock)) % 86400)
    return now - started_at


class BuildWatch:
    """Callable for `BuildRequest.on_progress`; `stage` is a StageHandle."""

    def __init__(self, workspace: Workspace, stage, *, operation: str, target: str,
                 fingerprint: str, now: Callable[[], float] = time.monotonic) -> None:
        self._workspace = workspace
        self._stage = stage
        self._operation = operation
        self._target = target
        self._fingerprint = fingerprint
        self._now = now
        self._began: dict[str, tuple[float, str]] = {}

    def __call__(self, progress: BuildProgress) -> None:
        try:
            self._handle(progress)
        except Exception:
            pass

    def _eta(self, step: str, elapsed: float) -> float | None:
        expected = expected_seconds(self._workspace, self._operation, self._target, step)
        return None if expected is None else max(expected - elapsed, 0.0)

    def _handle(self, progress: BuildProgress) -> None:
        step, now = progress.step, self._now()
        if progress.state == "started":
            self._began[step] = (now, progress.clock)
            self._stage.progress(step, message="started", eta_s=self._eta(step, 0.0))
        elif progress.state == "completed":
            began = self._began.pop(step, None)
            self._stage.progress(step, message="completed")
            if began is not None:
                self._remember(step, _took(began, now, progress.clock))
        else:
            began = self._began.get(step)
            elapsed = 0.0 if began is None else now - began[0]
            self._stage.progress(step, current=progress.current, total=progress.total,
                                 unit=progress.unit, eta_s=self._eta(step, elapsed),
                                 message=progress.message)

    def _remember(self, step: str, seconds: float) -> None:
        line = json.dumps({
            "operation": self._operation, "target": self._target, "step": step,
            "seconds": round(seconds, 3), "fingerprint": self._fingerprint,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }) + "\n"
        path = history_path(self._workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
