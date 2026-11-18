# Makerspace EDA and OBS Recording Controller
A Python desktop application to coordinate the recording of EDA with OpenSignals, LabRecorder, and OBS. It creates a session log file and checks the saved XDF file after recording is finished.


## Download the project

Download a ZIP file from GitHub (**Code > Download ZIP**) or clone the repository:

```powershell
git clone https://github.com/Damilola-0/eda-video-timestamp-alignment.git
cd eda-video-timestamp-alignment
```

Keep the `makerspace_controller` folder including the launcher and other root-level scripts. Do not separate the folder from the repository files.

## Project files

```text
makerspace_controller/
    app_icon.ico    Window icon
    config.py       Default ports, stream names, and output folders
    controller.py   Application window and recording workflow
    labrecorder.py  LabRecorder remote-control communication
    xdf.py          XDF file discovery and verification
    __init__.py

makerspace_recording_controller_obs27_parallel_stop.py  Application launcher
test_makerspace_recording_controller_obs27_parallel_stop.py
verify with official pyxdf.py                        Official PyXDF timestamp verifier
test_verify_with_official_pyxdf.py
Video_eda alignment.py                                EDA/video alignment
test_video_eda_alignment.py
```

Python may create a `__pycache__` folder while running. It contains temporary files and is not required.

## Requirements

- Windows with Python 3 and Tkinter
- Python packages for the recording controller:

  ```powershell
  python -m pip install pylsl pyxdf obsws-python
  ```

-A connected PLUX device and the OpenSignals app configured with TCP-IP on port 5555 and LSL enabled.
-LabRecorder with its RCS interface enabled and listening on port 22345.
-OBS with its WebSocket server enabled on port 4455.

These ports need to match the ones in your local OpenSignals, LabRecorder and OBS installations. The LabRecorder study root of the controller is set to DocumentsCurrentStudy by default, it needs to be the same as the Study Root of your LabRecorder. The password for the OBS WebSocket is entered into the app and is not stored.

## How to run the tests

From the repository root run:

```powershell
python -m unittest -v test_makerspace_recording_controller_obs27_parallel_stop.py
```

## Run a recording session

From the repository root:

```powershell
python makerspace_recording_controller_obs27_parallel_stop.py
```

1. Connect the PLUX device. Launch and configure OpenSignals, LabRecorder, and OBS.

2. Make sure LabRecorder is saving to the right location and that the Study Root is set. Stop any recordings that are currently in progress.

3. Enter the password for the OBS WebSocket and click CHECK CONNECTIONS.

4. Click START and make sure the recording indicators are turned on in both LabRecorder and OBS. Acknowledgement is not enough to ensure that data is being recorded.

5. Use the SEND BEEP and LSL MARKER buttons to add a synchronization cue when necessary.

6. Verify the XDF verification in the activity log. Click STOP.

The default Study Root is Documents\\CurrentStudy. Make sure the recording completed as expected.

Audit logs are written to Documents\\Makerspace_Sync_Logs. The default folders can be overridden with the MAKERSPACE_STUDY_ROOT and MAKERSPACE_LOG_ROOT environment variables.

## Verify EDA timestamps with official PyXDF

The PyXDF verifier will read in the selected XDF file three times.

The first time will be with raw timestamps, the second time will be with clock corrected timestamps, and the third time will be with clock corrected plus de-jittered timestamps. It will then check that the sample values and counts have not been changed, that the EDA sequence numbers are continuous, and that the processed timestamps are increasing. The input XDF file is read-only

Install the requirements and run the verifier from the repository root:

```powershell
python -m pip install pyxdf numpy
python "verify with official pyxdf.py"
```

Choose an XDF in the file dialog, or pass its path directly:

```powershell
python "verify with official pyxdf.py" "C:\path\to\recording.xdf"
```

The verifier currently expects exactly one EDA stream named `OpenSignals` and one `Makerspace_Sync_Marker` stream. The recording controller is configured to name its EDA stream `OpenSignals_EDA`, which this verifier does not currently accept.

Results are written beside the XDF in a `stamped_pyxdf_validation` folder:

- `official_pyxdf_validation_report.txt` summarizes sample continuity, sampling intervals, and timestamp shifts. Failed checks stop the script with an error.
- `eda_official_pyxdf_raw_vs_processed.csv` contains per-sample EDA values and raw, clock-corrected, and processed timestamps.
- `markers_official_pyxdf_clock_only.csv` contains marker timestamps before and after clock correction.

Running the verifier again for the same XDF overwrites these output files.

## Align an EDA recording with video

Install `pyxdf` and `numpy`, and make sure `ffmpeg` and `ffprobe` are available on `PATH`:

```powershell
python -m pip install pyxdf numpy
python "Video_eda alignment.py"
```

Choose the matching XDF and video when prompted. The video must contain the audible 900 Hz beeps corresponding to the XDF's `Makerspace_Sync_Marker` events. The script detects the beep tone relative to the recording's audio level rather than using a fixed amplitude threshold. It pairs beeps and markers chronologically and stops with an error if their counts do not match.

Results are written beside the selected XDF in a `<recording-name>_video_eda_alignment` folder, with a ZIP archive beside that folder. This is software-timestamp alignment; it does not measure physical sensor-to-camera latency.
