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


# ── do_commit push 자동 복구(non-ff → fetch+rebase+재push) ──

def test_is_non_fast_forward_detects_reject_patterns():
    """non-ff 판정 헬퍼: git 의 거부 메시지를 패턴으로 감지."""
    rejected = [
        " ! [rejected]        main -> main (non-fast-forward)",
        "hint: Updates were rejected because the tip of your current branch is behind",
        "! [rejected] main -> main (fetch first)",
        "Updates were rejected because the remote contains work that you do",
    ]
    for msg in rejected:
        assert go._is_non_fast_forward(msg) is True, f"non-ff 미감지: {msg!r}"


def test_is_non_fast_forward_ignores_unrelated_errors():
    """non-ff 가 아닌 실패(인증·네트워크 등)는 False — 자동 복구 트리거 금지."""
    others = [
        "",
        "fatal: Authentication failed for 'https://example/'",
        "fatal: unable to access 'https://example/': Could not resolve host",
        "everything up-to-date",
    ]
    for msg in others:
        assert go._is_non_fast_forward(msg) is False, f"오탐(non-ff 로 잘못 판정): {msg!r}"


def test_do_commit_push_rebases_when_behind(cloned_repo):
    """behind 상태에서 push 가 non-ff 거부되면 fetch+rebase 후 재push 로 성공."""
    clone = cloned_repo.clone
    # clone 에서 로컬 변경 + commit + push 시도 — upstream 은 이미 c2 만큼 ahead.
    (clone / "c.txt").write_text("from clone\n")
    res = go.do_commit(str(clone), "clone commit", push=True)
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is True, f"rebase 후 재push 실패: {res.detail}"
    # upstream(bare)에 clone 의 커밋이 반영됐는지 — work 를 fetch 해 확인.
    _git(cloned_repo.work, "fetch", "origin")
    log = _git(cloned_repo.work, "log", "--oneline", "origin/main").stdout
    assert "clone commit" in log
    # 워킹트리에 rebase 진행중 흔적 없음.
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()


def test_do_commit_push_rebase_conflict_aborts_nonblocking(cloned_repo):
    """rebase 충돌 시 abort 로 원상복구하고 pushed=False 비차단 반환."""
    clone = cloned_repo.clone
    work = cloned_repo.work
    # upstream 에 a.txt 를 바꾸는 새 커밋 push(work).
    (work / "a.txt").write_text("work edits a\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "work edit a")
    _git(work, "push")
    # clone 도 같은 a.txt 를 다르게 바꿔 commit — rebase 시 충돌.
    (clone / "a.txt").write_text("clone edits a\n")
    res = go.do_commit(str(clone), "clone edit a", push=True)
    assert res.ok is True       # 비차단
    assert res.committed is True  # 로컬 커밋 보존
    assert res.pushed is False    # 충돌로 push 못 함
    # 실제로 복구(fetch+rebase)를 시도하고 충돌로 abort 한 경로를 탔는지 detail 로 단언.
    # (사후상태만 보면 복구 블록을 통째로 꺼도 통과 — 돌연변이 미검출. 이 단언이 막는다.)
    assert "rebase failed" in res.detail and "aborted" in res.detail, \
        f"abort 경로 표식 없음(복구 시도 안 한 상태와 구별 불가): {res.detail!r}"
    # rebase 진행중 상태가 남지 않음(abort 됨).
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()
    # 로컬 커밋과 워킹트리가 보존됨 — clone 의 a.txt 내용 유지.
    assert (clone / "a.txt").read_text() == "clone edits a\n"
    head_msg = _git(clone, "log", "-1", "--format=%s").stdout.strip()
    assert head_msg == "clone edit a"


def test_do_commit_partial_push_autostash_recovers_with_dirty_tracked(cloned_repo):
    """주 패턴 회귀가드: partial-commit(paths=) 으로 한 파일만 커밋 + push 하는데
    워킹트리에 **다른 추적파일의 미커밋 변경(dirty)** 이 남아있어도, non-ff 복구
    rebase 가 --autostash 로 그 dirty 를 흡수하고 재push 까지 성공해야 한다.

    이게 auto-commit 의 실제 패턴(세션로그만 partial-commit, 코드 등 다른 추적파일은
    워킹트리에 dirty 로 남음). 평문 rebase 였다면 "unstaged changes" 로 거부돼 복구가
    매번 불발한다 — 이 테스트가 그 회귀를 잡는다.
    """
    clone = cloned_repo.clone
    work = cloned_repo.work
    # clone 은 c2 만큼 behind. 추적파일 a.txt 를 unstaged-dirty 로 둔다(스테이지 안 함).
    (clone / "a.txt").write_text("locally edited a — uncommitted\n")
    # 다른(새) 파일만 paths= 로 partial-commit + push.
    (clone / "log.txt").write_text("session log line\n")
    res = go.do_commit(str(clone), "session log", push=True, paths=["log.txt"])
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is True, f"autostash 복구로 재push 성공해야 함: {res.detail}"
    # upstream(bare)에 partial 커밋이 반영됐는지 확인.
    _git(work, "fetch", "origin")
    log = _git(work, "log", "--oneline", "origin/main").stdout
    assert "session log" in log
    # dirty 추적파일 변경이 보존됨(autostash pop 으로 복원 — 유실 0).
    assert (clone / "a.txt").read_text() == "locally edited a — uncommitted\n"
    # 커밋은 log.txt 만 — a.txt 의 dirty 변경은 커밋에 휩쓸리지 않음(여전히 unstaged).
    porcelain = _git(clone, "status", "--porcelain", "--", "a.txt").stdout
    assert porcelain.strip().startswith(("M", " M")), \
        f"a.txt 가 여전히 미커밋 변경이어야 함: {porcelain!r}"
    # autostash 잔여 stash 없음(pop 으로 정리됨).
    assert _git(clone, "stash", "list").stdout.strip() == ""


def test_do_commit_push_rebase_conflict_autostash_no_residue(cloned_repo):
    """충돌로 rebase 가 실패해 abort 할 때, --autostash 로 stash 했던 dirty 가
    깨끗이 원복되고 **stash 잔여가 0** 이어야 한다(어정쩡한 상태 금지).
    """
    clone = cloned_repo.clone
    work = cloned_repo.work
    # upstream 이 a.txt 를 바꾼다(충돌 소스).
    (work / "a.txt").write_text("work version of a\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "work edit a")
    _git(work, "push")
    # clone 에 추적파일 e.txt 를 로컬 커밋(rebase 로 재생될 깨끗한 베이스).
    (clone / "e.txt").write_text("e base\n")
    _git(clone, "add", "e.txt")
    _git(clone, "commit", "-m", "add e")
    # e.txt 를 unstaged-dirty 로 둔다 → autostash 대상.
    (clone / "e.txt").write_text("e DIRTY uncommitted\n")
    # a.txt 를 다르게 바꿔 partial-commit → rebase 시 upstream 과 충돌.
    (clone / "a.txt").write_text("clone version of a\n")
    res = go.do_commit(str(clone), "clone edit a", push=True, paths=["a.txt"])
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is False
    assert "aborted" in res.detail, f"abort 경로 표식 없음: {res.detail!r}"
    # rebase 진행중 흔적 없음.
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()
    # autostash 가 abort 로 원복 — dirty e.txt 보존, stash 잔여 0.
    assert (clone / "e.txt").read_text() == "e DIRTY uncommitted\n"
    assert _git(clone, "stash", "list").stdout.strip() == "", "stash 잔여 누수"


def test_is_non_fast_forward_ignores_server_hook_decline():
    """음성 가드: 서버훅/보호브랜치 거부(`[remote rejected] ... declined`)는 non-ff 가
    아니다 → False. 누가 패턴을 느슨하게 고쳐 훅거부에 rebase 를 걸면 이 테스트가 막는다
    (rebase+재push 가 보호브랜치를 영원히 두드리는 회귀 방지).
    """
    msg = " ! [remote rejected] main -> main (pre-receive hook declined)"
    assert go._is_non_fast_forward(msg) is False, \
        f"서버훅 거부를 non-ff 로 오탐: {msg!r}"


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
