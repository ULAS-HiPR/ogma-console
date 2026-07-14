from pathlib import Path

from ogma_app.board_tests import (
    parse_angle_list,
    run_foinse_monitor,
    run_lamh_servo_test,
    run_teachtaire_test,
    summarize_teachtaire_test,
    teachtaire_env_for_mode,
    teachtaire_mode_for_env,
)


class FakeTeachtaireController:
    def __init__(self) -> None:
        self.flashes: list[tuple[str, str]] = []
        self.reads: list[tuple[str, str | None]] = []

    def flash(self, board_id: str, env: str) -> None:
        self.flashes.append((board_id, env))

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, int]:
        self.reads.append((board_id, env))
        return {
            "magic": 0x54434854,
            "fault": 0,
            "clock_ok": 1,
            "gpio_ok": 1,
            "spi_ok": 1,
            "uart_ok": 1,
            "lora_init_ok": 1,
            "lora_error": 0,
            "lora_tx_count": 2,
            "lora_tx_done_count": 2,
            "lora_rx_count": 1,
            "gnss_bytes": 120,
            "gnss_parsed": 4,
            "gnss_overflows": 0,
            "gnss_fix": 1,
            "gnss_sats": 6,
            "gnss_seen": 4,
            "gnss_checksum_bad": 0,
        }


class FakeLamhController:
    def __init__(self) -> None:
        self.commands: list[tuple[int, int, str | None]] = []
        self.reads: list[tuple[str, str | None]] = []

    def send_lamh_servo_command(self, channel: int, angle_deg: int, env: str | None = None) -> None:
        self.commands.append((channel, angle_deg, env))

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, int]:
        self.reads.append((board_id, env))
        angle = self.commands[-1][1]
        return {
            "magic": 0x53455256,
            "stage": 30,
            "pca9685_found": 1,
            "pca9685_address": 0x40,
            "scan_last_error": 0,
            "i2c_last_error": 0,
            "can_init_ok": 1,
            "can_bus_off": 0,
            "can_error": 0,
            "can_tx_drops": 0,
            "servo_angle": angle,
            "servo_pwm": 300 + angle,
            "servo_set_count": len(self.commands),
        }


class FakeFoinseController:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str | None]] = []
        self.index = 0

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, int]:
        self.reads.append((board_id, env))
        self.index += 1
        return {
            "magic": 0x464F494E,
            "version": 1,
            "uptime_ms": self.index * 100,
            "loop_count": self.index,
            "adc_ok": 1,
            "fault": 0,
            "sense1_raw": 100 + self.index,
            "sense2_raw": 200 + self.index,
            "sense1_mv": 10 * self.index,
            "sense2_mv": 20 * self.index,
            "sense1_current_ma": 100 * self.index,
            "sense2_current_ma": 200 * self.index,
        }


def test_teachtaire_mode_env_mapping() -> None:
    assert teachtaire_env_for_mode("lora_tx") == "teachtaire_lora_tx"
    assert teachtaire_mode_for_env("teachtaire_lora_rx") == "lora_rx"


def test_run_teachtaire_test_flashes_polls_and_saves(tmp_path: Path) -> None:
    controller = FakeTeachtaireController()
    result = run_teachtaire_test(controller, "lora_tx", 0.0, 0.5, tmp_path)
    assert controller.flashes == [("teachtaire", "teachtaire_lora_tx")]
    assert controller.reads == [("teachtaire", "teachtaire_lora_tx")]
    assert result.summary["latest_health_ok"] is True
    assert result.summary["latest_lora_tx_count"] == 2
    assert (result.out / "summary.json").exists()
    assert (result.out / "samples.csv").exists()


def test_summarize_teachtaire_test_reports_warns() -> None:
    summary = summarize_teachtaire_test(
        "flight",
        "teachtaire_flight",
        [
            {
                "status": {"lora_tx_count": 0, "gnss_fix": 0},
                "health": {"ok": False, "checks": [{"name": "gnss_fix", "state": "warn"}]},
            }
        ],
        flashed=False,
    )
    assert summary["flashed"] is False
    assert summary["warn_checks"] == ["gnss_fix"]


def test_parse_angle_list() -> None:
    assert parse_angle_list("0, 45,90") == [0, 45, 90]


def test_run_lamh_servo_test_commands_reads_and_saves(tmp_path: Path) -> None:
    controller = FakeLamhController()
    result = run_lamh_servo_test(controller, 2, [0, 90, 180], 0.0, tmp_path)
    assert controller.commands == [
        (2, 0, "stm32f072c8t6"),
        (2, 90, "stm32f072c8t6"),
        (2, 180, "stm32f072c8t6"),
    ]
    assert controller.reads == [
        ("lamh", "stm32f072c8t6"),
        ("lamh", "stm32f072c8t6"),
        ("lamh", "stm32f072c8t6"),
    ]
    assert result.summary["latest_servo_angle"] == 180
    assert result.summary["output"] == 2
    assert result.summary["pca_channel"] == 2
    assert result.summary["latest_health_ok"] is True
    assert (result.out / "summary.json").exists()
    assert (result.out / "samples.csv").exists()


def test_run_foinse_monitor_reads_and_summarizes(tmp_path: Path) -> None:
    controller = FakeFoinseController()
    result = run_foinse_monitor(controller, 0.0, 0.5, tmp_path)
    assert controller.reads == [("foinse", "stm32f072c8t6")]
    assert result.summary["sample_count"] == 1
    assert result.summary["sense1_mv_min"] == 10
    assert result.summary["sense2_mv_avg"] == 20
    assert result.summary["sense1_current_ma_avg"] == 100
    assert result.summary["latest_health_ok"] is True
    assert (result.out / "summary.json").exists()
    assert (result.out / "samples.csv").exists()
