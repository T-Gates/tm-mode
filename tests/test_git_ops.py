"""V.3 — git_ops 공통 모듈 + `pull` 동사 테스트.

설계: auto_pull.py 의 do_pull(손자 killpg·ff-only·타임아웃·자격증명 차단 안전장치)을
`infra/git_ops.py` 공통 모듈로 끌어올려 재사용한다(신규 git 코드 작성 금지 = 드리프트
방지). pull/commit/auto-pull 이 같은 안전장치를 공유한다.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·실 ~/.claude 무접촉.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
sys.path.insert(0, str(REPO / "infra" / "hooks"))

import git_ops as go  # noqa: E402

ENGINE = REPO / "infra" / "teammode.py"


def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


@pytest.fixture
def cloned_repo(tmp_path):
    """upstream(bare) + clone, upstream 1 commit ahead → clone 1 behind."""
    upstream = tmp_path / "upstream.git"
    work = tmp_path / "work"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(work))
    (work / "a.txt").write_text("v1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c1")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "checkout", "main")
    (work / "b.txt").write_text("v2\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c2")
    _git(work, "push")

    class C:
        pass
    c = C()
    c.upstream, c.work, c.clone = upstream, work, clone
    return c


# ── git_ops.do_pull (이관된 안전장치) ──

def test_git_ops_exposes_do_pull(cloned_repo):
    res = go.do_pull(str(cloned_repo.clone))
    assert res.ok is True
    assert (cloned_repo.clone / "b.txt").exists()


def test_git_ops_do_pull_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.do_pull(str(plain))
    assert res.ok is False  # 예외 없이 실패 표현


def test_git_ops_has_safety_primitives():
    # 안전장치 함수들이 공통 모듈에 존재(재사용 대상)
    for name in ("_run_git", "_git_env", "_kill_group", "_is_git_worktree",
                 "PullResult"):
        assert hasattr(go, name), f"git_ops 에 {name} 없음"


# ── auto_pull 이 git_ops 를 재사용 (드리프트 방지) ──

def test_auto_pull_reuses_git_ops():
    import auto_pull as ap
    # auto_pull 의 do_pull/PullResult 는 git_ops 와 동일 객체여야 한다(중복 정의 아님)
    assert ap.do_pull is go.do_pull
    assert ap.PullResult is go.PullResult


# ── `pull` 동사 (엔진 노출) ──

def _run_engine(root, *argv):
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(Path(root) / ".teammode-settings.json"), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_pull_verb_ff_forwards(cloned_repo):
    r = _run_engine(cloned_repo.clone, "pull")
    assert r.returncode == 0, r.stderr
    assert (cloned_repo.clone / "b.txt").exists()  # 실제로 최신화됨


def test_pull_verb_non_git_graceful(tmp_path):
    # git 레포가 아니어도 우아하게 처리 — 작업 차단 금지(비치명 종료)
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run_engine(plain, "pull")
    # 실패해도 크래시(traceback) 아님. 비치명 처리.
    assert "Traceback" not in r.stderr


def test_pull_verb_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "pull"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


def test_pull_verb_offline_no_hang(tmp_path):
    """원격이 비라우팅 IP 면 타임아웃으로 끊겨야 한다(hang 금지)."""
    work = tmp_path / "off"
    _git(tmp_path, "init", str(work))
    (work / "x").write_text("x")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c")
    # 비라우팅 원격 (TEST-NET-1, RFC5737) — 연결 시도는 타임아웃
    _git(work, "remote", "add", "origin", "http://192.0.2.1/repo.git")
    _git(work, "branch", "--set-upstream-to=origin/main", check=False)
    import time
    t0 = time.time()
    r = _run_engine(work, "pull")
    elapsed = time.time() - t0
    # 타임아웃(do_pull 기본 5s) + 약간의 여유. hang(무한) 아님.
    assert elapsed < 20, f"pull 이 {elapsed:.1f}s 매달림 (hang)"
    assert "Traceback" not in r.stderr


def test_do_pull_timeout_no_orphan_grandchild(tmp_path):
    """역사적 버그 회귀 락: 타임아웃 시 손자 git-remote-http(s) 고아 누수 0.

    do_pull(짧은 timeout) 으로 비라우팅 원격에 pull → killpg 가 손자까지 죽이는지.
    git_ops 로 이관 후에도 안전장치가 살아있음을 실측한다.
    """
    import re
    import time
    work = tmp_path / "orphan"
    _git(tmp_path, "init", str(work))
    (work / "x").write_text("x")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c")
    _git(work, "remote", "add", "origin", "http://192.0.2.1/r.git")
    _git(work, "branch", "--set-upstream-to=origin/main", check=False)

    def _count_remote_http():
        try:
            out = subprocess.run(["pgrep", "-af", "git-remote-http"],
                                 capture_output=True, text=True).stdout
        except OSError:
            return 0
        return len([l for l in out.splitlines() if l.strip()])

    before = _count_remote_http()
    res = go.do_pull(str(work), timeout=2)
    assert res.ok is False  # 타임아웃 또는 즉시 실패 — 예외 전파 0
    time.sleep(1.5)  # 고아가 있었다면 이 시점까지 살아있을 것
    after = _count_remote_http()
    assert after <= before, f"손자 git-remote-http 고아 누수: before={before} after={after}"
