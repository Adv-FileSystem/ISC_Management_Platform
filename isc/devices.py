"""Which device, who holds it, how long to wait -- the policy half of running on hardware.

The mechanism (open a device, load an image, run a kernel) stays in `isc/backends/`.
This module knows no vendor vocabulary: it lists devices through the registered backends
and serialises access with one lock file per (backend, target, device index).

Mutual exclusion is the kernel's `flock` on `artifacts/locks/<backend>-<target>-<index>.lock`,
so a process that dies lets go of it by itself and a lock can never go stale. The file also
holds a one-line JSON note about the current holder. The note is for people (who has the
card, since when) and never decides who may run.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from isc.backends import BackendError, backend_names, get_backend
from isc.backends.base import DeviceInfo
from isc.workspace import Workspace

POLL_SECONDS = 0.2     # how often a waiter tries the lock again
REPORT_SECONDS = 5.0   # how often on_wait is repeated while the wait goes on
_KEY = re.compile(r"^(?P<backend>[^-]+)-(?P<target>.+)-(?P<index>\d+)$")


class DeviceBusy(BackendError):
    """The device stayed reserved for longer than the caller was willing to wait."""


@dataclass(frozen=True)
class Owner:
    run_id: str
    pid: int
    purpose: str            # "run", "reference" or "bench"


@dataclass(frozen=True)
class Holder:
    key: str                # the lock name, <backend>-<target>-<index>
    backend: str
    target: str
    index: int
    run_id: str             # empty when the holder left no readable note
    pid: int                # 0 when unknown
    since: str
    purpose: str


@dataclass(frozen=True)
class WaitInfo:
    key: str
    holder: Holder | None   # None when nobody could be identified
    waited_s: float


@dataclass(frozen=True)
class Reservation:
    key: str
    wait_ms: float


def lock_key(backend_name: str, target: str, index: int) -> str:
    return f"{backend_name}-{target}-{index}"


def describe_holder(holder: Holder | None) -> str:
    if holder is None or (not holder.run_id and not holder.pid):
        return "reserved by an unidentified process"
    ago = _ago(holder.since)
    detail = f"pid {holder.pid}" + (f", {ago} ago" if ago else "")
    return f"reserved by run {holder.run_id} ({detail})" if holder.run_id else f"reserved by {detail}"


def describe_wait(info: WaitInfo) -> str:
    return f"waiting for device {info.key}: {describe_holder(info.holder)}; waited {info.waited_s:.0f}s"


def _ago(since: str) -> str:
    try:
        started = datetime.strptime(since, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return ""
    seconds = max(int((datetime.now(started.tzinfo) - started).total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # it exists, it just belongs to someone else
    return True


def read_holder(path: Path) -> Holder | None:
    """The note in a lock file; None when it is missing, unreadable or names a dead process."""
    try:
        note = json.loads(path.read_text().splitlines()[0])
        pid = int(note["pid"])
    except (OSError, IndexError, ValueError, KeyError, TypeError):
        return None
    match = _KEY.match(path.stem)
    if match is None or not process_alive(pid):
        return None
    return Holder(
        key=path.stem, backend=match["backend"], target=match["target"], index=int(match["index"]),
        run_id=str(note.get("run_id", "")), pid=pid,
        since=str(note.get("since", "")), purpose=str(note.get("purpose", "")),
    )


def _is_held(path: Path) -> bool:
    """Whether some process holds the lock right now, found by trying to take it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class DeviceManager:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    # -- what is there ------------------------------------------------------
    def devices(self, backend_name: str | None = None) -> list[DeviceInfo]:
        """Every device the registered backends can see (or one backend's)."""
        found: list[DeviceInfo] = []
        for name in [backend_name] if backend_name else backend_names():
            try:
                found.extend(get_backend(name).devices())
            except Exception:       # one backend that cannot list must not hide the others
                continue
        return found

    # -- who has it ---------------------------------------------------------
    def lock_path(self, backend_name: str, target: str, index: int) -> Path:
        return self.workspace.locks_dir / f"{lock_key(backend_name, target, index)}.lock"

    def holders(self, backend_name: str | None = None) -> list[Holder]:
        """Every reservation held right now. A note left in a free lock is not a holder."""
        locks_dir = self.workspace.locks_dir
        if not locks_dir.is_dir():
            return []
        found: list[Holder] = []
        for path in sorted(locks_dir.glob("*.lock")):
            match = _KEY.match(path.stem)
            if match is None or (backend_name is not None and match["backend"] != backend_name):
                continue
            if not _is_held(path):
                continue
            found.append(read_holder(path) or Holder(
                key=path.stem, backend=match["backend"], target=match["target"],
                index=int(match["index"]), run_id="", pid=0, since="", purpose="",
            ))
        return found

    # -- taking it ----------------------------------------------------------
    @contextmanager
    def reserve(self, backend_name: str, target: str, index: int, *, owner: Owner,
                timeout_s: float | None = None,
                on_wait: Callable[[WaitInfo], None] | None = None) -> Iterator[Reservation]:
        """Exclusive, cross-process use of one device; waits (forever unless `timeout_s`) for it."""
        self.workspace.locks_dir.mkdir(parents=True, exist_ok=True)
        key = lock_key(backend_name, target, index)
        path = self.lock_path(backend_name, target, index)
        # O_CREAT without O_TRUNC: a process that only waits must never wipe the holder's note.
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
        started = time.monotonic()
        try:
            self._acquire(fd, path, key, started, timeout_s, on_wait)
            wait_ms = (time.monotonic() - started) * 1000.0
            self._write_note(fd, owner)
            try:
                yield Reservation(key=key, wait_ms=wait_ms)
            finally:
                self._clear_note(fd)
        finally:
            os.close(fd)            # closing the descriptor lets go of the flock

    def _acquire(self, fd: int, path: Path, key: str, started: float, timeout_s: float | None,
                 on_wait: Callable[[WaitInfo], None] | None) -> None:
        last_report: float | None = None
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                pass
            waited = time.monotonic() - started
            if timeout_s is not None and waited >= timeout_s:
                raise DeviceBusy(
                    f"device {key} is {describe_holder(read_holder(path))}; waited {waited:.1f}s")
            if on_wait is not None and (last_report is None or waited - last_report >= REPORT_SECONDS):
                on_wait(WaitInfo(key=key, holder=read_holder(path), waited_s=waited))
                last_report = waited
            pause = POLL_SECONDS if timeout_s is None else min(POLL_SECONDS, max(timeout_s - waited, 0.0))
            time.sleep(pause)

    @staticmethod
    def _write_note(fd: int, owner: Owner) -> None:
        note = {"run_id": owner.run_id, "pid": owner.pid,
                "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "purpose": owner.purpose}
        os.ftruncate(fd, 0)
        os.pwrite(fd, (json.dumps(note) + "\n").encode(), 0)

    @staticmethod
    def _clear_note(fd: int) -> None:
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
