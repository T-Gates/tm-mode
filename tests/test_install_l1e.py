"""L1-E — session-start.py 훅 테스트 (spec/04 §4⑦·spec02 §3.1, B1·M3).

SessionStart 훅이 팀 활성 시 맥락(멤버별 최근 세션로그)을 additionalContext 로 주입.
manifest 에 등록됐으나 부재했던 파일 — L1 진짜 payoff. normalize 경유 안 깨짐 확인.
호스트 무접촉: TEAMMODE_HOME 을 tmp 로 주입, 전부 tmp_path.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
HOOK = REPO / "infra" / "hooks" / "session-start.py"
NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"


def _hook_env(team_root: Path, extra_env=None):
    """훅 subprocess 격리 env — conftest(_isolate_pull_state)가 os.environ 에 박은
    격리 XDG_STATE_HOME 을 명시 전달한다(최소 env 라 자동상속이 안 됨).

    누락 시 session-start 의 auto-pull 이 expanduser("~") 폴백으로 **실**
    ~/.local/state/teammode/last-pull 에 쓴다(CI 가드 발화). test_install_golden._env 동형.
    """
    env = {"TEAMMODE_HOME": str(team_root), "PATH": "/usr/bin:/bin"}
    if "XDG_STATE_HOME" in os.environ:
        env["XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    if extra_env:
        env.update(extra_env)
    return env


def _run_hook(payload: dict, team_root: Path, extra_env=None):
    return subprocess.run(
        [PY, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, env=_hook_env(team_root, extra_env))


def _seed_team(team_root: Path, *, active=True, member="alice",
               summary="첫 작업 요약", date="2026-06-14"):
    sess = team_root / "memory" / "team" / "sessions" / member
    sess.mkdir(parents=True)
    (sess / f"{date}.md").write_text(
        f"---\nauthor: {member}\ndate: {date}\nsummary: {summary}\n---\n본문\n")
    (team_root / "memory" / "INDEX.md").write_text("# INDEX\n팀 인덱스\n")
    if active:
        (team_root / ".teammode-active").write_text("")


# ─────────────────────────── 활성/비활성 ───────────────────────────

def test_injects_context_when_active(tmp_path):
    _seed_team(tmp_path, summary="배포 파이프라인 작업")
    proc = _run_hook({"event": "SessionStart", "agent": "claude"}, tmp_path)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    ctx = hso["additionalContext"]
    assert "alice" in ctx
    assert "배포 파이프라인 작업" in ctx


def test_no_inject_when_inactive(tmp_path):
    """.teammode-active 없으면 무동작(빈 stdout, exit 0)."""
    _seed_team(tmp_path, active=False)
    proc = _run_hook({"event": "SessionStart", "agent": "claude"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_ignores_non_sessionstart_event(tmp_path):
    _seed_team(tmp_path)
    proc = _run_hook({"event": "UserPromptSubmit", "agent": "claude"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_empty_team_still_valid_structure(tmp_path):
    """I1: 로그 0이어도 활성이면 유효 구조 안내 주입(빈 상태도 읽어냄)."""
    (tmp_path / "memory" / "team" / "sessions").mkdir(parents=True)
    (tmp_path / ".teammode-active").write_text("")
    proc = _run_hook({"event": "SessionStart", "agent": "claude"}, tmp_path)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "세션로그 없음" in ctx or "아직 세션로그" in ctx


def test_malformed_stdin_does_not_block(tmp_path):
    """깨진 stdin → exit 0(advisory, 세션 안 막음)."""
    _seed_team(tmp_path)
    proc = subprocess.run(
        [PY, str(HOOK)], input="NOT JSON{{", capture_output=True, text=True,
        env=_hook_env(tmp_path))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_multiple_members_collected(tmp_path):
    _seed_team(tmp_path, member="alice", summary="A 작업")
    bob = tmp_path / "memory" / "team" / "sessions" / "bob"
    bob.mkdir(parents=True)
    (bob / "2026-06-13.md").write_text(
        "---\nauthor: bob\ndate: 2026-06-13\nsummary: B 작업\n---\n")
    proc = _run_hook({"event": "SessionStart", "agent": "claude"}, tmp_path)
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "alice" in ctx and "A 작업" in ctx
    assert "bob" in ctx and "B 작업" in ctx


# ─────────────────────────── normalize 경유 ───────────────────────────

def test_through_normalize_does_not_break(tmp_path):
    """normalize.py 가 Claude 원어 SessionStart 를 정규화→훅 호출, 주입 전파."""
    _seed_team(tmp_path, summary="노멀라이즈 경유 작업")
    raw = {"hook_event_name": "SessionStart", "session_id": "abc"}
    proc = subprocess.run(
        [PY, str(NORMALIZE), "session-start.py"],
        input=json.dumps(raw), capture_output=True, text=True,
        env=_hook_env(tmp_path))
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "노멀라이즈 경유 작업" in out["hookSpecificOutput"]["additionalContext"]


def test_manifest_registers_session_start():
    """manifest 에 등록된 session-start.py 파일이 이제 실재한다(부재 갭 해소)."""
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text())
    entry = next(e for e in manifest if e.get("script") == "session-start.py")
    assert entry["event"] == "SessionStart"
    assert HOOK.is_file()
