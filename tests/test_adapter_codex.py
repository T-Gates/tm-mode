"""슬라이스 4 — Codex 어댑터 + 폴백 테스트 (스펙 02 §4·§7·§11.11).

Codex 특성:
  - events.json: PreToolUse=null (미지원) → 폴백 발동
  - actions.file_edit = "apply_patch"
  - config_file = ~/.codex/config.toml (TOML 블록)

검증:
  1. file_edit action 번역 (→ apply_patch)
  2. PreToolUse null → drop + [warn] (무음 스킵 부재, §7)
  3. enforcement 축소: block 훅이 Codex에서 표현 불가 시 폴백 + warn
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


# ── 2. PreToolUse null → drop + warn (무음 스킵 부재) ──

def test_pretooluse_null_drops_with_warn(env, capsys):
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
    # 무음 스킵 금지 — confirm-action 비활성 경고가 떠야 함
    assert "[warn]" in out
    assert "confirm-action.py" in out
    # PreToolUse 는 config 에 등록되지 않음
    text = env.config.read_text()
    assert "PreToolUse" not in text
    # PostToolUse 는 정상 등록
    assert "PostToolUse" in text


# ── 3. enforcement 축소: block 인데 표현 불가 → 폴백 + warn ──

def test_block_enforcement_reduced_when_unsupported(env, capsys):
    # block 훅이 PreToolUse(null)에 걸리면 Codex 에선 차단 불가 → 폴백 축소 + warn
    env.write_manifest([
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "fallback": "runtime",
         "enforcement": "block", "strict": True},
    ])
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    assert "[warn]" in out
    # 차단 훅이 비활성됐음을 알린다 (enforcement 축소)
    assert "confirm-action.py" in out


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

def test_same_manifest_reduced_on_codex(env, capsys):
    # 슬라이스 2 와 동일한 manifest 를 Codex 에 — PreToolUse 만 빠지고 나머지 등록
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
    assert "PreToolUse" not in text  # 축소
    out = capsys.readouterr().out
    assert "[warn]" in out
