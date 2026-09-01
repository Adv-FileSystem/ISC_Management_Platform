"""Static checks that run without a device or a toolchain.

Catches mistakes that would otherwise only surface after a multi-minute build:
an invalid user parameter, an unresolved operation dependency, a damaged
platform-managed adapter, or an unusable validation target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from isc.backends import backend_names
from isc.loader import ModuleCodeError, load_file
from isc.manifest import Manifest, ManifestError


@dataclass
class CheckReport:
    module_id: str
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module_id, "passed": self.passed,
            "problems": self.problems, "notes": self.notes,
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


def _check_entry_point(report: CheckReport, directory: Path, filename: str,
                       attribute: str, namespace: str) -> None:
    try:
        loaded = load_file(directory, filename, f"check_{namespace}_{attribute}")
    except ModuleCodeError as exc:
        report.problems.append(str(exc))
        return
    if not callable(getattr(loaded, attribute, None)):
        report.problems.append(f"{directory / filename} must define a callable `{attribute}`")
