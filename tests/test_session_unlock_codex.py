"""A2 — 정규 session_id 승격 + Codex 세션 unlock 경로 테스트.

설계(합의):
  (1) normalize 가 훅 stdin top-level 세션 id(`session_id`→`sessionId` 순서 probe,
      비어있지 않은 문자열만)를 **모든 이벤트**의 정규 스키마로 승격한다.
      다른 키(thread_id/rollout_id/conversation_id)는 조사하지 않는다.
  (2) kb-write-guard 는 후보 최대 2개(정규 stdin session_id + env CLAUDE_*_SESSION_ID)를
      ^[A-Za-z0-9._-]{1,128}$ 로 검증(플래그 파일명에 박히므로 traversal 거부, malformed
      는 드롭)·중복 제거 후, **어느 후보든** 정확한 플래그 경로가 존재+TTL 유효하면
      unlock. 후보가 비었을 때만 fail-closed deny. 플래그 내용은 진단용(빈 파일도 유효).
  (3) session-start 가 stdin 세션 id 를 세션별 relay 파일로 영속(스테일 프루닝 포함),
      엔진 동사 `memory unlock begin|end` 가 env 우선 → 최신 relay(mtime) → 에러 순으로
      세션 id 를 해석해 플래그를 생성/제거한다.

안전 철칙: tmp_path 격리 — 실호스트 무접촉.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLAUDE_NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
CODEX_NORMALIZE = REPO / "infra" / "agents" / "codex" / "normalize.py"
CLAUDE_EVENTS = REPO / "infra" / "agents" / "claude" / "events.json"
CODEX_EVENTS = REPO / "infra" / "agents" / "codex" / "events.json"
KB_GUARD = REPO / "infra" / "hooks" / "kb-write-guard.py"
SESSION_START = REPO / "infra" / "hooks" / "session-start.py"
TEAMMODE = REPO / "infra" / "teammode.py"
SKILL_MD = REPO / "infra" / "skills" / "core" / "tm-manage-memory" / "SKILL.md"
PY = sys.executable


def _load_mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _root_hash(root: Path) -> str:
    return hashlib.sha1(str(root).encode()).hexdigest()[:8]


def _flag_path(state_home: Path, root: Path, session_id: str) -> Path:
    return state_home / "teammode" / f"kb-unlock-{_root_hash(root)}-{session_id}"


def _relay_dir(state_home: Path, root: Path) -> Path:
    return state_home / "teammode" / "sessions" / _root_hash(root)


def _active(root: Path) -> None:
    (root / ".teammode-active").write_text("")


def _memory_payload(root: Path, session_id: str | None = None, agent: str = "codex") -> dict:
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(root / "memory" / "note.md")],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": agent,
        "raw": {},
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def _install_hook(root: Path, src: Path) -> Path:
    hooks_dir = root / "infra" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / src.name
    shutil.copy2(str(src), str(dst))
    return dst


def _clean_env(state_home: Path, extra: dict | None = None) -> dict:
    """CLAUDE_* 세션 env 와 TEAMMODE_HOME 을 제거한 격리 env."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "TEAMMODE_HOME")}
    env["XDG_STATE_HOME"] = str(state_home)
    if extra:
        env.update(extra)
    return env


def _run_guard(payload: dict, root: Path, env: dict) -> subprocess.CompletedProcess:
    guard = _install_hook(root, KB_GUARD)
    return subprocess.run(
        [PY, str(guard)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


# ── (1) normalize: 정규 session_id 승격 ──────────────────────────────────────

def test_normalize_lifts_session_id_snake_case():
    """raw top-level `session_id`(Claude 원어)가 모든 이벤트에서 정규 필드로 승격된다."""
    mod = _load_mod(CLAUDE_NORMALIZE, "norm_sid_snake")
    events = json.loads(CLAUDE_EVENTS.read_text(encoding="utf-8"))
    for event in ("PreToolUse", "SessionStart", "UserPromptSubmit"):
        raw = {"hook_event_name": event, "session_id": "sess-abc123"}
        out = mod.normalize(raw, events)
        assert out.get("session_id") == "sess-abc123", f"{event}: session_id 승격 실패"


def test_normalize_lifts_sessionId_camel_case():
    """raw top-level `sessionId`(Codex 변형 대비)도 승격된다 — probe 순서 2번째."""
    mod = _load_mod(CLAUDE_NORMALIZE, "norm_sid_camel")
    events = json.loads(CLAUDE_EVENTS.read_text(encoding="utf-8"))
    raw = {"hook_event_name": "PreToolUse", "sessionId": "sess-camel-1"}
    out = mod.normalize(raw, events)
    assert out.get("session_id") == "sess-camel-1"


def test_normalize_session_id_probe_order_prefers_snake_case():
    """두 키가 모두 있으면 `session_id` 가 우선한다."""
    mod = _load_mod(CLAUDE_NORMALIZE, "norm_sid_order")
    events = json.loads(CLAUDE_EVENTS.read_text(encoding="utf-8"))
    raw = {"hook_event_name": "PreToolUse",
           "session_id": "snake-wins", "sessionId": "camel-loses"}
    out = mod.normalize(raw, events)
    assert out.get("session_id") == "snake-wins"


def test_normalize_omits_session_id_when_absent_or_invalid():
    """비문자열·빈 문자열이면 정규 필드를 생략한다(다른 키 probe 금지)."""
    mod = _load_mod(CLAUDE_NORMALIZE, "norm_sid_omit")
    events = json.loads(CLAUDE_EVENTS.read_text(encoding="utf-8"))
    for raw_extra in ({}, {"session_id": ""}, {"session_id": 123},
                      {"session_id": None}, {"thread_id": "t-1"},
                      {"conversation_id": "c-1"}, {"rollout_id": "r-1"}):
        raw = {"hook_event_name": "PreToolUse", **raw_extra}
        out = mod.normalize(raw, events)
        assert "session_id" not in out, f"{raw_extra}: session_id 가 생기면 안 된다"


def test_codex_normalize_inherits_session_id_lift():
    """codex normalize 는 claude normalize 코어를 그대로 상속한다 — 동일 승격."""
    mod = _load_mod(CODEX_NORMALIZE, "codex_norm_sid")
    events = json.loads(CODEX_EVENTS.read_text(encoding="utf-8"))
    raw = {"hook_event_name": "PreToolUse", "session_id": "codex-sess-9",
           "tool_name": "apply_patch",
           "tool_input": {"command": "*** Begin Patch\n*** Update File: a.md\n*** End Patch"}}
    out = mod._base.normalize(raw, events)
    assert out.get("session_id") == "codex-sess-9"


# ── (2) guard: stdin session_id 후보 + env 후보 union ────────────────────────

def test_guard_codex_stdin_session_unlock_roundtrip(tmp_path):
    """CODEX 형태 stdin(payload 에 session_id, CLAUDE env 없음) → 플래그 있으면 unlock."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    sid = "codex-sess-1"
    flag = _flag_path(state, root, sid)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    proc = _run_guard(_memory_payload(root, session_id=sid), root, _clean_env(state))
    assert proc.returncode == 0, (
        f"stdin session_id 로 unlock 되어야 한다: stdout={proc.stdout!r} stderr={proc.stderr!r}")


def test_guard_stdin_session_mismatch_denied(tmp_path):
    """stdin session_id 가 플래그의 id 와 다르면 deny(정확한 경로만, glob 금지)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    flag = _flag_path(state, root, "other-session")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    proc = _run_guard(_memory_payload(root, session_id="my-session"), root, _clean_env(state))
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_guard_malformed_stdin_session_id_is_dropped_then_denied(tmp_path):
    """traversal 문자 등 malformed stdin id 는 드롭 — 후보가 비면 fail-closed deny."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    for bad in ("../evil", "a/b", "x" * 129, "sess id", ""):
        proc = _run_guard(_memory_payload(root, session_id=bad), root, _clean_env(state))
        assert proc.returncode == 2, f"malformed id {bad!r} → deny 여야 한다"
        assert "Traceback" not in proc.stderr


def test_guard_env_flag_still_unlocks_when_stdin_id_differs(tmp_path):
    """union 후보: stdin id 플래그가 없어도 env id 플래그가 유효하면 unlock(Claude 무회귀)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    env_sid = "env-session-7"
    flag = _flag_path(state, root, env_sid)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    proc = _run_guard(
        _memory_payload(root, session_id="stdin-session-7"), root,
        _clean_env(state, {"CLAUDE_SESSION_ID": env_sid}))
    assert proc.returncode == 0


def test_guard_stdin_session_flag_respects_ttl(tmp_path):
    """stdin id 후보도 TTL 계약을 따른다 — 만료 플래그는 deny."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    sid = "codex-sess-ttl"
    flag = _flag_path(state, root, sid)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    stale = time.time() - 10_000
    os.utime(flag, (stale, stale))
    proc = _run_guard(_memory_payload(root, session_id=sid), root, _clean_env(state))
    assert proc.returncode == 2


def test_guard_json_flag_content_still_validates(tmp_path):
    """플래그 내용은 진단용 — JSON 내용이 있어도(새 포맷) 경로+mtime 계약으로 통과."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    sid = "codex-sess-json"
    flag = _flag_path(state, root, sid)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(json.dumps({"schema": "kb-unlock/1", "session_id": sid}))
    proc = _run_guard(_memory_payload(root, session_id=sid), root, _clean_env(state))
    assert proc.returncode == 0


# ── (3a) session-start: 세션 id relay 영속 ───────────────────────────────────

def _run_session_start(root: Path, payload: dict, env: dict) -> subprocess.CompletedProcess:
    hook = _install_hook(root, SESSION_START)
    _install_hook(root, KB_GUARD)  # relay 규약 단일 소스(guard) 시블링 필요
    # session-start 의 active 체크는 TEAMMODE_HOME 기반(런타임 훅 규약) — relay 자체는
    # guard 와 동일하게 __file__ 기준 root_hash 를 쓴다.
    env = {**env, "TEAMMODE_HOME": str(root)}
    return subprocess.run(
        [PY, str(hook)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def test_session_start_persists_session_relay(tmp_path):
    """SessionStart 정규 stdin 의 session_id 가 relay 파일로 영속된다."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    payload = {"event": "SessionStart", "agent": "codex",
               "session_id": "sess-relay-1", "raw": {}}
    proc = _run_session_start(root, payload, _clean_env(state))
    assert proc.returncode == 0
    relay = _relay_dir(state, root) / "sess-relay-1"
    assert relay.is_file(), f"relay 파일이 있어야 한다: {relay}"


def test_session_start_prunes_stale_relay_entries(tmp_path):
    """새 relay 기록 시 스테일(오래된 mtime) 항목은 기회적으로 프루닝된다."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    relay_dir = _relay_dir(state, root)
    relay_dir.mkdir(parents=True, exist_ok=True)
    stale_file = relay_dir / "sess-ancient"
    stale_file.write_text("")
    ancient = time.time() - 30 * 24 * 3600
    os.utime(stale_file, (ancient, ancient))
    payload = {"event": "SessionStart", "agent": "codex",
               "session_id": "sess-relay-2", "raw": {}}
    proc = _run_session_start(root, payload, _clean_env(state))
    assert proc.returncode == 0
    assert not stale_file.exists(), "스테일 relay 항목은 프루닝되어야 한다"
    assert (relay_dir / "sess-relay-2").is_file()


def test_session_start_without_session_id_still_exits_zero(tmp_path):
    """세션 id 없는 SessionStart 도 세션을 막지 않는다(advisory) — relay 만 생략."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    payload = {"event": "SessionStart", "agent": "claude", "raw": {}}
    proc = _run_session_start(root, payload, _clean_env(state))
    assert proc.returncode == 0
    relay_dir = _relay_dir(state, root)
    assert not relay_dir.exists() or not list(relay_dir.iterdir())


# ── (3b) 엔진 동사: memory unlock begin|end ──────────────────────────────────

def _run_engine_unlock(root: Path, sub: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(TEAMMODE), "memory", "unlock", sub, "--root", str(root)],
        capture_output=True, text=True, env=env,
    )


def test_engine_unlock_begin_end_with_env_session(tmp_path):
    """env 세션 id 로 begin → 플래그 생성(진단 JSON·0600), end → 제거."""
    root = tmp_path / "team"
    root.mkdir()
    state = tmp_path / "state"
    sid = "engine-env-sess"
    env = _clean_env(state, {"CLAUDE_SESSION_ID": sid})

    proc = _run_engine_unlock(root, "begin", env)
    assert proc.returncode == 0, f"begin 실패: {proc.stderr!r}"
    flag = _flag_path(state, Path(str(root.resolve())), sid)
    assert flag.is_file(), f"플래그가 생성되어야 한다: {flag}"
    content = json.loads(flag.read_text(encoding="utf-8"))
    assert content["session_id"] == sid
    assert content["source"] == "env"
    assert content["root_hash"] == _root_hash(Path(str(root.resolve())))
    if os.name == "posix":
        assert (flag.stat().st_mode & 0o777) == 0o600

    proc = _run_engine_unlock(root, "end", env)
    assert proc.returncode == 0, f"end 실패: {proc.stderr!r}"
    assert not flag.exists(), "end 후 플래그가 제거되어야 한다"


def test_engine_unlock_begin_falls_back_to_most_recent_relay(tmp_path):
    """env 부재 시 최신(mtime) relay 파일의 id 를 쓴다 — Codex 세션 경로."""
    root = tmp_path / "team"
    root.mkdir()
    state = tmp_path / "state"
    resolved = Path(str(root.resolve()))
    relay_dir = _relay_dir(state, resolved)
    relay_dir.mkdir(parents=True, exist_ok=True)
    old = relay_dir / "sess-older"
    old.write_text("")
    past = time.time() - 600
    os.utime(old, (past, past))
    newer = relay_dir / "sess-newer"
    newer.write_text("")

    proc = _run_engine_unlock(root, "begin", _clean_env(state))
    assert proc.returncode == 0, f"relay 폴백 begin 실패: {proc.stderr!r}"
    flag = _flag_path(state, resolved, "sess-newer")
    assert flag.is_file(), "최신 relay id 로 플래그가 생성되어야 한다"
    content = json.loads(flag.read_text(encoding="utf-8"))
    assert content["source"] == "relay"
    assert not _flag_path(state, resolved, "sess-older").exists()


def test_engine_unlock_begin_errors_without_any_session_source(tmp_path):
    """env 도 relay 도 없으면 명시 에러(비-0 exit) — 조용한 nosession 플래그 금지."""
    root = tmp_path / "team"
    root.mkdir()
    state = tmp_path / "state"
    proc = _run_engine_unlock(root, "begin", _clean_env(state))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_engine_unlock_requires_begin_or_end(tmp_path):
    """서브액션이 begin|end 가 아니면 usage 에러."""
    root = tmp_path / "team"
    root.mkdir()
    state = tmp_path / "state"
    proc = subprocess.run(
        [PY, str(TEAMMODE), "memory", "unlock", "--root", str(root)],
        capture_output=True, text=True, env=_clean_env(state),
    )
    assert proc.returncode != 0


def test_engine_unlock_roundtrip_opens_guard_window(tmp_path):
    """end-to-end: 엔진 begin 이 만든 플래그로 guard 가 통과, end 후 다시 deny."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    state = tmp_path / "state"
    sid = "roundtrip-sess"
    env = _clean_env(state, {"CLAUDE_SESSION_ID": sid})

    assert _run_engine_unlock(root, "begin", env).returncode == 0
    proc = _run_guard(_memory_payload(root, session_id=sid), root, _clean_env(state))
    assert proc.returncode == 0, "begin 후 stdin session_id 로 guard 를 통과해야 한다"

    assert _run_engine_unlock(root, "end", env).returncode == 0
    proc = _run_guard(_memory_payload(root, session_id=sid), root, _clean_env(state))
    assert proc.returncode == 2, "end 후에는 다시 deny 되어야 한다"


# ── (4) SKILL.md: 4-0/4-2 스니펫 → 엔진 동사 ─────────────────────────────────

def test_skill_md_uses_engine_unlock_verb():
    """tm-manage-memory SKILL 4-0/4-2 가 엔진 동사 호출로 대체됐다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "memory unlock begin" in text
    assert "memory unlock end" in text
    # 손파싱 플래그 스니펫(수기 flag 경로 계산)이 제거됐는지
    assert "flag.write_text" not in text
    assert "kb-unlock-{suffix}" not in text
