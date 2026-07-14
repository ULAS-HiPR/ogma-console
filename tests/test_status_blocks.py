import struct

import pytest

from ogma_app.status_blocks import (
    CROI_STATUS,
    FOINSE_STATUS,
    LAMH_SERVO_DEBUG,
    PLEASC_STATUS,
    TEACHTAIRE_STATUS,
    BinaryField,
    StatusBlock,
)


def test_status_block_rejects_fields_beyond_declared_size() -> None:
    with pytest.raises(ValueError, match="smaller than field extent"):
        StatusBlock("bad", 0, 4, (BinaryField("value", 4, "I"),))


def test_teachtaire_status_parse_minimal() -> None:
    data = bytearray(TEACHTAIRE_STATUS.size)
    struct.pack_into("<I", data, 0, TEACHTAIRE_STATUS.magic)
    struct.pack_into("<I", data, 4, 42)
    struct.pack_into("<i", data, 88, 531234567)
    parsed = TEACHTAIRE_STATUS.parse(bytes(data))
    assert parsed["loops"] == 42
    assert abs(parsed["gnss_latitude_e7"] - 53.1234567) < 0.000001


def test_teachtaire_status_parse_watchdog_extension() -> None:
    data = bytearray(TEACHTAIRE_STATUS.size)
    struct.pack_into("<I", data, 0, TEACHTAIRE_STATUS.magic)
    struct.pack_into("<B", data, 87, 3)
    struct.pack_into("<I", data, 216, 1)
    struct.pack_into("<I", data, 220, 77)
    parsed = TEACHTAIRE_STATUS.parse(bytes(data))
    assert parsed["watchdog_init_ok"] == 1
    assert parsed["watchdog_refresh_count"] == 77


def test_lamh_status_parse_minimal() -> None:
    data = bytearray(LAMH_SERVO_DEBUG.size)
    struct.pack_into("<I", data, 0, LAMH_SERVO_DEBUG.magic)
    struct.pack_into("<I", data, 4, 6)
    struct.pack_into("<h", data, 68, 90)
    struct.pack_into("<I", data, 76, 1)
    struct.pack_into("<I", data, 96, 3)
    struct.pack_into("<I", data, 124, 0x4C534346)
    struct.pack_into("<H", data, 128, 1)
    struct.pack_into("<h", data, 132, 72)
    struct.pack_into("<h", data, 138, 108)
    struct.pack_into("<I", data, 140, 1)
    parsed = LAMH_SERVO_DEBUG.parse(bytes(data))
    assert parsed["stage"] == 6
    assert parsed["servo_angle"] == 90
    assert parsed["can_init_ok"] == 1
    assert parsed["can_tx_drops"] == 3
    assert parsed["safety_config_magic"] == 0x4C534346
    assert parsed["safety_config_version"] == 1
    assert parsed["safe_angle_pwm1_deg"] == 72
    assert parsed["safe_angle_pwm4_deg"] == 108
    assert parsed["arm_input_active"] == 1


def test_foinse_status_parse_minimal() -> None:
    data = bytearray(FOINSE_STATUS.size)
    struct.pack_into("<I", data, 0, FOINSE_STATUS.magic)
    struct.pack_into("<I", data, 32, 1234)
    struct.pack_into("<i", data, 40, -3152)
    parsed = FOINSE_STATUS.parse(bytes(data))
    assert parsed["sense1_mv"] == 1234
    assert parsed["sense1_current_ma"] == -3152


def test_foinse_status_parse_can_extension() -> None:
    data = bytearray(FOINSE_STATUS.size)
    struct.pack_into("<I", data, 0, FOINSE_STATUS.magic)
    struct.pack_into("<I", data, 4, 3)
    struct.pack_into("<I", data, 56, 1)
    struct.pack_into("<I", data, 68, 44)
    parsed = FOINSE_STATUS.parse(bytes(data))
    assert parsed["can_init_ok"] == 1
    assert parsed["can_tx_count"] == 44


def test_foinse_status_parse_watchdog_extension() -> None:
    data = bytearray(FOINSE_STATUS.size)
    struct.pack_into("<I", data, 0, FOINSE_STATUS.magic)
    struct.pack_into("<I", data, 4, 5)
    struct.pack_into("<I", data, 108, 1)
    struct.pack_into("<I", data, 112, 88)
    parsed = FOINSE_STATUS.parse(bytes(data))
    assert parsed["watchdog_init_ok"] == 1
    assert parsed["watchdog_refresh_count"] == 88


def test_teachtaire_status_parse_telemetry_extension() -> None:
    data = bytearray(TEACHTAIRE_STATUS.size)
    struct.pack_into("<I", data, 0, TEACHTAIRE_STATUS.magic)
    struct.pack_into("<B", data, 87, 4)
    struct.pack_into("<I", data, 228, 11)
    struct.pack_into("<I", data, 240, 2)
    parsed = TEACHTAIRE_STATUS.parse(bytes(data))
    assert parsed["telemetry_core_tx_count"] == 11
    assert parsed["telemetry_event_tx_count"] == 2


def test_pleasc_status_parse_minimal() -> None:
    data = bytearray(PLEASC_STATUS.size)
    struct.pack_into("<I", data, 0, PLEASC_STATUS.magic)
    struct.pack_into("<I", data, 4, 1)
    struct.pack_into("<I", data, 28, 1)
    struct.pack_into("<I", data, 120, 1)
    parsed = PLEASC_STATUS.parse(bytes(data))
    assert parsed["can_init_ok"] == 1
    assert parsed["croi_timeout"] == 1


def test_pleasc_status_parse_fire_lock_extension() -> None:
    data = bytearray(PLEASC_STATUS.size)
    struct.pack_into("<I", data, 0, PLEASC_STATUS.magic)
    struct.pack_into("<I", data, 4, 3)
    struct.pack_into("<I", data, 140, 0)
    parsed = PLEASC_STATUS.parse(bytes(data))
    assert parsed["fire_enabled"] == 0


def test_pleasc_status_parse_rev1_safety_extension() -> None:
    data = bytearray(PLEASC_STATUS.size)
    struct.pack_into("<I", data, 0, PLEASC_STATUS.magic)
    struct.pack_into("<I", data, 4, 4)
    struct.pack_into("<I", data, 144, 3)
    struct.pack_into("<I", data, 148, 42)
    struct.pack_into("<I", data, 184, 1)
    parsed = PLEASC_STATUS.parse(bytes(data))
    assert parsed["fired_mask"] == 3
    assert parsed["last_command_sequence"] == 42
    assert parsed["rev1_accepted_risk"] == 1


def test_croi_status_parse_pyro_extension() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 9)
    struct.pack_into("<I", data, 268, 2)
    struct.pack_into("<I", data, 304, 1)
    parsed = CROI_STATUS.parse(bytes(data))
    assert parsed["pyro_fire_command_count"] == 2
    assert parsed["pyro_fired_mask"] == 1


def test_croi_status_parse_logger_startup_extension() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 10)
    struct.pack_into("<I", data, 312, 7)
    parsed = CROI_STATUS.parse(bytes(data))

    assert parsed["logger_startup_samples_skipped"] == 7


def test_croi_status_parse_main_fallback_extension() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 11)
    struct.pack_into("<I", data, 316, 1)
    parsed = CROI_STATUS.parse(bytes(data))

    assert parsed["main_fallback_triggered"] == 1


def test_croi_status_parse_minimal() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 5)
    struct.pack_into("<I", data, 16, 1)
    struct.pack_into("<I", data, 72, 2)
    struct.pack_into("<i", data, 100, 1234)
    struct.pack_into("<i", data, 124, -987)
    struct.pack_into("<I", data, 152, 768)
    struct.pack_into("<I", data, 168, 0x4F474D43)
    struct.pack_into("<I", data, 176, 0xAABBCCDD)
    parsed = CROI_STATUS.parse(bytes(data))
    assert parsed["imu_init_ok"] == 1
    assert parsed["can_tx_retry_drops"] == 2
    assert abs(parsed["baro_altitude_m"] - 12.34) < 0.000001
    assert abs(parsed["imu_accel_z_g"] - -0.987) < 0.000001
    assert parsed["fsm_stack_free_bytes"] == 768
    assert parsed["mission_config_magic"] == 0x4F474D43
    assert parsed["mission_config_crc32"] == 0xAABBCCDD


def test_croi_status_hides_fields_newer_than_firmware_version() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 2)
    struct.pack_into("<I", data, 140, 0xFFFFFFFF)
    parsed = CROI_STATUS.parse(bytes(data))
    assert parsed["version"] == 2
    assert "flight_state" in parsed
    assert "flash_wipe_state" not in parsed
    assert "flash_wipe_progress_percent" not in parsed


def test_croi_status_parses_actuator_extension() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 7)
    struct.pack_into("<I", data, 216, 55)
    struct.pack_into("<I", data, 220, 13)
    struct.pack_into("<I", data, 224, 2)
    struct.pack_into("<I", data, 228, 75)
    struct.pack_into("<I", data, 232, 1)
    parsed = CROI_STATUS.parse(bytes(data))
    assert parsed["actuator_command_count"] == 55
    assert parsed["actuator_last_sequence"] == 13
    assert parsed["actuator_last_output"] == 2
    assert parsed["actuator_last_angle_deg"] == 75
    assert parsed["actuator_active"] == 1


def test_croi_status_parses_watchdog_extension() -> None:
    data = bytearray(CROI_STATUS.size)
    struct.pack_into("<I", data, 0, CROI_STATUS.magic)
    struct.pack_into("<I", data, 4, 8)
    struct.pack_into("<I", data, 236, 1000)
    struct.pack_into("<I", data, 240, 1010)
    struct.pack_into("<I", data, 244, 1020)
    struct.pack_into("<I", data, 248, 1)
    struct.pack_into("<I", data, 252, 99)
    struct.pack_into("<I", data, 260, 0x20000000)
    parsed = CROI_STATUS.parse(bytes(data))
    assert parsed["fsm_task_heartbeat_ms"] == 1000
    assert parsed["can_task_heartbeat_ms"] == 1010
    assert parsed["logger_task_heartbeat_ms"] == 1020
    assert parsed["watchdog_init_ok"] == 1
    assert parsed["watchdog_refresh_count"] == 99
    assert parsed["reset_flags"] == 0x20000000
