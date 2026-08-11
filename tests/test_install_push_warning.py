"""Installer scaffold publication under the simple main-sync contract."""

from __future__ import annotations

import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as _install  # noqa: E402


class _FakeCR:
    def __init__(self, *, pushed: bool, committed: bool, detail: str = ""):
        self.pushed = pushed
        self.committed = committed
        self.ok = committed
        self.detail = detail


def test_scaffold_commit_is_scoped_and_pushes_main(monkeypatch, tmp_path):
    calls = []

    def fake_commit(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeCR(pushed=True, committed=True)

    monkeypatch.setattr(_install._git_ops, "do_commit", fake_commit)
    messages = []

    _install._autocommit_scaffold(tmp_path, "bob", messages.append)

    assert calls == [((str(tmp_path),), {
        "message": "team setup: register bob + memory scaffold [auto]",
        "push": True,
        "paths": ["memory", "team.config.json"],
    })]
    assert messages == ["[push] pushed memory/members to the team repo."]


def test_scaffold_sync_failure_preserves_commit_and_redacts_output(
        monkeypatch, tmp_path):
    raw = (
        "fatal https://alice:password@example.com/repo "
        "client_secret=oauth-secret Authorization: Bearer bearer-secret\x1b[31m")
    monkeypatch.setattr(
        _install._git_ops, "do_commit",
        lambda *_args, **_kwargs: _FakeCR(
            pushed=False, committed=True, detail=raw))
    messages = []

    _install._autocommit_scaffold(tmp_path, "bob", messages.append)

    rendered = "\n".join(messages)
    assert "committed" in rendered and "main sync failed" in rendered
    assert "pull --root" in rendered
    assert "password" not in rendered
    assert "oauth-secret" not in rendered
    assert "bearer-secret" not in rendered
    assert "\x1b" not in rendered
    assert "[redacted]" in rendered


def test_scaffold_no_commit_reports_uncommitted_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _install._git_ops, "do_commit",
        lambda *_args, **_kwargs: _FakeCR(
            pushed=False, committed=False, detail="nothing to commit"))
    messages = []

    _install._autocommit_scaffold(tmp_path, "bob", messages.append)

    assert len(messages) == 1
    assert "commit failed" in messages[0]
    assert "nothing to commit" in messages[0]
    assert "pushed" not in messages[0]
