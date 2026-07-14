from pathlib import Path

from ogma_app.telemetry import (
    MixedTelemetryAccumulator,
    load_mixed_telemetry_session,
    parse_mixed_telemetry_text,
    save_mixed_telemetry_bundle,
    save_mixed_telemetry_session,
)
from ogma_app.can_layouts import attach_can_ids, load_can_ids, load_payload_layouts
from ogma_app.paths import CAN_FRAMES_HEADER, PAYLOAD_LAYOUTS_CSV


def _frames():
    return attach_can_ids(load_payload_layouts(PAYLOAD_LAYOUTS_CSV), load_can_ids(CAN_FRAMES_HEADER))


def test_parse_mixed_telemetry_gps_and_can() -> None:
    parsed = parse_mixed_telemetry_text(
        "53.1,-6.2,7,123.4,42,-90\n"
        "423#0302052A\n"
        '{"lat": 54.0, "lon": -7.0, "sat": 5, "alt": 321.0, "fix_time": 12}\n',
        _frames(),
    )
    assert parsed["summary"]["gps_records"] == 2
    assert parsed["summary"]["gps_fix_records"] == 2
    assert parsed["summary"]["can_frames"] == 1
    assert parsed["summary"]["heartbeat_nodes"] == 1
    assert parsed["can"]["stack"]["nodes"]["lamh"]["err_flags"] == ["BUS_OFF", "TX_DROP"]
    assert parsed["groundstation"]["summary"]["latest_altitude_m"] == 321.0


def test_parse_mixed_telemetry_warns_on_unclassified_line() -> None:
    parsed = parse_mixed_telemetry_text("hello\n300#E803D00764000000\n", _frames())
    assert parsed["summary"]["can_frames"] == 1
    assert parsed["summary"]["warnings"]


def test_save_mixed_telemetry_bundle(tmp_path: Path) -> None:
    parsed = parse_mixed_telemetry_text("53,-6,6,100,10\n423#03000001\n", _frames())
    out = save_mixed_telemetry_bundle(parsed, "source.log", tmp_path, raw_text="raw")
    assert (out / "summary.json").exists()
    assert (out / "groundstation.json").exists()
    assert (out / "groundstation_records.csv").exists()
    assert (out / "can.json").exists()
    assert (out / "raw.txt").read_text(encoding="utf-8") == "raw"
    assert load_mixed_telemetry_session(out)["summary"] == parsed["summary"]


def test_incremental_telemetry_handles_split_lines() -> None:
    accumulator = MixedTelemetryAccumulator(_frames())
    accumulator.feed("53.1,-6.2,7,123.4")
    accumulator.feed(",42,-90\r\n423#030205")
    accumulator.feed("2A\npartial")
    accumulator.finish()

    parsed = accumulator.snapshot()
    assert parsed["summary"]["gps_records"] == 1
    assert parsed["summary"]["can_frames"] == 1
    assert parsed["summary"]["heartbeat_nodes"] == 1
    assert len(parsed["summary"]["warnings"]) == 1


def test_incremental_telemetry_bounds_display_history_without_losing_totals() -> None:
    accumulator = MixedTelemetryAccumulator(_frames(), history_limit=2)
    accumulator.feed("300#E803D00764000000\n" * 3)

    parsed = accumulator.snapshot()
    assert parsed["summary"]["can_frames"] == 3
    assert parsed["can"]["summary"]["frames"] == 3
    assert len(parsed["can"]["frames"]) == 2


def test_live_session_writer_keeps_existing_raw_file(tmp_path: Path) -> None:
    parsed = parse_mixed_telemetry_text("423#03000001\n", _frames())
    out = tmp_path / "live"
    out.mkdir()
    (out / "raw.txt").write_text("streamed", encoding="utf-8")

    save_mixed_telemetry_session(parsed, "serial:test", out)

    assert (out / "raw.txt").read_text(encoding="utf-8") == "streamed"
    assert (out / "summary.json").exists()
