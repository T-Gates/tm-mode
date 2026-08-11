"""PostToolUse auto-commit tests for the simple main-sync contract."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
AUTO_COMMIT = INFRA / "hooks" / "auto-commit.py"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

import git_ops  # noqa: E402


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "simple_auto_commit_under_test", AUTO_COMMIT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeGitOps:
    def __init__(self, results):
        self.results = list(results if isinstance(results, list) else [results])
        self.calls = []
        self.releases = []

    @staticmethod
    def hook_edit_mutex_token(_data):
        return "a" * 64

    def release_edit_mutex(self, root, token):
        self.releases.append((root, token))
        return True

    def do_commit(self, root, message, push=False, paths=None, **kwargs):
        self.calls.append({
            "root": root,
            "message": message,
            "push": push,
            "paths": paths,
            "kwargs": kwargs,
        })
        return self.results.pop(0)

    @staticmethod
    def sanitize_git_detail(detail):
        return git_ops.sanitize_git_detail(detail)


def _active_root(tmp_path: Path) -> Path:
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    (root / ".teammode-active").write_text("", encoding="utf-8")
    (root / "x.md").write_text("edited\n", encoding="utf-8")
    return root


def _run(module, fake, root: Path, monkeypatch: pytest.MonkeyPatch, *, lang="ko"):
    monkeypatch.setattr(module, "_git_ops", fake)
    monkeypatch.setattr(module, "_hook_lang", lambda _root: lang)
    monkeypatch.setenv("TEAMMODE_HOME", str(root))
    payload = {
        "event": "PostToolUse",
        "action": "file_edit",
        "files": [str(root / "x.md")],
        "agent": "claude",
        "session_id": "alice-session",
        "tool_use_id": "tool-a",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return module.main()


def test_success_uses_scoped_push_and_releases_exact_mutex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _active_root(tmp_path)
    fake = _FakeGitOps(git_ops.CommitResult(
        ok=True, committed=True, pushed=True, detail="main synchronized"))

    assert _run(_load_hook(), fake, root, monkeypatch) == 0

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["push"] is True
    assert call["paths"] == [":(literal)x.md"]
    assert call["kwargs"] == {"_edit_token": "a" * 64}
    assert fake.releases == [(str(root), "a" * 64)]


def test_sync_failure_is_sanitized_and_commit_remains_nonblocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    root = _active_root(tmp_path)
    raw = (
        "committed; https://alice:credential@example.invalid/repo "
        "Authorization: Bearer " + "bearer" + "-credential\x1b[31m")
    fake = _FakeGitOps(git_ops.CommitResult(
        ok=True, committed=True, pushed=False, detail=raw))

    assert _run(_load_hook(), fake, root, monkeypatch, lang="en") == 0
    stderr = capsys.readouterr().err

    assert "credential" not in stderr
    assert "\x1b" not in stderr
    assert "[redacted]" in stderr
    assert not re.search(r"[가-힣]", stderr)
    assert fake.releases == [(str(root), "a" * 64)]


def test_index_lock_failure_retries_once_with_same_mutex_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _active_root(tmp_path)
    fake = _FakeGitOps([
        git_ops.CommitResult(
            ok=False, committed=False, pushed=False,
            detail="fatal: index.lock already exists"),
        git_ops.CommitResult(
            ok=True, committed=True, pushed=True, detail="main synchronized"),
    ])
    module = _load_hook()
    monkeypatch.setattr(module._time, "sleep", lambda _seconds: None)

    assert _run(module, fake, root, monkeypatch) == 0

    assert len(fake.calls) == 2
    assert all(call["push"] is True for call in fake.calls)
    assert all(
        call["kwargs"] == {"_edit_token": "a" * 64}
        for call in fake.calls
    )
    assert fake.releases == [(str(root), "a" * 64)]


def test_nothing_to_commit_is_silent_but_releases_mutex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    root = _active_root(tmp_path)
    fake = _FakeGitOps(git_ops.CommitResult(
        ok=False, committed=False, pushed=False, detail="nothing to commit"))

    assert _run(_load_hook(), fake, root, monkeypatch) == 0

    assert capsys.readouterr().err == ""
    assert fake.releases == [(str(root), "a" * 64)]


def test_valid_root_teammode_off_does_not_commit_and_releases_mutex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _active_root(tmp_path)
    (root / ".teammode-active").unlink()
    fake = _FakeGitOps([])

    assert _run(_load_hook(), fake, root, monkeypatch) == 0

    assert fake.calls == []
    assert fake.releases == [(str(root), "a" * 64)]


def test_stale_teammode_home_warns_before_git_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    missing = tmp_path / "moved-away"
    fake = _FakeGitOps([])

    assert _run(_load_hook(), fake, missing, monkeypatch, lang="en") == 0

    stderr = capsys.readouterr().err
    assert "TEAMMODE_HOME" in stderr
    assert len(stderr.strip().splitlines()) == 1
    assert fake.calls == []
