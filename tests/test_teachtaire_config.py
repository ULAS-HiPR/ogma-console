import pytest
from pathlib import Path

from ogma_app.flight_manifest import RadioPolicy
from ogma_app.teachtaire_config import (
    radio_config_crc32,
    render_teachtaire_radio_header,
    write_teachtaire_radio_header,
)
from ogma_app.verification import verify_teachtaire_radio


def test_radio_policy_header_and_readback_share_crc(tmp_path) -> None:
    config = RadioPolicy(250, 1200, 1500, 6000)
    header = render_teachtaire_radio_header(config)
    path = write_teachtaire_radio_header(tmp_path / "teachtaire_radio_config.h", config)
    status = {
        "radio_config_magic": 0x54435243,
        "radio_config_schema_version": 1,
        "radio_config_crc32": radio_config_crc32(config),
    }

    assert f"0x{radio_config_crc32(config):08X}" in header
    assert path.read_text(encoding="utf-8") == header
    assert verify_teachtaire_radio(config, status).ok


def test_radio_policy_rejects_excessive_core_rate() -> None:
    with pytest.raises(ValueError, match="core"):
        RadioPolicy(core_period_ms=50).validate()


def test_checked_in_radio_header_matches_default_policy() -> None:
    header = (
        Path(__file__).resolve().parents[2]
        / "teachtaire"
        / "firmware"
        / "include"
        / "teachtaire_radio_config.h"
    )

    assert header.read_text(encoding="utf-8") == render_teachtaire_radio_header(RadioPolicy())
