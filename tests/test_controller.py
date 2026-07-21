import struct
from dataclasses import replace
from pathlib import Path

import pytest

from ogma_app.controller import DetectionResult, OgmaController
from ogma_app.boards import profile_for
from ogma_app.identity import BoardIdentity, CAP_POWER, IDENTITY_MAGIC, IDENTITY_SIZE
from ogma_app.lamh_config import LamhSafetyConfig
from ogma_app.mission_config import MissionConfig


class DummySession:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class StatusSession(DummySession):
    def __init__(self, data: bytes | None = None) -> None:
        super().__init__()
        self.data = data

    def read_bytes(self, _address: int, _count: int) -> bytes:
        if self.data is None:
            raise RuntimeError("transient mdw failure")
        return self.data


def test_flash_closes_openocd_session_before_upload(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    session = DummySession()
    controller.session = session  # type: ignore[assignment]
    controller.session_key = (Path("/tmp/firmware"), "target/stm32f0x.cfg", None)
    calls: list[tuple[Path, str]] = []

    def fake_flash_env(
        firmware_dir: Path, env: str, _log, upload_port=None, target_cfg=None
    ) -> None:
        calls.append((firmware_dir, env))

    monkeypatch.setattr("ogma_app.controller.flash_env", fake_flash_env)
    controller.flash("foinse", "stm32f072c8t6")

    assert session.stopped
    assert controller.session is None
    assert calls == [(profile_for("foinse").firmware_dir, "stm32f072c8t6")]


def test_flash_selects_configured_stlink(monkeypatch) -> None:
    controller = OgmaController(
        Path("/tmp/ogma-test"),
        lambda _text: None,
        "066EFF495365495067182508",
    )
    calls: list[tuple[str | None, str | None]] = []

    def fake_flash_env(
        _firmware_dir: Path, _env: str, _log, upload_port=None, target_cfg=None
    ) -> None:
        calls.append((upload_port, target_cfg))

    monkeypatch.setattr("ogma_app.controller.flash_env", fake_flash_env)
    controller.flash("foinse", "stm32f072c8t6")

    assert calls == [("066EFF495365495067182508", "target/stm32f0x.cfg")]


def test_status_read_reconnects_once_after_transient_failure(monkeypatch) -> None:
    logs: list[str] = []
    controller = OgmaController(Path("/tmp/ogma-test"), logs.append)
    profile = profile_for("foinse")
    assert profile.status_block is not None
    valid_status = bytearray(profile.status_block.size)
    struct.pack_into("<II", valid_status, 0, profile.status_block.magic, 5)
    sessions = iter((StatusSession(), StatusSession(bytes(valid_status))))

    def next_session(_profile):
        session = next(sessions)
        controller.session = session  # type: ignore[assignment]
        return session

    monkeypatch.setattr(
        "ogma_app.controller.symbol_addresses",
        lambda *_args, **_kwargs: {profile.status_block.symbol: 0x20000000},
    )
    monkeypatch.setattr(controller, "_session_for", next_session)
    monkeypatch.setattr("ogma_app.controller.time.sleep", lambda _seconds: None)

    status = controller._read_profile_status(profile)

    assert status["magic"] == profile.status_block.magic
    assert any("reconnecting once" in message for message in logs)


def test_flash_detected_uses_detected_profile_default_env(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    profile = profile_for("lamh")
    detection = DetectionResult(profile, None, "test identity")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(controller, "detect", lambda enrich_status=True: detection)
    monkeypatch.setattr(controller, "flash", lambda board_id, env: calls.append((board_id, env)))

    result = controller.flash_detected()

    assert result.detection is detection
    assert result.env == profile.default_env
    assert calls == [(profile.board_id, profile.default_env)]


def test_wipe_croi_flash_restore_flashes_wipe_waits_then_restores(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    calls: list[tuple[str, str]] = []
    builds: list[tuple[str, str]] = []
    statuses = [
        {
            "version": 3,
            "flash_init_ok": 0,
            "logger_fault_latched": 1,
            "logger_flash_status": 6,
            "flash_wipe_state": 1,
            "flash_wipe_progress_percent": 50,
        },
        {
            "version": 3,
            "flash_init_ok": 1,
            "logger_fault_latched": 0,
            "logger_flash_status": 0,
            "flash_wipe_state": 2,
            "flash_wipe_progress_percent": 100,
        },
    ]

    monkeypatch.setattr(controller, "build", lambda board_id, env: builds.append((board_id, env)))
    monkeypatch.setattr(controller, "flash", lambda board_id, env: calls.append((board_id, env)))
    monkeypatch.setattr(controller, "_read_profile_status", lambda profile, env, build_if_missing=False: statuses.pop(0))
    monkeypatch.setattr("ogma_app.controller.time.sleep", lambda _seconds: None)

    result = controller.wipe_croi_flash_restore(timeout_s=10.0)

    assert builds == [("croi", "stm32f072c8t6")]
    assert calls == [("croi", "stm32f072c8t6_flash_wipe"), ("croi", "stm32f072c8t6")]
    assert result.status["flash_init_ok"] == 1
    assert result.wipe_env == "stm32f072c8t6_flash_wipe"
    assert result.flight_env == "stm32f072c8t6"


def test_croi_wipe_v3_requires_explicit_completion() -> None:
    status = {
        "version": 3,
        "flash_init_ok": 1,
        "logger_fault_latched": 0,
        "logger_flash_status": 0,
        "flash_wipe_state": 1,
        "flash_wipe_progress_percent": 95,
    }

    assert not OgmaController._croi_wipe_complete(status)


def test_wipe_croi_flash_restore_restores_after_wipe_timeout(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(controller, "build", lambda _board_id, _env: None)
    monkeypatch.setattr(controller, "flash", lambda board_id, env: calls.append((board_id, env)))
    times = iter((0.0, 2.0))
    monkeypatch.setattr("ogma_app.controller.time.time", lambda: next(times))

    with pytest.raises(RuntimeError, match="flight image restored"):
        controller.wipe_croi_flash_restore(timeout_s=1.0)

    assert calls == [("croi", "stm32f072c8t6_flash_wipe"), ("croi", "stm32f072c8t6")]


def test_detect_enriches_identity_match_with_status(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    identity = BoardIdentity(IDENTITY_MAGIC, 1, IDENTITY_SIZE, 0x06, CAP_POWER, 1, 2, 0, 0)
    status = {"magic": 0x464F494E, "adc_ok": 1}

    monkeypatch.setattr(controller, "detect_identity", lambda: identity)
    monkeypatch.setattr(controller, "_read_profile_status", lambda profile, env=None, build_if_missing=True: status)

    result = controller.detect()

    assert result.profile == profile_for("foinse")
    assert result.identity is identity
    assert result.status is status
    assert result.reason == "matched ogma_board_identity + status block"


def test_detect_can_skip_identity_status_enrichment(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    identity = BoardIdentity(IDENTITY_MAGIC, 1, IDENTITY_SIZE, 0x06, CAP_POWER, 1, 2, 0, 0)

    monkeypatch.setattr(controller, "detect_identity", lambda: identity)
    monkeypatch.setattr(
        controller,
        "_read_profile_status",
        lambda profile, env=None, build_if_missing=True: (_ for _ in ()).throw(AssertionError("status read")),
    )

    result = controller.detect(enrich_status=False)

    assert result.profile == profile_for("foinse")
    assert result.status is None
    assert result.reason == "matched ogma_board_identity"


def test_flash_lamh_safety_config_detects_builds_flashes_and_verifies(monkeypatch, tmp_path) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    profile = profile_for("lamh")
    config = LamhSafetyConfig.from_values((60, 70, 80, 90))
    calls: list[tuple[str, str]] = []
    header_path = tmp_path / "lamh_safety_config.h"
    record_path = tmp_path / "lamh_safety.json"
    status = {
        "safety_config_magic": 0x4C534346,
        "safety_config_version": 1,
        "safe_angle_pwm1_deg": 60,
        "safe_angle_pwm2_deg": 70,
        "safe_angle_pwm3_deg": 80,
        "safe_angle_pwm4_deg": 90,
    }

    monkeypatch.setattr(controller, "detect", lambda enrich_status=False: DetectionResult(profile, None, "test identity"))
    monkeypatch.setattr(controller, "build", lambda board_id, env: calls.append(("build:" + board_id, env)))
    monkeypatch.setattr(controller, "flash", lambda board_id, env: calls.append(("flash:" + board_id, env)))
    monkeypatch.setattr(controller, "read_status", lambda board_id, env: status)
    monkeypatch.setattr("ogma_app.controller.write_lamh_safety_header", lambda _path, _config: header_path)
    monkeypatch.setattr("ogma_app.controller.save_lamh_safety_record", lambda *_args: record_path)

    result = controller.flash_lamh_safety_config(config)

    assert calls == [("build:lamh", "stm32f072c8t6"), ("flash:lamh", "stm32f072c8t6")]
    assert result.config == config
    assert result.header_path == header_path
    assert result.record_path == record_path


def test_flash_lamh_safety_config_blocks_non_lamh_target(monkeypatch) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    detection = DetectionResult(profile_for("croi"), None, "test identity")
    monkeypatch.setattr(controller, "detect", lambda enrich_status=False: detection)

    with pytest.raises(RuntimeError, match="connected board is Croí"):
        controller.flash_lamh_safety_config(LamhSafetyConfig.defaults())


def test_flash_croi_mission_detects_builds_flashes_and_verifies(monkeypatch, tmp_path) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    profile = profile_for("croi")
    config = MissionConfig.defaults()
    calls: list[tuple[str, str]] = []
    header_path = tmp_path / "croi_mission_config.h"
    record_path = tmp_path / "mission.json"
    status = {
        "mission_config_magic": 0x4F474D43,
            "mission_config_schema_version": 7,
        "mission_config_crc32": config.crc32(),
    }

    monkeypatch.setattr(controller, "detect", lambda enrich_status=False: DetectionResult(profile, None, "test identity"))
    monkeypatch.setattr(controller, "build", lambda board_id, env: calls.append(("build:" + board_id, env)))
    monkeypatch.setattr(controller, "flash", lambda board_id, env: calls.append(("flash:" + board_id, env)))
    monkeypatch.setattr(controller, "read_status", lambda board_id, env: status)
    monkeypatch.setattr(
        "ogma_app.controller.write_croi_mission_header",
        lambda _path, _config, _recovery, _logging, _detection: header_path,
    )
    monkeypatch.setattr("ogma_app.controller.save_mission_flash_record", lambda *_args: record_path)

    result = controller.flash_croi_mission_config(config)

    assert calls == [("build:croi", "stm32f072c8t6"), ("flash:croi", "stm32f072c8t6")]
    assert result.config == config
    assert result.header_path == header_path
    assert result.record_path == record_path


def test_flash_croi_mission_accepts_valid_pyro_channels(monkeypatch, tmp_path) -> None:
    controller = OgmaController(Path("/tmp/ogma-test"), lambda _text: None)
    config = replace(MissionConfig.defaults(), pyro_drogue_channel=0)
    profile = profile_for("croi")
    status = {
        "mission_config_magic": 0x4F474D43,
            "mission_config_schema_version": 7,
        "mission_config_crc32": config.crc32(),
    }
    monkeypatch.setattr(controller, "detect", lambda enrich_status=False: DetectionResult(profile, None, "test"))
    monkeypatch.setattr(controller, "build", lambda _board_id, _env: tmp_path / "firmware.elf")
    monkeypatch.setattr(controller, "flash", lambda _board_id, _env: None)
    monkeypatch.setattr(controller, "read_status", lambda _board_id, _env: status)
    monkeypatch.setattr(
        "ogma_app.controller.write_croi_mission_header",
        lambda _path, _config, _recovery, _logging, _detection: tmp_path / "mission.h",
    )
    monkeypatch.setattr("ogma_app.controller.save_mission_flash_record", lambda *_args: tmp_path / "record.json")

    result = controller.flash_croi_mission_config(config)
    assert result.config.pyro_drogue_channel == 0
