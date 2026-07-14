from ogma_app.lamh_config import LamhSafetyConfig
from ogma_app.mission_config import MissionConfig
from ogma_app.verification import verify_croi_mission, verify_lamh_safety


def test_croi_verification_reports_each_readback_field() -> None:
    config = MissionConfig.defaults()
    report = verify_croi_mission(
        config,
        {
            "mission_config_magic": 0x4F474D43,
            "mission_config_schema_version": 5,
            "mission_config_crc32": config.crc32(),
        },
    )

    assert report.ok
    assert [item.field for item in report.items] == ["magic", "schema_version", "crc32"]


def test_lamh_verification_detects_single_angle_mismatch() -> None:
    config = LamhSafetyConfig.from_values((10, 20, 30, 40))
    report = verify_lamh_safety(
        config,
        {
            "safety_config_magic": 0x4C534346,
            "safety_config_version": 1,
            "safe_angle_pwm1_deg": 10,
            "safe_angle_pwm2_deg": 20,
            "safe_angle_pwm3_deg": 31,
            "safe_angle_pwm4_deg": 40,
        },
    )

    assert not report.ok
    assert [item.field for item in report.items if not item.ok] == ["safe_angle_pwm3_deg"]
