import importlib.util
import struct
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "groundstation" / "firmware" / "telemetry_protocol.py"
SPEC = importlib.util.spec_from_file_location("groundstation_telemetry_protocol", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocol)


def _packet(packet_type: int, sequence: int, uptime_ms: int, count: int, flags: int, payload: bytes) -> bytes:
    content = b"OG" + bytes((1, packet_type)) + struct.pack("<HI", sequence, uptime_ms) + bytes((count, flags)) + payload
    return content + struct.pack("<H", protocol.crc16_ccitt(content))


def test_decode_can_bundle() -> None:
    payload = struct.pack("<HB8s", 0x030, 4, b"\x04\x00\x34\x12\x00\x00\x00\x00")
    decoded = protocol.decode_packet(_packet(protocol.TYPE_CAN_BUNDLE, 7, 1234, 1, 2, payload))
    assert decoded["sequence"] == 7
    assert decoded["records"][0]["id"] == 0x030
    assert decoded["records"][0]["data"][:4] == b"\x04\x00\x34\x12"


def test_decode_gps_packet() -> None:
    payload = struct.pack("<iihhBB", 531234567, -61234567, 1234, 56, 8, 1)
    decoded = protocol.decode_packet(_packet(protocol.TYPE_GPS, 8, 2000, 1, 1, payload))
    assert decoded["latitude_deg"] == 53.1234567
    assert decoded["altitude_m"] == 123.4
    assert decoded["fix"] is True


def test_decode_rejects_corruption() -> None:
    packet = bytearray(_packet(protocol.TYPE_TEST, 1, 0, 1, 0, struct.pack("<I", 42)))
    packet[8] ^= 0x01
    try:
        protocol.decode_packet(packet)
    except ValueError as exc:
        assert "CRC" in str(exc)
    else:
        raise AssertionError("corrupt packet accepted")
