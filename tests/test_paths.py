from ogma_app.paths import resolve_ogma_root, resolve_runs_root


def test_resolve_ogma_root_prefers_explicit_override(tmp_path) -> None:
    override = tmp_path / "workspace"
    assert resolve_ogma_root(tmp_path, tmp_path / "cwd", {"OGMA_ROOT": str(override)}) == override


def test_resolve_ogma_root_discovers_croi_sibling(tmp_path) -> None:
    app_root = tmp_path / "ogma-console"
    (tmp_path / "croi").mkdir()
    assert resolve_ogma_root(app_root, tmp_path / "elsewhere", {}) == tmp_path


def test_resolve_runs_root_keeps_source_checkout_local(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert resolve_runs_root(tmp_path, {}, tmp_path / "home", "darwin") == tmp_path / "runs"


def test_resolve_runs_root_uses_user_data_for_installed_package(tmp_path) -> None:
    home = tmp_path / "home"
    assert resolve_runs_root(tmp_path / "site-packages", {}, home, "darwin") == (
        home / "Library" / "Application Support" / "Ogma Console" / "runs"
    )
    assert resolve_runs_root(tmp_path / "site-packages", {}, home, "linux") == (
        home / ".local" / "share" / "ogma-console" / "runs"
    )


def test_resolve_runs_root_prefers_explicit_override(tmp_path) -> None:
    override = tmp_path / "runs"
    assert resolve_runs_root(
        tmp_path / "site-packages",
        {"OGMA_RUNS_ROOT": str(override)},
        tmp_path / "home",
        "linux",
    ) == override
