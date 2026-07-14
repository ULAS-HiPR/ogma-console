from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lamh_config import LamhSafetyConfig
from .mission_config import LoggingPolicy, MissionConfig, RecoveryFallbackConfig, load_mission_json


FLIGHT_MANIFEST_FORMAT = "ogma-flight-manifest"
FLIGHT_MANIFEST_SCHEMA_VERSION = 1
KNOWN_FLIGHT_BOARDS = frozenset(("croi", "teachtaire", "lamh", "foinse", "pleasc"))


@dataclass(frozen=True)
class RadioPolicy:
    core_period_ms: int = 200
    gps_period_ms: int = 1000
    slow_period_ms: int = 1000
    health_period_ms: int = 5000

    def validate(self) -> None:
        for name, value, minimum, maximum in (
            ("core", self.core_period_ms, 100, 5000),
            ("GPS", self.gps_period_ms, 200, 10000),
            ("slow", self.slow_period_ms, 200, 10000),
            ("health", self.health_period_ms, 500, 30000),
        ):
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} radio period must be {minimum} to {maximum} ms")


@dataclass(frozen=True)
class PreflightPolicy:
    required_boards: tuple[str, ...] = ("croi", "teachtaire", "lamh", "foinse")
    require_gps_fix: bool = True
    max_croi_status_age_s: float = 5.0
    max_heartbeat_age_s: float = 8.0
    max_gps_age_s: float = 3.0

    def validate(self) -> None:
        if not self.required_boards:
            raise ValueError("at least one flight board is required")
        unknown = set(self.required_boards) - KNOWN_FLIGHT_BOARDS
        if unknown:
            raise ValueError(f"unknown required boards: {', '.join(sorted(unknown))}")
        if "croi" not in self.required_boards:
            raise ValueError("Croí must be a required flight board")
        if len(set(self.required_boards)) != len(self.required_boards):
            raise ValueError("required flight boards must be unique")
        if not 1.0 <= self.max_croi_status_age_s <= 60.0:
            raise ValueError("Croí status age must be 1 to 60 seconds")
        if not 2.0 <= self.max_heartbeat_age_s <= 60.0:
            raise ValueError("heartbeat age must be 2 to 60 seconds")
        if not 1.0 <= self.max_gps_age_s <= 60.0:
            raise ValueError("GPS age must be 1 to 60 seconds")


@dataclass(frozen=True)
class FlightManifest:
    mission: MissionConfig
    lamh_safety: LamhSafetyConfig
    recovery: RecoveryFallbackConfig = RecoveryFallbackConfig()
    logging: LoggingPolicy = LoggingPolicy()
    radio: RadioPolicy = RadioPolicy()
    preflight: PreflightPolicy = PreflightPolicy()
    schema_version: int = FLIGHT_MANIFEST_SCHEMA_VERSION

    @classmethod
    def defaults(cls, lamh_safety: LamhSafetyConfig | None = None) -> "FlightManifest":
        return cls(MissionConfig.defaults(), lamh_safety or LamhSafetyConfig.defaults())

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "FlightManifest":
        mission = MissionConfig.from_dict(_required_dict(values, "mission"))
        lamh_values = _required_dict(values, "lamh_safety")
        manifest = cls(
            mission=mission,
            lamh_safety=LamhSafetyConfig.from_values(lamh_values.get("angles_deg", ())),
            recovery=RecoveryFallbackConfig(**_required_dict(values, "recovery")),
            logging=LoggingPolicy(**_required_dict(values, "logging")),
            radio=RadioPolicy(**_required_dict(values, "radio")),
            preflight=PreflightPolicy(
                **{
                    **_required_dict(values, "preflight"),
                    "required_boards": tuple(_required_dict(values, "preflight").get("required_boards", ())),
                }
            ),
            schema_version=int(values.get("schema_version", 0)),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != FLIGHT_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported flight manifest schema")
        self.mission.validate()
        LamhSafetyConfig.from_values(self.lamh_safety.angles_deg)
        self.recovery.validate(self.mission)
        self.logging.validate()
        self.radio.validate()
        self.preflight.validate()
        if self.mission.airbrake_enabled and "lamh" not in self.effective_required_boards():
            raise ValueError("airbrake mission requires Lámh")

    def effective_required_boards(self) -> tuple[str, ...]:
        boards = list(self.preflight.required_boards)
        pyro_enabled = (
            self.mission.pyro_drogue_channel is not None
            or self.mission.pyro_main_channel is not None
        )
        if pyro_enabled and "pleasc" not in boards:
            boards.append("pleasc")
        return tuple(boards)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission": self.mission.canonical_dict(),
            "lamh_safety": {"angles_deg": list(self.lamh_safety.angles_deg)},
            "recovery": asdict(self.recovery),
            "logging": asdict(self.logging),
            "radio": asdict(self.radio),
            "preflight": {
                **asdict(self.preflight),
                "required_boards": list(self.preflight.required_boards),
            },
        }

    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_dict())).hexdigest()


def save_flight_manifest(directory: Path, manifest: FlightManifest) -> Path:
    manifest.validate()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    digest = manifest.sha256()
    payload = {
        "format": FLIGHT_MANIFEST_FORMAT,
        "schema_version": FLIGHT_MANIFEST_SCHEMA_VERSION,
        "manifest_sha256": digest,
        "saved_at_utc": timestamp.isoformat(),
        "manifest": manifest.canonical_dict(),
    }
    path = directory / f"flight_{timestamp.strftime('%Y%m%dT%H%M%SZ')}_{digest[:12]}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def load_flight_manifest(path: Path, lamh_safety: LamhSafetyConfig | None = None) -> FlightManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != FLIGHT_MANIFEST_FORMAT:
        return FlightManifest(
            mission=load_mission_json(path),
            lamh_safety=lamh_safety or LamhSafetyConfig.defaults(),
        )
    if int(payload.get("schema_version", 0)) != FLIGHT_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported flight manifest file schema")
    values = payload.get("manifest")
    if not isinstance(values, dict):
        raise ValueError("flight manifest file has no manifest object")
    manifest = FlightManifest.from_dict(values)
    if str(payload.get("manifest_sha256", "")) != manifest.sha256():
        raise ValueError("flight manifest SHA-256 mismatch")
    return manifest


def _required_dict(values: dict[str, Any], key: str) -> dict[str, Any]:
    value = values.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"flight manifest has no {key} object")
    return value


def _canonical_json(values: dict[str, Any]) -> bytes:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
