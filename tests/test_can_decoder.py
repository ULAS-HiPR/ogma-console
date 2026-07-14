from ogma_app.can_decoder import decode_can_frame, decode_can_log_text, parse_can_log_line, summarize_stack_health
from ogma_app.can_layouts import attach_can_ids, load_can_ids, load_payload_layouts
from ogma_app.paths import CAN_FRAMES_HEADER, PAYLOAD_LAYOUTS_CSV


def _frames():
    return attach_can_ids(load_payload_layouts(PAYLOAD_LAYOUTS_CSV), load_can_ids(CAN_FRAMES_HEADER))


def test_decode_power_main_frame() -> None:
    data = bytes.fromhex("e803d00764000000")
    decoded = decode_can_frame(_frames(), 0x300, data)
    assert decoded["frame"] == "POWER_MAIN"
    assert decoded["fields"]["vbat_mv"]["value"] == 1000
    assert decoded["fields"]["ibat_ma"]["value"] == 2000
    assert decoded["fields"]["soc_pct"]["value"] == 100


def test_decode_signed_scaled_imu_accel() -> None:
    data = bytes.fromhex("e80318fc00002a00")
    decoded = decode_can_frame(_frames(), 0x000, data)
    assert decoded["fields"]["ax"]["value"] == 1
    assert decoded["fields"]["ay"]["value"] == -1
    assert decoded["fields"]["timestamp_ms"]["value"] == 42


def test_decode_live_flight_state_contract() -> None:
    decoded = decode_can_frame(_frames(), 0x030, bytes.fromhex("0100ab9700000000"))
    assert decoded["frame"] == "FLIGHT_STATE"
    assert decoded["fields"]["state"]["value"] == 1
    assert decoded["fields"]["flags"]["value"] == 0
    assert decoded["fields"]["timestamp_ms"]["value"] == 0x97AB


def test_decode_live_kalman_and_baro_contracts() -> None:
    kalman = decode_can_frame(_frames(), 0x040, bytes.fromhex("0000ffff0000ab97"))
    assert kalman["frame"] == "KALMANN"
    assert kalman["fields"]["acceleration_m_s2"]["value"] == 0
    assert kalman["fields"]["altitude_m"]["value"] == -1
    assert kalman["fields"]["vspeed_m_s"]["value"] == 0
    assert kalman["fields"]["timestamp_ms"]["value"] == 0x97AB

    baro = decode_can_frame(_frames(), 0x020, bytes.fromhex("e98e01005f0bf1ff"))
    assert baro["fields"]["pressure"]["value"] == 102121
    assert baro["fields"]["temp"]["value"] == 29.11
    assert baro["fields"]["altitude_m"]["value"] == -1.5


def test_parse_can_log_line_formats() -> None:
    assert parse_can_log_line("300#E803D00764000000") == (0x300, bytes.fromhex("e803d00764000000"))
    assert parse_can_log_line("0x300 8 e8 03 d0 07 64 00 00 00") == (
        0x300,
        bytes.fromhex("e803d00764000000"),
    )


def test_decode_can_log_text_warns_on_bad_lines() -> None:
    decoded = decode_can_log_text("bad\n300,E803D00764000000\n", _frames())
    assert decoded["summary"]["frames"] == 1
    assert decoded["summary"]["warnings"]


def test_decode_heartbeat_frame_and_stack_summary() -> None:
    decoded = decode_can_log_text("423#0302052A\n424#04000011\n", _frames())
    assert decoded["summary"]["frames"] == 2
    assert decoded["summary"]["unknown_frames"] == 0
    assert decoded["summary"]["heartbeat_nodes"] == 2
    assert decoded["summary"]["heartbeat_nodes_with_errors"] == 1
    assert decoded["frames"][0]["frame"] == "HEARTBEAT"
    assert decoded["frames"][0]["fields"]["node_name"]["value"] == "lamh"
    assert decoded["stack"]["nodes"]["lamh"]["err_flags"] == ["BUS_OFF", "TX_DROP"]
    assert decoded["stack"]["nodes"]["teachtaire"]["uptime_s"] == 17


def test_summarize_stack_health_uses_latest_heartbeat_per_node() -> None:
    decoded = decode_can_log_text("423#03000001\n423#03020209\n", _frames())
    stack = summarize_stack_health(decoded["frames"])
    assert stack["node_count"] == 1
    assert stack["nodes_with_errors"] == 1
    assert stack["nodes"]["lamh"]["state"] == 2
    assert stack["nodes"]["lamh"]["uptime_s"] == 9


def test_stack_health_preserves_receive_time_for_staleness_checks() -> None:
    frames = decode_can_log_text("423#03000001\n", _frames())["frames"]
    frames[0]["received_s"] = 12.5

    stack = summarize_stack_health(frames)

    assert stack["nodes"]["lamh"]["received_s"] == 12.5
