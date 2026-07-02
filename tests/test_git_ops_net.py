"""이슈 #33/#34 — git_ops 네트워크 타임아웃 분리 + no-upstream push 자동 복구 테스트.

#33: DEFAULT_TIMEOUT=2s 가 실 GitHub SSH 왕복(~2.5s+)을 죽였다. 네트워크 동사
(pull/fetch/push 를 포함하는 함수)는 NET_TIMEOUT 을 기본값으로 쓰고, 순수 로컬
동사(rev-list·log·status 류)는 세션 시작 스냅함을 위해 DEFAULT_TIMEOUT 을 유지한다.

#34: upstream 미설정 브랜치에서 평문 `git push` 는 영원히 실패한다. do_commit 의
push 단계가 no-upstream 서명을 감지하면 `push -u origin HEAD` 로 1회 재시도한다.

codex 리뷰 후속(PR #35):
  - -u 재시도가 non-ff 로 거부되면(원격에 같은 이름 브랜치가 이미 앞서 있음)
    fetch→rebase→push -u 복구로 이어져야 한다(dead-end 금지).
  - do_commit 내부의 **로컬** 하위호출(add·staged-diff·commit)은 함수의 네트워크
    timeout 이 아니라 DEFAULT_TIMEOUT 을 써야 한다(선언된 분리 복원).
  - 네트워크 훅(session-start·auto-commit)의 manifest timeout 은 NET_TIMEOUT 기반
    최악 순차 네트워크 호출을 덮어야 한다(3s 는 훅 러너가 git_ops 반환 전에 죽임).

네트워크는 /tmp 로컬 fake remote(bare) 로 모사 — 실 원격·실 ~/.claude 무접촉.
"""
import inspect
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops  # noqa: E402


# ──────────────────────────────────────────────────────────────────
# #33 — NET_TIMEOUT 상수 + 함수별 기본값 재분류
# ──────────────────────────────────────────────────────────────────

def test_net_timeout_exists_and_exceeds_default():
    assert hasattr(git_ops, "NET_TIMEOUT")
    assert git_ops.NET_TIMEOUT > git_ops.DEFAULT_TIMEOUT
    assert git_ops.NET_TIMEOUT == 10


def _timeout_default(func):
    return inspect.signature(func).parameters["timeout"].default


@pytest.mark.parametrize("name", [
    "do_pull",            # git pull — 네트워크
    "do_reconcile",       # 내부 fetch — 네트워크
    "do_commit",          # push + non-ff 복구 fetch/재push — 네트워크
    "fetch_upstream",     # git fetch — 네트워크
    "sync_from_upstream",  # 내부 fetch_upstream — 네트워크
])
def test_network_verbs_default_to_net_timeout(name):
    assert _timeout_default(getattr(git_ops, name)) == git_ops.NET_TIMEOUT


@pytest.mark.parametrize("name", [
    "ahead_behind",           # rev-list — 로컬
    "has_common_ancestor",    # merge-base — 로컬
    "count_behind",           # rev-list — 로컬
    "upstream_changes",       # log — 로컬
    "detect_default_branch",  # symbolic-ref/rev-parse — 로컬
    "diff_paths",             # diff — 로컬
    "read_upstream_notice",   # show(로컬 remote-tracking ref) — 로컬
])
def test_local_verbs_stay_at_default_timeout(name):
    assert _timeout_default(getattr(git_ops, name)) == git_ops.DEFAULT_TIMEOUT


# ──────────────────────────────────────────────────────────────────
# #34 — no-upstream 브랜치 push 자동 복구(-u origin HEAD 1회 재시도)
# ──────────────────────────────────────────────────────────────────

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
def new_branch_repo(tmp_path):
    """bare origin + clone, clone 은 upstream 없는 새 브랜치(feat/x) 체크아웃 상태."""
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(clone))
    # do_commit(제품 코드)의 커밋은 _git 헬퍼 env 를 못 받는다 — CI 러너(글로벌 git
    # 설정 없음)에선 identity 자동감지가 fatal 이므로 레포 로컬 config 로 고정.
    _git(clone, "config", "user.name", "t")
    _git(clone, "config", "user.email", "t@t")
    (clone / "a.txt").write_text("v1\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "c1")
    _git(clone, "branch", "-M", "main")
    _git(clone, "push", "-u", "origin", "main")
    # upstream 없는 새 브랜치 — 평문 `git push` 는 no-upstream 으로 거부된다.
    _git(clone, "checkout", "-b", "feat/x")
    return origin, clone


def test_do_commit_push_sets_upstream_on_new_branch(new_branch_repo):
    origin, clone = new_branch_repo
    (clone / "b.txt").write_text("v2\n")
    res = git_ops.do_commit(str(clone), "feat: b", push=True)
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is True, res.detail
    # 원격(bare)에 feat/x 가 실제로 생겼는지
    rp = _git(origin, "rev-parse", "feat/x", check=False)
    assert rp.returncode == 0, rp.stderr
    # 재시도 경로(-u) 를 탔다는 표식
    assert "set upstream" in res.detail


def test_do_commit_second_push_uses_now_set_upstream(new_branch_repo):
    origin, clone = new_branch_repo
    (clone / "b.txt").write_text("v2\n")
    first = git_ops.do_commit(str(clone), "feat: b", push=True)
    assert first.pushed is True, first.detail
    # -u 재시도가 upstream 을 심었으므로 두 번째부턴 평문 push 가 그냥 성공한다.
    (clone / "c.txt").write_text("v3\n")
    second = git_ops.do_commit(str(clone), "feat: c", push=True)
    assert second.ok is True
    assert second.pushed is True, second.detail
    assert "set upstream" not in second.detail
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(origin, "rev-parse", "feat/x").stdout.strip()
    assert head == remote_head


# ──────────────────────────────────────────────────────────────────
# codex P2-1 — no-upstream 재시도(-u)가 non-ff 로 막히면 rebase 복구로 이어진다
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def diverged_new_branch_repo(tmp_path):
    """원격에 feat/x 가 이미 **앞서** 존재 + 로컬 feat/x 는 upstream 미설정.

    시나리오(codex P2-1): 다른 기기가 feat/x 를 먼저 push 해 원격 feat/x 가 커밋
    하나 앞서 있는데, 이 클론의 feat/x 는 (더 옛 지점에서 만들어져) upstream 연결이
    없다. 평문 push → no-upstream → `push -u` 재시도 → non-ff 거부. 여기서 끝나면
    안 되고 fetch→rebase→push -u 복구로 이어져야 한다.
    """
    origin = tmp_path / "origin.git"
    clone_a = tmp_path / "clone_a"
    clone_b = tmp_path / "clone_b"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(clone_a))
    _git(clone_a, "config", "user.name", "t")
    _git(clone_a, "config", "user.email", "t@t")
    (clone_a / "a.txt").write_text("v1\n")
    _git(clone_a, "add", ".")
    _git(clone_a, "commit", "-m", "c1")
    _git(clone_a, "branch", "-M", "main")
    _git(clone_a, "push", "-u", "origin", "main")
    # 다른 기기(clone_b)가 feat/x 를 먼저 push — 원격 feat/x = c1 + remote-only.
    _git(tmp_path, "clone", str(origin), str(clone_b))
    _git(clone_b, "config", "user.name", "t")
    _git(clone_b, "config", "user.email", "t@t")
    _git(clone_b, "checkout", "-b", "feat/x")
    (clone_b / "remote.txt").write_text("from other device\n")
    _git(clone_b, "add", ".")
    _git(clone_b, "commit", "-m", "x-remote")
    _git(clone_b, "push", "-u", "origin", "feat/x")
    # clone_a: 옛 지점(main=c1)에서 같은 이름 브랜치를 upstream 없이 생성.
    _git(clone_a, "checkout", "-b", "feat/x", "main")
    return origin, clone_a


def test_do_commit_no_upstream_retry_falls_through_to_rebase(
        diverged_new_branch_repo):
    origin, clone = diverged_new_branch_repo
    (clone / "local.txt").write_text("from this device\n")
    res = git_ops.do_commit(str(clone), "feat: local", push=True)
    assert res.ok is True
    assert res.committed is True
    # dead-end 금지: -u 의 non-ff 거부에서 멈추지 말고 rebase 복구로 push 성공.
    assert res.pushed is True, res.detail
    assert "rebase" in res.detail, res.detail
    # 원격 feat/x 에 양쪽 커밋(remote-only + 로컬 신규)이 모두 존재(rebase 발생 증거).
    subjects = _git(origin, "log", "--format=%s", "feat/x").stdout
    assert "x-remote" in subjects
    assert "feat: local" in subjects
    # 로컬도 upstream 이 심어져 원격과 동일 지점.
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(origin, "rev-parse", "feat/x").stdout.strip()
    assert head == remote_head


# ──────────────────────────────────────────────────────────────────
# codex P2-2 — do_commit 의 로컬 하위호출은 DEFAULT_TIMEOUT, push 만 함수 timeout
# ──────────────────────────────────────────────────────────────────

def _fake_run_git_recorder(calls):
    """run_git 대역: (args, timeout) 기록 + 성공 응답. 네트워크 0."""
    def fake_run_git(args, timeout):
        calls.append((list(args), timeout))
        if "rev-parse" in args:            # is_git_worktree
            return (0, "true", "")
        if "diff" in args:                 # staged-diff check: rc!=0 == 변경 있음
            return (1, "", "")
        return (0, "", "")                 # add/commit/push 성공
    return fake_run_git


def _calls_with_verb(calls, verb):
    return [(args, t) for args, t in calls if verb in args]


def test_do_commit_local_subcalls_use_default_timeout(tmp_path, monkeypatch):
    """push=False: add·staged-diff·commit 은 함수 timeout(네트워크 기본)이 아니라
    DEFAULT_TIMEOUT 을 쓴다 — push=False 엔 네트워크 작업이 0이므로."""
    calls = []
    monkeypatch.setattr(git_ops, "run_git", _fake_run_git_recorder(calls))
    res = git_ops.do_commit(str(tmp_path), "m", push=False, timeout=77)
    assert res.ok is True and res.committed is True
    for verb in ("add", "diff", "commit"):
        got = _calls_with_verb(calls, verb)
        assert got, f"{verb} 호출 없음: {calls}"
        for args, t in got:
            assert t == git_ops.DEFAULT_TIMEOUT, (
                f"{verb} 가 로컬 기본(2s) 아닌 timeout={t} 사용: {args}")
    assert not _calls_with_verb(calls, "push")


def test_do_commit_push_uses_function_timeout(tmp_path, monkeypatch):
    """push=True: push(네트워크)만 함수 timeout 을 쓰고 로컬 하위호출은 그대로 2s.

    codex 재리뷰 P1 이후 push timeout 은 남은 총예산(PUSH_TOTAL_BUDGET)으로도
    클램프되므로, 예산보다 작은 timeout(7s)으로 '함수 timeout 이 그대로 쓰임'을 본다.
    """
    calls = []
    monkeypatch.setattr(git_ops, "run_git", _fake_run_git_recorder(calls))
    res = git_ops.do_commit(str(tmp_path), "m", push=True, timeout=7)
    assert res.pushed is True
    push_calls = _calls_with_verb(calls, "push")
    assert push_calls, f"push 호출 없음: {calls}"
    for args, t in push_calls:
        assert t == 7, f"push 가 함수 timeout 아닌 {t} 사용: {args}"
    for verb in ("add", "commit"):
        for args, t in _calls_with_verb(calls, verb):
            assert t == git_ops.DEFAULT_TIMEOUT, (
                f"{verb} 가 함수 네트워크 timeout 으로 승격됨: {args}")


# ──────────────────────────────────────────────────────────────────
# codex 재리뷰 P1 — push 흐름 공유 데드라인(PUSH_TOTAL_BUDGET)
# ──────────────────────────────────────────────────────────────────
#
# do_commit(push=True)의 복구 체인은 push→push -u→fetch→rebase→push -u 로
# NET_TIMEOUT(10s) 네트워크 호출을 최대 5회 순차 수행할 수 있다(최악 ~50s).
# 훅 manifest 캡(30s)이 먼저 프로세스를 죽이면 로컬 커밋/rebase 뒤에 써야 할
# sync-warning 마커가 유실된다. 엔진은 공유 총예산 안에서 **스스로** 반환해야 한다.

def test_push_total_budget_exists_and_below_net_worst_case():
    assert hasattr(git_ops, "PUSH_TOTAL_BUDGET")
    # 예산은 단일 네트워크 호출(NET_TIMEOUT)보다는 커야 정상 push 를 막지 않고,
    # 최악 5회 순차(50s)보다는 작아야 의미가 있다.
    assert git_ops.NET_TIMEOUT < git_ops.PUSH_TOTAL_BUDGET < 5 * git_ops.NET_TIMEOUT


def test_do_commit_push_budget_exhaustion_returns_with_marker_friendly_result(
        tmp_path, monkeypatch):
    """복구 체인 도중 총예산이 바닥나면 do_commit 이 hang 없이 스스로 반환한다.

    가짜 시계: time.monotonic 호출마다 12s 씩 전진 → 네트워크 호출 몇 번 만에
    데드라인(+25s)을 넘긴다. 평문 push 는 non-ff 로 실패시켜 복구 체인에 진입시킨다.
    기대: committed=True 보존, pushed=False, detail 에 'budget'(호출부 훅이
    sync-warning 마커를 쓸 수 있게 결과가 반환됨).
    """
    fake_now = {"t": 0.0}

    def fake_monotonic():
        fake_now["t"] += 12.0
        return fake_now["t"]

    monkeypatch.setattr(git_ops, "time",
                        types.SimpleNamespace(monotonic=fake_monotonic))

    def fake_run_git(args, timeout):
        assert timeout >= 1  # 클램프 하한(음수/0 타임아웃 금지)
        if "rev-parse" in args:            # is_git_worktree
            return (0, "true", "")
        if "diff" in args:                 # staged-diff: rc!=0 == 변경 있음
            return (1, "", "")
        if "push" in args:                 # 평문 push → non-ff 거부(복구 체인 진입)
            return (1, "", "error: failed to push some refs\n"
                           "hint: Updates were rejected because the remote "
                           "contains work that you do not have locally.")
        return (0, "", "")                 # add/commit/fetch/rebase 성공
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)

    res = git_ops.do_commit(str(tmp_path), "m", push=True)
    assert res.ok is True
    assert res.committed is True           # 커밋은 보존(철칙)
    assert res.pushed is False
    assert "budget" in res.detail, res.detail


def test_do_commit_push_fast_path_unaffected_by_budget(tmp_path, monkeypatch):
    """정상 경로(첫 push 즉시 성공)는 예산 도입과 무관하게 그대로 성공한다."""
    calls = []
    monkeypatch.setattr(git_ops, "run_git", _fake_run_git_recorder(calls))
    res = git_ops.do_commit(str(tmp_path), "m", push=True)
    assert res.ok is True and res.committed is True
    assert res.pushed is True
    assert "budget" not in res.detail
    # 예산이 넉넉(25s)하므로 push timeout 은 NET_TIMEOUT 그대로.
    for args, t in _calls_with_verb(calls, "push"):
        assert t == git_ops.NET_TIMEOUT, f"push timeout={t}: {args}"


# ──────────────────────────────────────────────────────────────────
# codex P1 — 네트워크 훅의 manifest timeout 이 NET_TIMEOUT 설계를 덮는지
# ──────────────────────────────────────────────────────────────────

def test_manifest_network_hooks_timeout_covers_net_flow():
    """session-start(do_reconcile)·auto-commit(do_commit push=True)은 내부에서
    NET_TIMEOUT(10s) 네트워크 호출을 순차로 여러 번 할 수 있다(fetch+push+재시도).
    manifest timeout(초 단위 — adapter 가 무변환 기록)이 3s 면 훅 러너가 git_ops
    반환 전에 훅을 죽여 정리·sync-warning 기록까지 날린다.

    핵심 불변식(codex 재리뷰 P1): manifest timeout > PUSH_TOTAL_BUDGET —
    엔진의 push 총예산이 훅 캡보다 **작아야** 엔진이 스스로 먼저 반환해
    sync-warning 마커를 쓸 수 있다(절대값 30 이 아니라 관계가 본질)."""
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text(encoding="utf-8"))
    net_scripts = {"session-start.py", "auto-commit.py"}
    seen = set()
    for entry in manifest:
        if entry.get("script") in net_scripts:
            seen.add(entry["script"])
            assert entry.get("_timeout_unit") == "seconds"
            assert entry.get("timeout", 0) > git_ops.PUSH_TOTAL_BUDGET, (
                f"{entry['script']}: manifest timeout={entry.get('timeout')} ≤ "
                f"PUSH_TOTAL_BUDGET={git_ops.PUSH_TOTAL_BUDGET} — 훅 러너가 "
                f"엔진 반환 전에 죽여 sync-warning 마커가 유실됨")
    assert seen == net_scripts, f"네트워크 훅 누락: {net_scripts - seen}"
