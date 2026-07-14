from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class FaultObservation:
    key: str
    severity: str
    source: str
    detail: str
    active: bool


@dataclass
class FaultEntry:
    key: str
    severity: str
    source: str
    detail: str
    active: bool
    first_seen_utc: str
    last_seen_utc: str
    occurrences: int


class FaultLedger:
    def __init__(self) -> None:
        self.entries: dict[str, FaultEntry] = {}

    def observe(self, observations: Iterable[FaultObservation], observed_at: float) -> None:
        timestamp = datetime.fromtimestamp(observed_at, timezone.utc).isoformat()
        for observation in observations:
            entry = self.entries.get(observation.key)
            if entry is None:
                if not observation.active:
                    continue
                self.entries[observation.key] = FaultEntry(
                    key=observation.key,
                    severity=observation.severity,
                    source=observation.source,
                    detail=observation.detail,
                    active=True,
                    first_seen_utc=timestamp,
                    last_seen_utc=timestamp,
                    occurrences=1,
                )
                continue
            if observation.active and not entry.active:
                entry.occurrences += 1
            entry.active = observation.active
            entry.severity = observation.severity
            entry.source = observation.source
            entry.detail = observation.detail
            entry.last_seen_utc = timestamp

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "ogma-fault-ledger",
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "entries": [asdict(entry) for entry in self.sorted_entries()],
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    def sorted_entries(self) -> list[FaultEntry]:
        rank = {"critical": 0, "warning": 1, "info": 2}
        return sorted(
            self.entries.values(),
            key=lambda entry: (not entry.active, rank.get(entry.severity, 3), entry.first_seen_utc),
        )


def board_fault_observations(board_id: str, status: dict[str, Any]) -> list[FaultObservation]:
    observations = []

    def add(name: str, active: bool, severity: str, detail: str) -> None:
        observations.append(FaultObservation(f"{board_id}.{name}", severity, board_id, detail, active))

    if board_id == "croi":
        add("init", _int(status, "init_ok", 1) == 0, "critical", "board initialization failed")
        add("logger", _int(status, "logger_fault_latched") != 0, "critical", f"logger fault {_int(status, 'logger_fault')}")
        add("can_bus_off", _int(status, "can_bus_off") != 0, "critical", "CAN bus-off")
        add("can_error", _int(status, "can_error") != 0, "warning", f"CAN error 0x{_int(status, 'can_error'):X}")
        add("sensor_stale", _int(status, "sensor_sample_valid", 1) == 0, "critical", "IMU/barometer sample invalid")
        add("watchdog", _int(status, "watchdog_init_ok", 1) == 0, "critical", "watchdog not initialized")
        add("pyro_tx_drop", _int(status, "pyro_critical_tx_drops") != 0, "critical", f"critical TX drops {_int(status, 'pyro_critical_tx_drops')}")
        add("pyro_fault", _int(status, "pyro_last_fault") != 0, "critical", f"Pleasc fault {_int(status, 'pyro_last_fault')}")
        add("main_fallback", _int(status, "main_fallback_triggered") != 0, "warning", "main recovery fallback triggered")
    elif board_id == "teachtaire":
        add("lora", _int(status, "lora_init_ok", 1) == 0, "critical", "SX1272 initialization failed")
        add("can_bus_off", _int(status, "can_bus_off") != 0, "critical", "CAN bus-off")
        add("can_error", _int(status, "can_error") != 0, "warning", f"CAN error 0x{_int(status, 'can_error'):X}")
        add("watchdog", _int(status, "watchdog_init_ok", 1) == 0, "critical", "watchdog not initialized")
        add("radio_timeout", _int(status, "lora_tx_timeout_count") != 0, "warning", f"LoRa TX timeouts {_int(status, 'lora_tx_timeout_count')}")
    elif board_id == "lamh":
        add("pca9685", _int(status, "pca9685_found", 1) == 0, "critical", "PCA9685 not found")
        add("can_bus_off", _int(status, "can_bus_off") != 0, "critical", "CAN bus-off")
        add("can_error", _int(status, "can_error") != 0, "warning", f"CAN error 0x{_int(status, 'can_error'):X}")
    elif board_id in ("foinse", "pleasc"):
        add("can_bus_off", _int(status, "can_bus_off") != 0, "critical", "CAN bus-off")
        add("can_error", _int(status, "can_error") != 0, "warning", f"CAN error 0x{_int(status, 'can_error'):X}")
        if board_id == "pleasc":
            add("fault", _int(status, "fault") != 0, "critical", f"Pleasc fault 0x{_int(status, 'fault'):X}")
    return observations


def telemetry_fault_observations(parsed: dict[str, Any]) -> list[FaultObservation]:
    summary = parsed.get("summary", {})
    observations = [
        FaultObservation(
            "telemetry.node_errors",
            "critical",
            "telemetry",
            f"nodes with errors {int(summary.get('heartbeat_nodes_with_errors', 0))}",
            int(summary.get("heartbeat_nodes_with_errors", 0)) > 0,
        ),
        FaultObservation(
            "telemetry.unknown_frames",
            "warning",
            "telemetry",
            f"unknown CAN frames {int(summary.get('unknown_can_frames', 0))}",
            int(summary.get("unknown_can_frames", 0)) > 0,
        ),
    ]
    nodes = parsed.get("can", {}).get("stack", {}).get("nodes", {})
    for board_id, node in nodes.items():
        error = _int(node, "err")
        observations.append(
            FaultObservation(
                f"telemetry.{board_id}.heartbeat_error",
                "critical",
                str(board_id),
                f"heartbeat error 0x{error:02X}",
                error != 0,
            )
        )
    return observations


def _int(values: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(values.get(key, default))
    except (TypeError, ValueError):
        return default
