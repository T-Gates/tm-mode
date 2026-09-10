"""Removed lease cleanup is unregistered and harmless for older installations."""
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "infra/hooks/edit-lease-cleanup.py"


def test_manifest_does_not_register_obsolete_lease_cleanup():
    entries = json.loads((ROOT / "infra/hooks/manifest.json").read_text())
    assert not any(entry.get("script") == HOOK.name for entry in entries)


def test_old_failure_hook_is_noop_without_reading_or_changing_state(monkeypatch):
    spec = importlib.util.spec_from_file_location("old_cleanup", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "event": "PostToolUseFailure", "agent": "claude",
        "session_id": "alice-session", "tool_use_id": "alice-tool"})))
    def forbidden(*args, **kwargs):
        pytest.fail("An obsolete hook must not inspect or alter state.")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    assert hook.main() == 0
