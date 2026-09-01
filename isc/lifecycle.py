"""The lifecycle: build -> execute -> validate -> record.

This module contains no vendor vocabulary. It never sees an xclbin, a memory
bank, or a v++ flag -- only a Manifest, a Backend, and a Session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from isc import record
from isc.backends import BackendError, get_backend
from isc.build import BuildResult, build
from isc.loader import ModuleCodeError, load_callable
from isc.manifest import Manifest
from isc.validate import ValidationResult, validate
from isc.workspace import Workspace


@dataclass
class RunResult:
    run_id: str
    module: str
    version: str
    target: str
    backend: str
    status: str
    started_at: str
    input_path: str = ""
    output_path: str = ""
    input_bytes: int = 0
    output_bytes: int = 0
    device: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    build: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        data = {key: value for key, value in vars(self).items()}
        if not data["error"]:
            data.pop("error")
        return data


def run(workspace: Workspace, module_id: str, input_path: str | Path, target: str, *,
        device_index: int | None = None, output_path: str | Path | None = None,
        force_build: bool = False, timeout_seconds: float | None = None,
        skip_validation: bool = False) -> RunResult:
    manifest = workspace.load(module_id)
    run_id = record.new_run_id()
    result = RunResult(
        run_id=run_id, module=manifest.id, version=manifest.version, target=target,
        backend=manifest.backend, status="failed",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )

    source = Path(input_path).resolve()
    if not source.is_file():
        result.error = f"input file not found: {source}"
        record.append(workspace, result.to_dict())
        return result
    result.input_path = str(source)

    try:
        data = source.read_bytes()
        result.input_bytes = len(data)
        manifest.validate_input(data)
        built = build(workspace, manifest, target, force=force_build, timeout_seconds=timeout_seconds)
        result.build = built.to_dict()
        outcome = _execute(workspace, manifest, built, target, device_index, data)
        result.device = outcome.device
        result.metrics = outcome.metrics
        output = outcome.output

        if skip_validation:
            validation = ValidationResult(manifest.validation_mode, True, "skipped")
        else:
            validation = _validate(workspace, manifest, target, data, output,
                                    device_index, timeout_seconds)
    except (BackendError, ModuleCodeError, LookupError, ValueError) as exc:
        result.error = str(exc)
        record.append(workspace, result.to_dict())
        return result
    except Exception as exc:  # a module's own code failing is a failed run, not a crash
        result.error = f"{type(exc).__name__}: {exc}"
        record.append(workspace, result.to_dict())
        return result

    destination = Path(output_path).resolve() if output_path else (
        workspace.outputs_dir / f"{run_id}-{source.name}.out"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    result.output_path = str(destination)
    result.output_bytes = len(output)
    result.validation = validation.to_dict()

    result.status = "succeeded" if validation.passed else "failed"
    if not validation.passed:
        result.error = f"validation failed: {validation.detail}"
    record.append(workspace, result.to_dict())
    return result


def revalidate(workspace: Workspace, module_id: str, input_path: str | Path,
               output_path: str | Path, target: str, *,
               device_index: int | None = None,
               timeout_seconds: float | None = None) -> ValidationResult:
    """Re-run only the correctness check against already-produced output.

    `target` is the target that produced `output_path` -- cross-target
    validation needs to know that to decide whether a reference execution is
    even necessary.
    """
    manifest = workspace.load(module_id)
    data = Path(input_path).resolve().read_bytes()
    output = Path(output_path).resolve().read_bytes()
    return _validate(workspace, manifest, target, data, output, device_index, timeout_seconds)


def _validate(workspace: Workspace, manifest: Manifest, target: str, data: bytes, output: bytes,
              device_index: int | None, timeout_seconds: float | None) -> ValidationResult:
    if manifest.validation_mode == "none":
        return ValidationResult("none", True, "validation disabled in module.yaml")
    if target == manifest.validation_reference_target:
        return ValidationResult(manifest.validation_mode, True,
                                 "ran on the reference target itself; nothing to compare")
    try:
        reference_output = _reference_output(workspace, manifest, data, device_index, timeout_seconds)
    except (BackendError, ModuleCodeError, LookupError, ValueError) as exc:
        return ValidationResult(
            manifest.validation_mode, False,
            f"could not compute reference output on {manifest.validation_reference_target}: {exc}",
        )
    return validate(manifest.validation_mode, output, reference_output)


def _reference_output(workspace: Workspace, manifest: Manifest, data: bytes,
                       device_index: int | None, timeout_seconds: float | None) -> bytes:
    """Execute the reference target in a fresh process.

    XRT binds a physical-device shim to a process the first time it opens a
    device; a process that already opened real hardware cannot then reopen
    the same index in emulation mode (or vice versa). So when the primary
    target and the reference target differ, the reference execution cannot
    safely share this process -- it is run as a separate `isc run` instead,
    which also means it gets its own entry in records.jsonl.
    """
    reference_target = manifest.validation_reference_target
    with tempfile.TemporaryDirectory() as scratch:
        input_path = Path(scratch) / "input.bin"
        output_path = Path(scratch) / "reference.out"
        input_path.write_bytes(data)
        command = [
            sys.executable, "-m", "isc", "--workspace", str(workspace.root), "--json",
            "run", manifest.id, "--input", str(input_path), "--output", str(output_path),
            "--target", reference_target, "--no-validate",
        ]
        if device_index is not None:
            command += ["--device", str(device_index)]
        if timeout_seconds is not None:
            command += ["--timeout", str(timeout_seconds)]
        try:
            # cwd=workspace.root so `python3 -m isc` resolves even when isc is
            # not pip-installed (README's no-install usage path).
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=timeout_seconds, cwd=str(workspace.root))
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"reference run on {reference_target} timed out after {timeout_seconds}s"
            ) from exc

        # XRT prints device/driver banners straight to stdout ahead of our JSON.
        start = completed.stdout.find("{")
        if start == -1:
            raise BackendError(
                f"reference run on {reference_target} produced no output (exit "
                f"{completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
            )
        try:
            payload = json.loads(completed.stdout[start:])
        except json.JSONDecodeError as exc:
            raise BackendError(
                f"reference run on {reference_target} produced unparsable output (exit "
                f"{completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
            ) from exc
        if payload.get("status") != "succeeded":
            raise BackendError(
                f"reference run on {reference_target} failed: "
                f"{payload.get('error', 'unknown error')}"
            )
        return Path(payload["output_path"]).read_bytes()


@dataclass
class _ExecutionOutcome:
    output: bytes
    device: dict[str, Any]
    metrics: dict[str, float]


def _execute(workspace: Workspace, manifest: Manifest, built: BuildResult, target: str,
             device_index: int | None, data: bytes) -> _ExecutionOutcome:
    backend = get_backend(manifest.backend)
    execute = load_callable(
        manifest.adapter_directory, "adapter.py", "execute",
        f"operation_{manifest.operation_id}",
    )
    lock_key = f"{backend.name}-{target}-{device_index if device_index is not None else 0}"

    started = time.perf_counter()
    with workspace.device_lock(lock_key), _working_directory(built.binary.parent):
        session = backend.open(manifest, built.binary, target, device_index)
        with session:
            output = execute(session, data, manifest.parameters)
            if not isinstance(output, (bytes, bytearray)):
                raise ModuleCodeError(
                    f"operation {manifest.operation_id!r} adapter execute() must return bytes, "
                    f"got {type(output).__name__}"
                )
            output = bytes(output)
            total_ms = (time.perf_counter() - started) * 1000.0
            device = {
                "backend": session.device.backend, "index": session.device.index,
                "name": session.device.name, "address": session.device.address,
                "emulated": session.device.emulated,
            }
            metrics: dict[str, float] = {
                "total_ms": round(total_ms, 3),
                "host_to_device_ms": round(session.timings.host_to_device_ms, 3),
                "kernel_ms": round(session.timings.kernel_ms, 3),
                "device_to_host_ms": round(session.timings.device_to_host_ms, 3),
                "input_bytes": float(len(data)),
                "output_bytes": float(len(output)),
            }
            if total_ms > 0:
                # bytes / (1000 * ms) == MB/s with MB = 1e6 bytes
                metrics["throughput_mbps"] = round(len(data) / (1000.0 * total_ms), 3)
            metrics.update(session.metrics)
    return _ExecutionOutcome(output=output, device=device, metrics=metrics)


@contextmanager
def _working_directory(directory: Path) -> Iterator[None]:
    """Run inside the build directory.

    Emulation needs emconfig.json in the working directory, and the simulator
    scatters scratch files next to it -- both belong in the build directory,
    not wherever the user happened to invoke the CLI from.
    """
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)
