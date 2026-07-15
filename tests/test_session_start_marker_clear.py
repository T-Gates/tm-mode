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

    def do_reconcile(self, team_root, **kwargs):
        assert kwargs.get("_allow_bound_mutation") is True
        return self._result

    def write_sync_warning(self, team_root, detail):
        self.writes.append((team_root, detail))

    def clear_sync_warning(self, team_root):
        self.cleared += 1
        self.cleared_root = team_root

    def clear_sync_warning_if_fully_published(self, team_root):
        self.clear_sync_warning(team_root)
        return True

    def read_push_pending_state(self, team_root):
        return go.PushPendingRead("", True)

    def bind_legacy_pending_to_current_checkout(self, team_root, snapshot):
        return snapshot

    def pending_entry_key_for_current_checkout(self, team_root, snapshot):
        return ""


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


def test_pending_reconcile_runs_before_exact_worker_restart(
        tmp_path, monkeypatch):
    """current pending uses ancestry-preserving recovery before exact worker."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    mod = _load_session_start()

    class _OrderingGitOps(_FakeGitOps):
        DEFAULT_TIMEOUT = 2

        def __init__(self):
            super().__init__(
                go.ReconcileResult(ok=True, action="rebased", ahead=1, behind=1,
                                   diverged=True))
            self.events = []

        def do_reconcile(self, team_root, **kwargs):
            self.events.append("reconcile")
            return super().do_reconcile(team_root, **kwargs)

        def read_push_pending_state(self, team_root):
            return go.PushPendingRead("pending", True)

        def bind_legacy_pending_to_current_checkout(self, team_root, snapshot):
            return snapshot

        def pending_entry_key_for_current_checkout(self, team_root, snapshot):
            return "branch:main"

        def pending_target_summary(self, snapshot, team_root=None):
            return "branch main"

        def reconcile_current_pending(
                self, team_root, snapshot, target_key, **kwargs):
            self.events.append("pending-reconcile")
            return go.ReconcileResult(
                ok=True, action="merged", ahead=1, behind=1,
                diverged=True)

        def _ahead_behind_raw(self, team_root, timeout):
            raise AssertionError(
                "current checkout state must not drive pending recovery")

        def kick_push_worker(self, team_root, worker):
            self.events.append("kick")
            return True

    fake = _OrderingGitOps()
    monkeypatch.setattr(mod, "_git_ops", fake)
    monkeypatch.setattr(mod, "_auto_pull", _FakeAutoPull)

    mod._maybe_auto_pull(str(tmp_path))

    assert fake.events == ["pending-reconcile", "kick"]


# ── 충돌은 마커 기록(가시화) ──

def test_conflict_writes_marker(tmp_path, monkeypatch):
    res = go.ReconcileResult(ok=False, action="conflict", ahead=1, behind=1,
                             diverged=True, detail="CONFLICT")
    fake = _run(res, tmp_path, monkeypatch)
    assert len(fake.writes) == 1
    assert fake.cleared == 0


def test_conflict_marker_content_english_for_en_locale_team(tmp_path, monkeypatch):
    """i18n(적대검수 — long tail): 마커 내용 자체가 lang 을 따른다.

    write_sync_warning 의 detail 은 나중에 session-start 의 hook_ss_sync_warn
    (이미 i18n 라우팅된 wrapper)의 {warn} 자리에 그대로 삽입되므로, 마커 자체가
    lang 에 안 맞으면 en 팀도 wrapper 안에 한글 상세가 섞인다(addendum 2 에서
    발견한 것과 동일 클래스). 여기서는 실제 team_root(tmp_path)에 en_US 팀 config 를
    둬 _hook_lang 이 진짜로 "en" 을 돌려주게 만들고, 마커 CONTENT 를 직접 검사한다.
    """
    import json
    import re
    (tmp_path / "team.config.json").write_text(
        json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    mod = _load_session_start()
    res = go.ReconcileResult(ok=False, action="conflict", ahead=1, behind=1,
                             diverged=True, detail="CONFLICT")
    fake = _FakeGitOps(res)
    monkeypatch.setattr(mod, "_git_ops", fake)
    monkeypatch.setattr(mod, "_auto_pull", _FakeAutoPull)
    mod._maybe_auto_pull(str(tmp_path))
    assert len(fake.writes) == 1
    _, detail = fake.writes[0]
    assert not re.search(r"[가-힣]", detail), f"en 팀 마커 내용에 한글 섞임: {detail!r}"
    assert "conflict" in detail.lower()
