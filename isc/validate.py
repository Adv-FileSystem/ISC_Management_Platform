"""Correctness check of a run's output.

One validation mode: cross_target. The same input is re-executed on the
module's reference target (module.yaml: validation.reference_target, normally
hw_emu) and the two outputs are compared byte for byte. sw_emu was removed in
Vitis 2025.1, so it is never a usable reference target -- see isc/check.py.

Running the module on the reference target itself has nothing to compare
against, so that case always passes; isc/lifecycle.py is what decides whether
a reference execution is needed and supplies its output here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ValidationResult:
    mode: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "passed": self.passed, "detail": self.detail}


def validate(mode: str, output: bytes, reference_output: bytes | None) -> ValidationResult:
    if mode == "none":
        return ValidationResult(mode, True, "validation disabled in module.yaml")
    if reference_output is None:
        return ValidationResult(mode, True, "ran on the reference target itself; nothing to compare")
    if output == reference_output:
        return ValidationResult(mode, True, f"matches reference target output ({len(output)} bytes)")
    return ValidationResult(mode, False, _mismatch(reference_output, output))


def _mismatch(reference: bytes, actual: bytes) -> str:
    if len(reference) != len(actual):
        return (f"size mismatch: reference target produced {len(reference)} bytes, "
                f"this target produced {len(actual)}")
    for offset, (want, got) in enumerate(zip(reference, actual)):
        if want != got:
            return (f"first difference at byte {offset}: reference target 0x{want:02x}, "
                    f"this target 0x{got:02x}")
    return "outputs differ"
