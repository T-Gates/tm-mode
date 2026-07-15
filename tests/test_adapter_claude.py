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
            "PostToolUseFailure": "PostToolUseFailure",
            "Stop": "Stop",
            "SubagentStop": "SubagentStop",
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


def test_unsupported_event_warns_english_for_en_locale_team(env, capsys):
    """i18n(적대검수 — B 지적, FIX-REQUIRED 항목2): codex 형제 어댑터와 동형인
    unsupported-event [warn] 이 en 팀에선 영어이고 한글이 섞이지 않는다(이 base
    클래스엔 grouping 메커니즘이 없어 단순 확인)."""
    import json as _json
    import re
    (env.root / "team.config.json").write_text(
        _json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    env.write_manifest([
        {"event": "UnsupportedTestEvent", "match": None,
         "script": "some-hook.py", "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "some-hook.py" in out and "UnsupportedTestEvent" in out
    assert "does not support event" in out
    assert not re.search(r"[가-힣]", out), f"en 팀 출력에 한글 섞임: {out!r}"


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


def test_failure_and_terminal_cleanup_hooks_registered(env):
    env.write_manifest([
        {"event": "PostToolUseFailure", "match": {"action": "file_edit"},
         "script": "edit-lease-cleanup.py", "fallback": "runtime"},
        {"event": "Stop", "script": "edit-lease-cleanup.py"},
        {"event": "SubagentStop", "script": "edit-lease-cleanup.py"},
    ])
    env.make_adapter().sync(mode="on")

    commands = _all_commands(_load(env.settings))
    events = {event for event, _matcher, _command in commands}
    assert {"PostToolUseFailure", "Stop", "SubagentStop"} <= events
    failure_matchers = [
        matcher for event, matcher, _command in commands
        if event == "PostToolUseFailure"]
    assert failure_matchers == ["Write|Edit"]


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
    # 매처 server = resolve_server_alias(정규명) = tm-linear (런타임 도구명과 일치)
    assert "mcp__tm-linear__create_issue" in matchers


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


# ── timeout 단위 — manifest 초 → settings.json 초 (변환 없음) ──
#
# manifest.timeout 은 이제 **초** 단위. Claude Code hook timeout 도 초이므로
# 어댑터가 변환 없이 그대로 settings.json 에 쓴다(드리프트 원천 차단).
#
# P1 회귀: 기존 settings.json 에 다른 timeout 값이 박혀 있어도 sync 가 manifest
# 새 값으로 갱신(upsert)한다. command 매칭만 보고 timeout 변경을 놓치던 버그 방지.

def _timeout_for(settings, event):
    for entry in settings["hooks"].get(event, []):
        for h in entry.get("hooks", []):
            if "timeout" in h:
                return h["timeout"]
    return None


def test_timeout_written_as_seconds(env):
    """manifest timeout=3(초) → settings.json 에 3이 그대로 기록된다."""
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py",
         "timeout": 3, "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    settings = _load(env.settings)
    assert _timeout_for(settings, "SessionStart") == 3


def test_timeout_various_values(env):
    """manifest timeout=2(초) → settings.json 에 2가 그대로 기록된다."""
    env.write_manifest([
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "confirm-action.py", "args": "allow",
         "timeout": 2, "fallback": "runtime"},
    ])
    env.make_adapter().sync(mode="on")
    settings = _load(env.settings)
    assert _timeout_for(settings, "PreToolUse") == 2


def test_no_timeout_when_manifest_omits(env):
    """timeout 미지정이면 settings 에도 timeout 키 없음(종전 동작 보존)."""
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    settings = _load(env.settings)
    assert _timeout_for(settings, "SessionStart") is None


def test_timeout_upserted_when_existing_differs(env):
    """P1 회귀: 기존 settings.json 에 timeout=5000 이 박혀 있을 때
    sync 가 manifest 새 값(3초)으로 갱신한다(command 동일이어도 upsert)."""
    # 먼저 timeout=5000 짜리 훅을 직접 심어둔다 (구버전 설치 시뮬레이션)
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py",
         "timeout": 3, "mode": "on"},
    ])
    a = env.make_adapter()
    a.sync(mode="on")
    # 저장된 settings 에서 timeout 을 5000 으로 강제로 교체 (구버전 잔존 시뮬레이션)
    settings = _load(env.settings)
    for entry in settings["hooks"].get("SessionStart", []):
        for h in entry.get("hooks", []):
            h["timeout"] = 5000
    env.settings.write_text(json.dumps(settings, indent=2) + "\n")

    # 재sync → timeout 이 3 으로 갱신돼야 한다
    env.make_adapter().sync(mode="on")
    settings2 = _load(env.settings)
    assert _timeout_for(settings2, "SessionStart") == 3


def test_timeout_removed_when_manifest_drops(env):
    """manifest 에서 timeout 이 사라지면 기존 settings 의 timeout 키도 제거된다."""
    # 먼저 timeout 있는 훅 등록
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py",
         "timeout": 3, "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    assert _timeout_for(_load(env.settings), "SessionStart") == 3

    # timeout 없는 manifest 로 재sync
    env.write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])
    env.make_adapter().sync(mode="on")
    assert _timeout_for(_load(env.settings), "SessionStart") is None


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
