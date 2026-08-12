"""Simple edit-mutex lifecycle tests for issue #128."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
HOOK = INFRA / "hooks" / "edit-lease-cleanup.py"
MANIFEST = INFRA / "hooks" / "manifest.json"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

import git_ops  # noqa: E402


def _payload(tool_use_id: str, *, event: str = "PostToolUseFailure") -> dict:
    return {
        "event": event,
        "agent": "claude",
        "session_id": "alice-session",
        "tool_use_id": tool_use_id,
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "-C", str(root), "init", "-b", "main", "."],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return root


def _load_cleanup_module():
    spec = importlib.util.spec_from_file_location(
        "edit_mutex_cleanup_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hook_edit_mutex_token_is_exact_to_one_tool_call() -> None:
    first = git_ops.hook_edit_mutex_token(_payload("tool-a"))
    same = git_ops.hook_edit_mutex_token(_payload("tool-a"))
    other = git_ops.hook_edit_mutex_token(_payload("tool-b"))

    assert first == same
    assert len(first) == 64
    assert first != other
    assert git_ops.hook_edit_mutex_token({}) == ""


def test_mutex_allows_only_owner_and_requires_exact_release(repo: Path) -> None:
    first = git_ops.hook_edit_mutex_token(_payload("tool-a"))
    second = git_ops.hook_edit_mutex_token(_payload("tool-b"))

    assert git_ops.acquire_edit_mutex(str(repo), first) is True
    assert git_ops.owns_edit_mutex(str(repo), first) is True
    assert git_ops.acquire_edit_mutex(str(repo), second) is False
    assert git_ops.release_edit_mutex(str(repo), second) is False
    assert git_ops.owns_edit_mutex(str(repo), first) is True
    assert git_ops.release_edit_mutex(str(repo), first) is True
    assert git_ops.acquire_edit_mutex(str(repo), second) is True


def test_stale_mutex_is_reclaimed_after_ttl(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_000.0]
    monkeypatch.setattr(git_ops.time, "time", lambda: now[0])
    first = git_ops.hook_edit_mutex_token(_payload("tool-a"))
    second = git_ops.hook_edit_mutex_token(_payload("tool-b"))

    assert git_ops.acquire_edit_mutex(str(repo), first) is True
    now[0] += git_ops._EDIT_MUTEX_TTL_SECONDS + 1

    assert git_ops.owns_edit_mutex(str(repo), first) is False
    assert git_ops.acquire_edit_mutex(str(repo), second) is True
    assert git_ops.owns_edit_mutex(str(repo), second) is True


def test_failure_hook_releases_only_matching_tool_token(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_cleanup_module()
    monkeypatch.setattr(module, "_team_root", lambda: str(repo))
    owner_payload = _payload("tool-a")
    owner = git_ops.hook_edit_mutex_token(owner_payload)
    assert git_ops.acquire_edit_mutex(str(repo), owner) is True

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload("tool-b"))))
    assert module.main() == 0
    assert git_ops.owns_edit_mutex(str(repo), owner) is True

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(owner_payload)))
    assert module.main() == 0
    assert git_ops.owns_edit_mutex(str(repo), owner) is False


def test_manifest_uses_cleanup_only_for_exact_failure_event() -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cleanup_events = [
        entry["event"]
        for entry in entries
        if entry.get("script") == "edit-lease-cleanup.py"
    ]

    assert cleanup_events == ["PostToolUseFailure"]
