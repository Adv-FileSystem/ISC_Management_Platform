"""Load a platform-managed operation adapter and any sibling Python files.

An adapter may import sibling files by bare name (`from helpers import decode`).
Python caches those imports globally, so each load runs with the operation
directory isolated and restores the previous process state afterward.
"""
from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator


class ModuleCodeError(RuntimeError):
    pass


@contextmanager
def _isolated_imports(directory: Path) -> Iterator[None]:
    siblings = {path.stem for path in directory.glob("*.py")}
    displaced = {name: sys.modules.pop(name) for name in siblings if name in sys.modules}
    sys.path.insert(0, str(directory))
    # Don't leave __pycache__ behind in the installed operation package.
    write_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        yield
    finally:
        sys.dont_write_bytecode = write_bytecode
        sys.path.remove(str(directory))
        for name in siblings:
            loaded = sys.modules.get(name)
            origin = getattr(loaded, "__file__", None)
            if origin and Path(origin).parent == directory:
                del sys.modules[name]
        sys.modules.update(displaced)


def load_file(directory: Path, filename: str, namespace: str) -> ModuleType:
    path = directory / filename
    if not path.exists():
        raise ModuleCodeError(f"{path} not found")
    spec = importlib.util.spec_from_file_location(f"isc_module_{namespace}", path)
    if spec is None or spec.loader is None:
        raise ModuleCodeError(f"cannot import {path}")
    loaded = importlib.util.module_from_spec(spec)
    with _isolated_imports(directory):
        try:
            spec.loader.exec_module(loaded)
        except Exception as exc:
            raise ModuleCodeError(f"{path} raised on import: {exc}") from exc
    return loaded


def load_callable(directory: Path, filename: str, attribute: str, namespace: str):
    loaded = load_file(directory, filename, f"{namespace}_{attribute}")
    function = getattr(loaded, attribute, None)
    if not callable(function):
        raise ModuleCodeError(f"{directory / filename} must define a callable `{attribute}`")
    return function
