"""Blind contract tests for issue #128's single-main synchronization flow."""

from __future__ import annotations

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
