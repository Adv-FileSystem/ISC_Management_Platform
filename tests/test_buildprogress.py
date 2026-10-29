"""Build progress: reading v++'s own markers, streaming its output, and turning both into events."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from isc import backends
from isc.backends import BackendError
from isc.backends.base import Backend, BuildOutput, BuildProgress, BuildRequest
from isc.backends.xrt import XrtBackend, _run, parse_progress
from isc.build import build
from isc.buildwatch import BuildWatch, expected_seconds, history_path, recent_durations
from isc.events import MemorySink, RunEvents
from isc.scaffold import create_module
from isc.workspace import Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "vpp_hw_build_excerpt.log"


def fields(progress: BuildProgress) -> tuple:
    return (progress.step, progress.state, progress.current, progress.total, progress.unit,
            progress.message, progress.clock)


def parse_all(text: str) -> list[tuple]:
    return [fields(progress) for progress in map(parse_progress, text.splitlines())
            if progress is not None]


# What the fixture says, line by line (the five lines that are not markers produce nothing).
# The last item is the tool's own clock on the line; the compile markers carry none.
EXPECTED_HW = [
    ("compile", "started", None, None, "", "", ""),
    ("compile", "completed", None, None, "", "", ""),
    ("link.system_link", "started", None, None, "", "", "17:56:46"),
    ("link.system_link", "completed", None, None, "", "", "17:56:51"),
    ("link.cf2sw", "started", None, None, "", "", "17:56:51"),
    ("link.vpl", "started", None, None, "", "", "17:56:53"),
    ("vpl.create_project", "started", None, None, "", "", "17:57:01"),
    ("vpl.create_project", "completed", None, None, "", "", "17:57:02"),
    ("vpl.synth", "started", None, None, "", "", "17:57:52"),
    ("vpl.synth", "running", 0, 114, "jobs", "8 jobs running", "17:58:22"),
    ("vpl.synth", "running", 7, 114, "jobs", "8 jobs running", "17:58:53"),
    ("vpl.synth", "running", 10, 114, "jobs", "6 jobs running", "17:59:23"),
    ("vpl.synth", "running", 109, 114, "jobs", "5 jobs running", "18:06:25"),
    ("vpl.synth", "running", 113, 114, "jobs", "1 job running", "18:06:55"),
    ("vpl.synth", "running", None, None, "", "top-level synthesis", "18:07:26"),
    ("vpl.synth", "completed", None, None, "", "", "18:07:54"),
    ("vpl.impl", "started", None, None, "", "", "18:07:54"),
    ("vpl.impl", "running", 2, 6, "tasks", "FPGA linking synthesized kernels to platform", "18:11:56"),
    ("vpl.impl", "running", 3, 6, "tasks", "FPGA logic optimization", "18:12:26"),
    ("vpl.impl", "running", 4, 6, "tasks", "FPGA logic placement", "18:24:01"),
    ("vpl.impl", "completed", None, None, "", "", "18:33:10"),
    ("link.vpl", "completed", None, None, "", "", "18:33:10"),
]


class ParsingTheMarkers(unittest.TestCase):
    def test_a_real_hw_build_log_excerpt_reads_as_the_expected_steps(self):
        self.assertEqual(parse_all(FIXTURE.read_text()), EXPECTED_HW)

    def test_the_hardware_emulation_steps_and_compile_line_are_read_too(self):
        text = "\n".join([
            "INFO: [v++ 60-585] Compiling for hardware emulation target",
            "[17:42:57] Run vpl: Step config_hw_emu.gen_scripts: Started",
            "[17:43:23] Run vpl: Step config_hw_emu.gen_scripts: Completed",
            "[17:44:03] Run vpl: Step config_hw_emu.elaborate: Started",
        ])
        self.assertEqual(parse_all(text), [
            ("compile", "started", None, None, "", "", ""),
            ("vpl.config_hw_emu.gen_scripts", "started", None, None, "", "", "17:42:57"),
            ("vpl.config_hw_emu.gen_scripts", "completed", None, None, "", "", "17:43:23"),
            ("vpl.config_hw_emu.elaborate", "started", None, None, "", "", "17:44:03"),
        ])

    def test_lines_that_are_not_markers_are_ignored(self):
        for line in [
            "", "   ", "INFO: [v++ 60-423]   Target device: xilinx_u50_gen3x16_xdma_5_202210_1",
            "[18:33:10] Run vpl: FINISHED. Run Status: impl Complete!",
            "[18:06:55] Block-level synthesis in progress.",        # no counts: nothing to report
            "Time (s): cpu = 00:00:02 ; elapsed = 00:00:02",
        ]:
            self.assertIsNone(parse_progress(line), line)

    def test_odd_input_never_raises(self):
        for line in ["\x00\xff", "[99:99:99] Run vpl: Step : Started",
                     "Finished 99999999999999999999999th of 6 tasks (x)", "é" * 10_000]:
            parse_progress(line)


class ProgressTypes(unittest.TestCase):
    def test_a_progress_has_no_counts_unless_the_tool_gave_them(self):
        progress = BuildProgress("compile", "started")
        self.assertEqual((progress.current, progress.total, progress.unit, progress.message),
                         (None, None, "", ""))

    def test_a_build_request_is_not_listened_to_unless_asked(self):
        request = BuildRequest(manifest=None, target="hw", output_dir=Path("."))
        self.assertIsNone(request.on_progress)


def alive(pid: int) -> bool:
    """A process that is running. A killed one that nobody has collected yet (a zombie) is not."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def wait_until_gone(pid: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


class StreamingATool(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)

    def tool(self, source: str) -> list[str]:
        script = self.tmp / "tool.py"
        script.write_text(source)
        return [sys.executable, "-u", str(script)]

    def test_lines_reach_the_callback_and_the_log_while_the_tool_is_still_running(self):
        go, log = self.tmp / "go", self.tmp / "build.log"
        command = self.tool(
            "import os, time\n"
            "print('one', flush=True)\n"
            f"for _ in range(100):\n"
            f"    if os.path.exists({str(go)!r}): break\n"
            f"    time.sleep(0.05)\n"
            f"print('two' if os.path.exists({str(go)!r}) else 'gave up', flush=True)\n"
        )
        seen = []

        def on_line(line):
            seen.append(line.strip())
            if line.strip() == "one":
                seen.append("log:" + log.read_text().strip())   # already written, tool still running
                go.write_text("go")

        code, output = _run(command, 30, log_path=log, on_line=on_line)
        self.assertEqual(seen, ["one", "log:one", "two"])
        self.assertEqual((code, output), (0, "one\ntwo\n"))
        self.assertEqual(log.read_text(), "one\ntwo\n")

    def test_the_log_is_appended_to_not_replaced(self):
        log = self.tmp / "build.log"
        log.write_text("$ command line\n")
        _run(self.tool("print('output')\n"), 30, log_path=log)
        self.assertEqual(log.read_text(), "$ command line\noutput\n")

    def test_error_output_is_merged_and_the_exit_code_comes_back(self):
        code, output = _run(self.tool(
            "import sys\nprint('out', flush=True)\nprint('err', file=sys.stderr, flush=True)\nsys.exit(3)\n"), 30)
        self.assertEqual(code, 3)
        self.assertEqual(sorted(output.split()), ["err", "out"])

    def test_a_callback_that_fails_does_not_stop_the_build(self):
        def explode(line):
            raise RuntimeError("callback broke")

        code, output = _run(self.tool("print('a')\nprint('b')\n"), 30, on_line=explode)
        self.assertEqual((code, output), (0, "a\nb\n"))

    def test_a_missing_program_is_a_backend_error(self):
        with self.assertRaisesRegex(BackendError, "no-such-tool-for-isc not found on PATH"):
            _run(["no-such-tool-for-isc"], None)

    def grandchild_tool(self, pids: Path) -> list[str]:
        return self.tool(
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pids)!r}, 'w').write(str(child.pid))\n"
            "print('started', flush=True)\n"
            "time.sleep(60)\n"
        )

    def test_a_time_out_ends_the_whole_process_group_not_only_the_direct_child(self):
        pids = self.tmp / "grandchild.pid"
        started = time.monotonic()
        with self.assertRaisesRegex(BackendError, "timed out after 1.0s"):
            _run(self.grandchild_tool(pids), 1.0)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(wait_until_gone(int(pids.read_text())), "the grandchild kept running")

    def test_an_interrupt_ends_the_whole_process_group_and_propagates(self):
        pids = self.tmp / "grandchild.pid"

        def interrupt(line):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            _run(self.grandchild_tool(pids), 60, on_line=interrupt)
        self.assertTrue(wait_until_gone(int(pids.read_text())), "the grandchild kept running")


FAKE_VPP = """#!{python}
import sys, time
args = sys.argv[1:]
out = args[args.index("-o") + 1]
if "-c" in args:
    print("INFO: [v++ 60-585] Compiling for hardware target", flush=True)
    time.sleep(0.2)
    if {fail_compile}:
        print("ERROR: [v++ 60-399] kernel does not synthesise", flush=True)
        sys.exit(1)
    print("INFO: [v++ 60-594] Finished kernel compilation", flush=True)
else:
    print("INFO: [v++ 60-1443] [17:56:53] Run run_link: Step vpl: Started", flush=True)
    time.sleep(0.2)
    print("[17:58:22] Block-level synthesis in progress, 7 of 114 jobs complete, 8 jobs running.", flush=True)
    time.sleep(0.2)
    print("INFO: [v++ 60-1441] [18:33:10] Run run_link: Step vpl: Completed", flush=True)
open(out, "w").write("image")
"""


class BuildingWithAFakeVpp(unittest.TestCase):
    """XrtBackend.build() against a script standing in for v++ (the real one takes minutes)."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        workspace = Workspace(self.tmp / "ws")
        workspace.modules_dir.mkdir(parents=True)
        create_module(workspace, "demo", operation_id="identity")
        self.manifest = workspace.load("demo")
        self.out = self.tmp / "out"

    def install(self, *, fail_compile: bool = False):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "v++"
        fake.write_text(FAKE_VPP.format(python=sys.executable, fail_compile=fail_compile))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, seen=None) -> BuildRequest:
        return BuildRequest(manifest=self.manifest, target="hw", output_dir=self.out,
                            kernel_source=self.manifest.kernel_source,
                            on_progress=seen.append if seen is not None else None)

    def test_progress_arrives_in_order_and_the_log_holds_everything(self):
        self.install()
        seen: list[BuildProgress] = []
        output = XrtBackend().build(self.request(seen))
        self.assertEqual([(p.step, p.state, p.current) for p in seen], [
            ("compile", "started", None), ("compile", "completed", None),
            ("link.vpl", "started", None), ("vpl.synth", "running", 7), ("link.vpl", "completed", None),
        ])
        log = (self.out / "build.log").read_text()
        self.assertIn("$ v++ -c", log)
        self.assertIn("$ v++ -l", log)
        self.assertIn("Block-level synthesis in progress, 7 of 114", log)
        self.assertEqual(log, output.log)
        self.assertTrue(output.binary.exists())

    def test_a_build_nobody_listens_to_still_logs(self):
        self.install()
        XrtBackend().build(self.request())
        self.assertIn("Finished kernel compilation", (self.out / "build.log").read_text())

    def test_a_failed_build_keeps_the_log_it_had_written(self):
        self.install(fail_compile=True)
        with self.assertRaisesRegex(BackendError, r"v\+\+ compile failed \(exit 1\)"):
            XrtBackend().build(self.request())
        self.assertIn("kernel does not synthesise", (self.out / "build.log").read_text())

    def test_a_new_build_starts_a_new_log(self):
        self.install()
        self.out.mkdir()
        (self.out / "build.log").write_text("the log of an earlier build\n")
        XrtBackend().build(self.request())
        self.assertNotIn("earlier build", (self.out / "build.log").read_text())


class WatchingABuild(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.sink = MemorySink()
        self.events = RunEvents("run-1", self.sink, kind="build", heartbeat_s=0)
        self.stage = self.events.stage("build")
        self.t = 1000.0

    def watch(self, **kwargs) -> BuildWatch:
        return BuildWatch(self.workspace, self.stage, operation="lz4", target="hw",
                          fingerprint="fp1", now=lambda: self.t, **kwargs)

    def progress_events(self) -> list[dict]:
        return [event for event in self.sink.events if event["type"] == "progress"]

    def remember(self, step: str, *seconds: float, operation="lz4", target="hw") -> None:
        history_path(self.workspace).parent.mkdir(parents=True, exist_ok=True)
        with history_path(self.workspace).open("a") as handle:
            for value in seconds:
                handle.write(json.dumps({"operation": operation, "target": target, "step": step,
                                         "seconds": value, "fingerprint": "old", "ts": "x"}) + "\n")

    def test_a_started_step_is_reported_without_an_estimate_when_there_is_no_history(self):
        self.watch()(BuildProgress("vpl.synth", "started"))
        (event,) = self.progress_events()
        self.assertEqual((event["stage"], event["step"], event["message"]), ("build", "vpl.synth", "started"))
        self.assertNotIn("eta_s", event)

    def test_a_counted_step_reports_only_the_counts_the_tool_gave(self):
        watch = self.watch()
        watch(BuildProgress("vpl.synth", "started"))
        self.t += 60
        watch(BuildProgress("vpl.synth", "running", 7, 114, "jobs", "8 jobs running"))
        event = self.progress_events()[-1]
        self.assertEqual((event["current"], event["total"], event["unit"], event["message"]),
                         (7, 114, "jobs", "8 jobs running"))
        self.assertNotIn("eta_s", event)

    def test_a_completed_step_is_remembered_with_how_long_it_took(self):
        watch = self.watch()
        watch(BuildProgress("vpl.synth", "started"))
        self.t += 612.5
        watch(BuildProgress("vpl.synth", "completed"))
        self.assertEqual(self.progress_events()[-1]["message"], "completed")
        (line,) = history_path(self.workspace).read_text().splitlines()
        entry = json.loads(line)
        self.assertEqual((entry["operation"], entry["target"], entry["step"], entry["seconds"],
                          entry["fingerprint"]), ("lz4", "hw", "vpl.synth", 612.5, "fp1"))
        self.assertRegex(entry["ts"], r"^\d{4}-\d\d-\d\dT")

    def test_a_step_is_timed_by_the_tools_own_clock_when_both_ends_have_one(self):
        watch = self.watch()
        watch(BuildProgress("vpl.create_project", "started", clock="18:06:18"))
        self.t += 0.02                       # both lines reached us in one burst
        watch(BuildProgress("vpl.create_project", "completed", clock="18:06:37"))
        entry = json.loads(history_path(self.workspace).read_text().splitlines()[-1])
        self.assertEqual(entry["seconds"], 19.0)

    def test_the_tools_clock_wraps_at_midnight(self):
        watch = self.watch()
        watch(BuildProgress("vpl.impl", "started", clock="23:59:50"))
        self.t += 20
        watch(BuildProgress("vpl.impl", "completed", clock="00:00:10"))
        entry = json.loads(history_path(self.workspace).read_text().splitlines()[-1])
        self.assertEqual(entry["seconds"], 20.0)

    def test_a_clock_on_only_one_end_falls_back_to_the_arrival_times(self):
        watch = self.watch()
        watch(BuildProgress("compile", "started"))
        self.t += 7.5
        watch(BuildProgress("compile", "completed", clock="18:06:37"))
        entry = json.loads(history_path(self.workspace).read_text().splitlines()[-1])
        self.assertEqual(entry["seconds"], 7.5)

    def test_a_step_that_never_completed_is_not_remembered(self):
        self.watch()(BuildProgress("vpl.synth", "started"))
        self.assertFalse(history_path(self.workspace).exists())

    def test_the_estimate_is_the_median_of_the_last_five_minus_the_time_already_spent(self):
        self.remember("vpl.synth", 100, 200, 300, 400, 500, 600)       # the first is too old
        self.assertEqual(recent_durations(self.workspace, "lz4", "hw", "vpl.synth"),
                         [200, 300, 400, 500, 600])
        self.assertEqual(expected_seconds(self.workspace, "lz4", "hw", "vpl.synth"), 400)
        watch = self.watch()
        watch(BuildProgress("vpl.synth", "started"))
        self.assertEqual(self.progress_events()[-1]["eta_s"], 400)
        self.t += 150
        watch(BuildProgress("vpl.synth", "running", 40, 114, "jobs"))
        self.assertEqual(self.progress_events()[-1]["eta_s"], 250)
        self.t += 1000
        watch(BuildProgress("vpl.synth", "running", 100, 114, "jobs"))
        self.assertEqual(self.progress_events()[-1]["eta_s"], 0)

    def test_history_of_another_operation_target_or_step_is_not_used(self):
        self.remember("vpl.synth", 500, operation="sha256")
        self.remember("vpl.synth", 500, target="hw_emu")
        self.remember("vpl.impl", 500)
        self.assertIsNone(expected_seconds(self.workspace, "lz4", "hw", "vpl.synth"))

    def test_unreadable_history_lines_are_skipped(self):
        self.remember("vpl.synth", 300)
        with history_path(self.workspace).open("a") as handle:
            handle.write("not json\n[1]\n" + json.dumps({"operation": "lz4", "target": "hw",
                                                          "step": "vpl.synth", "seconds": "x"}) + "\n")
        self.assertEqual(recent_durations(self.workspace, "lz4", "hw", "vpl.synth"), [300])

    def test_watching_never_fails_the_build(self):
        class BrokenStage:
            def progress(self, *args, **kwargs):
                raise RuntimeError("events broke")

        blocked = Path(self._temp.name) / "artifacts"
        blocked.write_text("a file where the directory should be")
        watch = BuildWatch(self.workspace, BrokenStage(), operation="lz4", target="hw",
                           fingerprint="fp1", now=lambda: self.t)
        watch(BuildProgress("vpl.synth", "started"))
        watch(BuildProgress("vpl.synth", "completed"))


class ProgressBackend(Backend):
    name = "fake"
    requests: list = []

    def available(self):
        return True, "fake"

    def devices(self):
        return []

    def toolchain_identity(self) -> str:
        return "fake-toolchain-1.0"

    def build(self, request: BuildRequest) -> BuildOutput:
        ProgressBackend.requests.append(request)
        if request.on_progress is not None:
            request.on_progress(BuildProgress("compile", "started"))
            request.on_progress(BuildProgress("compile", "completed"))
        binary = request.output_dir / "module.fake"
        binary.write_text("image")
        return BuildOutput(binary=binary, log="fake build")

    def open(self, manifest, binary, target, device_index=None):
        raise NotImplementedError


class CoreBuildRelaysProgress(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)
        create_module(self.workspace, "demo", operation_id="identity")
        self.manifest = self.workspace.load("demo")
        ProgressBackend.requests = []
        patcher = mock.patch.dict(backends._REGISTRY, {"xrt": ProgressBackend})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stage_given_to_build_receives_the_backend_progress_and_the_durations_are_kept(self):
        sink = MemorySink()
        events = RunEvents("run-1", sink, kind="build", heartbeat_s=0)
        with events.stage("build") as stage:
            result = build(self.workspace, self.manifest, "hw", stage=stage)
        steps = [(event["step"], event["message"]) for event in sink.events if event["type"] == "progress"]
        self.assertEqual(steps, [("compile", "started"), ("compile", "completed")])
        entry = json.loads(history_path(self.workspace).read_text().splitlines()[0])
        self.assertEqual((entry["operation"], entry["target"], entry["step"], entry["fingerprint"]),
                         ("identity", "hw", "compile", result.fingerprint))

    def test_without_a_stage_the_backend_is_not_asked_to_report(self):
        build(self.workspace, self.manifest, "hw")
        self.assertIsNone(ProgressBackend.requests[0].on_progress)
        self.assertFalse(history_path(self.workspace).exists())


if __name__ == "__main__":
    unittest.main()
