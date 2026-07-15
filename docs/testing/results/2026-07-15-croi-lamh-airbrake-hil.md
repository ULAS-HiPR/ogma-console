# Croi/Lamh Airbrake And Logger HIL

Status: PASS for timed PWM1 command flow, physical actuation, inactive-arm rejection, Croi-reset command-lease response, reset recovery, flash retention, and baseline restoration. Mechanical-load, instrumented-timing, raw CAN-loss, and Lamh-STM-reset-at-deployment tests remain open.

## Setup

- Stack: Croi, Teachtaire, Foinse, and Lamh
- Power: Foinse wall-input path; known-bad battery path not used
- Actuator: DS3225MG on Lamh PWM1
- Debug: one ST-Link moved between Lamh and Croi
- Lamh physical arm: PB2/J7 shorted to GND during actuation, removed for final verification
- Pleasc absent; Croi drogue/main channels disabled throughout

## Locked Test Configuration

- Lamh safe angles: PWM1 `0 deg`; PWM2-4 `90 deg`
- Lamh config write/readback passed; audit record: `runs/lamh_safety/lamh_safety_20260715T142025Z.json`
- Temporary Croi mission CRC: `0x5B72C32D`
- Liftoff test threshold: 1 m/s^2, IMU Z axis, positive sign
- PWM1 command: retract `0 deg`, deploy `45 deg` at T+2 s, stow `0 deg` at T+5 s
- Command lease: 500 ms
- Physical trigger: rotate stack 90 degrees, hold, then return flat
- Recovery fallback disabled; pyro channels disabled

## Results

- Lamh reported physical arm raw/debounced active before the first sequence.
- Three consecutive Croi reset/trigger cycles produced the observed physical sequence `0 -> 45 -> 0 deg`.
- Croi reached POWERED in cycles 1 and 2 and COASTING in cycle 3 by final readback.
- Croi emitted no pyro arm/fire command in any cycle.
- Croi retained three logger runs through two resets while actively logging.
- Final SWD dump read exactly the reported 209,424 bytes.
- Parser returned 2,078 valid records: 1,672 local flight records and 406 remote-CAN records.
- Parsed run IDs were 1, 2, and 3; parser warnings were empty.
- Final Lamh counters: 71,940 CAN RX, 12,986 Croi commands, 1,353 heartbeat TX, zero CAN TX drops, zero CAN error/bus-off, and zero I2C error.
- Lamh entered failsafe seven times across boot, command cessation, and disarm events.
- After removing the jumper, Lamh reported both arm inputs inactive and retained safe-angle readback `0,90,90,90`.
- Croi baseline mission was restored and verified as schema 6 / CRC `0x44A2AA23`.
- Final Croi state was READY with airbrake inactive, three active peers, valid sensors, zero current CAN/queue/logger/watchdog/pyro faults, and all 2,078 records retained.

## Extended Safety Tests

- A corrected deterministic HIL trigger used IMU Z / negative sign, CRC `0xE59C308B`. Croi flash records prove repeated active 45-degree commands while Lamh PB2 was inactive; the servo did not move.
- The disarmed test exposed repeated safe-state I2C writes for every rejected command. Lamh now writes the safe state once on transition and leaves it latched.
- A second review found identical armed lease renewals rewrote unchanged PWM. Lamh now renews command freshness without writing PCA9685 unless output or angle changes.
- Hardware verification after the first fix received 343 new Croi commands while I2C writes remained exactly 10 and servo-set batches remained exactly 5.
- For Croi-loss testing, temporary mission CRC `0xF98CA106` held deployment for 15 seconds. A persistent OpenOCD session reset Croi 13 ms after the operator signal while PWM1 was physically deployed. The servo returned immediately to its 0-degree safe position.
- Final Lamh readback after all tests: arm raw/debounced inactive, 89,861 CAN RX, 16,639 Croi commands, zero CAN/I2C errors or TX drops, and safe-angle readback `0,90,90,90`.
- Final Croi baseline restore reported READY, mission CRC `0x44A2AA23`, three peers, airbrake inactive, and zero current CAN/queue/sensor/watchdog/pyro faults.
- Failsafe entries and additional safe writes during this campaign include deliberate Croi resets and SWD halts longer than the 500 ms command lease. This is expected safe behavior; no-SWD flight-load jitter qualification remains required.

## Open Acceptance Work

- Measure deploy/stow timing electrically or from timestamped Lamh status; visual timing is insufficient for release tolerance.
- Instrument command/CAN loss with timestamped PWM capture and prove the return-to-safe latency bound; the observed Croi-reset response passed qualitatively.
- Remove raw CAN traffic independently of Croi reset and verify the same safe response.
- Reset Lamh STM while PWM1 is deployed and document PCA9685 retained output caused by OE being tied low.
- Repeat with representative airbrake mechanism, aerodynamic load surrogate, supply limits, and final safe/deploy angles.
- Repeat on repaired Foinse battery power after hot-plug protection and power-path acceptance.

## Evidence

- Temporary mission flash: `runs/missions/mission_flash_20260715T142841Z_5b72c32d.json`
- Deterministic disarmed mission: `runs/missions/mission_flash_20260715T145833Z_e59c308b.json`
- Extended reset-test mission: `runs/missions/mission_flash_20260715T153534Z_f98ca106.json`
- Final baseline restore: `runs/missions/mission_flash_20260715T154506Z_44a2aa23.json`
- Parsed flash bundle: `runs/croi_flash/20260715_154024`
- Disarmed-command flash bundle: `runs/croi_flash/20260715_160156`
- Raw SWD dump: `runs/croi_flash/latest_swd_dump.bin`
