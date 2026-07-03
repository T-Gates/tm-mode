"""#45 — async push: pending ledger + plain push + worker drain 테스트.

확정 스펙(이슈 #45 + plain-push-only 정정):
  - auto-commit 은 do_commit(push=False) 까지만 동기 → 커밋 성공 시 XDG pending 원자 기록
    + push-worker detach kick.
  - push-worker: per-team lock 단일 실행, drain loop(최대 3), **plain push only** —
    로컬 히스토리 무접촉(rebase 복구 없음 — index.lock 경합으로 편집 커밋 유실 방지).
    non-ff 는 복구 없이 sync-warning 마커만(정합은 session-start reconcile 에 위임).
    no-upstream 만 `push -u origin HEAD` 1회.
  - pending clear 는 push 성공 + ahead==0 확인 후에만(push 중 새 커밋 유실 방지).
  - 성공 = pending·sync-warning clear / 실패 = sync-warning detail.

모든 테스트는 tmp_path + XDG_STATE_HOME 격리 — 실 호스트 무접촉.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops  # noqa: E402


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    """XDG_STATE_HOME 격리 — 실 ~/.local/state 무접촉."""
    state = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def _init_repo(path: Path, *, bare: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    args = ["git", "init", "-q"] + (["--bare"] if bare else []) + [str(path)]
    subprocess.run(args, check=True, capture_output=True)
    if not bare:
        subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                       capture_output=True)
    return path


def _clone_pair(tmp_path) -> tuple:
    """bare origin + 작업 클론 (upstream tracking 설정 완료)."""
    origin = _init_repo(tmp_path / "origin.git", bare=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "T"],
                   capture_output=True)
    (work / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-qu", "origin", "HEAD"],
                   capture_output=True)
    return origin, work


def _commit_file(repo: Path, name: str, content: str = "x") -> None:
    (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"add {name}"],
                   capture_output=True)


# ── pending ledger ──────────────────────────────────────────────────

def test_pending_ledger_roundtrip(xdg, tmp_path):
    """write → read(truthy) → clear(멱등) — 팀별 파일, XDG 하위."""
    root = str(tmp_path / "team")
    assert git_ops.read_push_pending(root) == ""
    git_ops.write_push_pending(root)
    assert git_ops.read_push_pending(root) != ""
    p = Path(git_ops.push_pending_path(root))
    assert p.is_file() and str(xdg) in str(p)
    git_ops.clear_push_pending(root)
    assert git_ops.read_push_pending(root) == ""
    git_ops.clear_push_pending(root)  # 멱등 — 예외 없음


def test_pending_ledger_is_per_team(xdg, tmp_path):
    """팀 A 의 clear 가 팀 B 마커를 건드리지 않는다(sync-warning 과 동일 규약)."""
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    git_ops.write_push_pending(a)
    git_ops.write_push_pending(b)
    git_ops.clear_push_pending(a)
    assert git_ops.read_push_pending(a) == ""
    assert git_ops.read_push_pending(b) != ""


def test_pending_age_seconds(xdg, tmp_path):
    """age: 없으면 None, 있으면 0 이상 float — UserPromptSubmit 경량검사용."""
    root = str(tmp_path / "team")
    assert git_ops.push_pending_age_seconds(root) is None
    git_ops.write_push_pending(root)
    age = git_ops.push_pending_age_seconds(root)
    assert isinstance(age, float) and age >= 0.0


# ── push_plain (plain-push-only) ────────────────────────────────────

def test_push_plain_success(xdg, tmp_path):
    """로컬 ahead 1 → plain push 성공."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail
    ahead, behind = git_ops.ahead_behind(str(work))
    assert ahead == 0


def test_push_plain_non_ff_no_recovery(xdg, tmp_path):
    """non-ff: 복구(rebase/fetch) 없이 pushed=False + 'non-fast-forward' 분류.

    로컬 히스토리 무접촉이 계약 — worker 가 rebase 를 하면 index.lock 경합으로
    사용자 편집 커밋이 조용히 유실될 수 있다(#45 정정의 근거).
    """
    origin, work = _clone_pair(tmp_path)
    # 다른 클론이 먼저 push → work 는 non-ff
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "O"],
                   capture_output=True)
    _commit_file(other, "theirs.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"], capture_output=True)

    _commit_file(work, "mine.md")
    head_before = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is False
    assert "non-fast-forward" in detail
    # 로컬 히스토리 무접촉(HEAD 불변 — rebase 안 함)
    head_after = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    assert head_after == head_before


def test_push_plain_no_upstream_sets_u_once(xdg, tmp_path):
    """upstream 미설정 브랜치: `push -u origin HEAD` 1회로 성공."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "feat/x"],
                   capture_output=True)
    _commit_file(work, "b.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail
