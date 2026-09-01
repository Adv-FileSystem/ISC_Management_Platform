"""Parse the small user module manifest and resolve its built-in operation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from isc.operations import OperationError, OperationSpec, get_operation

MANIFEST_NAME = "module.yaml"
ARG_KINDS = ("buffer", "scalar")
ARG_DIRECTIONS = ("in", "out", "inout")
VALIDATION_MODES = ("cross_target", "none")
DEFAULT_REFERENCE_TARGET = "hw_emu"


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class KernelArgument:
    name: str
    kind: str = "buffer"
    direction: str = "in"


@dataclass(frozen=True)
class MemoryPort:
    port: str
    bank: int


@dataclass(frozen=True)
class KernelSpec:
    name: str
    source: str
    include_dirs: tuple[str, ...] = ()
    arguments: tuple[KernelArgument, ...] = ()
    memory: tuple[MemoryPort, ...] = ()


@dataclass(frozen=True)
class BuildSpec:
    platform: str
    targets: tuple[str, ...]
    memory_type: str = "HBM"
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    operation: OperationSpec
    parameters: dict[str, Any]
    backend: str
    kernel: KernelSpec
    build: BuildSpec
    validation_mode: str
    validation_reference_target: str
    directory: Path

    @property
    def operation_id(self) -> str:
        return self.operation.id

    @property
    def operation_version(self) -> str:
        return self.operation.version

    @property
    def kernel_source(self) -> Path:
        return self.operation.directory / self.kernel.source

    @property
    def adapter_directory(self) -> Path:
        return self.operation.directory

    def validate_input(self, data: bytes) -> None:
        self.operation.validate_input(data)

    def resolved_include_dirs(self) -> list[Path]:
        resolved = []
        for item in self.kernel.include_dirs:
            expanded = os.path.expanduser(os.path.expandvars(item))
            if "$" in expanded:
                raise ManifestError(
                    f"operation {self.operation_id!r} requires an environment variable "
                    f"for include path {item!r} (expanded to {expanded!r})"
                )
            path = Path(expanded)
            resolved.append(path if path.is_absolute() else self.operation.directory / path)
        return resolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "operation": {
                "id": self.operation_id,
                "version": self.operation_version,
                "parameters": self.parameters,
            },
            "input": self.operation.input_contract,
            "output": self.operation.output_contract,
            "backend": self.backend,
            "build": {
                "targets": list(self.build.targets),
                "platform": self.build.platform,
            },
            "directory": str(self.directory),
        }

    @classmethod
    def load(cls, directory: Path) -> "Manifest":
        directory = Path(directory).resolve()
        path = directory / MANIFEST_NAME
        if not path.exists():
            raise ManifestError(f"{path} not found")
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ManifestError(f"{path}: top level must be a mapping")
        return cls._parse(raw, directory)

    @classmethod
    def _parse(cls, raw: dict[str, Any], directory: Path) -> "Manifest":
        unknown_sections = sorted(set(raw) - {"module", "operation", "parameters"})
        if unknown_sections:
            raise ManifestError(
                "module.yaml contains unsupported section(s): "
                + ", ".join(unknown_sections)
            )
        module = _section(raw, "module")
        operation_selection = _section(raw, "operation")
        operation_id = _require(operation_selection, "id", "operation")
        try:
            operation = get_operation(operation_id)
        except OperationError as exc:
            raise ManifestError(str(exc)) from exc

        requested_version = str(operation_selection.get("version") or operation.version)
        if requested_version != operation.version:
            raise ManifestError(
                f"operation {operation_id!r} version {requested_version!r} is not installed "
                f"(available: {operation.version})"
            )
        supplied_parameters = raw.get("parameters") or {}
        if not isinstance(supplied_parameters, dict):
            raise ManifestError("parameters must be a mapping")
        try:
            parameters = operation.resolve_parameters(supplied_parameters)
        except OperationError as exc:
            raise ManifestError(str(exc)) from exc

        kernel_raw = _section(operation.raw, "kernel", f"operation {operation_id}")
        build_raw = _section(operation.raw, "build", f"operation {operation_id}")
        runtime_raw = operation.raw.get("runtime") or {}
        validation_raw = operation.raw.get("validation") or {}

        arguments = tuple(
            KernelArgument(
                name=_require(item, "name", "kernel.arguments[]"),
                kind=_choice(item.get("kind", "buffer"), ARG_KINDS,
                             "kernel.arguments[].kind"),
                direction=_choice(item.get("direction", "in"), ARG_DIRECTIONS,
                                  "kernel.arguments[].direction"),
            )
            for item in kernel_raw.get("arguments") or []
        )
        memory = tuple(
            MemoryPort(
                port=_require(item, "port", "kernel.memory[]"),
                bank=int(item.get("bank", 0)),
            )
            for item in kernel_raw.get("memory") or []
        )
        targets = tuple(build_raw.get("targets") or ())
        if not targets:
            raise ManifestError(f"operation {operation_id!r} has no build targets")

        manifest = cls(
            id=_require(module, "id", "module"),
            name=str(module.get("name") or _require(module, "id", "module")),
            version=str(module.get("version", "0.1.0")),
            operation=operation,
            parameters=parameters,
            backend=str(runtime_raw.get("backend") or "xrt"),
            kernel=KernelSpec(
                name=_require(kernel_raw, "name", "kernel"),
                source=_require(kernel_raw, "source", "kernel"),
                include_dirs=tuple(kernel_raw.get("include_dirs") or ()),
                arguments=arguments,
                memory=memory,
            ),
            build=BuildSpec(
                platform=_require(build_raw, "platform", "build"),
                targets=targets,
                memory_type=str(build_raw.get("memory_type", "HBM")),
                options=tuple(build_raw.get("options") or ()),
            ),
            validation_mode=_choice(
                validation_raw.get("mode", "none"), VALIDATION_MODES, "validation.mode"
            ),
            validation_reference_target=str(
                validation_raw.get("reference_target", DEFAULT_REFERENCE_TARGET)
            ),
            directory=directory,
        )
        if not manifest.kernel_source.is_file():
            raise ManifestError(
                f"operation {operation_id!r} kernel source not found: {manifest.kernel_source}"
            )
        if not (manifest.adapter_directory / "adapter.py").is_file():
            raise ManifestError(
                f"operation {operation_id!r} adapter not found: "
                f"{manifest.adapter_directory / 'adapter.py'}"
            )
        return manifest


def _section(raw: dict[str, Any], name: str, where: str = "module.yaml") -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ManifestError(f"{where}: missing required section: {name}")
    return value


def _require(section: dict[str, Any], key: str, where: str) -> str:
    value = section.get(key)
    if not value:
        raise ManifestError(f"missing required field: {where}.{key}")
    return str(value)


def _choice(value: Any, allowed: tuple[str, ...], where: str) -> str:
    if value not in allowed:
        raise ManifestError(f"{where} must be one of {list(allowed)}, got {value!r}")
    return str(value)
