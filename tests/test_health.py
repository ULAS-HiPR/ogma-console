from ogma_app.health import evaluate_health


def test_foinse_health_ok() -> None:
    report = evaluate_health(
        "foinse",
        {
            "adc_ok": 1,
            "fault": 0,
            "loop_count": 10,
            "uptime_ms": 1000,
            "sense1_raw": 2048,
            "sense2_raw": 1024,
            "sense1_mv": 1650,
            "sense2_mv": 825,
            "sense1_current_ma": 0,
            "sense2_current_ma": -6250,
        },
    )
    assert report.ok
    assert {check.name: check.state for check in report.checks}["adc_ok"] == "ok"


def test_pleasc_health_warns_on_croi_timeout() -> None:
    report = evaluate_health(
        "pleasc",
        {
            "init_ok": 1,
            "can_init_ok": 1,
            "can_bus_off": 0,
            "can_error": 0,
            "can_tx_drops": 0,
            "fault_latch": 0,
            "fire_pin_mask": 0,
            "croi_timeout": 1,
            "uptime_ms": 1000,
        },
    )
    states = {check.name: check.state for check in report.checks}
    assert states["croi_timeout"] == "warn"
    assert report.ok


def test_pleasc_health_warns_if_rev1_fire_enabled() -> None:
    report = evaluate_health(
        "pleasc",
        {
            "init_ok": 1,
            "can_init_ok": 1,
            "can_bus_off": 0,
            "can_error": 0,
            "can_tx_drops": 0,
            "fault_latch": 0,
            "fire_pin_mask": 0,
            "croi_timeout": 0,
            "uptime_ms": 1000,
            "fire_enabled": 1,
            "rev1_accepted_risk": 1,
        },
    )
    states = {check.name: check.state for check in report.checks}
    assert states["fire_enabled"] == "warn"
    assert states["rev1_accepted_risk"] == "ok"
    assert report.ok


def test_croi_health_ok() -> None:
    report = evaluate_health(
        "croi",
        {
            "init_ok": 1,
            "imu_init_ok": 1,
            "baro_init_ok": 1,
            "can_init_ok": 1,
            "flash_init_ok": 1,
            "logger_fault_latched": 0,
            "logger_logging_stopped": 0,
            "can_bus_off": 0,
            "can_error": 0,
            "can_tx_retry_drops": 0,
            "can_node_timeout_count": 0,
            "uptime_ms": 1000,
            "can_active_nodes": 2,
        },
    )
    assert report.ok


def test_croi_health_checks_mission_manifest_when_present() -> None:
    report = evaluate_health(
        "croi",
        {
            "mission_config_magic": 0x4F474D43,
            "mission_config_schema_version": 7,
        },
    )

    assert any(check.name == "mission_manifest" and check.state == "ok" for check in report.checks)


def test_croi_health_fails_task_stack_below_hil_minimum() -> None:
    report = evaluate_health("croi", {"logger_stack_free_bytes": 168})
    states = {check.name: check.state for check in report.checks}

    assert states["logger_stack_free_bytes"] == "fail"


def test_croi_health_tolerates_atomic_snapshot_heartbeat_skew() -> None:
    report = evaluate_health(
        "croi",
        {
            "uptime_ms": 1000,
            "watchdog_init_ok": 1,
            "watchdog_missed_count": 0,
            "fsm_task_heartbeat_ms": 1005,
            "can_task_heartbeat_ms": 1005,
            "logger_task_heartbeat_ms": 1005,
        },
    )
    states = {check.name: check.state for check in report.checks}

    assert states["fsm_task_heartbeat_ms"] == "ok"
    assert states["can_task_heartbeat_ms"] == "ok"
    assert states["logger_task_heartbeat_ms"] == "ok"


def test_lamh_health_fails_without_pca9685() -> None:
    report = evaluate_health(
        "lamh",
        {
            "pca9685_found": 0,
            "scan_last_error": 0,
            "i2c_last_error": 0,
            "stage": 7,
            "pca9685_address": 0x40,
            "servo_angle": 90,
        },
    )
    states = {check.name: check.state for check in report.checks}
    assert states["pca9685_found"] == "fail"
    assert states["stage"] == "fail"
    assert not report.ok


def test_teachtaire_health_warns_without_gnss_fix() -> None:
    report = evaluate_health(
        "teachtaire",
        {
            "fault": 0,
            "clock_ok": 1,
            "gpio_ok": 1,
            "spi_ok": 1,
            "uart_ok": 1,
            "lora_init_ok": 1,
            "lora_error": 0,
            "lora_tx_count": 2,
            "lora_tx_done_count": 2,
            "gnss_bytes": 100,
            "gnss_parsed": 10,
            "gnss_overflows": 0,
            "gnss_fix": 0,
            "gnss_sats": 2,
            "gnss_seen": 10,
            "gnss_checksum_bad": 0,
        },
    )
    states = {check.name: check.state for check in report.checks}
    assert states["gnss_fix"] == "warn"
    assert states["gnss_sats"] == "warn"
    assert report.ok


def test_teachtaire_health_fails_bad_bus_init() -> None:
    report = evaluate_health(
        "teachtaire",
        {
            "fault": 0,
            "clock_ok": 1,
            "gpio_ok": 1,
            "spi_ok": 0,
            "uart_ok": 1,
            "lora_init_ok": 0,
            "lora_error": 5,
        },
    )
    states = {check.name: check.state for check in report.checks}
    assert states["spi_ok"] == "fail"
    assert states["lora_init_ok"] == "fail"
    assert states["lora_error"] == "fail"
    assert not report.ok
