"""Public hook boundaries for direct session-log edits."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
KB_GUARD = REPO / "infra" / "hooks" / "kb-write-guard.py"


def _run_guard(root: Path, target: Path, member: str = "bob"):
    hook = root / "infra" / "hooks" / "kb-write-guard.py"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(KB_GUARD, hook)
    (root / ".teammode-active").write_text("", encoding="utf-8")
    payload = {
        "event": "PreToolUse",
        "action": "file_edit",
        "files": [str(target)],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude",
        "raw": {},
    }
    env = {key: value for key, value in os.environ.items() if key != "TEAMMODE_HOME"}
    env["TEAMMODE_MEMBER"] = member
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def test_own_session_log_edit_is_allowed(tmp_path):
    own_dir = tmp_path / "memory" / "team" / "sessions" / "bob"
    own_dir.mkdir(parents=True)

    result = _run_guard(tmp_path, own_dir / "today.md")

    assert result.returncode == 0, result.stderr


def test_other_members_session_log_edit_is_blocked(tmp_path):
    other_dir = tmp_path / "memory" / "team" / "sessions" / "alice"
    other_dir.mkdir(parents=True)

    result = _run_guard(tmp_path, other_dir / "today.md")

    assert result.returncode == 2
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"


def test_symlinked_own_session_directory_is_blocked(tmp_path):
    decisions = tmp_path / "memory" / "team" / "decisions"
    decisions.mkdir(parents=True)
    sessions = tmp_path / "memory" / "team" / "sessions"
    sessions.mkdir(parents=True)
    own_dir = sessions / "bob"
    own_dir.symlink_to(decisions)

    result = _run_guard(tmp_path, own_dir / "today.md")

    assert result.returncode == 2
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"
