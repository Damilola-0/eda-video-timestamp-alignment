import csv
import hashlib
import io
import runpy
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyxdf


SCRIPT = Path(__file__).with_name("verify with official pyxdf.py")
REAL_XDF = (
    Path.home()
    / "Documents"
    / "CurrentStudy"
    / "sub-P001"
    / "ses-S001"
    / "eeg"
    / "sub-P001_ses-S001_task-Default_run-001_eeg.xdf"
)


def _stream(name, timestamps, values):
    return {
        "info": {"name": [name]},
        "time_stamps": np.asarray(timestamps, dtype=float),
        "time_series": values,
    }


def _fixture_results(sequence=(100, 101, 102), omit=()):
    values = np.column_stack((sequence, [1.25, 1.5, 2.0]))
    results = {
        (False, False): [
            _stream("OpenSignals", [10.0, 10.01, 10.02], values.copy()),
            _stream("Makerspace_Sync_Marker", [10.001, 10.019], [["SYNC_BEGIN"], ["SYNC_END"]]),
        ],
        (True, False): [
            _stream("OpenSignals", [20.0, 20.01, 20.02], values.copy()),
            _stream("Makerspace_Sync_Marker", [20.002, 20.018], [["SYNC_BEGIN"], ["SYNC_END"]]),
        ],
        (True, True): [
            _stream("OpenSignals", [20.0, 20.0101, 20.0202], values.copy()),
            _stream("Makerspace_Sync_Marker", [20.002, 20.018], [["SYNC_BEGIN"], ["SYNC_END"]]),
        ],
    }
    return {
        settings: [stream for stream in streams if stream["info"]["name"][0] not in omit]
        for settings, streams in results.items()
    }


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_verifier(source):
    with patch.object(sys, "argv", [str(SCRIPT), str(source)]), redirect_stdout(io.StringIO()):
        runpy.run_path(str(SCRIPT))


def _run_verifier_with_file_dialog():
    with patch.object(sys, "argv", [str(SCRIPT)]), redirect_stdout(io.StringIO()):
        runpy.run_path(str(SCRIPT))


class VerifierMockedBehaviorTests(unittest.TestCase):
    """Controlled-data tests; pyxdf.load_xdf is mocked."""

    def test_no_argument_selects_xdf_and_processes_it(self):
        fixture = _fixture_results()
        selected_source = None

        def fake_load_xdf(filename, **kwargs):
            self.assertEqual(str(selected_source), filename)
            key = (kwargs["synchronize_clocks"], kwargs["dejitter_timestamps"])
            return fixture[key], {}

        with tempfile.TemporaryDirectory() as directory:
            selected_source = Path(directory) / "selected.xdf"
            selected_source.write_bytes(b"controlled test input")
            with (
                patch("tkinter.Tk") as tk_root,
                patch(
                    "tkinter.filedialog.askopenfilename",
                    return_value=str(selected_source),
                ) as choose_file,
                patch.object(pyxdf, "load_xdf", side_effect=fake_load_xdf),
            ):
                _run_verifier_with_file_dialog()

            tk_root.return_value.withdraw.assert_called_once_with()
            tk_root.return_value.destroy.assert_called_once_with()
            choose_file.assert_called_once_with(
                parent=tk_root.return_value,
                title="Select an XDF recording",
                filetypes=(("XDF files", "*.xdf"), ("All files", "*.*")),
            )
            output = selected_source.parent / "stamped_pyxdf_validation"
            self.assertTrue((output / "official_pyxdf_validation_report.txt").is_file())

    def test_canceling_file_dialog_exits_without_loading(self):
        with (
            patch("tkinter.Tk"),
            patch("tkinter.filedialog.askopenfilename", return_value=""),
            patch.object(pyxdf, "load_xdf") as loader,
            self.assertRaises(SystemExit) as exit_info,
        ):
            _run_verifier_with_file_dialog()
        self.assertEqual(0, exit_info.exception.code)
        loader.assert_not_called()

    def test_processing_modes_exports_and_report_metrics(self):
        fixture = _fixture_results()
        calls = []

        def fake_load_xdf(filename, **kwargs):
            calls.append((filename, kwargs))
            key = (kwargs["synchronize_clocks"], kwargs["dejitter_timestamps"])
            return fixture[key], {}

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.xdf"
            source.write_bytes(b"controlled test input")
            source_hash = _sha256(source)

            with patch.object(pyxdf, "load_xdf", side_effect=fake_load_xdf):
                _run_verifier(source)

            self.assertEqual(
                [
                    (str(source), {"synchronize_clocks": False, "dejitter_timestamps": False}),
                    (str(source), {"synchronize_clocks": True, "dejitter_timestamps": False}),
                    (str(source), {"synchronize_clocks": True, "dejitter_timestamps": True}),
                ],
                calls,
            )

            output = source.parent / "stamped_pyxdf_validation"
            with (output / "eda_official_pyxdf_raw_vs_processed.csv").open(
                newline="", encoding="utf-8"
            ) as data_file:
                data_rows = list(csv.DictReader(data_file))
            with (output / "markers_official_pyxdf_clock_only.csv").open(
                newline="", encoding="utf-8"
            ) as marker_file:
                marker_rows = list(csv.DictReader(marker_file))

            self.assertEqual(["100", "101", "102"], [row["OpenSignals_sequence"] for row in data_rows])
            self.assertEqual(["1.25", "1.5", "2"], [row["EDA_value_as_recorded"] for row in data_rows])
            self.assertEqual(
                ["10.000000000", "10.010000000", "10.020000000"],
                [row["raw_lsl_timestamp_s"] for row in data_rows],
            )
            self.assertEqual(
                ["20.000000000", "20.010000000", "20.020000000"],
                [row["clock_corrected_timestamp_s"] for row in data_rows],
            )
            self.assertEqual(
                ["20.000000000", "20.010100000", "20.020200000"],
                [row["pyxdf_clock_and_dejitter_timestamp_s"] for row in data_rows],
            )
            self.assertEqual(
                ["-0.002000000", "0.008100000", "0.018200000"],
                [row["processed_time_since_SYNC_BEGIN_s"] for row in data_rows],
            )
            self.assertEqual(
                ["-10000.00000", "-10000.10000", "-10000.20000"],
                [row["raw_minus_processed_ms"] for row in data_rows],
            )
            self.assertEqual(
                ["SYNC_BEGIN", "SYNC_END"],
                [row["marker"] for row in marker_rows],
            )
            self.assertEqual(
                ["20.002000000", "20.018000000"],
                [row["clock_corrected_timestamp_s"] for row in marker_rows],
            )
            self.assertEqual(
                ["10.001000000", "10.019000000"],
                [row["raw_timestamp_s"] for row in marker_rows],
            )
            self.assertEqual(
                ["0.000000000", "0.016000000"],
                [row["time_since_SYNC_BEGIN_s"] for row in marker_rows],
            )

            raw = np.array([10.0, 10.01, 10.02])
            clock = np.array([20.0, 20.01, 20.02])
            processed = np.array([20.0, 20.0101, 20.0202])
            report = (output / "official_pyxdf_validation_report.txt").read_text(encoding="utf-8")
            self.assertIn(f"Samples: {len(processed)}", report)
            self.assertIn("EDA seq: 100..102, continuous", report)
            self.assertIn(
                f"Fitted processed sampling rate: {(len(processed) - 1) / (processed[-1] - processed[0]):.9f} Hz",
                report,
            )
            self.assertIn(f"Raw interval median: {np.median(np.diff(raw)) * 1000:.6f} ms", report)
            self.assertIn(
                f"Processed interval median: {np.median(np.diff(processed)) * 1000:.6f} ms",
                report,
            )
            self.assertIn(
                f"Raw vs processed RMS shift: {np.sqrt(np.mean(((raw - processed) * 1000) ** 2)):.6f} ms",
                report,
            )
            self.assertIn(
                f"Clock-only max absolute shift: {np.max(np.abs(raw - clock)) * 1000:.6f} ms",
                report,
            )
            self.assertIn(
                f"Nominal 100Hz vs fitted duration difference: "
                f"{(processed[-1] - processed[0] - (len(processed) - 1) / 100) * 1000:.6f} ms",
                report,
            )
            self.assertEqual(source_hash, _sha256(source))

    def test_missing_file_fails_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.xdf"
            with patch.object(pyxdf, "load_xdf") as loader:
                with self.assertRaisesRegex(SystemExit, "XDF does not exist"):
                    _run_verifier(missing)
            loader.assert_not_called()

    def test_missing_expected_streams_fail(self):
        for missing_name in ("OpenSignals", "Makerspace_Sync_Marker"):
            with self.subTest(stream=missing_name), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "input.xdf"
                source.write_bytes(b"controlled test input")
                fixture = _fixture_results(omit=(missing_name,))

                def fake_load_xdf(filename, **kwargs):
                    key = (kwargs["synchronize_clocks"], kwargs["dejitter_timestamps"])
                    return fixture[key], {}

                with patch.object(pyxdf, "load_xdf", side_effect=fake_load_xdf):
                    with self.assertRaisesRegex(RuntimeError, f"Expected exactly one {missing_name}"):
                        _run_verifier(source)

    def test_eda_sequence_gap_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.xdf"
            source.write_bytes(b"controlled test input")
            fixture = _fixture_results(sequence=(100, 101, 103))

            def fake_load_xdf(filename, **kwargs):
                key = (kwargs["synchronize_clocks"], kwargs["dejitter_timestamps"])
                return fixture[key], {}

            with patch.object(pyxdf, "load_xdf", side_effect=fake_load_xdf):
                with self.assertRaisesRegex(AssertionError, "EDA sequence gap"):
                    _run_verifier(source)


@unittest.skipUnless(REAL_XDF.is_file(), f"No real XDF available at {REAL_XDF}")
class VerifierRealPyXDFIntegrationTests(unittest.TestCase):
    """Integration test using the installed, unmocked PyXDF package."""

    def test_real_xdf_runs_and_input_hash_is_unchanged(self):
        original_hash = _sha256(REAL_XDF)
        real_load_xdf = pyxdf.load_xdf
        calls = []

        def tracked_load_xdf(filename, **kwargs):
            calls.append(kwargs.copy())
            return real_load_xdf(filename, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "integration_input.xdf"
            shutil.copy2(REAL_XDF, source)
            source_hash = _sha256(source)

            with patch.object(pyxdf, "load_xdf", side_effect=tracked_load_xdf):
                _run_verifier(source)

            self.assertEqual(
                [
                    {"synchronize_clocks": False, "dejitter_timestamps": False},
                    {"synchronize_clocks": True, "dejitter_timestamps": False},
                    {"synchronize_clocks": True, "dejitter_timestamps": True},
                ],
                calls,
            )
            output = source.parent / "stamped_pyxdf_validation"
            self.assertTrue((output / "eda_official_pyxdf_raw_vs_processed.csv").is_file())
            self.assertTrue((output / "markers_official_pyxdf_clock_only.csv").is_file())
            self.assertTrue((output / "official_pyxdf_validation_report.txt").is_file())
            self.assertEqual(source_hash, _sha256(source))

        self.assertEqual(original_hash, _sha256(REAL_XDF))


if __name__ == "__main__":
    unittest.main()
