"""집계와 판정 — "이 연산은 가속 대상인가" 에 답한다.

speedup 만으로는 답이 반쪽이다. 느리다는 사실은 알려주지만 *왜* 느린지, 커널을
고치면 나아질지는 말해 주지 않는다. 그 답을 주는 지표가 상한 가속비다.

    ceiling_speedup = cpu_p50 / (host_to_device_ms + device_to_host_ms)

커널이 0 초로 끝난다고 가정한 값이다. 이 값이 1 미만이면 입력을 장치로 밀고
결과를 받아오는 것만으로 이미 CPU 전체 계산보다 느리다는 뜻이고, 그렇다면 커널을
아무리 최적화해도 이 연산은 이득이 없다. 추측이 아니라 단정할 수 있게 해 준다.
"""
from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from isc.benchdata import format_size

# 가속으로 인정하는 최소 배율. 측정 잡음과 운영 복잡도를 감안한 값이다.
ACCELERATION_THRESHOLD = 1.2

VERDICT_LABELS = {
    "accelerated": "가속 이득 있음",
    "conditional": "조건부 이득",
    "kernel_bound": "커널 개선 여지 있음",
    "transfer_bound": "가속 불가 (전송 바운드)",
    "inconclusive": "판정 보류",
}

CSV_COLUMNS = [
    "module", "operation", "input_bytes", "input", "cpu_p50_ms", "fpga_p50_ms",
    "kernel_p50_ms", "h2d_p50_ms", "d2h_p50_ms", "transfer_ms", "speedup",
    "ceiling_speedup", "transfer_share", "throughput_mbps", "verified", "error",
]


@dataclass
class CellAnalysis:
    module: str
    operation: str
    input_bytes: int
    fpga_p50_ms: float = 0.0
    fpga_min_ms: float = 0.0
    fpga_p95_ms: float = 0.0
    kernel_p50_ms: float = 0.0
    h2d_p50_ms: float = 0.0
    d2h_p50_ms: float = 0.0
    cpu_p50_ms: float | None = None
    speedup: float | None = None
    ceiling_speedup: float | None = None
    transfer_share: float | None = None
    throughput_mbps: float = 0.0
    verified: bool | None = None
    instrumentation_ok: bool = True
    verification_detail: str = ""
    baseline_reason: str = ""
    error: str = ""

    @property
    def transfer_ms(self) -> float:
        return self.h2d_p50_ms + self.d2h_p50_ms

    @property
    def usable(self) -> bool:
        return not self.error and self.speedup is not None and self.fpga_p50_ms > 0

    def to_dict(self) -> dict[str, Any]:
        data = {key: value for key, value in vars(self).items()}
        data["input"] = format_size(self.input_bytes)
        data["transfer_ms"] = round(self.transfer_ms, 4)
        return data


@dataclass
class ModuleVerdict:
    module: str
    operation: str
    verdict: str
    reason: str
    recommendation: str
    crossover_bytes: int | None = None
    best_speedup: float | None = None
    best_ceiling: float | None = None
    device_open_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {key: value for key, value in vars(self).items()}
        data["label"] = VERDICT_LABELS.get(self.verdict, self.verdict)
        if self.crossover_bytes:
            data["crossover"] = format_size(self.crossover_bytes)
        return data


@dataclass
class Analysis:
    experiment: dict[str, Any]
    target: str
    started_at: str
    source: str
    cells: list[CellAnalysis] = field(default_factory=list)
    verdicts: list[ModuleVerdict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "target": self.target,
            "started_at": self.started_at,
            "source": self.source,
            "warnings": self.warnings,
            "cells": [cell.to_dict() for cell in self.cells],
            "verdicts": [verdict.to_dict() for verdict in self.verdicts],
        }


def analyse(benchmark_path: Path) -> Analysis:
    payload = json.loads(Path(benchmark_path).read_text())
    experiment = payload.get("experiment") or {}
    analysis = Analysis(
        experiment=experiment,
        target=payload.get("target", "?"),
        started_at=payload.get("started_at", ""),
        source=str(benchmark_path),
    )
    if not experiment.get("performance_data", True):
        analysis.warnings.append(
            f"target 이 {payload.get('target')} 입니다. 에뮬레이션 시간은 성능 수치가 "
            "아니므로 speedup 을 가속 판정 근거로 쓰지 마십시오"
        )

    modules_meta = payload.get("modules") or {}
    for raw in payload.get("cells") or []:
        analysis.cells.append(_analyse_cell(raw))
    if any(not cell.instrumentation_ok for cell in analysis.cells):
        analysis.warnings.append(
            "일부 셀에서 h2d+kernel+d2h 의 합이 실행 시간을 넘습니다. 백엔드의 단계 "
            "계측이 어긋났다는 뜻이므로 상한 가속비를 그대로 신뢰하지 마십시오"
        )

    by_module: dict[str, list[CellAnalysis]] = {}
    for cell in analysis.cells:
        by_module.setdefault(cell.module, []).append(cell)
    for module, cells in by_module.items():
        meta = modules_meta.get(module) or {}
        analysis.verdicts.append(_verdict(module, cells, meta))
    return analysis


def _analyse_cell(raw: dict[str, Any]) -> CellAnalysis:
    cell = CellAnalysis(
        module=raw.get("module", "?"),
        operation=raw.get("operation", "?"),
        input_bytes=int(raw.get("input_bytes", 0)),
        error=raw.get("error", ""),
    )
    samples = raw.get("device_samples_ms") or []
    if samples:
        totals = [float(sample["total_ms"]) for sample in samples]
        cell.fpga_p50_ms = _percentile(totals, 50)
        cell.fpga_min_ms = min(totals)
        cell.fpga_p95_ms = _percentile(totals, 95)
        cell.kernel_p50_ms = _percentile(
            [float(sample["kernel_ms"]) for sample in samples], 50)
        cell.h2d_p50_ms = _percentile(
            [float(sample["host_to_device_ms"]) for sample in samples], 50)
        cell.d2h_p50_ms = _percentile(
            [float(sample["device_to_host_ms"]) for sample in samples], 50)
        if cell.fpga_p50_ms > 0:
            cell.throughput_mbps = round(
                cell.input_bytes / (1000.0 * cell.fpga_p50_ms), 3)
            share = cell.transfer_ms / cell.fpga_p50_ms
            # 단계 타이머의 합이 벽시계를 넘으면 백엔드 계측이 어긋난 것이다.
            # 그런 값을 "전송이 실행 시간의 380000%" 처럼 비율로 내보내면
            # 리포트가 스스로를 불신하게 만든다.
            cell.instrumentation_ok = share <= 1.05
            cell.transfer_share = round(min(share, 1.0), 4)

    baseline = raw.get("baseline") or {}
    if baseline.get("available") and baseline.get("samples_ms"):
        cell.cpu_p50_ms = _percentile(
            [float(value) for value in baseline["samples_ms"]], 50)
        if cell.fpga_p50_ms > 0:
            cell.speedup = round(cell.cpu_p50_ms / cell.fpga_p50_ms, 4)
        # 커널이 0 초여도 넘을 수 없는 상한. 전송 시간이 0 으로 측정되면
        # (백엔드가 단계 타이머를 채우지 않는 경우) 상한을 말할 수 없다.
        if cell.transfer_ms > 0:
            cell.ceiling_speedup = round(cell.cpu_p50_ms / cell.transfer_ms, 4)
    elif baseline:
        cell.baseline_reason = str(baseline.get("reason", ""))

    verification = raw.get("verification") or {}
    if verification.get("passed") is not None:
        cell.verified = bool(verification["passed"])
    cell.verification_detail = str(verification.get("detail", ""))
    return cell


def _verdict(module: str, cells: list[CellAnalysis],
             meta: dict[str, Any]) -> ModuleVerdict:
    operation = next((cell.operation for cell in cells), "?")
    device_open = meta.get("device_open_ms")
    usable = [cell for cell in cells if cell.usable]

    def make(verdict: str, reason: str, recommendation: str,
             crossover: int | None = None) -> ModuleVerdict:
        return ModuleVerdict(
            module=module, operation=operation, verdict=verdict, reason=reason,
            recommendation=recommendation, crossover_bytes=crossover,
            best_speedup=max((c.speedup for c in usable), default=None),
            best_ceiling=max(
                (c.ceiling_speedup for c in usable if c.ceiling_speedup is not None),
                default=None),
            device_open_ms=device_open,
        )

    failed = [cell for cell in cells if cell.error]
    if not usable:
        reason = meta.get("error") or (
            failed[0].error if failed else
            next((cell.baseline_reason for cell in cells if cell.baseline_reason),
                 "측정 표본이 없습니다")
        )
        return make("inconclusive", reason,
                    "CPU 기준선을 마련하거나 실패 원인을 해결한 뒤 다시 측정하십시오")

    mismatched = [cell for cell in usable if cell.verified is False]
    if mismatched:
        return make(
            "inconclusive",
            f"{format_size(mismatched[0].input_bytes)} 에서 장치 출력이 CPU 기준선과 "
            "다릅니다 -- 성능을 논하기 전에 정확성을 먼저 해결해야 합니다",
            "커널 또는 어댑터의 정확성 문제를 먼저 수정하십시오",
        )

    speedups = [cell.speedup for cell in usable]
    ceilings = [cell.ceiling_speedup for cell in usable
                if cell.ceiling_speedup is not None]
    best = max(speedups)
    largest = max(usable, key=lambda cell: cell.input_bytes)
    open_note = (
        f" 디바이스 오픈에 {device_open:.0f}ms 가 드는 1 회성 비용이 있으므로 "
        "한 번 열고 여러 건을 처리하는 형태여야 합니다."
        if device_open else ""
    )

    if min(speedups) >= ACCELERATION_THRESHOLD:
        return make(
            "accelerated",
            f"측정한 모든 입력 크기에서 {min(speedups):.2f}x 이상 (최대 {best:.2f}x)",
            f"상시 오프로드 대상입니다.{open_note}",
        )

    if best >= 1.0:
        crossover = _crossover(usable)
        where = format_size(crossover) if crossover else format_size(largest.input_bytes)
        return make(
            "conditional",
            f"작은 입력에서는 CPU 가 빠르고 {where} 부근에서 역전합니다 "
            f"(최대 {best:.2f}x)",
            f"{where} 이상인 입력만 오프로드하고 그 아래는 CPU 로 처리하도록 "
            f"분기하십시오.{open_note}",
            crossover,
        )

    if ceilings and max(ceilings) < 1.0:
        share = largest.transfer_share or 0.0
        return make(
            "transfer_bound",
            f"커널 시간을 0 으로 가정해도 최대 {max(ceilings):.2f}x 입니다 -- "
            f"입력을 장치로 옮기는 것만으로 이미 CPU 전체 계산보다 느립니다 "
            f"(가장 큰 입력에서 전송이 실행 시간의 {share:.0%})",
            "이 연산은 가속 대상에서 제외하십시오. 커널을 개선해도 이득이 없습니다. "
            "굳이 장치에서 처리해야 한다면 이 연산만 떼어 오프로드하지 말고, "
            "데이터가 이미 장치에 올라와 있는 더 큰 파이프라인의 한 단계로 붙이십시오",
        )

    if ceilings and max(ceilings) >= ACCELERATION_THRESHOLD:
        return make(
            "kernel_bound",
            f"현재는 최대 {best:.2f}x 로 느리지만 전송 비용만 보면 상한이 "
            f"{max(ceilings):.2f}x 입니다 -- 병목이 전송이 아니라 커널입니다",
            "커널 최적화(파이프라이닝, 병렬 처리 유닛 증설)에 투자할 가치가 있습니다. "
            "현재 상태로는 오프로드하지 마십시오",
        )

    return make(
        "transfer_bound",
        f"최대 {best:.2f}x 이고 전송 비용을 빼도 상한이 "
        f"{max(ceilings) if ceilings else float('nan'):.2f}x 에 그칩니다",
        "가속 여지가 거의 없습니다. 다른 연산을 우선 검토하십시오",
    )


def _crossover(cells: list[CellAnalysis]) -> int | None:
    """speedup 이 1.0 을 넘는 입력 크기. 인접 두 셀을 로그선형 보간한다."""
    ordered = sorted(cells, key=lambda cell: cell.input_bytes)
    previous: CellAnalysis | None = None
    for cell in ordered:
        if cell.speedup is not None and cell.speedup >= 1.0:
            if previous is None or previous.speedup is None:
                return cell.input_bytes
            low, high = previous.speedup, cell.speedup
            if high == low:
                return cell.input_bytes
            fraction = (1.0 - low) / (high - low)
            log_size = (math.log(previous.input_bytes)
                        + fraction * (math.log(cell.input_bytes)
                                      - math.log(previous.input_bytes)))
            return int(math.exp(log_size))
        previous = cell
    return None


def render(analysis: Analysis) -> str:
    lines: list[str] = []
    experiment = analysis.experiment
    lines.append(f"experiment : {experiment.get('id')} — {experiment.get('name')}")
    lines.append(f"target     : {analysis.target}   "
                 f"repeat={experiment.get('repeat')} warmup={experiment.get('warmup')} "
                 f"fill={experiment.get('input_fill')}")
    lines.append(f"source     : {analysis.source}")
    for warning in analysis.warnings:
        lines.append(f"warning    : {warning}")
    lines.append("")

    columns = (("MODULE", 14, "<"), ("OP", 10, "<"), ("INPUT", 7, ">"),
               ("CPU ms", 10, ">"), ("FPGA ms", 10, ">"), ("KERNEL", 10, ">"),
               ("H2D", 10, ">"), ("D2H", 10, ">"), ("SPEEDUP", 8, ">"),
               ("CEILING", 8, ">"), ("V", 1, ">"))
    lines.append(_row([name for name, _, _ in columns], columns))
    lines.append("-" * len(lines[-1]))
    for cell in analysis.cells:
        if cell.error:
            lines.append(f"{cell.module:<14} {cell.operation:<10} "
                         f"{format_size(cell.input_bytes):>7}  ERROR: {cell.error}")
            continue
        lines.append(_row([
            cell.module, cell.operation, format_size(cell.input_bytes),
            _ms(cell.cpu_p50_ms), _ms(cell.fpga_p50_ms), _ms(cell.kernel_p50_ms),
            _ms(cell.h2d_p50_ms), _ms(cell.d2h_p50_ms),
            _ratio(cell.speedup), _ratio(cell.ceiling_speedup),
            _mark(cell.verified),
        ], columns))

    lines.append("")
    lines.append("판정")
    lines.append("-" * 4)
    for verdict in analysis.verdicts:
        label = VERDICT_LABELS.get(verdict.verdict, verdict.verdict)
        lines.append(f"[{verdict.module}] {verdict.operation}: {label}")
        lines.append(f"  근거: {verdict.reason}")
        lines.append(f"  권고: {verdict.recommendation}")
        lines.append("")
    lines.append("V 열: + 정확성 확인됨, - 불일치, . 대조하지 않음")
    lines.append("CEILING: 커널이 0 초여도 넘을 수 없는 상한 (cpu / 전송시간). "
                 "1 미만이면 커널 개선으로도 이득 없음")
    return "\n".join(lines) + "\n"


def to_csv(analysis: Analysis) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for cell in analysis.cells:
        writer.writerow(cell.to_dict())
    return buffer.getvalue()


def _row(values: list[str], columns) -> str:
    """열 사이에 최소 한 칸을 보장한다 -- 값이 커도 옆 열과 붙지 않는다."""
    return " ".join(
        f"{value:{align}{width}}" if len(str(value)) <= width else str(value)
        for value, (_, width, align) in zip(values, columns)
    ).rstrip()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (percentile / 100.0) * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return round(ordered[low], 4)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 4)


def _ms(value: float | None) -> str:
    """유효 숫자를 유지하면서 폭이 넘치지 않는 밀리초 표기.

    실제 실행에서 전송 시간은 몇 마이크로초부터 1 GB 전송의 수백 밀리초까지
    걸친다. 고정 소수점으로 찍으면 큰 값에서 옆 열과 붙어 표가 읽히지 않는다.
    """
    if value is None:
        return "-"
    if value >= 10000:
        return f"{value:.4g}"
    if value >= 100:
        return f"{value:.1f}"
    return f"{value:.3f}"


def _ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}x"


def _mark(verified: bool | None) -> str:
    return {True: "+", False: "-", None: "."}[verified]
