"""슬라이스 T3 — upstream NOTICE 읽기 + tm ON 공지 표시 테스트.

설계:
  - git_ops.read_upstream_notice: `git show <remote>/<branch>:NOTICE.md` 로 upstream
    NOTICE 내용 읽기. 파일 없거나 오류면 빈 문자열(무raise).
  - teammode._maybe_notify_upstream: upstream NOTICE != 로컬 NOTICE 면 [공지] 출력.
    같으면 조용히 생략(매번 도배 방지). unrelated histories 환경에서도 동작.

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
def upstream_with_notice(tmp_path):
    """upstream(bare) + team 레포. upstream 에 NOTICE.md 있음.

    team 은 upstream 과 **unrelated histories** — template 레포 시나리오.
    """
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    # upstream 측: NOTICE.md 포함
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "NOTICE.md").write_text("# teammode\n\n## 2026-06-17\n- 신기능 추가\n",
                                    encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "init with NOTICE")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    # team 측: 완전히 별도 init (unrelated histories)
    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main")
    (team / "README.md").write_text("team\n")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    # upstream remote 등록 (install 이 하는 것)
    _git(team, "remote", "add", "upstream", str(upstream))

    class T:
        pass
    t = T()
    t.upstream, t.seed, t.team = upstream, seed, team
    t.notice_content = "# teammode\n\n## 2026-06-17\n- 신기능 추가\n"
    return t


@pytest.fixture
def upstream_without_notice(tmp_path):
    """upstream 에 NOTICE.md 없는 시나리오."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "infra").mkdir()
    (seed / "infra" / "engine.py").write_text("v1\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "init no NOTICE")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main")
    (team / "README.md").write_text("team\n")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    _git(team, "remote", "add", "upstream", str(upstream))

    class T:
        pass
    t = T()
    t.upstream, t.seed, t.team = upstream, seed, team
    return t


# ── read_upstream_notice API 노출 확인 ──

def test_git_ops_exposes_read_upstream_notice():
    assert hasattr(go, "read_upstream_notice"), "git_ops 에 read_upstream_notice 없음"


# ── read_upstream_notice: upstream NOTICE 읽기 ──

def test_read_upstream_notice_returns_content(upstream_with_notice):
    """upstream NOTICE.md 내용을 정확히 읽는다."""
    go.fetch_upstream(str(upstream_with_notice.team), remote="upstream")
    content = go.read_upstream_notice(str(upstream_with_notice.team), remote="upstream")
    assert "2026-06-17" in content
    assert "신기능 추가" in content


def test_read_upstream_notice_empty_when_absent(upstream_without_notice):
    """upstream 에 NOTICE.md 없으면 빈 문자열(graceful)."""
    go.fetch_upstream(str(upstream_without_notice.team), remote="upstream")
    content = go.read_upstream_notice(str(upstream_without_notice.team), remote="upstream")
    assert content == ""


def test_read_upstream_notice_no_raise_on_non_git(tmp_path):
    """git 레포가 아닌 디렉토리도 예외 전파 없이 빈 문자열."""
    plain = tmp_path / "plain"
    plain.mkdir()
    content = go.read_upstream_notice(str(plain))
    assert content == ""


def test_read_upstream_notice_no_raise_no_remote(tmp_path):
    """upstream remote 없어도 예외 전파 없이 빈 문자열."""
    repo = tmp_path / "repo"
    _git(tmp_path, "init", str(repo))
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "checkout", "-b", "main", check=False)
    (repo / "f.txt").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c")
    content = go.read_upstream_notice(str(repo), remote="upstream")
    assert content == ""


def test_read_upstream_notice_works_with_unrelated_histories(upstream_with_notice):
    """unrelated histories 환경에서도 NOTICE 를 정상 읽는다."""
    # has_common_ancestor 가 False 여야 "unrelated" 확인
    go.fetch_upstream(str(upstream_with_notice.team), remote="upstream")
    ancestor = go.has_common_ancestor(str(upstream_with_notice.team), "upstream/main")
    assert ancestor is False, "픽스처가 unrelated histories 여야 한다"
    # 그래도 NOTICE 읽기는 성공
    content = go.read_upstream_notice(str(upstream_with_notice.team), remote="upstream")
    assert content != ""


# ── tm ON: 공지 비교·출력 테스트 (teammode 엔진 호출) ──

def _run_engine(root, *argv, env=None):
    settings = str(Path(root) / ".s.json")
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", settings, *argv[1:]]
    run_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if env:
        run_env.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=run_env)


def _make_on_fixture(tmp_path):
    """on 테스트용 팀 레포 뼈대(adapter sync 대상 settings 포함)."""
    team = tmp_path / "team"
    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main", check=False)
    (team / "README.md").write_text("team\n")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "init")
    # banner
    (team / "memory").mkdir()
    (team / "memory" / "banner.txt").write_text("=== test team mode ON ===\n")
    # settings 더미 (adapter sync 가 쓸 파일)
    import json as _json
    (team / ".s.json").write_text(_json.dumps({}))
    return team


def test_on_shows_notice_when_upstream_differs(tmp_path):
    """upstream NOTICE 가 로컬과 다를 때 [공지] 출력."""
    # upstream 셋업
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "NOTICE.md").write_text("# teammode\n\n## 2026-06-17\n- 새 업데이트\n",
                                    encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "notice")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    team = _make_on_fixture(tmp_path)
    # 로컬 NOTICE.md 없음 → upstream 과 다름
    _git(team, "remote", "add", "upstream", str(upstream))

    res = _run_engine(team, "on")
    assert "[공지]" in res.stdout, f"[공지] 출력 기대, stdout={res.stdout!r}"
    assert "teammode update" in res.stdout


def test_on_silent_when_notice_same(tmp_path):
    """upstream NOTICE == 로컬 NOTICE 면 조용히 생략."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    notice_text = "# teammode\n\n## 2026-06-17\n- 동일\n"
    (seed / "NOTICE.md").write_text(notice_text, encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "notice")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    team = _make_on_fixture(tmp_path)
    # 로컬 NOTICE 도 upstream 과 동일 내용으로 생성
    (team / "NOTICE.md").write_text(notice_text, encoding="utf-8")
    _git(team, "remote", "add", "upstream", str(upstream))

    res = _run_engine(team, "on")
    assert "[공지]" not in res.stdout, f"같은 NOTICE면 조용해야 함, stdout={res.stdout!r}"


def test_on_silent_when_no_upstream(tmp_path):
    """upstream remote 없어도 on 이 정상 종료(NOTICE 알림 없음, 크래시 없음)."""
    team = _make_on_fixture(tmp_path)
    # upstream remote 등록 안 함

    res = _run_engine(team, "on")
    assert res.returncode == 0, f"exit={res.returncode}, stderr={res.stderr!r}"
    assert "[공지]" not in res.stdout
