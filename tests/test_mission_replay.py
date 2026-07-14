from dataclasses import replace

from ogma_app.flight_manifest import FlightManifest
from ogma_app.mission_config import RecoveryFallbackConfig
from ogma_app.mission_replay import (
    ReplaySample,
    load_replay_csv,
    run_firmware_replay,
    synthetic_nominal_profile,
)


def test_native_replay_runs_exact_nominal_state_sequence() -> None:
    result = run_firmware_replay(FlightManifest.defaults(), synthetic_nominal_profile())

    assert [point.state for point in result.transitions] == [2, 3, 4, 5, 6]
    assert not result.main_backup_triggered


def test_native_replay_reports_main_backup_transition() -> None:
    base = FlightManifest.defaults()
    manifest = replace(
        base,
        mission=replace(base.mission, main_deploy_altitude_m=0, pyro_main_channel=1),
        recovery=RecoveryFallbackConfig(
            main_backup_enabled=True,
            after_apogee_ms=500,
            descent_speed_m_s=30.0,
            min_altitude_m=100,
            max_altitude_m=2000,
            required_samples=3,
        ),
    )
    samples = []
    for time_ms in range(0, 1800, 100):
        if time_ms < 300:
            acceleration, velocity = 30.0, 20.0
        elif time_ms < 600:
            acceleration, velocity = -1.0, 20.0
        elif time_ms < 900:
            acceleration, velocity = 0.0, -1.0
        else:
            acceleration, velocity = 0.0, -40.0
        samples.append(ReplaySample(time_ms, acceleration, velocity, 1000.0))

    result = run_firmware_replay(manifest, samples)

    assert result.main_backup_triggered
    assert result.transitions[-1].state == 5


def test_replay_csv_accepts_croi_flash_column_names(tmp_path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text(
        "timestamp_ms,prediction_acceleration_m_s2,prediction_velocity_m_s,prediction_altitude_m\n"
        "0,0,0,0\n"
        "100,25,2,1\n",
        encoding="utf-8",
    )

    samples = load_replay_csv(path)

    assert samples[-1] == ReplaySample(100, 25.0, 2.0, 1.0)
