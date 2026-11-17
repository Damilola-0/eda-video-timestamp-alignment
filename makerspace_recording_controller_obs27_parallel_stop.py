"""Launch the Makerspace EDA, LabRecorder, and OBS recording controller."""
import tkinter as tk

from makerspace_controller.config import (
    EDA_STREAM,
    HOST,
    LABRECORDER_PORT,
    LOG_ROOT,
    MARKER_STREAM,
    OBS_PORT,
    OBS_STREAM,
    REQUIRED_STREAMS,
    ROOT_DEFAULT,
)
from makerspace_controller.controller import Controller
from makerspace_controller.labrecorder import tcp_command
from makerspace_controller.xdf import (
    new_xdfs_since_snapshot,
    parse_xdf_counts,
    saved_xdf_snapshot,
)

if __name__ == "__main__":
    app_root = tk.Tk()
    Controller(app_root)
    app_root.mainloop()
