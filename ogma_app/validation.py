from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .boards import BoardProfile
from .controller import DetectionResult
from .health import evaluate_health
from .probe import ProbeResult


class ValidationController(Protocol):
    def detect(self) -> DetectionResult: ...

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ValidationRunResult:
    report: dict[str, Any]
    out: Path


def run_bench_validation(
    controller: ValidationController,
    expected_board_id: str | None,
    out_root: Path,
    probe_fn: Callable[[], ProbeResult] | None = None,
) -> ValidationRunResult:
    report: dict[str, Any] = {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "expected_board_id": expected_board_id,
        "ok": False,
        "probe": None,
        "detection": None,
        "status": None,
        "health": None,
        "errors": [],
        "warnings": [],
    }

    # st-info --probe resets or halts some STM32/ST-Link combinations. Automatic
    # validation relies on the identity/status OpenOCD path, which resumes the
    # target after each read. Tests may inject a non-disruptive probe function.
    if probe_fn is not None:
        try:
            probe = probe_fn()
            report["probe"] = _probe_dict(probe)
            if not probe.connected:
                report["errors"].append("ST-Link target not connected")
        except Exception as exc:
            report["errors"].append(f"probe failed: {exc}")

    try:
        detection = controller.detect()
        report["detection"] = _detection_dict(detection)
    except Exception as exc:
        report["errors"].append(f"detect failed: {exc}")
        detection = DetectionResult(None, None, "detect exception")

    profile = detection.profile
    if profile is None:
        report["errors"].append(f"board not detected: {detection.reason}")
    else:
        if expected_board_id is not None and profile.board_id != expected_board_id:
            report["errors"].append(f"expected {expected_board_id}, detected {profile.board_id}")
        if profile.can_read_status():
            try:
                status = detection.status or controller.read_status(profile.board_id, profile.default_env)
                report["status"] = status
                health = evaluate_health(profile.board_id, status)
                report["health"] = _health_dict(health)
                if not health.ok:
                    report["errors"].append("health failed")
                elif any(check.state == "warn" for check in health.checks):
                    report["warnings"].append("health has warnings")
            except Exception as exc:
                report["errors"].append(f"status/health failed: {exc}")
        else:
            report["warnings"].append(f"{profile.board_id} has no status block profile")

    report["ok"] = not report["errors"]
    out = save_validation_report(report, out_root)
    return ValidationRunResult(report, out)


def save_validation_report(report: dict[str, Any], out_root: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    board = report.get("expected_board_id") or _detected_board_id(report) or "detected"
    out = out_root / str(board) / stamp / "validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return out


def _probe_dict(probe: ProbeResult) -> dict[str, Any]:
    return {
        "connected": probe.connected,
        "programmers": probe.programmers,
        "fields": probe.fields,
        "returncode": probe.returncode,
        "raw": probe.raw,
    }


def _detection_dict(detection: DetectionResult) -> dict[str, Any]:
    profile = detection.profile
    data: dict[str, Any] = {
        "board_id": profile.board_id if profile else None,
        "display_name": profile.display_name if profile else None,
        "reason": detection.reason,
        "status_present": detection.status is not None,
    }
    if detection.identity is not None:
        data["identity"] = {
            "board_id": detection.identity.board_id,
            "capabilities": detection.identity.capability_names(),
            "firmware_version": detection.identity.firmware_version,
            "firmware_build": detection.identity.firmware_build,
        }
    return data


def _health_dict(report) -> dict[str, Any]:
    return {
        "board_id": report.board_id,
        "ok": report.ok,
        "checks": [
            {"name": check.name, "state": check.state, "detail": check.detail}
            for check in report.checks
        ],
    }


def _detected_board_id(report: dict[str, Any]) -> str | None:
    detection = report.get("detection")
    if isinstance(detection, dict):
        board_id = detection.get("board_id")
        if isinstance(board_id, str):
            return board_id
    return None
