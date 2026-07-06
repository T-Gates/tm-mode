"""#36 PR1 — 엔진 동기화가 infra/skills/util(인스턴스 소유)을 보존한다.

설계 확정(이슈 #36 codex 2R): SYNC_PATHS 는 positive 존재확인용 유지, git 실행 시에만
`_sync_pathspecs()` 가 positive+`:(exclude)infra/skills/util` 조합을 만든다. checkout·
diff·dirty·**do_commit(tm on 자동 커밋)** 전부 pathspec 사용 — util 로컬 변경이 sync 로
덮이거나 자동 커밋에 섞이지 않는다.

모든 테스트 tmp_path 격리 — 실 호스트 무접촉.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
import git_ops as go  # noqa: E402


def _git(cwd, *args, check=True):
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


@pytest.fixture
def team_with_upstream(tmp_path):
    """upstream(bare) + team(unrelated init). util 로컬 파일 + engine stale 동시 재현."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "infra").mkdir()
    (seed / "infra" / "engine.py").write_text("v1\n")
    # upstream 은 util 을 소유하지 않음 — .gitkeep 만(스톡 템플릿 모양)
    (seed / "infra" / "skills" / "util").mkdir(parents=True)
    (seed / "infra" / "skills" / "util" / ".gitkeep").write_text("")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "tpl v1")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main")
    (team / "infra").mkdir()
    (team / "infra" / "engine.py").write_text("team-old\n")
    # 인스턴스 소유 util 스킬 — sync 가 절대 건드리면 안 됨
    (team / "infra" / "skills" / "util" / "acme-schedule").mkdir(parents=True)
    (team / "infra" / "skills" / "util" / "acme-schedule" / "SKILL.md").write_text(
        "INSTANCE OWNED — DO NOT SYNC\n")
    (team / "team.config.json").write_text('{"team":{"name":"t"}}\n')
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    _git(team, "remote", "add", "upstream", str(upstream))

    # upstream 앞섬: engine 변경 + 신규 + util .gitkeep 변경
    (seed / "infra" / "engine.py").write_text("v2\n")
    (seed / "infra" / "newfeature.py").write_text("feature\n")
    (seed / "infra" / "skills" / "util" / ".gitkeep").write_text("upstream-touched\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "tpl v2")
    _git(seed, "push")
    return team


# ── _sync_pathspecs 단위 계약 ───────────────────────────────────────

def test_sync_pathspecs_excludes_util_for_infra():
    """기본 SYNC_PATHS(infra 포함)엔 util exclude 를 붙인다."""
    specs = go._sync_pathspecs(["infra", "NOTICE.md"])
    assert "infra" in specs and "NOTICE.md" in specs
    assert ":(exclude)infra/skills/util" in specs


def test_sync_pathspecs_no_exclude_when_util_explicit():
    """util 을 명시 positive 로 주면 exclude 를 붙이지 않는다(상쇄 방지)."""
    specs = go._sync_pathspecs(["infra", "infra/skills/util"])
    assert ":(exclude)infra/skills/util" not in specs


def test_sync_pathspecs_no_exclude_without_ancestor():
    """util 조상 positive 가 없으면 exclude 불필요(붙이지 않음)."""
    specs = go._sync_pathspecs(["NOTICE.md"])
    assert ":(exclude)infra/skills/util" not in specs


# ── sync 통합: util 보존 ────────────────────────────────────────────

def test_sync_preserves_util_and_updates_engine(team_with_upstream):
    """engine stale 파일은 갱신, util 인스턴스 파일은 무변경, diff 에 util 없음."""
    team = team_with_upstream
    util_file = team / "infra" / "skills" / "util" / "acme-schedule" / "SKILL.md"
    res = go.sync_from_upstream(str(team))
    assert res.ok and res.changed, res.detail
    # engine 갱신됨
    assert (team / "infra" / "engine.py").read_text() == "v2\n"
    assert (team / "infra" / "newfeature.py").exists()
    # util 인스턴스 파일 무변경
    assert util_file.read_text() == "INSTANCE OWNED — DO NOT SYNC\n"
    # diff 에 util 경로 없음
    assert "skills/util" not in (res.diff or "")


def test_sync_util_local_change_does_not_block(team_with_upstream):
    """util 의 커밋 안 된 로컬 변경이 있어도 dirty block 안 됨 + 변경 보존."""
    team = team_with_upstream
    util_file = team / "infra" / "skills" / "util" / "acme-schedule" / "SKILL.md"
    util_file.write_text("locally edited util\n")  # uncommitted
    res = go.sync_from_upstream(str(team))
    assert res.blocked is False, "util 로컬 변경이 sync 를 오block"
    assert res.ok and res.changed
    assert (team / "infra" / "engine.py").read_text() == "v2\n"
    assert util_file.read_text() == "locally edited util\n"  # 보존


def test_sync_excludes_upstream_util_gitkeep(team_with_upstream):
    """upstream 의 util/.gitkeep 변경도 제외 — local .gitkeep 불변."""
    team = team_with_upstream
    res = go.sync_from_upstream(str(team))
    assert res.ok
    # team 엔 .gitkeep 이 없었으니 checkout 으로 생기지도 않아야(util 전체 제외)
    assert not (team / "infra" / "skills" / "util" / ".gitkeep").exists()
    assert "skills/util" not in (res.diff or "")


def test_sync_result_pathspecs_field(team_with_upstream):
    """SyncResult.pathspecs 는 git 실행용(positive+exclude), paths 는 positive 표시용."""
    res = go.sync_from_upstream(str(team_with_upstream), dry_run=True)
    assert ":(exclude)infra/skills/util" in res.pathspecs
    assert ":(exclude)infra/skills/util" not in res.paths


def test_do_commit_with_exclude_pathspec_skips_util(team_with_upstream):
    """tm on 자동 커밋 경로 회귀: do_commit(paths=pathspecs)가 util staged 변경을
    커밋에 섞지 않는다(codex R1 핵심 지적 — checkout 만 고치면 이 경로가 샌다)."""
    team = team_with_upstream
    # sync 로 engine 을 staged 상태로 만든 뒤, util 에 로컬 변경을 staged 로 추가
    res = go.sync_from_upstream(str(team))
    assert res.changed
    util_file = team / "infra" / "skills" / "util" / "acme-schedule" / "SKILL.md"
    util_file.write_text("edited during sync window\n")
    _git(team, "add", "infra/skills/util")  # 일부러 util 을 staged
    # 자동 커밋이 pathspecs(exclude 포함)로 커밋 → util 은 빠져야
    cr = go.do_commit(str(team), message="engine sync [auto]",
                      push=False, paths=list(res.pathspecs))
    assert cr.committed, cr.detail
    committed = _git(team, "show", "--name-only", "--format=", "HEAD").stdout
    assert "infra/engine.py" in committed
    assert "skills/util" not in committed, f"util 이 자동 커밋에 섞임:\n{committed}"
    # util 로컬 변경은 여전히 staged 로 남아 있음(유실 아님)
    assert util_file.read_text() == "edited during sync window\n"
