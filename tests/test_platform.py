"""플랫폼 코어 테스트 -- FPGA 도, Vitis 도 없이 전 주기를 검증한다.

가짜 백엔드를 하나 끼워 넣어 생성->빌드->실행->검증->기록->재사용이
실제로 도는지 확인한다. 이 테스트가 도는 것 자체가 벤더 이음새가
제대로 분리되어 있다는 증거다.
"""
from __future__ import annotations

import hashlib
import json
import io
import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from unittest import mock

from isc import backends, record, scaffold
from isc.backends.base import (
    Backend, BackendError, Buffer, BuildOutput, BuildRequest, DeviceInfo, Session,
)
from isc.build import build, fingerprint
from isc.check import check_module
from isc.cli import _cancel_on_signals, main as cli_main
from isc.devices import DeviceManager, Owner
from isc.elements import lookup
from isc.events import MemorySink, start_run
from isc.generate import HookTypes
from isc.lifecycle import revalidate, run, run_build
from isc.loader import load_callable
from isc.manifest import Manifest, ManifestError
from isc.operations import (
    OperationError, _validate_hook_spec, get_operation, get_profile, operation_ids, profile_ids,
)
from isc.scaffold import create_hook_module, create_module
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


class HookSession(Session):
    """CPU stand-in for the generated filter / map / aggregate wrapper kernels."""

    def __init__(self, manifest, function=None) -> None:
        super().__init__(
            DeviceInfo(backend="fake", index=0, name="fake-device"), manifest.kernel.arguments
        )
        self.profile = manifest.hook.profile
        self.types = manifest.hook_types()
        if function is not None:
            self.function = function
        elif self.profile == "map":
            self.function = lambda value: value
        else:
            self.function = lambda value: True

    def buffer(self, size: int, arg: int) -> Buffer:
        self.check_buffer_argument(arg)
        return FakeBuffer(size)

    def run(self, *args) -> None:
        self.check_run_arity(len(args))
        getattr(self, f"_{self.profile}")(*args)

    def _values(self, buffer, rows: int):
        element = self.types.inp.element
        return element.unpack(buffer.read(rows * element.size))

    def _filter(self, input_buffer, output_buffer, rows, count_buffer) -> None:
        kept = [value for value in self._values(input_buffer, rows) if self.function(value)]
        output_buffer.write(self.types.inp.element.pack(kept))
        count_buffer.write(len(kept).to_bytes(4, "little"))

    def _map(self, input_buffer, output_buffer, rows) -> None:
        mapped = [self.function(value) for value in self._values(input_buffer, rows)]
        output_buffer.write(self.types.out.element.pack(mapped))

    def _aggregate(self, input_buffer, result_buffer, rows) -> None:
        kept = [value for value in self._values(input_buffer, rows) if self.function(value)]
        mask = (1 << 64) - 1
        fields = [len(kept), sum(kept) & mask,
                  (min(kept) if kept else 0) & mask, (max(kept) if kept else 0) & mask]
        result_buffer.write(struct.pack("<4Q", *fields))


class FakeBackend(Backend):
    name = "fake"
    build_calls = 0
    last_request = None
    hook_function = None  # 훅 커널이 사용자 함수 대신 실행할 파이썬 함수

    def available(self):
        return True, "fake backend"

    def devices(self):
        return [DeviceInfo(backend=self.name, index=0, name="fake-device")]

    def toolchain_identity(self) -> str:
        return "fake-toolchain-1.0"

    def build(self, request: BuildRequest) -> BuildOutput:
        FakeBackend.build_calls += 1
        FakeBackend.last_request = request
        binary = request.output_dir / "module.fake"
        binary.write_text("fake device image")
        return BuildOutput(binary=binary, log="fake build")

    def open(self, manifest, binary, target, device_index=None) -> Session:
        if manifest.is_hook:
            return HookSession(manifest, FakeBackend.hook_function)
        return FakeSession(
            DeviceInfo(backend=self.name, index=device_index or 0,
                       name="fake-device", emulated=target.endswith("_emu"), target=target),
            manifest.kernel.arguments,
        )


class PlatformTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._xrt_backend = backends._REGISTRY["xrt"]
        backends._REGISTRY["xrt"] = FakeBackend
        FakeBackend.build_calls = 0
        FakeBackend.last_request = None
        FakeBackend.hook_function = None
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
            payload = {"status": "succeeded", "output_path": str(output_path),
                       "run_id": self.REFERENCE_RUN_ID}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return mock.patch("isc.lifecycle.subprocess.run", side_effect=fake_run)

    REFERENCE_RUN_ID = "20261007T000000-ref000"


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

    def test_custom_module_only_exposes_the_hook_source(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        self.assertEqual(
            sorted(item.name for item in path.iterdir()),
            ["README.md", "logic.hpp", "module.yaml"],
        )
        self.assertIn("isc_filter", (path / "logic.hpp").read_text())
        self.assertFalse((path / "kernel").exists())
        self.assertFalse((path / "adapter.py").exists())


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

    def test_hook_module_resolves_profile_and_user_source(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        manifest = Manifest.load(path)
        self.assertTrue(manifest.is_hook)
        self.assertEqual(manifest.operation_id, "filter")
        self.assertEqual(manifest.hook.symbol, "isc_filter")
        self.assertEqual(manifest.hook_source, path / "logic.hpp")
        self.assertIn(manifest.hook_source, manifest.source_files)

    def test_hook_source_cannot_escape_the_module(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        manifest_path = path / "module.yaml"
        manifest_path.write_text(
            manifest_path.read_text().replace("source: logic.hpp", "source: ../logic.hpp")
        )
        with self.assertRaises(ManifestError) as caught:
            Manifest.load(path)
        self.assertIn("must stay inside", str(caught.exception))

    def test_hook_input_must_follow_the_profile_contract(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        with self.assertRaises(OperationError):
            Manifest.load(path).validate_input(b"not-u32")


class OperationCatalog(PlatformTestCase):
    def test_expected_operations_are_available(self):
        self.assertEqual(
            operation_ids(),
            ["adler32", "crc32", "identity", "lz4", "sha256", "vector_topk"],
        )

    def test_hook_profiles_are_separate_from_built_in_operations(self):
        self.assertIn("filter", profile_ids())
        self.assertNotIn("table_filter_u32", profile_ids())
        self.assertNotIn("filter", operation_ids())
        profile = get_profile("filter")
        self.assertEqual(profile.kind, "hook")
        self.assertEqual(profile.hook_spec["symbol"], "isc_filter")
        self.assertEqual(profile.raw["kernel"]["name"], "isc_filter_kernel")

    def test_the_old_profile_name_is_an_alias_of_filter(self):
        self.assertEqual(get_profile("table_filter_u32").id, "filter")
        path = create_hook_module(self.workspace, "price_filter", profile_id="table_filter_u32")
        self.assertIn("profile: filter", (path / "module.yaml").read_text())

    def test_a_module_yaml_that_still_says_the_old_name_loads_as_filter(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="filter")
        manifest_path = path / "module.yaml"
        manifest_path.write_text(
            manifest_path.read_text().replace("profile: filter", "profile: table_filter_u32")
        )
        manifest = Manifest.load(path)
        self.assertEqual(manifest.hook.profile, "filter")
        self.assertEqual(manifest.operation_id, "filter")

    def _filter_module(self, module_id: str, spelling: str):
        path = create_hook_module(
            self.workspace, module_id, profile_id="filter",
            logic=f"inline bool isc_filter({spelling} value) {{ return true; }}\n",
        )
        return Manifest.load(path)

    def test_filter_adapter_compacts_matching_rows_for_every_element_type(self):
        cases = (
            ("uint32_t", struct.pack("<4I", 10, 20, 30, 40), lambda v: v >= 30,
             struct.pack("<2I", 30, 40)),
            ("int16_t", struct.pack("<4h", -5, 0, 5, 10), lambda v: v > 0,
             struct.pack("<2h", 5, 10)),
            ("float", struct.pack("<4f", 0.5, 1.5, 2.5, 3.5), lambda v: v > 1.0,
             struct.pack("<3f", 1.5, 2.5, 3.5)),
        )
        for index, (spelling, payload, predicate, expected) in enumerate(cases):
            with self.subTest(type=spelling):
                manifest = self._filter_module(f"filter_{index}", spelling)
                execute = load_callable(manifest.adapter_directory, "adapter.py", "execute", "filter")
                output = execute(HookSession(manifest, predicate), payload, manifest.adapter_parameters)
                self.assertEqual(output, expected)

    def test_filter_adapter_returns_nothing_for_empty_input(self):
        manifest = self._filter_module("filter_empty", "uint32_t")
        execute = load_callable(manifest.adapter_directory, "adapter.py", "execute", "filter")
        self.assertEqual(execute(HookSession(manifest), b"", manifest.adapter_parameters), b"")

    def test_map_is_a_hook_profile(self):
        self.assertIn("map", profile_ids())
        profile = get_profile("map")
        self.assertEqual(profile.hook_spec["symbol"], "isc_map")
        self.assertEqual(profile.raw["kernel"]["name"], "isc_map_kernel")

    def test_map_adapter_converts_every_row_to_the_output_type(self):
        path = create_hook_module(
            self.workspace, "quantize", profile_id="map",
            logic="inline uint8_t isc_map(float value) { return 0; }\n",
        )
        manifest = Manifest.load(path)
        execute = load_callable(manifest.adapter_directory, "adapter.py", "execute", "map")
        session = HookSession(manifest, lambda value: int(value * 2))
        output = execute(session, struct.pack("<3f", 0.0, 1.5, 100.0), manifest.adapter_parameters)
        self.assertEqual(output, bytes([0, 3, 200]))

    def test_the_catalog_has_exactly_the_three_shapes(self):
        self.assertEqual(profile_ids(), ["aggregate", "filter", "map"])

    def test_aggregate_adapter_returns_the_four_statistics(self):
        path = create_hook_module(
            self.workspace, "stats", profile_id="aggregate",
            logic="inline bool isc_where(int32_t value) { return true; }\n",
        )
        manifest = Manifest.load(path)
        execute = load_callable(manifest.adapter_directory, "adapter.py", "execute", "aggregate")
        session = HookSession(manifest, lambda value: value > 0)
        output = execute(session, struct.pack("<4i", -4, 3, -9, 8), manifest.adapter_parameters)
        self.assertEqual(output, struct.pack("<4Q", 2, 11, 3, 8))

    def test_aggregate_of_empty_input_is_all_zeros_without_running_the_kernel(self):
        path = create_hook_module(self.workspace, "stats", profile_id="aggregate")
        manifest = Manifest.load(path)
        execute = load_callable(manifest.adapter_directory, "adapter.py", "execute", "aggregate")

        class NoKernel(HookSession):
            def run(self, *args):
                raise AssertionError("the kernel must not run for empty input")

        self.assertEqual(
            execute(NoKernel(manifest), b"", manifest.adapter_parameters), bytes(32),
        )

    def test_aggregate_rejects_float_input(self):
        with self.assertRaises(ValueError):
            create_hook_module(self.workspace, "stats", profile_id="aggregate", in_type="f32")

    def test_map_adapter_returns_nothing_for_empty_input(self):
        path = create_hook_module(self.workspace, "quantize", profile_id="map")
        manifest = Manifest.load(path)
        execute = load_callable(manifest.adapter_directory, "adapter.py", "execute", "map")
        self.assertEqual(execute(HookSession(manifest), b"", manifest.adapter_parameters), b"")

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

    def test_parameter_scope_defaults_to_build(self):
        """스코프를 빠뜨린 파라미터는 재빌드를 유발해야 한다 -- 잘못된 기본값이
        낡은 바이너리를 재사용하게 만드는 쪽으로 기울면 안 된다."""
        operation = get_operation("lz4")
        unannotated = replace(operation, raw={
            **operation.raw, "parameters": {"knob": {"type": "integer"}},
        })
        self.assertEqual(unannotated.parameter_scope("knob"), "build")
        self.assertEqual(unannotated.build_parameters({"knob": 7}), {"knob": 7})

    def test_host_scoped_parameter_is_kept_out_of_the_build_key(self):
        operation = get_operation("lz4")
        self.assertEqual(operation.parameter_scope("block_size"), "host")
        self.assertEqual(operation.build_parameters({"block_size": 4096}), {})

    def test_unsupported_parameter_scope_is_rejected(self):
        directory = Path(self._temp.name) / "operations" / "bogus"
        directory.mkdir(parents=True)
        (directory / "operation.yaml").write_text(
            "operation: {id: bogus}\n"
            "parameters:\n  knob: {type: integer, scope: kernel}\n"
        )
        with mock.patch("isc.operations.OPERATIONS_DIR", directory.parent):
            with self.assertRaises(OperationError) as caught:
                get_operation("bogus")
        self.assertIn("scope", str(caught.exception))

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


class HookSpecValidation(unittest.TestCase):
    """프로파일 operation.yaml 의 hook: 절을 읽을 때 규격이 스스로 일관한지 본다."""

    def _spec(self, **changes):
        spec = {
            "symbol": "isc_x", "signature": "bool isc_x(T value)", "returns": "bool",
            "output_element": "input", "element_types": ["u32"], "template": "t.template",
            "staged_header": "h.hpp", "input_description": "i", "output_description": "o",
            "examples": {"int": ["return true;"]},
        }
        spec.update(changes)
        return spec

    def _check(self, spec):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "t.template").write_text("x")
            _validate_hook_spec(Path(directory) / "operation.yaml", spec)

    def test_a_complete_spec_is_accepted(self):
        self._check(self._spec())

    def test_each_required_field_is_enforced(self):
        for field in ("symbol", "signature", "returns", "output_element", "element_types",
                      "template", "staged_header", "input_description", "output_description",
                      "examples"):
            with self.subTest(field=field):
                spec = self._spec()
                del spec[field]
                with self.assertRaises(OperationError):
                    self._check(spec)

    def test_unknown_element_types_are_rejected(self):
        with self.assertRaises(OperationError) as caught:
            self._check(self._spec(element_types=["u32", "u12"]))
        self.assertIn("u12", str(caught.exception))

    def test_returns_and_output_element_must_agree(self):
        for changes in ({"returns": "element"}, {"output_element": "declared"},
                        {"returns": "string"}, {"output_element": "everything"}):
            with self.subTest(changes=changes):
                with self.assertRaises(OperationError):
                    self._check(self._spec(**changes))

    def test_float_support_requires_float_examples(self):
        with self.assertRaises(OperationError):
            self._check(self._spec(element_types=["u32", "f32"]))
        self._check(self._spec(element_types=["u32", "f32"],
                               examples={"int": ["a"], "float": ["b"]}))

    def test_a_missing_template_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OperationError):
                _validate_hook_spec(Path(directory) / "operation.yaml", self._spec())


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

    def test_profiles_and_new_custom_commands(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli_main(["profiles"]), 0)
            self.assertIn("filter", output.getvalue())
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            result = cli_main([
                "--workspace", str(self.workspace.root), "new-custom", "price_filter",
                "--profile", "table_filter_u32",
            ])
        self.assertEqual(result, 0)
        self.assertTrue((self.workspace.modules_dir / "price_filter" / "logic.hpp").is_file())


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

    def test_hook_module_uses_the_managed_adapter(self):
        create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        source = self._input(
            b"".join(value.to_bytes(4, "little") for value in (10, 20, 30))
        )
        result = run(self.workspace, "price_filter", source, "hw_emu")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(Path(result.output_path).read_bytes(), source.read_bytes())

    def test_aggregate_module_runs_end_to_end(self):
        create_hook_module(
            self.workspace, "stats", profile_id="aggregate",
            logic="inline bool isc_where(int32_t value) { return true; }\n",
        )
        FakeBackend.hook_function = lambda value: value > 0
        source = self._input(struct.pack("<4i", -4, 3, -9, 8))
        result = run(self.workspace, "stats", source, "hw_emu")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(Path(result.output_path).read_bytes(), struct.pack("<4Q", 2, 11, 3, 8))

    def test_map_module_runs_end_to_end(self):
        create_hook_module(
            self.workspace, "quantize", profile_id="map",
            logic="inline uint8_t isc_map(float value) { return 0; }\n",
        )
        FakeBackend.hook_function = lambda value: int(value * 2)
        source = self._input(struct.pack("<3f", 0.0, 1.5, 100.0))
        result = run(self.workspace, "quantize", source, "hw_emu")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(Path(result.output_path).read_bytes(), bytes([0, 3, 200]))

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

    # -- 검증은 실행 전에: hw 이미지를 만들기 전에 hw_emu 로 먼저 돌려 본다 -------------
    def test_a_hw_run_emulates_first_and_only_then_builds_the_hw_image(self):
        """hw 빌드는 수십 분, hw_emu 는 몇 분이다. 로직이 틀렸다면 hw 이미지를 만들기 전에
        알아야 하므로 참조(hw_emu) 실행이 먼저 돌고, 바이트 비교만 hw 실행 뒤에 한다."""
        self.make_module()
        built_when_the_reference_ran = []

        def reference_run(command, **kwargs):
            built_when_the_reference_ran.append(FakeBackend.build_calls)
            input_path = Path(command[command.index("--input") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(input_path.read_bytes())
            payload = {"status": "succeeded", "output_path": str(output_path)}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=reference_run):
            result = run(self.workspace, "demo", self._input(), "hw")

        self.assertTrue(result.ok, result.error)
        self.assertEqual(built_when_the_reference_ran, [0], "nothing may be built before the reference run")
        self.assertEqual(FakeBackend.build_calls, 1)
        self.assertIn("matches reference target output", result.validation["detail"])

    def test_a_failing_reference_run_stops_the_run_before_the_hw_image_is_built(self):
        self.make_module()

        def reference_run(command, **kwargs):
            payload = {"status": "failed", "error": "kernel hung in hw_emu"}
            return subprocess.CompletedProcess(command, 1, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=reference_run):
            result = run(self.workspace, "demo", self._input(), "hw")

        self.assertFalse(result.ok)
        self.assertEqual(FakeBackend.build_calls, 0)
        self.assertFalse(result.validation["passed"])
        self.assertIn("kernel hung in hw_emu", result.validation["detail"])
        self.assertIn("kernel hung in hw_emu", result.error)
        self.assertEqual(result.output_path, "")
        self.assertEqual(record.read_all(self.workspace)[0]["status"], "failed")

    def test_a_reference_mismatch_is_still_found_after_the_hw_run(self):
        """비교는 hw 출력이 생긴 뒤에야 가능하다. 순서가 바뀌어도 불일치는 실패여야 한다."""
        self.make_module()
        with self._mock_reference_run(lambda data: data[::-1]):
            result = run(self.workspace, "demo", self._input(), "hw")
        self.assertFalse(result.ok)
        self.assertEqual(FakeBackend.build_calls, 1)
        self.assertIn("first difference", result.validation["detail"])

    def test_no_validate_skips_the_reference_run(self):
        self.make_module()
        with mock.patch("isc.lifecycle.subprocess.run",
                        side_effect=AssertionError("no reference run with skip_validation")):
            result = run(self.workspace, "demo", self._input(), "hw", skip_validation=True)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.validation["detail"], "skipped")

    def test_an_input_the_operation_rejects_never_starts_a_reference_run(self):
        create_module(self.workspace, "checker", operation_id="crc32")  # 최소 1 바이트가 필요하다
        with mock.patch("isc.lifecycle.subprocess.run",
                        side_effect=AssertionError("no reference run for an invalid input")):
            result = run(self.workspace, "checker", self._input(b""), "hw")
        self.assertFalse(result.ok)
        self.assertIn("at least 1", result.error)
        self.assertEqual(FakeBackend.build_calls, 0)

    def test_revalidate_compares_an_existing_output_with_a_fresh_reference_run(self):
        self.make_module()
        source = self._input()
        output = Path(self._temp.name) / "hw.out"
        output.write_bytes(source.read_bytes())

        with self._mock_reference_run(lambda data: data):
            same = revalidate(self.workspace, "demo", source, output, "hw")
        with self._mock_reference_run(lambda data: data + b"x"):
            different = revalidate(self.workspace, "demo", source, output, "hw")

        self.assertTrue(same.passed, same.detail)
        self.assertFalse(different.passed)
        self.assertIn("size mismatch", different.detail)

    # -- 기록만으로 대조를 추적할 수 있어야 한다: 해시와 기준 실행 run_id --------------------
    def test_a_run_records_the_sha256_of_its_input_and_output(self):
        self.make_module()
        source = self._input()
        result = run(self.workspace, "demo", source, "hw_emu")

        self.assertTrue(result.ok, result.error)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(result.input_sha256, digest)
        self.assertEqual(result.output_sha256,
                         hashlib.sha256(Path(result.output_path).read_bytes()).hexdigest())
        stored = record.read_all(self.workspace)[0]
        self.assertEqual(stored["input_sha256"], digest)
        self.assertEqual(stored["output_sha256"], result.output_sha256)

    def test_a_cross_target_run_names_the_reference_run_it_was_compared_with(self):
        """기준(hw_emu) 실행은 별도 프로세스라 records.jsonl 에 따로 남는다. hw 행만 보고도
        어느 기준 실행과, 어떤 바이트와 비교했는지 알 수 있어야 한다."""
        self.make_module()
        with self._mock_reference_run(lambda data: data):
            result = run(self.workspace, "demo", self._input(), "hw")

        self.assertTrue(result.ok, result.error)
        reference = result.validation["reference"]
        self.assertEqual(reference["run_id"], self.REFERENCE_RUN_ID)
        self.assertEqual(reference["target"], "hw_emu")
        self.assertEqual(reference["output_sha256"], result.output_sha256)
        self.assertEqual(reference["output_bytes"], result.output_bytes)
        self.assertEqual(record.read_all(self.workspace)[0]["validation"]["reference"], reference)

    def test_a_mismatch_still_names_the_reference_run(self):
        self.make_module()
        with self._mock_reference_run(lambda data: data + b"x"):
            result = run(self.workspace, "demo", self._input(), "hw")

        self.assertFalse(result.ok)
        reference = result.validation["reference"]
        self.assertEqual(reference["run_id"], self.REFERENCE_RUN_ID)
        self.assertNotEqual(reference["output_sha256"], result.output_sha256)

    def test_a_failing_reference_run_is_named_in_the_error(self):
        self.make_module()

        def reference_run(command, **kwargs):
            payload = {"status": "failed", "error": "kernel hung in hw_emu", "run_id": "ref-failed"}
            return subprocess.CompletedProcess(command, 1, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=reference_run):
            result = run(self.workspace, "demo", self._input(), "hw")

        self.assertFalse(result.ok)
        self.assertIn("ref-failed", result.validation["detail"])

    def test_nothing_to_compare_names_no_reference(self):
        self.make_module()
        on_reference = run(self.workspace, "demo", self._input(), "hw_emu")
        unvalidated = run(self.workspace, "demo", self._input(), "hw", skip_validation=True)
        self.assertNotIn("reference", on_reference.validation)
        self.assertNotIn("reference", unvalidated.validation)

    def test_revalidate_names_the_reference_run(self):
        self.make_module()
        source = self._input()
        output = Path(self._temp.name) / "hw.out"
        output.write_bytes(source.read_bytes())
        with self._mock_reference_run(lambda data: data):
            result = revalidate(self.workspace, "demo", source, output, "hw")
        self.assertTrue(result.passed, result.detail)
        self.assertEqual(result.to_dict()["reference"]["run_id"], self.REFERENCE_RUN_ID)

    # -- 빌드 전 정적 검사: 모듈 id 로 실행해도 `isc check` 와 같은 검사를 통과해야 한다 ----
    def test_run_by_module_id_applies_the_static_checks_before_any_build(self):
        create_hook_module(
            self.workspace, "price_filter", profile_id="filter",
            logic="inline bool isc_filter(uint32_t value) { int *p = (int *)malloc(4); return true; }\n",
        )
        result = run(self.workspace, "price_filter", self._input((7).to_bytes(4, "little")), "hw_emu")

        self.assertFalse(result.ok)
        self.assertIn("static check failed", result.error)
        self.assertIn("dynamic allocation", result.error)
        self.assertEqual(FakeBackend.build_calls, 0)
        self.assertEqual(record.read_all(self.workspace)[0]["status"], "failed")

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

    def test_undeclared_target_is_rejected_before_anything_slow_starts(self):
        """sw_emu 는 module.yaml 에 없다. 참조 실행도 빌드도 시작하기 전에 거절해야 한다."""
        self.make_module()
        with mock.patch("isc.lifecycle.subprocess.run",
                        side_effect=AssertionError("no reference run for an undeclared target")):
            result = run(self.workspace, "demo", self._input(), "sw_emu")
        self.assertFalse(result.ok)
        self.assertIn("not declared", result.error)
        self.assertEqual(FakeBackend.build_calls, 0)

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


class DeviceReservation(PlatformTestCase):
    """The device is reserved through the runtime; what a run measures keeps its meaning."""

    def _input(self, payload: bytes = b"the quick brown fox" * 10) -> Path:
        path = Path(self._temp.name) / "input.bin"
        path.write_bytes(payload)
        return path

    def hold_device(self, seconds: float, *, target: str = "hw_emu") -> None:
        """Another reservation on the fake device, released after `seconds`."""
        ready = threading.Event()

        def hold():
            holder = Owner(run_id="holder-run", pid=os.getpid(), purpose="run")
            with DeviceManager(self.workspace).reserve("fake", target, 0, owner=holder):
                ready.set()
                time.sleep(seconds)

        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        self.assertTrue(ready.wait(5))

    def _cli(self, *argv) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_total_ms_still_counts_the_wait_and_says_how_much_of_it_was_waiting(self):
        self.make_module()
        self.hold_device(0.5)
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertTrue(result.ok, result.error)
        self.assertGreaterEqual(result.metrics["device_wait_ms"], 300)
        self.assertGreaterEqual(result.metrics["total_ms"], 300)
        self.assertLessEqual(result.metrics["device_wait_ms"], result.metrics["total_ms"])

    def test_an_uncontended_run_reports_no_wait_and_the_open_time(self):
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertLess(result.metrics["device_wait_ms"], 100)
        self.assertGreaterEqual(result.metrics["device_open_ms"], 0)
        self.assertEqual(list(result.metrics)[:3], ["total_ms", "device_wait_ms", "device_open_ms"])

    def test_the_device_is_identified_in_the_record(self):
        self.make_module()
        run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertEqual(record.read_all(self.workspace)[0]["device"]["id"], "fake:emu:hw_emu:0")

    def test_the_lock_file_keeps_the_name_older_processes_use(self):
        self.make_module()
        run(self.workspace, "demo", self._input(), "hw_emu")
        self.assertTrue((self.workspace.locks_dir / "fake-hw_emu-0.lock").is_file())

    def test_a_device_time_limit_fails_the_run_and_names_the_holder(self):
        self.make_module()
        self.hold_device(0.8)
        result = run(self.workspace, "demo", self._input(), "hw_emu", device_timeout_s=0.2)
        self.assertFalse(result.ok)
        self.assertIn("fake-hw_emu-0", result.error)
        self.assertIn("holder-run", result.error)
        self.assertEqual(result.output_path, "")
        self.assertEqual(record.read_all(self.workspace)[0]["status"], "failed")

    def test_the_caller_hears_about_the_wait(self):
        self.make_module()
        self.hold_device(0.5)
        seen = []
        result = run(self.workspace, "demo", self._input(), "hw_emu", on_wait=seen.append)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(seen[0].key, "fake-hw_emu-0")
        self.assertEqual(seen[0].holder.run_id, "holder-run")

    def _holders_seen_while_executing(self, **env) -> list:
        seen: list = []

        def adapter(session, data, parameters):
            seen.extend(DeviceManager(self.workspace).holders())
            return data

        with mock.patch.dict(os.environ, env), \
                mock.patch("isc.lifecycle.load_callable", return_value=adapter):
            self.result = run(self.workspace, "demo", self._input(), "hw_emu")
        return seen

    def test_a_run_is_the_holder_while_it_executes(self):
        self.make_module()
        (holder,) = self._holders_seen_while_executing()
        self.assertEqual((holder.run_id, holder.purpose, holder.pid),
                         (self.result.run_id, "run", os.getpid()))

    def test_a_reference_run_says_so(self):
        self.make_module()
        (holder,) = self._holders_seen_while_executing(**{record.PARENT_RUN_ENV: "parent-run"})
        self.assertEqual(holder.purpose, "reference")

    def _reference_call(self, **options) -> dict:
        self.make_module()
        captured: dict = {}

        def reference_run(command, **kwargs):
            captured["command"], captured["env"] = command, kwargs.get("env") or {}
            input_path = Path(command[command.index("--input") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(input_path.read_bytes())
            payload = {"status": "succeeded", "output_path": str(output_path)}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=reference_run):
            captured["result"] = run(self.workspace, "demo", self._input(), "hw", **options)
        return captured

    def test_the_reference_process_learns_its_parent_and_the_device_time_limit(self):
        captured = self._reference_call(device_timeout_s=30)
        command = captured["command"]
        self.assertEqual(command[command.index("--device-timeout") + 1], "30")
        self.assertEqual(captured["env"][record.PARENT_RUN_ENV], captured["result"].run_id)

    def test_without_a_time_limit_the_reference_process_gets_no_flag(self):
        captured = self._reference_call()
        self.assertNotIn("--device-timeout", captured["command"])
        self.assertEqual(captured["env"][record.PARENT_RUN_ENV], captured["result"].run_id)

    def test_the_command_line_says_who_holds_the_device_and_gives_up_after_the_limit(self):
        self.make_module()
        self.hold_device(0.8)
        code, out, err = self._cli("run", "demo", "--input", str(self._input()),
                                   "--target", "hw_emu", "--device-timeout", "0.3")
        self.assertEqual(code, 1)
        self.assertIn("waiting for device fake-hw_emu-0: reserved by run holder-run", err)
        self.assertIn("is reserved by run holder-run", err)

    def test_the_waiting_notice_leaves_json_output_alone(self):
        self.make_module()
        self.hold_device(0.8)
        code, out, err = self._cli("--json", "run", "demo", "--input", str(self._input()),
                                   "--target", "hw_emu", "--device-timeout", "0.3")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "failed")
        self.assertIn("waiting for device fake-hw_emu-0", err)


def stage_flow(events: list[dict]) -> list[tuple[str, str]]:
    return [(event["stage"], event["state"]) for event in events if event["type"] == "stage"]


class LifecycleEvents(PlatformTestCase):
    """What `run` tells anyone following it: each stage in order, and how the run ended."""

    def _input(self, payload: bytes = b"the quick brown fox" * 10) -> Path:
        path = Path(self._temp.name) / "input.bin"
        path.write_bytes(payload)
        return path

    def _run(self, target: str = "hw_emu", **options):
        sink = MemorySink()
        result = run(self.workspace, "demo", self._input(), target, events=sink, **options)
        return result, sink.events

    def hold_device(self, seconds: float, target: str = "hw_emu") -> None:
        ready = threading.Event()

        def hold():
            holder = Owner(run_id="holder-run", pid=os.getpid(), purpose="run")
            with DeviceManager(self.workspace).reserve("fake", target, 0, owner=holder):
                ready.set()
                time.sleep(seconds)

        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        self.assertTrue(ready.wait(5))

    def test_a_run_on_the_reference_target_reports_every_stage_in_order(self):
        self.make_module()
        result, events = self._run()
        self.assertTrue(result.ok, result.error)
        self.assertEqual(stage_flow(events), [
            ("check", "running"), ("check", "succeeded"),
            ("reference", "skipped"),
            ("build", "running"), ("build", "succeeded"),
            ("device_wait", "running"), ("device_wait", "succeeded"),
            ("device_open", "running"), ("device_open", "succeeded"),
            ("execute", "running"), ("execute", "succeeded"),
            ("compare", "skipped"),
            ("record", "running"), ("record", "succeeded"),
        ])
        first, last = events[0], events[-1]
        self.assertEqual((first["type"], first["state"], first["kind"], first["module"],
                          first["target"], first["backend"], first["run_id"]),
                         ("run", "started", "run", "demo", "hw_emu", "xrt", result.run_id))
        self.assertEqual((last["type"], last["state"], last["status"]), ("run", "finished", "succeeded"))

    def test_a_hw_run_emulates_first_and_names_the_child_run(self):
        self.make_module()

        def reference_run(command, **kwargs):
            input_path = Path(command[command.index("--input") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(input_path.read_bytes())
            payload = {"status": "succeeded", "output_path": str(output_path), "run_id": "child-1"}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=reference_run):
            result, events = self._run("hw")
        self.assertTrue(result.ok, result.error)
        flow = stage_flow(events)
        self.assertLess(flow.index(("reference", "succeeded")), flow.index(("build", "running")))
        self.assertIn(("compare", "succeeded"), flow)
        reference = [e for e in events if e["type"] == "stage" and e["stage"] == "reference"]
        self.assertEqual(reference[0]["detail"], {"mode": "cross_target", "target": "hw_emu"})
        self.assertEqual(reference[1]["detail"], {"child_run_id": "child-1"})
        compare = [e for e in events if e["type"] == "stage" and e["stage"] == "compare"]
        self.assertEqual(compare[-1]["detail"], {"passed": True})

    def test_a_failing_reference_ends_the_run_before_any_build_stage_appears(self):
        self.make_module()

        def reference_run(command, **kwargs):
            payload = {"status": "failed", "error": "kernel hung in hw_emu"}
            return subprocess.CompletedProcess(command, 1, stdout=json.dumps(payload), stderr="")

        with mock.patch("isc.lifecycle.subprocess.run", side_effect=reference_run):
            result, events = self._run("hw")
        self.assertFalse(result.ok)
        self.assertEqual(stage_flow(events), [
            ("check", "running"), ("check", "succeeded"),
            ("reference", "running"), ("reference", "failed"),
        ])
        self.assertIn("kernel hung in hw_emu", events[-2]["detail"]["error"])
        self.assertEqual((events[-1]["state"], events[-1]["status"]), ("finished", "failed"))
        self.assertIn("kernel hung in hw_emu", events[-1]["error"])

    def test_no_validate_skips_the_reference_and_the_comparison(self):
        self.make_module()
        with mock.patch("isc.lifecycle.subprocess.run", side_effect=AssertionError("no reference run")):
            result, events = self._run("hw", skip_validation=True)
        self.assertTrue(result.ok, result.error)
        skipped = [(e["stage"], e["detail"]) for e in events if e.get("state") == "skipped"]
        self.assertEqual(skipped, [("reference", {"reason": "no-validate"}),
                                   ("compare", {"reason": "no-validate"})])

    def test_a_cached_build_says_so(self):
        self.make_module()
        _, first = self._run()
        _, second = self._run()

        def built(events):
            return [e for e in events if e.get("stage") == "build" and e["state"] == "succeeded"][0]["detail"]

        self.assertIs(built(first)["cached"], False)
        self.assertIs(built(second)["cached"], True)
        self.assertEqual(built(first)["fingerprint"], built(second)["fingerprint"])

    def test_a_mismatch_fails_the_comparison_and_the_run_but_the_record_is_still_written(self):
        self.make_module()
        with self._mock_reference_run(lambda data: data + b"x"):
            result, events = self._run("hw")
        self.assertFalse(result.ok)
        self.assertEqual(stage_flow(events)[-4:], [
            ("compare", "running"), ("compare", "failed"), ("record", "running"), ("record", "succeeded"),
        ])
        failed = [e for e in events if e.get("stage") == "compare" and e["state"] == "failed"][0]
        self.assertIs(failed["detail"]["passed"], False)
        self.assertIn("size mismatch", failed["detail"]["error"])
        self.assertEqual(events[-1]["status"], "failed")
        self.assertIn("validation failed", events[-1]["error"])

    def test_a_missing_input_fails_the_check_stage(self):
        self.make_module()
        sink = MemorySink()
        run(self.workspace, "demo", Path(self._temp.name) / "nope.bin", "hw_emu", events=sink)
        self.assertEqual(stage_flow(sink.events), [("check", "running"), ("check", "failed")])
        self.assertIn("input file not found", sink.events[-2]["detail"]["error"])
        self.assertEqual(sink.events[-1]["status"], "failed")

    def test_an_input_the_operation_rejects_fails_the_check_stage(self):
        create_module(self.workspace, "checker", operation_id="crc32")
        sink = MemorySink()
        run(self.workspace, "checker", self._input(b""), "hw", events=sink)
        self.assertEqual(stage_flow(sink.events), [("check", "running"), ("check", "failed")])
        self.assertIn("at least 1", sink.events[-2]["detail"]["error"])

    def test_an_adapter_that_raises_fails_the_execute_stage(self):
        self.make_module()

        def broken(session, data, parameters):
            raise ValueError("adapter exploded")

        with mock.patch("isc.lifecycle.load_callable", return_value=broken):
            result, events = self._run()
        self.assertFalse(result.ok)
        self.assertEqual(stage_flow(events)[-3:], [
            ("device_open", "succeeded"), ("execute", "running"), ("execute", "failed")])
        self.assertEqual(events[-2]["detail"], {"error": "ValueError: adapter exploded"})
        self.assertIn("adapter exploded", events[-1]["error"])

    def test_an_interrupt_is_written_as_cancelled_and_still_raised(self):
        self.make_module()

        def interrupted(session, data, parameters):
            raise KeyboardInterrupt

        sink = MemorySink()
        with mock.patch("isc.lifecycle.load_callable", return_value=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                run(self.workspace, "demo", self._input(), "hw_emu", events=sink)
        last = sink.events[-1]
        self.assertEqual((last["type"], last["state"], last["status"]), ("run", "finished", "cancelled"))
        self.assertNotIn("error", last)
        self.assertEqual(stage_flow(sink.events)[-1], ("execute", "failed"))

    def test_without_a_sink_the_events_go_to_a_file_named_after_the_run(self):
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        path = self.workspace.events_dir / f"{result.run_id}.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual((lines[0]["type"], lines[-1]["status"]), ("run", "succeeded"))
        self.assertNotIn("events", result.to_dict())

    def test_the_callback_sees_what_the_sink_gets(self):
        self.make_module()
        sink, seen = MemorySink(), []
        run(self.workspace, "demo", self._input(), "hw_emu", events=sink, on_event=seen.append)
        self.assertEqual(seen, sink.events)

    def test_a_run_started_by_another_run_names_its_parent(self):
        self.make_module()
        with mock.patch.dict(os.environ, {record.PARENT_RUN_ENV: "parent-1"}):
            _, events = self._run()
        self.assertEqual(events[0]["parent_run_id"], "parent-1")

    def test_waiting_for_the_device_is_reported_while_it_lasts(self):
        self.make_module()
        self.hold_device(0.5)
        result, events = self._run()
        self.assertTrue(result.ok, result.error)
        waiting = [e for e in events if e["type"] == "progress" and e["stage"] == "device_wait"]
        self.assertTrue(waiting)
        self.assertEqual(waiting[0]["step"], "waiting")
        self.assertIn("holder-run", waiting[0]["message"])
        waited = [e for e in events if e.get("stage") == "device_wait" and e.get("state") == "succeeded"][0]
        self.assertGreaterEqual(waited["detail"]["wait_ms"], 300)
        opened = [e for e in events if e.get("stage") == "device_open" and e.get("state") == "succeeded"][0]
        self.assertEqual(opened["detail"]["device"], "fake:emu:hw_emu:0")

    def test_a_device_time_limit_fails_the_wait_stage_and_nothing_executes(self):
        self.make_module()
        self.hold_device(0.8)
        result, events = self._run(device_timeout_s=0.2)
        self.assertFalse(result.ok)
        flow = stage_flow(events)
        self.assertIn(("device_wait", "failed"), flow)
        self.assertNotIn(("execute", "running"), flow)
        failed = [e for e in events if e.get("stage") == "device_wait" and e.get("state") == "failed"][0]
        self.assertIn("holder-run", failed["detail"]["error"])


class BuildAsARun(PlatformTestCase):
    """`isc build` is a long job like any other: it gets a run id and events, but no record."""

    def setUp(self):
        super().setUp()
        self.make_module()
        self.manifest = self.workspace.load("demo")

    def test_a_build_is_a_run_with_one_stage_and_no_record(self):
        sink = MemorySink()
        result = run_build(self.workspace, self.manifest, "hw_emu", events=sink)
        self.assertEqual(stage_flow(sink.events), [("build", "running"), ("build", "succeeded")])
        first, last = sink.events[0], sink.events[-1]
        self.assertEqual((first["kind"], first["module"], first["target"], first["backend"]),
                         ("build", "demo", "hw_emu", "xrt"))
        self.assertEqual((last["state"], last["status"]), ("finished", "succeeded"))
        built = [e for e in sink.events if e.get("stage") == "build" and e.get("state") == "succeeded"][0]
        self.assertEqual(built["detail"], {"fingerprint": result.fingerprint, "cached": False})
        self.assertEqual(record.read_all(self.workspace), [])

    def test_the_second_build_is_a_cache_hit(self):
        run_build(self.workspace, self.manifest, "hw_emu", events=MemorySink())
        sink = MemorySink()
        run_build(self.workspace, self.manifest, "hw_emu", events=sink)
        built = [e for e in sink.events if e.get("stage") == "build" and e.get("state") == "succeeded"][0]
        self.assertIs(built["detail"]["cached"], True)

    def test_a_failed_build_is_a_failed_run_and_the_error_still_propagates(self):
        sink = MemorySink()
        with mock.patch.object(FakeBackend, "build", side_effect=BackendError("v++ not found")):
            with self.assertRaisesRegex(BackendError, "v\\+\\+ not found"):
                run_build(self.workspace, self.manifest, "hw_emu", events=sink)
        self.assertEqual(stage_flow(sink.events), [("build", "running"), ("build", "failed")])
        self.assertEqual((sink.events[-1]["status"], sink.events[-1]["error"]),
                         ("failed", "BackendError: v++ not found"))

    def test_an_interrupt_is_written_as_cancelled_and_still_raised(self):
        sink = MemorySink()
        with mock.patch.object(FakeBackend, "build", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_build(self.workspace, self.manifest, "hw_emu", events=sink)
        self.assertEqual(sink.events[-1]["status"], "cancelled")


class ProgressFlag(PlatformTestCase):
    """--progress puts the events on stderr; stdout and --json stay what they were."""

    def _cli(self, *argv) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def _input(self) -> str:
        path = Path(self._temp.name) / "in.bin"
        path.write_bytes(b"abc" * 10)
        return str(path)

    def test_run_prints_each_stage_to_stderr(self):
        self.make_module()
        code, out, err = self._cli("run", "demo", "--input", self._input(), "--target", "hw_emu",
                                   "--progress")
        self.assertEqual(code, 0)
        for line in ("  check: running", "  check: done in", "  reference: skipped", "  build: running",
                     "  device_wait: running", "  execute: running", "  record: done in",
                     "finished: succeeded"):
            self.assertIn(line, err)
        self.assertNotIn("check: running", out)

    def test_without_the_flag_nothing_is_printed_to_stderr(self):
        self.make_module()
        code, _, err = self._cli("run", "demo", "--input", self._input(), "--target", "hw_emu")
        self.assertEqual((code, err), (0, ""))

    def test_json_output_is_unchanged_by_the_flag(self):
        self.make_module()
        code, out, err = self._cli("--json", "run", "demo", "--input", self._input(),
                                   "--target", "hw_emu", "--progress")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "succeeded")
        self.assertIn("  build: running", err)

    def test_build_prints_its_stages_and_is_a_run_without_a_record(self):
        self.make_module()
        code, out, err = self._cli("build", "demo", "--target", "hw_emu", "--progress")
        self.assertEqual(code, 0)
        self.assertIn("demo [hw_emu] built", out)
        self.assertIn("  build: running", err)
        self.assertIn("  build: done in", err)
        (path,) = list(self.workspace.events_dir.glob("*.jsonl"))
        self.assertEqual(json.loads(path.read_text().splitlines()[0])["kind"], "build")
        self.assertEqual(record.read_all(self.workspace), [])


class StatusAndWatch(PlatformTestCase):
    """`isc status` and `isc watch` read the event files and nothing else."""

    def _cli(self, *argv) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def _input(self, payload: bytes = b"abc" * 10) -> Path:
        path = Path(self._temp.name) / "in.bin"
        path.write_bytes(payload)
        return path

    def test_status_lists_recent_runs_newest_first(self):
        self.make_module()
        for _ in range(3):
            run(self.workspace, "demo", self._input(), "hw_emu")
        code, out, _ = self._cli("--json", "status")
        self.assertEqual(code, 0)
        runs = json.loads(out)
        ids = [item["run_id"] for item in runs]
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual({item["state"] for item in runs}, {"succeeded"})
        self.assertEqual({(item["kind"], item["module"], item["target"]) for item in runs},
                         {("run", "demo", "hw_emu")})

    def test_status_text_has_a_header_and_a_line_per_run(self):
        self.make_module()
        run(self.workspace, "demo", self._input(), "hw_emu")
        code, out, _ = self._cli("status")
        self.assertEqual(code, 0)
        header, line = out.splitlines()
        for column in ("RUN", "KIND", "MODULE", "TARGET", "STATE", "STAGE", "ELAPSED"):
            self.assertIn(column, header)
        for part in ("run", "demo", "hw_emu", "succeeded"):
            self.assertIn(part, line)

    def test_status_limit_and_an_empty_workspace(self):
        code, out, _ = self._cli("status")
        self.assertEqual(code, 0)
        self.assertIn("no runs yet", out)
        self.make_module()
        for _ in range(3):
            run(self.workspace, "demo", self._input(), "hw_emu")
        _, out, _ = self._cli("--json", "status", "--limit", "1")
        self.assertEqual(len(json.loads(out)), 1)

    def test_a_run_in_progress_shows_its_stage_and_progress(self):
        events = start_run(self.workspace, "20261002T120000-aaaaaa", kind="run", module="demo",
                           target="hw", heartbeat_s=0)
        stage = events.stage("build")
        stage.progress("vpl.synth", current=40, total=114, unit="jobs")
        code, out, _ = self._cli("status")
        self.assertEqual(code, 0)
        self.assertIn("running", out)
        self.assertIn("build", out)
        self.assertIn("synth 40/114", out)

    def test_a_silent_run_whose_process_is_gone_is_shown_as_lost(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.workspace.events_dir.mkdir(parents=True)
        started = {"v": 1, "seq": 1, "ts": "2020-01-01T00:00:00+0000", "run_id": "20200101T000000-aaaaaa",
                   "type": "run", "state": "started", "kind": "run", "module": "demo", "target": "hw",
                   "backend": "xrt", "pid": gone.pid, "host": socket.gethostname()}
        (self.workspace.events_dir / "20200101T000000-aaaaaa.jsonl").write_text(json.dumps(started) + "\n")
        _, out, _ = self._cli("--json", "status")
        self.assertEqual(json.loads(out)[0]["state"], "lost")

    def test_watch_replays_a_finished_run_and_exits_zero(self):
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        code, out, _ = self._cli("watch", result.run_id)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^\d\d:\d\d:\d\d  run " + result.run_id + r": run demo \[hw_emu\]$")
        self.assertIn("check: running", out)
        self.assertEqual(lines[-1][10:], "finished: succeeded")

    def test_watch_json_prints_the_events_as_they_are_in_the_file(self):
        self.make_module()
        result = run(self.workspace, "demo", self._input(), "hw_emu")
        code, out, _ = self._cli("--json", "watch", result.run_id)
        self.assertEqual(code, 0)
        printed = [json.loads(line) for line in out.splitlines()]
        stored = [json.loads(line) for line in
                  (self.workspace.events_dir / f"{result.run_id}.jsonl").read_text().splitlines()]
        self.assertEqual(printed, stored)

    def test_watch_exits_one_for_a_run_that_failed(self):
        self.make_module()
        result = run(self.workspace, "demo", Path(self._temp.name) / "nope.bin", "hw_emu")
        code, out, _ = self._cli("watch", result.run_id)
        self.assertEqual(code, 1)
        self.assertIn("finished: failed -- input file not found", out)

    def test_watch_of_an_unknown_run_says_so(self):
        code, _, err = self._cli("watch", "20261002T000000-nothing")
        self.assertEqual(code, 2)
        self.assertIn("unknown run", err)

    def test_watch_follows_a_run_that_is_still_going_until_it_ends(self):
        events = start_run(self.workspace, "20261002T130000-bbbbbb", kind="run", module="demo",
                           target="hw", heartbeat_s=0)

        def go_on():
            time.sleep(0.3)
            with events.stage("build"):
                time.sleep(0.3)
            events.finish("succeeded")

        thread = threading.Thread(target=go_on)
        thread.start()
        self.addCleanup(thread.join)
        code, out, _ = self._cli("watch", "20261002T130000-bbbbbb")
        self.assertEqual(code, 0)
        self.assertIn("build: running", out)
        self.assertIn("finished: succeeded", out)


FAKE_LONG_VPP = """#!{python}
import os, sys, time
if "--version" in sys.argv:
    print("v++ fake 1.0")
    sys.exit(0)
open({pidfile!r}, "w").write(str(os.getpid()))
print("INFO: [v++ 60-585] Compiling for hardware target", flush=True)
time.sleep(60)
"""


def _running(pid: int) -> bool:
    """A process that is running; a killed one nobody has collected yet (a zombie) is not."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class Interrupts(PlatformTestCase):
    """A long job must be cancellable from outside: Ctrl-C, `kill`, a closed terminal."""

    def test_ctrl_c_is_one_line_and_exit_code_130(self):
        with mock.patch("isc.cli._dispatch", side_effect=KeyboardInterrupt), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), "list"])
        self.assertEqual((code, err.getvalue()), (130, "interrupted\n"))

    def test_the_signal_handlers_are_installed_for_a_real_command_line_only(self):
        with mock.patch("isc.cli._cancel_on_signals") as install, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            cli_main(["--workspace", str(self.workspace.root), "list"])
            self.assertFalse(install.called)
            with mock.patch("sys.argv", ["isc", "--workspace", str(self.workspace.root), "list"]):
                cli_main()
            self.assertTrue(install.called)

    def test_termination_signals_become_interrupts(self):
        saved = {name: signal.getsignal(getattr(signal, name)) for name in ("SIGTERM", "SIGHUP")}

        def restore():
            for name, handler in saved.items():
                signal.signal(getattr(signal, name), handler)

        self.addCleanup(restore)
        _cancel_on_signals()
        for name in ("SIGTERM", "SIGHUP"):
            with self.subTest(signal=name), self.assertRaises(KeyboardInterrupt):
                os.kill(os.getpid(), getattr(signal, name))
                time.sleep(1)               # the handler runs before this returns

    def test_a_signalled_build_ends_its_tool_and_is_recorded_as_cancelled(self):
        self.make_module()
        bin_dir = Path(self._temp.name) / "bin"
        bin_dir.mkdir()
        pidfile = Path(self._temp.name) / "tool.pid"
        fake = bin_dir / "v++"
        fake.write_text(FAKE_LONG_VPP.format(python=sys.executable, pidfile=str(pidfile)))
        fake.chmod(0o755)
        root = Path(backends.__file__).resolve().parents[2]
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PYTHONPATH": str(root)}
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            with self.subTest(signal=name):
                if pidfile.exists():
                    pidfile.unlink()
                proc = subprocess.Popen(
                    [sys.executable, "-m", "isc", "--workspace", str(self.workspace.root),
                     "build", "demo", "--target", "hw", "--force"],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
                self.addCleanup(lambda p=proc: p.poll() is None and p.kill())
                deadline = time.monotonic() + 30
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(pidfile.exists(), "the tool never started")
                tool_pid = int(pidfile.read_text())

                proc.send_signal(getattr(signal, name))
                _, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 130, err)
                self.assertIn("interrupted", err)
                deadline = time.monotonic() + 5
                while _running(tool_pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(_running(tool_pid), "the tool kept running")
                newest = max(self.workspace.events_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
                last = json.loads(newest.read_text().splitlines()[-1])
                self.assertEqual((last["type"], last["state"], last["status"]),
                                 ("run", "finished", "cancelled"))


class DeviceListing(PlatformTestCase):
    """`isc devices` and `isc doctor` go through the device manager; JSON gains the stable id."""

    def _cli(self, *argv) -> tuple[int, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue()

    def test_the_json_listing_carries_the_stable_id(self):
        code, out = self._cli("--json", "devices")
        self.assertEqual(code, 0)
        (device,) = json.loads(out)
        self.assertEqual((device["id"], device["index"], device["name"]),
                         ("fake:index0", 0, "fake-device"))

    def test_the_text_listing_is_unchanged(self):
        code, out = self._cli("devices")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "[0] fake-device    (fake)")

    def test_doctor_lists_the_same_devices_with_ids(self):
        code, out = self._cli("--json", "doctor")
        self.assertEqual(code, 0)
        devices = json.loads(out)["backends"]["xrt"]["devices"]
        self.assertEqual([device["id"] for device in devices], ["fake:index0"])

    def test_a_backend_that_cannot_list_devices_lists_none(self):
        with mock.patch.object(FakeBackend, "devices", side_effect=RuntimeError("no driver")):
            code, out = self._cli("--json", "devices")
            doctor_code, doctor_out = self._cli("--json", "doctor")
        self.assertEqual((code, json.loads(out)), (1, []))
        backend = json.loads(doctor_out)["backends"]["xrt"]
        self.assertEqual((doctor_code, backend["available"], backend["devices"]), (0, True, []))


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
    def test_hook_logic_changes_the_fingerprint(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        before = fingerprint(Manifest.load(path), FakeBackend(), "hw_emu")
        logic = path / "logic.hpp"
        logic.write_text(logic.read_text().replace("return true;", "return value > 1000;"))
        after = fingerprint(Manifest.load(path), FakeBackend(), "hw_emu")
        self.assertNotEqual(before, after)

    def test_hook_abi_changes_the_fingerprint(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        manifest = Manifest.load(path)
        changed = replace(
            manifest,
            operation=replace(manifest.operation, raw={
                **manifest.operation.raw,
                "hook": {**manifest.operation.hook_spec, "staged_header": "changed.hpp"},
            }),
        )
        self.assertNotEqual(
            fingerprint(manifest, FakeBackend(), "hw_emu"),
            fingerprint(changed, FakeBackend(), "hw_emu"),
        )

    def test_hook_build_stages_platform_wrapper_and_user_header(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        build(self.workspace, Manifest.load(path), "hw_emu")
        request = FakeBackend.last_request
        self.assertEqual(request.kernel_source.parent, request.output_dir)
        self.assertIn("isc_filter_kernel", request.kernel_source.read_text())
        staged = request.output_dir / "isc_user_logic.hpp"
        self.assertTrue(staged.is_file())
        self.assertEqual(staged.read_text(), (path / "logic.hpp").read_text())

    def test_hook_build_renders_the_wrapper_for_the_users_types(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="filter",
            logic="inline bool isc_filter(float value) { return value > 0.0f; }\n",
        )
        build(self.workspace, Manifest.load(path), "hw_emu")
        source = FakeBackend.last_request.kernel_source.read_text()
        self.assertIn("const float* input", source)
        self.assertNotIn("${", source)

    def test_the_hook_types_alone_change_the_fingerprint(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="filter")
        manifest = Manifest.load(path)

        def key(inp: str, out: str | None = None) -> str:
            types = HookTypes(inp=lookup(inp), out=lookup(out) if out else None)
            with mock.patch.object(Manifest, "hook_types", return_value=types):
                return fingerprint(manifest, FakeBackend(), "hw_emu")

        self.assertNotEqual(key("uint32_t"), key("float"))
        # 같은 요소의 다른 표기도 다른 래퍼 소스를 만들므로 다른 빌드다. 입력과 출력을 따로 본다
        # (필터는 둘이 같아서 한쪽이 다른 쪽을 가리므로, 출력이 없거나 다른 모양을 흉내 낸다).
        self.assertNotEqual(key("ap_uint<32>"), key("uint32_t"))
        self.assertNotEqual(key("float", "ap_uint<8>"), key("float", "uint8_t"))
        self.assertNotEqual(key("float", "uint8_t"), key("float"))

    def test_a_built_in_operation_keeps_its_cache_key_inputs(self):
        """지문 입력이 하나라도 늘면 기존 모듈의 hw 빌드(LZ4는 약 37분)가 전부 무효가 된다.
        훅 전용 입력은 훅 모듈에만 들어가야 한다."""
        self.make_module()
        manifest = self.workspace.load("demo")
        captured = {}
        real_dumps = json.dumps

        def spy(payload, **kwargs):
            captured.update(payload)
            return real_dumps(payload, **kwargs)

        with mock.patch.object(json, "dumps", side_effect=spy):
            fingerprint(manifest, FakeBackend(), "hw_emu")
        self.assertIn("kernel", captured)  # spy 가 실제 지문 입력을 받았는지
        self.assertNotIn("hook_types", captured)

    def _failing_build(self, request):
        raise BackendError("v++ compile failed (exit 1)")

    def test_a_failed_hook_build_says_where_the_generated_wrapper_is(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="filter")
        with mock.patch.object(FakeBackend, "build", self._failing_build):
            with self.assertRaises(BackendError) as caught:
                build(self.workspace, Manifest.load(path), "hw_emu")
        message = str(caught.exception)
        self.assertIn("v++ compile failed", message)
        self.assertIn("isc_filter_kernel.cpp", message)

    def test_a_failed_built_in_build_keeps_its_message(self):
        self.make_module()
        with mock.patch.object(FakeBackend, "build", self._failing_build):
            with self.assertRaises(BackendError) as caught:
                build(self.workspace, self.workspace.load("demo"), "hw_emu")
        self.assertEqual(str(caught.exception), "v++ compile failed (exit 1)")

    def _lz4_module(self, module_id: str, block_size: int) -> Manifest:
        create_module(self.workspace, module_id, operation_id="lz4",
                      parameters={"block_size": block_size})
        return self.workspace.load(module_id)

    def test_host_scoped_parameter_does_not_force_a_rebuild(self):
        """block_size 는 호스트 어댑터만 읽는다. 값이 달라도 장치 바이너리는
        같으므로 빌드는 한 번이어야 한다 -- hw 타깃에서 이 차이가 몇 시간이다."""
        small = self._lz4_module("small_blocks", 4096)
        large = self._lz4_module("large_blocks", 65536)
        with mock.patch.dict("os.environ", {"VITIS_LIBRARIES": self._temp.name}):
            first = build(self.workspace, small, "hw_emu")
            second = build(self.workspace, large, "hw_emu")

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertTrue(second.cached)
        self.assertEqual(FakeBackend.build_calls, 1)

    def test_build_scoped_parameter_changes_the_fingerprint(self):
        """호스트 스코프가 아닌 파라미터는 반대로 캐시를 반드시 갈라야 한다."""
        manifest = self._lz4_module("tuned", 4096)
        as_build_scoped = replace(
            manifest,
            operation=replace(manifest.operation, raw={
                **manifest.operation.raw,
                "parameters": {"block_size": {"type": "integer", "scope": "build"}},
            }),
        )
        backend = FakeBackend()
        with mock.patch.dict("os.environ", {"VITIS_LIBRARIES": self._temp.name}):
            self.assertNotEqual(
                fingerprint(manifest, backend, "hw_emu"),
                fingerprint(as_build_scoped, backend, "hw_emu"),
            )

    def test_force_rebuilds(self):
        self.make_module()
        manifest = self.workspace.load("demo")
        build(self.workspace, manifest, "hw_emu")
        build(self.workspace, manifest, "hw_emu", force=True)
        self.assertEqual(FakeBackend.build_calls, 2)


class HookChecks(PlatformTestCase):
    def test_missing_entry_point_is_rejected_before_build(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        logic = path / "logic.hpp"
        logic.write_text(logic.read_text().replace("isc_filter", "wrong_name"))
        report = check_module(path)
        self.assertFalse(report.passed)
        self.assertIn("isc_filter", "\n".join(report.problems))

    def _module_with(self, logic: str) -> Path:
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        (path / "logic.hpp").write_text(logic)
        return path

    def test_unsupported_hook_signature_is_rejected_before_build(self):
        path = self._module_with("inline bool isc_filter(unsigned int value) { return true; }\n")
        report = check_module(path)
        self.assertFalse(report.passed)
        self.assertIn("isc_filter", "\n".join(report.problems))
        self.assertIn("bool isc_filter", "\n".join(report.problems))

    def test_standard_integer_spelling_is_a_valid_signature(self):
        path = self._module_with("inline bool isc_filter(uint32_t value) { return true; }\n")
        self.assertTrue(check_module(path).passed)

    def test_forbidden_words_inside_comments_are_not_checked(self):
        path = self._module_with(
            "// never call malloc(4) or add #pragma HLS INTERFACE m_axi here\n"
            "/* a new value must not use free(p) */\n"
            "inline bool isc_filter(uint32_t value) { return true; }\n"
        )
        report = check_module(path)
        self.assertTrue(report.passed, report.problems)

    def test_a_commented_out_function_counts_as_missing(self):
        path = self._module_with("// bool isc_filter(uint32_t value) { return true; }\n")
        report = check_module(path)
        self.assertFalse(report.passed)
        self.assertIn("isc_filter", "\n".join(report.problems))

    def test_the_starter_marker_is_a_warning_not_a_problem(self):
        path = self._module_with(
            "inline bool isc_filter(uint32_t value) { return true; }  // ISC-TODO: not edited\n"
        )
        report = check_module(path)
        self.assertTrue(report.passed, report.problems)
        self.assertTrue(any("ISC-TODO" in warning for warning in report.warnings))
        self.assertEqual(report.to_dict()["warnings"], report.warnings)

    def test_an_edited_file_has_no_warnings(self):
        path = self._module_with("inline bool isc_filter(uint32_t value) { return value > 5; }\n")
        self.assertEqual(check_module(path).warnings, [])

    def test_user_cannot_override_the_hls_interface(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32"
        )
        logic = path / "logic.hpp"
        logic.write_text(logic.read_text() + "\n#pragma HLS INTERFACE m_axi port=value\n")
        report = check_module(path)
        self.assertFalse(report.passed)
        self.assertIn("interface pragmas", "\n".join(report.problems))


class StarterFiles(PlatformTestCase):
    def test_default_starter_is_u32_in_standard_spelling_with_the_marker(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="table_filter_u32")
        text = (path / "logic.hpp").read_text()
        self.assertIn("bool isc_filter(uint32_t value)", text)
        self.assertIn("return true;", text)
        self.assertIn("ISC-TODO", text)

    def test_starter_states_the_data_contract_for_the_chosen_type(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32", in_type="f32"
        )
        text = (path / "logic.hpp").read_text()
        self.assertIn("little-endian", text)
        self.assertIn("f32", text)

    def test_the_chosen_type_is_declared_and_read_back(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32", in_type="i16"
        )
        self.assertIn("bool isc_filter(int16_t value)", (path / "logic.hpp").read_text())
        self.assertEqual(Manifest.load(path).hook_types().inp.element.name, "i16")

    def test_an_unsupported_type_is_rejected_and_leaves_no_module(self):
        with self.assertRaises(ValueError):
            create_hook_module(
                self.workspace, "price_filter", profile_id="table_filter_u32", in_type="double"
            )
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_the_module_readme_describes_the_chosen_types(self):
        path = create_hook_module(
            self.workspace, "price_filter", profile_id="table_filter_u32", in_type="f32"
        )
        self.assertIn("f32", (path / "README.md").read_text())

    def test_a_fresh_starter_passes_the_static_check_with_a_warning(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="table_filter_u32")
        report = check_module(path)
        self.assertTrue(report.passed, report.problems)
        self.assertTrue(report.warnings)


class HookTypesInManifest(PlatformTestCase):
    """Manifest 가 사용자 함수의 시그니처에서 요소 타입을 읽는다."""

    def _hook(self, signature: str) -> Path:
        path = create_hook_module(self.workspace, "price_filter", profile_id="table_filter_u32")
        (path / "logic.hpp").write_text(f"#include <ap_int.h>\n{signature} {{ return true; }}\n")
        return path

    def test_types_come_from_the_users_signature(self):
        types = Manifest.load(self._hook("inline bool isc_filter(float value)")).hook_types()
        self.assertEqual((types.inp.element.name, types.inp.spelling), ("f32", "float"))

    def test_input_must_be_a_whole_number_of_the_declared_elements(self):
        manifest = Manifest.load(self._hook("inline bool isc_filter(uint16_t value)"))
        manifest.validate_input(b"\x00\x00")          # 값 하나
        with self.assertRaises(OperationError):
            manifest.validate_input(b"\x00\x00\x00")  # 3 바이트

    def test_adapter_parameters_carry_the_types_but_user_parameters_do_not(self):
        manifest = Manifest.load(self._hook("inline bool isc_filter(int32_t value)"))
        self.assertEqual(manifest.adapter_parameters["_types"]["in_bytes"], 4)
        self.assertEqual(manifest.adapter_parameters["_types"]["in_fmt"], "i")
        self.assertNotIn("_types", manifest.parameters)

    def test_a_module_that_is_not_a_hook_gets_its_parameters_unchanged(self):
        self.make_module("demo")
        manifest = self.workspace.load("demo")
        self.assertEqual(manifest.adapter_parameters, manifest.parameters)

    def test_inspect_view_includes_the_types(self):
        manifest = Manifest.load(self._hook("inline bool isc_filter(ap_uint<8> value)"))
        self.assertEqual(manifest.to_dict()["hook"]["types"]["in"]["element"], "u8")

    def test_an_unreadable_signature_fails_the_run_before_any_build(self):
        self._hook("inline bool isc_filter(unsigned int value)")
        source = Path(self._temp.name) / "input.bin"
        source.write_bytes(b"\x00" * 8)
        result = run(self.workspace, "price_filter", source, "hw_emu")
        self.assertFalse(result.ok)
        self.assertIn("isc_filter", result.error)
        self.assertEqual(FakeBackend.build_calls, 0)

    def test_the_run_hands_the_adapter_the_derived_types(self):
        self._hook("inline bool isc_filter(uint32_t value)")
        seen = []
        real_load = load_callable

        def spying_load(*args, **kwargs):
            function = real_load(*args, **kwargs)

            def execute(session, data, parameters):
                seen.append(parameters)
                return function(session, data, parameters)
            return execute

        source = Path(self._temp.name) / "input.bin"
        source.write_bytes((7).to_bytes(4, "little"))
        with mock.patch("isc.lifecycle.load_callable", side_effect=spying_load):
            result = run(self.workspace, "price_filter", source, "hw_emu")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(seen[0]["_types"]["in_bytes"], 4)


class LogicFileTestCase(PlatformTestCase):
    """로직 파일 하나만 쓰는 흐름의 공통 도구. 로직 파일은 워크스페이스 밖에 둔다."""

    def _logic(self, body: str = "return value >= 1000;", name: str = "price_filter.hpp") -> Path:
        path = Path(self._temp.name) / name
        path.write_text(
            "#pragma once\n"
            "#include <ap_int.h>\n\n"
            "inline bool isc_filter(ap_uint<32> value) {\n"
            f"    {body}\n"
            "}\n"
        )
        return path

    def _data(self) -> Path:
        path = Path(self._temp.name) / "prices.bin"
        path.write_bytes(b"".join(value.to_bytes(4, "little") for value in (10, 1500, 20, 3000)))
        return path


class LogicFirst(LogicFileTestCase):
    """isc.scaffold.create_module_from_logic -- 로직 파일에서 모듈을 만든다."""

    def test_users_code_is_copied_verbatim_into_a_valid_hook_module(self):
        logic = self._logic()
        result = scaffold.create_module_from_logic(self.workspace, logic)

        self.assertEqual(result.module_id, "price_filter")
        self.assertEqual(result.profile_id, "filter")
        self.assertEqual(result.action, "created")
        self.assertEqual((result.path / "logic.hpp").read_text(), logic.read_text())
        manifest = self.workspace.load("price_filter")
        self.assertTrue(manifest.is_hook)
        self.assertEqual(manifest.hook.profile, "filter")
        self.assertTrue(check_module(result.path).passed)

    def test_explicit_id_and_profile_are_honored(self):
        result = scaffold.create_module_from_logic(
            self.workspace, self._logic(), module_id="orders", profile_id="table_filter_u32"
        )
        self.assertEqual(result.module_id, "orders")
        self.assertTrue((self.workspace.modules_dir / "orders" / "module.yaml").is_file())
        self.assertFalse((self.workspace.modules_dir / "price_filter").exists())

    def test_file_name_that_is_not_a_module_id_is_rejected_and_says_where_the_id_came_from(self):
        with self.assertRaises(ValueError) as caught:
            scaffold.create_module_from_logic(self.workspace, self._logic(name="Price-Filter.hpp"))
        self.assertIn("Price-Filter.hpp", str(caught.exception))
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_same_file_again_changes_nothing(self):
        logic = self._logic()
        scaffold.create_module_from_logic(self.workspace, logic)
        key = fingerprint(self.workspace.load("price_filter"), FakeBackend(), "hw_emu")

        second = scaffold.create_module_from_logic(self.workspace, logic)

        self.assertEqual(second.action, "unchanged")
        self.assertEqual(
            fingerprint(self.workspace.load("price_filter"), FakeBackend(), "hw_emu"), key
        )

    def test_editing_the_file_updates_the_module_and_its_build_key(self):
        scaffold.create_module_from_logic(self.workspace, self._logic("return value >= 1000;"))
        before = fingerprint(self.workspace.load("price_filter"), FakeBackend(), "hw_emu")

        edited = self._logic("return value >= 2000;")  # 같은 경로를 다시 쓴다
        result = scaffold.create_module_from_logic(self.workspace, edited)

        self.assertEqual(result.action, "updated")
        self.assertIn("value >= 2000", (result.path / "logic.hpp").read_text())
        self.assertNotEqual(
            fingerprint(self.workspace.load("price_filter"), FakeBackend(), "hw_emu"), before
        )

    def test_logic_without_a_supported_signature_is_rejected_and_names_the_supported_one(self):
        logic = self._logic(name="other.hpp")
        logic.write_text("inline int twice(int x) { return 2 * x; }\n")
        with self.assertRaises(ValueError) as caught:
            scaffold.create_module_from_logic(self.workspace, logic)
        self.assertIn("bool isc_filter(T value)", str(caught.exception))
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_a_logic_file_that_belongs_to_a_module_is_that_module(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="filter")
        result = scaffold.create_module_from_logic(self.workspace, path / "logic.hpp")
        self.assertEqual((result.module_id, result.action), ("price_filter", "unchanged"))
        self.assertEqual(
            sorted(entry.name for entry in self.workspace.modules_dir.iterdir()), ["price_filter"]
        )

    def test_an_explicit_other_id_still_copies_a_modules_logic_into_a_new_module(self):
        path = create_hook_module(self.workspace, "price_filter", profile_id="filter")
        result = scaffold.create_module_from_logic(
            self.workspace, path / "logic.hpp", module_id="copy"
        )
        self.assertEqual((result.module_id, result.action), ("copy", "created"))

    def test_logic_with_an_unsupported_type_is_rejected_with_guidance(self):
        logic = self._logic(name="other.hpp")
        logic.write_text("inline bool isc_filter(unsigned int value) { return true; }\n")
        with self.assertRaises(ValueError) as caught:
            scaffold.create_module_from_logic(self.workspace, logic)
        self.assertIn("isc_filter", str(caught.exception))
        self.assertIn("bool isc_filter", str(caught.exception))
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_a_float_logic_file_becomes_an_f32_module(self):
        logic = self._logic(name="clip.hpp")
        logic.write_text("inline bool isc_filter(float value) { return value > 0.5f; }\n")
        result = scaffold.create_module_from_logic(self.workspace, logic)
        self.assertEqual(self.workspace.load("clip").hook_types().inp.element.name, "f32")
        self.assertTrue(check_module(result.path).passed)

    def test_explicit_profile_must_match_the_logic(self):
        logic = self._logic(name="other.hpp")
        logic.write_text("inline int twice(int x) { return 2 * x; }\n")
        with self.assertRaises(ValueError):
            scaffold.create_module_from_logic(self.workspace, logic, profile_id="table_filter_u32")
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_logic_matching_several_profiles_needs_an_explicit_choice(self):
        real = get_profile("table_filter_u32")
        twin = replace(real, id="table_filter_twin")  # 같은 시그니처를 가진 두 번째 프로파일
        logic = self._logic()
        with mock.patch("isc.scaffold.profiles", return_value=[real, twin]):
            with self.assertRaises(ValueError) as caught:
                scaffold.create_module_from_logic(self.workspace, logic)
            self.assertIn("--profile", str(caught.exception))
            self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

            chosen = scaffold.create_module_from_logic(
                self.workspace, logic, profile_id="table_filter_u32"
            )
        self.assertEqual(chosen.profile_id, "filter")

    def test_file_type_without_a_reader_is_rejected(self):
        with self.assertRaises(ValueError):
            scaffold.create_module_from_logic(self.workspace, self._logic(name="price_filter.txt"))
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_existing_module_of_another_kind_is_never_overwritten(self):
        self.make_module("price_filter")  # identity 연산 모듈
        manifest = self.workspace.modules_dir / "price_filter" / "module.yaml"
        before = manifest.read_text()

        with self.assertRaises(FileExistsError):
            scaffold.create_module_from_logic(self.workspace, self._logic())

        self.assertEqual(manifest.read_text(), before)
        self.assertFalse((self.workspace.modules_dir / "price_filter" / "logic.hpp").exists())


class LogicFirstCommandLine(LogicFileTestCase):
    """isc run <로직 파일> 과 isc new-custom --from <로직 파일>."""

    def _cli(self, *argv) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_run_takes_a_logic_file_and_does_everything(self):
        logic = self._logic()
        code, out, err = self._cli("--json", "run", str(logic), "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["module"], "price_filter")
        self.assertEqual(payload["logic"]["action"], "created")
        staged = FakeBackend.last_request.output_dir / "isc_user_logic.hpp"
        self.assertEqual(staged.read_text(), logic.read_text())
        self.assertEqual(len(record.read_all(self.workspace)), 1)

    def test_run_rejects_bad_logic_before_any_build(self):
        logic = self._logic("int *p = (int *)malloc(4); return true;")
        code, out, err = self._cli("run", str(logic), "--input", str(self._data()))

        self.assertEqual(code, 1)
        self.assertEqual(FakeBackend.build_calls, 0)
        self.assertEqual(record.read_all(self.workspace), [])
        self.assertIn("dynamic allocation", out + err)

    def test_run_says_what_it_did_with_the_file_before_the_long_build_starts(self):
        """hw 빌드는 수십 분이 걸린다. 파일을 어떻게 처리했는지는 그 전에 보여야 한다."""
        seen = []

        def watch(*args, **kwargs):
            seen.append(sys.stdout.getvalue())
            return run(*args, **kwargs)

        with mock.patch("isc.cli.run_module", side_effect=watch):
            code, out, err = self._cli("run", str(self._logic()), "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        self.assertIn("price_filter: created", seen[0])

    def test_run_again_with_the_same_logic_file_reuses_the_build(self):
        logic, data = self._logic(), self._data()
        self._cli("run", str(logic), "--input", str(data))

        code, out, err = self._cli("--json", "run", str(logic), "--input", str(data))

        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["logic"]["action"], "unchanged")
        self.assertEqual(FakeBackend.build_calls, 1)

    def test_editing_the_logic_file_rebuilds_instead_of_reusing_a_stale_binary(self):
        data = self._data()
        self._cli("run", str(self._logic("return value >= 1000;")), "--input", str(data))

        edited = self._logic("return value >= 2000;")
        code, out, err = self._cli("--json", "run", str(edited), "--input", str(data))

        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["logic"]["action"], "updated")
        self.assertEqual(FakeBackend.build_calls, 2)
        staged = FakeBackend.last_request.output_dir / "isc_user_logic.hpp"
        self.assertIn("value >= 2000", staged.read_text())

    def test_new_custom_from_a_logic_file_creates_the_module_without_running_it(self):
        logic = self._logic()
        code, out, err = self._cli("new-custom", "--from", str(logic))

        self.assertEqual(code, 0, out + err)
        module_logic = self.workspace.modules_dir / "price_filter" / "logic.hpp"
        self.assertEqual(module_logic.read_text(), logic.read_text())
        self.assertEqual(FakeBackend.build_calls, 0)
        self.assertEqual(record.read_all(self.workspace), [])

    def test_new_custom_without_from_still_needs_an_id_and_a_profile(self):
        for argv in (["new-custom"], ["new-custom", "price_filter"]):
            code, _, err = self._cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("--profile", err)
        self.assertEqual(list(self.workspace.modules_dir.iterdir()), [])

    def test_running_a_modules_own_logic_file_runs_that_module(self):
        self._cli("new-custom", "--from", str(self._logic()))
        module_logic = self.workspace.modules_dir / "price_filter" / "logic.hpp"
        code, out, err = self._cli("--json", "run", str(module_logic), "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["module"], "price_filter")
        self.assertEqual(
            sorted(entry.name for entry in self.workspace.modules_dir.iterdir()), ["price_filter"]
        )

    def test_run_by_module_id_is_unchanged(self):
        self.make_module("demo")
        code, out, err = self._cli("--json", "run", "demo", "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["module"], "demo")
        self.assertNotIn("logic", payload)

    def test_run_with_a_missing_logic_file_points_at_the_file(self):
        code, _, err = self._cli("run", "missing.hpp", "--input", str(self._data()))

        self.assertEqual(code, 2)
        self.assertIn("missing.hpp", err)
        self.assertNotIn("unknown module", err)


class RunByModuleIdCommandLine(LogicFileTestCase):
    """`isc run <모듈 id>` 도 로직 파일과 같은 사전 검사를 빌드 전에 통과하고, 경고는 먼저 보여 준다."""

    def _cli(self, *argv) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def _bad_module(self) -> None:
        create_hook_module(
            self.workspace, "price_filter", profile_id="filter",
            logic="inline bool isc_filter(uint32_t value) { int *p = (int *)malloc(4); return true; }\n",
        )

    def test_a_module_that_fails_the_static_check_is_not_built(self):
        self._bad_module()
        code, out, err = self._cli("run", "price_filter", "--input", str(self._data()))

        self.assertEqual(code, 1)
        self.assertEqual(FakeBackend.build_calls, 0)
        self.assertEqual(record.read_all(self.workspace), [])
        self.assertIn("dynamic allocation", out + err)

    def test_the_failed_check_is_reported_as_json_too(self):
        self._bad_module()
        code, out, _ = self._cli("--json", "run", "price_filter", "--input", str(self._data()))

        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["check"]["passed"])
        self.assertEqual(FakeBackend.build_calls, 0)

    def test_the_starter_warning_is_printed_before_the_run(self):
        self._cli("new-custom", "price_filter", "--profile", "filter")
        seen = []

        def watch(*args, **kwargs):
            seen.append(sys.stdout.getvalue())
            return run(*args, **kwargs)

        with mock.patch("isc.cli.run_module", side_effect=watch):
            code, out, err = self._cli("run", "price_filter", "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        self.assertIn("warning:", seen[0])
        self.assertIn("ISC-TODO", seen[0])

    def test_json_output_carries_the_warnings_of_a_module_run_by_id(self):
        self._cli("new-custom", "price_filter", "--profile", "filter")
        code, out, err = self._cli("--json", "run", "price_filter", "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertTrue(any("ISC-TODO" in warning for warning in payload["warnings"]))
        self.assertNotIn("logic", payload)

    def test_a_clean_module_adds_nothing_to_the_json_output(self):
        self.make_module("demo")
        code, out, err = self._cli("--json", "run", "demo", "--input", str(self._data()))

        self.assertEqual(code, 0, out + err)
        self.assertNotIn("warnings", json.loads(out))

    def test_an_unknown_module_still_lists_the_known_ones(self):
        self.make_module("demo")
        code, _, err = self._cli("run", "nope", "--input", str(self._data()))

        self.assertEqual(code, 2)
        self.assertIn("unknown module", err)
        self.assertIn("demo", err)


class TemplateCommandLine(LogicFileTestCase):
    def _cli(self, *argv) -> tuple[int, str, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_template_prints_a_starter_for_the_chosen_type(self):
        code, out, err = self._cli("template", "filter", "--type", "f32")
        self.assertEqual(code, 0, err)
        self.assertIn("bool isc_filter(float value)", out)

    def test_template_writes_a_file_that_runs_as_a_module(self):
        target = Path(self._temp.name) / "clip_filter.hpp"
        code, out, err = self._cli("template", "filter", "--type", "i32", "-o", str(target))
        self.assertEqual(code, 0, err)
        self.assertTrue(target.is_file())

        code, out, err = self._cli("--json", "run", str(target), "--input", str(self._data()))
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["logic"]["profile"], "filter")
        self.assertTrue(payload["logic"]["warnings"])  # ISC-TODO 가 남아 있다

    def test_run_prints_the_starter_warning_before_the_build(self):
        target = Path(self._temp.name) / "clip_filter.hpp"
        self._cli("template", "filter", "-o", str(target))
        code, out, err = self._cli("run", str(target), "--input", str(self._data()))
        self.assertEqual(code, 0, out + err)
        self.assertIn("warning:", out)
        self.assertIn("ISC-TODO", out)

    def test_template_refuses_to_overwrite_an_existing_file(self):
        target = Path(self._temp.name) / "mine.hpp"
        target.write_text("my work\n")
        code, _, err = self._cli("template", "filter", "-o", str(target))
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(target.read_text(), "my work\n")

    def test_map_template_takes_an_output_type(self):
        code, out, err = self._cli("template", "map", "--type", "f32", "--out-type", "u8")
        self.assertEqual(code, 0, err)
        self.assertIn("uint8_t isc_map(float value)", out)

    def test_filter_template_rejects_an_output_type(self):
        code, _, err = self._cli("template", "filter", "--out-type", "u8")
        self.assertEqual(code, 2)
        self.assertIn("--out-type", err)

    def test_an_unsupported_type_is_reported_with_the_choices(self):
        code, _, err = self._cli("template", "filter", "--type", "double")
        self.assertEqual(code, 2)
        self.assertIn("u32", err)

    def test_new_custom_takes_a_type(self):
        code, out, err = self._cli(
            "new-custom", "price_filter", "--profile", "filter", "--type", "f32"
        )
        self.assertEqual(code, 0, err)
        logic = (self.workspace.modules_dir / "price_filter" / "logic.hpp").read_text()
        self.assertIn("bool isc_filter(float value)", logic)

    def test_types_cannot_be_combined_with_from(self):
        code, _, err = self._cli("new-custom", "--from", str(self._logic()), "--type", "f32")
        self.assertEqual(code, 2)
        self.assertIn("--from", err)

    def test_profiles_lists_every_shape_with_its_signature(self):
        code, out, _ = self._cli("profiles")
        self.assertEqual(code, 0)
        for expected in ("filter", "map", "aggregate", "bool isc_filter(T value)",
                         "U isc_map(T value)", "bool isc_where(T value)"):
            self.assertIn(expected, out)

    def test_describe_profile_lists_element_types_and_notes_the_alias(self):
        code, out, _ = self._cli("describe-profile", "table_filter_u32")
        self.assertEqual(code, 0)
        self.assertIn("alias of filter", out)
        # 별칭 안내에도 'u32' 가 들어 있으므로 타입 목록 전체로 확인한다.
        self.assertIn("u8, u16, u32, u64, i8, i16, i32, i64, f32", out)

    def test_check_prints_the_starter_warning(self):
        self._cli("new-custom", "price_filter", "--profile", "filter")
        code, out, _ = self._cli("check", "price_filter")
        self.assertEqual(code, 0)
        self.assertIn("warning:", out)

    def test_inspect_shows_the_declared_types(self):
        self._cli("new-custom", "price_filter", "--profile", "filter", "--type", "i16")
        code, out, _ = self._cli("--json", "inspect", "price_filter")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hook"]["types"]["in"]["element"], "i16")


if __name__ == "__main__":
    unittest.main()
