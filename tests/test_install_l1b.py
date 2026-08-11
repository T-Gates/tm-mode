"""Representative install scaffold and preservation boundaries."""

import json
import runpy
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__install_l1b_test__")


def _git_init(path: Path, user="Test User"):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", user], cwd=str(path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=str(path), check=True
    )


def test_member_does_not_touch_other_members_entries(tmp_path):
    """A member install preserves every existing member entry."""
    cfg = {
        "spec_version": "0.1",
        "team": {"name": "acme"},
        "admin_contact": "founder",
        "services": {},
        "members": [{"name": "founder", "role": "pm"}],
    }
    (tmp_path / "team.config.json").write_text(json.dumps(cfg))
    il.scaffold_memory(
        tmp_path,
        member_name="bob",
        role="member",
        team_name="acme",
        member_role="developer",
    )
    members = json.loads((tmp_path / "team.config.json").read_text())["members"]
    assert {"name": "founder", "role": "pm"} in members
    assert {"name": "bob", "role": "developer"} in members


def test_bootstrap_scaffolds_introducer(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    assert (team / "memory" / "INDEX.md").is_file()
    sessions = team / "memory" / "team" / "sessions" / "testuser"
    assert sessions.is_dir()
    assert (team / "team.config.json").is_file()
    assert list(sessions.iterdir()) == []


def test_bootstrap_exit3_when_no_name_resolvable(tmp_path, capsys, monkeypatch):
    team = tmp_path / "team"
    team.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(team), check=True)
    home = tmp_path / "home"
    home.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    empty_gitconfig = tmp_path / "empty-gitconfig"
    empty_gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_gitconfig))
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 3
    assert "member-name" in capsys.readouterr().err.lower()


def test_bootstrap_invalid_member_name_exit3(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--member-name", "../escape"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 3


def test_bootstrap_idempotent_rerun(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    opts = il.parse_args(["--root", str(team)])
    _load_install()["bootstrap"](opts, home=home, python_version=(3, 13))
    cfg_before = (team / "team.config.json").read_text()
    _load_install()["bootstrap"](opts, home=home, python_version=(3, 13))
    assert (team / "team.config.json").read_text() == cfg_before
    members = (team / "memory" / "team" / "members.md").read_text()
    assert members.count("testuser") == 1


def test_bootstrap_i8_conflict_exit3_preserves_members(tmp_path, capsys):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team, user="Alice")
    subprocess.run(
        ["git", "config", "user.email", "alice@a.com"],
        cwd=str(team),
        check=True,
    )
    home = tmp_path / "home"
    home.mkdir()
    _load_install()["bootstrap"](
        il.parse_args(["--root", str(team)]),
        home=home,
        python_version=(3, 13),
    )
    members_file = team / "memory" / "team" / "members.md"
    members_before = members_file.read_text()
    subprocess.run(
        ["git", "config", "user.email", "mallory@evil.com"],
        cwd=str(team),
        check=True,
    )
    rc = _load_install()["bootstrap"](
        il.parse_args(["--root", str(team), "--member-name", "alice"]),
        home=home,
        python_version=(3, 13),
    )
    assert rc == 3
    assert "conflict" in capsys.readouterr().err
    assert members_file.read_text() == members_before
