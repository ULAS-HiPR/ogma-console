from pathlib import Path

from ogma_app.boards import profile_for
from ogma_app.controller import DetectionResult
from ogma_app.probe import ProbeResult
from ogma_app.validation import run_bench_validation


class FakeValidationController:
    def __init__(self, detection: DetectionResult) -> None:
        self.detection = detection
        self.reads: list[tuple[str, str | None]] = []

    def detect(self) -> DetectionResult:
        return self.detection

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, int]:
        self.reads.append((board_id, env))
        return {
            "magic": 0x464F494E,
            "version": 1,
            "uptime_ms": 100,
            "loop_count": 10,
            "adc_ok": 1,
            "fault": 0,
            "sense1_raw": 100,
            "sense2_raw": 200,
            "sense1_mv": 80,
            "sense2_mv": 160,
        }


def _probe(connected: bool = True) -> ProbeResult:
    return ProbeResult(
        connected=connected,
        programmers=1 if connected else 0,
        fields={"chipid": "0x0448"} if connected else {},
        returncode=0 if connected else 1,
        raw="probe raw",
    )


def test_bench_validation_success_reads_status_and_saves(tmp_path: Path) -> None:
    controller = FakeValidationController(DetectionResult(profile_for("foinse"), None, "test detect"))
    result = run_bench_validation(controller, "foinse", tmp_path, probe_fn=lambda: _probe())

    assert result.report["ok"] is True
    assert result.report["detection"]["board_id"] == "foinse"
    assert result.report["health"]["ok"] is True
    assert controller.reads == [("foinse", "stm32f072c8t6")]
    assert result.out.exists()


def test_bench_validation_flags_expected_board_mismatch(tmp_path: Path) -> None:
    controller = FakeValidationController(DetectionResult(profile_for("foinse"), None, "test detect"))
    result = run_bench_validation(controller, "lamh", tmp_path, probe_fn=lambda: _probe())

    assert result.report["ok"] is False
    assert "expected lamh, detected foinse" in result.report["errors"]


def test_bench_validation_records_probe_failure(tmp_path: Path) -> None:
    controller = FakeValidationController(DetectionResult(profile_for("foinse"), None, "test detect"))
    result = run_bench_validation(controller, None, tmp_path, probe_fn=lambda: _probe(False))

    assert result.report["ok"] is False
    assert "ST-Link target not connected" in result.report["errors"]
