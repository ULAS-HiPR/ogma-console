# Integrated Stack Debug/Telemetry Soak

Status: PASS for wall-powered bench operation. This does not clear the open Foinse battery-path fault.

## Setup

- Stack: Croi, Teachtaire, Foinse, and Lamh
- Groundstation: USB serial `/dev/cu.usbmodem1101`, 115200 baud
- Power: Foinse wall-input path
- Concurrent interfaces: Croi SWD status polling every 5 s and Teachtaire-to-groundstation LoRa telemetry
- Duration: 300 s
- No firmware flashing, servo movement, arming, or pyro commands

## Croi SWD Results

- 57 valid status samples over 296.35 s; no read failure or reset
- Uptime: 1,039,924 to 1,334,838 ms
- Mission schema/CRC remained 6 / `0x44A2AA23`
- `READY`, sensors valid, three active CAN peers throughout
- CAN bus-off/error: always 0
- CAN retry depth: normally 0; four transient samples at depth 2 or 3
- CAN retry drops: 125 to 125; no new drops
- CAN node timeouts, CAN queue drops, logger queue drops: 0 throughout
- IMU/barometer failures and watchdog misses: 0 throughout
- Minimum free memory: FSM stack 1,408 B; CAN stack 616 B; logger stack 868 B; RTOS heap 416 B
- Preflight flash records/bytes remained 0 as configured
- Actuator commands advanced while `actuator_active` remained 0; no pyro arm/fire count and no fallback trigger

## Groundstation Results

- 6,518 serial lines; 6,254 decoded CAN frames
- Unknown CAN frames/warnings: 0 / 0
- Three relayed heartbeat nodes: Croi, Foinse, Lamh
- Foinse and Lamh heartbeat flags: 0
- Croi heartbeat reported latched `TX_DROP`; SWD proved the underlying count stayed fixed at 125 during this run
- LoRa/GPS summary records: 264
- Radio sequence: 7,407 to 9,517 with no reversal
- GPS-summary uptime spans 300 s but contains 21 gaps; worst gap is 6 s
- RSSI: -23 to -20 dBm, mean -21.46 dBm
- SNR: 8.25 to 12.25 dB, mean 9.77 dB
- No GNSS fix or satellites; indoor/warehouse result only, not GNSS acceptance
- Main-current samples: 257, 0 to 484 mA, mean 136.47 mA
- Servo-current samples: 257, 0 to 689 mA, mean 95.22 mA
- Current readings are not final calibration evidence. Foinse has no voltage ADC; voltage fields remain protocol placeholders.
- This proves a live, decodable bench link, not packet-loss acceptance. Groundstation USB currently exposes sequence only on GPS packets, so the missing summaries cannot yet be attributed cleanly to RF loss, Teachtaire scheduling, or receiver/display servicing.

## Relayed CAN Rates

These are groundstation relay rates, not raw CAN-bus transmit rates.

| Frame | Count | Rate |
|---|---:|---:|
| ACTUATOR_COMMAND | 257 | 0.857 Hz |
| BARO | 1,048 | 3.493 Hz |
| FLIGHT_STATE | 1,131 | 3.770 Hz |
| HEARTBEAT | 123 | 0.410 Hz |
| IMU_ACCEL | 1,047 | 3.490 Hz |
| IMU_GYRO | 1,046 | 3.486 Hz |
| KALMANN | 1,088 | 3.626 Hz |
| POWER_MAIN | 257 | 0.857 Hz |
| POWER_SERVO | 257 | 0.857 Hz |

## Verdict

Concurrent SWD diagnostics did not halt, reset, or measurably disrupt Croi flight tasks, CAN, or the LoRa/USB telemetry path. Wall-powered integrated operation passes this five-minute non-actuating soak. Battery-powered operation remains blocked pending the Foinse DMM decision tree and repair verification.

Before radio range qualification, emit packet type/sequence for every received packet over USB and calculate missing, duplicate, and reordered sequence counts directly.

## Evidence

- Croi samples: `output/hil/integrated_stack_20260715/croi_poll/croi/20260715_122806`
- Groundstation bundle: `runs/telemetry/20260715_122820`
- Foinse fault report: `output/hil/foinse_power_path_20260715/RESULT.md`
