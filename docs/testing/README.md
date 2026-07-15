# Ogma Testing

This directory is the durable record of Ogma verification. A successful build,
bench demonstration, and flight acceptance are different claims; reports state
exactly which one has been demonstrated.

## Current Status

| Area | Latest evidence | Result | Still required |
|---|---|---|---|
| Croi mission configuration | [2026-07-14](results/2026-07-14-croi-mission-configuration.md) | Pass for flash, readback, MCU-reset persistence, and healthy post-restore soak | Cold power-cycle persistence and full launch-profile replay |
| Croi logger and stack CAN | [2026-07-12](results/2026-07-12-board-bring-up.md) | Five-minute soak passed; logger and CAN queues remained healthy | Fill/read/wipe/reuse endurance and injected CAN faults |
| Integrated stack | [2026-07-15](results/2026-07-15-integrated-stack-soak.md) | Five-minute wall-powered Croi/Foinse/Teachtaire/Lamh soak passed | Battery-powered soak, brownouts, longer endurance, actuator activity |
| Teachtaire and groundstation | [2026-07-15](results/2026-07-15-teachtaire-groundstation.md) | Short-range USB capture passed with 2,115/2,115 packets and zero CAN overruns | Outdoor GNSS, RF range, obstructed-link recovery, standalone-display loss acceptance |
| Lamh actuator path | [2026-07-15](results/2026-07-15-croi-lamh-airbrake-hil.md) | PWM1 safe angle, arm gating, three timed cycles, disarmed-command rejection, and Croi-reset failsafe passed | Instrumented timing, independent raw-CAN loss, Lamh-STM reset at deploy, final angles, and representative mechanical load |
| Foinse wall power | [2026-07-15](results/2026-07-15-foinse-power-path.md) | Wall path and downstream stack operation passed | Current calibration |
| Foinse battery power | [2026-07-15](results/2026-07-15-foinse-power-path.md) | Blocked by open `BAT -> Q1/U17 -> pwr_s` fault | DMM isolation, repair, scoped hot-plug/crossover tests, repeated cycling |
| Pleasc | [Campaign](HIL_TEST_CAMPAIGN.md#5-pleasc-rev1-with-inert-loads) | Not tested; board assembly incomplete | Optocouplers, inert-load acceptance, channel mapping, RBF procedure |

This table is a navigation aid, not flight clearance. Release criteria live in
[`HIL_TEST_CAMPAIGN.md`](HIL_TEST_CAMPAIGN.md#release-criteria).

## Evidence Layout

```text
docs/testing/
  README.md                 current status and run index
  HIL_TEST_CAMPAIGN.md      required campaign and release criteria
  TEST_RUN_TEMPLATE.md      mandatory report fields
  results/                  reviewed human-readable run reports
../output/hil/              workspace raw HIL captures and machine-readable snapshots
runs/                       Ogma Console status, mission, and telemetry bundles
```

Reports are immutable records. Corrections append an amendment; reruns get a new
UTC timestamp. Raw captures are never edited to make a test pass.

## Required Run Metadata

Every run records:

- UTC start/end and operator
- board revision and physical serial/asset ID
- exact firmware, Braiteoiri, Comheadan, and Ogma Console commits
- mission schema/CRC and configuration files where relevant
- supply voltage/current limit, wiring, termination, antennas, loads, and probes
- equipment model/serial or calibration reference
- preconditions, procedure, quantitative acceptance criteria, and result
- raw evidence paths, deviations, failures, and required follow-up

Use [`TEST_RUN_TEMPLATE.md`](TEST_RUN_TEMPLATE.md). Do not mark `PASS` when a
required condition was skipped; use `PARTIAL`, `BLOCKED`, or `FAIL`.

## Completed Runs

- [2026-07-12: board bring-up and initial integrated soak](results/2026-07-12-board-bring-up.md)
- [2026-07-14: Croi mission configuration](results/2026-07-14-croi-mission-configuration.md)
- [2026-07-15: Foinse battery power-path isolation](results/2026-07-15-foinse-power-path.md)
- [2026-07-15: integrated debug/telemetry soak](results/2026-07-15-integrated-stack-soak.md)
- [2026-07-15: Teachtaire/groundstation loss and CAN RX hardening](results/2026-07-15-teachtaire-groundstation.md)
- [2026-07-15: Croi/Lamh airbrake and logger HIL](results/2026-07-15-croi-lamh-airbrake-hil.md)
