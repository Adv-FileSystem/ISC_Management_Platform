"""Runtime: the order a session is opened in, and that everything is undone on every path."""
from __future__ import annotations

import inspect
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from isc import backends, bench, lifecycle
from isc.backends import BackendError
from isc.backends.base import Backend, Buffer, DeviceInfo, Session
from isc.devices import DeviceBusy, DeviceManager, Owner
from isc.events import MemorySink, RunEvents
from isc.runtime import Runtime
from isc.scaffold import create_module
from isc.workspace import Workspace


def reserved(manager: DeviceManager) -> list[str]:
    return [holder.key for holder in manager.holders("rec")]


class RecordingSession(Session):
    def buffer(self, size: int, arg: int) -> Buffer:
        raise NotImplementedError

    def run(self, *args) -> None:
        raise NotImplementedError

    def close(self) -> None:
        RecordingBackend.events.append(("close", reserved(RecordingBackend.manager)))


class RecordingBackend(Backend):
    """Notes what the runtime does around open(); the state lives on the class
    because get_backend() builds a fresh instance on every call."""
    name = "rec"
    events: list = []
    manager: DeviceManager | None = None
    open_delay = 0.0
    open_error: Exception | None = None

    def available(self):
        return True, "recording"

    def devices(self):
        return []

    def toolchain_identity(self) -> str:
        return "recording"

    def build(self, request):
        raise NotImplementedError

    def open(self, manifest, binary, target, device_index=None) -> Session:
        RecordingBackend.events.append(
            ("open", reserved(RecordingBackend.manager), os.getcwd(), target, device_index))
        if RecordingBackend.open_delay:
            time.sleep(RecordingBackend.open_delay)
        if RecordingBackend.open_error is not None:
            raise RecordingBackend.open_error
        device = DeviceInfo(backend=self.name, index=device_index or 0, name="rec-device",
                            emulated=target.endswith("_emu"), target=target)
        return RecordingSession(device)


class RuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.workspace.modules_dir.mkdir(parents=True)
        create_module(self.workspace, "demo", operation_id="identity")
        self.manifest = self.workspace.load("demo")     # runtime.backend is "xrt" by default
        self.image_dir = Path(self._temp.name) / "image"
        self.image_dir.mkdir()
        self.binary = self.image_dir / "module.img"
        self.binary.write_text("image")

        self.manager = DeviceManager(self.workspace)
        RecordingBackend.events = []
        RecordingBackend.manager = self.manager
        RecordingBackend.open_delay = 0.0
        RecordingBackend.open_error = None
        patcher = mock.patch.dict(backends._REGISTRY, {"xrt": RecordingBackend})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.runtime = Runtime(self.workspace)
        self.owner = Owner(run_id="runtime-test", pid=os.getpid(), purpose="run")

    def session(self, target="hw", device_index=None, **kwargs):
        return self.runtime.session(self.manifest, self.binary, target, device_index,
                                    owner=self.owner, **kwargs)

    def hold_for(self, seconds: float, target="hw", index=0) -> None:
        """Another reservation on the same device, released after `seconds`."""
        ready = threading.Event()

        def hold():
            holder = Owner(run_id="holder-run", pid=os.getpid(), purpose="run")
            with DeviceManager(self.workspace).reserve("rec", target, index, owner=holder):
                ready.set()
                time.sleep(seconds)

        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        self.assertTrue(ready.wait(5))


class SessionOrder(RuntimeTestCase):
    def test_the_device_is_opened_while_reserved_and_inside_the_image_directory(self):
        with self.session():
            pass
        kind, held, cwd, target, index = RecordingBackend.events[0]
        self.assertEqual((kind, held, target, index), ("open", ["rec-hw-0"], "hw", None))
        self.assertEqual(Path(cwd).resolve(), self.image_dir.resolve())

    def test_the_session_is_closed_while_the_reservation_is_still_held(self):
        with self.session():
            pass
        self.assertEqual(RecordingBackend.events[-1], ("close", ["rec-hw-0"]))
        self.assertEqual(reserved(self.manager), [])

    def test_the_working_directory_comes_back(self):
        before = os.getcwd()
        with self.session():
            self.assertNotEqual(os.getcwd(), before)
        self.assertEqual(os.getcwd(), before)

    def test_a_failing_open_lets_go_of_the_reservation_and_the_directory(self):
        RecordingBackend.open_error = BackendError("cannot open the card")
        before = os.getcwd()
        with self.assertRaisesRegex(BackendError, "cannot open the card"):
            with self.session():
                self.fail("the body must not run when the device did not open")
        self.assertEqual(reserved(self.manager), [])
        self.assertEqual(os.getcwd(), before)

    def test_an_error_in_the_body_still_closes_the_session_and_lets_go(self):
        before = os.getcwd()
        with self.assertRaisesRegex(ValueError, "adapter failed"):
            with self.session():
                raise ValueError("adapter failed")
        self.assertEqual([event[0] for event in RecordingBackend.events], ["open", "close"])
        self.assertEqual(reserved(self.manager), [])
        self.assertEqual(os.getcwd(), before)


class SessionHandleContents(RuntimeTestCase):
    def test_the_handle_carries_the_session_the_device_and_the_timings(self):
        RecordingBackend.open_delay = 0.05
        with self.session() as handle:
            self.assertIsInstance(handle.session, RecordingSession)
            self.assertIs(handle.device, handle.session.device)
            self.assertEqual(handle.device.id, "rec:index0")
            self.assertGreaterEqual(handle.open_ms, 40)
            self.assertLess(handle.wait_ms, 100)

    def test_the_reservation_follows_backend_target_and_device_index(self):
        for target, index, key in [("hw", None, "rec-hw-0"), ("hw", 2, "rec-hw-2"),
                                   ("hw_emu", None, "rec-hw_emu-0")]:
            with self.subTest(target=target, index=index):
                RecordingBackend.events.clear()
                with self.session(target, index):
                    self.assertEqual(RecordingBackend.events[0][1], [key])


class WaitingForTheDevice(RuntimeTestCase):
    def test_the_time_spent_waiting_is_reported(self):
        self.hold_for(0.4)
        with self.session(timeout_s=20) as handle:
            self.assertGreaterEqual(handle.wait_ms, 300)

    def test_a_time_limit_stops_the_run_before_the_device_is_opened(self):
        self.hold_for(1.0)
        with self.assertRaises(DeviceBusy):
            with self.session(timeout_s=0.2):
                self.fail("the device is reserved by someone else")
        self.assertEqual(RecordingBackend.events, [])

    def test_the_waiting_callback_is_handed_on_to_the_manager(self):
        self.hold_for(0.5)
        seen = []
        with self.session(timeout_s=20, on_wait=seen.append):
            pass
        self.assertEqual(seen[0].key, "rec-hw-0")
        self.assertEqual(seen[0].holder.run_id, "holder-run")

    def test_emulation_does_not_wait_for_hardware(self):
        self.hold_for(1.0, target="hw")
        with self.session("hw_emu", timeout_s=0.5) as handle:
            self.assertLess(handle.wait_ms, 200)


def flow(sink: MemorySink) -> list[tuple[str, str]]:
    return [(event["stage"], event["state"]) for event in sink.events if event["type"] == "stage"]


class SessionStages(RuntimeTestCase):
    """With a stream, the two slow steps -- waiting for the device and opening it -- are reported."""

    def stream(self):
        sink = MemorySink()
        return RunEvents("run-1", sink, kind="run", heartbeat_s=0), sink

    def stage_event(self, sink, stage, state):
        return [e for e in sink.events if e.get("stage") == stage and e.get("state") == state][0]

    def test_the_wait_and_the_open_are_stages_with_their_timings(self):
        RecordingBackend.open_delay = 0.05
        events, sink = self.stream()
        with self.session(stream=events):
            pass
        self.assertEqual(flow(sink), [("device_wait", "running"), ("device_wait", "succeeded"),
                                      ("device_open", "running"), ("device_open", "succeeded")])
        self.assertLess(self.stage_event(sink, "device_wait", "succeeded")["detail"]["wait_ms"], 100)
        opened = self.stage_event(sink, "device_open", "succeeded")["detail"]
        self.assertEqual(opened["device"], "rec:index0")
        self.assertGreaterEqual(opened["open_ms"], 40)

    def test_waiting_is_reported_as_progress_and_still_reaches_the_callers_callback(self):
        self.hold_for(0.5)
        events, sink = self.stream()
        seen = []
        with self.session(timeout_s=20, on_wait=seen.append, stream=events):
            pass
        waiting = [event for event in sink.events if event["type"] == "progress"]
        self.assertTrue(waiting)
        self.assertEqual((waiting[0]["stage"], waiting[0]["step"]), ("device_wait", "waiting"))
        self.assertIn("holder-run", waiting[0]["message"])
        self.assertEqual(seen[0].key, "rec-hw-0")

    def test_a_time_limit_fails_the_wait_stage_and_the_device_is_never_opened(self):
        self.hold_for(1.0)
        events, sink = self.stream()
        with self.assertRaises(DeviceBusy):
            with self.session(timeout_s=0.2, stream=events):
                self.fail("the device is reserved by someone else")
        self.assertEqual(flow(sink), [("device_wait", "running"), ("device_wait", "failed")])
        self.assertIn("holder-run", self.stage_event(sink, "device_wait", "failed")["detail"]["error"])
        self.assertEqual(RecordingBackend.events, [])

    def test_a_failing_open_fails_the_open_stage_after_a_successful_wait(self):
        RecordingBackend.open_error = BackendError("cannot open the card")
        events, sink = self.stream()
        with self.assertRaises(BackendError):
            with self.session(stream=events):
                self.fail("the device did not open")
        self.assertEqual(flow(sink), [("device_wait", "running"), ("device_wait", "succeeded"),
                                      ("device_open", "running"), ("device_open", "failed")])
        self.assertIn("cannot open the card",
                      self.stage_event(sink, "device_open", "failed")["detail"]["error"])

    def test_an_error_in_the_body_adds_no_stage_events(self):
        events, sink = self.stream()
        with self.assertRaises(ValueError):
            with self.session(stream=events):
                raise ValueError("adapter failed")
        self.assertEqual(len(flow(sink)), 4)


class OnePathToTheDevice(unittest.TestCase):
    """Reserving and opening live in devices.py and runtime.py; no caller keeps a copy."""

    def test_the_workspace_has_no_lock_of_its_own(self):
        self.assertFalse(hasattr(Workspace, "device_lock"))

    def test_the_callers_open_devices_only_through_the_runtime(self):
        for module in (lifecycle, bench):
            source = inspect.getsource(module)
            for forbidden in ("device_lock", "backend.open(", "working_directory", "get_backend"):
                self.assertNotIn(forbidden, source, f"{module.__name__} still handles {forbidden}")


if __name__ == "__main__":
    unittest.main()
