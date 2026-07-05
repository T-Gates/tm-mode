"""#36 PR2 — validation 층(conformance/·tests/) blob-history 동기화.

설계 확정(이슈 #36 codex 2R): 파일 단위 판정 — dirty(커밋 안 된 로컬 수정) 우선 skip,
local blob 이 upstream 히스토리에 존재=safe 갱신, 미존재=skip, ref 에 없는 local=local_only.
tm on 은 validation 무적용(알림만). XDG skip-cache 로 반복 skip 축약. --force --backup.

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


def _write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.fixture
def team_with_upstream(tmp_path, monkeypatch):
    """upstream(bare) + team(clone). validation 파일들의 stale/local-modified/local-only
    시나리오를 심는다. XDG 격리."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    _git(tmp_path, "init", "--bare", str(upstream))
    # CI 러너는 init.defaultBranch 미설정 → bare HEAD 가 master 를 가리켜
    # main push 후 clone 이 빈 트리가 된다(로컬은 ~/.gitconfig 로 우연히 통과).
    _git(upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    _write(seed, "conformance/check.py", "v1\n")
    _write(seed, "tests/test_a.py", "a1\n")
    _write(seed, "tests/test_stale.py", "stale-v1\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "up v1")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    # team = clone (공통 히스토리 — blob-history 판정이 동작하는 정상 케이스)
    _git(tmp_path, "clone", str(upstream), str(team))
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "remote", "add", "upstream", str(upstream))

    # upstream 앞섬: check.py v2, test_stale v2, 신규 test_new
    _write(seed, "conformance/check.py", "v2\n")
    _write(seed, "tests/test_stale.py", "stale-v2\n")
    _write(seed, "tests/test_new.py", "new\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "up v2")
    _git(seed, "push")
    _git(team, "fetch", "upstream")
    return team


def _plan(team):
    return go.plan_validation_sync(str(team), "upstream/main")


# ── 판정 계약 ───────────────────────────────────────────────────────

def test_stale_unmodified_is_safe(team_with_upstream):
    """로컬 무수정이고 upstream 이 앞선 파일 → safe(blob 이 upstream 히스토리에 있음)."""
    plan = _plan(team_with_upstream)
    assert "tests/test_stale.py" in plan.safe_paths
    assert "conformance/check.py" in plan.safe_paths


def test_upstream_addition_is_safe(team_with_upstream):
    """upstream 신규 파일(local 없음) → safe addition."""
    plan = _plan(team_with_upstream)
    assert "tests/test_new.py" in plan.safe_paths


def test_up_to_date_not_in_safe(team_with_upstream):
    """local==current 파일(test_a)은 up_to_date — safe 아님."""
    plan = _plan(team_with_upstream)
    assert "tests/test_a.py" in plan.up_to_date
    assert "tests/test_a.py" not in plan.safe_paths


def test_committed_local_modified_is_skipped(team_with_upstream):
    """로컬에서 커밋한 수정(upstream 히스토리에 없는 blob) → skip(local-unclassified)."""
    team = team_with_upstream
    _write(team, "conformance/check.py", "LOCAL PATCH\n")
    _git(team, "add", "conformance/check.py")
    _git(team, "commit", "-m", "local patch")
    plan = _plan(team)
    skipped = {s.path for s in plan.skipped}
    assert "conformance/check.py" in skipped
    assert "conformance/check.py" not in plan.safe_paths


def test_uncommitted_local_modified_is_skipped(team_with_upstream):
    """커밋 안 된 로컬 수정 → dirty 우선 skip(checkout 이 덮으면 유실 — 핵심 갭)."""
    team = team_with_upstream
    _write(team, "conformance/check.py", "UNCOMMITTED EDIT\n")  # dirty, not staged
    plan = _plan(team)
    skipped = {s.path: s for s in plan.skipped}
    assert "conformance/check.py" in skipped
    assert skipped["conformance/check.py"].reason == "dirty"
    assert "conformance/check.py" not in plan.safe_paths


def test_local_only_file_is_local_only(team_with_upstream):
    """local 에만 있고 upstream ref 에 없는 파일 → local_only(v1 삭제 안 함)."""
    team = team_with_upstream
    _write(team, "tests/test_instance.py", "instance-only\n")
    _git(team, "add", "tests/test_instance.py")
    _git(team, "commit", "-m", "instance test")
    plan = _plan(team)
    lo = {s.path for s in plan.local_only}
    assert "tests/test_instance.py" in lo
    assert "tests/test_instance.py" not in plan.safe_paths


def test_reserved_and_cache_excluded(team_with_upstream):
    """tests/local·conformance/local·__pycache__·*.pyc 는 대상에서 제외."""
    team = team_with_upstream
    _write(team, "tests/local/secret.py", "instance\n")
    _write(team, "conformance/local/x.py", "instance\n")
    _write(team, "tests/__pycache__/foo.pyc", "cache\n")
    _git(team, "add", "-A")
    _git(team, "commit", "-m", "local+cache")
    plan = _plan(team)
    allp = set(plan.safe_paths) | {s.path for s in plan.skipped} \
        | {s.path for s in plan.local_only} | set(plan.up_to_date)
    assert not any("tests/local/" in p for p in allp)
    assert not any("conformance/local/" in p for p in allp)
    assert not any("__pycache__" in p for p in allp)
    assert not any(p.endswith(".pyc") for p in allp)


def test_shallow_repo_skips_all(tmp_path, monkeypatch):
    """shallow clone → validation 전체 보수 skip(엔진은 별개로 정상)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    upstream = tmp_path / "up.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t"); _git(seed, "config", "user.email", "t@t")
    _write(seed, "tests/test_a.py", "a\n")
    _git(seed, "add", "."); _git(seed, "commit", "-m", "v1")
    _git(seed, "branch", "-M", "main"); _git(seed, "push", "-u", "origin", "main")
    team = tmp_path / "team"
    _git(tmp_path, "clone", "--depth", "1", str(upstream), str(team))
    _git(team, "config", "user.name", "t"); _git(team, "config", "user.email", "t@t")
    _git(team, "remote", "add", "upstream", str(upstream))
    _git(team, "fetch", "--depth", "1", "upstream")
    plan = go.plan_validation_sync(str(team), "upstream/main")
    assert plan.shallow is True
    assert plan.safe_paths == ()


def test_skip_hash_deterministic(team_with_upstream):
    """skip_hash 는 skipped 집합에 결정적 — 같은 상태 재계산 시 동일."""
    team = team_with_upstream
    _write(team, "conformance/check.py", "edit\n")
    h1 = _plan(team).skip_hash
    h2 = _plan(team).skip_hash
    assert h1 and h1 == h2


# ── apply_validation_sync + backup ──────────────────────────────────

def test_apply_checks_out_safe_only(team_with_upstream):
    """apply: safe_paths 만 checkout(staged), skip/local_only 는 무접촉."""
    team = team_with_upstream
    # 로컬 커밋 수정 1개(skip 대상) + dirty 1개
    _write(team, "conformance/check.py", "LOCAL\n")
    _git(team, "add", "conformance/check.py"); _git(team, "commit", "-m", "local")
    plan = _plan(team)
    res = go.apply_validation_sync(str(team), "upstream/main", plan)
    assert res.ok and res.changed
    # safe(test_stale)는 갱신됨
    assert (team / "tests" / "test_stale.py").read_text() == "stale-v2\n"
    assert "tests/test_stale.py" in res.applied
    # skip(check.py 로컬수정)은 보존
    assert (team / "conformance" / "check.py").read_text() == "LOCAL\n"
    assert "conformance/check.py" not in res.applied


def test_apply_no_safe_is_noop(team_with_upstream):
    """safe 가 없으면 changed=False(무접촉)."""
    team = team_with_upstream
    # 모든 stale 을 미리 최신으로 → safe 0
    _git(team, "checkout", "upstream/main", "--", "conformance", "tests")
    _git(team, "add", "-A"); _git(team, "commit", "-m", "sync all")
    plan = _plan(team)
    res = go.apply_validation_sync(str(team), "upstream/main", plan)
    assert res.changed is False


def test_force_backup_creates_patch_and_overwrites(team_with_upstream):
    """--force --backup: skip(로컬수정)도 덮되 backup patch 선행 생성."""
    team = team_with_upstream
    _write(team, "conformance/check.py", "LOCAL PATCH\n")
    _git(team, "add", "conformance/check.py"); _git(team, "commit", "-m", "local")
    plan = _plan(team)
    res = go.apply_validation_sync(str(team), "upstream/main", plan,
                                   force=True, backup=True)
    assert res.ok and res.changed
    assert "conformance/check.py" in res.forced
    assert res.backup_path and Path(res.backup_path).is_file()
    # 덮여서 upstream v2
    assert (team / "conformance" / "check.py").read_text() == "v2\n"
    # 백업 패치에 로컬 내용 흔적
    assert "LOCAL PATCH" in Path(res.backup_path).read_text(encoding="utf-8")


# ── skip-cache ──────────────────────────────────────────────────────

def test_skip_cache_roundtrip_and_repeat(tmp_path, monkeypatch):
    """skip-cache: 같은 skip_hash 반복이면 '이전과 동일' 판정(축약용)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    root = str(tmp_path / "team")
    assert go.validation_skip_seen(root, "hash-abc") is False  # 최초
    go.record_validation_skip(root, "hash-abc", counts={"skipped": 2})
    assert go.validation_skip_seen(root, "hash-abc") is True   # 반복
    assert go.validation_skip_seen(root, "hash-xyz") is False  # 다른 hash
    p = Path(go.validation_cache_path(root))
    assert p.is_file() and str(tmp_path / "xdg") in str(p)


# ── cmd_update / on 배선 통합 ────────────────────────────────────────

import runpy
ENGINE = REPO / "infra" / "teammode.py"


def _run_engine(root, *argv):
    """teammode.py main() in-proc 호출 — 출력 캡처."""
    import io, contextlib
    mod = runpy.run_path(str(ENGINE), run_name="__vsync_test__")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = mod["main"]([*argv, "--root", str(root)])
    return rc, buf.getvalue()


def test_cmd_update_applies_validation(team_with_upstream, monkeypatch):
    """tm-mode update: 엔진 뒤 validation safe 파일도 checkout(staged)."""
    team = team_with_upstream
    rc, out = _run_engine(team, "update")
    assert rc == 0, out
    assert "validation 동기화 완료" in out
    # safe 파일 실제 갱신
    assert (team / "tests" / "test_stale.py").read_text() == "stale-v2\n"


def test_on_notifies_validation_but_does_not_apply(team_with_upstream, monkeypatch):
    """tm on 자동 경로: validation 은 '업데이트 가능' 알림만, 파일 미적용."""
    team = team_with_upstream
    monkeypatch.setattr("sys.modules", sys.modules)
    import io, contextlib
    mod = runpy.run_path(str(ENGINE), run_name="__on_vsync__")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        mod["auto_update_on_start"](team)
    out = buf.getvalue()
    # 알림은 나오되
    assert "validation 업데이트 가능" in out
    # 파일은 안 바뀜(적용 안 함) — test_stale 은 여전히 stale-v1
    assert (team / "tests" / "test_stale.py").read_text() == "stale-v1\n"


# ── codex 적대검수 반영 (P1×3·P2×2) ────────────────────────────────

def test_cross_path_blob_collision_not_safe(team_with_upstream):
    """[P1] 크로스패스 blob 충돌: 로컬 의도 수정의 blob 이 upstream 역사의 **다른
    경로** blob 과 우연히 같아도 safe 로 오판하지 않는다(경로별 히스토리 판정)."""
    team = team_with_upstream
    # upstream 역사에 존재하는 내용("stale-v1\n" — tests/test_stale.py 의 v1 blob)을
    # 전혀 다른 경로(conformance/check.py)에 로컬 의도 수정으로 커밋
    _write(team, "conformance/check.py", "stale-v1\n")
    _git(team, "add", "conformance/check.py")
    _git(team, "commit", "-m", "intentional local edit (blob collides cross-path)")
    plan = _plan(team)
    assert "conformance/check.py" not in plan.safe_paths, \
        "크로스패스 blob 충돌이 safe 로 오판됨 — 로컬 수정 유실 경로"
    assert "conformance/check.py" in {s.path for s in plan.skipped}


def test_apply_rechecks_dirty_before_checkout(team_with_upstream):
    """[P1] plan 이후·apply 이전에 생긴 편집(stale plan)은 checkout 에서 제외·보존."""
    team = team_with_upstream
    plan = _plan(team)
    assert "tests/test_stale.py" in plan.safe_paths
    # plan 뒤에 사용자가 편집(커밋 안 함) — stale plan 그대로 apply
    _write(team, "tests/test_stale.py", "EDITED AFTER PLAN\n")
    res = go.apply_validation_sync(str(team), "upstream/main", plan)
    assert res.ok
    assert (team / "tests" / "test_stale.py").read_text() == "EDITED AFTER PLAN\n", \
        "stale plan 이 plan 이후 편집을 덮었다(유실)"
    assert "tests/test_stale.py" not in res.applied


def test_force_aborts_when_backup_fails(team_with_upstream, tmp_path, monkeypatch):
    """[P1] --force --backup 에서 백업 기록 실패 → 덮지 않고 ok=False 중단."""
    team = team_with_upstream
    _write(team, "conformance/check.py", "LOCAL\n")
    _git(team, "add", "conformance/check.py"); _git(team, "commit", "-m", "local")
    plan = _plan(team)
    # 백업 목적지(XDG sync 디렉토리)를 파일로 선점해 백업 기록을 실패시킨다
    sync_dir = Path(go._state_dir()) / "sync"
    sync_dir.parent.mkdir(parents=True, exist_ok=True)
    if sync_dir.exists():
        import shutil
        shutil.rmtree(sync_dir)
    sync_dir.write_text("not a dir", encoding="utf-8")
    res = go.apply_validation_sync(str(team), "upstream/main", plan,
                                   force=True, backup=True)
    assert res.ok is False, "백업 실패인데 force 덮어쓰기가 진행됨"
    assert (team / "conformance" / "check.py").read_text() == "LOCAL\n"


def test_force_applies_even_when_no_safe(team_with_upstream):
    """[P2] safe 0 + skip 만 있어도 --force 는 동작한다(무시되지 않음)."""
    team = team_with_upstream
    # 모든 safe 를 먼저 최신화 → 남는 차이는 로컬 수정 1개(skip)뿐
    _git(team, "checkout", "upstream/main", "--", "conformance", "tests")
    _git(team, "add", "-A"); _git(team, "commit", "-m", "sync all")
    _write(team, "conformance/check.py", "LOCAL ONLY DIFF\n")
    _git(team, "add", "conformance/check.py"); _git(team, "commit", "-m", "local")
    plan = _plan(team)
    assert plan.safe_paths == () and plan.skipped
    res = go.apply_validation_sync(str(team), "upstream/main", plan,
                                   force=True, backup=True)
    assert res.ok and res.changed and res.forced
    assert (team / "conformance" / "check.py").read_text() == "v2\n"


def test_force_excludes_local_only_and_dirty_local_only(team_with_upstream):
    """[P2] ref 에 없는 로컬 파일(dirty 포함)은 force 대상에서 제외 — checkout 실패 방지."""
    team = team_with_upstream
    _write(team, "tests/only_here.py", "mine\n")  # untracked = dirty + ref 부재
    plan = _plan(team)
    res = go.apply_validation_sync(str(team), "upstream/main", plan,
                                   force=True, backup=True)
    assert res.ok, res.detail
    assert "tests/only_here.py" not in res.forced
    assert (team / "tests" / "only_here.py").read_text() == "mine\n"


def test_cmd_update_force_applies_with_no_safe(team_with_upstream):
    """[P2] tm-mode update --force: safe 0 + skip 만 있어도 배선이 apply 를 호출."""
    team = team_with_upstream
    _git(team, "checkout", "upstream/main", "--", "conformance", "tests")
    _git(team, "add", "-A"); _git(team, "commit", "-m", "sync all")
    _write(team, "conformance/check.py", "LOCAL\n")
    _git(team, "add", "conformance/check.py"); _git(team, "commit", "-m", "local")
    rc, out = _run_engine(team, "update", "--force")
    assert rc == 0, out
    assert (team / "conformance" / "check.py").read_text() == "v2\n", \
        "--force 가 무시됨(safe 0 게이트)"


def test_force_excludes_untracked_collision_with_upstream_new(team_with_upstream):
    """[재검수] upstream 신규와 같은 path 의 untracked 로컬 파일은 force 도 보존
    (patch 백업이 untracked 를 못 담아 백업 없는 덮어쓰기가 됨)."""
    team = team_with_upstream
    _write(team, "tests/test_new.py", "my untracked draft\n")  # upstream 신규와 충돌
    plan = _plan(team)
    res = go.apply_validation_sync(str(team), "upstream/main", plan,
                                   force=True, backup=True)
    assert res.ok, res.detail
    assert "tests/test_new.py" not in res.forced
    assert (team / "tests" / "test_new.py").read_text() == "my untracked draft\n"
