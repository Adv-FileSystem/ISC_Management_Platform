"""Backend registry. One entry per vendor."""
from __future__ import annotations

from isc.backends.base import (
    Backend, BackendError, BuildOutput, BuildProgress, BuildRequest, Buffer, DeviceInfo, Session,
    Timings,
)
from isc.backends.xrt import XrtBackend

_REGISTRY: dict[str, type[Backend]] = {
    XrtBackend.name: XrtBackend,
}


def get_backend(name: str) -> Backend:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise LookupError(
            f"unknown backend {name!r}; available: {sorted(_REGISTRY)}"
        ) from None


def backend_names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "Backend", "BackendError", "BuildOutput", "BuildProgress", "BuildRequest", "Buffer",
    "DeviceInfo", "Session", "Timings", "get_backend", "backend_names",
]
