# Ogma Project Log

Append-only record of consequential progress, decisions, incidents, uncertainty, and next checkpoints for Ogma. This preserves what the team knew and decided at a point in time. Detailed test evidence belongs in [HIL testing](testing/README.md).

## Logging Rules

- Append entries in chronological order. Once committed, do not rewrite an old entry to match later understanding.
- Correct or supersede an old entry with a new dated amendment that links back to it.
- Separate verified evidence, decisions, assumptions, open risks, and proposed work.
- Link test reports, configuration CRCs, and commits where available.
- Treat a bench pass as evidence, not automatic flight clearance.
- Skip routine activity. Log work that changes architecture, safety, operation, scope, or the next meaningful checkpoint.

## Entry Template

```markdown
## YYYY-MM-DD - Short title

**Type:** decision | checkpoint | experiment | incident | amendment

**Context**

Why this entry exists.

**Progress / decision**

What changed or was decided.

**Evidence**

What was directly observed, tested, or reviewed.

**Consequences**

What this changes for the project.

**Open risks / uncertainty**

What is not yet known or accepted.

**Next checkpoints**

Concrete work required next.
```

## 2026-07-16 - Integrated baseline after first HIL campaign

**Type:** checkpoint and decision

**Context**

Ogma is a modular rocket avionics stack. Croi is the central flight computer and logger; Teachtaire handles SX1272 LoRa telemetry and u-blox GNSS; Foinse handles stack power and current sensing; Lamh controls PCA9685 servo outputs and the airbrake; Pleasc controls pyrotechnic channels; and the groundstation receives LoRa telemetry and bridges it to Ogma Console over USB.

Ogma Console is the shared SWD diagnostic, configuration, data-recovery, and ground telemetry application. It controls only the board attached to the selected ST-Link. Normal inter-board communication remains on the 500 kbit/s CAN bus.

**Progress / decisions**

- Keep passive, versioned SRAM status in standard firmware. Guard action mailboxes with explicit commands, nonces, leases, and firmware state checks. Do not require a separate debug image for routine diagnostics or Croi data recovery.
- Croi remains the central monitor, state estimator, broadcaster, sequencer, and blackbox logger.
- Lamh Rev 1 airbrake behavior is timed deploy and stow, with a physical arm input, short command lease, and return to configured safe angles. Current angles are provisional until mechanism-level testing is complete.
- Pleasc Rev 1 uses an explicitly accepted-risk architecture: external RBF/pyro-power disconnect, two-stage arm then fire commands, command freshness, Croi and flight-state gating, short arm lease, finite fire pulse, continuity checks, watchdog behavior, boot-safe outputs, and event logging. It remains fire-blocked until assembly and inert-load HIL are complete.
- Do not merge board flight-hardening branches to board `main` until integrated HIL and team code review are complete.
- Keep bench evidence, release criteria, and unresolved risk distinct in all HIL reports.

**Evidence**

- A wall-powered Croi, Foinse, Teachtaire, and Lamh stack operated over CAN. Croi reported three peer nodes.
- A five-minute concurrent SWD and telemetry soak passed. The final USB telemetry run delivered 2,115 of 2,115 packets with no missing, duplicate, or reordered sequence numbers. Teachtaire recorded zero CAN receive-queue overruns across 25,236 received frames.
- Croi mission schema 6, configuration CRC `0x44A2AA23`, flash logging, reset recovery, and bounded SWD flash extraction were exercised. Recovered records parsed without warnings.
- Lamh output 1 used safe angle 0 degrees; outputs 2-4 used 90 degrees. A DS3225MG completed three timed deploy/stow sequences. Inactive-arm commands were rejected. Resetting Croi during deployment caused Lamh to return safe. The reset request completed in 13 ms and safe stow was observed immediately, but physical/PWM response latency was not instrumented. Two redundant I2C write defects were fixed and hardware-verified.
- Console commit `a9f1394`, Lamh commit `83e540b`, Teachtaire commit `f8652e3`, and groundstation commit `e44f52c` contain the reviewed baseline from this campaign.
- Ogma Console's test suite passed 160 tests. Relevant GitHub Actions runs passed.
- Detailed evidence: [airbrake HIL](testing/results/2026-07-15-croi-lamh-airbrake-hil.md), [telemetry HIL](testing/results/2026-07-15-teachtaire-groundstation.md), [integrated soak](testing/results/2026-07-15-integrated-stack-soak.md), and [Foinse power incident](testing/results/2026-07-15-foinse-power-path.md).

**Consequences**

Ogma is materially integrated, but this checkpoint is not flight clearance. Verified work lives on Ogma Console `main` and board flight-hardening branches. Future consequential changes will be appended here; individual test runs remain in the HIL record.

**Open risks / uncertainty**

- Foinse's battery path stopped working after battery hot-plugging; wall input still works. Root cause and protection changes need DMM and oscilloscope evidence.
- Lamh's PCA9685 OE is tied low on Rev 1. An STM reset cannot hardware-disable PWM; reset behavior and response latency still need direct instrumentation under representative mechanical load.
- Pleasc is incomplete, lacks its optocouplers, and has no inert-load HIL evidence.
- Outdoor GNSS fix, RF range, and controlled packet-loss qualification remain open.
- Foinse current calibration remains open.
- Final safe/deploy angles and the real airbrake mechanism load remain undefined.
- Brownout, repeated power-cycle, endurance, CAN fault-injection, and full integrated dress-rehearsal tests remain incomplete.

**Next checkpoints**

1. Reset Lamh's MCU while an armed deployment is active; measure PWM and safe-return latency directly.
2. Capture raw CAN loss and instrument actuator response independently of SWD status.
3. Diagnose and repair Foinse's battery path; reproduce hot-plug behavior with protection instrumentation.
4. Run outdoor GNSS and controlled radio range/loss tests.
5. Assemble Pleasc and complete inert-load arm/fire/fault HIL before any energetic testing.
6. Run nominal and adversarial flight replays, then a full-stack dress rehearsal with archived evidence.
