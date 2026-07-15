from __future__ import annotations

import csv
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

from .can_decoder import decode_can_frame, parse_can_log_line, summarize_stack_health
from .can_layouts import CanFrameLayout, attach_can_ids, load_can_ids, load_payload_layouts
from .groundstation import parse_groundstation_text
from .paths import CAN_FRAMES_HEADER, PAYLOAD_LAYOUTS_CSV


_TERMINAL_CONTROL_RE = re.compile(
    r"\x1b\].*?(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]"
)


def _clean_serial_line(line: str) -> str:
    return _TERMINAL_CONTROL_RE.sub("", line).strip()


def load_default_can_frames() -> dict[str, CanFrameLayout]:
    return attach_can_ids(
        load_payload_layouts(PAYLOAD_LAYOUTS_CSV),
        load_can_ids(CAN_FRAMES_HEADER),
    )


def parse_mixed_telemetry_text(text: str, frames: dict[str, CanFrameLayout]) -> dict[str, Any]:
    can_frames: list[dict[str, Any]] = []
    gps_lines: list[str] = []
    warnings: list[str] = []
    total_lines = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = _clean_serial_line(line)
        if not stripped or stripped.startswith("#"):
            continue
        total_lines += 1
        try:
            can_id, data = parse_can_log_line(stripped)
        except ValueError:
            parsed_line = parse_groundstation_text(stripped)
            if parsed_line["records"]:
                gps_lines.append(stripped)
            else:
                reason = parsed_line["summary"]["warnings"][0] if parsed_line["summary"]["warnings"] else "unrecognized line"
                warnings.append(f"line {line_no}: {reason.replace('line 1: ', '')}")
            continue
        packet = decode_can_frame(frames, can_id, data)
        packet["line"] = line_no
        can_frames.append(packet)

    groundstation = parse_groundstation_text("\n".join(gps_lines))
    stack = summarize_stack_health(can_frames)
    can_summary = {
        "frames": len(can_frames),
        "warnings": [warning for packet in can_frames for warning in packet.get("warnings", [])],
        "unknown_frames": sum(1 for packet in can_frames if packet["frame"] == "UNKNOWN"),
        "heartbeat_nodes": stack["node_count"],
        "heartbeat_nodes_with_errors": stack["nodes_with_errors"],
    }
    summary = {
        "lines": total_lines,
        "gps_records": groundstation["summary"]["records"],
        "gps_fix_records": groundstation["summary"]["fix_records"],
        "can_frames": can_summary["frames"],
        "unknown_can_frames": can_summary["unknown_frames"],
        "heartbeat_nodes": stack["node_count"],
        "heartbeat_nodes_with_errors": stack["nodes_with_errors"],
        "warnings": warnings,
    }
    return {
        "summary": summary,
        "groundstation": groundstation,
        "can": {
            "summary": can_summary,
            "stack": stack,
            "frames": can_frames,
        },
    }


class MixedTelemetryAccumulator:
    """Incrementally decode a serial telemetry stream without reopening the port."""

    def __init__(self, frames: dict[str, CanFrameLayout], history_limit: int | None = None) -> None:
        if history_limit is not None and history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.frames = frames
        self.history_limit = history_limit
        self.can_frames: list[dict[str, Any]] = []
        self.gps_lines: list[str] = []
        self.gps_received_s: list[float] = []
        self.warnings: list[str] = []
        self.total_lines = 0
        self.total_can_frames = 0
        self.total_gps_records = 0
        self.total_gps_fix_records = 0
        self.total_unknown_can_frames = 0
        self.source_line = 0
        self._partial = ""
        self._started = time.monotonic()

    def feed(self, text: str) -> None:
        combined = self._partial + text
        self._partial = ""
        for part in combined.splitlines(keepends=True):
            if part.endswith(("\n", "\r")):
                self._process_line(part.rstrip("\r\n"))
            else:
                self._partial = part

    def finish(self) -> None:
        if self._partial:
            self._process_line(self._partial)
            self._partial = ""

    def snapshot(self) -> dict[str, Any]:
        groundstation = parse_groundstation_text("\n".join(self.gps_lines))
        for record, received_s in zip(groundstation["records"], self.gps_received_s):
            record["received_s"] = received_s
        stack = summarize_stack_health(self.can_frames)
        can_warnings = [warning for packet in self.can_frames for warning in packet.get("warnings", [])]
        return {
            "summary": {
                "elapsed_s": round(time.monotonic() - self._started, 3),
                "lines": self.total_lines,
                "gps_records": self.total_gps_records,
                "gps_fix_records": self.total_gps_fix_records,
                "can_frames": self.total_can_frames,
                "unknown_can_frames": self.total_unknown_can_frames,
                "heartbeat_nodes": stack["node_count"],
                "heartbeat_nodes_with_errors": stack["nodes_with_errors"],
                "warnings": list(self.warnings),
            },
            "groundstation": groundstation,
            "can": {
                "summary": {
                    "frames": self.total_can_frames,
                    "warnings": can_warnings,
                    "unknown_frames": self.total_unknown_can_frames,
                    "heartbeat_nodes": stack["node_count"],
                    "heartbeat_nodes_with_errors": stack["nodes_with_errors"],
                },
                "stack": stack,
                "frames": list(self.can_frames),
            },
        }

    def _process_line(self, line: str) -> None:
        self.source_line += 1
        stripped = _clean_serial_line(line)
        if not stripped or stripped.startswith("#"):
            return
        self.total_lines += 1
        try:
            can_id, data = parse_can_log_line(stripped)
        except ValueError:
            parsed_line = parse_groundstation_text(stripped)
            if parsed_line["records"]:
                self.gps_lines.append(stripped)
                self.gps_received_s.append(time.monotonic() - self._started)
                self.total_gps_records += len(parsed_line["records"])
                self.total_gps_fix_records += sum(1 for record in parsed_line["records"] if record.get("fix"))
                self._trim_history(self.gps_lines, self.gps_received_s)
                return
            reason = (
                parsed_line["summary"]["warnings"][0]
                if parsed_line["summary"]["warnings"]
                else "unrecognized line"
            )
            self.warnings.append(
                f"line {self.source_line}: {reason.replace('line 1: ', '')}"
            )
            self._trim_history(self.warnings)
            return
        packet = decode_can_frame(self.frames, can_id, data)
        packet["line"] = self.source_line
        packet["received_s"] = time.monotonic() - self._started
        self.can_frames.append(packet)
        self.total_can_frames += 1
        if packet["frame"] == "UNKNOWN":
            self.total_unknown_can_frames += 1
        self._trim_history(self.can_frames)

    def _trim_history(self, *collections: list[Any]) -> None:
        if self.history_limit is None:
            return
        for collection in collections:
            overflow = len(collection) - self.history_limit
            if overflow > 0:
                del collection[:overflow]


def parse_mixed_telemetry_file(path: Path, frames: dict[str, CanFrameLayout]) -> dict[str, Any]:
    return parse_mixed_telemetry_text(path.read_text(encoding="utf-8", errors="replace"), frames)


def save_mixed_telemetry_bundle(
    parsed: dict[str, Any],
    source: Path | str,
    out_root: Path,
    raw_text: str | None = None,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / stamp
    return save_mixed_telemetry_session(parsed, source, out, raw_text=raw_text)


def save_mixed_telemetry_session(
    parsed: dict[str, Any],
    source: Path | str,
    out: Path,
    raw_text: str | None = None,
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(parsed["summary"], indent=2), encoding="utf-8")
    (out / "source.txt").write_text(str(source), encoding="utf-8")
    if raw_text is not None:
        (out / "raw.txt").write_text(raw_text, encoding="utf-8")
    (out / "groundstation.json").write_text(json.dumps(parsed["groundstation"], indent=2), encoding="utf-8")
    (out / "can.json").write_text(json.dumps(parsed["can"], indent=2), encoding="utf-8")
    _write_records_csv(out / "groundstation_records.csv", parsed["groundstation"]["records"])
    return out


def load_mixed_telemetry_session(path: Path) -> dict[str, Any]:
    summary_path = path / "summary.json"
    can_path = path / "can.json"
    groundstation_path = path / "groundstation.json"
    missing = [item.name for item in (summary_path, can_path, groundstation_path) if not item.exists()]
    if missing:
        raise ValueError(f"telemetry session missing: {', '.join(missing)}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    can = json.loads(can_path.read_text(encoding="utf-8"))
    groundstation = json.loads(groundstation_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or not isinstance(can, dict) or not isinstance(groundstation, dict):
        raise ValueError("telemetry session JSON structure is invalid")
    return {"summary": summary, "can": can, "groundstation": groundstation}


def _write_records_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "sample_index",
        "latitude_deg",
        "longitude_deg",
        "satellites",
        "altitude_m",
        "fix_time_s",
        "fix",
        "rssi_dbm",
        "raw",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
