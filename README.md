# Makerspace EDA and OBS Recording Controller

A Python desktop app for coordinating OpenSignals EDA recording, LabRecorder, and OBS. It writes a session activity log and checks the saved XDF file after recording stops.

## Download the project

Download the repository as a ZIP from GitHub (**Code > Download ZIP**) or clone it:

```powershell
git clone https://github.com/Damilola-0/eda-video-timestamp-alignment.git
cd eda-video-timestamp-alignment
```

Keep the `makerspace_controller` folder together with the launcher and test files in the repository root. Do not copy just the launcher by itself.

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
test_makerspace_recording_controller_obs27_parallel_stop.py  Automated tests
```

Python may create a `__pycache__` folder while running. It contains temporary files and is not required.

## Requirements

- Windows with Python 3 and Tkinter
- Python packages:

  ```powershell
  python -m pip install pylsl pyxdf obsws-python
  ```

- A connected PLUX device and OpenSignals configured with TCP-IP on port `5555` and LSL enabled
- LabRecorder running with its RCS interface on port `22345`
- OBS running with its WebSocket server enabled on port `4455`

Make sure these ports match the settings in your local OpenSignals, LabRecorder, and OBS installations. The controller's default LabRecorder Study Root is `Documents\CurrentStudy`; it must match the Study Root shown in LabRecorder. The OBS WebSocket password is entered in the app and is not saved.

## Run the tests

From the repository root:

```powershell
python -m unittest -v test_makerspace_recording_controller_obs27_parallel_stop.py
```

## Run a recording session

From the repository root:

```powershell
python makerspace_recording_controller_obs27_parallel_stop.py
```

1. Start and configure OpenSignals, LabRecorder, and OBS; connect the PLUX device.
2. Confirm LabRecorder's save location and Study Root. Stop any existing recordings.
3. Enter the OBS WebSocket password and click **CHECK CONNECTIONS**.
4. Click **START** and confirm the recording indicators in LabRecorder and OBS. An acknowledgement alone does not confirm that data is being recorded.
5. Use **SEND BEEP and LSL MARKER** to add a synchronization cue when needed.
6. Click **STOP**. Check the activity log for the XDF verification and confirm the recording completed as expected.

By default, the Study Root is `Documents\CurrentStudy`, and audit CSV logs are written to `Documents\Makerspace_Sync_Logs`. The default folders can be overridden with the `MAKERSPACE_STUDY_ROOT` and `MAKERSPACE_LOG_ROOT` environment variables.
