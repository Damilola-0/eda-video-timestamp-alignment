import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


SCRIPT = Path(__file__).with_name("Video_eda alignment.py")
SPEC = importlib.util.spec_from_file_location("video_eda_alignment", SCRIPT)
ALIGNMENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ALIGNMENT)


def _stream(name, timestamps, values):
    return {
        "info": {"name": [name]},
        "time_stamps": np.asarray(timestamps, dtype=float),
        "time_series": np.asarray(values),
    }


def _xdf_views(eda_name="OpenSignals_EDA"):
    values = np.column_stack(([101, 102, 103, 104, 105], [0.5, 0.6, 0.7, 0.8, 0.9]))
    raw = [
        _stream(eda_name, [98.9, 99.9, 100.9, 101.9, 102.9], values),
        _stream("Makerspace_Sync_Marker", [0.01, 2.01], [["SYNC_BEGIN"], ["SYNC_END"]]),
    ]
    clock = [
        _stream(eda_name, [99.0, 100.0, 101.0, 102.0, 103.0], values),
        _stream("Makerspace_Sync_Marker", [100.01, 102.01], [["SYNC_BEGIN"], ["SYNC_END"]]),
    ]
    processed = [
        _stream(eda_name, [99.0, 100.0, 101.0, 102.0, 103.0], values),
    ]
    return {
        (False, False): raw,
        (True, False): clock,
        (True, True): processed,
    }


class VideoEdaAlignmentTests(unittest.TestCase):
    def test_selects_xdf_then_matching_video(self):
        with (
            patch("tkinter.Tk") as tk_root,
            patch(
                "tkinter.filedialog.askopenfilename",
                side_effect=[
                    r"C:\recordings\session.xdf",
                    r"C:\recordings\session.mkv",
                ],
            ) as choose_file,
        ):
            selected = ALIGNMENT.select_input_files()

        self.assertEqual(
            (Path(r"C:\recordings\session.xdf"), Path(r"C:\recordings\session.mkv")),
            selected,
        )
        self.assertEqual(2, choose_file.call_count)
        tk_root.return_value.destroy.assert_called_once_with()

    def test_new_recording_with_dynamic_marker_and_frame_counts(self):
        fixture = _xdf_views()

        def fake_load_xdf(filename, **options):
            return fixture[
                (options["synchronize_clocks"], options["dejitter_timestamps"])
            ], {}

        with tempfile.TemporaryDirectory() as directory:
            xdf = Path(directory) / "new_session.xdf"
            video = Path(directory) / "new_session.mkv"
            xdf.touch()
            video.touch()
            with (
                patch.object(ALIGNMENT.pyxdf, "load_xdf", side_effect=fake_load_xdf),
                patch.object(
                    ALIGNMENT,
                    "probe_video",
                    return_value=(
                        np.array([0.0, 1.0, 2.0]),
                        np.array([1.0, 2.0, 3.0]),
                        3.0,
                        0.0,
                    ),
                ),
                patch.object(
                    ALIGNMENT,
                    "detect_beeps",
                    return_value=(
                        np.array([0.01, 2.01]),
                        np.array([0.23, 2.23]),
                        3.0,
                    ),
                ),
            ):
                output = ALIGNMENT.run_alignment(xdf, video)

            with (output / "marker_beep_alignment.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                marker_rows = list(csv.DictReader(file))
            with (output / "video_frames_aligned_to_eda.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                frame_rows = list(csv.DictReader(file))
            report = (output / "alignment_report.txt").read_text(encoding="utf-8")

            self.assertEqual(["SYNC_BEGIN", "SYNC_END"], [r["marker"] for r in marker_rows])
            self.assertEqual(3, len(frame_rows))
            self.assertIn("EDA samples: 5", report)
            self.assertIn("Synchronization markers / detected beeps: 2", report)
            self.assertTrue((Path(directory) / "new_session_video_eda_alignment.zip").is_file())

    def test_mismatched_marker_and_beep_counts_fail_clearly(self):
        fixture = _xdf_views()

        def fake_load_xdf(filename, **options):
            return fixture[
                (options["synchronize_clocks"], options["dejitter_timestamps"])
            ], {}

        with tempfile.TemporaryDirectory() as directory:
            xdf = Path(directory) / "session.xdf"
            video = Path(directory) / "session.mkv"
            xdf.touch()
            video.touch()
            with (
                patch.object(ALIGNMENT.pyxdf, "load_xdf", side_effect=fake_load_xdf),
                patch.object(
                    ALIGNMENT,
                    "probe_video",
                    return_value=(
                        np.array([0.0, 1.0]),
                        np.array([1.0, 2.0]),
                        2.0,
                        0.0,
                    ),
                ),
                patch.object(
                    ALIGNMENT,
                    "detect_beeps",
                    return_value=(np.array([0.01]), np.array([0.23]), 2.0),
                ),
                self.assertRaisesRegex(
                    ValueError, "Found 1 audio events but 2 synchronization markers"
                ),
            ):
                ALIGNMENT.run_alignment(xdf, video)

    def test_probe_video_normalizes_pts_to_video_start(self):
        packets = {
            "packets": [
                {"pts_time": "100.000", "duration_time": "0.100"},
                {"pts_time": "100.100", "duration_time": "0.100"},
                {"pts_time": "100.200", "duration_time": "0.100"},
            ]
        }
        streams = {
            "streams": [
                {"codec_type": "video", "start_time": "100.000", "duration": "0.300"},
                {"codec_type": "audio", "start_time": "100.020", "duration": "0.280"},
            ]
        }
        with patch.object(
            ALIGNMENT.subprocess,
            "check_output",
            side_effect=[json.dumps(packets), json.dumps(streams)],
        ):
            pts, ends, duration, audio_offset = ALIGNMENT.probe_video("clip.mkv")

        np.testing.assert_allclose(pts, [0.0, 0.1, 0.2])
        np.testing.assert_allclose(ends, [0.1, 0.2, 0.3])
        self.assertAlmostEqual(0.3, duration)
        self.assertAlmostEqual(0.02, audio_offset)

    def test_detect_beeps_from_decoded_audio(self):
        audio = np.zeros(4 * ALIGNMENT.AUDIO_SAMPLE_RATE, dtype="<f4")
        samples = np.arange(1760)
        audio[4000:5760] = 0.02 * np.sin(
            2 * np.pi * ALIGNMENT.BEEP_FREQUENCY_HZ * samples / ALIGNMENT.AUDIO_SAMPLE_RATE
        )
        audio[16000:17600] = 0.5 * np.sin(2 * np.pi * 500 * np.arange(1600) / ALIGNMENT.AUDIO_SAMPLE_RATE)
        audio[24000:25760] = 0.08 * np.sin(
            2 * np.pi * ALIGNMENT.BEEP_FREQUENCY_HZ * samples / ALIGNMENT.AUDIO_SAMPLE_RATE
        )
        with patch.object(ALIGNMENT.subprocess, "check_output", return_value=audio.tobytes()):
            onsets, ends, duration = ALIGNMENT.detect_beeps("clip.mkv")

        np.testing.assert_allclose(onsets, [0.5, 3.0])
        self.assertEqual(2, len(ends))
        self.assertAlmostEqual(4.0, duration)

    def test_legacy_opensignals_stream_name_is_supported(self):
        fixture = _xdf_views(eda_name="OpenSignals")

        def fake_load_xdf(filename, **options):
            return fixture[
                (options["synchronize_clocks"], options["dejitter_timestamps"])
            ], {}

        with patch.object(ALIGNMENT.pyxdf, "load_xdf", side_effect=fake_load_xdf):
            data = ALIGNMENT.load_xdf_data(Path("session.xdf"))

        self.assertEqual("OpenSignals", data["eda_stream"])
        self.assertEqual(5, len(data["eda_values"]))


if __name__ == "__main__":
    unittest.main()
