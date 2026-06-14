"""W-A — Windows 네이티브 env 주입/제거 테스트 (CHECKLIST 🪟 W-A).

핵심 제약(파이=Linux, 실 윈도우 없음): Windows 코드 경로는 **플랫폼 모킹 +
subprocess(setx/reg) 모킹**으로 단위 테스트한다. **실제 setx/reg 를 절대 실행하지
않는다** — runner 를 주입해 "올바른 명령·인자를 만드는가"만 단언한다.

호스트 철칙: 실 환경변수/레지스트리/셸 프로파일 무접촉. 전부 모킹/격리.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


# ─────────────────────────── 플랫폼 감지 ───────────────────────────

def test_is_windows_detects_win_platforms():
    assert il.is_windows("win32") is True
    assert il.is_windows("cygwin") is True   # cygwin 도 윈도우 위
    assert il.is_windows("linux") is False
    assert il.is_windows("linux2") is False
    assert il.is_windows("darwin") is False


def test_is_windows_default_uses_sys_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert il.is_windows() is True
    monkeypatch.setattr(sys, "platform", "linux")
    assert il.is_windows() is False


# ─────────────────────────── 주입 (setx) ───────────────────────────

class _RecordingRunner:
    """subprocess 대역 — 호출 인자만 기록(실행 안 함). returncode 0 성공 모사."""
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))

        class _R:
            pass
        r = _R()
        r.returncode = self.returncode
        r.stdout = ""
        r.stderr = ""
        return r


def test_inject_env_windows_runs_setx(tmp_path):
    """nt 모킹 → setx TEAMMODE_HOME "<절대경로>" 명령을 만든다. 실행 안 함(runner 주입)."""
    team = tmp_path / "team"
    runner = _RecordingRunner()
    res = il.inject_env("bash", tmp_path / "home", team,
                        platform="win32", runner=runner)
    assert res["injected"] is True
    # setx 호출 정확히 1회, 인자: setx TEAMMODE_HOME <abs>
    assert len(runner.calls) == 1
    argv, _ = runner.calls[0]
    assert argv[0] == "setx"
    assert argv[1] == "TEAMMODE_HOME"
    assert argv[2] == str(team)
    # 셸 프로파일은 절대 안 건드림(윈도우는 setx 가 영구 user env)
    assert not (tmp_path / "home" / ".bashrc").exists()


def test_inject_env_windows_uses_absolute_path(tmp_path, monkeypatch):
    """상대 팀루트도 절대경로로 setx (HKCU\\Environment 는 절대경로라야 의미)."""
    monkeypatch.chdir(tmp_path)
    runner = _RecordingRunner()
    res = il.inject_env("bash", tmp_path / "home", Path("relteam"),
                        platform="win32", runner=runner)
    assert res["injected"] is True
    argv, _ = runner.calls[0]
    assert Path(argv[2]).is_absolute()
    assert argv[2].endswith("relteam")


def test_inject_env_windows_failure_is_nonfatal(tmp_path):
    """setx 실패(rc!=0) → injected False + reason, raise 안 함(비치명)."""
    runner = _RecordingRunner(returncode=1)
    res = il.inject_env("bash", tmp_path / "home", tmp_path / "team",
                        platform="win32", runner=runner)
    assert res["injected"] is False
    assert "reason" in res


def test_inject_env_windows_runner_raises_is_nonfatal(tmp_path):
    """runner 가 raise(예: setx 부재) → 비치명으로 흡수."""
    def boom(*a, **k):
        raise FileNotFoundError("setx not found")
    res = il.inject_env("bash", tmp_path / "home", tmp_path / "team",
                        platform="win32", runner=boom)
    assert res["injected"] is False
    assert "reason" in res


# ─────────────────────────── posix 무회귀 ───────────────────────────

def test_inject_env_posix_still_uses_profile(tmp_path):
    """platform=linux 명시 시 기존 셸 프로파일 경로 — Windows 분기 안 탐(무회귀)."""
    home = tmp_path / "home"
    home.mkdir()
    runner = _RecordingRunner()
    res = il.inject_env("bash", home, tmp_path / "team",
                        platform="linux", runner=runner)
    assert res["injected"] is True
    assert (home / ".bashrc").is_file()
    assert "TEAMMODE_HOME" in (home / ".bashrc").read_text()
    assert runner.calls == []   # setx 안 부름


# ─────────────────────────── 제거 (reg delete) ───────────────────────────

def test_remove_injected_env_windows_runs_reg_delete():
    """nt 모킹 → reg delete HKCU\\Environment /v TEAMMODE_HOME /f. 실행 안 함."""
    runner = _RecordingRunner()
    changed = il.remove_injected_env("/ignored/.bashrc",
                                     platform="win32", runner=runner)
    assert changed is True
    assert len(runner.calls) == 1
    argv, _ = runner.calls[0]
    assert argv[0] == "reg"
    assert argv[1] == "delete"
    # HKCU\Environment 키, /v TEAMMODE_HOME, /f
    joined = " ".join(argv)
    assert "Environment" in joined
    assert "TEAMMODE_HOME" in joined
    assert "/v" in argv
    assert "/f" in argv


def test_remove_injected_env_windows_missing_var_nonfatal():
    """변수가 이미 없으면 reg delete rc!=0 → changed False, raise 안 함(멱등·비치명)."""
    runner = _RecordingRunner(returncode=1)
    changed = il.remove_injected_env("/ignored", platform="win32", runner=runner)
    assert changed is False


def test_remove_injected_env_windows_runner_raises_nonfatal():
    def boom(*a, **k):
        raise FileNotFoundError("reg not found")
    changed = il.remove_injected_env("/ignored", platform="win32", runner=boom)
    assert changed is False


def test_remove_injected_env_posix_still_edits_profile(tmp_path):
    """platform=linux 명시 시 기존 프로파일 줄 제거 경로(무회귀)."""
    p = tmp_path / ".bashrc"
    p.write_text(
        "export KEEP=1\n"
        'export TEAMMODE_HOME="/x"  # teammode (env injection, §9)\n',
        encoding="utf-8")
    runner = _RecordingRunner()
    changed = il.remove_injected_env(p, platform="linux", runner=runner)
    assert changed is True
    text = p.read_text()
    assert "TEAMMODE_HOME" not in text
    assert "export KEEP=1" in text
    assert runner.calls == []


# ─────────────────── bootstrap → Windows env 통합 (nt 모킹) ───────────────────

import runpy  # noqa: E402
import subprocess  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


def _git_init(path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Win"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "w@w"], cwd=str(path), check=True)


def test_bootstrap_windows_runs_setx_not_profile(tmp_path, monkeypatch):
    """nt 모킹 부트스트랩 → setx 명령 생성(셸 프로파일 무접촉). 실 setx 실행 안 함."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # _default_runner 를 레코더로 교체 = 실 setx/verify subprocess 실행 차단
    recorder = _RecordingRunner()
    monkeypatch.setattr(il, "_default_runner", recorder)

    mod = runpy.run_path(str(INSTALL_PY), run_name="__win_bootstrap_test__")
    opts = il.parse_args(["--root", str(team), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13),
                          platform="win32")
    assert rc == 0
    # setx 호출이 기록됐는가(올바른 명령·인자)
    setx_calls = [c for c in recorder.calls if c[0][0] == "setx"]
    assert len(setx_calls) == 1
    argv, _ = setx_calls[0]
    assert argv[1] == "TEAMMODE_HOME"
    assert argv[2] == str(team.resolve())
    # 셸 프로파일은 절대 안 건드림
    for name in (".bashrc", ".zshrc", ".profile", ".bash_profile"):
        p = home / name
        if p.is_file():
            assert "TEAMMODE_HOME" not in p.read_text(), \
                f"윈도우인데 {name} 에 env 가 샜다(setx 만 써야 함)"


def test_bootstrap_windows_isolated_skips_setx(tmp_path, monkeypatch):
    """격리(--settings) 면 윈도우에서도 setx 안 부름(실 호스트 env 무접촉)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    recorder = _RecordingRunner()
    monkeypatch.setattr(il, "_default_runner", recorder)

    mod = runpy.run_path(str(INSTALL_PY), run_name="__win_iso_test__")
    opts = il.parse_args(["--root", str(team), "--settings", str(iso), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13),
                          platform="win32")
    assert rc == 0
    setx_calls = [c for c in recorder.calls if c[0][0] == "setx"]
    assert setx_calls == [], "격리 모드인데 setx 가 실행됐다(실 호스트 env 오염 위험)"
