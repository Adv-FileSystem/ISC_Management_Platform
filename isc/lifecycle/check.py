"""Static checks that run without a device or a toolchain.

Catches mistakes that would otherwise only surface after a multi-minute build:
an invalid user parameter, an unresolved operation dependency, a damaged
platform-managed adapter, or an unusable validation target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from isc.operations import baseline as baseline_module
from isc.backends import backend_names
from isc.generator.generate import TODO_MARKER, strip_comments
from isc.operations.loader import ModuleCodeError, load_file
from isc.lifecycle.manifest import Manifest, ManifestError


@dataclass
class CheckReport:
    module_id: str
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # 실패는 아니지만 사용자가 봐야 할 것

    @property
    def passed(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module_id, "passed": self.passed,
            "problems": self.problems, "notes": self.notes, "warnings": self.warnings,
        }


def check_module(directory: Path) -> CheckReport:
    directory = Path(directory)
    report = CheckReport(module_id=directory.name)

    try:
        manifest = Manifest.load(directory)
    except ManifestError as exc:
        report.problems.append(str(exc))
        return report

    if manifest.backend not in backend_names():
        report.problems.append(
            f"runtime.backend {manifest.backend!r} is not registered "
            f"(available: {backend_names()})"
        )

    try:
        for include_dir in manifest.resolved_include_dirs():
            if not include_dir.is_dir():
                report.problems.append(f"kernel.include_dirs entry does not exist: {include_dir}")
    except ManifestError as exc:
        report.problems.append(str(exc))

    if not manifest.kernel.memory:
        report.notes.append("kernel.memory is empty: no AXI port is pinned to a memory bank")

    _check_entry_point(
        report, manifest.adapter_directory, "adapter.py", "execute",
        f"operation_{manifest.operation_id}",
    )

    if manifest.is_hook:
        _check_hook_source(report, manifest)

    _check_baseline(report, manifest)

    if manifest.validation_mode == "cross_target":
        reference_target = manifest.validation_reference_target
        if reference_target == "sw_emu":
            report.problems.append(
                "validation.reference_target is 'sw_emu', which Vitis 2025.1 removed; use 'hw_emu'"
            )
        elif reference_target not in manifest.build.targets:
            report.problems.append(
                f"validation.reference_target {reference_target!r} is not declared in "
                f"build.targets {list(manifest.build.targets)}"
            )
    else:
        report.notes.append("validation.mode is 'none': runs will not be checked for correctness")

    return report


def _check_hook_source(report: CheckReport, manifest: Manifest) -> None:
    raw = manifest.hook_source.read_text()
    # 주석 속 설명(쓸 수 없는 것을 알리는 시작 파일 머리글 등)은 검사 대상이 아니다.
    source = strip_comments(raw)
    try:
        manifest.hook_types()
    except ManifestError as exc:
        report.problems.append(str(exc))

    if TODO_MARKER in raw:
        report.warnings.append(
            f"{manifest.hook.source} still has the starter marker {TODO_MARKER}; "
            "replace the placeholder body with your logic"
        )

    forbidden = (
        (r"#\s*pragma\s+HLS\s+INTERFACE", "HLS interface pragmas"),
        (r"\b(?:malloc|calloc|realloc|free|fopen|open|socket)\s*\(",
         "dynamic allocation or host I/O"),
        (r"\bnew\s+", "dynamic allocation"),
    )
    for pattern, label in forbidden:
        if re.search(pattern, source):
            report.problems.append(
                f"hook source contains unsupported {label}; the profile owns data movement"
            )


def _check_baseline(report: CheckReport, manifest: Manifest) -> None:
    """A missing CPU baseline is only a problem when something depends on it.

    `isc bench` degrades to "no speedup for this operation" without one, which
    is a note. `validation.mode: cpu_baseline` cannot work without one, which
    is a problem.
    """
    operation = manifest.operation
    needs_baseline = manifest.validation_mode == "cpu_baseline"

    if not operation.has_baseline:
        message = (f"operation {operation.id!r} has no CPU baseline "
                   f"(baseline.py); `isc bench` cannot compute a speedup for it")
        (report.problems if needs_baseline else report.notes).append(message)
        return

    available, reason = baseline_module.probe(operation, manifest.parameters)
    if not available:
        message = f"CPU baseline for {operation.id!r} is unusable: {reason}"
        (report.problems if needs_baseline else report.notes).append(message)
        return

    if needs_baseline and operation.baseline_equivalence != "exact":
        report.problems.append(
            f"validation.mode is 'cpu_baseline' but operation {operation.id!r} declares "
            "baseline.equivalence: none, so a CPU comparison cannot decide correctness"
        )


def _check_entry_point(report: CheckReport, directory: Path, filename: str,
                       attribute: str, namespace: str) -> None:
    try:
        loaded = load_file(directory, filename, f"check_{namespace}_{attribute}")
    except ModuleCodeError as exc:
        report.problems.append(str(exc))
        return
    if not callable(getattr(loaded, attribute, None)):
        report.problems.append(f"{directory / filename} must define a callable `{attribute}`")
