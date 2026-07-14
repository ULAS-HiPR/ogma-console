from pathlib import Path

from ogma_app.boards import profile_for
from ogma_app.ui import (
    PYRO_CHANNEL_VALUES,
    OgmaApp,
    action_states,
    available_pyro_channel_values,
    is_croi_flash_result,
)


def test_pyro_channel_options_exclude_other_assignment() -> None:
    assert available_pyro_channel_values("Disabled") == PYRO_CHANNEL_VALUES
    assert "Channel 2" not in available_pyro_channel_values("Channel 2")
    assert "Channel 1" in available_pyro_channel_values("Channel 2")


def test_toolbar_hides_irrelevant_board_actions() -> None:
    croi = OgmaApp._visible_toolbar_keys("croi")
    lamh = OgmaApp._visible_toolbar_keys("lamh")
    groundstation = OgmaApp._visible_toolbar_keys("groundstation")
    assert "wipe_croi_flash" in croi
    assert "lamh_servo_test" not in croi
    assert "lamh_servo_test" in lamh
    assert "read_croi_flash" not in lamh
    assert "groundstation_usb" in groundstation


def test_action_states_for_croi() -> None:
    states = action_states(profile_for("croi"), has_latest_status=False, busy=False)
    assert states["build"] == "normal"
    assert states["flash"] == "normal"
    assert states["validate"] == "normal"
    assert states["read_status"] == "normal"
    assert states["health"] == "normal"
    assert states["read_croi_flash"] == "normal"
    assert states["wipe_croi_flash"] == "normal"
    assert states["import_groundstation"] == "disabled"
    assert states["foinse_monitor"] == "disabled"


def test_action_states_for_lamh() -> None:
    states = action_states(profile_for("lamh"), has_latest_status=True, busy=False)
    assert states["read_status"] == "normal"
    assert states["health"] == "normal"
    assert states["teachtaire_test"] == "disabled"
    assert states["lamh_servo_test"] == "normal"
    assert states["read_croi_flash"] == "disabled"
    assert states["wipe_croi_flash"] == "disabled"
    assert states["save_status"] == "normal"


def test_action_states_for_teachtaire() -> None:
    states = action_states(profile_for("teachtaire"), has_latest_status=False, busy=False)
    assert states["teachtaire_test"] == "normal"
    assert states["lamh_servo_test"] == "disabled"
    assert states["read_status"] == "normal"
    assert states["read_croi_flash"] == "disabled"


def test_action_states_for_groundstation_and_busy() -> None:
    states = action_states(profile_for("groundstation"), has_latest_status=False, busy=False)
    assert states["build"] == "disabled"
    assert states["import_groundstation"] == "normal"
    assert states["groundstation_usb"] == "normal"
    assert states["telemetry_usb"] == "normal"

    busy_states = action_states(profile_for("groundstation"), has_latest_status=True, busy=True)
    assert set(busy_states.values()) == {"disabled"}

    polling_states = action_states(profile_for("teachtaire"), has_latest_status=True, busy=True, polling=True)
    assert polling_states["stop_poll"] == "normal"
    assert polling_states["poll"] == "disabled"


def test_action_states_for_foinse() -> None:
    states = action_states(profile_for("foinse"), has_latest_status=False, busy=False)
    assert states["foinse_monitor"] == "normal"
    assert states["read_status"] == "normal"
    assert states["teachtaire_test"] == "disabled"


def test_croi_status_result_is_not_croi_flash_result() -> None:
    assert not is_croi_flash_result(("croi", "stm32f072c8t6", {"magic": 1129467721}))
    assert is_croi_flash_result(("croi", {"summary": {}}, Path("croi_flash.json")))


def test_croi_can_diagnosis_flags_no_ack() -> None:
    from ogma_app.ui import OgmaApp

    diagnosis = OgmaApp.croi_can_diagnosis(
        init_ok=1,
        bus_off=0,
        can_error=0,
        retry_depth=16,
        retry_drops=12,
        active_nodes=0,
    )

    assert "TX stuck/no ACK" in diagnosis


def test_croi_can_diagnosis_ok_with_peer() -> None:
    from ogma_app.ui import OgmaApp

    assert (
        OgmaApp.croi_can_diagnosis(
            init_ok=1,
            bus_off=0,
            can_error=0,
            retry_depth=0,
            retry_drops=0,
            active_nodes=2,
        )
        == "ok"
    )


def test_telemetry_series_uses_arrival_time_and_validity_flags() -> None:
    packets = [
        {
            "frame": "POWER_MAIN",
            "received_s": 1.25,
            "fields": {
                "flags": {"value": 0x01},
                "ibat_ma": {"value": 420},
                "vbat_mv": {"value": 7400},
            },
        }
    ]

    assert OgmaApp._telemetry_series(packets, "POWER_MAIN", "ibat_ma", valid_flag=0x01) == [(1.25, 420.0)]
    assert OgmaApp._telemetry_series(packets, "POWER_MAIN", "vbat_mv", valid_flag=0x02) == []


def test_telemetry_link_series_reports_rate_and_gap() -> None:
    packets = [
        {"frame": "BARO", "received_s": 0.5},
        {"frame": "BARO", "received_s": 1.0},
        {"frame": "BARO", "received_s": 1.5},
    ]

    rates, gaps = OgmaApp._telemetry_link_series(packets, ["BARO"])

    assert rates["BARO"][-1] == (1.5, 2.0)
    assert gaps["BARO"][-1] == (1.5, 500.0)
