import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ogma_app.flight_manifest import FlightManifest, PreflightPolicy
from ogma_app.flight_package import build_flight_package, inspect_flight_package


def test_flight_package_hashes_every_payload(monkeypatch, tmp_path) -> None:
    firmware = tmp_path / "croi" / "firmware"
    build = firmware / ".pio" / "build" / "flight"
    build.mkdir(parents=True)
    (build / "firmware.bin").write_bytes(b"bin")
    (build / "firmware.elf").write_bytes(b"elf")
    monkeypatch.setattr(
        "ogma_app.flight_package.profile_for",
        lambda _board_id: SimpleNamespace(firmware_dir=firmware, default_env="flight"),
    )
    manifest = replace(
        FlightManifest.defaults(),
        preflight=PreflightPolicy(required_boards=("croi",), require_gps_fix=False),
    )

    result = build_flight_package(tmp_path / "flight.zip", manifest)
    inspection = inspect_flight_package(result.path)

    assert inspection.valid
    assert inspection.manifest_sha256 == manifest.sha256()
    assert not result.missing_firmware
    assert result.path.with_suffix(".zip.sha256").exists()


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_flight_package_detects_modified_entry(monkeypatch, tmp_path) -> None:
    firmware = tmp_path / "croi" / "firmware"
    build = firmware / ".pio" / "build" / "flight"
    build.mkdir(parents=True)
    (build / "firmware.bin").write_bytes(b"bin")
    (build / "firmware.elf").write_bytes(b"elf")
    monkeypatch.setattr(
        "ogma_app.flight_package.profile_for",
        lambda _board_id: SimpleNamespace(firmware_dir=firmware, default_env="flight"),
    )
    manifest = replace(
        FlightManifest.defaults(),
        preflight=PreflightPolicy(required_boards=("croi",), require_gps_fix=False),
    )
    result = build_flight_package(tmp_path / "flight.zip", manifest)
    with zipfile.ZipFile(result.path, "a") as archive:
        archive.writestr("config/croi_mission_config.h", b"tampered")

    inspection = inspect_flight_package(result.path)

    assert not inspection.valid
    assert any("mismatch" in error for error in inspection.errors)
