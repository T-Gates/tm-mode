import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as _install  # noqa: E402


class _FakeCR:
    def __init__(self, pushed, committed, detail):
        self.pushed = pushed
        self.committed = committed
        self.ok = committed
        self.detail = detail


def test_push_failure_writes_sync_warning(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(_install._git_ops, "do_commit",
                        lambda *a, **k: _FakeCR(False, True, "committed; push timeout"))
    monkeypatch.setattr(_install._git_ops, "write_sync_warning",
                        lambda root, detail: calls.__setitem__("warn", (root, detail)))
    # Stub with the same arity as the real signature (team_root required) —
    # a zero-arg lambda let install.py's missing-argument call pass silently,
    # masking the real TypeError crash.
    monkeypatch.setattr(_install._git_ops, "clear_sync_warning",
                        lambda team_root: calls.__setitem__("clear", team_root))
    msgs = []
    _install._autocommit_scaffold(tmp_path, "bob", msgs.append)
    assert calls["warn"] == (str(tmp_path), "committed; push timeout")
    assert "clear" not in calls
    assert any("committed; push timeout" in m for m in msgs)  # detail 표면화


def test_push_failure_redacts_credentials_before_marker_and_console(
        monkeypatch, tmp_path):
    calls = {}
    raw = ("fatal https://alice:password@example.com/repo "
           "client_secret=oauth-secret Authorization: Bearer bearer-secret")
    monkeypatch.setattr(_install._git_ops, "do_commit",
                        lambda *a, **k: _FakeCR(False, True, raw))
    monkeypatch.setattr(_install._git_ops, "write_sync_warning",
                        lambda root, detail: calls.__setitem__("warn", detail))
    msgs = []
    _install._autocommit_scaffold(tmp_path, "bob", msgs.append)
    rendered = calls["warn"] + "\n" + "\n".join(msgs)
    assert "password" not in rendered
    assert "oauth-secret" not in rendered
    assert "bearer-secret" not in rendered
    assert "[redacted]" in rendered


def test_push_success_clears_warning(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(_install._git_ops, "do_commit",
                        lambda *a, **k: _FakeCR(True, True, ""))
    monkeypatch.setattr(_install._git_ops, "write_sync_warning",
                        lambda root, detail: calls.__setitem__("warn", True))
    monkeypatch.setattr(_install._git_ops, "clear_sync_warning",
                        lambda team_root: calls.__setitem__("unsafe_clear", team_root))
    monkeypatch.setattr(
        _install._git_ops, "clear_sync_warning_if_fully_published",
        lambda team_root: calls.__setitem__("clear", team_root) or True)
    _install._autocommit_scaffold(tmp_path, "bob", lambda m: None)
    # On push success, only the publication-aware cleanup may clear this root.
    assert calls.get("clear") == str(tmp_path)
    assert "unsafe_clear" not in calls
    assert "warn" not in calls


def test_push_success_preserves_warning_when_other_branch_is_pending(
        monkeypatch, tmp_path):
    """설치 push 성공도 다른 branch pending의 warning을 지우면 안 된다."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                   check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"],
                   check=True)
    base_branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"], check=True,
        capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "pending-work"],
                   check=True)
    assert _install._git_ops.write_push_pending(str(repo)) is True
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", base_branch],
                   check=True)
    _install._git_ops.write_sync_warning(str(repo), "other branch not published")

    monkeypatch.setattr(_install._git_ops, "do_commit",
                        lambda *a, **k: _FakeCR(True, True, ""))
    monkeypatch.setattr(_install._git_ops, "_ahead_behind_raw",
                        lambda _root, _timeout: (0, 0, True))

    _install._autocommit_scaffold(repo, "bob", lambda _message: None)

    assert _install._git_ops.read_sync_warning(str(repo)) == (
        "other branch not published")
