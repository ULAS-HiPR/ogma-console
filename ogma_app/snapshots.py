from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from .boards import BoardProfile
from .identity import BoardIdentity


def make_status_snapshot(
    profile: BoardProfile,
    env: str | None,
    status: dict[str, Any],
    identity: BoardIdentity | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "board_id": profile.board_id,
        "display_name": profile.display_name,
        "role": profile.role,
        "env": env,
        "status": status,
    }
    if identity is not None:
        snapshot["identity"] = {
            "board_id": identity.board_id,
            "capabilities": identity.capability_names(),
            "firmware_version": identity.firmware_version,
            "firmware_build": identity.firmware_build,
        }
    return snapshot


def save_status_snapshot(snapshot: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        _write_status_csv(snapshot, path)
    else:
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return path


def make_status_sample(
    elapsed_s: float,
    status: dict[str, Any],
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "timestamp_utc": timestamp_utc or dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
        "elapsed_s": elapsed_s,
        "status": status,
    }


def save_status_series(
    profile: BoardProfile,
    env: str | None,
    samples: list[dict[str, Any]],
    out_root: Path,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / profile.board_id / stamp
    out.mkdir(parents=True, exist_ok=True)
    fields = _status_series_fields(samples)
    summary = {
        "board_id": profile.board_id,
        "display_name": profile.display_name,
        "env": env,
        "sample_count": len(samples),
        "fields": fields,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    _write_status_series_csv(out / "samples.csv", samples, fields)
    return out


def _write_status_csv(snapshot: dict[str, Any], path: Path) -> None:
    status = snapshot.get("status", {})
    if not isinstance(status, dict):
        status = {}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["field", "value"])
        writer.writeheader()
        for key in ("timestamp_utc", "board_id", "display_name", "role", "env"):
            writer.writerow({"field": key, "value": snapshot.get(key, "")})
        identity = snapshot.get("identity")
        if isinstance(identity, dict):
            for key, value in identity.items():
                writer.writerow({"field": f"identity.{key}", "value": value})
        for key, value in status.items():
            writer.writerow({"field": f"status.{key}", "value": value})


def _status_series_fields(samples: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for sample in samples:
        status = sample.get("status", {})
        if not isinstance(status, dict):
            continue
        for key in status:
            if key not in fields:
                fields.append(key)
    return fields


def _write_status_series_csv(path: Path, samples: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp_utc", "elapsed_s", *fields])
        writer.writeheader()
        for sample in samples:
            status = sample.get("status", {})
            if not isinstance(status, dict):
                status = {}
            row = {
                "timestamp_utc": sample.get("timestamp_utc", ""),
                "elapsed_s": sample.get("elapsed_s", ""),
            }
            row.update({field: status.get(field, "") for field in fields})
            writer.writerow(row)
