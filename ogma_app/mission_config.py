from __future__ import annotations

import hashlib
import json
import re
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CROI_MISSION_CONFIG_MAGIC = 0x4F474D43
CROI_MISSION_CONFIG_SCHEMA_VERSION = 7
CROI_MISSION_DEFAULT_LIFTOFF_ACCEL_M_S2 = 20.0
CROI_FLASH_CAPACITY_BYTES = 16 * 1024 * 1024
CROI_FLIGHT_RECORD_BYTES = 144
CROI_REMOTE_RECORD_BYTES = 104


def _optional_channel(value: int | None) -> int | None:
    if value is None:
        return None
    channel = int(value)
    if channel < 0:
        return None
    if channel > 3:
        raise ValueError("pyro channels must be Disabled or 0 through 3")
    return channel


@dataclass(frozen=True)
class RecoveryFallbackConfig:
    main_backup_enabled: bool = False
    after_apogee_ms: int = 5000
    descent_speed_m_s: float = 30.0
    min_altitude_m: int = 100
    max_altitude_m: int = 2000
    required_samples: int = 5

    def validate(self, mission: "MissionConfig") -> None:
        if not 100 <= self.after_apogee_ms <= 120000:
            raise ValueError("main fallback delay must be 100 to 120000 ms")
        if not 1.0 <= self.descent_speed_m_s <= 300.0:
            raise ValueError("main fallback descent speed must be 1 to 300 m/s")
        if not 0 <= self.min_altitude_m < self.max_altitude_m <= 20000:
            raise ValueError("main fallback altitude window must be ordered within 0 to 20000 m")
        if not 3 <= self.required_samples <= 100:
            raise ValueError("main fallback confirmation must be 3 to 100 samples")
        if self.main_backup_enabled and mission.pyro_main_channel is None:
            raise ValueError("main recovery fallback requires an enabled main pyro channel")


@dataclass(frozen=True)
class PhaseDetectionConfig:
    liftoff_confirm_ms: int = 300
    liftoff_baro_velocity_m_s: float = 30.0
    burnout_min_powered_ms: int = 500
    burnout_accel_threshold_m_s2: float = -1.0
    burnout_confirm_ms: int = 300
    burnout_timeout_ms: int = 10000
    apogee_min_coast_ms: int = 1500
    apogee_min_altitude_m: int = 20
    apogee_velocity_threshold_m_s: float = -1.0
    apogee_confirm_ms: int = 500
    apogee_single_sensor_confirm_ms: int = 1000
    apogee_baro_descent_m: float = 3.0
    apogee_high_speed_lockout_m_s: float = 20.0
    apogee_timeout_ms: int = 120000
    sensor_fault_timeout_ms: int = 500

    def validate(self) -> None:
        if not 100 <= self.liftoff_confirm_ms <= 5000:
            raise ValueError("liftoff confirmation must be 100 to 5000 ms")
        if not 1.0 <= self.liftoff_baro_velocity_m_s <= 200.0:
            raise ValueError("liftoff barometric velocity must be 1 to 200 m/s")
        if not 100 <= self.burnout_min_powered_ms < self.burnout_timeout_ms <= 120000:
            raise ValueError("burnout timing must be ordered within 100 to 120000 ms")
        if not -50.0 <= self.burnout_accel_threshold_m_s2 <= 0.0:
            raise ValueError("burnout acceleration threshold must be -50 to 0 m/s^2")
        if not 100 <= self.burnout_confirm_ms <= 5000:
            raise ValueError("burnout confirmation must be 100 to 5000 ms")
        if not 500 <= self.apogee_min_coast_ms < self.apogee_timeout_ms <= 600000:
            raise ValueError("apogee timing must be ordered within 500 to 600000 ms")
        if not 1 <= self.apogee_min_altitude_m <= 20000:
            raise ValueError("minimum apogee altitude must be 1 to 20000 m")
        if not -100.0 <= self.apogee_velocity_threshold_m_s <= 0.0:
            raise ValueError("apogee velocity threshold must be -100 to 0 m/s")
        if not 100 <= self.apogee_confirm_ms <= 5000:
            raise ValueError("apogee confirmation must be 100 to 5000 ms")
        if not self.apogee_confirm_ms <= self.apogee_single_sensor_confirm_ms <= 10000:
            raise ValueError("single-sensor apogee confirmation must be no shorter than nominal")
        if not 0.5 <= self.apogee_baro_descent_m <= 100.0:
            raise ValueError("apogee barometric descent must be 0.5 to 100 m")
        if not 1.0 <= self.apogee_high_speed_lockout_m_s <= 300.0:
            raise ValueError("apogee high-speed lockout must be 1 to 300 m/s")
        if not 100 <= self.sensor_fault_timeout_ms <= 5000:
            raise ValueError("sensor fault timeout must be 100 to 5000 ms")


@dataclass(frozen=True)
class LoggingPolicy:
    mode: str = "flight_window"
    flight_sample_period_ms: int = 100
    minimum_flight_ms: int = 1_200_000
    post_landing_ms: int = 60000
    include_remote_can: bool = True

    def validate(self) -> None:
        if self.mode != "flight_window":
            raise ValueError("unsupported logging mode")
        if not 20 <= self.flight_sample_period_ms <= 1000:
            raise ValueError("flight logging period must be 20 to 1000 ms")
        if not 60000 <= self.minimum_flight_ms <= 7_200_000:
            raise ValueError("minimum flight logging time must be 60000 to 7200000 ms")
        if not 0 <= self.post_landing_ms <= 600000:
            raise ValueError("post-landing logging time must be 0 to 600000 ms")
        if self.required_capacity_bytes() > CROI_FLASH_CAPACITY_BYTES:
            raise ValueError("configured logging window exceeds Croí flash capacity")

    def required_capacity_bytes(self) -> int:
        duration_ms = self.minimum_flight_ms + self.post_landing_ms
        samples = (duration_ms + self.flight_sample_period_ms - 1) // self.flight_sample_period_ms
        per_sample = CROI_FLIGHT_RECORD_BYTES
        if self.include_remote_can:
            per_sample += CROI_REMOTE_RECORD_BYTES
        return samples * per_sample


@dataclass(frozen=True)
class MissionConfig:
    name: str
    liftoff_accel_m_s2: float
    imu_vertical_axis: int
    imu_vertical_sign: int
    main_deploy_altitude_m: int
    drogue_delay_ms: int
    airbrake_enabled: bool
    airbrake_channel: int
    airbrake_retracted_angle_deg: int
    airbrake_max_angle_deg: int
    airbrake_start_delay_ms: int
    airbrake_stow_delay_ms: int
    airbrake_command_timeout_ms: int
    pyro_drogue_channel: int | None
    pyro_main_channel: int | None

    @classmethod
    def defaults(cls) -> "MissionConfig":
        return cls(
            name="ogma-mission",
            liftoff_accel_m_s2=CROI_MISSION_DEFAULT_LIFTOFF_ACCEL_M_S2,
            imu_vertical_axis=1,
            imu_vertical_sign=-1,
            main_deploy_altitude_m=200,
            drogue_delay_ms=0,
            airbrake_enabled=False,
            airbrake_channel=0,
            airbrake_retracted_angle_deg=0,
            airbrake_max_angle_deg=90,
            airbrake_start_delay_ms=0,
            airbrake_stow_delay_ms=10000,
            airbrake_command_timeout_ms=500,
            pyro_drogue_channel=None,
            pyro_main_channel=None,
        )

    @classmethod
    def from_values(cls, **values: Any) -> "MissionConfig":
        config = cls(
            name=str(values["name"]).strip(),
            liftoff_accel_m_s2=float(values["liftoff_accel_m_s2"]),
            imu_vertical_axis=int(values["imu_vertical_axis"]),
            imu_vertical_sign=int(values["imu_vertical_sign"]),
            main_deploy_altitude_m=int(values["main_deploy_altitude_m"]),
            drogue_delay_ms=int(values["drogue_delay_ms"]),
            airbrake_enabled=bool(values["airbrake_enabled"]),
            airbrake_channel=int(values["airbrake_channel"]),
            airbrake_retracted_angle_deg=int(values["airbrake_retracted_angle_deg"]),
            airbrake_max_angle_deg=int(values["airbrake_max_angle_deg"]),
            airbrake_start_delay_ms=int(values["airbrake_start_delay_ms"]),
            airbrake_stow_delay_ms=int(values["airbrake_stow_delay_ms"]),
            airbrake_command_timeout_ms=int(values["airbrake_command_timeout_ms"]),
            pyro_drogue_channel=_optional_channel(values["pyro_drogue_channel"]),
            pyro_main_channel=_optional_channel(values["pyro_main_channel"]),
        )
        config.validate()
        return config

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "MissionConfig":
        return cls.from_values(**values)

    def validate(self) -> None:
        if not self.name:
            raise ValueError("mission name is required")
        if len(self.name) > 64:
            raise ValueError("mission name must be at most 64 characters")
        if not 1.0 <= self.liftoff_accel_m_s2 <= 200.0:
            raise ValueError("liftoff threshold must be 1 to 200 m/s^2")
        if self.imu_vertical_axis not in (0, 1, 2):
            raise ValueError("IMU vertical axis must be X, Y, or Z")
        if self.imu_vertical_sign not in (-1, 1):
            raise ValueError("IMU vertical sign must be positive or negative")
        if not 0 <= self.main_deploy_altitude_m <= 20000:
            raise ValueError("main deployment altitude must be 0 to 20000 m")
        if not 0 <= self.drogue_delay_ms <= 600000:
            raise ValueError("drogue delay must be 0 to 600000 ms")
        if not 0 <= self.airbrake_channel <= 3:
            raise ValueError("airbrake channel must be 0 through 3")
        if not 0 <= self.airbrake_retracted_angle_deg <= 90:
            raise ValueError("airbrake retracted angle must be 0 through 90 degrees")
        if not self.airbrake_retracted_angle_deg <= self.airbrake_max_angle_deg <= 90:
            raise ValueError("airbrake maximum angle must be retracted angle through 90 degrees")
        if not 0 <= self.airbrake_start_delay_ms <= 120000:
            raise ValueError("airbrake delay must be 0 to 120000 ms")
        if not 0 <= self.airbrake_stow_delay_ms <= 600000:
            raise ValueError("airbrake stow delay must be 0 to 600000 ms")
        if self.airbrake_enabled and self.airbrake_stow_delay_ms <= self.airbrake_start_delay_ms:
            raise ValueError("airbrake stow time must be after deploy time")
        if not 500 <= self.airbrake_command_timeout_ms <= 2000:
            raise ValueError("airbrake command timeout must be 500 to 2000 ms")
        if (
            self.pyro_drogue_channel is not None
            and self.pyro_drogue_channel == self.pyro_main_channel
        ):
            raise ValueError("drogue and main cannot use the same pyro channel")

    def canonical_dict(self) -> dict[str, Any]:
        return asdict(self)

    def crc32(
        self,
        recovery: RecoveryFallbackConfig | None = None,
        logging: LoggingPolicy | None = None,
        detection: PhaseDetectionConfig | None = None,
    ) -> int:
        recovery = recovery or RecoveryFallbackConfig()
        logging = logging or LoggingPolicy()
        detection = detection or PhaseDetectionConfig()
        recovery.validate(self)
        logging.validate()
        detection.validate()
        mission_fields = self.canonical_dict()
        del mission_fields["name"]
        safety_fields = {
            "mission": mission_fields,
            "recovery": asdict(recovery),
            "logging": asdict(logging),
            "detection": asdict(detection),
        }
        payload = json.dumps(
            safety_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return zlib.crc32(payload) & 0xFFFFFFFF


@dataclass(frozen=True)
class MissionTimelineEvent:
    trigger: str
    state: str
    action: str
    guard: str


def build_mission_timeline(
    config: MissionConfig,
    recovery: RecoveryFallbackConfig | None = None,
    logging: LoggingPolicy | None = None,
    detection: PhaseDetectionConfig | None = None,
) -> list[MissionTimelineEvent]:
    """Describe the exact configured flight-state and output sequence."""
    config.validate()
    recovery = recovery or RecoveryFallbackConfig()
    logging = logging or LoggingPolicy()
    detection = detection or PhaseDetectionConfig()
    recovery.validate(config)
    logging.validate()
    detection.validate()
    events = [
        MissionTimelineEvent(
            "Boot",
            "CALIBRATING",
            f"Load locked manifest 0x{config.crc32(recovery, logging, detection):08X}",
            f"magic + schema {CROI_MISSION_CONFIG_SCHEMA_VERSION} + CRC",
        ),
        MissionTimelineEvent(
            f"Vertical accel > {config.liftoff_accel_m_s2:g} m/s^2",
            "READY -> POWERED",
            "Start flight clock",
            f"{detection.liftoff_confirm_ms} ms persistence; acceleration or barometric climb fallback",
        ),
    ]
    if config.airbrake_enabled:
        output = config.airbrake_channel + 1
        events.extend(
            (
                MissionTimelineEvent(
                    f"T+{config.airbrake_start_delay_ms / 1000:g} s",
                    "POWERED / COASTING",
                    f"Lamh output {output} -> {config.airbrake_max_angle_deg} deg",
                    f"physical arm + {config.airbrake_command_timeout_ms} ms command lease",
                ),
                MissionTimelineEvent(
                    f"T+{config.airbrake_stow_delay_ms / 1000:g} s",
                    "POWERED / COASTING",
                    f"Lamh output {output} -> {config.airbrake_retracted_angle_deg} deg",
                    f"physical arm + {config.airbrake_command_timeout_ms} ms command lease",
                ),
            )
        )
    else:
        events.append(
            MissionTimelineEvent(
                "All flight states",
                "AIRBRAKE",
                "No actuator commands",
                "airbrake disabled",
            )
        )
    events.extend(
        (
            MissionTimelineEvent(
                f"Vertical accel <= {detection.burnout_accel_threshold_m_s2:g} m/s^2",
                "POWERED -> COASTING",
                "Mark motor burnout",
                f">= {detection.burnout_min_powered_ms} ms powered; {detection.burnout_confirm_ms} ms persistence; {detection.burnout_timeout_ms} ms fallback",
            ),
            MissionTimelineEvent(
                f"Apogee evidence <= {detection.apogee_velocity_threshold_m_s:g} m/s",
                "COASTING -> DROGUE",
                _pyro_action("drogue", config.pyro_drogue_channel),
                (
                    f">= {detection.apogee_min_coast_ms} ms coast; >= {detection.apogee_min_altitude_m} m; "
                    f"2-of-3 fused/baro/inertial, degraded dwell {detection.apogee_single_sensor_confirm_ms} ms; "
                    f"high-speed lockout {detection.apogee_high_speed_lockout_m_s:g} m/s; "
                    + _pyro_guard(config.pyro_drogue_channel)
                ),
            ),
            MissionTimelineEvent(
                f"Altitude < {config.main_deploy_altitude_m} m",
                "DROGUE -> MAIN",
                _pyro_action("main", config.pyro_main_channel),
                (
                    f">= {config.drogue_delay_ms / 1000:g} s in DROGUE; "
                    + _pyro_guard(config.pyro_main_channel)
                ),
            ),
            MissionTimelineEvent(
                "|velocity| <= 1 m/s and |accel| <= 2.5 m/s^2",
                "MAIN -> LANDED",
                "End active flight sequence",
                "50 consecutive samples",
            ),
        )
    )
    if recovery.main_backup_enabled:
        events.insert(
            -1,
            MissionTimelineEvent(
                (
                    f">= {recovery.after_apogee_ms / 1000:g} s after apogee, "
                    f"descent >= {recovery.descent_speed_m_s:g} m/s"
                ),
                "DROGUE -> MAIN",
                _pyro_action("main backup", config.pyro_main_channel),
                (
                    f"altitude {recovery.min_altitude_m}-{recovery.max_altitude_m} m; "
                    f"{recovery.required_samples} consecutive samples; "
                    + _pyro_guard(config.pyro_main_channel)
                ),
            ),
        )
    return events


def _pyro_action(role: str, channel: int | None) -> str:
    if channel is None:
        return f"{role.capitalize()} output disabled"
    return f"Fire {role} on Pleasc channel {channel}"


def _pyro_guard(channel: int | None) -> str:
    if channel is None:
        return "no fire command compiled"
    return "3 samples + armed/continuity + mission tag + sequence + 250 ms settle"


def render_croi_mission_header(
    config: MissionConfig,
    recovery: RecoveryFallbackConfig | None = None,
    logging: LoggingPolicy | None = None,
    detection: PhaseDetectionConfig | None = None,
) -> str:
    config.validate()
    recovery = recovery or RecoveryFallbackConfig()
    logging = logging or LoggingPolicy()
    detection = detection or PhaseDetectionConfig()
    recovery.validate(config)
    logging.validate()
    detection.validate()
    crc32 = config.crc32(recovery, logging, detection)
    drogue_channel = -1 if config.pyro_drogue_channel is None else config.pyro_drogue_channel
    main_channel = -1 if config.pyro_main_channel is None else config.pyro_main_channel
    liftoff_x100 = round(config.liftoff_accel_m_s2 * 100.0)
    return "\n".join(
        (
            "#ifndef CROI_MISSION_CONFIG_H",
            "#define CROI_MISSION_CONFIG_H",
            "",
            "/* Generated by Ogma Console. Build-time mission manifest. */",
            f"#define CROI_MISSION_CONFIG_MAGIC 0x{CROI_MISSION_CONFIG_MAGIC:08X}U",
            f"#define CROI_MISSION_CONFIG_SCHEMA_VERSION {CROI_MISSION_CONFIG_SCHEMA_VERSION}U",
            f"#define CROI_MISSION_CONFIG_CRC32 0x{crc32:08X}U",
            f"#define CROI_MISSION_LIFTOFF_ACCEL_M_S2_X100 {liftoff_x100}U",
            f"#define CROI_MISSION_IMU_VERTICAL_AXIS {config.imu_vertical_axis}U",
            f"#define CROI_MISSION_IMU_VERTICAL_SIGN {config.imu_vertical_sign}",
            f"#define CROI_MISSION_MAIN_DEPLOY_ALTITUDE_M {config.main_deploy_altitude_m}U",
            f"#define CROI_MISSION_DROGUE_DELAY_MS {config.drogue_delay_ms}U",
            f"#define CROI_MISSION_AIRBRAKE_ENABLED {int(config.airbrake_enabled)}U",
            f"#define CROI_MISSION_AIRBRAKE_CHANNEL {config.airbrake_channel}U",
            f"#define CROI_MISSION_AIRBRAKE_RETRACTED_ANGLE_DEG {config.airbrake_retracted_angle_deg}U",
            f"#define CROI_MISSION_AIRBRAKE_MAX_ANGLE_DEG {config.airbrake_max_angle_deg}U",
            f"#define CROI_MISSION_AIRBRAKE_START_DELAY_MS {config.airbrake_start_delay_ms}U",
            f"#define CROI_MISSION_AIRBRAKE_STOW_DELAY_MS {config.airbrake_stow_delay_ms}U",
            f"#define CROI_MISSION_AIRBRAKE_COMMAND_TIMEOUT_MS {config.airbrake_command_timeout_ms}U",
            f"#define CROI_MISSION_PYRO_DROGUE_CHANNEL {drogue_channel}",
            f"#define CROI_MISSION_PYRO_MAIN_CHANNEL {main_channel}",
            f"#define CROI_MISSION_MAIN_BACKUP_ENABLED {int(recovery.main_backup_enabled)}U",
            f"#define CROI_MISSION_MAIN_BACKUP_AFTER_APOGEE_MS {recovery.after_apogee_ms}U",
            f"#define CROI_MISSION_MAIN_BACKUP_DESCENT_SPEED_M_S_X100 {round(recovery.descent_speed_m_s * 100.0)}U",
            f"#define CROI_MISSION_MAIN_BACKUP_MIN_ALTITUDE_M {recovery.min_altitude_m}U",
            f"#define CROI_MISSION_MAIN_BACKUP_MAX_ALTITUDE_M {recovery.max_altitude_m}U",
            f"#define CROI_MISSION_MAIN_BACKUP_REQUIRED_SAMPLES {recovery.required_samples}U",
            f"#define CROI_PHASE_LIFTOFF_CONFIRM_MS {detection.liftoff_confirm_ms}U",
            f"#define CROI_PHASE_LIFTOFF_BARO_VELOCITY_M_S_X100 {round(detection.liftoff_baro_velocity_m_s * 100.0)}U",
            f"#define CROI_PHASE_BURNOUT_MIN_POWERED_MS {detection.burnout_min_powered_ms}U",
            f"#define CROI_PHASE_BURNOUT_ACCEL_M_S2_X100 {round(detection.burnout_accel_threshold_m_s2 * 100.0)}",
            f"#define CROI_PHASE_BURNOUT_CONFIRM_MS {detection.burnout_confirm_ms}U",
            f"#define CROI_PHASE_BURNOUT_TIMEOUT_MS {detection.burnout_timeout_ms}U",
            f"#define CROI_PHASE_APOGEE_MIN_COAST_MS {detection.apogee_min_coast_ms}U",
            f"#define CROI_PHASE_APOGEE_MIN_ALTITUDE_M {detection.apogee_min_altitude_m}U",
            f"#define CROI_PHASE_APOGEE_VELOCITY_M_S_X100 {round(detection.apogee_velocity_threshold_m_s * 100.0)}",
            f"#define CROI_PHASE_APOGEE_CONFIRM_MS {detection.apogee_confirm_ms}U",
            f"#define CROI_PHASE_APOGEE_SINGLE_SENSOR_CONFIRM_MS {detection.apogee_single_sensor_confirm_ms}U",
            f"#define CROI_PHASE_APOGEE_BARO_DESCENT_M_X100 {round(detection.apogee_baro_descent_m * 100.0)}U",
            f"#define CROI_PHASE_APOGEE_HIGH_SPEED_LOCKOUT_M_S_X100 {round(detection.apogee_high_speed_lockout_m_s * 100.0)}U",
            f"#define CROI_PHASE_APOGEE_TIMEOUT_MS {detection.apogee_timeout_ms}U",
            f"#define CROI_PHASE_SENSOR_FAULT_TIMEOUT_MS {detection.sensor_fault_timeout_ms}U",
            f"#define CROI_LOGGING_FLIGHT_SAMPLE_PERIOD_MS {logging.flight_sample_period_ms}U",
            f"#define CROI_LOGGING_MINIMUM_FLIGHT_MS {logging.minimum_flight_ms}U",
            f"#define CROI_LOGGING_POST_LANDING_MS {logging.post_landing_ms}U",
            f"#define CROI_LOGGING_INCLUDE_REMOTE_CAN {int(logging.include_remote_can)}U",
            "",
            "#if CROI_MISSION_AIRBRAKE_CHANNEL > 3U",
            "#error \"invalid airbrake channel\"",
            "#endif",
            "#if CROI_MISSION_IMU_VERTICAL_AXIS > 2U",
            "#error \"invalid IMU vertical axis\"",
            "#endif",
            "#if CROI_MISSION_IMU_VERTICAL_SIGN != -1 && CROI_MISSION_IMU_VERTICAL_SIGN != 1",
            "#error \"invalid IMU vertical sign\"",
            "#endif",
            "#if CROI_MISSION_AIRBRAKE_MAX_ANGLE_DEG > 90U",
            "#error \"invalid airbrake maximum angle\"",
            "#endif",
            "#if CROI_MISSION_AIRBRAKE_RETRACTED_ANGLE_DEG > CROI_MISSION_AIRBRAKE_MAX_ANGLE_DEG",
            "#error \"airbrake retract angle exceeds maximum\"",
            "#endif",
            "#if CROI_MISSION_AIRBRAKE_ENABLED && CROI_MISSION_AIRBRAKE_STOW_DELAY_MS <= CROI_MISSION_AIRBRAKE_START_DELAY_MS",
            "#error \"airbrake stow time must be after deploy time\"",
            "#endif",
            "#if CROI_MISSION_AIRBRAKE_COMMAND_TIMEOUT_MS < 500U || CROI_MISSION_AIRBRAKE_COMMAND_TIMEOUT_MS > 2000U",
            "#error \"invalid airbrake command timeout\"",
            "#endif",
            "#if CROI_MISSION_PYRO_DROGUE_CHANNEL > 3 || CROI_MISSION_PYRO_MAIN_CHANNEL > 3",
            "#error \"invalid pyro channel\"",
            "#endif",
            "#if CROI_MISSION_PYRO_DROGUE_CHANNEL >= 0 && CROI_MISSION_PYRO_DROGUE_CHANNEL == CROI_MISSION_PYRO_MAIN_CHANNEL",
            "#error \"drogue and main cannot use the same pyro channel\"",
            "#endif",
            "#if CROI_MISSION_MAIN_BACKUP_ENABLED && CROI_MISSION_PYRO_MAIN_CHANNEL < 0",
            "#error \"main backup requires a main pyro channel\"",
            "#endif",
            "#if CROI_MISSION_MAIN_BACKUP_MIN_ALTITUDE_M >= CROI_MISSION_MAIN_BACKUP_MAX_ALTITUDE_M",
            "#error \"main backup altitude window is invalid\"",
            "#endif",
            "#if CROI_MISSION_MAIN_BACKUP_REQUIRED_SAMPLES < 3U || CROI_MISSION_MAIN_BACKUP_REQUIRED_SAMPLES > 100U",
            "#error \"main backup confirmation sample count is invalid\"",
            "#endif",
            "#if CROI_PHASE_LIFTOFF_CONFIRM_MS < 100U || CROI_PHASE_LIFTOFF_CONFIRM_MS > 5000U",
            "#error \"invalid liftoff confirmation time\"",
            "#endif",
            "#if CROI_PHASE_BURNOUT_MIN_POWERED_MS < 100U || CROI_PHASE_BURNOUT_MIN_POWERED_MS >= CROI_PHASE_BURNOUT_TIMEOUT_MS",
            "#error \"invalid burnout timing\"",
            "#endif",
            "#if CROI_PHASE_BURNOUT_ACCEL_M_S2_X100 > 0 || CROI_PHASE_BURNOUT_ACCEL_M_S2_X100 < -5000",
            "#error \"invalid burnout acceleration threshold\"",
            "#endif",
            "#if CROI_PHASE_APOGEE_MIN_COAST_MS < 500U || CROI_PHASE_APOGEE_MIN_COAST_MS >= CROI_PHASE_APOGEE_TIMEOUT_MS",
            "#error \"invalid apogee timing\"",
            "#endif",
            "#if CROI_PHASE_APOGEE_VELOCITY_M_S_X100 > 0 || CROI_PHASE_APOGEE_VELOCITY_M_S_X100 < -10000",
            "#error \"invalid apogee velocity threshold\"",
            "#endif",
            "#if CROI_PHASE_APOGEE_CONFIRM_MS < 100U || CROI_PHASE_APOGEE_CONFIRM_MS > 5000U",
            "#error \"invalid apogee confirmation time\"",
            "#endif",
            "#if CROI_PHASE_APOGEE_SINGLE_SENSOR_CONFIRM_MS < CROI_PHASE_APOGEE_CONFIRM_MS || CROI_PHASE_APOGEE_SINGLE_SENSOR_CONFIRM_MS > 10000U",
            "#error \"invalid degraded apogee confirmation time\"",
            "#endif",
            "#if CROI_PHASE_SENSOR_FAULT_TIMEOUT_MS < 100U || CROI_PHASE_SENSOR_FAULT_TIMEOUT_MS > 5000U",
            "#error \"invalid sensor fault timeout\"",
            "#endif",
            "#if CROI_LOGGING_FLIGHT_SAMPLE_PERIOD_MS < 20U || CROI_LOGGING_FLIGHT_SAMPLE_PERIOD_MS > 1000U",
            "#error \"flight logging period is invalid\"",
            "#endif",
            "#if CROI_LOGGING_MINIMUM_FLIGHT_MS < 60000U || CROI_LOGGING_MINIMUM_FLIGHT_MS > 7200000U",
            "#error \"minimum flight logging duration is invalid\"",
            "#endif",
            "#if CROI_LOGGING_POST_LANDING_MS > 600000U",
            "#error \"post-landing logging duration is invalid\"",
            "#endif",
            "#endif",
            "",
        )
    )


def write_croi_mission_header(
    path: Path,
    config: MissionConfig,
    recovery: RecoveryFallbackConfig | None = None,
    logging: LoggingPolicy | None = None,
    detection: PhaseDetectionConfig | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_croi_mission_header(config, recovery, logging, detection), encoding="utf-8")
    temporary.replace(path)
    return path


def save_mission_json(directory: Path, config: MissionConfig) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    recovery = RecoveryFallbackConfig()
    logging = LoggingPolicy()
    detection = PhaseDetectionConfig()
    payload = {
        "schema_version": CROI_MISSION_CONFIG_SCHEMA_VERSION,
        "mission_crc32": f"{config.crc32(recovery, logging, detection):08x}",
        "mission": config.canonical_dict(),
        "recovery": asdict(recovery),
        "logging": asdict(logging),
        "detection": asdict(detection),
    }
    path = directory / f"mission_{timestamp.strftime('%Y%m%dT%H%M%SZ')}_{config.crc32(recovery, logging, detection):08x}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def save_mission_flash_record(
    directory: Path,
    config: MissionConfig,
    env: str,
    header_path: Path,
    status: dict[str, object],
    recovery: RecoveryFallbackConfig | None = None,
    logging: LoggingPolicy | None = None,
    detection: PhaseDetectionConfig | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    recovery = recovery or RecoveryFallbackConfig()
    logging = logging or LoggingPolicy()
    detection = detection or PhaseDetectionConfig()
    header = header_path.read_bytes()
    payload = {
        "flashed_at_utc": timestamp.isoformat(),
        "env": env,
        "mission_crc32": f"{config.crc32(recovery, logging, detection):08x}",
        "mission": config.canonical_dict(),
        "recovery": asdict(recovery),
        "logging": asdict(logging),
        "detection": asdict(detection),
        "header": str(header_path),
        "header_sha256": hashlib.sha256(header).hexdigest(),
        "status": status,
    }
    path = directory / f"mission_flash_{timestamp.strftime('%Y%m%dT%H%M%SZ')}_{config.crc32(recovery, logging, detection):08x}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def load_mission_json(path: Path) -> MissionConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema_version = int(payload.get("schema_version", 0))
    if schema_version not in (1, 2, 3, 4, 5, 6, CROI_MISSION_CONFIG_SCHEMA_VERSION):
        raise ValueError("unsupported mission JSON schema")
    mission = payload.get("mission")
    if not isinstance(mission, dict):
        raise ValueError("mission JSON has no mission object")
    recorded_crc = str(payload.get("mission_crc32", ""))
    legacy_payload = json.dumps(
        {key: value for key, value in mission.items() if key != "name"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    legacy_crc = zlib.crc32(legacy_payload) & 0xFFFFFFFF
    mission = dict(mission)
    if schema_version == 1:
        mission.setdefault("imu_vertical_axis", 1)
        mission.setdefault("imu_vertical_sign", -1)
    if schema_version <= 2:
        mission.setdefault("airbrake_stow_delay_ms", 120000)
    config = MissionConfig.from_dict(mission)
    if schema_version >= 4:
        recovery_values = payload.get("recovery", {})
        if not isinstance(recovery_values, dict):
            raise ValueError("mission JSON recovery config is invalid")
        recovery = RecoveryFallbackConfig(**recovery_values)
        if schema_version >= 5:
            logging_values = payload.get("logging", {})
            if not isinstance(logging_values, dict):
                raise ValueError("mission JSON logging policy is invalid")
            if schema_version >= 7:
                logging = LoggingPolicy(**logging_values)
                detection_values = payload.get("detection", {})
                if not isinstance(detection_values, dict):
                    raise ValueError("mission JSON detection config is invalid")
                expected_crc = config.crc32(
                    recovery,
                    logging,
                    PhaseDetectionConfig(**detection_values),
                )
            elif schema_version >= 6:
                mission_fields = config.canonical_dict()
                del mission_fields["name"]
                expected_crc = zlib.crc32(
                    json.dumps(
                        {
                            "mission": mission_fields,
                            "recovery": recovery_values,
                            "logging": logging_values,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("ascii")
                ) & 0xFFFFFFFF
            else:
                mission_fields = config.canonical_dict()
                del mission_fields["name"]
                expected_crc = zlib.crc32(
                    json.dumps(
                        {
                            "mission": mission_fields,
                            "recovery": recovery_values,
                            "logging": logging_values,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("ascii")
                ) & 0xFFFFFFFF
        else:
            mission_fields = config.canonical_dict()
            del mission_fields["name"]
            expected_crc = zlib.crc32(
                json.dumps(
                    {"mission": mission_fields, "recovery": asdict(recovery)},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("ascii")
            ) & 0xFFFFFFFF
    else:
        expected_crc = legacy_crc
    if recorded_crc and recorded_crc.lower() != f"{expected_crc:08x}":
        raise ValueError("mission JSON CRC does not match its contents")
    return config


def load_croi_mission_config(path: Path) -> MissionConfig:
    text = path.read_text(encoding="utf-8")

    def macro(name: str) -> int:
        match = re.search(rf"^\s*#define\s+{name}\s+(-?(?:0x[0-9A-Fa-f]+|\d+))U?\s*$", text, re.MULTILINE)
        if match is None:
            raise ValueError(f"missing {name} in {path}")
        return int(match.group(1), 0)

    schema_version = macro("CROI_MISSION_CONFIG_SCHEMA_VERSION")

    config = MissionConfig.from_values(
        name="loaded-mission",
        liftoff_accel_m_s2=macro("CROI_MISSION_LIFTOFF_ACCEL_M_S2_X100") / 100.0,
        imu_vertical_axis=macro("CROI_MISSION_IMU_VERTICAL_AXIS"),
        imu_vertical_sign=macro("CROI_MISSION_IMU_VERTICAL_SIGN"),
        main_deploy_altitude_m=macro("CROI_MISSION_MAIN_DEPLOY_ALTITUDE_M"),
        drogue_delay_ms=macro("CROI_MISSION_DROGUE_DELAY_MS"),
        airbrake_enabled=bool(macro("CROI_MISSION_AIRBRAKE_ENABLED")),
        airbrake_channel=macro("CROI_MISSION_AIRBRAKE_CHANNEL"),
        airbrake_retracted_angle_deg=macro("CROI_MISSION_AIRBRAKE_RETRACTED_ANGLE_DEG"),
        airbrake_max_angle_deg=macro("CROI_MISSION_AIRBRAKE_MAX_ANGLE_DEG"),
        airbrake_start_delay_ms=macro("CROI_MISSION_AIRBRAKE_START_DELAY_MS"),
        airbrake_stow_delay_ms=(
            macro("CROI_MISSION_AIRBRAKE_STOW_DELAY_MS")
            if schema_version >= 3
            else 120000
        ),
        airbrake_command_timeout_ms=macro("CROI_MISSION_AIRBRAKE_COMMAND_TIMEOUT_MS"),
        pyro_drogue_channel=macro("CROI_MISSION_PYRO_DROGUE_CHANNEL"),
        pyro_main_channel=macro("CROI_MISSION_PYRO_MAIN_CHANNEL"),
    )
    if macro("CROI_MISSION_CONFIG_MAGIC") != CROI_MISSION_CONFIG_MAGIC:
        raise ValueError("unexpected Croí mission config magic")
    return config
