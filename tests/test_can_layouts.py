from ogma_app.can_layouts import attach_can_ids, load_can_ids, load_payload_layouts
from ogma_app.paths import CAN_FRAMES_HEADER, PACKAGE_DATA_ROOT, PAYLOAD_LAYOUTS_CSV


def test_payload_layouts_include_expected_frames() -> None:
    assert PAYLOAD_LAYOUTS_CSV.parent == PACKAGE_DATA_ROOT
    frames = load_payload_layouts(PAYLOAD_LAYOUTS_CSV)
    assert "IMU_ACCEL" in frames
    assert "POWER_MAIN" in frames
    assert frames["BARO"].fields[0].field_name == "pressure"


def test_can_ids_attach_from_header() -> None:
    ids = load_can_ids(CAN_FRAMES_HEADER)
    frames = attach_can_ids(load_payload_layouts(PAYLOAD_LAYOUTS_CSV), ids)
    assert frames["IMU_ACCEL"].can_id == 0x000
    assert frames["POWER_SERVO"].can_id == 0x310
