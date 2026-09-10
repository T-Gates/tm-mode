"""Scoped local commit and simple-main publication behavior."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from contextlib import contextmanager

import pytest


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

import git_ops  # noqa: E402


ENGINE = INFRA / "teammode.py"


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    for name in list(os.environ):
        if (name in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
                or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))):
            monkeypatch.delenv(name, raising=False)
    isolated = tmp_path_factory.mktemp("git-iso")
    empty_config = isolated / "empty-gitconfig"
    empty_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(isolated))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.name", "Alice Example")
    _git(repo, "config", "user.email", "alice@example.invalid")


def _commit(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run_engine(root: Path, *argv: str, env=None):
    command = [
        sys.executable, str(ENGINE), argv[0], "--root", str(root),
        "--settings", str(root / ".teammode-settings.json"), *argv[1:],
    ]
    return subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60)


def _hang_remote(tmp_path: Path, repo: Path) -> dict[str, str]:
    binary_dir = tmp_path / "hang-bin"
    binary_dir.mkdir(exist_ok=True)
    helper = binary_dir / "git-remote-sleep"
    helper.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    helper.chmod(0o755)
    _git(repo, "remote", "set-url", "origin", "sleep::repo")
    return {
        **os.environ,
        "PATH": f"{binary_dir}:{os.environ.get('PATH', '')}",
    }


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "local"
    repo.mkdir()
    _git(repo, "init", "-b", "main", ".")
    _configure(repo)
    _commit(repo, "base.txt", "base\n", "base")
    return repo


@pytest.fixture
def repo_with_remote(tmp_path: Path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    origin.mkdir()
    _git(origin, "init", "--bare", ".")
    seed.mkdir()
    _git(seed, "init", "-b", "main", ".")
    _configure(seed)
    _commit(seed, "base.txt", "base\n", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _configure(clone)
    return type("RemoteRepo", (), {"origin": origin, "clone": clone})()


def test_git_ops_exposes_do_commit() -> None:
    assert callable(git_ops.do_commit)


def test_do_commit_stages_and_commits_all_when_unscoped(local_repo: Path) -> None:
    (local_repo / "first.txt").write_text("first\n", encoding="utf-8")
    (local_repo / "second.txt").write_text("second\n", encoding="utf-8")

    result = git_ops.do_commit(str(local_repo), "all changes", push=False)

    assert result.ok is True and result.committed is True
    names = _git(local_repo, "show", "--format=", "--name-only", "HEAD").stdout
    assert "first.txt" in names and "second.txt" in names
    assert _git(local_repo, "status", "--porcelain=v1").stdout == ""


def test_scoped_commit_excludes_pre_staged_and_untracked_paths(local_repo: Path) -> None:
    target = local_repo / "target.txt"
    staged = local_repo / "already-staged.txt"
    untracked = local_repo / "untracked.txt"
    target.write_text("target\n", encoding="utf-8")
    staged.write_text("staged\n", encoding="utf-8")
    untracked.write_text("untracked\n", encoding="utf-8")
    _git(local_repo, "add", "--", staged.name)

    result = git_ops.do_commit(
        str(local_repo), "target only", push=False, paths=[target.name])

    assert result.ok is True
    names = _git(local_repo, "show", "--format=", "--name-only", "HEAD").stdout
    assert names.strip() == target.name
    status = _git(local_repo, "status", "--porcelain=v1").stdout
    assert "already-staged.txt" in status
    assert "untracked.txt" in status


def test_scoped_commit_records_a_named_deletion(local_repo: Path) -> None:
    target = local_repo / "base.txt"
    target.unlink()

    result = git_ops.do_commit(
        str(local_repo), "delete base", push=False, paths=[target.name])

    assert result.ok is True
    assert _git(
        local_repo, "show", "--format=", "--name-status", "HEAD"
    ).stdout == "D\tbase.txt\n"


def test_do_commit_no_changes_and_non_git_are_non_raising(
    local_repo: Path, tmp_path: Path,
) -> None:
    no_change = git_ops.do_commit(str(local_repo), "noop", push=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    non_git = git_ops.do_commit(str(plain), "noop", push=False)

    assert no_change.ok is False and no_change.committed is False
    assert non_git.ok is False and non_git.committed is False


def test_do_commit_pushes_main_to_origin(repo_with_remote, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    target = repo_with_remote.clone / "published.txt"
    target.write_text("published\n", encoding="utf-8")

    result = git_ops.do_commit(
        str(repo_with_remote.clone), "publish", push=True, paths=[target.name])

    assert result.ok is True and result.committed is True and result.pushed is True
    assert _git(
        repo_with_remote.origin,
        "show",
        "refs/heads/main:published.txt",
    ).stdout == "published\n"


def test_push_failure_preserves_local_commit_and_records_last_error(
    local_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    target = local_repo / "offline.txt"
    target.write_text("offline\n", encoding="utf-8")

    result = git_ops.do_commit(
        str(local_repo), "offline", push=True, paths=[target.name])

    assert result.ok is True and result.committed is True and result.pushed is False
    assert _git(local_repo, "show", "HEAD:offline.txt").stdout == "offline\n"
    assert git_ops.read_last_sync_error(str(local_repo))


def test_non_main_push_refuses_before_staging_or_committing(
    local_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _git(local_repo, "switch", "-c", "feature/alice")
    target = local_repo / "feature.txt"
    target.write_text("feature\n", encoding="utf-8")
    before = _git(local_repo, "rev-parse", "HEAD").stdout.strip()

    result = git_ops.do_commit(
        str(local_repo), "must not commit", push=True, paths=[target.name])

    assert result.ok is False and result.committed is False
    assert _git(local_repo, "rev-parse", "HEAD").stdout.strip() == before
    assert "feature.txt" in _git(
        local_repo, "status", "--porcelain=v1").stdout


def test_commit_message_starting_with_dash_is_data(local_repo: Path) -> None:
    (local_repo / "dash.txt").write_text("dash\n", encoding="utf-8")

    result = git_ops.do_commit(
        str(local_repo), "--not-an-option", push=False, paths=["dash.txt"])

    assert result.ok is True
    assert _git(local_repo, "log", "-1", "--format=%s").stdout.strip() == (
        "--not-an-option")


def test_cmd_commit_reports_preserved_local_commit_without_recovery_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "teammode_commit_under_test", INFRA / "teammode.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module._git_ops,
        "do_commit",
        lambda *_args, **_kwargs: git_ops.CommitResult(
            ok=True,
            committed=True,
            pushed=False,
            detail="committed; network unavailable",
        ),
    )

    assert module.cmd_commit(tmp_path, "message", True, ["note.md"]) == 0

    rendered = capsys.readouterr()
    assert "network unavailable" in rendered.out + rendered.err


def test_commit_command_commits_and_handles_no_changes(local_repo: Path) -> None:
    (local_repo / "cli.txt").write_text("cli\n", encoding="utf-8")

    committed = _run_engine(
        local_repo, "commit", "--message", "command commit")
    no_change = _run_engine(
        local_repo, "commit", "--message", "nothing to do")

    assert committed.returncode == 0, committed.stderr
    assert "command commit" in _git(
        local_repo, "log", "--oneline").stdout
    assert "Traceback" not in no_change.stderr


def test_commit_command_requires_message_in_team_language(local_repo: Path) -> None:
    (local_repo / "team.config.json").write_text(
        json.dumps({"team": {"name": "acme", "locale": "en_US"}}),
        encoding="utf-8",
    )

    result = _run_engine(local_repo, "commit")

    assert result.returncode != 0
    assert "--message" in result.stderr
    assert not re.search(r"[가-힣]", result.stderr)


def test_commit_command_requires_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ENGINE), "commit", "--message", "x"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
    )

    assert result.returncode != 0


def test_commit_command_push_without_remote_preserves_local_commit(
    local_repo: Path, tmp_path: Path,
) -> None:
    (local_repo / "offline.txt").write_text("offline\n", encoding="utf-8")
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")}

    result = _run_engine(
        local_repo, "commit", "--message", "offline command", "--push",
        env=env,
    )

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "offline command" in _git(
        local_repo, "log", "--oneline").stdout


def test_commit_message_cannot_inject_author_option(local_repo: Path) -> None:
    (local_repo / "author.txt").write_text("author\n", encoding="utf-8")

    result = _run_engine(
        local_repo,
        "commit",
        "--message",
        "normal --author=hacker <hacker@example.invalid>",
    )

    assert result.returncode == 0, result.stderr
    assert _git(local_repo, "log", "-1", "--format=%an").stdout.strip() != (
        "hacker")


def test_commit_command_rejects_empty_message(local_repo: Path) -> None:
    (local_repo / "empty-message.txt").write_text(
        "uncommitted\n", encoding="utf-8")
    before = _git(local_repo, "rev-parse", "HEAD").stdout.strip()

    result = _run_engine(local_repo, "commit", "--message", "")

    assert result.returncode != 0
    assert _git(local_repo, "rev-parse", "HEAD").stdout.strip() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group probe")
def test_commit_push_timeout_preserves_local_commit(
    repo_with_remote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _hang_remote(tmp_path, repo_with_remote.clone)
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (repo_with_remote.clone / "slow.txt").write_text(
        "slow\n", encoding="utf-8")
    before = int(_git(
        repo_with_remote.clone, "rev-list", "--count", "HEAD").stdout.strip())
    started = time.monotonic()

    result = git_ops.do_commit(
        str(repo_with_remote.clone), "preserve timeout", push=True, timeout=2)

    assert time.monotonic() - started < 10
    assert result.committed is True and result.pushed is False
    assert int(_git(
        repo_with_remote.clone,
        "rev-list",
        "--count",
        "HEAD",
    ).stdout.strip()) == before + 1


def test_do_commit_uses_noninteractive_git_environment(repo_with_remote) -> None:
    _git(
        repo_with_remote.clone,
        "remote",
        "set-url",
        "origin",
        "https://127.0.0.1:1/needs-auth.git",
    )
    (repo_with_remote.clone / "auth.txt").write_text(
        "auth\n", encoding="utf-8")
    started = time.monotonic()

    result = git_ops.do_commit(
        str(repo_with_remote.clone), "auth prompt guard", push=True)

    assert time.monotonic() - started < 30
    assert result.committed is True and result.pushed is False
    assert "auth prompt guard" in _git(
        repo_with_remote.clone, "log", "--oneline").stdout


@contextmanager
def _held_transaction(repo, operation, pause_after):
    """Pause after a real Git child exits; keep the parent transaction alive."""
    ready = repo.parent / "transaction-ready"
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import git_ops
root, operation, phase, ready = sys.argv[2:]
original = git_ops.run_git
paused = False
def run(args, *positional, **kwargs):
    global paused
    result = original(args, *positional, **kwargs)
    if phase in args and not paused:
        paused = True
        Path(ready).touch()
        sys.stdin.readline()
    return result
git_ops.run_git = run
result = (git_ops.sync_main(root) if operation == 'sync' else
          git_ops.do_commit(root, 'owner', push=operation == 'publish', paths=['owner.txt']))
print(json.dumps({'ok': result.ok, 'detail': result.detail}), flush=True)
"""
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(INFRA), str(repo), operation,
         pause_after, str(ready)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Owner failed to reach the transaction boundary."
        yield proc
    finally:
        if proc.poll() is None:
            try:
                out, err = proc.communicate("\n", timeout=15)
                assert proc.returncode == 0 and json.loads(out)["ok"], (out, err)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                pytest.fail("Owner failed to finish after the transaction handshake.")
        else:
            proc.communicate()


@pytest.mark.parametrize("operation,phase,contender", [
    ("publish", "add", "local"), ("publish", "commit", "sync"),
    ("publish", "push", "publish"), ("sync", "fetch", "local"),
    ("local", "add", "sync"),
])
def test_transaction_lock_serializes_commit_and_sync_processes(
        repo_with_remote, operation, phase, contender):
    repo = repo_with_remote.clone
    (repo / "owner.txt").write_text("owner\n")
    (repo / "contender.txt").write_text("contender\n")
    with _held_transaction(repo, operation, phase):
        before = _git(repo, "rev-parse", "HEAD").stdout
        started = time.monotonic()
        result = (git_ops.sync_main(str(repo)) if contender == "sync" else
                  git_ops.do_commit(str(repo), "contender", push=contender == "publish",
                                    paths=["contender.txt"]))
        assert not result.ok and time.monotonic() - started < 10
        assert _git(repo, "rev-parse", "HEAD").stdout == before
        assert not _git(repo, "ls-files", "--", "contender.txt").stdout
    assert git_ops.do_commit(str(repo), "after release", paths=["contender.txt"]).ok


def test_transaction_lock_is_shared_by_linked_worktrees(repo_with_remote, tmp_path):
    repo = repo_with_remote.clone
    other = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "other", str(other))
    (other / "contender.txt").write_text("contender\n")
    with _held_transaction(repo, "sync", "fetch"):
        result = git_ops.do_commit(str(other), "other checkout", paths=["contender.txt"])
        assert not result.ok
        assert not _git(other, "ls-files", "--", "contender.txt").stdout
    assert git_ops.do_commit(str(other), "after release", paths=["contender.txt"]).ok


def test_transaction_lock_does_not_expire_while_owner_is_alive(repo_with_remote, monkeypatch):
    repo = repo_with_remote.clone
    (repo / "owner.txt").write_text("owner\n")
    (repo / "contender.txt").write_text("contender\n")
    with _held_transaction(repo, "local", "add"):
        future = time.time() + 3600
        monkeypatch.setattr(git_ops.time, "time", lambda: future)
        assert not git_ops.do_commit(str(repo), "contender", paths=["contender.txt"]).ok


def test_transaction_lock_releases_on_process_death_without_removing_file(repo_with_remote):
    repo = repo_with_remote.clone
    lock_file = repo / ".git" / ".tm-mode-publication.lock"
    lock_file.touch()
    (repo / "owner.txt").write_text("owner\n")
    with _held_transaction(repo, "local", "add") as owner:
        owner.kill()
        owner.wait(timeout=5)
    assert lock_file.exists()
    assert git_ops.do_commit(str(repo), "after owner death", paths=["owner.txt"]).ok
    assert lock_file.exists()


@pytest.mark.parametrize("marker", ["valid", "malformed"])
def test_historical_edit_mutex_marker_does_not_block_transaction(local_repo, marker):
    key = hashlib.sha1(os.path.normpath(str(local_repo)).encode()).hexdigest()[:16]
    path = Path(os.environ["XDG_STATE_HOME"]) / "teammode" / ("edit-mutex-" + key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": "a" * 64, "created_at": time.time()})
                    if marker == "valid" else "malformed historical marker")
    (local_repo / "target.txt").write_text("target\n")
    assert git_ops.do_commit(str(local_repo), "ignore old marker", paths=["target.txt"]).ok


def test_transaction_error_releases_lock_for_next_commit(local_repo, monkeypatch):
    (local_repo / "target.txt").write_text("target\n")
    original = git_ops.run_git
    with monkeypatch.context() as patch:
        def fail_commit(args, *positional, **kwargs):
            return (128, "", "commit failed") if "commit" in args else original(args, *positional, **kwargs)
        patch.setattr(git_ops, "run_git", fail_commit)
        assert not git_ops.do_commit(str(local_repo), "failure", paths=["target.txt"]).ok
    assert git_ops.do_commit(str(local_repo), "after failure", paths=["target.txt"]).ok
