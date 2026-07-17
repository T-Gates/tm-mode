"""auto-commit foreground push + pending-worker fallback 가시화 계약."""
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

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
                 conditional_clear=True, ahead=0, has_upstream=True,
                 switch_checkout_on_commit=False, lease_owner="",
                 pending_available=True):
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
        self.reconcile_before_push_args = []
        self.pending_writes = 0
        self.pending_write_identities = []
        self.pending_write_targets = []
        self.pending_clears = []
        self.kicked = 0
        self.events = []
        self.current_key = "branch:test"
        self.switch_checkout_on_commit = switch_checkout_on_commit
        self.lease_owner = lease_owner
        self.pending_available = pending_available
        self.mutation_kwargs = []
        self.released_leases = []

    def do_commit(self, root, message, push=False, timeout=go.NET_TIMEOUT,
                  paths=None,
                  reconcile_before_push=False, **kwargs):
        self.push_args.append(push)
        self.reconcile_before_push_args.append(reconcile_before_push)
        self.mutation_kwargs.append(kwargs)
        result = self._results.pop(0)
        if self.switch_checkout_on_commit:
            self.current_key = "branch:other"
        return result

    def hook_edit_lease_owner(self, data):
        return self.lease_owner

    def end_hook_edit_lease(self, root, owner):
        self.released_leases.append((root, owner))
        return True

    def write_sync_warning(self, root, detail):
        self.events.append("warning")
        self.warnings.append((root, detail))

    def clear_sync_warning(self, root):
        self.cleared += 1
        self.cleared_root = root

    def _next_pending(self):
        if self._pending_reads:
            self._last_pending = self._pending_reads.pop(0)
        return self._last_pending

    def read_push_pending(self, root):
        return self._next_pending()

    def read_push_pending_state(self, root):
        return SimpleNamespace(
            content=self._next_pending(), available=self.pending_available)

    def bind_legacy_pending_to_current_checkout(self, root, snapshot):
        return snapshot

    def write_push_pending(self, root, identity=None, target=None):
        self.events.append("pending")
        self.pending_writes += 1
        self.pending_write_identities.append(identity)
        self.pending_write_targets.append(target)
        return self.pending_write_ok

    def pending_entry_covered_by_publication(
            self, root, snapshot, target_key, identity, target):
        return bool(
            snapshot and target_key == self.current_key and identity and target)

    def clear_push_pending_if_unchanged(self, root, snapshot, target_key=None):
        self.pending_clears.append((root, snapshot))
        return self.conditional_clear

    def pending_entry_key_for_current_checkout(self, root, snapshot):
        return self.current_key if snapshot else ""

    def pending_allows_current_checkout_reconcile(self, root, snapshot):
        return not snapshot

    def _ahead_behind_raw(self, root, timeout):
        return self.ahead, 0, self.has_upstream

    def clear_sync_warning_if_fully_published(self, root):
        pending = self._next_pending()
        if self.has_upstream and self.ahead == 0 and not pending:
            self.clear_sync_warning(root)
            return True
        return False

    def clear_sync_warning_after_exact_publication(
            self, root, identity, target):
        pending = self._next_pending()
        if identity and target and not pending:
            self.clear_sync_warning(root)
            return True
        return False

    @staticmethod
    def sanitize_git_detail(detail):
        return go.sanitize_git_detail(detail)

    def kick_push_worker(self, root, worker_path):
        self.events.append("kick")
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
    identity = {"key": "branch:test", "branch": "test", "head": "a" * 40}
    target = {
        "remote": "origin", "destination": "refs/heads/test",
        "reconcile_ref": "refs/remotes/origin/test",
        "remote_fingerprint": "b" * 64,
    }
    res = go.CommitResult(
        ok=True, committed=True, pushed=True, detail="committed and pushed",
        pending_identity=identity, pending_target=target)
    fake = _FakeGitOps(res, pending_reads=["old-nonce", ""])
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.reconcile_before_push_args == [True]
    assert fake.pending_clears == [(str(root), "old-nonce")]
    assert fake.pending_writes == 0
    assert fake.kicked == 0
    assert fake.cleared == 1
    assert fake.warnings == []


def test_unavailable_pending_ledger_disables_leased_worktree_mutation(
        tmp_path, monkeypatch):
    """Unreadable retry evidence is fail-closed even with exact hook identity."""
    root = _active_root(tmp_path)
    (root / "x.md").write_text("edit\n", encoding="utf-8")
    result = go.CommitResult(
        ok=False, committed=False, pushed=False, detail="nothing to commit")
    owner = "a" * 64
    fake = _FakeGitOps(
        result, lease_owner=owner, pending_available=False)

    rc = _run(_load_hook(), fake, root, monkeypatch)

    assert rc == 0
    assert fake.mutation_kwargs == [{}]
    assert fake.released_leases == [(str(root), owner)]


def test_foreground_push_success_preserves_replaced_pending(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    identity = {"key": "branch:test", "branch": "test", "head": "a" * 40}
    target = {
        "remote": "origin", "destination": "refs/heads/test",
        "reconcile_ref": "refs/remotes/origin/test",
        "remote_fingerprint": "b" * 64,
    }
    res = go.CommitResult(
        ok=True, committed=True, pushed=True, detail="committed and pushed",
        pending_identity=identity, pending_target=target)
    fake = _FakeGitOps(res, pending_reads=["old-nonce", "new-nonce"],
                       conditional_clear=False)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.pending_clears == [(str(root), "old-nonce")]
    assert fake.cleared == 0
    assert fake.pending_writes == 0
    assert fake.kicked == 0


def test_foreground_success_does_not_clear_if_checkout_changed_mid_hook(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    res = go.CommitResult(ok=True, committed=True, pushed=True,
                          detail="committed and pushed")
    fake = _FakeGitOps(
        res, pending_reads=["old-nonce", "old-nonce"],
        switch_checkout_on_commit=True)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.pending_clears == []


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
    assert fake.events == ["pending", "warning", "kick"]


def test_foreground_push_failure_records_commit_identity_not_later_checkout(
        tmp_path, monkeypatch):
    """do_commit 반환 직후 checkout이 바뀌어도 failed commit branch/HEAD에 bind한다."""
    root = _active_root(tmp_path)
    identity = {
        "key": "branch:session-a", "branch": "session-a",
        "head": "a" * 40,
    }
    res = go.CommitResult(
        ok=True, committed=True, pushed=False, detail="committed; push timeout",
        pending_identity=identity)
    fake = _FakeGitOps(res, switch_checkout_on_commit=True)

    assert _run(_load_hook(), fake, root, monkeypatch) == 0
    assert fake.current_key == "branch:other"
    assert fake.pending_write_identities == [identity]


def test_foreground_push_failure_records_exact_publication_target(
        tmp_path, monkeypatch):
    root = _active_root(tmp_path)
    identity = {"key": "branch:test", "branch": "test", "head": "a" * 40}
    target = {
        "remote": "fork", "destination": "refs/heads/team-sync",
        "reconcile_ref": "refs/remotes/fork/team-sync",
        "remote_fingerprint": "b" * 64,
    }
    result = go.CommitResult(
        ok=True, committed=True, pushed=False, detail="committed; push timeout",
        pending_identity=identity, pending_target=target)
    fake = _FakeGitOps(result)

    assert _run(_load_hook(), fake, root, monkeypatch) == 0
    assert fake.pending_write_identities == [identity]
    assert fake.pending_write_targets == [target]


@pytest.mark.parametrize("detail_fragment", [
    "tracking update skipped: raced tracking ref",
    "upstream setup skipped: remote binding changed",
])
def test_foreground_publication_partial_local_cleanup_stays_visible(
        tmp_path, monkeypatch, detail_fragment):
    root = _active_root(tmp_path)
    identity = {"key": "branch:test", "branch": "test", "head": "a" * 40}
    target = {
        "remote": "origin", "destination": "refs/heads/test",
        "reconcile_ref": "refs/remotes/origin/test",
        "remote_fingerprint": "b" * 64,
    }
    result = go.CommitResult(
        ok=True, committed=True, pushed=True,
        detail=f"committed and pushed; {detail_fragment}",
        pending_identity=identity, pending_target=target)
    fake = _FakeGitOps(result, pending_reads=[""])

    assert _run(_load_hook(), fake, root, monkeypatch) == 0
    assert fake.cleared == 0
    assert detail_fragment in fake.warnings[-1][1]


@pytest.mark.parametrize("lang", ("ko", "en"))
def test_pending_write_failure_preserves_push_detail_without_worker(
        tmp_path, monkeypatch, capsys, lang):
    root = _active_root(tmp_path)
    secret = "ghp_pending_write_secret_123456"
    res = go.CommitResult(ok=True, committed=True, pushed=False,
                          detail=f"committed; push timeout token={secret}")
    fake = _FakeGitOps(res, pending_write_ok=False)
    rc = _run(_load_hook(), fake, root, monkeypatch, lang=lang)
    err = capsys.readouterr().err
    rendered = fake.warnings[-1][1] + "\n" + err
    assert rc == 0
    assert fake.push_args == [True]
    assert fake.pending_writes == 1
    assert fake.kicked == 0
    expected = (("push-pending 상태를 안전하게 갱신하지 못했습니다",
                 "커밋은 보존됐지만 자동 push 복구는 예약되지 않았습니다")
                if lang == "ko" else
                ("could not safely update push-pending state",
                 "automatic push recovery was not scheduled"))
    assert all(text.lower() in rendered.lower() for text in expected)
    assert secret not in rendered and "[redacted]" in rendered
    assert "xdg" not in rendered.lower() and "permission" not in rendered.lower()
    assert fake.events == ["pending", "warning"]


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


def test_foreground_push_failure_redacts_credentials_and_control_codes(
        tmp_path, monkeypatch, capsys):
    root = _active_root(tmp_path)
    secret = "ghp_1234567890ABCDEFGHIJ"
    password = "supersecret"
    client_secret = "oauth-client-secret-value"
    api_key = "api-key-value"
    bearer = "bearer-token-value"
    res = go.CommitResult(
        ok=True, committed=True, pushed=False,
        detail=(f"fatal: https://alice:{password}@example.com/repo.git "
                f"token={secret} client_secret={client_secret} api_key={api_key} "
                f"Authorization: Bearer {bearer}\x1b[31m"))
    fake = _FakeGitOps(res)
    rc = _run(_load_hook(), fake, root, monkeypatch, lang="en")
    err = capsys.readouterr().err
    marker = fake.warnings[-1][1]
    assert rc == 0
    assert password not in marker and password not in err
    assert secret not in marker and secret not in err
    assert client_secret not in marker and client_secret not in err
    assert api_key not in marker and api_key not in err
    assert bearer not in marker and bearer not in err
    assert "\x1b" not in marker and "\x1b" not in err
    assert "[redacted]" in marker and "[redacted]" in err


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
    assert fake.reconcile_before_push_args == [True, True]
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
