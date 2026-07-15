# Croi Mission Configuration HIL

Result: PASS with explicitly deferred cold-power and CAN checks.

## Test Configuration

- Start: 2026-07-14T22:46:05Z
- Board: Croi Rev1; hardware serial not assigned
- Wiring: Croi alone over SWD; no CAN peers or actuators connected
- ST-Link: `25000f00082d343632525544`
- Target: STM32F07x, chip ID `0x0448`, 64 KiB flash, 16 KiB SRAM
- Target voltage reported by OpenOCD: 3.17 V
- Croi firmware: `247b528b2a694b40ad2aa0218657c94036ee8015`
- Braiteoiri: `0e046836d9c57c004fdc3658b102376f706a2ea9`
- Comheadan: `01f8aefc5d8effafc560f7d29bc8728fc28c1f23`
- Ogma Console after HIL fix: `b586231cc03670ba4ec4dcac8a502c948e873f78`

## Procedure And Evidence

1. Read healthy baseline: schema 6, CRC `0x44A2AA23`, `READY`, zero logger use.
2. Ran 25 mission/verification/preflight validation tests: all passed.
3. Flashed non-actuating test mission:
   - liftoff threshold: 33.0 m/s^2
   - main altitude: 321 m
   - drogue delay: 4321 ms
   - airbrakes: disabled
   - drogue/main pyros: disabled
   - recovery fallback: disabled
   - readback: schema 6, CRC `0xE7A22511`
4. Reset Croi with OpenOCD `reset halt` then `resume`. Uptime dropped from 68.8 s to 16.4 s; CRC `0xE7A22511` persisted.
5. Restored baseline mission. Readback returned schema 6, CRC `0x44A2AA23`.
6. Ran post-restore 45 s SWD soak: 9/9 samples valid, `READY` throughout, sensors valid, zero sensor failures, zero queue drops, zero watchdog misses, zero preflight records, zero preflight bytes.
7. Ran full Ogma Console regression suite after retry fix: 158 passed. GitHub Console CI run `29374779297` passed.

## Findings

- One transient empty OpenOCD `mdw` response aborted the first poll after four valid samples. Console now reconnects OpenOCD once after a failed status read. Repeat soak passed.
- OpenOCD `reset run` did not reset uptime with this ST-Link/target combination. `reset halt` followed by `resume` did.
- This run proves firmware-flash and MCU-reset persistence. Physical rail power-cycle persistence remains untested.
- CAN behavior was intentionally excluded because Croi was connected alone.

## Audit Records

- `runs/missions/mission_flash_20260714T224756Z_e7a22511.json`
- `runs/missions/mission_flash_20260714T225217Z_44a2aa23.json`
- Raw status and soak bundles are stored beside this file.
