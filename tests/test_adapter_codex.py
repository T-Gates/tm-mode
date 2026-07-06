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

    def make_adapter(member=None, member_fallback=None):
        # member: issue #26 — Codex hook command 에 TEAMMODE_MEMBER prefix 를 박을 멤버명.
        # 기본 None(기존 테스트 하위호환). 지정 시 build_command 가 prefix 를 붙인다.
        # member_fallback: issue #41 R2 — 엔진(teammode.py)의 자동 해석 체인이 넘기는
        # 폴백 멤버명. self.member·기존 config prefix 둘 다 없을 때만 쓰인다.
        kwargs = dict(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(config),
            python="python3",
            team_root=str(root),
            member=member,
        )
        if member_fallback is not None:
            kwargs["member_fallback"] = member_fallback
        return Adapter(**kwargs)

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
    """member 지정 → 'env TEAMMODE_MEMBER=<member> ' prefix + 기존 base command 포함.

    issue #9b 이후 member 유무와 무관하게 TEAMMODE_HOME 핀이 항상 붙으므로,
    'TEAMMODE_HOME= 이후 꼬리(실행 커맨드)'가 동일한지로 base 보존을 검증한다.
    """
    base = env.make_adapter(member=None).build_command(_remind_entry())
    cmd = env.make_adapter(member="leejhy").build_command(_remind_entry())
    assert cmd.startswith("env TEAMMODE_MEMBER=leejhy "), cmd
    assert "TEAMMODE_HOME=" in base and "TEAMMODE_HOME=" in cmd
    assert (cmd.split("TEAMMODE_HOME=", 1)[1]
            == base.split("TEAMMODE_HOME=", 1)[1]), (
        f"base command 꼬리가 그대로 와야 함: {cmd!r} / base={base!r}")
    # base 경유 검증: normalize.py + 스크립트가 그대로 들어있다
    assert "normalize.py" in cmd
    assert "session-log-remind.py" in cmd


def test_build_command_no_member_no_prefix(env):
    """member=None → TEAMMODE_MEMBER 없이(하위호환). TEAMMODE_HOME 핀은 유지(#9b)."""
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


# ── 10. issue #26 (codex review): tm on/off resync 가 prefix 를 떨구지 않는다 ──
#
# build_command 의 TEAMMODE_MEMBER prefix 는 install(--member) 경로에서만 self.member 로
# 들어온다. 그런데 teammode.cmd_on/off(`tm on/off`)는 codex 어댑터를 member 없이 만들어
# sync 한다 → managed hook 블록이 prefix 없는 command 로 재기록되어 회귀. self-healing:
# self.member 가 None 이면 현재 config.toml 의 기존 prefix 를 파싱해 재사용한다.

def test_resync_without_member_preserves_existing_prefix(env):
    """install(member) 후 member 없이 resync(=tm on/off)해도 기존 prefix 가 보존된다."""
    env.write_manifest([
        # base 엔트리(no mode) — on·off 양쪽에 남는다(auto-commit).
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
        # on 전용 엔트리 — on 에만 등록(session-log-remind).
        {"event": "UserPromptSubmit", "script": "session-log-remind.py", "mode": "on"},
    ])
    # 1) install 경로: member 박아 최초 sync → 두 hook 다 prefix
    env.make_adapter(member="leejhy").sync(mode="on")
    t1 = env.config.read_text()
    assert t1.count("env TEAMMODE_MEMBER=leejhy") >= 2, t1

    # 2) tm on resync(member 없음) → self-healing 으로 prefix 보존
    env.make_adapter(member=None).sync(mode="on")
    t2 = env.config.read_text()
    assert "env TEAMMODE_MEMBER=leejhy" in t2, f"on resync 후 prefix 유실: {t2!r}"
    assert "session-log-remind.py" in t2

    # 3) tm off resync(member 없음) → base hook(auto-commit) prefix 보존
    env.make_adapter(member=None).sync(mode="off")
    t3 = env.config.read_text()
    assert "env TEAMMODE_MEMBER=leejhy" in t3, f"off resync 후 prefix 유실: {t3!r}"


def test_resync_without_member_no_existing_prefix_stays_clean(env):
    """member 없고 기존 prefix 도 없으면 prefix 를 만들지 않는다(자가치유가 없는 값 생성 금지)."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member=None).sync(mode="on")
    text = env.config.read_text()
    assert "TEAMMODE_MEMBER" not in text, text


def test_resync_member_arg_overrides_and_updates_prefix(env):
    """member 인자가 있으면(install/`tm on --member`) 기존 prefix 보다 우선해 갱신한다."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member="leejhy").sync(mode="on")
    assert "env TEAMMODE_MEMBER=leejhy" in env.config.read_text()
    # 다른 member 로 재sync → 새 member 로 갱신(기존 prefix 파싱보다 self.member 우선)
    env.make_adapter(member="eunsu").sync(mode="on")
    t = env.config.read_text()
    assert "env TEAMMODE_MEMBER=eunsu" in t, t
    assert "TEAMMODE_MEMBER=leejhy" not in t, f"옛 member 가 남음: {t!r}"


def test_existing_member_prefix_ignores_outside_managed_block(env):
    """managed 블록 밖의 TEAMMODE_MEMBER 텍스트는 self-healing 이 줍지 않는다(오염 차단)."""
    # 사용자가 자기 hook 에 TEAMMODE_MEMBER 를 쓴 것처럼 블록 밖에 둔다.
    env.config.write_text(
        'model = "o1"\n'
        '[[hooks.SessionStart.hooks]]\n'
        "command = 'env TEAMMODE_MEMBER=evil /bin/echo hi'\n")
    a = env.make_adapter(member=None)
    assert a._existing_member_prefix() is None


# ── 11. issue #41 R1: legacy/어긋난 마커 블록 자기치유 — resync 는 항상 정확히 1블록 ──
#
# 실측 사고(2026-07-03, T-Gates): 과거 버전이 `# teammode-hooks-start` … `# teammode-mcp-end`
# 로 마커가 어긋난 블록을 남겼고, 정상 쌍만 찾는 _write_block 이 인식 실패 → append →
# 훅 2중 등록(구 블록은 prefix 없음 + stale timeout 3s). 요구: 어떤 잔재(어긋난 쌍·고아
# start/end·중복 블록·과거 네이밍)가 있어도 resync 후 결과는 '정상 마커의 블록 정확히 1개'.

_USER_TOML = (
    'model = "o1"\n'
    "\n"
    "[[hooks.SessionStart]]\n"
    "\n"
    "[[hooks.SessionStart.hooks]]\n"
    'type = "command"\n'
    "command = '/usr/bin/echo user-hook'\n"
)


def _legacy_block(env, start="# teammode-hooks-start", end="# teammode-mcp-end"):
    """옛 버전이 남긴 모양의 teammode 블록(prefix 없음 + stale timeout 3) 텍스트."""
    norm = f"{env.root}/infra/agents/codex/normalize.py"
    return (
        f"{start}\n\n"
        "[[hooks.UserPromptSubmit]]\n\n"
        "[[hooks.UserPromptSubmit.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {norm} session-log-remind.py'\n"
        "timeout = 3\n\n"
        f"{end}\n"
    )


def _remind_manifest(env):
    env.write_manifest([
        {"event": "UserPromptSubmit", "script": "session-log-remind.py", "mode": "on"},
    ])


def _assert_single_block(text):
    assert text.count("# teammode-hooks-start") == 1, text
    assert text.count("# teammode-hooks-end") == 1, text
    # 마커 순서도 정상(start 가 end 앞)
    assert text.index("# teammode-hooks-start") < text.index("# teammode-hooks-end")


def _assert_valid_toml(text):
    try:
        import tomllib  # py3.11+
    except ModuleNotFoundError:
        return  # py3.9/3.10 — 파서 없음, 구조 단정만으로 충분
    tomllib.loads(text)


def test_resync_heals_mismatched_marker_legacy_block(env):
    """사고 원형: hooks-start↔mcp-end 어긋난 쌍 → append 아닌 제거 후 1블록 재기록."""
    _remind_manifest(env)
    env.config.write_text(_USER_TOML + "\n" + _legacy_block(env))
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "# teammode-mcp-end" not in text          # 어긋난 잔재 소멸
    assert "timeout = 3" not in text                  # stale timeout 소멸
    assert text.count("session-log-remind.py") == 1   # 훅 2중 등록 없음
    assert "env TEAMMODE_MEMBER=leejhy" in text       # prefix 정상
    assert "command = '/usr/bin/echo user-hook'" in text  # 사용자 훅 보존
    _assert_valid_toml(text)


def test_resync_heals_two_stale_blocks(env):
    """append 사고의 잔해(정상 쌍 블록 2개) → resync 후 정확히 1블록."""
    _remind_manifest(env)
    stale = _legacy_block(env, end="# teammode-hooks-end")
    env.config.write_text(_USER_TOML + "\n" + stale + "\n" + stale)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert text.count("session-log-remind.py") == 1
    assert "env TEAMMODE_MEMBER=leejhy" in text
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


def test_resync_heals_orphan_end_marker(env):
    """짝 없는 end 마커 → 마커 라인만 제거, 사용자 내용 보존."""
    _remind_manifest(env)
    env.config.write_text(_USER_TOML + "\n# teammode-hooks-end\n")
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


def test_resync_heals_orphan_start_with_owned_tables(env):
    """고아 start + 소유 훅 테이블(end 없음) → 마커+소유 테이블 연속 구간까지 제거.

    보수 규칙: 직후에 이어지는 'normalize.py 를 가리키는 [[hooks.*]] 테이블 연속 구간'
    까지만 지우고, 그 다음(다른 섹션·일반 텍스트)은 남긴다."""
    _remind_manifest(env)
    norm = f"{env.root}/infra/agents/codex/normalize.py"
    orphan = (
        "# teammode-hooks-start\n\n"
        "[[hooks.UserPromptSubmit]]\n\n"
        "[[hooks.UserPromptSubmit.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {norm} session-log-remind.py'\n"
        "timeout = 3\n"
    )
    user_after = '\n[user_section]\nkey = "keep-me"\n'
    env.config.write_text(_USER_TOML + "\n" + orphan + user_after)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "timeout = 3" not in text                  # 옛 소유 테이블 소멸
    assert text.count("session-log-remind.py") == 1
    assert "command = '/usr/bin/echo user-hook'" in text  # 앞쪽 사용자 훅 보존
    assert 'key = "keep-me"' in text                  # 뒤쪽 사용자 섹션 보존
    _assert_valid_toml(text)


def test_resync_orphan_start_never_eats_user_tables(env):
    """고아 start 직후가 '소유 아닌' hooks 테이블이면 마커 라인만 제거(사용자 훅 무접촉)."""
    _remind_manifest(env)
    orphan = (
        "# teammode-hooks-start\n\n"
        "[[hooks.PostToolUse]]\n\n"
        "[[hooks.PostToolUse.hooks]]\n"
        'type = "command"\n'
        "command = '/usr/bin/echo mine'\n"
    )
    env.config.write_text(_USER_TOML + "\n" + orphan)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "command = '/usr/bin/echo mine'" in text   # 사용자 훅 보존
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


def test_resync_removes_unknown_legacy_marker_pair(env):
    """과거 네이밍 변형(teammode-legacy-start↔end 정상 쌍)도 잔재로 보고 제거."""
    _remind_manifest(env)
    legacy = _legacy_block(env, start="# teammode-legacy-start",
                           end="# teammode-legacy-end")
    env.config.write_text(_USER_TOML + "\n" + legacy)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "teammode-legacy-start" not in text
    assert "teammode-legacy-end" not in text
    assert text.count("session-log-remind.py") == 1
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


def test_resync_healing_preserves_valid_mcp_block(env):
    """정상 mcp-start↔mcp-end 쌍(=_write_mcp_block 소유)은 훅 sync 치유가 안 지운다."""
    _remind_manifest(env)
    mcp = (
        "# teammode-mcp-start\n\n"
        "[mcp_servers.tm-linear]\n"
        "_teammode_managed = true\n\n"
        "# teammode-mcp-end\n"
    )
    env.config.write_text(mcp + "\n" + _legacy_block(env))
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "[mcp_servers.tm-linear]" in text
    assert text.count("# teammode-mcp-start") == 1
    assert text.count("# teammode-mcp-end") == 1
    _assert_valid_toml(text)


def test_resync_healing_idempotent(env):
    """치유 후 재sync 는 무변경(멱등) — 정상 상태에서 치유가 아무것도 안 건드린다."""
    _remind_manifest(env)
    env.config.write_text(_USER_TOML + "\n" + _legacy_block(env))
    env.make_adapter(member="leejhy").sync(mode="on")
    t1 = env.config.read_text()
    env.make_adapter(member="leejhy").sync(mode="on")
    t2 = env.config.read_text()
    assert t1 == t2


def test_uninstall_removes_legacy_remnants_too(env):
    """uninstall 도 잔재(어긋난 쌍)를 함께 걷어낸다 — 재설치 전 수동 수술 불요."""
    _remind_manifest(env)
    env.config.write_text(_USER_TOML + "\n" + _legacy_block(env))
    env.make_adapter(member=None).uninstall()
    text = env.config.read_text()
    assert "teammode-" not in text
    assert "session-log-remind.py" not in text
    assert "command = '/usr/bin/echo user-hook'" in text


# ── 12. issue #41 R2(어댑터측): member_fallback — 엔진 자동 해석 체인의 폴백 주입 ──
#
# 우선순위: 명시 self.member > 기존 config prefix(자가치유) > member_fallback(엔진 체인).

def test_member_fallback_used_when_no_existing_prefix(env):
    """member 없음 + 기존 prefix 없음 → member_fallback 이 prefix 로 기록된다."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member=None, member_fallback="envguy").sync(mode="on")
    assert "env TEAMMODE_MEMBER=envguy" in env.config.read_text()


def test_existing_prefix_wins_over_member_fallback(env):
    """기존 config prefix(자가치유)가 member_fallback 보다 우선(최고 충실도, per-agent)."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member="leejhy").sync(mode="on")
    env.make_adapter(member=None, member_fallback="envguy").sync(mode="on")
    text = env.config.read_text()
    assert "env TEAMMODE_MEMBER=leejhy" in text
    assert "envguy" not in text


def test_explicit_member_wins_over_fallback(env):
    """명시 --member 는 절대 오버라이드 — fallback 이 있어도 명시값이 이긴다."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member="cli", member_fallback="envguy").sync(mode="on")
    text = env.config.read_text()
    assert "env TEAMMODE_MEMBER=cli" in text
    assert "envguy" not in text


def test_unsafe_member_fallback_rejected(env):
    """형식 위반 fallback 은 prefix 미기록(build_command 검증 정규식 공유, fail-safe)."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member=None, member_fallback="a; rm -rf /").sync(mode="on")
    assert "TEAMMODE_MEMBER" not in env.config.read_text()


def test_off_resync_no_prefix_stays_no_prefix(env):
    """off(preserve-only): member·기존 prefix 둘 다 없으면 off sync 도 prefix 미발명."""
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    env.make_adapter(member=None).sync(mode="off")
    assert "TEAMMODE_MEMBER" not in env.config.read_text()


# ── 13. codex-review P2-1: kept hooks 쌍 안에 중첩된 정상 MCP 쌍 보존 ──
#
# 손상 레이아웃(hooks-start … mcp-start … mcp-end … hooks-end): purge 가 hooks 쌍을
# 앵커로 keep 하면 _write_block 의 통짜 교체가 안쪽 MCP 블록까지 없앤다. 요구:
# 이런 hooks 쌍은 앵커로 keep 하지 않고 마커+소유 테이블만 걷어낸 뒤 새 블록 append.

def test_resync_nested_mcp_pair_inside_hooks_span_preserved(env):
    env.write_manifest([
        {"event": "UserPromptSubmit", "script": "session-log-remind.py", "mode": "on"},
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    norm = f"{env.root}/infra/agents/codex/normalize.py"
    corrupt = (
        "# teammode-hooks-start\n\n"
        "[[hooks.UserPromptSubmit]]\n\n"
        "[[hooks.UserPromptSubmit.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {norm} session-log-remind.py'\n"
        "timeout = 3\n\n"
        "# teammode-mcp-start\n\n"
        "[mcp_servers.tm-linear]\n"
        "_teammode_managed = true\n\n"
        "# teammode-mcp-end\n\n"
        "[[hooks.PostToolUse]]\n\n"
        "[[hooks.PostToolUse.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {norm} auto-commit.py'\n"
        "timeout = 3\n\n"
        "# teammode-hooks-end\n"
    )
    env.config.write_text(_USER_TOML + "\n" + corrupt)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)                        # hooks 블록 정확히 1개
    assert "[mcp_servers.tm-linear]" in text          # 안쪽 MCP 등록 보존
    assert text.count("# teammode-mcp-start") == 1
    assert text.count("# teammode-mcp-end") == 1
    assert "timeout = 3" not in text                  # 옛 소유 테이블(양쪽) 소멸
    assert text.count("session-log-remind.py") == 1   # 훅 2중 등록 없음
    assert text.count("auto-commit.py") == 1
    assert "env TEAMMODE_MEMBER=leejhy" in text
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


# ── 14. codex-review P2-2: 멀티라인 TOML 문자열 안의 마커-모양 라인 무시 ──
#
# _MARKER_LINE 은 라인 앵커라 인라인 문자열은 안전하지만, purge 는 raw 라인을
# 스캔하므로 '''…''' / \"\"\"…\"\"\" 멀티라인 문자열 **안의** 마커-모양 라인이
# 진짜 마커로 오인돼 사용자 설정이 지워질 수 있다. 요구: 스캔 중 멀티라인 문자열
# 상태를 추적해 안쪽 마커-모양 라인은 무시(사용자 설정 byte-identical 보존).

_ML_USER_TOML = (
    'model = "o1"\n'
    "banner = '''one-liner ''' \n"          # 같은 줄 open+close — 상태 오염 없음
    "\n"
    "[[hooks.SessionStart]]\n"
    "\n"
    "[[hooks.SessionStart.hooks]]\n"
    'type = "command"\n'
    "command = '''\n"
    "echo hi\n"
    "# teammode-hooks-start\n"              # 문자열 안 — 마커 아님
    "echo bye\n"
    "'''\n"
    "\n"
    "[[hooks.PostToolUse]]\n"
    "\n"
    "[[hooks.PostToolUse.hooks]]\n"
    'type = "command"\n'
    'command = """\n'
    "echo start\n"
    "# teammode-mcp-end\n"                  # 문자열 안 — 마커 아님
    "echo done\n"
    '"""\n'
)


def test_purge_ignores_marker_shaped_lines_inside_multiline_strings(env):
    """멀티라인 문자열 안의 가짜 마커 쌍(hooks-start↔mcp-end 모양) → purge 무접촉."""
    _remind_manifest(env)
    a = env.make_adapter(member=None)
    assert a._purge_legacy_markers(_ML_USER_TOML) == _ML_USER_TOML


def test_purge_real_marker_after_closed_multiline_string_still_healed(env):
    """닫힌 멀티라인 문자열 **뒤의** 진짜 잔재는 여전히 치유된다(과잉 억제 금지)."""
    _remind_manifest(env)
    text = _ML_USER_TOML + "\n" + _legacy_block(env)
    a = env.make_adapter(member=None)
    purged = a._purge_legacy_markers(text)
    assert "# teammode-hooks-start\necho bye" in purged   # 문자열 안 가짜 마커 보존
    assert "session-log-remind.py" not in purged           # 진짜 잔재 블록은 제거
    assert "timeout = 3" not in purged


# ── 15. codex-review P2-3: 고아 start 삭제는 managed command SHAPE 증명 필수 ──
#
# is_owned 의 느슨한 꼬리(agents/codex/normalize.py 부분문자열)로는 사용자가 직접
# normalize.py 를 경유시킨 훅도 '소유'로 오인된다. 고아 start 삭제 경로만은
# `[env KEY=VAL…] <python> <normalize.py> <manifest 의 알려진 스크립트>` 형태를
# 증명해야 지운다. 증명 실패 → 잔재로 남김(다음 sync 재시도, 무해).

def test_orphan_start_user_table_with_unknown_script_survives(env):
    """normalize.py 경유라도 manifest 에 없는 스크립트면 사용자 훅 — 삭제 금지."""
    _remind_manifest(env)
    norm = f"{env.root}/infra/agents/codex/normalize.py"
    orphan = (
        "# teammode-hooks-start\n\n"
        "[[hooks.UserPromptSubmit]]\n\n"
        "[[hooks.UserPromptSubmit.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {norm} my-custom-thing.py'\n"
    )
    env.config.write_text(_USER_TOML + "\n" + orphan)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "my-custom-thing.py" in text               # 사용자 테이블 보존
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


def test_orphan_start_env_prefixed_known_script_removed(env):
    """managed SHAPE(env prefix + python + normalize.py + manifest 스크립트)는 제거."""
    _remind_manifest(env)
    norm = f"{env.root}/infra/agents/codex/normalize.py"
    orphan = (
        "# teammode-hooks-start\n\n"
        "[[hooks.UserPromptSubmit]]\n\n"
        "[[hooks.UserPromptSubmit.hooks]]\n"
        'type = "command"\n'
        f"command = 'env TEAMMODE_MEMBER=leejhy TEAMMODE_HOME=/x "
        f"python3 {norm} session-log-remind.py'\n"
        "timeout = 3\n"
    )
    env.config.write_text(_USER_TOML + "\n" + orphan)
    env.make_adapter(member="leejhy").sync(mode="on")
    text = env.config.read_text()
    _assert_single_block(text)
    assert "timeout = 3" not in text                  # 옛 소유 테이블 소멸
    assert text.count("session-log-remind.py") == 1
    assert "command = '/usr/bin/echo user-hook'" in text
    _assert_valid_toml(text)


# ── 16. issue #46 A3: kept prefix ≠ 환경 폴백 mismatch [warn] — 경고만, 동작 무변경 ──
#
# resync 가 자가치유로 기존 TEAMMODE_MEMBER prefix 를 유지했는데 폴백 체인(env/settings)
# 이 **다른** 검증 통과 후보를 내놓으면, 조용한 불일치가 생긴다(사용자는 환경값이 반영
# 됐다고 믿기 쉽다). [warn] 1줄로 교정 커맨드(tm on --member <fb>)를 안내한다.
# 경고 억제 조건: 값 일치 / 폴백 미해석(또는 무효) / 명시 --member 지정.

def _autocommit_manifest(env):
    env.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])


def test_kept_prefix_env_mismatch_warns_once(env, capsys):
    """기존 prefix(alice) 유지 + 폴백(bob) 상이 → [warn] 1줄(교정 안내) + 동작 무변경."""
    _autocommit_manifest(env)
    env.make_adapter(member="alice").sync(mode="on")
    capsys.readouterr()  # install 출력 비움
    env.make_adapter(member=None, member_fallback="bob").sync(mode="on")
    out = capsys.readouterr().out
    assert out.count("[warn]") == 1, out
    assert "prefix(alice)" in out, out
    assert "환경(bob)" in out, out
    assert "tm on --member bob" in out, out
    # 동작 무변경: prefix 는 여전히 자가치유 값(alice) — bob 이 기록되면 안 된다.
    text = env.config.read_text()
    assert "env TEAMMODE_MEMBER=alice" in text
    assert "TEAMMODE_MEMBER=bob" not in text


def test_kept_prefix_env_match_is_silent(env, capsys):
    """기존 prefix 와 폴백이 같으면 경고 없음(불일치 아님)."""
    _autocommit_manifest(env)
    env.make_adapter(member="alice").sync(mode="on")
    capsys.readouterr()
    env.make_adapter(member=None, member_fallback="alice").sync(mode="on")
    assert "[warn]" not in capsys.readouterr().out


def test_explicit_member_suppresses_mismatch_warning(env, capsys):
    """명시 --member 는 절대 오버라이드 — 사용자가 이미 결정, mismatch 경고 불필요."""
    _autocommit_manifest(env)
    env.make_adapter(member="alice").sync(mode="on")
    capsys.readouterr()
    env.make_adapter(member="cli", member_fallback="bob").sync(mode="on")
    assert "[warn]" not in capsys.readouterr().out


def test_no_fallback_resolved_no_mismatch_warning(env, capsys):
    """폴백 미해석(None) → 비교 대상 없음, 경고 없음(기존 자가치유 조용히 유지)."""
    _autocommit_manifest(env)
    env.make_adapter(member="alice").sync(mode="on")
    capsys.readouterr()
    env.make_adapter(member=None).sync(mode="on")
    assert "[warn]" not in capsys.readouterr().out


def test_invalid_fallback_no_mismatch_warning(env, capsys):
    """형식 위반 폴백은 '검증된 후보'가 아니다 → 경고 없음(검증 정규식 공유)."""
    _autocommit_manifest(env)
    env.make_adapter(member="alice").sync(mode="on")
    capsys.readouterr()
    env.make_adapter(member=None, member_fallback="b b; rm").sync(mode="on")
    assert "[warn]" not in capsys.readouterr().out


def test_sync_notices_hooks_json_coexistence(tmp_path):
    """[2026-07-06 공존 계약] hooks.json 병용 감지 시 [info] 1줄 — 무해 경고 설명."""
    Adapter = _load_codex_adapter()
    import json as _json
    agent_dir = tmp_path / "agent"; agent_dir.mkdir()
    import shutil as _sh
    _sh.copy(REPO / "infra" / "agents" / "codex" / "events.json", agent_dir / "events.json")
    hooks_dir = tmp_path / "hooks"; hooks_dir.mkdir()
    (hooks_dir / "manifest.json").write_text(_json.dumps(
        [{"event": "SessionStart", "script": "session-start.py", "mode": "on"}]))
    (hooks_dir / "session-start.py").write_text("# stub")
    cfg = tmp_path / "config.toml"
    (tmp_path / "hooks.json").write_text("{}")  # 타 도구 소유 가정
    a = Adapter(agent_dir=str(agent_dir), manifest_path=str(hooks_dir / "manifest.json"),
                settings_path=str(cfg), python="python3", team_root=str(tmp_path), member=None)
    out = a.sync(mode="on")
    assert any("hooks.json coexists" in c for c in out), out
