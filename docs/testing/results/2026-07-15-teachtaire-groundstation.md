# Teachtaire / Groundstation Loss And CAN RX HIL

Status: PASS for short-range wall-powered bench telemetry. GNSS fix and RF range qualification remain open.

## Initial Finding

- Teachtaire firmware reported SX1272 version `0x22`, live CAN, no LoRa timeout/reinit, and live GNSS UART data.
- An exactly aligned interval sent 276 GPS packets and groundstation received 244: 32 missing, `11.6%` loss.
- Signal was strong, so RF margin was not the limiting factor.
- Groundstation used one SPI bus for SX1272 polling and ST7789 display refresh. CircuitPython slept 10 ms per loop and display work delayed FIFO service.

## Receiver A/B Tests

| Receiver configuration | Received / expected | Loss |
|---|---:|---:|
| LCD disabled, 1 ms poll | 2,115 / 2,116 | 0.047% |
| LCD active, 1 ms poll | 403 / 424 | 4.95% |
| LCD active, 1 ms poll, 25 ms TX guard | 745 / 786 | 5.22% |
| USB high-fidelity mode, no TX guard, final | 2,115 / 2,115 | 0.000% |

The transmitter guard reduced throughput and did not improve LCD contention, so it was removed. Teachtaire scheduling remains unchanged.

## Groundstation Fix

- Poll SX1272 every 1 ms instead of every 10 ms.
- Emit comment-prefixed packet sequence/type/count/RSSI/SNR metadata over USB without changing existing CAN/GPS lines.
- Standalone mode keeps the ST7789 display active.
- When a host opens the CircuitPython USB console, suspend LCD rendering and dedicate SPI/CPU time to radio capture.
- Resume LCD rendering automatically when USB capture closes.
- Ogma Console strips CircuitPython ANSI/OSC terminal-title sequences before parsing; raw capture remains unchanged.

## Teachtaire CAN RX Finding And Fix

- Polling firmware accumulated four hardware CAN FIFO overruns in 394 s without SWD polling.
- CAN RX now drains FIFO0 in `CEC_CAN_IRQHandler` into a 16-frame queue.
- Main-loop frame classification, event handling, radio caching, and CAN TX remain outside the ISR.
- Static RAM changed from 1,708 B to 2,356 B (`14.4%` of 16 KiB); flash is 38,212 B (`58.3%` of 64 KiB).
- Final 394 s snapshot: 25,236 CAN frames received, zero FIFO/software-queue overruns, zero CAN TX drops, zero bus-off/error.

## Final Five-Minute Result

- Radio packets: 2,115 received / 2,115 expected
- Missing / duplicate / reordered: 0 / 0 / 0
- Decoded CAN frames: 6,932
- Unknown frames / parser warnings: 0 / 0
- GPS packets: 294; no fix indoors
- RSSI: -24 to -21 dBm, mean -22.73 dBm
- SNR: 8.0 to 12.5 dB, mean 9.66 dB
- LoRa timeout / reinit / telemetry-event drop: 0 / 0 / 0
- CAN RX overrun / CAN TX drop: 0 / 0
- Watchdog active; no firmware fault

## Evidence

- Initial synchronized TX/RX run: `output/hil/teachtaire_radio_loss_20260715/teachtaire_poll/teachtaire/20260715_125003` and `runs/telemetry/20260715_124948`
- LCD-disabled A/B: `runs/telemetry/20260715_130123`
- LCD-active fast-poll A/B: `runs/telemetry/20260715_130457`
- Rejected guard A/B: `runs/telemetry/20260715_131418`
- Final CAN IRQ and USB-mode capture: `runs/telemetry/20260715_140253`
- CAN IRQ baseline/final status: `output/hil/teachtaire_radio_loss_20260715/can_irq_baseline` and `output/hil/teachtaire_radio_loss_20260715/can_irq_final`

## Still Open

- Outdoor GNSS cold/warm fix and stale-fix behavior
- Packet loss and latency at increasing distance, obstructed line of sight, and expected launch range
- Standalone LCD-mode packet-loss acceptance; USB high-fidelity mode is the validated zero-loss bench path
- Exact regulatory/launch-site radio settings and antenna installation qualification
