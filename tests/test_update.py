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
    # timeout=60: 러너 보호 하드캡 — 개별 테스트의 벽시계 단정(<20s 등)과 별개로,
    # 엔진 subprocess 가 폭주해도 suite 전체를 물고 늘어지지 않게(#36 flaky 진단).
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)


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


def test_on_auto_syncs_and_commits(team_with_upstream):
    """작업 D: on 시 upstream 변경이 있으면 자동 동기화 + 자동 커밋.

    이전 동작(fetch만 자동, sync 금지)이 변경됨 — auto_update_on_start() 도입으로
    on 시 변경된 엔진 파일이 자동 적용되고 커밋까지 생성된다.
    """
    team = team_with_upstream.team
    head_before = _git(team, "rev-parse", "HEAD").stdout.strip()
    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr
    # 자동 동기화: upstream 변경이 적용됨
    assert (team / "infra" / "newfeature.py").exists(), \
        "on 이 upstream 변경을 자동 적용하지 않음(작업 D 미구현)"
    assert (team / "infra" / "engine.py").read_text() == "v2\n"
    # 자동 커밋: HEAD 가 변경됨
    head_after = _git(team, "rev-parse", "HEAD").stdout.strip()
    assert head_before != head_after, "on 이 자동 커밋을 생성하지 않음"
    # 커밋 메시지 확인
    msg = _git(team, "log", "--format=%s", "-1").stdout.strip()
    assert "[auto]" in msg, f"커밋 메시지에 [auto] 없음: {msg!r}"
    # 팀 데이터 무손상
    assert (team / "memory" / "team-secret.md").read_text() == "DO NOT TOUCH\n"


def test_on_auto_sync_autopush_nonblocking(team_with_upstream):
    """작업 D + 6/23 자동push 철학: 자동 커밋은 생성되고 자동 push 를 *시도*하되,
    원격(origin) 미설정이면 push 는 비차단으로 우아하게 실패하고 on 은 성공한다.

    핵심: auto_update 의 push 는 도입 레포의 기본 원격(origin)으로 향한다 —
    템플릿 원본 upstream 으로 엔진 sync 를 되쏘지 않는다. 이 fixture 의 team 은
    origin 이 없어 push 가 실패하지만(비차단), upstream bare repo 의 HEAD 는 절대
    변하지 않아야 한다(엔진 sync 가 upstream 으로 push 되지 않음을 검증).
    """
    team = team_with_upstream.team
    upstream = team_with_upstream.upstream

    # on 실행 전 upstream HEAD 기록
    upstream_head_before = _git(upstream, "rev-parse", "HEAD").stdout.strip()

    r = _run_engine(team, "on")
    # push 실패(origin 없음)는 비차단 — on 은 성공해야 한다
    assert r.returncode == 0, r.stderr

    # 로컬 자동 커밋은 생성됨
    msg = _git(team, "log", "--format=%s", "-1").stdout.strip()
    assert "[auto]" in msg, f"자동 커밋이 생성되지 않음: {msg!r}"

    # upstream(템플릿 원본) HEAD 는 불변 — 엔진 sync 가 upstream 으로 push 되지 않음
    upstream_head_after = _git(upstream, "rev-parse", "HEAD").stdout.strip()
    assert upstream_head_before == upstream_head_after, (
        f"upstream HEAD 가 변경됨: {upstream_head_before!r} → {upstream_head_after!r} "
        "— auto_update 가 upstream 으로 push 한 것으로 보임(금지)"
    )


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

@pytest.mark.skipif(os.name == "nt", reason="git-remote-sleep 셸 helper 는 POSIX 전제")
def test_on_offline_upstream_no_hang(tmp_path):
    """on 자동 업데이트의 hang 내성 — 결정적 remote helper 로(실네트워크 무접촉,
    update 테스트와 동일 전환 사유: TEST-NET blackhole 은 부하에서 flaky, #36 진단)."""
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "memory").mkdir()
    (team / "x").write_text("x")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "c")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    helper = bin_dir / "git-remote-sleep"
    helper.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    helper.chmod(0o755)
    _git(team, "remote", "add", "upstream", "sleep::repo")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH','')}"}
    import time
    t0 = time.time()
    r = _run_engine(team, "on", env=env)
    elapsed = time.time() - t0
    assert elapsed < 20, f"on 이 {elapsed:.1f}s 매달림(offline upstream hang)"
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(os.name == "nt", reason="git-remote-sleep 셸 helper 는 POSIX 전제")
def test_update_offline_upstream_no_hang(tmp_path):
    """offline/행업 fetch 에서 update 가 20초 내 비치명 실패하는가.

    종전엔 TEST-NET blackhole(http://192.0.2.1)로 실 TCP 를 태웠는데, OS/libcurl/
    시스템 부하에 따라 벽시계 결정성을 잃어 full suite 에서 121s 오탐이 났다
    (#36 진단 — codex 합의: 선재 flaky). git **remote helper** 로 결정적 hang 을
    만들어(fetch 프로세스가 sleep) run_git 의 timeout+killpg 계약만 검증한다 —
    실네트워크 무접촉.
    """
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "infra").mkdir()
    (team / "infra" / "x.py").write_text("x")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "c")

    # 결정적 hang: git 이 `sleep::` URL 을 만나면 PATH 의 git-remote-sleep 을 실행한다.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    helper = bin_dir / "git-remote-sleep"
    helper.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    helper.chmod(0o755)
    _git(team, "remote", "add", "upstream", "sleep::repo")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH','')}"}

    import time
    t0 = time.time()
    r = _run_engine(team, "update", env=env)
    elapsed = time.time() - t0
    assert elapsed < 20, f"update 가 {elapsed:.1f}s 매달림(hang kill 실패)"
    assert r.returncode != 0  # fetch 실패 → 비치명 실패
    assert "Traceback" not in r.stderr


def test_update_missing_remote_fails_fast(tmp_path):
    """존재하지 않는 로컬 remote(file://) — 즉시 결정적 실패(네트워크 무관)."""
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "infra").mkdir()
    (team / "infra" / "x.py").write_text("x")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "c")
    _git(team, "remote", "add", "upstream",
         f"file://{tmp_path}/missing.git")
    r = _run_engine(team, "update")
    assert r.returncode != 0
    assert "Traceback" not in r.stderr


# ── 작업 D: auto_update_on_start 전용 테스트 ──

def test_on_auto_update_dirty_guard_skip_with_notice(team_with_upstream):
    """작업 D: dirty 상태면 자동 업데이트 skip + 알림. on 은 정상 성공."""
    team = team_with_upstream.team
    # 대상 경로(infra/)에 커밋 안 된 로컬 변경 심기
    (team / "infra" / "engine.py").write_text("LOCAL DIRTY\n")
    r = _run_engine(team, "on")
    # on 은 dirty 여도 성공(return 0)
    assert r.returncode == 0, r.stderr
    # 로컬 변경 보존(덮어쓰기 0)
    assert (team / "infra" / "engine.py").read_text() == "LOCAL DIRTY\n"
    # upstream 신규 파일도 적용 안 됨
    assert not (team / "infra" / "newfeature.py").exists()
    # 알림 메시지 출력됨
    combined = r.stdout + r.stderr
    assert "skip" in combined.lower() or "커밋 안 된" in combined, \
        f"dirty 알림이 없음: {combined!r}"


def test_on_auto_update_fetch_fail_no_crash(tmp_path):
    """작업 D: fetch 실패(remote 없음)면 auto_update skip, on 은 정상 성공."""
    team = tmp_path / "team"
    _git(tmp_path, "init", str(team))
    (team / "memory").mkdir()
    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr


def test_on_auto_update_idempotent_no_double_commit(team_with_upstream):
    """작업 D: 변경 없으면(이미 최신) 자동 커밋 안 생김, on 정상."""
    team = team_with_upstream.team
    # 먼저 on 으로 자동 커밋 한 번
    r1 = _run_engine(team, "on")
    assert r1.returncode == 0
    head_after_first = _git(team, "rev-parse", "HEAD").stdout.strip()
    # off 후 다시 on — 이미 최신 상태
    _run_engine(team, "off")
    r2 = _run_engine(team, "on")
    assert r2.returncode == 0
    head_after_second = _git(team, "rev-parse", "HEAD").stdout.strip()
    # HEAD 불변(추가 커밋 없음)
    assert head_after_first == head_after_second, \
        "변경 없는데 auto_update 가 커밋을 중복 생성함"


def test_on_auto_update_commit_message_format(team_with_upstream):
    """작업 D: 자동 커밋 메시지가 chore: sync ... [auto] 형식이어야 한다."""
    team = team_with_upstream.team
    r = _run_engine(team, "on")
    assert r.returncode == 0
    msg = _git(team, "log", "--format=%s", "-1").stdout.strip()
    assert "chore: sync teammode engine from upstream [auto]" == msg, \
        f"커밋 메시지 형식 불일치: {msg!r}"


def test_on_auto_update_paths_limited_not_add_all(team_with_upstream):
    """작업 D: 자동 커밋은 paths 한정(infra/)이고 memory/ 등 다른 파일을 휩쓸지 않는다."""
    team = team_with_upstream.team
    # memory/ 에 staged 파일 추가(커밋 안 함)
    mem_file = team / "memory" / "staged-but-not-auto.md"
    mem_file.write_text("should not be auto-committed\n")
    _git(team, "add", str(mem_file))
    # on 실행 — auto_update 가 이 staged 파일을 함께 커밋하면 안 됨
    r = _run_engine(team, "on")
    assert r.returncode == 0
    # memory/ 파일은 커밋에 포함 안 됨 — staged 잔존
    staged = _git(team, "diff", "--cached", "--name-only").stdout
    assert "staged-but-not-auto" in staged, \
        "auto_update 가 memory/ staged 파일을 커밋에 휩쓸었음(paths=None 금지 위반)"


def test_on_auto_update_blocked_when_infra_staged(team_with_upstream):
    """작업 D: infra/ 안에 사용자 staged 변경이 있으면 자동 커밋 자체를 skip 한다.

    dirty 가드(SYNC_PATHS 범위): infra/ 에 커밋 안 된 변경이 있으면 auto_update_on_start
    가 blocked=True 로 early-return 하므로 HEAD 가 변하지 않는다.
    사용자 staged 변경이 자동 커밋에 휩쓸리지 않고 잔존함을 검증한다.
    """
    team = team_with_upstream.team
    head_before = _git(team, "rev-parse", "HEAD").stdout.strip()

    # infra/ 에 사용자 staged 변경 추가(커밋 안 함)
    user_infra_file = team / "infra" / "user-pending.py"
    user_infra_file.write_text("# 사용자 작업 중\n")
    _git(team, "add", str(user_infra_file))

    r = _run_engine(team, "on")
    assert r.returncode == 0, r.stderr  # on 자체는 성공해야 함(가드가 막아도 on 은 산다)

    head_after = _git(team, "rev-parse", "HEAD").stdout.strip()
    assert head_before == head_after, (
        "infra/ staged 변경이 있는데 auto_update 가 커밋을 생성했음 — "
        "dirty 가드 위반: 사용자 변경이 자동 커밋에 휩쓸렸거나 가드가 작동 안 함"
    )

    # 사용자 staged 파일이 그대로 staged 에 남아 있어야 함
    staged = _git(team, "diff", "--cached", "--name-only").stdout
    assert "user-pending.py" in staged, (
        "사용자가 staged 한 infra/user-pending.py 가 사라짐 — "
        "auto_update 가 무언가를 커밋하거나 staged 를 건드린 것으로 보임"
    )


def test_update_verb_still_no_auto_commit(team_with_upstream):
    """작업 D 이후도 cmd_update(update 동사)는 자동 커밋 없어야 한다(기존 동작 불변)."""
    head_before = _git(team_with_upstream.team, "rev-parse", "HEAD").stdout.strip()
    _run_engine(team_with_upstream.team, "update")
    head_after = _git(team_with_upstream.team, "rev-parse", "HEAD").stdout.strip()
    assert head_before == head_after, "update 동사가 자동 커밋함(기존 동작 훼손)"
