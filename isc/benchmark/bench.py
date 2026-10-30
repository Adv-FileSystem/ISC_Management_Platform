"""매트릭스 실행기 — 가속 효과를 판정할 수 있는 형태로 측정한다.

`isc run` 을 셸 루프로 N 번 도는 것과 이 모듈의 차이가 이 프레임워크의 핵심이다.

    isc run 반복:  [락 + 디바이스 오픈 + xclbin 로드 + 실행] x N
    isc bench   :  [락 + 디바이스 오픈 + xclbin 로드] x 1  +  [실행] x N

디바이스 오픈은 수 초가 걸린다. 그것을 회차마다 다시 내고 CPU 와 비교하면
FPGA 는 무조건 진다. 오픈 비용은 device_open_ms 로 따로 기록하고 반복 측정에서
제외한다 -- 1 회성 비용이라는 사실 자체가 활용 계획에 필요한 정보다.

측정 규약
- 모듈마다 디바이스를 한 번 열고 그 세션 안에서 입력 크기를 순회한다.
- 예약과 열기는 isc.lifecycle.runtime 이 맡는다. 예약을 기다린 시간은 device_wait_ms 로 따로 남기고
  표본에는 들어가지 않는다.
- 회차마다 Session.timings 를 리셋해 회차별 h2d/kernel/d2h 를 얻는다.
- CPU 기준선은 같은 warmup/repeat 으로, 디바이스 락 밖에서 잰다.
- 정확성은 셀마다 한 번만 확인한다(성능 반복에 검증을 섞지 않는다).
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import json

from isc.operations import baseline as baseline_module
from isc.benchmark import benchdata
from isc.lifecycle import record
from isc.backends import BackendError
from isc.lifecycle.build import build
from isc.lifecycle.devices import Owner, WaitInfo, describe_wait
from isc.lifecycle.events import RunEvents, Sink, start_run
from isc.benchmark.experiment import Experiment
from isc.operations.loader import ModuleCodeError, load_callable
from isc.lifecycle.manifest import Manifest
from isc.lifecycle.runtime import Runtime
from isc.lifecycle.workspace import Workspace

Reporter = Callable[[str], None]


@dataclass
class Cell:
    """실험 매트릭스의 한 칸: 한 모듈 x 한 입력 크기."""
    module: str
    operation: str
    input_bytes: int
    device_samples_ms: list[dict[str, float]] = field(default_factory=list)
    device_metrics: dict[str, float] = field(default_factory=dict)
    output_bytes: int = 0
    baseline: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "module": self.module,
            "operation": self.operation,
            "input_bytes": self.input_bytes,
            "output_bytes": self.output_bytes,
            "device_samples_ms": self.device_samples_ms,
            "device_metrics": self.device_metrics,
            "baseline": self.baseline,
            "verification": self.verification,
        }
        if self.error:
            data["error"] = self.error
        return data


@dataclass
class BenchResult:
    experiment: dict[str, Any]
    started_at: str
    target: str
    modules: dict[str, Any] = field(default_factory=dict)
    cells: list[Cell] = field(default_factory=list)
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "bench",
            "experiment": self.experiment,
            "started_at": self.started_at,
            "target": self.target,
            "modules": self.modules,
            "cells": [cell.to_dict() for cell in self.cells],
        }


def plan(workspace: Workspace, experiment: Experiment) -> list[dict[str, Any]]:
    """무엇을 재게 될지 -- 실제로 재기 전에 보여줄 계획."""
    return [
        {
            "module": module,
            "input_bytes": size,
            "input": benchdata.format_size(size),
            "target": experiment.target,
            "iterations": experiment.warmup + experiment.repeat,
        }
        for module, size in experiment.cells
    ]


class _CellCounter:
    """How many of the matrix's cells are finished, for the `measure` stage's progress events.

    A module that fails before its cells run (unknown module, failed build, no device) still
    moves the count past its cells, so a reader sees the matrix reach its total.
    """

    def __init__(self, total: int) -> None:
        self.total = total
        self.done = 0
        self._module_start = 0

    def begin_module(self) -> None:
        self._module_start = self.done

    def cell_done(self, stage, module_id: str, input_bytes: int) -> None:
        self.done += 1
        stage.progress("cells", current=self.done, total=self.total, unit="cells",
                       message=f"{module_id} {benchdata.format_size(input_bytes)}")

    def end_module(self, cells: int) -> None:
        self.done = self._module_start + cells


def run(workspace: Workspace, experiment: Experiment, *,
        device_index: int | None = None, timeout_seconds: float | None = None,
        device_timeout_s: float | None = None,
        report: Reporter | None = None, events: Sink | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None) -> BenchResult:
    say = report or (lambda _message: None)
    # 시작할 때 정한다: 장치를 예약하는 동안 락 파일에 이 id 가 적혀 다른 사용자가 누구인지 볼 수 있다.
    run_id = record.new_run_id()
    stream = start_run(workspace, run_id, kind="bench", module=",".join(experiment.modules),
                       target=experiment.target, sink=events, on_event=on_event)
    try:
        result = _run_matrix(workspace, experiment, run_id, stream, device_index=device_index,
                             timeout_seconds=timeout_seconds, device_timeout_s=device_timeout_s,
                             say=say)
    except KeyboardInterrupt:
        stream.finish("cancelled")
        raise
    except BaseException as exc:
        stream.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    failed = [cell for cell in result.cells if cell.error]
    if failed:
        stream.finish("failed", f"{len(failed)} of {len(result.cells)} cells failed: "
                                f"{failed[0].error}")
    else:
        stream.finish("succeeded")
    return result


def _run_matrix(workspace: Workspace, experiment: Experiment, run_id: str, stream: RunEvents, *,
                device_index: int | None, timeout_seconds: float | None,
                device_timeout_s: float | None, say: Reporter) -> BenchResult:
    owner = Owner(run_id=run_id, pid=os.getpid(), purpose="bench")
    counter = _CellCounter(len(experiment.modules) * len(experiment.input_sizes))
    result = BenchResult(
        experiment=experiment.to_dict(),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        target=experiment.target,
    )

    with stream.stage("prepare", sizes=len(experiment.input_sizes)):
        inputs = {
            size: benchdata.ensure(workspace, size, experiment.input_fill)
            for size in experiment.input_sizes
        }

    for module_id in experiment.modules:
        say(f"[{module_id}] preparing")
        try:
            manifest = workspace.load(module_id)
        except (LookupError, ValueError) as exc:
            result.modules[module_id] = {"error": str(exc)}
            for size in experiment.input_sizes:
                result.cells.append(Cell(module=module_id, operation="?",
                                         input_bytes=size, error=str(exc)))
            counter.done += len(experiment.input_sizes)
            continue

        module_cells = [
            Cell(module=module_id, operation=manifest.operation_id, input_bytes=size)
            for size in experiment.input_sizes
        ]
        result.cells.extend(module_cells)
        counter.begin_module()
        summary = _measure_module(
            workspace, manifest, experiment, module_cells, inputs,
            device_index=device_index, timeout_seconds=timeout_seconds,
            device_timeout_s=device_timeout_s, owner=owner, stream=stream,
            counter=counter, say=say,
        )
        counter.end_module(len(module_cells))
        result.modules[module_id] = summary

    with stream.stage("persist"):
        result.path = _persist(workspace, experiment, result)
        record.append(workspace, {
            "run_id": run_id,
            "kind": "bench",
            "experiment": experiment.id,
            "module": ",".join(experiment.modules),
            "target": experiment.target,
            "status": "succeeded" if not any(cell.error for cell in result.cells) else "partial",
            "started_at": result.started_at,
            "cells": len(result.cells),
            "benchmark": str(result.path),
        })
    return result


def _measure_module(workspace: Workspace, manifest: Manifest, experiment: Experiment,
                    cells: list[Cell], inputs: dict[int, Path], *,
                    device_index: int | None, timeout_seconds: float | None,
                    device_timeout_s: float | None, owner: Owner, stream: RunEvents,
                    counter: _CellCounter, say: Reporter) -> dict[str, Any]:
    summary: dict[str, Any] = {"operation": manifest.operation_id}
    try:
        with stream.stage("build", module=manifest.id, target=experiment.target) as build_stage:
            built = build(workspace, manifest, experiment.target,
                          timeout_seconds=timeout_seconds, stage=build_stage)
            build_stage.succeed(fingerprint=built.fingerprint, cached=built.cached)
    except Exception as exc:
        message = f"build failed: {exc}"
        for cell in cells:
            cell.error = message
        return {**summary, "error": message}
    summary["build"] = {"fingerprint": built.fingerprint, "cached": built.cached}
    say(f"[{manifest.id}] build {'cache hit' if built.cached else 'built'} "
        f"({built.fingerprint})")

    # 1 단계: 디바이스. 락을 쥐고 있는 동안 CPU 일은 하지 않는다.
    device_error = _measure_on_device(
        workspace, manifest, experiment, cells, inputs, summary,
        binary=built.binary, device_index=device_index,
        device_timeout_s=device_timeout_s, owner=owner, stream=stream,
        counter=counter, say=say,
    )
    if device_error:
        return {**summary, "error": device_error}

    # 2 단계: CPU 기준선과 정확성. 락 밖에서 돈다.
    if experiment.baseline:
        with stream.stage("baseline", module=manifest.id):
            _measure_baseline(manifest, experiment, cells, inputs, say=say)
    return summary


def _measure_on_device(workspace: Workspace, manifest: Manifest, experiment: Experiment,
                       cells: list[Cell], inputs: dict[int, Path],
                       summary: dict[str, Any], *, binary: Path,
                       device_index: int | None, device_timeout_s: float | None,
                       owner: Owner, stream: RunEvents, counter: _CellCounter,
                       say: Reporter) -> str:
    try:
        execute = load_callable(
            manifest.adapter_directory, "adapter.py", "execute",
            f"operation_{manifest.operation_id}",
        )
    except ModuleCodeError as exc:
        for cell in cells:
            cell.error = str(exc)
        return str(exc)

    if not binary.exists():
        message = f"build produced no loadable image: {binary}"
        for cell in cells:
            cell.error = message
        return message

    def announce(info: WaitInfo) -> None:
        say(f"[{manifest.id}] {describe_wait(info)}")

    try:
        with Runtime(workspace).session(manifest, binary, experiment.target, device_index,
                                        owner=owner, timeout_s=device_timeout_s,
                                        on_wait=announce, stream=stream) as handle:
            session = handle.session
            summary["device_open_ms"] = round(handle.open_ms, 3)
            summary["device_wait_ms"] = round(handle.wait_ms, 3)
            summary["device"] = {
                "backend": session.device.backend,
                "index": session.device.index,
                "name": session.device.name,
                "emulated": session.device.emulated,
                "id": session.device.id,
            }
            say(f"[{manifest.id}] device open in {summary['device_open_ms']}ms "
                f"-- excluded from every sample below")
            with stream.stage("measure", module=manifest.id) as measure:
                for cell in cells:
                    _measure_cell(session, execute, manifest, experiment, cell,
                                  inputs[cell.input_bytes], say=say)
                    counter.cell_done(measure, manifest.id, cell.input_bytes)
    except BackendError as exc:
        for cell in cells:
            cell.error = cell.error or str(exc)
        return str(exc)
    return ""


def _measure_cell(session, execute, manifest: Manifest, experiment: Experiment,
                  cell: Cell, input_path: Path, *, say: Reporter) -> None:
    data = input_path.read_bytes()
    try:
        manifest.validate_input(data)
    except ValueError as exc:
        cell.error = str(exc)
        return

    label = f"[{manifest.id}] {benchdata.format_size(cell.input_bytes)}"
    parameters = manifest.adapter_parameters
    try:
        for _ in range(experiment.warmup):
            execute(session, data, parameters)

        digest = hashlib.sha256()
        for index in range(experiment.repeat):
            session.timings.reset()
            session.metrics.clear()
            started = time.perf_counter()
            output = execute(session, data, parameters)
            total_ms = (time.perf_counter() - started) * 1000.0
            if not isinstance(output, (bytes, bytearray)):
                raise ModuleCodeError(
                    f"operation {manifest.operation_id!r} adapter execute() must return "
                    f"bytes, got {type(output).__name__}"
                )
            # 나노초 해상도로 남긴다. 밀리초 4 자리에서 자르면 작은 입력의
            # 마이크로초 단위 차이가 뭉개지는데, 교차점은 바로 그 구간에서 갈린다.
            cell.device_samples_ms.append({
                "total_ms": round(total_ms, 6),
                "host_to_device_ms": round(session.timings.host_to_device_ms, 6),
                "kernel_ms": round(session.timings.kernel_ms, 6),
                "device_to_host_ms": round(session.timings.device_to_host_ms, 6),
            })
            if index == 0:
                # 큰 출력을 통째로 들고 있지 않는다. 정확성 대조는 다이제스트로 한다.
                cell.output_bytes = len(output)
                cell.device_metrics = dict(session.metrics)
                digest.update(output)
        cell.verification["device_sha256"] = digest.hexdigest()
    except (BackendError, ModuleCodeError, ValueError) as exc:
        cell.error = str(exc)
    except Exception as exc:
        cell.error = f"{type(exc).__name__}: {exc}"
    else:
        say(f"{label}: {len(cell.device_samples_ms)} samples, "
            f"p50 {_median([s['total_ms'] for s in cell.device_samples_ms]):.3f}ms")


def _measure_baseline(manifest: Manifest, experiment: Experiment, cells: list[Cell],
                      inputs: dict[int, Path], *, say: Reporter) -> None:
    operation = manifest.operation
    available, reason = baseline_module.probe(operation, manifest.parameters)
    if not available:
        say(f"[{manifest.id}] CPU 기준선 없음 -- {reason}")
        for cell in cells:
            cell.baseline = {"available": False, "reason": reason}
            cell.verification.setdefault("mode", "skipped")
            cell.verification.setdefault("detail", "CPU 기준선이 없어 대조하지 않았습니다")
        return

    for cell in cells:
        if cell.error:
            continue
        data = inputs[cell.input_bytes].read_bytes()
        measurement = baseline_module.measure(
            operation, data, manifest.parameters,
            repeat=experiment.repeat, warmup=experiment.warmup,
        )
        cell.baseline = measurement.to_dict()
        cell.baseline["description"] = operation.baseline_description
        _verify(operation, cell, measurement, experiment)
        if measurement.available:
            say(f"[{manifest.id}] {benchdata.format_size(cell.input_bytes)} baseline "
                f"p50 {_median(measurement.samples_ms):.3f}ms")


def _verify(operation, cell: Cell, measurement, experiment: Experiment) -> None:
    if not experiment.verify:
        cell.verification.update(mode="skipped", detail="measure.verify 가 false 입니다")
        return
    if operation.baseline_equivalence != "exact":
        cell.verification.update(
            mode="none",
            detail=(f"{operation.id} 는 CPU 구현과 바이트가 달라도 올바릅니다 "
                    "(baseline.equivalence: none) -- 성능 비교에만 사용합니다"),
        )
        return
    if not measurement.available:
        cell.verification.update(mode="cpu_baseline", passed=None,
                                 detail=measurement.reason)
        return
    expected = hashlib.sha256(measurement.output).hexdigest()
    actual = cell.verification.get("device_sha256")
    passed = expected == actual
    cell.verification.update(
        mode="cpu_baseline", passed=passed,
        detail=("CPU 기준선과 일치" if passed else
                f"CPU 기준선과 불일치: 기준선 {len(measurement.output)}바이트 "
                f"sha256 {expected[:16]}…, 장치 {cell.output_bytes}바이트 "
                f"sha256 {(actual or '')[:16]}…"),
    )


def _persist(workspace: Workspace, experiment: Experiment,
             result: BenchResult) -> Path:
    workspace.benchmarks_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = workspace.benchmarks_dir / f"{experiment.id}-{stamp}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False,
                               default=str))
    return path


def latest(workspace: Workspace, experiment_id: str) -> Path:
    workspace.benchmarks_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted(workspace.benchmarks_dir.glob(f"{experiment_id}-*.json"))
    if not candidates:
        raise LookupError(
            f"실험 {experiment_id!r} 의 측정 결과가 없습니다. 먼저 `isc bench "
            f"{experiment_id}` 를 실행하십시오"
        )
    return candidates[-1]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0
