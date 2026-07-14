from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import OGMA_ROOT
from .status_blocks import CROI_STATUS, FOINSE_STATUS, LAMH_SERVO_DEBUG, PLEASC_STATUS, TEACHTAIRE_STATUS, StatusBlock


@dataclass(frozen=True)
class FirmwareEnv:
    name: str
    label: str


@dataclass(frozen=True)
class ServoOutput:
    index: int
    label: str
    pca_channel: int


@dataclass(frozen=True)
class BoardProfile:
    board_id: str
    identity_id: int | None
    display_name: str
    repo_dir: Path
    role: str
    firmware_dir: Path | None = None
    envs: tuple[FirmwareEnv, ...] = ()
    default_env: str | None = None
    target_cfg: str = "target/stm32f0x.cfg"
    status_block: StatusBlock | None = None
    servo_outputs: tuple[ServoOutput, ...] = ()
    notes: str = ""

    def env_names(self) -> list[str]:
        return [env.name for env in self.envs]

    def can_build(self) -> bool:
        return self.firmware_dir is not None and bool(self.envs)

    def can_flash(self) -> bool:
        return self.can_build()

    def can_read_status(self) -> bool:
        return self.status_block is not None and self.default_env is not None

    def can_evaluate_health(self) -> bool:
        return self.can_read_status()

    def can_poll_status(self) -> bool:
        return self.can_read_status()

    def can_read_flash_log(self) -> bool:
        return self.board_id == "croi" and self.default_env is not None

    def can_wipe_flash(self) -> bool:
        return self.board_id == "croi" and self.default_env is not None

    def can_command_servo(self) -> bool:
        return self.board_id == "lamh" and self.default_env is not None and bool(self.servo_outputs)

    def can_use_groundstation_usb(self) -> bool:
        return self.board_id == "groundstation"

    def can_run_teachtaire_test(self) -> bool:
        return self.board_id == "teachtaire" and self.default_env is not None

    def can_run_lamh_servo_test(self) -> bool:
        return self.can_command_servo()

    def can_run_foinse_monitor(self) -> bool:
        return self.board_id == "foinse" and self.can_read_status()


PROFILES: tuple[BoardProfile, ...] = (
    BoardProfile(
        board_id="croi",
        identity_id=0x01,
        display_name="Croí",
        repo_dir=OGMA_ROOT / "croi",
        firmware_dir=OGMA_ROOT / "croi" / "firmware",
        role="core flight computer: IMU, baro, flight state, CAN, flash logging",
        envs=(
            FirmwareEnv("stm32f072c8t6", "flight STM32F072"),
            FirmwareEnv("sil", "native SIL tests"),
        ),
        default_env="stm32f072c8t6",
        status_block=CROI_STATUS,
        notes="Read Status checks IMU, baro, CAN, and logger health. Read Croí Flash uses the SRAM mailbox.",
    ),
    BoardProfile(
        board_id="teachtaire",
        identity_id=0x04,
        display_name="Teachtaire",
        repo_dir=OGMA_ROOT / "teachtaire",
        firmware_dir=OGMA_ROOT / "teachtaire" / "firmware",
        role="telemetry/comms: LoRa radio and GNSS",
        envs=(
            FirmwareEnv("teachtaire_flight", "flight GNSS + LoRa heartbeat"),
            FirmwareEnv("teachtaire_lora_tx", "LoRa TX test"),
            FirmwareEnv("teachtaire_lora_rx", "LoRa RX test"),
        ),
        default_env="teachtaire_flight",
        status_block=TEACHTAIRE_STATUS,
    ),
    BoardProfile(
        board_id="lamh",
        identity_id=0x03,
        display_name="Lámh",
        repo_dir=OGMA_ROOT / "lamh",
        firmware_dir=OGMA_ROOT / "lamh" / "firmware",
        role="servo/canards board",
        envs=(
            FirmwareEnv("stm32f072c8t6", "flight STM32F072"),
            FirmwareEnv("stm32f072c8t6_input_probe", "input pin probe"),
        ),
        default_env="stm32f072c8t6",
        status_block=LAMH_SERVO_DEBUG,
        servo_outputs=(
            ServoOutput(1, "PWM1", 0),
            ServoOutput(2, "PWM2", 2),
            ServoOutput(3, "PWM3", 4),
            ServoOutput(4, "PWM4", 6),
        ),
        notes="Schematic maps PWM1/PWM2/PWM3/PWM4 to PCA9685 channels 0/2/4/6.",
    ),
    BoardProfile(
        board_id="foinse",
        identity_id=0x06,
        display_name="Foinse",
        repo_dir=OGMA_ROOT / "foinse",
        firmware_dir=OGMA_ROOT / "foinse" / "firmware",
        role="power/source board: 5 V generation and current sensor ADCs",
        envs=(FirmwareEnv("stm32f072c8t6", "flight STM32F072"),),
        default_env="stm32f072c8t6",
        status_block=FOINSE_STATUS,
        notes="Firmware reads ACS71240 Sense1/Sense2 on PA3/PA4 and reports raw ADC, mV, and mA.",
    ),
    BoardProfile(
        board_id="pleasc",
        identity_id=0x02,
        display_name="Pleasc",
        repo_dir=OGMA_ROOT / "pleasc",
        firmware_dir=OGMA_ROOT / "pleasc" / "firmware",
        role="pyro/deployment board",
        envs=(
            FirmwareEnv("stm32f072c8t6", "flight locked / inert"),
            FirmwareEnv("stm32f072c8t6_rev1_pyro", "Rev1 accepted-risk pyro"),
        ),
        default_env="stm32f072c8t6",
        status_block=PLEASC_STATUS,
        notes="Croí-heartbeat-gated pyro arm/fire status, continuity, and CAN diagnostics.",
    ),
    BoardProfile(
        board_id="groundstation",
        identity_id=0x64,
        display_name="Groundstation",
        repo_dir=OGMA_ROOT / "groundstation",
        firmware_dir=OGMA_ROOT / "groundstation" / "firmware",
        role="LoRa receiver and field display",
        notes="Current firmware is CircuitPython; app profile is for telemetry mode.",
    ),
)


PROFILES_BY_ID = {profile.board_id: profile for profile in PROFILES}
PROFILES_BY_IDENTITY = {
    profile.identity_id: profile for profile in PROFILES if profile.identity_id is not None
}


def profile_for(board_id: str) -> BoardProfile:
    try:
        return PROFILES_BY_ID[board_id]
    except KeyError as exc:
        raise KeyError(f"unknown board profile: {board_id}") from exc


def servo_output_for(profile: BoardProfile, index: int) -> ServoOutput:
    for output in profile.servo_outputs:
        if output.index == index:
            return output
    choices = ", ".join(str(output.index) for output in profile.servo_outputs)
    raise ValueError(f"{profile.display_name} servo output must be one of: {choices}")
