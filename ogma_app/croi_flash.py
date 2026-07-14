from __future__ import annotations

import csv
import datetime as dt
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FLASH_LOG_MAGIC = 0x48495052
FLASH_LOG_VERSION = 2
FLASH_LOG_UNCOMMITTED = 0xFFFFFFFF
FLASH_LOG_COMMITTED = 0x00000000
FNV1A_OFFSET = 2166136261
FNV1A_PRIME = 16777619

PAYLOAD_FLIGHT_DATA = 1
PAYLOAD_SECONDARY_FLIGHT_DATA = 2

HEADER = struct.Struct("<IHHIIIHHIIII")
FLIGHT_DATA_SIZE = 60
SECONDARY_DATA_SIZE_V1 = 48
SECONDARY_DATA_SIZE_V2 = 64


@dataclass(frozen=True)
class FlashRecord:
    address: int
    header: dict[str, int]
    payload: bytes


def align4(value: int) -> int:
    return (value + 3) & ~3


def fnv1a(data: bytes) -> int:
    value = FNV1A_OFFSET
    for byte in data:
        value ^= byte
        value = (value * FNV1A_PRIME) & 0xFFFFFFFF
    return value


def header_checksum(header_bytes: bytes) -> int:
    data = bytearray(header_bytes)
    struct.pack_into("<I", data, 32, 0)
    struct.pack_into("<I", data, 36, FLASH_LOG_UNCOMMITTED)
    return fnv1a(bytes(data))


def _parse_header(blob: bytes, offset: int) -> dict[str, int]:
    values = HEADER.unpack_from(blob, offset)
    return {
        "magic": values[0],
        "version": values[1],
        "header_size": values[2],
        "run_id": values[3],
        "sequence": values[4],
        "timestamp_ms": values[5],
        "payload_type": values[6],
        "payload_version": values[7],
        "payload_length": values[8],
        "payload_checksum": values[9],
        "header_checksum": values[10],
        "commit_marker": values[11],
    }


def parse_flight_data(payload: bytes, payload_version: int = 1) -> dict[str, Any]:
    if len(payload) < FLIGHT_DATA_SIZE:
        raise ValueError(f"flight_data too short: {len(payload)} < {FLIGHT_DATA_SIZE}")
    acceleration = struct.unpack_from("<f", payload, 12)[0]
    acceleration_m_s2 = acceleration if payload_version >= 1 else acceleration * 9.80665
    return {
        "time_ms": struct.unpack_from("<I", payload, 0)[0],
        "prediction_altitude_m": struct.unpack_from("<f", payload, 4)[0],
        "prediction_velocity_m_s": struct.unpack_from("<f", payload, 8)[0],
        "prediction_acceleration_m_s2": acceleration_m_s2,
        "prediction_acceleration_g": acceleration_m_s2 / 9.80665,
        "core_time_ms": struct.unpack_from("<I", payload, 16)[0],
        "pressure_pa": struct.unpack_from("<i", payload, 20)[0],
        "baro_temperature_c": struct.unpack_from("<f", payload, 24)[0],
        "baro_altitude_m": struct.unpack_from("<f", payload, 28)[0],
        "accel_x": struct.unpack_from("<f", payload, 32)[0],
        "accel_y": struct.unpack_from("<f", payload, 36)[0],
        "accel_z": struct.unpack_from("<f", payload, 40)[0],
        "gyro_x": struct.unpack_from("<h", payload, 44)[0],
        "gyro_y": struct.unpack_from("<h", payload, 46)[0],
        "gyro_z": struct.unpack_from("<h", payload, 48)[0],
        "imu_temperature": struct.unpack_from("<i", payload, 52)[0],
        "state": struct.unpack_from("<h", payload, 56)[0],
    }


def parse_secondary_data(payload: bytes, payload_version: int = 1) -> dict[str, Any]:
    required_size = SECONDARY_DATA_SIZE_V2 if payload_version >= 2 else SECONDARY_DATA_SIZE_V1
    if len(payload) < required_size:
        raise ValueError(f"secondary_flight_data too short: {len(payload)} < {required_size}")
    common = {
        "gps_latitude_deg": struct.unpack_from("<d", payload, 0)[0],
        "gps_longitude_deg": struct.unpack_from("<d", payload, 8)[0],
        "gps_altitude_m": struct.unpack_from("<f", payload, 16)[0],
        "gps_velocity_m_s": struct.unpack_from("<f", payload, 20)[0],
        "gps_satellites": struct.unpack_from("<B", payload, 24)[0],
    }
    if payload_version >= 1:
        common.update(
            {
                "actuator_output_index": struct.unpack_from("<B", payload, 32)[0],
                "actuator_sequence": struct.unpack_from("<H", payload, 34)[0],
                "actuator_angle_deg": struct.unpack_from("<f", payload, 36)[0],
                "actuator_active": bool(struct.unpack_from("<B", payload, 40)[0]),
            }
        )
    else:
        common.update(
            {
                "canards_kp": struct.unpack_from("<f", payload, 32)[0],
                "canards_kd": struct.unpack_from("<f", payload, 36)[0],
                "canards_servo_angle_deg": struct.unpack_from("<f", payload, 40)[0],
                "canards_active": bool(struct.unpack_from("<B", payload, 44)[0]),
            }
        )
    if payload_version >= 2:
        common.update(
            {
                "pyro_timestamp_ms": struct.unpack_from("<I", payload, 44)[0],
                "pyro_mission_tag": struct.unpack_from("<H", payload, 48)[0],
                "pyro_sequence": struct.unpack_from("<H", payload, 50)[0],
                "pyro_channel": struct.unpack_from("<B", payload, 52)[0],
                "pyro_action": struct.unpack_from("<B", payload, 53)[0],
                "pyro_result": struct.unpack_from("<B", payload, 54)[0],
                "pyro_fault": struct.unpack_from("<B", payload, 55)[0],
                "pyro_armed_mask": struct.unpack_from("<B", payload, 56)[0],
                "pyro_continuity_mask": struct.unpack_from("<B", payload, 57)[0],
                "pyro_fired_mask": struct.unpack_from("<B", payload, 58)[0],
            }
        )
    return common


def parse_croi_flash_dump(blob: bytes) -> dict[str, Any]:
    offset = 0
    records: list[dict[str, Any]] = []
    flight: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    warnings: list[str] = []

    while offset + HEADER.size <= len(blob):
        header_bytes = blob[offset : offset + HEADER.size]
        if all(byte == 0xFF for byte in header_bytes):
            break
        header = _parse_header(blob, offset)
        if header["magic"] != FLASH_LOG_MAGIC:
            warnings.append(f"bad magic at 0x{offset:06x}")
            break
        if header["version"] != FLASH_LOG_VERSION or header["header_size"] != HEADER.size:
            warnings.append(f"unsupported header at 0x{offset:06x}")
            break
        header_valid = header["header_checksum"] == header_checksum(header_bytes)
        if not header_valid:
            warnings.append(f"header checksum mismatch at 0x{offset:06x}")
        total_size = align4(header["header_size"] + header["payload_length"])
        if total_size <= 0 or offset + total_size > len(blob):
            warnings.append(f"truncated record at 0x{offset:06x}")
            break
        if header["commit_marker"] != FLASH_LOG_COMMITTED:
            warnings.append(f"incomplete record at 0x{offset:06x}")
            offset += total_size
            continue
        payload_start = offset + header["header_size"]
        payload_end = payload_start + header["payload_length"]
        payload = blob[payload_start:payload_end]
        payload_valid = fnv1a(payload) == header["payload_checksum"]
        if not payload_valid:
            warnings.append(f"payload checksum mismatch at 0x{offset:06x}")

        record = {
            "address": offset,
            **header,
            "header_checksum_valid": header_valid,
            "payload_checksum_valid": payload_valid,
        }
        records.append(record)
        if not header_valid or not payload_valid:
            warnings.append(f"record excluded from decoded data at 0x{offset:06x}")
            offset += total_size
            continue
        try:
            if header["payload_type"] == PAYLOAD_FLIGHT_DATA:
                if len(payload) != FLIGHT_DATA_SIZE:
                    warnings.append(f"flight_data size mismatch at 0x{offset:06x}: {len(payload)}")
                flight.append(
                    {**record, **parse_flight_data(payload, header["payload_version"])}
                )
            elif header["payload_type"] == PAYLOAD_SECONDARY_FLIGHT_DATA:
                expected_size = SECONDARY_DATA_SIZE_V2 if header["payload_version"] >= 2 else SECONDARY_DATA_SIZE_V1
                if len(payload) != expected_size:
                    warnings.append(f"secondary_data size mismatch at 0x{offset:06x}: {len(payload)}")
                decoded = {**record, **parse_secondary_data(payload, header["payload_version"])}
                secondary.append(decoded)
                if int(decoded.get("pyro_action", 0)) != 0:
                    events.append(decoded)
            else:
                warnings.append(f"unknown payload type {header['payload_type']} at 0x{offset:06x}")
        except (struct.error, ValueError) as exc:
            warnings.append(f"payload parse failed at 0x{offset:06x}: {exc}")
        offset += total_size

    duration_s = 0.0
    if records:
        duration_s = max(0.0, (records[-1]["timestamp_ms"] - records[0]["timestamp_ms"]) / 1000.0)
    run_ids = sorted({int(record["run_id"]) for record in records})
    return {
        "summary": {
            "records": len(records),
            "flight_records": len(flight),
            "secondary_records": len(secondary),
            "event_records": len(events),
            "run_ids": run_ids,
            "latest_run_id": run_ids[-1] if run_ids else 0,
            "used_bytes": offset,
            "duration_s": duration_s,
            "warnings": warnings,
        },
        "records": records,
        "flight": flight,
        "secondary": secondary,
        "events": events,
    }


def save_croi_flash_bundle(parsed: dict[str, Any], source: Path, out_root: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(parsed["summary"], indent=2), encoding="utf-8")
    (out / "source.txt").write_text(str(source), encoding="utf-8")
    _write_csv(out / "records.csv", parsed["records"])
    _write_csv(out / "flight.csv", parsed["flight"])
    _write_csv(out / "secondary.csv", parsed["secondary"])
    _write_csv(out / "events.csv", parsed["events"])
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in keys})
