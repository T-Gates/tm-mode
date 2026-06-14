"""W-D — Windows 라운드트립 e2e (CHECKLIST 🪟 W-D).

install→on→context→uninstall 을 **nt 모킹**(sys.platform=win32 + setx/reg runner 주입)
으로 통과시킨다. 실 setx/reg 미실행(runner 레코더). 호스트 무접촉(fake HOME + 격리/모킹).

파이=Linux → 실 윈도우 동작(레지스트리 반영 등)은 Jane 내일. 여기선 "윈도우 분기를
끝까지 타고, 올바른 명령을 만들고, 라운드트립이 깨지지 않는다"만 단언.
"""
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


class _RecordingRunner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))

        class _R:
            pass
        r = _R()
        r.returncode = self.returncode
        r.stdout = ""
        r.stderr = ""
        return r


def _git_init(path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Wendy"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "w@w.com"], cwd=str(path), check=True)


def _load_install(run_name):
    return runpy.run_path(str(INSTALL_PY), run_name=run_name)


def test_windows_roundtrip_install_on_context_uninstall(tmp_path, monkeypatch):
    """nt 모킹 풀 라운드트립: bootstrap(setx) → context 읽힘 → uninstall(reg delete)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    # nt 모킹: platform="win32" 를 bootstrap/cmd_uninstall 에 *명시 주입*(전역 sys.platform
    # 은 안 건드림 — 그러면 stdlib subprocess/git 까지 윈도우 분기로 깨짐). + _default_runner
    # 레코더(실 setx/reg 차단).
    recorder = _RecordingRunner()
    monkeypatch.setattr(il, "_default_runner", recorder)

    # ── install (bootstrap, 실설치 --yes 로 윈도우 env=setx 경로) ──
    mod = _load_install("__win_rt_install__")
    opts = il.parse_args(["--root", str(team), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13),
                          platform="win32")
    assert rc == 0, "윈도우 부트스트랩 실패"

    # setx 로 env 주입(셸 프로파일 무접촉)
    setx = [c for c in recorder.calls if c and c[0] == "setx"]
    assert len(setx) == 1
    assert setx[0][1] == "TEAMMODE_HOME"
    assert setx[0][2] == str(team.resolve())
    for name in (".bashrc", ".zshrc"):
        p = home / name
        if p.is_file():
            assert "TEAMMODE_HOME" not in p.read_text()

    # on 단계 산물: active 마커·배너·memory (verify 가 on 호출)
    assert (team / ".acme-active").is_file()
    assert (team / "memory" / "INDEX.md").is_file()

    # ── context: 엔진이 L1 데이터 읽힘 ──
    engine = REPO / "infra" / "teammode.py"
    ctx = subprocess.run(
        [sys.executable, str(engine), "context", "--root", str(team), "--json"],
        capture_output=True, text=True, timeout=30)
    assert ctx.returncode == 0, ctx.stderr
    data = json.loads(ctx.stdout)
    assert data.get("state") == "on"

    # ── uninstall: reg delete 로 env 제거(윈도우 분기) ──
    recorder.calls.clear()
    mod_u = _load_install("__win_rt_uninstall__")
    rc_u = mod_u["cmd_uninstall"]({"root": str(team), "yes": True},
                                  platform="win32")
    assert rc_u == 0
    reg = [c for c in recorder.calls if c and c[0] == "reg"]
    assert len(reg) == 1
    assert reg[0][1] == "delete"
    assert "Environment" in " ".join(reg[0])
    assert "TEAMMODE_HOME" in reg[0]
    # off 되돌림: active 마커 제거
    assert not (team / ".acme-active").is_file()
    # 팀 데이터(memory)는 보존
    assert (team / "memory" / "INDEX.md").is_file()


def test_windows_roundtrip_isolated_no_setx(tmp_path, monkeypatch):
    """격리(--settings) nt 라운드트립: 실 호스트 env(setx/reg) 절대 미실행."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    monkeypatch.setenv("HOME", str(home))
    recorder = _RecordingRunner()
    monkeypatch.setattr(il, "_default_runner", recorder)

    mod = _load_install("__win_rt_iso_install__")
    opts = il.parse_args(["--root", str(team), "--settings", str(iso), "--yes"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13),
                          platform="win32")
    assert rc == 0
    # 격리면 setx 안 부름
    assert [c for c in recorder.calls if c and c[0] == "setx"] == []

    # uninstall 도 격리 → reg delete 안 부름(실 호스트 env 무접촉)
    recorder.calls.clear()
    mod_u = _load_install("__win_rt_iso_uninstall__")
    rc_u = mod_u["cmd_uninstall"](
        {"root": str(team), "settings": str(iso / "claude" / "settings.json")},
        platform="win32")
    assert rc_u == 0
    assert [c for c in recorder.calls if c and c[0] == "reg"] == [], \
        "격리 모드인데 reg delete 가 실행됐다(실 호스트 env 위험)"
