from __future__ import annotations

import struct
from dataclasses import dataclass


IDENTITY_MAGIC = 0x4F474944
IDENTITY_SCHEMA_VERSION = 1
IDENTITY_SYMBOL = "ogma_board_identity"
IDENTITY_SIZE = 32
IDENTITY_STRUCT = struct.Struct("<IHHIIIIII")

CAP_CAN = 1 << 0
CAP_FLASH = 1 << 1
CAP_SERVO = 1 << 2
CAP_LORA = 1 << 3
CAP_GNSS = 1 << 4
CAP_POWER = 1 << 5
CAP_PYRO = 1 << 6
CAP_GROUNDSTATION = 1 << 7


@dataclass(frozen=True)
class BoardIdentity:
    magic: int
    schema_version: int
    struct_size: int
    board_id: int
    capabilities: int
    firmware_version: int
    firmware_build: int
    reserved0: int
    reserved1: int

    @classmethod
    def parse(cls, data: bytes) -> "BoardIdentity":
        if len(data) < IDENTITY_SIZE:
            raise ValueError(f"identity too short: {len(data)} < {IDENTITY_SIZE}")
        values = IDENTITY_STRUCT.unpack_from(data, 0)
        identity = cls(*values)
        if identity.magic != IDENTITY_MAGIC:
            raise ValueError(f"bad identity magic: 0x{identity.magic:08x}")
        if identity.schema_version != IDENTITY_SCHEMA_VERSION:
            raise ValueError(f"unsupported identity schema: {identity.schema_version}")
        if identity.struct_size != IDENTITY_SIZE:
            raise ValueError(f"unexpected identity size: {identity.struct_size}")
        return identity

    def capability_names(self) -> list[str]:
        names: list[str] = []
        for flag, name in (
            (CAP_CAN, "CAN"),
            (CAP_FLASH, "flash"),
            (CAP_SERVO, "servo"),
            (CAP_LORA, "LoRa"),
            (CAP_GNSS, "GNSS"),
            (CAP_POWER, "power"),
            (CAP_PYRO, "pyro"),
            (CAP_GROUNDSTATION, "groundstation"),
        ):
            if self.capabilities & flag:
                names.append(name)
        return names
