import os
from pathlib import Path

HOST = "localhost"
OPENSIGNALS_PORT = 5555
LABRECORDER_PORT = 22345
OBS_PORT = 4455

EDA_STREAM = "OpenSignals_EDA"
MARKER_STREAM = "Makerspace_Sync_Marker"
OBS_STREAM = "OBS"
REQUIRED_STREAMS = (EDA_STREAM, MARKER_STREAM, OBS_STREAM)

ROOT_DEFAULT = Path(os.environ.get(
    "MAKERSPACE_STUDY_ROOT",
    Path.home() / "Documents" / "CurrentStudy",
))
LOG_ROOT = Path(os.environ.get(
    "MAKERSPACE_LOG_ROOT",
    Path.home() / "Documents" / "Makerspace_Sync_Logs",
))
