"""vector_topk: the byte format, the kernel, the adapter, the CPU baseline and the input generator.

Everything here runs without a card and without Vitis. The kernel's C++ is compiled with g++
when the Vitis HLS headers are available (ISC_HLS_INCLUDE) and skipped otherwise.
"""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from isc import backends, baseline, loader, record
from isc.backends.base import Backend, Buffer, BuildOutput, BuildRequest, DeviceInfo, Session
from isc.build import fingerprint
from isc.check import check_module
from isc.cli import main as cli_main
from isc.lifecycle import run as run_module
from isc.loader import load_callable, load_file
from isc.manifest import KernelArgument, Manifest
from isc.operations import OPERATIONS_DIR, OperationError, get_operation
from isc.scaffold import create_module
from isc.workspace import Workspace

OPERATION_DIR = OPERATIONS_DIR / "vector_topk"
topk_format = load_file(OPERATION_DIR, "topk_format.py", "test_vector_topk_format")


def signed_bytes(values) -> bytes:
    return bytes(value & 0xFF for value in values)


def make_input(query, rows) -> bytes:
    """An input file: header, query, then the database row by row."""
    return (struct.pack("<II", len(rows), len(query)) + signed_bytes(query)
            + b"".join(signed_bytes(row) for row in rows))


def reference(query, rows, k):
    """The answer written the obvious way: score every row, sort by (score descending, id)."""
    scored = [(index, sum(a * b for a, b in zip(query, row))) for index, row in enumerate(rows)]
    scored.sort(key=lambda entry: (-entry[1], entry[0]))
    return scored[:k]


def expected_output(query, rows, k) -> bytes:
    return topk_format.pack(reference(query, rows, k), k)


class FormatBytes(unittest.TestCase):
    def test_the_documented_example_is_exactly_these_bytes(self):
        packed = topk_format.pack([(7, 300), (2, -5)], 3)
        self.assertEqual(packed, bytes.fromhex("07000000" "2c010000"      # id 7, score 300
                                               "02000000" "fbffffff"      # id 2, score -5
                                               "ffffffff" "00000080"))    # an empty slot

    def test_order_is_score_descending_then_id_ascending(self):
        self.assertEqual(topk_format.order([(5, 10), (1, 10), (9, 11), (0, -3)]),
                         [(9, 11), (1, 10), (5, 10), (0, -3)])

    def test_pack_keeps_only_k_and_pads_the_rest(self):
        self.assertEqual(len(topk_format.pack([(i, i) for i in range(5)], 2)), 16)
        self.assertEqual(topk_format.decode(topk_format.pack([(i, i) for i in range(5)], 2)),
                         [(4, 4), (3, 3)])
        self.assertEqual(topk_format.pack([], 2), bytes.fromhex("ffffffff00000080") * 2)

    def test_decode_drops_empty_slots_and_round_trips(self):
        entries = [(3, 2_000_000), (8, 2_000_000), (1, -9)]
        self.assertEqual(topk_format.decode(topk_format.pack(entries, 5)), entries)

    def test_decode_refuses_a_truncated_entry(self):
        with self.assertRaisesRegex(ValueError, "multiple of 8"):
            topk_format.decode(b"\x00" * 9)

    def test_a_well_formed_input_gives_rows_and_dim(self):
        self.assertEqual(topk_format.check(make_input([1, 2], [[3, 4], [5, 6]])), (2, 2))
        self.assertEqual(topk_format.check(make_input([1, 2, 3], [])), (0, 3))

    def test_a_malformed_input_says_what_is_wrong(self):
        good = make_input([1, 2], [[3, 4], [5, 6]])
        cases = [
            (b"", "needs at least 9 bytes"),
            (good[:8], "needs at least 9 bytes"),
            (struct.pack("<II", 1, 0) + b"\x01", r"dim must be 1\.\.2048, the header says 0"),
            (struct.pack("<II", 1, 2049) + bytes(2049 * 2), r"dim must be 1\.\.2048, the header says 2049"),
            (good + b"\x00", "header says rows=2, dim=2, which is 14 bytes; received 15"),
            (good[:-1], "header says rows=2, dim=2, which is 14 bytes; received 13"),
        ]
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    topk_format.check(data)

    def test_dim_is_rounded_up_to_the_lane_width(self):
        self.assertEqual([topk_format.padded_dim(d) for d in (1, 16, 17, 128, 1536, 2048)],
                         [16, 16, 32, 128, 1536, 2048])

    def test_rows_are_padded_one_by_one_with_zeros(self):
        block = bytes([1, 2, 3, 4, 5, 6])
        padded = topk_format.pad_rows(block, 2, 3, 16)
        self.assertEqual(padded, bytes([1, 2, 3]) + bytes(13) + bytes([4, 5, 6]) + bytes(13))
        self.assertEqual(topk_format.pad_rows(block, 2, 3, 3), block)


KERNEL_SOURCE = OPERATION_DIR / "kernel" / "vector_topk_kernel.cpp"

DRIVER = """#include "{kernel}"

#include <cstdio>
#include <cstdlib>
#include <vector>

static std::vector<unsigned char> slurp(const char* path) {{
    std::vector<unsigned char> bytes;
    FILE* file = fopen(path, "rb");
    if (!file) return bytes;
    unsigned char chunk[4096];
    size_t got;
    while ((got = fread(chunk, 1, sizeof chunk, file)) > 0) bytes.insert(bytes.end(), chunk, chunk + got);
    fclose(file);
    return bytes;
}}

static std::vector<ap_uint<128> > words(const std::vector<unsigned char>& bytes) {{
    std::vector<ap_uint<128> > packed((bytes.size() + 15) / 16 + 1);
    for (size_t i = 0; i < bytes.size(); ++i)
        packed[i / 16].range(8 * (i % 16) + 7, 8 * (i % 16)) = bytes[i];
    return packed;
}}

// driver query.bin db.bin out.bin rows dim k id_base
int main(int argc, char** argv) {{
    if (argc != 8) return 2;
    std::vector<ap_uint<128> > query = words(slurp(argv[1]));
    std::vector<ap_uint<128> > db = words(slurp(argv[2]));
    std::vector<ap_uint<64> > out(16);
    const unsigned k = (unsigned)strtoul(argv[6], 0, 10);
    isc_vector_topk(query.data(), db.data(), out.data(), (uint32_t)strtoul(argv[4], 0, 10),
                    (uint32_t)strtoul(argv[5], 0, 10), k, (uint32_t)strtoul(argv[7], 0, 10));
    FILE* file = fopen(argv[3], "wb");
    for (unsigned i = 0; i < k; ++i)
        for (int b = 0; b < 8; ++b) fputc((unsigned char)out[i].range(8 * b + 7, 8 * b), file);
    fclose(file);
    return 0;
}}
"""


def hls_include() -> Path | None:
    candidates = [os.environ.get("ISC_HLS_INCLUDE")]
    for variable in ("XILINX_HLS", "XILINX_VITIS"):
        root = os.environ.get(variable)
        if root:
            candidates.append(str(Path(root) / "include"))
    for candidate in candidates:
        if candidate and (Path(candidate) / "ap_int.h").is_file():
            return Path(candidate)
    return None


NEEDS_COMPILER = unittest.skipUnless(
    hls_include() and shutil.which("g++"),
    "needs g++ and the Vitis HLS headers (set ISC_HLS_INCLUDE to the directory with ap_int.h)",
)


def compile_driver(work: Path) -> Path:
    (work / "driver.cpp").write_text(DRIVER.format(kernel=KERNEL_SOURCE))
    built = subprocess.run(
        ["g++", "-std=c++14", "-O1", f"-I{hls_include()}", str(work / "driver.cpp"),
         "-o", str(work / "driver")],
        capture_output=True, text=True,
    )
    assert built.returncode == 0, built.stderr[-3000:]
    return work / "driver"


def run_driver(binary: Path, work: Path, query: bytes, db: bytes, rows: int, stride: int,
               k: int, id_base: int) -> bytes:
    (work / "query.bin").write_bytes(query)
    (work / "db.bin").write_bytes(db)
    done = subprocess.run(
        [str(binary), str(work / "query.bin"), str(work / "db.bin"), str(work / "out.bin"),
         str(rows), str(stride), str(k), str(id_base)],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    return (work / "out.bin").read_bytes()


@NEEDS_COMPILER
class CompiledKernel(unittest.TestCase):
    """The kernel's C++ itself, called once the way the adapter calls it."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._temp.name)
        cls.binary = compile_driver(cls.work)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def call(self, query, rows, k, id_base=0) -> bytes:
        stride = topk_format.padded_dim(len(query))
        pad = lambda values: signed_bytes(values) + bytes(stride - len(values))
        return run_driver(self.binary, self.work, pad(query), b"".join(pad(row) for row in rows),
                          len(rows), stride, k, id_base)

    def expect(self, query, rows, k, id_base=0):
        wanted = [(entry_id + id_base, score) for entry_id, score in reference(query, rows, k)]
        self.assertEqual(self.call(query, rows, k, id_base), topk_format.pack(wanted, k))

    def test_a_small_example_worked_by_hand(self):
        # scores: 1, 15, 18
        self.assertEqual(
            topk_format.decode(self.call([1, 2, 3], [[1, 0, 0], [0, 0, 5], [3, 3, 3]], 2)),
            [(2, 18), (1, 15)])

    def test_the_full_int8_range_cannot_overflow_a_score(self):
        query = [-128] * 2048
        rows = [[-128] * 2048, [127] * 2048]
        self.expect(query, rows, 2)
        self.assertEqual(topk_format.decode(self.call(query, rows, 2)),
                         [(0, 33_554_432), (1, -33_292_288)])

    def test_equal_scores_keep_the_lower_id_first(self):
        rows = [[2, 2]] * 6
        self.assertEqual([i for i, _ in topk_format.decode(self.call([1, 1], rows, 4))], [0, 1, 2, 3])

    def test_a_row_that_only_ties_the_kth_best_does_not_displace_it(self):
        self.assertEqual([i for i, _ in topk_format.decode(self.call([1], [[5], [5], [5]], 2))], [0, 1])

    def test_fewer_rows_than_k_leave_empty_slots(self):
        output = self.call([1, 1], [[1, 1], [2, 2]], 5)
        self.assertEqual(topk_format.decode(output), [(1, 4), (0, 2)])
        self.assertEqual(output, expected_output([1, 1], [[1, 1], [2, 2]], 5))

    def test_no_rows_at_all(self):
        self.assertEqual(self.call([3, 4], [], 3), topk_format.pack([], 3))

    def test_ids_are_offset_by_the_first_row_of_the_call(self):
        self.expect([1, 2], [[1, 1], [3, 3], [2, 2]], 3, id_base=1000)

    def test_every_k_from_1_to_16(self):
        rng = random.Random(3)
        query = [rng.randint(-5, 5) for _ in range(8)]
        rows = [[rng.randint(-5, 5) for _ in range(8)] for _ in range(40)]
        for k in range(1, 17):
            with self.subTest(k=k):
                self.expect(query, rows, k)

    def test_random_inputs_agree_with_the_reference(self):
        rng = random.Random(7)
        for trial in range(120):
            dim = rng.choice([1, 5, 16, 17, 100, 128])
            span = rng.choice([1, 3, 127])
            low = -128 if span == 127 and rng.random() < 0.3 else -span   # many ties when span is small
            query = [rng.randint(low, span) for _ in range(dim)]
            rows = [[rng.randint(low, span) for _ in range(dim)] for _ in range(rng.randint(0, 40))]
            with self.subTest(trial=trial, dim=dim, rows=len(rows)):
                self.expect(query, rows, rng.randint(1, 16), rng.choice([0, 1000]))


class Bytes(Buffer):
    """A device buffer that is only host memory."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.data = bytearray(size)

    def write(self, data: bytes, offset: int = 0) -> None:
        self.data[offset:offset + len(data)] = data

    def read(self, size: int | None = None, offset: int = 0) -> bytes:
        length = self.size if size is None else size
        return bytes(self.data[offset:offset + length])


def kernel_arguments() -> tuple[KernelArgument, ...]:
    """The kernel arguments exactly as operation.yaml declares them."""
    return tuple(KernelArgument(name=item["name"], kind=item.get("kind", "buffer"),
                                direction=item.get("direction", "in"))
                 for item in get_operation("vector_topk").raw["kernel"]["arguments"])


def kernel_model(query: bytes, db: bytes, rows: int, stride: int, k: int, id_base: int) -> bytes:
    """What the kernel does, written the way the kernel does it: k sorted slots and an insertion
    before the first slot the row strictly beats. Deliberately not the sort() of `reference`."""
    best = [(topk_format.NO_ID, topk_format.NO_SCORE)] * k
    query_values = struct.unpack(f"{stride}b", query[:stride])
    for r in range(rows):
        row = struct.unpack(f"{stride}b", db[r * stride:(r + 1) * stride])
        score = sum(a * b for a, b in zip(query_values, row))
        for slot in range(k):
            if score > best[slot][1]:
                best.insert(slot, (id_base + r, score))
                best.pop()
                break
    return b"".join(topk_format.ENTRY.pack(entry_id, score) for entry_id, score in best)


class ModelSession(Session):
    """A session whose kernel is the Python model above."""

    def __init__(self, arguments=None) -> None:
        super().__init__(DeviceInfo(backend="model", index=0, name="model-device"),
                         kernel_arguments() if arguments is None else arguments)
        self.runs = 0

    def buffer(self, size: int, arg: int) -> Buffer:
        self.check_buffer_argument(arg)
        return Bytes(size)

    def run(self, *args) -> None:
        self.check_run_arity(len(args))
        query, db, out, rows, stride, k, id_base = args
        self.runs += 1
        out.write(self.kernel(query, db, rows, stride, k, id_base))

    def kernel(self, query, db, rows, stride, k, id_base) -> bytes:
        return kernel_model(bytes(query.data), bytes(db.data), rows, stride, k, id_base)


class CSimSession(ModelSession):
    """The compiled kernel C++ behind the same interface."""
    binary: Path
    work: Path

    def kernel(self, query, db, rows, stride, k, id_base) -> bytes:
        return run_driver(self.binary, self.work, bytes(query.data[:stride]), bytes(db.data),
                          rows, stride, k, id_base)


def adapter_execute():
    return load_callable(OPERATION_DIR, "adapter.py", "execute", "test_vector_topk")


class AdapterCases:
    """What the adapter must do whichever kernel stands behind the session."""

    def make_session(self) -> ModelSession:
        raise NotImplementedError

    def execute(self, query, rows, **parameters):
        session = self.make_session()
        settings = {"k": 10, "chunk_bytes": 16 * 1024 * 1024, **parameters}
        return adapter_execute()(session, make_input(query, rows), settings), session

    def assert_matches(self, query, rows, **parameters):
        output, session = self.execute(query, rows, **parameters)
        self.assertEqual(output, expected_output(query, rows, parameters.get("k", 10)))
        return session

    def test_a_small_example(self):
        output, _ = self.execute([1, 2, 3], [[1, 0, 0], [0, 0, 5], [3, 3, 3]], k=2)
        self.assertEqual(topk_format.decode(output), [(2, 18), (1, 15)])

    def test_random_shapes_agree_with_the_reference(self):
        rng = random.Random(11)
        # (dim, rows, k, chunk_bytes): many chunks, uneven last chunk, padded dims, the largest dim
        for dim, rows, k, chunk in [(1, 1, 1, 4096), (3, 10, 4, 4096), (16, 100, 10, 160),
                                    (17, 64, 16, 4096), (100, 57, 5, 400), (128, 200, 10, 4096),
                                    (2048, 6, 3, 4096)]:
            query = [rng.randint(-128, 127) for _ in range(dim)]
            db = [[rng.randint(-128, 127) for _ in range(dim)] for _ in range(rows)]
            with self.subTest(dim=dim, rows=rows, k=k, chunk_bytes=chunk):
                self.assert_matches(query, db, k=k, chunk_bytes=chunk)

    def test_equal_scores_across_chunk_boundaries_keep_the_lower_id(self):
        session = self.assert_matches([1, 1, 1], [[1, 2, 3]] * 25, k=5, chunk_bytes=16 * 4)
        self.assertEqual(session.metrics["chunks"], 7)          # 4 rows per call, 25 rows

    def test_a_call_per_row_still_agrees(self):
        session = self.assert_matches([2, -1], [[1, 1], [3, 0], [-2, 5], [3, 0]] * 3, k=4, chunk_bytes=1)
        self.assertEqual(session.metrics["chunks"], 12)

    def test_values_at_the_ends_of_the_int8_range(self):
        rng = random.Random(5)
        pick = lambda: rng.choice([-128, -1, 0, 1, 127])
        self.assert_matches([pick() for _ in range(33)], [[pick() for _ in range(33)] for _ in range(80)],
                            k=16, chunk_bytes=700)

    def test_a_dim_that_is_not_a_multiple_of_the_lane_width(self):
        rng = random.Random(9)
        for dim in (1, 5, 17, 31, 47):
            query = [rng.randint(-9, 9) for _ in range(dim)]
            db = [[rng.randint(-9, 9) for _ in range(dim)] for _ in range(30)]
            with self.subTest(dim=dim):
                self.assert_matches(query, db, k=6, chunk_bytes=4096)

    def test_fewer_rows_than_k_leave_empty_slots(self):
        output, _ = self.execute([1, 1], [[1, 1], [2, 2], [-1, -1]], k=10)
        self.assertEqual(len(output), 80)
        self.assertEqual(topk_format.decode(output), [(1, 4), (0, 2), (2, -2)])

    def test_no_rows_means_no_kernel_call(self):
        output, session = self.execute([1, 2, 3], [], k=4)
        self.assertEqual(output, topk_format.pack([], 4))
        self.assertEqual(session.runs, 0)

    def test_the_run_is_described_in_its_metrics(self):
        session = self.assert_matches([1] * 16, [[1] * 16] * 100, k=10, chunk_bytes=160)
        self.assertEqual({name: session.metrics[name] for name in ("rows", "dim", "k", "chunks")},
                         {"rows": 100.0, "dim": 16.0, "k": 10.0, "chunks": 10.0})

    def test_a_malformed_input_never_reaches_the_kernel(self):
        session = self.make_session()
        with self.assertRaisesRegex(ValueError, "needs at least 9 bytes"):
            adapter_execute()(session, b"short", {"k": 10, "chunk_bytes": 4096})
        with self.assertRaisesRegex(ValueError, "header says"):
            adapter_execute()(session, make_input([1, 2], [[1, 2]]) + b"\0", {"k": 10, "chunk_bytes": 4096})
        self.assertEqual(session.runs, 0)

    def test_a_k_outside_the_kernels_range_is_refused(self):
        for k in (0, 17, -1):
            with self.subTest(k=k):
                with self.assertRaisesRegex(ValueError, r"k must be 1\.\.16"):
                    adapter_execute()(self.make_session(), make_input([1], [[1]]),
                                      {"k": k, "chunk_bytes": 4096})


class AdapterWithKernelModel(AdapterCases, unittest.TestCase):
    def make_session(self) -> ModelSession:
        return ModelSession()


@NEEDS_COMPILER
class AdapterWithCompiledKernel(AdapterCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        CSimSession.work = Path(cls._temp.name)
        CSimSession.binary = compile_driver(CSimSession.work)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def make_session(self) -> ModelSession:
        return CSimSession()


class StubBackend(Backend):
    name = "stub"

    def available(self):
        return True, "stub"

    def devices(self):
        return []

    def toolchain_identity(self) -> str:
        return "stub-toolchain"

    def build(self, request):
        raise NotImplementedError

    def open(self, manifest, binary, target, device_index=None):
        raise NotImplementedError


class Catalog(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)

    def module(self, module_id: str = "rag", **parameters) -> Manifest:
        create_module(self.workspace, module_id, operation_id="vector_topk",
                      parameters=parameters or None)
        return self.workspace.load(module_id)

    def test_the_operation_describes_its_input_output_and_baseline(self):
        operation = get_operation("vector_topk")
        self.assertEqual((operation.category, operation.version), ("retrieval", "1.0"))
        self.assertEqual((operation.input_contract["format"], operation.input_contract["min_bytes"]),
                         ("isc_vectors_i8", 9))
        self.assertEqual(operation.output_contract["format"], "isc_topk_pairs")
        self.assertEqual((operation.baseline_equivalence, operation.baseline_requires), ("exact", "numpy"))

    def test_the_defaults_are_ten_results_and_sixteen_mebibyte_chunks(self):
        self.assertEqual(self.module().parameters, {"k": 10, "chunk_bytes": 16 * 1024 * 1024})

    def test_a_parameter_outside_its_range_is_refused(self):
        for bad in ({"k": 0}, {"k": 17}, {"chunk_bytes": 1000}, {"chunk_bytes": 134_217_729}):
            with self.subTest(bad=bad):
                with self.assertRaises(OperationError):
                    create_module(self.workspace, "bad", operation_id="vector_topk", parameters=bad)

    def test_the_kernel_declaration_matches_what_the_adapter_expects(self):
        manifest = self.module()
        self.assertEqual(manifest.kernel.name, "isc_vector_topk")
        self.assertEqual([(a.name, a.kind) for a in manifest.kernel.arguments], [
            ("query", "buffer"), ("db", "buffer"), ("out", "buffer"), ("rows", "scalar"),
            ("dim", "scalar"), ("k", "scalar"), ("id_base", "scalar")])
        self.assertEqual([(m.port, m.bank) for m in manifest.kernel.memory],
                         [("m_axi_gmem0", 0), ("m_axi_gmem1", 1), ("m_axi_gmem2", 2)])
        self.assertEqual(manifest.kernel.include_dirs, ())
        self.assertTrue(manifest.kernel_source.is_file())
        self.assertEqual(manifest.build.targets, ("hw_emu", "hw"))

    def test_the_default_validation_is_cross_target_against_hw_emu(self):
        manifest = self.module()
        self.assertEqual((manifest.validation_mode, manifest.validation_reference_target),
                         ("cross_target", "hw_emu"))

    def test_both_parameters_are_host_scoped_so_they_never_force_a_rebuild(self):
        operation = get_operation("vector_topk")
        self.assertEqual([operation.parameter_scope(name) for name in ("k", "chunk_bytes")], ["host", "host"])
        self.assertEqual(operation.build_parameters({"k": 5, "chunk_bytes": 4096}), {})

    def test_changing_a_host_parameter_keeps_the_fingerprint(self):
        few = self.module("few", k=3, chunk_bytes=4096)
        many = self.module("many", k=16, chunk_bytes=65536)
        self.assertEqual(fingerprint(few, StubBackend(), "hw_emu"), fingerprint(many, StubBackend(), "hw_emu"))

    def test_changing_the_kernel_source_changes_the_fingerprint(self):
        manifest = self.module()
        copy = Path(self._temp.name) / "vector_topk_copy"
        shutil.copytree(OPERATION_DIR, copy)
        kernel = copy / "kernel" / "vector_topk_kernel.cpp"
        kernel.write_text(kernel.read_text() + "\n// a changed kernel\n")
        changed = replace(manifest, operation=replace(manifest.operation, directory=copy))
        self.assertNotEqual(fingerprint(manifest, StubBackend(), "hw_emu"),
                            fingerprint(changed, StubBackend(), "hw_emu"))

    def test_describe_shows_the_formats_and_the_parameters(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(cli_main(["describe", "vector_topk"]), 0)
        for text in ("isc_vectors_i8", "isc_topk_pairs", "chunk_bytes", "k"):
            self.assertIn(text, out.getvalue())


try:
    import numpy
except ImportError:           # the baseline then reports itself unavailable; its tests are skipped
    numpy = None

NEEDS_NUMPY = unittest.skipIf(numpy is None, "numpy is not installed")


def baseline_compute(query, rows, k=10) -> bytes:
    return baseline.compute(get_operation("vector_topk"), make_input(query, rows), {"k": k})


@NEEDS_NUMPY
class CpuBaseline(unittest.TestCase):
    def assert_matches(self, query, rows, k=10):
        self.assertEqual(baseline_compute(query, rows, k), expected_output(query, rows, k))

    def test_a_small_example(self):
        output = baseline_compute([1, 2, 3], [[1, 0, 0], [0, 0, 5], [3, 3, 3]], k=2)
        self.assertEqual(topk_format.decode(output), [(2, 18), (1, 15)])

    def test_random_shapes_agree_with_the_reference(self):
        rng = random.Random(21)
        for dim, rows, k in [(1, 1, 1), (3, 10, 4), (16, 100, 10), (17, 64, 16), (100, 57, 5),
                             (2048, 6, 3)]:
            query = [rng.randint(-128, 127) for _ in range(dim)]
            db = [[rng.randint(-128, 127) for _ in range(dim)] for _ in range(rows)]
            with self.subTest(dim=dim, rows=rows, k=k):
                self.assert_matches(query, db, k)

    def test_the_largest_possible_scores_do_not_overflow(self):
        output = baseline_compute([-128] * 2048, [[-128] * 2048, [127] * 2048], k=2)
        self.assertEqual(topk_format.decode(output), [(0, 33_554_432), (1, -33_292_288)])

    def test_equal_scores_keep_the_lower_id(self):
        self.assert_matches([1, 1, 1], [[1, 2, 3]] * 25, k=5)

    def test_fewer_rows_than_k_leave_empty_slots(self):
        output = baseline_compute([1, 1], [[1, 1], [2, 2], [-1, -1]], k=10)
        self.assertEqual(len(output), 80)
        self.assertEqual(topk_format.decode(output), [(1, 4), (0, 2), (2, -2)])

    def test_no_rows(self):
        self.assertEqual(baseline_compute([1, 2, 3], [], k=4), topk_format.pack([], 4))

    def test_a_database_larger_than_one_block(self):
        rng = random.Random(3)
        db = [[rng.randint(-128, 127), rng.randint(-128, 127)] for _ in range(70_000)]
        # 637 (3*127 + -2*-128) is the highest possible score for this query. Plant it once before
        # the 65_536-row block boundary and twice after it. Random rows alone would rank entirely
        # inside the first block and never prove the second block is scored or that ties break by
        # id across the boundary. With this seed two random rows (15283, 36629) also reach 637.
        for planted in (100, 65_536, 69_999):
            db[planted] = [127, -128]
        self.assert_matches([3, -2], db, k=16)
        output = baseline_compute([3, -2], db, k=16)
        self.assertEqual(topk_format.decode(output)[:5],
                         [(100, 637), (15_283, 637), (36_629, 637), (65_536, 637), (69_999, 637)])

    def test_a_malformed_input_is_refused(self):
        with self.assertRaisesRegex(ValueError, "needs at least 9 bytes"):
            baseline.compute(get_operation("vector_topk"), b"short", {"k": 10})


class CpuBaselineWithoutNumpy(unittest.TestCase):
    def test_a_missing_numpy_makes_the_baseline_unavailable_not_wrong(self):
        with mock.patch.dict(sys.modules, {"numpy": None}):
            with self.assertRaisesRegex(baseline.BaselineUnavailable, "needs `numpy`"):
                baseline_compute([1], [[1]])


class Probe(unittest.TestCase):
    @NEEDS_NUMPY
    def test_the_probe_runs_the_baseline_on_the_input_it_declares(self):
        operation = get_operation("vector_topk")
        self.assertEqual(baseline.probe(operation, {"k": 10}), (True, operation.baseline_description))

    def test_an_operation_that_declares_nothing_is_probed_with_the_old_input(self):
        operation = get_operation("crc32")
        with mock.patch.object(baseline, "compute", wraps=baseline.compute) as spy:
            self.assertTrue(baseline.probe(operation, {})[0])
        spy.assert_called_once_with(operation, b"probe", {})

    def test_a_probe_input_that_is_not_bytes_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / "vector_topk"
            shutil.copytree(OPERATION_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
            with open(copy / "baseline.py", "a") as handle:
                handle.write("\nPROBE_INPUT = 'text'\n")
            operation = replace(get_operation("vector_topk"), directory=copy)
            available, reason = baseline.probe(operation, {"k": 10})
        self.assertFalse(available)
        self.assertIn("PROBE_INPUT must be bytes", reason)


class Check(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)

    @NEEDS_NUMPY
    def test_isc_check_passes_without_a_false_note_about_the_baseline(self):
        create_module(self.workspace, "rag", operation_id="vector_topk")
        report = check_module(self.workspace.load("rag").directory)
        self.assertEqual(report.problems, [])
        self.assertEqual([note for note in report.notes if "baseline" in note], [])


class OptionalCallable(unittest.TestCase):
    def directory(self, source: str | None) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)
        if source is not None:
            (path / "adapter.py").write_text(source)
        return path

    def test_a_function_the_file_does_not_define_gives_none(self):
        directory = self.directory("def execute(session, data, parameters):\n    return data\n")
        self.assertIsNone(loader.load_optional_callable(directory, "adapter.py", "check_input", "t1"))

    def test_a_defined_function_is_returned(self):
        directory = self.directory("def check_input(data, parameters):\n    return 7\n")
        function = loader.load_optional_callable(directory, "adapter.py", "check_input", "t2")
        self.assertEqual(function(b"", {}), 7)

    def test_a_name_that_is_not_callable_is_reported(self):
        with self.assertRaisesRegex(loader.ModuleCodeError, "defines `check_input` but it is not callable"):
            loader.load_optional_callable(self.directory("check_input = 5\n"), "adapter.py",
                                          "check_input", "t3")

    def test_a_missing_file_is_reported(self):
        with self.assertRaisesRegex(loader.ModuleCodeError, "not found"):
            loader.load_optional_callable(self.directory(None), "adapter.py", "check_input", "t4")


class InputCheck(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)

    def manifest(self, operation_id: str = "vector_topk") -> Manifest:
        create_module(self.workspace, "rag", operation_id=operation_id)
        return self.workspace.load("rag")

    def test_a_header_that_disagrees_with_the_size_is_refused_with_the_module_named(self):
        manifest = self.manifest()
        good = make_input([1, 2], [[1, 2], [3, 4]])
        manifest.validate_input(good)
        with self.assertRaisesRegex(
                OperationError,
                r"module 'rag': vector_topk header says rows=2, dim=2, which is 14 bytes; received 13"):
            manifest.validate_input(good[:-1])

    def test_the_input_contract_is_checked_before_the_adapter_is_asked(self):
        with self.assertRaisesRegex(OperationError, "requires at least 9 input bytes; received 5"):
            self.manifest().validate_input(b"short")

    def test_an_adapter_that_defines_no_check_is_not_asked_for_one(self):
        self.manifest("crc32").validate_input(b"anything at all")


class ModelBackend(Backend):
    """A backend whose device is the kernel model, so lifecycle.run goes all the way through."""
    name = "model"
    build_calls = 0

    def available(self):
        return True, "model backend"

    def devices(self):
        return [DeviceInfo(backend=self.name, index=0, name="model-device")]

    def toolchain_identity(self) -> str:
        return "model-toolchain-1.0"

    def build(self, request: BuildRequest) -> BuildOutput:
        ModelBackend.build_calls += 1
        binary = request.output_dir / "module.model"
        binary.write_text("model device image")
        return BuildOutput(binary=binary, log="model build")

    def open(self, manifest, binary, target, device_index=None) -> Session:
        return ModelSession(manifest.kernel.arguments)


class Lifecycle(unittest.TestCase):
    QUERY = [3, -1, 2]
    ROWS = [[1, 1, 1], [0, 5, 0], [4, 0, 1], [-2, -2, 9], [3, -1, 2]] * 7      # 35 rows, many ties

    def setUp(self):
        self._xrt_backend = backends._REGISTRY["xrt"]
        backends._REGISTRY["xrt"] = ModelBackend
        ModelBackend.build_calls = 0
        self.addCleanup(backends._REGISTRY.__setitem__, "xrt", self._xrt_backend)
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)

    def data(self) -> bytes:
        return make_input(self.QUERY, self.ROWS)

    def input_file(self, data: bytes) -> Path:
        path = Path(self._temp.name) / "input.bin"
        path.write_bytes(data)
        return path

    def reference_run(self, transform):
        """Stand in for the child process that computes the reference target's output (the same
        stand-in tests/test_platform.py uses: a real child would not have this backend registered)."""
        def fake_run(command, **kwargs):
            input_path = Path(command[command.index("--input") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(transform(input_path.read_bytes()))
            payload = {"status": "succeeded", "output_path": str(output_path)}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return mock.patch("isc.lifecycle.subprocess.run", side_effect=fake_run)

    def test_a_run_on_the_reference_target_returns_the_top_k_and_records_the_run(self):
        create_module(self.workspace, "rag", operation_id="vector_topk", parameters={"k": 4})
        result = run_module(self.workspace, "rag", self.input_file(self.data()), "hw_emu")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(Path(result.output_path).read_bytes(), expected_output(self.QUERY, self.ROWS, 4))
        self.assertEqual([result.metrics[name] for name in ("rows", "dim", "k", "chunks")],
                         [35.0, 3.0, 4.0, 1.0])
        self.assertEqual([entry["status"] for entry in record.read_all(self.workspace)], ["succeeded"])

    def test_hw_is_compared_with_the_reference_target_byte_for_byte(self):
        create_module(self.workspace, "rag", operation_id="vector_topk", parameters={"k": 4})
        with self.reference_run(lambda data: expected_output(self.QUERY, self.ROWS, 4)):
            result = run_module(self.workspace, "rag", self.input_file(self.data()), "hw")
        self.assertTrue(result.ok, result.error)
        self.assertIn("matches reference target output (32 bytes)", result.validation["detail"])

    def test_a_device_that_disagrees_with_the_reference_fails_the_run(self):
        create_module(self.workspace, "rag", operation_id="vector_topk", parameters={"k": 4})
        wrong = bytearray(expected_output(self.QUERY, self.ROWS, 4))
        wrong[0] ^= 1
        with self.reference_run(lambda data: bytes(wrong)):
            result = run_module(self.workspace, "rag", self.input_file(self.data()), "hw")
        self.assertFalse(result.ok)
        self.assertIn("first difference at byte 0", result.validation["detail"])

    @NEEDS_NUMPY
    def test_device_reference_and_cpu_baseline_give_the_same_bytes(self):
        create_module(self.workspace, "rag", operation_id="vector_topk", parameters={"k": 4})
        manifest = self.workspace.load("rag")
        with self.reference_run(lambda data: expected_output(self.QUERY, self.ROWS, 4)):
            result = run_module(self.workspace, "rag", self.input_file(self.data()), "hw")
        self.assertTrue(result.ok, result.error)
        on_device = Path(result.output_path).read_bytes()
        on_cpu = baseline.compute(manifest.operation, self.data(), manifest.parameters)
        self.assertEqual(on_device, on_cpu)
        self.assertEqual(on_device, expected_output(self.QUERY, self.ROWS, 4))

    def test_a_malformed_input_is_refused_before_anything_is_built(self):
        create_module(self.workspace, "rag", operation_id="vector_topk")
        result = run_module(self.workspace, "rag", self.input_file(self.data()[:-1]), "hw_emu")
        self.assertFalse(result.ok)
        self.assertEqual(ModelBackend.build_calls, 0)
        self.assertIn("module 'rag': vector_topk header says rows=35, dim=3", result.error)
        self.assertEqual([entry["status"] for entry in record.read_all(self.workspace)], ["failed"])

    def test_modules_that_differ_only_in_host_parameters_share_one_build(self):
        create_module(self.workspace, "few", operation_id="vector_topk", parameters={"k": 3})
        create_module(self.workspace, "many", operation_id="vector_topk",
                      parameters={"k": 16, "chunk_bytes": 4096})
        source = self.input_file(self.data())
        first = run_module(self.workspace, "few", source, "hw_emu")
        second = run_module(self.workspace, "many", source, "hw_emu")
        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertEqual(ModelBackend.build_calls, 1)
        self.assertTrue(second.build["cached"])
        self.assertEqual(Path(first.output_path).read_bytes(), expected_output(self.QUERY, self.ROWS, 3))
        self.assertEqual(Path(second.output_path).read_bytes(), expected_output(self.QUERY, self.ROWS, 16))


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def generator():
    return load_file(SCRIPTS_DIR, "make_vector_input.py", "test_vector_topk_generator")


def parse_input(data: bytes):
    """The query and the database rows of an input file, as lists of signed values."""
    rows, dim = struct.unpack_from("<II", data)
    values = struct.unpack_from(f"{dim + rows * dim}b", data, 8)
    return list(values[:dim]), [list(values[dim + r * dim:dim + (r + 1) * dim]) for r in range(rows)]


class Generator(unittest.TestCase):
    def test_the_same_arguments_give_the_same_bytes(self):
        first, _ = generator().build(200, 24, seed=3, plant=2)
        again, _ = generator().build(200, 24, seed=3, plant=2)
        other, _ = generator().build(200, 24, seed=4, plant=2)
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)

    def test_the_file_has_the_input_format(self):
        data, planted = generator().build(120, 40, seed=1)
        self.assertEqual(topk_format.check(data), (120, 40))
        self.assertEqual(len(data), 8 + 40 + 120 * 40)
        self.assertEqual(planted, [])

    def test_every_vector_is_scaled_to_a_peak_of_127_and_minus_128_never_appears(self):
        data, _ = generator().build(60, 33, seed=2)
        query, db = parse_input(data)
        for vector in [query] + db:
            self.assertEqual(max(abs(value) for value in vector), 127)
        self.assertGreaterEqual(min(min(row) for row in db + [query]), -127)

    def test_planted_copies_are_the_best_matches(self):
        data, planted = generator().build(500, 64, seed=7, plant=5)
        query, db = parse_input(data)
        self.assertEqual(len(planted), 5)
        self.assertEqual(planted, sorted(set(planted)))
        self.assertEqual({entry_id for entry_id, _ in reference(query, db, 5)}, set(planted))

    def test_the_generated_file_is_a_valid_input_for_the_adapter(self):
        data, planted = generator().build(300, 48, seed=5, plant=4)
        query, db = parse_input(data)
        output = adapter_execute()(ModelSession(), data, {"k": 10, "chunk_bytes": 4096})
        self.assertEqual(output, expected_output(query, db, 10))
        self.assertEqual({entry_id for entry_id, _ in topk_format.decode(output)[:4]}, set(planted))

    def test_arguments_outside_the_format_are_refused_before_a_file_is_made(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "vec.bin"
            for bad, text in ((["--rows", "0", "--dim", "8"], "rows must be at least 1"),
                              (["--rows", "10", "--dim", "0"], "dim must be 1..2048"),
                              (["--rows", "10", "--dim", "2049"], "dim must be 1..2048"),
                              (["--rows", "10", "--dim", "8", "--plant", "11"], "plant must be 0..rows"),
                              (["--rows", "10", "--dim", "8", "--plant", "-1"], "plant must be 0..rows")):
                with self.subTest(arguments=bad):
                    with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                        with self.assertRaises(SystemExit) as caught:
                            generator().main(bad + ["-o", str(target)])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertIn(text, err.getvalue())
                    self.assertFalse(target.exists())

    def test_the_command_line_writes_the_file_and_reports_the_planted_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nested" / "vec.bin"
            done = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "make_vector_input.py"), "--rows", "300", "--dim", "32",
                 "--seed", "4", "--plant", "3", "-o", str(target)],
                capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads(done.stderr)
            self.assertEqual((report["rows"], report["dim"], report["bytes"]), (300, 32, 8 + 32 + 300 * 32))
            self.assertEqual(len(report["planted_ids"]), 3)
            self.assertEqual(target.read_bytes()[:8], struct.pack("<II", 300, 32))


class Documentation(unittest.TestCase):
    TEXT = (OPERATIONS_DIR.parents[1] / "README.md").read_text()

    def test_the_operation_is_in_the_supported_operations_table_and_the_baseline_table(self):
        self.assertEqual(self.TEXT.count("| `vector_topk` |"), 2)

    def test_the_input_format_the_generator_and_the_large_input_advice_are_given(self):
        for needle in ("isc_vectors_i8", "make_vector_input.py", "--no-validate", "0xFFFFFFFF"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.TEXT)


if __name__ == "__main__":
    unittest.main()
