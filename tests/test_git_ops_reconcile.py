"""이슈 #23 — git_ops.do_reconcile / ahead_behind / sync-warning 마커 테스트.

세션 시작 시 단순 `pull --ff-only` 가 로컬 diverge 에서 조용히 실패하던 문제를
do_reconcile(fetch + ff/rebase)로 실제 정합하고, 상태를 표면화한다.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·라이브 레포 무접촉.
"""
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops as go  # noqa: E402


def _worktree_probe_result(args):
    """Return the exact two-line contract required by is_git_worktree()."""
    argv = list(args)
    root = Path(argv[argv.index("-C") + 1]).resolve()
    return 0, f"true\n{root}\n", ""


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Keep helper and product Git subprocesses off host configuration."""
    for name in list(os.environ):
        if (name in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
                or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))):
            monkeypatch.delenv(name, raising=False)
    iso = tmp_path_factory.mktemp("git-iso")
    empty_cfg = iso / "empty-gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(iso))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(iso / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    # rebase/merge 가 committer 신원을 요구하므로 repo-local 로 박는다(결정적).
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


@pytest.fixture
def remote_clone(tmp_path):
    """bare upstream + work(올린 쪽) + clone(검사 대상). clone 은 origin/main 추적."""
    upstream = tmp_path / "upstream.git"
    work = tmp_path / "work"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(upstream))
    _init_repo(work)
    (work / "a.txt").write_text("v1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c1")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(upstream))
    _git(work, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    _git(clone, "checkout", "main")

    class C:
        pass
    c = C()
    c.upstream, c.work, c.clone = upstream, work, clone
    return c


def _push_new_upstream_commit(c, name="b.txt", content="up\n"):
    """work 에서 새 커밋을 만들어 origin 에 push(클론을 behind 로 만든다)."""
    (c.work / name).write_text(content)
    _git(c.work, "add", ".")
    _git(c.work, "commit", "-m", f"add {name}")
    _git(c.work, "push")


def _local_commit(c, name="local.txt", content="local\n"):
    """clone 에 push 안 한 로컬 커밋(ahead)을 만든다."""
    (c.clone / name).write_text(content)
    _git(c.clone, "add", ".")
    _git(c.clone, "commit", "-m", f"local {name}")


# ── do_reconcile ──

def test_reconcile_up_to_date(remote_clone):
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "up-to-date"
    assert res.ahead == 0 and res.behind == 0


def test_reconcile_fast_forward(remote_clone):
    _push_new_upstream_commit(remote_clone)
    res = go.do_reconcile(
        str(remote_clone.clone), _allow_bound_mutation=True)
    assert res.ok is True
    assert res.action == "fast-forward"
    assert (remote_clone.clone / "b.txt").exists()   # 실제 정합됨


def test_reconcile_ahead_only(remote_clone):
    _local_commit(remote_clone)
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "ahead-only"
    assert res.ahead == 1 and res.behind == 0


def test_reconcile_diverged_rebases(remote_clone):
    # upstream 과 로컬이 서로 다른 파일을 추가 → diverge, 충돌 없이 rebase 성공.
    _push_new_upstream_commit(remote_clone, name="b.txt")
    _local_commit(remote_clone, name="local.txt")
    res = go.do_reconcile(
        str(remote_clone.clone), _allow_bound_mutation=True)
    assert res.ok is True
    assert res.action == "rebased"
    assert res.diverged is True
    # 두 변경 모두 working tree 에 존재(rebase 로 로컬이 upstream 위로 올라감).
    assert (remote_clone.clone / "b.txt").exists()
    assert (remote_clone.clone / "local.txt").exists()
    assert res.ahead == 1   # 미push 로컬 커밋 1개 남음


def test_unbound_fast_forward_defers_for_edit_lease_then_succeeds(
        remote_clone):
    """Explicit unbound mutation must not move HEAD during any active edit."""
    _push_new_upstream_commit(remote_clone)
    clone = remote_clone.clone
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    owner = "1" * 64
    begun, detail = go.begin_hook_edit_lease(str(clone), owner)
    assert begun, detail
    try:
        blocked = go.do_reconcile(
            str(clone), _allow_bound_mutation=True)
    finally:
        assert go.end_hook_edit_lease(str(clone), owner)

    assert blocked.ok is False
    assert blocked.action == "deferred"
    assert "edit lease" in blocked.detail
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before

    reconciled = go.do_reconcile(
        str(clone), _allow_bound_mutation=True)

    assert reconciled.ok is True
    assert reconciled.action == "fast-forward"
    assert (clone / "b.txt").exists()


def test_unbound_rebase_defers_for_edit_lease_then_succeeds(remote_clone):
    """A diverged unbound reconcile waits until the active edit is released."""
    _push_new_upstream_commit(remote_clone, name="upstream.txt")
    _local_commit(remote_clone, name="local.txt")
    clone = remote_clone.clone
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    owner = "2" * 64
    begun, detail = go.begin_hook_edit_lease(str(clone), owner)
    assert begun, detail
    try:
        blocked = go.do_reconcile(
            str(clone), _allow_bound_mutation=True)
    finally:
        assert go.end_hook_edit_lease(str(clone), owner)

    assert blocked.ok is False
    assert blocked.action == "deferred"
    assert blocked.diverged is True
    assert "edit lease" in blocked.detail
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before

    reconciled = go.do_reconcile(
        str(clone), _allow_bound_mutation=True)

    assert reconciled.ok is True
    assert reconciled.action == "rebased"
    assert (clone / "upstream.txt").exists()
    assert (clone / "local.txt").exists()


def test_reconcile_shared_deadline_clamps_rebase_timeout(tmp_path, monkeypatch):
    """fetch와 local probes가 쓴 시간을 빼고 rebase가 총예산의 남은 몫만 받는다."""
    now = {"value": 100.0}
    calls = []
    rev_list_calls = 0

    def fake_run_git(args, timeout, **_kwargs):
        nonlocal rev_list_calls
        calls.append((list(args), timeout, now["value"]))
        now["value"] += 2.0
        if "--is-inside-work-tree" in args:
            return _worktree_probe_result(args)
        if "fetch" in args:
            return 0, "", ""
        if "rev-list" in args:
            rev_list_calls += 1
            return 0, ("1 1\n" if rev_list_calls == 1 else "0 1\n"), ""
        if "status" in args:
            return 0, "", ""
        if "symbolic-ref" in args:
            return 0, "main\n", ""
        if "refs/stash" in args:
            return 1, "", ""
        if "rev-parse" in args:
            return 0, "a" * 40 + "\n", ""
        if "rebase" in args:
            return 0, "", ""
        if "diff" in args:
            return 0, "", ""
        raise AssertionError(args)

    @go.contextmanager
    def ready_interlock(_root, _timeout=1.0):
        yield True, ""

    monkeypatch.setattr(go, "run_git", fake_run_git)
    monkeypatch.setattr(go, "_publication_interlock", ready_interlock)
    monkeypatch.setattr(go, "publication_blocker_detail", lambda *_a: "")
    monkeypatch.setattr(go, "_edit_gate", ready_interlock)
    monkeypatch.setattr(
        go, "_active_edit_lease_owners_locked", lambda _root: set())
    monkeypatch.setattr(
        go, "time", types.SimpleNamespace(monotonic=lambda: now["value"]))
    started = now["value"]

    res = go.do_reconcile(str(tmp_path), _allow_bound_mutation=True)

    assert res.ok is True and res.action == "rebased"
    rebase_timeout = next(timeout for args, timeout, _at in calls if "rebase" in args)
    assert rebase_timeout < go.NET_TIMEOUT
    assert now["value"] - started <= go.RECONCILE_TOTAL_BUDGET


def test_reconcile_defers_when_dirty_path_overlaps_upstream(remote_clone):
    """autostash apply 충돌 예상 시 사용자 dirty 파일을 건드리지 않고 보류한다."""
    _push_new_upstream_commit(remote_clone, name="a.txt", content="UPSTREAM\n")
    _local_commit(remote_clone, name="local.txt")
    dirty = remote_clone.clone / "a.txt"
    dirty.write_text("LOCAL DIRTY\n")

    res = go.do_reconcile(
        str(remote_clone.clone), _allow_bound_mutation=True)

    assert res.ok is False
    assert res.action == "conflict"
    assert "rebase deferred" in res.detail
    assert dirty.read_text() == "LOCAL DIRTY\n"
    assert " M a.txt" in _git(remote_clone.clone, "status", "--short").stdout
    assert _git(remote_clone.clone, "stash", "list").stdout == ""
    assert not (remote_clone.clone / ".git" / "rebase-merge").exists()


def test_reconcile_rolls_back_autostash_conflict_from_dirty_toctou(
        remote_clone, monkeypatch):
    """preflight/rebase 사이 dirty edit도 성공으로 오판하거나 conflict로 남기지 않는다."""
    _push_new_upstream_commit(remote_clone, name="a.txt", content="UPSTREAM\n")
    _local_commit(remote_clone, name="local.txt")
    pre_head = _git(remote_clone.clone, "rev-parse", "HEAD").stdout.strip()
    real_run_git = go.run_git
    injected = False

    def racing_run_git(args, timeout, **kwargs):
        nonlocal injected
        if not injected and "rebase" in args and "--autostash" in args:
            injected = True
            (remote_clone.clone / "a.txt").write_text("LOCAL DIRTY\n")
            _rc, out, err = real_run_git(args, timeout, **kwargs)
            # 실제 rebase mutation/rc0 출력 직후 timeout이 표면화되는 경로.
            raise subprocess.TimeoutExpired(
                cmd="git rebase", timeout=timeout, output=out, stderr=err)
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", racing_run_git)
    res = go.do_reconcile(
        str(remote_clone.clone), _allow_bound_mutation=True)

    assert injected is True
    assert res.ok is False
    assert res.action == "conflict"
    assert "timeout" in res.detail
    assert _git(remote_clone.clone, "rev-parse", "HEAD").stdout.strip() == pre_head
    assert (remote_clone.clone / "a.txt").read_text() == "LOCAL DIRTY\n"
    assert "UU" not in _git(remote_clone.clone, "status", "--short").stdout
    assert "<<<<<<<" not in (remote_clone.clone / "a.txt").read_text()


def test_reconcile_conflict_aborts_and_surfaces(remote_clone):
    # 같은 파일을 upstream·로컬이 충돌하게 수정 → rebase 충돌 → abort + conflict.
    _push_new_upstream_commit(remote_clone, name="a.txt", content="UP\n")
    _local_commit(remote_clone, name="a.txt", content="LOCAL\n")
    res = go.do_reconcile(
        str(remote_clone.clone), _allow_bound_mutation=True)
    assert res.ok is False
    assert res.action == "conflict"
    assert res.diverged is True
    assert "aborted" in res.detail
    assert "abort attempted" not in res.detail
    assert "rollback not proven" not in res.detail
    # rebase 가 진행 중으로 남지 않아야 한다(abort 로 원복).
    st = _git(remote_clone.clone, "status", "--porcelain=v1")
    assert "rebase" not in st.stdout.lower()
    assert _git(remote_clone.clone, "ls-files", "-u").stdout.strip() == ""
    assert not (remote_clone.clone / ".git" / "rebase-merge").exists()
    assert not (remote_clone.clone / ".git" / "rebase-apply").exists()


def test_reconcile_failed_abort_reports_rollback_not_proven(
        remote_clone, monkeypatch):
    """A failed `rebase --abort` must never be reported as proven aborted."""
    _push_new_upstream_commit(remote_clone, name="a.txt", content="UP\n")
    _local_commit(remote_clone, name="a.txt", content="LOCAL\n")
    real_run_git = go.run_git
    abort_attempts = []

    def failing_abort_run_git(args, timeout, **kwargs):
        if "rebase" in args and "--abort" in args:
            abort_attempts.append(list(args))
            return 1, "", "simulated abort failure"
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", failing_abort_run_git)
    res = go.do_reconcile(
        str(remote_clone.clone), _allow_bound_mutation=True)
    try:
        assert abort_attempts, "conflicting rebase must attempt cleanup"
        assert res.ok is False and res.action == "conflict"
        assert _git(remote_clone.clone, "ls-files", "-u").stdout.strip() != ""
        assert ((remote_clone.clone / ".git" / "rebase-merge").exists()
                or (remote_clone.clone / ".git" / "rebase-apply").exists())
        detail = res.detail.lower()
        assert "rebase failed (aborted)" not in detail
        assert "abort attempted; rollback not proven" in detail
    finally:
        # The product call intentionally failed to recover; keep the fixture
        # hygienic even when the RED assertion above fails.
        cleanup = _git(remote_clone.clone, "rebase", "--abort", check=False)
        assert cleanup.returncode == 0, cleanup.stderr
        assert _git(remote_clone.clone, "ls-files", "-u").stdout.strip() == ""
        assert not (remote_clone.clone / ".git" / "rebase-merge").exists()
        assert not (remote_clone.clone / ".git" / "rebase-apply").exists()


def test_bound_reconcile_postcondition_error_rolls_back_exact_identity(
        remote_clone, monkeypatch):
    """A failed private-index postcondition must not publish the rebased state."""
    _push_new_upstream_commit(remote_clone, name="remote.txt")
    _local_commit(remote_clone, name="local.txt")
    clone = remote_clone.clone
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": before}
    target = go._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")
    real_capture = go._capture_bound_user_state
    captures = []

    def fail_success_postcondition(*args, **kwargs):
        captures.append(True)
        # 1: original, 2: final pre-mutation proof,
        # 3: success postcondition, 4: rollback proof.
        if len(captures) == 3:
            return None
        return real_capture(*args, **kwargs)

    monkeypatch.setattr(
        go, "_capture_bound_user_state", fail_success_postcondition)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    after = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    assert after == before
    assert res.ok is False and res.action == "conflict"
    assert "aborted" in res.detail
    assert "rollback not proven" not in res.detail
    assert res.final_identity == identity
    assert _git(
        clone, "for-each-ref", "--format=%(refname)",
        "refs/tm-mode/reconcile").stdout.strip() == ""


def test_bound_reconcile_final_identity_probe_stays_inside_deadline(
        tmp_path, monkeypatch):
    """The transaction's two identity probes share one absolute deadline."""
    deadline = 3.0
    clock = {"now": 0.0}
    new_head = "2" * 40
    identity_timeouts = []
    txn = go._BoundIndexTxn(
        index_path=tmp_path / "index", lock_path=tmp_path / "index.lock",
        lock_fd=-1, tx_dir=tmp_path / "tx",
        original_index=tmp_path / "tx" / "original-index",
        work_index=tmp_path / "tx" / "work-index", token="token",
        head_ref="refs/tm-mode/reconcile/token/head",
        stash_ref="refs/tm-mode/reconcile/token/stash",
        original_head="1" * 40)

    monkeypatch.setattr(go.time, "monotonic", lambda: clock["now"])

    def fake_run_git(args, timeout, **kwargs):
        identity_timeouts.append(timeout)
        clock["now"] += timeout
        if "symbolic-ref" in args:
            return 0, "main\n", ""
        if "rev-parse" in args:
            return 0, f"{new_head}\n", ""
        raise AssertionError(args)

    monkeypatch.setattr(go, "run_git", fake_run_git)

    result = go._bound_identity_probe(
        str(tmp_path), txn, "main", deadline)

    assert result == {
        "key": "branch:main", "branch": "main", "head": new_head,
    }
    assert identity_timeouts == [2, 1]
    assert clock["now"] <= deadline


def test_reconcile_no_upstream(remote_clone):
    # origin 은 있지만 추적 upstream 이 없는 브랜치 → fetch 는 성공, @{u} 해석 실패.
    _git(remote_clone.clone, "checkout", "-b", "orphan-branch")
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "no-upstream"


def test_reconcile_not_worktree(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.do_reconcile(str(plain))
    assert res.ok is False
    assert res.action == "not-worktree"


# ── ahead_behind ──

def test_ahead_behind_counts(remote_clone):
    _push_new_upstream_commit(remote_clone)   # upstream +1 (clone behind 1)
    _local_commit(remote_clone)               # clone +1 (ahead 1)
    _git(remote_clone.clone, "fetch")         # origin/main 갱신(측정 전 fetch 필요)
    ahead, behind = go.ahead_behind(str(remote_clone.clone))
    assert ahead == 1
    assert behind == 1


# ── sync-warning 마커 (team_root 별 파일 — codex 리뷰 P2) ──

def test_sync_warning_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = "/team/alpha"
    assert go.read_sync_warning(root) == ""        # 없음
    go.write_sync_warning(root, "push 실패: GH007")
    assert "GH007" in go.read_sync_warning(root)
    go.clear_sync_warning(root)
    assert go.read_sync_warning(root) == ""


def test_sync_warning_writer_redacts_common_http_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = "/team/alpha"
    go.write_sync_warning(
        root, "client_secret=oauth api_key=key Authorization: Bearer bearer")
    marker = go.read_sync_warning(root)
    assert "oauth" not in marker and "api_key=key" not in marker
    assert "bearer" not in marker.lower()
    assert "[redacted]" in marker


def test_sync_warning_reader_scrubs_legacy_raw_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = "/team/alpha"
    marker_path = Path(go.sync_warning_path(root))
    marker_path.parent.mkdir(parents=True, mode=0o700)
    marker_path.write_text(
        "https://alice:password@example.com Authorization: Bearer old-token",
        encoding="utf-8")
    marker_path.chmod(0o600)
    marker = go.read_sync_warning(root)
    assert "password" not in marker and "old-token" not in marker
    assert "[redacted]" in marker


def test_sync_warning_per_team_paths_differ():
    # 팀마다 마커 파일 경로가 달라야 교차 간섭이 없다(격리의 근거).
    assert go.sync_warning_path("/team/alpha") != go.sync_warning_path("/team/beta")
    # 표기차(trailing slash)는 같은 파일로 정규화.
    assert go.sync_warning_path("/team/alpha") == go.sync_warning_path("/team/alpha/")


def test_sync_warning_read_isolated_by_team(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "alpha 경고")
    # 다른 팀은 자기 파일이 없어 빈 문자열.
    assert go.read_sync_warning("/team/beta") == ""
    assert go.read_sync_warning("/team/alpha") == "alpha 경고"


def test_sync_warning_clear_does_not_touch_other_team(tmp_path, monkeypatch):
    # 핵심 P2 회귀: repo B 의 clear 가 repo A 의 미해결 마커를 지우면 안 된다.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "alpha 미해결 push 실패")
    go.clear_sync_warning("/team/beta")     # repo B 성공 → 자기 것만 지움(없음)
    assert go.read_sync_warning("/team/alpha") == "alpha 미해결 push 실패"  # 보존


def test_sync_warning_clear_same_team_removes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "alpha 경고")
    go.clear_sync_warning("/team/alpha")
    assert go.read_sync_warning("/team/alpha") == ""


def test_sync_warning_two_teams_coexist(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "A")
    go.write_sync_warning("/team/beta", "B")
    # 독립 공존 — write 경합("마지막이 이김") 없음.
    assert go.read_sync_warning("/team/alpha") == "A"
    assert go.read_sync_warning("/team/beta") == "B"
