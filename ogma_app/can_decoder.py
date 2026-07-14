from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .can_layouts import CanFrameLayout

HEARTBEAT_BASE_ID = 0x420
HEARTBEAT_MASK = 0x7F0
NODE_NAMES = {
    0x01: "croi",
    0x02: "pleasc",
    0x03: "lamh",
    0x04: "teachtaire",
    0x05: "muon",
    0x06: "foinse",
}
HEARTBEAT_ERROR_FLAGS = {
    0x01: "BUS_OFF",
    0x02: "CAN_ERROR",
    0x04: "TX_DROP",
    0x08: "NODE_TIMEOUT",
}


def decode_can_frame(frames: dict[str, CanFrameLayout], can_id: int, data: bytes) -> dict[str, Any]:
    if _is_heartbeat_id(can_id):
        return _decode_heartbeat_frame(can_id, data)
    frame = _frame_for_id(frames, can_id)
    if frame is None:
        return {
            "can_id": can_id,
            "frame": "UNKNOWN",
            "data_hex": data.hex(),
            "fields": {},
            "warnings": [f"unknown CAN id 0x{can_id:03x}"],
        }
    fields: dict[str, Any] = {}
    warnings: list[str] = []
    for layout in frame.fields:
        byte_range = _byte_range(layout.bytes_)
        if byte_range is None:
            warnings.append(f"{layout.field_name}: bad byte range {layout.bytes_!r}")
            continue
        start, end = byte_range
        if end >= len(data):
            warnings.append(f"{layout.field_name}: frame too short for bytes {layout.bytes_}")
            continue
        raw = _read_typed_value(data[start : end + 1], layout.type_name)
        value = _apply_scale(raw, layout.scale)
        fields[layout.field_name] = {
            "raw": raw,
            "value": value,
            "unit": _scale_unit(layout.scale),
            "type": layout.type_name,
        }
    return {
        "can_id": can_id,
        "frame": frame.name,
        "data_hex": data.hex(),
        "fields": fields,
        "warnings": warnings,
    }


def decode_can_log_text(text: str, frames: dict[str, CanFrameLayout]) -> dict[str, Any]:
    decoded: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            can_id, data = parse_can_log_line(stripped)
        except ValueError as exc:
            warnings.append(f"line {line_no}: {exc}")
            continue
        packet = decode_can_frame(frames, can_id, data)
        packet["line"] = line_no
        decoded.append(packet)
    stack = summarize_stack_health(decoded)
    return {
        "summary": {
            "frames": len(decoded),
            "warnings": warnings,
            "unknown_frames": sum(1 for packet in decoded if packet["frame"] == "UNKNOWN"),
            "heartbeat_nodes": stack["node_count"],
            "heartbeat_nodes_with_errors": stack["nodes_with_errors"],
        },
        "stack": stack,
        "frames": decoded,
    }


def decode_can_log_file(path: Path, frames: dict[str, CanFrameLayout]) -> dict[str, Any]:
    return decode_can_log_text(path.read_text(encoding="utf-8", errors="replace"), frames)


def save_can_decode_bundle(decoded: dict[str, Any], source: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": str(source), **decoded}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def parse_can_log_line(line: str) -> tuple[int, bytes]:
    candump = re.search(r"\b([0-9A-Fa-f]{1,8})#([0-9A-Fa-f]*)\b", line)
    if candump:
        return _parse_id(candump.group(1)), _parse_hex_bytes(candump.group(2))

    parts = [part for part in re.split(r"[\s,]+", line) if part]
    if len(parts) < 2:
        raise ValueError("expected CAN id plus payload bytes")
    can_id = _parse_id(parts[0])
    payload_parts = parts[1:]
    if len(payload_parts) > 1 and _looks_like_dlc(payload_parts[0], payload_parts[1:]):
        payload_parts = payload_parts[1:]
    if len(payload_parts) == 1:
        return can_id, _parse_hex_bytes(payload_parts[0])
    return can_id, bytes(_parse_byte(part) for part in payload_parts)


def summarize_stack_health(decoded_frames: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    for packet in decoded_frames:
        if packet.get("frame") != "HEARTBEAT":
            continue
        fields = packet.get("fields", {})
        node_id = _field_value(fields, "node_id")
        if node_id is None:
            node_id = packet.get("can_id", HEARTBEAT_BASE_ID) & 0x0F
        node_name = NODE_NAMES.get(int(node_id), f"node_{int(node_id)}")
        err = int(_field_value(fields, "err") or 0)
        nodes[node_name] = {
            "node_id": int(node_id),
            "line": packet.get("line"),
            "received_s": packet.get("received_s"),
            "can_id": packet.get("can_id"),
            "state": int(_field_value(fields, "state") or 0),
            "err": err,
            "err_flags": _heartbeat_error_names(err),
            "uptime_s": int(_field_value(fields, "uptime_s") or 0),
        }
    return {
        "node_count": len(nodes),
        "nodes_with_errors": sum(1 for node in nodes.values() if node["err"] != 0),
        "nodes": dict(sorted(nodes.items())),
    }


def _decode_heartbeat_frame(can_id: int, data: bytes) -> dict[str, Any]:
    warnings: list[str] = []
    if len(data) < 4:
        warnings.append("heartbeat frame too short for bytes 0-3")
    node_id = data[0] if len(data) > 0 else can_id & 0x0F
    state = data[1] if len(data) > 1 else 0
    err = data[2] if len(data) > 2 else 0
    uptime_s = data[3] if len(data) > 3 else 0
    return {
        "can_id": can_id,
        "frame": "HEARTBEAT",
        "data_hex": data.hex(),
        "fields": {
            "node_id": {"raw": node_id, "value": node_id, "unit": "", "type": "uint8"},
            "node_name": {"raw": node_id, "value": NODE_NAMES.get(node_id, f"node_{node_id}"), "unit": "", "type": "enum"},
            "state": {"raw": state, "value": state, "unit": "", "type": "uint8"},
            "err": {"raw": err, "value": err, "unit": "", "type": "bitmask"},
            "err_flags": {"raw": err, "value": _heartbeat_error_names(err), "unit": "", "type": "bitmask"},
            "uptime_s": {"raw": uptime_s, "value": uptime_s, "unit": "s", "type": "uint8"},
        },
        "warnings": warnings,
    }


def _is_heartbeat_id(can_id: int) -> bool:
    return (can_id & HEARTBEAT_MASK) == HEARTBEAT_BASE_ID


def _heartbeat_error_names(err: int) -> list[str]:
    names = [name for bit, name in HEARTBEAT_ERROR_FLAGS.items() if err & bit]
    known_mask = 0
    for bit in HEARTBEAT_ERROR_FLAGS:
        known_mask |= bit
    unknown = err & ~known_mask
    if unknown:
        names.append(f"UNKNOWN_0x{unknown:02x}")
    return names


def _field_value(fields: dict[str, Any], name: str) -> Any:
    field = fields.get(name)
    if isinstance(field, dict):
        return field.get("value")
    return None


def _frame_for_id(frames: dict[str, CanFrameLayout], can_id: int) -> CanFrameLayout | None:
    for frame in frames.values():
        if frame.can_id == can_id:
            return frame
    return None


def _byte_range(text: str) -> tuple[int, int] | None:
    numbers = re.findall(r"\d+", text)
    if not numbers:
        return None
    start = int(numbers[0])
    end = int(numbers[1]) if len(numbers) > 1 else start
    if end < start:
        return None
    return start, end


def _read_typed_value(blob: bytes, type_name: str) -> int:
    type_name = type_name.lower().strip()
    signed = type_name.startswith("int")
    width_bits = int(re.search(r"\d+", type_name).group(0)) if re.search(r"\d+", type_name) else len(blob) * 8
    width_bytes = max(1, (width_bits + 7) // 8)
    value = int.from_bytes(blob[:width_bytes], "little", signed=False)
    if signed:
        sign_bit = 1 << (width_bits - 1)
        if value & sign_bit:
            value -= 1 << width_bits
    return value


def _apply_scale(raw: int, scale: str) -> int | float:
    factor = _scale_factor(scale)
    if factor is None:
        return raw
    value = raw * factor
    return int(value) if value.is_integer() else value


def _scale_factor(scale: str) -> float | None:
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)\s*[^/]*\s*/\s*LSB\b", scale)
    if not match:
        return None
    return float(match.group(1))


def _scale_unit(scale: str) -> str:
    match = re.match(r"\s*[+-]?\d+(?:\.\d+)?\s*([^/]*)\s*/\s*LSB\b", scale)
    return match.group(1).strip() if match else ""


def _parse_id(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") or re.search(r"[a-fA-F]", text) else int(text, 16)


def _parse_hex_bytes(text: str) -> bytes:
    clean = text.strip().replace("0x", "").replace("0X", "")
    if len(clean) % 2:
        raise ValueError("payload hex must have even length")
    return bytes.fromhex(clean)


def _parse_byte(text: str) -> int:
    value = int(text, 16)
    if not 0 <= value <= 0xFF:
        raise ValueError(f"payload byte out of range: {text}")
    return value


def _looks_like_dlc(text: str, payload_parts: list[str]) -> bool:
    try:
        dlc = int(text, 10)
    except ValueError:
        return False
    return dlc == len(payload_parts) and 0 <= dlc <= 8
