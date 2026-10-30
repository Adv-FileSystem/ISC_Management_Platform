"""The order a device session is opened in, written once.

reserve -> enter the image directory -> open (timed) -> hand the session out ->
close -> leave the directory -> release. `isc run` and `isc bench` both go through
here, so the order cannot drift between them, and it is undone on every path out.

Given a stream (isc/events.py) it also reports the two slow steps as stages: `device_wait`
(progress while someone else holds the device) and `device_open`.

Adapter loading stays with the callers: the runtime knows how to open a device,
not what to run on it. No vendor vocabulary either -- that is the Backend's.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from isc.backends import get_backend
from isc.backends.base import DeviceInfo, Session
from isc.lifecycle.devices import DeviceManager, Owner, WaitInfo, describe_wait
from isc.lifecycle.events import RunEvents, silent
from isc.lifecycle.manifest import Manifest
from isc.lifecycle.workspace import Workspace, working_directory


@dataclass(frozen=True)
class SessionHandle:
    session: Session        # the vendor session; a module adapter gets this and nothing else
    wait_ms: float          # how long the reservation was waited for
    open_ms: float          # opening the device and loading the image; bench takes it out of every sample

    @property
    def device(self) -> DeviceInfo:
        return self.session.device


class Runtime:
    def __init__(self, workspace: Workspace, devices: DeviceManager | None = None) -> None:
        self.devices = devices or DeviceManager(workspace)

    @contextmanager
    def session(self, manifest: Manifest, binary: Path, target: str,
                device_index: int | None = None, *, owner: Owner,
                timeout_s: float | None = None,
                on_wait: Callable[[WaitInfo], None] | None = None,
                stream: RunEvents | None = None) -> Iterator[SessionHandle]:
        stream = stream or silent()
        backend = get_backend(manifest.backend)
        index = device_index if device_index is not None else 0
        wait = stream.stage("device_wait", target=target)

        def waiting(info: WaitInfo) -> None:
            wait.progress("waiting", message=describe_wait(info))
            if on_wait is not None:
                on_wait(info)

        try:
            with self.devices.reserve(backend.name, target, index, owner=owner,
                                      timeout_s=timeout_s, on_wait=waiting) as reservation:
                wait.succeed(wait_ms=round(reservation.wait_ms, 3))
                # Emulation needs emconfig.json in the working directory and the simulator leaves
                # scratch files there: both belong in the image's directory.
                with working_directory(binary.parent):
                    with stream.stage("device_open") as opening:
                        started = time.perf_counter()
                        session = backend.open(manifest, binary, target, device_index)
                        open_ms = (time.perf_counter() - started) * 1000.0
                        opening.succeed(device=session.device.id, open_ms=round(open_ms, 3))
                    with session:
                        yield SessionHandle(session=session, wait_ms=reservation.wait_ms,
                                            open_ms=open_ms)
        except BaseException as exc:
            wait.fail(f"{type(exc).__name__}: {exc}")     # a no-op once the wait has succeeded
            raise
