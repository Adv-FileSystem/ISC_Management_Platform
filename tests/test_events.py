"""Run events: what is written, that writing can never fail a job, and what reads it back."""
from __future__ import annotations

import io
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from isc.events import (
    FileSink, MemorySink, RunEvents, Sink, events_path, follow, list_runs, read_events, start_run,
    describe_event, silent, summarize,
)
from isc.record import PARENT_RUN_ENV
from isc.workspace import Workspace


class Clock:
    """A clock the test moves by hand: event times (`ts`) come from it."""

    def __init__(self, start: float = 1_790_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


def make(**kwargs):
    sink, clock = MemorySink(), Clock()
    events = RunEvents("run-1", sink, kind="run", module="demo", target="hw", backend="xrt",
                       heartbeat_s=0, now=clock, **kwargs)
    return events, sink, clock


class Writing(unittest.TestCase):
    def test_the_first_event_says_who_started_what(self):
        _, sink, _ = make()
        (first,) = sink.events
        self.assertEqual((first["v"], first["seq"], first["run_id"], first["type"]),
                         (1, 1, "run-1", "run"))
        self.assertEqual((first["state"], first["kind"], first["module"], first["target"],
                          first["backend"]), ("started", "run", "demo", "hw", "xrt"))
        self.assertEqual((first["pid"], first["host"]), (os.getpid(), socket.gethostname()))
        self.assertNotIn("parent_run_id", first)

    def test_every_event_carries_the_common_fields_and_seq_counts_up(self):
        events, sink, _ = make()
        with events.stage("build") as stage:
            stage.progress("synth", current=1, total=2)
        events.finish("succeeded")
        self.assertEqual([event["seq"] for event in sink.events], [1, 2, 3, 4, 5])
        for event in sink.events:
            self.assertEqual(event["v"], 1)
            self.assertEqual(event["run_id"], "run-1")
            self.assertRegex(event["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$")

    def test_a_stage_runs_reports_progress_then_succeeds_with_its_duration(self):
        events, sink, _ = make()
        with events.stage("build", target="hw") as stage:
            stage.progress("synth", current=40, total=114, unit="jobs", eta_s=1260.4,
                           message="8 jobs running")
        running, progress, done = sink.events[1:]
        self.assertEqual((running["type"], running["stage"], running["state"]),
                         ("stage", "build", "running"))
        self.assertEqual(running["detail"], {"target": "hw"})
        self.assertEqual((progress["type"], progress["stage"], progress["step"], progress["current"],
                          progress["total"], progress["unit"], progress["eta_s"], progress["message"]),
                         ("progress", "build", "synth", 40, 114, "jobs", 1260, "8 jobs running"))
        self.assertEqual((done["stage"], done["state"]), ("build", "succeeded"))
        self.assertIsInstance(done["duration_s"], float)
        self.assertNotIn("detail", done)

    def test_progress_carries_only_the_values_it_was_given(self):
        events, sink, _ = make()
        with events.stage("build") as stage:
            stage.progress("compile")
        self.assertEqual(set(sink.events[2]), {"v", "seq", "ts", "run_id", "type", "stage", "step"})

    def test_a_stage_that_raises_fails_and_the_error_still_propagates(self):
        events, sink, _ = make()
        with self.assertRaisesRegex(ValueError, "boom"):
            with events.stage("build"):
                raise ValueError("boom")
        failed = sink.events[-1]
        self.assertEqual((failed["stage"], failed["state"]), ("build", "failed"))
        self.assertEqual(failed["detail"], {"error": "ValueError: boom"})

    def test_a_stage_ended_by_hand_is_not_ended_again(self):
        events, sink, _ = make()
        with events.stage("compare") as stage:
            stage.succeed(mode="exact")
        states = [event["state"] for event in sink.events if event["type"] == "stage"]
        self.assertEqual(states, ["running", "succeeded"])
        self.assertEqual(sink.events[-1]["detail"], {"mode": "exact"})

    def test_a_skipped_stage_never_ran(self):
        events, sink, _ = make()
        events.skip("reference", reason="no-validate")
        skipped = sink.events[-1]
        self.assertEqual((skipped["stage"], skipped["state"], skipped["detail"]),
                         ("reference", "skipped", {"reason": "no-validate"}))
        self.assertNotIn("duration_s", skipped)
        self.assertEqual(len(sink.events), 2)

    def test_finish_is_written_once(self):
        events, sink, _ = make()
        events.finish("failed", "bad input")
        events.finish("succeeded")
        finished = [event for event in sink.events if event.get("state") == "finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["status"], finished[0]["error"]), ("failed", "bad input"))

    def test_a_successful_finish_has_no_error_field(self):
        events, sink, _ = make()
        events.finish("succeeded")
        self.assertNotIn("error", sink.events[-1])

    def test_the_callback_sees_every_event(self):
        seen = []
        events, sink, _ = make(on_event=seen.append)
        with events.stage("check"):
            pass
        events.finish("succeeded")
        self.assertEqual(seen, sink.events)


class WritingNeverFailsTheJob(unittest.TestCase):
    def test_a_sink_that_fails_is_reported_once_and_the_job_carries_on(self):
        class Broken(Sink):
            def write(self, event):
                raise OSError("disk full")

        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            events = RunEvents("run-1", Broken(), kind="run", heartbeat_s=0)
            with events.stage("build"):
                pass
            events.finish("succeeded")
        self.assertEqual(err.getvalue().count("warning"), 1)
        self.assertIn("disk full", err.getvalue())

    def test_a_callback_that_fails_does_not_stop_the_events(self):
        def explode(event):
            raise RuntimeError("echo broke")

        with mock.patch("sys.stderr", new_callable=io.StringIO):
            events, sink, _ = make(on_event=explode)
            with events.stage("check"):
                pass
        self.assertEqual([event["type"] for event in sink.events], ["run", "stage", "stage"])


class Files(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)

    def test_the_workspace_keeps_events_under_artifacts(self):
        self.assertEqual(self.workspace.events_dir, Path(self._temp.name).resolve() / "artifacts" / "events")

    def test_each_run_gets_its_own_file_of_one_json_line_per_event(self):
        events = start_run(self.workspace, "run-1", kind="build", module="demo", target="hw",
                           heartbeat_s=0)
        with events.stage("build"):
            pass
        events.finish("succeeded")
        other = start_run(self.workspace, "run-2", kind="run", heartbeat_s=0)
        other.finish("failed", "x")

        lines = (self.workspace.events_dir / "run-1.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["type"] for line in lines], ["run", "stage", "stage", "run"])
        self.assertEqual(len((self.workspace.events_dir / "run-2.jsonl").read_text().splitlines()), 2)

    def test_the_parent_run_is_taken_from_the_environment(self):
        with mock.patch.dict(os.environ, {PARENT_RUN_ENV: "parent-1"}):
            child = start_run(self.workspace, "child-1", kind="run", heartbeat_s=0)
        child.finish("succeeded")
        first = json.loads((self.workspace.events_dir / "child-1.jsonl").read_text().splitlines()[0])
        self.assertEqual(first["parent_run_id"], "parent-1")

    def test_a_run_without_a_parent_names_none(self):
        env = {key: value for key, value in os.environ.items() if key != PARENT_RUN_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            events = start_run(self.workspace, "solo-1", kind="run", heartbeat_s=0)
        events.finish("succeeded")
        first = json.loads((self.workspace.events_dir / "solo-1.jsonl").read_text().splitlines()[0])
        self.assertNotIn("parent_run_id", first)

    def test_a_directory_that_cannot_be_written_is_reported_once_and_skipped(self):
        blocked = Path(self._temp.name) / "artifacts"
        blocked.write_text("a file where the directory should be")
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            events = start_run(self.workspace, "run-1", kind="run", heartbeat_s=0)
            with events.stage("build"):
                pass
            events.finish("succeeded")
        self.assertEqual(err.getvalue().count("warning"), 1)

    def test_two_threads_writing_at_once_never_interleave_lines(self):
        path = Path(self._temp.name) / "run-1.jsonl"
        events = RunEvents("run-1", FileSink(path), kind="run", heartbeat_s=0)

        def spam(name):
            stage = events.stage(name)
            for index in range(100):
                stage.progress("step", current=index, total=100)
            stage.succeed()

        threads = [threading.Thread(target=spam, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        events.finish("succeeded")

        sequence = [json.loads(line)["seq"] for line in path.read_text().splitlines()]
        self.assertEqual(sequence, list(range(1, len(sequence) + 1)))
        self.assertEqual(len(sequence), 1 + 2 * (1 + 100 + 1) + 1)


class Heartbeats(unittest.TestCase):
    def test_a_running_stage_keeps_beating_and_a_finished_one_stops(self):
        sink = MemorySink()
        events = RunEvents("run-1", sink, kind="run", heartbeat_s=0.05)
        with events.stage("build"):
            time.sleep(0.4)
        beats = [event for event in sink.events if event["type"] == "heartbeat"]
        self.assertGreaterEqual(len(beats), 3)
        self.assertEqual({beat["stage"] for beat in beats}, {"build"})
        count = len(beats)
        time.sleep(0.2)
        self.assertEqual(len([e for e in sink.events if e["type"] == "heartbeat"]), count)
        events.finish("succeeded")

    def test_nothing_beats_when_no_stage_is_running(self):
        sink = MemorySink()
        events = RunEvents("run-1", sink, kind="run", heartbeat_s=0.05)
        time.sleep(0.3)
        events.finish("succeeded")
        self.assertEqual([event for event in sink.events if event["type"] == "heartbeat"], [])

    def test_no_beat_comes_after_the_run_has_finished(self):
        sink = MemorySink()
        events = RunEvents("run-1", sink, kind="run", heartbeat_s=0.05)
        stage = events.stage("build")
        time.sleep(0.2)
        events.finish("cancelled")
        count = len(sink.events)
        time.sleep(0.3)
        self.assertEqual(len(sink.events), count)
        stage.fail("interrupted")


def timeline(*steps):
    """Events from a scripted run on a hand-moved clock. Each step is (seconds_to_wait, action)."""
    events, sink, clock = make()
    for wait, action in steps:
        clock.tick(wait)
        action(events)
    return sink.events, clock


class Summarising(unittest.TestCase):
    def test_a_run_in_progress_shows_its_stage_and_the_last_progress(self):
        stage = {}

        def begin(events):
            stage["handle"] = events.stage("build")

        events, clock = timeline(
            (5, begin),
            (60, lambda e: stage["handle"].progress("vpl.synth", current=40, total=114, unit="jobs")),
        )
        clock.tick(10)
        summary = summarize(events, now=clock())
        self.assertEqual((summary.state, summary.stage, summary.progress), ("running", "build", "synth 40/114"))
        self.assertEqual((summary.run_id, summary.kind, summary.module, summary.target),
                         ("run-1", "run", "demo", "hw"))
        self.assertAlmostEqual(summary.elapsed_s, 75, delta=1)
        self.assertEqual((summary.pid, summary.parent_run_id), (os.getpid(), ""))

    def test_a_progress_without_counts_shows_its_message(self):
        stage = {}

        def begin(events):
            stage["handle"] = events.stage("device_wait")

        events, clock = timeline(
            (1, begin),
            (1, lambda e: stage["handle"].progress("waiting", message="reserved by run other-1")),
        )
        self.assertEqual(summarize(events, now=clock()).progress, "waiting reserved by run other-1")

    def test_a_finished_run_reports_its_status_and_how_long_it_took(self):
        events, clock = timeline((2, lambda e: e.finish("succeeded")))
        summary = summarize(events, now=clock() + 3600)
        self.assertEqual((summary.state, summary.stage), ("succeeded", ""))
        self.assertAlmostEqual(summary.elapsed_s, 2, delta=1)

    def test_failed_and_cancelled_runs_carry_their_state_and_error(self):
        failed, _ = timeline((1, lambda e: e.finish("failed", "validation failed")))
        cancelled, _ = timeline((1, lambda e: e.finish("cancelled")))
        self.assertEqual((summarize(failed).state, summarize(failed).error), ("failed", "validation failed"))
        self.assertEqual(summarize(cancelled).state, "cancelled")

    def test_a_stage_that_ended_is_no_longer_the_current_one(self):
        def run_check(events):
            with events.stage("check"):
                pass

        events, clock = timeline((1, run_check))
        self.assertEqual(summarize(events, now=clock()).stage, "")

    def test_a_silent_run_whose_process_is_gone_is_lost(self):
        events, clock = timeline((1, lambda e: e.stage("build")))
        self.assertEqual(summarize(events, now=clock() + 120, alive=lambda pid: False).state, "lost")

    def test_a_silent_run_whose_process_is_alive_is_still_running(self):
        events, clock = timeline((1, lambda e: e.stage("build")))
        self.assertEqual(summarize(events, now=clock() + 120, alive=lambda pid: True).state, "running")

    def test_a_run_that_spoke_recently_is_not_lost_yet(self):
        events, clock = timeline((1, lambda e: e.stage("build")))
        self.assertEqual(summarize(events, now=clock() + 30, alive=lambda pid: False).state, "running")

    def test_a_run_from_another_host_cannot_be_judged_lost(self):
        events, clock = timeline((1, lambda e: e.stage("build")))
        events[0] = {**events[0], "host": "some-other-host"}
        self.assertEqual(summarize(events, now=clock() + 3600, alive=lambda pid: False).state, "running")

    def test_a_child_run_names_its_parent(self):
        events, _ = timeline()
        events[0] = {**events[0], "parent_run_id": "parent-1"}
        self.assertEqual(summarize(events).parent_run_id, "parent-1")

    def test_nothing_to_summarise_without_a_start(self):
        self.assertIsNone(summarize([]))
        self.assertIsNone(summarize([{"v": 1, "seq": 1, "type": "heartbeat", "stage": "build"}]))

    def test_unknown_event_types_and_fields_are_ignored(self):
        events, _ = timeline()
        events.append({"v": 2, "seq": 2, "ts": events[0]["ts"], "run_id": "run-1",
                       "type": "something_new", "extra": [1, 2]})
        self.assertEqual(summarize(events).state, "running")


class ReadingFiles(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)

    def test_a_missing_file_has_no_events(self):
        self.assertEqual(read_events(Path(self._temp.name) / "nope.jsonl"), [])

    def test_a_line_still_being_written_is_ignored(self):
        path = Path(self._temp.name) / "run-1.jsonl"
        path.write_text('{"seq": 1, "type": "run"}\n{"seq": 2, "type": "stag')
        self.assertEqual([event["seq"] for event in read_events(path)], [1])

    def test_a_line_that_is_not_json_is_skipped(self):
        path = Path(self._temp.name) / "run-1.jsonl"
        path.write_text('{"seq": 1}\nnot json\n[1, 2]\n{"seq": 3}\n')
        self.assertEqual([event["seq"] for event in read_events(path)], [1, 3])

    def test_runs_are_listed_newest_first_and_limited(self):
        for run_id in ("20261002T100000-aaaaaa", "20261002T120000-bbbbbb", "20261002T110000-cccccc"):
            start_run(self.workspace, run_id, kind="run", heartbeat_s=0).finish("succeeded")
        listed = list_runs(self.workspace, limit=2)
        self.assertEqual([summary.run_id for summary in listed],
                         ["20261002T120000-bbbbbb", "20261002T110000-cccccc"])

    def test_a_directory_without_events_lists_nothing(self):
        self.assertEqual(list_runs(self.workspace), [])

    def test_a_file_without_a_start_event_is_left_out_of_the_list(self):
        self.workspace.events_dir.mkdir(parents=True)
        (self.workspace.events_dir / "junk.jsonl").write_text("garbage\n")
        self.assertEqual(list_runs(self.workspace), [])


class Following(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "run-1.jsonl"

    def line(self, seq, **fields):
        return json.dumps({"v": 1, "seq": seq, "ts": "2026-10-02T16:15:00+0900", "run_id": "run-1",
                           **fields}) + "\n"

    def test_events_come_out_as_they_arrive_and_it_ends_when_the_run_finishes(self):
        script = [
            self.line(2, type="stage", stage="build", state="running"),
            self.line(3, type="progress", stage="build", step="vpl.synth", current=1, total=2),
            self.line(4, type="run", state="finished", status="succeeded"),
        ]
        self.path.write_text(self.line(1, type="run", state="started", kind="run", module="demo",
                                       target="hw", backend="xrt", pid=os.getpid(),
                                       host=socket.gethostname()))
        naps = []

        def sleep(seconds):
            naps.append(seconds)
            with self.path.open("a") as handle:
                handle.write(script.pop(0))

        seen = [event["seq"] for event in follow(self.path, poll_s=0.5, sleep=sleep, now=lambda: 0.0)]
        self.assertEqual(seen, [1, 2, 3, 4])
        self.assertEqual(naps, [0.5, 0.5, 0.5])

    def test_it_stops_when_the_run_is_lost(self):
        self.path.write_text(self.line(1, type="run", state="started", kind="run", module="demo",
                                       target="hw", backend="xrt", pid=1, host=socket.gethostname()))
        far_future = 4_000_000_000.0
        seen = list(follow(self.path, sleep=lambda _: None, now=lambda: far_future,
                           alive=lambda pid: False))
        self.assertEqual([event["seq"] for event in seen], [1])


def event(**fields):
    return {"v": 1, "seq": 1, "ts": "2026-10-02T16:15:00+0900", "run_id": "run-1", **fields}


class Describing(unittest.TestCase):
    def test_a_started_run_names_what_it_runs(self):
        self.assertEqual(
            describe_event(event(type="run", state="started", kind="run", module="compressor", target="hw")),
            "run run-1: run compressor [hw]")

    def test_a_run_started_by_another_run_names_it(self):
        self.assertEqual(
            describe_event(event(type="run", state="started", kind="run", module="compressor",
                                 target="hw_emu", parent_run_id="parent-1")),
            "run run-1: run compressor [hw_emu] (started by parent-1)")

    def test_a_finished_run_shows_its_status_and_error(self):
        self.assertEqual(describe_event(event(type="run", state="finished", status="succeeded")),
                         "finished: succeeded")
        self.assertEqual(
            describe_event(event(type="run", state="finished", status="failed", error="validation failed")),
            "finished: failed -- validation failed")

    def test_stage_lines(self):
        cases = [
            (dict(stage="build", state="running"), "  build: running"),
            (dict(stage="build", state="succeeded", duration_s=2190.0), "  build: done in 36m30s"),
            (dict(stage="check", state="succeeded", duration_s=0.04), "  check: done in 0.0s"),
            (dict(stage="reference", state="failed", detail={"error": "kernel hung"}),
             "  reference: FAILED -- kernel hung"),
            (dict(stage="build", state="failed"), "  build: FAILED"),
            (dict(stage="reference", state="skipped", detail={"reason": "no-validate"}),
             "  reference: skipped (no-validate)"),
            (dict(stage="compare", state="skipped"), "  compare: skipped"),
        ]
        for fields, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(describe_event(event(type="stage", **fields)), expected)

    def test_progress_lines(self):
        cases = [
            (dict(stage="build", step="vpl.synth", current=40, total=114, unit="jobs", eta_s=1260,
                  message="8 jobs running"), "  build: vpl.synth 40/114 (8 jobs running), ~21m00s left"),
            (dict(stage="device_wait", step="waiting", message="reserved by run other-1"),
             "  device_wait: waiting (reserved by run other-1)"),
            (dict(stage="build", step="vpl.synth", message="started", eta_s=400),
             "  build: vpl.synth (started), ~6m40s left"),
            (dict(stage="build", step="vpl.synth", current=100, total=114, eta_s=0),
             "  build: vpl.synth 100/114"),
        ]
        for fields, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(describe_event(event(type="progress", **fields)), expected)

    def test_a_heartbeat_and_an_unknown_type_have_no_line(self):
        self.assertIsNone(describe_event(event(type="heartbeat", stage="build")))
        self.assertIsNone(describe_event(event(type="something_new")))


class Silent(unittest.TestCase):
    def test_a_silent_stream_accepts_everything_and_says_nothing(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            events = silent()
            with events.stage("build") as stage:
                stage.progress("compile", current=1, total=2)
            events.skip("reference", reason="x")
            events.finish("succeeded")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


if __name__ == "__main__":
    unittest.main()
