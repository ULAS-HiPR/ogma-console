from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GroundstationRecord:
    sample_index: int
    latitude_deg: float
    longitude_deg: float
    satellites: int
    altitude_m: float
    fix_time_s: int
    fix: bool
    rssi_dbm: int | None
    raw: str


def parse_groundstation_text(text: str) -> dict[str, Any]:
    records: list[GroundstationRecord] = []
    warnings: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            record = _parse_line(stripped, len(records))
        except ValueError as exc:
            warnings.append(f"line {line_no}: {exc}")
            continue
        records.append(record)
    return _bundle(records, warnings)


def parse_groundstation_file(path: Path) -> dict[str, Any]:
    return parse_groundstation_text(path.read_text(encoding="utf-8", errors="replace"))


def save_groundstation_bundle(
    parsed: dict[str, Any],
    source: Path | str,
    out_root: Path,
    raw_text: str | None = None,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(parsed["summary"], indent=2), encoding="utf-8")
    (out / "source.txt").write_text(str(source), encoding="utf-8")
    if raw_text is not None:
        (out / "raw.txt").write_text(raw_text, encoding="utf-8")
    _write_records_csv(out / "records.csv", parsed["records"])
    (out / "records.json").write_text(json.dumps(parsed["records"], indent=2), encoding="utf-8")
    return out


def _parse_line(line: str, sample_index: int) -> GroundstationRecord:
    if line.startswith("{"):
        return _parse_json_line(line, sample_index)
    return _parse_csv_line(line, sample_index)


def _parse_json_line(line: str, sample_index: int) -> GroundstationRecord:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"bad JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON packet is not an object")
    lat = _float_field(data, "lat", "latitude", "latitude_deg")
    lon = _float_field(data, "lon", "longitude", "longitude_deg")
    sat = _int_field(data, "sat", "sats", "satellites")
    alt = _float_field(data, "alt", "altitude", "altitude_m")
    fix_time = _int_field(data, "fix_time", "fix_time_s", default=0)
    rssi = _optional_int_field(data, "rssi", "rssi_dbm")
    fix = bool(data.get("fix", (lat != 0.0 or lon != 0.0)))
    return GroundstationRecord(sample_index, lat, lon, sat, alt, fix_time, fix, rssi, line)


def _parse_csv_line(line: str, sample_index: int) -> GroundstationRecord:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 5:
        raise ValueError("expected lat,lon,sat,alt,fix_time[,rssi]")
    try:
        lat = float(parts[0])
        lon = float(parts[1])
        sat = int(float(parts[2]))
        alt = float(parts[3])
        fix_time = int(float(parts[4]))
        rssi = int(float(parts[5])) if len(parts) >= 6 and parts[5] else None
    except ValueError as exc:
        raise ValueError("could not parse numeric GPS packet") from exc
    fix = lat != 0.0 or lon != 0.0
    return GroundstationRecord(sample_index, lat, lon, sat, alt, fix_time, fix, rssi, line)


def _bundle(records: list[GroundstationRecord], warnings: list[str]) -> dict[str, Any]:
    serializable = [asdict(record) for record in records]
    fixes = [record for record in records if record.fix]
    last = records[-1] if records else None
    last_fix = fixes[-1] if fixes else None
    rssi_values = [record.rssi_dbm for record in records if record.rssi_dbm is not None]
    summary = {
        "records": len(records),
        "fix_records": len(fixes),
        "warnings": warnings,
        "latest_latitude_deg": last.latitude_deg if last else 0.0,
        "latest_longitude_deg": last.longitude_deg if last else 0.0,
        "latest_altitude_m": last.altitude_m if last else 0.0,
        "latest_fix": bool(last.fix) if last else False,
        "last_fix_latitude_deg": last_fix.latitude_deg if last_fix else 0.0,
        "last_fix_longitude_deg": last_fix.longitude_deg if last_fix else 0.0,
        "last_fix_altitude_m": last_fix.altitude_m if last_fix else 0.0,
        "max_satellites": max((record.satellites for record in records), default=0),
        "min_rssi_dbm": min(rssi_values) if rssi_values else None,
        "max_rssi_dbm": max(rssi_values) if rssi_values else None,
    }
    return {"summary": summary, "records": serializable}


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


def _float_field(data: dict[str, Any], *keys: str, default: float | None = None) -> float:
    value = _field(data, *keys, default=default)
    if value is None:
        raise ValueError(f"missing float field: {'/'.join(keys)}")
    return float(value)


def _int_field(data: dict[str, Any], *keys: str, default: int | None = None) -> int:
    value = _field(data, *keys, default=default)
    if value is None:
        raise ValueError(f"missing int field: {'/'.join(keys)}")
    return int(float(value))


def _optional_int_field(data: dict[str, Any], *keys: str) -> int | None:
    value = _field(data, *keys, default=None)
    if value in (None, ""):
        return None
    return int(float(value))


def _field(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return default
