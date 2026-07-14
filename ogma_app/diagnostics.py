from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .boards import PROFILES, BoardProfile, profile_for
from .identity import IDENTITY_SYMBOL
from .paths import DEPENDENCY_LOCK_PATH, NM, OGMA_ROOT, OPENOCD, PIO, ST_INFO
from .toolchain import elf_path, symbol_addresses

SHARED_DEP_PATHS = {
    "comheadan": ("firmware/lib/comheadan",),
    "braiteoiri": ("firmware/lib/braiteoiri", "firmware/lib/braitheoiri"),
}
def dependency_lock() -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(DEPENDENCY_LOCK_PATH.read_text(encoding="utf-8"))
        dependencies = payload.get("dependencies", {})
        if not isinstance(dependencies, dict):
            return {}
        return {
            str(name): dict(value)
            for name, value in dependencies.items()
            if isinstance(value, dict)
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


@dataclass(frozen=True)
class DiagnosticRow:
    subject: str
    check: str
    state: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.state != "fail"


@dataclass(frozen=True)
class DiagnosticReport:
    rows: tuple[DiagnosticRow, ...]

    @property
    def ok(self) -> bool:
        return all(row.ok for row in self.rows)

    def lines(self) -> list[str]:
        return [f"{row.state:4} {row.subject:14} {row.check:18} {row.detail}" for row in self.rows]


@dataclass(frozen=True)
class GitState:
    branch: str
    short_sha: str
    dirty: bool
    behind: bool
    ahead: bool

    @property
    def ok(self) -> bool:
        return not self.dirty and not self.behind

    def compact(self) -> str:
        flags: list[str] = []
        if self.dirty:
            flags.append("dirty")
        if self.behind:
            flags.append("behind")
        if self.ahead:
            flags.append("ahead")
        suffix = "" if not flags else f" ({', '.join(flags)})"
        return f"{self.branch}@{self.short_sha}{suffix}"


def required_symbols(profile: BoardProfile) -> set[str]:
    symbols: set[str] = set()
    if profile.identity_id is not None and profile.firmware_dir is not None and profile.default_env is not None:
        symbols.add(IDENTITY_SYMBOL)
    if profile.status_block is not None:
        symbols.add(profile.status_block.symbol)
    if profile.board_id == "croi":
        symbols.add("ogma_flash_mailbox")
    if profile.board_id == "lamh":
        symbols.add("ogma_servo_command")
    return symbols


def collect_diagnostics(board_id: str | None = None) -> DiagnosticReport:
    rows: list[DiagnosticRow] = []
    rows.extend(_tool_rows())
    profiles = (profile_for(board_id),) if board_id else PROFILES
    for profile in profiles:
        rows.extend(_profile_rows(profile))
    if board_id is None:
        rows.extend(_shared_dependency_summary_rows(profiles))
    return DiagnosticReport(tuple(rows))


def _tool_rows() -> list[DiagnosticRow]:
    rows: list[DiagnosticRow] = []
    rows.append(_path_row("tool", "pio", Path(PIO), "PlatformIO executable"))
    rows.append(_path_row("tool", "openocd", OPENOCD, "OpenOCD executable"))
    rows.append(_path_row("tool", "nm", NM, "ARM nm executable"))
    rows.append(_path_row("tool", "st-info", ST_INFO, "ST-Link probe executable"))
    return rows


def _profile_rows(profile: BoardProfile) -> list[DiagnosticRow]:
    rows: list[DiagnosticRow] = []
    rows.append(_path_row(profile.board_id, "repo", profile.repo_dir, "repository directory"))
    if profile.repo_dir.exists():
        rows.extend(_git_rows(profile))
        rows.extend(_dependency_rows(profile))
    if profile.firmware_dir is None:
        rows.append(DiagnosticRow(profile.board_id, "firmware", "fail", "no firmware dir configured"))
        return rows
    if not profile.envs:
        state = "ok" if profile.firmware_dir.exists() else "warn"
        rows.append(DiagnosticRow(profile.board_id, "firmware", state, f"firmware directory: {profile.firmware_dir}"))
        rows.append(DiagnosticRow(profile.board_id, "envs", "ok", "non-PlatformIO firmware"))
        return rows
    rows.append(_path_row(profile.board_id, "firmware", profile.firmware_dir, "firmware directory"))
    if profile.default_env is None:
        rows.append(DiagnosticRow(profile.board_id, "default env", "fail", "missing default env"))
    else:
        env_names = profile.env_names()
        state = "ok" if profile.default_env in env_names else "fail"
        rows.append(DiagnosticRow(profile.board_id, "default env", state, profile.default_env))

    symbols = required_symbols(profile)
    for env in profile.env_names():
        if env == "sil":
            program = profile.firmware_dir / ".pio" / "build" / env / "program"
            state = "ok" if program.exists() else "warn"
            rows.append(
                DiagnosticRow(
                    profile.board_id,
                    "sil test binary",
                    state,
                    f"native test output: {program}",
                )
            )
            continue
        elf = elf_path(profile.firmware_dir, env)
        rows.append(_elf_row(profile, env, elf))
        if env == profile.default_env and elf.exists() and symbols:
            rows.extend(_symbol_rows(profile, env, symbols))
    return rows


def _path_row(subject: str, check: str, path: Path, detail: str) -> DiagnosticRow:
    exists = path.exists()
    state = "ok" if exists else "fail"
    return DiagnosticRow(subject, check, state, f"{detail}: {path}")


def _elf_row(profile: BoardProfile, env: str, path: Path) -> DiagnosticRow:
    if path.exists():
        return DiagnosticRow(profile.board_id, f"{env} elf", "ok", f"build output: {path}")
    state = "fail" if env == profile.default_env else "warn"
    return DiagnosticRow(profile.board_id, f"{env} elf", state, f"build output: {path}")


def _git_rows(profile: BoardProfile) -> list[DiagnosticRow]:
    rows: list[DiagnosticRow] = []
    git_state = _git_state(profile.repo_dir)
    if isinstance(git_state, str):
        rows.append(DiagnosticRow(profile.board_id, "git", "fail", git_state))
        return rows
    branch_state = "warn" if git_state.behind else "ok"
    rows.append(DiagnosticRow(profile.board_id, "branch", branch_state, git_state.branch))
    rows.append(DiagnosticRow(profile.board_id, "dirty", "warn" if git_state.dirty else "ok", "dirty worktree" if git_state.dirty else "clean"))
    return rows


def _dependency_rows(profile: BoardProfile) -> list[DiagnosticRow]:
    rows: list[DiagnosticRow] = []
    lock = dependency_lock()
    for dep_name, candidates in SHARED_DEP_PATHS.items():
        dep_path = _first_existing(profile.repo_dir, candidates)
        if dep_path is None:
            continue
        if not _is_git_checkout(dep_path):
            rows.append(
                DiagnosticRow(
                    profile.board_id,
                    f"dep {dep_name}",
                    "warn",
                    f"directory present but not a git checkout: {dep_path}",
                )
            )
            continue
        git_state = _git_state(dep_path)
        check = f"dep {dep_name}"
        if isinstance(git_state, str):
            rows.append(DiagnosticRow(profile.board_id, check, "fail", git_state))
            continue
        expected = str(lock.get(dep_name, {}).get("commit", ""))
        actual = _run_git(dep_path, "rev-parse", "HEAD").stdout.strip()
        pin_ok = bool(expected) and actual.startswith(expected)
        if not pin_ok:
            rows.append(
                DiagnosticRow(
                    profile.board_id,
                    check,
                    "fail",
                    f"{git_state.compact()}; expected {expected or 'missing lock'}",
                )
            )
            continue
        rows.append(
            DiagnosticRow(
                profile.board_id,
                check,
                "ok" if git_state.ok else "warn",
                f"{git_state.compact()}; locked {expected}",
            )
        )
    return rows


def _shared_dependency_summary_rows(profiles: tuple[BoardProfile, ...]) -> list[DiagnosticRow]:
    rows: list[DiagnosticRow] = []
    for dep_name, candidates in SHARED_DEP_PATHS.items():
        states: dict[str, GitState] = {}
        for profile in profiles:
            dep_path = _first_existing(profile.repo_dir, candidates)
            if dep_path is None or not _is_git_checkout(dep_path):
                continue
            git_state = _git_state(dep_path)
            if not isinstance(git_state, str):
                states[profile.board_id] = git_state
        if len(states) < 2:
            continue
        shas = {state.short_sha for state in states.values()}
        dirty_or_behind = any(not state.ok for state in states.values())
        state = "ok" if len(shas) == 1 and not dirty_or_behind else "warn"
        detail = ", ".join(f"{board}={git_state.compact()}" for board, git_state in sorted(states.items()))
        rows.append(DiagnosticRow("shared deps", dep_name, state, detail))
    return rows


def _symbol_rows(profile: BoardProfile, env: str, symbols: set[str]) -> list[DiagnosticRow]:
    try:
        found = symbol_addresses(profile.firmware_dir or Path(), env, symbols, lambda _text: None, build_if_missing=False)
    except Exception as exc:
        return [DiagnosticRow(profile.board_id, f"{env} symbols", "fail", str(exc))]
    rows: list[DiagnosticRow] = []
    for name in sorted(symbols):
        rows.append(DiagnosticRow(profile.board_id, name, "ok", f"{env}: 0x{found[name]:08x}"))
    return rows


def _git_state(repo: Path) -> GitState | str:
    status = _run_git(repo, "status", "--short", "--branch")
    if status.returncode != 0:
        return status.stderr.strip() or "git status failed"
    sha = _run_git(repo, "rev-parse", "--short", "HEAD")
    if sha.returncode != 0:
        return sha.stderr.strip() or "git rev-parse failed"
    return parse_git_state(status.stdout, sha.stdout.strip())


def parse_git_state(status_text: str, short_sha: str) -> GitState:
    lines = status_text.splitlines()
    branch = lines[0].removeprefix("## ") if lines else "unknown"
    return GitState(
        branch=branch,
        short_sha=short_sha,
        dirty=len(lines) > 1,
        behind="behind" in branch,
        ahead="ahead" in branch,
    )


def _first_existing(root: Path, candidates: tuple[str, ...]) -> Path | None:
    for candidate in candidates:
        path = root / candidate
        if path.exists():
            return path
    return None


def _is_git_checkout(path: Path) -> bool:
    return (path / ".git").exists()


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
