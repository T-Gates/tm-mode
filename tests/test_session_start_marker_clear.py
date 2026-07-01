"""이슈 #23 (codex 리뷰) — session-start 의 sync-warning 마커 제거 조건.

마커는 **실제 origin 정합이 입증된** 경우(up-to-date/fast-forward/rebased & ahead==0)
에만 지운다. no-upstream·ahead-only·fetch-failed·conflict·error 는 직전 push 실패가
미해결이므로 마커를 보존해 push 실패 가시성을 유지한다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SESSION_START = REPO / "infra" / "hooks" / "session-start.py"
sys.path.insert(0, str(REPO / "infra"))

import git_ops as go  # noqa: E402


def _load_session_start():
    spec = importlib.util.spec_from_file_location("session_start_mod", SESSION_START)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAutoPull:
    DEFAULT_THROTTLE_SECONDS = 300

    @staticmethod
    def should_pull(state, now, throttle):
        return True   # 스로틀 통과(항상 정합 시도)


class _FakeGitOps:
    """do_reconcile 결과를 고정하고 write/clear 호출을 기록."""

    def __init__(self, result):
        self._result = result
        self.writes = []
        self.cleared = 0

    def do_reconcile(self, team_root):
        return self._result

    def write_sync_warning(self, team_root, detail):
        self.writes.append((team_root, detail))

    def clear_sync_warning(self, team_root):
        self.cleared += 1
        self.cleared_root = team_root


def _run(result, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # 실 상태 무접촉
    mod = _load_session_start()
    fake = _FakeGitOps(result)
    monkeypatch.setattr(mod, "_git_ops", fake)
    monkeypatch.setattr(mod, "_auto_pull", _FakeAutoPull)
    mod._maybe_auto_pull("/team/alpha")
    return fake


# ── 마커 보존(미해결) ──

def test_no_upstream_preserves_marker(tmp_path, monkeypatch):
    # 핵심 버그: no-upstream 은 ok=True·ahead=0 이지만 정합이 입증된 게 아니다.
    res = go.ReconcileResult(ok=True, action="no-upstream", ahead=0, behind=0)
    fake = _run(res, tmp_path, monkeypatch)
    assert fake.cleared == 0       # 마커 지우면 안 됨
    assert fake.writes == []


def test_ahead_only_preserves_marker(tmp_path, monkeypatch):
    res = go.ReconcileResult(ok=True, action="ahead-only", ahead=2, behind=0)
    fake = _run(res, tmp_path, monkeypatch)
    assert fake.cleared == 0
    assert fake.writes == []


# ── 마커 제거(정합 입증) ──

def test_up_to_date_clears_marker(tmp_path, monkeypatch):
    res = go.ReconcileResult(ok=True, action="up-to-date", ahead=0, behind=0)
    fake = _run(res, tmp_path, monkeypatch)
    assert fake.cleared == 1


def test_fast_forward_clears_marker(tmp_path, monkeypatch):
    res = go.ReconcileResult(ok=True, action="fast-forward", ahead=0, behind=3)
    fake = _run(res, tmp_path, monkeypatch)
    assert fake.cleared == 1


def test_rebased_ahead_zero_clears_marker(tmp_path, monkeypatch):
    res = go.ReconcileResult(ok=True, action="rebased", ahead=0, behind=2, diverged=True)
    fake = _run(res, tmp_path, monkeypatch)
    assert fake.cleared == 1


def test_rebased_with_unpushed_ahead_preserves_marker(tmp_path, monkeypatch):
    # rebase 됐지만 미push 로컬 커밋이 남았으면(ahead>0) 아직 origin 정합 미완 → 보존.
    res = go.ReconcileResult(ok=True, action="rebased", ahead=1, behind=2, diverged=True)
    fake = _run(res, tmp_path, monkeypatch)
    assert fake.cleared == 0


# ── 충돌은 마커 기록(가시화) ──

def test_conflict_writes_marker(tmp_path, monkeypatch):
    res = go.ReconcileResult(ok=False, action="conflict", ahead=1, behind=1,
                             diverged=True, detail="CONFLICT")
    fake = _run(res, tmp_path, monkeypatch)
    assert len(fake.writes) == 1
    assert fake.cleared == 0
