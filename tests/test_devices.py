"""Devices: stable identity, reservation, waiting, who holds what."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import isc
from isc import backends
from isc.backends import BackendError
from isc.backends.base import Backend, DeviceInfo
from isc.backends.xrt import XrtBackend
from isc.devices import DeviceBusy, DeviceManager, Owner
from isc.scaffold import create_module
from isc.workspace import Workspace

ROOT = Path(isc.__file__).resolve().parent.parent   # a child process has to import this same isc


class DeviceIdentity(unittest.TestCase):
    def test_a_card_is_named_by_its_slot_not_by_its_index(self):
        device = DeviceInfo(backend="xrt", index=0, name="u50", address="0000:03:00.1")
        self.assertEqual(device.id, "xrt:0000:03:00.1")

    def test_an_emulated_device_is_named_by_the_target_it_emulates(self):
        device = DeviceInfo(backend="xrt", index=0, name="u50", emulated=True, target="hw_emu")
        self.assertEqual(device.id, "xrt:emu:hw_emu:0")

    def test_a_device_without_an_address_falls_back_to_its_index(self):
        self.assertEqual(DeviceInfo(backend="fake", index=2, name="x").id, "fake:index2")

    def test_the_target_does_not_change_the_id_of_a_real_device(self):
        listed = DeviceInfo(backend="xrt", index=0, name="u50", address="0000:03:00.1")
        opened = DeviceInfo(backend="xrt", index=0, name="u50", address="0000:03:00.1", target="hw")
        self.assertEqual(listed.id, opened.id)

    def test_an_emulated_device_without_a_target_still_has_an_id(self):
        self.assertEqual(DeviceInfo(backend="xrt", index=1, name="u50", emulated=True).id, "xrt:emu:emu:1")


class XrtOpenRecordsTheTarget(unittest.TestCase):
    """The id of an emulated device names its target, so XrtBackend.open() has to say which."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        workspace = Workspace(self._temp.name)
        workspace.modules_dir.mkdir(parents=True)
        create_module(workspace, "demo", operation_id="identity")
        self.manifest = workspace.load("demo")

    def _open(self, target: str):
        class FakeDevice:
            def __init__(self, index):
                self.index = index

            def load_xclbin(self, path):
                return "uuid"

            def get_info(self, key):
                return {"name": "fake-u50", "bdf": "0000:03:00.1"}[key]

        fake_pyxrt = types.SimpleNamespace(
            device=FakeDevice,
            kernel=lambda device, uuid, name: object(),
            xrt_info_device=types.SimpleNamespace(name="name", bdf="bdf"),
        )
        # open() sets XCL_EMULATION_MODE for emulation targets; patch.dict puts the environment back.
        with mock.patch.dict("os.environ", clear=False), \
                mock.patch("isc.backends.xrt._import_pyxrt", return_value=fake_pyxrt):
            return XrtBackend().open(self.manifest, Path(self._temp.name) / "x.xclbin", target, 0)

    def test_an_emulated_session_names_its_target(self):
        self.assertEqual(self._open("hw_emu").device.id, "xrt:emu:hw_emu:0")

    def test_a_real_session_is_named_by_its_slot(self):
        self.assertEqual(self._open("hw").device.id, "xrt:0000:03:00.1")


def owner(run_id: str = "20261002T161500-ab12cd", purpose: str = "run") -> Owner:
    return Owner(run_id=run_id, pid=os.getpid(), purpose=purpose)


class Holding:
    """A real second process that keeps a reservation until it is told to let go."""

    CODE = (
        "import os, sys\n"
        "from isc.devices import DeviceManager, Owner\n"
        "from isc.workspace import Workspace\n"
        "backend, target, index, run_id = sys.argv[2:6]\n"
        "owner = Owner(run_id=run_id, pid=os.getpid(), purpose='run')\n"
        "with DeviceManager(Workspace(sys.argv[1])).reserve(backend, target, int(index), owner=owner):\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.readline()\n"
    )

    def __init__(self, workspace: Workspace, backend="fake", target="hw", index=0, run_id="holder-run"):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        self.process = subprocess.Popen(
            [sys.executable, "-c", self.CODE, str(workspace.root), backend, target, str(index), run_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
        )
        if self.process.stdout.readline().strip() != "held":
            raise AssertionError("the holding process did not get the reservation")

    def release(self) -> None:
        if self.process.poll() is None:
            self.process.stdin.write("\n")
            self.process.stdin.flush()
            self.process.wait(timeout=10)


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Workspace(self._temp.name)
        self.manager = DeviceManager(self.workspace)

    def hold(self, **kwargs) -> Holding:
        holder = Holding(self.workspace, **kwargs)
        self.addCleanup(holder.release)
        return holder


class Reserving(ManagerTestCase):
    def test_the_lock_file_keeps_the_name_older_processes_use(self):
        self.assertEqual(self.manager.lock_path("xrt", "hw", 0).name, "xrt-hw-0.lock")
        self.assertEqual(self.manager.lock_path("xrt", "hw_emu", 1).name, "xrt-hw_emu-1.lock")

    def test_a_free_device_is_reserved_without_waiting(self):
        with self.manager.reserve("fake", "hw", 0, owner=owner()) as reservation:
            self.assertEqual(reservation.key, "fake-hw-0")
            self.assertLess(reservation.wait_ms, 100)

    def test_the_holder_is_visible_while_the_reservation_lasts_and_gone_after(self):
        with self.manager.reserve("fake", "hw", 0, owner=owner("run-1", "bench")):
            (holder,) = self.manager.holders()
            self.assertEqual((holder.key, holder.backend, holder.target, holder.index),
                             ("fake-hw-0", "fake", "hw", 0))
            self.assertEqual((holder.run_id, holder.pid, holder.purpose),
                             ("run-1", os.getpid(), "bench"))
            self.assertTrue(holder.since)
        self.assertEqual(self.manager.holders(), [])

    def test_holders_can_be_limited_to_one_backend(self):
        with self.manager.reserve("fake", "hw", 0, owner=owner()):
            self.assertEqual(len(self.manager.holders("fake")), 1)
            self.assertEqual(self.manager.holders("xrt"), [])

    def test_a_note_in_a_lock_nobody_holds_is_not_a_holder(self):
        path = self.manager.lock_path("fake", "hw", 0)
        path.parent.mkdir(parents=True)
        note = {"run_id": "gone", "pid": 2 ** 22 + 7, "since": "x", "purpose": "run"}
        path.write_text(json.dumps(note) + "\n")
        self.assertEqual(self.manager.holders(), [])

    def test_a_held_lock_with_an_unreadable_note_is_still_reported(self):
        path = self.manager.lock_path("fake", "hw", 0)
        path.parent.mkdir(parents=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, b"not json")
        (holder,) = self.manager.holders()
        self.assertEqual((holder.key, holder.run_id, holder.pid), ("fake-hw-0", "", 0))

    def test_the_note_is_cleared_when_the_reservation_ends(self):
        with self.manager.reserve("fake", "hw", 0, owner=owner()):
            pass
        self.assertEqual(self.manager.lock_path("fake", "hw", 0).read_text(), "")

    def test_a_busy_device_is_a_backend_error(self):
        self.assertTrue(issubclass(DeviceBusy, BackendError))


class WaitingForAnotherProcess(ManagerTestCase):
    def test_the_wait_ends_with_device_busy_after_the_time_limit_and_names_the_holder(self):
        holder = self.hold(run_id="holder-run")
        started = time.monotonic()
        with self.assertRaises(DeviceBusy) as caught:
            with self.manager.reserve("fake", "hw", 0, owner=owner(), timeout_s=0.4):
                self.fail("the device is held by another process")
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        message = str(caught.exception)
        self.assertIn("fake-hw-0", message)
        self.assertIn("holder-run", message)
        self.assertIn(f"pid {holder.process.pid}", message)

    def test_a_waiter_never_wipes_the_note_of_the_holder(self):
        self.hold(run_id="holder-run")
        with self.assertRaises(DeviceBusy):
            with self.manager.reserve("fake", "hw", 0, owner=owner(), timeout_s=0.3):
                pass
        (still,) = self.manager.holders()
        self.assertEqual(still.run_id, "holder-run")

    def test_on_wait_is_called_at_once_and_then_every_report_interval(self):
        self.hold(run_id="holder-run")
        seen = []
        with mock.patch("isc.devices.POLL_SECONDS", 0.05), mock.patch("isc.devices.REPORT_SECONDS", 0.25):
            with self.assertRaises(DeviceBusy):
                with self.manager.reserve("fake", "hw", 0, owner=owner(), timeout_s=1.0,
                                          on_wait=seen.append):
                    pass
        self.assertGreaterEqual(len(seen), 3)
        self.assertLess(seen[0].waited_s, 0.1)
        self.assertEqual(seen[0].key, "fake-hw-0")
        self.assertEqual(seen[0].holder.run_id, "holder-run")

    def test_the_waiter_gets_the_device_as_soon_as_the_holder_lets_go(self):
        holder = self.hold(run_id="holder-run")
        timer = threading.Timer(0.5, holder.release)
        self.addCleanup(timer.cancel)
        timer.start()
        with self.manager.reserve("fake", "hw", 0, owner=owner("waiter"), timeout_s=20) as reservation:
            self.assertGreaterEqual(reservation.wait_ms, 400)
            self.assertEqual(self.manager.holders()[0].run_id, "waiter")

    def test_emulation_and_hardware_never_wait_for_each_other(self):
        self.hold(target="hw")
        with self.manager.reserve("fake", "hw_emu", 0, owner=owner(), timeout_s=0.5):
            pass

    def test_a_different_index_is_a_different_device(self):
        self.hold(index=0)
        with self.manager.reserve("fake", "hw", 1, owner=owner(), timeout_s=0.5):
            pass


class _StubBackend(Backend):
    name = "stub"

    def available(self):
        return True, "stub"

    def devices(self):
        return []

    def toolchain_identity(self):
        return "stub"

    def build(self, request):
        raise NotImplementedError

    def open(self, manifest, binary, target, device_index=None):
        raise NotImplementedError


class _GoodBackend(_StubBackend):
    def devices(self):
        return [DeviceInfo(backend="good", index=0, name="card", address="0000:01:00.1")]


class _BrokenBackend(_StubBackend):
    def devices(self):
        raise RuntimeError("cannot enumerate")


class Listing(ManagerTestCase):
    def test_devices_come_from_every_backend_and_a_failing_backend_is_skipped(self):
        registry = {"good": _GoodBackend, "broken": _BrokenBackend}
        with mock.patch.dict(backends._REGISTRY, registry, clear=True):
            self.assertEqual([device.id for device in self.manager.devices()], ["good:0000:01:00.1"])
            self.assertEqual(len(self.manager.devices("good")), 1)
            self.assertEqual(self.manager.devices("broken"), [])


if __name__ == "__main__":
    unittest.main()
