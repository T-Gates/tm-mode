"""Failure/Stop edit-lease recovery without cross-session lease deletion."""

from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops as go  # noqa: E402

CLEANUP = REPO / "infra" / "hooks" / "edit-lease-cleanup.py"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "infra" / "hooks").mkdir(parents=True)
    shutil.copy2(REPO / "infra" / "git_ops.py", root / "infra" / "git_ops.py")
    shutil.copy2(CLEANUP, root / "infra" / "hooks" / CLEANUP.name)
    return root


def _payload(*, agent="codex", session="session-a", turn="turn-a",
             tool="tool-a", agent_id=""):
    data = {
        "agent": agent,
        "session_id": session,
        "turn_id": turn,
        "tool_use_id": tool,
    }
    if agent_id:
        data["agent_id"] = agent_id
    return data


def _runtime(pid: int, token: str):
    return {
        "pid": pid,
        "started": token,
        "executable": (f"{pid:064x}")[-64:],
    }


def _begin(root: Path, payload: dict, runtime: dict | None):
    metadata = go.hook_edit_lease_metadata(
        payload, _runtime_identity=runtime)
    assert metadata is not None
    ok, detail = go.begin_hook_edit_lease(
        str(root), metadata["owner"], metadata=metadata)
    assert ok, detail
    return metadata


def test_claude_failure_hook_releases_only_exact_tool_owner(repo, monkeypatch):
    monkeypatch.setattr(go, "_hook_runtime_liveness", lambda _runtime: True)
    own = _payload(agent="claude", tool="failed-tool")
    foreign = _payload(
        agent="claude", session="session-b", turn="turn-b",
        tool="active-tool")
    own_meta = _begin(repo, own, _runtime(101, "start-a"))
    foreign_meta = _begin(repo, foreign, _runtime(202, "start-b"))
    with go._edit_gate(str(repo), 0.2) as (acquired, detail):
        assert acquired, detail
        assert go._active_edit_lease_owners_locked(str(repo)) == {
            own_meta["owner"], foreign_meta["owner"]}

    failed = {**own, "event": "PostToolUseFailure", "action": "file_edit"}
    proc = subprocess.run(
        [sys.executable, str(repo / "infra" / "hooks" / CLEANUP.name)],
        input=json.dumps(failed),
        text=True, capture_output=True,
        env={**os.environ, "TEAMMODE_HOME": str(repo)}, check=False)

    assert proc.returncode == 0
    assert go.end_hook_edit_lease(str(repo), own_meta["owner"]) is False
    assert go.end_hook_edit_lease(str(repo), foreign_meta["owner"]) is True


def test_stop_cleanup_is_exact_scope_and_runtime(repo, monkeypatch):
    runtime_a = _runtime(301, "start-a")
    runtime_b = _runtime(302, "start-b")
    monkeypatch.setattr(go, "_hook_runtime_liveness", lambda _runtime: True)
    same = _begin(repo, _payload(tool="failed-a"), runtime_a)
    other_turn = _begin(
        repo, _payload(turn="turn-b", tool="active-other-turn"), runtime_a)
    other_runtime = _begin(
        repo, _payload(tool="active-other-runtime"), runtime_b)
    removed = go.end_hook_edit_leases_for_scope(
        str(repo), same["scope"], runtime_a)

    assert removed == 1
    assert go.end_hook_edit_lease(str(repo), same["owner"]) is False
    assert go.end_hook_edit_lease(str(repo), other_turn["owner"]) is True
    assert go.end_hook_edit_lease(str(repo), other_runtime["owner"]) is True


def test_dead_runtime_is_pruned_but_active_runtime_is_preserved(repo, monkeypatch):
    dead_runtime = _runtime(401, "dead")
    live_runtime = _runtime(402, "live")
    dead = _begin(repo, _payload(tool="dead-tool"), dead_runtime)
    live = _begin(repo, _payload(tool="live-tool"), live_runtime)

    monkeypatch.setattr(
        go, "_hook_runtime_liveness",
        lambda runtime: runtime["pid"] == live_runtime["pid"])
    with go._edit_gate(str(repo), 0.2) as (acquired, detail):
        assert acquired, detail
        owners = go._active_edit_lease_owners_locked(str(repo))

    assert owners == {live["owner"]}
    assert go.end_hook_edit_lease(str(repo), dead["owner"]) is False
    assert go.end_hook_edit_lease(str(repo), live["owner"]) is True


def test_unknown_runtime_liveness_fails_closed(repo, monkeypatch):
    runtime = _runtime(501, "unknown")
    marker = _begin(repo, _payload(tool="unknown-tool"), runtime)
    monkeypatch.setattr(go, "_hook_runtime_liveness", lambda _runtime: None)

    with go._edit_gate(str(repo), 0.2) as (acquired, detail):
        assert acquired, detail
        owners = go._active_edit_lease_owners_locked(str(repo))

    assert owners == {marker["owner"]}
    assert go.end_hook_edit_lease(str(repo), marker["owner"]) is True


def test_crashed_atomic_temp_is_pruned_without_blocking_new_edits(repo):
    payload = _payload(tool="next-tool")
    metadata = go.hook_edit_lease_metadata(payload, _runtime_identity=None)
    assert metadata is not None
    directory = go._edit_lease_dir(str(repo))
    assert directory is not None
    residue = directory / f".lease-{metadata['owner']}.crash42.tmp"
    residue.write_text("partial", encoding="utf-8")
    residue.chmod(0o600)

    ok, detail = go.begin_hook_edit_lease(
        str(repo), metadata["owner"], metadata=metadata)

    assert ok, detail
    assert not residue.exists()
    assert go.end_hook_edit_lease(str(repo), metadata["owner"]) is True


def test_same_process_start_with_executable_drift_is_unknown(monkeypatch):
    runtime = _runtime(601, "same-start")
    current = {
        **runtime,
        "parent": 1,
        "path": "/updated/codex",
        "executable": "f" * 64,
    }
    monkeypatch.setattr(go, "_process_identity", lambda _pid: current)

    assert go._hook_runtime_liveness(runtime) is None


@pytest.mark.skipif(os.name != "posix" or sys.platform.startswith("linux"),
                    reason="macOS/BSD ps contract")
def test_process_birth_probe_forces_stable_c_locale(monkeypatch):
    observed = {}

    def fake_run(*_args, **kwargs):
        observed.update(kwargs.get("env") or {})
        return SimpleNamespace(
            returncode=0,
            stdout="700 1 Tue Jul 14 16:24:58 2026 /usr/local/bin/codex\n")

    monkeypatch.setattr(go.subprocess, "run", fake_run)
    identity = go._process_identity(700)

    assert isinstance(identity, dict)
    assert identity["started"] == "Tue Jul 14 16:24:58 2026"
    assert observed["LC_ALL"] == "C" and observed["LANG"] == "C"
    assert observed["TZ"] == "UTC"


def test_claude_scope_without_turn_is_agent_narrow():
    root = _payload(agent="claude", turn="", tool="root-tool")
    subagent = _payload(
        agent="claude", turn="", tool="sub-tool", agent_id="agent-1")

    assert go.hook_edit_lease_scope(root)
    assert go.hook_edit_lease_scope(subagent)
    assert go.hook_edit_lease_scope(root) != go.hook_edit_lease_scope(subagent)


def test_claude_stop_defers_while_background_tasks_exist():
    spec = importlib.util.spec_from_file_location("lease_cleanup", CLEANUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._terminal_cleanup_allowed({
        "event": "Stop", "agent": "claude",
        "raw": {"background_tasks": [{"type": "subagent"}]},
    }) is False
    assert module._terminal_cleanup_allowed({
        "event": "Stop", "agent": "claude",
        "raw": {"background_tasks": []},
    }) is True
    assert module._terminal_cleanup_allowed({
        "event": "Stop", "agent": "claude", "raw": {},
    }) is False
    assert module._terminal_cleanup_allowed({
        "event": "Stop", "agent": "claude",
        "raw": {"background_tasks": "unknown"},
    }) is False
    assert module._terminal_cleanup_allowed({
        "event": "SubagentStop", "agent": "claude", "agent_id": "",
    }) is False
    assert module._terminal_cleanup_allowed({
        "event": "SubagentStop", "agent": "codex", "agent_id": "",
    }) is False


def test_manifest_and_agent_event_maps_install_failure_and_stop_cleanup():
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text())
    shapes = {(entry["event"], entry["script"]) for entry in manifest}
    assert ("PostToolUseFailure", "edit-lease-cleanup.py") in shapes
    assert ("Stop", "edit-lease-cleanup.py") in shapes
    assert ("SubagentStop", "edit-lease-cleanup.py") in shapes

    claude = json.loads(
        (REPO / "infra" / "agents" / "claude" / "events.json").read_text())
    codex = json.loads(
        (REPO / "infra" / "agents" / "codex" / "events.json").read_text())
    assert claude["events"]["PostToolUseFailure"] == "PostToolUseFailure"
    assert codex["events"]["PostToolUseFailure"] is None
    for event in ("Stop", "SubagentStop"):
        assert claude["events"][event] == event
        assert codex["events"][event] == event
