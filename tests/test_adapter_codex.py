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

    def make_adapter(member=None):
        # member: issue #26 — Codex hook command 에 TEAMMODE_MEMBER prefix 를 박을 멤버명.
        # 기본 None(기존 테스트 하위호환). 지정 시 build_command 가 prefix 를 붙인다.
        return Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(config),
            python="python3",
            team_root=str(root),
            member=member,
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


# ── 9. issue #26: build_command 가 TEAMMODE_MEMBER env prefix 를 박는다 ──
#
# Codex 의 command hook 에는 env 필드가 없고 command 를 셸로 실행하므로, 멀티멤버 식별을
# 위해 build_command 가 `env TEAMMODE_MEMBER=<member> <base command>` 로 prefix 를 붙인다.
# 값은 ascii 영숫자로 시작하는 '-_' 단일 토큰만 허용(셸 인젝션 차단), 위반 시 prefix 생략.

def _remind_entry():
    return {"script": "session-log-remind.py"}


def test_build_command_member_prefix(env):
    """member 지정 → 'env TEAMMODE_MEMBER=<member> ' prefix + 기존 base command 포함."""
    base = env.make_adapter(member=None).build_command(_remind_entry())
    cmd = env.make_adapter(member="leejhy").build_command(_remind_entry())
    assert cmd.startswith("env TEAMMODE_MEMBER=leejhy "), cmd
    assert cmd.endswith(base), f"base command 가 prefix 뒤에 그대로 와야 함: {cmd!r} / base={base!r}"
    assert base in cmd
    # base 경유 검증: normalize.py + 스크립트가 그대로 들어있다
    assert "normalize.py" in cmd
    assert "session-log-remind.py" in cmd


def test_build_command_no_member_no_prefix(env):
    """member=None → prefix 없이 base command 그대로(하위호환)."""
    cmd = env.make_adapter(member=None).build_command(_remind_entry())
    assert not cmd.startswith("env TEAMMODE_MEMBER"), cmd
    assert "TEAMMODE_MEMBER" not in cmd
    assert "normalize.py" in cmd
    assert "session-log-remind.py" in cmd


@pytest.mark.parametrize("bad", [
    "a b",            # 공백
    "x; rm -rf /",    # 셸 메타문자 + 공백
    "../evil",        # path traversal (/ 와 .)
    "",               # 빈 문자열
    "   ",            # 공백만
    "-leading",       # 선두 dash (식별자 시작 규칙 위반)
    "_leading",       # 선두 underscore (영숫자 시작 규칙 위반)
    "ko한글",          # 비-ascii
    "a&b",            # 셸 메타문자
    "$(whoami)",      # 명령치환 시도
    "a\nb",           # 개행
])
def test_build_command_rejects_unsafe_member(env, bad):
    """형식 위반 member → prefix 없이 base command 그대로(검증 정규식이 거부, fail-safe)."""
    base = env.make_adapter(member=None).build_command(_remind_entry())
    cmd = env.make_adapter(member=bad).build_command(_remind_entry())
    assert cmd == base, f"unsafe member {bad!r} 에 prefix 가 붙음: {cmd!r}"
    assert "TEAMMODE_MEMBER" not in cmd


@pytest.mark.parametrize("ok", [
    "leejhy", "eunsu", "a", "A1", "user-name", "user_name", "u1-2_3", "X",
])
def test_build_command_accepts_valid_member(env, ok):
    """정상 토큰(영숫자 시작 + 영숫자/-/_)은 prefix 가 붙는다."""
    cmd = env.make_adapter(member=ok).build_command(_remind_entry())
    assert cmd.startswith(f"env TEAMMODE_MEMBER={ok} "), cmd


def test_is_owned_holds_with_member_prefix(env):
    """prefix 가 붙어도 is_owned True — normalize substring 매칭이 prefix 에 안 깨진다."""
    a = env.make_adapter(member="leejhy")
    cmd = a.build_command(_remind_entry())
    assert cmd.startswith("env TEAMMODE_MEMBER=leejhy ")
    assert a.is_owned(cmd) is True, f"prefix 붙은 command 가 소유 판정 실패: {cmd!r}"
    # 음성 대조: teammode 소유가 아닌 임의 command 는 False
    assert a.is_owned("env TEAMMODE_MEMBER=leejhy /usr/bin/echo hi") is False
