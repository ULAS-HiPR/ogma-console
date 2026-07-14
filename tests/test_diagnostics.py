from ogma_app.boards import profile_for
from ogma_app.diagnostics import (
    DiagnosticReport,
    DiagnosticRow,
    _is_git_checkout,
    dependency_lock,
    parse_git_state,
    required_symbols,
)


def test_required_symbols_by_board() -> None:
    assert {"ogma_board_identity", "ogma_flash_mailbox"} <= required_symbols(profile_for("croi"))
    assert {"ogma_board_identity", "ogma_servo_command", "servo_debug"} <= required_symbols(profile_for("lamh"))
    assert {"ogma_board_identity", "report"} <= required_symbols(profile_for("teachtaire"))
    assert {"ogma_board_identity", "foinse_status"} <= required_symbols(profile_for("foinse"))


def test_diagnostic_report_ok_and_lines() -> None:
    report = DiagnosticReport(
        (
            DiagnosticRow("tool", "pio", "ok", "present"),
            DiagnosticRow("croi", "dirty", "warn", "dirty worktree"),
        )
    )
    assert report.ok
    assert report.lines()[0].startswith("ok")
    assert "dirty worktree" in report.lines()[1]


def test_diagnostic_report_fails_on_fail() -> None:
    report = DiagnosticReport((DiagnosticRow("tool", "openocd", "fail", "missing"),))
    assert not report.ok


def test_parse_git_state_flags_dirty_and_behind() -> None:
    state = parse_git_state("## Flash...origin/Flash [behind 2]\n M src/file.cpp\n", "abc1234")
    assert state.branch == "Flash...origin/Flash [behind 2]"
    assert state.short_sha == "abc1234"
    assert state.dirty
    assert state.behind
    assert not state.ok
    assert state.compact() == "Flash...origin/Flash [behind 2]@abc1234 (dirty, behind)"


def test_is_git_checkout_requires_marker(tmp_path) -> None:
    dep = tmp_path / "firmware" / "lib" / "comheadan"
    dep.mkdir(parents=True)
    assert not _is_git_checkout(dep)
    (dep / ".git").write_text("gitdir: ../.git/modules/comheadan\n", encoding="utf-8")
    assert _is_git_checkout(dep)


def test_dependency_lock_names_canonical_revisions() -> None:
    lock = dependency_lock()
    assert lock["comheadan"]["branch"] == "ogma/flight-hardening"
    assert lock["comheadan"]["commit"].startswith("01f8aef")
    assert lock["braiteoiri"]["commit"].startswith("0e04683")
