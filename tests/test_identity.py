import struct

from ogma_app.identity import (
    CAP_CAN,
    CAP_FLASH,
    IDENTITY_MAGIC,
    IDENTITY_SCHEMA_VERSION,
    IDENTITY_SIZE,
    BoardIdentity,
)


def test_parse_identity() -> None:
    data = struct.pack(
        "<IHHIIIIII",
        IDENTITY_MAGIC,
        IDENTITY_SCHEMA_VERSION,
        IDENTITY_SIZE,
        1,
        CAP_CAN | CAP_FLASH,
        20260707,
        0,
        0,
        0,
    )
    identity = BoardIdentity.parse(data)
    assert identity.board_id == 1
    assert identity.capability_names() == ["CAN", "flash"]
