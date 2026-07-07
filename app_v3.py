"""
app_v2.py — GRATMA v2.0.0 Desktop Application (tkinter + matplotlib)

Improvements v2.0.0:
    - Automatic ping (active handshake with firmware)
    - Ping-pong connection status indicator
    - Differential measurement at two points
    - Manual measurement with custom points
    - Real-time graph streaming
    - Improved VS/VG control (high-level and raw)
    - Reorganized UI for better usability

Requirements:
    pip install pyusb matplotlib

Run:
    python app_v2.py
"""
import csv
import math
import os
import queue
import threading
import time
from datetime import datetime
from tkinter import (
    BOTH, DISABLED, END, INSERT, NORMAL, X, Y, BooleanVar, Canvas, Frame,
    Label, StringVar, Text, Tk, Button, filedialog, messagebox, ttk, Toplevel,
)

import usb.core as _usb_core
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from gratma_usb import (
    DeviceStatus,
    GratmaDeviceBusy,
    GratmaError,
    GratmaUSB,
    RecordType,
)

# ---------------------------------------------------------------------------
# Color palette (industrial dark theme)
# ---------------------------------------------------------------------------
BG     = "#1e1e2e"
BG2    = "#2a2a3d"
ACCENT = "#7c9fd4"
FG     = "#cdd6f4"
FG_DIM = "#6c7086"
GREEN  = "#a6e3a1"
RED    = "#f38ba8"
ORANGE = "#fab387"
YELLOW = "#f9e2af"
CYAN   = "#89dceb"
MAGENTA= "#cba6f7"
PAD    = 6

PING_INTERVAL_MS = 2500  # Send PING every 2.5 seconds

# Base color for each sensor (dark tones; lighter variants are
# generated with _tint to distinguish forward/backward and phase 1/phase 2)
SENSOR_COLORS = [
    "#2f9e44",  # S1 green
    "#1c7ed6",  # S2 blue
    "#f76707",  # S3 orange
    "#e03131",  # S4 red
    "#ae3ec9",  # S5 purple
    "#1098ad",  # S6 cyan
    "#f59f00",  # S7 yellow
    "#d6336c",  # S8 pink
]


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class GratmaApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("GRATMA v2.0.0 — Control & Measurement")
        self.root.configure(bg=BG)
        self.root.minsize(1100, 750)

        self._dev: GratmaUSB | None = None
        self._busy = False
        self._rq: queue.Queue = queue.Queue()
        self._scan_results: list[dict] = []
        self._sweep_records: list[dict] = []
        self._idt_records: list[dict] = []
        self._diff_records_phase1: list[dict] = []
        self._diff_records_phase2: list[dict] = []
        self._last_meas_data: dict | None = None  # Last completed measurement (for export)
        self._continuous_job: str | None = None
        self._ping_job: str | None = None
        self._ping_active = False
        self._realtime_job: str | None = None
        self._abort_measurement = False  # Flag to abort measurements

        # Accumulated points from the current measurement, for the real-time
        # graph on the Measurements tab. Key: (phase, sensor) where phase is
        # 0 (simple measurement) or 1/2 (differential). Value: ordered list of
        # (x, y, backward) tuples in arrival order — kept in arrival order (not
        # split by direction) so that _render_meas_plot can break the drawn
        # line exactly where the sweep switches forward <-> backward.
        self._rt_series: dict[tuple[int, int], list[tuple[float, float, bool]]] = {}
        self._rt_kind = "iv"  # iv | idt | differential | manual

        self._apply_style()
        self._build_ui()
        self._update_conn_state(connected=False)
        self._poll()

    # -----------------------------------------------------------------------
    # ttk Style
    # -----------------------------------------------------------------------
    def _apply_style(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        base = dict(
            background=BG, foreground=FG,
            fieldbackground=BG2, troughcolor=BG2,
            bordercolor=BG2, darkcolor=BG2, lightcolor=BG2,
            selectbackground=ACCENT, selectforeground=BG,
            font=("Segoe UI", 9),
        )
        s.configure(".", **base)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=FG_DIM,
                    padding=[14, 5])
        s.map("TNotebook.Tab",
              background=[("selected", ACCENT)],
              foreground=[("selected", BG)])
        s.configure("TLabelframe", background=BG, foreground=FG_DIM)
        s.configure("TLabelframe.Label", background=BG, foreground=FG_DIM,
                    font=("Segoe UI", 8))
        s.configure("TEntry", fieldbackground=BG2, foreground=FG,
                    insertcolor=FG)
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.configure("TCombobox", fieldbackground=BG2, foreground=FG,
                    selectbackground=BG2, selectforeground=FG)
        s.configure("TScrollbar", background=BG2, arrowcolor=FG_DIM)
        s.configure("Horizontal.TProgressbar",
                    background=ACCENT, troughcolor=BG2, borderwidth=0)
        s.configure("TRadiobutton", background=BG, foreground=FG)

    # -----------------------------------------------------------------------
    # Main Layout
    # -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_conn_bar()

        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill=BOTH, expand=True, padx=PAD, pady=(2, PAD))

        self._build_tab_connection()
        self._build_tab_measurements()
        self._build_tab_gratma_control()

        self._build_log()
        self._scan_devices()

    # -----------------------------------------------------------------------
    # Connection Bar (top)
    # -----------------------------------------------------------------------
    def _build_conn_bar(self) -> None:
        bar = Frame(self.root, bg=BG2, pady=7)
        bar.pack(fill=X)

        self._conn_led = Label(bar, text="●", font=("Segoe UI", 13),
                               bg=BG2, fg=RED)
        self._conn_led.pack(side="left", padx=(12, 4))

        self._conn_label = Label(bar, text="Disconnected",
                                 bg=BG2, fg=FG_DIM, font=("Segoe UI", 9))
        self._conn_label.pack(side="left", padx=(0, 8))

        # Ping-pong indicator
        self._ping_led = Label(bar, text="○", font=("Segoe UI", 10),
                               bg=BG2, fg=FG_DIM)
        self._ping_led.pack(side="left", padx=(8, 4))

        self._ping_label = Label(bar, text="Ping-Pong: Inactive",
                                 bg=BG2, fg=FG_DIM, font=("Segoe UI", 8))
        self._ping_label.pack(side="left", padx=(0, 12))

        self._btn_connect = Button(
            bar, text="Connect", width=13, command=self._on_connect,
            bg=ACCENT, fg=BG, relief="flat",
            activebackground=FG, activeforeground=BG,
            font=("Segoe UI", 9, "bold"),
        )
        self._btn_connect.pack(side="left", padx=4)

        # Device selector
        Label(bar, text="  Device:", bg=BG2, fg=FG_DIM,
              font=("Segoe UI", 9)).pack(side="left", padx=(8, 4))
        self._dev_combo_var = StringVar()
        self._dev_combo = ttk.Combobox(
            bar, textvariable=self._dev_combo_var,
            state="readonly", width=36, font=("Segoe UI", 8),
        )
        self._dev_combo.pack(side="left", padx=(0, 2))
        self._btn_scan = Button(
            bar, text="⟳", width=3, command=self._on_scan,
            bg=BG, fg=FG_DIM, relief="flat",
            activebackground=BG2, activeforeground=FG,
            font=("Segoe UI", 10),
        )
        self._btn_scan.pack(side="left", padx=(0, 8))

        Label(bar, text="  Status:", bg=BG2, fg=FG_DIM,
              font=("Segoe UI", 9)).pack(side="left", padx=(16, 4))
        self._dev_status_lbl = Label(bar, text="—", bg=BG2, fg=FG_DIM,
                                     font=("Segoe UI", 9, "bold"))
        self._dev_status_lbl.pack(side="left")

        # Progress bar (right side)
        self._progress = ttk.Progressbar(
            bar, mode="indeterminate", length=120,
            style="Horizontal.TProgressbar",
        )
        self._progress.pack(side="right", padx=(0, 16))

    # -----------------------------------------------------------------------
    # Tab 1: Connection
    # -----------------------------------------------------------------------
    def _build_tab_connection(self) -> None:
        tab = Frame(self._nb, bg=BG)
        self._nb.add(tab, text="  Connection  ")

        # Connection status
        conn_frame = ttk.LabelFrame(tab, text="Connection Status", padding=16)
        conn_frame.pack(fill=X, padx=PAD * 3, pady=(PAD * 2, PAD))

        info_grid = Frame(conn_frame, bg=BG)
        info_grid.pack(fill=X)

        labels = [
            ("USB Device:", "_info_device"),
            ("Bus/Address:", "_info_bus"),
            ("Firmware Rev:", "_info_fw"),
            ("Serial Number:", "_info_serial"),
            ("System Status:", "_info_system_status"),
            ("USB Mode:", "_info_usb_mode"),
        ]

        for i, (lbl, attr) in enumerate(labels):
            Label(info_grid, text=lbl, bg=BG, fg=FG_DIM,
                  font=("Segoe UI", 9), anchor="e", width=18).grid(
                row=i, column=0, sticky="e", padx=(0, 12), pady=4)
            var = StringVar(value="—")
            setattr(self, attr, var)
            Label(info_grid, textvariable=var, bg=BG, fg=YELLOW,
                  font=("Consolas", 10), anchor="w").grid(
                row=i, column=1, sticky="w", pady=4)

        # Manual ping
        ping_frame = ttk.LabelFrame(tab, text="Communication Test", padding=16)
        ping_frame.pack(fill=X, padx=PAD * 3, pady=PAD)

        self._btn_ping = Button(
            ping_frame, text="▶  Manual Ping", command=self._on_ping,
            bg=GREEN, fg=BG, relief="flat",
            activebackground=FG, activeforeground=BG,
            font=("Segoe UI", 10, "bold"), state=DISABLED, width=20,
        )
        self._btn_ping.pack(pady=8)

        Label(ping_frame, text="Automatic ping runs every 2.5s when connected",
              bg=BG, fg=FG_DIM, font=("Segoe UI", 8)).pack(pady=(0, 4))

    # -----------------------------------------------------------------------
    # Tab 2: Measurements
    # -----------------------------------------------------------------------
    def _make_scrollable_panel(self, parent, width: int) -> Frame:
        """Creates a fixed-width panel with vertical scrolling and returns the inner
        frame where to place controls."""
        outer = Frame(parent, bg=BG, width=width)
        outer.pack(side="left", fill=Y, padx=(PAD, 0), pady=PAD)
        outer.pack_propagate(False)

        canvas = Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill=Y)
        canvas.pack(side="left", fill=BOTH, expand=True)

        inner = Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        # Keep scrollregion at content size and inner frame width equal to canvas.
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Mouse wheel (only when pointer is over this panel)
        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        return inner

    def _build_tab_measurements(self) -> None:
        tab = Frame(self._nb, bg=BG)
        self._nb.add(tab, text="  Measurements  ")

        # Left panel: scrollable so all controls are accessible
        # even if the window is small.
        left = self._make_scrollable_panel(tab, width=320)

        # Type selector
        type_frame = ttk.LabelFrame(left, text="Measurement Type", padding=12)
        type_frame.pack(fill=X, pady=(0, 6))

        self._meas_type = StringVar(value="iv_parametric")
        types = [
            ("iv_parametric", "I-V Parametric"),
            ("idt", "I vs Time (IDT)"),
            ("differential", "Differential Two Points"),
        ]

        for val, lbl in types:
            ttk.Radiobutton(type_frame, text=lbl, variable=self._meas_type,
                            value=val, command=self._on_meas_type_change).pack(
                anchor="w", pady=2)

        # Sample identification and output folder (common to all types)
        self._build_identification(left)

        # Sensor selection and mode (common to all types)
        self._build_sensor_selection(left)

        # Parameter frames (shown/hidden according to type)
        self._meas_params_frame = Frame(left, bg=BG)
        self._meas_params_frame.pack(fill=BOTH, expand=True, pady=(0, 6))

        self._build_meas_params()

        # Start and stop buttons
        btn_frame = Frame(left, bg=BG)
        btn_frame.pack(fill=X, pady=(0, 6))
        
        self._btn_start_meas = Button(
            btn_frame, text="▶  Start", command=self._on_start_measurement,
            bg=GREEN, fg=BG, relief="flat",
            activebackground=FG, activeforeground=BG,
            font=("Segoe UI", 10, "bold"), state=DISABLED, height=2,
        )
        self._btn_start_meas.pack(side="left", fill=X, expand=True, padx=(0, 3))
        
        self._btn_stop_meas = Button(
            btn_frame, text="⬛  STOP", command=self._on_stop_measurement,
            bg=RED, fg=BG, relief="flat",
            activebackground=FG, activeforeground=BG,
            font=("Segoe UI", 10, "bold"), state=DISABLED, height=2,
        )
        self._btn_stop_meas.pack(side="left", fill=X, expand=True)

        # Result
        res_frame = ttk.LabelFrame(left, text="Result", padding=10)
        res_frame.pack(fill=X, pady=(0, 6))
        self._meas_result_lbl = Label(res_frame, text="—", bg=BG, fg=YELLOW,
                                      font=("Consolas", 14, "bold"))
        self._meas_result_lbl.pack(anchor="w")

        # Export
        self._btn_export_meas = Button(
            left, text="Export CSV", command=self._on_export_measurement,
            bg=BG2, fg=FG, relief="flat",
            font=("Segoe UI", 9), state=DISABLED,
        )
        self._btn_export_meas.pack(fill=X)

        # Right panel: Real-time graph
        right = Frame(tab, bg=BG)
        right.pack(side="left", fill=BOTH, expand=True, padx=PAD, pady=PAD)

        Label(right, text="Preview (Real-time)", bg=BG, fg=ACCENT,
              font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))

        self._meas_fig = Figure(facecolor=BG, figsize=(7, 5))
        self._meas_ax = self._meas_fig.add_subplot(111)
        self._setup_axes(self._meas_ax, "Ongoing Measurement", "$V_G$ (V)", "$I_{DS}$ (µA)")

        self._meas_canvas = FigureCanvasTkAgg(self._meas_fig, master=right)
        self._meas_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        tb = NavigationToolbar2Tk(self._meas_canvas, right, pack_toolbar=False)
        tb.update()
        tb.pack(fill=X)
        self._style_toolbar(tb)

    def _build_identification(self, parent) -> None:
        """Sample name, extra text and output folder (common to all measurements)."""
        frame = ttk.LabelFrame(parent, text="Identification / Output", padding=10)
        frame.pack(fill=X, pady=(0, 6))

        Label(frame, text="Sample ID:", bg=BG, fg=FG_DIM,
              font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=2, padx=(0, 8))
        self._sample_name = StringVar(value="sample")
        ttk.Entry(frame, textvariable=self._sample_name, width=16).grid(
            row=0, column=1, columnspan=2, sticky="we", pady=2)

        Label(frame, text="Extra:", bg=BG, fg=FG_DIM,
              font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=2, padx=(0, 8))
        self._extra_text = StringVar(value="")
        ttk.Entry(frame, textvariable=self._extra_text, width=16).grid(
            row=1, column=1, columnspan=2, sticky="we", pady=2)

        Label(frame, text="Folder:", bg=BG, fg=FG_DIM,
              font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=2, padx=(0, 8))
        self._out_folder = StringVar(value="")
        ttk.Entry(frame, textvariable=self._out_folder, width=16).grid(
            row=2, column=1, sticky="we", pady=2)
        Button(frame, text="…", width=2, command=self._on_pick_folder,
               bg=BG2, fg=FG, relief="flat").grid(row=2, column=2, padx=(4, 0))

        Label(frame, text="CSVs are saved automatically when finished\n"
              "as <sample>_<sensor>_<type>_<rep>_<extra>.csv",
              bg=BG, fg=FG_DIM, font=("Segoe UI", 7, "italic"), justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        frame.grid_columnconfigure(1, weight=1)

    def _build_sensor_selection(self, parent) -> None:
        """Checkboxes for sensors 1-8 + sequential/parallel mode (common to I-V and IDT)."""
        frame = ttk.LabelFrame(parent, text="Sensors", padding=10)
        frame.pack(fill=X, pady=(0, 6))

        self._sensor_vars: list[BooleanVar] = []
        checks = Frame(frame, bg=BG)
        checks.pack(fill=X)
        for i in range(8):
            var = BooleanVar(value=(i == 0))  # S1 checked by default
            self._sensor_vars.append(var)
            ttk.Checkbutton(checks, text=f"S{i + 1}", variable=var).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=2)

        mode_row = Frame(frame, bg=BG)
        mode_row.pack(fill=X, pady=(6, 0))
        self._sensor_mode = StringVar(value="sequential")
        ttk.Radiobutton(mode_row, text="Sequential", variable=self._sensor_mode,
                        value="sequential").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(mode_row, text="Parallel", variable=self._sensor_mode,
                        value="parallel").pack(side="left")
        Label(frame, text="Parallel: all sensors at once. Sequential: one after another.",
              bg=BG, fg=FG_DIM, font=("Segoe UI", 7, "italic")).pack(anchor="w", pady=(2, 0))

    def _selected_sensors(self) -> list[int]:
        """List of selected sensors (1-based)."""
        return [i + 1 for i, v in enumerate(self._sensor_vars) if v.get()]

    def _build_meas_params(self) -> None:
        """Builds parameter frames for each measurement type"""
        # Parametric I-V frame (also used by differential)
        self._params_iv_param = ttk.LabelFrame(self._meas_params_frame,
                                               text="I-V Parameters", padding=10)
        self._iv_vs = self._entry_row(self._params_iv_param, "VS (mV):", 100, 0)
        self._iv_vg_start = self._entry_row(self._params_iv_param, "VG start (mV):", 0, 1)
        self._iv_vg_end = self._entry_row(self._params_iv_param, "VG end (mV):", 1200, 2)
        self._iv_vg_step = self._entry_row(self._params_iv_param, "VG step (mV):", 50, 3)
        self._iv_reps = self._entry_row(self._params_iv_param, "Repetitions:", 1, 4)
        self._iv_reverse = BooleanVar(value=False)
        ttk.Checkbutton(self._params_iv_param, text="Reverse sweep",
                        variable=self._iv_reverse).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(6, 2))

        # IDT frame
        self._params_idt = ttk.LabelFrame(self._meas_params_frame,
                                          text="IDT Parameters", padding=10)
        self._idt_vg = self._entry_row(self._params_idt, "VG (mV):", 600, 0)
        self._idt_vs = self._entry_row(self._params_idt, "VS (mV):", 100, 1)
        self._idt_total = self._entry_row(self._params_idt, "Duration (s):", 10, 2)
        self._idt_period = self._entry_row(self._params_idt, "Period (s):", 1, 3)

        # Differential frame: reuses I-V parameters (same in both phases)
        self._params_diff = Frame(self._meas_params_frame, bg=BG)
        Label(self._params_diff, text="Two phases (baseline / with sample), each is\n"
              "an I-V with the parameters above.\n"
              "Result: ΔVG = VG_min2 - VG_min1",
              bg=BG, fg=FG_DIM, font=("Segoe UI", 9), justify="left").pack(pady=(12, 4))
        Label(self._params_diff, text="⚠ Requires manual intervention between phases",
              bg=BG, fg=ORANGE, font=("Segoe UI", 8, "italic")).pack(pady=(0, 8))

        # Show IV Parametric frame by default
        self._on_meas_type_change()

    def _on_meas_type_change(self) -> None:
        """Shows/hides parameter frames according to selected type"""
        # Hide all
        for frame in [self._params_iv_param, self._params_idt, self._params_diff]:
            frame.pack_forget()

        # Show selected (differential reuses the I-V panel)
        meas_type = self._meas_type.get()
        if meas_type == "iv_parametric":
            self._params_iv_param.pack(fill=X, pady=6)
        elif meas_type == "idt":
            self._params_idt.pack(fill=X, pady=6)
        elif meas_type == "differential":
            self._params_iv_param.pack(fill=X, pady=6)
            self._params_diff.pack(fill=X, pady=6)

    def _on_pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="Output folder for CSV files")
        if folder:
            self._out_folder.set(folder)

    # -----------------------------------------------------------------------
    # Tab 3: GRATMA Control
    # -----------------------------------------------------------------------
    def _build_tab_gratma_control(self) -> None:
        tab = Frame(self._nb, bg=BG)
        self._nb.add(tab, text="  GRATMA  ")

        # Left panel: Controls
        left = Frame(tab, bg=BG, width=380)
        left.pack(side="left", fill=Y, padx=(PAD, 0), pady=PAD)
        left.pack_propagate(False)

        # High-Level VS/VG Control
        hl_frame = ttk.LabelFrame(left, text="High-Level Control (VS / VG)", padding=12)
        hl_frame.pack(fill=X, pady=(0, 8))

        Label(hl_frame, text="VS (mV):", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        self._hl_vs = StringVar(value="0")
        ttk.Entry(hl_frame, textvariable=self._hl_vs, width=12).grid(
            row=0, column=1, pady=4, sticky="w")
        self._btn_set_vs = Button(
            hl_frame, text="Set VS", command=self._on_set_vs,
            bg=ACCENT, fg=BG, relief="flat", font=("Segoe UI", 9), state=DISABLED)
        self._btn_set_vs.grid(row=0, column=2, padx=(8, 0))

        Label(hl_frame, text="VG (mV):", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        self._hl_vg = StringVar(value="0")
        ttk.Entry(hl_frame, textvariable=self._hl_vg, width=12).grid(
            row=1, column=1, pady=4, sticky="w")
        self._btn_set_vg = Button(
            hl_frame, text="Set VG", command=self._on_set_vg,
            bg=ACCENT, fg=BG, relief="flat", font=("Segoe UI", 9), state=DISABLED)
        self._btn_set_vg.grid(row=1, column=2, padx=(8, 0))

        Label(hl_frame, text="Note: The firmware calculates the necessary DAC values",
              bg=BG, fg=FG_DIM, font=("Segoe UI", 7, "italic")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # Raw DAC Control
        dac_frame = ttk.LabelFrame(left, text="Raw DAC Control", padding=12)
        dac_frame.pack(fill=X, pady=(0, 8))

        Label(dac_frame, text="DAC:", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=0, column=0, padx=4, sticky="w")
        self._dac_idx = StringVar(value="0 (VG)")
        ttk.Combobox(dac_frame, textvariable=self._dac_idx, width=10,
                     values=["0 (VG)", "1 (VS)"], state="readonly").grid(
            row=0, column=1, padx=4)

        Label(dac_frame, text="Channel:", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=0, column=2, padx=4, sticky="w")
        self._dac_out = StringVar(value="0")
        ttk.Combobox(dac_frame, textvariable=self._dac_out, width=5,
                     values=["0", "1"], state="readonly").grid(
            row=0, column=3, padx=4)

        Label(dac_frame, text="mV:", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=0, column=4, padx=4, sticky="w")
        self._dac_mv = StringVar(value="0")
        ttk.Entry(dac_frame, textvariable=self._dac_mv, width=8).grid(
            row=0, column=5, padx=4)

        self._btn_set_voltage = Button(
            dac_frame, text="Set Voltage", command=self._on_set_voltage,
            bg=ACCENT, fg=BG, relief="flat", font=("Segoe UI", 9), state=DISABLED)
        self._btn_set_voltage.grid(row=0, column=6, padx=(12, 0))

        # Switch Control
        sw_frame = ttk.LabelFrame(left, text="Switch Control (MAX14662)", padding=12)
        sw_frame.pack(fill=X, pady=(0, 8))

        Label(sw_frame, text="Switch:", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=0, column=0, padx=4, sticky="w")
        self._sw_idx = StringVar(value="0")
        ttk.Combobox(sw_frame, textvariable=self._sw_idx, width=5,
                     values=["0", "1"], state="readonly").grid(
            row=0, column=1, padx=4)

        Label(sw_frame, text="Map (hex):", bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=0, column=2, padx=4, sticky="w")
        self._sw_map_var = StringVar(value="0xFF")
        ttk.Entry(sw_frame, textvariable=self._sw_map_var, width=8).grid(
            row=0, column=3, padx=4)

        self._btn_set_switch = Button(
            sw_frame, text="Set Switch", command=self._on_set_switch,
            bg=ACCENT, fg=BG, relief="flat", font=("Segoe UI", 9), state=DISABLED)
        self._btn_set_switch.grid(row=0, column=4, padx=(12, 0))

        # Status
        stat_frame = ttk.LabelFrame(left, text="System Status", padding=12)
        stat_frame.pack(fill=X, pady=(0, 8))

        self._btn_get_status = Button(
            stat_frame, text="Read Status", command=self._on_get_status,
            bg=BG2, fg=FG, relief="flat", font=("Segoe UI", 9), state=DISABLED)
        self._btn_get_status.pack(side="left", padx=(0, 14))

        self._man_status_lbl = Label(stat_frame, text="—", bg=BG, fg=YELLOW,
                                     font=("Segoe UI", 11, "bold"))
        self._man_status_lbl.pack(side="left")

        # Right panel: Instrument readings
        right = Frame(tab, bg=BG)
        right.pack(side="left", fill=BOTH, expand=True, padx=PAD, pady=PAD)

        Label(right, text="Instrument Readings (INA228)", bg=BG, fg=ACCENT,
              font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        ctrl = Frame(right, bg=BG)
        ctrl.pack(fill=X, pady=(0, 12))

        self._btn_instr_refresh = Button(
            ctrl, text="↺  Read All", command=self._on_read_instruments,
            bg=ACCENT, fg=BG, relief="flat",
            font=("Segoe UI", 9, "bold"), state=DISABLED)
        self._btn_instr_refresh.pack(side="left", padx=(0, 14))

        self._instr_continuous = BooleanVar(value=False)
        ttk.Checkbutton(ctrl, text="Continuous", variable=self._instr_continuous,
                        command=self._on_continuous_toggle).pack(side="left")

        Label(ctrl, text="  Interval (s):", bg=BG, fg=FG_DIM,
              font=("Segoe UI", 9)).pack(side="left")
        self._instr_interval = StringVar(value="1.0")
        ttk.Entry(ctrl, textvariable=self._instr_interval, width=6).pack(
            side="left", padx=(4, 0))

        # Grid de lecturas
        grid = Frame(right, bg=BG2, padx=20, pady=16)
        grid.pack(fill=X)

        READINGS = [
            ("ina0_vbus", "INA228[0] Vbus:", "V"),
            ("ina1_vbus", "INA228[1] Vbus:", "V"),
            ("ina0_vshunt", "INA228[0] Vshunt:", "µV"),
            ("ina1_vshunt", "INA228[1] Vshunt:", "µV"),
            ("ina0_is", "INA228[0] IS (50Ω):", "A"),
            ("ina1_is", "INA228[1] IS (200Ω):", "A"),
            ("ina0_temp", "INA228[0] Temp:", "°C"),
        ]

        self._instr_vars: dict[str, StringVar] = {}

        for i, (key, lbl, unit) in enumerate(READINGS):
            row = i // 2
            col = (i % 2) * 4

            Label(grid, text=lbl, bg=BG2, fg=FG_DIM, font=("Segoe UI", 9),
                  anchor="e").grid(row=row, column=col, sticky="e",
                                   padx=(0, 8), pady=7)

            var = StringVar(value="—")
            self._instr_vars[key] = var
            Label(grid, textvariable=var, bg=BG2, fg=YELLOW,
                  font=("Consolas", 12, "bold"), width=14, anchor="w").grid(
                row=row, column=col + 1, sticky="w")

            Label(grid, text=unit, bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).grid(
                row=row, column=col + 2, sticky="w", padx=(0, 32))

        self._instr_ts_lbl = Label(right, text="", bg=BG, fg=FG_DIM,
                                   font=("Segoe UI", 8))
        self._instr_ts_lbl.pack(anchor="w", pady=(8, 0))

    # -----------------------------------------------------------------------
    # Log Panel
    # -----------------------------------------------------------------------
    def _build_log(self) -> None:
        frm = Frame(self.root, bg=BG2, height=140)
        frm.pack(fill=X)
        frm.pack_propagate(False)

        header = Frame(frm, bg=BG2)
        header.pack(fill=X, padx=8, pady=(4, 0))

        Label(header, text="Log / Debug Console", bg=BG2, fg=FG_DIM,
              font=("Segoe UI", 9, "bold")).pack(side="left")

        self._btn_clear_log = Button(
            header, text="Clear", command=self._on_clear_log,
            bg=BG, fg=FG_DIM, relief="flat", font=("Segoe UI", 8))
        self._btn_clear_log.pack(side="right")

        self._log = Text(frm, bg=BG2, fg=FG_DIM,
                         font=("Consolas", 8), relief="flat",
                         state=DISABLED, wrap="word")
        sb = ttk.Scrollbar(frm, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill=Y, pady=(0, 4))
        self._log.pack(fill=BOTH, expand=True, padx=8, pady=(0, 4))

    # -----------------------------------------------------------------------
    # UI Helpers
    # -----------------------------------------------------------------------
    def _entry_row(self, parent, label: str, default: int, row: int) -> StringVar:
        Label(parent, text=label, bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
            row=row, column=0, sticky="w", pady=2, padx=(0, 8))
        var = StringVar(value=str(default))
        ttk.Entry(parent, textvariable=var, width=12).grid(
            row=row, column=1, sticky="w", pady=2)
        return var

    def _setup_axes(self, ax, title: str, xlabel: str, ylabel: str) -> None:
        ax.set_facecolor(BG2)
        for spine in ax.spines.values():
            spine.set_edgecolor(FG_DIM)
        ax.tick_params(colors=FG_DIM, labelsize=8)
        ax.xaxis.label.set_color(FG_DIM)
        ax.yaxis.label.set_color(FG_DIM)
        ax.title.set_color(FG)
        ax.title.set_fontsize(10)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(True, color=BG, linewidth=0.5, alpha=0.6)

    @staticmethod
    def _style_toolbar(toolbar) -> None:
        toolbar.config(background=BG2)
        for w in toolbar.winfo_children():
            try:
                w.config(background=BG2, foreground=FG,
                         activebackground=BG, activeforeground=FG)
            except Exception:
                pass

    def _log_write(self, msg: str, color: str = FG_DIM) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log.config(state=NORMAL)
        start = self._log.index(INSERT)
        self._log.insert(END, f"[{ts}] {msg}\n")
        end = self._log.index(INSERT)
        tag = f"fg_{color.replace('#', '')}"
        if tag not in self._log.tag_names():
            self._log.tag_config(tag, foreground=color)
        self._log.tag_add(tag, start, end)
        self._log.see(END)
        self._log.config(state=DISABLED)

    def _on_clear_log(self) -> None:
        self._log.config(state=NORMAL)
        self._log.delete("1.0", END)
        self._log.config(state=DISABLED)

    def _update_conn_state(self, connected: bool) -> None:
        if connected:
            self._conn_led.config(fg=GREEN)
            self._conn_label.config(text="Connected  ", fg=GREEN)
            self._btn_connect.config(text="Disconnect", bg=RED, fg=BG)
            self._dev_combo.config(state=DISABLED)
            self._btn_scan.config(state=DISABLED)
            # Start automatic ping
            self._start_ping_loop()
        else:
            self._conn_led.config(fg=RED)
            self._conn_label.config(text="Disconnected", fg=FG_DIM)
            self._btn_connect.config(text="Connect", bg=ACCENT, fg=BG)
            self._dev_status_lbl.config(text="—", fg=FG_DIM)
            self._dev_combo.config(state="readonly")
            self._btn_scan.config(state=NORMAL)
            # Stop automatic ping
            self._stop_ping_loop()
            # Reset info
            self._info_device.set("—")
            self._info_bus.set("—")
            self._info_fw.set("—")
            self._info_serial.set("—")
            self._info_system_status.set("—")
            self._info_usb_mode.set("—")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        connected = self._dev is not None
        btn_state = NORMAL if (connected and not busy) else DISABLED

        # Buttons that require connection
        for btn in [self._btn_ping, self._btn_start_meas, self._btn_instr_refresh,
                    self._btn_set_voltage, self._btn_set_switch, self._btn_get_status,
                    self._btn_set_vs, self._btn_set_vg]:
            btn.config(state=btn_state)
        
        # STOP button: active only when a measurement is in progress
        self._btn_stop_meas.config(state=NORMAL if (connected and busy) else DISABLED)

        self._btn_connect.config(state=DISABLED if busy else NORMAL)

        if busy:
            self._progress.start(10)
        else:
            self._progress.stop()

    # -----------------------------------------------------------------------
    # Automatic PING System (ping-pong)
    # -----------------------------------------------------------------------
    def _start_ping_loop(self) -> None:
        """Starts the automatic ping loop every 2.5 seconds"""
        if self._ping_job is None and self._dev is not None:
            self._log_write("Automatic ping started (every 2.5s)", CYAN)
            self._ping_loop()

    def _stop_ping_loop(self) -> None:
        """Stops the automatic ping loop"""
        if self._ping_job is not None:
            self.root.after_cancel(self._ping_job)
            self._ping_job = None
            self._ping_active = False
            self._update_ping_indicator(False)
            self._log_write("Automatic ping stopped", FG_DIM)

    def _ping_loop(self) -> None:
        """Ping loop: sends ALWAYS, even during measurements"""
        if self._dev is None:
            self._ping_job = None
            return

        # CRITICAL: Ping must run ALWAYS, even during measurements
        # Otherwise, firmware returns to USB_CONSOLE after 5s
        threading.Thread(target=self._ping_background, daemon=True).start()

        self._ping_job = self.root.after(PING_INTERVAL_MS, self._ping_loop)

    def _ping_background(self) -> None:
        """Sends ping in background without blocking UI"""
        try:
            if self._dev:
                self._dev.ping()
                self._rq.put(("ping_ok", None))
        except Exception as e:
            self._rq.put(("ping_fail", str(e)))

    def _update_ping_indicator(self, active: bool) -> None:
        """Updates the visual ping-pong indicator"""
        if active:
            self._ping_led.config(fg=GREEN, text="●")
            self._ping_label.config(text="Ping-Pong: ACTIVE (USB_VENDOR)", fg=GREEN)
            self._info_usb_mode.set("USB_VENDOR (app active)")
        else:
            self._ping_led.config(fg=ORANGE, text="○")
            self._ping_label.config(text="Ping-Pong: Inactive", fg=ORANGE)
            self._info_usb_mode.set("USB_CONSOLE")

    # -----------------------------------------------------------------------
    # Background Threading Infrastructure
    # -----------------------------------------------------------------------
    def _run_async(self, func, *args) -> None:
        def _wrapper():
            try:
                func(*args)
            except GratmaDeviceBusy as e:
                self._rq.put(("log_warn", f"Device busy: {e}"))
                self._rq.put(("busy_off", None))
            except GratmaError as e:
                self._rq.put(("log_error", f"Error GRATMA: {e}"))
                self._rq.put(("busy_off", None))
            except _usb_core.USBError as e:
                errno = getattr(e, 'errno', '?')
                self._rq.put(("log_error",
                    f"Error USB (errno={errno}): {e}\n"
                    "  → Reconnect device if persists."))
                self._rq.put(("busy_off", None))
            except Exception as e:
                self._rq.put(("log_error", f"{type(e).__name__}: {e}"))
                self._rq.put(("busy_off", None))

        threading.Thread(target=_wrapper, daemon=True).start()

    def _poll(self) -> None:
        """Processes messages from background threads every 100 ms"""
        try:
            while True:
                msg, data = self._rq.get_nowait()
                self._dispatch(msg, data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _dispatch(self, msg: str, data) -> None:
        match msg:
            case "log":
                self._log_write(data, FG_DIM)
            case "log_ok":
                self._log_write(data, GREEN)
            case "log_warn":
                self._log_write(data, ORANGE)
            case "log_error":
                self._log_write(data, RED)
            case "busy_off":
                self._set_busy(False)
            case "connected":
                self._update_conn_state(True)
                self._log_write("GRATMA device connected", GREEN)
                # Update device info
                if data:
                    self._info_device.set("GRATMA")
                    self._info_bus.set(f"{data['bus']} / {data['address']}")
                    self._info_fw.set(f"0x{data['bcdDevice']:04X}")
                    self._info_serial.set(data['serial'] or "N/A")
            case "disconnected":
                self._instr_continuous.set(False)
                if self._continuous_job:
                    self.root.after_cancel(self._continuous_job)
                    self._continuous_job = None
                self._update_conn_state(False)
                self._log_write("Device disconnected", FG_DIM)
            case "ping_ok":
                self._ping_active = True
                self._update_ping_indicator(True)
            case "ping_fail":
                self._ping_active = False
                self._update_ping_indicator(False)
                self._log_write(f"Ping failed: {data}", RED)
            case "dev_status":
                _map = {
                    DeviceStatus.IDLE: ("IDLE", GREEN),
                    DeviceStatus.SWEEPING: ("SWEEPING", ORANGE),
                    DeviceStatus.ERROR: ("ERROR", RED),
                }
                text, color = _map.get(data, (f"0x{data:02X}", FG_DIM))
                self._dev_status_lbl.config(text=text, fg=color)
                self._man_status_lbl.config(text=text, fg=color)
                self._info_system_status.set(text)
                self._set_busy(False)
            case "instruments":
                self._update_instruments(data)
                self._set_busy(False)
            case "meas_done":
                self._on_measurement_complete(data)
            case "meas_update":
                self._on_measurement_update(data)

    # -----------------------------------------------------------------------
    # Handlers de eventos
    # -----------------------------------------------------------------------

    # -- Device Scanning -----------------------------------------------
    def _scan_devices(self) -> None:
        """Searches for available GRATMA devices and updates the combo."""
        results = GratmaUSB.scan()
        self._scan_results = results
        labels = [r['label'] for r in results]
        self._dev_combo.config(values=labels)
        if labels:
            self._dev_combo.current(0)
        else:
            self._dev_combo_var.set("— No devices —")
        n = len(results)
        self._log_write(
            f"USB Scan: {n} device{'s' if n != 1 else ''} found.",
            GREEN if n else ORANGE)

    def _on_scan(self) -> None:
        self._scan_devices()

    # -- Connection -----------------------------------------------------------
    def _on_connect(self) -> None:
        self._set_busy(True)
        if self._dev is not None:
            self._run_async(self._disconnect_worker)
        else:
            idx = self._dev_combo.current()
            selected = self._scan_results[idx] if 0 <= idx < len(self._scan_results) else None
            self._run_async(self._connect_worker, selected)

    def _connect_worker(self, selected: dict | None) -> None:
        dev = GratmaUSB()
        usb_device = selected['device'] if selected is not None else None
        dev.open(device=usb_device)
        self._dev = dev
        self._rq.put(("connected", selected))

    def _disconnect_worker(self) -> None:
        if self._dev:
            self._dev.close()
            self._dev = None
        self._rq.put(("disconnected", None))

    # -- Ping ---------------------------------------------------------------
    def _on_ping(self) -> None:
        self._set_busy(True)
        self._run_async(self._ping_worker)

    def _ping_worker(self) -> None:
        self._dev.ping()
        self._rq.put(("log_ok", "Manual Ping OK — device responds"))
        self._rq.put(("busy_off", None))

    # --  Measurements -----------------------------------------------------------
    def _on_start_measurement(self) -> None:
        meas_type = self._meas_type.get()

        # Reset abort flag
        self._abort_measurement = False

        # Reset real-time graph
        self._rt_kind = {
            "iv_parametric": "iv", "idt": "idt", "differential": "differential",
        }[meas_type]
        self._rt_series = {}
        self._render_meas_plot(final=False)

        # Capture identification/sensors for CSV saving
        sensors_list = self._selected_sensors()
        parallel = (self._sensor_mode.get() == "parallel")

        try:
            if not sensors_list:
                raise ValueError("Select at least one sensor")

            self._cur_sample = self._sample_name.get().strip() or "sample"
            self._cur_extra = self._extra_text.get().strip()
            self._cur_sensors = sensors_list
            self._cur_parallel = parallel

            if meas_type == "iv_parametric":
                vs = int(self._iv_vs.get())
                vg_start = int(self._iv_vg_start.get())
                vg_end = int(self._iv_vg_end.get())
                vg_step = int(self._iv_vg_step.get())
                reps = int(self._iv_reps.get())
                reverse = self._iv_reverse.get()
                if vg_step <= 0:
                    raise ValueError("VG step must be > 0")
                mask = self._sensor_mask(sensors_list)
                self._set_busy(True)
                self._run_async(self._meas_iv_param_worker,
                                vs, vg_start, vg_end, vg_step, mask, reverse, reps, parallel)
            elif meas_type == "idt":
                vg = int(self._idt_vg.get())
                vs = int(self._idt_vs.get())
                total = int(self._idt_total.get())
                period = int(self._idt_period.get())
                if period <= 0:
                    raise ValueError("Period must be > 0")
                self._set_busy(True)
                self._run_async(self._meas_idt_worker, sensors_list, vg, vs, total, period, parallel)
            elif meas_type == "differential":
                vs = int(self._iv_vs.get())
                vg_start = int(self._iv_vg_start.get())
                vg_end = int(self._iv_vg_end.get())
                vg_step = int(self._iv_vg_step.get())
                reps = int(self._iv_reps.get())
                reverse = self._iv_reverse.get()
                if vg_step <= 0:
                    raise ValueError("VG step must be > 0")
                mask = self._sensor_mask(sensors_list)
                self._set_busy(True)
                self._run_async(self._meas_differential_worker,
                                vs, vg_start, vg_end, vg_step, mask, reverse, reps, parallel)
        except ValueError as e:
            messagebox.showerror("Parameter Error", str(e))
            self._set_busy(False)

    @staticmethod
    def _sensor_mask(sensors_list: list[int]) -> int:
        """Converts a list of 1-based sensors into a bit mask."""
        mask = 0
        for s in sensors_list:
            mask |= 1 << (s - 1)
        return mask

    def _on_stop_measurement(self) -> None:
        """Aborts current measurement"""
        if not self._busy:
            return

        self._abort_measurement = True
        self._log_write("⚠ STOP requested - aborting measurement...", ORANGE)

    def _stream_records(self, update_type: str, point_type: int, end_type: int,
                        phase: int | None = None, poll_s: float = 0.3) -> list[dict] | None:
        """
        Drains firmware buffer until end marker, emitting
        'meas_update' for each batch to refresh the real-time graph.

        Returns all received records, or None if user aborts
        (in which case sends STOP to firmware).
        """
        all_records: list[dict] = []

        def drain_pending() -> bool:
            found_end = False
            while self._dev.get_data_count() > 0:
                batch = self._dev.get_data(4)
                all_records.extend(batch)
                pts = [r for r in batch if r["type"] == point_type]
                if pts:
                    upd = {"type": update_type, "records": pts}
                    if phase is not None:
                        upd["phase"] = phase
                    self._rq.put(("meas_update", upd))
                if any(r["type"] == end_type for r in batch):
                    found_end = True
            return found_end

        while True:
            if self._abort_measurement:
                try:
                    self._dev.stop()
                except Exception:
                    pass
                return None
            if drain_pending():
                return all_records
            status = self._dev.get_status()
            if status == DeviceStatus.ERROR:
                raise GratmaError("Device entered ERROR status during measurement")
            if status == DeviceStatus.IDLE:
                drain_pending()
                return all_records
            time.sleep(poll_s)

    def _meas_iv_param_worker(self, vs, vg_start, vg_end, vg_step, mask, reverse, reps, parallel) -> None:
        mode = "parallel" if parallel else "sequential"
        self._log_write(
            f"I-V ({mode}): VS={vs}mV  VG={vg_start}→{vg_end}mV  step={vg_step}mV  "
            f"sensors=0x{mask:02X}  reps={reps}", CYAN)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._dev.start_sweep_ex(
            vs_mv=vs, vg_start_mv=vg_start, vg_end_mv=vg_end,
            vg_step_mv=vg_step, sensors=mask,
            reverse=reverse, repetitions=reps, parallel=parallel)

        records = self._stream_records("iv", RecordType.SWEEP_POINT, RecordType.SWEEP_END)
        if records is None:
            self._rq.put(("log_warn", "I-V measurement aborted by user"))
            self._rq.put(("busy_off", None))
            return

        result = None
        try:
            result = self._dev.get_result()
        except Exception:
            pass
        pts = [r for r in records if r["type"] == RecordType.SWEEP_POINT]
        meta = {
            "timestamp": timestamp, "vs_mv": vs, "vg_start_mv": vg_start,
            "vg_end_mv": vg_end, "vg_step_mv": vg_step, "reps": reps,
            "reverse": reverse,
        }
        self._rq.put(("meas_done", {"type": "iv", "records": pts, "result": result, "meta": meta}))

    def _meas_idt_worker(self, sensors_list, vg, vs, total, period, parallel) -> None:
        mode = "parallel" if parallel else "sequential"
        self._log_write(
            f"IDT ({mode}): sensors={sensors_list}  VG={vg}mV  VS={vs}mV  "
            f"dur={total}s  period={period}s", CYAN)

        all_samples: list[dict] = []

        if parallel:
            # Single measurement with all sensors connected at once.
            # Firmware expects the sensor index in the same 1-based
            # numbering used everywhere else in the UI (S1..S8).
            mask = self._sensor_mask(sensors_list)
            self._dev.start_idt(sensor=sensors_list[0], vg_mv=vg, vs_mv=vs,
                                total_s=total, period_s=period,
                                parallel=True, sensors_mask=mask)
            records = self._stream_records("idt", RecordType.IDT_SAMPLE, RecordType.IDT_END,
                                           poll_s=0.5)
            if records is None:
                self._rq.put(("log_warn", "Medida IDT abortada por el usuario"))
                self._rq.put(("busy_off", None))
                return
            all_samples.extend(r for r in records if r["type"] == RecordType.IDT_SAMPLE)
        else:
            for sensor in sensors_list:  # 1-based
                if self._abort_measurement:
                    self._rq.put(("log_warn", "IDT measurement aborted by user"))
                    self._rq.put(("busy_off", None))
                    return
                self._log_write(f"  IDT sensor {sensor} ...", FG_DIM)
                # Firmware expects the same 1-based sensor numbering as the
                # rest of the UI (S1..S8) — do not shift it.
                self._dev.start_idt(sensor=sensor, vg_mv=vg, vs_mv=vs,
                                    total_s=total, period_s=period)
                records = self._stream_records("idt", RecordType.IDT_SAMPLE, RecordType.IDT_END,
                                               poll_s=0.5)
                if records is None:
                    self._rq.put(("log_warn", "IDT measurement aborted by user"))
                    self._rq.put(("busy_off", None))
                    return
                all_samples.extend(r for r in records if r["type"] == RecordType.IDT_SAMPLE)

        self._rq.put(("meas_done", {"type": "idt", "records": all_samples, "result": None}))

    def _meas_differential_worker(self, vs, vg_start, vg_end, vg_step, mask, reverse, reps, parallel) -> None:
        def start_phase():
            self._dev.start_sweep_ex(
                vs_mv=vs, vg_start_mv=vg_start, vg_end_mv=vg_end,
                vg_step_mv=vg_step, sensors=mask,
                reverse=reverse, repetitions=reps, parallel=parallel)

        def build_meta(ts: str) -> dict:
            return {
                "timestamp": ts, "vs_mv": vs, "vg_start_mv": vg_start,
                "vg_end_mv": vg_end, "vg_step_mv": vg_step, "reps": reps,
                "reverse": reverse,
            }

        self._log_write("═══ Differential Measurement - PHASE 1: Baseline ═══", MAGENTA)
        # Phase 1 (streaming real-time, phase=1)
        ts1 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_phase()
        records1 = self._stream_records("differential", RecordType.SWEEP_POINT,
                                        RecordType.SWEEP_END, phase=1)
        if records1 is None:
            self._rq.put(("log_warn", "Differential measurement aborted by user"))
            self._rq.put(("busy_off", None))
            return
        result1 = None
        try:
            result1 = self._dev.get_result()
        except Exception:
            pass
        pts1 = [r for r in records1 if r["type"] == RecordType.SWEEP_POINT]
        self._diff_records_phase1 = pts1

        self._log_write(f"Phase 1 completed: VG_min1 = {result1:.4f} V" if result1 is not None
                        else "Phase 1 completed", GREEN)

        # Wait for user confirmation to start phase 2. No timeout:
        # adding the sample can take an indefinite amount of time, so wait
        # until user responds Yes (continue) or No (cancel).
        self._rq.put(("log", "⚠ Add the sample to the sensor and confirm in the dialog to continue with Phase 2"))

        answer = [False]
        answered = threading.Event()

        def ask_in_main():
            answer[0] = messagebox.askyesno(
                "Differential Measurement",
                "Phase 1 (Baseline) completed.\n\n"
                "Add the sample to the sensor and press Yes to continue with Phase 2.\n\n"
                "Press No to cancel.")
            answered.set()

        self.root.after(0, ask_in_main)
        answered.wait()

        if not answer[0]:
            self._rq.put(("log_warn", "Differential measurement cancelled by user"))
            self._rq.put(("busy_off", None))
            return

        self._log_write("═══ Differential Measurement - PHASE 2: With Sample ═══", MAGENTA)
        # Phase 2 (streaming real-time, phase=2; curves from phase 1
        # remain on the graph)
        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_phase()
        records2 = self._stream_records("differential", RecordType.SWEEP_POINT,
                                        RecordType.SWEEP_END, phase=2)
        if records2 is None:
            self._rq.put(("log_warn", "Differential measurement aborted by user"))
            self._rq.put(("busy_off", None))
            return
        result2 = None
        try:
            result2 = self._dev.get_result()
        except Exception:
            pass
        pts2 = [r for r in records2 if r["type"] == RecordType.SWEEP_POINT]
        self._diff_records_phase2 = pts2

        # Calculate delta
        meta1 = build_meta(ts1)
        meta2 = build_meta(ts2)
        if result1 is not None and result2 is not None:
            delta_vg = result2 - result1
            self._log_write(f"Phase 2 completed: VG_min2 = {result2:.4f} V", GREEN)
            self._log_write(f"═══ Differential Result: ΔVG = {delta_vg:.4f} V ═══", YELLOW)
            self._rq.put(("meas_done", {
                "type": "differential",
                "phase1": pts1,
                "phase2": pts2,
                "result": delta_vg,
                "meta1": meta1,
                "meta2": meta2,
            }))
        else:
            self._rq.put(("meas_done", {
                "type": "differential",
                "phase1": pts1,
                "phase2": pts2,
                "result": None,
                "meta1": meta1,
                "meta2": meta2,
            }))

    def _on_measurement_complete(self, data) -> None:
        """Callback when a measurement completes"""
        meas_type = data["type"]
        result = data.get("result")

        # Rebuild series from complete result and paint
        # final graph in Measurements tab (real-time updates
        # may have been lost if the app fell behind)
        self._rt_series = {}
        if meas_type == "differential":
            self._series_ingest(data["phase1"], phase=1)
            self._series_ingest(data["phase2"], phase=2)
        else:
            self._series_ingest(data["records"])
        self._render_meas_plot(final=True)

        if meas_type == "iv":
            self._sweep_records = data["records"]
            if result is not None:
                self._meas_result_lbl.config(text=f"VG_min = {result:.4f} V")
            self._log_write(f"I-V completed — {len(data['records'])} points", GREEN)

        elif meas_type == "idt":
            self._idt_records = data["records"]
            self._log_write(f"IDT completed — {len(data['records'])} samples", GREEN)

        elif meas_type == "differential":
            self._diff_records_phase1 = data["phase1"]
            self._diff_records_phase2 = data["phase2"]
            if result is not None:
                self._meas_result_lbl.config(text=f"ΔVG = {result:.4f} V")
            self._log_write(f"Differential completed — ΔVG = {result:.4f} V" if result is not None
                            else "Differential completed", GREEN)

        self._last_meas_data = data
        self._btn_export_meas.config(state=NORMAL)
        self._set_busy(False)

        # Auto-save in the output folder (if one is configured)
        if self._out_folder.get().strip():
            self._export_measurement_files(data, auto=True)
        else:
            self._log_write("No output folder: use 'Export CSV' to save", ORANGE)

    def _on_measurement_update(self, data) -> None:
        """Accumulates received points and refreshes the real-time graph"""
        self._series_ingest(data["records"], phase=data.get("phase", 0))
        self._render_meas_plot(final=False)

    # -----------------------------------------------------------------------
    # Measurement tab graph (real-time and final result)
    # -----------------------------------------------------------------------
    def _series_ingest(self, records: list[dict], phase: int = 0) -> None:
        """Appends records (in arrival order) to the accumulated series for
        (phase, sensor). Each point keeps its own forward/backward flag so
        the plot can later split the line wherever direction changes."""
        for r in records:
            key = (phase, r["sensor"])
            if self._rt_kind == "idt":
                x = r["seq"] / 1000.0
                backward = False
            else:
                x = r["v1"]
                backward = bool(r.get("backward", False))
            self._rt_series.setdefault(key, []).append((x, r["v2"], backward))

    _RT_TITLES = {
        "iv": "I-V Curve",
        "manual": "Manual Measurement",
        "differential": "Differential Measurement (Baseline vs Sample)",
        "idt": "I vs Time (IDT)",
    }

    # Current unit prefixes (exponent of 10 -> unit label), used to pick a
    # readable order of magnitude for the Y axis of the measurement graph.
    _CURRENT_UNITS = [(0, "A"), (-3, "mA"), (-6, "µA"), (-9, "nA"),
                       (-12, "pA"), (-15, "fA")]

    @classmethod
    def _select_current_unit(cls, values) -> tuple[str, float]:
        """Picks the most readable current unit (A, mA, µA, nA, pA, fA) from
        a list of raw current values in Amperes, based on the order of
        magnitude of the largest one. Returns (unit_label, scale) where
        scale converts A -> unit_label (value_in_unit = value_A / scale)."""
        abs_vals = [abs(v) for v in values if v]
        if not abs_vals:
            return "µA", 1e-6  # sensible default: sensor currents are usually in the µA range
        max_val = max(abs_vals)
        exp3 = int(math.floor(math.log10(max_val) / 3.0)) * 3
        exp3 = max(-15, min(0, exp3))
        scale = 10 ** exp3
        unit = dict(cls._CURRENT_UNITS)[exp3]
        return unit, scale

    def _render_meas_plot(self, final: bool = False) -> None:
        """Repaints the Measurements graph from accumulated series."""
        ax = self._meas_ax
        ax.clear()
        title = self._RT_TITLES.get(self._rt_kind, "Measurement")
        if not final:
            title += " — Real-time"
        xlabel = "Time (s)" if self._rt_kind == "idt" else "$V_G$ (V)"

        # Pick the Y axis unit/scale from the current data so the plotted
        # numbers stay in a readable range (e.g. µA instead of 0.0000012 A).
        all_ys = [y for pts in self._rt_series.values() for _x, y, _b in pts]
        unit, scale = self._select_current_unit(all_ys)
        ylabel = f"$I_{{DS}}$ ({unit})"
        self._setup_axes(ax, title, xlabel, ylabel)

        plotted_labels: set[str] = set()

        # Order: by sensor, then phase
        for key in sorted(self._rt_series, key=lambda k: (k[1], k[0])):
            phase, sensor = key
            pts = self._rt_series[key]
            # Split the accumulated points into contiguous runs of the same
            # direction (forward/backward). Each run is drawn as its own
            # line, so no segment ever connects a forward point straight to
            # a backward point (or vice versa) — matplotlib never draws a
            # line between two separate ax.plot() calls.
            n = len(pts)
            run_start = 0
            i = 1
            while i <= n:
                if i == n or pts[i][2] != pts[run_start][2]:
                    run = pts[run_start:i]
                    backward = run[0][2]
                    xs = [p[0] for p in run]
                    ys_scaled = [p[1] / scale for p in run]
                    label = self._curve_label(sensor, phase, backward)
                    show_label = label not in plotted_labels
                    if show_label:
                        plotted_labels.add(label)
                    ax.plot(xs, ys_scaled,
                            marker="s" if phase == 2 else "o",
                            markersize=3, linewidth=1.5,
                            color=self._curve_color(sensor, phase, backward),
                            label=label if show_label else None)
                    run_start = i
                i += 1

        if plotted_labels:
            ax.legend(facecolor=BG2, edgecolor=FG_DIM, labelcolor=FG,
                      fontsize=7, ncol=2 if len(plotted_labels) > 8 else 1)

        self._meas_fig.tight_layout()
        self._meas_canvas.draw()

    @staticmethod
    def _tint(hex_color: str, t: float) -> str:
        """Lightens a color by mixing it with white (t=0 → base color, t=1 → white)."""
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        r = round(r + (255 - r) * t)
        g = round(g + (255 - g) * t)
        b = round(b + (255 - b) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _curve_color(self, sensor: int, phase: int, backward: bool) -> str:
        """Curve color: base tone by sensor, lightened based on phase/direction.
        Simple measurement: forward = base tone (dark), backward = lighter.
        Differential: 4 tones per sensor (phase1 forward → phase2 backward, dark to light)."""
        base = SENSOR_COLORS[(sensor - 1) % len(SENSOR_COLORS)]
        if phase:
            t = {(1, False): 0.0, (1, True): 0.25,
                 (2, False): 0.5, (2, True): 0.7}[(phase, backward)]
        else:
            t = 0.4 if backward else 0.0
        return self._tint(base, t)

    def _curve_label(self, sensor: int, phase: int, backward: bool) -> str:
        if self._rt_kind == "idt":
            return f"Sensor {sensor}"
        direction = "backward" if backward else "forward"
        if phase:
            return f"S{sensor} {'baseline' if phase == 1 else 'sample'} {direction}"
        return f"S{sensor} {direction}"

    def _on_export_measurement(self) -> None:
        if self._last_meas_data is None:
            return
        if not self._out_folder.get().strip():
            folder = filedialog.askdirectory(title="Output folder for CSV files")
            if not folder:
                return
            self._out_folder.set(folder)
        self._export_measurement_files(self._last_meas_data, auto=False)

    @staticmethod
    def _sanitize_token(tok: str) -> str:
        """Cleans a token for use in a file name."""
        tok = (tok or "").strip().replace(" ", "-")
        return "".join(c for c in tok if c.isalnum() or c in "-+.")

    def _csv_path(self, sensor_tok: str, type_tok: str, rep: int) -> str:
        """Builds the path <sample>_<sensor>_<type>_<rep>_<extra>.csv in the output folder."""
        parts = [self._sanitize_token(self._cur_sample) or "sample",
                 sensor_tok, type_tok, str(rep)]
        extra = self._sanitize_token(self._cur_extra)
        if extra:
            parts.append(extra)
        return os.path.join(self._out_folder.get().strip(), "_".join(parts) + ".csv")

    @staticmethod
    def _write_iv_csv(path: str, recs: list[dict], meta: dict | None = None) -> None:
        """Writes an I-V CSV: a metadata block with the measurement
        conditions (date/time, VS, VG start/end/step, repetitions, reverse
        sweep), a blank line, and then the data table (VG_V, IS_A, VS_V,
        IG_A — point number and direction are not exported)."""
        meta = meta or {}
        recs = sorted(recs, key=lambda r: (bool(r.get("backward")), r["seq"]))
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", meta.get("timestamp", "")])
            w.writerow(["VS_mV", meta.get("vs_mv", "")])
            w.writerow(["VG_start_mV", meta.get("vg_start_mv", "")])
            w.writerow(["VG_end_mV", meta.get("vg_end_mv", "")])
            w.writerow(["VG_step_mV", meta.get("vg_step_mv", "")])
            w.writerow(["Repetitions", meta.get("reps", "")])
            w.writerow(["Reverse_sweep", "Y" if meta.get("reverse") else "N"])
            w.writerow([])
            w.writerow(["VG_V", "IS_A", "VS_V", "IG_A"])
            for r in recs:
                w.writerow([r["v1"], r["v2"], r.get("v3", 0.0), r.get("v4", 0.0)])

    @staticmethod
    def _write_idt_csv(path: str, recs: list[dict]) -> None:
        recs = sorted(recs, key=lambda r: r["seq"])
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Time_ms", "Time_s", "Vbus_V", "IS_A", "VG_V", "IG_A"])
            for r in recs:
                w.writerow([r["seq"], r["seq"] / 1000.0, r["v1"], r["v2"],
                            r.get("v3", 0.0), r.get("v4", 0.0)])

    def _export_iv_phase(self, records: list[dict], type_tok: str, meta: dict | None = None) -> list[str]:
        """Writes one CSV per (sensor, repetition) — or per repetition if parallel.
        Returns the paths written."""
        written: list[str] = []
        if self._cur_parallel:
            sensor_tok = "P" + "-".join(str(s) for s in self._cur_sensors)
            groups: dict[int, list[dict]] = {}
            for r in records:
                groups.setdefault(r.get("rep", 1) or 1, []).append(r)
            for rep, recs in sorted(groups.items()):
                path = self._csv_path(sensor_tok, type_tok, rep)
                self._write_iv_csv(path, recs, meta)
                written.append(path)
        else:
            groups2: dict[tuple[int, int], list[dict]] = {}
            for r in records:
                groups2.setdefault((r["sensor"], r.get("rep", 1) or 1), []).append(r)
            for (sensor, rep), recs in sorted(groups2.items()):
                path = self._csv_path(f"{sensor}", type_tok, rep)
                self._write_iv_csv(path, recs, meta)
                written.append(path)
        return written

    def _export_measurement_files(self, data: dict, auto: bool) -> None:
        """Saves the measurement CSVs to the output folder with agreed naming."""
        try:
            meas_type = data["type"]
            written: list[str] = []
            if meas_type == "iv":
                written = self._export_iv_phase(data["records"], "iv", data.get("meta"))
            elif meas_type == "differential":
                written = self._export_iv_phase(data["phase1"], "diff-ph1", data.get("meta1"))
                written += self._export_iv_phase(data["phase2"], "diff-ph2", data.get("meta2"))
            elif meas_type == "idt":
                if self._cur_parallel:
                    # Single combined measurement → one CSV with sensor list
                    sensor_tok = "P" + "-".join(str(s) for s in self._cur_sensors)
                    path = self._csv_path(sensor_tok, "idt", 1)
                    self._write_idt_csv(path, data["records"])
                    written.append(path)
                else:
                    groups: dict[int, list[dict]] = {}
                    for r in data["records"]:
                        groups.setdefault(r["sensor"], []).append(r)
                    for sensor, recs in sorted(groups.items()):
                        path = self._csv_path(f"S{sensor}", "idt", 1)
                        self._write_idt_csv(path, recs)
                        written.append(path)

            if written:
                prefix = "Auto-saved" if auto else "Exported"
                self._log_write(
                    f"{prefix}: {len(written)} CSV in {self._out_folder.get().strip()}", GREEN)
                for p in written:
                    self._log_write(f"  → {os.path.basename(p)}", FG_DIM)
            else:
                self._log_write("No data to export", ORANGE)
        except Exception as e:
            self._log_write(f"Error saving CSV: {e}", RED)

    # -- GRATMA Control -------------------------------------------------------
    def _on_set_vs(self) -> None:
        try:
            vs_mv = int(self._hl_vs.get())
        except ValueError:
            messagebox.showerror("Error", "VS must be an integer (mV)")
            return
        self._set_busy(True)
        self._run_async(self._set_vs_worker, vs_mv)

    def _set_vs_worker(self, vs_mv) -> None:
        # Firmware performs automatic calibration with these commands
        # Approximation: DAC VS channel 0
        self._dev.set_voltage(dac=1, out=0, mv=vs_mv)
        self._rq.put(("log_ok", f"Set VS = {vs_mv} mV (high-level)"))
        self._rq.put(("busy_off", None))

    def _on_set_vg(self) -> None:
        try:
            vg_mv = int(self._hl_vg.get())
        except ValueError:
            messagebox.showerror("Error", "VG must be an integer (mV)")
            return
        self._set_busy(True)
        self._run_async(self._set_vg_worker, vg_mv)

    def _set_vg_worker(self, vg_mv) -> None:
        # Approximation: DAC VG channel 0
        self._dev.set_voltage(dac=0, out=0, mv=vg_mv)
        self._rq.put(("log_ok", f"Set VG = {vg_mv} mV (high-level)"))
        self._rq.put(("busy_off", None))

    def _on_set_voltage(self) -> None:
        try:
            dac = int(self._dac_idx.get()[0])
            out = int(self._dac_out.get())
            mv = int(self._dac_mv.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid DAC parameters.")
            return
        self._set_busy(True)
        self._run_async(self._set_voltage_worker, dac, out, mv)

    def _set_voltage_worker(self, dac, out, mv) -> None:
        self._dev.set_voltage(dac=dac, out=out, mv=mv)
        self._rq.put(("log_ok", f"Set Voltage RAW — DAC={dac} out={out} → {mv} mV"))
        self._rq.put(("busy_off", None))

    def _on_set_switch(self) -> None:
        try:
            sw = int(self._sw_idx.get())
            raw = self._sw_map_var.get().strip()
            sw_map = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        except ValueError:
            messagebox.showerror("Error", "Invalid switch parameters.")
            return
        self._set_busy(True)
        self._run_async(self._set_switch_worker, sw, sw_map)

    def _set_switch_worker(self, sw, sw_map) -> None:
        self._dev.set_switch(sw=sw, sw_map=sw_map)
        self._rq.put(("log_ok", f"Set Switch — SW={sw} map=0x{sw_map:02X}"))
        self._rq.put(("busy_off", None))

    def _on_get_status(self) -> None:
        self._set_busy(True)
        self._run_async(self._get_status_worker)

    def _get_status_worker(self) -> None:
        st = self._dev.get_status()
        self._rq.put(("dev_status", st))

    # -- Instruments ---------------------------------------------------------
    def _on_read_instruments(self) -> None:
        self._set_busy(True)
        self._run_async(self._instruments_worker)

    def _instruments_worker(self) -> None:
        if self._dev is None:
            return
        d = {
            "ina0_vbus": self._dev.get_vbus(0),
            "ina1_vbus": self._dev.get_vbus(1),
            "ina0_vshunt": self._dev.get_vshunt(0),
            "ina1_vshunt": self._dev.get_vshunt(1),
            "ina0_temp": self._dev.get_temp(0),
        }
        self._rq.put(("instruments", d))

    def _update_instruments(self, d: dict) -> None:
        vbus0 = d["ina0_vbus"]
        vbus1 = d["ina1_vbus"]
        vsh0 = d["ina0_vshunt"]
        vsh1 = d["ina1_vshunt"]
        temp0 = d["ina0_temp"]

        self._instr_vars["ina0_vbus"].set(f"{vbus0:.4f}")
        self._instr_vars["ina1_vbus"].set(f"{vbus1:.4f}")
        self._instr_vars["ina0_vshunt"].set(f"{vsh0 * 1e6:.2f}")
        self._instr_vars["ina1_vshunt"].set(f"{vsh1 * 1e6:.2f}")
        self._instr_vars["ina0_is"].set(f"{vsh0 / 50.0:.6f}")
        self._instr_vars["ina1_is"].set(f"{vsh1 / 200.0:.6f}")
        self._instr_vars["ina0_temp"].set(f"{temp0:.1f}")
        self._instr_ts_lbl.config(
            text=f"Last reading: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    def _on_continuous_toggle(self) -> None:
        if self._instr_continuous.get():
            self._schedule_continuous()
        else:
            if self._continuous_job:
                self.root.after_cancel(self._continuous_job)
                self._continuous_job = None

    def _schedule_continuous(self) -> None:
        if not self._instr_continuous.get() or self._dev is None:
            return
        if not self._busy:
            self._set_busy(True)
            self._run_async(self._instruments_worker)
        try:
            interval_ms = max(200, int(float(self._instr_interval.get()) * 1000))
        except ValueError:
            interval_ms = 1000
        self._continuous_job = self.root.after(interval_ms, self._schedule_continuous)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    root = Tk()
    app = GratmaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
