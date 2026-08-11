"""Read-only sync diagnostics and machine-local error state."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "infra") not in sys.path:
    sys.path.insert(0, str(ROOT / "infra"))

import git_ops  # noqa: E402


def test_ahead_behind_keeps_public_diagnostic_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git_ops, "_ahead_behind_raw", lambda _root, _timeout: (3, 2, True))

    assert git_ops.ahead_behind("/acme/team") == (3, 2)


def test_ahead_behind_unknown_upstream_is_harmless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git_ops, "_ahead_behind_raw", lambda _root, _timeout: (0, 0, False))

    assert git_ops.ahead_behind("/acme/team") == (0, 0)


def test_last_sync_errors_are_isolated_per_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    first = tmp_path / "alice-team"
    second = tmp_path / "bob-team"

    assert git_ops.write_last_sync_error(str(first), "first failure") is True
    assert git_ops.write_last_sync_error(str(second), "second failure") is True
    assert git_ops.read_last_sync_error(str(first)) == "first failure"
    assert git_ops.read_last_sync_error(str(second)) == "second failure"

    assert git_ops.clear_last_sync_error(str(first)) is True
    assert git_ops.read_last_sync_error(str(first)) == ""
    assert git_ops.read_last_sync_error(str(second)) == "second failure"


def test_last_sync_error_reader_scrubs_legacy_unsanitized_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "team"
    raw = (
        "fatal https://alice:credential@example.invalid/repo "
        "Authorization: Bearer " + "bearer" + "-credential\x1b[31m")
    path = git_ops._last_sync_error_path(str(root))
    assert git_ops._write_private_text(path, raw) is True

    rendered = git_ops.read_last_sync_error(str(root))

    assert "credential" not in rendered
    assert "\x1b" not in rendered
    assert "[redacted]" in rendered
