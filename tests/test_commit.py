"""V.4 `commit` 동사 — git add/commit/push 묶음 테스트.

설계: git_ops 에 do_commit 추가(auto_pull 안전장치 패턴 따름 — 타임아웃·자격증명 차단·
무raise). add → commit → push 묶음. push 는 실제 원격 없으면 우아하게 처리(커밋은 성공).
실패 무해: 변경 없음·원격 없음·푸시 실패 모두 비치명.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·실 ~/.claude 무접촉.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
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
def local_repo(tmp_path):
    """원격 없는 로컬 레포 + 초기 커밋."""
    work = tmp_path / "work"
    _git(tmp_path, "init", str(work))
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@t")
    (work / "init.txt").write_text("init\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "initial")
    return work


@pytest.fixture
def repo_with_remote(tmp_path):
    """bare 원격 + clone(push 대상)."""
    upstream = tmp_path / "up.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "config", "user.name", "t")
    _git(clone, "config", "user.email", "t@t")
    (clone / "init.txt").write_text("init\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "initial")
    _git(clone, "branch", "-M", "main")
    _git(clone, "push", "-u", "origin", "main")

    class R:
        pass
    r = R()
    r.upstream, r.clone = upstream, clone
    return r


def _run_engine(root, *argv):
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(Path(root) / ".s.json"), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True)


# ── git_ops.do_commit ──

def test_git_ops_exposes_do_commit():
    assert hasattr(go, "do_commit")


def test_do_commit_stages_and_commits(local_repo):
    (local_repo / "new.txt").write_text("hi\n")
    res = go.do_commit(str(local_repo), message="add new", push=False)
    assert res.ok is True
    # 커밋이 실제로 생겼다
    log = _git(local_repo, "log", "--oneline").stdout
    assert "add new" in log
    # 워킹트리 clean (전부 스테이지됨)
    assert _git(local_repo, "status", "--short").stdout.strip() == ""


def test_do_commit_no_changes_is_harmless(local_repo):
    # 변경 없음 → 비치명. ok=False 이되 예외 0, 레포 무손상.
    res = go.do_commit(str(local_repo), message="noop", push=False)
    assert res.ok is False
    assert "nothing" in res.detail.lower() or "no change" in res.detail.lower() \
        or res.detail != ""


def test_do_commit_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.do_commit(str(plain), message="x", push=False)
    assert res.ok is False  # 예외 전파 0


def test_do_commit_with_push(repo_with_remote):
    (repo_with_remote.clone / "f.txt").write_text("v\n")
    res = go.do_commit(str(repo_with_remote.clone), message="pushme", push=True)
    assert res.ok is True
    # 원격에 반영됐는지 — bare 원격의 main 브랜치 로그 확인 (bare HEAD 는 master 라
    # ref 없이 log 하면 unborn 오류; push 한 main 을 명시한다)
    log = _git(repo_with_remote.upstream, "log", "--oneline", "main").stdout
    assert "pushme" in log


def test_do_commit_push_no_remote_commit_still_ok(local_repo):
    # 원격 없음: 커밋은 성공해야 하고 push 실패는 비치명(커밋 보존).
    (local_repo / "g.txt").write_text("v\n")
    res = go.do_commit(str(local_repo), message="local only", push=True)
    # 커밋 자체는 됐다
    log = _git(local_repo, "log", "--oneline").stdout
    assert "local only" in log


# ── commit 동사 (엔진) ──

def test_commit_verb_commits(local_repo):
    (local_repo / "h.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit", "--message", "verb commit")
    assert r.returncode == 0, r.stderr
    assert "verb commit" in _git(local_repo, "log", "--oneline").stdout


def test_commit_verb_requires_message(local_repo):
    (local_repo / "i.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit")
    assert r.returncode != 0  # 메시지 필수


def test_commit_verb_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "commit", "--message", "x"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


def test_commit_verb_no_changes_graceful(local_repo):
    r = _run_engine(local_repo, "commit", "--message", "nothing to do")
    # 변경 없음 → 비치명(크래시 없음)
    assert "Traceback" not in r.stderr


def test_commit_verb_push_no_remote_graceful(local_repo):
    (local_repo / "j.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit", "--message", "p", "--push")
    assert "Traceback" not in r.stderr
    # 커밋은 보존
    assert "p" in _git(local_repo, "log", "--oneline").stdout


def test_commit_verb_offline_push_no_hang(repo_with_remote):
    # 원격을 비라우팅으로 바꿔 push 가 hang 하지 않는지(타임아웃)
    _git(repo_with_remote.clone, "remote", "set-url", "origin",
         "http://192.0.2.1/r.git")
    (repo_with_remote.clone / "k.txt").write_text("v\n")
    import time
    t0 = time.time()
    r = _run_engine(repo_with_remote.clone, "commit", "--message", "offline", "--push")
    elapsed = time.time() - t0
    assert elapsed < 30, f"commit/push 가 {elapsed:.1f}s 매달림 (hang)"
    # 커밋은 로컬에 보존(push 만 실패)
    assert "offline" in _git(repo_with_remote.clone, "log", "--oneline").stdout


# ── 자격증명 hang 차단 (auto_pull 패턴 재사용) ──

def test_do_commit_uses_git_env_no_credential_prompt(repo_with_remote):
    # push 가 자격증명 프롬프트로 hang 하지 않게 git_env(GIT_TERMINAL_PROMPT=0)를 쓴다.
    # https 인증 필요한 가짜 원격 → 즉시 실패(프롬프트 hang 아님)
    _git(repo_with_remote.clone, "remote", "set-url", "origin",
         "https://127.0.0.1:1/needs-auth.git")
    (repo_with_remote.clone / "m.txt").write_text("v\n")
    import time
    t0 = time.time()
    res = go.do_commit(str(repo_with_remote.clone), message="authtest", push=True)
    assert time.time() - t0 < 30
    # 커밋 보존
    assert "authtest" in _git(repo_with_remote.clone, "log", "--oneline").stdout
