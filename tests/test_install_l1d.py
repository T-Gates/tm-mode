"""L1-D — env 주입 테스트 (spec/04 §9, m2).

셸 프로파일 감지·TEAMMODE_HOME 멱등 1줄 주입. 호스트 무접촉 철칙(B1):
HOME=tmp + fake 프로파일로만. 실 ~/.bashrc 등은 conftest 가드가 보호(L1-0).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


# ─────────────────────────── 셸 감지 ───────────────────────────

def test_detect_shell_variants():
    assert il.detect_shell("/bin/bash") == "bash"
    assert il.detect_shell("/usr/bin/zsh") == "zsh"
    assert il.detect_shell("/usr/local/bin/fish") == "fish"
    assert il.detect_shell("/bin/sh") is None        # 미지원
    assert il.detect_shell(None) is None
    assert il.detect_shell("") is None


def test_profile_path_for(tmp_path):
    assert il.profile_path_for("bash", tmp_path) == tmp_path / ".bashrc"
    assert il.profile_path_for("zsh", tmp_path) == tmp_path / ".zshrc"
    assert il.profile_path_for("fish", tmp_path) == \
        tmp_path / ".config" / "fish" / "config.fish"
    assert il.profile_path_for("tcsh", tmp_path) is None


# ─────────────────────────── 주입 (멱등·셸별) ───────────────────────────

def test_inject_bash_creates_export(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    team = tmp_path / "team"
    res = il.inject_env("bash", home, team)
    assert res["injected"] is True
    content = (home / ".bashrc").read_text()
    assert "TEAMMODE_HOME" in content
    assert str(team) in content
    assert "export" in content


def test_inject_fish_uses_set_gx(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    team = tmp_path / "team"
    res = il.inject_env("fish", home, team)
    assert res["injected"] is True
    content = (home / ".config" / "fish" / "config.fish").read_text()
    assert "set -gx TEAMMODE_HOME" in content


def test_inject_idempotent_no_duplicate(tmp_path):
    """재실행 시 중복 라인 0(§9 멱등)."""
    home = tmp_path / "home"
    home.mkdir()
    team = tmp_path / "team"
    il.inject_env("bash", home, team)
    res2 = il.inject_env("bash", home, team)
    assert res2["injected"] is False           # 이미 최신
    content = (home / ".bashrc").read_text()
    assert content.count("TEAMMODE_HOME") == 1


def test_inject_preserves_existing_content(tmp_path):
    """기존 프로파일 내용 보존 — 끝에 1줄 append."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("# my stuff\nalias ll='ls -la'\n")
    team = tmp_path / "team"
    il.inject_env("bash", home, team)
    content = (home / ".bashrc").read_text()
    assert "my stuff" in content
    assert "alias ll" in content
    assert "TEAMMODE_HOME" in content


def test_inject_updates_on_root_change(tmp_path):
    """팀루트가 바뀌면 마커 라인만 교체(중복 금지)."""
    home = tmp_path / "home"
    home.mkdir()
    il.inject_env("bash", home, tmp_path / "team1")
    il.inject_env("bash", home, tmp_path / "team2")
    content = (home / ".bashrc").read_text()
    assert content.count("TEAMMODE_HOME") == 1   # 1줄만
    assert "team2" in content
    assert "team1" not in content


def test_inject_unsupported_shell(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    res = il.inject_env("tcsh", home, tmp_path / "team")
    assert res["injected"] is False
    assert "미지원" in res["reason"]


def test_inject_cleans_duplicate_markers(tmp_path):
    """과거 버그로 마커 라인 2개면 → 1개로 정리(방어)."""
    home = tmp_path / "home"
    home.mkdir()
    line = il._env_line("bash", tmp_path / "old")
    (home / ".bashrc").write_text(f"{line}\n{line}\n# other\n")
    il.inject_env("bash", home, tmp_path / "new")
    content = (home / ".bashrc").read_text()
    assert content.count("TEAMMODE_HOME") == 1
    assert "new" in content


# ─────────────────────── bootstrap → env 통합 ───────────────────────

import json  # noqa: E402
import runpy  # noqa: E402
import subprocess  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__install_l1d_test__")


def _git_init(path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Frank"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "f@f"], cwd=str(path), check=True)


def test_bootstrap_isolated_settings_skips_env(tmp_path, monkeypatch):
    """⑥env (격리): --settings 지정 = 격리 모드 → 실 호스트 프로파일에 env 주입 안 함.

    회귀 방지(도그푸딩 버그): --settings 는 *격리 의도*이므로 agent settings 뿐
    아니라 env 주입까지 실 HOME 프로파일을 건드리면 안 된다(spec/04 §10 I4b).
    fake HOME 의 .bashrc/.zshrc/.profile/.bash_profile 어디에도 TEAMMODE_HOME 줄이
    안 생겨야 한다.
    """
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    monkeypatch.setenv("SHELL", "/bin/bash")
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--settings", str(iso), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    for name in (".bashrc", ".zshrc", ".profile", ".bash_profile"):
        p = home / name
        if p.is_file():
            assert "TEAMMODE_HOME" not in p.read_text(), \
                f"격리(--settings)인데 {name} 에 env 가 샜다"


def test_bootstrap_real_install_injects_env_to_fake_home(tmp_path, monkeypatch):
    """⑥env (실설치): --settings 없이 --yes 면 fake HOME 프로파일에 env 주입(정상)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("SHELL", "/bin/bash")
    # 엔진 verify subprocess 가 실 ~/.claude 를 건드리지 않게 HOME 도 fake 로(B1).
    monkeypatch.setenv("HOME", str(home))
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    bashrc = home / ".bashrc"
    assert bashrc.is_file()
    assert "TEAMMODE_HOME" in bashrc.read_text()
    assert str(team) in bashrc.read_text()


def test_bootstrap_real_install_env_idempotent(tmp_path, monkeypatch):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("HOME", str(home))
    opts = il.parse_args(["--root", str(team), "--yes"])
    _load_install()["bootstrap"](opts, home=home, python_version=(3, 13))
    _load_install()["bootstrap"](opts, home=home, python_version=(3, 13))
    content = (home / ".bashrc").read_text()
    assert content.count("TEAMMODE_HOME") == 1
