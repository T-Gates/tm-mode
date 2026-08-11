"""Blind contract tests for issue #128's single-main synchronization flow."""

from __future__ import annotations

from contextlib import contextmanager
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

import git_ops  # noqa: E402


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_text(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.name", "Alice Example")
    _git(repo, "config", "user.email", "alice@example.invalid")


def _commit_file(repo: Path, name: str, content: str, message: str) -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", message)
    return _git_text(repo, "rev-parse", "HEAD")


@dataclass(frozen=True)
class MainRepos:
    origin: Path
    local: Path
    peer: Path
    initial_head: str


@pytest.fixture
def main_repos(tmp_path: Path) -> MainRepos:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"
    peer = tmp_path / "peer"

    origin.mkdir()
    _git(origin, "init", "--bare", ".")
    seed.mkdir()
    _git(seed, "init", "-b", "main", ".")
    _configure_identity(seed)
    initial_head = _commit_file(seed, "seed.txt", "seed\n", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    subprocess.run(
        ["git", "clone", "-q", str(origin), str(local)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(peer)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _configure_identity(local)
    _configure_identity(peer)
    return MainRepos(origin, local, peer, initial_head)


def _origin_main(origin: Path) -> str:
    return _git_text(origin, "rev-parse", "refs/heads/main")


def test_sync_main_pushes_a_normal_local_main_commit(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local_head = _commit_file(
        main_repos.local, "normal.txt", "normal\n", "normal main change")

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is True
    assert _origin_main(main_repos.origin) == local_head
    assert _git_text(
        main_repos.origin, "show", "refs/heads/main:normal.txt") == "normal"
    assert git_ops.read_last_sync_error(str(main_repos.local)) == ""


def test_sync_main_rebases_remote_ahead_then_pushes_linear_main(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local_before = _commit_file(
        main_repos.local, "local.txt", "local\n", "local main change")
    remote_head = _commit_file(
        main_repos.peer, "remote.txt", "remote\n", "remote main change")
    _git(main_repos.peer, "push", "origin", "main")

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is True
    local_after = _git_text(main_repos.local, "rev-parse", "main")
    assert local_after != local_before
    assert local_after == _origin_main(main_repos.origin)
    assert _git(
        main_repos.local,
        "merge-base",
        "--is-ancestor",
        remote_head,
        local_after,
        check=False,
    ).returncode == 0
    assert _git_text(
        main_repos.local, "rev-list", "--merges", f"{remote_head}..{local_after}"
    ) == ""
    assert _git_text(
        main_repos.origin, "show", "refs/heads/main:remote.txt") == "remote"
    assert _git_text(
        main_repos.origin, "show", "refs/heads/main:local.txt") == "local"


def test_failed_do_commit_keeps_local_commit_and_retry_publishes_it(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    note = main_repos.local / "retry.txt"
    note.write_text("retry\n", encoding="utf-8")
    missing_origin = tmp_path / "temporarily-unavailable.git"
    _git(main_repos.local, "remote", "set-url", "origin", str(missing_origin))

    first = git_ops.do_commit(
        str(main_repos.local),
        message="retry after failure",
        push=True,
        paths=[str(note)],
    )

    assert first.committed is True
    assert first.pushed is False
    committed_head = _git_text(main_repos.local, "rev-parse", "HEAD")
    assert _git_text(main_repos.local, "show", "HEAD:retry.txt") == "retry"
    assert git_ops.read_last_sync_error(str(main_repos.local))

    _git(main_repos.local, "remote", "set-url", "origin", str(main_repos.origin))
    retry = git_ops.sync_main(str(main_repos.local))

    assert retry.ok is True
    assert _origin_main(main_repos.origin) == committed_head
    assert _git_text(
        main_repos.origin, "show", "refs/heads/main:retry.txt") == "retry"
    assert git_ops.read_last_sync_error(str(main_repos.local)) == ""


def test_sync_main_refuses_non_main_without_mutating_repository(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _git(main_repos.local, "switch", "-c", "feature/alice")
    (main_repos.local / "seed.txt").write_text("dirty feature bytes\n", encoding="utf-8")
    branch_before = _git_text(main_repos.local, "branch", "--show-current")
    head_before = _git_text(main_repos.local, "rev-parse", "HEAD")
    status_before = _git_text(main_repos.local, "status", "--porcelain=v1")
    origin_before = _origin_main(main_repos.origin)

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is False
    assert _git_text(main_repos.local, "branch", "--show-current") == branch_before
    assert _git_text(main_repos.local, "rev-parse", "HEAD") == head_before
    assert _git_text(main_repos.local, "status", "--porcelain=v1") == status_before
    assert _origin_main(main_repos.origin) == origin_before
    assert git_ops.read_last_sync_error(str(main_repos.local))


def test_success_clears_last_error_without_pending_or_worker_surface(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    local_head = _commit_file(
        main_repos.local, "recover.txt", "recover\n", "recover main change")
    missing_origin = tmp_path / "offline.git"
    _git(main_repos.local, "remote", "set-url", "origin", str(missing_origin))

    failed = git_ops.sync_main(str(main_repos.local))

    assert failed.ok is False
    assert git_ops.read_last_sync_error(str(main_repos.local))
    state_dir = state_home / "teammode"
    if state_dir.exists():
        assert not any("push-pending" in item.name for item in state_dir.iterdir())

    _git(main_repos.local, "remote", "set-url", "origin", str(main_repos.origin))
    recovered = git_ops.sync_main(str(main_repos.local))

    assert recovered.ok is True
    assert _origin_main(main_repos.origin) == local_head
    assert git_ops.read_last_sync_error(str(main_repos.local)) == ""
    if state_dir.exists():
        assert not any("push-pending" in item.name for item in state_dir.iterdir())
    for removed_api in (
        "kick_push_worker",
        "push_pending_path",
        "read_push_pending_state",
        "write_push_pending",
    ):
        assert not hasattr(git_ops, removed_api)


def test_sync_main_aborts_only_its_conflicting_rebase_and_preserves_local_commit(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local_head = _commit_file(
        main_repos.local, "seed.txt", "local version\n", "local conflict")
    remote_head = _commit_file(
        main_repos.peer, "seed.txt", "remote version\n", "remote conflict")
    _git(main_repos.peer, "push", "origin", "main")

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is False
    assert result.action == "conflict"
    assert _git_text(main_repos.local, "rev-parse", "HEAD") == local_head
    assert (main_repos.local / "seed.txt").read_text(encoding="utf-8") == (
        "local version\n")
    assert _git_text(main_repos.local, "status", "--porcelain=v1") == ""
    for rebase_dir in ("rebase-merge", "rebase-apply"):
        git_path = Path(_git_text(
            main_repos.local, "rev-parse", "--git-path", rebase_dir))
        if not git_path.is_absolute():
            git_path = main_repos.local / git_path
        assert not git_path.exists()
    assert _origin_main(main_repos.origin) == remote_head
    assert git_ops.read_last_sync_error(str(main_repos.local))


def test_sync_main_never_autostashes_unrelated_dirty_bytes(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local_head = _commit_file(
        main_repos.local, "local.txt", "local\n", "local ahead")
    remote_head = _commit_file(
        main_repos.peer, "remote.txt", "remote\n", "remote ahead")
    _git(main_repos.peer, "push", "origin", "main")
    dirty = main_repos.local / "seed.txt"
    dirty.write_text("unrelated dirty bytes\n", encoding="utf-8")
    stash_before = _git_text(main_repos.local, "stash", "list")

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is False
    assert _git_text(main_repos.local, "rev-parse", "HEAD") == local_head
    assert dirty.read_text(encoding="utf-8") == "unrelated dirty bytes\n"
    assert _git_text(main_repos.local, "stash", "list") == stash_before
    assert _origin_main(main_repos.origin) == remote_head


def test_sync_main_holds_one_repo_lock_for_every_network_git_call(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _commit_file(main_repos.local, "locked.txt", "locked\n", "locked sync")
    real_lock = git_ops._repo_sync_lock
    real_run_git = git_ops.run_git
    state = {"held": False, "entries": 0, "network_calls": 0}

    @contextmanager
    def observed_lock(team_root: str, timeout: float):
        with real_lock(team_root, timeout) as acquired:
            state["entries"] += 1
            state["held"] = acquired
            try:
                yield acquired
            finally:
                state["held"] = False

    def guarded_run_git(args, *call_args, **call_kwargs):
        if any(command in args for command in ("fetch", "pull", "push")):
            state["network_calls"] += 1
            assert state["held"] is True
        return real_run_git(args, *call_args, **call_kwargs)

    monkeypatch.setattr(git_ops, "_repo_sync_lock", observed_lock)
    monkeypatch.setattr(git_ops, "run_git", guarded_run_git)

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is True
    assert state["entries"] == 1
    assert state["network_calls"] >= 3
    assert state["held"] is False


def test_stale_auto_merge_marker_does_not_preblock_main_sync(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local_head = _commit_file(
        main_repos.local, "auto-merge.txt", "ok\n", "stale marker sync")
    marker = Path(_git_text(
        main_repos.local, "rev-parse", "--git-path", "AUTO_MERGE"))
    if not marker.is_absolute():
        marker = main_repos.local / marker
    marker.write_text(
        _git_text(main_repos.local, "rev-parse", "HEAD^{tree}") + "\n",
        encoding="ascii",
    )

    result = git_ops.sync_main(str(main_repos.local))

    assert result.ok is True
    assert _origin_main(main_repos.origin) == local_head


def test_last_sync_error_redacts_and_clears_in_machine_local_state(
    main_repos: MainRepos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    raw = (
        "fatal https://alice:credential@example.invalid/repo "
        "Authorization: Bearer " + "bearer" + "-credential\x1b[31m")

    assert git_ops.write_last_sync_error(str(main_repos.local), raw) is True
    rendered = git_ops.read_last_sync_error(str(main_repos.local))

    assert "credential" not in rendered
    assert "\x1b" not in rendered
    assert "[redacted]" in rendered
    assert git_ops.clear_last_sync_error(str(main_repos.local)) is True
    assert git_ops.read_last_sync_error(str(main_repos.local)) == ""


def test_pending_worker_public_surface_and_script_are_absent() -> None:
    removed_names = (
        "kick_" + "push_worker",
        "push_" + "pending_path",
        "read_" + "push_pending_state",
        "write_" + "push_pending",
    )
    assert not (INFRA / "hooks" / ("push" + "-worker.py")).exists()
    for name in removed_names:
        assert not hasattr(git_ops, name)
