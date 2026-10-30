"""The vendor seam.

Everything vendor-specific -- the build toolchain AND the device runtime --
lives behind these types. Core (isc/lifecycle/orchestrator.py, isc/lifecycle/build.py, isc/cli.py)
must never import a vendor SDK or mention xclbin/HBM/v++ directly.

Adding a vendor = adding one module under isc/backends/ that subclasses
Backend and registering it in isc/backends/__init__.py.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from isc.lifecycle.manifest import Manifest


class BackendError(RuntimeError):
    """Vendor toolchain or runtime failure, already carrying a readable message."""


@dataclass(frozen=True)
class DeviceInfo:
    backend: str
    index: int
    name: str
    address: str = ""          # PCIe BDF or equivalent vendor address
    emulated: bool = False
    target: str = ""           # the build target this session was opened for ("" when only listed)

    @property
    def id(self) -> str:
        """Identity shared by events and metrics.

        A card is named by its slot (the PCIe address), which survives a reboot, not by an
        index that can change; an emulated device is named by the target it emulates.
        """
        if self.emulated:
            return f"{self.backend}:emu:{self.target or 'emu'}:{self.index}"
        if self.address:
            return f"{self.backend}:{self.address}"
        return f"{self.backend}:index{self.index}"


@dataclass(frozen=True)
class BuildProgress:
    """One step of a long build, in vendor-neutral terms. Counts are only what the tool said."""
    step: str                  # "compile", "link.<name>", "vpl.<name>": the backend's own steps
    state: str                 # "started" | "running" | "completed"
    current: int | None = None
    total: int | None = None
    unit: str = ""             # what current/total count: "jobs", "tasks"
    message: str = ""
    clock: str = ""            # the tool's own HH:MM:SS stamp on the line, when it prints one


@dataclass
class BuildRequest:
    manifest: Manifest
    target: str
    output_dir: Path           # backend writes its artifacts here
    kernel_source: Path | None = None  # complete top-level source prepared by the core
    timeout_seconds: float | None = None
    on_progress: Callable[[BuildProgress], None] | None = None  # told as the build moves


@dataclass
class BuildOutput:
    binary: Path               # the loadable device image (xclbin, bitstream, ...)
    log: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Timings:
    """Phase timers filled in by the backend as the adapter drives the device."""
    host_to_device_ms: float = 0.0
    kernel_ms: float = 0.0
    device_to_host_ms: float = 0.0

    def reset(self) -> None:
        """Zero the phase timers.

        Phase timers accumulate, which is what a single run wants. A benchmark
        needs per-iteration numbers out of one long-lived session, so it resets
        between iterations.
        """
        self.host_to_device_ms = 0.0
        self.kernel_ms = 0.0
        self.device_to_host_ms = 0.0

    @contextmanager
    def measure(self, phase: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            setattr(self, phase, getattr(self, phase) + elapsed)


class Buffer(ABC):
    """A device buffer bound to one kernel argument index."""

    size: int

    @abstractmethod
    def write(self, data: bytes, offset: int = 0) -> None:
        """Copy host bytes into the buffer and push them to the device."""

    @abstractmethod
    def read(self, size: int | None = None, offset: int = 0) -> bytes:
        """Pull bytes back from the device and return them."""


class Session(ABC):
    """One opened device with the module's image loaded.

    A module adapter receives this and nothing else; that is what keeps
    adapters portable across vendors.
    """

    def __init__(self, device: DeviceInfo, arguments: tuple = ()) -> None:
        self.device = device
        self.arguments = arguments      # manifest kernel.arguments, for checking
        self.timings = Timings()
        self.metrics: dict[str, float] = {}

    def check_buffer_argument(self, arg: int) -> None:
        """Fail loudly on a wrong argument index instead of at the vendor layer.

        An out-of-range index otherwise surfaces as an opaque driver error, and
        a scalar index silently allocates a buffer nothing will read.
        """
        if not self.arguments:
            return
        if not 0 <= arg < len(self.arguments):
            names = ", ".join(f"{i}:{a.name}" for i, a in enumerate(self.arguments))
            raise BackendError(
                f"kernel argument index {arg} is out of range; "
                f"module.yaml declares {len(self.arguments)} arguments ({names})"
            )
        declared = self.arguments[arg]
        if declared.kind != "buffer":
            raise BackendError(
                f"kernel argument {arg} is declared as a {declared.kind} "
                f"({declared.name!r}) in module.yaml, not a buffer"
            )

    def check_run_arity(self, count: int) -> None:
        if self.arguments and count != len(self.arguments):
            raise BackendError(
                f"kernel takes {len(self.arguments)} arguments per module.yaml, "
                f"but run() was called with {count}"
            )

    @abstractmethod
    def buffer(self, size: int, arg: int) -> Buffer:
        """Allocate a device buffer for kernel argument index `arg`."""

    @abstractmethod
    def run(self, *args: Any) -> None:
        """Launch the kernel with `args` and block until it completes.

        Buffer objects are passed straight through; ints/floats are scalars.
        """

    def metric(self, name: str, value: float) -> None:
        """Record a module-specific metric (e.g. compression_ratio)."""
        self.metrics[name] = float(value)

    def close(self) -> None:
        pass

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Backend(ABC):
    name: str = "abstract"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(usable, human-readable reason) for `isc doctor`."""

    @abstractmethod
    def devices(self) -> list[DeviceInfo]:
        ...

    @abstractmethod
    def toolchain_identity(self) -> str:
        """Stable string identifying the build toolchain; feeds the build cache key."""

    @abstractmethod
    def build(self, request: BuildRequest) -> BuildOutput:
        ...

    @abstractmethod
    def open(self, manifest: Manifest, binary: Path, target: str,
             device_index: int | None = None) -> Session:
        ...

    def is_emulation(self, target: str) -> bool:
        return target.endswith("_emu")
