"""The lifecycle: check -> reference -> build -> execute -> compare -> record.

The reference output (another target's, or the CPU baseline's) is settled before the
build starts, so a wrong module fails on the cheap target; only the comparison has to
wait for the device.

Each step is also written as a run event (isc/lifecycle/events.py), so a long run can be followed
from outside. The events never change what a run returns or records.

This module contains no vendor vocabulary. It never sees an xclbin, a memory
bank, or a v++ flag -- only a Manifest, a Backend, and a Session.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from isc.lifecycle import record
from isc.operations.baseline import BaselineUnavailable, compute as compute_baseline
from isc.backends import BackendError
from isc.lifecycle.build import BuildResult, build, ensure_target_declared
from isc.lifecycle.check import check_module
from isc.lifecycle.devices import Owner, WaitInfo
from isc.lifecycle.events import RunEvents, Sink, silent, start_run
from isc.operations.loader import ModuleCodeError, load_callable
from isc.lifecycle.manifest import Manifest
from isc.lifecycle.runtime import Runtime
from isc.lifecycle.validate import ValidationResult, validate
from isc.lifecycle.workspace import Workspace


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
    input_sha256: str = ""
    output_sha256: str = ""
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
        skip_validation: bool = False, device_timeout_s: float | None = None,
        on_wait: Callable[[WaitInfo], None] | None = None,
        events: Sink | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None) -> RunResult:
    manifest = workspace.load(module_id)
    run_id = record.new_run_id()
    stream = start_run(workspace, run_id, kind="run", module=manifest.id, target=target,
                       backend=manifest.backend, sink=events, on_event=on_event)
    try:
        result = _run_job(workspace, manifest, run_id, stream, input_path, target,
                          device_index=device_index, output_path=output_path,
                          force_build=force_build, timeout_seconds=timeout_seconds,
                          skip_validation=skip_validation, device_timeout_s=device_timeout_s,
                          on_wait=on_wait)
    except KeyboardInterrupt:
        stream.finish("cancelled")
        raise
    except BaseException as exc:
        stream.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    stream.finish("succeeded" if result.ok else "failed", result.error)
    return result


def _run_job(workspace: Workspace, manifest: Manifest, run_id: str, stream: RunEvents,
             input_path: str | Path, target: str, *, device_index: int | None,
             output_path: str | Path | None, force_build: bool, timeout_seconds: float | None,
             skip_validation: bool, device_timeout_s: float | None,
             on_wait: Callable[[WaitInfo], None] | None) -> RunResult:
    result = RunResult(
        run_id=run_id, module=manifest.id, version=manifest.version, target=target,
        backend=manifest.backend, status="failed",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )

    source = Path(input_path).resolve()
    if not source.is_file():
        result.error = f"input file not found: {source}"
        stream.stage("check").fail(result.error)
        record.append(workspace, result.to_dict())
        return result
    result.input_path = str(source)

    try:
        with stream.stage("check"):
            data = source.read_bytes()
            result.input_bytes = len(data)
            result.input_sha256 = _sha256(data)
            manifest.validate_input(data)
            ensure_target_declared(manifest, target)
            _static_check(manifest)
        # 기준 출력은 빌드보다 먼저 만든다. hw 이미지는 수십 분이 걸리고 hw_emu 는 몇 분이므로,
        # 틀린 모듈은 싼 쪽에서 먼저 실패해야 한다. 비교는 장치 출력이 생긴 뒤에야 할 수 있다.
        if skip_validation:
            stream.skip("reference", reason="no-validate")
            reference = None
        else:
            reference = _prepare_reference(
                workspace, manifest, target, data, device_index, timeout_seconds,
                device_timeout_s, parent_run_id=run_id, stream=stream)
        if reference is not None and reference.failure:
            result.validation = ValidationResult(reference.mode, False, reference.failure).to_dict()
            result.error = f"validation failed before the run: {reference.failure}"
            record.append(workspace, result.to_dict())
            return result

        with stream.stage("build", target=target) as build_stage:
            built = build(workspace, manifest, target, force=force_build,
                          timeout_seconds=timeout_seconds, stage=build_stage)
            build_stage.succeed(fingerprint=built.fingerprint, cached=built.cached)
        result.build = built.to_dict()
        outcome = _execute(workspace, manifest, built, target, device_index, data,
                           owner=_owner(run_id), device_timeout_s=device_timeout_s,
                           on_wait=on_wait, stream=stream)
        result.device = outcome.device
        result.metrics = outcome.metrics
        output = outcome.output

        if reference is None:
            stream.skip("compare", reason="no-validate")
            validation = ValidationResult(manifest.validation_mode, True, "skipped")
        elif reference.output is None:
            stream.skip("compare", reason=reference.note)
            validation = _compare(reference, output)
        else:
            with stream.stage("compare", mode=reference.mode) as compare_stage:
                validation = _compare(reference, output)
                if validation.passed:
                    compare_stage.succeed(passed=True)
                else:
                    compare_stage.fail(validation.detail, passed=False)
    except (BackendError, ModuleCodeError, LookupError, ValueError) as exc:
        result.error = str(exc)
        record.append(workspace, result.to_dict())
        return result
    except Exception as exc:  # a module's own code failing is a failed run, not a crash
        result.error = f"{type(exc).__name__}: {exc}"
        record.append(workspace, result.to_dict())
        return result

    with stream.stage("record"):
        destination = Path(output_path).resolve() if output_path else (
            workspace.outputs_dir / f"{run_id}-{source.name}.out"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(output)
        result.output_path = str(destination)
        result.output_bytes = len(output)
        result.output_sha256 = _sha256(output)
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


def run_build(workspace: Workspace, manifest: Manifest, target: str, *, force: bool = False,
              timeout_seconds: float | None = None, events: Sink | None = None,
              on_event: Callable[[dict[str, Any]], None] | None = None) -> BuildResult:
    """`isc build` as a run of its own: it gets a run id and events like any long job.

    It writes no record -- a build is not an execution, and records.jsonl is a list of those.
    """
    run_id = record.new_run_id()
    stream = start_run(workspace, run_id, kind="build", module=manifest.id, target=target,
                       backend=manifest.backend, sink=events, on_event=on_event)
    try:
        with stream.stage("build", target=target) as stage:
            built = build(workspace, manifest, target, force=force,
                          timeout_seconds=timeout_seconds, stage=stage)
            stage.succeed(fingerprint=built.fingerprint, cached=built.cached)
    except KeyboardInterrupt:
        stream.finish("cancelled")
        raise
    except BaseException as exc:
        stream.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    stream.finish("succeeded")
    return built


def _static_check(manifest: Manifest) -> None:
    """The same checks as `isc check`, before anything slow starts.

    The CLI runs them too, to print warnings ahead of a long build; this keeps the
    rule true for every caller of run(), not only for the command line.
    """
    report = check_module(manifest.directory)
    if not report.passed:
        raise ValueError("static check failed: " + "; ".join(report.problems))


@dataclass
class _Reference:
    """What the device output has to equal, settled before the device is touched.

    `output` is None when there is nothing to compare; `note` then says why. `failure`
    is set when the reference could not be produced at all -- the run must not start.
    """
    mode: str
    output: bytes | None = None
    note: str = ""
    failure: str = ""
    # Who produced `output`: the reference run's record id and target, or the CPU baseline.
    source: dict[str, Any] = field(default_factory=dict)


def _nothing_to_compare(stream: RunEvents, reference: _Reference) -> _Reference:
    stream.skip("reference", reason=reference.note)
    return reference


def _prepare_reference(workspace: Workspace, manifest: Manifest, target: str, data: bytes,
                       device_index: int | None, timeout_seconds: float | None,
                       device_timeout_s: float | None = None,
                       parent_run_id: str | None = None,
                       stream: RunEvents | None = None) -> _Reference:
    stream = stream or silent()
    mode = manifest.validation_mode
    if mode == "none":
        return _nothing_to_compare(
            stream, _Reference(mode, note="validation disabled in module.yaml"))
    if mode == "cpu_baseline":
        return _baseline_reference(manifest, data, stream)
    reference_target = manifest.validation_reference_target
    if target == reference_target:
        return _nothing_to_compare(stream, _Reference(
            mode, note="ran on the reference target itself; nothing to compare"))
    with stream.stage("reference", mode=mode, target=reference_target) as stage:
        try:
            output, child_run_id = _reference_output(
                workspace, manifest, data, device_index, timeout_seconds,
                device_timeout_s, parent_run_id)
        except (BackendError, ModuleCodeError, LookupError, ValueError) as exc:
            failure = f"could not compute reference output on {reference_target}: {exc}"
            stage.fail(failure)
            return _Reference(mode, failure=failure)
        stage.succeed(**({"child_run_id": child_run_id} if child_run_id else {}))
    return _Reference(mode, output=output,
                      source={"run_id": child_run_id, "target": reference_target})


def _baseline_reference(manifest: Manifest, data: bytes, stream: RunEvents) -> _Reference:
    """The operation's CPU reference instead of another target's output.

    Only sound where the CPU result is the one correct answer. LZ4 declares
    `equivalence: none` precisely because a different-but-valid block stream is
    not a defect, and reporting it as one would train users to ignore failures.
    """
    operation = manifest.operation
    if operation.baseline_equivalence != "exact":
        return _nothing_to_compare(stream, _Reference(
            "cpu_baseline",
            note=f"operation {operation.id!r} declares baseline.equivalence: none, so a "
                 "CPU comparison cannot decide correctness; nothing was checked",
        ))
    with stream.stage("reference", mode="cpu_baseline") as stage:
        try:
            output = compute_baseline(operation, data, manifest.parameters)
        except BaselineUnavailable as exc:
            failure = f"CPU baseline unavailable: {exc}"
            stage.fail(failure)
            return _Reference("cpu_baseline", failure=failure)
    return _Reference("cpu_baseline", output=output, source={"baseline": operation.id})


def _compare(reference: _Reference, output: bytes) -> ValidationResult:
    if reference.output is None:
        return ValidationResult(reference.mode, True, reference.note)
    result = validate(reference.mode, output, reference.output)
    result.reference = {**reference.source, "output_sha256": _sha256(reference.output),
                        "output_bytes": len(reference.output)}
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate(workspace: Workspace, manifest: Manifest, target: str, data: bytes, output: bytes,
              device_index: int | None, timeout_seconds: float | None) -> ValidationResult:
    """Prepare the reference and compare in one go -- for an output that already exists."""
    reference = _prepare_reference(workspace, manifest, target, data, device_index, timeout_seconds)
    if reference.failure:
        return ValidationResult(reference.mode, False, reference.failure)
    return _compare(reference, output)


def _reference_output(workspace: Workspace, manifest: Manifest, data: bytes,
                       device_index: int | None, timeout_seconds: float | None,
                       device_timeout_s: float | None = None,
                       parent_run_id: str | None = None) -> tuple[bytes, str]:
    """Execute the reference target in a fresh process; return its output and its run id.

    XRT binds a physical-device shim to a process the first time it opens a
    device; a process that already opened real hardware cannot then reopen
    the same index in emulation mode (or vice versa). So when the primary
    target and the reference target differ, the reference execution cannot
    safely share this process -- it is run as a separate `isc run` instead,
    which also means it gets its own entry in records.jsonl -- the run id returned
    here is what ties that entry to the run that was compared with it.
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
        if device_timeout_s is not None:
            command += ["--device-timeout", str(device_timeout_s)]
        # The child labels its device reservation "reference" when it sees its parent's run id.
        env = {**os.environ, **({record.PARENT_RUN_ENV: parent_run_id} if parent_run_id else {})}
        try:
            # cwd=workspace.root so `python3 -m isc` resolves even when isc is
            # not pip-installed (README's no-install usage path).
            completed = subprocess.run(command, capture_output=True, text=True, env=env,
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
        run_id = payload.get("run_id", "")
        if payload.get("status") != "succeeded":
            raise BackendError(
                f"reference run {run_id or '(no run id)'} on {reference_target} failed: "
                f"{payload.get('error', 'unknown error')}"
            )
        return Path(payload["output_path"]).read_bytes(), run_id


def _owner(run_id: str) -> Owner:
    """Who holds the device: a reference run says so (see _reference_output)."""
    purpose = "reference" if os.environ.get(record.PARENT_RUN_ENV) else "run"
    return Owner(run_id=run_id, pid=os.getpid(), purpose=purpose)


@dataclass
class _ExecutionOutcome:
    output: bytes
    device: dict[str, Any]
    metrics: dict[str, float]


def _execute(workspace: Workspace, manifest: Manifest, built: BuildResult, target: str,
             device_index: int | None, data: bytes, *, owner: Owner,
             device_timeout_s: float | None = None,
             on_wait: Callable[[WaitInfo], None] | None = None,
             stream: RunEvents | None = None) -> _ExecutionOutcome:
    stream = stream or silent()
    execute = load_callable(
        manifest.adapter_directory, "adapter.py", "execute",
        f"operation_{manifest.operation_id}",
    )
    adapter_parameters = manifest.adapter_parameters

    # The clock starts before the reservation on purpose: waiting for the device and opening
    # it are part of what one `isc run` costs. `isc bench` exists to measure without them.
    started = time.perf_counter()
    with Runtime(workspace).session(manifest, built.binary, target, device_index, owner=owner,
                                    timeout_s=device_timeout_s, on_wait=on_wait,
                                    stream=stream) as handle:
        session = handle.session
        with stream.stage("execute"):
            output = execute(session, data, adapter_parameters)
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
            "emulated": session.device.emulated, "id": session.device.id,
        }
        metrics: dict[str, float] = {
            "total_ms": round(total_ms, 3),
            "device_wait_ms": round(handle.wait_ms, 3),
            "device_open_ms": round(handle.open_ms, 3),
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
