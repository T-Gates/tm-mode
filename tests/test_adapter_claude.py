"""슬라이스 2 — Claude 어댑터 sync 테스트.

6 케이스 (스펙 02 §4·§5):
  1. 정규 엔트리 → settings.json에 등록 (정규형 manifest)
  2. action 번역 (file_edit → "Write|Edit")
  3. mcp 번역 (mcp:{server,tool} → "mcp__linear__create_issue")
  4. 멱등 (재실행 시 settings.json 무변경)
  5. 제거 (manifest에서 빠지면 settings에서도 제거 / 사용자 훅 무접촉)
  6. normalize 경유 배선 (등록 커맨드가 agents/claude/normalize.py 가리킴)

모든 테스트는 tmp_path에 가짜 settings.json·manifest를 둔다 — 실환경 무접촉.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra" / "agents" / "claude"))

import adapter as claude_adapter  # noqa: E402


# ── 픽스처: 가짜 팀 루트 + manifest + events.json + 빈 settings ──

def _events_json():
    return {
        "agent": "claude",
        "config_file": "~/.claude/settings.json",
        "events": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
        },
        "actions": {"file_edit": "Write|Edit"},
        "mcp_tool_format": "mcp__{server}__{tool}",
    }


@pytest.fixture
def env(tmp_path):
    """team_root/infra/{hooks,agents/claude} + 별도 settings.json 경로."""
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "claude"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_events_json()))
    # normalize.py 가 존재해야 경유 배선 경로가 실제 파일을 가리킴
    (agent_dir / "normalize.py").write_text("# stub\n")

    settings = tmp_path / "settings.json"

    def write_manifest(entries):
        (hooks_dir / "manifest.json").write_text(json.dumps(entries))

    def make_adapter():
        return claude_adapter.Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(settings),
            python="python3",
            team_root=str(root),
        )

    class Env:
        pass
    e = Env()
    e.root = root
    e.agent_dir = agent_dir
    e.settings = settings
    e.write_manifest = write_manifest
    e.make_adapter = make_adapter
    return e


def _load(settings_path):
    return json.loads(Path(settings_path).read_text())


def _all_commands(settings):
    cmds = []
    for event, arr in settings.get("hooks", {}).items():
        for entry in arr:
            for h in entry.get("hooks", []):
                cmds.append((event, entry.get("matcher"), h.get("command")))
    return cmds


# ── 1. 정규 엔트리 등록 ──

def test_canonical_entry_registered(env):
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    settings = _load(env.settings)
    assert "SessionStart" in settings["hooks"]
    cmds = _all_commands(settings)
    assert any(event == "SessionStart" for event, _, _ in cmds)


# ── 2. action 번역 ──

def test_action_translated_to_matcher(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    cmds = _all_commands(_load(env.settings))
    matchers = [m for e, m, c in cmds if e == "PostToolUse"]
    assert "Write|Edit" in matchers


# ── 3. mcp 번역 ──

def test_mcp_translated_to_matcher(env):
    env.write_manifest([
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "args": "allow", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    cmds = _all_commands(_load(env.settings))
    matchers = [m for e, m, c in cmds if e == "PreToolUse"]
    assert "mcp__linear__create_issue" in matchers


# ── 4. 멱등 ──

def test_idempotent(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])
    a = env.make_adapter()
    a.sync(mode="on")
    first = env.settings.read_text()
    env.make_adapter().sync(mode="on")
    second = env.settings.read_text()
    assert first == second


# ── 5. 제거 ──

def test_removal_when_dropped_from_manifest(env):
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    assert any(e == "SessionStart" for e, _, _ in _all_commands(_load(env.settings)))

    # auto-commit 만 남기고 session-start 제거
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    cmds = _all_commands(_load(env.settings))
    scripts = " ".join(c or "" for _, _, c in cmds)
    assert "session-start.py" not in scripts
    assert "auto-commit.py" in scripts


def test_user_hooks_untouched(env):
    # 사용자가 직접 등록한 훅(normalize 경유 아님)은 건드리지 않는다
    user_settings = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "my-own-script.sh"}
                ]}
            ]
        }
    }
    env.settings.write_text(json.dumps(user_settings))
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    cmds = _all_commands(_load(env.settings))
    assert any(c == "my-own-script.sh" for _, _, c in cmds)


# ── 6. normalize 경유 배선 ──

def test_command_routed_through_normalize(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    cmds = _all_commands(_load(env.settings))
    cmd = [c for e, m, c in cmds if e == "PostToolUse"][0]
    # agents/claude/normalize.py 를 경유하고 공통 스크립트를 직접 등록하지 않음
    assert "normalize.py" in cmd
    assert "auto-commit.py" in cmd  # 인자로 넘어감
    # normalize 가 공통 스크립트 앞에 와야 함 (배선 순서)
    assert cmd.index("normalize.py") < cmd.index("auto-commit.py")


def test_args_passed_through(env):
    env.write_manifest([
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "args": "acme-allow", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    cmds = _all_commands(_load(env.settings))
    cmd = [c for e, m, c in cmds if e == "PreToolUse"][0]
    assert "confirm-action.py" in cmd
    assert "acme-allow" in cmd


# ── off 모드: mode:"on" 훅 비활성 ──

def test_off_removes_on_hooks_keeps_base(env):
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    a = env.make_adapter()
    a.sync(mode="on")
    assert any(e == "SessionStart" for e, _, _ in _all_commands(_load(env.settings)))

    env.make_adapter().sync(mode="off")
    cmds = _all_commands(_load(env.settings))
    scripts = " ".join(c or "" for _, _, c in cmds)
    assert "session-start.py" not in scripts   # mode:on 훅 비활성
    assert "auto-commit.py" in scripts          # base 훅 유지
