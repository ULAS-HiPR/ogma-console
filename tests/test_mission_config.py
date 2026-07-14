import json
import zlib
from pathlib import Path

import pytest

from ogma_app.mission_config import (
    CROI_MISSION_CONFIG_MAGIC,
    LoggingPolicy,
    MissionConfig,
    build_mission_timeline,
    load_croi_mission_config,
    load_mission_json,
    render_croi_mission_header,
    save_mission_json,
    write_croi_mission_header,
)


def test_logging_policy_budgets_configured_flight_window() -> None:
    policy = LoggingPolicy()

    assert policy.mode == "flight_window"
    assert policy.required_capacity_bytes() == 2_570_400
    policy.validate()


def test_logging_policy_rejects_window_larger_than_flash() -> None:
    with pytest.raises(ValueError, match="flash capacity"):
        LoggingPolicy(
            flight_sample_period_ms=20,
            minimum_flight_ms=7_200_000,
            post_landing_ms=600_000,
        ).validate()


def test_mission_config_header_and_json_round_trip(tmp_path) -> None:
    config = MissionConfig.from_values(
        name="flight-a",
        liftoff_accel_m_s2=18.5,
        imu_vertical_axis=2,
        imu_vertical_sign=1,
        main_deploy_altitude_m=350,
        drogue_delay_ms=1500,
        airbrake_enabled=True,
        airbrake_channel=2,
        airbrake_retracted_angle_deg=0,
        airbrake_max_angle_deg=75,
        airbrake_start_delay_ms=250,
        airbrake_stow_delay_ms=5000,
        airbrake_command_timeout_ms=500,
        pyro_drogue_channel=0,
        pyro_main_channel=1,
    )
    header = write_croi_mission_header(tmp_path / "croi_mission_config.h", config)
    mission_json = save_mission_json(tmp_path / "missions", config)

    loaded_header = load_croi_mission_config(header)
    loaded_json = load_mission_json(mission_json)
    assert loaded_header.liftoff_accel_m_s2 == 18.5
    assert loaded_header.imu_vertical_axis == 2
    assert loaded_header.imu_vertical_sign == 1
    assert loaded_header.pyro_drogue_channel == 0
    assert loaded_header.pyro_main_channel == 1
    assert loaded_json == config
    assert f"0x{config.crc32():08X}" in render_croi_mission_header(config)


@pytest.mark.parametrize(
    "overrides",
    (
        {"airbrake_max_angle_deg": 91},
        {"airbrake_enabled": True, "airbrake_start_delay_ms": 1000, "airbrake_stow_delay_ms": 1000},
        {"airbrake_command_timeout_ms": 50},
        {"pyro_drogue_channel": 2, "pyro_main_channel": 2},
        {"liftoff_accel_m_s2": 0.5},
        {"imu_vertical_axis": 3},
        {"imu_vertical_sign": 0},
    ),
)
def test_mission_config_rejects_invalid_values(overrides) -> None:
    values = MissionConfig.defaults().canonical_dict()
    values.update(overrides)
    with pytest.raises(ValueError):
        MissionConfig.from_values(**values)


def test_mission_json_rejects_modified_payload(tmp_path) -> None:
    config = MissionConfig.defaults()
    path = save_mission_json(tmp_path, config)
    payload = json.loads(path.read_text())
    payload["mission"]["main_deploy_altitude_m"] = 999
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="CRC"):
        load_mission_json(path)


def test_schema_two_mission_crc_is_checked_before_timed_stow_migration(tmp_path) -> None:
    mission = MissionConfig.defaults().canonical_dict()
    mission.pop("airbrake_stow_delay_ms")
    encoded = json.dumps(
        {key: value for key, value in mission.items() if key != "name"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    payload = {
        "schema_version": 2,
        "mission_crc32": f"{zlib.crc32(encoded) & 0xFFFFFFFF:08x}",
        "mission": mission,
    }
    path = tmp_path / "schema2.json"
    path.write_text(json.dumps(payload))

    migrated = load_mission_json(path)
    assert migrated.airbrake_stow_delay_ms == 120000


def test_mission_magic_is_stable() -> None:
    assert CROI_MISSION_CONFIG_MAGIC == 0x4F474D43


def test_mission_timeline_matches_enabled_outputs() -> None:
    values = MissionConfig.defaults().canonical_dict()
    values.update(
        airbrake_enabled=True,
        airbrake_channel=0,
        airbrake_retracted_angle_deg=10,
        airbrake_max_angle_deg=70,
        airbrake_start_delay_ms=1500,
        airbrake_stow_delay_ms=9000,
        pyro_drogue_channel=2,
        pyro_main_channel=3,
    )
    timeline = build_mission_timeline(MissionConfig.from_values(**values))

    assert any(event.trigger == "T+1.5 s" and "output 1 -> 70 deg" in event.action for event in timeline)
    assert any("Fire drogue on Pleasc channel 2" == event.action for event in timeline)
    assert any("Fire main on Pleasc channel 3" == event.action for event in timeline)


def test_mission_timeline_states_disabled_outputs_explicitly() -> None:
    timeline = build_mission_timeline(MissionConfig.defaults())

    assert any(event.action == "No actuator commands" for event in timeline)
    assert any(event.action == "Drogue output disabled" for event in timeline)
    assert any(event.action == "Main output disabled" for event in timeline)


def test_checked_in_croi_mission_header_matches_its_manifest_crc() -> None:
    header = Path(__file__).resolve().parents[2] / "croi" / "firmware" / "include" / "croi_mission_config.h"
    assert load_croi_mission_config(header).crc32() == 0x44A2AA23
    assert header.read_text(encoding="utf-8") == render_croi_mission_header(MissionConfig.defaults())
