"""Built-in operation catalog exposed to ordinary ISC users.

An operation owns the hardware contract and implementation.  A user module
only selects an operation and supplies parameters that its descriptor allows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from isc.elements import ELEMENTS


OPERATIONS_DIR = Path(__file__).parent / "operations"

# 이름이 바뀐 프로파일의 옛 이름. 옛 module.yaml 이 계속 읽히도록 정준 id 로 풀어 준다.
PROFILE_ALIASES = {"table_filter_u32": "filter"}


def canonical_profile_id(profile_id: str) -> str:
    return PROFILE_ALIASES.get(profile_id, profile_id)

# Where a parameter takes effect. A `build` parameter can change the device
# binary and therefore keys the build cache; a `host` parameter is read by the
# adapter at run time and leaves the binary byte-identical.  The default is
# `build`: a forgotten annotation must cost a rebuild, never a stale binary.
PARAMETER_SCOPES = ("build", "host")
DEFAULT_PARAMETER_SCOPE = "build"

# How a CPU baseline relates to the device output. `exact` means the two must
# be byte-identical, so the baseline doubles as a correctness reference.
# `none` means the baseline is a fair performance comparison but a different
# valid answer -- LZ4 match finding differs between implementations, so
# comparing bytes there would report a failure that is not one.
BASELINE_EQUIVALENCE = ("exact", "none")
BASELINE_FILENAME = "baseline.py"


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
    kind: str = "builtin"

    @property
    def hook_spec(self) -> dict[str, Any]:
        return self.raw.get("hook") or {}

    @property
    def parameter_specs(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("parameters") or {}

    def parameter_scope(self, name: str) -> str:
        spec = self.parameter_specs.get(name) or {}
        return str(spec.get("scope", DEFAULT_PARAMETER_SCOPE))

    def build_parameters(self, resolved: dict[str, Any]) -> dict[str, Any]:
        """The subset of `resolved` that can change the device binary.

        Host-scoped parameters are excluded so that changing one does not
        invalidate the build cache: rebuilding for hours to produce a
        byte-identical binary is the difference between a usable knob and an
        unusable one.
        """
        return {
            name: value for name, value in resolved.items()
            if self.parameter_scope(name) == "build"
        }

    @property
    def baseline_spec(self) -> dict[str, Any]:
        return self.raw.get("baseline") or {}

    @property
    def has_baseline(self) -> bool:
        return (self.directory / BASELINE_FILENAME).is_file()

    @property
    def baseline_equivalence(self) -> str:
        return str(self.baseline_spec.get("equivalence", "none"))

    @property
    def baseline_description(self) -> str:
        return str(self.baseline_spec.get("description", ""))

    @property
    def baseline_requires(self) -> str:
        """Optional third-party package the baseline needs, for a clear message."""
        return str(self.baseline_spec.get("requires", ""))

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
            "kind": self.kind,
            "input": self.input_contract,
            "output": self.output_contract,
            "parameters": self.parameter_specs,
            "baseline": {
                "available": self.has_baseline,
                "equivalence": self.baseline_equivalence,
                "description": self.baseline_description,
                "requires": self.baseline_requires,
            },
        }


def _catalog_ids() -> list[str]:
    if not OPERATIONS_DIR.is_dir():
        return []
    return sorted(
        entry.name for entry in OPERATIONS_DIR.iterdir()
        if entry.is_dir() and (entry / "operation.yaml").is_file()
    )


def operations() -> list[OperationSpec]:
    return [get_operation(operation_id) for operation_id in operation_ids()]


def operation_ids() -> list[str]:
    return [entry_id for entry_id in _catalog_ids() if _load_spec(entry_id).kind == "builtin"]


def profile_ids() -> list[str]:
    return [entry_id for entry_id in _catalog_ids() if _load_spec(entry_id).kind == "hook"]


def profiles() -> list[OperationSpec]:
    return [get_profile(profile_id) for profile_id in profile_ids()]


def get_operation(operation_id: str) -> OperationSpec:
    if operation_id not in _catalog_ids():
        known = ", ".join(operation_ids()) or "none"
        raise OperationError(
            f"unknown operation {operation_id!r} (available: {known})"
        )
    operation = _load_spec(operation_id)
    if operation.kind != "builtin":
        known = ", ".join(operation_ids()) or "none"
        raise OperationError(
            f"unknown operation {operation_id!r} (available: {known})"
        )
    return operation


def get_profile(profile_id: str) -> OperationSpec:
    profile_id = canonical_profile_id(profile_id)
    if profile_id not in _catalog_ids():
        known = ", ".join(profile_ids()) or "none"
        raise OperationError(
            f"unknown hook profile {profile_id!r} (available: {known})"
        )
    profile = _load_spec(profile_id)
    if profile.kind != "hook":
        known = ", ".join(profile_ids()) or "none"
        raise OperationError(
            f"unknown hook profile {profile_id!r} (available: {known})"
        )
    return profile


def _load_spec(entry_id: str) -> OperationSpec:
    path = OPERATIONS_DIR / entry_id / "operation.yaml"
    if not path.is_file():
        known = ", ".join(_catalog_ids()) or "none"
        raise OperationError(
            f"unknown catalog entry {entry_id!r} (available: {known})"
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise OperationError(f"{path}: invalid YAML: {exc}") from exc
    metadata = raw.get("operation")
    if not isinstance(metadata, dict):
        raise OperationError(f"{path}: missing operation section")
    declared_id = str(metadata.get("id") or "")
    if declared_id != entry_id:
        raise OperationError(
            f"{path}: operation.id must be {entry_id!r}, got {declared_id!r}"
        )
    kind = str(metadata.get("kind") or "builtin")
    if kind not in {"builtin", "hook"}:
        raise OperationError(f"{path}: operation.kind must be 'builtin' or 'hook'")
    _validate_parameter_specs(path, raw.get("parameters") or {})
    _validate_baseline_spec(path, raw.get("baseline") or {})
    if kind == "hook":
        _validate_hook_spec(path, raw.get("hook"))
    return OperationSpec(
        id=declared_id,
        name=str(metadata.get("name") or declared_id),
        version=str(metadata.get("version") or "1.0"),
        description=str(metadata.get("description") or ""),
        category=str(metadata.get("category") or "general"),
        kind=kind,
        directory=path.parent.resolve(),
        raw=raw,
    )


def _validate_hook_spec(path: Path, spec: Any) -> None:
    if not isinstance(spec, dict):
        raise OperationError(f"{path}: hook must be a mapping")
    required = ("symbol", "signature", "returns", "output_element", "element_types",
                "template", "staged_header", "input_description", "output_description",
                "examples")
    for field in required:
        if not spec.get(field):
            raise OperationError(f"{path}: hook.{field} is required")
    if spec["returns"] not in ("bool", "element"):
        raise OperationError(f"{path}: hook.returns must be 'bool' or 'element'")
    if spec["output_element"] not in ("input", "declared", "none"):
        raise OperationError(f"{path}: hook.output_element must be input, declared or none")
    if (spec["returns"] == "element") != (spec["output_element"] == "declared"):
        raise OperationError(
            f"{path}: hook.returns 'element' goes with output_element 'declared', and only that"
        )
    unknown = [name for name in spec["element_types"] if name not in ELEMENTS]
    if unknown:
        raise OperationError(
            f"{path}: hook.element_types has unknown element(s): {', '.join(unknown)}"
        )
    wanted = {"int"} | ({"float"} if "f32" in spec["element_types"] else set())
    examples = spec["examples"]
    if not isinstance(examples, dict) or any(not examples.get(kind) for kind in wanted):
        raise OperationError(
            f"{path}: hook.examples needs a list for each of: {', '.join(sorted(wanted))}"
        )
    template = path.parent / str(spec["template"])
    if not template.is_file():
        raise OperationError(f"{path}: hook template not found: {template}")


def _validate_parameter_specs(path: Path, specs: Any) -> None:
    """Reject a malformed descriptor at load time, not at fingerprint time."""
    if not isinstance(specs, dict):
        raise OperationError(f"{path}: parameters must be a mapping")
    for name, spec in specs.items():
        if not isinstance(spec, dict):
            raise OperationError(f"{path}: parameter {name!r} must be a mapping")
        scope = spec.get("scope", DEFAULT_PARAMETER_SCOPE)
        if scope not in PARAMETER_SCOPES:
            raise OperationError(
                f"{path}: parameter {name!r} has scope {scope!r}; "
                f"must be one of {list(PARAMETER_SCOPES)}"
            )


def _validate_baseline_spec(path: Path, spec: Any) -> None:
    if not isinstance(spec, dict):
        raise OperationError(f"{path}: baseline must be a mapping")
    equivalence = spec.get("equivalence", "none")
    if equivalence not in BASELINE_EQUIVALENCE:
        raise OperationError(
            f"{path}: baseline.equivalence is {equivalence!r}; "
            f"must be one of {list(BASELINE_EQUIVALENCE)}"
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
