"""Bounded network defaults for the simplified synchronization path."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

import git_ops  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    isolated = tmp_path_factory.mktemp("git-net-iso")
    empty_config = isolated / "empty-gitconfig"
    empty_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(isolated))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def _default(function, parameter):
    return inspect.signature(function).parameters[parameter].default


def _load_session_start():
    path = INFRA / "hooks" / "session-start.py"
    spec = importlib.util.spec_from_file_location(
        "session_start_budget_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_network_timeout_exceeds_local_timeout() -> None:
    assert git_ops.NET_TIMEOUT > git_ops.DEFAULT_TIMEOUT >= 1


@pytest.mark.parametrize("function", [
    git_ops.do_pull,
    git_ops.sync_main,
    git_ops.do_commit,
    git_ops.fetch_upstream,
    git_ops.sync_from_upstream,
])
def test_network_entrypoints_default_to_network_timeout(function) -> None:
    assert _default(function, "timeout") == git_ops.NET_TIMEOUT


@pytest.mark.parametrize("function", [
    git_ops.ahead_behind,
    git_ops.has_common_ancestor,
    git_ops.count_behind,
    git_ops.upstream_changes,
    git_ops.detect_default_branch,
    git_ops.diff_paths,
    git_ops.read_upstream_notice,
    git_ops.plan_validation_sync,
    git_ops.apply_validation_sync,
    git_ops.strip_template_workflows,
])
def test_local_update_and_validation_entrypoints_keep_local_timeout(function) -> None:
    assert _default(function, "timeout") == git_ops.DEFAULT_TIMEOUT


def test_git_test_environment_does_not_read_host_config() -> None:
    for scope in ("--global", "--system"):
        result = subprocess.run(
            ["git", "config", scope, "--list"],
            capture_output=True,
            text=True,
            env={**os.environ},
            check=False,
        )
        assert result.stdout.strip() == ""


def test_push_total_budget_is_finite_and_manifest_has_cleanup_room() -> None:
    assert git_ops.PUSH_TOTAL_BUDGET > git_ops.NET_TIMEOUT
    assert git_ops.PUSH_TOTAL_BUDGET < 60
    entries = json.loads(
        (INFRA / "hooks" / "manifest.json").read_text(encoding="utf-8"))
    auto_commit = next(
        entry for entry in entries if entry.get("script") == "auto-commit.py")
    assert auto_commit["timeout"] > git_ops.PUSH_TOTAL_BUDGET


def test_do_commit_uses_local_timeouts_then_forwards_network_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    sync_calls = []

    monkeypatch.setattr(git_ops, "is_git_worktree", lambda _root: True)
    monkeypatch.setattr(
        git_ops, "_current_branch", lambda _root, _timeout: "main")

    @contextmanager
    def edit_scope(_root, token):
        yield True, token or "owned-token"

    monkeypatch.setattr(git_ops, "_edit_mutex_scope", edit_scope)

    def fake_run_git(args, timeout, **_kwargs):
        calls.append((list(args), timeout))
        if "add" in args:
            return 0, "", ""
        if "diff" in args and "--cached" in args:
            return 1, "", ""
        if "commit" in args:
            return 0, "committed", ""
        raise AssertionError(args)

    def fake_sync(root, timeout, *, deadline, _edit_token):
        sync_calls.append((root, timeout, deadline, _edit_token))
        return git_ops.MainSyncResult(
            ok=True, action="up-to-date", detail="main synchronized")

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    monkeypatch.setattr(git_ops, "sync_main", fake_sync)

    result = git_ops.do_commit(
        str(tmp_path), "message", push=True, timeout=17,
        paths=["memory/session.md"],
    )

    assert result.ok is True and result.pushed is True
    assert calls and all(timeout == git_ops.DEFAULT_TIMEOUT for _args, timeout in calls)
    assert len(sync_calls) == 1
    assert sync_calls[0][0] == str(tmp_path)
    assert sync_calls[0][1] == 17
    assert sync_calls[0][3] == "owned-token"


def test_remaining_timeout_never_exceeds_cap_or_goes_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git_ops.time, "monotonic", lambda: 100.0)

    assert git_ops._remaining_timeout(120.0, 5.0) == 5.0
    assert git_ops._remaining_timeout(102.0, 5.0) == 2.0
    assert git_ops._remaining_timeout(99.0, 5.0) == 0.0


def test_session_start_budget_finishes_before_manifest_timeout() -> None:
    module = _load_session_start()
    entries = json.loads(
        (INFRA / "hooks" / "manifest.json").read_text(encoding="utf-8"))
    session_start = next(
        entry for entry in entries if entry.get("script") == "session-start.py")

    assert module._SESSION_CONTEXT_RESERVE > 0
    assert module._SESSION_START_TOTAL_BUDGET < session_start["timeout"]


def test_origin_sync_has_no_pull_throttle_but_product_fetch_keeps_one() -> None:
    module = _load_session_start()

    assert not hasattr(module, "_pull_state_path")
    assert module._UPSTREAM_FETCH_THROTTLE_SECONDS == 86_400
    assert callable(module._maybe_fetch_upstream)
