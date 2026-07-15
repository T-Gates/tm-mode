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
import hashlib
import importlib.util
import inspect
import io
import json
import os
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops  # noqa: E402


def _worktree_probe_result(args):
    """Return the exact two-line contract required by is_git_worktree()."""
    argv = list(args)
    root = Path(argv[argv.index("-C") + 1]).resolve()
    return 0, f"true\n{root}\n", ""


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

# ── B1(codex) — 글로벌/시스템 git 설정·HOME 격리(hermetic) ─────────────────
# bare/clone 통합 테스트는 제품 코드(git_ops)가 os.environ 상속으로 git 을 부른다.
# 개발자/CI 이미지의 commit.gpgsign=true, core.hooksPath, init.templateDir 같은
# 글로벌 설정이 새어 들어오면 테스트가 환경 따라 깨진다. 모든 테스트에 빈 설정
# 파일을 GIT_CONFIG_GLOBAL/SYSTEM 으로 강제하고 HOME 도 tmp 로 돌린다.

@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    iso = tmp_path_factory.mktemp("git-iso")
    empty_cfg = iso / "empty-gitconfig"
    empty_cfg.write_text("")
    monkeypatch.setenv("HOME", str(iso))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(iso / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")  # 구 git 대비 벨트앤브레이스
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def test_git_env_is_hermetic():
    """이 모듈의 git 호출이 실 글로벌/시스템 설정을 보지 않는다(B1 회귀 가드)."""
    for scope in ("--global", "--system"):
        proc = subprocess.run(
            ["git", "config", scope, "--list"],
            capture_output=True, text=True, env={**os.environ},
        )
        assert (proc.stdout or "").strip() == "", (
            f"{scope} git 설정이 테스트 env 로 새어 들어옴: {proc.stdout!r}")


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


def test_do_commit_opt_in_publishes_new_branch_and_sets_upstream(
        new_branch_repo):
    origin, clone = new_branch_repo
    (clone / "b.txt").write_text("v2\n")

    res = git_ops.do_commit(
        str(clone), "feat: opt-in", push=True,
        reconcile_before_push=True)

    assert res.ok is True
    assert res.committed is True
    assert res.pushed is True, res.detail
    local_head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(origin, "rev-parse", "feat/x").stdout.strip()
    assert local_head == remote_head
    upstream = _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{u}").stdout.strip()
    assert upstream == "origin/feat/x"


def test_do_commit_opt_in_new_branch_pushes_captured_oid_before_upstream_setup(
        new_branch_repo, monkeypatch):
    """A same-branch ref move at push invocation must not change the published OID."""
    origin, clone = new_branch_repo
    (clone / "b.txt").write_text("v2\n")
    real_run_git = git_ops.run_git
    observed = {}

    def racing_run_git(args, timeout, **kwargs):
        argv = list(args)
        if "push" in argv and "push" not in observed:
            captured = _git(
                clone, "rev-parse", "refs/heads/feat/x").stdout.strip()
            tree = _git(clone, "write-tree").stdout.strip()
            raced = _git(
                clone, "commit-tree", tree, "-p", captured,
                "-m", "concurrent branch advance").stdout.strip()
            _git(
                clone, "update-ref", "refs/heads/feat/x", raced, captured)
            observed.update(push=argv, captured=captured, raced=raced)
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(git_ops, "run_git", racing_run_git)
    res = git_ops.do_commit(
        str(clone), "feat: immutable publication", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is True, res.detail
    push_args = observed["push"]
    push_index = push_args.index("push")
    separator = push_args.index("--", push_index)
    endpoint_alias = push_args[separator + 1]
    assert endpoint_alias.startswith("tm-mode-exact-")
    assert endpoint_alias.endswith("://endpoint")
    assert push_args[separator + 2:] == [
        f"{observed['captured']}:refs/heads/feat/x"]
    assert f"url.{origin}.insteadOf={endpoint_alias}" in push_args
    assert f"url.{origin}.pushInsteadOf={endpoint_alias}" in push_args
    assert "--no-verify" not in push_args[push_index:separator]
    assert "--no-follow-tags" in push_args[push_index:separator]
    assert "--recurse-submodules=check" in push_args[push_index:separator]
    assert "-u" not in push_args[push_index:separator]
    assert _git(
        origin, "rev-parse", "refs/heads/feat/x").stdout.strip() == (
            observed["captured"])
    assert _git(
        clone, "rev-parse", "refs/heads/feat/x").stdout.strip() == (
            observed["raced"])
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        check=False).returncode != 0
    assert "upstream" in res.detail.lower()


def test_do_commit_opt_in_upstream_mode_rejects_different_push_remote(
        new_branch_repo, tmp_path):
    """Git's upstream-mode remote mismatch must fail closed, not be synthesized."""
    _origin, clone = new_branch_repo
    fork = tmp_path / "fork.git"
    _git(tmp_path, "init", "--bare", str(fork))
    _git(clone, "remote", "add", "fork", str(fork))
    _git(clone, "config", "remote.pushDefault", "fork")
    _git(clone, "config", "push.default", "upstream")
    _git(clone, "checkout", "main")
    (clone / "upstream-mode.txt").write_text("must stay local\n")

    res = git_ops.do_commit(
        str(clone), "upstream mode mismatch", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is False
    assert "publication target unavailable" in res.detail
    assert "upstream" in res.detail.lower()
    assert _git(
        fork, "rev-parse", "--verify", "refs/heads/main",
        check=False).returncode != 0


def test_do_commit_opt_in_simple_triangular_push_preserves_pull_upstream(
        new_branch_repo, tmp_path):
    """simple triangular push publishes to fork without replacing origin upstream."""
    origin, clone = new_branch_repo
    fork = tmp_path / "fork.git"
    _git(tmp_path, "init", "--bare", str(fork))
    _git(clone, "remote", "add", "fork", str(fork))
    _git(clone, "config", "remote.pushDefault", "fork")
    _git(clone, "config", "push.default", "simple")
    _git(clone, "checkout", "main")
    origin_before = _git(origin, "rev-parse", "refs/heads/main").stdout.strip()
    (clone / "triangular.txt").write_text("publish to fork\n")

    res = git_ops.do_commit(
        str(clone), "triangular publication", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is True, res.detail
    local_head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    assert _git(
        fork, "rev-parse", "refs/heads/main").stdout.strip() == local_head
    assert _git(
        origin, "rev-parse", "refs/heads/main").stdout.strip() == origin_before
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{u}").stdout.strip() == "origin/main"


def test_opt_in_simple_triangular_allows_pull_upstream_name_mismatch(
        new_branch_repo, tmp_path):
    """Git simple permits fork/<local> even when pull upstream has another name."""
    origin, clone = new_branch_repo
    fork = tmp_path / "fork.git"
    _git(tmp_path, "init", "--bare", str(fork))
    _git(clone, "remote", "add", "fork", str(fork))
    _git(clone, "branch", "--set-upstream-to=origin/main", "feat/x")
    _git(clone, "config", "remote.pushDefault", "fork")
    _git(clone, "config", "push.default", "simple")
    oracle = _git(clone, "push", "--dry-run", check=False)
    assert oracle.returncode == 0, oracle.stderr
    origin_before = _git(origin, "rev-parse", "refs/heads/main").stdout.strip()
    (clone / "triangular-mismatch.txt").write_text("publish to fork\n")

    res = git_ops.do_commit(
        str(clone), "triangular name mismatch", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is True, res.detail
    local_head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    assert _git(
        fork, "rev-parse", "refs/heads/feat/x").stdout.strip() == local_head
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == origin_before
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{u}").stdout.strip() == "origin/main"


def test_opt_in_simple_same_remote_name_mismatch_uses_tm_mode_recovery(
        new_branch_repo):
    """tm-mode intentionally recovers the simple mismatch that plain Git rejects."""
    origin, clone = new_branch_repo
    _git(clone, "branch", "--set-upstream-to=origin/main", "feat/x")
    _git(clone, "config", "push.default", "simple")
    oracle = _git(clone, "push", "--dry-run", check=False)
    assert oracle.returncode != 0
    origin_before = _git(origin, "rev-parse", "refs/heads/main").stdout.strip()
    (clone / "same-remote-mismatch.txt").write_text("must stay local\n")

    res = git_ops.do_commit(
        str(clone), "same remote mismatch", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is True, res.detail
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == origin_before
    assert _git(origin, "rev-parse", "refs/heads/feat/x").stdout.strip() == (
        _git(clone, "rev-parse", "HEAD").stdout.strip())
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{u}").stdout.strip() == "origin/feat/x"


def test_opt_in_current_no_upstream_publishes_without_setting_upstream(
        new_branch_repo):
    """push.default=current publishes same-name but does not invent pull tracking."""
    origin, clone = new_branch_repo
    _git(clone, "config", "push.default", "current")
    oracle = _git(clone, "push", "--dry-run", check=False)
    assert oracle.returncode == 0, oracle.stderr
    (clone / "current.txt").write_text("current mode\n")

    res = git_ops.do_commit(
        str(clone), "current mode", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is True, res.detail
    assert _git(origin, "rev-parse", "refs/heads/feat/x").stdout.strip() == (
        _git(clone, "rev-parse", "HEAD").stdout.strip())
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        check=False).returncode != 0


def test_opt_in_explicit_remote_push_infers_single_remote_without_upstream(
        new_branch_repo):
    """An explicit remote push refspec may leave %(push:remotename) blank."""
    origin, clone = new_branch_repo
    _git(
        clone, "config", "remote.origin.push",
        "refs/heads/feat/x:refs/heads/published")
    oracle = _git(clone, "push", "--dry-run", check=False)
    assert oracle.returncode == 0, oracle.stderr
    (clone / "explicit.txt").write_text("explicit destination\n")

    res = git_ops.do_commit(
        str(clone), "explicit destination", push=True,
        reconcile_before_push=True)

    assert res.committed is True and res.pushed is True, res.detail
    assert _git(origin, "rev-parse", "refs/heads/published").stdout.strip() == (
        _git(clone, "rev-parse", "HEAD").stdout.strip())
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        check=False).returncode != 0


def test_publication_target_probes_share_absolute_deadline(tmp_path, monkeypatch):
    clock = {"now": 0.0}
    calls = []
    branch_ref = "refs/heads/main"
    tracking_ref = "refs/remotes/origin/main"
    fmt = ("%(refname)%00%(upstream)%00%(upstream:remotename)%00"
           "%(upstream:remoteref)%00%(push)%00%(push:remotename)%00"
           "%(push:remoteref)")

    def fake_run_git(args, timeout, **_kwargs):
        calls.append(timeout)
        clock["now"] += 1.0
        if "for-each-ref" in args:
            return 0, "\0".join([
                branch_ref, tracking_ref, "origin", branch_ref,
                tracking_ref, "origin", ""]) + "\n", ""
        if args[-1] == "remote":
            return 0, "origin\n", ""
        if "push.default" in args:
            return 1, "", ""
        if "get-url" in args:
            return 0, "/tmp/origin.git\n", ""
        if "check-ref-format" in args:
            return 0, "", ""
        raise AssertionError(args)

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    target, detail = git_ops._resolve_publication_target(
        str(tmp_path),
        {"key": "branch:main", "branch": "main", "head": "a" * 40},
        deadline=6.0)

    assert target is not None, detail
    assert calls == [2, 2, 2, 2, 2, 1]
    assert clock["now"] <= 6.0


def test_upstream_setup_probes_share_absolute_deadline(tmp_path, monkeypatch):
    clock = {"now": 0.0}
    calls = []
    head = "a" * 40
    branch_ref = "refs/heads/main"
    tracking_ref = "refs/remotes/origin/main"
    endpoint = "/tmp/origin.git"
    target = git_ops._PublicationTarget(
        remote="origin", destination=branch_ref,
        reconcile_ref=tracking_ref, set_upstream=True,
        remote_fingerprint=hashlib.sha256(endpoint.encode()).hexdigest(),
        push_endpoint=endpoint)

    def fake_run_git(args, timeout, **_kwargs):
        calls.append(timeout)
        clock["now"] += 2.0 if not calls[:-1] else 1.0
        if "get-url" in args:
            return 0, endpoint + "\n", ""
        if "for-each-ref" in args:
            return 0, (
                f"{branch_ref}\0{head}\n{tracking_ref}\0{head}\n"), ""
        if "branch" in args:
            return 0, "", ""
        raise AssertionError(args)

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    ok, detail = git_ops._set_publication_upstream_locked(
        str(tmp_path),
        {"key": "branch:main", "branch": "main", "head": head},
        target, deadline=4.0)

    assert ok is True, detail
    assert calls == [2, 2, 1]
    assert clock["now"] <= 4.0


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
    _git(clone_b, "checkout", "-b", "feat/x", "origin/main")
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


def test_do_commit_opt_in_defers_same_name_remote_ahead_without_worktree_mutation(
        diverged_new_branch_repo):
    origin, clone = diverged_new_branch_repo
    remote_only = _git(origin, "rev-parse", "feat/x").stdout.strip()
    (clone / "local.txt").write_text("from this device\n")

    res = git_ops.do_commit(
        str(clone), "feat: opt-in local", push=True,
        reconcile_before_push=True)

    assert res.ok is True
    assert res.committed is True
    assert res.pushed is False
    assert "foreground worktree reconciliation disabled" in res.detail
    local_head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(origin, "rev-parse", "feat/x").stdout.strip()
    assert local_head != remote_head
    assert remote_head == remote_only
    assert _git(clone, "log", "-1", "--format=%s").stdout.strip() == (
        "feat: opt-in local")
    assert res.pending_identity == {
        "key": "branch:feat/x", "branch": "feat/x", "head": local_head}
    assert res.pending_target == {
        "remote": "origin",
        "destination": "refs/heads/feat/x",
        "reconcile_ref": "refs/remotes/origin/feat/x",
        "set_upstream": True,
        "remote_fingerprint": git_ops._remote_push_fingerprint(
            str(clone), "origin"),
    }
    assert _git(
        origin, "rev-list", "--merges", f"{remote_only}..{remote_head}",
    ).stdout.strip() == ""
    assert _git(
        clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{u}", check=False).returncode != 0


@pytest.mark.parametrize("failure_kind", ["timeout", "exec-error"])
def test_do_commit_no_upstream_rebase_exception_reports_failed_abort(
        diverged_new_branch_repo, monkeypatch, failure_kind):
    """Every legacy push-u rebase exception must report unproven cleanup."""
    _origin, clone = diverged_new_branch_repo
    (clone / "local.txt").write_text("from this device\n")
    real_run_git = git_ops.run_git
    abort_calls = []

    def failing_rebase(args, timeout, **kwargs):
        if "rebase" in args and "--autostash" in args:
            if failure_kind == "timeout":
                raise subprocess.TimeoutExpired(cmd="git rebase", timeout=timeout)
            raise OSError("simulated rebase exec failure")
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(git_ops, "run_git", failing_rebase)
    monkeypatch.setattr(
        git_ops, "_abort_rebase",
        lambda *_args: abort_calls.append(True) or False)

    res = git_ops.do_commit(str(clone), "feat: local", push=True)

    assert abort_calls
    assert res.committed is True and res.pushed is False
    assert "rebase failed (aborted)" not in res.detail
    assert "abort attempted; rollback not proven" in res.detail


# ──────────────────────────────────────────────────────────────────
# codex P2-2 — do_commit 의 로컬 하위호출은 DEFAULT_TIMEOUT, push 만 함수 timeout
# ──────────────────────────────────────────────────────────────────

def _fake_run_git_recorder(calls):
    """run_git 대역: (args, timeout) 기록 + 성공 응답. 네트워크 0."""
    def fake_run_git(args, timeout, **_kwargs):
        calls.append((list(args), timeout))
        if "--is-inside-work-tree" in args:
            return _worktree_probe_result(args)
        if "--git-common-dir" in args:
            return (0, ".\n", "")
        if "rev-parse" in args:
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
# 훅 manifest 캡(70s)이 먼저 프로세스를 죽이면 로컬 커밋/rebase 뒤에 써야 할
# sync-warning 마커가 유실된다. 엔진은 공유 총예산 안에서 **스스로** 반환해야 한다.

def test_push_total_budget_exists_and_below_net_worst_case():
    assert hasattr(git_ops, "PUSH_TOTAL_BUDGET")
    # 예산은 단일 네트워크 호출(NET_TIMEOUT)보다는 커야 정상 push 를 막지 않고,
    # 최악 5회 순차(50s)보다는 작아야 의미가 있다.
    assert git_ops.PUSH_TOTAL_BUDGET == 45
    assert git_ops.RECONCILE_TOTAL_BUDGET == 40
    assert git_ops.PUSH_TOTAL_BUDGET < 5 * git_ops.NET_TIMEOUT


def test_bound_reconcile_can_mutate_after_ten_second_fetch(tmp_path, monkeypatch):
    """A normal full fetch still leaves reserve plus one mutation second."""
    clock = {"now": 0.0}
    identity = {
        "key": "branch:main", "branch": "main", "head": "a" * 40,
    }
    target = git_ops._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")
    observed = {}

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(git_ops, "is_git_worktree", lambda _root: True)
    monkeypatch.setattr(
        git_ops, "_validated_branch_identity",
        lambda _root, expected, _timeout: dict(expected))
    monkeypatch.setattr(
        git_ops, "_checkout_matches_identity", lambda *_args: True)
    monkeypatch.setattr(
        git_ops, "_read_ref_oid", lambda *_args: (True, "b" * 40))
    monkeypatch.setattr(
        git_ops, "_ahead_behind_refs", lambda *_args: (0, 1, True))

    def fake_run_git(args, timeout, **_kwargs):
        assert "fetch" in args
        assert timeout == git_ops.NET_TIMEOUT
        clock["now"] += 10.0
        return 0, "", ""

    def fake_bound(*_args, deadline, **_kwargs):
        observed["remaining"] = deadline - clock["now"]
        return git_ops.ReconcileResult(
            ok=True, action="fast-forward", behind=1,
            final_identity={**identity, "head": "b" * 40})

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    monkeypatch.setattr(git_ops, "_bound_reconcile_transaction", fake_bound)

    res = git_ops.do_reconcile(
        str(tmp_path), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is True
    assert observed["remaining"] >= (
        git_ops._BOUND_RECONCILE_RECOVERY_RESERVE + 1)


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

    @contextmanager
    def ready_interlock(_root, _timeout=1.0):
        yield True, ""

    # This case isolates the push deadline formula; real interlock/blocker
    # timing and contention have dedicated repository integration coverage.
    monkeypatch.setattr(git_ops, "_publication_interlock", ready_interlock)
    monkeypatch.setattr(
        git_ops, "publication_blocker_detail", lambda *_args, **_kwargs: "")

    def fake_run_git(args, timeout, **_kwargs):
        assert timeout >= 1  # 클램프 하한(음수/0 타임아웃 금지)
        if "--is-inside-work-tree" in args:
            return _worktree_probe_result(args)
        if "--git-common-dir" in args:
            return (0, ".\n", "")
        if "rev-parse" in args:
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


def test_net_timeout_floor_holds_for_nonpositive_caller_timeout(
        tmp_path, monkeypatch):
    """[codex A1] timeout<=0 으로 호출돼도 네트워크 타임아웃 하한 1s 가 유지된다.

    종전 클램프 min(timeout, max(1, 남은예산)) 은 caller timeout 이 0/음수면
    하한(1s) 문서 계약이 깨져 push 가 즉시 TimeoutExpired 로 죽었다(커밋만 남고
    push 미수행). 하한은 항상 바깥에서 강제돼야 한다: max(1, min(timeout, 남은예산)).
    """
    for bad_timeout in (0, -3):
        calls = []
        monkeypatch.setattr(git_ops, "run_git", _fake_run_git_recorder(calls))
        res = git_ops.do_commit(str(tmp_path), "m", push=True,
                                timeout=bad_timeout)
        assert res.committed is True
        assert res.pushed is True, res.detail
        push_calls = _calls_with_verb(calls, "push")
        assert push_calls, f"push 호출 없음: {calls}"
        for args, t in push_calls:
            assert t >= 1, (
                f"timeout={bad_timeout} 호출에서 push subprocess timeout={t} — "
                f"하한 1s 계약 위반: {args}")
            # 같은 클램프 불변식이 git 자체 방어(http.lowSpeedTime)에도 적용된다 —
            # 0 은 curl 저속 감지를 끄고(defense-in-depth 무력화), 음수는 부적합.
            lst = [a for a in args if str(a).startswith("http.lowSpeedTime=")]
            assert lst, f"http.lowSpeedTime 옵션 누락: {args}"
            for opt in lst:
                assert int(opt.split("=", 1)[1]) >= 1, (
                    f"timeout={bad_timeout} 호출에서 {opt} — 하한 1s 계약 위반")


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
# A1 — 데드라인 진입 앵커: 로컬 단계도 벽시계 예산을 소모한다
# ──────────────────────────────────────────────────────────────────
#
# 종전엔 _deadline 이 로컬 commit **이후**(push 직전)에 시작돼, 로컬 단계
# (rev-parse·add·staged-diff·commit, 최악 ~8s)가 예산 밖이었다 — 최악 로컬 8s +
# 네트워크 25s = 33s 로 당시 훅 manifest 캡(30s)을 넘길 수 있었다. A1: 데드라인을
# do_commit 진입에 앵커해 로컬 단계가 예산을 소모하고 네트워크는 남은 만큼만 쓴다.
# 로컬 하위호출 자체는 예산으로 클램프/중단하지 않는다(로컬 커밋은 항상 완주·보존).

def _fake_wall_clock(monkeypatch, per_call: float):
    """가짜 벽시계: run_git 호출마다 per_call 초씩 전진(호출 자체가 그만큼 걸린 셈).

    time.monotonic 은 전진 없이 현재 가짜 시각만 읽는다(기존 예산 테스트의
    'monotonic 호출마다 전진'과 달리, 소모 주체를 run_git 호출로 고정해
    로컬/네트워크 단계별 소모를 정확히 모사한다). (calls, now) 를 돌려준다.
    """
    fake_now = {"t": 1000.0}
    monkeypatch.setattr(git_ops, "time",
                        types.SimpleNamespace(monotonic=lambda: fake_now["t"]))
    calls = []

    @contextmanager
    def ready_interlock(_root, _timeout=1.0):
        yield True, ""

    # These tests isolate the do_commit wall-clock formula. Interlock behavior
    # has dedicated real-repository contention coverage.
    monkeypatch.setattr(git_ops, "_publication_interlock", ready_interlock)
    monkeypatch.setattr(git_ops, "publication_blocker_detail", lambda *_a: "")

    def fake_run_git(args, timeout, **_kwargs):
        calls.append((list(args), timeout, fake_now["t"]))
        fake_now["t"] += per_call
        if "--is-inside-work-tree" in args:
            return _worktree_probe_result(args)
        if "--git-common-dir" in args:
            return (0, ".\n", "")
        if "rev-parse" in args:
            return (0, "true", "")
        if "diff" in args:                 # staged-diff: rc!=0 == 변경 있음
            return (1, "", "")
        return (0, "", "")                 # add/commit/push 성공
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    return calls, fake_now


def test_do_commit_slow_local_phases_shrink_first_push_timeout(
        tmp_path, monkeypatch):
    """로컬 단계가 벽시계를 많이 먹으면 첫 push 의 timeout 이 남은 예산으로 준다.

    로컬 8회(worktree·add·diff·pre-identity×2·commit·post-identity×2) × 4s =
    32s 소모 → 반환 identity 4s를 예약하고 남은 약 9s → 첫 push
    timeout 은 NET_TIMEOUT(10s)이 아니라 그 이하로 클램프돼야 한다. 데드라인이
    push 직전에 시작되면(종전) push 가 10s 를 그대로 받아 총 30s 를 넘긴다.
    """
    calls, fake_now = _fake_wall_clock(monkeypatch, per_call=4.0)
    entry = fake_now["t"]
    res = git_ops.do_commit(str(tmp_path), "m", push=True)
    assert res.ok is True and res.committed is True
    assert res.pushed is True, res.detail
    push_calls = [(t, at) for args, t, at in calls if "push" in args]
    assert push_calls, f"push 호출 없음: {calls}"
    push_t, push_at = push_calls[0]
    # 핵심(A1): 첫 push timeout 이 남은 예산으로 클램프됐다(종전엔 NET_TIMEOUT 그대로).
    assert push_t < git_ops.NET_TIMEOUT, (
        f"push timeout={push_t} — 로컬 단계 32s 소모 후에도 클램프 안 됨(예산이 "
        f"진입 앵커가 아님)")
    # 예산 수식 불변식: (진입~push 경과) + push timeout ≤ PUSH_TOTAL_BUDGET —
    # 총 벽시계가 훅 manifest 캡(70s)에서 kill-drain/abort 꼬리 슬랙을 뺀 값 아래.
    assert (push_at - entry) + push_t <= git_ops.PUSH_TOTAL_BUDGET
    # 로컬 하위호출은 예산으로 클램프하지 않는다(로컬 커밋 완주 보장).
    for verb in ("add", "commit"):
        for args, t, _at in calls:
            if verb in args:
                assert t == git_ops.DEFAULT_TIMEOUT, (
                    f"{verb} 가 예산으로 클램프됨: timeout={t}")


def test_do_commit_budget_gone_after_local_commit_skips_push(
        tmp_path, monkeypatch):
    """로컬 커밋 성공 후 예산이 이미 바닥이면 push 를 아예 시도하지 않는다.

    로컬 8회 × 6s = 48s > 예산 → preflight 가 1s 짜리(하한 floor) 헛 push 를
    쏘는 대신 즉시 반환한다. 결과 모양(committed=True/pushed=False + 'budget')은
    auto-commit 훅이 sync-warning 마커를 쓰는 그 모양이어야 한다.
    """
    calls, _fake_now = _fake_wall_clock(monkeypatch, per_call=6.0)
    res = git_ops.do_commit(str(tmp_path), "m", push=True)
    assert res.ok is True
    assert res.committed is True           # 커밋은 보존(철칙)
    assert res.pushed is False
    assert "budget" in res.detail, res.detail
    assert not [args for args, _t, _at in calls if "push" in args], (
        f"예산 소진 후에도 push 시도: {calls}")


def test_do_commit_normal_remote_rtt_still_attempts_non_ff_rebase(
        tmp_path, monkeypatch):
    """GitHub SSH의 정상 RTT 수준에서도 foreground non-ff 복구를
    budget exhaustion으로 생략하지 않아야 한다.

    첫 push 거부와 fetch가 각 2.5s를 쓰고, 로컬 probe는 0.01s인
    경로를 실제 do_commit으로 통과시킨다. #33에서 NET_TIMEOUT을
    늘린 근거 자체가 실 GitHub SSH 왕복이 2.5s+라는 것이었다.
    """
    clock = {"now": 0.0}
    state = {"head": "1" * 40, "pushes": 0, "rebases": 0}
    monkeypatch.setattr(
        git_ops.time, "monotonic", lambda: clock["now"])

    def fake_run_git(args, timeout, **_kwargs):
        argv = list(args)
        if "push" in argv:
            clock["now"] += 2.5
            state["pushes"] += 1
            if state["pushes"] == 1:
                return 1, "", (
                    "error: failed to push some refs\n"
                    "hint: Updates were rejected because the remote contains "
                    "work that you do not have locally.")
            return 0, "", ""
        if "fetch" in argv:
            clock["now"] += 2.5
            return 0, "", ""

        clock["now"] += 0.01
        if "--is-inside-work-tree" in argv:
            return _worktree_probe_result(argv)
        if "--git-common-dir" in argv:
            return 0, ".\n", ""
        if "for-each-ref" in argv:
            return 0, "", ""
        if "symbolic-ref" in argv and "--short" in argv:
            return 0, "main\n", ""
        if "rev-parse" in argv:
            if "refs/stash" in argv:
                return 1, "", ""
            return 0, state["head"] + "\n", ""
        if "add" in argv:
            return 0, "", ""
        if "diff" in argv and "--cached" in argv:
            return 1, "", ""
        if "diff" in argv:
            return 0, "", ""
        if "status" in argv:
            return 0, "", ""
        if "commit" in argv:
            state["head"] = "2" * 40
            return 0, "committed\n", ""
        if "rebase" in argv:
            state["rebases"] += 1
            state["head"] = "3" * 40
            return 0, "", ""
        raise AssertionError(argv)

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)

    res = git_ops.do_commit(str(tmp_path), "m", push=True)

    assert state["rebases"] == 1, (
        f"normal 2.5s push + 2.5s fetch skipped rebase: {res.detail}")
    assert res.pushed is True, res.detail


# ──────────────────────────────────────────────────────────────────
# codex P1 — 네트워크 훅의 manifest timeout 이 NET_TIMEOUT 설계를 덮는지
# ──────────────────────────────────────────────────────────────────

def test_manifest_network_hooks_timeout_covers_net_flow():
    """session-start 와 foreground auto-commit 의 네트워크 예산을 훅 캡이 덮는다.

    불변식 2개:
      ① session-start: reconcile 총예산 + 후속 upstream fetch + cleanup 여유를 덮는다.
      ② auto-commit: 첫 로컬 시도의 index.lock 실패 worst-case + 1s backoff +
        재시도 do_commit 의 PUSH_TOTAL_BUDGET + abort/ledger cleanup 여유를 덮는다."""
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text(encoding="utf-8"))
    entries = {e.get("script"): e for e in manifest if e.get("script")}

    ss = entries.get("session-start.py")
    assert ss is not None
    assert ss.get("_timeout_unit") == "seconds"
    session_required = (
        git_ops.RECONCILE_TOTAL_BUDGET + git_ops.NET_TIMEOUT + 8)
    assert ss.get("timeout", 0) >= session_required, (
        f"session-start: manifest timeout={ss.get('timeout')} < "
        f"reconcile+upstream+cleanup={session_required} — 훅 러너가 "
        f"엔진 반환 전에 죽여 sync-warning 마커가 유실됨")

    ac = entries.get("auto-commit.py")
    assert ac is not None
    assert ac.get("_timeout_unit") == "seconds"
    # 첫 시도가 commit의 index.lock에서 끝나는 최장 경로:
    # worktree/add/diff + pre-commit checkout identity 2회 + commit = 6 local calls.
    first_local_worst = 6 * git_ops.DEFAULT_TIMEOUT
    retry_and_push_worst = 1 + git_ops.PUSH_TOTAL_BUDGET
    # do_commit은 반환 identity를 자체 예산에 예약한다. 호출부는 branch/HEAD 검증
    # 2회(최대 4s), ledger/warning lock(최대 2s), worker/출력 여유가 별도로 필요하다.
    cleanup_headroom = 8
    required = first_local_worst + retry_and_push_worst + cleanup_headroom
    assert ac.get("timeout", 0) >= required, (
        f"auto-commit: manifest timeout={ac.get('timeout')} < foreground worst "
        f"{required} — runner kill 이 pending/sync-warning 기록보다 먼저 발생")


def test_auto_commit_retry_records_pending_before_manifest_timeout(
        tmp_path, monkeypatch):
    """첫 commit의 index.lock 재시도 + remote-ahead defer도 훅 cap 안에서
    반환해 pending을 써야 한다.

    실제 벽시계 대신 첫 index.lock 경로는 선언 timeout 거의 전부를 쓰고, retry
    fetch는 정상 GitHub SSH 수준(2.5s)을 쓰는 경로를 모사한다. foreground hook은
    remote-ahead worktree mutation을 시작하지 않고 durable pending으로 전환해야 한다.
    """
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text(
            encoding="utf-8"))
    cap = next(
        e["timeout"] for e in manifest if e.get("script") == "auto-commit.py")

    spec = importlib.util.spec_from_file_location(
        "auto_commit_deadline_regression",
        REPO / "infra" / "hooks" / "auto-commit.py")
    hook = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hook)

    clock = {"now": 0.0}
    state = {
        "commit_attempt": 0,
        "after_rebase": False,
        "restored": False,
        "head": "1" * 40,
    }
    autostash = "a" * 40
    committed_head = "3" * 40
    branch_ref = "refs/heads/main"
    remote_ref = "refs/remotes/origin/main"
    target_format = (
        "%(refname)%00%(upstream)%00%(upstream:remotename)%00"
        "%(upstream:remoteref)%00%(push)%00%(push:remotename)%00"
        "%(push:remoteref)")

    monkeypatch.setattr(
        git_ops.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        hook._time, "sleep",
        lambda seconds: clock.__setitem__(
            "now", clock["now"] + float(seconds)))

    @contextmanager
    def ready_interlock(_root, _timeout=1.0):
        yield True, ""

    # Deadline arithmetic is the subject here. The common publication lease
    # and blocker scan are covered with real cross-process/recovery artifacts.
    monkeypatch.setattr(git_ops, "_publication_interlock", ready_interlock)
    monkeypatch.setattr(
        git_ops, "publication_blocker_detail", lambda *_args, **_kwargs: "")

    def fake_run_git(args, timeout, **_kwargs):
        argv = list(args)
        retry = state["commit_attempt"] >= 1
        expected_fetch = [
            "-C", str(root), *git_ops.http_timeout_opts(timeout),
            "fetch", "--", "origin",
        ]
        expected_rebase = [
            "-C", str(root), "rebase", "--autostash", remote_ref,
        ]
        expected_push = [
            "-C", str(root), *git_ops.http_timeout_opts(timeout),
            "push", "--", "origin", f"{branch_ref}:{branch_ref}",
        ]
        duration = (
            0.01 if retry and not state["after_rebase"]
            else 0.99 * timeout)
        if retry and argv == expected_fetch:
            duration = 2.5
        if retry and argv == expected_rebase:
            # subprocess timeout + run_git.kill_group() 후 communicate drain.
            clock["now"] += timeout + 2.0
            state["after_rebase"] = True
            state["head"] = "2" * 40
            raise subprocess.TimeoutExpired(
                cmd="git rebase", timeout=timeout,
                output=f"Created autostash: {autostash[:12]}\n",
                stderr="Applying autostash resulted in conflicts.\n")

        clock["now"] += duration
        if "--is-inside-work-tree" in argv:
            return _worktree_probe_result(argv)
        if "symbolic-ref" in argv and "--short" in argv:
            return 0, "main\n", ""
        if argv == [
                "-C", str(root), "check-ref-format", "--branch", "main"]:
            return 0, "", ""
        if argv == [
                "-C", str(root), "cat-file", "-e",
                f"{committed_head}^{{commit}}"]:
            return 0, "", ""
        if argv == [
                "-C", str(root), "for-each-ref",
                f"--format={target_format}", "--", branch_ref]:
            return 0, "\0".join([
                branch_ref,
                remote_ref,
                "origin",
                branch_ref,
                remote_ref,
                "origin",
                "",
            ]) + "\n", ""
        if argv == ["-C", str(root), "remote"]:
            return 0, "origin\n", ""
        if argv == [
                "-C", str(root), "config", "--get", "push.default"]:
            return 1, "", ""  # unset means Git's default `simple`
        if argv in (
                ["-C", str(root), "check-ref-format", branch_ref],
                ["-C", str(root), "check-ref-format", remote_ref]):
            return 0, "", ""
        if "rev-parse" in argv:
            joined = " ".join(argv)
            if "refs/stash" in argv:
                if state["after_rebase"]:
                    return 0, autostash + "\n", ""
                return 1, "", ""
            if autostash[:12] in joined:
                return 0, autostash + "\n", ""
            return 0, state["head"] + "\n", ""
        if "show" in argv and "-s" in argv:
            return 0, "On main: autostash\n", ""
        if "add" in argv:
            return 0, "", ""
        if "diff" in argv and "--cached" in argv:
            return 1, "", ""
        if "status" in argv:
            return 0, "", ""  # preflight 직후 TOCTOU dirty edit
        if "diff" in argv and "--diff-filter=U" in argv:
            return 0, ("" if state["restored"] else "dirty.md\0"), ""
        if "diff" in argv:
            return 0, "", ""
        if "commit" in argv:
            state["commit_attempt"] += 1
            if state["commit_attempt"] == 1:
                return 1, "", (
                    "fatal: Unable to create .git/index.lock: File exists")
            state["head"] = committed_head
            return 0, "committed\n", ""
        if retry and argv == expected_push:
            return 1, "", (
                "error: failed to push some refs\n"
                "hint: Updates were rejected because the remote contains work "
                "that you do not have locally.")
        if retry and argv == expected_fetch:
            return 0, "", ""
        if argv == [
                "-C", str(root), "rev-list", "--count", "--left-right",
                f"{remote_ref}...{branch_ref}"]:
            return 0, "1 1\n", ""
        if argv == ["-C", str(root), "rebase", "--abort"]:
            return 0, "", ""
        if "reset" in argv and "--hard" in argv:
            state["head"] = "3" * 40
            return 0, "", ""
        if "stash" in argv and "apply" in argv:
            state["restored"] = True
            return 0, "", ""
        raise AssertionError(argv)

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)

    def timed_bound_transaction(*_args, **_kwargs):
        raise AssertionError("foreground hook must not mutate the worktree")

    monkeypatch.setattr(
        git_ops, "_bound_reconcile_transaction", timed_bound_transaction)
    pending_write_at = []
    warning_write_at = []

    def timed_pending_write(_root, _identity=None, *, target=None):
        # 실제 writer tail의 branch/ref 검증 4s + ledger lock/fsync 2s.
        clock["now"] += 6.0
        pending_write_at.append(clock["now"])
        return True

    def timed_warning_write(*_args, **_kwargs):
        # warning lock + atomic fsync 여유. 호출 시작이 아니라 durable 완료 시각을 잰다.
        clock["now"] += 2.0
        warning_write_at.append(clock["now"])
        return True

    monkeypatch.setattr(git_ops, "write_push_pending", timed_pending_write)
    monkeypatch.setattr(git_ops, "write_sync_warning", timed_warning_write)
    monkeypatch.setattr(
        git_ops, "clear_sync_warning_if_fully_published",
        lambda *_a, **_k: False)
    monkeypatch.setattr(
        git_ops, "read_push_pending_state",
        lambda _root: types.SimpleNamespace(available=True, content=""))
    monkeypatch.setattr(
        git_ops, "bind_legacy_pending_to_current_checkout",
        lambda _root, snapshot: snapshot)
    monkeypatch.setattr(
        git_ops, "pending_entry_key_for_current_checkout",
        lambda _root, _snapshot: "")
    monkeypatch.setattr(git_ops, "sanitize_git_detail", lambda detail: detail)
    monkeypatch.setattr(hook, "_kick_push_worker", lambda _root: None)

    root = tmp_path / "team"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".teammode-active").write_text("", encoding="utf-8")
    edited = root / "edited.md"
    edited.write_text("edited\n", encoding="utf-8")
    monkeypatch.setenv("TEAMMODE_HOME", str(root))
    monkeypatch.setattr(
        hook.sys, "stdin",
        io.StringIO(json.dumps({
            "event": "PostToolUse",
            "action": "file_edit",
            "files": [str(edited)],
        })))

    assert hook.main() == 0
    assert state["commit_attempt"] == 2
    assert state["after_rebase"] is False
    assert state["restored"] is False
    assert pending_write_at, "push failure must reach pending ledger write"
    assert warning_write_at, "push failure must persist a sync warning"
    assert pending_write_at[0] < cap, (
        f"pending write at {pending_write_at[0]:.2f}s >= hook cap {cap}s; "
        "runner can kill the committed session-log path before recovery is durable")
    assert warning_write_at[0] < cap, (
        f"warning write at {warning_write_at[0]:.2f}s >= hook cap {cap}s; "
        "runner can kill the hook before publication failure is visible")


def test_auto_commit_windows_writer_fallback_finishes_before_manifest_timeout(
        tmp_path, monkeypatch):
    """Windows에서 pending identity 재검증이 timeout되어도 fallback
    warning의 durable write가 auto-commit manifest cap 전에 끝나야 한다.

    첫 index.lock 경로 12s + 1s backoff + retry do_commit 총예산 45s 후,
    실제 write_push_pending의 check-ref/cat-file 검증과 실제 fallback
    write_sync_warning을 통과시킨다. cat-file은 Windows run_git의
    timeout 1s + taskkill 5s + drain 2s 상한을 모사한다.
    """
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text(
            encoding="utf-8"))
    cap = next(
        e["timeout"] for e in manifest if e.get("script") == "auto-commit.py")

    spec = importlib.util.spec_from_file_location(
        "auto_commit_windows_writer_deadline_regression",
        REPO / "infra" / "hooks" / "auto-commit.py")
    hook = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hook)

    clock = {"now": 0.0}
    attempts = {"count": 0}
    identity = {
        "key": "branch:main", "branch": "main", "head": "a" * 40,
    }
    monkeypatch.setattr(
        git_ops.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        hook._time, "sleep",
        lambda seconds: clock.__setitem__(
            "now", clock["now"] + float(seconds)))

    def timed_do_commit(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            clock["now"] += 12.0
            return git_ops.CommitResult(
                ok=False, committed=False,
                detail="fatal: .git/index.lock exists")
        clock["now"] += git_ops.PUSH_TOTAL_BUDGET
        return git_ops.CommitResult(
            ok=True, committed=True, pushed=False,
            detail="committed; push timeout", pending_identity=identity)

    def writer_validation_run_git(args, timeout, **_kwargs):
        assert timeout <= 1
        if "check-ref-format" in args:
            clock["now"] += timeout
            return 0, "", ""
        if "cat-file" in args:
            # Windows kill_group: taskkill timeout 5s + communicate drain 2s.
            clock["now"] += timeout + 5 + 2
            raise subprocess.TimeoutExpired(cmd="git cat-file", timeout=timeout)
        raise AssertionError(args)

    monkeypatch.setattr(git_ops, "do_commit", timed_do_commit)
    monkeypatch.setattr(git_ops, "run_git", writer_validation_run_git)
    monkeypatch.setattr(hook, "_kick_push_worker", lambda _root: None)

    warning_completed_at = []
    real_warning_write = git_ops.write_sync_warning

    def contended_warning_write(root, detail):
        # _push_pending_ledger_lock가 규약한 최대 대기 1s.
        clock["now"] += git_ops._PUSH_PENDING_LOCK_WAIT_SECONDS
        real_warning_write(root, detail)
        warning_completed_at.append(clock["now"])

    monkeypatch.setattr(git_ops, "write_sync_warning", contended_warning_write)

    root = tmp_path / "team"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".teammode-active").write_text("", encoding="utf-8")
    edited = root / "edited.md"
    edited.write_text("edited\n", encoding="utf-8")
    monkeypatch.setenv("TEAMMODE_HOME", str(root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        hook.sys, "stdin",
        io.StringIO(json.dumps({
            "event": "PostToolUse",
            "action": "file_edit",
            "files": [str(edited)],
        })))

    assert hook.main() == 0
    assert attempts["count"] == 2
    assert git_ops.read_push_pending(str(root)) == ""
    assert git_ops.read_sync_warning(str(root))
    assert warning_completed_at, "pending validation failure must persist fallback warning"
    assert warning_completed_at[0] < cap, (
        f"fallback warning completed at {warning_completed_at[0]:.2f}s >= "
        f"auto hook cap {cap}s; runner can kill before failure is durable")


def test_session_start_emits_context_before_manifest_timeout(
        tmp_path, monkeypatch):
    """reconcile + upstream refresh + context probes 전체가 SessionStart cap 안에
    context JSON을 출력해야 한다.

    각 Git 하위호출이 자신의 선언 timeout의 99%를 쓰고 성공하는
    정상 경로를 가짜 벽시계로 모사한다. 훅은 실제 main 순서대로
    do_reconcile, warning cleanup, fetch_upstream, _build_context를 전부 호출한다.
    """
    manifest = json.loads(
        (REPO / "infra" / "hooks" / "manifest.json").read_text(
            encoding="utf-8"))
    cap = next(
        e["timeout"] for e in manifest if e.get("script") == "session-start.py")

    monkeypatch.syspath_prepend(str(REPO / "infra" / "hooks"))
    spec = importlib.util.spec_from_file_location(
        "session_start_deadline_regression",
        REPO / "infra" / "hooks" / "session-start.py")
    hook = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hook)
    assert hook._auto_pull is not None

    clock = {"now": 0.0}
    rev_list_calls = {"count": 0}
    monkeypatch.setattr(
        git_ops.time, "monotonic", lambda: clock["now"])

    def fake_run_git(args, timeout, **_kwargs):
        argv = list(args)
        clock["now"] += 0.99 * timeout
        if "--is-inside-work-tree" in argv:
            return _worktree_probe_result(argv)
        if "fetch" in argv:
            return 0, "", ""
        if "remote" in argv and "fetch" not in argv:
            return 0, "origin\nupstream\n", ""
        if "rev-list" in argv:
            rev_list_calls["count"] += 1
            if rev_list_calls["count"] == 1:
                return 0, "1 1\n", ""
            return 0, "0 0\n", ""
        if "status" in argv:
            return 0, "", ""
        if "symbolic-ref" in argv:
            if "--short" in argv:
                return 0, "main\n", ""
            # upstream/HEAD가 없는 clone fallback: detect_default_branch가
            # refs/remotes/upstream/main rev-parse를 한 번 더 수행한다.
            return 1, "", ""
        if "rev-parse" in argv:
            if "refs/stash" in argv:
                return 1, "", ""
            return 0, "b" * 40 + "\n", ""
        if "rebase" in argv:
            return 0, "", ""
        if "diff" in argv:
            return 0, "", ""
        if "show" in argv:
            return 0, "", ""
        raise AssertionError(argv)

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    monkeypatch.setattr(hook, "_kb_guard", None)

    root = tmp_path / "team"
    (root / ".git").mkdir(parents=True)
    (root / ".teammode-active").write_text("", encoding="utf-8")
    (root / "memory" / "team" / "sessions").mkdir(parents=True)
    (root / "memory" / "INDEX.md").write_text("", encoding="utf-8")
    monkeypatch.setenv("TEAMMODE_HOME", str(root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("TEAMMODE_PULL_THROTTLE", "0")
    monkeypatch.setenv("TEAMMODE_UPSTREAM_FETCH_THROTTLE", "0")
    monkeypatch.setattr(
        hook.sys, "stdin", io.StringIO(json.dumps({"event": "SessionStart"})))

    context_emit_at = []

    def timed_print(*args, **kwargs):
        rendered = " ".join(str(arg) for arg in args)
        if "hookSpecificOutput" in rendered:
            context_emit_at.append(clock["now"])

    monkeypatch.setattr(hook, "print", timed_print, raising=False)

    assert hook.main() == 0
    assert context_emit_at, "SessionStart must emit hookSpecificOutput JSON"
    assert context_emit_at[0] < hook._SESSION_START_TOTAL_BUDGET, (
        f"context JSON emitted at {context_emit_at[0]:.2f}s >= internal hard "
        f"budget {hook._SESSION_START_TOTAL_BUDGET}s")
    assert context_emit_at[0] < cap, (
        f"context JSON emitted at {context_emit_at[0]:.2f}s >= hook cap {cap}s; "
        "the runner can kill SessionStart before its primary context output")


def test_session_context_notice_fallback_respects_hard_deadline(
        tmp_path, monkeypatch):
    """upstream/HEAD가 없어서 default-branch fallback이 한 call 늘어도 context
    decoration이 hard deadline을 전부 소비하면 안 된다."""
    monkeypatch.syspath_prepend(str(REPO / "infra" / "hooks"))
    spec = importlib.util.spec_from_file_location(
        "session_start_notice_deadline_regression",
        REPO / "infra" / "hooks" / "session-start.py")
    hook = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hook)

    clock = {"now": 40.0}
    monkeypatch.setattr(hook.time, "monotonic", lambda: clock["now"])

    def fake_run_git(args, timeout, **_kwargs):
        argv = list(args)
        clock["now"] += timeout
        if "--is-inside-work-tree" in argv:
            return _worktree_probe_result(argv)
        if "rev-list" in argv:
            return 0, "0 0\n", ""
        if "symbolic-ref" in argv:
            return 1, "", ""  # upstream/HEAD 없음 → main ref fallback
        if "rev-parse" in argv:
            return 0, "b" * 40 + "\n", ""
        if "show" in argv:
            return 0, "new notice\n", ""
        raise AssertionError(argv)

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)

    class FakeEngine:
        @staticmethod
        def _read_index(_root):
            return ""

        @staticmethod
        def _collect_members(_root):
            return []

        @staticmethod
        def _read_local_notice(_root):
            return "old notice\n"

    monkeypatch.setattr(hook, "_git_ops", git_ops)
    monkeypatch.setattr(hook, "_engine", FakeEngine)

    context = hook._build_context(tmp_path, deadline=50.0)

    assert context is not None
    assert clock["now"] < 50.0, (
        f"fallback NOTICE probes consumed the context hard deadline: {clock['now']}")
