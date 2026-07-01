"""이슈 #23 — git_ops.do_reconcile / ahead_behind / sync-warning 마커 테스트.

세션 시작 시 단순 `pull --ff-only` 가 로컬 diverge 에서 조용히 실패하던 문제를
do_reconcile(fetch + ff/rebase)로 실제 정합하고, 상태를 표면화한다.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·라이브 레포 무접촉.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops as go  # noqa: E402


def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    # rebase/merge 가 committer 신원을 요구하므로 repo-local 로 박는다(결정적).
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


@pytest.fixture
def remote_clone(tmp_path):
    """bare upstream + work(올린 쪽) + clone(검사 대상). clone 은 origin/main 추적."""
    upstream = tmp_path / "upstream.git"
    work = tmp_path / "work"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(upstream))
    _init_repo(work)
    (work / "a.txt").write_text("v1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c1")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(upstream))
    _git(work, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    _git(clone, "checkout", "main")

    class C:
        pass
    c = C()
    c.upstream, c.work, c.clone = upstream, work, clone
    return c


def _push_new_upstream_commit(c, name="b.txt", content="up\n"):
    """work 에서 새 커밋을 만들어 origin 에 push(클론을 behind 로 만든다)."""
    (c.work / name).write_text(content)
    _git(c.work, "add", ".")
    _git(c.work, "commit", "-m", f"add {name}")
    _git(c.work, "push")


def _local_commit(c, name="local.txt", content="local\n"):
    """clone 에 push 안 한 로컬 커밋(ahead)을 만든다."""
    (c.clone / name).write_text(content)
    _git(c.clone, "add", ".")
    _git(c.clone, "commit", "-m", f"local {name}")


# ── do_reconcile ──

def test_reconcile_up_to_date(remote_clone):
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "up-to-date"
    assert res.ahead == 0 and res.behind == 0


def test_reconcile_fast_forward(remote_clone):
    _push_new_upstream_commit(remote_clone)
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "fast-forward"
    assert (remote_clone.clone / "b.txt").exists()   # 실제 정합됨


def test_reconcile_ahead_only(remote_clone):
    _local_commit(remote_clone)
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "ahead-only"
    assert res.ahead == 1 and res.behind == 0


def test_reconcile_diverged_rebases(remote_clone):
    # upstream 과 로컬이 서로 다른 파일을 추가 → diverge, 충돌 없이 rebase 성공.
    _push_new_upstream_commit(remote_clone, name="b.txt")
    _local_commit(remote_clone, name="local.txt")
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "rebased"
    assert res.diverged is True
    # 두 변경 모두 working tree 에 존재(rebase 로 로컬이 upstream 위로 올라감).
    assert (remote_clone.clone / "b.txt").exists()
    assert (remote_clone.clone / "local.txt").exists()
    assert res.ahead == 1   # 미push 로컬 커밋 1개 남음


def test_reconcile_conflict_aborts_and_surfaces(remote_clone):
    # 같은 파일을 upstream·로컬이 충돌하게 수정 → rebase 충돌 → abort + conflict.
    _push_new_upstream_commit(remote_clone, name="a.txt", content="UP\n")
    _local_commit(remote_clone, name="a.txt", content="LOCAL\n")
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is False
    assert res.action == "conflict"
    assert res.diverged is True
    # rebase 가 진행 중으로 남지 않아야 한다(abort 로 원복).
    st = _git(remote_clone.clone, "status", "--porcelain=v1")
    assert "rebase" not in st.stdout.lower()
    assert not (remote_clone.clone / ".git" / "rebase-merge").exists()
    assert not (remote_clone.clone / ".git" / "rebase-apply").exists()


def test_reconcile_no_upstream(remote_clone):
    # origin 은 있지만 추적 upstream 이 없는 브랜치 → fetch 는 성공, @{u} 해석 실패.
    _git(remote_clone.clone, "checkout", "-b", "orphan-branch")
    res = go.do_reconcile(str(remote_clone.clone))
    assert res.ok is True
    assert res.action == "no-upstream"


def test_reconcile_not_worktree(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.do_reconcile(str(plain))
    assert res.ok is False
    assert res.action == "not-worktree"


# ── ahead_behind ──

def test_ahead_behind_counts(remote_clone):
    _push_new_upstream_commit(remote_clone)   # upstream +1 (clone behind 1)
    _local_commit(remote_clone)               # clone +1 (ahead 1)
    _git(remote_clone.clone, "fetch")         # origin/main 갱신(측정 전 fetch 필요)
    ahead, behind = go.ahead_behind(str(remote_clone.clone))
    assert ahead == 1
    assert behind == 1


# ── sync-warning 마커 (team_root 별 파일 — codex 리뷰 P2) ──

def test_sync_warning_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = "/team/alpha"
    assert go.read_sync_warning(root) == ""        # 없음
    go.write_sync_warning(root, "push 실패: GH007")
    assert "GH007" in go.read_sync_warning(root)
    go.clear_sync_warning(root)
    assert go.read_sync_warning(root) == ""


def test_sync_warning_per_team_paths_differ():
    # 팀마다 마커 파일 경로가 달라야 교차 간섭이 없다(격리의 근거).
    assert go.sync_warning_path("/team/alpha") != go.sync_warning_path("/team/beta")
    # 표기차(trailing slash)는 같은 파일로 정규화.
    assert go.sync_warning_path("/team/alpha") == go.sync_warning_path("/team/alpha/")


def test_sync_warning_read_isolated_by_team(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "alpha 경고")
    # 다른 팀은 자기 파일이 없어 빈 문자열.
    assert go.read_sync_warning("/team/beta") == ""
    assert go.read_sync_warning("/team/alpha") == "alpha 경고"


def test_sync_warning_clear_does_not_touch_other_team(tmp_path, monkeypatch):
    # 핵심 P2 회귀: repo B 의 clear 가 repo A 의 미해결 마커를 지우면 안 된다.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "alpha 미해결 push 실패")
    go.clear_sync_warning("/team/beta")     # repo B 성공 → 자기 것만 지움(없음)
    assert go.read_sync_warning("/team/alpha") == "alpha 미해결 push 실패"  # 보존


def test_sync_warning_clear_same_team_removes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "alpha 경고")
    go.clear_sync_warning("/team/alpha")
    assert go.read_sync_warning("/team/alpha") == ""


def test_sync_warning_two_teams_coexist(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    go.write_sync_warning("/team/alpha", "A")
    go.write_sync_warning("/team/beta", "B")
    # 독립 공존 — write 경합("마지막이 이김") 없음.
    assert go.read_sync_warning("/team/alpha") == "A"
    assert go.read_sync_warning("/team/beta") == "B"
