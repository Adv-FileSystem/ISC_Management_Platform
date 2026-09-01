"""Build orchestration and the reuse cache.

Core owns *when* to build and how to key the cache; the backend owns *how* to
build. A rebuild is skipped whenever the fingerprint of everything that can
change the output is unchanged -- that is what makes "local reuse" real for a
kernel build that costs hours.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from isc.backends import Backend, BuildRequest, get_backend
from isc.manifest import Manifest
from isc.workspace import Workspace

CACHE_SCHEMA = 2


@dataclass
class BuildResult:
    module_id: str
    target: str
    backend: str
    fingerprint: str
    binary: Path
    cached: bool
    log_path: Path | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module_id, "target": self.target, "backend": self.backend,
            "fingerprint": self.fingerprint, "binary": str(self.binary),
            "cached": self.cached,
            "log": str(self.log_path) if self.log_path else None,
            "extra": self.extra,
        }


def fingerprint(manifest: Manifest, backend: Backend, target: str) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "backend": backend.name,
        "toolchain": backend.toolchain_identity(),
        "target": target,
        "operation": manifest.operation_id,
        "operation_version": manifest.operation_version,
        "parameters": manifest.parameters,
        "platform": manifest.build.platform,
        "memory_type": manifest.build.memory_type,
        "options": list(manifest.build.options),
        "kernel": manifest.kernel.name,
        "memory": [vars(port) for port in manifest.kernel.memory],
        "sources": _digest([manifest.kernel_source, *manifest.resolved_include_dirs()]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:32]


def build(workspace: Workspace, manifest: Manifest, target: str, *,
          force: bool = False, timeout_seconds: float | None = None) -> BuildResult:
    if target not in manifest.build.targets:
        raise ValueError(
            f"target {target!r} is not declared in module.yaml "
            f"(build.targets: {list(manifest.build.targets)})"
        )
    backend = get_backend(manifest.backend)
    key = fingerprint(manifest, backend, target)
    directory = workspace.cache_dir / key
    meta_path = directory / "meta.json"

    if not force and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        binary = Path(meta["binary"])
        if binary.exists():
            return BuildResult(
                module_id=manifest.id, target=target, backend=backend.name,
                fingerprint=key, binary=binary, cached=True,
                log_path=directory / "build.log" if (directory / "build.log").exists() else None,
                extra=meta.get("extra", {}),
            )

    directory.mkdir(parents=True, exist_ok=True)
    output = backend.build(BuildRequest(
        manifest=manifest, target=target, output_dir=directory,
        timeout_seconds=timeout_seconds,
    ))
    log_path = directory / "build.log"
    log_path.write_text(output.log)
    meta_path.write_text(json.dumps({
        "schema": CACHE_SCHEMA, "module": manifest.id, "version": manifest.version,
        "target": target, "backend": backend.name, "fingerprint": key,
        "binary": str(output.binary), "extra": output.extra,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=2))
    return BuildResult(
        module_id=manifest.id, target=target, backend=backend.name, fingerprint=key,
        binary=output.binary, cached=False, log_path=log_path, extra=output.extra,
    )


def _digest(paths: list[Path]) -> str:
    """Content digest of every file that can change the build output."""
    digest = hashlib.sha256()
    for order, path in enumerate(sorted(paths, key=str)):
        if not path.exists():
            digest.update(f"{order}:missing:{path.name}\n".encode())
            continue
        files = [path] if path.is_file() else sorted(
            (item for item in path.rglob("*") if item.is_file()), key=str
        )
        for item in files:
            label = item.name if path.is_file() else str(item.relative_to(path))
            digest.update(f"{order}:{label}\0".encode())
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()
