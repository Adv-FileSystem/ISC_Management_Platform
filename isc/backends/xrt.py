"""AMD/Xilinx backend: v++ for building, pyxrt for running.

This is the only file in the project that knows what an .xclbin is, what
`HBM[0]` means, or that a tool called v++ exists.

Scope note: one compute unit per kernel. Multi-CU needs --connectivity.nk and
per-CU connectivity lines; that change is local to this file.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from isc.backends.base import (
    Backend, BackendError, BuildOutput, BuildRequest, Buffer, DeviceInfo, Session,
)
from isc.manifest import Manifest

# `v++ --version` greets every invocation with a wall-clock line; it must not
# reach the build cache key or no build would ever be reused.
_VOLATILE = re.compile(r"Start of session at:")

XRT_PYTHON_DIR = "/opt/xilinx/xrt/python"


def _import_pyxrt():
    try:
        import pyxrt  # noqa: F401
    except ImportError:
        if XRT_PYTHON_DIR not in os.sys.path and Path(XRT_PYTHON_DIR).is_dir():
            os.sys.path.append(XRT_PYTHON_DIR)
        try:
            import pyxrt  # noqa: F811
        except ImportError as exc:
            raise BackendError(
                "pyxrt is not importable. Source the XRT setup script first:\n"
                "  source /opt/xilinx/xrt/setup.sh"
            ) from exc
    return pyxrt


def _run(command: list[str], timeout: float | None, cwd: Path | None = None) -> tuple[int, str]:
    try:
        finished = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise BackendError(f"{command[0]} not found on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"{command[0]} timed out after {timeout}s") from exc
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


class XrtBuffer(Buffer):
    def __init__(self, session: "XrtSession", bo, size: int) -> None:
        self._session = session
        self._bo = bo
        self.size = size

    def write(self, data: bytes, offset: int = 0) -> None:
        pyxrt = self._session.pyxrt
        with self._session.timings.measure("host_to_device_ms"):
            self._bo.write(data, offset)
            self._bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_TO_DEVICE, len(data), offset)

    def read(self, size: int | None = None, offset: int = 0) -> bytes:
        pyxrt = self._session.pyxrt
        length = self.size if size is None else size
        if length <= 0:
            return b""
        with self._session.timings.measure("device_to_host_ms"):
            self._bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_FROM_DEVICE, length, offset)
            return bytes(self._bo.read(length, offset))


class XrtSession(Session):
    def __init__(self, pyxrt_module, device, kernel, info: DeviceInfo,
                 arguments: tuple = ()) -> None:
        super().__init__(info, arguments)
        self.pyxrt = pyxrt_module
        self._device = device
        self._kernel = kernel

    def buffer(self, size: int, arg: int) -> Buffer:
        self.check_buffer_argument(arg)
        pyxrt = self.pyxrt
        try:
            bo = pyxrt.bo(self._device, size, pyxrt.bo.normal, self._kernel.group_id(arg))
        except Exception as exc:
            raise BackendError(f"failed to allocate {size} bytes for kernel arg {arg}: {exc}") from exc
        return XrtBuffer(self, bo, size)

    def run(self, *args: object) -> None:
        self.check_run_arity(len(args))
        unwrapped = [a._bo if isinstance(a, XrtBuffer) else a for a in args]
        with self.timings.measure("kernel_ms"):
            try:
                self._kernel(*unwrapped).wait()
            except Exception as exc:
                raise BackendError(f"kernel execution failed: {exc}") from exc

    def close(self) -> None:
        self._kernel = None
        self._device = None


class XrtBackend(Backend):
    name = "xrt"

    def available(self) -> tuple[bool, str]:
        try:
            _import_pyxrt()
        except BackendError as exc:
            return False, str(exc)

        missing_tools = [tool for tool in ("v++", "emconfigutil") if shutil.which(tool) is None]
        # v++ refuses to start without these even when it is on PATH, and the
        # error it prints then ("Invalid Environment was detected") only shows
        # up minutes into a build. Catch it here instead.
        missing_env = [name for name in ("XILINX_VITIS", "XILINX_VIVADO") if not os.environ.get(name)]
        if missing_tools or missing_env:
            problems = []
            if missing_tools:
                problems.append(f"tools not on PATH: {', '.join(missing_tools)}")
            if missing_env:
                problems.append(f"environment not set: {', '.join(missing_env)}")
            return False, (
                f"cannot build ({'; '.join(problems)}). Source the Vitis setup script:\n"
                "  source /path/to/Vitis/settings64.sh\n"
                "Running an already-built image still works."
            )
        return True, "v++, emconfigutil and pyxrt available"

    def devices(self) -> list[DeviceInfo]:
        pyxrt = _import_pyxrt()
        found: list[DeviceInfo] = []
        for index in range(pyxrt.enumerate_devices()):
            try:
                device = pyxrt.device(index)
                found.append(DeviceInfo(
                    backend=self.name, index=index,
                    name=device.get_info(pyxrt.xrt_info_device.name),
                    address=device.get_info(pyxrt.xrt_info_device.bdf),
                ))
            except Exception:  # a device XRT lists but cannot open is not usable
                continue
        return found

    def toolchain_identity(self) -> str:
        if shutil.which("v++") is None:
            return "v++:unavailable"
        _, output = _run(["v++", "--version"], timeout=120)
        return "\n".join(
            line.rstrip() for line in output.splitlines() if not _VOLATILE.search(line)
        ).strip()

    def build(self, request: BuildRequest) -> BuildOutput:
        manifest, target = request.manifest, request.target
        out = request.output_dir
        out.mkdir(parents=True, exist_ok=True)
        xo = out / f"{manifest.kernel.name}.{target}.xo"
        xclbin = out / f"{manifest.kernel.name}.{target}.xclbin"

        compile_command = [
            "v++", "-c", "-k", manifest.kernel.name, "-t", target,
            "--platform", manifest.build.platform, "-o", str(xo),
        ]
        for include_dir in manifest.resolved_include_dirs():
            compile_command += ["-I", str(include_dir)]
        compile_command += list(manifest.build.options)
        compile_command.append(str(manifest.kernel_source))

        log = f"$ {' '.join(compile_command)}\n"
        code, output = _run(compile_command, request.timeout_seconds, cwd=out)
        log += output
        if code != 0 or not xo.exists():
            raise BackendError(f"v++ compile failed (exit {code}). Log:\n{log[-4000:]}")

        link_command = [
            "v++", "-l", "-t", target, "--platform", manifest.build.platform,
            "-o", str(xclbin),
        ]
        for entry in self._connectivity(manifest):
            link_command += ["--connectivity.sp", entry]
        link_command.append(str(xo))

        log += f"\n$ {' '.join(link_command)}\n"
        code, output = _run(link_command, request.timeout_seconds, cwd=out)
        log += output
        if code != 0 or not xclbin.exists():
            raise BackendError(f"v++ link failed (exit {code}). Log:\n{log[-4000:]}")

        extra = {"xo": str(xo)}
        if self.is_emulation(target):
            emconfig = out / "emconfig.json"
            if not emconfig.exists():
                code, output = _run(
                    ["emconfigutil", "--platform", manifest.build.platform, "--od", str(out)],
                    request.timeout_seconds,
                )
                log += output
                if code != 0 or not emconfig.exists():
                    raise BackendError(f"emconfigutil failed (exit {code}). Log:\n{log[-2000:]}")
            extra["emconfig"] = str(emconfig)

        return BuildOutput(binary=xclbin, log=log, extra=extra)

    def open(self, manifest: Manifest, binary: Path, target: str,
             device_index: int | None = None) -> Session:
        # XRT decides between a real card and the emulator from the environment,
        # and it reads that at device-open time -- so it must be set here, before
        # pyxrt.device() is called.
        if self.is_emulation(target):
            os.environ["XCL_EMULATION_MODE"] = target
            os.environ["EMCONFIG_PATH"] = str(Path(binary).parent)
        else:
            os.environ.pop("XCL_EMULATION_MODE", None)

        pyxrt = _import_pyxrt()
        index = 0 if device_index is None else device_index
        try:
            device = pyxrt.device(index)
            uuid = device.load_xclbin(str(binary))
            kernel = pyxrt.kernel(device, uuid, manifest.kernel.name)
        except Exception as exc:
            raise BackendError(
                f"could not open device {index} / load {binary.name} / "
                f"find kernel '{manifest.kernel.name}': {exc}"
            ) from exc

        info = DeviceInfo(
            backend=self.name, index=index,
            name=device.get_info(pyxrt.xrt_info_device.name),
            address=device.get_info(pyxrt.xrt_info_device.bdf),
            emulated=self.is_emulation(target),
        )
        return XrtSession(pyxrt, device, kernel, info, manifest.kernel.arguments)

    def _connectivity(self, manifest: Manifest) -> list[str]:
        memory_type = manifest.build.memory_type
        return [
            f"{manifest.kernel.name}_1.{port.port}:{memory_type}[{port.bank}]"
            for port in manifest.kernel.memory
        ]
