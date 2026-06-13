"""V.5 / 슬라이스 T — 템플릿 풀(upstream fetch + update 동사) 테스트.

설계(Jane: "팀모드 킬때 템플릿 풀도"):
  - `on` 시 upstream 을 **fetch 만** 자동(조용·실패무시·타임아웃). behind 면 변경목록+알림.
  - **merge 는 절대 자동 금지** — 적용은 명시적 `update` 동사(merge --ff-only 우선,
    첫회 --allow-unrelated-histories 고려).
  - upstream 미설정/오프라인 → 우아한 축소(on 막지 않기, 조용히 패스).

Gstack 교훈: fetch throttle·실패는 작업 차단 안 함. 네트워크는 /tmp fake remote 로 모사.
실 toolkit·실 ~/.claude 무접촉.
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
def team_with_upstream(tmp_path):
    """upstream(템플릿 원본·bare) + team(팀 레포 클론). upstream 1 commit ahead."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "template.txt").write_text("v1\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "tpl v1")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", str(upstream), str(team))
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "main")
    # team 은 origin=upstream. 'upstream' remote 도 같은 곳을 가리키게 추가(템플릿 원본).
    _git(team, "remote", "add", "upstream", str(upstream))

    # upstream(템플릿)에 새 커밋 → team 은 upstream/main 대비 1 behind
    (seed / "template.txt").write_text("v2\n")
    (seed / "newfeature.txt").write_text("feature\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "tpl v2 newfeature")
    _git(seed, "push")

    class T:
        pass
    t = T()
    t.upstream, t.seed, t.team = upstream, seed, team
    return t


def _run_engine(root, *argv, env=None):
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(Path(root) / ".s.json"), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ── git_ops 빌딩블록 ──

def test_git_ops_exposes_fetch_and_behind():
    for name in ("fetch_upstream", "count_behind", "upstream_changes"):
        assert hasattr(go, name), f"git_ops 에 {name} 없음"


def test_fetch_upstream_succeeds(team_with_upstream):
    res = go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    assert res.ok is True


def test_fetch_upstream_no_remote_graceful(tmp_path):
    # upstream remote 미설정 → 우아한 실패(ok=False), 예외 0
    work = tmp_path / "noup"
    _git(tmp_path, "init", str(work))
    res = go.fetch_upstream(str(work), remote="upstream")
    assert res.ok is False


def test_fetch_upstream_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.fetch_upstream(str(plain), remote="upstream")
    assert res.ok is False


def test_count_behind_detects_behind(team_with_upstream):
    go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    n = go.count_behind(str(team_with_upstream.team), "upstream/main")
    assert n >= 1


def test_count_behind_zero_when_uptodate(team_with_upstream):
    go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    # update 적용 후엔 0 behind
    go.update_from_upstream(str(team_with_upstream.team), "upstream/main")
    n = go.count_behind(str(team_with_upstream.team), "upstream/main")
    assert n == 0


def test_upstream_changes_lists_commits(team_with_upstream):
    go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    changes = go.upstream_changes(str(team_with_upstream.team), "upstream/main")
    assert "newfeature" in changes


# ── update_from_upstream (명시적 merge) ──

def test_update_from_upstream_ff_merges(team_with_upstream):
    go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    res = go.update_from_upstream(str(team_with_upstream.team), "upstream/main")
    assert res.ok is True
    # 템플릿 변경이 실제로 반영됐다
    assert (team_with_upstream.team / "newfeature.txt").exists()
    assert (team_with_upstream.team / "template.txt").read_text() == "v2\n"


def test_update_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.update_from_upstream(str(plain), "upstream/main")
    assert res.ok is False


# ── on 동사: fetch 자동, merge 금지 ──

def test_on_fetches_upstream_and_notifies_behind(team_with_upstream):
    (team_with_upstream.team / "memory").mkdir(exist_ok=True)
    r = _run_engine(team_with_upstream.team, "on")
    assert r.returncode == 0, r.stderr
    # behind 알림이 배너 뒤에 노출
    combined = r.stdout + r.stderr
    assert "upstream" in combined.lower() or "템플릿" in combined or "update" in combined.lower()


def test_on_does_NOT_auto_merge(team_with_upstream):
    # 핵심 안전: on 은 fetch 만. merge 금지 → newfeature.txt 가 아직 없어야 한다.
    (team_with_upstream.team / "memory").mkdir(exist_ok=True)
    _run_engine(team_with_upstream.team, "on")
    assert not (team_with_upstream.team / "newfeature.txt").exists(), \
        "on 이 자동 merge 함(금지 위반)"


def test_on_no_upstream_still_succeeds(tmp_path):
    # upstream remote 없는 일반 팀 레포 → on 은 막히지 않는다(우아한 축소)
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "memory").mkdir()
    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr


def test_on_non_git_root_still_succeeds(tmp_path):
    # git 레포조차 아닌 root → on 은 여전히 동작(fetch 조용히 스킵)
    team = tmp_path / "plain"
    (team / "memory").mkdir(parents=True)
    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr


# ── update 동사 (엔진) ──

def test_update_verb_applies(team_with_upstream):
    r = _run_engine(team_with_upstream.team, "update")
    assert r.returncode == 0, r.stderr
    assert (team_with_upstream.team / "newfeature.txt").exists()


def test_update_verb_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "update"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


def test_update_verb_no_upstream_graceful(tmp_path):
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    r = _run_engine(team, "update")
    assert "Traceback" not in r.stderr


def test_update_verb_already_uptodate(team_with_upstream):
    _run_engine(team_with_upstream.team, "update")  # 1st apply
    r = _run_engine(team_with_upstream.team, "update")  # 2nd: nothing to do
    assert "Traceback" not in r.stderr


# ── 오프라인 안전(hang 금지) ──

def test_on_offline_upstream_no_hang(tmp_path):
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "memory").mkdir()
    (team / "x").write_text("x")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "c")
    _git(team, "remote", "add", "upstream", "http://192.0.2.1/r.git")
    import time
    t0 = time.time()
    r = _run_engine(team, "on")
    elapsed = time.time() - t0
    assert elapsed < 20, f"on 이 {elapsed:.1f}s 매달림(offline upstream hang)"
    assert r.returncode == 0, r.stderr  # on 은 막히지 않는다
