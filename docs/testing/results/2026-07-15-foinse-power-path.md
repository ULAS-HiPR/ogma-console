# Foinse Rev1 Battery Power-Path Finding

Status: OPEN HARDWARE FAULT. Wall-input bench operation works; battery operation is not accepted.

## Isolation

- Live wall-powered telemetry proves Foinse MCU, current sensors, downstream regulators, CAN, Croi, Lamh, Teachtaire radio, and groundstation remain operational.
- Battery-only failure is isolated to `BAT -> Q1/U17 -> pwr_s`, its connector, or those local joints/traces.
- Schematic/PCB mapping:
  - U17 LTC4412 pin 1 `VIN` -> `BAT`
  - U17 pin 2 `GND` -> ground
  - U17 pin 3 `CTL` -> ground; controller always enabled
  - U17 pin 5 `GATE` -> Q1 pad 1
  - U17 pin 6 `SENSE` -> `pwr_s`
  - Q1 pad/tab 2 `D` -> `BAT`
  - Q1 pad 3 `S` -> `pwr_s`

## Leading Hypothesis

Repeated failure after hot-plugging the bench supply is consistent with an undamped input transient, but U17 is not proven dead yet. The LTC4412 datasheet explicitly warns that hot-connecting a source into low-ESR ceramic capacitance can ring above absolute maximum ratings and recommends adding ESR/damping and verifying VIN/SENSE on a scope. Rev1 has fast P-channel wall switching, ceramic bypassing, and no visible TVS, hot-swap, or deliberate damping at this boundary.

Official source: https://www.analog.com/media/en/technical-documentation/data-sheets/4412fb.pdf

## Required DMM Test

Disconnect wall input. Connect battery only. Use ground as reference and avoid bridging adjacent TSOT pins.

1. Measure battery connector and U17 pin 1.
2. Measure U17 pin 3; expected approximately 0 V.
3. Measure U17 pin 6 / `pwr_s`.
4. Measure U17 pin 5 / Q1 gate.
5. Record Q1 drain, source, gate, and calculate `VGS = gate - source`.

Healthy battery path:

- U17 pin 1 equals battery voltage.
- `pwr_s` rises close to battery voltage; LTC4412 normally regulates about 20 mV across Q1 when load and Q1 permit.
- Q1 `VGS` is negative enough to conduct. LTC4412 clamps maximum gate drive near 7 V.

Decision tree:

- No voltage at U17 pin 1/Q1 drain: connector, fuse, trace, or joint fault.
- Pin 1 good but CTL not low: U17 ground/CTL joint fault.
- Pin 1 good, CTL low, gate remains near source with wall removed: U17/gate joint is suspect.
- Gate is driven low relative to source but `pwr_s` stays dead: Q1, Q1 joints, or power trace is suspect.
- `pwr_s` sits roughly one body-diode drop below BAT but collapses under load: Q1 is not being enhanced; inspect U17 gate drive and load current.
- With all power removed and capacitors discharged, Q1 body-diode test open in both directions: Q1/open-joint fault. Interpret in-circuit diode readings cautiously.

Replacing U17 alone is justified only after these measurements. If `pwr_s` is truly zero with valid BAT, inspect Q1 too: a healthy correctly oriented Q1 body diode should initially pass current before U17 enhances it.

## Immediate Controls

- Do not qualify or fly from battery until repaired and repeated transition testing passes.
- For bench wall power: turn PSU output off, connect, set current limit, then ramp/enable. Do not hot-plug a live lead.
- Do not bypass Q1/U17 for flight.

## Rev2 / Repair Verification

- Scope U17 VIN, SENSE, GATE, and `pwr_s` during battery attach, wall attach/removal, brownout, and source crossover.
- Add measured TVS/surge protection, damped bypassing or controlled ESR, bulk capacitance, and inrush/hot-swap control as required by captured waveforms.
- Add test points for BAT, wall input, `pwr_s`, Q1 gate, 5V logic, servo rail, and 3V3.
- Repeat at minimum/nominal/maximum input, current limit, cable length, and load.

Provisional protection architecture, subject to scope validation:

```text
battery -> fuse -> TVS + damped bulk capacitor
        -> eFuse/hot-swap stage with controlled inrush and OVP
        -> LTC4412/Q1 ideal-diode stage
        -> pwr_s
```

For Rev1 investigation, fit a 25 V to 35 V aluminium electrolytic at the board
input alongside existing ceramics and tune capacitance/ESR from measured ringing;
`47 uF` to `100 uF` and approximately `0.2 ohm` to `0.6 ohm` ESR are starting
values, not released component values. For a confirmed 2S battery with 8.4 V
maximum, investigate a unidirectional TVS with 10 V to 12 V working standoff,
then verify its actual clamp voltage and pulse energy against every downstream
absolute maximum. TVS is secondary protection; damping and controlled inrush
remain required.

Analog Devices AN88 demonstrates that cable inductance and low-ESR ceramics can
produce severe hot-plug overshoot and that an ESR-bearing bulk capacitor or a
series-RC damping branch can suppress it:
https://www.analog.com/media/en/technical-documentation/application-notes/an88f.pdf

Important uncertainty: a nominal 2S step with ordinary two-times overshoot is
still below the LTC4412's 36 V absolute maximum. Ringing is therefore plausible,
not proven. If scoped BAT, VIN, SENSE, VIN-to-SENSE, GATE, and `pwr_s` remain
within limits, investigate Q1 orientation/footprint, U17 joints/ground, connector
bounce, and source-crossover sequencing instead of continuing to replace U17.

## Wall-Powered Evidence

- Bundle: `runs/telemetry/20260715_121854`
- Duration: 300.031 s
- Serial lines: 6,450
- Decoded CAN frames: 6,193
- Unknown frames/warnings: 0 / 0
- Radio sequence: monotonic
- RSSI: -23 to -20 dBm
- SNR: 9 to 12 dB
- Foinse heartbeat error flags: 0
- Battery-path current frame: 261 samples, 15 to 537 mA, mean 168.1 mA
- Servo current frame: 261 samples, 0 to 689 mA, mean 121.7 mA
- Heartbeat uptime is an 8-bit modulo-256-second field; observed drops were synchronized wraps, not resets.
