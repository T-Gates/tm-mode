"""이슈 #23 → #45 — auto-commit 의 push 가시화 계약.

#45 async push 전환으로 훅은 sync-warning 을 직접 쓰지/지우지 않는다 —
커밋 성공 시 **pending ledger 기록 + worker kick** 까지가 훅 몫이고,
실패 가시화(sync-warning)는 push-worker, 회복 판정은 session-start 가 담당한다.
커밋 거동 자체는 불변(비차단 유지).
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AUTO_COMMIT = REPO / "infra" / "hooks" / "auto-commit.py"
sys.path.insert(0, str(REPO / "infra"))

import git_ops as go  # noqa: E402


def _load_hook():
    spec = importlib.util.spec_from_file_location("auto_commit_mod", AUTO_COMMIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeGitOps:
    """do_commit 결과를 고정해 가시화 분기만 검증. write/clear 호출을 기록."""

    def __init__(self, result):
        self._result = result
        self.warnings = []
        self.cleared = 0

    def do_commit(self, root, message, push=False, paths=None):
        self.push_arg = push
        return self._result

    def write_sync_warning(self, root, detail):
        self.warnings.append((root, detail))

    def clear_sync_warning(self, root):
        self.cleared += 1
        self.cleared_root = root

    # ── #45 pending ledger 표면 (훅이 호출) ──
    def read_push_pending(self, root):
        return ""

    def write_push_pending(self, root):
        self.pending_writes = getattr(self, "pending_writes", 0) + 1


def _run(mod, fake, root, monkeypatch):
    monkeypatch.setattr(mod, "_git_ops", fake)
    monkeypatch.setenv("TEAMMODE_HOME", str(root))
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(root / "x.md")], "agent": "claude"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return mod.main()


def test_commit_success_writes_pending_not_marker(tmp_path, monkeypatch):
    """#45: 커밋 성공 → pending 기록. sync-warning 은 훅이 쓰지 않는다(worker 몫)."""
    monkeypatch.setenv("TEAMMODE_DISABLE_PUSH_WORKER", "1")
    root = tmp_path / "team"
    root.mkdir()
    (root / ".teammode-active").write_text("")
    res = go.CommitResult(ok=True, committed=True, pushed=False,
                          detail="committed")
    fake = _FakeGitOps(res)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0                        # 비차단 유지
    assert fake.push_arg is False         # 동기 push 폐기(#45)
    assert getattr(fake, "pending_writes", 0) == 1
    assert fake.warnings == []            # 마커는 worker 몫
    assert fake.cleared == 0


def test_hook_never_clears_marker(tmp_path, monkeypatch):
    """#45: 회복 판정(clear)도 훅 몫이 아니다 — worker 가 push 성공+ahead==0 후 지운다."""
    monkeypatch.setenv("TEAMMODE_DISABLE_PUSH_WORKER", "1")
    root = tmp_path / "team"
    root.mkdir()
    (root / ".teammode-active").write_text("")
    res = go.CommitResult(ok=True, committed=True, pushed=False,
                          detail="committed")
    fake = _FakeGitOps(res)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.cleared == 0              # 훅은 clear 하지 않는다


def test_nothing_to_commit_no_marker(tmp_path, monkeypatch):
    # 변경 없음(committed=False) → 마커도 clear 도 건드리지 않는다.
    root = tmp_path / "team"
    root.mkdir()
    (root / ".teammode-active").write_text("")
    res = go.CommitResult(ok=False, committed=False, pushed=False,
                          detail="nothing to commit")
    fake = _FakeGitOps(res)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    assert rc == 0
    assert fake.warnings == []
    assert fake.cleared == 0
    assert getattr(fake, "pending_writes", 0) == 0  # 커밋 없으면 pending 도 없다(#45)


# ─── 이슈 #9(a): TEAMMODE_HOME 스테일 시 stderr 경고 ───

def test_stale_teammode_home_warns_on_stderr(tmp_path, monkeypatch, capsys):
    """TEAMMODE_HOME 이 존재하지 않는 경로 → exit 0 불변 + stderr 한 줄 경고 (커밋 시도 없음)."""
    gone = tmp_path / "moved-away"  # 존재하지 않음
    fake = _FakeGitOps(None)  # 도달하면 안 되는 경로 — do_commit 호출 자체가 없어야 함
    rc = _run(_load_hook(), fake, gone, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == ""
    assert "TEAMMODE_HOME" in captured.err
    assert "유효한 팀 루트" in captured.err
    assert len(captured.err.strip().splitlines()) == 1, "경고는 정확히 한 줄"
    assert fake.warnings == []            # git 동작 전에 멈춘다(거동 불변)


def test_valid_root_teammode_off_stays_silent(tmp_path, monkeypatch, capsys):
    """유효 팀 루트(memory 표식)인데 .teammode-active 없음 = 정상 off — 침묵 유지."""
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    fake = _FakeGitOps(None)
    rc = _run(_load_hook(), fake, root, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err.strip() == "", f"정상 off 상태는 경고 금지: {captured.err!r}"
