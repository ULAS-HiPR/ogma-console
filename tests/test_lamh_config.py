import json

import pytest

from ogma_app.lamh_config import (
    LamhSafetyConfig,
    load_lamh_safety_config,
    render_lamh_safety_header,
    save_lamh_safety_record,
    write_lamh_safety_header,
)


def test_lamh_safety_header_round_trip(tmp_path) -> None:
    config = LamhSafetyConfig.from_values((60, 70, 80, 90))
    path = write_lamh_safety_header(tmp_path / "lamh_safety_config.h", config)

    assert load_lamh_safety_config(path) == config
    assert "#define LAMH_SAFE_ANGLE_PWM3_DEG 80" in render_lamh_safety_header(config)


@pytest.mark.parametrize("values", ((90, 90, 90), (-1, 90, 90, 90), (90, 90, 90, 91)))
def test_lamh_safety_config_rejects_invalid_angles(values) -> None:
    with pytest.raises(ValueError):
        LamhSafetyConfig.from_values(values)


def test_lamh_safety_record_contains_config_and_readback(tmp_path) -> None:
    config = LamhSafetyConfig.from_values((60, 70, 80, 90))
    header = write_lamh_safety_header(tmp_path / "lamh_safety_config.h", config)
    record_path = save_lamh_safety_record(
        tmp_path / "records",
        config,
        "stm32f072c8t6",
        header,
        {"safe_angle_pwm1_deg": 60},
    )

    record = json.loads(record_path.read_text())
    assert record["angles_deg"] == [60, 70, 80, 90]
    assert record["status"]["safe_angle_pwm1_deg"] == 60
