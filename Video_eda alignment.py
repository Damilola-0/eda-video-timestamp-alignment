"""Align an EDA XDF recording with its matching video.

Run the script and select the XDF and video files in the dialogs. PyXDF applies
clock synchronization and EDA de-jittering; FFmpeg detects the recorded beeps.
Requires: python -m pip install pyxdf numpy, plus ffmpeg and ffprobe on PATH.
"""
import csv
import json
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pyxdf


EDA_STREAM_NAMES = ("OpenSignals_EDA", "OpenSignals")
MARKER_STREAM_NAME = "Makerspace_Sync_Marker"
AUDIO_SAMPLE_RATE = 8000
BEEP_FREQUENCY_HZ = 900
BEEP_FRAME_SECONDS = 0.01
MIN_BEEP_TONE_FRACTION = 0.55
MIN_BEEP_DURATION_SECONDS = 0.12
MAX_BEEP_DURATION_SECONDS = 0.5
MAX_BEEP_FRAME_GAP_SECONDS = 0.08


def select_input_files():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        xdf = filedialog.askopenfilename(
            parent=root,
            title="Select the XDF recording",
            filetypes=(("XDF files", "*.xdf"), ("All files", "*.*")),
        )
        if not xdf:
            return None
        video = filedialog.askopenfilename(
            parent=root,
            title="Select the matching video",
            filetypes=(
                ("Video files", "*.mkv *.mp4 *.mov *.avi"),
                ("All files", "*.*"),
            ),
        )
    finally:
        root.destroy()
    if not video:
        return None
    return Path(xdf), Path(video)


def stream_name(stream):
    return stream["info"]["name"][0]


def find_stream(streams, names, description):
    matches = [stream for stream in streams if stream_name(stream) in names]
    if len(matches) != 1:
        available = [stream_name(stream) for stream in streams]
        raise ValueError(
            f"Expected one {description} stream; found {len(matches)}. "
            f"Available streams: {available}"
        )
    return matches[0]


def marker_label(sample):
    value = sample[0] if isinstance(sample, (list, tuple, np.ndarray)) else sample
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def load_xdf_data(xdf_path):
    raw_streams, _ = pyxdf.load_xdf(
        str(xdf_path), synchronize_clocks=False, dejitter_timestamps=False
    )
    clock_streams, _ = pyxdf.load_xdf(
        str(xdf_path), synchronize_clocks=True, dejitter_timestamps=False
    )
    processed_streams, _ = pyxdf.load_xdf(
        str(xdf_path), synchronize_clocks=True, dejitter_timestamps=True
    )

    raw_eda = find_stream(raw_streams, EDA_STREAM_NAMES, "EDA")
    clock_eda = find_stream(clock_streams, EDA_STREAM_NAMES, "EDA")
    processed_eda = find_stream(processed_streams, EDA_STREAM_NAMES, "EDA")
    raw_markers = find_stream(
        raw_streams, (MARKER_STREAM_NAME,), "synchronization marker"
    )
    clock_markers = find_stream(
        clock_streams, (MARKER_STREAM_NAME,), "synchronization marker"
    )

    raw_times = np.asarray(raw_eda["time_stamps"], dtype=float)
    clock_times = np.asarray(clock_eda["time_stamps"], dtype=float)
    processed_times = np.asarray(processed_eda["time_stamps"], dtype=float)
    raw_values = np.asarray(raw_eda["time_series"], dtype=float)
    processed_values = np.asarray(processed_eda["time_series"], dtype=float)
    if raw_values.ndim == 1:
        raw_values = raw_values[:, np.newaxis]
    if processed_values.ndim == 1:
        processed_values = processed_values[:, np.newaxis]
    if not (
        len(raw_times)
        == len(clock_times)
        == len(processed_times)
        == len(raw_values)
        == len(processed_values)
    ):
        raise ValueError("PyXDF changed the EDA sample count")
    if not np.array_equal(raw_values, processed_values):
        raise ValueError("EDA values changed during PyXDF timestamp processing")
    if processed_values.ndim != 2 or processed_values.shape[1] == 0:
        raise ValueError("EDA stream has no sample columns")
    if len(processed_times) < 2 or np.any(np.diff(processed_times) <= 0):
        raise ValueError("PyXDF EDA timestamps must be strictly increasing")

    if processed_values.shape[1] > 1:
        sequence_values = processed_values[:, 0]
        if not np.all(np.isfinite(sequence_values)) or not np.allclose(
            sequence_values, np.rint(sequence_values), rtol=0, atol=1e-6
        ):
            raise ValueError("The first EDA sample column is not an integer sequence")
        sequence = np.rint(sequence_values).astype(np.int64)
        eda_values = processed_values[:, 1]
    else:
        sequence = np.arange(len(processed_values), dtype=np.int64)
        eda_values = processed_values[:, 0]

    raw_marker_times = np.asarray(raw_markers["time_stamps"], dtype=float)
    marker_times = np.asarray(clock_markers["time_stamps"], dtype=float)
    marker_names = [marker_label(sample) for sample in raw_markers["time_series"]]
    clock_marker_names = [
        marker_label(sample) for sample in clock_markers["time_series"]
    ]
    if not (
        len(raw_marker_times)
        == len(marker_times)
        == len(marker_names)
        == len(clock_marker_names)
    ):
        raise ValueError("PyXDF changed the synchronization marker count")
    if marker_names != clock_marker_names:
        raise ValueError("PyXDF changed synchronization marker labels")
    if not marker_names:
        raise ValueError("The XDF contains no synchronization markers")

    order = np.argsort(marker_times, kind="stable")
    return {
        "eda_stream": stream_name(processed_eda),
        "raw_times": raw_times,
        "clock_times": clock_times,
        "processed_times": processed_times,
        "sequence": sequence,
        "eda_values": eda_values,
        "raw_marker_times": raw_marker_times[order],
        "marker_times": marker_times[order],
        "marker_names": [marker_names[index] for index in order],
    }


def optional_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe_video(video_path):
    packet_json = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_packets",
                "-show_entries",
                "packet=pts_time,duration_time",
                "-of",
                "json",
                str(video_path),
            ],
            text=True,
        )
    )
    packets = []
    for packet in packet_json["packets"]:
        pts = optional_float(packet.get("pts_time"))
        if pts is not None:
            packets.append((pts, optional_float(packet.get("duration_time"))))
    if not packets:
        raise ValueError(f"No video packet timestamps found in {video_path}")

    stream_json = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,start_time,duration",
                "-of",
                "json",
                str(video_path),
            ],
            text=True,
        )
    )
    video_streams = [
        stream for stream in stream_json["streams"] if stream["codec_type"] == "video"
    ]
    audio_streams = [
        stream for stream in stream_json["streams"] if stream["codec_type"] == "audio"
    ]
    if not video_streams:
        raise ValueError(f"No video stream found in {video_path}")
    if not audio_streams:
        raise ValueError(f"No audio stream found in {video_path}")

    video_start = optional_float(video_streams[0].get("start_time"))
    audio_start = optional_float(audio_streams[0].get("start_time"))
    order = np.argsort([packet[0] for packet in packets], kind="stable")
    packet_times = np.asarray([packets[index][0] for index in order], dtype=float)
    packet_durations = [packets[index][1] for index in order]
    if video_start is None:
        video_start = packet_times[0]
    if audio_start is None:
        audio_start = video_start
    pts = packet_times - video_start
    if np.any(np.diff(pts) <= 0):
        raise ValueError("Video packet presentation timestamps must be unique")

    last_duration = packet_durations[-1]
    if last_duration is not None and last_duration > 0:
        video_end = pts[-1] + last_duration
    else:
        stream_duration = optional_float(video_streams[0].get("duration"))
        if stream_duration is None or stream_duration <= pts[-1]:
            raise ValueError("Could not determine the final video frame duration")
        video_end = stream_duration
    if video_end <= pts[-1]:
        raise ValueError("Video duration does not extend beyond the final frame PTS")

    frame_ends = np.r_[pts[1:], video_end]
    return pts, frame_ends, video_end, audio_start - video_start


def detect_beeps(video_path):
    audio_bytes = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ]
    )
    audio = np.frombuffer(audio_bytes, dtype="<f4")
    if not len(audio):
        raise ValueError(f"No audio samples decoded from {video_path}")

    frame_size = round(AUDIO_SAMPLE_RATE * BEEP_FRAME_SECONDS)
    frame_count = len(audio) // frame_size
    if not frame_count:
        raise ValueError("Decoded audio is shorter than one beep-detection frame")
    frames = audio[:frame_count * frame_size].reshape(frame_count, frame_size)
    sample_indexes = np.arange(frame_size)
    phase = 2 * np.pi * BEEP_FREQUENCY_HZ * sample_indexes / AUDIO_SAMPLE_RATE
    cosine = np.cos(phase)
    sine = np.sin(phase)
    tone_amplitudes = np.empty(frame_count)
    tone_fractions = np.empty(frame_count)

    for start in range(0, frame_count, 10000):
        batch = frames[start:start + 10000]
        rms = np.sqrt(np.mean(batch * batch, axis=1))
        tone_amplitude = (2 / frame_size) * np.hypot(
            batch @ cosine, batch @ sine
        )
        tone_amplitudes[start:start + len(batch)] = tone_amplitude
        tone_fractions[start:start + len(batch)] = tone_amplitude / (
            np.sqrt(2) * rms + np.finfo(float).eps
        )

    amplitude_threshold = max(
        float(np.max(tone_amplitudes)) * 0.08,
        float(np.median(tone_amplitudes)) * 4,
        np.finfo(float).eps,
    )
    active = np.flatnonzero(
        (tone_amplitudes >= amplitude_threshold)
        & (tone_fractions >= MIN_BEEP_TONE_FRACTION)
    )
    if not len(active):
        raise ValueError(
            f"No {BEEP_FREQUENCY_HZ} Hz beep tones detected in the video audio"
        )

    max_gap = round(MAX_BEEP_FRAME_GAP_SECONDS / BEEP_FRAME_SECONDS)
    boundaries = np.flatnonzero(np.diff(active) > max_gap)
    first_frames = active[np.r_[0, boundaries + 1]]
    last_frames = active[np.r_[boundaries, len(active) - 1]]
    durations = (last_frames - first_frames + 1) * BEEP_FRAME_SECONDS
    valid = (
        (durations >= MIN_BEEP_DURATION_SECONDS)
        & (durations <= MAX_BEEP_DURATION_SECONDS)
    )
    first_frames = first_frames[valid]
    last_frames = last_frames[valid]
    if not len(first_frames):
        raise ValueError(
            f"No {BEEP_FREQUENCY_HZ} Hz tones had a plausible beep duration"
        )

    onsets = first_frames * BEEP_FRAME_SECONDS
    ends = (last_frames + 1) * BEEP_FRAME_SECONDS
    return onsets, ends, len(audio) / AUDIO_SAMPLE_RATE


def nearest_indices(sorted_times, targets):
    right = np.searchsorted(sorted_times, targets)
    right = np.clip(right, 0, len(sorted_times) - 1)
    left = np.clip(right - 1, 0, len(sorted_times) - 1)
    return np.where(
        np.abs(sorted_times[left] - targets)
        <= np.abs(sorted_times[right] - targets),
        left,
        right,
    )


def write_rows(path, headers, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


def run_alignment(xdf_path, video_path):
    xdf_path = Path(xdf_path)
    video_path = Path(video_path)
    if not xdf_path.is_file():
        raise FileNotFoundError(f"XDF does not exist: {xdf_path}")
    if not video_path.is_file():
        raise FileNotFoundError(f"Video does not exist: {video_path}")

    data = load_xdf_data(xdf_path)
    pts, frame_ends, video_duration, audio_start_offset = probe_video(video_path)
    beep_onsets, beep_ends, audio_duration = detect_beeps(video_path)
    beep_onsets = beep_onsets + audio_start_offset
    beep_ends = beep_ends + audio_start_offset
    marker_times = data["marker_times"]
    if len(beep_onsets) != len(marker_times):
        raise ValueError(
            f"Found {len(beep_onsets)} audio events but "
            f"{len(marker_times)} synchronization markers. "
            "Check that the selected XDF and video belong to the same recording."
        )

    offsets = marker_times - beep_onsets
    video_zero_lsl_offset = float(np.median(offsets))
    residuals_ms = (offsets - video_zero_lsl_offset) * 1000
    processed_times = data["processed_times"]
    sequence = data["sequence"]
    eda_values = data["eda_values"]

    frame_lsl_times = pts + video_zero_lsl_offset
    nearest_eda = nearest_indices(processed_times, frame_lsl_times)
    frame_begin_idx = np.searchsorted(
        processed_times, frame_lsl_times, side="left"
    )
    frame_end_idx = np.searchsorted(
        processed_times, frame_ends + video_zero_lsl_offset, side="left"
    )

    estimated_video_times = processed_times - video_zero_lsl_offset
    nearest_frame = nearest_indices(pts, estimated_video_times)
    inside_video = (
        (estimated_video_times >= pts[0])
        & (estimated_video_times < video_duration)
    )

    output_dir = xdf_path.parent / f"{xdf_path.stem}_video_eda_alignment"
    output_dir.mkdir(exist_ok=True)
    frame_file = output_dir / "video_frames_aligned_to_eda.csv"
    frame_rows = []
    frame_gaps = frame_ends - pts
    for index, frame_pts in enumerate(pts):
        sample_index = int(nearest_eda[index])
        start = frame_begin_idx[index]
        end = frame_end_idx[index]
        samples_in_frame = end - start
        frame_rows.append(
            [
                index,
                f"{frame_pts:.9f}",
                f"{frame_ends[index]:.9f}",
                f"{frame_gaps[index] * 1000:.3f}",
                str(bool(frame_gaps[index] > 0.05)).lower(),
                f"{frame_lsl_times[index]:.9f}",
                sample_index,
                int(sequence[sample_index]),
                f"{processed_times[sample_index]:.9f}",
                f"{eda_values[sample_index]:.9f}",
                int(samples_in_frame),
                int(sequence[start]) if samples_in_frame else "",
                int(sequence[end - 1]) if samples_in_frame else "",
                f"{np.mean(eda_values[start:end]):.9f}" if samples_in_frame else "",
            ]
        )
    write_rows(
        frame_file,
        [
            "video_frame_index_0based",
            "video_pts_s",
            "next_video_pts_or_end_s",
            "interval_to_next_pts_ms",
            "interval_gt_50ms",
            "estimated_frame_lsl_timestamp_s",
            "nearest_eda_sample_index_0based",
            "nearest_eda_sequence",
            "nearest_eda_processed_timestamp_s",
            "nearest_eda_value_as_recorded",
            "eda_samples_in_pts_window",
            "eda_window_first_sequence",
            "eda_window_last_sequence",
            "eda_window_mean_value_as_recorded",
        ],
        frame_rows,
    )

    eda_file = output_dir / "eda_samples_aligned_to_video.csv"
    eda_rows = []
    for index, timestamp in enumerate(processed_times):
        frame_index = int(nearest_frame[index])
        eda_rows.append(
            [
                index,
                int(sequence[index]),
                f"{eda_values[index]:.9f}",
                f"{data['raw_times'][index]:.9f}",
                f"{data['clock_times'][index]:.9f}",
                f"{timestamp:.9f}",
                f"{estimated_video_times[index]:.9f}",
                str(bool(inside_video[index])).lower(),
                frame_index if inside_video[index] else "",
                f"{pts[frame_index]:.9f}" if inside_video[index] else "",
                f"{(estimated_video_times[index] - pts[frame_index]) * 1000:.3f}"
                if inside_video[index]
                else "",
            ]
        )
    write_rows(
        eda_file,
        [
            "eda_sample_index_0based",
            "eda_sequence",
            "eda_value_as_recorded",
            "raw_lsl_timestamp_s",
            "clock_corrected_lsl_timestamp_s",
            "pyxdf_dejittered_lsl_timestamp_s",
            "estimated_video_time_s",
            "inside_video_duration",
            "nearest_video_frame_index_0based",
            "nearest_video_pts_s",
            "eda_minus_nearest_video_pts_ms",
        ],
        eda_rows,
    )

    marker_file = output_dir / "marker_beep_alignment.csv"
    marker_rows = [
        [
            name,
            f"{raw_time:.9f}",
            f"{corrected_time:.9f}",
            f"{beep_time:.9f}",
            f"{offset:.9f}",
            f"{residual:.3f}",
            f"{end_time:.9f}",
        ]
        for name, raw_time, corrected_time, beep_time, offset, residual, end_time in zip(
            data["marker_names"],
            data["raw_marker_times"],
            marker_times,
            beep_onsets,
            offsets,
            residuals_ms,
            beep_ends,
        )
    ]
    write_rows(
        marker_file,
        [
            "marker",
            "raw_lsl_timestamp_s",
            "clock_corrected_lsl_timestamp_s",
            "detected_video_beep_onset_s",
            "marker_minus_beep_offset_s",
            "residual_from_median_offset_ms",
            "detected_beep_end_s",
        ],
        marker_rows,
    )

    sample_intervals = np.diff(processed_times)
    video_intervals = np.diff(pts)
    sample_rate = 1 / float(np.median(sample_intervals))
    report_file = output_dir / "alignment_report.txt"
    report = (
        f"XDF: {xdf_path}\nVideo: {video_path}\n"
        f"EDA stream: {data['eda_stream']}\n"
        f"EDA samples: {len(processed_times)}\n"
        f"Estimated EDA sampling rate: {sample_rate:.6f} Hz\n"
        f"Synchronization markers / detected beeps: {len(marker_times)}\n"
        f"Video frames: {len(pts)}\nVideo duration: {video_duration:.6f} s\n"
        f"Decoded audio duration: {audio_duration:.6f} s\n"
        f"Median marker-minus-beep offset: {video_zero_lsl_offset:.9f} s\n"
        f"Marker/beep residual range: {np.ptp(residuals_ms):.6f} ms\n"
        f"Marker/beep residual RMS: "
        f"{np.sqrt(np.mean(residuals_ms ** 2)):.6f} ms\n"
        f"EDA samples outside video duration: "
        f"{int(np.sum(~inside_video))}\n"
        f"Video intervals over 50 ms: {int(np.sum(video_intervals > 0.05))}\n"
        f"Output directory: {output_dir}\n"
        "\nMethod: pair marker timestamps and detected audio beeps in chronological "
        "order; use their median offset to map video PTS onto the PyXDF EDA timeline.\n"
        f"Beep detection searches for {BEEP_FREQUENCY_HZ} Hz tones using "
        "relative tone strength and 10 ms audio frames.\n"
        "Limits: this aligns software timestamps and recorded audio. It does not "
        "measure physical EDA sensor or camera capture latency. Verify the "
        "detected events in marker_beep_alignment.csv before interpreting results.\n"
    )
    report_file.write_text(report, encoding="utf-8")

    zip_path = xdf_path.parent / f"{xdf_path.stem}_video_eda_alignment.zip"
    output_files = [frame_file, eda_file, marker_file, report_file]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in output_files + [Path(__file__)]:
            archive.write(path, arcname=path.name)

    print(report)
    print(f"ZIP: {zip_path}")
    return output_dir


def main():
    selected = select_input_files()
    if selected is None:
        return
    run_alignment(*selected)


if __name__ == "__main__":
    main()
