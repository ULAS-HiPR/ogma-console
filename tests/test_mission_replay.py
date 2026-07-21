from dataclasses import replace

from ogma_app.flight_manifest import FlightManifest
from ogma_app.mission_config import PhaseDetectionConfig, RecoveryFallbackConfig
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
        detection=PhaseDetectionConfig(
            liftoff_confirm_ms=100,
            burnout_min_powered_ms=100,
            burnout_confirm_ms=100,
            burnout_timeout_ms=2000,
            apogee_min_coast_ms=500,
            apogee_confirm_ms=100,
            apogee_single_sensor_confirm_ms=100,
            apogee_baro_descent_m=0.5,
            apogee_high_speed_lockout_m_s=300.0,
            apogee_timeout_ms=5000,
        ),
    )
    samples = []
    for time_ms in range(0, 1800, 100):
        if time_ms < 300:
            acceleration, velocity = 30.0, 20.0
            altitude = 1000.0
        elif time_ms < 500:
            acceleration, velocity = -5.0, 20.0
            altitude = 1000.0
        elif time_ms < 700:
            acceleration, velocity = -5.0, -5.0
            altitude = 1000.0 - (time_ms - 500) / 50.0
        else:
            acceleration, velocity = 0.0, -40.0
            altitude = 990.0
        samples.append(ReplaySample(time_ms, acceleration, velocity, altitude))

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


def _fast_detector(**overrides) -> PhaseDetectionConfig:
    values = {
        "liftoff_confirm_ms": 100,
        "burnout_min_powered_ms": 100,
        "burnout_confirm_ms": 100,
        "burnout_timeout_ms": 2000,
        "apogee_min_coast_ms": 500,
        "apogee_min_altitude_m": 1,
        "apogee_confirm_ms": 100,
        "apogee_single_sensor_confirm_ms": 100,
        "apogee_baro_descent_m": 1.0,
        "apogee_high_speed_lockout_m_s": 20.0,
        "apogee_timeout_ms": 5000,
        "sensor_fault_timeout_ms": 100,
    }
    values.update(overrides)
    return PhaseDetectionConfig(**values)


def _through_burnout() -> list[ReplaySample]:
    return [
        ReplaySample(0, 30.0, 0.0, 0.0),
        ReplaySample(100, 30.0, 10.0, 2.0),
        ReplaySample(200, 30.0, 20.0, 8.0),
        ReplaySample(300, -5.0, 20.0, 12.0),
        ReplaySample(400, -5.0, 18.0, 15.0),
    ]


def test_replay_rejects_single_fused_velocity_glitch() -> None:
    manifest = replace(FlightManifest.defaults(), detection=_fast_detector())
    samples = _through_burnout() + [
        ReplaySample(500, -5.0, 15.0, 17.0),
        ReplaySample(600, -5.0, -5.0, 18.0),
        ReplaySample(700, -5.0, 10.0, 20.0),
        ReplaySample(800, -5.0, 8.0, 22.0),
        ReplaySample(900, -5.0, 6.0, 24.0),
        ReplaySample(1000, -5.0, 4.0, 26.0),
    ]

    result = run_firmware_replay(manifest, samples)

    assert 4 not in [point.state for point in result.points]


def test_replay_uses_imu_only_apogee_fallback_after_baro_failure() -> None:
    manifest = replace(FlightManifest.defaults(), detection=_fast_detector())
    samples = _through_burnout()
    for time_ms in range(500, 2200, 100):
        samples.append(
            ReplaySample(
                time_ms,
                -20.0,
                max(-20.0, 18.0 - (time_ms - 400) * 0.02),
                15.0,
                baro_valid=False,
            )
        )

    result = run_firmware_replay(manifest, samples)

    drogue = next(point for point in result.transitions if point.state == 4)
    assert drogue.detector_mode == 2
    assert drogue.transition_reason == 7


def test_replay_uses_barometer_only_apogee_fallback_after_imu_failure() -> None:
    manifest = replace(FlightManifest.defaults(), detection=_fast_detector())
    samples = _through_burnout()
    for time_ms in range(500, 1600, 100):
        altitude = 15.0 - (time_ms - 400) * 0.01
        samples.append(
            ReplaySample(
                time_ms,
                0.0,
                -5.0,
                altitude,
                barometric_altitude_m=altitude,
                imu_valid=False,
            )
        )

    result = run_firmware_replay(manifest, samples)

    drogue = next(point for point in result.transitions if point.state == 4)
    assert drogue.detector_mode == 1
    assert drogue.transition_reason == 6


def test_replay_bounded_apogee_timeout_requires_recorded_altitude() -> None:
    manifest = replace(
        FlightManifest.defaults(),
        detection=_fast_detector(apogee_timeout_ms=1000),
    )
    samples = _through_burnout()
    for time_ms in range(500, 1600, 100):
        samples.append(
            ReplaySample(
                time_ms,
                0.0,
                0.0,
                0.0,
                imu_valid=False,
                baro_valid=False,
            )
        )

    result = run_firmware_replay(manifest, samples)

    drogue = next(point for point in result.transitions if point.state == 4)
    assert drogue.detector_mode == 3
    assert drogue.transition_reason == 8
