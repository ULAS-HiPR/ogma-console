# Initial Board Bring-Up And Integrated Soak

Status: PARTIAL PASS. Bench evidence passed for tested paths; several flight
acceptance cases remained intentionally untested.

## Setup

- Date: 2026-07-12
- Boards: Croi, Foinse, Teachtaire, and Lamh Rev1
- CAN: 500 kbit/s; termination supplied through Teachtaire
- Interfaces: one or two ST-Link probes during different runs
- Actuation: DS3225MG later fitted to Lamh PWM1 for an unloaded sweep
- GNSS: no antenna during initial Teachtaire checks
- Pleasc: absent

Physical board serials, supply measurements, operator, and exact complete commit
manifest were not captured for every early run. This is a documentation gap and
prevents treating the evidence as release acceptance.

## Results

- Croi, Foinse, and Teachtaire formed a three-board CAN stack. Croi reported two
  active peers; retry/drop counters remained stable after termination was fitted.
- Teachtaire CAN RX/TX and LoRa TX/done counters advanced under CAN traffic and
  repeated SWD polling with zero TX drops, ESR, bus-off, or LoRa timeout.
- Teachtaire GNSS UART overrun was reproduced. Comheadan `01f8aef` added ORE
  clearing/counting; bytes and NAV-SAT replies then continued under CAN/SWD load.
- Lamh `dbfd9c8` detected PCA9685 at `0x40`, received Croi commands, and reported
  zero CAN TX drops/ESR. Physical arm remained inactive.
- Redundant inactive failsafe writes were fixed. Before: 81 inactive Croi frames
  caused 324 I2C writes in about 9 s. After: write count stayed at 10 while 92
  additional commands arrived.
- A DS3225MG completed the commanded PWM1 sweep. This proves MCU-to-PCA9685-to-
  connector-to-servo operation, not loaded airbrake margin or final safe angles.
- Croi logger run 10 stopped safely on `VerifyFailed` at `0x00301740`. After power
  reset, run 11 reused the erased address. An 8 KiB read found 80 complete records
  with valid header and payload checksums. Braiteoiri `0e04683` added three readback
  retries while preserving stop-on-persistent-mismatch behavior.
- Post-fix Croi run 12 wrote 150 records in 15 s with zero logger faults/drops and
  all three CAN peers visible.
- Five-minute integrated soak added 3,023 Croi records and 304,652 bytes with zero
  logger/CAN queue drops, logger faults, CAN errors, bus-off, retry drops, or
  watchdog misses. Lamh received 15,612 CAN frames and 2,901 Croi commands with
  zero new drops/errors; failsafe and I2C counts stayed stable.

## Safety Finding

PlatformIO probe selection wrote a Lamh image to Teachtaire during a two-probe
session. Ogma Console was changed to build first, then call direct OpenOCD
`program ... verify reset exit` against the selected ST-Link serial. Both boards
were restored and checked.

## Evidence

- `runs/status/teachtaire/20260712_232350`
- `runs/status/lamh/20260712_233834`
- `runs/status/lamh/20260712_234111`
- `runs/status/croi/20260713_084421`
- `runs/status/croi/20260713_084852`
- `runs/status/croi/20260713_090203`
- `runs/status/lamh/20260713_090158`

## Open Work

- Assign physical board serials and capture an exact release manifest every run.
- Cold-start, brownout, CAN fault/recovery, and long endurance testing.
- Lamh final safe angles, all outputs, representative load, and reset/timeout tests.
- Croi complete flash fill/read/wipe/reuse cycle.
- GNSS outdoor fix and radio range/loss qualification.
