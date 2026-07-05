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


def _run_engine(root, *argv, env=None):
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(Path(root) / ".s.json"), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          timeout=60)  # 러너 보호 하드캡(#36 flaky 진단)




def _hang_remote(tmp_path, repo, remote="origin"):
    """결정적 hang 원격(#36 flaky 진단): TEST-NET blackhole 실 TCP 는 부하에서
    벽시계 결정성을 잃는다(121s 오탐 실측). git remote helper(sleep)로 대체 —
    fetch/push 프로세스가 확실히 매달리고 run_git killpg 계약만 검증한다."""
    bin_dir = tmp_path / "hang-bin"
    bin_dir.mkdir(exist_ok=True)
    helper = bin_dir / "git-remote-sleep"
    helper.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    helper.chmod(0o755)
    _git(repo, "remote", "set-url", remote, "sleep::repo")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH','')}"}

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


def test_commit_verb_offline_push_no_hang(repo_with_remote, tmp_path):
    # 원격을 결정적 hang(remote helper)으로 바꿔 push 가 hang 하지 않는지(타임아웃)
    env = _hang_remote(tmp_path, repo_with_remote.clone)
    (repo_with_remote.clone / "k.txt").write_text("v\n")
    import time
    t0 = time.time()
    r = _run_engine(repo_with_remote.clone, "commit", "--message", "offline",
                    "--push", env=env)
    elapsed = time.time() - t0
    assert elapsed < 30, f"commit/push 가 {elapsed:.1f}s 매달림 (hang)"
    # 커밋은 로컬에 보존(push 만 실패)
    assert "offline" in _git(repo_with_remote.clone, "log", "--oneline").stdout


# ── 자격증명 hang 차단 (auto_pull 패턴 재사용) ──

def test_commit_message_with_leading_dash_not_treated_as_option(local_repo):
    # 적대 검수 락: '--amend' 류 메시지가 git 옵션으로 오인되지 않는다(list-form argv).
    (local_repo / "n.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit", "--message", "--amend evil")
    assert r.returncode == 0, r.stderr
    last = _git(local_repo, "log", "-1", "--format=%s").stdout.strip()
    assert last == "--amend evil"  # 메시지로 보존, amend 안 됨


def test_commit_message_author_injection_blocked(local_repo):
    # '--author=' 주입이 메시지로만 들어가고 author 를 바꾸지 않는다.
    (local_repo / "o.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit", "--message",
                    "normal --author=hacker <h@h>")
    assert r.returncode == 0
    info = _git(local_repo, "log", "-1", "--format=%an").stdout.strip()
    assert info != "hacker"  # author 변조 안 됨


def test_commit_push_fail_preserves_local_commit(repo_with_remote, tmp_path,
                                                  monkeypatch):
    # 적대 검수 락: push 실패해도 로컬 커밋은 절대 롤백되지 않는다.
    env = _hang_remote(tmp_path, repo_with_remote.clone)
    monkeypatch.setenv("PATH", env["PATH"])  # in-proc do_commit 의 git 이 helper 를 찾게
    (repo_with_remote.clone / "p.txt").write_text("v\n")
    before = int(_git(repo_with_remote.clone, "rev-list", "--count",
                      "HEAD").stdout.strip())
    res = go.do_commit(str(repo_with_remote.clone), message="preserve me",
                       push=True, timeout=3)
    after = int(_git(repo_with_remote.clone, "rev-list", "--count",
                     "HEAD").stdout.strip())
    assert after == before + 1, "push 실패가 로컬 커밋을 롤백함(치명 버그)"
    assert res.committed is True and res.pushed is False


def test_commit_empty_message_rejected(local_repo):
    (local_repo / "q.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit", "--message", "")
    assert r.returncode != 0
    # 빈 메시지 커밋 안 생김
    assert "init" in _git(local_repo, "log", "-1", "--format=%s").stdout


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
