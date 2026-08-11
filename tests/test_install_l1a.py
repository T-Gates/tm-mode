"""Representative install bootstrap safety boundaries."""

import runpy
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__install_test__")


def _git_init(path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=str(path), check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=str(path), check=True
    )


def test_preflight_no_remote_auth_warns_not_fatal(tmp_path):
    """Missing remote auth must not block local installation."""
    (tmp_path / ".git").mkdir()
    res = il.preflight(
        team_root=tmp_path,
        python_version=(3, 13),
        git_present=True,
        remote_authed=False,
    )
    assert res.ok is True
    assert res.exit_code == 0
    assert any("인증" in warning or "remote" in warning.lower() for warning in res.warnings)


def test_bootstrap_requires_root_or_marker(tmp_path, monkeypatch, capsys):
    """An unmarked cwd must not be guessed as the team root."""
    mod = _load_install()
    opts = il.parse_args([])
    monkeypatch.chdir(tmp_path)
    rc = mod["bootstrap"](opts, home=tmp_path, python_version=(3, 13))
    assert rc == 2
    assert "root" in capsys.readouterr().err.lower()


def test_bootstrap_ignores_ambient_teammode_home(tmp_path, monkeypatch, capsys):
    """Ambient root variables must not redirect installation."""
    victim = tmp_path / "victim"
    victim.mkdir()
    monkeypatch.setenv("TEAMMODE_HOME", str(victim))
    monkeypatch.setenv("LEGACY_TOOL_HOME", str(victim))
    mod = _load_install()
    opts = il.parse_args([])
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    rc = mod["bootstrap"](opts, home=tmp_path, python_version=(3, 13))
    assert rc == 2


def test_bootstrap_dry_run_no_side_effects(tmp_path, capsys):
    """Dry-run prints a plan without creating repository state."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--dry-run"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    assert "dry-run" in capsys.readouterr().out.lower()
    assert not (team / "memory").exists()


def test_bootstrap_python_below_min_exits_2(tmp_path, capsys):
    """Unsupported Python stops before installation."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=tmp_path, python_version=(2, 7))
    assert rc == 2
    assert "python" in capsys.readouterr().err.lower()
