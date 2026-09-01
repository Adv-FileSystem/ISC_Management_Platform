"""Built-in operation catalog exposed to ordinary ISC users.

An operation owns the hardware contract and implementation.  A user module
only selects an operation and supplies parameters that its descriptor allows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


OPERATIONS_DIR = Path(__file__).parent / "operations"


class OperationError(ValueError):
    pass


@dataclass(frozen=True)
class OperationSpec:
    id: str
    name: str
    version: str
    description: str
    category: str
    directory: Path
    raw: dict[str, Any]

    @property
    def parameter_specs(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("parameters") or {}

    @property
    def input_contract(self) -> dict[str, Any]:
        return self.raw.get("input") or {}

    @property
    def output_contract(self) -> dict[str, Any]:
        return self.raw.get("output") or {}

    def resolve_parameters(self, supplied: dict[str, Any] | None = None) -> dict[str, Any]:
        supplied = supplied or {}
        unknown = sorted(set(supplied) - set(self.parameter_specs))
        if unknown:
            known = ", ".join(self.parameter_specs) or "none"
            raise OperationError(
                f"operation {self.id!r} does not support parameter(s) "
                f"{', '.join(unknown)} (available: {known})"
            )

        resolved: dict[str, Any] = {}
        for name, spec in self.parameter_specs.items():
            if name in supplied:
                value = supplied[name]
            elif "default" in spec:
                value = spec["default"]
            elif spec.get("required"):
                raise OperationError(
                    f"operation {self.id!r} requires parameter {name!r}"
                )
            else:
                continue
            resolved[name] = _validate_parameter(self.id, name, value, spec)
        return resolved

    def validate_input(self, data: bytes) -> None:
        contract = self.input_contract
        size = len(data)
        minimum = int(contract.get("min_bytes", 0))
        maximum = contract.get("max_bytes")
        multiple = int(contract.get("size_multiple", 1))
        if size < minimum:
            raise OperationError(
                f"operation {self.id!r} requires at least {minimum} input bytes; received {size}"
            )
        if maximum is not None and size > int(maximum):
            raise OperationError(
                f"operation {self.id!r} accepts at most {maximum} input bytes; received {size}"
            )
        if multiple > 1 and size % multiple:
            raise OperationError(
                f"operation {self.id!r} requires input size to be a multiple of "
                f"{multiple} bytes; received {size}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "category": self.category,
            "input": self.input_contract,
            "output": self.output_contract,
            "parameters": self.parameter_specs,
        }


def operation_ids() -> list[str]:
    if not OPERATIONS_DIR.is_dir():
        return []
    return sorted(
        entry.name for entry in OPERATIONS_DIR.iterdir()
        if entry.is_dir() and (entry / "operation.yaml").is_file()
    )


def operations() -> list[OperationSpec]:
    return [get_operation(operation_id) for operation_id in operation_ids()]


def get_operation(operation_id: str) -> OperationSpec:
    path = OPERATIONS_DIR / operation_id / "operation.yaml"
    if not path.is_file():
        known = ", ".join(operation_ids()) or "none"
        raise OperationError(
            f"unknown operation {operation_id!r} (available: {known})"
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise OperationError(f"{path}: invalid YAML: {exc}") from exc
    metadata = raw.get("operation")
    if not isinstance(metadata, dict):
        raise OperationError(f"{path}: missing operation section")
    declared_id = str(metadata.get("id") or "")
    if declared_id != operation_id:
        raise OperationError(
            f"{path}: operation.id must be {operation_id!r}, got {declared_id!r}"
        )
    return OperationSpec(
        id=declared_id,
        name=str(metadata.get("name") or declared_id),
        version=str(metadata.get("version") or "1.0"),
        description=str(metadata.get("description") or ""),
        category=str(metadata.get("category") or "general"),
        directory=path.parent.resolve(),
        raw=raw,
    )


def parse_parameter_assignments(assignments: list[str] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for assignment in assignments or []:
        if "=" not in assignment:
            raise OperationError(
                f"invalid --set {assignment!r}: expected NAME=VALUE"
            )
        name, raw_value = assignment.split("=", 1)
        name = name.strip()
        if not name:
            raise OperationError(f"invalid --set {assignment!r}: parameter name is empty")
        if name in parsed:
            raise OperationError(f"parameter {name!r} was supplied more than once")
        try:
            parsed[name] = yaml.safe_load(raw_value)
        except yaml.YAMLError as exc:
            raise OperationError(f"invalid value for parameter {name!r}: {exc}") from exc
    return parsed


def _validate_parameter(operation_id: str, name: str, value: Any,
                        spec: dict[str, Any]) -> Any:
    kind = spec.get("type", "string")
    if kind == "integer":
        if isinstance(value, bool):
            raise OperationError(f"{operation_id}.{name} must be an integer")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise OperationError(f"{operation_id}.{name} must be an integer") from exc
        if "minimum" in spec and value < int(spec["minimum"]):
            raise OperationError(
                f"{operation_id}.{name} must be at least {spec['minimum']}"
            )
        if "maximum" in spec and value > int(spec["maximum"]):
            raise OperationError(
                f"{operation_id}.{name} must be at most {spec['maximum']}"
            )
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise OperationError(f"{operation_id}.{name} must be true or false")
    elif kind in {"string", "expression"}:
        if not isinstance(value, str):
            raise OperationError(f"{operation_id}.{name} must be a string")
    elif kind == "enum":
        allowed = spec.get("values") or []
        if value not in allowed:
            raise OperationError(
                f"{operation_id}.{name} must be one of {allowed}, got {value!r}"
            )
    else:
        raise OperationError(
            f"operation {operation_id!r} has unsupported parameter type {kind!r}"
        )
    return value
