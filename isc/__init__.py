"""ISC module lifecycle platform.

Python usage mirrors the CLI:

    import isc
    workspace = isc.Workspace.discover()
    result = isc.run(workspace, "lz4", "corpus.bin", "hw_emu")
    print(result.ok, result.metrics)
"""
from isc.build import build
from isc.check import check_module
from isc.lifecycle import RunResult, revalidate, run
from isc.manifest import Manifest, ManifestError
from isc.scaffold import create_module
from isc.workspace import Workspace

__version__ = "0.1.0"
__all__ = [
    "Workspace", "Manifest", "ManifestError", "RunResult",
    "build", "run", "revalidate", "check_module", "create_module", "__version__",
]
