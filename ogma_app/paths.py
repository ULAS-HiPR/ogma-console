from __future__ import annotations

import os
import shutil
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
OGMA_ROOT = Path(os.environ.get("OGMA_ROOT", APP_ROOT.parent)).expanduser().resolve()
PACKAGE_DATA_ROOT = Path(__file__).resolve().parent / "data"
PAYLOAD_LAYOUTS_CSV = PACKAGE_DATA_ROOT / "payload_layouts.csv"
CAN_FRAMES_HEADER = PACKAGE_DATA_ROOT / "CAN_Frames.h"
DEPENDENCY_LOCK_PATH = PACKAGE_DATA_ROOT / "dependencies.lock.json"
RUNS_ROOT = APP_ROOT / "runs"

OPENOCD = Path.home() / ".platformio/packages/tool-openocd/bin/openocd"
NM = Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-nm"
ST_INFO = Path.home() / ".platformio/packages/tool-stm32duino/stlink/st-info"
PIO = shutil.which("pio") or "pio"
