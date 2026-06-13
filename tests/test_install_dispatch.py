"""슬라이스 2 — install.py 디스패처 테스트.

디스패처는 분기 로직 없이 --<agent> → agents/<name>/adapter.py 위임만 한다(§2 불변식 3).
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _run_dispatch(argv):
    # install.py 를 모듈로 로드해 main 호출 (sys.argv 오염 복원)
    import runpy
    saved = sys.argv[:]
    try:
        mod = runpy.run_path(str(REPO / "infra" / "install.py"),
                             run_name="__dispatch_test__")
        return mod["main"](argv)
    finally:
        sys.argv = saved


def test_dispatch_unknown_agent_errors(capsys):
    rc = _run_dispatch(["sync", "--on"])  # 에이전트 플래그 없음
    assert rc == 2
    assert "에이전트를 지정" in capsys.readouterr().err


def test_dispatch_claude_sync_writes_settings(tmp_path):
    settings = tmp_path / "settings.json"
    rc = _run_dispatch(["--claude", "--settings", str(settings), "sync", "--on"])
    assert rc == 0
    assert settings.is_file()
    data = json.loads(settings.read_text())
    assert "hooks" in data
    # 실제 manifest(SessionStart 등)가 등록됨 + normalize 경유
    blob = json.dumps(data)
    assert "normalize.py" in blob


def test_dispatch_agent_resolved_by_dir_not_hardcode(tmp_path):
    # 에이전트는 agents/<name>/ 디렉토리 존재로 판정(분기 하드코딩 아님).
    # 존재하지 않는 에이전트 플래그는 위임 불가 → 에러.
    rc = _run_dispatch(["--nonexistent-agent", "sync"])
    assert rc == 2


def test_dispatch_refuses_sync_without_settings_or_install(capsys):
    """L1-0 P2 가드: --settings 도 --install 도 없으면 실 호스트 오염 거부(exit 2)."""
    rc = _run_dispatch(["--claude", "sync", "--on"])  # 격리/실설치 의사 없음
    assert rc == 2
    err = capsys.readouterr().err
    assert "settings" in err.lower() or "install" in err.lower()


def test_dispatch_install_flag_allows_real_install_path(tmp_path, monkeypatch):
    """--install 은 실설치 의사 — 가드를 통과하되 어댑터엔 넘기지 않는다.

    실 ~/.claude 오염을 피하려고 HOME 을 tmp 로 강제하고, fake HOME 하위
    .claude/settings.json 에만 쓰이는지 확인한다(실 호스트 무접촉).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home, raising=False)
    rc = _run_dispatch(["--claude", "--install", "sync", "--on"])
    assert rc == 0
    written = fake_home / ".claude" / "settings.json"
    assert written.is_file(), "fake HOME 의 .claude/settings.json 에 쓰여야 함"
