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


def test_dispatch_codex_config_flag_passes_gate(tmp_path, monkeypatch):
    """C1: codex 자기 설정 플래그 --config 도 격리 의도로 인정(agent-aware 게이트).

    internals.md 부록 A.3 known gap이었다 — `--codex --config <path> sync`가
    어댑터 도달 전 exit 2. _AGENT_WIRE[agent]['flag'] 조회로 닫는다.
    """
    monkeypatch.setenv("TEAMMODE_CODEX_TRUST_CHECK", "0")  # 호스트 codex 프로브 차단
    cfg = tmp_path / "config.toml"
    rc = _run_dispatch(["--codex", "--config", str(cfg), "sync", "--on"])
    assert rc == 0
    assert cfg.is_file(), "격리 config.toml 에 쓰여야 함"
    assert "teammode-hooks-start" in cfg.read_text(encoding="utf-8")


def test_dispatch_claude_config_flag_still_refused(tmp_path, capsys):
    """C1 경계: claude 의 --config 는 팀 config(cfg_flag)지 격리 설정 플래그가 아니다.

    claude 의 격리 의도 플래그는 --settings 뿐 — --config 만으로는 여전히 exit 2
    (실 ~/.claude/settings.json 오염 방지 게이트 유지).
    """
    rc = _run_dispatch(["--claude", "--config", str(tmp_path / "team.config.json"),
                        "sync", "--on"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "settings" in err.lower() or "install" in err.lower()


def test_dispatch_codex_without_any_gate_flag_refused(capsys):
    """C1 경계: codex 도 아무 플래그 없으면 여전히 거부(실호스트 보호 불변)."""
    rc = _run_dispatch(["--codex", "sync", "--on"])
    assert rc == 2


def _team_root(tmp_path, name):
    """--root 번역 검증용 tmp 팀 루트(팀명 있는 config)."""
    import json as _json
    root = tmp_path / "teamroot"
    root.mkdir(exist_ok=True)
    (root / "team.config.json").write_text(_json.dumps(
        {"spec_version": "0.2", "team": {"name": name}}), encoding="utf-8")
    return root


def test_dispatch_translates_root_to_team_root(tmp_path, monkeypatch):
    """C3: 디스패치의 --root <값> 은 무언 제거가 아니라 --team-root 로 번역된다.

    관측: codex sync --on 의 statusMessage 팀명이 --root 가 가리키는
    team.config.json 의 team.name 에서 온다(번역이 실제로 어댑터에 닿음).
    """
    monkeypatch.setenv("TEAMMODE_CODEX_TRUST_CHECK", "0")
    root = _team_root(tmp_path, "rooted-team")
    cfg = tmp_path / "config.toml"
    rc = _run_dispatch(["--codex", "--config", str(cfg),
                        "--root", str(root), "sync", "--on"])
    assert rc == 0
    text = cfg.read_text(encoding="utf-8")
    assert "rooted-team" in text, "--root 팀명이 statusMessage 에 반영돼야 함(번역 증거)"


def test_dispatch_root_and_team_root_conflict_refused(tmp_path, capsys):
    """C3 경계: --root 와 --team-root 가 서로 다른 값이면 exit 2(모호성 거부)."""
    cfg = tmp_path / "config.toml"
    rc = _run_dispatch(["--codex", "--config", str(cfg),
                        "--root", str(tmp_path / "a"),
                        "--team-root", str(tmp_path / "b"), "sync", "--on"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--team-root" in err or "--root" in err


def test_dispatch_root_and_team_root_same_value_ok(tmp_path, monkeypatch):
    """C3 경계: 같은 값이면 중복 지정이어도 통과(놀람 없음)."""
    monkeypatch.setenv("TEAMMODE_CODEX_TRUST_CHECK", "0")
    root = _team_root(tmp_path, "same-team")
    cfg = tmp_path / "config.toml"
    rc = _run_dispatch(["--codex", "--config", str(cfg),
                        "--root", str(root),
                        "--team-root", str(root), "sync", "--on"])
    assert rc == 0
    assert "same-team" in cfg.read_text(encoding="utf-8")


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
