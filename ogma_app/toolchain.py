from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from .paths import NM, OPENOCD, PIO


def run_process(
    cmd: list[str],
    cwd: Path,
    log: Callable[[str], None],
    timeout: float | None = None,
) -> int:
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    assert proc.stdout is not None
    start = time.time()
    for line in proc.stdout:
        log(line.rstrip())
        if timeout is not None and time.time() - start > timeout:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            log("process timed out")
            return 124
    return proc.wait()


def elf_path(firmware_dir: Path, env: str) -> Path:
    return firmware_dir / ".pio" / "build" / env / "firmware.elf"


def build_env(firmware_dir: Path, env: str, log: Callable[[str], None]) -> Path:
    rc = run_process([PIO, "run", "-e", env], firmware_dir, log)
    if rc != 0:
        raise RuntimeError(f"pio build failed for {env} rc={rc}")
    elf = elf_path(firmware_dir, env)
    if not elf.exists():
        raise RuntimeError(f"ELF missing after build: {elf}")
    return elf


def flash_env(
    firmware_dir: Path,
    env: str,
    log: Callable[[str], None],
    upload_port: str | None = None,
    target_cfg: str = "target/stm32f0x.cfg",
) -> None:
    if upload_port:
        elf = build_env(firmware_dir, env, log)
        relative_elf = elf.relative_to(firmware_dir)
        cmd = [
            str(OPENOCD),
            "-f",
            "interface/stlink.cfg",
            "-c",
            f"adapter serial {upload_port}",
            "-f",
            target_cfg,
            "-c",
            f"program {relative_elf} verify reset exit",
        ]
        rc = run_process(cmd, firmware_dir, log)
        if rc != 0:
            raise RuntimeError(f"OpenOCD upload failed for {env} rc={rc}")
        return

    cmd = [PIO, "run", "-e", env, "-t", "upload"]
    rc = run_process(cmd, firmware_dir, log)
    if rc != 0:
        raise RuntimeError(f"pio upload failed for {env} rc={rc}")


def symbol_addresses(
    firmware_dir: Path,
    env: str,
    names: set[str],
    log: Callable[[str], None],
    build_if_missing: bool = False,
) -> dict[str, int]:
    elf = elf_path(firmware_dir, env)
    if not elf.exists():
        if not build_if_missing:
            raise RuntimeError(f"ELF missing for {env}; build first: {elf}")
        log(f"{env} ELF missing; building")
        elf = build_env(firmware_dir, env, log)

    proc = subprocess.run(
        [str(NM), "-S", str(elf)],
        cwd=firmware_dir,
        text=True,
        capture_output=True,
        check=True,
    )
    symbols: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] in names:
            symbols[parts[3]] = int(parts[0], 16)
    missing = names - set(symbols)
    if missing:
        raise RuntimeError(f"missing symbols in {env}: {sorted(missing)}")
    return symbols
