"""작업 E — KB 쓰기 거버넌스 훅 테스트 (test_kb_write_guard.py).

검증 목록:
  - 직접 Edit/Write (memory/ 타겟) → deny (exit 2 + permissionDecision=deny JSON).
  - unlock 플래그 있으면 + 세션ID 일치 → allow (exit 0).
  - TTL 만료 플래그 → deny (잔류 무효).
  - memory/ 밖 경로 Edit → 무영향 (통과, exit 0).
  - .teammode-active 없으면 → no-op (exit 0, 빌드 안전).
  - file_edit 아닌 액션(mcp 등) → 통과.
  - 세션ID 없으면(CLAUDE_SESSION_ID 환경변수 없음) → deny(fail-closed).
  - 빈 플래그(세션ID 없이 touch) → deny(fail-closed — 세션ID 없이 플래그 경로가 맞지 않음).
  - 경로 판별 불가(path 없음) + file_edit → deny(fail-closed, 오탐 방지 통과 폐지).
  - bad stdin → deny(fail-closed).
  - symlink 우회(alias → memory/) → deny(resolve 기반 containment).
  - 다른 레포 플래그로 unlock 시도 → deny(root_hash 격리).
  - 훅 스크립트 단위: unlock_flag_path() XDG/TMPDIR 폴백 확인 + root_hash + session 포함.
  - manifest 에 kb-write-guard.py 등록 확인 + strict: true.
  - manifest 선언 스크립트 파일 실재 확인.

안전 철칙: tmp_path 격리 — 실호스트 무접촉.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import hashlib

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "infra" / "hooks"
KB_GUARD = HOOKS / "kb-write-guard.py"
MANIFEST = HOOKS / "manifest.json"
PY = sys.executable


def _root_hash(root: Path) -> str:
    """팀루트 SHA-1 앞 8자리 — 플래그 경로 계산에 사용."""
    return hashlib.sha1(str(root).encode()).hexdigest()[:8]


def _install_guard(root: Path) -> Path:
    """root 안에 infra/hooks/kb-write-guard.py 를 복사한다.

    복사본을 실행하면 __file__ 이 root 를 가리키므로 _team_root() 가
    root 를 반환한다(P0-3: env 무신뢰 + __file__ 기준 격리 검증).
    """
    hooks_dir = root / "infra" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / "kb-write-guard.py"
    shutil.copy2(str(KB_GUARD), str(dst))
    return dst


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _run_hook(payload: dict, root: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """kb-write-guard.py 를 root 안에 복사해 서브프로세스로 실행.

    훅 파일을 root/infra/hooks/ 에 복사하면 __file__ 이 root 를 가리키므로
    _team_root() 가 TEAMMODE_HOME env 없이도 root 를 반환한다(P0-3 격리).
    TEAMMODE_HOME 은 주입하지 않는다.
    """
    guard = _install_guard(root)
    env = {k: v for k, v in os.environ.items() if k != "TEAMMODE_HOME"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [PY, str(guard)],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env=env,
    )


def _active(root: Path) -> None:
    """팀 루트에 .teammode-active 마커 생성."""
    (root / ".teammode-active").write_text("")


def _memory_payload(root: Path, filename: str = "test.md") -> dict:
    """memory/ 하위 파일을 타겟으로 하는 file_edit PreToolUse 정규 스키마."""
    return {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(root / "memory" / filename)],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }


def _outside_payload(root: Path) -> dict:
    """memory/ 밖 경로를 타겟으로 하는 file_edit PreToolUse 정규 스키마."""
    return {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(root / "infra" / "teammode.py")],
        "tool": {"kind": "builtin", "name": "Edit"},
        "agent": "claude",
        "raw": {},
    }


def _flag_path(tmp_path: Path, root: Path, session_id: str = "test-session") -> Path:
    """테스트용 XDG_STATE_HOME 기반 플래그 경로 (root_hash + session_id 포함)."""
    rh = _root_hash(root)
    return tmp_path / "state" / "teammode" / f"kb-unlock-{rh}-{session_id}"


# ── 기본 차단 / 허용 ──────────────────────────────────────────────────────────

def test_no_marker_is_noop(tmp_path):
    """.teammode-active 없으면 차단 안 함 (빌드 안전)."""
    root = tmp_path / "team"
    root.mkdir()
    proc = _run_hook(_memory_payload(root), root)
    assert proc.returncode == 0


def test_memory_write_without_unlock_is_denied(tmp_path):
    """활성 상태 + unlock 없음 + memory/ 타겟 → deny."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    proc = _run_hook(_memory_payload(root), root)
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_memory_write_with_unlock_is_allowed(tmp_path):
    """unlock 플래그 있고 세션ID 일치 → allow."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    session = "test-session-abc"
    flag = _flag_path(tmp_path, root, session)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": session,
    })
    assert proc.returncode == 0


def test_outside_memory_is_allowed(tmp_path):
    """memory/ 밖 경로는 무영향 (통과)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    proc = _run_hook(_outside_payload(root), root)
    assert proc.returncode == 0


# ── TTL 가드 ──────────────────────────────────────────────────────────────────

def test_expired_flag_is_denied(tmp_path):
    """TTL 만료 플래그 → deny (잔류 무효)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    session = "test-session-ttl"
    flag = _flag_path(tmp_path, root, session)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    # mtime 을 TTL(300s) 훨씬 전으로 설정
    stale_time = time.time() - 10_000
    os.utime(flag, (stale_time, stale_time))
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": session,
    })
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_fresh_flag_is_allowed(tmp_path):
    """방금 만든 플래그(TTL 이내) → allow."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    session = "test-session-fresh"
    flag = _flag_path(tmp_path, root, session)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    # mtime 그대로 (방금 생성)
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": session,
    })
    assert proc.returncode == 0


# ── 세션 ID 매칭 ──────────────────────────────────────────────────────────────

def test_session_id_mismatch_is_denied(tmp_path):
    """다른 세션 ID 로 만든 플래그 → deny (파일명에 session-B 가 없어 플래그 없음과 동일)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    # session-A 로 플래그 생성
    flag_a = _flag_path(tmp_path, root, "session-A")
    flag_a.parent.mkdir(parents=True, exist_ok=True)
    flag_a.write_text("")
    # session-B 로 훅 실행 → 플래그 경로가 다르므로 없음으로 판정 → deny
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": "session-B",
    })
    assert proc.returncode == 2


def test_session_id_match_is_allowed(tmp_path):
    """동일 세션 ID 로 만든 플래그 → allow."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    flag = _flag_path(tmp_path, root, "session-X")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": "session-X",
    })
    assert proc.returncode == 0


def test_empty_flag_content_with_correct_path_is_allowed(tmp_path):
    """파일명에 올바른 session_id 가 포함된 빈 내용 플래그 → allow (내용이 아닌 경로로 격리)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    session = "some-session"
    flag = _flag_path(tmp_path, root, session)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")  # 내용 비어도 파일명이 맞으면 통과
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": session,
    })
    assert proc.returncode == 0


def test_no_env_session_id_is_denied(tmp_path):
    """CLAUDE_SESSION_ID 환경변수 없으면 → deny (fail-closed, P0-2)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    guard = _install_guard(root)
    # 어떤 플래그를 만들어도 세션ID 없으면 deny
    flag = tmp_path / "state" / "teammode" / "kb-unlock-anything"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_SESSION_ID", "TEAMMODE_HOME")}
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    proc = subprocess.run(
        [PY, str(guard)],
        input=json.dumps(_memory_payload(root)),
        capture_output=True, text=True,
        env=env,
    )
    assert proc.returncode == 2


# ── 비파일편집 액션 ──────────────────────────────────────────────────────────

def test_non_file_edit_action_passes(tmp_path):
    """file_edit 아닌 액션(mcp 등)은 통과."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "mcp_tool",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0


def test_no_action_passes(tmp_path):
    """action 필드 없으면 통과 (경로 판별 불가)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "builtin", "name": "Bash"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0


def test_no_path_in_files_is_denied(tmp_path):
    """file_edit 인데 files 도 raw 도 경로 없음 → deny (fail-closed, P1-2)."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── raw 보조 조회 ─────────────────────────────────────────────────────────────

def test_path_from_raw_tool_input_is_checked(tmp_path):
    """files 가 없어도 raw.tool_input.file_path 에서 경로 추출 → memory/ 타겟이면 deny."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [],  # 비어있음 — raw 에서 보조 조회
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {
            "tool_input": {
                "file_path": str(root / "memory" / "secret.md"),
            }
        },
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2


# ── 입력 파싱 에지케이스 ──────────────────────────────────────────────────────

def test_bad_stdin_is_denied(tmp_path):
    """파싱 불가 입력 → deny (fail-closed, P1-2). Traceback 없이 명시적 차단."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    proc = subprocess.run(
        [PY, str(KB_GUARD)], input="not json{{",
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(root)})
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


def test_non_pretooluse_event_passes(tmp_path):
    """PostToolUse 이벤트는 통과."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PostToolUse",
        "action": "file_edit",
        "files": [str(root / "memory" / "foo.md")],
        "agent": "claude",
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0


# ── unlock_flag_path() 단위 테스트 ───────────────────────────────────────────

def _load_guard_mod(name: str = "kb_write_guard"):
    """kb-write-guard.py 를 독립 모듈로 로드 (importlib)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, KB_GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unlock_flag_path_xdg(monkeypatch, tmp_path):
    """XDG_STATE_HOME 있으면 그 하위 teammode/kb-unlock-<root_hash>-<session> 경로."""
    mod = _load_guard_mod("kbg_xdg")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess1")
    monkeypatch.setenv("TEAMMODE_HOME", "/fake/root")
    rh = hashlib.sha1(b"/fake/root").hexdigest()[:8]
    result = mod.unlock_flag_path("/fake/root")
    expected = str(tmp_path / "state" / "teammode" / f"kb-unlock-{rh}-sess1")
    assert result == expected


def test_unlock_flag_path_tmpdir_fallback(monkeypatch, tmp_path):
    """XDG_STATE_HOME 없으면 TMPDIR/teammode-kb-unlock-<USER>-<root_hash>-<session> 폴백."""
    mod = _load_guard_mod("kbg_tmpdir")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess2")
    rh = hashlib.sha1(b"/fake/root2").hexdigest()[:8]
    result = mod.unlock_flag_path("/fake/root2")
    expected = str(tmp_path / "tmp" / f"teammode-kb-unlock-testuser-{rh}-sess2")
    assert result == expected


def test_unlock_flag_path_includes_root_hash_and_session(monkeypatch, tmp_path):
    """플래그 파일명에 root_hash 와 session_id 가 모두 포함된다(레포별·세션별 격리)."""
    mod = _load_guard_mod("kbg_isolation")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "my-session")
    root_a = "/path/to/repo-a"
    root_b = "/path/to/repo-b"
    flag_a = mod.unlock_flag_path(root_a)
    flag_b = mod.unlock_flag_path(root_b)
    # 레포가 다르면 플래그 경로가 다름
    assert flag_a != flag_b
    # session 이 다르면 경로가 다름
    monkeypatch.setenv("CLAUDE_SESSION_ID", "other-session")
    flag_a2 = mod.unlock_flag_path(root_a)
    assert flag_a != flag_a2


# ── manifest 정합 ─────────────────────────────────────────────────────────────

def test_manifest_includes_kb_write_guard():
    """manifest 에 kb-write-guard.py 가 등록돼 있는지."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scripts = {e.get("script") for e in entries}
    assert "kb-write-guard.py" in scripts


def test_manifest_kb_guard_is_enforcement_block_and_strict():
    """kb-write-guard 의 enforcement 가 block 이고 strict 가 true 인지."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    guard = next((e for e in entries if e.get("script") == "kb-write-guard.py"), None)
    assert guard is not None, "kb-write-guard.py 가 manifest 에 없음"
    assert guard.get("enforcement") == "block"
    assert guard.get("event") == "PreToolUse"
    assert guard.get("strict") is True, "kb-write-guard manifest 엔트리에 strict: true 가 없음"


def test_manifest_all_declared_scripts_exist():
    """manifest 가 선언한 모든 script 파일이 hooks/ 에 실재한다."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for e in entries:
        script = e.get("script")
        assert script
        assert (HOOKS / script).is_file(), f"선언된 script 파일 부재: {script}"


# ── deny 메시지 품질 ─────────────────────────────────────────────────────────

def test_deny_message_mentions_tm_manage_knowledge(tmp_path):
    """차단 사유에 tm-manage-knowledge 안내가 포함돼 있는지."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    proc = _run_hook(_memory_payload(root), root)
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "tm-manage-knowledge" in reason


def test_deny_stderr_contains_block_notice(tmp_path):
    """차단 시 stderr 에 [teammode] 블록 알림이 나오는지."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    proc = _run_hook(_memory_payload(root), root)
    assert proc.returncode == 2
    assert "[teammode]" in proc.stderr


# ── P1-1: resolve() containment (symlink 우회 차단) ──────────────────────────

def test_symlink_to_memory_is_denied(tmp_path):
    """alias → memory/ symlink 우회 → deny (resolve() containment, P1-1)."""
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    _active(root)
    # memory/ 를 가리키는 symlink 생성
    alias = root / "alias"
    alias.symlink_to(root / "memory")
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(alias / "secret.md")],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, "symlink 우회가 deny 되어야 한다"
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_outside_symlink_is_allowed(tmp_path):
    """memory/ 밖을 가리키는 symlink → allow (memory/ 하위가 아님)."""
    root = tmp_path / "team"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _active(root)
    alias = root / "alias-other"
    alias.symlink_to(other)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(alias / "file.md")],
        "tool": {"kind": "builtin", "name": "Edit"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0


# ── P1-3: 다른 레포 플래그로 unlock 시도 → deny ──────────────────────────────

def test_different_repo_flag_does_not_unlock(tmp_path):
    """다른 레포(root_hash 다름)의 unlock 플래그는 현재 레포에 효과 없음 → deny."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)

    session = "shared-session"
    # "다른 레포" root_hash 로 플래그 생성
    other_root = tmp_path / "other-repo"
    other_rh = _root_hash(other_root)
    flag_dir = tmp_path / "state" / "teammode"
    flag_dir.mkdir(parents=True, exist_ok=True)
    flag_for_other = flag_dir / f"kb-unlock-{other_rh}-{session}"
    flag_for_other.write_text("")

    # 실제 root 에서 훅 실행 → root_hash 가 달라 플래그를 못 찾음 → deny
    proc = _run_hook(_memory_payload(root), root, env_extra={
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_SESSION_ID": session,
    })
    assert proc.returncode == 2, "다른 레포 플래그로 unlock 되면 안 된다"


# ── P0-3: env 오염 시에도 __file__ 기준 자기 repo를 본다 ──────────────────────

def test_team_root_ignores_env_and_uses_file_location(tmp_path):
    """TEAMMODE_HOME 이 다른 경로를 가리켜도 훅은 __file__ 기준 자기 repo 를 보므로
    오염된 env 로 guard 가 무력화되지 않는다(P0-3).

    방식: tmp_path 에 복사된 훅을 실행하고, 오염 env(TEAMMODE_HOME=다른경로)를 주입한다.
    훅이 env 를 신뢰하면 다른 경로의 .teammode-active 를 보거나 no-op;
    __file__ 을 따르면 tmp_path 의 .teammode-active 를 보고 deny 한다.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    _active(root)  # 자기 repo에 .teammode-active 생성

    # 다른 inactive repo (오염 env)
    other = tmp_path / "other-inactive-repo"
    other.mkdir()
    # other 에는 .teammode-active 없음 → env 신뢰하면 no-op(exit 0) 됨

    # 복사된 훅 실행 (TEAMMODE_HOME=other 오염), CLAUDE_SESSION_ID 없음 → deny
    guard = _install_guard(root)
    env = {k: v for k, v in os.environ.items() if k not in ("TEAMMODE_HOME", "CLAUDE_SESSION_ID")}
    env["TEAMMODE_HOME"] = str(other)  # 의도적 오염
    proc = subprocess.run(
        [PY, str(guard)],
        input=json.dumps(_memory_payload(root)),
        capture_output=True, text=True,
        env=env,
    )
    # env 를 신뢰하면 other 의 .teammode-active 를 보고 no-op(0); __file__ 따르면 deny(2)
    assert proc.returncode == 2, (
        "TEAMMODE_HOME 오염에도 불구하고 __file__ 기준 자기 repo 를 보고 deny 해야 한다(P0-3)"
    )


# ── P1-1: memory/ 자체가 symlink일 때 containment deny ────────────────────────

def test_memory_dir_itself_symlink_is_denied(tmp_path):
    """memory/ 디렉터리 자체가 repo 밖을 가리키는 symlink 일 때도
    그 하위 파일 편집 시도는 deny 되어야 한다(P1-1 memory.resolve()).

    memory_root 를 resolve() 하지 않으면 symlink target 의 하위 파일이
    memory_root 기준 relative_to 에서 실패해 통과 가능성이 생긴다.
    """
    root = tmp_path / "team"
    root.mkdir()
    _active(root)

    # repo 밖 실제 디렉터리
    external_mem = tmp_path / "external-memory"
    external_mem.mkdir()

    # root/memory 자체를 external_mem 으로 symlink
    memory_link = root / "memory"
    memory_link.symlink_to(external_mem)

    # external_mem 하위 파일을 memory_link 경유로 편집 시도
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(memory_link / "secret.md")],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, "memory/ 자체가 symlink여도 그 하위는 deny 되어야 한다(P1-1)"
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── S2-1: 상대경로 fail-closed ────────────────────────────────────────────────

def test_relative_path_to_memory_is_denied(tmp_path):
    """상대경로 file_path 가 memory/ 를 가리키면 deny (S2-1 fail-closed).

    절대경로가 아닌 입력을 CWD 의존 resolve 없이 팀루트 기준으로 정규화한 뒤
    containment 판정을 수행하므로 memory/ 하위이면 차단된다.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    _active(root)
    # 상대경로: "memory/secret.md" (절대경로 아님)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": ["memory/secret.md"],  # 상대경로
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, "상대경로로 memory/ 타겟 시 deny 되어야 한다(S2-1)"
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_relative_path_outside_memory_is_allowed(tmp_path):
    """상대경로 file_path 가 memory/ 밖을 가리키면 allow (S2-1 — 차단 과잉 방지).

    팀루트 기준 resolve 후 containment 체크에서 False → 통과.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "infra").mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": ["infra/some-file.py"],  # 상대경로이지만 memory/ 밖
        "tool": {"kind": "builtin", "name": "Edit"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0, "상대경로라도 memory/ 밖이면 통과해야 한다(S2-1)"


# ── S2-2: memory 내부 symlink 경계 ───────────────────────────────────────────

def test_symlink_inside_memory_pointing_outside_is_denied(tmp_path):
    """memory/ 내부 symlink 가 밖을 가리키는 경우 편집 시도 → deny (S2-2).

    예: memory/link → /tmp/external/file.md
    file_path = memory/link/target.md 처럼 memory/ 하위 경로를 통해 들어오면
    resolve 결과가 memory/ 밖이지만, raw 경로상 memory/ 하위이므로 차단된다.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    _active(root)

    # memory/ 밖 실제 디렉터리
    external = tmp_path / "external-data"
    external.mkdir()

    # memory/ 내부에서 밖을 가리키는 symlink
    inside_link = root / "memory" / "outside-link"
    inside_link.symlink_to(external)

    # memory/ 내부 symlink 경유로 편집 시도
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(inside_link / "leaked.md")],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, (
        "memory/ 내부 symlink 가 밖을 가리켜도 경로상 memory/ 하위이면 deny 되어야 한다(S2-2)"
    )
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_symlink_inside_memory_to_within_memory_is_denied(tmp_path):
    """memory/ 내부 symlink 가 같은 memory/ 안을 가리키는 경우에도 deny (S2-2).

    memory 안에서 안을 가리키는 symlink는 편집 자체가 차단되어야 한다.
    """
    root = tmp_path / "team"
    root.mkdir()
    mem = root / "memory"
    mem.mkdir()
    (mem / "real.md").write_text("data")
    _active(root)

    # memory/ 내부 symlink → 같은 memory/ 안
    inside_link = mem / "alias-real.md"
    inside_link.symlink_to(mem / "real.md")

    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(inside_link)],
        "tool": {"kind": "builtin", "name": "Edit"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, (
        "memory/ 내부 symlink(→memory/ 안)도 deny 되어야 한다(S2-2)"
    )


# ── S2-2(A): normpath ./../ 처리 ──────────────────────────────────────────────

def test_dotdot_escape_from_memory_is_allowed(tmp_path):
    """memory/../infra/file.py 는 normpath 후 memory/ 밖 → allow (S2-2 fix).

    수정 전: p.relative_to(memory_root_raw) 가 `memory/..` 를 해소하지 않아
    false deny(정당한 파일을 잘못 차단)할 수 있었다.
    수정 후: os.path.normpath 로 lexical 정규화 후 containment 판정 → memory/ 밖임이 정확히 판별.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    (root / "infra").mkdir()
    _active(root)
    # memory/../infra/file.py : normpath 하면 infra/file.py → memory/ 밖
    dotdot_path = str(root / "memory" / ".." / "infra" / "file.py")
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [dotdot_path],
        "tool": {"kind": "builtin", "name": "Edit"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0, (
        "memory/../infra/file.py 는 memory/ 밖이므로 allow 되어야 한다(S2-2 normpath fix)"
    )


def test_prefix_trap_outside_memory_is_allowed(tmp_path):
    """memory-notes/ 처럼 memory 로 시작하지만 다른 디렉터리는 allow (prefix 함정 방지).

    normpath 후에도 memory-notes/ 는 memory/ 와 다른 경로이므로 통과해야 한다.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    notes_dir = root / "memory-notes"
    notes_dir.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(notes_dir / "x.md")],
        "tool": {"kind": "builtin", "name": "Edit"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 0, (
        "memory-notes/x.md 는 memory/ 밖이므로 allow 되어야 한다(prefix 함정 방지)"
    )


def test_symlink_inside_memory_pointing_outside_is_still_denied(tmp_path):
    """memory/ 내부 symlink 가 밖을 가리키는 경우 normpath 후에도 deny (S2-2 유지).

    normpath fix 후에도 memory/outside-link/file 경로는 normpath 상 memory/ 하위이므로
    기존 S2-2 (A) 차단이 그대로 작동해야 한다.
    """
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    _active(root)

    external = tmp_path / "external-data"
    external.mkdir()

    inside_link = root / "memory" / "outside-link"
    inside_link.symlink_to(external)

    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(inside_link / "leaked.md")],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, (
        "memory/ 내부 symlink 경유 편집은 normpath 후에도 deny 되어야 한다(S2-2 유지)"
    )
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── malformed 입력 fail-closed ────────────────────────────────────────────────

def test_files_is_integer_list_is_denied(tmp_path):
    """files=[123] (정수 원소) → deny(exit2, fail-closed).

    수정 전: TypeError traceback 이 나며 exit1. 수정 후: 명시 deny + exit2.
    """
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [123],  # 정수 원소 — malformed
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, "files=[123] → exit2 fail-closed 되어야 한다"
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Traceback" not in proc.stderr


def test_files_is_string_is_denied(tmp_path):
    """files='memory/x.md' (리스트 대신 문자열) → deny(exit2, fail-closed).

    수정 전: files[0] == 'm' 로 취급돼 allow(exit0). 수정 후: isinstance 검증으로 deny.
    """
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": str(root / "memory" / "x.md"),  # 문자열 — malformed
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, "files=문자열 → exit2 fail-closed 되어야 한다"
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Traceback" not in proc.stderr


def test_raw_tool_input_is_string_is_denied(tmp_path):
    """raw.tool_input 이 dict 대신 문자열 → deny(exit2, fail-closed).

    수정 전: AttributeError traceback(exit1). 수정 후: 명시 deny + exit2.
    """
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [],  # 비어있음 → raw 보조 조회로 진행
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {
            "tool_input": "not-a-dict",  # 문자열 — malformed
        },
    }
    proc = _run_hook(payload, root)
    assert proc.returncode == 2, "raw.tool_input=문자열 → exit2 fail-closed 되어야 한다"
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Traceback" not in proc.stderr


def test_toplevel_non_dict_json_is_denied(tmp_path):
    """top-level 이 dict 아닌 유효 JSON([], "x", 123, null) → deny(exit2, fail-closed).

    수정 전: data.get() 에서 AttributeError traceback(exit1). 수정 후: isinstance(dict) 검증 deny.
    """
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    for payload in ([], "x", 123, None):
        proc = _run_hook(payload, root)
        assert proc.returncode == 2, f"{payload!r} → exit2 fail-closed 되어야 한다"
        out = json.loads(proc.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Traceback" not in proc.stderr


def test_files_multiple_elements_is_denied(tmp_path):
    """files 다중 요소 → deny(exit2, fail-closed).

    수정 전: files[0] 만 봐서 [밖, memory] 면 memory 경로를 놓치고 allow.
    수정 후: len(files)>1 malformed deny.
    """
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    outside = str(tmp_path / "outside.md")
    inside = str(root / "memory" / "secret.md")
    for files in ([outside, inside], [outside, 123]):
        payload = {
            "event": "PreToolUse",
            "action": "file_edit",
            "files": files,
            "tool": {"kind": "builtin", "name": "Write"},
            "agent": "claude",
            "raw": {},
        }
        proc = _run_hook(payload, root)
        assert proc.returncode == 2, f"files={files!r} → exit2 fail-closed 되어야 한다"
        out = json.loads(proc.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Traceback" not in proc.stderr


def test_deny_message_explains_kb_purpose(tmp_path):
    """차단 메시지에 KB(동사 경유 원칙) 설명이 포함된다."""
    root = tmp_path / "team"
    root.mkdir()
    _active(root)
    proc = _run_hook(_memory_payload(root), root)
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    # KB 설명 키워드 포함 여부
    assert "KB" in reason or "지식 베이스" in reason or "동사" in reason
