"""Codex 훅 trust 검사 (#D1) — read-only 검사 + 안내 1줄.

배경: Codex 는 훅을 config.toml [hooks.state] 의 per-key trusted_hash 로 게이트하고,
untrusted/modified 훅은 headless(exec)에서 **무경고 스킵**된다. sync 직후 read-only 로
방금 쓴 teammode 훅들의 기대 해시를 재계산해 비교하고, 문제가 있으면 warnings 채널로
1줄만 안내한다(trusted_hash 직접 기록 금지 — 사용자 동의 게이트 우회).

검증:
  1. 해시 재계산 golden — 실측 라이브 벡터(55d6811a…/df52c6cc… 등) 재현
  2. 전부 trusted → 침묵
  3. 키 부재(hooks.state 는 있으나 teammode 키 없음) → 경고 1줄
  4. 해시 불일치(modified) → 경고 1줄
  5. 버전 게이트 — codex 부재/타 버전 → 침묵 skip
  6. hooks.state/config 파싱 불가 → 침묵 (절대 sync 실패 없음)
  7. per-event 테이블 순번 — teammode 블록 앞 사용자 훅이 있으면 순번이 밀린다
모든 테스트 tmp_path — 실 ~/.codex 무접촉. 버전 프로브는 명시 주입(호스트 무관 결정성).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# ── 실측 라이브 벡터 (2026-07-03, codex-cli 0.142.5 가 실제로 기록한 trusted_hash) ──
LIVE_CMD_KB_GUARD = (
    "env TEAMMODE_MEMBER=leejhy /opt/homebrew/opt/python@3.14/bin/python3.14 "
    "/Users/junhyeong/Documents/greengorae/tgates-team/infra/agents/codex/"
    "normalize.py kb-write-guard.py")
LIVE_HASH_KB_GUARD = (
    "sha256:55d6811a8a3007bd268621f8b9076303c4d1354d4e028be5e2bde689614a0601")
LIVE_CMD_LOG_REMIND = (
    "env TEAMMODE_MEMBER=leejhy /opt/homebrew/opt/python@3.14/bin/python3.14 "
    "/Users/junhyeong/Documents/greengorae/tgates-team/infra/agents/codex/"
    "normalize.py session-log-remind.py")
LIVE_HASH_LOG_REMIND = (
    "sha256:df52c6cceabb17e8d6572fcf23b282bdf508ef04c42bbc353c7f9b4211d9e5e4")
LIVE_STATUS = "[T-Gates] 팀모드 ON"
# statusMessage **부재** 훅의 라이브 벡터(codex@openai-codex 플러그인 Stop 훅) —
# '부재 필드는 payload 에서 키 생략' 직렬화의 실측 근거(null 은 불일치 확인).
LIVE_CMD_STOP = 'node "${CLAUDE_PLUGIN_ROOT}/scripts/stop-review-gate-hook.mjs"'
LIVE_HASH_STOP = (
    "sha256:3dfaa7e4f022570b260b8a8cf4ac7585c986175256b887a4723fac4f358a482f")


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


# 검사 대상을 정확히 통제하는 작은 manifest — SessionStart(매처 없음) +
# PreToolUse(file_edit 매처) 2개.
def _manifest():
    return [
        {"event": "SessionStart", "script": "session-start.py", "timeout": 30,
         "enforcement": "advisory"},
        {"event": "PreToolUse", "match": {"action": "file_edit"},
         "script": "kb-write-guard.py", "timeout": 2, "fallback": "runtime",
         "enforcement": "block"},
    ]


@pytest.fixture
def env(tmp_path, monkeypatch):
    # conftest 가 결정성 위해 끄는 kill-switch 를 여기서는 되살린다(검사 대상 기능).
    monkeypatch.delenv("TEAMMODE_CODEX_TRUST_CHECK", raising=False)
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "codex"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_events()))
    (agent_dir / "normalize.py").write_text("# stub\n")
    (hooks_dir / "manifest.json").write_text(json.dumps(_manifest()))
    config = tmp_path / "config.toml"

    Adapter = _load_codex_adapter()

    def make_adapter(version="0.142.5"):
        a = Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(config),
            python="python3",
            team_root=str(root),
            member="alice",
        )
        if version is not None:
            # 버전 프로브 명시 주입 — 호스트 codex 설치 여부와 무관한 결정성.
            a._codex_version = lambda: version
        return a

    class Env:
        pass

    e = Env()
    e.config = config
    e.make_adapter = make_adapter
    e.root = root
    return e


# ── 독립 재구현(테스트 전용) — 구현과 순환 참조 없이 스펙 자체를 고정 ──

def _spec_hash(label, command, status_message, timeout, matcher=None):
    # 부재 필드는 payload 키 생략(실측: Stop 라이브 벡터가 생략으로만 재현).
    hook = {"async": False, "command": command, "type": "command"}
    if status_message is not None:
        hook["statusMessage"] = status_message
    if timeout is not None:
        hook["timeout"] = timeout
    payload = {"event_name": label, "hooks": [hook]}
    if matcher is not None:
        payload["matcher"] = matcher
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False)
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _teammode_hooks_from_config(config: Path):
    """sync 가 쓴 teammode 블록에서 (event, matcher, command, timeout, status) 추출."""
    text = config.read_text(encoding="utf-8")
    m = re.search(r"# teammode-hooks-start(.*?)# teammode-hooks-end", text, re.S)
    assert m, "teammode 블록이 없다"
    out = []
    cur = None
    for ln in m.group(1).split("\n"):
        em = re.match(r"^\[\[hooks\.([A-Za-z0-9_-]+)\]\]$", ln.strip())
        if em:
            cur = {"event": em.group(1), "matcher": None, "command": None,
                   "timeout": None, "status": None}
            out.append(cur)
            continue
        if cur is None:
            continue
        kv = re.match(r"^(matcher|command|timeout|statusMessage)\s*=\s*(.+)$",
                      ln.strip())
        if not kv:
            continue
        key, raw = kv.group(1), kv.group(2)
        if key == "timeout":
            cur["timeout"] = int(raw)
        else:
            val = raw[1:-1]
            if raw[0] == '"':
                val = val.replace('\\"', '"').replace("\\\\", "\\")
            cur[{"statusMessage": "status"}.get(key, key)] = val
    return out


def _snake(event):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def _write_trusted_state(env, index_offset=None):
    """config 의 teammode 훅들에 대응하는 **올바른** hooks.state 를 덧붙인다."""
    hooks = _teammode_hooks_from_config(env.config)
    per_event = {}
    lines = ["", "[hooks.state]", ""]
    for h in hooks:
        label = _snake(h["event"])
        i = per_event.get(label, 0)
        per_event[label] = i + 1
        if index_offset:
            i += index_offset.get(label, 0)
        digest = _spec_hash(label, h["command"], h["status"], h["timeout"],
                            matcher=h["matcher"])
        key = f"{env.config}:{label}:{i}:0"
        lines.append(f'[hooks.state."{key}"]')
        lines.append(f'trusted_hash = "{digest}"')
        lines.append("")
    env.config.write_text(env.config.read_text(encoding="utf-8")
                          + "\n".join(lines), encoding="utf-8")


# ── 1. golden — 실측 라이브 벡터 재현 ──

def test_expected_hash_matches_live_vector_with_matcher(env):
    a = env.make_adapter()
    got = a.expected_trust_hash(
        "pre_tool_use",
        [{"async": False, "command": LIVE_CMD_KB_GUARD,
          "statusMessage": LIVE_STATUS, "timeout": 2, "type": "command"}],
        matcher="apply_patch")
    assert got == LIVE_HASH_KB_GUARD


def test_expected_hash_matches_live_vector_without_matcher(env):
    a = env.make_adapter()
    got = a.expected_trust_hash(
        "user_prompt_submit",
        [{"async": False, "command": LIVE_CMD_LOG_REMIND,
          "statusMessage": LIVE_STATUS, "timeout": 2, "type": "command"}])
    assert got == LIVE_HASH_LOG_REMIND


def test_expected_hash_matches_live_vector_without_status_message(env):
    # 부재 필드(statusMessage) 키 생략 직렬화의 golden — Stop 라이브 벡터.
    a = env.make_adapter()
    got = a.expected_trust_hash(
        "stop",
        [{"async": False, "command": LIVE_CMD_STOP, "timeout": 900,
          "type": "command"}])
    assert got == LIVE_HASH_STOP


# ── 2. 전부 trusted → 침묵 ──

def test_all_trusted_silent(env, capsys):
    a = env.make_adapter()
    a.sync(mode="on")
    _write_trusted_state(env)
    assert env.make_adapter().check_hook_trust() is None
    # sync 경유도 무경고(멱등 resync)
    capsys.readouterr()
    env.make_adapter().sync(mode="on")
    assert "[warn]" not in capsys.readouterr().out


# ── 3/4. untrusted(키 부재)·modified(불일치) → 경고 1줄 ──

def test_missing_keys_warn_one_line(env, capsys):
    a = env.make_adapter()
    capsys.readouterr()
    a.sync(mode="on")  # hooks.state 없음 → 전부 untrusted
    out = capsys.readouterr().out
    warns = [l for l in out.splitlines() if "trust되지 않음" in l]
    assert len(warns) == 1
    assert warns[0].startswith("[warn] codex 훅 2개가 아직 trust되지 않음")
    assert "Trust" in warns[0]


def test_state_section_present_but_key_missing_warns(env):
    a = env.make_adapter()
    a.sync(mode="on")
    env.config.write_text(
        env.config.read_text(encoding="utf-8")
        + '\n[hooks.state]\n\n[hooks.state."/other/config.toml:stop:0:0"]\n'
          'trusted_hash = "sha256:deadbeef"\n', encoding="utf-8")
    warn = env.make_adapter().check_hook_trust()
    assert warn is not None and "2개" in warn


def test_modified_hash_warns(env):
    a = env.make_adapter()
    a.sync(mode="on")
    _write_trusted_state(env)
    # 하나만 위조 — modified 1개
    text = env.config.read_text(encoding="utf-8")
    hooks = _teammode_hooks_from_config(env.config)
    good = _spec_hash("session_start", hooks[0]["command"], hooks[0]["status"],
                      hooks[0]["timeout"], matcher=hooks[0]["matcher"])
    env.config.write_text(text.replace(good, "sha256:" + "0" * 64),
                          encoding="utf-8")
    warn = env.make_adapter().check_hook_trust()
    assert warn is not None and "1개" in warn


# ── 5. 버전 게이트 ──

def test_other_version_silent(env):
    a = env.make_adapter(version="0.143.0")
    a.sync(mode="on")  # untrusted 상태
    assert a.check_hook_trust() is None


def test_version_probe_failure_silent(env, tmp_path):
    a = env.make_adapter(version=None)  # 실 프로브 사용
    a.CODEX_BIN = str(tmp_path / "definitely-missing-codex-bin")
    a.sync(mode="on")
    assert a.check_hook_trust() is None


def test_real_version_probe_parses_or_none(env):
    # 실 프로브는 'X.Y.Z' 또는 None 만 낸다(예외 금지) — 호스트 설치 여부 무관 통과.
    a = env.make_adapter(version=None)
    v = a._codex_version()
    assert v is None or re.fullmatch(r"\d+\.\d+\.\d+", v)


# ── 6. 파싱 불가·비정상 입력 → 침묵, 절대 sync 실패 없음 ──

def test_garbage_config_never_raises(env):
    a = env.make_adapter()
    a.sync(mode="on")
    env.config.write_text(env.config.read_text(encoding="utf-8")
                          + "\n[hooks.state\n%%% not toml '''\n",
                          encoding="utf-8")
    # 경고가 나오든 None 이든 — 예외만 없으면 된다(경고여도 안내는 무해).
    env.make_adapter().check_hook_trust()


def test_unreadable_config_silent(env, monkeypatch):
    a = env.make_adapter()

    def boom():
        raise OSError("unreadable")

    a._read_config = boom
    assert a.check_hook_trust() is None


def test_no_teammode_block_silent(env):
    env.config.write_text("[hooks.state]\n", encoding="utf-8")
    assert env.make_adapter().check_hook_trust() is None


def test_env_killswitch_silent(env, monkeypatch):
    a = env.make_adapter()
    a.sync(mode="on")  # untrusted
    monkeypatch.setenv("TEAMMODE_CODEX_TRUST_CHECK", "0")
    assert a.check_hook_trust() is None


# ── 7. per-event 테이블 순번 — 블록 앞 사용자 훅이 있으면 순번이 밀린다 ──

def test_user_hook_before_block_shifts_index(env):
    # 사용자 SessionStart 훅을 먼저 심는다 → teammode SessionStart 는 :1:0
    env.config.write_text(
        "[[hooks.SessionStart]]\n\n[[hooks.SessionStart.hooks]]\n"
        'type = "command"\ncommand = "echo user-hook"\ntimeout = 5\n\n',
        encoding="utf-8")
    a = env.make_adapter()
    a.sync(mode="on")
    # 올바른 순번(session_start=1)로 trust 기록 → 침묵
    _write_trusted_state(env, index_offset={"session_start": 1})
    assert env.make_adapter().check_hook_trust() is None


def test_wrong_index_detected_as_untrusted(env):
    env.config.write_text(
        "[[hooks.SessionStart]]\n\n[[hooks.SessionStart.hooks]]\n"
        'type = "command"\ncommand = "echo user-hook"\ntimeout = 5\n\n',
        encoding="utf-8")
    a = env.make_adapter()
    a.sync(mode="on")
    # 옛 순번(session_start=0)로 기록된 stale trust → codex 도 스킵할 상태 → 경고
    _write_trusted_state(env)
    warn = env.make_adapter().check_hook_trust()
    assert warn is not None and "1개" in warn


# ── off/base 모드(statusMessage 부재) — 부재 필드는 payload 키 생략으로 검증 ──

def test_base_mode_untrusted_warns(env, capsys):
    a = env.make_adapter()
    capsys.readouterr()
    a.sync(mode=None)  # statusMessage 없는 base 블록 — 그래도 검증 대상
    out = capsys.readouterr().out
    assert "trust되지 않음" in out


def test_base_mode_trusted_silent(env):
    a = env.make_adapter()
    a.sync(mode=None)
    _write_trusted_state(env)  # statusMessage=None → payload 키 생략으로 계산
    assert env.make_adapter().check_hook_trust() is None
