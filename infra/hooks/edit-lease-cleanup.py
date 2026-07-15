#!/usr/bin/env python3
"""Recover edit leases at exact failure or verified terminal scope boundaries."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import git_ops as _git_ops
except ImportError:
    _git_ops = None


def _team_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _terminal_cleanup_allowed(data: dict) -> bool:
    """Reject broad/ambiguous terminal boundaries before touching markers."""
    event = data.get("event")
    if event == "SubagentStop":
        return bool(str(data.get("agent_id") or "").strip())
    if data.get("agent") != "claude":
        return True
    if event != "Stop":
        return True
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    background = raw.get("background_tasks")
    if background is None:
        background = data.get("background_tasks")
    return isinstance(background, list) and not background


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict) or _git_ops is None:
        return 0

    event = data.get("event")
    root = _team_root()
    if event == "PostToolUseFailure":
        # Failure carries the exact tool_use_id on Claude.  Codex deliberately
        # does not register this unsupported event.
        owner = _git_ops.hook_edit_lease_owner(data)
        if owner:
            _git_ops.end_hook_edit_lease(root, owner)
        return 0

    if event not in {"Stop", "SubagentStop"}:
        return 0
    if not _terminal_cleanup_allowed(data):
        return 0
    scope = _git_ops.hook_edit_lease_scope(data)
    agent = str(data.get("agent") or "").strip().lower()
    runtime = _git_ops._current_hook_runtime_identity(agent)
    if scope and runtime is not None:
        _git_ops.end_hook_edit_leases_for_scope(root, scope, runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
