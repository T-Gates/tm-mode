"""auto-commit foreground push + pending-worker fallback 가시화 계약."""
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AUTO_COMMIT = REPO / "infra" / "hooks" / "auto-commit.py"
sys.path.insert(0, str(REPO / "infra"))

import git_ops as go  # noqa: E402


def _load_hook():
    spec = importlib.util.spec_from_file_location("auto_commit_mod", AUTO_COMMIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeGitOps:
    """auto-commit 상태 전이를 검증하는 기록형 git_ops fake."""

    DEFAULT_TIMEOUT = 2

    def __init__(self, result, *, pending_reads=None, pending_write_ok=True,
                 conditional_clear=True, ahead=0, has_upstream=True):
        self._results = list(result) if isinstance(result, list) else [result]
        self._pending_reads = list(pending_reads or [""])
        self._last_pending = self._pending_reads[-1]
        self.pending_write_ok = pending_write_ok
        self.conditional_clear = conditional_clear
        self.ahead = ahead
        self.has_upstream = has_upstream
        self.warnings = []
        self.cleared = 0
        self.push_args = []
        self.pending_writes = 0
        self.pending_clears = []
        self.kicked = 0

    def do_commit(self, root, message, push=False, paths=None):
        self.push_args.append(push)
        return self._results.pop(0)

    def write_sync_warning(self, root, detail):
        self.warnings.append((root, detail))

    def clear_sync_warning(self, root):
        self.cleared += 1
        self.cleared_root = root

    def read_push_pending(self, root):
        if self._pending_reads:
            self._last_pending = self._pending_reads.pop(0)
        return self._last_pending

    def write_push_pending(self, root):
        self.pending_writes += 1
        return self.pending_write_ok

    def clear_push_pending_if_unchanged(self, root, snapshot):
        self.pending_clears.append((root, snapshot))
        return self.conditional_clear

    def _ahead_behind_raw(self, root, timeout):
        return self.ahead, 0, self.has_upstream

    def kick_push_worker(self, root, worker_path):
        self.kicked += 1
        return True


def _run(mod, fake, root, monkeypatch, *, lang="ko"):
    monkeypatch.setattr(mod, "_git_ops", fake)
    monkeypatch.setattr(mod, "_hook_lang", lambda _root: lang)
    monkeypatch.setenv("TEAMMODE_HOME", str(root))
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(root / "x.md")], "agent": "claude"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return mod.main()


def _active_root(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    (root / ".teammode-active").write_text("")
    return root


def test_foreground_push_success_clears_unchanged_pending_and_warning(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=True,
                          detail="committed and pushed")
    fake = _FakeGitOps(res, pending_reads=["old-nonce", ""])
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.pending_clears == [(str(root), "old-nonce")]
    assert fake.pending_writes == 0
    assert fake.kicked == 0
    assert fake.cleared == 1
    assert fake.warnings == []


def test_foreground_push_success_preserves_replaced_pending(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=True,
                          detail="committed and pushed")
    fake = _FakeGitOps(res, pending_reads=["old-nonce", "new-nonce"],
                       conditional_clear=False)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.pending_clears == [(str(root), "old-nonce")]
    assert fake.cleared == 0
    assert fake.pending_writes == 0
    assert fake.kicked == 0


@pytest.mark.parametrize("ahead,has_upstream", [(1, True), (0, False)])
def test_foreground_push_success_keeps_warning_until_fully_synced(
        tmp_path, monkeypatch, ahead, has_upstream):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=True,
                          detail="committed and pushed")
    fake = _FakeGitOps(res, pending_reads=[""], ahead=ahead,
                       has_upstream=has_upstream)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.cleared == 0


def test_foreground_push_failure_writes_detail_pending_and_kicks_worker(
        tmp_path, monkeypatch, capsys):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=False,
                          detail="committed; rebase failed (aborted): conflict")
    fake = _FakeGitOps(res)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    err = capsys.readouterr().err
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.pending_writes == 1
    assert fake.kicked == 1
    assert fake.cleared == 0
    assert any(res.detail in detail for _, detail in fake.warnings)
    assert res.detail in err


def test_pending_write_failure_preserves_push_detail_without_worker(
        tmp_path, monkeypatch, capsys):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=False,
                          detail="committed; push timeout")
    fake = _FakeGitOps(res, pending_write_ok=False)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    err = capsys.readouterr().err
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.pending_writes == 1
    assert fake.kicked == 0
    assert res.detail in fake.warnings[-1][1]
    assert "pending" in fake.warnings[-1][1].lower()
    assert res.detail in err


def test_foreground_push_failure_english_output_has_no_hangul(
        tmp_path, monkeypatch, capsys):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=False,
                          detail="committed; network timeout")
    fake = _FakeGitOps(res)
    rc = _run(_load_hook(), fake, root, monkeypatch, lang="en")
    err = capsys.readouterr().err
    marker = fake.warnings[-1][1] if fake.warnings else ""
    assert rc == 0
    assert res.detail in marker and res.detail in err
    assert not re.search(r"[가-힣]", marker)
    assert not re.search(r"[가-힣]", err)


def test_index_lock_retry_keeps_foreground_push(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    first = go.CommitResult(ok=False, committed=False, pushed=False,
                            detail="fatal: index.lock already exists")
    second = go.CommitResult(ok=True, committed=True, pushed=False,
                             detail="committed; push timeout")
    fake = _FakeGitOps([first, second])
    mod = _load_hook()
    monkeypatch.setattr(mod._time, "sleep", lambda _seconds: None)
    rc = _run(mod, fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True, True]
    assert fake.pending_writes == 1


def test_nothing_to_commit_leaves_pending_and_warning_unchanged(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=False, committed=False, pushed=False,
                          detail="nothing to commit")
    fake = _FakeGitOps(res, pending_reads=["old-nonce"])
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.warnings == []
    assert fake.cleared == 0
    assert fake.pending_writes == 0
    assert fake.pending_clears == []
    assert fake.kicked == 0


# ─── 이슈 #9(a): TEAMMODE_HOME 스테일 시 stderr 경고 ───

def test_stale_teammode_home_warns_on_stderr(tmp_path, monkeypatch, capsys):
    """TEAMMODE_HOME 이 존재하지 않는 경로 → exit 0 불변 + stderr 한 줄 경고 (커밋 시도 없음)."""
    gone = tmp_path / "moved-away"  # 존재하지 않음
    fake = _FakeGitOps(None)  # 도달하면 안 되는 경로 — do_commit 호출 자체가 없어야 함
    rc = _run(_load_hook(), fake, gone, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == ""
    # i18n 갱신(적대검수 — long tail): config 없는 픽스처는 en 기본(team_lang 계약) —
    # 언어중립 마커만 확인.
    assert "TEAMMODE_HOME" in captured.err
    assert len(captured.err.strip().splitlines()) == 1, "경고는 정확히 한 줄"
    assert fake.warnings == []            # git 동작 전에 멈춘다(거동 불변)


def test_valid_root_teammode_off_stays_silent(tmp_path, monkeypatch, capsys):
    """유효 팀 루트(memory 표식)인데 .teammode-active 없음 = 정상 off — 침묵 유지."""
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    fake = _FakeGitOps(None)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err.strip() == "", f"정상 off 상태는 경고 금지: {captured.err!r}"
