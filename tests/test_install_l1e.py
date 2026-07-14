"""L1-E — session-start.py 훅 테스트 (spec/04 §4⑦·spec02 §3.1, B1·M3).

SessionStart 훅이 팀 활성 시 맥락(멤버별 최근 세션로그)을 additionalContext 로 주입.
manifest 에 등록됐으나 부재했던 파일 — L1 진짜 payoff. normalize 경유 안 깨짐 확인.
호스트 무접촉: TEAMMODE_HOME 을 tmp 로 주입, 전부 tmp_path.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
HOOK = REPO / "infra" / "hooks" / "session-start.py"
NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
CODEX_NORMALIZE = REPO / "infra" / "agents" / "codex" / "normalize.py"


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
    """I1: 로그 0이어도 활성이면 유효 구조 안내 주입(빈 상태도 읽어냄).

    PR-i1: 한국어 안내 문구를 단정하므로 ko 팀 픽스처(locale=ko_KR)로 고정
    (config 없음 → en 폴백 계약).
    """
    (tmp_path / "memory" / "team" / "sessions").mkdir(parents=True)
    (tmp_path / ".teammode-active").write_text("")
    (tmp_path / "team.config.json").write_text(
        json.dumps({"team": {"locale": "ko_KR"}}), encoding="utf-8")
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


def _codex_transcript(path: Path, turn_id: str,
                      timestamp="2026-07-14T14:35:11Z") -> None:
    path.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"id": "root-session"}}),
        json.dumps({
            "timestamp": timestamp,
            "type": "turn_context",
            "payload": {"turn_id": turn_id},
        }),
        "",
    ]), encoding="utf-8")


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("session_start_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_codex_start(team: Path, transcript: Path, state: Path,
                     *, source="resume", session_id="root-session"):
    return subprocess.run(
        [PY, str(CODEX_NORMALIZE), "session-start.py"],
        input=json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": session_id,
            "transcript_path": str(transcript),
            "source": source,
        }),
        capture_output=True, text=True,
        env=_hook_env(team, {"XDG_STATE_HOME": str(state)}))


def test_codex_duplicate_session_start_same_turn_emits_context_once(tmp_path):
    """같은 root turn을 재구성한 SessionStart 3회 중 첫 1회만 맥락을 주입한다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "root.jsonl"
    _seed_team(team, summary="한 번만 보일 맥락")
    _codex_transcript(transcript, "turn-1")

    runs = [_run_codex_start(team, transcript, state) for _ in range(3)]

    assert all(run.returncode == 0 for run in runs)
    assert sum(bool(run.stdout.strip()) for run in runs) == 1
    assert "한 번만 보일 맥락" in next(run.stdout for run in runs if run.stdout.strip())


def test_concurrent_codex_duplicate_session_start_has_one_winner(tmp_path):
    """별도 프로세스가 동시에 같은 resume를 claim해도 정확히 하나만 실행한다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "root.jsonl"
    _seed_team(team)
    _codex_transcript(transcript, "turn-race")
    raw = json.dumps({
        "hook_event_name": "SessionStart",
        "session_id": "root-session",
        "transcript_path": str(transcript),
        "source": "resume",
    })
    procs = [subprocess.Popen(
        [PY, str(CODEX_NORMALIZE), "session-start.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_hook_env(team, {"XDG_STATE_HOME": str(state)}))
        for _ in range(3)]

    results = [proc.communicate(raw, timeout=10) for proc in procs]

    assert all(proc.returncode == 0 for proc in procs)
    assert sum(bool(stdout.strip()) for stdout, _stderr in results) == 1


def test_codex_same_session_new_turn_and_compact_are_not_suppressed(tmp_path):
    """중복만 거르고 다음 turn 및 같은 turn의 별도 compact generation은 살린다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "root.jsonl"
    _seed_team(team)
    _codex_transcript(transcript, "turn-1")
    first = _run_codex_start(team, transcript, state)
    _codex_transcript(transcript, "turn-2")
    next_turn = _run_codex_start(team, transcript, state)
    compact = _run_codex_start(team, transcript, state, source="compact")

    assert first.stdout.strip()
    assert next_turn.stdout.strip()
    assert compact.stdout.strip()


def test_codex_same_turn_id_new_context_record_is_new_generation(tmp_path):
    """같은 turn_id여도 새 turn_context row면 독립 reopen으로 즉시 실행한다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "root.jsonl"
    _seed_team(team)
    _codex_transcript(transcript, "turn-reused", "2026-07-14T14:35:11Z")
    first = _run_codex_start(team, transcript, state)
    _codex_transcript(transcript, "turn-reused", "2026-07-14T14:36:11Z")

    reopened = _run_codex_start(team, transcript, state)

    assert first.stdout.strip()
    assert reopened.stdout.strip()


def test_codex_same_turn_resume_is_allowed_again_after_window(tmp_path):
    """동일 turn도 5분이 지나면 장기 resume로 보고 다시 실행한다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "root.jsonl"
    _seed_team(team)
    _codex_transcript(transcript, "turn-old")
    first = _run_codex_start(team, transcript, state)
    claim_file = next((state / "teammode").glob("session-start-resume-*.json"))
    claim_state = json.loads(claim_file.read_text(encoding="utf-8"))
    claim_state["claims"] = {
        key: {"status": "completed", "completed_at": time.time() - 301}
        for key in claim_state["claims"]
    }
    claim_file.write_text(json.dumps(claim_state), encoding="utf-8")

    after_window = _run_codex_start(team, transcript, state)

    assert first.stdout.strip()
    assert after_window.stdout.strip()


def test_two_phase_running_lease_and_stale_owner_cas(tmp_path, monkeypatch):
    """timeout claim은 lease 뒤 복구되고 늦은 old owner settle은 새 claim을 못 덮는다."""
    hook = _load_hook_module()
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    transcript = tmp_path / "root.jsonl"
    _codex_transcript(transcript, "turn-lease")
    data = {
        "event": "SessionStart", "agent": "codex", "session_id": "root-session",
        "raw": {"source": "resume", "transcript_path": str(transcript)},
    }
    root = str(tmp_path / "team")

    run1, token1 = hook._begin_resume_generation(data, root, now=1_000)
    run2, _ = hook._begin_resume_generation(data, root, now=1_069)
    run3, token3 = hook._begin_resume_generation(data, root, now=1_070)
    hook._settle_resume_generation(root, token1, completed=True, now=1_071)
    run4, _ = hook._begin_resume_generation(data, root, now=1_072)
    hook._settle_resume_generation(root, token3, completed=True, now=1_073)
    run5, _ = hook._begin_resume_generation(data, root, now=1_372)
    run6, _ = hook._begin_resume_generation(data, root, now=1_373)

    assert run1 and token1
    assert not run2
    assert run3 and token3 and token3 != token1
    assert not run4
    assert not run5
    assert run6


def test_future_claim_and_lock_contention_fail_open(tmp_path, monkeypatch):
    hook = _load_hook_module()
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    transcript = tmp_path / "root.jsonl"
    _codex_transcript(transcript, "turn-future")
    data = {
        "event": "SessionStart", "agent": "codex", "session_id": "root-session",
        "raw": {"source": "resume", "transcript_path": str(transcript)},
    }
    root = str(tmp_path / "team")
    key = hook._resume_claim_key(data)
    path = hook._claim_state_path(root)
    assert hook._write_claims(path, {
        key: {"status": "completed", "completed_at": float("inf")},
    })
    future_run, future_token = hook._begin_resume_generation(data, root, now=1_000)
    hook._settle_resume_generation(root, future_token, completed=False, now=1_001)
    Path(path).write_text("not-json", encoding="utf-8")
    corrupt_run, corrupt_token = hook._begin_resume_generation(data, root, now=1_002)
    hook._settle_resume_generation(root, corrupt_token, completed=False, now=1_003)

    @contextlib.contextmanager
    def unavailable_lock(_root):
        yield False

    monkeypatch.setattr(hook._git_ops, "_push_pending_ledger_lock", unavailable_lock)
    lock_run, lock_token = hook._begin_resume_generation(data, root, now=1_004)

    assert future_run and future_token
    assert corrupt_run and corrupt_token
    assert lock_run and lock_token is None


def test_unreadable_generation_fails_open_to_context(tmp_path):
    """turn 식별 불가 때문에 정상 세션 맥락을 숨기지 않는다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "broken.jsonl"
    _seed_team(team, summary="fail-open 맥락")
    transcript.write_text("not-json\n", encoding="utf-8")

    runs = [_run_codex_start(team, transcript, state) for _ in range(2)]

    assert all("fail-open 맥락" in json.loads(run.stdout)["hookSpecificOutput"]["additionalContext"]
               for run in runs)


def test_claude_resume_without_codex_turn_context_is_not_suppressed(tmp_path):
    """Claude에는 안전한 invocation 세대가 없어 기존 SessionStart 동작을 보존한다."""
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "claude.jsonl"
    _seed_team(team)
    transcript.write_text(json.dumps({
        "type": "assistant", "uuid": "message-1", "isSidechain": False,
        "message": {"role": "assistant", "content": "done"},
    }) + "\n", encoding="utf-8")

    def run():
        return subprocess.run(
            [PY, str(NORMALIZE), "session-start.py"],
            input=json.dumps({
                "hook_event_name": "SessionStart",
                "session_id": "claude-session",
                "transcript_path": str(transcript),
                "source": "resume",
            }), capture_output=True, text=True,
            env=_hook_env(team, {"XDG_STATE_HOME": str(state)}))

    first = run()
    duplicate = run()
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "type": "user", "uuid": "message-2", "isSidechain": False,
            "message": {"role": "user", "content": "next"},
        }) + "\n")
    next_head = run()

    assert first.stdout.strip()
    assert duplicate.stdout.strip()
    assert next_head.stdout.strip()


@pytest.mark.parametrize("failure_stage", ["context", "stdout"])
def test_codex_known_main_failure_releases_resume_claim(
        tmp_path, monkeypatch, failure_stage):
    """context/출력의 확인 가능한 실패는 같은 세대의 즉시 재시도를 허용한다."""
    hook = _load_hook_module()
    team = tmp_path / "team"
    state = tmp_path / "state"
    transcript = tmp_path / "root.jsonl"
    _seed_team(team)
    _codex_transcript(transcript, "turn-main-fail")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setattr(hook, "_team_root", lambda: str(team))
    monkeypatch.setattr(hook, "_warn_if_stale_home", lambda _root: None)
    monkeypatch.setattr(hook, "_persist_session_relay", lambda _data: None)
    monkeypatch.setattr(hook, "_maybe_auto_pull", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hook, "_maybe_fetch_upstream", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hook, "_hook_lang", lambda _root: "ko")
    data = {
        "event": "SessionStart", "agent": "codex", "session_id": "root-session",
        "raw": {"source": "resume", "transcript_path": str(transcript)},
    }
    key = hook._resume_claim_key(data)
    if failure_stage == "context":
        monkeypatch.setattr(
            hook, "_build_context",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    else:
        monkeypatch.setattr(hook, "_build_context", lambda *_args, **_kwargs: "context")

        def broken_stdout(*_args, **_kwargs):
            raise BrokenPipeError("closed")

        monkeypatch.setattr(hook, "print", broken_stdout, raising=False)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(data)))

    assert hook.main() == 0
    available, claims = hook._read_claims(hook._claim_state_path(str(team)))
    assert available
    assert key not in claims
    should_retry, token = hook._begin_resume_generation(data, str(team))
    assert should_retry and token


def test_manifest_registers_session_start():
    """manifest 에 등록된 session-start.py 파일이 이제 실재한다(부재 갭 해소)."""
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text())
    entry = next(e for e in manifest if e.get("script") == "session-start.py")
    assert entry["event"] == "SessionStart"
    assert HOOK.is_file()
