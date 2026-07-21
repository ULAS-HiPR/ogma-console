from dataclasses import replace

from ogma_app.flight_manifest import FlightManifest
from ogma_app.mission_config import MissionConfig
from ogma_app.preflight import StatusEvidence, evaluate_preflight
from ogma_app.teachtaire_config import radio_config_crc32


def _croi_status(manifest: FlightManifest) -> dict[str, int]:
    return {
        "init_ok": 1,
        "imu_init_ok": 1,
        "baro_init_ok": 1,
        "can_init_ok": 1,
        "flash_init_ok": 1,
        "logger_fault_latched": 0,
        "logger_logging_stopped": 0,
        "can_bus_off": 0,
        "can_error": 0,
        "sensor_sample_valid": 1,
        "watchdog_init_ok": 1,
        "mission_config_magic": 0x4F474D43,
        "mission_config_schema_version": 7,
        "mission_config_crc32": manifest.mission.crc32(
            manifest.recovery,
            manifest.logging,
            manifest.detection,
        ),
        "logger_free_bytes": 16 * 1024 * 1024,
        "logger_required_bytes": manifest.logging.required_capacity_bytes(),
        "pyro_continuity_mask": 0,
        "pyro_fired_mask": 0,
        "pyro_critical_tx_drops": 0,
    }


def _telemetry(nodes=("croi", "teachtaire", "lamh", "foinse")) -> dict:
    return {
        "summary": {"elapsed_s": 10.0},
        "can": {
            "stack": {
                "nodes": {
                    node: {"received_s": 9.0, "err": 0}
                    for node in nodes
                }
            }
        },
        "groundstation": {"records": [{"fix": True, "received_s": 9.5}]},
    }


def _statuses(manifest: FlightManifest, croi: dict[str, int] | None = None) -> dict[str, StatusEvidence]:
    return {
        "croi": StatusEvidence(croi or _croi_status(manifest), 0.5),
        "teachtaire": StatusEvidence(
            {
                "lora_init_ok": 1,
                "can_init_ok": 1,
                "watchdog_init_ok": 1,
                "radio_config_magic": 0x54435243,
                "radio_config_schema_version": 1,
                "radio_config_crc32": radio_config_crc32(manifest.radio),
            },
            0.5,
        ),
    }


def test_preflight_go_with_fresh_complete_evidence() -> None:
    manifest = FlightManifest.defaults()
    report = evaluate_preflight(
        manifest,
        _statuses(manifest),
        _telemetry(),
        0.1,
    )

    assert report.go
    assert report.failures == 0


def test_preflight_fails_missing_required_node() -> None:
    manifest = FlightManifest.defaults()
    report = evaluate_preflight(
        manifest,
        _statuses(manifest),
        _telemetry(("croi", "teachtaire", "foinse")),
        0.1,
    )

    assert not report.go
    assert any(check.name == "lamh" and check.state == "fail" for check in report.checks)


def test_preflight_fails_mission_crc_mismatch() -> None:
    manifest = FlightManifest.defaults()
    status = _croi_status(manifest)
    status["mission_config_crc32"] ^= 1

    report = evaluate_preflight(
        manifest,
        _statuses(manifest, status),
        _telemetry(),
        0.1,
    )

    assert not report.go
    assert any(check.name == "mission CRC" and check.state == "fail" for check in report.checks)


def test_preflight_fails_insufficient_flight_log_capacity() -> None:
    manifest = FlightManifest.defaults()
    status = _croi_status(manifest)
    status["logger_free_bytes"] = status["logger_required_bytes"] - 1

    report = evaluate_preflight(
        manifest,
        _statuses(manifest, status),
        _telemetry(),
        0.1,
    )

    assert not report.go
    assert any(check.name == "flight log capacity" and check.state == "fail" for check in report.checks)


def test_preflight_requires_pyro_continuity_and_pleasc() -> None:
    mission = replace(MissionConfig.defaults(), pyro_drogue_channel=0, pyro_main_channel=1)
    manifest = replace(FlightManifest.defaults(), mission=mission)
    status = _croi_status(manifest)
    status["pyro_continuity_mask"] = 0x01

    report = evaluate_preflight(
        manifest,
        _statuses(manifest, status),
        _telemetry(("croi", "teachtaire", "lamh", "foinse", "pleasc")),
        0.1,
    )

    assert not report.go
    assert any(check.name == "pyro continuity" and check.state == "fail" for check in report.checks)


def test_preflight_fails_stale_heartbeat() -> None:
    manifest = FlightManifest.defaults()
    telemetry = _telemetry()
    telemetry["can"]["stack"]["nodes"]["foinse"]["received_s"] = 0.0

    report = evaluate_preflight(
        manifest,
        _statuses(manifest),
        telemetry,
        0.1,
    )

    assert not report.go
    assert any(check.name == "foinse" and check.state == "fail" for check in report.checks)


def test_preflight_fails_stale_teachtaire_swd_evidence() -> None:
    manifest = FlightManifest.defaults()
    statuses = _statuses(manifest)
    statuses["teachtaire"] = replace(statuses["teachtaire"], age_s=30.0)

    report = evaluate_preflight(manifest, statuses, _telemetry(), 0.1)

    assert not report.go
    assert any(
        check.category == "Teachtaire" and check.name == "status age" and check.state == "fail"
        for check in report.checks
    )
