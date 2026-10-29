"""Workspace layout and module discovery (device reservation lives in isc/devices.py).

    <root>/modules/<id>/module.yaml     modules the user writes
    <root>/artifacts/cache/<fp>/        build outputs, reused across runs
    <root>/artifacts/outputs/           execution outputs
    <root>/artifacts/records.jsonl      one line per run
    <root>/artifacts/locks/             device reservations
    <root>/artifacts/events/            run events, one JSONL file per run
    <root>/experiments/<id>/experiment.yaml   benchmark matrices the user writes
    <root>/artifacts/benchmarks/        raw benchmark samples, one file per run
    <root>/artifacts/benchdata/         generated benchmark inputs, cached by size
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from isc.manifest import MANIFEST_NAME, Manifest


@contextmanager
def working_directory(directory: Path) -> Iterator[None]:
    """Run inside `directory`, then go back.

    Emulation needs emconfig.json in the working directory, and the simulator
    scatters scratch files next to it -- both belong in the build directory,
    not wherever the user happened to invoke the CLI from.
    """
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


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

    @property
    def events_dir(self) -> Path:
        return self.artifacts_dir / "events"

    @property
    def experiments_dir(self) -> Path:
        return self.root / "experiments"

    @property
    def benchmarks_dir(self) -> Path:
        return self.artifacts_dir / "benchmarks"

    @property
    def benchdata_dir(self) -> Path:
        return self.artifacts_dir / "benchdata"

    def experiment_ids(self) -> list[str]:
        if not self.experiments_dir.is_dir():
            return []
        return sorted(
            entry.name for entry in self.experiments_dir.iterdir()
            if (entry / "experiment.yaml").exists()
        )

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
