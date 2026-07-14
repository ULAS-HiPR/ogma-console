import os
from pathlib import Path

from ogma_app.groundstation import parse_groundstation_text, save_groundstation_bundle
from ogma_app.serial_capture import read_fd_text


def test_parse_groundstation_csv_lines() -> None:
    parsed = parse_groundstation_text("53.1,-6.2,7,123.4,42,-90\n0,0,0,0,43\n")
    assert parsed["summary"]["records"] == 2
    assert parsed["summary"]["fix_records"] == 1
    assert parsed["summary"]["last_fix_latitude_deg"] == 53.1
    assert parsed["records"][0]["rssi_dbm"] == -90
    assert parsed["records"][1]["fix"] is False


def test_parse_groundstation_jsonl() -> None:
    parsed = parse_groundstation_text('{"lat": 54.0, "lon": -7.0, "sat": 5, "alt": 321.0, "fix_time": 12}\n')
    assert parsed["summary"]["records"] == 1
    assert parsed["summary"]["latest_fix"] is True
    assert parsed["records"][0]["satellites"] == 5


def test_groundstation_warnings_and_bundle(tmp_path: Path) -> None:
    parsed = parse_groundstation_text("header\n53,-6,6,100,10\n")
    assert parsed["summary"]["records"] == 1
    assert parsed["summary"]["warnings"]
    out = save_groundstation_bundle(parsed, tmp_path / "source.txt", tmp_path / "out", raw_text="header\n53,-6,6,100,10\n")
    assert (out / "summary.json").exists()
    assert (out / "raw.txt").exists()
    assert "latitude_deg" in (out / "records.csv").read_text(encoding="utf-8")


def test_read_fd_text_collects_available_bytes() -> None:
    packet = b"53,-6,6,100,10,-91\n"
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, packet)
        os.close(write_fd)
        write_fd = -1
        result = read_fd_text(read_fd, duration_s=0.2)
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)
    assert result.bytes_read == len(packet)
    assert result.text == packet.decode()
