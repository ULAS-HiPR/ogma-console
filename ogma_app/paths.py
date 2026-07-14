from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


def resolve_ogma_root(
    app_root: Path,
    current_directory: Path,
    environment: Mapping[str, str],
) -> Path:
    if environment.get("OGMA_ROOT"):
        return Path(environment["OGMA_ROOT"]).expanduser().resolve()
    for candidate in (app_root.parent, current_directory, current_directory.parent):
        if (candidate / "croi").is_dir():
            return candidate.resolve()
    return current_directory.resolve()


def resolve_runs_root(
    app_root: Path,
    environment: Mapping[str, str],
    home: Path,
    platform: str,
) -> Path:
    if environment.get("OGMA_RUNS_ROOT"):
        return Path(environment["OGMA_RUNS_ROOT"]).expanduser().resolve()
    if (app_root / "pyproject.toml").is_file():
        return app_root / "runs"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Ogma Console" / "runs"
    if platform == "win32":
        data_root = Path(environment.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return data_root / "Ogma Console" / "runs"
    data_root = Path(environment.get("XDG_DATA_HOME", home / ".local" / "share"))
    return data_root / "ogma-console" / "runs"


OGMA_ROOT = resolve_ogma_root(APP_ROOT, Path.cwd(), os.environ)
PACKAGE_DATA_ROOT = Path(__file__).resolve().parent / "data"
PAYLOAD_LAYOUTS_CSV = PACKAGE_DATA_ROOT / "payload_layouts.csv"
CAN_FRAMES_HEADER = PACKAGE_DATA_ROOT / "CAN_Frames.h"
DEPENDENCY_LOCK_PATH = PACKAGE_DATA_ROOT / "dependencies.lock.json"
RUNS_ROOT = resolve_runs_root(APP_ROOT, os.environ, Path.home(), sys.platform)

OPENOCD = Path.home() / ".platformio/packages/tool-openocd/bin/openocd"
NM = Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-nm"
ST_INFO = Path.home() / ".platformio/packages/tool-stm32duino/stlink/st-info"
PIO = shutil.which("pio") or "pio"
