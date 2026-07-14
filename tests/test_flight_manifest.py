import json
from dataclasses import replace

import pytest

from ogma_app.flight_manifest import (
    FlightManifest,
    RecoveryFallbackConfig,
    load_flight_manifest,
    save_flight_manifest,
)
from ogma_app.lamh_config import LamhSafetyConfig
from ogma_app.mission_config import MissionConfig, save_mission_json


def test_manifest_round_trip_and_hash(tmp_path) -> None:
    manifest = FlightManifest.defaults(LamhSafetyConfig.from_values((0, 10, 20, 30)))
    path = save_flight_manifest(tmp_path, manifest)

    loaded = load_flight_manifest(path)

    assert loaded == manifest
    assert len(manifest.sha256()) == 64
    assert manifest.sha256() in path.read_text(encoding="utf-8")


def test_manifest_rejects_tampered_payload(tmp_path) -> None:
    path = save_flight_manifest(tmp_path, FlightManifest.defaults())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest"]["mission"]["main_deploy_altitude_m"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256"):
        load_flight_manifest(path)


def test_manifest_loads_legacy_mission_json(tmp_path) -> None:
    mission = MissionConfig.defaults()
    path = save_mission_json(tmp_path, mission)
    safety = LamhSafetyConfig.from_values((1, 2, 3, 4))

    manifest = load_flight_manifest(path, safety)

    assert manifest.mission == mission
    assert manifest.lamh_safety == safety


def test_pyro_mission_automatically_requires_pleasc() -> None:
    mission = replace(MissionConfig.defaults(), pyro_main_channel=2)
    manifest = replace(FlightManifest.defaults(), mission=mission)

    assert "pleasc" in manifest.effective_required_boards()


def test_enabled_recovery_fallback_rejects_invalid_delay() -> None:
    mission = replace(MissionConfig.defaults(), pyro_main_channel=1)
    manifest = replace(
        FlightManifest.defaults(),
        mission=mission,
        recovery=RecoveryFallbackConfig(main_backup_enabled=True, after_apogee_ms=99),
    )

    with pytest.raises(ValueError, match="delay"):
        manifest.validate()
