from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BinaryField:
    name: str
    offset: int
    fmt: str
    label: str | None = None
    scale: float = 1.0
    unit: str = ""
    enum: dict[int, str] | None = None
    min_version: int = 1

    def read(self, data: bytes) -> Any:
        value = struct.unpack_from("<" + self.fmt, data, self.offset)[0]
        if self.scale != 1.0:
            return value * self.scale
        return value

    def display_name(self) -> str:
        return self.label or self.name.replace("_", " ")

    def display_value(self, value: Any) -> str:
        if self.enum and isinstance(value, int):
            return self.enum.get(value, str(value))
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)


@dataclass(frozen=True)
class StatusBlock:
    symbol: str
    magic: int
    size: int
    fields: tuple[BinaryField, ...]

    def __post_init__(self) -> None:
        required_size = max(
            (field.offset + struct.calcsize("<" + field.fmt) for field in self.fields),
            default=0,
        )
        if self.size < required_size:
            raise ValueError(
                f"{self.symbol} size {self.size} is smaller than field extent {required_size}"
            )

    def parse(self, data: bytes) -> dict[str, Any]:
        if len(data) < self.size:
            raise ValueError(f"status block too short: {len(data)} < {self.size}")
        magic_field = next((field for field in self.fields if field.name == "magic"), None)
        if magic_field is None:
            raise ValueError(f"{self.symbol} has no magic field")
        magic = int(magic_field.read(data))
        version_field = next((field for field in self.fields if field.name == "version"), None)
        version = int(version_field.read(data)) if version_field is not None else 1
        if version <= 0:
            version = 1
        values = {field.name: field.read(data) for field in self.fields if field.min_version <= version}
        magic = int(values.get("magic", 0))
        if magic != self.magic:
            raise ValueError(f"bad magic for {self.symbol}: 0x{magic:08x}")
        return values


BOOL_STATUS = {
    0: "no",
    1: "yes",
}


TEACHTAIRE_STATUS = StatusBlock(
    symbol="report",
    magic=0x54434854,
    size=268,
    fields=(
        BinaryField("magic", 0, "I"),
        BinaryField("loops", 4, "I"),
        BinaryField("clock_hz", 8, "I", unit="Hz"),
        BinaryField("clock_ok", 12, "B"),
        BinaryField("gpio_ok", 13, "B"),
        BinaryField("spi_ok", 14, "B"),
        BinaryField("uart_ok", 15, "B"),
        BinaryField("lora_init_ok", 16, "B"),
        BinaryField("lora_rx_ok", 17, "B"),
        BinaryField("lora_version", 18, "B"),
        BinaryField("lora_error", 19, "B"),
        BinaryField("lora_irq", 20, "B"),
        BinaryField("lora_rssi_dbm", 22, "h", unit="dBm"),
        BinaryField("lora_snr_db", 24, "b", unit="dB"),
        BinaryField("lora_tx_count", 28, "I"),
        BinaryField("lora_tx_done_count", 32, "I"),
        BinaryField("lora_rx_count", 36, "I"),
        BinaryField("lora_rx_bad_count", 40, "I"),
        BinaryField("lora_last_counter", 44, "I"),
        BinaryField("gnss_seen", 48, "I"),
        BinaryField("gnss_parsed", 52, "I"),
        BinaryField("gnss_checksum_bad", 56, "I"),
        BinaryField("gnss_bytes", 60, "I"),
        BinaryField("gnss_starts", 64, "I"),
        BinaryField("gnss_overflows", 68, "I"),
        BinaryField("gnss_txt", 72, "I"),
        BinaryField("gnss_nav_sat", 76, "I"),
        BinaryField("gnss_fix", 80, "B"),
        BinaryField("gnss_sats", 81, "B"),
        BinaryField("gnss_sats_in_view", 82, "B"),
        BinaryField("gnss_ant_status", 83, "B"),
        BinaryField("gnss_nav_sat_count", 84, "B"),
        BinaryField("gnss_nav_sat_signal", 85, "B"),
        BinaryField("gnss_nav_sat_max_cno", 86, "B"),
        BinaryField("version", 87, "B"),
        BinaryField("gnss_latitude_e7", 88, "i", scale=1e-7, unit="deg"),
        BinaryField("gnss_longitude_e7", 92, "i", scale=1e-7, unit="deg"),
        BinaryField("gnss_altitude_mm", 96, "i", scale=0.001, unit="m"),
        BinaryField("gnss_velocity_mm_s", 100, "i", scale=0.001, unit="m/s"),
        BinaryField("spi_status", 104, "I"),
        BinaryField("spi_error", 108, "I"),
        BinaryField("uart_status", 112, "I"),
        BinaryField("uart_error", 116, "I"),
        BinaryField("usart1_isr", 120, "I"),
        BinaryField("gpioa_idr", 124, "I"),
        BinaryField("gpioa_odr", 128, "I"),
        BinaryField("gpiob_idr", 132, "I"),
        BinaryField("gpiob_odr", 136, "I"),
        BinaryField("fault", 140, "I"),
        BinaryField("clock_source", 144, "I", min_version=2),
        BinaryField("can_init_ok", 148, "I", enum=BOOL_STATUS, min_version=2),
        BinaryField("can_bus_off", 152, "I", enum=BOOL_STATUS, min_version=2),
        BinaryField("can_error", 156, "I", min_version=2),
        BinaryField("can_tx_count", 160, "I", min_version=2),
        BinaryField("can_rx_count", 164, "I", min_version=2),
        BinaryField("can_tx_drops", 168, "I", min_version=2),
        BinaryField("can_tx_queue_depth", 172, "I", min_version=2),
        BinaryField("heartbeat_tx_count", 176, "I", min_version=2),
        BinaryField("gps_can_tx_count", 180, "I", min_version=2),
        BinaryField("tx_status_can_tx_count", 184, "I", min_version=2),
        BinaryField("croi_last_seen_ms", 188, "I", unit="ms", min_version=2),
        BinaryField("can_esr", 192, "I", min_version=2),
        BinaryField("lora_reinit_count", 196, "I", min_version=2),
        BinaryField("lora_tx_timeout_count", 200, "I", min_version=2),
        BinaryField("gnss_last_fix_ms", 204, "I", unit="ms", min_version=2),
        BinaryField("gnss_fix_age_ms", 208, "I", unit="ms", min_version=2),
        BinaryField("can_rx_overruns", 212, "I", min_version=2),
        BinaryField("watchdog_init_ok", 216, "I", enum=BOOL_STATUS, min_version=3),
        BinaryField("watchdog_refresh_count", 220, "I", min_version=3),
        BinaryField("reset_flags", 224, "I", min_version=3),
        BinaryField("telemetry_core_tx_count", 228, "I", min_version=4),
        BinaryField("telemetry_gps_tx_count", 232, "I", min_version=4),
        BinaryField("telemetry_slow_tx_count", 236, "I", min_version=4),
        BinaryField("telemetry_event_tx_count", 240, "I", min_version=4),
        BinaryField("telemetry_deep_tx_count", 244, "I", min_version=4),
        BinaryField("telemetry_event_drop_count", 248, "I", min_version=4),
        BinaryField("gnss_uart_overrun_recoveries", 252, "I", min_version=5),
        BinaryField("radio_config_magic", 256, "I", min_version=6),
        BinaryField("radio_config_schema_version", 260, "I", min_version=6),
        BinaryField("radio_config_crc32", 264, "I", min_version=6),
    ),
)


SERVO_STAGE = {
    1: "boot",
    2: "clock ready",
    3: "i2c ready",
    4: "scan start",
    5: "scan probe",
    6: "scan found",
    7: "scan not found",
    8: "rtos start",
    9: "task create failed",
    20: "task start",
    21: "pca init start",
    22: "pca reset",
    23: "pca mode2",
    24: "pca freq start",
    25: "pca freq done",
    26: "pca init done",
    27: "pca init failed",
    30: "servo set",
    31: "pwm write",
}

I2C_OP = {
    0: "none",
    1: "write",
    2: "read reg",
    3: "read data",
    4: "ready",
}

LAMH_SAFETY_CONFIG_STATUS = {
    0x4C534346: "loaded",
}

LAMH_SERVO_DEBUG = StatusBlock(
    symbol="servo_debug",
    magic=0x53455256,
    size=148,
    fields=(
        BinaryField("magic", 0, "I"),
        BinaryField("stage", 4, "I", enum=SERVO_STAGE),
        BinaryField("ticks", 8, "I"),
        BinaryField("pca9685_found", 12, "B"),
        BinaryField("pca9685_address", 13, "B"),
        BinaryField("servo_channel", 14, "B"),
        BinaryField("scan_attempts", 16, "I"),
        BinaryField("scan_last_status", 20, "I"),
        BinaryField("scan_last_error", 24, "I"),
        BinaryField("scan_last_address", 28, "B"),
        BinaryField("i2c_last_op", 32, "I", enum=I2C_OP),
        BinaryField("i2c_last_status", 36, "I"),
        BinaryField("i2c_last_error", 40, "I"),
        BinaryField("i2c_last_address", 44, "B"),
        BinaryField("i2c_last_register", 45, "B"),
        BinaryField("i2c_last_length", 46, "H"),
        BinaryField("i2c_write_count", 48, "I"),
        BinaryField("i2c_read_count", 52, "I"),
        BinaryField("pca9685_init_count", 56, "I"),
        BinaryField("pca9685_mode1_before_prescale", 60, "B"),
        BinaryField("pca9685_prescale", 61, "B"),
        BinaryField("servo_set_count", 64, "I"),
        BinaryField("servo_angle", 68, "h", unit="deg"),
        BinaryField("servo_pwm", 70, "H"),
        BinaryField("pwm_on", 72, "H"),
        BinaryField("pwm_off", 74, "H"),
        BinaryField("can_init_ok", 76, "I", enum=BOOL_STATUS),
        BinaryField("can_bus_off", 80, "I", enum=BOOL_STATUS),
        BinaryField("can_error", 84, "I"),
        BinaryField("can_tx_count", 88, "I"),
        BinaryField("can_rx_count", 92, "I"),
        BinaryField("can_tx_drops", 96, "I"),
        BinaryField("can_tx_queue_depth", 100, "I"),
        BinaryField("heartbeat_tx_count", 104, "I"),
        BinaryField("command_rx_count", 108, "I"),
        BinaryField("croi_last_seen_ms", 112, "I", unit="ms"),
        BinaryField("failsafe_count", 116, "I"),
        BinaryField("can_esr", 120, "I"),
        BinaryField("safety_config_magic", 124, "I", enum=LAMH_SAFETY_CONFIG_STATUS),
        BinaryField("safety_config_version", 128, "H"),
        BinaryField("safe_angle_pwm1_deg", 132, "h", unit="deg"),
        BinaryField("safe_angle_pwm2_deg", 134, "h", unit="deg"),
        BinaryField("safe_angle_pwm3_deg", 136, "h", unit="deg"),
        BinaryField("safe_angle_pwm4_deg", 138, "h", unit="deg"),
        BinaryField("arm_input_active", 140, "I", enum=BOOL_STATUS),
        BinaryField("arm_input_raw_active", 144, "I", enum=BOOL_STATUS),
    ),
)

LOGGER_FAULT = {
    0: "none",
    1: "no storage",
    2: "flash init failed",
    3: "log begin failed",
    4: "full",
    5: "corrupt",
    6: "flash io",
    7: "verify failed",
    8: "payload too large",
    9: "not initialized",
    10: "unknown",
}

FLASH_LOG_STATUS = {
    0: "ok",
    1: "not initialized",
    2: "bad config",
    3: "flash error",
    4: "full",
    5: "payload too large",
    6: "corrupt",
    7: "verify failed",
    8: "not found",
    9: "run id exhausted",
    10: "incomplete",
}

CROI_FLIGHT_STATE = {
    0: "calibrating",
    1: "ready",
    2: "powered",
    3: "coasting",
    4: "drogue",
    5: "main",
    6: "landed",
}

CROI_WIPE_STATE = {
    0: "idle",
    1: "erasing",
    2: "done",
    3: "failed",
}

CROI_MISSION_CONFIG_STATUS = {
    0x4F474D43: "loaded",
}

CROI_PHASE_TRANSITION_REASON = {
    0: "none",
    1: "liftoff acceleration",
    2: "liftoff barometric climb",
    3: "burnout acceleration",
    4: "burnout timeout",
    5: "apogee voting",
    6: "apogee barometer fallback",
    7: "apogee inertial fallback",
    8: "apogee timeout",
    9: "main altitude",
    10: "main fast descent",
    11: "landed",
}

CROI_PHASE_DETECTOR_MODE = {
    0: "IMU + barometer",
    1: "barometer only",
    2: "IMU only",
    3: "no sensors",
}


CROI_STATUS = StatusBlock(
    symbol="croi_status",
    magic=0x43524F49,
    size=372,
    fields=(
        BinaryField("magic", 0, "I"),
        BinaryField("version", 4, "I"),
        BinaryField("uptime_ms", 8, "I", unit="ms"),
        BinaryField("init_ok", 12, "I", enum=BOOL_STATUS),
        BinaryField("imu_init_ok", 16, "I", enum=BOOL_STATUS),
        BinaryField("baro_init_ok", 20, "I", enum=BOOL_STATUS),
        BinaryField("can_init_ok", 24, "I", enum=BOOL_STATUS),
        BinaryField("flash_init_ok", 28, "I", label="logger init ok", enum=BOOL_STATUS),
        BinaryField("logger_fault_latched", 32, "I", enum=BOOL_STATUS),
        BinaryField("logger_logging_stopped", 36, "I", enum=BOOL_STATUS),
        BinaryField("logger_fault", 40, "I", enum=LOGGER_FAULT),
        BinaryField("logger_flash_status", 44, "I", enum=FLASH_LOG_STATUS),
        BinaryField("logger_run_id", 48, "I"),
        BinaryField("logger_records_written", 52, "I"),
        BinaryField("logger_used_bytes", 56, "I", unit="B"),
        BinaryField("can_bus_off", 60, "I", enum=BOOL_STATUS),
        BinaryField("can_error", 64, "I", enum=BOOL_STATUS),
        BinaryField("can_tx_retry_depth", 68, "I"),
        BinaryField("can_tx_retry_drops", 72, "I"),
        BinaryField("can_node_timeout_count", 76, "I"),
        BinaryField("can_active_nodes", 80, "I"),
        BinaryField("can_last_heartbeat_ms", 84, "I", unit="ms"),
        BinaryField("flight_state", 88, "I", enum=CROI_FLIGHT_STATE, min_version=2),
        BinaryField("baro_pressure_pa", 92, "I", unit="Pa", min_version=2),
        BinaryField("baro_temperature_c", 96, "i", scale=0.01, unit="C", min_version=2),
        BinaryField("baro_altitude_m", 100, "i", scale=0.01, unit="m", min_version=2),
        BinaryField("prediction_altitude_m", 104, "i", scale=0.01, unit="m", min_version=2),
        BinaryField("prediction_velocity_m_s", 108, "i", scale=0.01, unit="m/s", min_version=2),
        BinaryField("prediction_accel_m_s2", 112, "i", scale=0.01, unit="m/s^2", min_version=2),
        BinaryField("imu_accel_x_g", 116, "i", scale=0.001, unit="g", min_version=2),
        BinaryField("imu_accel_y_g", 120, "i", scale=0.001, unit="g", min_version=2),
        BinaryField("imu_accel_z_g", 124, "i", scale=0.001, unit="g", min_version=2),
        BinaryField("imu_gyro_x_dps", 128, "i", unit="deg/s", min_version=2),
        BinaryField("imu_gyro_y_dps", 132, "i", unit="deg/s", min_version=2),
        BinaryField("imu_gyro_z_dps", 136, "i", unit="deg/s", min_version=2),
        BinaryField("flash_wipe_state", 140, "I", enum=CROI_WIPE_STATE, min_version=3),
        BinaryField("flash_wipe_progress_percent", 144, "I", unit="%", min_version=3),
        BinaryField("flash_wipe_address", 148, "I", min_version=3),
        BinaryField("fsm_stack_free_bytes", 152, "I", unit="B", min_version=4),
        BinaryField("can_stack_free_bytes", 156, "I", unit="B", min_version=4),
        BinaryField("logger_stack_free_bytes", 160, "I", unit="B", min_version=4),
        BinaryField("rtos_heap_free_bytes", 164, "I", unit="B", min_version=4),
        BinaryField("mission_config_magic", 168, "I", enum=CROI_MISSION_CONFIG_STATUS, min_version=5),
        BinaryField("mission_config_schema_version", 172, "I", min_version=5),
        BinaryField("mission_config_crc32", 176, "I", min_version=5),
        BinaryField("sensor_sample_valid", 180, "I", enum=BOOL_STATUS, min_version=6),
        BinaryField("imu_read_failures", 184, "I", min_version=6),
        BinaryField("baro_read_failures", 188, "I", min_version=6),
        BinaryField("imu_last_ok_ms", 192, "I", unit="ms", min_version=6),
        BinaryField("baro_last_ok_ms", 196, "I", unit="ms", min_version=6),
        BinaryField("can_queue_drops", 200, "I", min_version=6),
        BinaryField("logger_queue_drops", 204, "I", min_version=6),
        BinaryField("state_transition_count", 208, "I", min_version=6),
        BinaryField("state_entry_ms", 212, "I", unit="ms", min_version=6),
        BinaryField("actuator_command_count", 216, "I", min_version=7),
        BinaryField("actuator_last_sequence", 220, "I", min_version=7),
        BinaryField("actuator_last_output", 224, "I", min_version=7),
        BinaryField("actuator_last_angle_deg", 228, "I", unit="deg", min_version=7),
        BinaryField("actuator_active", 232, "I", enum=BOOL_STATUS, min_version=7),
        BinaryField("fsm_task_heartbeat_ms", 236, "I", unit="ms", min_version=8),
        BinaryField("can_task_heartbeat_ms", 240, "I", unit="ms", min_version=8),
        BinaryField("logger_task_heartbeat_ms", 244, "I", unit="ms", min_version=8),
        BinaryField("watchdog_init_ok", 248, "I", enum=BOOL_STATUS, min_version=8),
        BinaryField("watchdog_refresh_count", 252, "I", min_version=8),
        BinaryField("watchdog_missed_count", 256, "I", min_version=8),
        BinaryField("reset_flags", 260, "I", min_version=8),
        BinaryField("pyro_arm_command_count", 264, "I", min_version=9),
        BinaryField("pyro_fire_command_count", 268, "I", min_version=9),
        BinaryField("pyro_ack_count", 272, "I", min_version=9),
        BinaryField("pyro_status_count", 276, "I", min_version=9),
        BinaryField("pyro_last_sequence", 280, "I", min_version=9),
        BinaryField("pyro_last_channel", 284, "I", min_version=9),
        BinaryField("pyro_last_result", 288, "I", min_version=9),
        BinaryField("pyro_last_fault", 292, "I", min_version=9),
        BinaryField("pyro_armed_mask", 296, "I", min_version=9),
        BinaryField("pyro_continuity_mask", 300, "I", min_version=9),
        BinaryField("pyro_fired_mask", 304, "I", min_version=9),
        BinaryField("pyro_critical_tx_drops", 308, "I", min_version=9),
        BinaryField("logger_startup_samples_skipped", 312, "I", min_version=10),
        BinaryField("main_fallback_triggered", 316, "I", min_version=11, enum=BOOL_STATUS),
        BinaryField("logger_free_bytes", 320, "I", unit="B", min_version=12),
        BinaryField("logger_required_bytes", 324, "I", unit="B", min_version=12),
        BinaryField("phase_candidate_mask", 328, "I", min_version=13),
        BinaryField("phase_confirmed_vote_mask", 332, "I", min_version=13),
        BinaryField("phase_gate_mask", 336, "I", min_version=13),
        BinaryField("phase_rejection_mask", 340, "I", min_version=13),
        BinaryField("phase_rejection_count", 344, "I", min_version=13),
        BinaryField("phase_last_transition_ms", 348, "I", unit="ms", min_version=13),
        BinaryField("phase_last_transition_vote_mask", 352, "I", min_version=13),
        BinaryField("phase_inertial_velocity_m_s", 356, "i", scale=0.01, unit="m/s", min_version=13),
        BinaryField("phase_baro_peak_altitude_m", 360, "i", scale=0.01, unit="m", min_version=13),
        BinaryField("phase_baro_velocity_m_s", 364, "i", scale=0.01, unit="m/s", min_version=13),
        BinaryField("phase_required_votes", 368, "B", min_version=13),
        BinaryField("phase_detector_mode", 369, "B", enum=CROI_PHASE_DETECTOR_MODE, min_version=13),
        BinaryField("phase_last_transition_reason", 370, "B", enum=CROI_PHASE_TRANSITION_REASON, min_version=13),
        BinaryField("phase_sensor_health_mask", 371, "B", min_version=13),
    ),
)


FOINSE_STATUS = StatusBlock(
    symbol="foinse_status",
    magic=0x464F494E,
    size=120,
    fields=(
        BinaryField("magic", 0, "I"),
        BinaryField("version", 4, "I"),
        BinaryField("uptime_ms", 8, "I", unit="ms"),
        BinaryField("loop_count", 12, "I"),
        BinaryField("adc_ok", 16, "I"),
        BinaryField("fault", 20, "I"),
        BinaryField("sense1_raw", 24, "I"),
        BinaryField("sense2_raw", 28, "I"),
        BinaryField("sense1_mv", 32, "I", label="bat sensor output", unit="mV"),
        BinaryField("sense2_mv", 36, "I", label="servo sensor output", unit="mV"),
        BinaryField("sense1_current_ma", 40, "i", label="battery current", unit="mA"),
        BinaryField("sense2_current_ma", 44, "i", label="servo current", unit="mA"),
        BinaryField("clock_hz", 48, "I", unit="Hz", min_version=3),
        BinaryField("clock_source", 52, "I", min_version=3),
        BinaryField("can_init_ok", 56, "I", enum=BOOL_STATUS, min_version=3),
        BinaryField("can_bus_off", 60, "I", enum=BOOL_STATUS, min_version=3),
        BinaryField("can_error", 64, "I", min_version=3),
        BinaryField("can_tx_count", 68, "I", min_version=3),
        BinaryField("can_rx_count", 72, "I", min_version=3),
        BinaryField("can_tx_drops", 76, "I", min_version=3),
        BinaryField("can_tx_queue_depth", 80, "I", min_version=3),
        BinaryField("heartbeat_tx_count", 84, "I", min_version=3),
        BinaryField("power_tx_count", 88, "I", min_version=3),
        BinaryField("can_esr", 92, "I", min_version=3),
        BinaryField("sense1_valid", 96, "I", label="battery sample valid", enum=BOOL_STATUS, min_version=4),
        BinaryField("sense2_valid", 100, "I", label="servo sample valid", enum=BOOL_STATUS, min_version=4),
        BinaryField("adc_error_count", 104, "I", min_version=4),
        BinaryField("watchdog_init_ok", 108, "I", enum=BOOL_STATUS, min_version=5),
        BinaryField("watchdog_refresh_count", 112, "I", min_version=5),
        BinaryField("reset_flags", 116, "I", min_version=5),
    ),
)


PLEASC_STATUS = StatusBlock(
    symbol="pleasc_status",
    magic=0x504C5343,
    size=188,
    fields=(
        BinaryField("magic", 0, "I"),
        BinaryField("version", 4, "I"),
        BinaryField("uptime_ms", 8, "I", unit="ms"),
        BinaryField("loop_count", 12, "I"),
        BinaryField("clock_hz", 16, "I", unit="Hz"),
        BinaryField("clock_source", 20, "I"),
        BinaryField("init_ok", 24, "I", enum=BOOL_STATUS),
        BinaryField("can_init_ok", 28, "I", enum=BOOL_STATUS),
        BinaryField("can_bus_off", 32, "I", enum=BOOL_STATUS),
        BinaryField("can_error", 36, "I"),
        BinaryField("can_tx_drops", 40, "I"),
        BinaryField("can_rx_count", 44, "I"),
        BinaryField("can_tx_count", 48, "I"),
        BinaryField("heartbeat_tx_count", 52, "I"),
        BinaryField("status_tx_count", 56, "I"),
        BinaryField("ack_tx_count", 60, "I"),
        BinaryField("armed_mask", 64, "I"),
        BinaryField("continuity_mask", 68, "I"),
        BinaryField("fire_pin_mask", 72, "I"),
        BinaryField("fault_latch", 76, "I"),
        BinaryField("last_fault", 80, "I"),
        BinaryField("last_channel", 84, "I"),
        BinaryField("fire_count", 88, "I"),
        BinaryField("rejected_count", 92, "I"),
        BinaryField("last_arm_ms", 96, "I", unit="ms"),
        BinaryField("last_fire_ms", 100, "I", unit="ms"),
        BinaryField("gpioa_idr", 104, "I"),
        BinaryField("gpiob_idr", 108, "I"),
        BinaryField("can_esr", 112, "I"),
        BinaryField("croi_last_seen_ms", 116, "I", unit="ms"),
        BinaryField("croi_timeout", 120, "I", enum=BOOL_STATUS),
        BinaryField("rejected_no_croi", 124, "I"),
        BinaryField("arm_expiry_count", 128, "I", min_version=2),
        BinaryField("can_rx_overruns", 132, "I", min_version=2),
        BinaryField("watchdog_refresh_count", 136, "I", min_version=2),
        BinaryField("fire_enabled", 140, "I", enum=BOOL_STATUS, min_version=3),
        BinaryField("fired_mask", 144, "I", min_version=4),
        BinaryField("last_command_sequence", 148, "I", min_version=4),
        BinaryField("mission_tag", 152, "I", min_version=4),
        BinaryField("croi_state", 156, "I", enum=CROI_FLIGHT_STATE, min_version=4),
        BinaryField("rejected_auth", 160, "I", min_version=4),
        BinaryField("rejected_replay", 164, "I", min_version=4),
        BinaryField("rejected_state", 168, "I", min_version=4),
        BinaryField("rejected_repeat", 172, "I", min_version=4),
        BinaryField("rejected_mission", 176, "I", min_version=4),
        BinaryField("arm_settle_rejects", 180, "I", min_version=4),
        BinaryField("rev1_accepted_risk", 184, "I", enum=BOOL_STATUS, min_version=4),
    ),
)
