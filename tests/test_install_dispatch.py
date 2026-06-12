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


def test_dispatch_known_agent_only_via_dir(tmp_path):
    # codex 디렉토리가 없으면 codex 위임 불가 (분기 하드코딩 아님 — 디렉토리 존재로 판정)
    rc = _run_dispatch(["--codex", "sync"])
    assert rc == 2
