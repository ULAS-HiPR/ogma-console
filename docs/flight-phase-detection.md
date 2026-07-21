# Croi Flight-Phase Detection

Status: implemented in Croi mission schema 7; software-tested; hardware replay and motor-specific qualification remain open.

## Purpose

Croi owns flight-phase detection. Ogma Console configures bounded parameters, seals them into the mission CRC, replays recorded profiles through the same C++ logic, and exposes the detector's evidence. The console does not decide flight state during operation.

The state machine is monotonic:

```text
CALIBRATING -> READY -> POWERED -> COASTING -> DROGUE -> MAIN -> LANDED
```

## Transition Contract

| Transition | Evidence | Mandatory gates | Fallback |
| --- | --- | --- | --- |
| READY -> POWERED | corrected vertical acceleration above threshold, or sustained barometric climb | configured persistence | either sensor may establish liftoff |
| POWERED -> COASTING | corrected acceleration below burnout threshold | minimum powered time and persistence | bounded powered-state timeout |
| COASTING -> DROGUE | fused velocity, barometric descent from peak, and independently integrated inertial velocity | minimum coast time, minimum recorded altitude, high-speed lockout | two confirmed votes with both sensors; longer one-sensor dwell after a runtime fault; bounded timeout only after the altitude gate was previously met |
| DROGUE -> MAIN | altitude below main threshold | minimum drogue delay and three samples | optional fast-descent fallback with time, speed, altitude-window, and persistence guards |
| MAIN -> LANDED | low speed and low corrected acceleration | 50 samples | none |

With IMU and barometer healthy, apogee requires two of three evidence channels. After a sensor has failed continuously for the configured fault timeout, Croi permits the remaining independent channel with a longer persistence time. A transient missed read does not immediately enter degraded mode.

The no-sensor apogee timeout is deliberately altitude-gated. It cannot deploy from a sustained false liftoff indication while the vehicle remains below the configured minimum altitude. Consequence: losing all altitude evidence before that gate is reached remains an unresolved failure mode.

## Default Development Baseline

These values are conservative development assumptions, not motor qualification:

| Setting | Default |
| --- | ---: |
| Liftoff persistence | 300 ms |
| Barometric liftoff speed | 30 m/s |
| Minimum powered time | 500 ms |
| Burnout threshold / persistence | -1 m/s^2 / 300 ms |
| Burnout timeout | 10 s |
| Minimum coast time / altitude | 1.5 s / 20 m |
| Apogee velocity / persistence | -1 m/s / 500 ms |
| Single-sensor apogee persistence | 1 s |
| Barometric descent from peak | 3 m |
| High-speed apogee lockout | 20 m/s inertial ascent speed |
| Apogee timeout | 120 s |
| Runtime sensor-fault timeout | 500 ms |

Every value is bounded in the Python model and generated header. The mission manifest SHA-256 and Croi CRC include the complete detector configuration.

## Evidence And Logging

Croi status version 13 publishes:

- current candidate and confirmed-vote masks;
- active gates and rejection reasons;
- detector mode and sensor-health mask;
- inertial velocity, barometric velocity, and peak altitude;
- required vote count and last transition reason/time.

Croí flash payload version 2 stores the same 44-byte diagnostic snapshot beside every local flight sample. Ogma Console continues to decode legacy 60-byte flight records and decodes the new 104-byte records into CSV/JSON fields.

## Known Limits

- The fused vote is not fully independent of the barometric vote. High-speed lockout and the inertial vote reduce, but do not eliminate, common-mode pressure risk.
- No attitude solution exists yet, so gyroscope tilt is not used as an apogee vote.
- Accelerometer-only velocity will drift. It is a bounded degraded path, not the nominal estimator.
- Default thresholds need recorded motor/vehicle profiles, vibration data, barometric transients, and hardware replay before release.
- Deployment still depends on the separate Pleasc and Lamh safety contracts.

## Source Of Truth

- Croi detector: `croi/firmware/src/tools/flight_phase_logic.h`
- Croi diagnostics: `croi/firmware/src/tools/flight_phase_diagnostics.h`
- Mission contract: `ogma-console/ogma_app/mission_config.py`
- Native replay: `ogma-console/ogma_app/native/flight_replay.cpp`
- HIL requirements: `ogma-console/docs/testing/HIL_TEST_CAMPAIGN.md`
