from __future__ import annotations

import csv
import datetime as dt
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .boards import profile_for, servo_output_for
from .health import HealthReport, evaluate_health


TEACHTAIRE_TEST_ENVS = {
    "flight": "teachtaire_flight",
    "lora_tx": "teachtaire_lora_tx",
    "lora_rx": "teachtaire_lora_rx",
}


class TeachtaireController(Protocol):
    def flash(self, board_id: str, env: str) -> None: ...

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, Any]: ...


class LamhController(Protocol):
    def send_lamh_servo_command(self, channel: int, angle_deg: int, env: str | None = None) -> None: ...

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, Any]: ...


class FoinseController(Protocol):
    def read_status(self, board_id: str, env: str | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TeachtaireTestResult:
    mode: str
    env: str
    summary: dict[str, Any]
    samples: list[dict[str, Any]]
    out: Path


@dataclass(frozen=True)
class LamhServoTestResult:
    output: int
    pca_channel: int
    env: str
    summary: dict[str, Any]
    samples: list[dict[str, Any]]
    out: Path


@dataclass(frozen=True)
class FoinseMonitorResult:
    env: str
    summary: dict[str, Any]
    samples: list[dict[str, Any]]
    out: Path


def teachtaire_mode_for_env(env: str) -> str:
    for mode, candidate in TEACHTAIRE_TEST_ENVS.items():
        if candidate == env:
            return mode
    raise ValueError(f"no Teachtaire test mode for env: {env}")


def teachtaire_env_for_mode(mode: str) -> str:
    try:
        return TEACHTAIRE_TEST_ENVS[mode]
    except KeyError as exc:
        choices = ", ".join(sorted(TEACHTAIRE_TEST_ENVS))
        raise ValueError(f"unknown Teachtaire test mode {mode!r}; choose {choices}") from exc


def run_teachtaire_test(
    controller: TeachtaireController,
    mode: str,
    duration_s: float,
    interval_s: float,
    out_root: Path,
    flash: bool = True,
) -> TeachtaireTestResult:
    env = teachtaire_env_for_mode(mode)
    if flash:
        controller.flash("teachtaire", env)
    samples = _poll_teachtaire(controller, env, duration_s, interval_s)
    summary = summarize_teachtaire_test(mode, env, samples, flashed=flash)
    out = save_teachtaire_test_bundle(mode, env, summary, samples, out_root)
    return TeachtaireTestResult(mode=mode, env=env, summary=summary, samples=samples, out=out)


def run_lamh_servo_test(
    controller: LamhController,
    output_index: int,
    angles: list[int],
    dwell_s: float,
    out_root: Path,
    env: str | None = None,
) -> LamhServoTestResult:
    profile = profile_for("lamh")
    selected_env = env or profile.default_env
    if selected_env is None:
        raise RuntimeError("lamh has no default env")
    output = servo_output_for(profile, output_index)
    checked_angles = [_clamp_angle(angle) for angle in angles]
    if not checked_angles:
        raise ValueError("at least one servo angle is required")
    samples: list[dict[str, Any]] = []
    start = time.monotonic()
    for angle in checked_angles:
        controller.send_lamh_servo_command(output.pca_channel, angle, selected_env)
        time.sleep(max(0.0, dwell_s))
        status = controller.read_status("lamh", selected_env)
        report = evaluate_health("lamh", status)
        samples.append(_make_lamh_servo_sample(time.monotonic() - start, output.index, output.pca_channel, angle, status, report))
    summary = summarize_lamh_servo_test(output.index, output.pca_channel, selected_env, checked_angles, samples)
    out = save_lamh_servo_test_bundle(output.index, selected_env, summary, samples, out_root)
    return LamhServoTestResult(output=output.index, pca_channel=output.pca_channel, env=selected_env, summary=summary, samples=samples, out=out)


def run_foinse_monitor(
    controller: FoinseController,
    duration_s: float,
    interval_s: float,
    out_root: Path,
    env: str | None = None,
) -> FoinseMonitorResult:
    profile = profile_for("foinse")
    selected_env = env or profile.default_env
    if selected_env is None:
        raise RuntimeError("foinse has no default env")
    samples: list[dict[str, Any]] = []
    start = time.monotonic()
    deadline = start + max(0.0, duration_s)
    while time.monotonic() <= deadline or not samples:
        status = controller.read_status("foinse", selected_env)
        report = evaluate_health("foinse", status)
        samples.append(_make_foinse_sample(time.monotonic() - start, status, report))
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.05, interval_s))
    summary = summarize_foinse_monitor(selected_env, samples)
    out = save_foinse_monitor_bundle(selected_env, summary, samples, out_root)
    return FoinseMonitorResult(env=selected_env, summary=summary, samples=samples, out=out)


def parse_angle_list(text: str) -> list[int]:
    angles: list[int] = []
    for item in text.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        angles.append(_clamp_angle(int(stripped)))
    return angles


def summarize_lamh_servo_test(
    output: int,
    pca_channel: int,
    env: str,
    angles: list[int],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    latest = samples[-1]["status"] if samples else {}
    reports = [sample["health"] for sample in samples]
    return {
        "board_id": "lamh",
        "output": output,
        "pca_channel": pca_channel,
        "env": env,
        "angles": angles,
        "sample_count": len(samples),
        "healthy_samples": sum(1 for report in reports if report["ok"]),
        "latest_health_ok": bool(reports[-1]["ok"]) if reports else False,
        "latest_servo_angle": int(latest.get("servo_angle", 0) or 0),
        "latest_servo_pwm": int(latest.get("servo_pwm", 0) or 0),
        "latest_servo_set_count": int(latest.get("servo_set_count", 0) or 0),
        "latest_pca9685_found": int(latest.get("pca9685_found", 0) or 0),
        "fail_checks": sorted(
            {
                check["name"]
                for report in reports
                for check in report["checks"]
                if check["state"] == "fail"
            }
        ),
        "warn_checks": sorted(
            {
                check["name"]
                for report in reports
                for check in report["checks"]
                if check["state"] == "warn"
            }
        ),
    }


def summarize_foinse_monitor(env: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    reports = [sample["health"] for sample in samples]
    summary: dict[str, Any] = {
        "board_id": "foinse",
        "env": env,
        "sample_count": len(samples),
        "healthy_samples": sum(1 for report in reports if report["ok"]),
        "latest_health_ok": bool(reports[-1]["ok"]) if reports else False,
        "fail_checks": sorted(
            {
                check["name"]
                for report in reports
                for check in report["checks"]
                if check["state"] == "fail"
            }
        ),
        "warn_checks": sorted(
            {
                check["name"]
                for report in reports
                for check in report["checks"]
                if check["state"] == "warn"
            }
        ),
    }
    for key in ("sense1_raw", "sense2_raw", "sense1_mv", "sense2_mv", "sense1_current_ma", "sense2_current_ma"):
        values = [int(sample["status"].get(key, 0) or 0) for sample in samples]
        summary[f"{key}_min"] = min(values) if values else 0
        summary[f"{key}_max"] = max(values) if values else 0
        summary[f"{key}_avg"] = round(sum(values) / len(values), 3) if values else 0.0
    latest = samples[-1]["status"] if samples else {}
    summary["latest_uptime_ms"] = int(latest.get("uptime_ms", 0) or 0)
    summary["latest_loop_count"] = int(latest.get("loop_count", 0) or 0)
    return summary


def save_lamh_servo_test_bundle(
    output: int,
    env: str,
    summary: dict[str, Any],
    samples: list[dict[str, Any]],
    out_root: Path,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / "lamh" / f"pwm{output}" / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    _write_lamh_servo_test_csv(out / "samples.csv", env, samples)
    return out


def save_foinse_monitor_bundle(
    env: str,
    summary: dict[str, Any],
    samples: list[dict[str, Any]],
    out_root: Path,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / "foinse" / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    _write_foinse_monitor_csv(out / "samples.csv", env, samples)
    return out


def summarize_teachtaire_test(
    mode: str,
    env: str,
    samples: list[dict[str, Any]],
    flashed: bool,
) -> dict[str, Any]:
    latest = samples[-1]["status"] if samples else {}
    reports = [sample["health"] for sample in samples]
    ok_samples = sum(1 for report in reports if report["ok"])
    fail_checks = sorted(
        {
            check["name"]
            for report in reports
            for check in report["checks"]
            if check["state"] == "fail"
        }
    )
    warn_checks = sorted(
        {
            check["name"]
            for report in reports
            for check in report["checks"]
            if check["state"] == "warn"
        }
    )
    return {
        "board_id": "teachtaire",
        "mode": mode,
        "env": env,
        "flashed": flashed,
        "sample_count": len(samples),
        "healthy_samples": ok_samples,
        "latest_health_ok": bool(reports[-1]["ok"]) if reports else False,
        "fail_checks": fail_checks,
        "warn_checks": warn_checks,
        "latest_lora_tx_count": int(latest.get("lora_tx_count", 0) or 0),
        "latest_lora_tx_done_count": int(latest.get("lora_tx_done_count", 0) or 0),
        "latest_lora_rx_count": int(latest.get("lora_rx_count", 0) or 0),
        "latest_gnss_fix": int(latest.get("gnss_fix", 0) or 0),
        "latest_gnss_sats": int(latest.get("gnss_sats", 0) or 0),
        "latest_gnss_bytes": int(latest.get("gnss_bytes", 0) or 0),
    }


def save_teachtaire_test_bundle(
    mode: str,
    env: str,
    summary: dict[str, Any],
    samples: list[dict[str, Any]],
    out_root: Path,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / "teachtaire" / mode / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    _write_teachtaire_test_csv(out / "samples.csv", env, samples)
    return out


def _poll_teachtaire(
    controller: TeachtaireController,
    env: str,
    duration_s: float,
    interval_s: float,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    start = time.monotonic()
    deadline = start + max(0.0, duration_s)
    while time.monotonic() <= deadline or not samples:
        elapsed = time.monotonic() - start
        status = controller.read_status("teachtaire", env)
        report = evaluate_health("teachtaire", status)
        samples.append(_make_teachtaire_sample(elapsed, status, report))
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.05, interval_s))
    return samples


def _make_teachtaire_sample(
    elapsed_s: float,
    status: dict[str, Any],
    report: HealthReport,
) -> dict[str, Any]:
    return {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
        "elapsed_s": elapsed_s,
        "status": status,
        "health": {
            "ok": report.ok,
            "checks": [
                {"name": check.name, "state": check.state, "detail": check.detail}
                for check in report.checks
            ],
        },
    }


def _make_lamh_servo_sample(
    elapsed_s: float,
    output: int,
    pca_channel: int,
    command_angle: int,
    status: dict[str, Any],
    report: HealthReport,
) -> dict[str, Any]:
    return {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
        "elapsed_s": elapsed_s,
        "output": output,
        "pca_channel": pca_channel,
        "command_angle": command_angle,
        "status": status,
        "health": {
            "ok": report.ok,
            "checks": [
                {"name": check.name, "state": check.state, "detail": check.detail}
                for check in report.checks
            ],
        },
    }


def _make_foinse_sample(
    elapsed_s: float,
    status: dict[str, Any],
    report: HealthReport,
) -> dict[str, Any]:
    return {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
        "elapsed_s": elapsed_s,
        "status": status,
        "health": {
            "ok": report.ok,
            "checks": [
                {"name": check.name, "state": check.state, "detail": check.detail}
                for check in report.checks
            ],
        },
    }


def _write_teachtaire_test_csv(path: Path, env: str, samples: list[dict[str, Any]]) -> None:
    profile = profile_for("teachtaire")
    status_fields = [field.name for field in profile.status_block.fields] if profile.status_block else []
    fields = ["timestamp_utc", "elapsed_s", "env", "health_ok", *status_fields]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for sample in samples:
            status = sample.get("status", {})
            if not isinstance(status, dict):
                status = {}
            row = {
                "timestamp_utc": sample.get("timestamp_utc", ""),
                "elapsed_s": sample.get("elapsed_s", ""),
                "env": env,
                "health_ok": sample.get("health", {}).get("ok", False),
            }
            row.update({field: status.get(field, "") for field in status_fields})
            writer.writerow(row)


def _write_lamh_servo_test_csv(path: Path, env: str, samples: list[dict[str, Any]]) -> None:
    profile = profile_for("lamh")
    status_fields = [field.name for field in profile.status_block.fields] if profile.status_block else []
    fields = ["timestamp_utc", "elapsed_s", "env", "output", "pca_channel", "command_angle", "health_ok", *status_fields]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for sample in samples:
            status = sample.get("status", {})
            if not isinstance(status, dict):
                status = {}
            row = {
                "timestamp_utc": sample.get("timestamp_utc", ""),
                "elapsed_s": sample.get("elapsed_s", ""),
                "env": env,
                "output": sample.get("output", ""),
                "pca_channel": sample.get("pca_channel", ""),
                "command_angle": sample.get("command_angle", ""),
                "health_ok": sample.get("health", {}).get("ok", False),
            }
            row.update({field: status.get(field, "") for field in status_fields})
            writer.writerow(row)


def _write_foinse_monitor_csv(path: Path, env: str, samples: list[dict[str, Any]]) -> None:
    profile = profile_for("foinse")
    status_fields = [field.name for field in profile.status_block.fields] if profile.status_block else []
    fields = ["timestamp_utc", "elapsed_s", "env", "health_ok", *status_fields]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for sample in samples:
            status = sample.get("status", {})
            if not isinstance(status, dict):
                status = {}
            row = {
                "timestamp_utc": sample.get("timestamp_utc", ""),
                "elapsed_s": sample.get("elapsed_s", ""),
                "env": env,
                "health_ok": sample.get("health", {}).get("ok", False),
            }
            row.update({field: status.get(field, "") for field in status_fields})
            writer.writerow(row)


def _clamp_angle(angle: int) -> int:
    if not 0 <= angle <= 180:
        raise ValueError(f"servo angle out of range: {angle}")
    return angle
