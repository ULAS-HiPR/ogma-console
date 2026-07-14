from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mission_config import CROI_MISSION_CONFIG_SCHEMA_VERSION


@dataclass(frozen=True)
class HealthCheck:
    name: str
    state: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.state != "fail"


@dataclass(frozen=True)
class HealthReport:
    board_id: str
    checks: tuple[HealthCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def lines(self) -> list[str]:
        return [f"{check.state:4} {check.name:22} {check.detail}" for check in self.checks]


def evaluate_health(board_id: str, status: dict[str, Any]) -> HealthReport:
    if board_id == "croi":
        checks = _croi_health(status)
    elif board_id == "teachtaire":
        checks = _teachtaire_health(status)
    elif board_id == "lamh":
        checks = _lamh_health(status)
    elif board_id == "foinse":
        checks = _foinse_health(status)
    elif board_id == "pleasc":
        checks = _pleasc_health(status)
    else:
        checks = (HealthCheck("health", "warn", f"no health rules for {board_id}"),)
    return HealthReport(board_id, checks)


def _croi_health(status: dict[str, Any]) -> tuple[HealthCheck, ...]:
    checks = [
        _one_check(status, "init_ok", "board initialization"),
        _one_check(status, "imu_init_ok", "LSM6DSO32 initialized"),
        _one_check(status, "baro_init_ok", "MS5607 initialized"),
        _one_check(status, "can_init_ok", "CAN initialized"),
        _one_check(status, "flash_init_ok", "MX25 flash logger initialized"),
        _zero_check(status, "logger_fault_latched", "logger fault latched"),
        _zero_warn(status, "logger_logging_stopped", "logger stopped"),
        _zero_check(status, "can_bus_off", "CAN bus-off state"),
        _zero_warn(status, "can_error", "CAN driver error"),
        _zero_warn(status, "can_tx_retry_drops", "CAN TX drops"),
        _zero_warn(status, "can_node_timeout_count", "CAN node timeouts"),
        _positive_warn(status, "uptime_ms", "uptime increasing"),
    ]
    active_nodes = _int(status, "can_active_nodes")
    checks.append(HealthCheck("can_active_nodes", "ok" if active_nodes > 0 else "warn", f"{active_nodes} nodes seen"))
    if "sensor_sample_valid" in status:
        checks.extend(
            (
                _one_check(status, "sensor_sample_valid", "fresh IMU and barometer sample"),
                _zero_warn(status, "imu_read_failures", "IMU runtime read failures"),
                _zero_warn(status, "baro_read_failures", "barometer runtime read failures"),
                _zero_warn(status, "can_queue_drops", "FSM to CAN queue drops"),
                _zero_warn(status, "logger_queue_drops", "FSM to logger queue drops"),
            )
        )
    if "logger_startup_samples_skipped" in status:
        checks.append(
            _zero_warn(
                status,
                "logger_startup_samples_skipped",
                "samples produced before logger recovery completed",
            )
        )
    for key, detail in (
        ("fsm_stack_free_bytes", "FSM stack headroom"),
        ("can_stack_free_bytes", "CAN stack headroom"),
        ("logger_stack_free_bytes", "logger stack headroom"),
    ):
        if key in status:
            checks.append(_minimum_check(status, key, 256, detail))
    if "rtos_heap_free_bytes" in status:
        checks.append(_minimum_warn(status, "rtos_heap_free_bytes", 128, "RTOS heap headroom"))
    if "mission_config_magic" in status:
        magic = _int(status, "mission_config_magic")
        schema = _int(status, "mission_config_schema_version")
        state = (
            "ok"
            if magic == 0x4F474D43 and schema == CROI_MISSION_CONFIG_SCHEMA_VERSION
            else "fail"
        )
        checks.append(HealthCheck("mission_manifest", state, f"magic=0x{magic:08x} schema={schema}"))
    if "watchdog_init_ok" in status:
        checks.append(_one_check(status, "watchdog_init_ok", "hardware watchdog initialized"))
        checks.append(_zero_warn(status, "watchdog_missed_count", "watchdog supervision misses"))
        uptime = _int(status, "uptime_ms")
        for key, detail in (
            ("fsm_task_heartbeat_ms", "FSM task progress"),
            ("can_task_heartbeat_ms", "CAN task progress"),
            ("logger_task_heartbeat_ms", "logger task progress"),
        ):
            heartbeat = _int(status, key)
            age = _heartbeat_age_ms(uptime, heartbeat)
            state = "ok" if heartbeat > 0 and age <= 2000 else "fail"
            checks.append(HealthCheck(key, state, f"{detail}: age={age} ms"))
    if "pyro_critical_tx_drops" in status:
        checks.append(_zero_warn(status, "pyro_critical_tx_drops", "critical pyro TX drops"))
    return tuple(checks)


def _teachtaire_health(status: dict[str, Any]) -> tuple[HealthCheck, ...]:
    checks = [
        _zero_check(status, "fault", "firmware fault"),
        _one_check(status, "clock_ok", "clock initialized"),
        _one_check(status, "gpio_ok", "GPIO initialized"),
        _one_check(status, "spi_ok", "SPI initialized"),
        _one_check(status, "uart_ok", "UART initialized"),
        _one_check(status, "lora_init_ok", "LoRa initialized"),
        _zero_check(status, "lora_error", "LoRa driver error"),
    ]
    if "watchdog_init_ok" in status:
        checks.append(_one_check(status, "watchdog_init_ok", "hardware watchdog initialized"))
    tx_count = _int(status, "lora_tx_count")
    tx_done = _int(status, "lora_tx_done_count")
    if tx_count <= tx_done + 1:
        checks.append(HealthCheck("lora_tx_done", "ok", f"tx={tx_count} done={tx_done}"))
    else:
        checks.append(HealthCheck("lora_tx_done", "warn", f"tx={tx_count} done={tx_done}"))

    checks.append(_positive_warn(status, "gnss_bytes", "GNSS UART bytes seen"))
    checks.append(_positive_warn(status, "gnss_parsed", "GNSS sentences parsed"))
    checks.append(_zero_warn(status, "gnss_overflows", "GNSS parser overflows"))
    checks.append(_one_warn(status, "gnss_fix", "GNSS has fix"))
    sats = _int(status, "gnss_sats")
    checks.append(HealthCheck("gnss_sats", "ok" if sats >= 4 else "warn", f"{sats} satellites"))

    seen = _int(status, "gnss_seen")
    bad = _int(status, "gnss_checksum_bad")
    if seen == 0:
        checks.append(HealthCheck("gnss_checksum", "warn", "no GNSS sentences seen"))
    else:
        frac = bad / seen
        state = "ok" if frac <= 0.1 else "warn"
        checks.append(HealthCheck("gnss_checksum", state, f"{bad}/{seen} bad ({frac:.0%})"))
    if "can_init_ok" in status:
        checks.extend(
            (
                _one_check(status, "can_init_ok", "CAN initialized"),
                _zero_check(status, "can_bus_off", "CAN bus-off state"),
                _zero_warn(status, "can_error", "CAN driver error"),
                _zero_warn(status, "can_tx_drops", "CAN TX drops"),
                _zero_warn(status, "can_rx_overruns", "CAN RX overruns"),
                _zero_warn(status, "lora_tx_timeout_count", "LoRa TX timeouts"),
            )
        )
    if "telemetry_core_tx_count" in status:
        checks.append(_positive_warn(status, "telemetry_core_tx_count", "core telemetry packets sent"))
        checks.append(_positive_warn(status, "telemetry_gps_tx_count", "GPS telemetry packets sent"))
        checks.append(_zero_warn(status, "telemetry_event_drop_count", "radio event queue drops"))
    return tuple(checks)


def _lamh_health(status: dict[str, Any]) -> tuple[HealthCheck, ...]:
    checks = [
        _one_check(status, "pca9685_found", "PCA9685 found on I2C"),
        _zero_warn(status, "scan_last_error", "last scan I2C error"),
        _zero_warn(status, "i2c_last_error", "last I2C error"),
        _one_check(status, "can_init_ok", "CAN initialized"),
        _zero_check(status, "can_bus_off", "CAN bus-off state"),
        _zero_warn(status, "can_error", "CAN driver error"),
        _zero_warn(status, "can_tx_drops", "CAN TX drops"),
    ]
    stage = _int(status, "stage")
    if stage == 7:
        checks.append(HealthCheck("stage", "fail", "scan not found"))
    elif stage >= 20:
        checks.append(HealthCheck("stage", "ok", f"runtime stage {stage}"))
    else:
        checks.append(HealthCheck("stage", "warn", f"early stage {stage}"))

    address = _int(status, "pca9685_address")
    checks.append(HealthCheck("pca9685_address", "ok" if 0x40 <= address <= 0x7F else "warn", f"0x{address:02x}"))

    angle = _int(status, "servo_angle")
    checks.append(HealthCheck("servo_angle", "ok" if 0 <= angle <= 180 else "fail", f"{angle} deg"))
    return tuple(checks)


def _foinse_health(status: dict[str, Any]) -> tuple[HealthCheck, ...]:
    checks = [
        _one_check(status, "adc_ok", "ADC initialized"),
        _zero_check(status, "fault", "firmware fault"),
        _positive_warn(status, "loop_count", "main loop running"),
        _positive_warn(status, "uptime_ms", "uptime increasing"),
    ]
    if "sense1_valid" in status:
        checks.extend(
            (
                _one_check(status, "sense1_valid", "battery current sample valid"),
                _one_check(status, "sense2_valid", "servo current sample valid"),
                _zero_warn(status, "adc_error_count", "ADC conversion errors"),
            )
        )
    for name in ("sense1_raw", "sense2_raw"):
        value = _int(status, name)
        checks.append(HealthCheck(name, "ok" if 0 <= value <= 4095 else "fail", f"{value} counts"))
    for name in ("sense1_mv", "sense2_mv"):
        value = _int(status, name)
        checks.append(HealthCheck(name, "ok" if 0 <= value <= 3300 else "warn", f"{value} mV"))
    for name in ("sense1_current_ma", "sense2_current_ma"):
        value = _int(status, name)
        checks.append(HealthCheck(name, "ok" if -12000 <= value <= 12000 else "warn", f"{value} mA"))
    if "can_init_ok" in status:
        checks.extend(
            [
                _one_check(status, "can_init_ok", "CAN initialized"),
                _zero_check(status, "can_bus_off", "CAN bus-off state"),
                _zero_warn(status, "can_error", "CAN driver error"),
                _zero_warn(status, "can_tx_drops", "CAN TX drops"),
            ]
        )
    if "watchdog_init_ok" in status:
        checks.append(_one_check(status, "watchdog_init_ok", "hardware watchdog initialized"))
    return tuple(checks)


def _pleasc_health(status: dict[str, Any]) -> tuple[HealthCheck, ...]:
    checks = [
        _one_check(status, "init_ok", "board initialization"),
        _one_check(status, "can_init_ok", "CAN initialized"),
        _zero_check(status, "can_bus_off", "CAN bus-off state"),
        _zero_warn(status, "can_error", "CAN driver error"),
        _zero_warn(status, "can_tx_drops", "CAN TX drops"),
        _zero_warn(status, "fault_latch", "pyro fault latch"),
        _zero_warn(status, "fire_pin_mask", "fire outputs inactive"),
        _zero_warn(status, "croi_timeout", "Croí heartbeat timeout"),
        _zero_warn(status, "can_rx_overruns", "CAN RX overruns"),
        _positive_warn(status, "uptime_ms", "uptime increasing"),
    ]
    if "fire_enabled" in status:
        enabled = _int(status, "fire_enabled")
        checks.append(HealthCheck(
            "fire_enabled",
            "warn" if enabled else "ok",
            "Rev1 accepted-risk firing image" if enabled else "firing locked",
        ))
        if enabled and "rev1_accepted_risk" in status:
            checks.append(_one_check(status, "rev1_accepted_risk", "explicit Rev1 release gate"))
    for key, detail in (
        ("rejected_auth", "command tag rejects"),
        ("rejected_replay", "stale/replayed command rejects"),
        ("rejected_state", "flight-state rejects"),
        ("rejected_repeat", "repeat-fire rejects"),
        ("rejected_mission", "mission mismatch rejects"),
        ("arm_settle_rejects", "arm-settle rejects"),
    ):
        if key in status:
            checks.append(_zero_warn(status, key, detail))
    return tuple(checks)


def _one_check(status: dict[str, Any], key: str, detail: str) -> HealthCheck:
    value = _int(status, key)
    return HealthCheck(key, "ok" if value == 1 else "fail", f"{detail}: {value}")


def _one_warn(status: dict[str, Any], key: str, detail: str) -> HealthCheck:
    value = _int(status, key)
    return HealthCheck(key, "ok" if value == 1 else "warn", f"{detail}: {value}")


def _zero_check(status: dict[str, Any], key: str, detail: str) -> HealthCheck:
    value = _int(status, key)
    return HealthCheck(key, "ok" if value == 0 else "fail", f"{detail}: {value}")


def _zero_warn(status: dict[str, Any], key: str, detail: str) -> HealthCheck:
    value = _int(status, key)
    return HealthCheck(key, "ok" if value == 0 else "warn", f"{detail}: {value}")


def _positive_warn(status: dict[str, Any], key: str, detail: str) -> HealthCheck:
    value = _int(status, key)
    return HealthCheck(key, "ok" if value > 0 else "warn", f"{detail}: {value}")


def _minimum_warn(status: dict[str, Any], key: str, minimum: int, detail: str) -> HealthCheck:
    value = _int(status, key)
    state = "ok" if value >= minimum else "warn"
    return HealthCheck(key, state, f"{detail}: {value} B")


def _minimum_check(status: dict[str, Any], key: str, minimum: int, detail: str) -> HealthCheck:
    value = _int(status, key)
    state = "ok" if value > minimum else "fail"
    return HealthCheck(key, state, f"{detail}: {value} B")


def _heartbeat_age_ms(uptime: int, heartbeat: int) -> int:
    if heartbeat > uptime and heartbeat - uptime <= 1000:
        return 0
    return (uptime - heartbeat) & 0xFFFFFFFF


def _int(status: dict[str, Any], key: str) -> int:
    try:
        return int(status.get(key, 0))
    except (TypeError, ValueError):
        return 0
