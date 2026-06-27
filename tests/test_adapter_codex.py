"""슬라이스 4 — Codex 어댑터 + 폴백 테스트 (스펙 02 §4·§7·§11.11).

Codex 특성:
  - events.json: PreToolUse 지원 → 차단 훅도 TOML hooks 에 등록
  - actions.file_edit = "apply_patch"
  - config_file = ~/.codex/config.toml (TOML 블록)

검증:
  1. file_edit action 번역 (→ apply_patch)
  2. PreToolUse 등록 + Codex MCP matcher 형식
  3. block enforcement 훅 등록
  4. normalize 경유 배선
  5. 멱등
모든 테스트 tmp_path — 실 ~/.codex 무접촉.
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_codex_adapter():
    import runpy
    mod = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                         run_name="__codex_test__")
    return mod["Adapter"]


def _events():
    return {
        "agent": "codex",
        "config_file": "~/.codex/config.toml",
        "events": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
        },
        "actions": {"file_edit": "apply_patch"},
        "mcp_tool_format": "mcp__{server}__{tool}",
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


# ── 1. action 번역 → apply_patch ──

def test_file_edit_translated_to_apply_patch(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    text = env.config.read_text()
    assert "apply_patch" in text
    assert "PostToolUse" in text


# ── 2. PreToolUse 지원 → 등록 + Codex matcher ──

def test_pretooluse_registered_with_codex_mcp_matcher(env, capsys):
    env.write_manifest([
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "fallback": "runtime",
         "enforcement": "block"},
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    assert "[warn]" not in out
    text = env.config.read_text()
    assert "[[hooks.PreToolUse]]" in text
    assert 'matcher = "mcp__tm-linear__create_issue"' in text
    assert "confirm-action.py" in text
    assert "PostToolUse" in text


# ── 3. enforcement 유지: block 훅도 Codex PreToolUse 로 등록 ──

def test_block_enforcement_registered_on_pretooluse(env, capsys):
    env.write_manifest([
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "fallback": "runtime",
         "enforcement": "block", "strict": True},
    ])
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    assert "[warn]" not in out
    text = env.config.read_text()
    assert "[[hooks.PreToolUse]]" in text
    assert "confirm-action.py" in text


# ── 4. normalize 경유 배선 ──

def test_command_routed_through_normalize(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    text = env.config.read_text()
    assert "normalize.py" in text
    assert "auto-commit.py" in text


# ── 5. 멱등 ──

def test_idempotent(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    first = env.config.read_text()
    env.make_adapter().sync(mode="on")
    second = env.config.read_text()
    assert first == second


# ── 6. 사용자 config 보존 (teammode 블록만 관리) ──

def test_user_config_preserved(env):
    env.config.write_text('model = "o1"\n\n[some.user.setting]\nkey = "val"\n')
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    text = env.config.read_text()
    assert 'model = "o1"' in text
    assert "[some.user.setting]" in text
    assert "apply_patch" in text


# ── 7. 크로스에이전트: 같은 manifest 가 Codex 에선 축소되어 표현 ──

# ── 8. Codex timeout — manifest 초 → TOML 에 초로 그대로 ──

def test_codex_timeout_written_as_seconds(env):
    """manifest timeout=3(초) → config.toml 에 'timeout = 3' 이 그대로 기록된다(변환 없음)."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime", "timeout": 3},
    ])
    env.make_adapter().sync(mode="on")
    text = env.config.read_text()
    assert "timeout = 3" in text


def test_codex_no_timeout_when_manifest_omits(env):
    """manifest 에 timeout 없으면 TOML 에도 'timeout = ...' 행 없음."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    text = env.config.read_text()
    # 'timeout = <숫자>' 패턴이 없어야 한다(경로 안에 'timeout' 문자열이 있을 수 있으므로
    # 단순 포함 검사 대신 패턴 검사).
    import re
    assert not re.search(r'^timeout\s*=', text, re.MULTILINE)


def test_same_manifest_preserves_pretooluse_on_codex(env, capsys):
    # 슬라이스 2 와 동일한 manifest 를 Codex 에 — PreToolUse 까지 등록
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
        {"event": "UserPromptSubmit", "script": "session-log-remind.py", "mode": "on"},
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    text = env.config.read_text()
    assert "SessionStart" in text
    assert "UserPromptSubmit" in text
    assert "PostToolUse" in text
    assert "PreToolUse" in text
    assert 'matcher = "mcp__tm-linear__create_issue"' in text
    out = capsys.readouterr().out
    assert "[warn]" not in out
