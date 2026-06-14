"""W-C — POSIX 종속 감사 회귀 테스트 (CHECKLIST 🪟 W-C).

install_lib·install.py·hooks·adapter·teammode 전수 grep 으로 색출한 POSIX 가정을
플랫폼 분기/pathlib 로 교정한 것을 회귀로 박는다. 파이=Linux → 윈도우는 플랫폼 주입으로 모사.
"""
import os
import runpy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
sys.path.insert(0, str(REPO / "infra" / "agents" / "claude"))

import install_lib as il  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__posix_audit_test__")


# ─────────────── _default_profile: 윈도우엔 셸 프로파일 없음 ───────────────

def test_default_profile_windows_returns_none():
    """Windows: env 는 레지스트리(setx)에 살아서 셸 프로파일 대상 없음 → None."""
    mod = _load_install()
    prof = mod["_default_profile"](platform="win32")
    assert prof is None


def test_default_profile_posix_returns_bashrc():
    """POSIX: 기존대로 ~/.bashrc (무회귀)."""
    mod = _load_install()
    prof = mod["_default_profile"](platform="linux")
    assert prof is not None
    assert str(prof).endswith(".bashrc")


def test_default_profile_default_uses_sys_platform():
    """인자 없으면 sys.platform 사용(파이=linux → bashrc)."""
    mod = _load_install()
    prof = mod["_default_profile"]()
    # 현재 실행 플랫폼(linux)에선 bashrc, 윈도우면 None — 둘 중 하나(크래시 안 함)
    assert prof is None or str(prof).endswith(".bashrc")


# ─────────────── 셸 경로 감지: 백슬래시도 경로로 인식 ───────────────

def test_shell_path_detection_handles_backslash():
    """윈도우식 백슬래시 셸 경로도 '경로'로 인식(detect_shell 경유).

    bootstrap 의 `shell` 파라미터 정규화: '/' 뿐 아니라 '\\' 도 경로 구분자로 본다.
    (detect_shell 자체는 Path(...).name 으로 양쪽 다 처리하므로, 정규화 분기가
    백슬래시를 누락하면 안 됨.)
    """
    # detect_shell 은 Path 기반이라 백슬래시 경로의 basename 도 정확히 추출
    assert il.detect_shell(r"C:\msys64\usr\bin\bash.exe") == "bash"
    assert il.detect_shell(r"C:\Program Files\zsh\zsh.exe") == "zsh"


# ─────────────── settings 기본 경로: expanduser 크로스플랫폼 ───────────────

def test_default_settings_paths_use_expanduser():
    """~/.claude/settings.json 등은 expanduser(크로스플랫폼) — 하드코딩 절대경로 아님.

    소스 가드: 엔진/어댑터가 '/home/' 같은 POSIX 절대경로를 하드코딩하지 않는다.
    """
    for rel in ("infra/teammode.py", "infra/agents/claude/adapter.py",
                "infra/agents/codex/adapter.py", "infra/install.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "/home/" not in src, f"{rel} 에 POSIX 절대경로 하드코딩"
        assert '"/Users/' not in src, f"{rel} 에 mac 절대경로 하드코딩"


# ─────────────── 런타임 훅: 경로 조립 os.path.join (구분자 무하드코딩) ───────────────

def test_default_obsidian_config_windows_uses_appdata():
    """Windows obsidian.json → AppData\\Roaming 경로(Linux XDG 가정 제거)."""
    mod = _load_install()
    p = mod["_default_obsidian_config"](platform="win32")
    s = str(p)
    assert "obsidian.json" in s
    assert "AppData" in s and "Roaming" in s


def test_default_obsidian_config_linux_uses_config():
    mod = _load_install()
    p = mod["_default_obsidian_config"](platform="linux")
    assert "obsidian.json" in str(p)


def test_bootstrap_normalizes_backslash_shell(tmp_path, monkeypatch):
    """bootstrap 의 shell 경로 정규화가 백슬래시도 경로로 인식(detect_shell 경유).

    윈도우식 셸 경로를 명시 주입해도 종류(bash)로 정규화돼야 한다.
    (platform=linux 로 둬서 env 주입은 셸 프로파일 경로 — 정규화만 검증.)
    """
    import subprocess
    team = tmp_path / "team"
    team.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(team), check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=str(team), check=True)
    subprocess.run(["git", "config", "user.email", "a@a"], cwd=str(team), check=True)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13),
                          shell=r"C:\msys64\usr\bin\bash.exe", platform="linux")
    assert rc == 0
    # bash 로 정규화됐다면 .bashrc 에 env 주입됨
    assert (home / ".bashrc").is_file()
    assert "TEAMMODE_HOME" in (home / ".bashrc").read_text()


def test_hooks_use_os_path_join_not_string_slash():
    """런타임 훅의 경로 조립이 리터럴 '/' 결합이 아니라 os.path.join/pathlib.

    (윈도우에서 'root' + '/memory/...' 식 문자열 결합은 깨질 수 있음.)
    """
    for rel in ("infra/hooks/session-log-remind.py",
                "infra/hooks/session-start.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        # 명백한 안티패턴: f"{root}/memory" 류 직접 결합 금지
        assert 'f"{root}/' not in src, f"{rel} 에 리터럴 '/' 경로 결합"
        assert "+ '/'" not in src and '+ "/"' not in src, \
            f"{rel} 에 '/' 문자열 덧붙이기"
