from pathlib import Path

from ogma_app.boards import profile_for
from ogma_app.snapshots import (
    make_status_sample,
    make_status_snapshot,
    save_status_series,
    save_status_snapshot,
)


def test_make_status_snapshot_minimal() -> None:
    profile = profile_for("foinse")
    snapshot = make_status_snapshot(profile, "stm32f072c8t6", {"sense1_mv": 1200})
    assert snapshot["board_id"] == "foinse"
    assert snapshot["env"] == "stm32f072c8t6"
    assert snapshot["status"]["sense1_mv"] == 1200
    assert "timestamp_utc" in snapshot


def test_save_status_snapshot_json_and_csv(tmp_path: Path) -> None:
    profile = profile_for("lamh")
    snapshot = make_status_snapshot(profile, "stm32f072c8t6", {"servo_angle": 90})
    json_path = save_status_snapshot(snapshot, tmp_path / "status.json")
    csv_path = save_status_snapshot(snapshot, tmp_path / "status.csv")
    assert '"board_id": "lamh"' in json_path.read_text(encoding="utf-8")
    assert "status.servo_angle,90" in csv_path.read_text(encoding="utf-8")


def test_save_status_series(tmp_path: Path) -> None:
    profile = profile_for("teachtaire")
    samples = [
        make_status_sample(0.0, {"gnss_sats": 0}, timestamp_utc="2026-07-07T00:00:00Z"),
        make_status_sample(0.5, {"gnss_sats": 4}, timestamp_utc="2026-07-07T00:00:01Z"),
    ]
    out = save_status_series(profile, "teachtaire_empty", samples, tmp_path)
    assert (out / "summary.json").exists()
    assert (out / "samples.json").exists()
    csv_text = (out / "samples.csv").read_text(encoding="utf-8")
    assert "timestamp_utc,elapsed_s,gnss_sats" in csv_text
    assert "2026-07-07T00:00:01Z,0.5,4" in csv_text
