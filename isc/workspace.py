"""Workspace layout, module discovery, and device reservation.

    <root>/modules/<id>/module.yaml     modules the user writes
    <root>/artifacts/cache/<fp>/        build outputs, reused across runs
    <root>/artifacts/outputs/           execution outputs
    <root>/artifacts/records.jsonl      one line per run
    <root>/artifacts/locks/             device reservations
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from isc.manifest import MANIFEST_NAME, Manifest


class Workspace:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    # -- layout ---------------------------------------------------------
    @property
    def modules_dir(self) -> Path:
        return self.root / "modules"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def cache_dir(self) -> Path:
        return self.artifacts_dir / "cache"

    @property
    def outputs_dir(self) -> Path:
        return self.artifacts_dir / "outputs"

    @property
    def records_path(self) -> Path:
        return self.artifacts_dir / "records.jsonl"

    @property
    def locks_dir(self) -> Path:
        return self.artifacts_dir / "locks"

    # -- module discovery -----------------------------------------------
    def module_ids(self) -> list[str]:
        if not self.modules_dir.is_dir():
            return []
        return sorted(
            entry.name for entry in self.modules_dir.iterdir()
            if (entry / MANIFEST_NAME).exists()
        )

    def load(self, module_id: str) -> Manifest:
        directory = self.modules_dir / module_id
        if not (directory / MANIFEST_NAME).exists():
            known = ", ".join(self.module_ids()) or "none"
            raise LookupError(f"unknown module {module_id!r} in {self.modules_dir} (known: {known})")
        return Manifest.load(directory)

    # -- device reservation ----------------------------------------------
    @contextmanager
    def device_lock(self, key: str) -> Iterator[None]:
        """Exclusive, cross-process reservation so two runs cannot share a card."""
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        path = self.locks_dir / f"{key}.lock"
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    # -- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, explicit: Path | str | None = None) -> "Workspace":
        if explicit:
            return cls(explicit)
        from_env = os.environ.get("ISC_WORKSPACE")
        if from_env:
            return cls(from_env)
        current = Path.cwd().resolve()
        for candidate in (current, *current.parents):
            if (candidate / "modules").is_dir():
                return cls(candidate)
        return cls(current)
