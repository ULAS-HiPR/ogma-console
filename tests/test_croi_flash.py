import struct

from ogma_app.croi_flash import (
    FLASH_LOG_COMMITTED,
    FLASH_LOG_MAGIC,
    FLASH_LOG_UNCOMMITTED,
    FLASH_LOG_VERSION,
    HEADER,
    PAYLOAD_FLIGHT_DATA,
    PAYLOAD_SECONDARY_FLIGHT_DATA,
    align4,
    fnv1a,
    header_checksum,
    parse_croi_flash_dump,
)


def _record(payload: bytes, payload_type: int = PAYLOAD_FLIGHT_DATA, payload_version: int = 1) -> bytes:
    header = bytearray(HEADER.size)
    struct.pack_into("<I", header, 0, FLASH_LOG_MAGIC)
    struct.pack_into("<H", header, 4, FLASH_LOG_VERSION)
    struct.pack_into("<H", header, 6, HEADER.size)
    struct.pack_into("<I", header, 8, 7)
    struct.pack_into("<I", header, 12, 0)
    struct.pack_into("<I", header, 16, 1234)
    struct.pack_into("<H", header, 20, payload_type)
    struct.pack_into("<H", header, 22, payload_version)
    struct.pack_into("<I", header, 24, len(payload))
    struct.pack_into("<I", header, 28, fnv1a(payload))
    struct.pack_into("<I", header, 36, FLASH_LOG_UNCOMMITTED)
    struct.pack_into("<I", header, 32, header_checksum(header))
    struct.pack_into("<I", header, 36, FLASH_LOG_COMMITTED)
    data = bytes(header) + payload
    return data + (b"\xff" * (align4(len(data)) - len(data)))


def test_parse_croi_flight_record() -> None:
    payload = bytearray(60)
    struct.pack_into("<I", payload, 0, 99)
    struct.pack_into("<f", payload, 4, 123.5)
    struct.pack_into("<i", payload, 20, 101325)
    struct.pack_into("<h", payload, 56, 3)
    parsed = parse_croi_flash_dump(_record(bytes(payload)) + b"\xff" * 64)
    assert parsed["summary"]["records"] == 1
    assert parsed["summary"]["flight_records"] == 1
    assert parsed["flight"][0]["time_ms"] == 99
    assert parsed["flight"][0]["state"] == 3


def test_parse_croi_pyro_event_record() -> None:
    payload = bytearray(64)
    struct.pack_into("<I", payload, 44, 5000)
    struct.pack_into("<H", payload, 48, 0x1234)
    struct.pack_into("<H", payload, 50, 17)
    struct.pack_into("<BBBBBBB", payload, 52, 1, 3, 0, 0, 2, 3, 1)
    parsed = parse_croi_flash_dump(
        _record(bytes(payload), PAYLOAD_SECONDARY_FLIGHT_DATA, 2) + b"\xff" * 64
    )
    assert parsed["summary"]["event_records"] == 1
    assert parsed["events"][0]["pyro_sequence"] == 17
    assert parsed["events"][0]["pyro_channel"] == 1
