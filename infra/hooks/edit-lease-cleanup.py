#!/usr/bin/env python3
"""Release the exact edit mutex token after a failed file tool call."""

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


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict) or _git_ops is None:
        return 0

    if data.get("event") != "PostToolUseFailure":
        return 0
    # Claude failure events carry the exact tool_use_id. Codex does not expose
    # this event; a lost token there is recovered by the core mutex TTL.
    try:
        token = _git_ops.hook_edit_mutex_token(data)
        if token:
            _git_ops.release_edit_mutex(_team_root(), token)
    except Exception:  # noqa: BLE001 — cleanup cannot block lifecycle teardown
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
