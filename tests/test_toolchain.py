from pathlib import Path

from ogma_app import toolchain


def test_flash_env_uses_direct_openocd_for_selected_probe(monkeypatch, tmp_path) -> None:
    firmware_dir = tmp_path / "firmware"
    elf = firmware_dir / ".pio" / "build" / "flight" / "firmware.elf"
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.setattr(toolchain, "OPENOCD", Path("/tools/openocd"))
    monkeypatch.setattr(toolchain, "build_env", lambda *_args: elf)
    monkeypatch.setattr(
        toolchain,
        "run_process",
        lambda cmd, cwd, _log: calls.append((cmd, cwd)) or 0,
    )

    toolchain.flash_env(
        firmware_dir,
        "flight",
        lambda _text: None,
        "probe-serial",
        "target/stm32f0x.cfg",
    )

    assert calls == [
        (
            [
                "/tools/openocd",
                "-f",
                "interface/stlink.cfg",
                "-c",
                "adapter serial probe-serial",
                "-f",
                "target/stm32f0x.cfg",
                "-c",
                "program .pio/build/flight/firmware.elf verify reset exit",
            ],
            firmware_dir,
        )
    ]
