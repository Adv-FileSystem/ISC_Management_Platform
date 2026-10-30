"""실험 정의 — 무엇을, 어떤 크기로, 몇 번 재는지.

셸 히스토리에 남은 벤치마크는 재현할 수 없다. 판정("이 연산은 가속 대상이
아니다")이 신뢰를 얻으려면 그 근거가 파일로 남아 버전 관리되어야 한다.
그래서 실험은 명령 인자가 아니라 experiments/<id>/experiment.yaml 이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from isc.benchdata import DEFAULT_FILL, FILL_MODES, format_size, parse_size
from isc.lifecycle.workspace import Workspace

EXPERIMENT_NAME = "experiment.yaml"
SECTIONS = {"experiment", "matrix", "measure"}
DEFAULT_TARGET = "hw"
DEFAULT_REPEAT = 10
DEFAULT_WARMUP = 2


class ExperimentError(ValueError):
    pass


@dataclass(frozen=True)
class Experiment:
    id: str
    name: str
    modules: tuple[str, ...]
    input_sizes: tuple[int, ...]
    target: str
    repeat: int
    warmup: int
    input_fill: str
    baseline: bool
    verify: bool
    directory: Path

    @property
    def is_performance_data(self) -> bool:
        """에뮬레이션 시간은 성능 수치가 아니다 -- 리포트가 이 값으로 경고한다."""
        return not self.target.endswith("_emu")

    @property
    def cells(self) -> list[tuple[str, int]]:
        return [(module, size) for module in self.modules for size in self.input_sizes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "modules": list(self.modules),
            "input_sizes": list(self.input_sizes),
            "input_sizes_human": [format_size(size) for size in self.input_sizes],
            "target": self.target,
            "repeat": self.repeat,
            "warmup": self.warmup,
            "input_fill": self.input_fill,
            "baseline": self.baseline,
            "verify": self.verify,
            "performance_data": self.is_performance_data,
        }

    @classmethod
    def load(cls, directory: Path) -> "Experiment":
        directory = Path(directory).resolve()
        path = directory / EXPERIMENT_NAME
        if not path.exists():
            raise ExperimentError(f"{path} not found")
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ExperimentError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ExperimentError(f"{path}: top level must be a mapping")
        unknown = sorted(set(raw) - SECTIONS)
        if unknown:
            raise ExperimentError(
                f"{path}: unsupported section(s): {', '.join(unknown)}"
            )

        header = _mapping(raw, "experiment", path)
        matrix = _mapping(raw, "matrix", path)
        measure = raw.get("measure") or {}
        if not isinstance(measure, dict):
            raise ExperimentError(f"{path}: measure must be a mapping")

        modules = tuple(str(item) for item in (matrix.get("modules") or ()))
        if not modules:
            raise ExperimentError(f"{path}: matrix.modules is empty")

        raw_sizes = matrix.get("input_sizes") or ()
        if not raw_sizes:
            raise ExperimentError(f"{path}: matrix.input_sizes is empty")
        try:
            sizes = tuple(sorted({parse_size(item) for item in raw_sizes}))
        except ValueError as exc:
            raise ExperimentError(f"{path}: {exc}") from exc

        fill = str(measure.get("input_fill", DEFAULT_FILL))
        if fill not in FILL_MODES:
            raise ExperimentError(
                f"{path}: measure.input_fill must be one of {list(FILL_MODES)}, got {fill!r}"
            )
        repeat = _positive(measure, "repeat", DEFAULT_REPEAT, path, minimum=1)
        warmup = _positive(measure, "warmup", DEFAULT_WARMUP, path, minimum=0)

        return cls(
            id=str(header.get("id") or directory.name),
            name=str(header.get("name") or directory.name),
            modules=modules,
            input_sizes=sizes,
            target=str(matrix.get("target") or DEFAULT_TARGET),
            repeat=repeat,
            warmup=warmup,
            input_fill=fill,
            baseline=bool(measure.get("baseline", True)),
            verify=bool(measure.get("verify", True)),
            directory=directory,
        )


def load(workspace: Workspace, experiment_id: str) -> Experiment:
    directory = workspace.experiments_dir / experiment_id
    if not (directory / EXPERIMENT_NAME).exists():
        known = ", ".join(workspace.experiment_ids()) or "none"
        raise LookupError(
            f"unknown experiment {experiment_id!r} in {workspace.experiments_dir} "
            f"(known: {known})"
        )
    return Experiment.load(directory)


def create(workspace: Workspace, experiment_id: str, *, modules: list[str],
           sizes: list[str | int], name: str | None = None,
           target: str = DEFAULT_TARGET, repeat: int = DEFAULT_REPEAT,
           warmup: int = DEFAULT_WARMUP, input_fill: str = DEFAULT_FILL) -> Path:
    if not modules:
        raise ExperimentError("실험에는 모듈이 하나 이상 필요합니다 (--modules)")
    if not sizes:
        raise ExperimentError("실험에는 입력 크기가 하나 이상 필요합니다 (--sizes)")
    unknown = [module for module in modules if module not in workspace.module_ids()]
    if unknown:
        known = ", ".join(workspace.module_ids()) or "none"
        raise ExperimentError(
            f"모듈을 찾을 수 없습니다: {', '.join(unknown)} (있는 모듈: {known}). "
            "`isc new <id> --operation <연산>` 으로 먼저 만드십시오"
        )

    destination = workspace.experiments_dir / experiment_id
    if destination.exists():
        raise FileExistsError(f"experiment already exists: {destination}")

    document = {
        "experiment": {"id": experiment_id, "name": name or experiment_id},
        "matrix": {
            "modules": list(modules),
            "input_sizes": [
                format_size(parse_size(size)) for size in sizes
            ],
            "target": target,
        },
        "measure": {
            "repeat": repeat,
            "warmup": warmup,
            "input_fill": input_fill,
            "baseline": True,
            "verify": True,
        },
    }
    destination.mkdir(parents=True)
    (destination / EXPERIMENT_NAME).write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    )
    Experiment.load(destination)  # 만들자마자 스스로 읽히는지 확인한다
    return destination


def _mapping(raw: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ExperimentError(f"{path}: missing required section: {name}")
    return value


def _positive(measure: dict[str, Any], key: str, default: int, path: Path,
              *, minimum: int) -> int:
    value = measure.get(key, default)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentError(f"{path}: measure.{key} must be an integer") from exc
    if number < minimum:
        raise ExperimentError(f"{path}: measure.{key} must be at least {minimum}")
    return number
