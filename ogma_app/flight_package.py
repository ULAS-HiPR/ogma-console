from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .boards import profile_for
from .flight_manifest import FLIGHT_MANIFEST_FORMAT, FlightManifest
from .lamh_config import render_lamh_safety_header
from .mission_config import render_croi_mission_header
from .preflight import PreflightReport
from .teachtaire_config import render_teachtaire_radio_header


FLIGHT_PACKAGE_FORMAT = "ogma-flight-package"
FLIGHT_PACKAGE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FlightPackageResult:
    path: Path
    sha256: str
    missing_firmware: tuple[str, ...]
    dirty_repositories: tuple[str, ...]
    preflight_go: bool


@dataclass(frozen=True)
class FlightPackageInspection:
    path: Path
    valid: bool
    manifest_sha256: str
    package_sha256: str
    errors: tuple[str, ...]
    metadata: dict[str, Any]


def build_flight_package(
    path: Path,
    manifest: FlightManifest,
    preflight: PreflightReport | None = None,
) -> FlightPackageResult:
    manifest.validate()
    if path.suffix.lower() != ".zip":
        path = path.with_suffix(".zip")
    path.parent.mkdir(parents=True, exist_ok=True)

    config_entries = {
        "config/flight_manifest.json": _json_bytes(
            {
                "format": FLIGHT_MANIFEST_FORMAT,
                "manifest_sha256": manifest.sha256(),
                "manifest": manifest.canonical_dict(),
            }
        ),
        "config/croi_mission_config.h": render_croi_mission_header(
            manifest.mission, manifest.recovery, manifest.logging
        ).encode("ascii"),
        "config/lamh_safety_config.h": render_lamh_safety_header(manifest.lamh_safety).encode("ascii"),
        "config/teachtaire_radio_config.h": render_teachtaire_radio_header(manifest.radio).encode("ascii"),
    }
    firmware_entries: dict[str, bytes] = {}
    missing_firmware = []
    provenance = {}
    dirty_repositories = []
    for board_id in manifest.effective_required_boards():
        profile = profile_for(board_id)
        if profile.firmware_dir is None or profile.default_env is None:
            missing_firmware.append(board_id)
            continue
        repository = profile.firmware_dir.parent
        source = _git_provenance(repository)
        provenance[board_id] = source
        if source.get("dirty"):
            dirty_repositories.append(board_id)
        binary = profile.firmware_dir / ".pio" / "build" / profile.default_env / "firmware.bin"
        elf = profile.firmware_dir / ".pio" / "build" / profile.default_env / "firmware.elf"
        if not binary.exists() or not elf.exists():
            missing_firmware.append(board_id)
            continue
        firmware_entries[f"firmware/{board_id}/firmware.bin"] = binary.read_bytes()
        firmware_entries[f"firmware/{board_id}/firmware.elf"] = elf.read_bytes()

    preflight_payload = None
    if preflight is not None:
        preflight_payload = {
            "go": preflight.go,
            "failures": preflight.failures,
            "warnings": preflight.warnings,
            "checks": [asdict(check) for check in preflight.checks],
        }
    metadata = {
        "format": FLIGHT_PACKAGE_FORMAT,
        "schema_version": FLIGHT_PACKAGE_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest.sha256(),
        "preflight": preflight_payload,
        "missing_firmware": missing_firmware,
        "dirty_repositories": dirty_repositories,
        "release_ready": bool(preflight and preflight.go and not missing_firmware and not dirty_repositories),
        "provenance": provenance,
    }
    entries = {
        **config_entries,
        **firmware_entries,
        "package-metadata.json": _json_bytes(metadata),
    }
    index = {
        name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        for name, content in sorted(entries.items())
    }

    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)
        archive.writestr("package-index.json", _json_bytes(index))
    temporary.replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return FlightPackageResult(
        path,
        digest,
        tuple(missing_firmware),
        tuple(dirty_repositories),
        bool(preflight and preflight.go),
    )


def inspect_flight_package(path: Path) -> FlightPackageInspection:
    errors = []
    metadata: dict[str, Any] = {}
    manifest_sha256 = ""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            index = json.loads(archive.read("package-index.json"))
            metadata = json.loads(archive.read("package-metadata.json"))
            if metadata.get("format") != FLIGHT_PACKAGE_FORMAT:
                errors.append("wrong package format")
            manifest_sha256 = str(metadata.get("manifest_sha256", ""))
            for name, expected in index.items():
                try:
                    content = archive.read(name)
                except KeyError:
                    errors.append(f"missing {name}")
                    continue
                if len(content) != int(expected.get("bytes", -1)):
                    errors.append(f"size mismatch: {name}")
                if hashlib.sha256(content).hexdigest() != str(expected.get("sha256", "")):
                    errors.append(f"SHA-256 mismatch: {name}")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    package_sha256 = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    return FlightPackageInspection(
        path,
        not errors,
        manifest_sha256,
        package_sha256,
        tuple(errors),
        metadata,
    )


def _git_provenance(repository: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repository), *args),
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    return {
        "path": str(repository),
        "commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")),
        "submodules": git("submodule", "status", "--recursive").splitlines(),
    }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
