"""Agent raw PostToolUse payload -> normalize -> auto-commit publication E2E."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _clone_with_upstream(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    _git(tmp_path, "clone", str(origin), str(work))
    seed = work / "README.md"
    seed.write_text("seed\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "-u", "origin", "HEAD")
    return origin, work


def _raw_posttooluse(agent: str, target: Path, team_root: Path) -> dict:
    if agent == "claude":
        return {
            "hook_event_name": "PostToolUse",
            "session_id": "claude-publication-e2e",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target)},
        }

    relative_target = target.relative_to(team_root).as_posix()
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {relative_target}\n"
        "@@\n"
        "+published\n"
        "*** End Patch\n"
    )
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "codex-publication-e2e",
        "tool_name": "apply_patch",
        "tool_input": {"command": patch},
    }


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_raw_posttooluse_publishes_through_normalize(agent: str, tmp_path: Path):
    origin, work = _clone_with_upstream(tmp_path)
    (work / ".teammode-active").write_text("on\n", encoding="utf-8")
    target = work / "memory" / "team" / "sessions" / agent / "2026-07-11.md"
    target.parent.mkdir(parents=True)
    session_text = (
        "---\n"
        f"author: {agent}\n"
        "date: 2026-07-11\n"
        f"summary: {agent} publication E2E\n"
        "---\n\n"
        f"published by {agent}\n"
    )
    target.write_text(session_text, encoding="utf-8")

    state_home = tmp_path / "state"
    state_home.mkdir()
    env = _git_env()
    env.update({
        "TEAMMODE_HOME": str(work),
        "XDG_STATE_HOME": str(state_home),
        "TEAMMODE_DISABLE_PUSH_WORKER": "1",
    })
    normalize = REPO / "infra" / "agents" / agent / "normalize.py"
    proc = subprocess.run(
        [PY, str(normalize), "auto-commit.py"],
        input=json.dumps(_raw_posttooluse(agent, target, work)),
        capture_output=True,
        text=True,
        cwd=work,
        env=env,
        timeout=90,
    )

    assert proc.returncode == 0, proc.stderr
    assert "auto-commit" in _git(work, "log", "-1", "--format=%s").stdout
    assert _git(
        tmp_path,
        "--git-dir", str(origin),
        "show", f"refs/heads/main:{target.relative_to(work).as_posix()}",
    ).stdout == session_text
    assert _git(work, "rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.strip() == "0\t0"
