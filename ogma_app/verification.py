from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .lamh_config import LAMH_SAFETY_CONFIG_MAGIC, LAMH_SAFETY_CONFIG_VERSION, LamhSafetyConfig
from .mission_config import (
    CROI_MISSION_CONFIG_MAGIC,
    CROI_MISSION_CONFIG_SCHEMA_VERSION,
    LoggingPolicy,
    MissionConfig,
    RecoveryFallbackConfig,
)
from .flight_manifest import RadioPolicy
from .teachtaire_config import (
    TEACHTAIRE_RADIO_CONFIG_MAGIC,
    TEACHTAIRE_RADIO_CONFIG_SCHEMA_VERSION,
    radio_config_crc32,
)


@dataclass(frozen=True)
class VerificationItem:
    field: str
    expected: Any
    actual: Any

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


@dataclass(frozen=True)
class ConfigVerification:
    target: str
    items: tuple[VerificationItem, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items)

    def require_ok(self) -> None:
        failed = [item for item in self.items if not item.ok]
        if failed:
            details = ", ".join(
                f"{item.field}: expected {item.expected!r}, got {item.actual!r}"
                for item in failed
            )
            raise RuntimeError(f"{self.target} config readback failed: {details}")


def verify_croi_mission(
    config: MissionConfig,
    status: dict[str, Any],
    recovery: RecoveryFallbackConfig | None = None,
    logging: LoggingPolicy | None = None,
) -> ConfigVerification:
    return ConfigVerification(
        "Croí mission",
        (
            VerificationItem("magic", CROI_MISSION_CONFIG_MAGIC, int(status.get("mission_config_magic", 0))),
            VerificationItem(
                "schema_version",
                CROI_MISSION_CONFIG_SCHEMA_VERSION,
                int(status.get("mission_config_schema_version", 0)),
            ),
            VerificationItem("crc32", config.crc32(recovery, logging), int(status.get("mission_config_crc32", 0))),
        ),
    )


def verify_lamh_safety(config: LamhSafetyConfig, status: dict[str, Any]) -> ConfigVerification:
    items = [
        VerificationItem("magic", LAMH_SAFETY_CONFIG_MAGIC, int(status.get("safety_config_magic", 0))),
        VerificationItem("version", LAMH_SAFETY_CONFIG_VERSION, int(status.get("safety_config_version", 0))),
    ]
    items.extend(
        VerificationItem(
            f"safe_angle_pwm{index}_deg",
            expected,
            int(status.get(f"safe_angle_pwm{index}_deg", -1)),
        )
        for index, expected in enumerate(config.angles_deg, start=1)
    )
    return ConfigVerification("Lámh safety", tuple(items))


def verify_teachtaire_radio(config: RadioPolicy, status: dict[str, Any]) -> ConfigVerification:
    return ConfigVerification(
        "Teachtaire radio",
        (
            VerificationItem(
                "magic",
                TEACHTAIRE_RADIO_CONFIG_MAGIC,
                int(status.get("radio_config_magic", 0)),
            ),
            VerificationItem(
                "schema_version",
                TEACHTAIRE_RADIO_CONFIG_SCHEMA_VERSION,
                int(status.get("radio_config_schema_version", 0)),
            ),
            VerificationItem(
                "crc32",
                radio_config_crc32(config),
                int(status.get("radio_config_crc32", 0)),
            ),
        ),
    )
