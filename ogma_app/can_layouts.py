from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CanFieldLayout:
    frame: str
    bytes_: str
    field_name: str
    type_name: str
    scale: str
    range_: str
    notes: str


@dataclass
class CanFrameLayout:
    name: str
    can_id: int | None = None
    fields: list[CanFieldLayout] = field(default_factory=list)


def load_payload_layouts(path: Path) -> dict[str, CanFrameLayout]:
    frames: dict[str, CanFrameLayout] = {}
    current_frame = ""
    header_seen = False
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            row = [cell.strip() for cell in row]
            if not header_seen:
                header_seen = len(row) >= 3 and row[0] == "Frame" and row[1].startswith("Byte")
                continue
            if not any(row):
                continue
            frame = row[0] if len(row) > 0 else ""
            byte_range = row[1] if len(row) > 1 else ""
            field_name = row[2] if len(row) > 2 else ""
            if frame:
                current_frame = frame
            if not current_frame or not field_name:
                continue
            layout = frames.setdefault(current_frame, CanFrameLayout(current_frame))
            layout.fields.append(
                CanFieldLayout(
                    frame=current_frame,
                    bytes_=byte_range,
                    field_name=field_name,
                    type_name=row[3] if len(row) > 3 else "",
                    scale=row[4] if len(row) > 4 else "",
                    range_=row[5] if len(row) > 5 else "",
                    notes=row[6] if len(row) > 6 else "",
                )
            )
    return frames

def load_can_ids(path: Path) -> dict[str, int]:
    direct: dict[str, int] = {}
    aliases: dict[str, str] = {}
    pattern = re.compile(r"^\s*#define\s+CAN_ID_([A-Z0-9_]+)\s+([A-Za-z0-9_]+|0x[0-9A-Fa-f]+)")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        name, raw_value = match.groups()
        if raw_value.startswith("0x"):
            direct[name] = int(raw_value, 16)
        elif raw_value.isdigit():
            direct[name] = int(raw_value, 10)
        elif raw_value.startswith("CAN_ID_"):
            aliases[name] = raw_value.removeprefix("CAN_ID_")
    changed = True
    while changed:
        changed = False
        for name, target in list(aliases.items()):
            if name not in direct and target in direct:
                direct[name] = direct[target]
                changed = True
    return direct


def attach_can_ids(
    frames: dict[str, CanFrameLayout],
    can_ids: dict[str, int],
) -> dict[str, CanFrameLayout]:
    for name, frame in frames.items():
        normalized = name.upper().replace(" ", "_")
        frame.can_id = can_ids.get(normalized)
    return frames
