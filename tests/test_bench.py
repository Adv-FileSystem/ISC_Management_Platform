"""가속 효과 검증 계층 테스트 -- FPGA 도, Vitis 도 없이 판정까지 검증한다.

이 계층이 답해야 하는 질문은 "얼마나 빠른가"가 아니라 "이 연산을 장치에 올릴
가치가 있는가"다. 그래서 테스트의 무게중심도 측정값이 아니라 판정에 있다.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from isc import backends, baseline, benchdata, bench, experiment, report
from isc.backends.base import Backend, Buffer, BuildOutput, BuildRequest, DeviceInfo, Session
from isc.check import check_module
from isc.cli import main as cli_main
from isc.devices import DeviceManager, Owner
from isc.events import MemorySink
from isc.experiment import Experiment, ExperimentError
from isc.lifecycle import run as run_module
from isc.loader import load_callable
from isc.operations import get_operation
from isc.scaffold import create_hook_module, create_module
from isc.workspace import Workspace


# 벽시계 대신 바이트당 고정 비용을 적립한다. 실제 시간에 기대면 단언이
# 잡음에 묻혀 "회차마다 타이머를 리셋한다"는 계약을 검증하지 못한다.
# PCIe Gen3 x16 로 실측되는 대역폭 언저리(약 8 GB/s)를 흉내 낸다. 비현실적으로
# 큰 값을 쓰면 단계 타이머의 합이 벽시계를 넘어 계측 경고가 헛돈다.
MS_PER_BYTE_H2D = 1.25e-7
MS_PER_BYTE_D2H = 1.25e-7
MS_PER_KERNEL_CALL = 0.05


class TimedBuffer(Buffer):
    """전송 단계를 세션 타이머에 적립하는 버퍼.

    h2d/d2h 가 0 이면 상한 가속비를 계산할 수 없다. 가짜 백엔드라도 그 단계를
    구분해서 적립해야 리포트 경로가 검증된다.
    """

    def __init__(self, session: Session, size: int) -> None:
        self.session = session
        self.size = size
        self.data = bytearray(size)

    def write(self, data: bytes, offset: int = 0) -> None:
        self.data[offset:offset + len(data)] = data
        self.session.timings.host_to_device_ms += len(data) * MS_PER_BYTE_H2D

    def read(self, size: int | None = None, offset: int = 0) -> bytes:
        length = self.size if size is None else size
        self.session.timings.device_to_host_ms += length * MS_PER_BYTE_D2H
        return bytes(self.data[offset:offset + length])


class OperationSession(Session):
    """CPU 위에서 각 연산의 커널이 하는 일을 흉내 낸다."""

    def __init__(self, device: DeviceInfo, manifest) -> None:
        super().__init__(device, manifest.kernel.arguments)
        self.operation_id = manifest.operation_id
        self.types = manifest.hook_types() if manifest.is_hook else None

    def buffer(self, size: int, arg: int) -> Buffer:
        self.check_buffer_argument(arg)
        return TimedBuffer(self, size)

    def run(self, *args) -> None:
        self.check_run_arity(len(args))
        getattr(self, f"_{self.operation_id}")(*args)
        self.timings.kernel_ms += MS_PER_KERNEL_CALL

    # 커널은 장치 메모리를 직접 다룬다. Buffer.write/read 를 쓰면 커널 내부
    # 동작이 호스트<->장치 전송으로 잘못 집계되어 상한 가속비가 흐려진다.
    @staticmethod
    def _poke(buffer, payload: bytes) -> None:
        buffer.data[:len(payload)] = payload

    @staticmethod
    def _peek(buffer, size: int) -> bytes:
        return bytes(buffer.data[:size])

    def _identity(self, bo_in, bo_out, size, bo_size) -> None:
        self._poke(bo_out, self._peek(bo_in, size))
        self._poke(bo_size, int(size).to_bytes(4, "little"))

    def _filter(self, bo_in, bo_out, rows, bo_count) -> None:
        # 표본 조건이 모든 값을 통과시킨다: 입력을 그대로 돌려준다.
        self._poke(bo_out, self._peek(bo_in, rows * self.types.inp.element.size))
        self._poke(bo_count, int(rows).to_bytes(4, "little"))

    def _crc32(self, bo_in, bo_out, size) -> None:
        self._poke(bo_out, zlib.crc32(self._peek(bo_in, size)).to_bytes(4, "little"))

    def _adler32(self, bo_in, bo_out, size) -> None:
        self._poke(bo_out, zlib.adler32(self._peek(bo_in, size)).to_bytes(4, "little"))

    def _sha256(self, bo_in, bo_out, size) -> None:
        self._poke(bo_out, hashlib.sha256(self._peek(bo_in, size)).digest())

    def _lz4(self, bo_in, bo_out, size, bo_size, bo_overflow) -> None:
        # 실제 LZ4 는 아니지만 어댑터의 프레이밍 분기(압축 이득 여부)를 지나간다.
        squeezed = zlib.compress(self._peek(bo_in, size), 1)
        self._poke(bo_out, squeezed)
        self._poke(bo_size, len(squeezed).to_bytes(4, "little"))
        self._poke(bo_overflow, (0).to_bytes(4, "little"))


class CountingBackend(Backend):
    name = "fake"
    open_calls = 0
    build_calls = 0

    def available(self):
        return True, "fake backend"

    def devices(self):
        return [DeviceInfo(backend=self.name, index=0, name="fake-device")]

    def toolchain_identity(self) -> str:
        return "fake-toolchain-1.0"

    def build(self, request: BuildRequest) -> BuildOutput:
        CountingBackend.build_calls += 1
        binary = request.output_dir / "module.fake"
        binary.write_text("fake device image")
        return BuildOutput(binary=binary, log="fake build")

    def open(self, manifest, binary, target, device_index=None) -> Session:
        CountingBackend.open_calls += 1
        return OperationSession(
            DeviceInfo(backend=self.name, index=device_index or 0, name="fake-device",
                       emulated=target.endswith("_emu"), target=target),
            manifest,
        )


class BenchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._xrt_backend = backends._REGISTRY["xrt"]
        backends._REGISTRY["xrt"] = CountingBackend
        CountingBackend.open_calls = 0
        CountingBackend.build_calls = 0
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)
        self._env = mock.patch.dict("os.environ", {"VITIS_LIBRARIES": self._temp.name})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        backends._REGISTRY["xrt"] = self._xrt_backend
        self._temp.cleanup()

    def plan(self, experiment_id="scan", *, modules=("copier",), sizes=("4KB", "16KB"),
             target="hw", repeat=3, warmup=1) -> Experiment:
        for module_id in modules:
            if module_id not in self.workspace.module_ids():
                create_module(self.workspace, module_id, operation_id="identity")
        path = experiment.create(
            self.workspace, experiment_id, modules=list(modules), sizes=list(sizes),
            target=target, repeat=repeat, warmup=warmup,
        )
        return Experiment.load(path)


# --------------------------------------------------------------------------
# 입력 생성
# --------------------------------------------------------------------------
class BenchData(unittest.TestCase):
    def test_size_suffixes_are_binary(self):
        self.assertEqual(benchdata.parse_size("4KB"), 4096)
        self.assertEqual(benchdata.parse_size("16MB"), 16 * 1024 ** 2)
        self.assertEqual(benchdata.parse_size(4096), 4096)

    def test_unparsable_size_is_rejected(self):
        with self.assertRaises(ValueError):
            benchdata.parse_size("a lot")

    def test_same_size_and_fill_always_give_the_same_bytes(self):
        """어제 잰 수치와 오늘 잰 수치를 비교할 수 없으면 벤치마크가 아니다."""
        for fill in benchdata.FILL_MODES:
            with self.subTest(fill=fill):
                self.assertEqual(benchdata.generate(9001, fill),
                                 benchdata.generate(9001, fill))

    def test_fill_modes_differ_in_compressibility(self):
        """압축 연산의 결과는 데이터 성질에 좌우된다 -- 채움 방식은 결과를 바꿔야 한다."""
        size = 1 << 18
        ratios = {
            fill: len(zlib.compress(benchdata.generate(size, fill), 6)) / size
            for fill in benchdata.FILL_MODES
        }
        self.assertLess(ratios["zeros"], ratios["text"])
        self.assertLess(ratios["text"], ratios["random"])


# --------------------------------------------------------------------------
# CPU 기준선
# --------------------------------------------------------------------------
class Baselines(unittest.TestCase):
    def test_exact_baselines_match_their_canonical_values(self):
        payload = b"123456789"
        cases = {
            "crc32": zlib.crc32(payload).to_bytes(4, "big"),
            "adler32": zlib.adler32(payload).to_bytes(4, "big"),
            "sha256": hashlib.sha256(payload).digest(),
            "identity": payload,
        }
        for operation_id, expected in cases.items():
            with self.subTest(operation=operation_id):
                operation = get_operation(operation_id)
                self.assertEqual(operation.baseline_equivalence, "exact")
                self.assertEqual(baseline.compute(operation, payload, {}), expected)

    def test_identity_baseline_actually_copies(self):
        """`bytes(data)` 는 같은 객체를 돌려주어 아무 일도 하지 않는다.
        identity 는 순수 이동 비용을 재는 기준이므로 실제로 복사해야 한다."""
        payload = b"x" * 4096
        produced = baseline.compute(get_operation("identity"), payload, {})
        self.assertEqual(produced, payload)
        self.assertIsNot(produced, payload)

    def test_lz4_baseline_is_performance_only(self):
        """CPU LZ4 와 커널은 서로 다른, 그러나 둘 다 올바른 블록을 낸다.
        바이트 대조를 하면 결함이 아닌 것을 결함으로 보고하게 된다."""
        self.assertEqual(get_operation("lz4").baseline_equivalence, "none")

    def test_missing_baseline_is_reported_not_substituted(self):
        operation = get_operation("crc32")
        with mock.patch.object(type(operation), "has_baseline",
                               property(lambda self: False)):
            with self.assertRaises(baseline.BaselineUnavailable):
                baseline.compute(operation, b"x", {})

    def test_measure_uses_the_requested_iteration_count(self):
        measurement = baseline.measure(get_operation("sha256"), b"payload", {},
                                       repeat=5, warmup=2)
        self.assertTrue(measurement.available)
        self.assertEqual(len(measurement.samples_ms), 5)
        self.assertEqual(measurement.output, hashlib.sha256(b"payload").digest())

    def test_unavailable_baseline_degrades_instead_of_raising(self):
        operation = get_operation("lz4")
        with mock.patch.object(baseline, "load",
                               side_effect=baseline.BaselineUnavailable("no lz4 package")):
            measurement = baseline.measure(operation, b"x", {"block_size": 4096},
                                           repeat=3, warmup=0)
        self.assertFalse(measurement.available)
        self.assertIn("no lz4 package", measurement.reason)


# --------------------------------------------------------------------------
# 실험 정의
# --------------------------------------------------------------------------
class Experiments(BenchTestCase):
    def test_created_experiment_reads_back(self):
        definition = self.plan("scan", sizes=("4KB", "1MB"))
        self.assertEqual(definition.modules, ("copier",))
        self.assertEqual(definition.input_sizes, (4096, 1024 ** 2))
        self.assertEqual(definition.target, "hw")
        self.assertTrue(definition.is_performance_data)

    def test_sizes_are_sorted_and_deduplicated(self):
        definition = self.plan("scan", sizes=("1MB", "4KB", "4096"))
        self.assertEqual(definition.input_sizes, (4096, 1024 ** 2))

    def test_emulation_target_is_flagged_as_not_performance_data(self):
        definition = self.plan("emu", target="hw_emu")
        self.assertFalse(definition.is_performance_data)

    def test_unknown_module_is_refused_at_definition_time(self):
        with self.assertRaises(ExperimentError) as caught:
            experiment.create(self.workspace, "bad", modules=["nope"], sizes=["4KB"])
        self.assertIn("nope", str(caught.exception))

    def test_unsupported_section_is_rejected(self):
        definition = self.plan("scan")
        path = definition.directory / "experiment.yaml"
        path.write_text(path.read_text() + "extra: {a: 1}\n")
        with self.assertRaises(ExperimentError) as caught:
            Experiment.load(definition.directory)
        self.assertIn("unsupported section", str(caught.exception))

    def test_empty_matrix_is_rejected(self):
        definition = self.plan("scan")
        path = definition.directory / "experiment.yaml"
        path.write_text(
            "experiment: {id: scan}\nmatrix: {modules: [], input_sizes: [4KB]}\n")
        with self.assertRaises(ExperimentError):
            Experiment.load(definition.directory)


# --------------------------------------------------------------------------
# 측정
# --------------------------------------------------------------------------
class Measurement(BenchTestCase):
    def test_device_is_opened_once_per_module_not_once_per_iteration(self):
        """이 테스트가 이 프레임워크의 존재 이유다.

        디바이스 오픈은 수 초가 걸린다. 회차마다 다시 열면 그 비용이 측정에
        섞여 들어가고, 그렇게 나온 speedup 으로 내린 판정은 틀린 판정이다.
        """
        definition = self.plan("scan", sizes=("4KB", "16KB"), repeat=4, warmup=2)
        result = bench.run(self.workspace, definition)

        self.assertEqual(CountingBackend.open_calls, 1)
        total_executions = sum(len(cell.device_samples_ms) for cell in result.cells)
        self.assertEqual(total_executions, 2 * 4)  # 2 크기 x repeat 4
        self.assertIn("device_open_ms", result.modules["copier"])

    def test_warmup_iterations_run_but_are_not_recorded(self):
        """워밍업은 실제로 돌아야 한다. 첫 회차는 할당과 캐시 때문에 느리고,
        그 값을 표본에 넣으면 CPU 쪽만 워밍업된 불공정한 비교가 된다."""
        definition = self.plan("scan", sizes=("4KB",), repeat=3, warmup=5)
        manifest = self.workspace.load("copier")
        real = load_callable(manifest.adapter_directory, "adapter.py", "execute",
                             "operation_identity")
        calls = []

        def counting(session, data, parameters):
            calls.append(len(data))
            return real(session, data, parameters)

        with mock.patch("isc.bench.load_callable", return_value=counting):
            result = bench.run(self.workspace, definition)

        self.assertEqual(len(calls), 5 + 3)
        self.assertEqual(len(result.cells[0].device_samples_ms), 3)

    def test_each_sample_carries_its_own_phase_breakdown(self):
        """회차마다 타이머를 리셋하지 않으면 h2d/kernel/d2h 가 누적되고,
        그러면 뒤쪽 회차일수록 느려 보여 상한 가속비가 무의미해진다."""
        size = 64 * 1024
        definition = self.plan("scan", sizes=("64KB",), repeat=6, warmup=0)
        result = bench.run(self.workspace, definition)
        samples = result.cells[0].device_samples_ms

        self.assertEqual(len(samples), 6)
        for index, sample in enumerate(samples):
            # identity 어댑터는 회차마다 입력을 정확히 한 번 올린다.
            self.assertAlmostEqual(sample["host_to_device_ms"],
                                   size * MS_PER_BYTE_H2D, places=6,
                                   msg=f"sample {index} accumulated across iterations")
            self.assertAlmostEqual(sample["kernel_ms"], MS_PER_KERNEL_CALL, places=6)
            self.assertGreater(sample["device_to_host_ms"], 0)
        self.assertEqual(samples[0]["host_to_device_ms"],
                         samples[-1]["host_to_device_ms"])

    def test_phase_timers_reset_between_iterations(self):
        """Timings.reset() 자체의 계약 -- 누적 타이머를 0 으로 되돌린다."""
        from isc.backends.base import Timings

        timings = Timings()
        timings.host_to_device_ms = 3.0
        timings.kernel_ms = 4.0
        timings.device_to_host_ms = 5.0
        timings.reset()
        self.assertEqual(
            (timings.host_to_device_ms, timings.kernel_ms, timings.device_to_host_ms),
            (0.0, 0.0, 0.0),
        )

    def test_cpu_baseline_is_measured_alongside(self):
        definition = self.plan("scan", sizes=("4KB",), repeat=3, warmup=1)
        result = bench.run(self.workspace, definition)
        cell = result.cells[0]
        self.assertTrue(cell.baseline["available"])
        self.assertEqual(len(cell.baseline["samples_ms"]), 3)

    def test_correctness_is_checked_once_against_the_cpu_baseline(self):
        definition = self.plan("scan", sizes=("4KB",))
        result = bench.run(self.workspace, definition)
        verification = result.cells[0].verification
        self.assertEqual(verification["mode"], "cpu_baseline")
        self.assertTrue(verification["passed"], verification["detail"])

    def test_a_wrong_device_result_is_caught(self):
        definition = self.plan("scan", sizes=("4KB",))

        def corrupt(session, data, parameters):
            return data + b"!"

        with mock.patch("isc.bench.load_callable", return_value=corrupt):
            result = bench.run(self.workspace, definition)
        self.assertFalse(result.cells[0].verification["passed"])

    def test_results_are_written_and_summarised_in_records(self):
        definition = self.plan("scan", sizes=("4KB",))
        result = bench.run(self.workspace, definition)
        self.assertTrue(result.path.exists())
        payload = json.loads(result.path.read_text())
        self.assertEqual(payload["kind"], "bench")
        lines = self.workspace.records_path.read_text().splitlines()
        self.assertEqual(json.loads(lines[-1])["experiment"], "scan")

    def test_a_failing_cell_does_not_abort_the_matrix(self):
        definition = self.plan("scan", modules=("copier", "second"), sizes=("4KB",))

        original = self.workspace.load

        def load(module_id):
            if module_id == "second":
                raise LookupError("unknown module 'second'")
            return original(module_id)

        with mock.patch.object(self.workspace, "load", side_effect=load):
            result = bench.run(self.workspace, definition)
        self.assertEqual(len(result.cells), 2)
        self.assertTrue(any(cell.error for cell in result.cells))
        self.assertTrue(any(cell.device_samples_ms for cell in result.cells))

    def test_build_is_reused_across_sizes(self):
        definition = self.plan("scan", sizes=("4KB", "16KB", "64KB"))
        bench.run(self.workspace, definition)
        self.assertEqual(CountingBackend.build_calls, 1)


class BenchDeviceReservation(BenchTestCase):
    """The bench reserves the device through the runtime and says so while it waits."""

    def hold_device(self, seconds: float, target: str = "hw") -> None:
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

    def test_the_device_is_reserved_under_the_lock_name_older_processes_use(self):
        bench.run(self.workspace, self.plan("scan", sizes=("4KB",)))
        self.assertTrue((self.workspace.locks_dir / "fake-hw-0.lock").is_file())

    def test_the_bench_is_the_holder_while_it_measures(self):
        definition = self.plan("scan", sizes=("4KB",))
        manifest = self.workspace.load("copier")
        real = load_callable(manifest.adapter_directory, "adapter.py", "execute",
                             "operation_identity")
        seen = []

        def watching(session, data, parameters):
            seen.extend(DeviceManager(self.workspace).holders())
            return real(session, data, parameters)

        with mock.patch("isc.bench.load_callable", return_value=watching):
            bench.run(self.workspace, definition)

        run_id = json.loads(self.workspace.records_path.read_text().splitlines()[-1])["run_id"]
        self.assertTrue(seen)
        self.assertEqual({(holder.run_id, holder.purpose) for holder in seen}, {(run_id, "bench")})

    def test_the_wait_is_reported_and_kept_out_of_the_samples(self):
        self.hold_device(0.5)
        messages: list[str] = []
        result = bench.run(self.workspace, self.plan("scan", sizes=("4KB",)),
                           report=messages.append)
        self.assertTrue(any("waiting for device fake-hw-0" in message and "holder-run" in message
                            for message in messages), messages)
        self.assertGreaterEqual(result.modules["copier"]["device_wait_ms"], 300)
        for sample in result.cells[0].device_samples_ms:
            self.assertLess(sample["total_ms"], 100)

    def test_a_device_time_limit_fails_the_module_without_opening_the_device(self):
        self.hold_device(0.8)
        result = bench.run(self.workspace, self.plan("scan", sizes=("4KB",)), device_timeout_s=0.2)
        self.assertIn("holder-run", result.modules["copier"]["error"])
        self.assertTrue(all(cell.error for cell in result.cells))
        self.assertEqual(CountingBackend.open_calls, 0)

    def test_the_device_is_identified_in_the_summary(self):
        result = bench.run(self.workspace, self.plan("scan", sizes=("4KB",)))
        self.assertEqual(result.modules["copier"]["device"]["id"], "fake:index0")

    def test_the_command_line_takes_a_device_time_limit(self):
        self.plan("scan", sizes=("4KB",))
        self.hold_device(0.8)
        code, out, err = self._cli("bench", "scan", "--device-timeout", "0.3")
        self.assertEqual(code, 1)
        self.assertIn("waiting for device fake-hw-0", out)
        self.assertIn("holder-run", err)


def stage_flow(events: list[dict]) -> list[tuple[str, str]]:
    return [(event["stage"], event["state"]) for event in events if event["type"] == "stage"]


class BenchEvents(BenchTestCase):
    """What a bench tells anyone following it: its stages, and how many cells are done."""

    def run_bench(self, definition, **options):
        sink = MemorySink()
        result = bench.run(self.workspace, definition, events=sink, **options)
        return result, sink.events

    def hold_device(self, seconds: float, target: str = "hw") -> None:
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

    def test_a_bench_reports_its_stages_in_order(self):
        definition = self.plan("scan", sizes=("4KB", "16KB"), repeat=2, warmup=0)
        result, events = self.run_bench(definition)
        self.assertEqual(stage_flow(events), [
            ("prepare", "running"), ("prepare", "succeeded"),
            ("build", "running"), ("build", "succeeded"),
            ("device_wait", "running"), ("device_wait", "succeeded"),
            ("device_open", "running"), ("device_open", "succeeded"),
            ("measure", "running"), ("measure", "succeeded"),
            ("baseline", "running"), ("baseline", "succeeded"),
            ("persist", "running"), ("persist", "succeeded"),
        ])
        first, last = events[0], events[-1]
        self.assertEqual((first["type"], first["state"], first["kind"], first["module"], first["target"]),
                         ("run", "started", "bench", "copier", "hw"))
        self.assertEqual((last["type"], last["state"], last["status"]), ("run", "finished", "succeeded"))

    def test_the_measure_stage_counts_the_cells_of_the_whole_matrix(self):
        definition = self.plan("scan", modules=("copier", "second"), sizes=("4KB", "16KB"),
                               repeat=2, warmup=0)
        _, events = self.run_bench(definition)
        cells = [e for e in events if e["type"] == "progress" and e["stage"] == "measure"]
        self.assertEqual([(e["step"], e["current"], e["total"], e["unit"]) for e in cells],
                         [("cells", number, 4, "cells") for number in (1, 2, 3, 4)])
        self.assertIn("copier", cells[0]["message"])
        self.assertIn("second", cells[2]["message"])

    def test_the_run_id_is_known_from_the_start_and_is_the_one_recorded(self):
        result = bench.run(self.workspace, self.plan("scan", sizes=("4KB",)))
        run_id = json.loads(self.workspace.records_path.read_text().splitlines()[-1])["run_id"]
        path = self.workspace.events_dir / f"{run_id}.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual((lines[0]["kind"], lines[0]["module"], lines[-1]["status"]),
                         ("bench", "copier", "succeeded"))

    def test_a_module_that_cannot_be_built_fails_its_build_stage_and_the_run(self):
        definition = self.plan("scan", sizes=("4KB", "16KB"))
        with mock.patch("isc.bench.build", side_effect=backends.BackendError("no toolchain")):
            _, events = self.run_bench(definition)
        flow = stage_flow(events)
        self.assertIn(("build", "failed"), flow)
        self.assertNotIn(("device_wait", "running"), flow)
        self.assertEqual((events[-1]["status"],), ("failed",))
        self.assertIn("2 of 2 cells failed", events[-1]["error"])
        self.assertIn("no toolchain", events[-1]["error"])

    def test_a_missing_module_still_moves_the_cell_count_to_its_total(self):
        definition = self.plan("scan", modules=("copier", "second"), sizes=("4KB",), repeat=2, warmup=0)
        original = self.workspace.load

        def load(module_id):
            if module_id == "copier":
                raise LookupError("unknown module 'copier'")
            return original(module_id)

        with mock.patch.object(self.workspace, "load", side_effect=load):
            _, events = self.run_bench(definition)
        cells = [e for e in events if e["type"] == "progress" and e["stage"] == "measure"]
        self.assertEqual([(e["current"], e["total"]) for e in cells], [(2, 2)])
        self.assertEqual(events[-1]["status"], "failed")

    def test_a_device_time_limit_fails_the_wait_stage(self):
        self.hold_device(0.8)
        _, events = self.run_bench(self.plan("scan", sizes=("4KB",)), device_timeout_s=0.2)
        flow = stage_flow(events)
        self.assertIn(("device_wait", "failed"), flow)
        self.assertNotIn(("measure", "running"), flow)
        self.assertEqual(events[-1]["status"], "failed")

    def test_an_interrupt_is_written_as_cancelled_and_still_raised(self):
        definition = self.plan("scan", sizes=("4KB",))

        def interrupted(session, data, parameters):
            raise KeyboardInterrupt

        sink = MemorySink()
        with mock.patch("isc.bench.load_callable", return_value=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                bench.run(self.workspace, definition, events=sink)
        last = sink.events[-1]
        self.assertEqual((last["type"], last["state"], last["status"]), ("run", "finished", "cancelled"))
        self.assertEqual(stage_flow(sink.events)[-1], ("measure", "failed"))


class BenchProgressFlag(BenchTestCase):
    def test_bench_prints_stages_and_cell_progress_to_stderr_with_the_flag(self):
        self.plan("scan", sizes=("4KB",), repeat=2, warmup=0)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli_main(["--workspace", str(self.workspace.root), "bench", "scan", "--progress"])
        self.assertEqual(code, 0)
        for line in ("  prepare: running", "  measure: cells 1/1", "  persist: done in", "finished: succeeded"):
            self.assertIn(line, err.getvalue())
        self.assertNotIn("prepare: running", out.getvalue())


class HookBench(BenchTestCase):
    def test_a_hook_module_is_measured_even_without_a_cpu_baseline(self):
        create_hook_module(self.workspace, "clip", profile_id="filter")
        definition = self.plan("hooks", modules=("clip",), sizes=("4KB",), repeat=2, warmup=1)
        result = bench.run(self.workspace, definition)

        cell = result.cells[0]
        self.assertEqual(cell.error, "")
        self.assertEqual(len(cell.device_samples_ms), 2)
        self.assertFalse(cell.baseline["available"])


# --------------------------------------------------------------------------
# 판정
# --------------------------------------------------------------------------
class Verdicts(unittest.TestCase):
    def _analyse(self, cells, *, target="hw", performance_data=True,
                 modules=None) -> report.Analysis:
        payload = {
            "kind": "bench",
            "experiment": {"id": "scan", "name": "scan", "repeat": 3, "warmup": 1,
                           "input_fill": "text", "performance_data": performance_data},
            "target": target,
            "started_at": "2026-09-03T00:00:00+0900",
            "modules": modules or {"m": {"operation": "op", "device_open_ms": 1200.0}},
            "cells": cells,
        }
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "bench.json"
            path.write_text(json.dumps(payload))
            return report.analyse(path)

    def _cell(self, size, *, cpu_ms, kernel_ms, h2d_ms, d2h_ms, module="m",
              operation="op", verified=True):
        total = kernel_ms + h2d_ms + d2h_ms
        return {
            "module": module, "operation": operation, "input_bytes": size,
            "output_bytes": size,
            "device_samples_ms": [{
                "total_ms": total, "kernel_ms": kernel_ms,
                "host_to_device_ms": h2d_ms, "device_to_host_ms": d2h_ms,
            }] * 3,
            "baseline": {"available": True, "samples_ms": [cpu_ms] * 3},
            "verification": {"mode": "cpu_baseline", "passed": verified,
                             "detail": "CPU 기준선과 일치"},
        }

    def test_uniform_speedup_is_accelerated(self):
        analysis = self._analyse([
            self._cell(1 << 20, cpu_ms=100, kernel_ms=10, h2d_ms=5, d2h_ms=1),
            self._cell(1 << 24, cpu_ms=1600, kernel_ms=160, h2d_ms=80, d2h_ms=16),
        ])
        verdict = analysis.verdicts[0]
        self.assertEqual(verdict.verdict, "accelerated")
        self.assertIn("상시 오프로드", verdict.recommendation)

    def test_transfer_cost_alone_beating_the_cpu_is_a_hard_no(self):
        """상한 가속비가 1 미만이면 커널을 아무리 고쳐도 이득이 없다.
        이 판정이 "가속 대상이 아니다"를 추측이 아니라 단정으로 만든다."""
        analysis = self._analyse([
            self._cell(1 << 20, cpu_ms=2.0, kernel_ms=1.0, h2d_ms=8.0, d2h_ms=0.01),
            self._cell(1 << 24, cpu_ms=30.0, kernel_ms=16.0, h2d_ms=128.0, d2h_ms=0.01),
        ])
        verdict = analysis.verdicts[0]
        self.assertEqual(verdict.verdict, "transfer_bound")
        self.assertLess(verdict.best_ceiling, 1.0)
        self.assertIn("제외", verdict.recommendation)

    def test_slow_now_but_transfer_is_cheap_points_at_the_kernel(self):
        analysis = self._analyse([
            self._cell(1 << 20, cpu_ms=100, kernel_ms=400, h2d_ms=8, d2h_ms=1),
        ])
        verdict = analysis.verdicts[0]
        self.assertEqual(verdict.verdict, "kernel_bound")
        self.assertGreater(verdict.best_ceiling, 1.2)
        self.assertIn("오프로드하지 마십시오", verdict.recommendation)

    def test_crossover_is_reported_when_only_large_inputs_win(self):
        analysis = self._analyse([
            self._cell(1 << 12, cpu_ms=0.5, kernel_ms=0.4, h2d_ms=1.0, d2h_ms=0.1),
            self._cell(1 << 20, cpu_ms=120.0, kernel_ms=40.0, h2d_ms=10.0, d2h_ms=1.0),
        ])
        verdict = analysis.verdicts[0]
        self.assertEqual(verdict.verdict, "conditional")
        self.assertIsNotNone(verdict.crossover_bytes)
        self.assertLessEqual(verdict.crossover_bytes, 1 << 20)
        self.assertGreaterEqual(verdict.crossover_bytes, 1 << 12)

    def test_a_correctness_failure_blocks_any_performance_verdict(self):
        analysis = self._analyse([
            self._cell(1 << 20, cpu_ms=100, kernel_ms=1, h2d_ms=1, d2h_ms=1,
                       verified=False),
        ])
        self.assertEqual(analysis.verdicts[0].verdict, "inconclusive")
        self.assertIn("정확성", analysis.verdicts[0].reason)

    def test_missing_baseline_is_inconclusive_not_a_win(self):
        cell = self._cell(1 << 20, cpu_ms=1, kernel_ms=1, h2d_ms=1, d2h_ms=1)
        cell["baseline"] = {"available": False, "reason": "lz4 패키지가 없습니다"}
        cell["verification"] = {"mode": "none", "detail": "-"}
        analysis = self._analyse([cell])
        self.assertEqual(analysis.verdicts[0].verdict, "inconclusive")
        self.assertIn("lz4", analysis.verdicts[0].reason)

    def test_phase_timers_exceeding_the_wall_clock_are_flagged(self):
        """단계 타이머의 합이 실행 시간을 넘으면 계측이 어긋난 것이다.
        그런 값을 비율로 내보내면 리포트가 스스로를 불신하게 만든다."""
        cell = self._cell(1 << 20, cpu_ms=10.0, kernel_ms=1.0, h2d_ms=1.0, d2h_ms=1.0)
        for sample in cell["device_samples_ms"]:
            sample["total_ms"] = 0.5  # 합(3.0)보다 작은 벽시계
        analysis = self._analyse([cell])
        self.assertTrue(any("단계" in warning for warning in analysis.warnings))
        self.assertLessEqual(analysis.cells[0].transfer_share, 1.0)

    def test_large_values_do_not_run_columns_together(self):
        analysis = self._analyse([
            self._cell(1 << 30, cpu_ms=9000.0, kernel_ms=1200.0,
                       h2d_ms=8388.6, d2h_ms=16777.2),
        ])
        for line in report.render(analysis).splitlines():
            self.assertNotRegex(line, r"\d\.\d+\d*\.\d",
                                f"columns ran together: {line!r}")

    def test_emulation_results_carry_a_warning(self):
        analysis = self._analyse(
            [self._cell(1 << 20, cpu_ms=100, kernel_ms=10, h2d_ms=5, d2h_ms=1)],
            target="hw_emu", performance_data=False,
        )
        self.assertTrue(any("에뮬레이션" in warning for warning in analysis.warnings))

    def test_render_shows_the_ceiling_column_and_the_verdict(self):
        analysis = self._analyse([
            self._cell(1 << 20, cpu_ms=2.0, kernel_ms=1.0, h2d_ms=8.0, d2h_ms=0.01),
        ])
        text = report.render(analysis)
        self.assertIn("CEILING", text)
        self.assertIn("가속 불가", text)

    def test_csv_has_a_row_per_cell(self):
        analysis = self._analyse([
            self._cell(1 << 12, cpu_ms=1, kernel_ms=1, h2d_ms=1, d2h_ms=1),
            self._cell(1 << 20, cpu_ms=1, kernel_ms=1, h2d_ms=1, d2h_ms=1),
        ])
        self.assertEqual(len(report.to_csv(analysis).strip().splitlines()), 3)


# --------------------------------------------------------------------------
# CPU 기준선을 정확성 참조로 쓰는 검증 모드
# --------------------------------------------------------------------------
class CpuBaselineValidation(BenchTestCase):
    def test_hw_run_is_checked_against_the_cpu_baseline_without_an_emulation_run(self):
        create_module(self.workspace, "checker", operation_id="identity")
        source = Path(self._temp.name) / "in.bin"
        source.write_bytes(b"payload" * 100)

        with _validation_mode("identity", "cpu_baseline"):
            with mock.patch("isc.lifecycle.subprocess.run",
                            side_effect=AssertionError("no reference run may be spawned")):
                result = run_module(self.workspace, "checker", source, "hw")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.validation["mode"], "cpu_baseline")
        self.assertTrue(result.validation["passed"])

    def test_a_mismatch_against_the_cpu_baseline_fails_the_run(self):
        create_module(self.workspace, "checker", operation_id="identity")
        source = Path(self._temp.name) / "in.bin"
        source.write_bytes(b"payload")

        def corrupt(session, data, parameters):
            return data + b"!"

        with _validation_mode("identity", "cpu_baseline"):
            with mock.patch("isc.lifecycle.load_callable", return_value=corrupt):
                result = run_module(self.workspace, "checker", source, "hw")
        self.assertFalse(result.ok)
        self.assertIn("size mismatch", result.validation["detail"])

    def test_an_unavailable_baseline_stops_the_run_before_the_build(self):
        """기준선이 없다는 사실은 빌드 전에도 알 수 있다. 수십 분짜리 hw 빌드를 하고 나서
        검증 실패로 끝나면 안 된다."""
        create_module(self.workspace, "checker", operation_id="identity")
        source = Path(self._temp.name) / "in.bin"
        source.write_bytes(b"payload")

        with _validation_mode("identity", "cpu_baseline"):
            with mock.patch("isc.lifecycle.compute_baseline",
                            side_effect=baseline.BaselineUnavailable("package missing")):
                result = run_module(self.workspace, "checker", source, "hw")

        self.assertFalse(result.ok)
        self.assertEqual(CountingBackend.build_calls, 0)
        self.assertIn("CPU baseline unavailable: package missing", result.validation["detail"])
        self.assertEqual(result.output_path, "")

    def test_cpu_baseline_mode_needs_an_exactly_equivalent_baseline(self):
        create_module(self.workspace, "squeezer", operation_id="lz4")
        with _validation_mode("lz4", "cpu_baseline"):
            report_ = check_module(self.workspace.modules_dir / "squeezer")
        self.assertFalse(report_.passed)
        self.assertTrue(any("equivalence" in problem for problem in report_.problems))


def _with_validation(operation_id: str, mode: str):
    operation = get_operation(operation_id)
    from dataclasses import replace
    return replace(operation, raw={**operation.raw, "validation": {"mode": mode}})


def _validation_mode(operation_id: str, mode: str):
    """해당 연산만 검증 모드를 바꿔서 로드되게 한다."""
    real = get_operation

    def patched(requested_id: str):
        if requested_id == operation_id:
            return _with_validation(operation_id, mode)
        return real(requested_id)

    return mock.patch("isc.manifest.get_operation", side_effect=patched)


# --------------------------------------------------------------------------
# 명령줄
# --------------------------------------------------------------------------
class BenchCommandLine(BenchTestCase):
    def _cli(self, *argv) -> tuple[int, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            code = cli_main(["--workspace", str(self.workspace.root), *argv])
        return code, output.getvalue()

    def test_plan_bench_report_round_trip(self):
        create_module(self.workspace, "copier", operation_id="identity")

        code, out = self._cli("plan", "scan", "--modules", "copier",
                              "--sizes", "4KB,16KB", "--repeat", "3", "--warmup", "1")
        self.assertEqual(code, 0)
        self.assertIn("isc bench scan", out)

        code, out = self._cli("experiments")
        self.assertEqual(code, 0)
        self.assertIn("scan", out)

        code, out = self._cli("bench", "scan", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("2 cells", out)

        code, out = self._cli("bench", "scan")
        self.assertEqual(code, 0, out)
        self.assertIn("isc report scan", out)

        code, out = self._cli("report", "scan")
        self.assertEqual(code, 0)
        self.assertIn("CEILING", out)
        self.assertIn("판정", out)

    def test_describe_says_whether_a_cpu_baseline_exists(self):
        """실험을 계획하기 전에 알아야 한다 -- 기준선이 없으면 speedup 이 안 나온다."""
        _, out = self._cli("describe", "sha256")
        self.assertIn("correctness comparable", out)
        _, out = self._cli("describe", "lz4")
        self.assertIn("performance comparison only", out)
        self.assertIn("needs `lz4`", out)

    def test_report_without_a_measurement_says_what_to_do(self):
        self.plan("scan")
        with mock.patch("sys.stderr", new_callable=io.StringIO) as error:
            code = cli_main(["--workspace", str(self.workspace.root), "report", "scan"])
        self.assertEqual(code, 2)
        self.assertIn("isc bench scan", error.getvalue())


if __name__ == "__main__":
    unittest.main()
