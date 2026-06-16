"""슬라이스 T / T2 — 템플릿 풀(upstream fetch + update 동사) 테스트.

설계(Jane: "팀모드 킬때 템플릿 풀도" + 2026-06-17 재설계):
  - `on` 시 upstream 을 **fetch 만** 자동(조용·실패무시·타임아웃). behind 면 변경목록+알림.
  - **merge 는 쓰지 않는다** — 도입 레포가 GitHub *template* 으로 생성돼 upstream 과
    공통 조상이 0(unrelated histories)이라 merge/pull --ff-only 가 영원히 막힌다.
    → `update` 동사는 upstream 의 **엔진 경로(infra/)만** `git checkout` 으로 덮어쓰는
    **파일 동기화**다. 히스토리 관계와 무관하게 동작한다.
  - 가드: dirty(대상 경로에 커밋 안 된 변경) → 중단. --dry-run → 미리보기. 멱등.
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


def _seed_infra(repo, content):
    """upstream 측 엔진 경로(infra/)에 파일을 심는다(동기화 대상)."""
    (repo / "infra").mkdir(exist_ok=True)
    (repo / "infra" / "engine.py").write_text(content)


@pytest.fixture
def team_with_upstream(tmp_path):
    """upstream(템플릿 원본·bare) + team(팀 레포). upstream 이 infra/ 변경으로 앞섬.

    핵심: team 과 upstream 은 **unrelated histories**(각자 git init, 공통 조상 0).
    template 레포 시나리오를 그대로 재현한다 — merge 면 막혀야 하고 sync 면 성공해야 한다.
    """
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    # upstream 측: 독립 레포(self-contained init → unrelated)
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    _seed_infra(seed, "v1\n")
    (seed / "memory").mkdir()
    (seed / "memory" / "tpl-data.md").write_text("upstream-only\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "tpl v1")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    # team 측: **완전히 별도** git init (origin 클론 아님 → unrelated histories)
    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main")
    _seed_infra(team, "team-old\n")            # team 자기 버전(다름)
    (team / "memory").mkdir()
    (team / "memory" / "team-secret.md").write_text("DO NOT TOUCH\n")
    (team / "team.config.json").write_text('{"team":{"name":"t"}}\n')
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    # template 처럼: upstream remote 등록(install 이 하는 것)
    _git(team, "remote", "add", "upstream", str(upstream))

    # upstream(템플릿)에 새 커밋 — infra/ 변경 + 신규 파일
    _seed_infra(seed, "v2\n")
    (seed / "infra" / "newfeature.py").write_text("feature\n")
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


# ── git_ops 빌딩블록 (fetch/behind — on 알림이 재사용) ──

def test_git_ops_exposes_fetch_and_sync():
    for name in ("fetch_upstream", "count_behind", "upstream_changes",
                 "sync_from_upstream", "detect_default_branch", "diff_paths",
                 "has_common_ancestor", "SYNC_PATHS"):
        assert hasattr(go, name), f"git_ops 에 {name} 없음"


def test_sync_paths_excludes_team_owned():
    # 동기화 대상은 엔진 경로(infra/)만 — memory/·team.config.json 절대 미포함
    assert "infra" in go.SYNC_PATHS
    assert "memory" not in go.SYNC_PATHS
    assert "team.config.json" not in go.SYNC_PATHS


def test_fetch_upstream_succeeds(team_with_upstream):
    res = go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    assert res.ok is True


def test_fetch_upstream_no_remote_graceful(tmp_path):
    work = tmp_path / "noup"
    _git(tmp_path, "init", str(work))
    res = go.fetch_upstream(str(work), remote="upstream")
    assert res.ok is False


def test_fetch_upstream_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.fetch_upstream(str(plain), remote="upstream")
    assert res.ok is False


# ── detect_default_branch ──

def test_detect_default_branch_main(team_with_upstream):
    go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    b = go.detect_default_branch(str(team_with_upstream.team), remote="upstream")
    assert b == "main"


def test_detect_default_branch_fallback_when_unknown(tmp_path):
    # remote ref 가 전혀 없어도 폴백 'main' (raise 0)
    work = tmp_path / "w"
    _git(tmp_path, "init", str(work))
    b = go.detect_default_branch(str(work), remote="upstream")
    assert b == "main"


# ── sync_from_upstream (파일 동기화, merge 아님) ──

def test_sync_overwrites_infra_unrelated_histories(team_with_upstream):
    """핵심: unrelated histories 인데도 merge 실패 없이 infra/ 동기화 성공."""
    res = go.sync_from_upstream(str(team_with_upstream.team), remote="upstream")
    assert res.ok is True, res.detail
    assert res.changed is True
    team = team_with_upstream.team
    # 엔진 파일이 upstream 버전으로 덮어써짐
    assert (team / "infra" / "engine.py").read_text() == "v2\n"
    assert (team / "infra" / "newfeature.py").exists()


def test_sync_does_not_touch_memory_or_config(team_with_upstream):
    res = go.sync_from_upstream(str(team_with_upstream.team), remote="upstream")
    assert res.ok is True
    team = team_with_upstream.team
    # 팀 소유 파일 무손상
    assert (team / "memory" / "team-secret.md").read_text() == "DO NOT TOUCH\n"
    assert not (team / "memory" / "tpl-data.md").exists()  # upstream-only 안 들어옴
    assert (team / "team.config.json").exists()


def test_sync_stages_but_does_not_commit(team_with_upstream):
    head_before = _git(team_with_upstream.team, "rev-parse", "HEAD").stdout.strip()
    res = go.sync_from_upstream(str(team_with_upstream.team), remote="upstream")
    assert res.changed is True
    # 커밋 안 함 — HEAD 불변, 변경은 staged
    head_after = _git(team_with_upstream.team, "rev-parse", "HEAD").stdout.strip()
    assert head_before == head_after
    staged = _git(team_with_upstream.team, "diff", "--cached", "--name-only").stdout
    assert "infra/engine.py" in staged


def test_sync_idempotent_when_uptodate(team_with_upstream):
    go.sync_from_upstream(str(team_with_upstream.team), remote="upstream")
    _git(team_with_upstream.team, "add", "-A")
    _git(team_with_upstream.team, "commit", "-m", "applied sync")
    # 두 번째 — 변경 없음
    res = go.sync_from_upstream(str(team_with_upstream.team), remote="upstream")
    assert res.ok is True
    assert res.changed is False


def test_sync_dry_run_no_changes(team_with_upstream):
    team = team_with_upstream.team
    res = go.sync_from_upstream(str(team), remote="upstream", dry_run=True)
    assert res.ok is True
    assert res.changed is False
    assert res.diff  # 미리보기 채워짐
    # 실제 파일 안 바뀜
    assert (team / "infra" / "engine.py").read_text() == "team-old\n"
    assert not (team / "infra" / "newfeature.py").exists()


def test_sync_dirty_guard_blocks(team_with_upstream):
    team = team_with_upstream.team
    # 대상 경로(infra/)에 커밋 안 된 로컬 변경
    (team / "infra" / "engine.py").write_text("LOCAL UNCOMMITTED\n")
    res = go.sync_from_upstream(str(team), remote="upstream")
    assert res.ok is False
    assert res.blocked is True
    # 덮어쓰지 않음 — 로컬 변경 보존
    assert (team / "infra" / "engine.py").read_text() == "LOCAL UNCOMMITTED\n"


def test_sync_no_upstream_graceful(tmp_path):
    work = tmp_path / "w"
    _git(tmp_path, "init", str(work))
    res = go.sync_from_upstream(str(work), remote="upstream")
    assert res.ok is False
    assert res.blocked is False


def test_sync_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.sync_from_upstream(str(plain), remote="upstream")
    assert res.ok is False


# ── on 동사: fetch 자동, merge/sync 금지 ──

def test_on_fetches_upstream_no_misleading_behind_for_unrelated(team_with_upstream):
    """unrelated histories(GitHub template 도입 레포) 에서 on 이 성공하고
    "N커밋 앞섭니다" 오알림을 발화하지 않아야 한다 (P2 버그 수정).

    공통 조상이 없으면 git rev-list HEAD..upstream 이 upstream 의 모든 커밋을 반환해
    뻥튀기된 behind 카운트가 찍혔다. has_common_ancestor() 가 False 면 알림을 억제한다.
    team_with_upstream fixture 는 의도적으로 unrelated histories 로 구성됐으므로
    이 fixture 에서 "커밋 앞섭니다" 메시지가 없어야 한다.
    """
    r = _run_engine(team_with_upstream.team, "on")
    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    # unrelated histories → 오알림 억제
    assert "앞섭니다" not in combined, (
        "unrelated histories 인데 'N커밋 앞섭니다' 알림이 발화됨 — P2 수정 확인")


def test_has_common_ancestor_false_for_unrelated(team_with_upstream):
    """unrelated histories(GitHub template 레포) 에서 has_common_ancestor 가 False."""
    # fetch 먼저(refs/remotes/upstream/main 생성)
    go.fetch_upstream(str(team_with_upstream.team), remote="upstream")
    result = go.has_common_ancestor(str(team_with_upstream.team), "upstream/main")
    assert result is False, (
        "unrelated histories 인데 has_common_ancestor=True — P2 수정 확인")


def test_has_common_ancestor_true_for_related(tmp_path):
    """공통 조상이 있으면 has_common_ancestor 가 True."""
    # 단일 레포 안에서 HEAD 와 로컬 ref 가 공통 조상을 가지는 경우를 모사.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "checkout", "-b", "main")
    (repo / "a.txt").write_text("v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    # 로컬 브랜치 하나 더 만들어 공통 조상을 공유하게 함
    _git(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("v2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature ahead")
    # main 을 "upstream/main" 처럼 사용: HEAD=feature, ref=main
    _git(repo, "checkout", "main")
    result = go.has_common_ancestor(str(repo), "feature")
    assert result is True, "related histories 인데 has_common_ancestor=False"


def test_on_does_NOT_auto_sync(team_with_upstream):
    # 핵심 안전: on 은 fetch 만. 엔진 파일을 자동으로 덮어쓰지 않는다.
    _run_engine(team_with_upstream.team, "on")
    assert not (team_with_upstream.team / "infra" / "newfeature.py").exists(), \
        "on 이 자동 sync 함(금지 위반)"
    assert (team_with_upstream.team / "infra" / "engine.py").read_text() == "team-old\n"


def test_on_no_upstream_still_succeeds(tmp_path):
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "memory").mkdir()
    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr


def test_on_non_git_root_still_succeeds(tmp_path):
    team = tmp_path / "plain"
    (team / "memory").mkdir(parents=True)
    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr


# ── update 동사 (엔진) — 파일 동기화 ──

def test_update_verb_syncs_infra(team_with_upstream):
    r = _run_engine(team_with_upstream.team, "update")
    assert r.returncode == 0, r.stderr
    team = team_with_upstream.team
    assert (team / "infra" / "newfeature.py").exists()
    assert (team / "infra" / "engine.py").read_text() == "v2\n"
    # 팀 데이터 무손상
    assert (team / "memory" / "team-secret.md").read_text() == "DO NOT TOUCH\n"


def test_update_verb_unrelated_histories_no_merge_error(team_with_upstream):
    """가장 중요한 회귀 테스트: unrelated histories 에서 update 가 성공한다."""
    r = _run_engine(team_with_upstream.team, "update")
    assert r.returncode == 0, r.stderr
    assert "unrelated" not in (r.stdout + r.stderr).lower()
    assert "refusing to merge" not in (r.stdout + r.stderr).lower()


def test_update_verb_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "update"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


def test_update_verb_no_upstream_graceful(tmp_path):
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    r = _run_engine(team, "update")
    assert "Traceback" not in r.stderr
    assert r.returncode != 0
    # 수동 등록 안내 노출
    assert "remote add" in (r.stdout + r.stderr)


def test_update_verb_already_uptodate_idempotent(team_with_upstream):
    _run_engine(team_with_upstream.team, "update")     # 1st apply (staged)
    _git(team_with_upstream.team, "add", "-A")
    _git(team_with_upstream.team, "commit", "-m", "applied")
    r = _run_engine(team_with_upstream.team, "update")  # 2nd: nothing
    assert r.returncode == 0, r.stderr
    assert "최신" in (r.stdout + r.stderr)


def test_update_verb_dry_run_no_changes(team_with_upstream):
    team = team_with_upstream.team
    r = _run_engine(team, "update", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "dry-run" in (r.stdout + r.stderr).lower()
    # 실제 변경 0
    assert (team / "infra" / "engine.py").read_text() == "team-old\n"
    assert not (team / "infra" / "newfeature.py").exists()


def test_update_verb_dry_run_with_changes_shows_preview(team_with_upstream):
    """P2 회귀: dry-run 인데 upstream 에 실제 변경이 있으면 **미리보기**를 출력해야 한다.

    버그: cmd_update 가 res.changed 를 먼저 검사했는데 sync 는 dry_run 시 changed=False
    (diff 만 채움)로 돌려줘, 변경이 있어도 "이미 최신"으로 잘못 출력하고 미리보기에 도달
    못 했다. 수정 후 diff 유무로 분기 → 미리보기(바뀔 파일 목록) 출력 + 실제 변경 0.
    """
    team = team_with_upstream.team
    r = _run_engine(team, "update", "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    # 미리보기 도달: "동기화하면 바뀔 파일" + 변경 파일명이 보여야 한다
    assert "바뀔 파일" in out, out
    assert "engine.py" in out, out
    # "이미 최신" 으로 오출력하면 안 됨(버그 재현 방지)
    assert "이미 최신" not in out, out
    # 실제 변경 0(미리보기만) — working tree 무손상
    assert (team / "infra" / "engine.py").read_text() == "team-old\n"
    assert not (team / "infra" / "newfeature.py").exists()


def test_update_verb_dry_run_uptodate_says_latest(team_with_upstream):
    """dry-run + 실제 변경 없음 → "이미 최신"(diff 빈 분기). 위 케이스의 대칭."""
    team = team_with_upstream.team
    # 먼저 동기화 적용 + 커밋 → upstream 과 동일 상태로 만든다
    _run_engine(team, "update")
    _git(team, "add", "-A")
    _git(team, "commit", "-m", "applied")
    r = _run_engine(team, "update", "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert "이미 최신" in out, out
    assert "바뀔 파일" not in out, out


def test_update_verb_dirty_guard_blocks(team_with_upstream):
    team = team_with_upstream.team
    (team / "infra" / "engine.py").write_text("LOCAL EDIT\n")
    r = _run_engine(team, "update")
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    # 로컬 변경 보존(덮어쓰기 0)
    assert (team / "infra" / "engine.py").read_text() == "LOCAL EDIT\n"


def test_update_verb_no_auto_commit(team_with_upstream):
    head_before = _git(team_with_upstream.team, "rev-parse", "HEAD").stdout.strip()
    _run_engine(team_with_upstream.team, "update")
    head_after = _git(team_with_upstream.team, "rev-parse", "HEAD").stdout.strip()
    assert head_before == head_after, "update 가 자동 커밋함(금지 위반)"


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
    assert r.returncode == 0, r.stderr


def test_update_offline_upstream_no_hang(tmp_path):
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "infra").mkdir()
    (team / "infra" / "x.py").write_text("x")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "c")
    _git(team, "remote", "add", "upstream", "http://192.0.2.1/r.git")
    import time
    t0 = time.time()
    r = _run_engine(team, "update")
    elapsed = time.time() - t0
    assert elapsed < 20, f"update 가 {elapsed:.1f}s 매달림(offline hang)"
    assert r.returncode != 0  # fetch 실패 → 비치명 실패
    assert "Traceback" not in r.stderr
