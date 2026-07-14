from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from .paths import ST_INFO


@dataclass(frozen=True)
class ProbeResult:
    connected: bool
    programmers: int
    fields: dict[str, str]
    returncode: int
    raw: str

    def lines(self) -> list[str]:
        state = "connected" if self.connected else "not connected"
        lines = [f"state: {state}", f"stlink_programmers: {self.programmers}"]
        for key in sorted(self.fields):
            lines.append(f"{key}: {self.fields[key]}")
        if self.raw.strip() and not self.fields:
            lines.append(self.raw.strip())
        return lines


def parse_st_info_probe(text: str, returncode: int = 0) -> ProbeResult:
    programmers = 0
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.search(r"Found\s+(\d+)\s+stlink", stripped, re.IGNORECASE)
        if match:
            programmers = int(match.group(1))
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            fields[key.strip().lower().replace("-", "_")] = value.strip()

    chipid = fields.get("chipid", "").lower()
    target_seen = bool(chipid and chipid not in {"0x0000", "0x0"})
    connected = returncode == 0 and programmers > 0 and target_seen
    return ProbeResult(connected, programmers, fields, returncode, text)


def probe_stlink(timeout: float = 8.0) -> ProbeResult:
    if not ST_INFO.exists():
        raise RuntimeError(f"st-info not found: {ST_INFO}")
    proc = subprocess.run(
        [str(ST_INFO), "--probe"],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return parse_st_info_probe(proc.stdout + proc.stderr, proc.returncode)
