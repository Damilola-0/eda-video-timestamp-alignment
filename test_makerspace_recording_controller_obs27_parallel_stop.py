import csv
import importlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


_import_home = None
controller_module = None


def setUpModule():
    global _import_home, controller_module
    _import_home = tempfile.TemporaryDirectory()
    with patch.object(Path, "home", return_value=Path(_import_home.name)):
        controller_module = importlib.import_module("makerspace_controller.controller")


def tearDownModule():
    for name in tuple(sys.modules):
        if name == "makerspace_controller" or name.startswith("makerspace_controller."):
            sys.modules.pop(name, None)
    _import_home.cleanup()


class ControllerLoggingTests(unittest.TestCase):
    def test_log_records_lsl_clock_failure_without_dropping_csv_entry(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        controller.log_lock = threading.Lock()
        controller._ui = lambda **kwargs: None

        def fail_clock():
            raise RuntimeError("clock unavailable")

        controller._lsl = lambda: SimpleNamespace(local_clock=fail_clock)

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "audit.csv"
            with log_path.open("w", newline="", encoding="utf-8") as log_file:
                csv.writer(log_file).writerow(
                    ["event", "lsl_seconds", "unix_seconds", "local_datetime", "utc_datetime", "detail"]
                )
            controller.log_path = log_path
            controller._log("TEST_EVENT", "test detail")

            with log_path.open(newline="", encoding="utf-8") as log_file:
                row = next(csv.DictReader(log_file))

        self.assertEqual(row["event"], "TEST_EVENT")
        self.assertEqual(row["lsl_seconds"], "")
        self.assertIn("LSL timestamp unavailable", row["detail"])
        self.assertIn("clock unavailable", row["detail"])
        self.assertTrue(row["unix_seconds"])


class ControllerCueTests(unittest.TestCase):
    def test_cue_failure_is_logged_and_reported(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        controller.recording = True
        controller.marker_no = 0
        controller.busy = False
        logs = []
        statuses = []

        def fail_marker(label):
            raise RuntimeError("clock unavailable")

        def run_task(target):
            controller.busy = True
            target()

        controller._beep_and_marker = fail_marker
        controller._log = lambda event, detail="": logs.append((event, detail))
        controller._ui = lambda status=None: statuses.append(status)
        controller._launch = run_task

        controller.cue()

        self.assertEqual(logs[0][0], "CUE_FAILED")
        self.assertIn("clock unavailable", logs[0][1])
        self.assertTrue(any("Cue failed" in status for status in statuses if status))
        self.assertFalse(controller.busy)

    def test_cue_reports_marker_when_beep_fails(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        controller.recording = True
        controller.marker_no = 0
        controller.busy = False
        statuses = []
        controller._beep_and_marker = lambda label: False
        controller._log = lambda event, detail="": None
        controller._ui = lambda status=None, **kwargs: statuses.append(status)
        controller._launch = lambda target: target()

        controller.cue()

        self.assertIn("Marker sent, but beep failed. See log.", statuses)
        self.assertFalse(controller.busy)

    def test_beep_failure_is_logged_after_marker_emission(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        markers = []
        logs = []
        controller.marker_outlet = SimpleNamespace(push_sample=lambda sample, timestamp: markers.append(sample))
        controller._lsl = lambda: SimpleNamespace(local_clock=lambda: 1.0)
        controller._log = lambda event, detail="", lsl_timestamp=None: logs.append(event)

        def fail_beep(frequency, duration):
            raise RuntimeError("audio unavailable")

        failing_winsound = SimpleNamespace(Beep=fail_beep)

        with patch.dict(sys.modules, {"winsound": failing_winsound}):
            beep_sent = controller._beep_and_marker("SYNC_001")

        self.assertFalse(beep_sent)
        self.assertEqual([["SYNC_001"]], markers)
        self.assertIn("BEEP_ERROR", logs)


class ControllerStartTests(unittest.TestCase):
    def _controller(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        controller.start_attempted = False
        controller.busy = False
        controller.os_start_attempted = False
        controller.lr_start_attempted = False
        controller.root_folder = SimpleNamespace(get=lambda: ".")
        controller._log = lambda event, detail="": None
        controller._ui = lambda status=None, **kwargs: None
        controller._launch = lambda target: target()
        controller._open_os = lambda: None
        controller._marker = lambda: None
        controller._obs = lambda: SimpleNamespace(
            get_record_status=lambda: SimpleNamespace(output_active=False),
            start_record=lambda: None,
        )
        return controller

    def test_opensignals_stop_is_eligible_when_start_ack_is_lost(self):
        controller = self._controller()

        def lose_start_ack(command):
            raise RuntimeError("START acknowledgment lost")

        controller._send_os = lose_start_ack

        with patch.object(controller_module, "saved_xdf_snapshot", return_value={}):
            controller.start()

        self.assertTrue(controller.os_start_attempted)

    def test_labrecorder_stop_is_eligible_when_start_ack_is_lost(self):
        controller = self._controller()
        controller._send_os = lambda command: {"returnCode": 0}
        controller._wait_streams = lambda: [
            controller_module.EDA_STREAM,
            controller_module.MARKER_STREAM,
            controller_module.OBS_STREAM,
        ]

        def lose_lr_start_ack(command):
            if command == "start":
                raise RuntimeError("START acknowledgment lost")
            return b"OK"

        controller._lr = lose_lr_start_ack

        with patch.object(controller_module, "saved_xdf_snapshot", return_value={}), patch.object(
            controller_module.time, "sleep"
        ):
            controller.start()

        self.assertTrue(controller.os_start_attempted)
        self.assertTrue(controller.lr_start_attempted)


class ControllerObsStopTests(unittest.TestCase):
    def test_obs_stop_is_confirmed_by_nonempty_saved_video(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        controller.obs_video = ""
        controller._obs = lambda: SimpleNamespace(
            stop_record=lambda: SimpleNamespace(output_path=str(self.video_path))
        )
        controller._log = lambda event, detail="": self.events.append((event, detail))
        self.events = []

        with tempfile.TemporaryDirectory() as directory:
            self.video_path = Path(directory) / "recording.mp4"
            self.video_path.write_bytes(b"video")

            self.assertTrue(controller._stop_obs_verified())

        self.assertTrue(any(event == "OBS_STOP_CONFIRMED" for event, _ in self.events))


class ControllerOpenSignalsTests(unittest.TestCase):
    def test_send_os_reads_fragmented_json_response(self):
        controller = controller_module.Controller.__new__(controller_module.Controller)
        packets = iter([b'{"return', b'Code": 0, "returnData": ["device"]}'])
        controller.open_conn = SimpleNamespace(
            sendall=lambda command: None,
            recv=lambda size: next(packets),
        )
        controller._log = lambda event, detail="": None

        response = controller._send_os("devices")

        self.assertEqual(["device"], response["returnData"])


class ControllerXdfVerificationTests(unittest.TestCase):
    def test_current_run_xdf_disappearing_does_not_verify_stale_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_xdf = root / "older.xdf"
            old_xdf.write_bytes(b"stale existing recording")
            old_snapshot = (old_xdf.stat().st_size, old_xdf.stat().st_mtime_ns)

            current_xdf = root / "current_run.xdf"
            current_xdf.write_bytes(b"new recording")
            current_snapshot = (current_xdf.stat().st_size, current_xdf.stat().st_mtime_ns)
            current_path = str(current_xdf.resolve())

            before = {
                str(old_xdf.resolve()): old_snapshot,
                current_path: current_snapshot,
            }

            current_xdf.unlink()
            old_xdf.write_bytes(b"modified stale recording")

            changed = controller_module.new_xdfs_since_snapshot(str(root), before)
            self.assertEqual([], [Path(path) for path in (str(p) for p in changed)])

    def test_disappeared_current_xdf_and_older_file_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older_xdf = root / "older.xdf"
            older_xdf.write_bytes(b"older recording")
            current_xdf = root / "current.xdf"
            current_xdf.write_bytes(b"current run")
            before = controller_module.saved_xdf_snapshot(root)
            current_xdf.unlink()

            controller = controller_module.Controller.__new__(controller_module.Controller)
            controller.recording = False
            controller.lr_start_attempted = False
            controller.os_start_attempted = False
            controller.busy = False
            controller.start_attempted = True
            controller.obs_conn = None
            controller.obs_video = ""
            controller.log_path = root / "audit.csv"
            controller.before_files = before
            controller.root_folder = SimpleNamespace(get=lambda: str(root))
            controller.root = SimpleNamespace(after=lambda delay, callback: None)
            statuses = []
            events = []
            controller._log = lambda event, detail="": events.append((event, detail))
            controller._ui = lambda status=None: statuses.append(status)
            controller._close_os = lambda: None
            controller._stop_obs_verified = lambda: True

            def run_task(target):
                controller.busy = True
                target()

            controller._launch = run_task

            with patch.object(controller_module.time, "sleep"), patch.object(
                controller_module, "parse_xdf_counts",
                return_value={
                    controller_module.EDA_STREAM: 1,
                    controller_module.MARKER_STREAM: 1,
                    controller_module.OBS_STREAM: 1,
                },
            ):
                controller.stop()

        self.assertTrue(any("STOP NOT FULLY VERIFIED" in status for status in statuses if status))
        self.assertTrue(any("XDF verification incomplete" in detail for _, detail in events))
        self.assertFalse(any(event == "XDF_CHECK" for event, _ in events))

    def test_snapshot_failure_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "recording.xdf").write_bytes(b"recording")
            real_stat = Path.stat

            def fail_recording_stat(path, *args, **kwargs):
                if path.name == "recording.xdf":
                    raise PermissionError("file is not readable")
                return real_stat(path, *args, **kwargs)

            with patch.object(Path, "stat", fail_recording_stat):
                with self.assertRaises(PermissionError):
                    controller_module.saved_xdf_snapshot(root)

    def test_new_xdf_sort_uses_snapshot_metadata(self):
        new_xdf = Path("missing-after-snapshot.xdf")
        with patch.object(
            importlib.import_module("makerspace_controller.xdf"),
            "saved_xdf_snapshot",
            return_value={str(new_xdf): (10, 123)},
        ), patch.object(Path, "stat", side_effect=FileNotFoundError):
            self.assertEqual(
                [new_xdf],
                controller_module.new_xdfs_since_snapshot(".", {}),
            )


if __name__ == "__main__":
    unittest.main()
