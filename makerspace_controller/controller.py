import csv
from concurrent.futures import ThreadPoolExecutor # Run OBS and OpenSignals concurrently
import json
import socket
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import (
    EDA_STREAM,
    HOST,
    LABRECORDER_PORT,
    LOG_ROOT,
    MARKER_STREAM,
    OBS_PORT,
    OBS_STREAM,
    OPENSIGNALS_PORT,
    REQUIRED_STREAMS,
    ROOT_DEFAULT,
)
from .labrecorder import tcp_command
from .xdf import new_xdfs_since_snapshot, parse_xdf_counts, saved_xdf_snapshot


class Controller:
    def __init__(self, root):
        self.session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = LOG_ROOT / ("recording_controller_" + self.session_tag + ".csv")

        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.root.title("Makerspace: EDA, LabRecorder and OBS Controller")
        self.root.iconbitmap(str(Path(__file__).with_name("app_icon.ico")))
        self.root.geometry("820x760")
        self.root.minsize(720, 640)

        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.root.configure(background="#F3F6FA")
        self.style.configure(".", font=("Segoe UI", 10), background="#F3F6FA", foreground="#24364B")
        self.style.configure("TFrame", background="#F3F6FA")
        self.style.configure("TLabel", background="#F3F6FA", foreground="#24364B")
        self.style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#173B63")
        self.style.configure("Subtitle.TLabel", foreground="#5B6B7D")
        self.style.configure(
            "Section.TLabelframe",
            background="#F3F6FA",
            bordercolor="#D5DFEA",
            padding=12,
        )
        self.style.configure(
            "Section.TLabelframe.Label",
            background="#F3F6FA",
            foreground="#173B63",
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure("TEntry", fieldbackground="#FFFFFF", padding=6)
        self.style.configure("TButton", padding=(10, 7), borderwidth=0)
        self.style.configure(
            "Primary.TButton",
            background="#1769AA",
            foreground="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
        )
        self.style.map(
            "Primary.TButton",
            background=[("disabled", "#D5DEE8"), ("active", "#12578F")],
            foreground=[("disabled", "#7B8794")],
        )
        self.style.configure("Cue.TButton", background="#DFECF8", foreground="#174A78")
        self.style.map("Cue.TButton", background=[("active", "#C9DDF0")])
        self.style.configure(
            "Stop.TButton",
            background="#B64242",
            foreground="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
        )
        self.style.map(
            "Stop.TButton",
            background=[("disabled", "#E6D6D6"), ("active", "#963535")],
            foreground=[("disabled", "#927D7D")],
        )
        self.style.configure(
            "Status.TLabel",
            background="#E7F1FB",
            foreground="#184A74",
            padding=(10, 8),
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "Hint.TLabel",
            background="#FFF8E7",
            foreground="#755719",
            padding=9,
        )
        self.style.configure("Audit.TLabel", foreground="#5B6B7D", font=("Segoe UI", 9))

        self.pylsl = None
        self.marker_outlet = None
        self.obs_module = None
        self.obs_conn = None
        self.open_conn = None
        self.os_start_attempted = False
        self.lr_start_attempted = False
        self.start_attempted = False
        self.busy = False
        self.recording = False
        self.marker_no = 0
        self.before_files = {}
        self.obs_video = ""
        self.root_folder = tk.StringVar(master=self.root, value=str(ROOT_DEFAULT))
        self.obs_password = tk.StringVar()
        self.status = tk.StringVar(value="Ready. Check LabRecorder save location before starting.")
        self.log_lock = threading.Lock()
        with self.log_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["event", "lsl_seconds", "unix_seconds", "local_datetime", "utc_datetime", "detail"])

        pane = ttk.Frame(root, padding=18)
        pane.pack(fill="both", expand=True)
        ttk.Label(pane, text="Makerspace recording controller", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            pane,
            text="Manage EDA, LabRecorder, and OBS recording from one window.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        setup_frame = ttk.LabelFrame(pane, text="Setup", style="Section.TLabelframe")
        setup_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(setup_frame, text="OBS WebSocket password (not saved):").pack(anchor="w", pady=(0, 3))
        ttk.Entry(setup_frame, show="*", textvariable=self.obs_password).pack(fill="x")
        ttk.Label(
            setup_frame,
            text="LabRecorder Study Root (must match the 'Study Root' shown in LabRecorder):",
        ).pack(anchor="w", pady=(10, 3))
        folder_line = ttk.Frame(setup_frame)
        folder_line.pack(fill="x")
        ttk.Entry(folder_line, textvariable=self.root_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(folder_line, text="Browse", command=self.choose_root).pack(side="left", padx=(6, 0))
        ttk.Label(
            setup_frame,
            text="Before starting: connect PLUX; enable OpenSignals TCP-IP 5555 and LSL; "
                 "start LabRecorder RCS 22345 and OBS WebSocket 4455. Stop any existing recordings.",
            style="Hint.TLabel",
            wraplength=740,
        ).pack(fill="x", pady=(10, 0))

        controls_frame = ttk.LabelFrame(pane, text="Recording controls", style="Section.TLabelframe")
        controls_frame.pack(fill="x", pady=(0, 10))
        self.check_button = ttk.Button(controls_frame, text="1. CHECK CONNECTIONS", command=self.check)
        self.check_button.pack(fill="x", pady=3)
        self.start_button = ttk.Button(
            controls_frame,
            text="2. START EDA, LABRECORDER and OBS",
            command=self.start,
            style="Primary.TButton",
        )
        self.start_button.pack(fill="x", pady=3)
        self.cue_button = ttk.Button(
            controls_frame,
            text="SEND BEEP and LSL MARKER",
            command=self.cue,
            style="Cue.TButton",
        )
        self.cue_button.pack(fill="x", pady=3)
        self.stop_button = ttk.Button(
            controls_frame,
            text="3. STOP ALL THREE and CHECK XDF",
            command=self.stop,
            style="Stop.TButton",
        )
        self.stop_button.pack(fill="x", pady=3)

        ttk.Label(pane, textvariable=self.status, style="Status.TLabel", wraplength=760).pack(fill="x", pady=(0, 10))
        log_frame = ttk.LabelFrame(pane, text="Activity log (also saved to CSV)", style="Section.TLabelframe")
        log_frame.pack(fill="both", expand=True, pady=(0, 10))
        self.log_area = tk.Text(
            log_frame,
            height=8,
            state="disabled",
            wrap="word",
            font=("Consolas", 9),
            background="#FFFFFF",
            foreground="#24364B",
            insertbackground="#24364B",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=8,
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_area.yview)
        self.log_area.configure(yscrollcommand=scrollbar.set)
        self.log_area.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(
            pane,
            text="LabRecorder's 'OK' acknowledges the command, not the data. For the first test, confirm "
                 "recording is active. After STOP, the controller checks the saved XDF for required "
                 "streams and nonzero samples.",
            style="Hint.TLabel",
            wraplength=760,
        ).pack(fill="x", pady=(0, 8))
        ttk.Label(pane, text=f"Audit CSV: {self.log_path}", style="Audit.TLabel", wraplength=760).pack(anchor="w")
        self._controls()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def choose_root(self):
        choice = filedialog.askdirectory(initialdir=self.root_folder.get())
        if choice:
            self.root_folder.set(choice)

    def _controls(self):
        self.check_button.configure(state="disabled" if self.busy or self.start_attempted else "normal")
        self.start_button.configure(state="disabled" if self.busy or self.start_attempted else "normal")
        self.cue_button.configure(state="normal" if self.recording and not self.busy else "disabled")
        self.stop_button.configure(state="normal" if self.start_attempted and not self.busy else "disabled")

    def _ui(self, status=None, line=None):
        def apply():
            if status is not None:
                self.status.set(status)
            if line:
                self.log_area.configure(state="normal")
                self.log_area.insert("end", line + "\n")
                self.log_area.see("end")
                self.log_area.configure(state="disabled")
            self._controls()
        self.root.after(0, apply)

    def _log(self, event, detail="", lsl_timestamp=None):
        wall = time.time()
        if lsl_timestamp is None:
            try:
                lsl_timestamp = self._lsl().local_clock()
            except Exception as exc:
                lsl_timestamp = None
                if detail:
                    detail = f"{detail}; LSL timestamp unavailable: {exc}"
                else:
                    detail = f"LSL timestamp unavailable: {exc}"
        now = datetime.fromtimestamp(wall, tz=timezone.utc)
        with self.log_lock, self.log_path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([event, "" if lsl_timestamp is None else f"{lsl_timestamp:.9f}",
                                    f"{wall:.9f}", now.astimezone().isoformat(timespec="milliseconds"),
                                    now.isoformat(timespec="milliseconds"), str(detail)])
        self._ui(line=f"{datetime.now().strftime('%H:%M:%S')}  {event}: {detail}")

    def _lsl(self):
        if self.pylsl is None:
            import pylsl
            self.pylsl = pylsl
        return self.pylsl

    def _marker(self):
        if self.marker_outlet is None:
            lsl = self._lsl()
            info = lsl.StreamInfo(MARKER_STREAM, "Markers", 1, 0, "string",
                                  "makerspace_controller_fixed_" + self.session_tag)
            self.marker_outlet = lsl.StreamOutlet(info)
            self._log("MARKER_STREAM_CREATED", MARKER_STREAM)

    def _open_os(self):
        if self.open_conn is not None:
            return
        self.open_conn = socket.create_connection((HOST, OPENSIGNALS_PORT), timeout=6)
        self.open_conn.settimeout(8)
        try:
            answer = self._send_os("devices")
            if not answer.get("returnData"):
                raise RuntimeError("OpenSignals reported no connected PLUX device")
        except Exception:
            self._close_os()
            raise

    def _close_os(self):
        if self.open_conn:
            try:
                self.open_conn.close()
            except OSError:
                pass
            self.open_conn = None

    def _send_os(self, cmd):
        if self.open_conn is None:
            raise RuntimeError("OpenSignals TCP connection is not active")
        self.open_conn.sendall((cmd + "\n").encode("utf-8"))
        self._log("OPENSIGNALS_SENT", cmd)
        data = ""
        decoder = json.JSONDecoder()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            packet = self.open_conn.recv(8192)
            if not packet:
                raise RuntimeError("OpenSignals connection closed after " + cmd)
            data += packet.decode("utf-8", "replace")
            for offset, char in enumerate(data):
                if char != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(data[offset:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and "returnCode" in value:
                    break
            else:
                data = data[-131072:]
                continue
            break
        else:
            raise RuntimeError("No complete OpenSignals response received after " + cmd)
        if value.get("returnCode") != 0:
            raise RuntimeError("OpenSignals rejected " + cmd + ": " + str(value))
        self._log("OPENSIGNALS_ACK", cmd)
        return value

    def _stop_opensignals_verified(self):
        """Send STOP to OpenSignals and require the documented acknowledgement.

        During acquisition, TCP data can be interleaved with command replies.
        Never equate sendall() with success: wait for the explicit JSON reply.
        """
        if self.open_conn is None:
            raise RuntimeError("OpenSignals TCP connection is closed; STOP was NOT confirmed")

        sock = self.open_conn
        sock.sendall(b"stop\n")
        self._log("OPENSIGNALS_STOP_SENT", "Waiting for acquisition-stop confirmation")

        deadline = time.monotonic() + 8.0
        data = b""
        decoder = json.JSONDecoder()
        old_timeout = sock.gettimeout()
        try:
            sock.settimeout(0.75)
            while time.monotonic() < deadline:
                try:
                    part = sock.recv(16384)
                except socket.timeout:
                    continue
                if not part:
                    break
                data += part
                # The OpenSignals TCP channel may also carry data. Scan each
                # complete JSON response for the documented STOP acknowledgement.
                decoded = data.decode("utf-8", errors="replace")
                for offset, char in enumerate(decoded):
                    if char != "{":
                        continue
                    try:
                        response, _ = decoder.raw_decode(decoded[offset:])
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(response, dict):
                        continue
                    reply = response.get("returnData", "")
                    if (response.get("returnCode") == 0
                            and isinstance(reply, str)
                            and "acquisition stopped" in reply.lower()):
                        self._log("OPENSIGNALS_STOP_CONFIRMED", str(response))
                        return response
                # Do not let stream samples accumulate without bound.
                data = data[-131072:]
        finally:
            try:
                sock.settimeout(old_timeout)
            except OSError:
                pass

        raise RuntimeError(
            "No 'Acquisition Stopped' acknowledgement received from OpenSignals. "
            "Check its acquisition screen and stop it manually if still running."
        )

    def _lr(self, command):
        answer = tcp_command(HOST, LABRECORDER_PORT, command)
        self._log("LABRECORDER_ACK", f"{command} => {answer.decode()}; this is NOT recording verification")
        return answer

    def _new_obs_client(self):
        if self.obs_module is None:
            import obsws_python
            self.obs_module = obsws_python
        return self.obs_module.ReqClient(host=HOST, port=OBS_PORT,
                                        password=self.obs_password.get(), timeout=8)

    def _obs(self):
        if self.obs_conn is None:
            self.obs_conn = self._new_obs_client()
        return self.obs_conn

    def _visible_streams(self):
        return [info.name() for info in self._lsl().resolve_streams(wait_time=1.5)]

    def _unexpected_streams(self, names):
        return [name for name in names if name not in REQUIRED_STREAMS]

    def _wait_streams(self, seconds=25):
        end = time.monotonic() + seconds
        last_names = []
        while time.monotonic() < end:
            last_names = self._visible_streams()
            if all(last_names.count(name) == 1 for name in REQUIRED_STREAMS):
                return last_names
            time.sleep(0.35)
        raise RuntimeError("Expected exactly one of each LSL stream (OpenSignals, Makerspace_Sync_Marker, OBS). Visible: " + str(last_names))

    def _beep_and_marker(self, label):
        lsl = self._lsl()
        marker_time = lsl.local_clock()
        self.marker_outlet.push_sample([label], timestamp=marker_time)
        self._log("SYNC_MARKER", label + "; LSL marker emitted just before beep", marker_time)
        # Beep latency matters when comparing it with the emitted marker.
        try:
            import winsound
            winsound.Beep(900, 220)
        except (ImportError, RuntimeError, OSError) as exc:
            self._log("BEEP_ERROR", str(exc))
            return False
        return True

    def _launch(self, target):
        if self.busy:
            return
        self.busy = True
        self._controls()
        threading.Thread(target=target, daemon=True).start()

    def check(self):
        def task():
            try:
                self._open_os()
                self._marker()
                self._lr("update")
                if self._obs().get_record_status().output_active:
                    raise RuntimeError("OBS is already recording")
                self._log("CHECK_PASSED", "Connections available; EDA acquisition has not started")
                self._ui("All three applications can be contacted. Ready for START.")
            except Exception as exc:
                self._log("CHECK_FAILED", str(exc))
                self._ui("Connection check failed. See log.")
            finally:
                self.busy = False
                self._ui()
        self._launch(task)

    def start(self):
        if self.start_attempted:
            return
        try:
            self.before_files = saved_xdf_snapshot(self.root_folder.get())
        except OSError as exc:
            self._log("XDF_SNAPSHOT_FAILED", str(exc))
            self._ui("Cannot start: the XDF folder could not be fully checked. See log.")
            return
        self.start_attempted = True
        def task():
            try:
                self._ui("Starting EDA and waiting for the LSL streams...")
                self._open_os()
                self._marker()
                if self._obs().get_record_status().output_active:
                    raise RuntimeError("OBS is already recording; please stop it before START")
                # The device may start even if its acknowledgment is lost.
                self.os_start_attempted = True
                self._send_os("start")
                names = self._wait_streams()
                unexpected = self._unexpected_streams(names)
                if unexpected:
                    raise RuntimeError("Other LSL streams are present, and LabRecorder 'select all' would include them: " + str(unexpected))
                self._log("LSL_READY", "Required LSL streams discovered: " + str(names))
                self._ui("EDA detected. Refreshing LabRecorder and selecting streams...")
                self._lr("update")
                time.sleep(3.5)  # LabRecorder update is asynchronous
                self._lr("select all")
                time.sleep(1.0)
                self._ui("Requesting LabRecorder recording. The ACK alone does not prove data capture.")
                # Do NOT send filename: LabRecorder's displayed Save Location is authoritative.
                # LabRecorder may start even if its acknowledgment is lost.
                self.lr_start_attempted = True
                self._lr("start")
                self._ui("LabRecorder START acknowledged. Starting OBS camera...")
                self._obs().start_record()
                # StartRecord acknowledges the command. GetRecordStatus can still
                # report the previous state for a short period after this request.
                # Do not treat one immediate status read as a failed START.
                self._log("OBS_START_ACK", "StartRecord accepted; confirm OBS GUI indicates recording")
                time.sleep(0.6)
                self._beep_and_marker("SYNC_BEGIN")
                self.recording = True
                self._log("START_COMMANDS_SENT", "OBS StartRecord accepted; OpenSignals accepted START; LabRecorder acknowledged START. Verify recording in both GUIs now.")
                self._ui("START COMMANDS SENT. Verify OBS recording indicator and LabRecorder STOP button. Send a cue, then STOP to verify the XDF.")
            except Exception as exc:
                self._log("START_INCOMPLETE", str(exc))
                self._ui("START INCOMPLETE. Read log. DO NOT click START again; use STOP ALL to finish this attempt.")
            finally:
                self.busy = False
                self._ui()
        self._launch(task)

    def cue(self):
        if not self.recording:
            return
        def task():
            try:
                self.marker_no += 1
                if self._beep_and_marker(f"SYNC_{self.marker_no:03d}"):
                    self._ui("Marker and beep sent. Confirm camera microphone captured the beep.")
                else:
                    self._ui("Marker sent, but beep failed. See log.")
            except Exception as exc:
                self._log("CUE_FAILED", f"Marker/beep not sent: {exc}")
                self._ui("Cue failed; no marker or beep sent. See log.")
            finally:
                self.busy = False
                self._ui()
        self._launch(task)

    def _wait_for_obs_inactive(self, client, attempts=20):
        for _ in range(attempts):
            if client.get_record_status().output_active is False:
                return True
            time.sleep(0.4)
        return False

    def _stop_obs_verified(self):
        """Request OBS STOP and verify saved video or independent inactive status.

        This is intentionally kept independent of the OpenSignals TCP socket so
        both STOP requests can be made without waiting for one another.
        """
        client = self._obs()
        obs_stopped = False
        stop_error = None
        try:
            self._log("OBS_STOP_SENT", "Requesting StopRecord while LabRecorder is still recording")
            response = client.stop_record()
            self.obs_video = str(getattr(response, "output_path", "") or "")
            self._log("OBS_STOP_ACK", "StopRecord succeeded; outputPath=" + repr(self.obs_video))
        except Exception as exc:
            stop_error = exc
            self._log("OBS_STOP_REQUEST_ERROR", str(exc))

        if stop_error is None and self.obs_video:
            for _ in range(20):
                file_path = Path(self.obs_video)
                if file_path.is_file() and file_path.stat().st_size > 0:
                    obs_stopped = True
                    self._log("OBS_STOP_CONFIRMED", "StopRecord acknowledged and saved video exists: " + self.obs_video)
                    break
                time.sleep(0.4)

        if not obs_stopped:
            fresh = self._new_obs_client()
            if self._wait_for_obs_inactive(fresh):
                obs_stopped = True
                self._log("OBS_STOP_CONFIRMED", "Independent OBS websocket reports output_active=False")
            elif stop_error:
                raise RuntimeError("StopRecord failed and a new connection still reports recording active: " + str(stop_error))
            else:
                raise RuntimeError("StopRecord returned, but neither a saved file nor an inactive status could be verified")
        return obs_stopped

    def _verify_xdf(self, errors):
        try:
            changed_files = new_xdfs_since_snapshot(self.root_folder.get(), self.before_files)
        except OSError as exc:
            errors.append("XDF verification incomplete: " + str(exc))
            self._log("XDF_SNAPSHOT_FAILED", str(exc))
            return

        if changed_files:
            for path in changed_files[:5]:
                try:
                    size = path.stat().st_size
                    counts = parse_xdf_counts(path)
                    ok = all(counts.get(name, 0) > 0 for name in REQUIRED_STREAMS)
                    self._log("XDF_CHECK", f"file={path} size={size} bytes streams={counts} required_present={ok}")
                    if not ok:
                        errors.append(f"XDF verification incomplete: required streams missing from {path}")
                except Exception as exc:
                    errors.append(f"XDF verification incomplete for {path}: {exc}")
                    self._log("XDF_READ_ERROR", f"{path}: {exc}")
            return

        detail = "No newly created XDF found under " + self.root_folder.get() + ". Check LabRecorder's 'Saving to...' path."
        errors.append("XDF verification incomplete: " + detail)
        self._log("XDF_NOT_LOCATED", detail)

    def stop(self):
        def task():
            errors = []
            os_stopped = False
            lr_stopped = False
            obs_stopped = False
            lr_should_stop = self.lr_start_attempted
            try:
                if self.recording:
                    self._beep_and_marker("SYNC_END")
            except Exception as exc:
                errors.append("Marker: " + str(exc))

            self._log("STOP_PARALLEL_BEGIN", "Requesting OBS and OpenSignals STOP without waiting for the other; LabRecorder will stop last")
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="makerspace_stop") as pool:
                obs_future = pool.submit(self._stop_obs_verified)
                os_future = pool.submit(self._stop_opensignals_verified) if self.os_start_attempted else None

                if os_future is None:
                    self._log("OPENSIGNALS_STOP_SKIPPED", "No START command was attempted by this controller")
                else:
                    try:
                        os_future.result()
                        os_stopped = True
                    except Exception as exc:
                        errors.append("OpenSignals STOP NOT CONFIRMED: " + str(exc))

                try:
                    obs_stopped = bool(obs_future.result())
                except Exception as exc:
                    errors.append("OBS STOP not confirmed: " + str(exc))

            try:
                if self.lr_start_attempted:
                    self._log("LABRECORDER_STOP_LAST", "Both source STOP operations have finished (possibly with warnings)")
                    self._lr("stop")
                    lr_stopped = True
                else:
                    self._log("LABRECORDER_STOP_SKIPPED", "No START command was attempted by this controller")
            except Exception as exc:
                errors.append("LabRecorder STOP not confirmed: " + str(exc))

            self._close_os()
            self.obs_conn = None
            self.recording = False
            self.os_start_attempted = self.lr_start_attempted = False
            self.start_attempted = False
            time.sleep(1.2)
            self._verify_xdf(errors)

            self._log("STOP_RESULTS", f"OpenSignals_confirmed={os_stopped}, LabRecorder_ACK={lr_stopped}, OBS_inactive={obs_stopped}")
            if errors:
                detail = "; ".join(errors)
                self._log("STOP_WARNINGS", detail)
                self._ui("STOP NOT FULLY VERIFIED. Check OpenSignals, LabRecorder and OBS. " + detail)
                self.root.after(0, lambda: messagebox.showwarning(
                    "Recording verification needs manual check",
                    "A recording stop or XDF verification was not confirmed.\n\n" + detail + "\n\nCheck all three applications and manually stop any that remain active."
                ))
            elif os_stopped and obs_stopped and (lr_stopped or not lr_should_stop):
                self._ui("STOP confirmed: OpenSignals and LabRecorder acknowledged STOP; OBS verified using its saved video or a fresh status check. Inspect the XDF summary in the log.")
            else:
                self._ui("STOP commands processed, but not all three stop states were independently confirmed. Check all applications.")
            self._log("SESSION_END", f"OBS video: {self.obs_video or '(not confirmed)'}; Audit log: {self.log_path}")
            self.busy = False
            self._ui()
        self._launch(task)

    def close(self):
        if self.start_attempted or self.busy:
            messagebox.showwarning("STOP first", "Press STOP ALL, and confirm applications stopped, before closing")
            return
        self._close_os()
        self.root.destroy()
