from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .flight_manifest import FlightManifest
from .mission_config import CROI_MISSION_CONFIG_MAGIC, CROI_MISSION_CONFIG_SCHEMA_VERSION
from .teachtaire_config import (
    TEACHTAIRE_RADIO_CONFIG_MAGIC,
    TEACHTAIRE_RADIO_CONFIG_SCHEMA_VERSION,
    radio_config_crc32,
)


@dataclass(frozen=True)
class StatusEvidence:
    status: dict[str, Any]
    age_s: float


@dataclass(frozen=True)
class PreflightCheck:
    category: str
    name: str
    state: str
    actual: str
    required: str
    source: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def go(self) -> bool:
        return bool(self.checks) and all(check.state != "fail" for check in self.checks)

    @property
    def failures(self) -> int:
        return sum(check.state == "fail" for check in self.checks)

    @property
    def warnings(self) -> int:
        return sum(check.state == "warn" for check in self.checks)


def evaluate_preflight(
    manifest: FlightManifest,
    statuses: dict[str, StatusEvidence],
    telemetry: dict[str, Any] | None,
    telemetry_age_s: float | None,
) -> PreflightReport:
    checks: list[PreflightCheck] = []
    try:
        manifest.validate()
    except ValueError as exc:
        checks.append(_check("manifest", "schema", "fail", str(exc), "valid manifest", "local"))
        return PreflightReport(tuple(checks))

    checks.append(
        _check("manifest", "integrity", "pass", manifest.sha256()[:16], "valid SHA-256", "local")
    )
    checks.extend(_mission_checks(manifest))
    checks.extend(_croi_checks(manifest, statuses.get("croi")))
    checks.extend(_lamh_checks(manifest, statuses.get("lamh")))
    checks.extend(_teachtaire_checks(manifest, statuses.get("teachtaire")))
    checks.extend(_telemetry_checks(manifest, telemetry, telemetry_age_s))
    return PreflightReport(tuple(checks))


def _mission_checks(manifest: FlightManifest) -> list[PreflightCheck]:
    mission = manifest.mission
    safe_angle = manifest.lamh_safety.angles_deg[mission.airbrake_channel]
    checks = [
        _check(
            "mission",
            "Croí mission CRC",
            "pass",
            f"0x{mission.crc32(manifest.recovery, manifest.logging, manifest.detection):08X}",
            "generated",
            "manifest",
        )
    ]
    if mission.airbrake_enabled:
        matches = safe_angle == mission.airbrake_retracted_angle_deg
        checks.append(
            _check(
                "mission",
                "airbrake failsafe",
                "pass" if matches else "fail",
                f"PWM{mission.airbrake_channel + 1}={safe_angle} deg",
                f"{mission.airbrake_retracted_angle_deg} deg",
                "manifest",
            )
        )
    else:
        checks.append(_check("mission", "airbrake", "pass", "disabled", "disabled or configured", "manifest"))

    if manifest.recovery.main_backup_enabled:
        checks.append(
            _check(
                "mission",
                "main recovery fallback",
                "pass",
                (
                    f"{manifest.recovery.after_apogee_ms} ms, "
                    f"{manifest.recovery.descent_speed_m_s:g} m/s, "
                    f"{manifest.recovery.required_samples} samples"
                ),
                "sealed in Croí config",
                "manifest",
            )
        )
    else:
        checks.append(_check("mission", "main recovery fallback", "warn", "disabled", "team decision", "manifest"))
    return checks


def _croi_checks(manifest: FlightManifest, evidence: StatusEvidence | None) -> list[PreflightCheck]:
    if evidence is None:
        return [_check("Croí", "status", "fail", "missing", "fresh SWD status", "SWD")]
    status = evidence.status
    checks = [
        _check(
            "Croí",
            "status age",
            "pass" if evidence.age_s <= manifest.preflight.max_croi_status_age_s else "fail",
            f"{evidence.age_s:.1f} s",
            f"<= {manifest.preflight.max_croi_status_age_s:g} s",
            "SWD",
        )
    ]
    for field, expected, label in (
        ("init_ok", 1, "initialization"),
        ("imu_init_ok", 1, "IMU"),
        ("baro_init_ok", 1, "barometer"),
        ("can_init_ok", 1, "CAN"),
        ("flash_init_ok", 1, "flash logger"),
        ("logger_fault_latched", 0, "logger fault"),
        ("logger_logging_stopped", 0, "logger running"),
        ("can_bus_off", 0, "CAN bus-off"),
        ("can_error", 0, "CAN error"),
        ("sensor_sample_valid", 1, "fresh sensor sample"),
        ("watchdog_init_ok", 1, "watchdog"),
    ):
        actual = int(status.get(field, -1))
        checks.append(
            _check("Croí", label, "pass" if actual == expected else "fail", str(actual), str(expected), "SWD")
        )
    free_bytes = int(status.get("logger_free_bytes", -1))
    required_bytes = int(status.get("logger_required_bytes", -1))
    checks.append(
        _check(
            "Croí",
            "flight log capacity",
            "pass" if required_bytes > 0 and free_bytes >= required_bytes else "fail",
            f"{free_bytes} B free",
            f">= {required_bytes} B",
            "SWD",
        )
    )
    if "phase_detector_mode" in status:
        detector_mode = int(status.get("phase_detector_mode", -1))
        sensor_health = int(status.get("phase_sensor_health_mask", 0))
        checks.append(
            _check(
                "Croí",
                "phase detector mode",
                "pass" if detector_mode == 0 else "fail",
                str(detector_mode),
                "0 (IMU + barometer)",
                "SWD",
            )
        )
        checks.append(
            _check(
                "Croí",
                "phase sensor health",
                "pass" if sensor_health & 0x03 == 0x03 else "fail",
                f"0x{sensor_health:02X}",
                "IMU + barometer healthy",
                "SWD",
            )
        )
    for field, expected, label, display in (
        ("mission_config_magic", CROI_MISSION_CONFIG_MAGIC, "mission magic", "hex"),
        ("mission_config_schema_version", CROI_MISSION_CONFIG_SCHEMA_VERSION, "mission schema", "int"),
        (
            "mission_config_crc32",
            manifest.mission.crc32(
                manifest.recovery,
                manifest.logging,
                manifest.detection,
            ),
            "mission CRC",
            "hex",
        ),
    ):
        actual = int(status.get(field, 0))
        render = (lambda value: f"0x{value:08X}") if display == "hex" else str
        checks.append(
            _check(
                "Croí",
                label,
                "pass" if actual == expected else "fail",
                render(actual),
                render(expected),
                "SWD readback",
            )
        )

    required_mask = _required_pyro_mask(manifest)
    if required_mask:
        continuity = int(status.get("pyro_continuity_mask", 0))
        checks.append(
            _check(
                "recovery",
                "pyro continuity",
                "pass" if continuity & required_mask == required_mask else "fail",
                f"0x{continuity:02X}",
                f"mask 0x{required_mask:02X}",
                "Croí CAN mirror",
            )
        )
        fired = int(status.get("pyro_fired_mask", 0))
        checks.append(
            _check("recovery", "pyro fired mask", "pass" if fired == 0 else "fail", f"0x{fired:02X}", "0x00", "Croí CAN mirror")
        )
        armed = int(status.get("pyro_armed_mask", 0))
        checks.append(
            _check(
                "recovery",
                "preflight armed mask",
                "pass" if armed == 0 else "fail",
                f"0x{armed:02X}",
                "0x00 before liftoff",
                "Croí CAN mirror",
            )
        )
        status_count = int(status.get("pyro_status_count", 0))
        checks.append(
            _check(
                "recovery",
                "Pleasc status received",
                "pass" if status_count > 0 else "fail",
                str(status_count),
                "> 0",
                "Croí CAN mirror",
            )
        )
        fault = int(status.get("pyro_last_fault", 0))
        checks.append(
            _check(
                "recovery",
                "Pleasc fault",
                "pass" if fault == 0 else "fail",
                f"0x{fault:02X}",
                "0x00",
                "Croí CAN mirror",
            )
        )
        drops = int(status.get("pyro_critical_tx_drops", 0))
        checks.append(
            _check("recovery", "critical CAN drops", "pass" if drops == 0 else "fail", str(drops), "0", "Croí")
        )
    return checks


def _lamh_checks(manifest: FlightManifest, evidence: StatusEvidence | None) -> list[PreflightCheck]:
    if not manifest.mission.airbrake_enabled:
        return []
    if evidence is None:
        return [_check("Lámh", "config readback", "fail", "missing", "verified safe angles", "SWD")]
    status = evidence.status
    checks = [
        _check(
            "Lámh",
            "status age",
            "pass" if evidence.age_s <= manifest.preflight.max_croi_status_age_s else "fail",
            f"{evidence.age_s:.1f} s",
            f"<= {manifest.preflight.max_croi_status_age_s:g} s",
            "SWD",
        )
    ]
    for index, expected in enumerate(manifest.lamh_safety.angles_deg, start=1):
        actual = int(status.get(f"safe_angle_pwm{index}_deg", -1))
        checks.append(
            _check(
                "Lámh",
                f"PWM{index} safe angle",
                "pass" if actual == expected else "fail",
                f"{actual} deg",
                f"{expected} deg",
                f"SWD, {evidence.age_s:.1f} s old",
            )
        )
    pca = int(status.get("pca9685_found", 0))
    checks.append(_check("Lámh", "PCA9685", "pass" if pca == 1 else "fail", str(pca), "1", "SWD"))
    return checks


def _teachtaire_checks(
    manifest: FlightManifest,
    evidence: StatusEvidence | None,
) -> list[PreflightCheck]:
    if "teachtaire" not in manifest.effective_required_boards():
        return []
    if evidence is None:
        return [_check("Teachtaire", "status", "fail", "missing", "fresh SWD status", "SWD")]
    status = evidence.status
    checks = [
        _check(
            "Teachtaire",
            "status age",
            "pass" if evidence.age_s <= manifest.preflight.max_croi_status_age_s else "fail",
            f"{evidence.age_s:.1f} s",
            f"<= {manifest.preflight.max_croi_status_age_s:g} s",
            "SWD",
        )
    ]
    for field, expected, label in (
        ("lora_init_ok", 1, "SX1272"),
        ("can_init_ok", 1, "CAN"),
        ("watchdog_init_ok", 1, "watchdog"),
        ("radio_config_magic", TEACHTAIRE_RADIO_CONFIG_MAGIC, "radio magic"),
        ("radio_config_schema_version", TEACHTAIRE_RADIO_CONFIG_SCHEMA_VERSION, "radio schema"),
        ("radio_config_crc32", radio_config_crc32(manifest.radio), "radio CRC"),
    ):
        actual = int(status.get(field, -1))
        hexadecimal = field.endswith("magic") or field.endswith("crc32")
        render = (lambda value: f"0x{value:08X}") if hexadecimal else str
        checks.append(
            _check(
                "Teachtaire",
                label,
                "pass" if actual == expected else "fail",
                render(actual),
                render(expected),
                f"SWD, {evidence.age_s:.1f} s old",
            )
        )
    return checks


def _telemetry_checks(
    manifest: FlightManifest,
    telemetry: dict[str, Any] | None,
    telemetry_age_s: float | None,
) -> list[PreflightCheck]:
    if telemetry is None or telemetry_age_s is None:
        return [_check("stack", "telemetry evidence", "fail", "missing", "live Groundstation stream", "USB")]
    checks = [
        _check(
            "stack",
            "telemetry stream age",
            "pass" if telemetry_age_s <= 2.0 else "fail",
            f"{telemetry_age_s:.1f} s",
            "<= 2.0 s",
            "Groundstation USB",
        )
    ]
    elapsed_s = float(telemetry.get("summary", {}).get("elapsed_s", 0.0))
    nodes = telemetry.get("can", {}).get("stack", {}).get("nodes", {})
    for board_id in manifest.effective_required_boards():
        node = nodes.get(board_id)
        if not isinstance(node, dict):
            checks.append(_check("stack", board_id, "fail", "missing", "heartbeat", "radio/CAN"))
            continue
        received_s = node.get("received_s")
        age_s = elapsed_s - float(received_s) if isinstance(received_s, (int, float)) else float("inf")
        err = int(node.get("err", 0))
        fresh = age_s <= manifest.preflight.max_heartbeat_age_s
        state = "pass" if fresh and err == 0 else "fail"
        actual = f"age {age_s:.1f} s, err 0x{err:02X}" if age_s != float("inf") else f"age unknown, err 0x{err:02X}"
        checks.append(
            _check(
                "stack",
                board_id,
                state,
                actual,
                f"age <= {manifest.preflight.max_heartbeat_age_s:g} s, err 0",
                "radio/CAN heartbeat",
            )
        )

    if manifest.preflight.require_gps_fix:
        records = telemetry.get("groundstation", {}).get("records", [])
        latest = records[-1] if records else None
        fixed = isinstance(latest, dict) and bool(latest.get("fix"))
        received_s = latest.get("received_s") if isinstance(latest, dict) else None
        age_s = elapsed_s - float(received_s) if isinstance(received_s, (int, float)) else float("inf")
        good = fixed and age_s <= manifest.preflight.max_gps_age_s
        actual = f"fix={fixed}, age={age_s:.1f} s" if age_s != float("inf") else f"fix={fixed}, age=unknown"
        checks.append(
            _check(
                "telemetry",
                "GPS fix",
                "pass" if good else "fail",
                actual,
                f"fresh <= {manifest.preflight.max_gps_age_s:g} s",
                "Teachtaire",
            )
        )
    return checks


def _required_pyro_mask(manifest: FlightManifest) -> int:
    mask = 0
    for channel in (manifest.mission.pyro_drogue_channel, manifest.mission.pyro_main_channel):
        if channel is not None:
            mask |= 1 << channel
    return mask


def _check(category: str, name: str, state: str, actual: str, required: str, source: str) -> PreflightCheck:
    return PreflightCheck(category, name, state, actual, required, source)
