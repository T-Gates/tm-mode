"""SessionStart integration with immediate main sync and last-sync-error."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "infra" / "hooks" / "session-start.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "session_start_simple_sync_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeGitOps:
    NET_TIMEOUT = 10
    DEFAULT_TIMEOUT = 2

    def __init__(self, result, *, warning="", ahead=0, behind=0):
        self.result = result
        self.warning = warning
        self.ahead = ahead
        self.behind = behind
        self.sync_calls = []

    def sync_main(self, team_root, **kwargs):
        self.sync_calls.append((team_root, kwargs))
        return self.result

    @staticmethod
    def sanitize_git_detail(detail):
        return str(detail)

    def read_last_sync_error(self, _team_root):
        return self.warning

    def ahead_behind(self, _team_root, timeout=2):
        return self.ahead, self.behind

    @staticmethod
    def read_upstream_notice(_team_root, timeout=2):
        return ""


class _FakeEngine:
    @staticmethod
    def _read_index(_root):
        return "# INDEX\n"

    @staticmethod
    def _collect_members(_root):
        return []

    @staticmethod
    def _read_local_notice(_root):
        return ""


def test_session_start_sync_is_immediate_and_not_pull_throttled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_hook()
    fake = _FakeGitOps(SimpleNamespace(ok=True, action="up-to-date", detail=""))
    monkeypatch.setattr(module, "_git_ops", fake)

    module._maybe_sync_main(str(tmp_path))
    module._maybe_sync_main(str(tmp_path))

    assert len(fake.sync_calls) == 2
    assert all(call[0] == str(tmp_path) for call in fake.sync_calls)
    assert all(call[1]["timeout"] == fake.NET_TIMEOUT for call in fake.sync_calls)
    assert not hasattr(module, "_pull_state_path")


def test_session_start_sync_failure_is_advisory_and_localized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    module = _load_hook()
    fake = _FakeGitOps(SimpleNamespace(
        ok=False, action="fetch-failed", detail="network unavailable"))
    monkeypatch.setattr(module, "_git_ops", fake)
    monkeypatch.setattr(module, "_hook_lang", lambda _root: "en")

    module._maybe_sync_main(str(tmp_path))

    stderr = capsys.readouterr().err
    assert "network unavailable" in stderr
    assert not re.search(r"[가-힣]", stderr)


def test_session_start_sync_success_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    module = _load_hook()
    fake = _FakeGitOps(SimpleNamespace(
        ok=True, action="up-to-date", detail="main synchronized"))
    monkeypatch.setattr(module, "_git_ops", fake)

    module._maybe_sync_main(str(tmp_path))

    assert capsys.readouterr().err == ""


def test_context_surfaces_last_sync_error_without_mutating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_hook()
    fake = _FakeGitOps(
        SimpleNamespace(ok=False),
        warning="push origin main failed",
        ahead=1,
        behind=2,
    )
    monkeypatch.setattr(module, "_git_ops", fake)
    monkeypatch.setattr(module, "_engine", _FakeEngine)
    monkeypatch.setattr(module, "_slog_rules_mod", None)

    context = module._build_context(tmp_path, "en")

    assert "push origin main failed" in context
    assert "ahead 1 / behind 2" in context
    assert fake.warning == "push origin main failed"
