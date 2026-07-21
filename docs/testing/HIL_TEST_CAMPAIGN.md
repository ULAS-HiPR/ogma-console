# Ogma Hardware-In-The-Loop Campaign

Status: active. Initial Croi/Foinse/Teachtaire/Lamh CAN bench bring-up completed 2026-07-12. No energetic pyrotechnics until inert-load Pleasc acceptance passes.

Record firmware SHA, submodule SHAs, mission CRC, board serial, operator, UTC time, supply, wiring, equipment, raw logs, and result for every run.

## Completed Bench Evidence: 2026-07-12

- Three-board CAN stack with Croi, Foinse, and Teachtaire: Croi reported two active peers; retry/drop counters remained stable after termination was fitted through Teachtaire.
- Teachtaire under live CAN and repeated SWD polling: CAN RX/TX advanced with zero TX drops, zero ESR, and no bus-off. LoRa TX/done advanced without timeout. No GNSS antenna was fitted, so fix/satellite warnings are expected.
- Teachtaire GNSS UART overrun reproduced and fixed in Comheadan `01f8aef`: receiver now clears/counts ORE and continues parsing. Verified bytes and NAV-SAT replies continued under CAN/SWD load.
- Lamh `dbfd9c8`: PCA9685 detected at `0x40`; CAN RX and Croi command counters advanced; zero TX drops/ESR; arm input inactive. Servos and servo 5 V were disconnected. Placeholder safe angles remain 90 degrees.
- Lamh redundant failsafe writes fixed: before, 81 inactive Croi frames caused 324 extra I2C writes in about 9 s; after, I2C write count remained 10 while 92 more commands arrived.
- Multi-probe incident: PlatformIO `--upload-port` ignored/mishandled the binary ST-Link serial and wrote the Lamh image to Teachtaire. Ogma Console now builds then invokes direct OpenOCD `program ... verify reset exit` whenever a probe serial is selected. Both boards were restored and health-checked.
- Croi logger transient: run 10 latched `VerifyFailed` at next address `0x00301740` and stopped safely. After power reset, run 11 reused that erased address and resumed. An 8 KiB targeted read found 80 complete records with valid header/payload checksums; the old failure address now holds a valid committed record. Braiteoiri `0e04683` retries readback verification three times but still stops on persistent mismatch. Post-flash run 12 wrote 150 records in 15 s with zero logger faults/drops while Croi saw all three peers.
- Five-minute integrated soak after hardening: Croi added 3,023 records and 304,652 bytes with zero logger/CAN queue drops, zero logger faults, three peers throughout, zero CAN errors/bus-off/retry drops, and zero watchdog misses. Lamh received 15,612 CAN frames and 2,901 Croi commands with zero new TX drops/errors; PCA9685 I2C writes and failsafe count stayed flat.
- Later Lamh bench test drove a DS3225MG on PWM1 through the commanded sweep successfully. This proves the command/PCA/output path, not mechanical airbrake load margin or final safe angles.
- Groundstation USB streaming was decoded live in Ogma Console with board telemetry, CAN frame types, current, IMU, flight-state, and link plots updating together. GNSS outdoor fix and LoRa range/loss qualification remain pending.
- Evidence bundles: `runs/status/teachtaire/20260712_232350`, `runs/status/lamh/20260712_233834`, `runs/status/lamh/20260712_234111`, `runs/status/croi/20260713_084421`, `runs/status/croi/20260713_084852`, `runs/status/croi/20260713_090203`, and `runs/status/lamh/20260713_090158`.

## Completed Croi Mission Configuration HIL: 2026-07-14

- Baseline mission was schema 6, CRC `0x44A2AA23`, with airbrakes, both pyro channels, and recovery fallback disabled.
- A non-actuating test mission changed liftoff, main-altitude, and drogue-delay fields. Croi built, flashed, and read back schema 6 and CRC `0xE7A22511` exactly.
- Controlled SWD reset reduced uptime from 68.8 s to 16.4 s while CRC `0xE7A22511` persisted. This proves MCU-reset persistence; a cold rail power-cycle remains required.
- Baseline mission was restored and read back as schema 6, CRC `0x44A2AA23`.
- Post-restore 45 s soak returned 9/9 valid samples: `READY`, sensors valid, zero IMU/baro failures, zero queue drops, zero watchdog misses, and zero preflight flash records/bytes.
- First post-restore poll exposed one transient empty OpenOCD `mdw` response that aborted capture. Ogma Console now closes and reconnects OpenOCD once after a failed status read; regression suite passes 158 tests and repeat soak completed.
- Evidence: `output/hil/croi_mission_config_20260714T224605Z`, `runs/missions/mission_flash_20260714T224756Z_e7a22511.json`, and `runs/missions/mission_flash_20260714T225217Z_44a2aa23.json`.

## Open Foinse Battery Power-Path Fault: 2026-07-15

- Battery input stopped powering the stack after repeated bench-supply connection cycles; wall-input path remains operational.
- Five-minute wall-powered capture returned 6,193 decoded CAN frames with zero unknown frames/warnings. Foinse heartbeat remained error-free, isolating the fault to `BAT -> Q1/U17 -> pwr_s` or local interconnect.
- Leading hypothesis is hot-plug ringing at LTC4412 VIN/SENSE, but U17 is not proven failed. DMM gate/source measurements and unpowered Q1 diode test are required before replacing parts.
- Battery operation is blocked until repair, scoped source-transition testing, and repeated power-cycle acceptance pass.
- Evidence and measurement tree: `output/hil/foinse_power_path_20260715/RESULT.md`; telemetry bundle: `runs/telemetry/20260715_121854`.

## Completed Integrated Debug/Telemetry Soak: 2026-07-15

- Ran Croi SWD status polling every 5 s while the four-board stack carried CAN traffic and Teachtaire relayed telemetry over LoRa to the USB groundstation.
- Croi returned 57/57 samples over 296 s without reset/read failure: three peers remained active, sensors stayed valid, and mission CRC remained `0x44A2AA23`.
- CAN retry drops remained 125, with zero new bus-off, CAN errors, node timeouts, CAN/logger queue drops, sensor failures, or watchdog misses. Four samples saw transient retry depth 2-3 and drained without loss.
- Groundstation captured 6,254 decoded CAN frames over 300 s with zero unknown frames/warnings. GPS-visible radio sequence never reversed; RSSI was -23 to -20 dBm and SNR 8.25 to 12.25 dB.
- Only 264 GPS summaries covered the 300 s transmitter-uptime span, with 21 gaps and a worst gap of 6 s. USB currently exposes sequence only for GPS packets, so this is not packet-loss acceptance; add metadata for every radio packet before qualification.
- Foinse and Lamh heartbeats were clean. Croi's relayed `TX_DROP` flag was historical/latching; SWD verified its drop count did not increase.
- No GNSS fix was obtained indoors. Current data remains uncalibrated. No actuator or pyro action was performed.
- Evidence: `output/hil/integrated_stack_20260715/RESULT.md`, `output/hil/integrated_stack_20260715/croi_poll/croi/20260715_122806`, and `runs/telemetry/20260715_122820`.

## Completed Teachtaire/Groundstation Bench-Loss Hardening: 2026-07-15

- Initial aligned comparison found 32 missing of 276 GPS packets (`11.6%`) despite strong signal. The bottleneck was CircuitPython servicing one SPI bus for SX1272 polling and ST7789 display work.
- A/B testing proved LCD-disabled 1 ms polling received 2,115/2,116 packets, while LCD-active 1 ms polling still lost 4.95%. A 25 ms transmitter guard did not help and was removed.
- Groundstation now keeps its display in standalone mode, automatically suspends LCD rendering while USB serial is open, polls radio every 1 ms, and emits packet sequence/type metadata. LCD resumes after USB closes.
- Final USB-mode run received 2,115/2,115 packets with zero missing, duplicates, or reordering; decoded 6,932 CAN frames with zero unknown frames/parser warnings.
- Teachtaire also exposed four CAN FIFO overruns in 394 s. Interrupt-driven FIFO draining into a 16-frame queue reduced final result to zero overruns across 25,236 received CAN frames, with zero CAN TX drops/bus-off/error.
- Final RSSI was -24 to -21 dBm and SNR 8.0 to 12.5 dB. No GNSS fix was expected indoors. Outdoor/range qualification remains open.
- Evidence: `output/hil/teachtaire_radio_loss_20260715/RESULT.md`, `runs/telemetry/20260715_140253`, `output/hil/teachtaire_radio_loss_20260715/can_irq_baseline`, and `output/hil/teachtaire_radio_loss_20260715/can_irq_final`.

## Completed Croi/Lamh Airbrake And Logger HIL: 2026-07-15

- Programmed and read back Lamh safe angles `0,90,90,90`; PWM1 drove a DS3225MG and physical arm input was confirmed active before testing.
- Flashed temporary Croi mission CRC `0x5B72C32D`: PWM1 retracted at 0 degrees, deployed to 45 degrees at T+2 s, and stowed at T+5 s; both pyro channels and recovery fallback remained disabled.
- Three consecutive reset/physical-trigger cycles produced the observed `0 -> 45 -> 0 degree` sequence. Trigger was a 90-degree stack rotation followed by return to flat.
- Croi logger recovered across resets as run IDs 1, 2, and 3. SWD retrieval parsed 2,078 records / 209,424 bytes with zero warnings: 1,672 local flight and 406 remote-CAN records.
- Final Lamh readback after jumper removal showed arm inactive, 12,986 Croi commands received, zero CAN drops/errors/bus-off, zero I2C errors, and seven failsafe entries.
- Restored Croi baseline schema 6 / CRC `0x44A2AA23`; final state READY, airbrake inactive, three peers, and zero current sensor/queue/logger/watchdog/pyro faults.
- Extended testing proved active 45-degree Croi commands do not move the servo with PB2 inactive. Two Lamh write-deduplication defects found during this test were fixed and hardware-verified.
- A persistent SWD session reset Croi 13 ms after operator confirmation of physical deployment; Lamh returned immediately to its safe position. Instrumented latency and independent raw-CAN loss remain open.
- Final Lamh status was disarmed with 16,639 Croi commands received, zero CAN/I2C errors or TX drops, and safe angles `0,90,90,90`.
- Release acceptance remains open for instrumented timing, raw-CAN timeout latency, Lamh STM reset while deployed, representative mechanical load, final angles, and battery-powered repetition.
- Evidence: `docs/testing/results/2026-07-15-croi-lamh-airbrake-hil.md`, `runs/lamh_safety/lamh_safety_20260715T142025Z.json`, `runs/missions/mission_flash_20260715T142841Z_5b72c32d.json`, `runs/missions/mission_flash_20260715T143820Z_44a2aa23.json`, and `runs/croi_flash/20260715_154024`.

## 1. Board Acceptance

- Power-cycle each board 100 times at minimum/nominal/maximum qualified input voltage.
- Verify identity/status appears, watchdog runs, reset cause is recorded, and no output glitches occur.
- Run each board for two hours while polling status; require zero hangs, stack minimum above 256 B, no unexplained resets, no CAN queue growth, no bus-off latch.
- Brownout each board independently; verify remaining stack continues and recovered board rejoins within 5 s.

## 2. CAN Stack

- Test one board, full stack, missing terminator, one disconnected node, and reconnect.
- Verify 500 kbit/s on scope and correct termination resistance with power removed.
- Saturate noncritical traffic; require Croi heartbeat, Pleasc command, and Lamh lease frames retain bounded latency.
- Force bus-off; require safe outputs, visible diagnostics, and controlled recovery.
- Confirm node-dead indication at 5 s and recovery after valid heartbeat.
- Capture frame counts/IDs and prove no malformed DLC or unexpected critical sender.

## 3. Croi Flight And Logger

- Save the versioned flight manifest and archive its SHA-256. Verify Croi reports mission schema 7 and the exact generated mission/detection/logging CRC after flash.
- Run Ogma Console native replay against nominal and adversarial CSV profiles before hardware replay. Require the replayed transition sequence to match Croi SIL because both execute `FlightPhaseLogic` directly.
- Replay calibrated launch profiles: no-launch vibration, nominal flight, slow liftoff, high acceleration, sensor dropout, false apogee, baro spike, IMU saturation, and power interruption.
- Verify exact state sequence: calibrating, ready, powered, coasting, drogue, main, landed.
- Verify liftoff, burnout, and apogee persistence in milliseconds at the configured sample period. Prove a single threshold crossing cannot transition.
- Prove the minimum-powered and minimum-coast gates independently; hold every other condition true while each gate is false.
- Inject a barometric descent while inertial ascent speed exceeds the lockout; require no apogee transition and a recorded high-speed rejection.
- Fail only the barometer, only the IMU, and both sensors. Verify the fault timeout, detector mode, longer one-sensor dwell, transition reason, and altitude-gated no-sensor timeout.
- Verify every transition record contains candidate, confirmed-vote, gate, rejection, mode, health, and reason fields matching the replay stimulus.
- Verify five-second landing confirmation.
- Enable the main backup only with inert loads first. Verify delay, descent-speed threshold, altitude window, consecutive-sample reset, `main_fallback_triggered`, and exactly one transition to MAIN. Repeat with every individual guard false.
- Verify configured sample period, post-landing duration, and remote-CAN inclusion against recovered records; do not infer policy only from source code.
- Fill, power-cycle, read, wipe, and reuse flash. Require record CRCs, monotonic sequence, correct run IDs, and no data beyond reported used length.
- Confirm log contains onboard sensors, GPS/airbrake secondary data, state transitions, and every Pleasc command/status/ACK event.
- Measure minimum task stack and heap throughout worst-case logging/readout.

## 4. Lamh Airbrakes

- Enter measured safe angle for PWM1-4 in Ogma Console; flash and verify readback.
- Test each output alone through full mechanical travel under representative load.
- Verify physical arm input blocks movement when inactive.
- Verify inactive command, command lease timeout, Croi heartbeat timeout, CAN loss, and MCU watchdog reset drive/leave mechanism in accepted safe behavior.
- Specifically reset STM while PCA9685 commands maximum deployment. Record retained PWM caused by OE tied low; obtain mechanical-risk sign-off or apply hardware mitigation before release.
- Run timed deploy/stow mission profile at temperature and supply limits. Verify deployment begins at the locked start delay and retract begins at the locked stow delay; inspect current and linkage margin.

## 5. Pleasc Rev1 With Inert Loads

- Keep external RBF open. Power-cycle/reset/brownout; require PYRO_ON and PB3-PB6 always low.
- Use LEDs/resistors or instrumented electronic loads only.
- Prove inert image cannot fire under any CAN traffic.
- Flash Rev1 image only after explicit UI warning; verify status reports `fire_enabled=1` and `rev1_accepted_risk=1`.
- Reject bad command tag, stale/repeated sequence, wrong mission tag, wrong flight state, absent/stale Croi heartbeat, missing continuity, unarmed channel, arm-settle violation, and repeated channel.
- Verify first arm latches mission tag, arm lease expires in 2 s, Croi timeout is 5 s, pulse is 250 ms, and each channel fires once per boot.
- Remove Croi during arm and pulse; require immediate bounded shutdown behavior.
- Drop the first fire frame, first ACK, and first status independently; require bounded retry and exactly one 250 ms physical pulse.
- Verify channel mapping physically for all four outputs before assigning drogue/main.
- Repeat complete sequence with external RBF procedure witnessed and signed. Energetic testing requires separate range procedure.

## 6. Teachtaire And Groundstation

- Flash the manifest radio policy and verify Teachtaire reports radio config magic, schema, and CRC over SWD.
- Verify SX1272 register version, GNSS fix/stale behavior, TX timeout/reinit, and watchdog reset.
- Compare protocol vectors between Teachtaire and groundstation; corrupt every header/payload field and require rejection.
- Confirm core 5 Hz, GPS/slow 1 Hz, event priority, deep 0.2 Hz under full CAN traffic.
- Measure packet loss, duplicate sequence, latency, RSSI/SNR, and recovery at bench, field, and expected range.
- Verify groundstation display and USB output simultaneously; Ogma Console must decode/save/plot GPS and CAN without UI blocking.
- Confirm radio settings and operating procedure are approved for launch location.

## 7. Integrated Dress Rehearsal

- Assemble flight wiring, batteries, antennas, RBF, inert pyros, and actual airbrake mechanism.
- Flash release candidates from clean checkouts using `dependencies.lock.json`.
- Build an Ogma flight package. Require valid package index hashes, no missing firmware, no dirty repositories, GO preflight evidence, and matching manifest SHA-256.
- Run Ogma Console doctor/status/health on every board; archive outputs.
- Lock mission, verify Croi mission CRC readback, verify Lamh safe-angle CRC, and verify Pleasc image/environment.
- Execute full countdown, arm, launch replay, deployment, landing, telemetry capture, flash recovery, and post-flight inspection.
- Repeat three consecutive times with zero unexplained deviation.

## 8. Audible Annunciation

- Keep Croi buzzer annunciation out of release firmware until nonblocking patterns are bench-qualified.
- Verify boot, ready, fault, and armed patterns are distinct, bounded, silent during active flight unless explicitly required, and always end with PWM compare at zero.
- Reset and brownout during every tone phase; require no continuous tone and no impact on watchdog, sensor, CAN, or logger timing.

## Release Criteria

- Every required test passes with archived evidence.
- Safe angles and Foinse calibration are final.
- Lamh OE residual risk has documented disposition.
- External RBF procedure and Pleasc Rev1 risk acceptance are signed.
- No open severity-1/2 software or hardware findings.
- Exact source commits, dependency pins, binaries, mission file, and build logs are archived and tagged.
