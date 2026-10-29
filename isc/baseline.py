"""CPU 기준선 — 가속 효과 판정의 비교 대상.

FPGA 가속에 이득이 있는지는 장치 시간만 봐서는 알 수 없다. 같은 입력을 같은
호스트의 CPU 로 처리했을 때와 견줘야 한다. 그 비교 대상을 연산 패키지가 직접
들고 있게 하는 것이 이 모듈이다.

기준선은 두 가지 일을 한다.

1. 성능 비교 대상 (`isc bench`)
2. `baseline.equivalence: exact` 인 연산에 한해 정확성 참조
   (`validation.mode: cpu_baseline`) -- hw_emu 레퍼런스 런보다 훨씬 싸다.

기준선이 없거나 외부 패키지를 요구하는데 설치되어 있지 않으면 BaselineUnavailable
을 올린다. 다른 알고리즘으로 조용히 대체하지 않는다: 틀린 speedup 을 내느니
비교하지 않는 편이 낫다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from isc.loader import ModuleCodeError, load_callable, load_file
from isc.operations import BASELINE_FILENAME, OperationSpec


class BaselineUnavailable(RuntimeError):
    """이 연산에는 쓸 수 있는 CPU 기준선이 없다."""


def load(operation: OperationSpec) -> Callable[[bytes, dict[str, Any]], bytes]:
    if not operation.has_baseline:
        raise BaselineUnavailable(
            f"연산 {operation.id!r} 에는 CPU 기준선이 없습니다 "
            f"({operation.directory / BASELINE_FILENAME} 없음)"
        )
    try:
        return load_callable(
            operation.directory, BASELINE_FILENAME, "compute",
            f"baseline_{operation.id}",
        )
    except ModuleCodeError as exc:
        raise BaselineUnavailable(str(exc)) from exc


def compute(operation: OperationSpec, data: bytes,
            parameters: dict[str, Any]) -> bytes:
    result = load(operation)(data, parameters)
    if not isinstance(result, (bytes, bytearray)):
        raise BaselineUnavailable(
            f"연산 {operation.id!r} 의 baseline.compute() 는 bytes 를 반환해야 "
            f"하는데 {type(result).__name__} 를 반환했습니다"
        )
    return bytes(result)


DEFAULT_PROBE_INPUT = b"probe"


def probe_input(operation: OperationSpec) -> bytes:
    """`probe` 가 기준선에 먹이는 입력: 기준선 파일이 `PROBE_INPUT` 을 선언했으면 그것.

    머리글이나 고정 배치가 있는 입력은 아무 바이트나 받을 수 없다. 그런 연산의 기준선은
    올바른 작은 입력이 어떤 모양인지 스스로 말한다. 선언이 없으면 구조 없는 입력을 받는
    연산이 쓰는 평범한 바이트다.
    """
    if not operation.has_baseline:
        return DEFAULT_PROBE_INPUT
    try:
        loaded = load_file(operation.directory, BASELINE_FILENAME,
                           f"baseline_{operation.id}_probe")
    except ModuleCodeError as exc:
        raise BaselineUnavailable(str(exc)) from exc
    declared = getattr(loaded, "PROBE_INPUT", DEFAULT_PROBE_INPUT)
    if not isinstance(declared, (bytes, bytearray)):
        raise BaselineUnavailable(
            f"{operation.directory / BASELINE_FILENAME}: PROBE_INPUT must be bytes, "
            f"not {type(declared).__name__}"
        )
    return bytes(declared)


def probe(operation: OperationSpec, parameters: dict[str, Any]) -> tuple[bool, str]:
    """기준선을 실제로 한 번 돌려 쓸 수 있는지 확인한다.

    `requires` 로 선언된 패키지가 없는 경우는 import 시점이 아니라 호출 시점에
    드러난다. 매트릭스를 다 돌고 나서 알게 되면 늦으므로 미리 확인한다.
    """
    try:
        compute(operation, probe_input(operation), parameters)
    except BaselineUnavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, operation.baseline_description or "available"


@dataclass
class BaselineMeasurement:
    samples_ms: list[float] = field(default_factory=list)
    output: bytes = b""
    available: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "samples_ms": self.samples_ms,
            "output_bytes": len(self.output),
        }


def measure(operation: OperationSpec, data: bytes, parameters: dict[str, Any], *,
            repeat: int, warmup: int) -> BaselineMeasurement:
    """CPU 기준선을 warmup 회 버리고 repeat 회 측정한다.

    장치 측 측정과 같은 회차 규약을 쓴다 -- 한쪽만 워밍업하면 비교가 기운다.
    """
    try:
        function = load(operation)
    except BaselineUnavailable as exc:
        return BaselineMeasurement(available=False, reason=str(exc))

    try:
        output = b""
        for _ in range(max(0, warmup)):
            function(data, parameters)
        samples: list[float] = []
        for index in range(max(1, repeat)):
            started = time.perf_counter()
            produced = function(data, parameters)
            samples.append((time.perf_counter() - started) * 1000.0)
            if index == 0:
                output = bytes(produced)
    except BaselineUnavailable as exc:
        return BaselineMeasurement(available=False, reason=str(exc))
    except Exception as exc:
        return BaselineMeasurement(
            available=False, reason=f"{type(exc).__name__}: {exc}")

    return BaselineMeasurement(samples_ms=samples, output=output)
