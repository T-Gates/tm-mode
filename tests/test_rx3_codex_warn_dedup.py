"""처방 3 — codex adapter warn 도배 제거 (TDD).

동일 이벤트(PreToolUse 미지원)에 대해 N개 항목이 warn을 발생시킬 때,
N줄 반복 대신 묶어 1줄 요약으로 출력해야 한다.
기존 [warn] + 스크립트명 포함 어설션은 여전히 통과해야 한다.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_codex_adapter():
    import runpy
    mod = runpy.run_path(
        str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
        run_name="__rx3_test__",
    )
    return mod["Adapter"]


def _events():
    return {
        "agent": "codex",
        "config_file": "~/.codex/config.toml",
        "events": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": None,
            "PostToolUse": "PostToolUse",
        },
        "actions": {"file_edit": "apply_patch"},
        "mcp_tool_format": "{server}.{tool}",
    }


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "codex"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_events()))
    (agent_dir / "normalize.py").write_text("# stub\n")
    config = tmp_path / "config.toml"

    Adapter = _load_codex_adapter()

    def write_manifest(entries):
        (hooks_dir / "manifest.json").write_text(json.dumps(entries))

    def make_adapter():
        return Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(config),
            python="python3",
            team_root=str(root),
        )

    class E:
        pass
    e = E()
    e.root = root
    e.agent_dir = agent_dir
    e.config = config
    e.write_manifest = write_manifest
    e.make_adapter = make_adapter
    return e


def test_multiple_pretooluse_warns_collapsed_to_one_line(env, capsys):
    """PreToolUse 미지원으로 warn이 7개 발생할 때 출력 줄이 1줄이어야 한다."""
    # confirm-action.py 가 7개의 PreToolUse 항목으로 등록된 상황 시뮬레이션
    manifest = [
        {
            "event": "PreToolUse",
            "match": {"mcp": {"server": f"svc{i}", "tool": "do_something"}},
            "script": "confirm-action.py",
            "fallback": "drop",
            "enforcement": "block",
        }
        for i in range(7)
    ]
    env.write_manifest(manifest)
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    warn_lines = [l for l in out.splitlines() if "[warn]" in l]
    assert len(warn_lines) == 1, (
        f"warn 줄이 1줄이어야 하는데 {len(warn_lines)}줄 출력됨:\n{out}"
    )
    # 묶음 요약에 스크립트명과 개수 정보가 포함되어야 함
    assert "confirm-action.py" in out
    assert "7" in out


def test_mixed_warns_collapsed_by_script(env, capsys):
    """confirm-action.py 7개 + kb-write-guard.py 1개 → warn 줄이 2줄 이하여야 한다."""
    manifest = [
        {
            "event": "PreToolUse",
            "match": {"mcp": {"server": f"svc{i}", "tool": "act"}},
            "script": "confirm-action.py",
            "fallback": "drop",
            "enforcement": "block",
        }
        for i in range(7)
    ] + [
        {
            "event": "PreToolUse",
            "match": {"action": "file_edit"},
            "script": "kb-write-guard.py",
            "fallback": "drop",
            "enforcement": "block",
        }
    ]
    env.write_manifest(manifest)
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    warn_lines = [l for l in out.splitlines() if "[warn]" in l]
    # 8줄 → 2줄 이하 (스크립트별 묶기 또는 이벤트별 묶기)
    assert len(warn_lines) <= 2, (
        f"warn 줄이 2줄 이하여야 하는데 {len(warn_lines)}줄 출력됨:\n{out}"
    )
    assert "confirm-action.py" in out
    assert "kb-write-guard.py" in out


def test_single_pretooluse_warn_still_shows(env, capsys):
    """PreToolUse warn이 1개일 때도 출력이 있어야 한다 (무음 스킵 금지)."""
    env.write_manifest([
        {
            "event": "PreToolUse",
            "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
            "script": "confirm-action.py",
            "fallback": "runtime",
            "enforcement": "block",
        },
    ])
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "confirm-action.py" in out
