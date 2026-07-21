from __future__ import annotations

import csv
import hashlib
import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .flight_manifest import FlightManifest
from .paths import OGMA_ROOT


@dataclass(frozen=True)
class ReplaySample:
    time_ms: int
    acceleration_m_s2: float
    velocity_m_s: float
    altitude_m: float
    barometric_altitude_m: float | None = None
    imu_valid: bool = True
    baro_valid: bool = True


@dataclass(frozen=True)
class ReplayPoint:
    time_ms: int
    state: int
    transitioned: bool
    main_backup: bool
    airbrake_active: bool
    airbrake_angle_deg: int
    candidate_mask: int
    confirmed_vote_mask: int
    gate_mask: int
    rejection_mask: int
    transition_reason: int
    detector_mode: int
    required_votes: int


@dataclass(frozen=True)
class ReplayResult:
    points: tuple[ReplayPoint, ...]

    @property
    def transitions(self) -> tuple[ReplayPoint, ...]:
        return tuple(point for point in self.points if point.transitioned)

    @property
    def main_backup_triggered(self) -> bool:
        return any(point.main_backup for point in self.points)


@dataclass(frozen=True)
class ReplaySession:
    source: str
    samples: tuple[ReplaySample, ...]
    result: ReplayResult


def load_replay_csv(path: Path) -> list[ReplaySample]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("replay CSV has no samples")

    samples = []
    for index, row in enumerate(rows, start=2):
        try:
            time_ms = _number(row, ("time_ms", "timestamp_ms", "time"))
            if "time_s" in row and row.get("time_s") not in (None, ""):
                time_ms = float(row["time_s"]) * 1000.0
            samples.append(
                ReplaySample(
                    int(time_ms),
                    _number(row, ("acceleration_m_s2", "prediction_acceleration_m_s2", "accel_m_s2")),
                    _number(row, ("velocity_m_s", "prediction_velocity_m_s", "vspeed_m_s")),
                    _number(row, ("altitude_m", "prediction_altitude_m", "kalman_altitude_m")),
                    _optional_number(row, ("barometric_altitude_m", "baro_altitude_m")),
                    _optional_bool(row, "imu_valid", True),
                    _optional_bool(row, "baro_valid", True),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid replay CSV row {index}: {exc}") from exc
    if any(current.time_ms <= previous.time_ms for previous, current in zip(samples, samples[1:])):
        raise ValueError("replay timestamps must increase strictly")
    return samples


def run_firmware_replay(manifest: FlightManifest, samples: list[ReplaySample]) -> ReplayResult:
    manifest.validate()
    if not samples:
        raise ValueError("replay requires at least one sample")
    executable = _native_replay_executable()
    mission = manifest.mission
    recovery = manifest.recovery
    detection = manifest.detection
    arguments = (
        str(executable),
        str(mission.liftoff_accel_m_s2),
        str(mission.main_deploy_altitude_m),
        str(mission.drogue_delay_ms),
        str(int(recovery.main_backup_enabled)),
        str(recovery.after_apogee_ms),
        str(recovery.descent_speed_m_s),
        str(recovery.min_altitude_m),
        str(recovery.max_altitude_m),
        str(recovery.required_samples),
        str(int(mission.airbrake_enabled)),
        str(mission.airbrake_channel),
        str(mission.airbrake_retracted_angle_deg),
        str(mission.airbrake_max_angle_deg),
        str(mission.airbrake_start_delay_ms),
        str(mission.airbrake_stow_delay_ms),
        str(detection.liftoff_confirm_ms),
        str(detection.liftoff_baro_velocity_m_s),
        str(detection.burnout_min_powered_ms),
        str(detection.burnout_accel_threshold_m_s2),
        str(detection.burnout_confirm_ms),
        str(detection.burnout_timeout_ms),
        str(detection.apogee_min_coast_ms),
        str(detection.apogee_min_altitude_m),
        str(detection.apogee_velocity_threshold_m_s),
        str(detection.apogee_confirm_ms),
        str(detection.apogee_single_sensor_confirm_ms),
        str(detection.apogee_baro_descent_m),
        str(detection.apogee_high_speed_lockout_m_s),
        str(detection.apogee_timeout_ms),
        str(detection.sensor_fault_timeout_ms),
    )
    payload = "".join(
        (
            f"{sample.time_ms},{sample.acceleration_m_s2},{sample.velocity_m_s},"
            f"{sample.altitude_m},"
            f"{sample.altitude_m if sample.barometric_altitude_m is None else sample.barometric_altitude_m},"
            f"{int(sample.imu_valid)},{int(sample.baro_valid)}\n"
        )
        for sample in samples
    )
    completed = subprocess.run(
        arguments,
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"firmware replay failed: {detail}")
    rows = csv.DictReader(io.StringIO(completed.stdout))
    points = tuple(
        ReplayPoint(
            time_ms=int(row["time_ms"]),
            state=int(row["state"]),
            transitioned=bool(int(row["transition"])),
            main_backup=bool(int(row["main_backup"])),
            airbrake_active=bool(int(row["airbrake_active"])),
            airbrake_angle_deg=int(row["airbrake_angle_deg"]),
            candidate_mask=int(row["candidate_mask"]),
            confirmed_vote_mask=int(row["confirmed_vote_mask"]),
            gate_mask=int(row["gate_mask"]),
            rejection_mask=int(row["rejection_mask"]),
            transition_reason=int(row["transition_reason"]),
            detector_mode=int(row["detector_mode"]),
            required_votes=int(row["required_votes"]),
        )
        for row in rows
    )
    if len(points) != len(samples):
        raise RuntimeError("firmware replay returned the wrong sample count")
    return ReplayResult(points)


def synthetic_nominal_profile() -> list[ReplaySample]:
    samples = []
    for time_ms in range(0, 31000, 100):
        if time_ms < 1000:
            acceleration, velocity, altitude = 0.0, 0.0, 0.0
        elif time_ms < 4000:
            elapsed = (time_ms - 1000) / 1000.0
            acceleration, velocity, altitude = 35.0, elapsed * 35.0, elapsed * elapsed * 17.5
        elif time_ms < 12000:
            elapsed = (time_ms - 4000) / 1000.0
            acceleration = -14.0
            velocity = 105.0 - elapsed * 14.0
            altitude = 157.5 + 105.0 * elapsed - 7.0 * elapsed * elapsed
        elif time_ms < 22000:
            elapsed = (time_ms - 12000) / 1000.0
            acceleration, velocity, altitude = 0.0, -20.0, max(150.0, 610.0 - elapsed * 45.0)
        else:
            acceleration, velocity, altitude = 0.0, 0.5, 0.0
        samples.append(ReplaySample(time_ms, acceleration, velocity, altitude))
    return samples


def _native_replay_executable() -> Path:
    source = Path(__file__).with_name("native") / "flight_replay.cpp"
    phase_header = OGMA_ROOT / "croi" / "firmware" / "src" / "tools" / "flight_phase_logic.h"
    diagnostics_header = OGMA_ROOT / "croi" / "firmware" / "src" / "tools" / "flight_phase_diagnostics.h"
    airbrake_header = OGMA_ROOT / "croi" / "firmware" / "src" / "tools" / "airbrake_logic.h"
    data_header = OGMA_ROOT / "croi" / "firmware" / "lib" / "comheadan" / "include" / "data.h"
    digest = hashlib.sha256(
        b"".join(
            path.read_bytes()
            for path in (source, phase_header, diagnostics_header, airbrake_header, data_header)
        )
    ).hexdigest()[:16]
    executable = Path(tempfile.gettempdir()) / f"ogma-flight-replay-{digest}"
    if executable.exists():
        return executable
    command = (
        "c++",
        "-std=c++17",
        "-O2",
        f"-I{OGMA_ROOT / 'croi' / 'firmware' / 'src'}",
        f"-I{OGMA_ROOT / 'croi' / 'firmware' / 'lib' / 'comheadan' / 'include'}",
        str(source),
        "-o",
        str(executable),
    )
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"native replay build failed: {detail}")
    return executable


def _number(row: dict[str, str | None], names: tuple[str, ...]) -> float:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return float(value)
    raise ValueError(f"missing one of: {', '.join(names)}")


def _optional_number(row: dict[str, str | None], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return float(value)
    return None


def _optional_bool(row: dict[str, str | None], name: str, default: bool) -> bool:
    value = row.get(name)
    if value in (None, ""):
        return default
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "valid"):
        return True
    if normalized in ("0", "false", "no", "invalid"):
        return False
    raise ValueError(f"{name} must be true/false or 1/0")
