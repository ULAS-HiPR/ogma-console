# Ogma Console

Ogma Console is the desktop interface for Ogma, the modular flight-computer stack developed by ULAS HiPR. It gives the team one place to identify, configure, build, flash, inspect, and test every board in the stack, then switches roles to become a live ground telemetry console when connected to the Ogma groundstation.

The application talks to flight boards through an ST-Link over SWD. No debug UART is required: firmware exposes versioned status blocks and tightly controlled command mailboxes in SRAM, which the console reads while briefly halting the target. Only the board physically connected to the selected ST-Link is controlled.

## What It Does

- Detects the connected Ogma board from its firmware identity and SRAM status block.
- Builds and flashes the correct PlatformIO environment for that board.
- Reads live health, sensor, CAN, radio, logger, power, actuator, and recovery state.
- Polls status without redirecting or replacing the board's normal CAN responsibilities.
- Configures a complete flight manifest: detection thresholds, recovery channels, airbrake timing, failsafe angles, logging policy, radio schedule, and preflight requirements.
- Replays recorded or synthetic flight profiles through Croi's actual C++ flight-state and airbrake logic.
- Reads, validates, exports, and plots Croi's onboard flash log.
- Runs Teachtaire LoRa and GNSS diagnostics and Lamh servo tests.
- Decodes live CAN and GPS telemetry bridged by the groundstation over USB.
- Produces hashed flight packages containing configuration, firmware, provenance, and preflight evidence.

## Ogma Boards

| Board | Role | Console support |
| --- | --- | --- |
| **Croi** | Core flight computer, state estimation, sequencing, CAN coordinator, blackbox logger | Mission configuration, health polling, sensor plots, CAN monitor, flash read/wipe, replay and recovery inspection |
| **Teachtaire** | SX1272 LoRa telemetry and u-blox GNSS | Radio schedule, GNSS/LoRa status, TX/RX tests, packet counters and configuration readback |
| **Lamh** | PCA9685 actuator and airbrake controller | Per-output commands, four failsafe angles, physical-arm status, timeout/failsafe inspection and configuration readback |
| **Foinse** | Stack power delivery and current sensing | Battery/board and servo-rail current monitoring, CAN and regulator diagnostics |
| **Pleasc** | Four-channel pyrotechnic controller | Inert and Rev 1 firmware environments, continuity/arm/fire status, guarded channel assignment and recovery-event monitoring |
| **Groundstation** | LoRa receiver, local display and USB telemetry bridge | Live telemetry capture, CAN/GPS decoding, plots, fault tracking and session archival |

## How It Works

### SWD board interface

Each STM32 firmware image publishes an `ogma_board_identity` structure and a board-specific passive status structure at fixed SRAM symbols. Ogma Console uses OpenOCD and the ELF symbol table to locate and decode these structures. Passive reads do not enable control paths.

Bench actions use separate mailboxes with explicit magic values, versions, commands, nonces, leases, and firmware-side state guards. Croi uses this mechanism for flash extraction; Lamh uses it for individual servo commands. Mailbox inactivity leaves normal flight behavior unchanged.

### CAN and telemetry

Ogma boards continue communicating over the stack's 500 kbit/s CAN bus while the console observes one board over SWD. Croi exposes stack-level CAN health and mirrors recovery state. Teachtaire bundles selected canonical CAN frames into the radio protocol. The groundstation validates those packets, updates its local display, and emits app-compatible CAN/GPS records over USB.

Ogma Console decodes frame layouts from the shared CAN definitions, keeps full raw telemetry on disk, and limits only the on-screen history so long sessions remain responsive.

### Flight configuration

The Mission view produces a versioned, hashed flight manifest. Configuration is validated in several layers:

1. Bounded Python data models reject invalid values and unsafe combinations.
2. Generated firmware headers contain independent compile-time guards.
3. Croi, Lamh, and Teachtaire report configuration magic, schema, and CRC values over SWD.
4. Preflight compares those readbacks with the selected manifest and live stack evidence.

Drogue and main cannot share a Pleasc channel. The UI removes the channel already assigned to the other event, while model validation and generated C++ checks independently reject duplicate assignments.

Lamh's four failsafe angles are part of the same manifest. They can be edited under **Mission > Airbrake**, flashed while Lamh is connected, and verified against firmware readback before the manifest is accepted.

### Native mission replay

Replay does not maintain a second Python implementation of flight behavior. The console compiles a small native harness against Croi's real `FlightPhaseLogic` and `AirbrakeLogic` headers, then feeds it timestamped acceleration, velocity, and altitude samples. This keeps desktop replay behavior tied to the code that is built into Croi.

### Flight packages

A flight package is a ZIP archive containing:

- the canonical flight manifest and its SHA-256;
- generated Croi, Lamh, and Teachtaire configuration headers;
- required firmware BIN and ELF files;
- board commit and submodule provenance;
- preflight checks and results;
- a SHA-256 index covering every packaged file.

Packages can be inspected offline. Dirty repositories, missing binaries, failed preflight checks, or hash mismatches remain visible in package metadata.

## Installation

### Requirements

- Python 3.11 or newer with Tk support
- PlatformIO
- OpenOCD
- `st-info` / ST-Link tools
- a C++17 compiler for native mission replay
- an ST-Link V2 or compatible probe for flight-board access

Ogma Console expects the board repositories to share a parent directory:

```text
ogma/
├── ogma-console/
├── croi/
├── teachtaire/
├── lamh/
├── foinse/
├── pleasc/
└── groundstation/
```

Install the console in editable mode:

```sh
cd ogma-console
python3 -m pip install -e .
```

If the repositories are elsewhere, point the app at their parent workspace:

```sh
export OGMA_ROOT=/path/to/ogma
```

Source checkouts write session data to `runs/`. Installed packages use the operating system's user data directory. Override either location when needed:

```sh
export OGMA_RUNS_ROOT=/path/to/ogma-runs
```

## Running

Start the GUI:

```sh
ogma-console
```

or:

```sh
python3 -m ogma_app
```

Useful CLI operations:

```sh
ogma-console --list
ogma-console --doctor all
ogma-console --probe
ogma-console --detect
ogma-console --validate croi
ogma-console --build teachtaire --env teachtaire_flight
ogma-console --flash lamh
ogma-console --status foinse
ogma-console --poll-status croi --duration 30 --interval 0.5
ogma-console --teachtaire-test lora_tx --duration 30
ogma-console --lamh-servo-test 1 --angles 0,30,60,90
ogma-console --import-telemetry telemetry.log
ogma-console --decode-can-log can.log --can-out decoded.json
```

Use `ogma-console --help` for the complete command list.

## Main Workflows

### Bring up a board

1. Connect one ST-Link to the target board.
2. Select the expected board or use **Detect SWD**.
3. Run **Doctor** to check tools, repositories, dependency pins, firmware artifacts, and required symbols.
4. Run **Validate** for probe, identity, status, and health evidence.
5. Build or flash the selected firmware environment.

### Configure a flight

1. Set flight, recovery, airbrake, Lamh failsafe, radio, and logging policy.
2. Save the manifest and run synthetic or CSV replay.
3. Connect each configurable board and flash its locked configuration.
4. Read each board's status to collect configuration evidence.
5. Start groundstation telemetry and review **Preflight**.
6. Build and archive the flight package.

### Recover Croi data

1. Connect Croi over SWD.
2. Use **Read Croi Flash** to request bounded chunks from the logger mailbox.
3. The console validates headers and record CRCs, then saves raw binary, JSON, and CSV output.
4. Reopen saved sessions later for plotting and analysis.

### Use the groundstation

1. Connect the groundstation board over USB.
2. Start live telemetry from its `/dev/cu.usbmodem...` device.
3. Inspect decoded flight, IMU, power, link, CAN, GPS, recovery, and fault views.
4. Stop the stream to finalize the raw and decoded session bundle.

## Data And Evidence

Runtime output is written below the configured runs directory and is intentionally excluded from Git in source checkouts. Depending on the operation, a bundle may include raw serial data, flash images, decoded JSON, CSV tables, health reports, manifest records, plots, firmware provenance, and validation results.

The console keeps action and evidence paths separate: passive status can be read continuously, while build/flash, actuator, erase, and configuration operations require explicit commands and board-specific checks.

## Development

Run the test suite:

```sh
python3 -m pytest -q
```

Build a wheel:

```sh
python3 -m pip wheel . --no-deps --no-build-isolation
```

The package includes the native replay source, CAN frame contract, payload layouts, dependency lock, and `ogma-console` entry point. `ogma-console --doctor all` verifies every board against the packaged shared-dependency commits.

## Related Repositories

- [Croi](https://github.com/ULAS-HiPR/croi)
- [Teachtaire](https://github.com/ULAS-HiPR/teachtaire)
- [Lamh](https://github.com/ULAS-HiPR/lamh)
- [Foinse](https://github.com/ULAS-HiPR/foinse)
- [Pleasc](https://github.com/ULAS-HiPR/pleasc)
- [Groundstation](https://github.com/ULAS-HiPR/gs_receiver)
- [Comheadan](https://github.com/ULAS-HiPR/comheadan)
- [Braiteoiri](https://github.com/ULAS-HiPR/braiteoiri)
