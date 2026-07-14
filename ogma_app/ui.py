from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .boards import PROFILES, BoardProfile, profile_for, servo_output_for
from .board_tests import (
    FoinseMonitorResult,
    LamhServoTestResult,
    TeachtaireTestResult,
    parse_angle_list,
    run_foinse_monitor,
    run_lamh_servo_test,
    run_teachtaire_test,
    teachtaire_mode_for_env,
)
from .can_layouts import attach_can_ids, load_can_ids, load_payload_layouts
from .can_decoder import decode_can_log_file, save_can_decode_bundle
from .controller import (
    CroiMissionFlashResult,
    CroiWipeRestoreResult,
    DetectionResult,
    FlashDetectedResult,
    LamhSafetyFlashResult,
    OgmaController,
    TeachtaireRadioFlashResult,
)
from .croi_flash import parse_croi_flash_dump, save_croi_flash_bundle
from .diagnostics import DiagnosticReport, collect_diagnostics
from .flight_manifest import (
    FlightManifest,
    LoggingPolicy,
    RadioPolicy,
    load_flight_manifest,
    save_flight_manifest,
)
from .flight_package import (
    FlightPackageInspection,
    FlightPackageResult,
    build_flight_package,
    inspect_flight_package,
)
from .fault_ledger import FaultLedger, board_fault_observations, telemetry_fault_observations
from .groundstation import parse_groundstation_file, parse_groundstation_text, save_groundstation_bundle
from .health import HealthReport, evaluate_health
from .lamh_config import LamhSafetyConfig, load_lamh_safety_config
from .mission_config import (
    CROI_MISSION_CONFIG_SCHEMA_VERSION,
    MissionConfig,
    RecoveryFallbackConfig,
    build_mission_timeline,
)
from .mission_replay import (
    ReplaySession,
    load_replay_csv,
    run_firmware_replay,
    synthetic_nominal_profile,
)
from .paths import CAN_FRAMES_HEADER, OGMA_ROOT, PAYLOAD_LAYOUTS_CSV, RUNS_ROOT
from .preflight import StatusEvidence, evaluate_preflight
from .probe import ProbeResult, probe_stlink
from .serial_capture import (
    DEFAULT_BAUD,
    SerialCaptureResult,
    capture_serial_text,
    serial_capture_summary,
    stream_serial_text,
)
from .snapshots import make_status_sample, make_status_snapshot, save_status_series, save_status_snapshot
from .telemetry import (
    MixedTelemetryAccumulator,
    load_default_can_frames,
    parse_mixed_telemetry_file,
    parse_mixed_telemetry_text,
    load_mixed_telemetry_session,
    save_mixed_telemetry_bundle,
    save_mixed_telemetry_session,
)
from .validation import ValidationRunResult, run_bench_validation


CROI_CAN_RETRY_QUEUE_LEN = 16
TELEMETRY_DISPLAY_FRAME_LIMIT = 2000
PYRO_CHANNEL_VALUES = ("Disabled", "Channel 0", "Channel 1", "Channel 2", "Channel 3")
FLIGHT_STATE_NAMES = {
    0: "calibrating",
    1: "ready",
    2: "powered",
    3: "coasting",
    4: "drogue",
    5: "main",
    6: "landed",
}


def available_pyro_channel_values(reserved: str) -> tuple[str, ...]:
    if reserved == "Disabled":
        return PYRO_CHANNEL_VALUES
    return tuple(value for value in PYRO_CHANNEL_VALUES if value != reserved)


class LinePlot(tk.Canvas):
    def __init__(self, parent: tk.Widget, height: int = 280) -> None:
        super().__init__(parent, height=height, bg="#fbfcfe", highlightthickness=1, highlightbackground="#cfd8e3")
        self.points: list[tuple[float, float]] = []
        self.series: dict[str, list[tuple[float, float]]] = {}
        self.title = ""
        self.ylabel = ""
        self.bind("<Configure>", lambda _event: self.draw())

    def set_points(self, title: str, ylabel: str, points: list[tuple[float, float]]) -> None:
        self.title = title
        self.ylabel = ylabel
        self.points = points[-240:]
        self.series = {"": self.points} if self.points else {}
        self.draw()

    def set_series(self, title: str, ylabel: str, series: dict[str, list[tuple[float, float]]]) -> None:
        self.title = title
        self.ylabel = ylabel
        self.series = {name: points[-240:] for name, points in series.items()}
        self.points = next(iter(self.series.values()), [])
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 320)
        height = max(self.winfo_height(), 120)
        left, right, top, bottom = 54, 18, 24, 28
        plot_w = width - left - right
        plot_h = height - top - bottom
        self.create_text(width // 2, 12, text=self.title, fill="#1f2937", font=("Helvetica", 12, "bold"))
        self.create_rectangle(left, top, left + plot_w, top + plot_h, outline="#c7d2df")
        all_points = [point for points in self.series.values() for point in points]
        if len(all_points) < 2:
            self.create_text(width // 2, height // 2, text="waiting for data", fill="#6b7280")
            return
        xs = [point[0] for point in all_points]
        ys = [point[1] for point in all_points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        if max_x <= min_x:
            max_x = min_x + 1.0
        if max_y <= min_y:
            pad = max(1.0, abs(max_y) * 0.1)
            min_y -= pad
            max_y += pad
        else:
            pad = (max_y - min_y) * 0.12
            min_y -= pad
            max_y += pad

        def sx(value: float) -> float:
            return left + (value - min_x) * plot_w / (max_x - min_x)

        def sy(value: float) -> float:
            return top + (max_y - value) * plot_h / (max_y - min_y)

        for frac in (0.25, 0.5, 0.75):
            y = top + plot_h * frac
            self.create_line(left, y, left + plot_w, y, fill="#e5ebf2")
        self.create_text(8, top + plot_h / 2, text=self.ylabel, angle=90, fill="#64748b")
        self.create_text(left - 6, top + 8, text=f"{max_y:.1f}", anchor="e", fill="#64748b")
        self.create_text(left - 6, top + plot_h - 8, text=f"{min_y:.1f}", anchor="e", fill="#64748b")
        colors = ("#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0891b2")
        legend_index = 0
        for index, (name, points) in enumerate(self.series.items()):
            if len(points) < 2:
                continue
            coords: list[float] = []
            for x, y in points:
                coords.extend([sx(x), sy(y)])
            color = colors[index % len(colors)]
            self.create_line(*coords, fill=color, width=2, smooth=True)
            if name:
                legend_x = left + 8
                legend_y = top + 12 + legend_index * 15
                self.create_line(legend_x, legend_y, legend_x + 16, legend_y, fill=color, width=2)
                self.create_text(legend_x + 22, legend_y, text=name, anchor="w", fill="#334155", font=("Helvetica", 10))
                legend_index += 1


def action_states(profile: BoardProfile, has_latest_status: bool, busy: bool, polling: bool = False) -> dict[str, str]:
    if busy:
        states = {
            key: "disabled"
            for key in (
                "probe",
                "doctor",
                "detect",
                "validate",
                "build",
                "flash",
                "flash_detected",
                "stop_link",
                "read_status",
                "health",
                "poll",
                "stop_poll",
                "teachtaire_test",
                "lamh_servo_test",
                "foinse_monitor",
                "save_status",
                "read_croi_flash",
                "wipe_croi_flash",
                "import_croi_dump",
                "import_groundstation",
                "import_telemetry",
                "open_session",
                "groundstation_usb",
                "telemetry_usb",
                "live_telemetry",
                "stop_telemetry",
                "import_can",
            )
        }
        if polling:
            states["stop_poll"] = "normal"
        return states
    return {
        "probe": "normal",
        "doctor": "normal",
        "detect": "normal",
        "validate": "normal",
        "build": "normal" if profile.can_build() else "disabled",
        "flash": "normal" if profile.can_flash() else "disabled",
        "flash_detected": "normal",
        "stop_link": "normal",
        "read_status": "normal" if profile.can_read_status() else "disabled",
        "health": "normal" if profile.can_evaluate_health() else "disabled",
        "poll": "normal" if profile.can_poll_status() else "disabled",
        "stop_poll": "disabled",
        "teachtaire_test": "normal" if profile.can_run_teachtaire_test() else "disabled",
        "lamh_servo_test": "normal" if profile.can_run_lamh_servo_test() else "disabled",
        "foinse_monitor": "normal" if profile.can_run_foinse_monitor() else "disabled",
        "save_status": "normal" if has_latest_status else "disabled",
        "read_croi_flash": "normal" if profile.can_read_flash_log() else "disabled",
        "wipe_croi_flash": "normal" if profile.can_wipe_flash() else "disabled",
        "import_croi_dump": "normal" if profile.board_id == "croi" else "disabled",
        "import_groundstation": "normal" if profile.can_use_groundstation_usb() else "disabled",
        "import_telemetry": "normal",
        "open_session": "normal",
        "groundstation_usb": "normal" if profile.can_use_groundstation_usb() else "disabled",
        "telemetry_usb": "normal" if profile.can_use_groundstation_usb() else "disabled",
        "live_telemetry": "normal",
        "stop_telemetry": "disabled",
        "import_can": "normal",
    }


def is_croi_flash_result(result: Any) -> bool:
    return (
        isinstance(result, tuple)
        and len(result) == 3
        and result[0] == "croi"
        and isinstance(result[1], dict)
        and isinstance(result[2], Path)
    )


class OgmaApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Ogma Console")
        self.geometry("1280x840")
        self.minsize(900, 640)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.controller = OgmaController(OGMA_ROOT, self.thread_log)
        self.selected_board = tk.StringVar(value=PROFILES[0].board_id)
        self.selected_env = tk.StringVar(value=PROFILES[0].default_env or "")
        self.status_history: dict[str, list[tuple[float, float]]] = {}
        self.can_history: dict[str, list[tuple[float, float]]] = {}
        self.latest_status: tuple[str, str | None, dict[str, Any]] | None = None
        self.status_evidence_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self.latest_telemetry: dict[str, Any] | None = None
        self.latest_telemetry_updated_at = 0.0
        self.current_preflight_report = None
        self.fault_ledger = FaultLedger()
        self.worker_active = False
        self.worker_label = ""
        self.stop_requested = threading.Event()
        self.clear_poll_data_requested = threading.Event()
        self.telemetry_stop_requested = threading.Event()
        self.telemetry_live_active = False
        self.telemetry_accumulator: MixedTelemetryAccumulator | None = None
        self.telemetry_archive_accumulator: MixedTelemetryAccumulator | None = None
        self.telemetry_live_session: Path | None = None
        self.telemetry_live_device = ""
        self.telemetry_live_dirty = False
        self.telemetry_last_draw = 0.0
        self.plot_generation = 0
        self.action_buttons: dict[str, ttk.Button] = {}
        self.toolbar_buttons: list[ttk.Button] = []
        self.toolbar_groups: dict[str, ttk.LabelFrame] = {}
        self.toolbar_group_buttons: dict[str, list[tuple[str, ttk.Button]]] = {}
        self.toolbar_group_order: list[str] = []
        self.toolbar_visible_groups: list[str] = []
        self._toolbar_columns = 0
        self._equalized_panes: set[str] = set()
        self.header_board = tk.StringVar(value="")
        self.header_env = tk.StringVar(value="")
        self.header_activity = tk.StringVar(value="Idle")
        self.servo_widgets: list[tk.Widget] = []
        self.servo_box_visible = False
        self.mission_widgets: list[tk.Widget] = []
        self.mission_flash_widgets: list[tk.Widget] = []
        self.lamh_config_flash_widgets: list[tk.Widget] = []
        self.radio_widgets: list[tk.Widget] = []
        self.radio_flash_widgets: list[tk.Widget] = []
        self.logging_widgets: list[tk.Widget] = []
        self.logging_flash_widgets: list[tk.Widget] = []
        try:
            self.lamh_safety_config = load_lamh_safety_config(
                OGMA_ROOT / "lamh" / "firmware" / "include" / "lamh_safety_config.h"
            )
        except (OSError, ValueError):
            self.lamh_safety_config = LamhSafetyConfig.defaults()
        self.lamh_safe_angles = [tk.IntVar(value=angle) for angle in self.lamh_safety_config.angles_deg]
        self.flight_manifest = FlightManifest.defaults(self.lamh_safety_config)
        self._apply_mission_config(self.flight_manifest.mission)
        self._apply_recovery_config(self.flight_manifest.recovery)
        self._apply_logging_config(self.flight_manifest.logging)
        self._apply_radio_config(self.flight_manifest.radio)

        self._configure_style()
        self._build_ui()
        self._load_can_table()
        self._select_board(PROFILES[0].board_id)
        self.after(100, self._pump_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        bg = "#f4f7fb"
        surface = "#ffffff"
        panel = "#e8eef6"
        text = "#172033"
        muted = "#52637a"
        border = "#c7d2df"
        primary = "#2563eb"
        disabled_text = "#8a97a8"

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.configure(bg=bg)
        try:
            self.tk_setPalette(
                background=bg,
                foreground=text,
                activeBackground="#dbe7f5",
                activeForeground=text,
                selectBackground=primary,
                selectForeground="#ffffff",
                highlightColor=primary,
                highlightBackground=border,
                insertBackground=text,
                troughColor="#d9e2ec",
            )
        except tk.TclError:
            pass

        base_font = ("Helvetica", 13)
        title_font = ("Helvetica", 16, "bold")
        heading_font = ("Helvetica", 12, "bold")
        style.configure(".", background=bg, foreground=text, font=base_font)
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=text, bordercolor=border, relief="solid")
        style.configure("TLabelframe.Label", background=bg, foreground=text, font=heading_font)
        style.configure("TLabel", background=bg, foreground=text)
        style.configure("Title.TLabel", background=bg, foreground=text, font=title_font)
        style.configure("Muted.TLabel", background=bg, foreground=muted)
        style.configure("StatusHeader.TFrame", background=surface, bordercolor=border, relief="solid")
        style.configure("StatusKey.TLabel", background=surface, foreground=muted, font=("Helvetica", 10, "bold"))
        style.configure("StatusValue.TLabel", background=surface, foreground=text, font=heading_font)
        style.configure("MissionWarning.TLabel", background="#fff7df", foreground="#704d00", padding=(10, 8))
        style.configure("PreflightGo.TLabel", background=bg, foreground="#08765b", font=title_font)
        style.configure("PreflightNoGo.TLabel", background=bg, foreground="#b42318", font=title_font)
        style.configure(
            "TButton",
            padding=(10, 7),
            background=panel,
            foreground=text,
            bordercolor=border,
            lightcolor=surface,
            darkcolor=border,
            focusthickness=1,
            focuscolor=border,
        )
        style.map(
            "TButton",
            background=[("pressed", "#ccd8ea"), ("active", "#dbe7f5"), ("disabled", "#eef2f7")],
            foreground=[("disabled", disabled_text)],
            bordercolor=[("focus", primary), ("disabled", "#d8e0ea")],
        )
        button_styles = {
            "Link.TButton": ("#e7eef8", "#d9e6f7", "#c7d9f1", "#17365d"),
            "Firmware.TButton": ("#e4f2ef", "#d2e9e4", "#bdded7", "#164e46"),
            "Runtime.TButton": ("#edf1f5", "#dde5ed", "#cbd7e3", text),
            "Test.TButton": ("#fff3d6", "#fee7ad", "#f7d47e", "#684900"),
            "Data.TButton": ("#e4f1f5", "#d1e8ef", "#b9dbe5", "#164e63"),
            "Danger.TButton": ("#fde8e7", "#f9d2cf", "#f1b8b3", "#8a1c16"),
        }
        for style_name, (normal_bg, active_bg, pressed_bg, fg) in button_styles.items():
            style.configure(
                style_name,
                padding=(10, 7),
                background=normal_bg,
                foreground=fg,
                bordercolor=border,
                lightcolor=surface,
                darkcolor=border,
                focusthickness=1,
                focuscolor=border,
            )
            style.map(
                style_name,
                background=[("pressed", pressed_bg), ("active", active_bg), ("disabled", "#eef2f7")],
                foreground=[("disabled", disabled_text)],
                bordercolor=[("focus", primary), ("disabled", "#d8e0ea")],
            )
        style.configure(
            "Treeview",
            rowheight=28,
            background=surface,
            fieldbackground=surface,
            foreground=text,
            bordercolor=border,
            lightcolor=surface,
            darkcolor=border,
        )
        style.configure(
            "Treeview.Heading",
            background=panel,
            foreground=text,
            font=heading_font,
            padding=(6, 6),
            bordercolor=border,
        )
        style.map("Treeview", background=[("selected", primary)], foreground=[("selected", "#ffffff")])
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=text, padding=(16, 8), font=heading_font)
        style.map(
            "TNotebook.Tab",
            background=[("selected", surface), ("active", "#dbe7f5"), ("disabled", "#eef2f7")],
            foreground=[("selected", text), ("disabled", disabled_text)],
        )
        style.configure("TCombobox", padding=(4, 4), background=panel, fieldbackground=surface, foreground=text, arrowcolor=text)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", surface), ("disabled", "#eef2f7")],
            foreground=[("readonly", text), ("disabled", disabled_text)],
        )
        style.configure("TSpinbox", padding=(4, 4), background=panel, fieldbackground=surface, foreground=text, arrowcolor=text)
        style.map(
            "TSpinbox",
            fieldbackground=[("readonly", surface), ("disabled", "#eef2f7")],
            foreground=[("readonly", text), ("disabled", disabled_text)],
        )

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        sidebar = ttk.Frame(root)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 10))
        ttk.Label(sidebar, text="Boards", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        self.board_list = tk.Listbox(
            sidebar,
            width=27,
            height=12,
            exportselection=False,
            activestyle="none",
            borderwidth=1,
            relief="solid",
            bg="#ffffff",
            fg="#172033",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#c7d2df",
            highlightcolor="#2563eb",
            font=("Helvetica", 13),
        )
        self.board_list.pack(fill="y")
        for profile in PROFILES:
            self.board_list.insert("end", f"{profile.display_name}  ({profile.board_id})")
        self.board_list.selection_set(0)
        self.board_list.bind("<ButtonRelease-1>", self._on_board_list, add="+")
        self.board_list.bind("<<ListboxSelect>>", self._on_board_list)

        top = ttk.Frame(root)
        top.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        top.columnconfigure(0, weight=1)

        status_bar = ttk.Frame(top, padding=(10, 7), style="StatusHeader.TFrame")
        status_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            status_bar.columnconfigure(column, weight=1)
        for column, (label, variable) in enumerate(
            (("BOARD", self.header_board), ("ENVIRONMENT", self.header_env), ("ACTIVITY", self.header_activity))
        ):
            cell = ttk.Frame(status_bar, style="StatusHeader.TFrame")
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
            ttk.Label(cell, text=label, style="StatusKey.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=variable, style="StatusValue.TLabel").pack(anchor="w")

        self.toolbar_actions = ttk.Frame(top)
        self.toolbar_actions.grid(row=1, column=0, sticky="ew")
        self.toolbar_actions.bind("<Configure>", self._on_toolbar_configure)
        toolbar_specs: list[tuple[str, str, str, Callable[[], None], str]] = [
            ("Connection", "probe", "Probe", self.probe_link, "Link.TButton"),
            ("Connection", "detect", "Detect SWD", self.detect, "Link.TButton"),
            ("Connection", "stop_link", "Stop Link", self.stop_link, "Link.TButton"),
            ("Connection", "doctor", "Doctor", self.doctor_selected, "Link.TButton"),
            ("Firmware", "validate", "Validate", self.validate_selected, "Firmware.TButton"),
            ("Firmware", "build", "Build", self.build_selected, "Firmware.TButton"),
            ("Firmware", "flash", "Flash", self.flash_selected, "Firmware.TButton"),
            ("Firmware", "flash_detected", "Flash Detected", self.flash_detected, "Firmware.TButton"),
            ("Observe", "read_status", "Read Status", self.read_status, "Runtime.TButton"),
            ("Observe", "poll", "Poll", self.poll_status, "Runtime.TButton"),
            ("Observe", "stop_poll", "Stop Poll", self.stop_polling, "Runtime.TButton"),
            ("Observe", "clear_plot", "Clear Plot", self.clear_plot, "Runtime.TButton"),
            ("Observe", "health", "Health", self.health_selected, "Runtime.TButton"),
            ("Observe", "save_status", "Save Status", self.save_status, "Runtime.TButton"),
            ("Tests", "teachtaire_test", "LoRa / GNSS Test", self.run_teachtaire_test, "Test.TButton"),
            ("Tests", "lamh_servo_test", "Servo Test", self.run_lamh_servo_test, "Test.TButton"),
            ("Tests", "foinse_monitor", "Current Monitor", self.run_foinse_monitor, "Test.TButton"),
            ("Data", "read_croi_flash", "Read Croí Flash", self.read_croi_flash, "Data.TButton"),
            ("Data", "import_croi_dump", "Import Croí Dump", self.import_croi_dump, "Data.TButton"),
            ("Data", "import_groundstation", "Import GS", self.import_groundstation, "Data.TButton"),
            ("Data", "import_telemetry", "Import Telemetry", self.import_telemetry, "Data.TButton"),
            ("Data", "open_session", "Open Session", self.open_telemetry_session, "Data.TButton"),
            ("Data", "groundstation_usb", "Groundstation USB", self.capture_groundstation_usb, "Data.TButton"),
            ("Data", "telemetry_usb", "Timed Capture", self.capture_telemetry_usb, "Data.TButton"),
            ("Data", "live_telemetry", "Start Telemetry", self.start_live_telemetry, "Data.TButton"),
            ("Data", "stop_telemetry", "Stop Telemetry", self.stop_live_telemetry, "Data.TButton"),
            ("Data", "import_can", "Import CAN", self.import_can_log, "Data.TButton"),
            ("Safety", "wipe_croi_flash", "Wipe Croí Flash", self.wipe_croi_flash, "Danger.TButton"),
        ]
        for group, key, text, command, style_name in toolbar_specs:
            self._add_toolbar_button(group, key, text, command, style_name)
        self.after_idle(self._layout_toolbar_buttons)

        env_row = ttk.Frame(top)
        env_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        env_row.columnconfigure(1, weight=1)
        ttk.Label(env_row, text="Env", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(3, 8))
        self.env_combo = ttk.Combobox(env_row, textvariable=self.selected_env, state="readonly")
        self.env_combo.grid(row=0, column=1, sticky="ew", padx=(0, 3))
        self.env_combo.bind("<<ComboboxSelected>>", lambda _event: self.header_env.set(self.selected_env.get() or "N/A"))

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=1, sticky="nsew")
        self._bind_notebook_clicks(self.notebook)

        board_tab = ttk.Frame(self.notebook, padding=10)
        board_tab.columnconfigure(0, weight=1)
        board_tab.rowconfigure(2, weight=1)
        self.notebook.add(board_tab, text="Board")

        self.profile_title = ttk.Label(board_tab, text="", style="Title.TLabel")
        self.profile_title.grid(row=0, column=0, sticky="w")
        self.profile_notes = ttk.Label(board_tab, text="", style="Muted.TLabel", wraplength=840)
        self.profile_notes.grid(row=1, column=0, sticky="ew", pady=(4, 10))
        board_tab.bind("<Configure>", self._on_board_tab_configure)

        panes = ttk.PanedWindow(board_tab, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        self.status_tree = ttk.Treeview(left, columns=("value", "unit"), show="tree headings")
        self.status_tree.heading("#0", text="Field")
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="Unit")
        self.status_tree.column("#0", width=260)
        self.status_tree.column("value", width=180)
        self.status_tree.column("unit", width=80)
        self.status_tree.pack(fill="both", expand=True)

        self.plot = LinePlot(right)
        self.plot.pack(fill="both", expand=True, pady=(0, 10))

        self.servo_box = ttk.LabelFrame(right, text="Lámh Servo")
        for column in range(3):
            self.servo_box.columnconfigure(column, weight=1)
        self.servo_channel = tk.IntVar(value=1)
        self.servo_angle = tk.IntVar(value=90)
        ttk.Label(self.servo_box, text="Output").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        servo_channel = ttk.Spinbox(self.servo_box, from_=1, to=4, textvariable=self.servo_channel, width=6)
        servo_channel.grid(row=0, column=1, padx=6, pady=6)
        self.servo_channel_label = ttk.Label(self.servo_box, text="", style="Muted.TLabel")
        self.servo_channel_label.grid(row=0, column=2, padx=6, pady=6, sticky="w")
        ttk.Label(self.servo_box, text="Angle").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        servo_angle = ttk.Spinbox(self.servo_box, from_=0, to=90, textvariable=self.servo_angle, width=6)
        servo_angle.grid(row=1, column=1, padx=6, pady=6)
        servo_send = ttk.Button(self.servo_box, text="Send", command=self.send_servo)
        servo_send.grid(row=2, column=0, columnspan=3, sticky="ew", padx=6, pady=6)
        ttk.Separator(self.servo_box, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=6, pady=(4, 6)
        )
        ttk.Label(self.servo_box, text="Failsafe Angles", style="Title.TLabel").grid(
            row=4, column=0, columnspan=3, padx=6, pady=(0, 4), sticky="w"
        )
        safety_widgets: list[tk.Widget] = []
        for index, (angle, output) in enumerate(zip(self.lamh_safe_angles, profile_for("lamh").servo_outputs)):
            row = index + 5
            ttk.Label(self.servo_box, text=f"{output.label} / PCA {output.pca_channel}").grid(
                row=row, column=0, padx=6, pady=3, sticky="w"
            )
            angle_input = ttk.Spinbox(self.servo_box, from_=0, to=90, textvariable=angle, width=6)
            angle_input.grid(row=row, column=1, padx=6, pady=3, sticky="w")
            ttk.Label(self.servo_box, text="deg", style="Muted.TLabel").grid(
                row=row, column=2, padx=6, pady=3, sticky="w"
            )
            safety_widgets.append(angle_input)
        safety_flash = ttk.Button(self.servo_box, text="Flash Failsafe Angles", command=self.flash_lamh_safety_config)
        safety_flash.grid(row=9, column=0, columnspan=3, sticky="ew", padx=6, pady=(6, 8))
        self.servo_widgets = [servo_channel, servo_angle, servo_send, *safety_widgets, safety_flash]

        self.action_hint = ttk.Label(right, text="", style="Muted.TLabel", wraplength=300)
        self.action_hint.pack(fill="x")
        right.bind("<Configure>", self._on_right_pane_configure)

        can_tab = ttk.Frame(self.notebook, padding=10)
        can_tab.rowconfigure(1, weight=1)
        can_tab.columnconfigure(0, weight=1)
        self.notebook.add(can_tab, text="CAN")
        self.can_summary = ttk.Label(can_tab, text="", style="Muted.TLabel", wraplength=960)
        self.can_summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        can_panes = ttk.PanedWindow(can_tab, orient="horizontal")
        can_panes.grid(row=1, column=0, sticky="nsew")
        can_left = ttk.Frame(can_panes)
        can_right = ttk.Frame(can_panes)
        can_panes.add(can_left, weight=1)
        can_panes.add(can_right, weight=1)
        can_left.columnconfigure(0, weight=1)
        can_left.rowconfigure(1, weight=0)
        can_left.rowconfigure(3, weight=1)
        can_right.columnconfigure(0, weight=1)
        can_right.rowconfigure(0, weight=1)
        can_right.rowconfigure(1, weight=1)

        ttk.Label(can_left, text="Croí Live Monitor", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.can_health_tree = ttk.Treeview(can_left, columns=("value", "unit"), show="tree headings", height=9)
        self.can_health_tree.heading("#0", text="Field")
        self.can_health_tree.heading("value", text="Value")
        self.can_health_tree.heading("unit", text="Unit")
        self.can_health_tree.column("#0", width=210)
        self.can_health_tree.column("value", width=160)
        self.can_health_tree.column("unit", width=80)
        self.can_health_tree.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(can_left, text="Frame Definitions", style="Title.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.can_tree = ttk.Treeview(
            can_left,
            columns=("id", "bytes", "type", "scale", "notes"),
            show="tree headings",
        )
        self.can_tree.heading("#0", text="Frame / Field")
        self.can_tree.heading("id", text="ID")
        self.can_tree.heading("bytes", text="Bytes")
        self.can_tree.heading("type", text="Type")
        self.can_tree.heading("scale", text="Scale")
        self.can_tree.heading("notes", text="Notes")
        self.can_tree.column("#0", width=230)
        self.can_tree.column("id", width=80)
        self.can_tree.column("bytes", width=80)
        self.can_tree.column("type", width=100)
        self.can_tree.column("scale", width=160)
        self.can_tree.column("notes", width=360)
        self.can_tree.grid(row=3, column=0, sticky="nsew")

        self.can_traffic_plot = LinePlot(can_right, height=230)
        self.can_traffic_plot.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.can_data_plot = LinePlot(can_right, height=230)
        self.can_data_plot.grid(row=1, column=0, sticky="nsew")

        self.telemetry_tab = ttk.Frame(self.notebook, padding=10)
        self.telemetry_tab.rowconfigure(1, weight=1)
        self.telemetry_tab.columnconfigure(0, weight=1)
        self.notebook.add(self.telemetry_tab, text="Telemetry")
        self.telemetry_summary = tk.StringVar(value="Stopped. Start Telemetry to read the Groundstation USB stream.")
        ttk.Label(
            self.telemetry_tab,
            textvariable=self.telemetry_summary,
            style="Muted.TLabel",
            wraplength=960,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        telemetry_panes = ttk.PanedWindow(self.telemetry_tab, orient="horizontal")
        telemetry_panes.grid(row=1, column=0, sticky="nsew")
        telemetry_left = ttk.Frame(telemetry_panes)
        telemetry_right = ttk.Frame(telemetry_panes)
        telemetry_panes.add(telemetry_left, weight=1)
        telemetry_panes.add(telemetry_right, weight=1)
        telemetry_left.rowconfigure(0, weight=1)
        telemetry_left.columnconfigure(0, weight=1)
        telemetry_right.rowconfigure(0, weight=1)
        telemetry_right.columnconfigure(0, weight=1)

        self.telemetry_tree = ttk.Treeview(
            telemetry_left,
            columns=("value", "detail"),
            show="tree headings",
        )
        self.telemetry_tree.heading("#0", text="Stream / Frame")
        self.telemetry_tree.heading("value", text="Value")
        self.telemetry_tree.heading("detail", text="Detail")
        self.telemetry_tree.column("#0", width=220)
        self.telemetry_tree.column("value", width=180)
        self.telemetry_tree.column("detail", width=360)
        self.telemetry_tree.grid(row=0, column=0, sticky="nsew")

        self.telemetry_views = ttk.Notebook(telemetry_right)
        self.telemetry_views.grid(row=0, column=0, sticky="nsew")
        self._bind_notebook_clicks(self.telemetry_views)

        def telemetry_plot_tab(name: str) -> tuple[LinePlot, LinePlot]:
            tab = ttk.Frame(self.telemetry_views, padding=6)
            tab.rowconfigure(0, weight=1)
            tab.rowconfigure(1, weight=1)
            tab.columnconfigure(0, weight=1)
            self.telemetry_views.add(tab, text=name)
            upper = LinePlot(tab, height=220)
            lower = LinePlot(tab, height=220)
            upper.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
            lower.grid(row=1, column=0, sticky="nsew")
            return upper, lower

        def telemetry_single_plot_tab(name: str) -> LinePlot:
            tab = ttk.Frame(self.telemetry_views, padding=6)
            tab.rowconfigure(0, weight=1)
            tab.columnconfigure(0, weight=1)
            self.telemetry_views.add(tab, text=name)
            plot = LinePlot(tab, height=440)
            plot.grid(row=0, column=0, sticky="nsew")
            return plot

        self.telemetry_altitude_plot, self.telemetry_velocity_plot = telemetry_plot_tab("Flight")
        self.telemetry_accel_plot, self.telemetry_gyro_plot = telemetry_plot_tab("IMU")
        self.telemetry_current_plot = telemetry_single_plot_tab("Power")
        self.telemetry_rate_plot, self.telemetry_gap_plot = telemetry_plot_tab("Link")
        for split in (panes, can_panes, telemetry_panes):
            self.after(100, lambda pane=split: self._set_initial_equal_split(pane))
            split.bind(
                "<Map>",
                lambda _event, pane=split: self.after_idle(
                    lambda: self._set_initial_equal_split(pane)
                ),
                add="+",
            )

        self.log_tab = ttk.Frame(self.notebook, padding=10)
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        self.notebook.add(self.log_tab, text="Log")
        self.log_text = tk.Text(
            self.log_tab,
            height=10,
            bg="#ffffff",
            fg="#172033",
            insertbackground="#172033",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#c7d2df",
            highlightcolor="#2563eb",
            font=("Helvetica", 13),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        self.mission_tab = ttk.Frame(self.notebook, padding=10)
        self.mission_tab.columnconfigure(0, weight=1)
        self.mission_tab.columnconfigure(1, weight=1)
        self.mission_tab.rowconfigure(5, weight=1)
        self.notebook.add(self.mission_tab, text="Mission")

        mission_warning = ttk.Label(
            self.mission_tab,
            text=(
                "Airbrake uses timed deploy/stow with physical arm and command lease.  "
                "Rev1 pyro missions require external RBF, accepted-risk Pleasc firmware, continuity, and live Croí commands."
            ),
            style="MissionWarning.TLabel",
            wraplength=900,
        )
        mission_warning.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        mission_name = ttk.Entry(self.mission_tab, textvariable=self.mission_name)
        ttk.Label(self.mission_tab, text="Mission Name").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        mission_name.grid(row=1, column=1, sticky="ew", pady=(0, 8))

        mission_sections = ttk.Notebook(self.mission_tab)
        self._bind_notebook_clicks(mission_sections)
        flight = ttk.Frame(mission_sections, padding=8)
        recovery = ttk.Frame(mission_sections, padding=8)
        airbrake = ttk.Frame(mission_sections, padding=8)
        actions = ttk.Frame(self.mission_tab)
        mission_sections.add(flight, text="Flight")
        mission_sections.add(recovery, text="Recovery")
        mission_sections.add(airbrake, text="Airbrake")
        mission_sections.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        actions.grid(row=3, column=0, columnspan=2, sticky="ew")
        for frame in (flight, recovery, airbrake):
            frame.columnconfigure(1, weight=1)

        ttk.Label(flight, text="Liftoff Threshold").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        liftoff = ttk.Spinbox(flight, from_=1.0, to=200.0, increment=0.1, textvariable=self.mission_liftoff_accel)
        liftoff.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(flight, text="m/s^2", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(flight, text="Vertical Axis").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        imu_axis = ttk.Combobox(
            flight, values=("X", "Y", "Z"), textvariable=self.mission_imu_axis, state="readonly", width=6
        )
        imu_axis.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        imu_sign = ttk.Combobox(
            flight,
            values=("Positive", "Negative"),
            textvariable=self.mission_imu_sign,
            state="readonly",
            width=10,
        )
        imu_sign.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=6)
        ttk.Label(flight, text="Main Altitude").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        main_altitude = ttk.Spinbox(flight, from_=0, to=20000, increment=10, textvariable=self.mission_main_altitude)
        main_altitude.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(flight, text="m", style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=6)

        ttk.Label(recovery, text="Drogue Channel").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        drogue_channel = ttk.Combobox(recovery, values=PYRO_CHANNEL_VALUES, textvariable=self.mission_drogue_channel, state="readonly")
        drogue_channel.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(recovery, text="Main Channel").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        main_channel = ttk.Combobox(recovery, values=PYRO_CHANNEL_VALUES, textvariable=self.mission_main_channel, state="readonly")
        main_channel.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        self.mission_drogue_channel_combo = drogue_channel
        self.mission_main_channel_combo = main_channel
        ttk.Label(recovery, text="Main Min Delay After Drogue").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        drogue_delay = ttk.Spinbox(recovery, from_=0, to=600000, increment=100, textvariable=self.mission_drogue_delay)
        drogue_delay.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(recovery, text="ms", style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=6)
        main_backup = ttk.Checkbutton(
            recovery,
            text="Main Backup Enabled",
            variable=self.mission_main_backup_enabled,
        )
        main_backup.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Label(recovery, text="After Apogee").grid(row=4, column=0, sticky="w", padx=8, pady=6)
        main_backup_delay = ttk.Spinbox(
            recovery,
            from_=100,
            to=120000,
            increment=100,
            textvariable=self.mission_main_backup_after_apogee,
        )
        main_backup_delay.grid(row=4, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(recovery, text="ms", style="Muted.TLabel").grid(row=4, column=2, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(recovery, text="Descent Speed").grid(row=5, column=0, sticky="w", padx=8, pady=6)
        main_backup_speed = ttk.Spinbox(
            recovery,
            from_=1.0,
            to=300.0,
            increment=1.0,
            textvariable=self.mission_main_backup_descent_speed,
        )
        main_backup_speed.grid(row=5, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(recovery, text="m/s", style="Muted.TLabel").grid(row=5, column=2, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(recovery, text="Altitude Window").grid(row=6, column=0, sticky="w", padx=8, pady=6)
        backup_altitudes = ttk.Frame(recovery)
        backup_altitudes.grid(row=6, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        backup_altitudes.columnconfigure(0, weight=1)
        backup_altitudes.columnconfigure(2, weight=1)
        main_backup_min_altitude = ttk.Spinbox(
            backup_altitudes,
            from_=0,
            to=19999,
            increment=10,
            textvariable=self.mission_main_backup_min_altitude,
        )
        main_backup_min_altitude.grid(row=0, column=0, sticky="ew")
        ttk.Label(backup_altitudes, text="to", style="Muted.TLabel").grid(row=0, column=1, padx=6)
        main_backup_max_altitude = ttk.Spinbox(
            backup_altitudes,
            from_=1,
            to=20000,
            increment=10,
            textvariable=self.mission_main_backup_max_altitude,
        )
        main_backup_max_altitude.grid(row=0, column=2, sticky="ew")
        ttk.Label(recovery, text="Confirm Samples").grid(row=7, column=0, sticky="w", padx=8, pady=6)
        main_backup_samples = ttk.Spinbox(
            recovery,
            from_=3,
            to=100,
            textvariable=self.mission_main_backup_required_samples,
        )
        main_backup_samples.grid(row=7, column=1, sticky="ew", padx=8, pady=6)

        airbrake_enabled = ttk.Checkbutton(airbrake, text="Enabled", variable=self.mission_airbrake_enabled)
        airbrake_enabled.grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Label(airbrake, text="Lamh Output").grid(row=0, column=1, sticky="w", padx=8, pady=6)
        airbrake_channel = ttk.Spinbox(airbrake, from_=1, to=4, textvariable=self.mission_airbrake_channel, width=6)
        airbrake_channel.grid(row=0, column=2, sticky="w", padx=8, pady=6)
        ttk.Label(airbrake, text="Retract").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        airbrake_retracted = ttk.Spinbox(airbrake, from_=0, to=90, textvariable=self.mission_airbrake_retracted_angle, width=6)
        airbrake_retracted.grid(row=1, column=1, sticky="w", padx=8, pady=6)
        ttk.Label(airbrake, text="Max").grid(row=1, column=2, sticky="w", padx=8, pady=6)
        airbrake_max = ttk.Spinbox(airbrake, from_=0, to=90, textvariable=self.mission_airbrake_max_angle, width=6)
        airbrake_max.grid(row=1, column=3, sticky="w", padx=8, pady=6)
        ttk.Label(airbrake, text="Deploy After Liftoff").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        airbrake_delay = ttk.Spinbox(airbrake, from_=0, to=120000, increment=100, textvariable=self.mission_airbrake_delay)
        airbrake_delay.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(airbrake, text="Stow After Liftoff").grid(row=2, column=2, sticky="w", padx=8, pady=6)
        airbrake_stow_delay = ttk.Spinbox(
            airbrake,
            from_=0,
            to=600000,
            increment=100,
            textvariable=self.mission_airbrake_stow_delay,
        )
        airbrake_stow_delay.grid(row=2, column=3, sticky="ew", padx=8, pady=6)
        ttk.Label(airbrake, text="Command Lease").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        airbrake_watchdog = ttk.Spinbox(airbrake, from_=500, to=2000, increment=50, textvariable=self.mission_airbrake_watchdog)
        airbrake_watchdog.grid(row=3, column=1, sticky="ew", padx=8, pady=6)
        ttk.Separator(airbrake, orient="horizontal").grid(
            row=4, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 6)
        )
        ttk.Label(airbrake, text="Lámh Failsafe Angles", style="Title.TLabel").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4)
        )
        mission_safety_widgets: list[tk.Widget] = []
        for index, (angle, output) in enumerate(zip(self.lamh_safe_angles, profile_for("lamh").servo_outputs)):
            row = 6 + index // 2
            column = 0 if index % 2 == 0 else 2
            ttk.Label(airbrake, text=f"{output.label} Safe").grid(
                row=row, column=column, sticky="w", padx=8, pady=4
            )
            angle_input = ttk.Spinbox(airbrake, from_=0, to=90, textvariable=angle, width=6)
            angle_input.grid(row=row, column=column + 1, sticky="ew", padx=8, pady=4)
            mission_safety_widgets.append(angle_input)
        mission_safety_flash = ttk.Button(
            airbrake,
            text="Flash Lámh Failsafe Angles",
            command=self.flash_lamh_safety_config,
            style="Firmware.TButton",
        )
        mission_safety_flash.grid(row=8, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 4))

        mission_save = ttk.Button(actions, text="Save Manifest", command=self.save_mission)
        mission_load = ttk.Button(actions, text="Load Manifest", command=self.load_mission)
        mission_preview = ttk.Button(actions, text="Validate / Preview", command=self.refresh_mission_preview)
        mission_flash = ttk.Button(actions, text="Flash Locked Mission", command=self.flash_croi_mission, style="Firmware.TButton")
        mission_replay = ttk.Button(actions, text="Replay Flight CSV", command=self.replay_flight_csv, style="Test.TButton")
        mission_synthetic = ttk.Button(actions, text="Synthetic Replay", command=self.replay_synthetic_flight, style="Test.TButton")
        mission_package = ttk.Button(actions, text="Build Flight Package", command=self.build_current_flight_package, style="Data.TButton")
        mission_package_open = ttk.Button(actions, text="Inspect Flight Package", command=self.open_flight_package, style="Data.TButton")
        mission_save.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        mission_load.grid(row=0, column=1, sticky="ew", padx=4)
        mission_preview.grid(row=0, column=2, sticky="ew", padx=4)
        mission_flash.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        mission_replay.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(8, 0))
        mission_synthetic.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(4, 0), pady=(8, 0))
        mission_package.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(8, 0))
        mission_package_open.grid(row=2, column=2, columnspan=2, sticky="ew", padx=(4, 0), pady=(8, 0))
        for column in range(4):
            actions.columnconfigure(column, weight=1)

        self.mission_audit = tk.StringVar(value="")
        ttk.Label(self.mission_tab, textvariable=self.mission_audit, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(10, 6)
        )
        self.mission_timeline_tree = ttk.Treeview(
            self.mission_tab,
            columns=("state", "action", "guard"),
            show="tree headings",
            height=8,
        )
        self.mission_timeline_tree.heading("#0", text="Trigger")
        self.mission_timeline_tree.heading("state", text="State")
        self.mission_timeline_tree.heading("action", text="Action")
        self.mission_timeline_tree.heading("guard", text="Guard")
        self.mission_timeline_tree.column("#0", width=250)
        self.mission_timeline_tree.column("state", width=170)
        self.mission_timeline_tree.column("action", width=280)
        self.mission_timeline_tree.column("guard", width=360)
        self.mission_timeline_tree.grid(row=5, column=0, columnspan=2, sticky="nsew")
        self.mission_readonly_widgets = [drogue_channel, main_channel]
        self.mission_widgets = [
            mission_name, liftoff, imu_axis, imu_sign, main_altitude, drogue_channel, main_channel, drogue_delay,
            main_backup, main_backup_delay, main_backup_speed, main_backup_min_altitude,
            main_backup_max_altitude, main_backup_samples,
            airbrake_enabled, airbrake_channel, airbrake_retracted, airbrake_max,
            airbrake_delay, airbrake_stow_delay, airbrake_watchdog, *mission_safety_widgets,
            mission_save, mission_load,
            mission_preview, mission_replay, mission_synthetic,
            mission_package, mission_package_open,
        ]
        self.mission_flash_widgets = [mission_flash]
        self.lamh_config_flash_widgets = [mission_safety_flash]
        self._build_preflight_tab()
        self._build_recovery_tab()
        self._build_radio_tab()
        self._build_logging_tab()
        self._build_fault_tab()
        for variable in (
            self.mission_name,
            self.mission_liftoff_accel,
            self.mission_imu_axis,
            self.mission_imu_sign,
            self.mission_main_altitude,
            self.mission_drogue_delay,
            self.mission_airbrake_enabled,
            self.mission_airbrake_channel,
            self.mission_airbrake_retracted_angle,
            self.mission_airbrake_max_angle,
            self.mission_airbrake_delay,
            self.mission_airbrake_stow_delay,
            self.mission_airbrake_watchdog,
            self.mission_drogue_channel,
            self.mission_main_channel,
            self.mission_main_backup_enabled,
            self.mission_main_backup_after_apogee,
            self.mission_main_backup_descent_speed,
            self.mission_main_backup_min_altitude,
            self.mission_main_backup_max_altitude,
            self.mission_main_backup_required_samples,
            self.radio_core_period,
            self.radio_gps_period,
            self.radio_slow_period,
            self.radio_health_period,
            self.logging_sample_period,
            self.logging_post_landing,
            self.logging_include_remote_can,
            *self.lamh_safe_angles,
        ):
            variable.trace_add("write", lambda *_args: self.refresh_mission_preview())
        self.mission_drogue_channel.trace_add("write", lambda *_args: self._refresh_pyro_channel_options())
        self.mission_main_channel.trace_add("write", lambda *_args: self._refresh_pyro_channel_options())
        self._refresh_pyro_channel_options()
        self.refresh_mission_preview()

    def _build_preflight_tab(self) -> None:
        self.preflight_tab = ttk.Frame(self.notebook, padding=10)
        self.preflight_tab.columnconfigure(0, weight=1)
        self.preflight_tab.rowconfigure(2, weight=1)
        self.notebook.add(self.preflight_tab, text="Preflight")

        header = ttk.Frame(self.preflight_tab)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        self.preflight_summary = tk.StringVar(value="NO-GO | evidence not collected")
        self.preflight_summary_label = ttk.Label(
            header,
            textvariable=self.preflight_summary,
            style="PreflightNoGo.TLabel",
        )
        self.preflight_summary_label.grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Refresh", command=self.refresh_preflight, style="Runtime.TButton").grid(
            row=0, column=1, sticky="e"
        )
        ttk.Label(
            self.preflight_tab,
            text="Advisory view. Flight firmware guards remain authoritative.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.preflight_tree = ttk.Treeview(
            self.preflight_tab,
            columns=("state", "actual", "required", "source"),
            show="tree headings",
        )
        self.preflight_tree.heading("#0", text="Check")
        self.preflight_tree.heading("state", text="State")
        self.preflight_tree.heading("actual", text="Actual")
        self.preflight_tree.heading("required", text="Required")
        self.preflight_tree.heading("source", text="Source")
        self.preflight_tree.column("#0", width=220)
        self.preflight_tree.column("state", width=90, anchor="center")
        self.preflight_tree.column("actual", width=280)
        self.preflight_tree.column("required", width=280)
        self.preflight_tree.column("source", width=180)
        self.preflight_tree.tag_configure("pass", foreground="#08765b")
        self.preflight_tree.tag_configure("warn", foreground="#8a5a00")
        self.preflight_tree.tag_configure("fail", foreground="#b42318")
        self.preflight_tree.grid(row=2, column=0, sticky="nsew")

    def _build_recovery_tab(self) -> None:
        self.recovery_tab = ttk.Frame(self.notebook, padding=10)
        self.recovery_tab.columnconfigure(0, weight=1)
        self.recovery_tab.rowconfigure(1, weight=1)
        self.notebook.add(self.recovery_tab, text="Recovery")
        self.recovery_summary = tk.StringVar(value="No replay or live Croí recovery evidence yet")
        ttk.Label(
            self.recovery_tab,
            textvariable=self.recovery_summary,
            style="Muted.TLabel",
            wraplength=960,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        panes = ttk.PanedWindow(self.recovery_tab, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.recovery_tree = ttk.Treeview(
            left,
            columns=("time", "state", "event"),
            show="tree headings",
        )
        self.recovery_tree.heading("#0", text="Source")
        self.recovery_tree.heading("time", text="Time")
        self.recovery_tree.heading("state", text="State")
        self.recovery_tree.heading("event", text="Event")
        self.recovery_tree.column("#0", width=140)
        self.recovery_tree.column("time", width=120)
        self.recovery_tree.column("state", width=140)
        self.recovery_tree.column("event", width=300)
        self.recovery_tree.grid(row=0, column=0, sticky="nsew")

        self.recovery_state_plot = LinePlot(right, height=220)
        self.recovery_airbrake_plot = LinePlot(right, height=220)
        self.recovery_state_plot.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        self.recovery_airbrake_plot.grid(row=1, column=0, sticky="nsew")
        self.after(100, lambda: self._set_initial_equal_split(panes))

    def _build_radio_tab(self) -> None:
        self.radio_tab = ttk.Frame(self.notebook, padding=10)
        self.radio_tab.columnconfigure(0, weight=1)
        self.notebook.add(self.radio_tab, text="Radio")

        ttk.Label(self.radio_tab, text="Teachtaire Telemetry Schedule", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            self.radio_tab,
            text="Event packets remain immediate. Periods below control scheduled packet classes.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))
        fields = ttk.LabelFrame(self.radio_tab, text="Packet Periods")
        fields.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        fields.columnconfigure(1, weight=1)
        specs = (
            ("Core flight + IMU", self.radio_core_period, 100, 5000),
            ("GPS", self.radio_gps_period, 200, 10000),
            ("Power + actuator + pyro", self.radio_slow_period, 200, 10000),
            ("Heartbeat + deep health", self.radio_health_period, 500, 30000),
        )
        for row, (label, variable, minimum, maximum) in enumerate(specs):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
            input_widget = ttk.Spinbox(
                fields,
                from_=minimum,
                to=maximum,
                increment=100,
                textvariable=variable,
            )
            input_widget.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
            self.radio_widgets.append(input_widget)
            ttk.Label(fields, text="ms", style="Muted.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=6)
        radio_flash = ttk.Button(
            self.radio_tab,
            text="Flash Teachtaire Radio Policy",
            command=self.flash_teachtaire_radio_config,
            style="Firmware.TButton",
        )
        radio_flash.grid(row=3, column=0, sticky="ew")
        self.radio_flash_widgets = [radio_flash]

    def _build_logging_tab(self) -> None:
        self.logging_tab = ttk.Frame(self.notebook, padding=10)
        self.logging_tab.columnconfigure(0, weight=1)
        self.notebook.add(self.logging_tab, text="Logging")
        ttk.Label(self.logging_tab, text="Croí Blackbox Policy", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        fields = ttk.LabelFrame(self.logging_tab, text="Flight Log")
        fields.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text="Sample Period").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        sample_period = ttk.Spinbox(
            fields,
            from_=20,
            to=1000,
            increment=20,
            textvariable=self.logging_sample_period,
        )
        sample_period.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(fields, text="ms", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(fields, text="Guaranteed Flight Logging").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        minimum_flight = ttk.Spinbox(
            fields,
            from_=1,
            to=120,
            increment=1,
            textvariable=self.logging_minimum_flight_minutes,
        )
        minimum_flight.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(fields, text="min", style="Muted.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(fields, text="Post-Landing Duration").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        post_landing = ttk.Spinbox(
            fields,
            from_=0,
            to=600000,
            increment=1000,
            textvariable=self.logging_post_landing,
        )
        post_landing.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(fields, text="ms", style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=6)
        remote_can = ttk.Checkbutton(
            fields,
            text="Store remote CAN events",
            variable=self.logging_include_remote_can,
        )
        remote_can.grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=6)
        logging_flash = ttk.Button(
            self.logging_tab,
            text="Flash Croí Locked Configuration",
            command=self.flash_croi_mission,
            style="Firmware.TButton",
        )
        logging_flash.grid(row=2, column=0, sticky="ew")
        self.logging_widgets = [sample_period, minimum_flight, post_landing, remote_can]
        self.logging_flash_widgets = [logging_flash]

    def _build_fault_tab(self) -> None:
        self.fault_tab = ttk.Frame(self.notebook, padding=10)
        self.fault_tab.columnconfigure(0, weight=1)
        self.fault_tab.rowconfigure(1, weight=1)
        self.notebook.add(self.fault_tab, text="Faults")
        header = ttk.Frame(self.fault_tab)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        self.fault_summary = tk.StringVar(value="No faults observed")
        ttk.Label(header, textvariable=self.fault_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Export", command=self.export_fault_ledger, style="Data.TButton").grid(
            row=0, column=1, sticky="e"
        )
        self.fault_tree = ttk.Treeview(
            self.fault_tab,
            columns=("state", "severity", "source", "count", "last", "detail"),
            show="tree headings",
        )
        self.fault_tree.heading("#0", text="Fault")
        self.fault_tree.heading("state", text="State")
        self.fault_tree.heading("severity", text="Severity")
        self.fault_tree.heading("source", text="Source")
        self.fault_tree.heading("count", text="Count")
        self.fault_tree.heading("last", text="Last Seen UTC")
        self.fault_tree.heading("detail", text="Detail")
        self.fault_tree.column("#0", width=220)
        self.fault_tree.column("state", width=90)
        self.fault_tree.column("severity", width=100)
        self.fault_tree.column("source", width=110)
        self.fault_tree.column("count", width=70)
        self.fault_tree.column("last", width=220)
        self.fault_tree.column("detail", width=340)
        self.fault_tree.tag_configure("active", foreground="#b42318")
        self.fault_tree.tag_configure("resolved", foreground="#52637a")
        self.fault_tree.grid(row=1, column=0, sticky="nsew")

    def _add_toolbar_button(
        self,
        group: str,
        key: str,
        text: str,
        command: Callable[[], None],
        style_name: str = "TButton",
    ) -> ttk.Button:
        if group not in self.toolbar_groups:
            frame = ttk.LabelFrame(self.toolbar_actions, text=group, padding=4)
            frame.columnconfigure(0, weight=1, uniform=f"{group}-button")
            frame.columnconfigure(1, weight=1, uniform=f"{group}-button")
            self.toolbar_groups[group] = frame
            self.toolbar_group_buttons[group] = []
            self.toolbar_group_order.append(group)
        button = ttk.Button(self.toolbar_groups[group], text=text, command=command, style=style_name)
        self.toolbar_buttons.append(button)
        self.toolbar_group_buttons[group].append((key, button))
        self.action_buttons[key] = button
        return button

    def _bind_notebook_clicks(self, notebook: ttk.Notebook) -> None:
        notebook.bind("<ButtonRelease-1>", self._select_notebook_tab, add="+")

    @staticmethod
    def _select_notebook_tab(event: tk.Event[tk.Misc]) -> str | None:
        notebook = event.widget
        if not isinstance(notebook, ttk.Notebook):
            return None
        try:
            tab_index = notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return None
        notebook.select(tab_index)
        return None

    def _set_initial_equal_split(self, pane: ttk.PanedWindow, attempt: int = 0) -> None:
        pane_key = str(pane)
        if pane_key in self._equalized_panes:
            return
        width = pane.winfo_width()
        if width <= 1 and attempt < 10:
            self.after(50, lambda: self._set_initial_equal_split(pane, attempt + 1))
            return
        if width > 1:
            pane.sashpos(0, width // 2)
            self._equalized_panes.add(pane_key)

    @staticmethod
    def _visible_toolbar_keys(board_id: str) -> set[str]:
        keys = {
            "probe", "detect", "stop_link", "doctor",
            "validate", "build", "flash", "flash_detected",
            "read_status", "poll", "stop_poll", "clear_plot", "health", "save_status",
            "import_telemetry", "live_telemetry", "stop_telemetry", "import_can",
            "open_session",
        }
        if board_id == "croi":
            keys.update(("read_croi_flash", "import_croi_dump", "wipe_croi_flash"))
        elif board_id == "teachtaire":
            keys.add("teachtaire_test")
        elif board_id == "lamh":
            keys.add("lamh_servo_test")
        elif board_id == "foinse":
            keys.add("foinse_monitor")
        elif board_id == "groundstation":
            keys.update(("import_groundstation", "groundstation_usb", "telemetry_usb"))
        return keys

    def _update_toolbar_visibility(self) -> None:
        visible_keys = self._visible_toolbar_keys(self.selected_board.get())
        self.toolbar_visible_groups = []
        for group in self.toolbar_group_order:
            visible_buttons = [
                button for key, button in self.toolbar_group_buttons[group] if key in visible_keys
            ]
            for _key, button in self.toolbar_group_buttons[group]:
                button.grid_forget()
            for index, button in enumerate(visible_buttons):
                row, column = divmod(index, 2)
                button.grid(row=row, column=column, sticky="ew", padx=2, pady=2)
            if visible_buttons:
                self.toolbar_visible_groups.append(group)
        self._toolbar_columns = 0
        self._layout_toolbar_buttons()

    def _on_toolbar_configure(self, event: tk.Event[tk.Misc]) -> None:
        self._layout_toolbar_buttons(event.width)

    def _layout_toolbar_buttons(self, width: int | None = None) -> None:
        if not self.toolbar_visible_groups:
            return
        width = width if width is not None else self.toolbar_actions.winfo_width()
        if width <= 1:
            return
        min_group_width = 260
        columns = max(1, min(len(self.toolbar_visible_groups), width // min_group_width))
        if columns == self._toolbar_columns:
            return
        previous_columns = self._toolbar_columns
        self._toolbar_columns = columns
        for column in range(max(previous_columns, columns)):
            self.toolbar_actions.columnconfigure(column, weight=0, uniform="toolbar")
        for column in range(columns):
            self.toolbar_actions.columnconfigure(column, weight=1, uniform="toolbar")
        for frame in self.toolbar_groups.values():
            frame.grid_forget()
        for index, group in enumerate(self.toolbar_visible_groups):
            row, column = divmod(index, columns)
            self.toolbar_groups[group].grid(row=row, column=column, sticky="nsew", padx=3, pady=3)

    def _on_board_tab_configure(self, event: tk.Event[tk.Misc]) -> None:
        self.profile_notes.configure(wraplength=max(320, event.width - 30))

    def _on_right_pane_configure(self, event: tk.Event[tk.Misc]) -> None:
        self.action_hint.configure(wraplength=max(220, event.width - 16))

    def _load_can_table(self) -> None:
        try:
            frames = load_payload_layouts(PAYLOAD_LAYOUTS_CSV)
            attach_can_ids(frames, load_can_ids(CAN_FRAMES_HEADER))
            for frame in frames.values():
                parent = self.can_tree.insert("", "end", text=frame.name, values=(self._format_can_id(frame.can_id), "", "", "", ""))
                for field in frame.fields:
                    self.can_tree.insert(
                        parent,
                        "end",
                        text=field.field_name,
                        values=("", field.bytes_, field.type_name, field.scale, field.notes),
                    )
            self.can_summary.configure(
                text="Croí CAN monitor waits for Croí Read Status/Poll. Definitions use Ogma Console's pinned CAN contract."
            )
            self._clear_croi_can_panel()
        except Exception as exc:
            self.log(f"CAN table load failed: {exc}")

    @staticmethod
    def _format_can_id(can_id: int | None) -> str:
        return "" if can_id is None else f"0x{can_id:03x}"

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _yes_no(value: Any) -> str:
        return "yes" if OgmaApp._as_int(value) != 0 else "no"

    @staticmethod
    def croi_can_diagnosis(
        *,
        init_ok: int,
        bus_off: int,
        can_error: int,
        retry_depth: int,
        retry_drops: int,
        active_nodes: int,
    ) -> str:
        if init_ok == 0:
            return "CAN not initialized"
        if bus_off != 0:
            return "bus-off"
        if retry_depth >= CROI_CAN_RETRY_QUEUE_LEN and retry_drops > 0:
            if active_nodes == 0:
                return "TX stuck/no ACK: no live peer, standby transceiver, wiring, or bitrate fault"
            return "TX stuck: retry queue full"
        if can_error != 0:
            return f"driver error 0x{can_error:x}"
        if retry_depth > 0:
            return "TX backlog"
        if active_nodes == 0:
            return "no heartbeat peers seen"
        return "ok"

    def _clear_croi_can_panel(self) -> None:
        for item in self.can_health_tree.get_children():
            self.can_health_tree.delete(item)
        self.can_health_tree.insert("", "end", text="source", values=("waiting for Croí status", ""))
        self.can_health_tree.insert("", "end", text="scope", values=("health counters only", ""))
        self.can_traffic_plot.set_series("Croí CAN Traffic", "count", {})
        self.can_data_plot.set_series("Croí CAN State", "state", {})

    def _append_can_point(self, key: str, value: float, now: float) -> list[tuple[float, float]]:
        points = self.can_history.setdefault(key, [])
        points.append((now, value))
        start = points[0][0] if points else now
        return [(x - start, y) for x, y in points[-240:]]

    def _update_croi_can_panel(self, status: dict[str, Any]) -> None:
        uptime_ms = self._as_int(status.get("uptime_ms"))
        last_tx_heartbeat_ms = self._as_int(status.get("can_last_heartbeat_ms"))
        heartbeat_age_ms = max(0, uptime_ms - last_tx_heartbeat_ms) if uptime_ms else 0
        bus_off = self._as_int(status.get("can_bus_off"))
        can_error = self._as_int(status.get("can_error"))
        retry_depth = self._as_int(status.get("can_tx_retry_depth"))
        retry_drops = self._as_int(status.get("can_tx_retry_drops"))
        timeouts = self._as_int(status.get("can_node_timeout_count"))
        active_nodes = self._as_int(status.get("can_active_nodes"))
        init_ok = self._as_int(status.get("can_init_ok"))
        bus_state = "bus-off" if bus_off else "ok"
        diagnosis = self.croi_can_diagnosis(
            init_ok=init_ok,
            bus_off=bus_off,
            can_error=can_error,
            retry_depth=retry_depth,
            retry_drops=retry_drops,
            active_nodes=active_nodes,
        )
        backlog_state = "full" if retry_depth >= CROI_CAN_RETRY_QUEUE_LEN else str(retry_depth)

        for item in self.can_health_tree.get_children():
            self.can_health_tree.delete(item)
        rows = (
            ("source", "Croí status block", ""),
            ("diagnosis", diagnosis, ""),
            ("can init", self._yes_no(init_ok), ""),
            ("bus state", bus_state, ""),
            ("driver error", self._yes_no(can_error), ""),
            ("active nodes", active_nodes, ""),
            ("tx retry depth", backlog_state, ""),
            ("tx retry drops", retry_drops, ""),
            ("node timeouts", timeouts, ""),
            ("last heartbeat tx age", heartbeat_age_ms, "ms"),
            ("scope", "no per-frame counters yet", ""),
        )
        for field, value, unit in rows:
            self.can_health_tree.insert("", "end", text=field, values=(str(value), unit))

        self.can_summary.configure(
            text=(
                "Croí CAN monitor: "
                f"init={self._yes_no(init_ok)} bus={bus_state} nodes={active_nodes} "
                f"retry={retry_depth} drops={retry_drops} timeouts={timeouts}; {diagnosis}"
            )
        )

        now = time.time()
        self.can_traffic_plot.set_series(
            "Croí CAN Traffic",
            "count",
            {
                "nodes": self._append_can_point("can.active_nodes", float(active_nodes), now),
                "retry": self._append_can_point("can.retry_depth", float(retry_depth), now),
                "drops": self._append_can_point("can.retry_drops", float(retry_drops), now),
                "timeouts": self._append_can_point("can.timeouts", float(timeouts), now),
            },
        )
        self.can_data_plot.set_series(
            "Croí CAN State",
            "state",
            {
                "busoff": self._append_can_point("can.bus_off", float(bus_off), now),
                "err": self._append_can_point("can.error", float(1 if can_error else 0), now),
                "hb_s": self._append_can_point("can.heartbeat_age_s", float(heartbeat_age_ms) / 1000.0, now),
            },
        )

    def _on_board_list(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self.board_list.curselection()
        if not selection:
            return
        self._select_board(PROFILES[selection[0]].board_id)

    def _select_board(self, board_id: str) -> None:
        profile = profile_for(board_id)
        self.selected_board.set(board_id)
        envs = profile.env_names()
        self.env_combo["values"] = envs
        self.selected_env.set(profile.default_env or (envs[0] if envs else ""))
        self.header_board.set(profile.display_name)
        self.header_env.set(self.selected_env.get() or "N/A")
        self.profile_title.configure(text=f"{profile.display_name} / {profile.board_id}")
        note = f"{profile.role}. {profile.notes}".strip()
        self.profile_notes.configure(text=note)
        self.action_hint.configure(text=self._action_hint(profile))
        self._clear_status()
        self._update_toolbar_visibility()
        self._update_action_states()

    def _action_hint(self, profile: BoardProfile) -> str:
        if profile.board_id == "teachtaire":
            return "Use env selector for flight, LoRa TX test, or LoRa RX test. Read Status shows GNSS and LoRa counters."
        if profile.board_id == "lamh":
            return "Read Status shows PCA9685 debug. Servo outputs are PWM1/PWM2/PWM3/PWM4 mapped to PCA channels 0/2/4/6."
        if profile.board_id == "croi":
            return "Read Status checks IMU, baro, CAN, and logger health. Read Croí Flash parses FlashLogger records and saves CSV/JSON locally."
        if profile.board_id == "foinse":
            return "Read Status shows ACS71240 battery and servo rail current. Plot tracks bat/servo current."
        if profile.board_id == "groundstation":
            return "Import or capture Groundstation telemetry as lat,lon,sat,alt,fix_time[,rssi] lines or JSONL packets."
        return "Profile registered. Firmware support incomplete in current repo state."

    @staticmethod
    def _mission_channel(value: str) -> int | None:
        return None if value == "Disabled" else int(value.rsplit(" ", 1)[-1])

    @staticmethod
    def _mission_channel_text(channel: int | None) -> str:
        return "Disabled" if channel is None else f"Channel {channel}"

    def _refresh_pyro_channel_options(self) -> None:
        drogue = getattr(self, "mission_drogue_channel_combo", None)
        main = getattr(self, "mission_main_channel_combo", None)
        if drogue is None or main is None:
            return
        drogue.configure(values=available_pyro_channel_values(self.mission_main_channel.get()))
        main.configure(values=available_pyro_channel_values(self.mission_drogue_channel.get()))

    def _apply_mission_config(self, config: MissionConfig) -> None:
        values = (
            ("mission_name", tk.StringVar, config.name),
            ("mission_liftoff_accel", tk.StringVar, f"{config.liftoff_accel_m_s2:.2f}"),
            ("mission_imu_axis", tk.StringVar, ("X", "Y", "Z")[config.imu_vertical_axis]),
            ("mission_imu_sign", tk.StringVar, "Positive" if config.imu_vertical_sign > 0 else "Negative"),
            ("mission_main_altitude", tk.IntVar, config.main_deploy_altitude_m),
            ("mission_drogue_delay", tk.IntVar, config.drogue_delay_ms),
            ("mission_airbrake_enabled", tk.BooleanVar, config.airbrake_enabled),
            ("mission_airbrake_channel", tk.IntVar, config.airbrake_channel + 1),
            ("mission_airbrake_retracted_angle", tk.IntVar, config.airbrake_retracted_angle_deg),
            ("mission_airbrake_max_angle", tk.IntVar, config.airbrake_max_angle_deg),
            ("mission_airbrake_delay", tk.IntVar, config.airbrake_start_delay_ms),
            ("mission_airbrake_stow_delay", tk.IntVar, config.airbrake_stow_delay_ms),
            ("mission_airbrake_watchdog", tk.IntVar, config.airbrake_command_timeout_ms),
            ("mission_drogue_channel", tk.StringVar, self._mission_channel_text(config.pyro_drogue_channel)),
            ("mission_main_channel", tk.StringVar, self._mission_channel_text(config.pyro_main_channel)),
        )
        for name, variable_type, value in values:
            variable = getattr(self, name, None)
            if variable is None:
                setattr(self, name, variable_type(value=value))
            else:
                variable.set(value)

    def _apply_recovery_config(self, config: RecoveryFallbackConfig) -> None:
        values = (
            ("mission_main_backup_enabled", tk.BooleanVar, config.main_backup_enabled),
            ("mission_main_backup_after_apogee", tk.IntVar, config.after_apogee_ms),
            ("mission_main_backup_descent_speed", tk.StringVar, f"{config.descent_speed_m_s:g}"),
            ("mission_main_backup_min_altitude", tk.IntVar, config.min_altitude_m),
            ("mission_main_backup_max_altitude", tk.IntVar, config.max_altitude_m),
            ("mission_main_backup_required_samples", tk.IntVar, config.required_samples),
        )
        for name, variable_type, value in values:
            variable = getattr(self, name, None)
            if variable is None:
                setattr(self, name, variable_type(value=value))
            else:
                variable.set(value)

    def _apply_radio_config(self, config: RadioPolicy) -> None:
        values = (
            ("radio_core_period", config.core_period_ms),
            ("radio_gps_period", config.gps_period_ms),
            ("radio_slow_period", config.slow_period_ms),
            ("radio_health_period", config.health_period_ms),
        )
        for name, value in values:
            variable = getattr(self, name, None)
            if variable is None:
                setattr(self, name, tk.IntVar(value=value))
            else:
                variable.set(value)

    def _apply_logging_config(self, config: LoggingPolicy) -> None:
        values = (
            ("logging_sample_period", tk.IntVar, config.flight_sample_period_ms),
            ("logging_minimum_flight_minutes", tk.IntVar, config.minimum_flight_ms // 60000),
            ("logging_post_landing", tk.IntVar, config.post_landing_ms),
            ("logging_include_remote_can", tk.BooleanVar, config.include_remote_can),
        )
        for name, variable_type, value in values:
            variable = getattr(self, name, None)
            if variable is None:
                setattr(self, name, variable_type(value=value))
            else:
                variable.set(value)

    def _logging_policy_from_ui(self) -> LoggingPolicy:
        config = LoggingPolicy(
            flight_sample_period_ms=self.logging_sample_period.get(),
            minimum_flight_ms=self.logging_minimum_flight_minutes.get() * 60000,
            post_landing_ms=self.logging_post_landing.get(),
            include_remote_can=self.logging_include_remote_can.get(),
        )
        config.validate()
        return config

    def _radio_policy_from_ui(self) -> RadioPolicy:
        config = RadioPolicy(
            core_period_ms=self.radio_core_period.get(),
            gps_period_ms=self.radio_gps_period.get(),
            slow_period_ms=self.radio_slow_period.get(),
            health_period_ms=self.radio_health_period.get(),
        )
        config.validate()
        return config

    def _mission_config_from_ui(self) -> MissionConfig:
        return MissionConfig.from_values(
            name=self.mission_name.get(),
            liftoff_accel_m_s2=float(self.mission_liftoff_accel.get()),
            imu_vertical_axis=("X", "Y", "Z").index(self.mission_imu_axis.get()),
            imu_vertical_sign=1 if self.mission_imu_sign.get() == "Positive" else -1,
            main_deploy_altitude_m=self.mission_main_altitude.get(),
            drogue_delay_ms=self.mission_drogue_delay.get(),
            airbrake_enabled=self.mission_airbrake_enabled.get(),
            airbrake_channel=self.mission_airbrake_channel.get() - 1,
            airbrake_retracted_angle_deg=self.mission_airbrake_retracted_angle.get(),
            airbrake_max_angle_deg=self.mission_airbrake_max_angle.get(),
            airbrake_start_delay_ms=self.mission_airbrake_delay.get(),
            airbrake_stow_delay_ms=self.mission_airbrake_stow_delay.get(),
            airbrake_command_timeout_ms=self.mission_airbrake_watchdog.get(),
            pyro_drogue_channel=self._mission_channel(self.mission_drogue_channel.get()),
            pyro_main_channel=self._mission_channel(self.mission_main_channel.get()),
        )

    def _manifest_from_ui(self) -> FlightManifest:
        recovery = RecoveryFallbackConfig(
            main_backup_enabled=self.mission_main_backup_enabled.get(),
            after_apogee_ms=self.mission_main_backup_after_apogee.get(),
            descent_speed_m_s=float(self.mission_main_backup_descent_speed.get()),
            min_altitude_m=self.mission_main_backup_min_altitude.get(),
            max_altitude_m=self.mission_main_backup_max_altitude.get(),
            required_samples=self.mission_main_backup_required_samples.get(),
        )
        manifest = replace(
            self.flight_manifest,
            mission=self._mission_config_from_ui(),
            lamh_safety=LamhSafetyConfig.from_values(angle.get() for angle in self.lamh_safe_angles),
            recovery=recovery,
            logging=self._logging_policy_from_ui(),
            radio=self._radio_policy_from_ui(),
        )
        manifest.validate()
        return manifest

    def _apply_flight_manifest(self, manifest: FlightManifest) -> None:
        manifest.validate()
        self.flight_manifest = manifest
        self._apply_mission_config(manifest.mission)
        self._apply_recovery_config(manifest.recovery)
        self._apply_logging_config(manifest.logging)
        self._apply_radio_config(manifest.radio)
        for variable, angle in zip(self.lamh_safe_angles, manifest.lamh_safety.angles_deg):
            variable.set(angle)
        self.refresh_mission_preview()

    def refresh_mission_preview(self) -> None:
        tree = getattr(self, "mission_timeline_tree", None)
        audit = getattr(self, "mission_audit", None)
        if tree is None or audit is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        try:
            manifest = self._manifest_from_ui()
            config = manifest.mission
        except (tk.TclError, ValueError) as exc:
            audit.set(f"INVALID: {exc}")
            if hasattr(self, "preflight_tree"):
                self.refresh_preflight()
            return
        safety_error = self._mission_airbrake_safety_error(config)
        pyro_state = "pyro disabled" if config.pyro_drogue_channel is None and config.pyro_main_channel is None else "LIVE PYRO"
        ready_state = f"BLOCKED: {safety_error}" if safety_error else "VALID"
        audit.set(
            f"{ready_state} | schema {CROI_MISSION_CONFIG_SCHEMA_VERSION} | "
            f"CRC 0x{config.crc32(manifest.recovery, manifest.logging):08X} | manifest {manifest.sha256()[:12]} | {pyro_state}"
        )
        for event in build_mission_timeline(config, manifest.recovery, manifest.logging):
            tree.insert(
                "",
                "end",
                text=event.trigger,
                values=(event.state, event.action, event.guard),
            )
        if hasattr(self, "preflight_tree"):
            self.refresh_preflight()

    def refresh_preflight(self) -> None:
        tree = getattr(self, "preflight_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        try:
            manifest = self._manifest_from_ui()
        except (tk.TclError, ValueError) as exc:
            self.current_preflight_report = None
            self.preflight_summary.set("NO-GO | invalid manifest")
            self.preflight_summary_label.configure(style="PreflightNoGo.TLabel")
            tree.insert(
                "",
                "end",
                text="manifest",
                values=("FAIL", str(exc), "valid manifest", "local"),
                tags=("fail",),
            )
            return

        now = time.monotonic()
        statuses = {
            board_id: StatusEvidence(status, max(0.0, now - observed_at))
            for board_id, (status, observed_at) in self.status_evidence_cache.items()
        }
        telemetry_age = None
        if self.latest_telemetry is not None and self.latest_telemetry_updated_at > 0.0:
            telemetry_age = max(0.0, now - self.latest_telemetry_updated_at)
        report = evaluate_preflight(manifest, statuses, self.latest_telemetry, telemetry_age)
        self.current_preflight_report = report
        categories: dict[str, str] = {}
        for check in report.checks:
            parent = categories.get(check.category)
            if parent is None:
                parent = tree.insert("", "end", text=check.category, open=True)
                categories[check.category] = parent
            tree.insert(
                parent,
                "end",
                text=check.name,
                values=(check.state.upper(), check.actual, check.required, check.source),
                tags=(check.state,),
            )
        state = "GO" if report.go else "NO-GO"
        self.preflight_summary.set(
            f"{state} | failures {report.failures} | warnings {report.warnings} | manifest {manifest.sha256()[:12]}"
        )
        self.preflight_summary_label.configure(
            style="PreflightGo.TLabel" if report.go else "PreflightNoGo.TLabel"
        )

    def _mission_airbrake_safety_error(self, config: MissionConfig) -> str | None:
        if not config.airbrake_enabled:
            return None
        safe_angle = self.lamh_safety_config.angles_deg[config.airbrake_channel]
        if config.airbrake_retracted_angle_deg == safe_angle:
            return None
        return (
            f"Lamh output {config.airbrake_channel + 1} failsafe is {safe_angle} deg, "
            f"but mission retract is {config.airbrake_retracted_angle_deg} deg"
        )

    def _clear_status(self) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_history.clear()
        self.latest_status = None
        self.plot.set_points("", "", [])

    def _run_worker(self, label: str, fn: Callable[[], Any]) -> None:
        if self.worker_active:
            self.log("worker already running")
            return
        self.worker_active = True
        self.worker_label = label
        self.header_activity.set(label.title())
        self.stop_requested.clear()
        self._update_action_states()

        def runner() -> None:
            self.thread_log(f"{label} started")
            try:
                result = fn()
                self.events.put(("worker_done", (label, result, None)))
            except Exception as exc:
                self.events.put(("worker_done", (label, None, exc)))

        threading.Thread(target=runner, daemon=True).start()

    def detect(self) -> None:
        self._run_worker("detect", self.controller.detect)

    def validate_selected(self) -> None:
        expected = self.selected_board.get()
        self._run_worker(
            "validate",
            lambda: run_bench_validation(self.controller, expected, RUNS_ROOT / "validation"),
        )

    def probe_link(self) -> None:
        self._run_worker("probe", probe_stlink)

    def doctor_selected(self) -> None:
        board_id = self.selected_board.get()
        self._run_worker("doctor", lambda: collect_diagnostics(board_id))

    def build_selected(self) -> None:
        board_id, env = self._selected_board_env()
        self._run_worker("build", lambda: self.controller.build(board_id, env))

    def _confirm_bench_action(self, title: str, detail: str) -> bool:
        return messagebox.askyesno(
            title,
            f"{detail}\n\nBench/debug use only. Continue?",
        )

    def flash_selected(self) -> None:
        board_id, env = self._selected_board_env()
        if board_id == "pleasc" and env == "stm32f072c8t6_rev1_pyro":
            if not messagebox.askyesno(
                "Enable Pleasc Rev1 firing",
                "This image can energize pyrotechnic outputs. External RBF/pyro-power disconnect is mandatory. Continue?",
            ):
                return
        if not self._confirm_bench_action("Flash firmware", f"Flash {board_id} env {env}?"):
            return
        self._run_worker("flash", lambda: self.controller.flash(board_id, env))

    def flash_detected(self) -> None:
        if not self._confirm_bench_action("Flash detected board", "Detect the connected board and flash its default firmware?"):
            return
        self._run_worker("flash detected", lambda: self.controller.flash_detected())

    def read_status(self) -> None:
        board_id, env = self._selected_board_env()
        self._run_worker("read status", lambda: (board_id, env, self.controller.read_status(board_id, env)))

    def health_selected(self) -> None:
        board_id, env = self._selected_board_env()

        def read_health() -> tuple[str, str, dict[str, Any], HealthReport]:
            status = self.controller.read_status(board_id, env)
            return board_id, env, status, evaluate_health(board_id, status)

        self._run_worker("health", read_health)

    def poll_status(self) -> None:
        board_id, env = self._selected_board_env()

        def poll() -> tuple[str, str, dict[str, Any], Path]:
            end = time.time() + 30.0
            latest: dict[str, Any] = {}
            start = time.monotonic()
            generation = self.plot_generation
            samples: list[dict[str, Any]] = []

            def clear_poll_samples_if_requested() -> None:
                nonlocal start, generation
                if not self.clear_poll_data_requested.is_set():
                    return
                samples.clear()
                start = time.monotonic()
                generation = self.plot_generation
                self.clear_poll_data_requested.clear()
                self.thread_log("poll data cleared")

            self.clear_poll_data_requested.clear()
            while time.time() < end and not self.stop_requested.is_set():
                clear_poll_samples_if_requested()
                latest = self.controller.read_status(board_id, env)
                clear_poll_samples_if_requested()
                samples.append(make_status_sample(time.monotonic() - start, latest))
                self.events.put(("status", (board_id, env, latest, generation)))
                if self.stop_requested.wait(0.5):
                    break
            out = save_status_series(profile_for(board_id), env, samples, RUNS_ROOT / "status")
            return board_id, env, latest, out

        self._run_worker("poll status", poll)

    def run_teachtaire_test(self) -> None:
        env = self.selected_env.get()
        mode = teachtaire_mode_for_env(env)
        if not self._confirm_bench_action("Teachtaire test", f"Flash and run Teachtaire {mode} test firmware?"):
            return
        duration = simpledialog.askfloat("Teachtaire test", "Poll seconds", initialvalue=30.0, minvalue=0.1)
        if duration is None:
            return
        self._run_worker(
            "teachtaire test",
            lambda: run_teachtaire_test(
                self.controller,
                mode,
                duration,
                0.5,
                RUNS_ROOT / "board_tests",
                flash=True,
            ),
        )

    def run_lamh_servo_test(self) -> None:
        if not self._confirm_bench_action("Lámh servo test", "Move Lámh servo outputs through a commanded angle list?"):
            return
        output = simpledialog.askinteger("Lámh servo test", "Output 1-4", initialvalue=int(self.servo_channel.get()), minvalue=1, maxvalue=4)
        if output is None:
            return
        angles_text = simpledialog.askstring("Lámh servo test", "Angles", initialvalue="0,30,60,90")
        if angles_text is None:
            return
        env = self.selected_env.get() or None
        self._run_worker(
            "lamh servo test",
            lambda: run_lamh_servo_test(
                self.controller,
                output,
                parse_angle_list(angles_text),
                0.25,
                RUNS_ROOT / "board_tests",
                env=env,
            ),
        )

    def run_foinse_monitor(self) -> None:
        duration = simpledialog.askfloat("Foinse monitor", "Sample seconds", initialvalue=30.0, minvalue=0.1)
        if duration is None:
            return
        env = self.selected_env.get() or None
        self._run_worker(
            "foinse monitor",
            lambda: run_foinse_monitor(
                self.controller,
                duration,
                0.5,
                RUNS_ROOT / "board_tests",
                env=env,
            ),
        )

    def send_servo(self) -> None:
        env = self.selected_env.get() or None
        output = servo_output_for(profile_for("lamh"), int(self.servo_channel.get()))
        angle = int(self.servo_angle.get())
        if not self._confirm_bench_action("Lámh servo command", f"Move {output.label} / PCA channel {output.pca_channel} to {angle} degrees?"):
            return
        self._run_worker("servo command", lambda: self.controller.send_lamh_servo_command(output.pca_channel, angle, env))

    def flash_lamh_safety_config(self) -> None:
        try:
            config = LamhSafetyConfig.from_values(angle.get() for angle in self.lamh_safe_angles)
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Lámh failsafe angles", str(exc))
            return
        if self.selected_board.get() != "lamh":
            messagebox.showerror("Lámh failsafe angles", "Select Lámh before flashing failsafe angles")
            return
        env = self.selected_env.get() or None
        values = ", ".join(f"PWM{index + 1}={angle}deg" for index, angle in enumerate(config.angles_deg))
        if not self._confirm_bench_action(
            "Flash Lámh failsafe angles",
            f"Build, flash, and verify flight firmware with {values}?",
        ):
            return
        self._run_worker(
            "flash Lámh safety config",
            lambda: self.controller.flash_lamh_safety_config(config, env),
        )

    def flash_teachtaire_radio_config(self) -> None:
        try:
            config = self._radio_policy_from_ui()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Teachtaire Radio", str(exc))
            return
        if self.selected_board.get() != "teachtaire":
            messagebox.showerror("Teachtaire Radio", "Select Teachtaire before flashing radio policy")
            return
        details = (
            f"core={config.core_period_ms} ms, GPS={config.gps_period_ms} ms, "
            f"slow={config.slow_period_ms} ms, health={config.health_period_ms} ms"
        )
        if not messagebox.askyesno(
            "Flash Teachtaire Radio Policy",
            f"Build, flash, and verify Teachtaire flight firmware with {details}?",
        ):
            return
        self.flight_manifest = replace(self.flight_manifest, radio=config)
        env = self.selected_env.get() or None
        self._run_worker(
            "flash Teachtaire radio policy",
            lambda: self.controller.flash_teachtaire_radio_config(config, env),
        )

    def save_mission(self) -> None:
        try:
            manifest = self._manifest_from_ui()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Mission", str(exc))
            return
        self.flight_manifest = manifest
        path = save_flight_manifest(RUNS_ROOT / "manifests", manifest)
        self.log(f"flight manifest saved: {path} (SHA-256 {manifest.sha256()})")

    def load_mission(self) -> None:
        path = filedialog.askopenfilename(
            title="Load flight manifest",
            filetypes=(("Ogma manifests", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            manifest = load_flight_manifest(Path(path), self.lamh_safety_config)
            self._apply_flight_manifest(manifest)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Mission", str(exc))
            return
        self._update_action_states()
        self.log(f"flight manifest loaded: {path} (SHA-256 {manifest.sha256()})")

    def replay_flight_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Replay flight profile",
            filetypes=(("CSV flight profiles", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            manifest = self._manifest_from_ui()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Flight Replay", str(exc))
            return
        source = Path(path)

        def replay() -> ReplaySession:
            samples = tuple(load_replay_csv(source))
            return ReplaySession(str(source), samples, run_firmware_replay(manifest, list(samples)))

        self._run_worker("flight replay", replay)

    def replay_synthetic_flight(self) -> None:
        try:
            manifest = self._manifest_from_ui()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Flight Replay", str(exc))
            return

        def replay() -> ReplaySession:
            samples = tuple(synthetic_nominal_profile())
            return ReplaySession("synthetic nominal profile", samples, run_firmware_replay(manifest, list(samples)))

        self._run_worker("synthetic flight replay", replay)

    def build_current_flight_package(self) -> None:
        try:
            manifest = self._manifest_from_ui()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Flight Package", str(exc))
            return
        self.refresh_preflight()
        path = filedialog.asksaveasfilename(
            title="Build flight package",
            initialfile=f"{manifest.mission.name}.zip",
            defaultextension=".zip",
            filetypes=(("Ogma flight package", "*.zip"),),
        )
        if not path:
            return
        report = self.current_preflight_report
        self._run_worker(
            "build flight package",
            lambda: build_flight_package(Path(path), manifest, report),
        )

    def open_flight_package(self) -> None:
        path = filedialog.askopenfilename(
            title="Inspect flight package",
            filetypes=(("Ogma flight package", "*.zip"), ("All files", "*.*")),
        )
        if not path:
            return
        self._run_worker(
            "inspect flight package",
            lambda: inspect_flight_package(Path(path)),
        )

    def flash_croi_mission(self) -> None:
        try:
            manifest = self._manifest_from_ui()
            config = manifest.mission
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Mission", str(exc))
            return
        if self.selected_board.get() != "croi":
            messagebox.showerror("Mission", "Select Croí before flashing a mission")
            return
        safety_error = self._mission_airbrake_safety_error(config)
        if safety_error:
            messagebox.showerror(
                "Airbrake Failsafe Mismatch",
                f"{safety_error}. Flash matching Lamh failsafe angles before locking this mission.",
            )
            return
        pyro_enabled = config.pyro_drogue_channel is not None or config.pyro_main_channel is not None
        if pyro_enabled and not messagebox.askyesno(
            "Rev1 Pyrotechnic Mission",
            "Mission contains live pyro channels. Confirm external RBF procedure, channel wiring, continuity checks, and accepted-risk Pleasc image.",
        ):
            return
        if not messagebox.askyesno(
            "Flash Locked Mission",
            f"Build and flash Croí flight firmware with mission CRC 0x{config.crc32(manifest.recovery, manifest.logging):08x} and manifest {manifest.sha256()[:12]}?",
        ):
            return
        self.flight_manifest = manifest
        manifest_path = save_flight_manifest(RUNS_ROOT / "manifests", manifest)
        self.log(f"pre-flash manifest sealed: {manifest_path}")
        env = self.selected_env.get() or None
        self._run_worker(
            "flash Croí mission",
            lambda: self.controller.flash_croi_mission_config(
                config,
                env,
                manifest.recovery,
                manifest.logging,
            ),
        )

    def import_croi_dump(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Croí flash dump",
            filetypes=(("Binary dumps", "*.bin *.dump *.raw"), ("All files", "*.*")),
        )
        if not path:
            return
        source = Path(path)

        def parse() -> tuple[str, dict[str, Any], Path]:
            parsed = parse_croi_flash_dump(source.read_bytes())
            out = save_croi_flash_bundle(parsed, source, RUNS_ROOT / "croi_flash")
            return "croi", parsed, out

        self._run_worker("import croi dump", parse)

    def import_groundstation(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Groundstation telemetry",
            filetypes=(("Telemetry", "*.txt *.csv *.jsonl *.log"), ("All files", "*.*")),
        )
        if not path:
            return
        source = Path(path)

        def parse() -> tuple[str, dict[str, Any], Path]:
            parsed = parse_groundstation_file(source)
            out = save_groundstation_bundle(parsed, source, RUNS_ROOT / "groundstation")
            return "groundstation", parsed, out

        self._run_worker("import groundstation", parse)

    def import_telemetry(self) -> None:
        path = filedialog.askopenfilename(
            title="Import mixed telemetry",
            filetypes=(("Telemetry", "*.txt *.csv *.jsonl *.log"), ("All files", "*.*")),
        )
        if not path:
            return
        source = Path(path)

        def parse() -> tuple[str, dict[str, Any], Path]:
            parsed = parse_mixed_telemetry_file(source, load_default_can_frames())
            out = save_mixed_telemetry_bundle(parsed, source, RUNS_ROOT / "telemetry")
            return "telemetry", parsed, out

        self._run_worker("import telemetry", parse)

    def open_telemetry_session(self) -> None:
        path = filedialog.askdirectory(title="Open saved telemetry session")
        if not path:
            return
        source = Path(path)

        def load() -> tuple[str, dict[str, Any], Path]:
            return "telemetry", load_mixed_telemetry_session(source), source

        self._run_worker("open telemetry session", load)

    def capture_groundstation_usb(self) -> None:
        device = simpledialog.askstring("Groundstation USB", "Serial device", initialvalue="/dev/cu.usbmodem")
        if not device:
            return
        duration = simpledialog.askfloat("Groundstation USB", "Capture seconds", initialvalue=30.0, minvalue=0.1)
        if duration is None:
            return

        def capture() -> tuple[str, dict[str, Any], Path]:
            result = capture_serial_text(device, DEFAULT_BAUD, duration)
            parsed = parse_groundstation_text(result.text)
            parsed["summary"].update(serial_capture_summary(result))
            source = f"serial:{result.device}@{result.baud}"
            out = save_groundstation_bundle(parsed, source, RUNS_ROOT / "groundstation", raw_text=result.text)
            return "groundstation", parsed, out

        self._run_worker("groundstation usb", capture)

    def capture_telemetry_usb(self) -> None:
        device = simpledialog.askstring("Telemetry USB", "Serial device", initialvalue="/dev/cu.usbmodem")
        if not device:
            return
        duration = simpledialog.askfloat("Telemetry USB", "Capture seconds", initialvalue=30.0, minvalue=0.1)
        if duration is None:
            return

        def capture() -> tuple[str, dict[str, Any], Path]:
            result = capture_serial_text(device, DEFAULT_BAUD, duration)
            parsed = parse_mixed_telemetry_text(result.text, load_default_can_frames())
            parsed["summary"].update(serial_capture_summary(result))
            source = f"serial:{result.device}@{result.baud}"
            out = save_mixed_telemetry_bundle(parsed, source, RUNS_ROOT / "telemetry", raw_text=result.text)
            return "telemetry", parsed, out

        self._run_worker("telemetry usb", capture)

    def start_live_telemetry(self) -> None:
        if self.telemetry_live_active:
            return
        device = simpledialog.askstring(
            "Live Telemetry",
            "Groundstation serial device",
            initialvalue=self._default_telemetry_device(),
        )
        if not device:
            return
        device = device.strip()
        if not Path(device).exists():
            messagebox.showerror("Live Telemetry", f"Serial device not found: {device}")
            return

        self.telemetry_stop_requested.clear()
        frames = load_default_can_frames()
        self.telemetry_accumulator = MixedTelemetryAccumulator(
            frames,
            history_limit=TELEMETRY_DISPLAY_FRAME_LIMIT,
        )
        self.telemetry_archive_accumulator = MixedTelemetryAccumulator(frames)
        self.telemetry_live_session = RUNS_ROOT / "telemetry" / f"live_{time.strftime('%Y%m%d_%H%M%S')}"
        self.telemetry_live_device = device
        self.telemetry_live_dirty = False
        self.telemetry_last_draw = 0.0
        self.telemetry_live_active = True
        self.telemetry_summary.set(f"LIVE | {device} | waiting for packets")
        self.notebook.select(self.telemetry_tab)
        self._update_action_states()
        self.log(f"live telemetry started: {device}")

        def run_stream() -> None:
            try:
                result = stream_serial_text(
                    device,
                    self.telemetry_stop_requested,
                    lambda text: self.events.put(("telemetry_chunk", text)),
                    baud=DEFAULT_BAUD,
                    raw_path=self.telemetry_live_session / "raw.txt",
                )
                self.events.put(("telemetry_live_done", (result, None)))
            except Exception as exc:
                self.events.put(("telemetry_live_done", (None, exc)))

        threading.Thread(target=run_stream, daemon=True).start()

    def stop_live_telemetry(self) -> None:
        if not self.telemetry_live_active:
            return
        self.telemetry_stop_requested.set()
        self.telemetry_summary.set(f"STOPPING | {self.telemetry_live_device}")
        self.log("live telemetry stop requested")

    @staticmethod
    def _default_telemetry_device() -> str:
        devices = sorted(Path("/dev").glob("cu.usbmodem*"))
        return str(devices[0]) if devices else "/dev/cu.usbmodem"

    def _finish_live_telemetry(
        self,
        result: SerialCaptureResult | None,
        exc: Exception | None,
    ) -> None:
        accumulator = self.telemetry_accumulator
        archive_accumulator = self.telemetry_archive_accumulator
        session = self.telemetry_live_session
        self.telemetry_live_active = False
        self.telemetry_stop_requested.clear()
        self._update_action_states()
        if accumulator is None or archive_accumulator is None or session is None:
            self.telemetry_summary.set("Stopped")
            return

        accumulator.finish()
        archive_accumulator.finish()
        parsed = accumulator.snapshot()
        archive_parsed = archive_accumulator.snapshot()
        if result is not None:
            archive_parsed["summary"].update(serial_capture_summary(result))
        out = save_mixed_telemetry_session(
            archive_parsed,
            f"serial:{self.telemetry_live_device}@{DEFAULT_BAUD}",
            session,
        )
        self._show_mixed_telemetry(parsed, select_tab=False)
        if exc is not None:
            self.telemetry_summary.set(f"STOPPED WITH ERROR | {exc} | saved {out}")
            self.log(f"live telemetry failed: {exc}")
        else:
            self.log(f"live telemetry bundle: {out}")
        self.telemetry_accumulator = None
        self.telemetry_archive_accumulator = None
        self.telemetry_live_session = None

    def import_can_log(self) -> None:
        path = filedialog.askopenfilename(
            title="Import CAN log",
            filetypes=(("CAN logs", "*.txt *.log *.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        source = Path(path)

        def decode() -> tuple[str, dict[str, Any], Path]:
            decoded = decode_can_log_file(source, load_default_can_frames())
            out = save_can_decode_bundle(decoded, source, RUNS_ROOT / "can" / f"{source.stem}_decoded.json")
            return "can_log", decoded, out

        self._run_worker("import can log", decode)

    def read_croi_flash(self) -> None:
        env = self.selected_env.get() or None
        if not self._confirm_bench_action("Read Croí flash", "Grant a short Croí bench lease and read external flash over SWD?"):
            return
        max_bytes = simpledialog.askinteger(
            "Read Croí flash",
            "Max bytes (0 = all firmware-reported used bytes)",
            initialvalue=1048576,
            minvalue=0,
        )
        if max_bytes is None:
            return
        byte_limit = None if max_bytes == 0 else max_bytes

        def read() -> tuple[str, dict[str, Any], Path]:
            dump = self.controller.read_croi_flash_dump(env, max_bytes=byte_limit)
            source = RUNS_ROOT / "croi_flash" / "latest_swd_dump.bin"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(dump)
            parsed = parse_croi_flash_dump(dump)
            out = save_croi_flash_bundle(parsed, source, RUNS_ROOT / "croi_flash")
            return "croi", parsed, out

        self._run_worker("read croi flash", read)

    def wipe_croi_flash(self) -> None:
        if self.selected_board.get() != "croi":
            self.log("wipe clean is only available for Croí")
            return
        ok = messagebox.askyesno(
            "Wipe Croí flash",
            "Flash the Croí erase image, wipe external flash, then restore the flight firmware?\n\nBench/debug use only. Continue?",
        )
        if not ok:
            return
        self._run_worker("wipe croi flash", self.controller.wipe_croi_flash_restore)

    def save_status(self) -> None:
        if self.latest_status is None:
            self.log("save status failed: no status read yet")
            return
        board_id, env, status = self.latest_status
        profile = profile_for(board_id)
        default = f"{board_id}_status.json"
        path = filedialog.asksaveasfilename(
            title="Save board status",
            initialfile=default,
            defaultextension=".json",
            filetypes=(("JSON", "*.json"), ("CSV", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        out = save_status_snapshot(make_status_snapshot(profile, env, status), Path(path))
        self.log(f"status snapshot: {out}")

    def stop_link(self) -> None:
        self.controller.close()
        self.log("OpenOCD stopped")

    def stop_polling(self) -> None:
        if self.worker_active and self.worker_label == "poll status":
            self.stop_requested.set()
            self.log("poll stop requested")

    def clear_plot(self) -> None:
        self.plot_generation += 1
        if self.worker_active and self.worker_label == "poll status":
            self.clear_poll_data_requested.set()
        self.status_history.clear()
        self.can_history.clear()
        self.plot.set_points("", "", [])
        self.can_traffic_plot.set_series("Croí CAN Traffic", "count", {})
        self.can_data_plot.set_series("Croí CAN State", "state", {})
        self.telemetry_altitude_plot.set_series("Altitude", "m", {})
        self.telemetry_velocity_plot.set_series("Vertical Velocity", "m/s", {})
        self.telemetry_accel_plot.set_series("Acceleration", "g", {})
        self.telemetry_gyro_plot.set_series("Angular Velocity", "deg/s", {})
        self.telemetry_current_plot.set_series("Current", "mA", {})
        self.telemetry_rate_plot.set_series("Rolling Frame Rate", "Hz", {})
        self.telemetry_gap_plot.set_series("Inter-arrival Gap", "ms", {})
        self.recovery_state_plot.set_series("Flight State", "state", {})
        self.recovery_airbrake_plot.set_series("Airbrake Command", "deg", {})
        self.latest_telemetry = None
        self.latest_telemetry_updated_at = 0.0
        if self.telemetry_live_active and self.telemetry_archive_accumulator is not None:
            self.telemetry_accumulator = MixedTelemetryAccumulator(
                self.telemetry_archive_accumulator.frames,
                history_limit=TELEMETRY_DISPLAY_FRAME_LIMIT,
            )
            self.telemetry_live_dirty = False
            self.telemetry_last_draw = time.monotonic()
            for item in self.telemetry_tree.get_children():
                self.telemetry_tree.delete(item)
            self.telemetry_summary.set(
                f"LIVE | {self.telemetry_live_device} | display history cleared"
            )
        self.refresh_preflight()
        self.log("plots cleared")

    def _selected_board_env(self) -> tuple[str, str]:
        board_id = self.selected_board.get()
        env = self.selected_env.get()
        if not env:
            raise RuntimeError(f"{board_id} has no selectable firmware env")
        return board_id, env

    def thread_log(self, text: str) -> None:
        self.events.put(("log", text))

    def log(self, text: str) -> None:
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def _pump_events(self) -> None:
        deadline = time.monotonic() + 0.01
        for _ in range(200):
            if time.monotonic() >= deadline:
                break
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(str(payload))
            elif kind == "status":
                if len(payload) == 4:
                    board_id, env, status, generation = payload
                    if generation != self.plot_generation:
                        continue
                else:
                    board_id, env, status = payload
                self._show_status(board_id, status, env, select_tab=False)
            elif kind == "telemetry_chunk":
                if self.telemetry_accumulator is not None:
                    self.telemetry_accumulator.feed(str(payload))
                    if self.telemetry_archive_accumulator is not None:
                        self.telemetry_archive_accumulator.feed(str(payload))
                    self.telemetry_live_dirty = True
            elif kind == "telemetry_live_done":
                self._finish_live_telemetry(*payload)
            elif kind == "worker_done":
                try:
                    self._worker_done(*payload)
                except Exception as exc:
                    self.worker_active = False
                    self.worker_label = ""
                    self.header_activity.set("Idle")
                    self.stop_requested.clear()
                    self._update_action_states()
                    self.log(f"ui handler failed: {type(exc).__name__}: {exc}")
                    self.notebook.select(self.log_tab)
        telemetry_draw_age = time.monotonic() - self.telemetry_last_draw
        if (
            self.telemetry_accumulator is not None
            and (
                (self.telemetry_live_dirty and telemetry_draw_age >= 0.5)
                or (self.telemetry_live_active and telemetry_draw_age >= 1.0)
            )
        ):
            self._show_mixed_telemetry(self.telemetry_accumulator.snapshot(), select_tab=False)
            self.telemetry_last_draw = time.monotonic()
            self.telemetry_live_dirty = False
        self.after(10 if not self.events.empty() else 100, self._pump_events)

    def _worker_done(self, label: str, result: Any, exc: Exception | None) -> None:
        self.worker_active = False
        self.worker_label = ""
        self.header_activity.set("Idle")
        self.stop_requested.clear()
        self._update_action_states()
        if exc is not None:
            self.log(f"{label} failed: {exc}")
            self.notebook.select(self.log_tab)
            return
        self.log(f"{label} done")
        if isinstance(result, DetectionResult):
            self._handle_detection(result)
        elif isinstance(result, FlashDetectedResult):
            self._handle_detection(result.detection)
            profile = result.detection.profile
            if profile is not None:
                self.log(f"flashed detected board: {profile.board_id} env={result.env}")
        elif isinstance(result, ProbeResult):
            self._show_probe(result)
        elif isinstance(result, DiagnosticReport):
            self._show_diagnostics(result)
        elif isinstance(result, ValidationRunResult):
            self._show_validation(result)
            self.log(f"validation report: {result.out}")
        elif isinstance(result, tuple) and len(result) == 4 and isinstance(result[3], HealthReport):
            board_id, env, status, report = result
            self.latest_status = (board_id, env, status)
            self._record_status_evidence(board_id, status)
            self._show_health(report)
        elif isinstance(result, tuple) and len(result) == 4 and isinstance(result[3], Path):
            board_id, env, status, out = result
            if status:
                self._show_status(board_id, status, env, select_tab=(label != "poll status"))
            self.log(f"status series: {out}")
        elif isinstance(result, TeachtaireTestResult):
            self._show_teachtaire_test(result)
            self.log(f"teachtaire test bundle: {result.out}")
        elif isinstance(result, LamhServoTestResult):
            self._show_lamh_servo_test(result)
            self.log(f"lamh servo test bundle: {result.out}")
        elif isinstance(result, LamhSafetyFlashResult):
            self.lamh_safety_config = result.config
            self.flight_manifest = replace(self.flight_manifest, lamh_safety=result.config)
            for variable, angle in zip(self.lamh_safe_angles, result.config.angles_deg):
                variable.set(angle)
            self._show_status("lamh", result.status, result.env)
            self._log_verification(result.verification)
            self.log(f"lamh safety config audit: {result.record_path}")
        elif isinstance(result, CroiMissionFlashResult):
            self.flight_manifest = replace(
                self.flight_manifest,
                mission=result.config,
                recovery=result.recovery,
                logging=result.logging,
            )
            self._apply_mission_config(result.config)
            self._show_status("croi", result.status, result.env)
            self._log_verification(result.verification)
            self.log(f"croi mission config audit: {result.record_path}")
        elif isinstance(result, TeachtaireRadioFlashResult):
            self.flight_manifest = replace(self.flight_manifest, radio=result.config)
            self._apply_radio_config(result.config)
            self._show_status("teachtaire", result.status, result.env)
            self._log_verification(result.verification)
            self.log(f"Teachtaire radio config audit: {result.record_path}")
        elif isinstance(result, FlightPackageResult):
            release_ready = result.preflight_go and not result.missing_firmware and not result.dirty_repositories
            self.log(f"flight package: {result.path}")
            self.log(f"flight package SHA-256: {result.sha256}")
            self.log(f"release ready: {'yes' if release_ready else 'no'}")
            if result.missing_firmware:
                self.log(f"missing firmware: {', '.join(result.missing_firmware)}")
            if result.dirty_repositories:
                self.log(f"dirty repositories: {', '.join(result.dirty_repositories)}")
            self.notebook.select(self.log_tab)
        elif isinstance(result, FlightPackageInspection):
            self.log(f"flight package integrity: {'VALID' if result.valid else 'INVALID'}")
            self.log(f"manifest SHA-256: {result.manifest_sha256}")
            self.log(f"package SHA-256: {result.package_sha256}")
            for error in result.errors:
                self.log(f"package error: {error}")
            self.notebook.select(self.log_tab)
        elif isinstance(result, ReplaySession):
            self._show_mission_replay(result)
        elif isinstance(result, FoinseMonitorResult):
            self._show_foinse_monitor(result)
            self.log(f"foinse monitor bundle: {result.out}")
        elif isinstance(result, CroiWipeRestoreResult):
            self.log(f"croi flash wiped with {result.wipe_env}; restored {result.flight_env}")
            self._show_status("croi", result.status, result.wipe_env)
        elif isinstance(result, tuple) and len(result) == 3 and result[0] == "groundstation":
            _board_id, parsed, out = result
            self._show_groundstation(parsed)
            self.log(f"groundstation bundle: {out}")
        elif isinstance(result, tuple) and len(result) == 3 and result[0] == "telemetry":
            _kind, parsed, out = result
            self._show_mixed_telemetry(parsed)
            self.log(f"telemetry bundle: {out}")
        elif is_croi_flash_result(result):
            _board_id, parsed, out = result
            self._show_croi_flash(parsed)
            self.log(f"croi flash bundle: {out}")
        elif isinstance(result, tuple) and len(result) == 3 and result[0] == "can_log":
            _kind, decoded, out = result
            self._show_can_log(decoded)
            self.log(f"can decode: {out}")
        elif isinstance(result, tuple) and len(result) == 3 and isinstance(result[2], dict):
            board_id, env, status = result
            self._show_status(board_id, status, env)
        elif isinstance(result, Path):
            self.log(str(result))

    def _handle_detection(self, result: DetectionResult) -> None:
        if result.profile is None:
            self.log("detect: no board matched")
            self.log(result.reason)
            return
        profile = result.profile
        self.log(f"detect: {profile.display_name} ({result.reason})")
        if result.identity is not None:
            caps = ", ".join(result.identity.capability_names()) or "none"
            self.log(
                f"identity: board_id={result.identity.board_id} "
                f"fw={result.identity.firmware_version} caps={caps}"
            )
        index = [item.board_id for item in PROFILES].index(profile.board_id)
        self.board_list.selection_clear(0, "end")
        self.board_list.selection_set(index)
        self._select_board(profile.board_id)
        if result.status:
            self._show_status(profile.board_id, result.status, profile.default_env)

    def _show_diagnostics(self, report: DiagnosticReport) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="State")
        self.status_tree.heading("unit", text="Detail")
        self.status_tree.column("value", width=80)
        self.status_tree.column("unit", width=520)
        for row in report.rows:
            self.status_tree.insert(
                "",
                "end",
                text=f"{row.subject}: {row.check}",
                values=(row.state, row.detail),
            )
        self.plot.set_points("", "", [])
        self.notebook.select(0)

    def _show_probe(self, result: ProbeResult) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="")
        self.status_tree.column("value", width=420)
        self.status_tree.column("unit", width=80)
        self.status_tree.insert("", "end", text="state", values=("connected" if result.connected else "not connected", ""))
        self.status_tree.insert("", "end", text="stlink programmers", values=(result.programmers, ""))
        self.status_tree.insert("", "end", text="return code", values=(result.returncode, ""))
        for key in sorted(result.fields):
            self.status_tree.insert("", "end", text=key, values=(result.fields[key], ""))
        self.plot.set_points("", "", [])
        self.notebook.select(0)

    def _show_validation(self, result: ValidationRunResult) -> None:
        report = result.report
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="")
        self.status_tree.column("value", width=420)
        self.status_tree.column("unit", width=80)
        detection = report.get("detection") if isinstance(report.get("detection"), dict) else {}
        self.status_tree.insert("", "end", text="ok", values=(str(report.get("ok")), ""))
        self.status_tree.insert("", "end", text="expected", values=(str(report.get("expected_board_id")), ""))
        self.status_tree.insert("", "end", text="detected", values=(str(detection.get("board_id")), ""))
        for error in report.get("errors", []):
            self.status_tree.insert("", "end", text="error", values=(str(error), ""))
        for warning in report.get("warnings", []):
            self.status_tree.insert("", "end", text="warning", values=(str(warning), ""))
        health = report.get("health")
        if isinstance(health, dict):
            parent = self.status_tree.insert("", "end", text="health", values=(str(health.get("ok")), ""))
            for check in health.get("checks", []):
                self.status_tree.insert(
                    parent,
                    "end",
                    text=str(check.get("name")),
                    values=(str(check.get("state")), str(check.get("detail"))),
                )
        self.status_tree.insert("", "end", text="report", values=(str(result.out), ""))
        self.plot.set_points("", "", [])
        self.notebook.select(0)

    def _show_health(self, report: HealthReport) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="State")
        self.status_tree.heading("unit", text="Detail")
        self.status_tree.column("value", width=80)
        self.status_tree.column("unit", width=520)
        for check in report.checks:
            self.status_tree.insert("", "end", text=check.name, values=(check.state, check.detail))
        self.plot.set_points("", "", [])
        self.notebook.select(0)

    def _show_status(
        self,
        board_id: str,
        status: dict[str, Any],
        env: str | None = None,
        *,
        select_tab: bool = True,
    ) -> None:
        profile = profile_for(board_id)
        block = profile.status_block
        if block is None:
            return
        self.latest_status = (board_id, env, status)
        self._record_status_evidence(board_id, status)
        self._update_action_states()
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="Unit")
        self.status_tree.column("value", width=180)
        self.status_tree.column("unit", width=80)
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        for field in block.fields:
            if field.name not in status:
                continue
            value = status.get(field.name, "")
            self.status_tree.insert(
                "",
                "end",
                text=field.display_name(),
                values=(field.display_value(value), field.unit),
            )
        self._update_plot(board_id, status)
        if board_id == "croi":
            self._update_croi_can_panel(status)
            self.recovery_summary.set(
                "LIVE Croí | "
                f"state {FLIGHT_STATE_NAMES.get(self._as_int(status.get('flight_state')), 'unknown')} | "
                f"continuity 0x{self._as_int(status.get('pyro_continuity_mask')):02X} | "
                f"armed 0x{self._as_int(status.get('pyro_armed_mask')):02X} | "
                f"fired 0x{self._as_int(status.get('pyro_fired_mask')):02X} | "
                f"main backup {self._yes_no(status.get('main_fallback_triggered'))}"
            )
        if select_tab and not (self.worker_active and self.worker_label == "poll status"):
            self.notebook.select(0)

    def _record_status_evidence(self, board_id: str, status: dict[str, Any]) -> None:
        self.status_evidence_cache[board_id] = (dict(status), time.monotonic())
        self.fault_ledger.observe(board_fault_observations(board_id, status), time.time())
        self._refresh_fault_view()
        self.refresh_preflight()

    def _refresh_fault_view(self) -> None:
        tree = getattr(self, "fault_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        entries = self.fault_ledger.sorted_entries()
        active = sum(entry.active for entry in entries)
        critical = sum(entry.active and entry.severity == "critical" for entry in entries)
        self.fault_summary.set(
            f"active {active} | critical {critical} | historical {len(entries)}"
            if entries
            else "No faults observed"
        )
        for entry in entries:
            tree.insert(
                "",
                "end",
                text=entry.key,
                values=(
                    "ACTIVE" if entry.active else "resolved",
                    entry.severity,
                    entry.source,
                    entry.occurrences,
                    entry.last_seen_utc,
                    entry.detail,
                ),
                tags=("active" if entry.active else "resolved",),
            )

    def export_fault_ledger(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export fault ledger",
            initialfile="ogma_fault_ledger.json",
            defaultextension=".json",
            filetypes=(("JSON", "*.json"),),
        )
        if not path:
            return
        out = self.fault_ledger.save(Path(path))
        self.log(f"fault ledger: {out}")

    def _log_verification(self, verification: Any) -> None:
        for item in verification.items:
            state = "PASS" if item.ok else "FAIL"
            self.log(f"readback {state}: {verification.target} {item.field}={item.actual!r}")

    def _show_croi_flash(self, parsed: dict[str, Any]) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        summary = parsed["summary"]
        for key, value in summary.items():
            self.status_tree.insert("", "end", text=str(key), values=(str(value), ""))
        points = [
            (float(row["timestamp_ms"]) / 1000.0, float(row["prediction_altitude_m"]))
            for row in parsed["flight"]
            if "timestamp_ms" in row and "prediction_altitude_m" in row
        ]
        if points:
            start = points[0][0]
            points = [(x - start, y) for x, y in points]
        self.plot.set_points("Croí Predicted Altitude", "m", points)
        self.notebook.select(0)

    def _show_mission_replay(self, session: ReplaySession) -> None:
        for item in self.recovery_tree.get_children():
            self.recovery_tree.delete(item)
        for point in session.result.transitions:
            event = "main backup" if point.main_backup else "state transition"
            self.recovery_tree.insert(
                "",
                "end",
                text="firmware replay",
                values=(f"{point.time_ms / 1000.0:.2f} s", FLIGHT_STATE_NAMES.get(point.state, point.state), event),
            )
        final_state = session.result.points[-1].state if session.result.points else 1
        backup = "TRIGGERED" if session.result.main_backup_triggered else "not triggered"
        self.recovery_summary.set(
            f"{session.source} | samples {len(session.samples)} | transitions {len(session.result.transitions)} | "
            f"final {FLIGHT_STATE_NAMES.get(final_state, final_state)} | main backup {backup}"
        )
        self.recovery_state_plot.set_series(
            "Flight State",
            "state",
            {
                "state": [
                    (point.time_ms / 1000.0, float(point.state))
                    for point in session.result.points
                ]
            },
        )
        self.recovery_airbrake_plot.set_series(
            "Airbrake Command",
            "deg",
            {
                "angle": [
                    (point.time_ms / 1000.0, float(point.airbrake_angle_deg))
                    for point in session.result.points
                ]
            },
        )
        self.notebook.select(self.recovery_tab)

    def _show_groundstation(self, parsed: dict[str, Any]) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="")
        self.status_tree.column("value", width=220)
        self.status_tree.column("unit", width=80)
        for key, value in parsed["summary"].items():
            self.status_tree.insert("", "end", text=str(key), values=(str(value), ""))
        points = [
            (float(row["sample_index"]), float(row["altitude_m"]))
            for row in parsed["records"]
            if row.get("fix")
        ]
        self.plot.set_points("Groundstation Altitude", "m", points)
        self.notebook.select(0)

    def _show_teachtaire_test(self, result: TeachtaireTestResult) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="")
        self.status_tree.column("value", width=220)
        self.status_tree.column("unit", width=80)
        for key, value in result.summary.items():
            self.status_tree.insert("", "end", text=str(key), values=(str(value), ""))
        tx_points: list[tuple[float, float]] = []
        rx_points: list[tuple[float, float]] = []
        sats_points: list[tuple[float, float]] = []
        for sample in result.samples:
            elapsed = float(sample["elapsed_s"])
            status = sample["status"]
            tx_points.append((elapsed, float(status.get("lora_tx_count", 0))))
            rx_points.append((elapsed, float(status.get("lora_rx_count", 0))))
            sats_points.append((elapsed, float(status.get("gnss_sats", 0))))
        self.plot.set_series(
            "Teachtaire Test",
            "count",
            {"tx": tx_points, "rx": rx_points, "sats": sats_points},
        )
        if result.samples:
            status = result.samples[-1]["status"]
            self.latest_status = ("teachtaire", result.env, status)
            self._record_status_evidence("teachtaire", status)
            self._update_action_states()
        self.notebook.select(0)

    def _show_lamh_servo_test(self, result: LamhServoTestResult) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="")
        self.status_tree.column("value", width=220)
        self.status_tree.column("unit", width=80)
        for key, value in result.summary.items():
            self.status_tree.insert("", "end", text=str(key), values=(str(value), ""))
        commanded: list[tuple[float, float]] = []
        measured: list[tuple[float, float]] = []
        pwm: list[tuple[float, float]] = []
        for sample in result.samples:
            elapsed = float(sample["elapsed_s"])
            status = sample["status"]
            commanded.append((elapsed, float(sample.get("command_angle", 0))))
            measured.append((elapsed, float(status.get("servo_angle", 0))))
            pwm.append((elapsed, float(status.get("servo_pwm", 0))))
        self.plot.set_series(
            "Lámh Servo Test",
            "deg/pwm",
            {"cmd": commanded, "angle": measured, "pwm": pwm},
        )
        if result.samples:
            status = result.samples[-1]["status"]
            self.latest_status = ("lamh", result.env, status)
            self._record_status_evidence("lamh", status)
            self._update_action_states()
        self.notebook.select(0)

    def _show_foinse_monitor(self, result: FoinseMonitorResult) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Value")
        self.status_tree.heading("unit", text="")
        self.status_tree.column("value", width=220)
        self.status_tree.column("unit", width=80)
        for key, value in result.summary.items():
            self.status_tree.insert("", "end", text=str(key), values=(str(value), ""))
        series: dict[str, list[tuple[float, float]]] = {"bat": [], "servo": []}
        for sample in result.samples:
            elapsed = float(sample["elapsed_s"])
            status = sample["status"]
            series["bat"].append((elapsed, float(status.get("sense1_current_ma", 0))))
            series["servo"].append((elapsed, float(status.get("sense2_current_ma", 0))))
        self.plot.set_series("Foinse Current", "mA", series)
        if result.samples:
            status = result.samples[-1]["status"]
            self.latest_status = ("foinse", result.env, status)
            self._record_status_evidence("foinse", status)
            self._update_action_states()
        self.notebook.select(0)

    def _show_mixed_telemetry(self, parsed: dict[str, Any], *, select_tab: bool = True) -> None:
        self.latest_telemetry = parsed
        self.latest_telemetry_updated_at = time.monotonic()
        self.fault_ledger.observe(telemetry_fault_observations(parsed), time.time())
        self._refresh_fault_view()
        summary = parsed["summary"]
        stream_state = "LIVE" if self.telemetry_live_active else "STOPPED"
        device = f" | {self.telemetry_live_device}" if self.telemetry_live_device else ""
        packets = parsed["can"].get("frames", [])[-TELEMETRY_DISPLAY_FRAME_LIMIT:]
        received_times = [
            float(packet["received_s"])
            for packet in packets
            if isinstance(packet.get("received_s"), (int, float))
        ]
        elapsed_s = float(summary.get("elapsed_s", max(received_times, default=0.0)))
        recent_window_s = min(2.0, max(0.25, elapsed_s))
        recent_frames = sum(1 for received_s in received_times if received_s >= elapsed_s - recent_window_s)
        rx_rate_hz = recent_frames / recent_window_s if received_times else 0.0
        newest_age_s = max(0.0, elapsed_s - max(received_times)) if received_times else 0.0
        self.telemetry_summary.set(
            f"{stream_state}{device} | lines {summary.get('lines', 0)} | "
            f"CAN {summary.get('can_frames', 0)} | GPS {summary.get('gps_records', 0)} | "
            f"RX {rx_rate_hz:.1f} Hz | age {newest_age_s:.1f} s | "
            f"nodes {summary.get('heartbeat_nodes', 0)} | "
            f"node errors {summary.get('heartbeat_nodes_with_errors', 0)} | "
            f"unknown {summary.get('unknown_can_frames', 0)}"
        )

        for item in self.telemetry_tree.get_children():
            self.telemetry_tree.delete(item)
        summary_parent = self.telemetry_tree.insert("", "end", text="Session", values=(stream_state, device.strip(" |")))
        for key, value in summary.items():
            display = len(value) if key == "warnings" and isinstance(value, list) else value
            self.telemetry_tree.insert(summary_parent, "end", text=str(key), values=(str(display), ""))

        latest: dict[str, dict[str, Any]] = {}
        frame_counts: dict[str, int] = {}
        for packet in packets:
            name = str(packet.get("frame", "UNKNOWN"))
            latest[name] = packet
            frame_counts[name] = frame_counts.get(name, 0) + 1

        snapshot_parent = self.telemetry_tree.insert("", "end", text="Flight Snapshot", values=("decoded", "latest values"))
        snapshot_rows: list[tuple[str, Any, str]] = []
        state_value = self._telemetry_value(latest.get("FLIGHT_STATE"), "state")
        if state_value is not None:
            state_number = int(state_value)
            snapshot_rows.append(("flight state", FLIGHT_STATE_NAMES.get(state_number, f"unknown ({state_number})"), ""))
        snapshot_fields = (
            ("KALMANN", "altitude_m", "filtered altitude", "m", None),
            ("KALMANN", "vspeed_m_s", "vertical velocity", "m/s", None),
            ("KALMANN", "acceleration_m_s2", "vertical acceleration", "m/s^2", None),
            ("BARO", "pressure", "barometric pressure", "Pa", None),
            ("BARO", "temp", "barometer temperature", "C", None),
            ("BARO", "altitude_m", "barometric altitude", "m", None),
            ("POWER_MAIN", "ibat_ma", "battery current", "mA", 0x01),
            ("POWER_SERVO", "iservo_ma", "servo current", "mA", 0x02),
            ("TX_STATUS", "rssi_dbm", "radio RSSI", "dBm", None),
            ("TX_STATUS", "snr_db", "radio SNR", "dB", None),
        )
        for frame_name, field_name, label, unit, valid_flag in snapshot_fields:
            packet = latest.get(frame_name)
            if valid_flag is not None:
                flags = self._telemetry_value(packet, "flags")
                if flags is None or int(flags) & valid_flag == 0:
                    continue
            value = self._telemetry_value(packet, field_name)
            if value is not None:
                snapshot_rows.append((label, value, unit))
        for label, value, unit in snapshot_rows:
            display = f"{value:.2f}" if isinstance(value, float) else str(value)
            self.telemetry_tree.insert(snapshot_parent, "end", text=label, values=(display, unit))

        nodes = parsed["can"].get("stack", {}).get("nodes", {})
        stack_parent = self.telemetry_tree.insert("", "end", text="Stack", values=(len(nodes), "heartbeat nodes"))
        for name, node in nodes.items():
            err_flags = ",".join(node.get("err_flags", [])) or "ok"
            state = node.get("state")
            state_text = FLIGHT_STATE_NAMES.get(int(state), state) if name == "croi" and state is not None else state
            detail = f"state={state_text} uptime={node.get('uptime_s')}s"
            self.telemetry_tree.insert(stack_parent, "end", text=str(name), values=(err_flags, detail))

        frames_parent = self.telemetry_tree.insert("", "end", text="Latest Frames", values=(len(latest), "types"))
        for name, packet in sorted(latest.items()):
            received_s = packet.get("received_s")
            age_text = ""
            if isinstance(received_s, (int, float)):
                age_text = f" age={max(0.0, elapsed_s - float(received_s)):.1f}s"
            parent = self.telemetry_tree.insert(
                frames_parent,
                "end",
                text=name,
                values=(frame_counts[name], f"0x{int(packet.get('can_id', 0)):03X}{age_text} {packet.get('data_hex', '')}"),
            )
            for field_name, field in packet.get("fields", {}).items():
                unit = field.get("unit", "") if isinstance(field, dict) else ""
                value = field.get("value", "") if isinstance(field, dict) else field
                if name == "FLIGHT_STATE" and field_name == "state":
                    value = FLIGHT_STATE_NAMES.get(int(value), f"unknown ({value})")
                self.telemetry_tree.insert(parent, "end", text=field_name, values=(str(value), str(unit)))

        altitude_series = {
            "baro": self._telemetry_series(packets, "BARO", "altitude_m"),
            "kalman": self._telemetry_series(packets, "KALMANN", "altitude_m"),
            "gps": [
                (float(row.get("received_s", row["sample_index"])), float(row["altitude_m"]))
                for row in parsed["groundstation"]["records"]
                if row.get("fix")
            ],
        }
        self.telemetry_altitude_plot.set_series("Altitude", "m", altitude_series)
        self.telemetry_velocity_plot.set_series(
            "Vertical Velocity", "m/s", {"kalman": self._telemetry_series(packets, "KALMANN", "vspeed_m_s")}
        )
        self.telemetry_accel_plot.set_series(
            "Acceleration",
            "g",
            {
                "x": self._telemetry_series(packets, "IMU_ACCEL", "ax"),
                "y": self._telemetry_series(packets, "IMU_ACCEL", "ay"),
                "z": self._telemetry_series(packets, "IMU_ACCEL", "az"),
            },
        )
        self.telemetry_gyro_plot.set_series(
            "Angular Velocity",
            "deg/s",
            {
                "x": self._telemetry_series(packets, "IMU_GYRO", "gx"),
                "y": self._telemetry_series(packets, "IMU_GYRO", "gy"),
                "z": self._telemetry_series(packets, "IMU_GYRO", "gz"),
            },
        )
        self.telemetry_current_plot.set_series(
            "Current",
            "mA",
            {
                "battery": self._telemetry_series(packets, "POWER_MAIN", "ibat_ma", valid_flag=0x01),
                "servo": self._telemetry_series(packets, "POWER_SERVO", "iservo_ma", valid_flag=0x02),
            },
        )
        top_frames = [
            name for name, _count in sorted(frame_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        ]
        rate_series, gap_series = self._telemetry_link_series(packets, top_frames)
        self.telemetry_rate_plot.set_series("Rolling Frame Rate", "Hz", rate_series)
        self.telemetry_gap_plot.set_series("Inter-arrival Gap", "ms", gap_series)
        if select_tab:
            self.notebook.select(self.telemetry_tab)
        self.refresh_preflight()

    @staticmethod
    def _telemetry_value(packet: dict[str, Any] | None, field_name: str) -> Any:
        if packet is None:
            return None
        field = packet.get("fields", {}).get(field_name)
        return field.get("value") if isinstance(field, dict) else None

    @classmethod
    def _telemetry_series(
        cls,
        packets: list[dict[str, Any]],
        frame_name: str,
        field_name: str,
        *,
        valid_flag: int | None = None,
    ) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for index, packet in enumerate(packets):
            if packet.get("frame") != frame_name:
                continue
            if valid_flag is not None:
                flags = cls._telemetry_value(packet, "flags")
                if flags is None or int(flags) & valid_flag == 0:
                    continue
            value = cls._telemetry_value(packet, field_name)
            if not isinstance(value, (int, float)):
                continue
            x = packet.get("received_s", index)
            points.append((float(x), float(value)))
        return points

    @staticmethod
    def _telemetry_link_series(
        packets: list[dict[str, Any]],
        frame_names: list[str],
    ) -> tuple[dict[str, list[tuple[float, float]]], dict[str, list[tuple[float, float]]]]:
        rate_series = {name: [] for name in frame_names}
        gap_series = {name: [] for name in frame_names}
        windows = {name: deque() for name in frame_names}
        previous: dict[str, float] = {}
        for packet in packets:
            name = str(packet.get("frame", "UNKNOWN"))
            received_s = packet.get("received_s")
            if name not in windows or not isinstance(received_s, (int, float)):
                continue
            received_s = float(received_s)
            window = windows[name]
            window.append(received_s)
            while window and window[0] < received_s - 2.0:
                window.popleft()
            width_s = min(2.0, max(0.25, received_s))
            rate_series[name].append((received_s, len(window) / width_s))
            if name in previous:
                gap_series[name].append((received_s, (received_s - previous[name]) * 1000.0))
            previous[name] = received_s
        return rate_series, gap_series

    def _show_can_log(self, decoded: dict[str, Any]) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        self.status_tree.heading("value", text="Frame")
        self.status_tree.heading("unit", text="Value")
        self.status_tree.column("value", width=160)
        self.status_tree.column("unit", width=280)
        for key, value in decoded["summary"].items():
            self.status_tree.insert("", "end", text=str(key), values=("", str(value)))
        stack = decoded.get("stack", {})
        nodes = stack.get("nodes", {}) if isinstance(stack, dict) else {}
        if isinstance(nodes, dict) and nodes:
            stack_parent = self.status_tree.insert("", "end", text="stack nodes", values=("", f"{len(nodes)} seen"))
            for name, node in nodes.items():
                err_flags = ",".join(node.get("err_flags", [])) or "ok"
                detail = f"state={node.get('state')} err={node.get('err')} uptime={node.get('uptime_s')}s {err_flags}"
                self.status_tree.insert(stack_parent, "end", text=str(name), values=("HEARTBEAT", detail))
            self.can_summary.configure(text=self._format_stack_summary(nodes))
        for packet in decoded["frames"][:100]:
            parent = self.status_tree.insert(
                "",
                "end",
                text=f"line {packet['line']} 0x{packet['can_id']:03x}",
                values=(packet["frame"], packet["data_hex"]),
            )
            for name, field in packet["fields"].items():
                value = field["value"]
                unit = field.get("unit", "")
                self.status_tree.insert(parent, "end", text=name, values=("", f"{value} {unit}".strip()))
        self.plot.set_points("", "", [])
        self.notebook.select(0)

    def _update_plot(self, board_id: str, status: dict[str, Any]) -> None:
        now = time.time()
        if board_id == "croi":
            series: dict[str, list[tuple[float, float]]] = {}
            sensor_keys = (
                ("baro_altitude_m", "baro"),
                ("prediction_altitude_m", "pred"),
                ("prediction_velocity_m_s", "vel"),
                ("prediction_accel_m_s2", "accel"),
                ("imu_accel_z_g", "z-g"),
            )
            status_version = int(status.get("version", 0))
            active_sensor_keys = [item for item in sensor_keys if status_version >= 2 and item[0] in status]
            if active_sensor_keys:
                for key, label in active_sensor_keys:
                    points = self.status_history.setdefault(key, [])
                    points.append((now, float(status.get(key, 0))))
                    start = points[0][0] if points else now
                    series[label] = [(x - start, y) for x, y in points]
                self.plot.set_series("Croí Sensors", "m / m/s / g", series)
                return
            fallback_keys = (
                ("can_active_nodes", "nodes"),
                ("logger_records_written", "logs"),
            )
            for key, label in fallback_keys:
                points = self.status_history.setdefault(key, [])
                points.append((now, float(status.get(key, 0))))
                start = points[0][0] if points else now
                series[label] = [(x - start, y) for x, y in points]
            self.plot.set_series("Croí Health", "count", series)
            return
        if board_id == "teachtaire":
            key = "gnss_sats"
            value = float(status.get(key, 0))
            title = "GNSS Satellites"
            ylabel = "sat"
        elif board_id == "lamh":
            key = "servo_angle"
            value = float(status.get(key, 0))
            title = "Servo Angle"
            ylabel = "deg"
        elif board_id == "foinse":
            series: dict[str, list[tuple[float, float]]] = {}
            current_keys = (
                ("sense1_current_ma", "bat"),
                ("sense2_current_ma", "servo"),
            )
            for key, label in current_keys:
                points = self.status_history.setdefault(key, [])
                points.append((now, float(status.get(key, 0))))
                start = points[0][0] if points else now
                series[label] = [(x - start, y) for x, y in points]
            self.plot.set_series("Foinse Current", "mA", series)
            return
        else:
            return
        points = self.status_history.setdefault(key, [])
        points.append((now, value))
        start = points[0][0] if points else now
        self.plot.set_points(title, ylabel, [(x - start, y) for x, y in points])

    def _update_action_states(self) -> None:
        profile = profile_for(self.selected_board.get())
        busy = self.worker_active
        polling = busy and self.worker_label == "poll status"
        states = action_states(profile, self.latest_status is not None, busy, polling=polling)
        for key, button in self.action_buttons.items():
            button.configure(state=states.get(key, "normal"))
        self.action_buttons["live_telemetry"].configure(
            state="disabled" if self.telemetry_live_active else "normal"
        )
        self.action_buttons["stop_telemetry"].configure(
            state="normal" if self.telemetry_live_active else "disabled"
        )
        self.env_combo.configure(state="disabled" if busy or not profile.envs else "readonly")
        servo_state = "normal" if profile.can_command_servo() and not busy else "disabled"
        if profile.can_command_servo():
            if not self.servo_box_visible:
                self.servo_box.pack(fill="x", pady=(0, 10), before=self.action_hint)
                self.servo_box_visible = True
            try:
                output = servo_output_for(profile, int(self.servo_channel.get()))
                self.servo_channel_label.configure(text=f"{output.label} -> PCA {output.pca_channel}")
            except ValueError:
                self.servo_channel_label.configure(text="")
        elif self.servo_box_visible:
            self.servo_box.pack_forget()
            self.servo_box_visible = False
        for widget in self.servo_widgets:
            widget.configure(state=servo_state)

        editor_state = "disabled" if busy else "normal"
        for widget in self.mission_widgets:
            widget.configure(state=editor_state)
        if editor_state == "normal":
            for widget in self.mission_readonly_widgets:
                widget.configure(state="readonly")

        croi_flash_state = "normal" if profile.board_id == "croi" and not busy else "disabled"
        for widget in (*self.mission_flash_widgets, *self.logging_flash_widgets):
            widget.configure(state=croi_flash_state)

        lamh_flash_state = "normal" if profile.board_id == "lamh" and not busy else "disabled"
        for widget in self.lamh_config_flash_widgets:
            widget.configure(state=lamh_flash_state)

        for widget in self.radio_widgets:
            widget.configure(state=editor_state)
        radio_flash_state = "normal" if profile.board_id == "teachtaire" and not busy else "disabled"
        for widget in self.radio_flash_widgets:
            widget.configure(state=radio_flash_state)

        for widget in self.logging_widgets:
            widget.configure(state=editor_state)

    @staticmethod
    def _format_stack_summary(nodes: dict[str, Any]) -> str:
        parts: list[str] = []
        for name, node in nodes.items():
            err_flags = node.get("err_flags", [])
            flag_text = ",".join(err_flags) if err_flags else "ok"
            parts.append(f"{name}: {flag_text}, uptime={node.get('uptime_s')}s")
        return "CAN heartbeat stack health: " + "; ".join(parts)

    def _on_close(self) -> None:
        self.telemetry_stop_requested.set()
        self.controller.close()
        self.destroy()


def run() -> None:
    app = OgmaApp()
    app.mainloop()
