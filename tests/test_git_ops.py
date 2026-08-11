"""Generic Git runner and ff-only pull safety contracts."""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
HOOKS = INFRA / "hooks"
ENGINE = INFRA / "teammode.py"
for path in (INFRA, HOOKS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import git_ops  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Keep test Git processes independent of the developer's configuration."""
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
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main", ".")
    _configure(work)
    (work / "base.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "base.txt")
    _git(work, "commit", "-m", "base")
    return work


@pytest.fixture
def behind_clone(tmp_path: Path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    origin.mkdir()
    _git(origin, "init", "--bare", ".")
    seed.mkdir()
    _git(seed, "init", "-b", "main", ".")
    _configure(seed)
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "base.txt")
    _git(seed, "commit", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _configure(clone)
    (seed / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(seed, "add", "remote.txt")
    _git(seed, "commit", "-m", "remote")
    _git(seed, "push", "origin", "main")
    return type("BehindClone", (), {"origin": origin, "seed": seed, "clone": clone})()


def test_upstream_sync_and_scoped_commit_preserve_instance_util(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    upstream.mkdir()
    _git(upstream, "init", "--bare", ".")
    seed.mkdir()
    _git(seed, "init", "-b", "main", ".")
    _configure(seed)
    (seed / "infra" / "skills" / "util").mkdir(parents=True)
    (seed / "infra" / "engine.py").write_text("v1\n", encoding="utf-8")
    (seed / "infra" / "skills" / "util" / ".gitkeep").write_text(
        "v1\n", encoding="utf-8"
    )
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "upstream v1")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "-u", "origin", "main")

    team.mkdir()
    _git(team, "init", "-b", "main", ".")
    _configure(team)
    util_file = (
        team
        / "infra"
        / "skills"
        / "util"
        / "acme-schedule"
        / "SKILL.md"
    )
    util_file.parent.mkdir(parents=True)
    (team / "infra" / "engine.py").write_text("team-old\n", encoding="utf-8")
    util_file.write_text("committed custom util\n", encoding="utf-8")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team instance")
    _git(team, "remote", "add", "upstream", str(upstream))

    (seed / "infra" / "engine.py").write_text("v2\n", encoding="utf-8")
    (seed / "infra" / "skills" / "util" / ".gitkeep").write_text(
        "upstream touched util\n", encoding="utf-8"
    )
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "upstream v2")
    _git(seed, "push", "origin", "main")

    util_file.write_text("uncommitted custom util\n", encoding="utf-8")
    _git(team, "add", "--", str(util_file.relative_to(team)))
    util_bytes_before = util_file.read_bytes()
    util_status_before = _git(
        team,
        "status",
        "--porcelain=v1",
        "--",
        "infra/skills/util",
    ).stdout

    result = git_ops.sync_from_upstream(str(team))

    assert result.ok is True and result.changed is True
    assert result.blocked is False
    assert (team / "infra" / "engine.py").read_text(encoding="utf-8") == "v2\n"
    assert util_file.read_bytes() == util_bytes_before
    assert not (team / "infra" / "skills" / "util" / ".gitkeep").exists()
    assert _git(
        team,
        "status",
        "--porcelain=v1",
        "--",
        "infra/skills/util",
    ).stdout == util_status_before
    assert ":(exclude)infra/skills/util" in result.pathspecs

    committed = git_ops.do_commit(
        str(team),
        "engine sync",
        push=False,
        paths=list(result.pathspecs),
    )

    assert committed.committed is True, committed.detail
    committed_paths = _git(
        team, "show", "--format=", "--name-only", "HEAD"
    ).stdout.splitlines()
    assert "infra/engine.py" in committed_paths
    assert not any(path.startswith("infra/skills/util") for path in committed_paths)
    assert util_file.read_bytes() == util_bytes_before
    assert _git(
        team,
        "status",
        "--porcelain=v1",
        "--",
        "infra/skills/util",
    ).stdout == util_status_before


def test_git_env_disables_interactive_prompts_without_disabling_helpers() -> None:
    env = git_ops.git_env()

    assert env["LC_ALL"] == "C"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "0"
    assert "credential.helper" not in env


def test_http_timeout_options_have_a_one_second_floor() -> None:
    assert "http.lowSpeedTime=1" in git_ops.http_timeout_opts(0)
    assert "http.lowSpeedTime=1" in git_ops.http_timeout_opts(-10)


def test_run_git_env_overrides_are_process_local(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_index = tmp_path / "private-index"
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)

    rc, out, err = git_ops.run_git(
        ["-C", str(repo), "rev-parse", "--git-path", "index"],
        timeout=git_ops.DEFAULT_TIMEOUT,
        env_overrides={"GIT_INDEX_FILE": str(private_index)},
    )

    assert rc == 0, err
    assert Path(out.strip()) == private_index
    assert "GIT_INDEX_FILE" not in os.environ
    rc, normal, err = git_ops.run_git(
        ["-C", str(repo), "rev-parse", "--git-path", "index"],
        timeout=git_ops.DEFAULT_TIMEOUT,
    )
    assert rc == 0, err
    assert Path(normal.strip()) != private_index


def test_repo_scoped_git_ignores_ambient_redirect(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    _git(redirected, "init", ".")
    monkeypatch.setenv("GIT_DIR", str(redirected / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(redirected))

    rc, out, err = git_ops.run_git(
        ["-C", str(repo), "rev-parse", "--show-toplevel"],
        timeout=git_ops.DEFAULT_TIMEOUT,
    )

    assert rc == 0, err
    assert Path(out.strip()).resolve() == repo.resolve()


def test_repo_scoped_git_strips_redirect_env_but_preserves_config_files(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    redirected = tmp_path / "redirected-config"
    redirected.mkdir()
    _git(redirected, "init", ".")
    hostile = {
        "GIT_DIR": str(redirected / ".git"),
        "GIT_WORK_TREE": str(redirected),
        "GIT_COMMON_DIR": str(redirected / ".git"),
        "GIT_INDEX_FILE": str(redirected / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(redirected / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(redirected / "alternate"),
        "GIT_NAMESPACE": "redirected",
        "GIT_SHALLOW_FILE": str(redirected / "shallow"),
        "GIT_GRAFT_FILE": str(redirected / "grafts"),
        "GIT_REPLACE_REF_BASE": "refs/replace-hostile/",
        "GIT_ATTR_SOURCE": "refs/heads/hostile-attributes",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_PARAMETERS": "'core.bare=false'",
        "GIT_CONFIG_KEY_0": "core.bare",
        "GIT_CONFIG_VALUE_0": "false",
    }
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    expected_config = {
        name: os.environ[name]
        for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM")
    }
    child_envs = []
    real_popen = git_ops.subprocess.Popen

    def observing_popen(*args, **kwargs):
        child_envs.append(dict(kwargs.get("env") or {}))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(git_ops.subprocess, "Popen", observing_popen)
    rc, out, err = git_ops.run_git(
        ["-C", str(repo), "rev-parse", "--show-toplevel"],
        timeout=git_ops.DEFAULT_TIMEOUT,
    )

    assert rc == 0, err
    assert Path(out.strip()).resolve() == repo.resolve()
    assert child_envs
    child = child_envs[-1]
    assert set(hostile).isdisjoint(child)
    assert {name: child.get(name) for name in expected_config} == expected_config


def test_is_git_worktree_requires_exact_root(repo: Path) -> None:
    nested = repo / "nested"
    nested.mkdir()

    assert git_ops.is_git_worktree(str(repo)) is True
    assert git_ops.is_git_worktree(str(nested)) is False


def test_do_pull_fast_forwards_and_non_git_is_non_raising(
    behind_clone, tmp_path: Path,
) -> None:
    result = git_ops.do_pull(str(behind_clone.clone))

    assert result.ok is True
    assert (behind_clone.clone / "remote.txt").read_text(encoding="utf-8") == (
        "remote\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_ops.do_pull(str(plain)).ok is False


def test_pull_command_fast_forwards_and_localizes_english(behind_clone) -> None:
    (behind_clone.clone / "team.config.json").write_text(
        json.dumps({"team": {"name": "acme", "locale": "en_US"}}),
        encoding="utf-8",
    )

    result = _run_engine(behind_clone.clone, "pull")

    assert result.returncode == 0, result.stderr
    assert "updated" in result.stdout.lower()
    assert not re.search(r"[가-힣]", result.stdout)
    assert (behind_clone.clone / "remote.txt").is_file()


def test_pull_command_non_git_and_missing_root_are_graceful(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    non_git = _run_engine(plain, "pull")
    missing_root = subprocess.run(
        [sys.executable, str(ENGINE), "pull"],
        cwd=plain,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "Traceback" not in non_git.stderr
    assert missing_root.returncode != 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group probe")
def test_do_pull_timeout_does_not_leave_remote_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "timeout-work"
    work.mkdir()
    _git(work, "init", "-b", "main", ".")
    _configure(work)
    (work / "x").write_text("x\n", encoding="utf-8")
    _git(work, "add", "x")
    _git(work, "commit", "-m", "base")
    _git(work, "remote", "add", "origin", "placeholder")
    env = _hang_remote(tmp_path, work)
    monkeypatch.setenv("PATH", env["PATH"])

    def count_helpers() -> int:
        probe = subprocess.run(
            ["pgrep", "-af", "git-remote-sleep"],
            capture_output=True,
            text=True,
            check=False,
        )
        return len([line for line in probe.stdout.splitlines() if line.strip()])

    before = count_helpers()
    started = time.monotonic()
    result = git_ops.do_pull(str(work), timeout=2)
    elapsed = time.monotonic() - started
    time.sleep(1.5)

    assert result.ok is False
    assert 1.5 <= elapsed < 10
    assert count_helpers() <= before
