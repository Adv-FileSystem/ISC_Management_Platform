"""플랫폼 코어 테스트 -- FPGA 도, Vitis 도 없이 전 주기를 검증한다.

가짜 백엔드를 하나 끼워 넣어 생성->빌드->실행->검증->기록->재사용이
실제로 도는지 확인한다. 이 테스트가 도는 것 자체가 벤더 이음새가
제대로 분리되어 있다는 증거다.
"""
from __future__ import annotations

import hashlib
import json
import io
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from isc import backends, record
from isc.backends.base import Backend, Buffer, BuildOutput, BuildRequest, DeviceInfo, Session
from isc.build import build
from isc.check import check_module
from isc.cli import main as cli_main
from isc.lifecycle import run
from isc.loader import load_callable
from isc.manifest import Manifest, ManifestError
from isc.operations import OperationError, get_operation, operation_ids
from isc.scaffold import create_module
from isc.workspace import Workspace


class FakeBuffer(Buffer):
    def __init__(self, size: int) -> None:
        self.size = size
        self.data = bytearray(size)

    def write(self, data: bytes, offset: int = 0) -> None:
        self.data[offset:offset + len(data)] = data

    def read(self, size: int | None = None, offset: int = 0) -> bytes:
        length = self.size if size is None else size
        return bytes(self.data[offset:offset + length])


class FakeSession(Session):
    def buffer(self, size: int, arg: int) -> Buffer:
        self.check_buffer_argument(arg)
        return FakeBuffer(size)

    def run(self, *args) -> None:
        """스켈레톤 커널(항등 변환)과 같은 일을 CPU 에서 한다."""
        self.check_run_arity(len(args))
        bo_in, bo_out, size, bo_size = args
        bo_out.write(bo_in.read(size))
        bo_size.write(int(size).to_bytes(4, "little"))


class DigestSession(Session):
    """CPU stand-in for fixed-size checksum and digest operation kernels."""

    def __init__(self, arguments, transform, *, initial: int | None = None) -> None:
        super().__init__(DeviceInfo(backend="fake", index=0, name="fake-device"), arguments)
        self.transform = transform
        self.initial = initial

    def buffer(self, size: int, arg: int) -> Buffer:
        self.check_buffer_argument(arg)
        return FakeBuffer(size)

    def run(self, *args) -> None:
        self.check_run_arity(len(args))
        bo_in, bo_out, size = args
        if self.initial is not None:
            actual = int.from_bytes(bo_out.read(4), "little")
            if actual != self.initial:
                raise AssertionError(f"initial state is {actual:#x}, expected {self.initial:#x}")
        bo_out.write(self.transform(bo_in.read(size)))


class FakeBackend(Backend):
    name = "fake"
    build_calls = 0

    def available(self):
        return True, "fake backend"

    def devices(self):
        return [DeviceInfo(backend=self.name, index=0, name="fake-device")]

    def toolchain_identity(self) -> str:
        return "fake-toolchain-1.0"

    def build(self, request: BuildRequest) -> BuildOutput:
        FakeBackend.build_calls += 1
        binary = request.output_dir / "module.fake"
        binary.write_text("fake device image")
        return BuildOutput(binary=binary, log="fake build")

    def open(self, manifest, binary, target, device_index=None) -> Session:
        return FakeSession(
            DeviceInfo(backend=self.name, index=device_index or 0,
                       name="fake-device", emulated=target.endswith("_emu")),
            manifest.kernel.arguments,
        )


class PlatformTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._xrt_backend = backends._REGISTRY["xrt"]
        backends._REGISTRY["xrt"] = FakeBackend
        FakeBackend.build_calls = 0
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        backends._REGISTRY["xrt"] = self._xrt_backend
        self._temp.cleanup()

    def make_module(self, module_id: str = "demo") -> Path:
        return create_module(self.workspace, module_id, operation_id="identity")

    def _mock_reference_run(self, transform):
        """Fake the child process _reference_output() spawns for the
        reference target -- a real subprocess would not have the "fake"
        backend registered. `transform(data) -> bytes` decides what that
        process "produces"; it is written where --output points.
        """
        def fake_run(command, **kwargs):
            input_path = Path(command[command.index("--input") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(transform(input_path.read_bytes()))
            payload = {"status": "succeeded", "output_path": str(output_path)}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return mock.patch("isc.lifecycle.subprocess.run", side_effect=fake_run)


class Scaffold(PlatformTestCase):
    def test_new_module_passes_static_check(self):
        path = self.make_module()
        report = check_module(path)
        self.assertTrue(report.passed, report.problems)

    def test_expected_files_are_generated(self):
        path = self.make_module("mymod")
        for name in ("module.yaml", "README.md"):
            self.assertTrue((path / name).exists(), f"missing {name}")
        self.assertFalse((path / "adapter.py").exists())
        self.assertFalse((path / "kernel").exists())

    def test_rejects_invalid_id(self):
        with self.assertRaises(ValueError):
            create_module(self.workspace, "Bad-Id", operation_id="identity")

    def test_rejects_duplicate(self):
        self.make_module()
        with self.assertRaises(FileExistsError):
            self.make_module()


class ManifestParsing(PlatformTestCase):
    def test_round_trip(self):
        self.make_module("demo")
        manifest = self.workspace.load("demo")
        self.assertEqual(manifest.id, "demo")
        self.assertEqual(manifest.backend, "xrt")
        self.assertEqual(manifest.operation_id, "identity")
        self.assertEqual(manifest.build.targets, ("hw_emu", "hw"))
        self.assertEqual(len(manifest.kernel.arguments), 4)
        self.assertEqual(manifest.validation_mode, "cross_target")
        self.assertEqual(manifest.validation_reference_target, "hw_emu")

    def test_missing_section_is_reported(self):
        directory = self.workspace.modules_dir / "broken"
        directory.mkdir()
        (directory / "module.yaml").write_text("module: {id: broken}\n")
        with self.assertRaises(ManifestError) as caught:
            Manifest.load(directory)
        self.assertIn("operation", str(caught.exception))

    def test_unset_environment_variable_in_include_dir_is_reported(self):
        path = create_module(
            self.workspace, "envmod", operation_id="lz4"
        )
        manifest = self.workspace.load("envmod")
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ManifestError):
                manifest.resolved_include_dirs()

    def test_unknown_module_lists_known_ones(self):
        self.make_module("demo")
        with self.assertRaises(LookupError) as caught:
            self.workspace.load("nope")
        self.assertIn("demo", str(caught.exception))

    def test_user_cannot_override_platform_managed_sections(self):
        path = self.make_module("locked")
        (path / "module.yaml").write_text(
            (path / "module.yaml").read_text() + "runtime: {backend: custom}\n"
        )
        with self.assertRaises(ManifestError) as caught:
            self.workspace.load("locked")
        self.assertIn("unsupported section", str(caught.exception))


class OperationCatalog(PlatformTestCase):
    def test_expected_operations_are_available(self):
        self.assertEqual(
            operation_ids(),
            ["adler32", "crc32", "identity", "lz4", "sha256"],
        )

    def test_operation_description_exposes_contract(self):
        operation = get_operation("lz4")
        self.assertEqual(operation.category, "compression")
        self.assertEqual(operation.input_contract["formats"], ["*"])
        self.assertIn("block_size", operation.parameter_specs)

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(OperationError):
            create_module(self.workspace, "demo", operation_id="unknown")

    def test_operation_parameters_are_validated(self):
        with self.assertRaises(OperationError):
            create_module(
                self.workspace, "demo", operation_id="lz4",
                parameters={"block_size": 1000000},
            )

    def test_default_parameter_is_written_to_module(self):
        path = create_module(self.workspace, "compressor", operation_id="lz4")
        self.assertIn("block_size: 65536", (path / "module.yaml").read_text())

    def test_fixed_digest_contracts(self):
        self.assertEqual(get_operation("crc32").output_contract["fixed_bytes"], 4)
        self.assertEqual(get_operation("adler32").output_contract["fixed_bytes"], 4)
        self.assertEqual(get_operation("sha256").output_contract["fixed_bytes"], 32)

    def test_memory_mapped_checksums_reject_empty_input(self):
        for operation_id in ("crc32", "adler32"):
            with self.subTest(operation=operation_id):
                with self.assertRaises(OperationError):
                    get_operation(operation_id).validate_input(b"")

    def _run_digest_adapter(self, operation_id, payload, transform, *, initial=None):
        path = create_module(self.workspace, f"test_{operation_id}", operation_id=operation_id)
        manifest = Manifest.load(path)
        execute = load_callable(
            manifest.adapter_directory, "adapter.py", "execute", operation_id
        )
        session = DigestSession(manifest.kernel.arguments, transform, initial=initial)
        return execute(session, payload, manifest.parameters)

    def test_crc32_adapter_returns_canonical_bytes(self):
        payload = b"123456789"
        expected = zlib.crc32(payload).to_bytes(4, "big")
        result = self._run_digest_adapter(
            "crc32", payload,
            lambda data: zlib.crc32(data).to_bytes(4, "little"),
            initial=0xFFFFFFFF,
        )
        self.assertEqual(result, expected)
        self.assertEqual(result.hex(), "cbf43926")

    def test_adler32_adapter_returns_canonical_bytes(self):
        payload = b"Wikipedia"
        expected = zlib.adler32(payload).to_bytes(4, "big")
        result = self._run_digest_adapter(
            "adler32", payload,
            lambda data: zlib.adler32(data).to_bytes(4, "little"),
            initial=1,
        )
        self.assertEqual(result, expected)
        self.assertEqual(result.hex(), "11e60398")

    def test_sha256_adapter_supports_empty_input(self):
        result = self._run_digest_adapter(
            "sha256", b"", lambda data: hashlib.sha256(data).digest()
        )
        self.assertEqual(result, hashlib.sha256(b"").digest())


class CommandLine(PlatformTestCase):
    def test_operations_and_describe_commands(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli_main(["operations"]), 0)
            self.assertIn("identity", output.getvalue())
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli_main(["describe", "lz4"]), 0)
            self.assertIn("block_size", output.getvalue())

    def test_new_requires_an_operation(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as error:
            result = cli_main(["--workspace", str(self.workspace.root), "new", "demo"])
        self.assertEqual(result, 2)
        self.assertIn("operation is required", error.getvalue())

    def test_new_only_generates_user_configuration(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            result = cli_main([
                "--workspace", str(self.workspace.root), "new", "compressor",
                "--operation", "lz4", "--set", "block_size=32768",
            ])
        self.assertEqual(result, 0)
        directory = self.workspace.modules_dir / "compressor"
        self.assertEqual(
            sorted(path.name for path in directory.iterdir()),
            ["README.md", "module.yaml"],
        )


class Lifecycle(PlatformTestCase):
    def _input(self, payload: bytes = b"the quick brown fox" * 10) -> Path:
        path = Path(self._temp.name) / "input.bin"
        path.write_bytes(payload)
        return path

    def test_full_loop_succeeds_and_is_recorded(self):
        self.make_module()
        source = self._input()
        result = run(self.workspace, "demo", source, "hw_emu")

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output_bytes, source.stat().st_size)
        self.assertTrue(result.validation["passed"])
        self.assertEqual(Path(result.output_path).read_bytes(), source.read_bytes())

        records = record.read_all(self.workspace)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["run_id"], result.run_id)
        self.assertEqual(records[0]["status"], "succeeded")

    def test_metrics_are_collected(self):
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        for key in ("total_ms", "kernel_ms", "host_to_device_ms", "device_to_host_ms",
                    "throughput_mbps"):
            self.assertIn(key, result.metrics)

    def test_running_on_the_reference_target_skips_comparison(self):
        """module.yaml 의 reference_target 은 기본 hw_emu다. 그 타깃으로 돌리면
        비교할 대상이 없으므로 빌드가 한 번만 일어나고 그냥 통과해야 한다."""
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertTrue(result.validation["passed"])
        self.assertIn("nothing to compare", result.validation["detail"])
        self.assertEqual(FakeBackend.build_calls, 1)

    def test_cross_target_validation_passes_when_outputs_match(self):
        """reference_target 이 아닌 타깃(hw)으로 돌리면 hw_emu 를 별도 프로세스로
        한 번 더 실행해서 바이트 단위로 대조한다(이유는 아래 test 참고). 두 실행이
        같은 바이트를 내면 통과해야 한다."""
        self.make_module()
        with self._mock_reference_run(lambda data: data):
            result = run(self.workspace, "demo", self._input(), "hw")
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.validation["passed"])
        self.assertIn("matches reference target output", result.validation["detail"])

    def test_second_run_reuses_the_build(self):
        self.make_module()
        source = self._input()
        run(self.workspace, "demo", source, "hw_emu")
        second = run(self.workspace, "demo", source, "hw_emu")
        self.assertEqual(FakeBackend.build_calls, 1)
        self.assertTrue(second.build["cached"])

    def test_two_modules_reuse_the_same_managed_operation_build(self):
        self.make_module("first")
        self.make_module("second")
        source = self._input()
        first = run(self.workspace, "first", source, "hw_emu")
        second = run(self.workspace, "second", source, "hw_emu")
        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertEqual(FakeBackend.build_calls, 1)
        self.assertTrue(second.build["cached"])

    def test_validation_failure_marks_the_run_failed(self):
        """hw 에서 낸 출력이 reference_target(hw_emu) 출력과 다르면 실패해야 한다."""
        self.make_module()
        with self._mock_reference_run(lambda data: data + b"x"):
            result = run(self.workspace, "demo", self._input(), "hw")
        self.assertFalse(result.ok)
        self.assertFalse(result.validation["passed"])
        self.assertIn("size mismatch", result.validation["detail"])
        self.assertEqual(record.read_all(self.workspace)[0]["status"], "failed")

    def test_reference_run_failure_is_reported_as_validation_failure(self):
        """실물 장치를 이미 연 프로세스는 같은 인덱스를 hw_emu 로 다시 열 수
        없다 -- 그래서 참조 실행은 별도 프로세스에서 돈다(_reference_output()).
        그 프로세스가 실패해도 run() 자체가 죽지 않고 검증 실패로 보고해야 한다.
        """
        self.make_module()

        def fake_run(command, **kwargs):
            payload = {"status": "failed", "error": "could not open device 0 in hw_emu mode"}
            return subprocess.CompletedProcess(command, 1, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=fake_run):
            result = run(self.workspace, "demo", self._input(), "hw")
        self.assertFalse(result.ok)
        self.assertFalse(result.validation["passed"])
        self.assertIn("could not open device 0", result.validation["detail"])

    def test_wrong_kernel_argument_index_is_reported(self):
        self.make_module()

        def wrong_adapter(session, data, parameters):
            session.buffer(len(data), arg=9)
            return data

        with mock.patch("isc.lifecycle.load_callable", return_value=wrong_adapter):
            result = run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertFalse(result.ok)
        self.assertIn("out of range", result.error)

    def test_scalar_argument_cannot_be_used_as_a_buffer(self):
        self.make_module()

        def wrong_adapter(session, data, parameters):
            session.buffer(4, arg=2)
            return data

        with mock.patch("isc.lifecycle.load_callable", return_value=wrong_adapter):
            result = run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertFalse(result.ok)
        self.assertIn("not a buffer", result.error)

    def test_undeclared_target_is_rejected(self):
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "sw_emu")
        self.assertFalse(result.ok)
        self.assertIn("not declared", result.error)

    def test_missing_input_is_recorded_not_raised(self):
        self.make_module()
        result = run(self.workspace, "demo", Path(self._temp.name) / "nope.bin", "hw_emu")
        self.assertFalse(result.ok)
        self.assertIn("input file not found", result.error)
        self.assertEqual(len(record.read_all(self.workspace)), 1)

    def test_adapter_returning_wrong_type_is_reported(self):
        self.make_module()

        def wrong_adapter(session, data, parameters):
            return "not bytes"

        with mock.patch("isc.lifecycle.load_callable", return_value=wrong_adapter):
            result = run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertFalse(result.ok)
        self.assertIn("must return bytes", result.error)


class Records(PlatformTestCase):
    def test_csv_export_has_a_row_per_run(self):
        self.make_module()
        source = Path(self._temp.name) / "in.bin"
        source.write_bytes(b"abc")
        run(self.workspace, "demo", source, "hw_emu")
        run(self.workspace, "demo", source, "hw_emu")
        csv_text = record.to_csv(record.read_all(self.workspace))
        self.assertEqual(len(csv_text.strip().splitlines()), 3)  # header + 2 runs

    def test_records_survive_as_valid_json_lines(self):
        self.make_module()
        source = Path(self._temp.name) / "in.bin"
        source.write_bytes(b"abc")
        run(self.workspace, "demo", source, "hw_emu")
        for line in self.workspace.records_path.read_text().splitlines():
            json.loads(line)


class BuildCache(PlatformTestCase):
    def test_force_rebuilds(self):
        self.make_module()
        manifest = self.workspace.load("demo")
        build(self.workspace, manifest, "hw_emu")
        build(self.workspace, manifest, "hw_emu", force=True)
        self.assertEqual(FakeBackend.build_calls, 2)


if __name__ == "__main__":
    unittest.main()
