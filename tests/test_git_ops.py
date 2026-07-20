"""V.3 — git_ops 공통 모듈 + `pull` 동사 테스트.

설계: auto_pull.py 의 do_pull(손자 killpg·ff-only·타임아웃·자격증명 차단 안전장치)을
`infra/git_ops.py` 공통 모듈로 끌어올려 재사용한다(신규 git 코드 작성 금지 = 드리프트
방지). pull/commit/auto-pull 이 같은 안전장치를 공유한다.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·실 ~/.claude 무접촉.
"""
import ctypes
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
sys.path.insert(0, str(REPO / "infra" / "hooks"))

import git_ops as go  # noqa: E402

ENGINE = REPO / "infra" / "teammode.py"


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Keep product Git and fixture subprocesses off host configuration."""
    for name in list(os.environ):
        if (name in {
                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        } or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))):
            monkeypatch.delenv(name, raising=False)
    iso = tmp_path_factory.mktemp("git-iso")
    empty_cfg = iso / "empty-gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(iso))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(iso / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


def _git_subcommand(args):
    iterator = iter(args)
    for arg in iterator:
        if arg in ("-C", "-c"):
            next(iterator, None)
            continue
        if str(arg).startswith("-"):
            continue
        return str(arg)
    return ""


def _publication_lock_probe(repo):
    common_raw = Path(_git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    common = common_raw if common_raw.is_absolute() else repo / common_raw
    lock_path = common / ".tm-mode-publication.lock"
    code = (
        "import fcntl,sys\n"
        "f=open(sys.argv[1], 'a+b')\n"
        "try:\n"
        " fcntl.flock(f.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        " print('acquired')\n"
        "except BlockingIOError:\n"
        " print('blocked')\n")
    return subprocess.run(
        [sys.executable, "-c", code, str(lock_path)], check=True,
        capture_output=True, text=True, timeout=5).stdout.strip()


@pytest.fixture
def cloned_repo(tmp_path):
    """upstream(bare) + clone, upstream 1 commit ahead → clone 1 behind."""
    upstream = tmp_path / "upstream.git"
    work = tmp_path / "work"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(work))
    # do_commit(제품 코드)이 만드는 커밋은 _git 헬퍼의 env 주입을 못 받는다 —
    # CI 러너(글로벌 git 설정 없음)에선 identity 자동감지가 fatal 이므로
    # 레포 로컬 config 로 identity 를 고정한다(test_commit.py 와 동일 패턴).
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@t")
    (work / "a.txt").write_text("v1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c1")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "config", "user.name", "t")
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "checkout", "main")
    (work / "b.txt").write_text("v2\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c2")
    _git(work, "push")

    class C:
        pass
    c = C()
    c.upstream, c.work, c.clone = upstream, work, clone
    return c


# ── do_commit push 자동 복구(non-ff → fetch+rebase+재push) ──

def test_is_non_fast_forward_detects_reject_patterns():
    """non-ff 판정 헬퍼: git 의 거부 메시지를 패턴으로 감지."""
    rejected = [
        " ! [rejected]        main -> main (non-fast-forward)",
        "hint: Updates were rejected because the tip of your current branch is behind",
        "! [rejected] main -> main (fetch first)",
        "Updates were rejected because the remote contains work that you do",
    ]
    for msg in rejected:
        assert go._is_non_fast_forward(msg) is True, f"non-ff 미감지: {msg!r}"


def test_is_non_fast_forward_ignores_unrelated_errors():
    """non-ff 가 아닌 실패(인증·네트워크 등)는 False — 자동 복구 트리거 금지."""
    others = [
        "",
        "fatal: Authentication failed for 'https://example/'",
        "fatal: unable to access 'https://example/': Could not resolve host",
        "everything up-to-date",
    ]
    for msg in others:
        assert go._is_non_fast_forward(msg) is False, f"오탐(non-ff 로 잘못 판정): {msg!r}"


def test_do_commit_push_rebases_when_behind(cloned_repo):
    """behind 상태에서 push 가 non-ff 거부되면 fetch+rebase 후 재push 로 성공."""
    clone = cloned_repo.clone
    # clone 에서 로컬 변경 + commit + push 시도 — upstream 은 이미 c2 만큼 ahead.
    (clone / "c.txt").write_text("from clone\n")
    res = go.do_commit(str(clone), "clone commit", push=True)
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is True, f"rebase 후 재push 실패: {res.detail}"
    # upstream(bare)에 clone 의 커밋이 반영됐는지 — work 를 fetch 해 확인.
    _git(cloned_repo.work, "fetch", "origin")
    log = _git(cloned_repo.work, "log", "--oneline", "origin/main").stdout
    assert "clone commit" in log
    # 워킹트리에 rebase 진행중 흔적 없음.
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock probe")
def test_legacy_nonff_recovery_holds_one_interlock_through_rebase(
        cloned_repo, monkeypatch):
    clone = cloned_repo.clone
    (clone / "legacy-chain.txt").write_text("local\n")
    real_run_git = go.run_git
    observations = []

    def probe_during_rebase(args, timeout, **kwargs):
        if "rebase" in args and "--autostash" in args:
            observations.append(_publication_lock_probe(clone))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", probe_during_rebase)
    res = go.do_commit(
        str(clone), "legacy chain", push=True, paths=["legacy-chain.txt"])

    assert res.pushed is True, res.detail
    assert observations == ["blocked"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock probe")
def test_legacy_push_u_recovery_holds_one_interlock_through_rebase(
        cloned_repo, monkeypatch):
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    _git(work, "checkout", "-b", "feat/legacy-lock")
    (work / "remote-feature.txt").write_text("remote\n")
    _git(work, "add", "remote-feature.txt")
    _git(work, "commit", "-m", "remote feature")
    _git(work, "push", "-u", "origin", "feat/legacy-lock")
    _git(clone, "checkout", "-b", "feat/legacy-lock", "main")
    (clone / "local-feature.txt").write_text("local\n")
    real_run_git = go.run_git
    observations = []

    def probe_during_rebase(args, timeout, **kwargs):
        if "rebase" in args and "--autostash" in args:
            observations.append(_publication_lock_probe(clone))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", probe_during_rebase)
    res = go.do_commit(
        str(clone), "legacy push-u chain", push=True,
        paths=["local-feature.txt"])

    assert res.pushed is True, res.detail
    assert observations == ["blocked"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock probe")
def test_legacy_publication_interlock_releases_on_unexpected_exception(
        cloned_repo, monkeypatch):
    clone = cloned_repo.clone
    _git(clone, "pull", "--ff-only")
    (clone / "unexpected.txt").write_text("local\n")

    def unexpected_push(*_args, **_kwargs):
        raise RuntimeError("injected publication bug")

    monkeypatch.setattr(go, "_run_publication_push_locked", unexpected_push)
    res = go.do_commit(
        str(clone), "unexpected", push=True, paths=["unexpected.txt"])

    assert res.committed is True and res.pushed is False
    assert "injected publication bug" in res.detail
    assert _publication_lock_probe(clone) == "acquired"


def test_do_commit_push_rebase_conflict_aborts_nonblocking(cloned_repo):
    """rebase 충돌 시 abort 로 원상복구하고 pushed=False 비차단 반환."""
    clone = cloned_repo.clone
    work = cloned_repo.work
    # upstream 에 a.txt 를 바꾸는 새 커밋 push(work).
    (work / "a.txt").write_text("work edits a\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "work edit a")
    _git(work, "push")
    # clone 도 같은 a.txt 를 다르게 바꿔 commit — rebase 시 충돌.
    (clone / "a.txt").write_text("clone edits a\n")
    res = go.do_commit(str(clone), "clone edit a", push=True)
    assert res.ok is True       # 비차단
    assert res.committed is True  # 로컬 커밋 보존
    assert res.pushed is False    # 충돌로 push 못 함
    # 실제로 복구(fetch+rebase)를 시도하고 충돌로 abort 한 경로를 탔는지 detail 로 단언.
    # (사후상태만 보면 복구 블록을 통째로 꺼도 통과 — 돌연변이 미검출. 이 단언이 막는다.)
    assert "rebase failed" in res.detail and "aborted" in res.detail, \
        f"abort 경로 표식 없음(복구 시도 안 한 상태와 구별 불가): {res.detail!r}"
    # rebase 진행중 상태가 남지 않음(abort 됨).
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()
    # 로컬 커밋과 워킹트리가 보존됨 — clone 의 a.txt 내용 유지.
    assert (clone / "a.txt").read_text() == "clone edits a\n"
    head_msg = _git(clone, "log", "-1", "--format=%s").stdout.strip()
    assert head_msg == "clone edit a"


@pytest.mark.parametrize("failure_kind", ["timeout", "exec-error"])
def test_do_commit_tracked_rebase_exception_reports_failed_abort(
        cloned_repo, monkeypatch, failure_kind):
    """Every legacy tracked-branch rebase exception must report failed abort."""
    clone = cloned_repo.clone
    (clone / "local.txt").write_text("local commit\n")
    real_run_git = go.run_git
    abort_calls = []

    def failing_rebase(args, timeout, **kwargs):
        if "rebase" in args and "--autostash" in args:
            if failure_kind == "timeout":
                raise subprocess.TimeoutExpired(cmd="git rebase", timeout=timeout)
            raise OSError("simulated rebase exec failure")
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", failing_rebase)
    monkeypatch.setattr(
        go, "_abort_rebase",
        lambda *_args: abort_calls.append(True) or False)

    res = go.do_commit(str(clone), "local commit", push=True)

    assert abort_calls
    assert res.committed is True and res.pushed is False
    assert "rebase failed (aborted)" not in res.detail
    assert "abort attempted; rollback not proven" in res.detail


def _real_index_path(repo):
    raw = _git(repo, "rev-parse", "--git-path", "index").stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else repo / path


def _transaction_observation(repo, call_kwargs):
    real_index = _real_index_path(repo)
    overrides = call_kwargs.get("env_overrides") or {}
    raw_private = overrides.get("GIT_INDEX_FILE", "")
    private = Path(raw_private) if raw_private else None
    if private is not None and not private.is_absolute():
        private = Path.cwd() / private
    return {
        "real_index": real_index,
        "real_lock": Path(f"{real_index}.lock").exists(),
        "private": private,
        "private_exists": bool(private and private.exists()),
    }


def _assert_transaction_active(observation):
    assert observation["real_lock"] is True
    assert observation["private"] is not None
    assert observation["private"] != observation["real_index"]
    assert observation["private_exists"] is True


def _assert_transaction_clean(observation):
    private = observation["private"]
    assert not Path(f"{observation['real_index']}.lock").exists()
    assert private is not None
    assert not private.exists()
    assert not Path(f"{private}.lock").exists()


def test_bound_fast_forward_blocks_checkout_at_mutation_boundary(
        cloned_repo, monkeypatch):
    """The real index lock must reject checkout at the FF mutation invocation."""
    clone = cloned_repo.clone
    _git(clone, "checkout", "-b", "other", "main")
    (clone / "other.txt").write_text("other branch\n")
    _git(clone, "add", "other.txt")
    _git(clone, "commit", "-m", "other branch commit")
    other_before = _git(clone, "rev-parse", "refs/heads/other").stdout.strip()
    _git(clone, "checkout", "main")
    main_before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": main_before}
    target = go._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")
    real_run_git = go.run_git
    checkout_attempts = []
    transaction = []

    def racing_run_git(args, timeout, **kwargs):
        if (not checkout_attempts
                and any(command in args
                        for command in ("merge", "reset", "read-tree"))):
            transaction.append(_transaction_observation(clone, kwargs))
            checkout_attempts.append(
                _git(clone, "checkout", "other", check=False))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", racing_run_git)
    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert transaction
    _assert_transaction_active(transaction[0])
    assert checkout_attempts and checkout_attempts[0].returncode != 0
    assert res.ok is True and res.action == "fast-forward"
    assert _git(
        clone, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == remote_head
    assert _git(clone, "rev-parse", "refs/heads/other").stdout.strip() == other_before
    assert res.final_identity == {
        "key": "branch:main", "branch": "main", "head": remote_head,
    }
    _assert_transaction_clean(transaction[0])


@pytest.mark.parametrize("hook_name", ["post-merge", "reference-transaction"])
def test_bound_fast_forward_disables_repository_hook_side_effects(
        cloned_repo, hook_name):
    """Repository hooks must not mutate paths outside the proven FF delta."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    protected = clone / "protected.txt"
    protected.write_bytes(b"protected bytes\n")
    _git(clone, "add", protected.name)
    _git(clone, "commit", "-m", "add protected path")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-only.txt").write_text("remote advance\n", encoding="utf-8")
    _git(work, "add", "remote-only.txt")
    _git(work, "commit", "-m", "remote-only advance")
    _git(work, "push")

    attr_name = ("com.tm-mode.hook-protected"
                 if sys.platform == "darwin"
                 else "user.tm-mode.hook-protected")
    expected_xattr = b"hook-protected metadata\x00\xff"
    if all(hasattr(os, name) for name in ("setxattr", "getxattr")):
        try:
            os.setxattr(
                protected, attr_name, expected_xattr, follow_symlinks=False)
        except OSError as exc:
            pytest.skip(f"filesystem xattrs unavailable: {exc}")

        def read_xattr():
            return os.getxattr(
                protected, attr_name, follow_symlinks=False)
    elif sys.platform == "darwin" and Path("/usr/bin/xattr").is_file():
        wrote = subprocess.run(
            ["/usr/bin/xattr", "-w", "-x", "--", attr_name,
             expected_xattr.hex(), str(protected)],
            capture_output=True, text=True, check=False)
        if wrote.returncode != 0:
            pytest.skip(f"filesystem xattrs unavailable: {wrote.stderr}")

        def read_xattr():
            read = subprocess.run(
                ["/usr/bin/xattr", "-p", "-x", "--", attr_name,
                 str(protected)],
                capture_output=True, text=True, check=False)
            assert read.returncode == 0, read.stderr
            return bytes.fromhex("".join(read.stdout.split()))
    else:
        pytest.skip("filesystem xattrs unavailable")
    before_xattr = read_xattr()

    hooks = clone / ".githooks"
    hooks.mkdir()
    hook_marker = clone / ".post-merge-ran"
    hook = hooks / hook_name
    hook.write_text(
        "#!/bin/sh\n"
        "rm -f protected.txt\n"
        "printf 'protected bytes\\n' > protected.txt\n"
        "printf 'ran\\n' > .post-merge-ran\n",
        encoding="utf-8")
    hook.chmod(0o700)
    _git(clone, "config", "core.hooksPath", str(hooks))

    identity, target = _bound_main_identity_and_target(clone)
    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is True and result.action == "fast-forward", result.detail
    assert hook_marker.exists() is False
    assert protected.read_bytes() == b"protected bytes\n"
    assert read_xattr() == before_xattr


def test_bound_reconcile_foreign_index_lock_defers_without_mutation(
        cloned_repo, monkeypatch):
    """A competing real index.lock must stop reconcile before mutation or push."""
    clone = cloned_repo.clone
    edited = clone / "session.txt"
    edited.write_text("captured session\n")
    index_path = _real_index_path(clone)
    index_lock = Path(f"{index_path}.lock")
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    real_run_git = go.run_git
    captured = {}
    mutation_commands = []
    push_commands = []

    def contended_run_git(args, timeout, **kwargs):
        if any(command in args for command in ("merge", "rebase", "reset", "read-tree")):
            mutation_commands.append(list(args))
        if "push" in args:
            push_commands.append(list(args))
        result = real_run_git(args, timeout, **kwargs)
        if (_git_subcommand(args) == "commit" and result[0] == 0
                and not captured):
            captured.update(
                head=_git(clone, "rev-parse", "refs/heads/main").stdout.strip(),
                index=index_path.read_bytes(),
                status=_git(clone, "status", "--porcelain=v1").stdout,
                worktree=edited.read_bytes(),
            )
            subprocess.run(
                [sys.executable, "-c",
                 ("import os,sys; p=sys.argv[1]; "
                  "fd=os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); "
                  "os.write(fd, b'foreign-lock'); os.close(fd)"),
                 str(index_lock)], check=True)
        return result

    monkeypatch.setattr(go, "run_git", contended_run_git)
    try:
        res = go.do_commit(
            str(clone), "captured session", push=True,
            paths=[edited.name], reconcile_before_push=True)

        assert captured
        assert mutation_commands == []
        assert push_commands == []
        assert res.committed is True and res.pushed is False
        assert res.pending_identity == {
            "key": "branch:main", "branch": "main", "head": captured["head"],
        }
        assert _git(
            clone, "rev-parse", "refs/heads/main").stdout.strip() == captured["head"]
        assert index_path.read_bytes() == captured["index"]
        assert _git(clone, "status", "--porcelain=v1").stdout == captured["status"]
        assert edited.read_bytes() == captured["worktree"]
        assert _git(
            cloned_repo.upstream, "rev-parse",
            "refs/heads/main").stdout.strip() == remote_before
        assert index_lock.read_bytes() == b"foreign-lock"
    finally:
        index_lock.unlink(missing_ok=True)


def test_bound_rebase_blocks_checkout_at_mutation_boundary(
        cloned_repo, monkeypatch):
    """A checkout injected at rebase invocation must lose to the real index lock."""
    clone = cloned_repo.clone
    _git(clone, "checkout", "-b", "other", "main")
    (clone / "other.txt").write_text("other branch\n")
    _git(clone, "add", "other.txt")
    _git(clone, "commit", "-m", "other branch commit")
    other_before = _git(clone, "rev-parse", "refs/heads/other").stdout.strip()
    _git(clone, "checkout", "main")
    edited = clone / "session.txt"
    edited.write_text("captured main\n")
    real_run_git = go.run_git
    checkout_attempts = []
    transaction = []

    def racing_run_git(args, timeout, **kwargs):
        if "rebase" in args and "--abort" not in args and not checkout_attempts:
            transaction.append(_transaction_observation(clone, kwargs))
            checkout_attempts.append(
                _git(clone, "checkout", "other", check=False))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", racing_run_git)
    res = go.do_commit(
        str(clone), "captured main", push=True,
        paths=[edited.name], reconcile_before_push=True,
        _allow_bound_mutation=True)

    assert transaction
    _assert_transaction_active(transaction[0])
    assert checkout_attempts and checkout_attempts[0].returncode != 0
    assert res.committed is True and res.pushed is True, res.detail
    assert _git(
        clone, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert _git(clone, "rev-parse", "refs/heads/other").stdout.strip() == other_before
    local_main = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    remote_main = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    assert local_main == remote_main
    subjects = _git(
        cloned_repo.upstream, "log", "--format=%s", "refs/heads/main").stdout
    assert "c2" in subjects and "captured main" in subjects
    assert res.pending_identity == {
        "key": "branch:main", "branch": "main", "head": local_main,
    }
    _assert_transaction_clean(transaction[0])


def _prepare_dirty_rebase(cloned_repo, *, conflict=False):
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    for name in ("staged.txt", "unstaged.txt"):
        path = clone / name
        path.write_text(f"base {name}\n")
        path.chmod(0o755)
    _git(clone, "add", "staged.txt", "unstaged.txt")
    _git(clone, "commit", "-m", "add dirty-state fixtures")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    remote_path = work / ("a.txt" if conflict else "remote.txt")
    remote_path.write_text("remote conflict\n" if conflict else "remote advance\n")
    _git(work, "add", remote_path.name)
    _git(work, "commit", "-m", "remote advance")
    _git(work, "push")


def _dirty_state(repo):
    paths = ("staged.txt", "unstaged.txt")
    index = {}
    for line in _git(repo, "ls-files", "-s").stdout.splitlines():
        if "\t" in line:
            _metadata, path = line.split("\t", 1)
            index[path] = line
    return {
        "status": _git(repo, "status", "--porcelain=v1").stdout,
        "index": index,
        "worktree": {
            path: ((repo / path).read_bytes(), (repo / path).stat().st_mode & 0o777)
            for path in paths
        },
    }


def _make_staged_and_unstaged_changes(repo):
    staged = repo / "staged.txt"
    unstaged = repo / "unstaged.txt"
    staged.write_text("staged content\n")
    unstaged.write_text("unstaged content\n")
    staged.chmod(0o755)
    unstaged.chmod(0o755)
    _git(repo, "add", "staged.txt")


def test_bound_rebase_preserves_unrelated_staged_and_unstaged_state(cloned_repo):
    """Successful private-index rebase preserves stagedness, blob, and file mode."""
    _prepare_dirty_rebase(cloned_repo)
    clone = cloned_repo.clone
    _make_staged_and_unstaged_changes(clone)
    edited = clone / "session.txt"
    edited.write_text("session\n")
    real_run_git = go.run_git
    captured = {}

    def capture_committed_state(args, timeout, **kwargs):
        result = real_run_git(args, timeout, **kwargs)
        if (_git_subcommand(args) == "commit" and result[0] == 0
                and not captured):
            captured["state"] = _dirty_state(clone)
        return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(go, "run_git", capture_committed_state)
        res = go.do_commit(
            str(clone), "session with dirty state", push=True,
            paths=[edited.name], reconcile_before_push=True,
            _allow_bound_mutation=True)

    assert captured
    assert res.committed is True and res.pushed is True, res.detail
    after = _dirty_state(clone)
    assert after["status"] == captured["state"]["status"]
    assert after["worktree"] == captured["state"]["worktree"]
    assert {
        path: entry for path, entry in after["index"].items()
        if path != "remote.txt"
    } == {
        path: entry for path, entry in captured["state"]["index"].items()
        if path != "remote.txt"
    }


def test_bound_rebase_conflict_restores_exact_state_and_cleans_transaction(
        cloned_repo, monkeypatch):
    """Conflict abort restores branch/index/worktree and leaves no transaction residue."""
    _prepare_dirty_rebase(cloned_repo, conflict=True)
    clone = cloned_repo.clone
    _git(clone, "branch", "other", "main")
    _make_staged_and_unstaged_changes(clone)
    (clone / "a.txt").write_text("local conflict\n")
    real_run_git = go.run_git
    captured = {}
    transaction = []

    def capture_rebase(args, timeout, **kwargs):
        result = real_run_git(args, timeout, **kwargs)
        if (_git_subcommand(args) == "commit" and result[0] == 0
                and not captured):
            captured.update(
                head=_git(clone, "rev-parse", "refs/heads/main").stdout.strip(),
                heads=_git(
                    clone, "for-each-ref", "--format=%(refname)%00%(objectname)",
                    "refs/heads").stdout,
                state=_dirty_state(clone),
            )
        if ("rebase" in args and "--abort" not in args
                and not transaction):
            transaction.append(_transaction_observation(clone, kwargs))
        return result

    monkeypatch.setattr(go, "run_git", capture_rebase)
    res = go.do_commit(
        str(clone), "local conflict", push=True,
        paths=["a.txt"], reconcile_before_push=True,
        _allow_bound_mutation=True)

    assert captured
    assert transaction
    _assert_transaction_active(transaction[0])
    assert res.committed is True and res.pushed is False
    assert "aborted" in res.detail
    assert "rollback not proven" not in res.detail
    assert _git(
        clone, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == captured["head"]
    assert _dirty_state(clone) == captured["state"]
    assert (clone / "a.txt").read_text() == "local conflict\n"
    assert _git(
        clone, "for-each-ref", "--format=%(refname)%00%(objectname)",
        "refs/heads").stdout == captured["heads"]
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()
    assert not (clone / ".git" / "index.lock").exists()
    assert _git(
        clone, "for-each-ref", "--format=%(refname)",
        "refs/tm-mode/reconcile").stdout.strip() == ""
    _assert_transaction_clean(transaction[0])


def _bound_main_identity_and_target(repo):
    head = _git(repo, "rev-parse", "refs/heads/main").stdout.strip()
    return (
        {"key": "branch:main", "branch": "main", "head": head},
        go._PublicationTarget(
            remote="origin", destination="refs/heads/main",
            reconcile_ref="refs/remotes/origin/main"),
    )


def _watched_path_snapshot(path):
    if path.is_dir():
        entries = []
        for child in sorted(path.rglob("*")):
            relative = child.relative_to(path).as_posix()
            mode = child.stat().st_mode & 0o777
            entries.append((relative, "dir" if child.is_dir() else "file",
                            mode, b"" if child.is_dir() else child.read_bytes()))
        return "dir", path.stat().st_mode & 0o777, tuple(entries)
    return "file", path.stat().st_mode & 0o777, path.read_bytes()


def _write_test_xattr(path, name, value):
    if all(hasattr(os, attr) for attr in ("setxattr", "getxattr")):
        try:
            os.setxattr(path, name, value, follow_symlinks=False)
        except OSError as exc:
            pytest.skip(f"filesystem xattrs unavailable: {exc}")
        return
    if sys.platform == "darwin" and Path("/usr/bin/xattr").is_file():
        wrote = subprocess.run(
            ["/usr/bin/xattr", "-w", "-x", "--", name, value.hex(),
             str(path)], capture_output=True, text=True, check=False)
        if wrote.returncode != 0:
            pytest.skip(f"filesystem xattrs unavailable: {wrote.stderr}")
        return
    pytest.skip("filesystem xattr writer unavailable")


def _read_test_xattr(path, name):
    if hasattr(os, "getxattr"):
        return os.getxattr(path, name, follow_symlinks=False)
    read = subprocess.run(
        ["/usr/bin/xattr", "-p", "-x", "--", name, str(path)],
        capture_output=True, text=True, check=False)
    assert read.returncode == 0, read.stderr
    return bytes.fromhex("".join(read.stdout.split()))


@pytest.mark.parametrize("index_flag", ["assume-unchanged", "skip-worktree"])
@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_reconcile_defers_hidden_index_flags_before_mutation(
        cloned_repo, index_flag, reconcile_mode):
    """Hidden dirty tracked bytes must never reach reset/merge/rebase."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    hidden = clone / "hidden.bin"
    hidden.write_bytes(b"tracked baseline\n")
    _git(clone, "add", hidden.name)
    _git(clone, "commit", "-m", "add hidden fixture")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-advance.txt").write_text("remote\n")
    _git(work, "add", "remote-advance.txt")
    _git(work, "commit", "-m", "remote advance")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-only.txt").write_text("local\n")
        _git(clone, "add", "local-only.txt")
        _git(clone, "commit", "-m", "local advance")

    _git(clone, "update-index", f"--{index_flag}", hidden.name)
    hidden.write_bytes(b"SECRET LOCAL RAW BYTES\x00\xff")
    hidden.chmod(0o751)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = {
        "branch": _git(clone, "symbolic-ref", "--short", "HEAD").stdout,
        "head": identity["head"],
        "index": index_path.read_bytes(),
        "path": _watched_path_snapshot(hidden),
        "remote": _git(
            cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout,
    }

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "hidden index flags" in res.detail
    assert _git(clone, "symbolic-ref", "--short", "HEAD").stdout == before["branch"]
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before["head"]
    assert index_path.read_bytes() == before["index"]
    assert _watched_path_snapshot(hidden) == before["path"]
    assert _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout == before["remote"]


@pytest.mark.parametrize("collision_kind", ["file", "directory"])
@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_reconcile_defers_ignored_upstream_path_collisions(
        cloned_repo, collision_kind, reconcile_mode):
    """Ignored local file/path-prefix collisions must be found before mutation."""
    clone, work = cloned_repo.clone, cloned_repo.work
    exclude = clone / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\nignored-local-node\nignored-local-dir/\n")
    if collision_kind == "file":
        watched = clone / "ignored-local-node"
        watched.write_bytes(b"LOCAL IGNORED SECRET\x00\xff")
        watched.chmod(0o751)
        upstream_path = work / "ignored-local-node" / "remote.bin"
        upstream_path.parent.mkdir()
    else:
        watched = clone / "ignored-local-dir"
        watched.mkdir()
        watched.chmod(0o751)
        secret = watched / "secret.bin"
        secret.write_bytes(b"LOCAL DIRECTORY SECRET\x00\xff")
        secret.chmod(0o741)
        # A tracked file at the local ignored directory prefix would replace it.
        upstream_path = work / "ignored-local-dir"
    upstream_path.write_bytes(b"REMOTE TRACKED BYTES\n")
    _git(work, "add", upstream_path.relative_to(work).as_posix())
    _git(work, "commit", "-m", f"remote {collision_kind} collision")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-only.txt").write_text("local\n")
        _git(clone, "add", "local-only.txt")
        _git(clone, "commit", "-m", "local advance")

    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = {
        "branch": _git(clone, "symbolic-ref", "--short", "HEAD").stdout,
        "head": identity["head"],
        "index": index_path.read_bytes(),
        "path": _watched_path_snapshot(watched),
        "remote": _git(
            cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout,
    }

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "ignored path collision" in res.detail
    assert _git(clone, "symbolic-ref", "--short", "HEAD").stdout == before["branch"]
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before["head"]
    assert index_path.read_bytes() == before["index"]
    assert _watched_path_snapshot(watched) == before["path"]
    assert _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout == before["remote"]


def test_bound_reconcile_ignored_collision_honors_git_ignorecase_and_nfc(
        cloned_repo):
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "config", "core.ignorecase", "true")
    exclude = clone / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\nsecret\n")
    watched = clone / "secret"
    watched.write_bytes(b"LOCAL CASE SECRET\x00\xff")
    watched.chmod(0o751)
    upstream_path = work / "Secret"
    upstream_path.write_bytes(b"REMOTE CASE BYTES\n")
    _git(work, "add", upstream_path.name)
    _git(work, "commit", "-m", "remote case collision")
    _git(work, "push")
    (clone / "local-only.txt").write_text("local\n")
    _git(clone, "add", "local-only.txt")
    _git(clone, "commit", "-m", "local advance")
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(),
        _watched_path_snapshot(watched))

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "ignored path collision" in res.detail
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert _watched_path_snapshot(watched) == before[2]


@pytest.mark.parametrize("toggle_replace_overlay", [False, True])
def test_bound_rebase_defers_ignored_path_from_intermediate_replay_tree(
        cloned_repo, monkeypatch, toggle_replace_overlay):
    """A change-then-delete replay path can still overwrite ignored local bytes."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    ignored = clone / "transient.bin"
    (clone / ".gitignore").write_text("transient.bin\n")
    _git(clone, "add", ".gitignore")
    _git(clone, "commit", "-m", "ignore transient replay path")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-only.txt").write_text("remote\n")
    _git(work, "add", "remote-only.txt")
    _git(work, "commit", "-m", "remote disjoint advance")
    _git(work, "push")

    ignored.write_bytes(b"temporary tracked bytes\n")
    _git(clone, "add", "-f", ignored.name)
    _git(clone, "commit", "-m", "add transient replay path")
    add_commit = _git(clone, "rev-parse", "HEAD").stdout.strip()
    add_parent = _git(clone, "rev-parse", "HEAD^").stdout.strip()
    _git(clone, "rm", ignored.name)
    _git(clone, "commit", "-m", "remove transient replay path")
    ignored.write_bytes(b"LOCAL IGNORED SECRET\x00\xff")
    ignored.chmod(0o751)
    assert _git(clone, "status", "--porcelain=v1").stdout == ""

    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(),
        _watched_path_snapshot(ignored))
    mutation_calls = []
    real_run_bound_git = go._run_bound_git
    overlay_installed = {"value": False}

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        command = _git_subcommand(args)
        if command == "rev-list" and not overlay_installed["value"]:
            response = real_run_bound_git(
                team_root, txn, args, timeout, **kwargs)
            if toggle_replace_overlay:
                _git(clone, "replace", add_commit, add_parent)
                overlay_installed["value"] = True
            return response
        if (command == "stash" and "create" in args
                and overlay_installed["value"]):
            _git(clone, "replace", "-d", add_commit)
            overlay_installed["value"] = False
        if (command == "rebase" or command == "merge"
                or (command == "reset" and "--hard" in args)):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)

    try:
        result = go.do_reconcile(
            str(clone), expected_identity=identity, _target=target,
            _allow_bound_mutation=True)
    finally:
        if overlay_installed["value"]:
            _git(clone, "replace", "-d", add_commit)

    assert result.ok is False
    assert "untracked path collision" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert _watched_path_snapshot(ignored) == before[2]


def test_bound_fast_forward_preserves_hidden_dirty_submodule_bytes(
        cloned_repo, tmp_path):
    """A clean-looking superproject must not recursively reset a submodule."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")

    submodule_origin = tmp_path / "submodule-origin"
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main",
         str(submodule_origin)], check=True, capture_output=True)
    _git(submodule_origin, "config", "user.name", "t")
    _git(submodule_origin, "config", "user.email", "t@t")
    submodule_file = submodule_origin / "data.txt"
    submodule_file.write_bytes(b"submodule baseline\n")
    _git(submodule_origin, "add", submodule_file.name)
    _git(submodule_origin, "commit", "-m", "submodule baseline")

    subprocess.run(
        ["git", "-C", str(clone), "-c", "protocol.file.allow=always",
         "submodule", "add", str(submodule_origin), "sm"],
        check=True, capture_output=True, text=True)
    _git(clone, "commit", "-m", "add submodule")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-after-submodule.txt").write_text("remote\n")
    _git(work, "add", "remote-after-submodule.txt")
    _git(work, "commit", "-m", "remote advance after submodule")
    _git(work, "push")

    _git(clone, "config", "submodule.recurse", "true")
    _git(clone, "config", "submodule.sm.ignore", "all")
    watched = clone / "sm" / "data.txt"
    watched.write_bytes(b"HIDDEN LOCAL SUBMODULE SECRET\x00\xff")
    assert _git(clone, "status", "--porcelain=v1").stdout == ""
    before = watched.read_bytes()
    identity, target = _bound_main_identity_and_target(clone)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok and result.action == "fast-forward", result.detail
    assert (clone / "remote-after-submodule.txt").read_text() == "remote\n"
    assert watched.read_bytes() == before


@pytest.mark.parametrize("staged", [False, True], ids=["unstaged", "staged"])
def test_bound_state_proof_uses_raw_bytes_not_textconv(
        cloned_repo, tmp_path, staged):
    """A constant textconv must not make distinct raw states compare equal."""
    clone = cloned_repo.clone
    driver = tmp_path / "constant-textconv"
    driver.write_text("#!/bin/sh\nprintf 'constant-output\\n'\n")
    driver.chmod(0o755)
    (clone / ".gitattributes").write_text("raw.bin diff=constant\n")
    raw = clone / "raw.bin"
    raw.write_bytes(b"baseline\n")
    _git(clone, "config", "diff.constant.textconv", str(driver))
    _git(clone, "add", ".gitattributes", raw.name)
    _git(clone, "commit", "-m", "add textconv fixture")
    identity, _target = _bound_main_identity_and_target(clone)

    states = []
    for payload in (b"first raw bytes\x00\x01", b"second raw bytes\x00\x02"):
        raw.write_bytes(payload)
        if staged:
            _git(clone, "add", raw.name)
        txn, detail = go._begin_bound_index_tx(
            str(clone), identity, go.DEFAULT_TIMEOUT)
        assert txn is not None, detail
        try:
            state = go._capture_bound_user_state(
                str(clone), txn, go.time.monotonic() + 20)
            assert state is not None
            states.append(state)
            assert go._remove_bound_tx_dir(txn)
        finally:
            go._release_bound_lock(txn)

    assert states[0] != states[1]


@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_reconcile_defers_external_clean_filter_before_mutation(
        cloned_repo, tmp_path, reconcile_mode):
    """A clean filter can hide distinct raw worktree bytes from every Git diff."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    driver = tmp_path / "constant-clean-filter"
    driver.write_text("#!/bin/sh\nprintf 'canonical-filter-output\\n'\n")
    driver.chmod(0o755)
    _git(clone, "config", "filter.constant.clean", str(driver))
    _git(clone, "config", "filter.constant.smudge", "cat")
    (clone / ".gitattributes").write_text("raw.bin filter=constant\n")
    raw = clone / "raw.bin"
    raw.write_bytes(b"canonical-filter-output\n")
    raw.chmod(0o751)
    _git(clone, "add", ".gitattributes", raw.name)
    _git(clone, "commit", "-m", "add clean-filter fixture")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-advance.txt").write_text("remote\n")
    _git(work, "add", "remote-advance.txt")
    _git(work, "commit", "-m", "remote advance")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-only.txt").write_text("local\n")
        _git(clone, "add", "local-only.txt")
        _git(clone, "commit", "-m", "local advance")

    raw.write_bytes(b"SECRET RAW BYTES HIDDEN BY CLEAN FILTER\x00\xff")
    # The reproducer is meaningful only if Git's content diff is blind to it.
    assert _git(clone, "diff", "--exit-code", "--", raw.name).returncode == 0
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(), _watched_path_snapshot(raw))

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "working-tree transform" in res.detail
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert _watched_path_snapshot(raw) == before[2]


def test_bound_current_transform_proof_ignores_ambient_git_attr_source(
        cloned_repo, tmp_path, monkeypatch):
    """Host environment cannot redirect dirty worktree attribute lookup."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    raw = clone / "ambient-attr.bin"
    raw.write_bytes(b"canonical\n")
    _git(clone, "add", raw.name)
    _git(clone, "commit", "-m", "add ambient attr fixture")
    _git(clone, "push")
    driver = tmp_path / "ambient-clean-filter"
    driver.write_text("#!/bin/sh\nprintf 'canonical\\n'\n")
    driver.chmod(0o755)
    _git(clone, "config", "filter.ambient.clean", str(driver))
    _git(clone, "config", "filter.ambient.smudge", "cat")
    (clone / ".gitattributes").write_text(
        f"{raw.name} filter=ambient\n")
    raw.write_bytes(b"SECRET HIDDEN BY DIRTY ATTR\x00\xff")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-ambient.txt").write_text("remote\n")
    _git(work, "add", "remote-ambient.txt")
    _git(work, "commit", "-m", "remote ambient advance")
    _git(work, "push")
    monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (identity["head"], index_path.read_bytes(), raw.read_bytes())

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "current working-tree transform" in res.detail
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert raw.read_bytes() == before[2]


@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_reconcile_defers_transform_introduced_by_upstream_candidate(
        cloned_repo, tmp_path, reconcile_mode):
    """The exact fetched candidate tree must be checked before it changes attrs."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    raw = clone / "raw.bin"
    raw.write_bytes(b"candidate-canonical-output\n")
    raw.chmod(0o751)
    _git(clone, "add", raw.name)
    _git(clone, "commit", "-m", "add raw fixture")
    _git(clone, "push")

    driver = tmp_path / "upstream-constant-clean-filter"
    driver.write_text("#!/bin/sh\nprintf 'candidate-canonical-output\\n'\n")
    driver.chmod(0o755)
    _git(clone, "config", "filter.candidate.clean", str(driver))
    _git(clone, "config", "filter.candidate.smudge", "cat")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / ".gitattributes").write_text("raw.bin filter=candidate\n")
    _git(work, "add", ".gitattributes")
    _git(work, "commit", "-m", "remote introduces clean filter")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-only.txt").write_text("local\n")
        _git(clone, "add", "local-only.txt")
        _git(clone, "commit", "-m", "local advance")

    raw.write_bytes(b"SECRET RAW BYTES BEFORE CANDIDATE ATTR\x00\xff")
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(), _watched_path_snapshot(raw))

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "upstream working-tree transform" in res.detail
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert _watched_path_snapshot(raw) == before[2]


@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
@pytest.mark.parametrize(
    "candidate_cleanup_failure,toggle_replace_overlay", [
        (False, False), (False, True), (True, False),
    ], ids=["clean", "replace-overlay", "fsync-fails"])
def test_bound_candidate_transform_uses_safe_old_git_attribute_fallback(
        cloned_repo, tmp_path, monkeypatch, reconcile_mode,
        candidate_cleanup_failure, toggle_replace_overlay):
    """Git without check-attr --source still proves the exact candidate tree."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    raw = clone / "old-git-raw.bin"
    raw.write_bytes(b"candidate-canonical-output\n")
    _git(clone, "add", raw.name)
    _git(clone, "commit", "-m", "add old-git attr fixture")
    _git(clone, "push")

    driver = tmp_path / "old-git-clean-filter"
    driver.write_text("#!/bin/sh\nprintf 'candidate-canonical-output\\n'\n")
    driver.chmod(0o755)
    _git(clone, "config", "filter.oldgit.clean", str(driver))
    _git(clone, "config", "filter.oldgit.smudge", "cat")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / ".gitattributes").write_text(
        f"{raw.name} filter=oldgit\n")
    _git(work, "add", ".gitattributes")
    _git(work, "commit", "-m", "candidate adds old-git filter")
    _git(work, "push")
    upstream_oid = _git(work, "rev-parse", "HEAD").stdout.strip()
    upstream_parent = _git(work, "rev-parse", "HEAD^").stdout.strip()
    if reconcile_mode == "rebase":
        (clone / "old-git-local.txt").write_text("local\n")
        _git(clone, "add", "old-git-local.txt")
        _git(clone, "commit", "-m", "old-git local advance")

    raw.write_bytes(b"SECRET RAW BYTES BEFORE OLD-GIT ATTR\x00\xff")
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(), _watched_path_snapshot(raw))
    real_run_git = go.run_git
    real_fsync_parent = go._fsync_parent_dir
    observations = []
    cleanup_failure = {"seen": False}
    overlay_installed = {"value": False}
    mutation_calls = []

    def emulate_git_without_check_attr_source(args, timeout, **kwargs):
        subcommand = _git_subcommand(args)
        source_args = [
            str(arg) for arg in args if str(arg).startswith("--source=")]
        if subcommand == "check-attr" and source_args:
            observations.append(("source-rejected", source_args[0], ""))
            if (toggle_replace_overlay
                    and source_args[0] == f"--source={upstream_oid}"
                    and not overlay_installed["value"]):
                _git(clone, "replace", upstream_oid, upstream_parent)
                overlay_installed["value"] = True
            return 129, "", "error: unknown option `source'"
        if subcommand == "read-tree":
            observations.append((
                "read-tree", str(args[-1]),
                str((kwargs.get("env_overrides") or {}).get(
                    "GIT_INDEX_FILE", ""))))
        if subcommand == "check-attr" and "--cached" in args:
            observations.append((
                "cached", "",
                str((kwargs.get("env_overrides") or {}).get(
                    "GIT_INDEX_FILE", ""))))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(
        go, "run_git", emulate_git_without_check_attr_source)
    real_run_bound_git = go._run_bound_git

    def observe_bound_mutation(team_root, txn, args, timeout, **kwargs):
        command = _git_subcommand(args)
        if (command == "stash" and "create" in args
                and overlay_installed["value"]):
            _git(clone, "replace", "-d", upstream_oid)
            overlay_installed["value"] = False
        if (command in {"merge", "rebase"}
                or (command == "reset" and "--hard" in args)):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", observe_bound_mutation)
    if candidate_cleanup_failure:
        def fail_candidate_cleanup_fsync(path):
            if (Path(path).name.startswith("attr-index-")
                    and not cleanup_failure["seen"]):
                cleanup_failure["seen"] = True
                return False
            return real_fsync_parent(path)

        monkeypatch.setattr(
            go, "_fsync_parent_dir", fail_candidate_cleanup_fsync)

    try:
        res = go.do_reconcile(
            str(clone), expected_identity=identity, _target=target,
            _allow_bound_mutation=True)
    finally:
        if overlay_installed["value"]:
            _git(clone, "replace", "-d", upstream_oid)

    assert res.ok is False
    if candidate_cleanup_failure:
        assert cleanup_failure["seen"]
        assert "working-tree transform proof unavailable" in res.detail
        assert "recovery cleanup failed" in res.detail
    else:
        assert "upstream working-tree transform" in res.detail
        assert mutation_calls == []
    assert any(
        kind == "source-rejected" for kind, _source, _index in observations)
    read_tree = [item for item in observations if item[0] == "read-tree"]
    cached = [item for item in observations if item[0] == "cached"]
    assert read_tree and cached
    expected_fallback_source = (
        identity["head"] if candidate_cleanup_failure else upstream_oid)
    assert any(source == expected_fallback_source
               for _kind, source, _index in read_tree)
    assert all(Path(index).parent.name.startswith(".tm-mode-reconcile-")
               for _kind, _source, index in read_tree + cached)
    assert {index for _kind, _source, index in read_tree} == {
        index for _kind, _source, index in cached}
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert _watched_path_snapshot(raw) == before[2]
    tx_dirs = list(index_path.parent.glob(".tm-mode-reconcile-*"))
    if candidate_cleanup_failure:
        assert len(tx_dirs) == 1
        assert (tx_dirs[0] / "RECOVERY").is_file()
        assert not list(tx_dirs[0].glob("attr-index-*"))
        assert "transaction directory" in go.publication_blocker_detail(
            str(clone), go.DEFAULT_TIMEOUT)
    else:
        assert not tx_dirs
        assert _git(
            clone, "for-each-ref", "--format=%(refname)",
            "refs/tm-mode/reconcile").stdout.strip() == ""


@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_old_git_attribute_fallback_allows_fast_forward_and_defers_rebase(
        cloned_repo, monkeypatch, reconcile_mode):
    """Candidate attrs can fall back, but exact replay paths require --source."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "old-git-safe-remote.txt").write_text("remote\n")
    _git(work, "add", "old-git-safe-remote.txt")
    _git(work, "commit", "-m", "safe old-git remote advance")
    _git(work, "push")
    upstream_oid = _git(work, "rev-parse", "HEAD").stdout.strip()
    if reconcile_mode == "rebase":
        (clone / "old-git-safe-local.txt").write_text("local\n")
        _git(clone, "add", "old-git-safe-local.txt")
        _git(clone, "commit", "-m", "safe old-git local advance")

    dirty = clone / "a.txt"
    dirty.write_bytes(b"safe candidate preserves dirty bytes\x00\xff")
    identity, target = _bound_main_identity_and_target(clone)
    before_status = _git(
        clone, "status", "--porcelain=v1", "-z").stdout
    before_raw = dirty.read_bytes()
    observations = []
    real_run_git = go.run_git

    def emulate_git_without_check_attr_source(args, timeout, **kwargs):
        subcommand = _git_subcommand(args)
        source_args = [
            str(arg) for arg in args if str(arg).startswith("--source=")]
        if subcommand == "check-attr" and source_args:
            observations.append(("source-rejected", source_args[0], ""))
            return 129, "", "error: unknown option `source'"
        if subcommand == "read-tree":
            observations.append((
                "read-tree", str(args[-1]),
                str((kwargs.get("env_overrides") or {}).get(
                    "GIT_INDEX_FILE", ""))))
        if subcommand == "check-attr" and "--cached" in args:
            observations.append((
                "cached", "",
                str((kwargs.get("env_overrides") or {}).get(
                    "GIT_INDEX_FILE", ""))))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(
        go, "run_git", emulate_git_without_check_attr_source)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    read_tree = [item for item in observations if item[0] == "read-tree"]
    cached = [item for item in observations if item[0] == "cached"]
    assert any(source == identity["head"]
               for _kind, source, _index in read_tree)
    assert any(source == upstream_oid
               for _kind, source, _index in read_tree)
    assert {index for _kind, _source, index in read_tree} == {
        index for _kind, _source, index in cached}
    if reconcile_mode == "rebase":
        assert res.ok is False
        assert "mutation-path proof unavailable" in res.detail
        assert _git(
            clone, "rev-parse", "refs/heads/main").stdout.strip() == identity["head"]
    else:
        assert res.ok is True, res.detail
        assert res.action == "fast-forward"
        assert res.final_identity == {
            "key": "branch:main", "branch": "main",
            "head": _git(
                clone, "rev-parse", "refs/heads/main").stdout.strip(),
        }
        assert _git(
            clone, "merge-base", "--is-ancestor", upstream_oid,
            "HEAD").returncode == 0
    assert dirty.read_bytes() == before_raw
    assert _git(
        clone, "status", "--porcelain=v1", "-z").stdout == before_status
    index_path = _real_index_path(clone)
    assert not Path(f"{index_path}.lock").exists()
    assert not list(index_path.parent.glob(".tm-mode-reconcile-*"))
    assert _git(
        clone, "for-each-ref", "--format=%(refname)",
        "refs/tm-mode/reconcile").stdout.strip() == ""


def test_bound_old_git_rebase_defers_generated_df_conflict_path_before_mutation(
        cloned_repo, monkeypatch):
    """Old Git cannot prove merge-ort's synthetic D/F conflict paths exactly."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    (clone / ".gitignore").write_text("d~HEAD*\n")
    base_dir = clone / "d"
    base_dir.mkdir()
    (base_dir / "base").write_text("base\n")
    _git(clone, "add", ".gitignore", "d/base")
    _git(clone, "commit", "-m", "add directory conflict base")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (base_dir / "local").write_text("local\n")
    _git(clone, "add", "d/local")
    _git(clone, "commit", "-m", "local keeps directory")
    _git(work, "rm", "-r", "d")
    (work / "d").write_text("upstream file\n")
    _git(work, "add", "d")
    _git(work, "commit", "-m", "upstream replaces directory")
    _git(work, "push")

    ignored = clone / "d~HEAD"
    ignored.write_bytes(b"IGNORED RECOVERY SECRET\x00\xff")
    assert _git(
        clone, "status", "--porcelain=v1", "-z",
        "--untracked-files=all").stdout == ""
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (identity["head"], index_path.read_bytes(), ignored.read_bytes())
    mutation_calls = []
    real_run_bound_git = go._run_bound_git

    def emulate_old_git(team_root, txn, args, timeout, **kwargs):
        if (args and args[0] == "check-attr"
                and any(str(arg).startswith("--source=") for arg in args)):
            return 129, "", "error: unknown option `source'\nusage: git check-attr"
        command = _git_subcommand(args)
        if (command in {"merge", "rebase"}
                or (command == "reset" and "--hard" in args)):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", emulate_old_git)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "mutation-path proof unavailable" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert ignored.is_file() and ignored.read_bytes() == before[2]


def test_bound_rebase_proves_more_than_thirty_two_local_commits(
        cloned_repo, monkeypatch):
    """Long local histories use one immutable rev-list snapshot."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-after-long-pending.txt").write_text("remote\n")
    _git(work, "add", "remote-after-long-pending.txt")
    _git(work, "commit", "-m", "remote after long pending")
    _git(work, "push")

    local_state = clone / "long-pending" / "state.txt"
    local_state.parent.mkdir()
    original_local_head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    replay_parent = original_local_head
    for index in range(33):
        local_state.write_text(f"local {index}\n")
        _git(clone, "add", local_state.relative_to(clone).as_posix())
        replay_tree = _git(clone, "write-tree").stdout.strip()
        replay_parent = _git(
            clone, "commit-tree", replay_tree, "-p", replay_parent,
            "-m", f"long pending {index:02d}").stdout.strip()
    _git(
        clone, "update-ref", "refs/heads/main", replay_parent,
        original_local_head)

    rev_list_calls = []
    real_run_bound_git = go._run_bound_git

    def observe_rev_list(team_root, txn, args, timeout, **kwargs):
        if args and args[0] == "rev-list":
            rev_list_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", observe_rev_list)
    identity, target = _bound_main_identity_and_target(clone)
    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok and result.action == "rebased", result.detail
    assert len(rev_list_calls) == 1
    assert (clone / "remote-after-long-pending.txt").read_text() == "remote\n"
    assert local_state.read_text() == "local 32\n"


def test_bound_rebase_replay_capture_limit_defers_before_mutation(
        cloned_repo, monkeypatch):
    """The single-snapshot stdout bound fails closed without touching user state."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "pagination-remote.txt").write_text("remote\n")
    _git(work, "add", "pagination-remote.txt")
    _git(work, "commit", "-m", "pagination remote")
    _git(work, "push")
    for index in range(2):
        path = clone / f"pagination-local-{index}.txt"
        path.write_text(f"local {index}\n")
        _git(clone, "add", path.name)
        _git(clone, "commit", "-m", f"pagination local {index}")
    _git(clone, "fetch", "origin")

    identity, _target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(),
        tuple((clone / f"pagination-local-{index}.txt").read_bytes()
              for index in range(2)))
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail
    monkeypatch.setattr(go, "_BOUND_REBASE_PROOF_CAPTURE_LIMIT", 1)
    try:
        proof = go._bound_worktree_mutation_paths(
            str(clone), txn, go.time.monotonic() + 20,
            mode="rebase",
            upstream_source=_git(
                clone, "rev-parse", "refs/remotes/origin/main").stdout.strip())
        assert proof is None
        assert _git(
            clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
        assert index_path.read_bytes() == before[1]
        assert tuple((clone / f"pagination-local-{index}.txt").read_bytes()
                     for index in range(2)) == before[2]
    finally:
        assert go._remove_bound_tx_dir(txn)
        assert go._release_bound_lock(txn)[0]


@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
@pytest.mark.parametrize(
    "metadata_kind", ["mode", "xattr", "hardlink", "flags", "acl"])
def test_bound_reconcile_defers_nonrecoverable_tracked_metadata_before_mutation(
        cloned_repo, tmp_path, monkeypatch, reconcile_mode, metadata_kind):
    """reset/stash must not normalize inode metadata Git cannot reproduce."""
    if metadata_kind in {"flags", "acl"} and sys.platform != "darwin":
        pytest.skip("Darwin inode metadata contract")
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    tracked = clone / "metadata\nfixture.bin"
    tracked.write_bytes(b"tracked baseline\n")
    tracked.chmod(0o644)
    _git(clone, "add", tracked.name)
    _git(clone, "commit", "-m", "add metadata fixture")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / tracked.name).write_bytes(b"remote replacement\n")
    _git(work, "add", tracked.name)
    _git(work, "commit", "-m", "remote metadata advance")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-metadata.txt").write_text("local\n")
        _git(clone, "add", "local-metadata.txt")
        _git(clone, "commit", "-m", "local metadata advance")

    linked = tmp_path / "metadata-hardlink"
    attr_name = ("com.tm-mode.worktree-test" if sys.platform == "darwin"
                 else "user.tm-mode.worktree-test")
    if metadata_kind == "mode":
        tracked.chmod(0o610)
    elif metadata_kind == "xattr":
        if hasattr(os, "setxattr"):
            try:
                os.setxattr(
                    tracked, attr_name, b"custom metadata\x00\xff",
                    follow_symlinks=False)
            except OSError as exc:
                pytest.skip(f"filesystem xattrs unavailable: {exc}")
        elif sys.platform == "darwin":
            wrote = subprocess.run(
                ["/usr/bin/xattr", "-w", "-x", "--", attr_name,
                 b"custom metadata\x00\xff".hex(), str(tracked)],
                capture_output=True, text=True, check=False)
            if wrote.returncode != 0:
                pytest.skip(f"filesystem xattrs unavailable: {wrote.stderr}")
        else:
            pytest.skip("filesystem xattr writer unavailable")
    elif metadata_kind == "hardlink":
        os.link(tracked, linked)
    elif metadata_kind == "flags":
        os.chflags(tracked, stat.UF_HIDDEN, follow_symlinks=False)
    elif metadata_kind == "acl":
        import pwd
        acl = f"user:{pwd.getpwuid(os.getuid()).pw_name} allow read"
        wrote = subprocess.run(
            ["/bin/chmod", "+a", acl, str(tracked)],
            capture_output=True, text=True, check=False)
        if wrote.returncode != 0:
            pytest.skip(f"Darwin ACL fixture unavailable: {wrote.stderr}")

    def metadata_snapshot():
        current = os.lstat(tracked)
        try:
            if all(hasattr(os, name) for name in ("listxattr", "getxattr")):
                attrs = tuple(sorted(
                    (os.fsencode(name), os.getxattr(
                        tracked, name, follow_symlinks=False))
                    for name in os.listxattr(
                        tracked, follow_symlinks=False)))
            elif sys.platform == "darwin":
                attrs = go._capture_xattrs_with_backend(
                    tracked, go._DARWIN_XATTR_BACKEND)
            else:
                attrs = ()
        except (AttributeError, OSError):
            attrs = ()
        acl_text = ""
        if sys.platform == "darwin":
            acl_text = subprocess.run(
                ["/bin/ls", "-lde", str(tracked)], capture_output=True,
                text=True, check=False).stdout
        return (
            tracked.read_bytes(), stat.S_IMODE(current.st_mode),
            current.st_uid, current.st_gid, current.st_nlink,
            getattr(current, "st_flags", 0), attrs, acl_text)

    # Bound rebase safety performs a read-only Git diff before the private-index
    # transaction.  Refresh its canonical stat cache now so the before snapshot
    # measures reconcile mutation rather than that ordinary lstat refresh.
    _git(clone, "update-index", "--refresh", check=False)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    before = (identity["head"], index_path.read_bytes(), metadata_snapshot())
    mutation_calls = []
    real_run_bound_git = go._run_bound_git

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)
    try:
        res = go.do_reconcile(
            str(clone), expected_identity=identity, _target=target,
            _allow_bound_mutation=True)

        assert res.ok is False
        assert "non-recoverable tracked path" in res.detail
        assert _git(
            clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
        assert index_path.read_bytes() == before[1]
        assert metadata_snapshot() == before[2]
        assert mutation_calls == []
        assert _git(
            cloned_repo.upstream, "rev-parse",
            "refs/heads/main").stdout.strip() == remote_before
    finally:
        if metadata_kind == "flags":
            os.chflags(tracked, 0, follow_symlinks=False)


def test_bound_fast_forward_checks_casefold_alias_metadata_before_mutation(
        cloned_repo, monkeypatch):
    """A casefold-equivalent upstream path can replace the tracked inode."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    tracked = clone / "Foo"
    tracked.write_bytes(b"case-preserved bytes\n")
    _git(clone, "add", tracked.name)
    _git(clone, "commit", "-m", "add case alias fixture")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")

    blob = subprocess.run(
        ["git", "-C", str(work), "hash-object", "-w", "--stdin"],
        input="lowercase alias bytes\n", capture_output=True, text=True,
        check=True).stdout.strip()
    entries = [
        line for line in _git(work, "ls-tree", "HEAD").stdout.splitlines()
        if line]
    entries.append(f"100644 blob {blob}\tfoo")
    entries.sort(key=lambda line: line.split("\t", 1)[1].encode("utf-8"))
    alias_tree = subprocess.run(
        ["git", "-C", str(work), "mktree"],
        input="\n".join(entries) + "\n", capture_output=True, text=True,
        check=True).stdout.strip()
    base_head = _git(work, "rev-parse", "HEAD").stdout.strip()
    alias_commit = subprocess.run(
        ["git", "-C", str(work), "commit-tree", alias_tree,
         "-p", base_head],
        input="add casefold alias\n", capture_output=True, text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }, check=True).stdout.strip()
    _git(work, "update-ref", "refs/heads/main", alias_commit, base_head)
    _git(work, "push")
    tree_paths = _git(
        work, "ls-tree", "-r", "--name-only", alias_commit).stdout.splitlines()
    assert "Foo" in tree_paths and "foo" in tree_paths

    _git(clone, "config", "core.ignorecase", "true")
    attr_name = ("com.tm-mode.case-alias"
                 if sys.platform == "darwin"
                 else "user.tm-mode.case-alias")
    expected_xattr = b"case alias metadata\x00\xff"
    if all(hasattr(os, name) for name in ("setxattr", "getxattr")):
        try:
            os.setxattr(
                tracked, attr_name, expected_xattr, follow_symlinks=False)
        except OSError as exc:
            pytest.skip(f"filesystem xattrs unavailable: {exc}")

        def read_xattr():
            return os.getxattr(
                tracked, attr_name, follow_symlinks=False)
    elif sys.platform == "darwin" and Path("/usr/bin/xattr").is_file():
        wrote = subprocess.run(
            ["/usr/bin/xattr", "-w", "-x", "--", attr_name,
             expected_xattr.hex(), str(tracked)],
            capture_output=True, text=True, check=False)
        if wrote.returncode != 0:
            pytest.skip(f"filesystem xattrs unavailable: {wrote.stderr}")

        def read_xattr():
            read = subprocess.run(
                ["/usr/bin/xattr", "-p", "-x", "--", attr_name,
                 str(tracked)],
                capture_output=True, text=True, check=False)
            assert read.returncode == 0, read.stderr
            return bytes.fromhex("".join(read.stdout.split()))
    else:
        pytest.skip("filesystem xattrs unavailable")
    before_xattr = read_xattr()

    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    index_before = index_path.read_bytes()
    mutation_calls = []
    real_run_bound_git = go._run_bound_git

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)
    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "non-recoverable tracked path xattrs" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == identity["head"]
    assert index_path.read_bytes() == index_before
    assert tracked.read_bytes() == b"case-preserved bytes\n"
    assert read_xattr() == before_xattr


def test_bound_rebase_ignores_stable_parent_macl_and_preserves_xattr(
        cloned_repo, monkeypatch):
    """Disjoint member logs must not inspect a parent Git cannot recreate."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    alice_parent = clone / "memory" / "team" / "sessions" / "alice"
    alice_parent.mkdir(parents=True)
    alice_log = alice_parent / "2026-07-20.md"
    alice_log.write_text("alice baseline\n")
    _git(clone, "add", "memory/team/sessions/alice")
    _git(clone, "commit", "-m", "add alice log baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    bob_log = work / "memory" / "team" / "sessions" / "bob" / "2026-07-20.md"
    bob_log.parent.mkdir(parents=True)
    bob_log.write_text("bob remote log\n")
    _git(work, "add", "memory/team/sessions/bob/2026-07-20.md")
    _git(work, "commit", "-m", "bob remote log")
    _git(work, "push")
    remote_head = _git(work, "rev-parse", "HEAD").stdout.strip()

    alice_log.write_text("alice local log\n")
    _git(clone, "add", "memory/team/sessions/alice/2026-07-20.md")
    _git(clone, "commit", "-m", "alice local log")

    attr_name = ("com.tm-mode.stable-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.stable-parent")
    requested_xattr = b"stable parent metadata\x00\xff"
    _write_test_xattr(alice_parent, attr_name, requested_xattr)
    before_xattr = _read_test_xattr(alice_parent, attr_name)
    before_parent = os.lstat(alice_parent)

    real_xattr_names = go._nofollow_xattr_names

    def stable_parent_macl(path):
        if Path(path) == alice_parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    monkeypatch.setattr(go, "_nofollow_xattr_names", stable_parent_macl)
    identity, target = _bound_main_identity_and_target(clone)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is True, result.detail
    assert result.action == "rebased"
    assert _git(
        clone, "merge-base", "--is-ancestor", remote_head,
        "HEAD").returncode == 0
    assert alice_log.read_text() == "alice local log\n"
    assert (clone / bob_log.relative_to(work)).read_text() == "bob remote log\n"
    after_parent = os.lstat(alice_parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(alice_parent, attr_name) == before_xattr


def test_bound_fast_forward_defers_xattr_when_parent_children_are_replaced(
        cloned_repo, monkeypatch):
    """Tree presence alone is unsafe when Git replaces every child inode."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    replaced_parent = clone / "replaced-parent"
    replaced_parent.mkdir()
    old_child = replaced_parent / "old.txt"
    old_child.write_text("old\n")
    _git(clone, "add", "replaced-parent/old.txt")
    _git(clone, "commit", "-m", "add replace-parent baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    _git(work, "rm", "replaced-parent/old.txt")
    new_child = work / "replaced-parent" / "new.txt"
    new_child.parent.mkdir()
    new_child.write_text("new\n")
    _git(work, "add", "replaced-parent/new.txt")
    _git(work, "commit", "-m", "replace every parent child")
    _git(work, "push")

    attr_name = ("com.tm-mode.replaced-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.replaced-parent")
    _write_test_xattr(
        replaced_parent, attr_name, b"replacement would lose this\x00\xff")
    before_xattr = _read_test_xattr(replaced_parent, attr_name)
    before_parent = os.lstat(replaced_parent)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (identity["head"], index_path.read_bytes(), old_child.read_bytes())
    mutation_calls = []
    real_run_bound_git = go._run_bound_git
    real_xattr_names = go._nofollow_xattr_names

    def replaced_parent_macl(path):
        if Path(path) == replaced_parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_nofollow_xattr_names", replaced_parent_macl)
    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "non-recoverable parent directory metadata xattrs" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert old_child.read_bytes() == before[2]
    assert not (clone / new_child.relative_to(work)).exists()
    after_parent = os.lstat(replaced_parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(replaced_parent, attr_name) == before_xattr


def test_bound_fast_forward_untracked_leaf_anchors_parent_xattr(
        cloned_repo, monkeypatch):
    """A proven non-colliding untracked leaf prevents parent recreation."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    parent = clone / "untracked-anchor-parent"
    parent.mkdir()
    old_child = parent / "old.txt"
    old_child.write_text("old\n")
    _git(clone, "add", "untracked-anchor-parent/old.txt")
    _git(clone, "commit", "-m", "add untracked anchor baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    _git(work, "rm", "untracked-anchor-parent/old.txt")
    new_child = work / "untracked-anchor-parent" / "new.txt"
    new_child.parent.mkdir()
    new_child.write_text("new\n")
    _git(work, "add", "untracked-anchor-parent/new.txt")
    _git(work, "commit", "-m", "replace tracked children upstream")
    _git(work, "push")

    untracked_anchor = parent / "keep.bin"
    untracked_anchor.write_bytes(b"untracked anchor\x00\xff")
    attr_name = ("com.tm-mode.untracked-anchor-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.untracked-anchor-parent")
    _write_test_xattr(parent, attr_name, b"untracked parent metadata\x00\xff")
    before_xattr = _read_test_xattr(parent, attr_name)
    before_parent = os.lstat(parent)
    before_status = _git(
        clone, "status", "--porcelain=v1", "-z").stdout
    identity, target = _bound_main_identity_and_target(clone)
    real_xattr_names = go._nofollow_xattr_names

    def parent_macl(path):
        if Path(path) == parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    monkeypatch.setattr(go, "_nofollow_xattr_names", parent_macl)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is True, result.detail
    assert result.action == "fast-forward"
    assert not old_child.exists()
    assert (clone / new_child.relative_to(work)).read_text() == "new\n"
    assert untracked_anchor.read_bytes() == b"untracked anchor\x00\xff"
    assert _git(clone, "status", "--porcelain=v1", "-z").stdout == before_status
    after_parent = os.lstat(parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(parent, attr_name) == before_xattr


def test_bound_rebase_does_not_chain_different_phantom_parent_anchors(
        cloned_repo, monkeypatch):
    """Original commit trees cannot stand in for actual replay tree states."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    parent = clone / "replay-parent"
    parent.mkdir()
    for name in ("p.txt", "r.txt", "t.txt"):
        (parent / name).write_text(f"{name}\n")
    _git(clone, "add", "replay-parent")
    _git(clone, "commit", "-m", "add replay parent baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    _git(work, "rm", "replay-parent/p.txt")
    _git(work, "commit", "-m", "upstream removes p")
    _git(work, "push")

    _git(clone, "rm", "replay-parent/t.txt")
    _git(clone, "commit", "-m", "local replay removes t")
    _git(clone, "rm", "replay-parent/r.txt")
    (parent / "t.txt").write_text("t restored\n")
    _git(clone, "add", "replay-parent/t.txt")
    _git(clone, "commit", "-m", "local replay swaps r for t")

    attr_name = ("com.tm-mode.replay-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.replay-parent")
    _write_test_xattr(parent, attr_name, b"replay parent metadata\x00\xff")
    before_xattr = _read_test_xattr(parent, attr_name)
    before_parent = os.lstat(parent)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(),
        tuple(sorted(
            (child.name, child.read_bytes()) for child in parent.iterdir())))
    mutation_calls = []
    real_run_bound_git = go._run_bound_git
    real_xattr_names = go._nofollow_xattr_names

    def replay_parent_macl(path):
        if Path(path) == parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_nofollow_xattr_names", replay_parent_macl)
    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "non-recoverable parent directory metadata xattrs" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert tuple(sorted(
        (child.name, child.read_bytes()) for child in parent.iterdir())) == before[2]
    after_parent = os.lstat(parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(parent, attr_name) == before_xattr


def test_bound_rebase_defers_parent_without_one_global_rollback_anchor(
        cloned_repo, monkeypatch):
    """Different forward anchors cannot prove the direct rollback transition."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    parent = clone / "rotating-parent"
    parent.mkdir()
    for name in ("a.txt", "b.txt"):
        (parent / name).write_text(f"{name}\n")
    _git(clone, "add", "rotating-parent")
    _git(clone, "commit", "-m", "add rotating parent baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    remote_leaf = work / "remote-rotating.txt"
    remote_leaf.write_text("remote\n")
    _git(work, "add", remote_leaf.name)
    _git(work, "commit", "-m", "remote rotating advance")
    _git(work, "push")
    _git(clone, "rm", "rotating-parent/a.txt")
    (parent / "c.txt").write_text("c.txt\n")
    _git(clone, "add", "rotating-parent/c.txt")
    _git(clone, "commit", "-m", "rotate a to c")
    _git(clone, "rm", "rotating-parent/b.txt")
    (parent / "d.txt").write_text("d.txt\n")
    _git(clone, "add", "rotating-parent/d.txt")
    _git(clone, "commit", "-m", "rotate b to d")
    (parent / "a.txt").write_text("a restored\n")
    _git(clone, "add", "rotating-parent/a.txt")
    _git(clone, "commit", "-m", "restore a anchor")

    attr_name = ("com.tm-mode.rotating-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.rotating-parent")
    _write_test_xattr(parent, attr_name, b"rotating parent metadata\x00\xff")
    before_xattr = _read_test_xattr(parent, attr_name)
    before_parent = os.lstat(parent)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(),
        tuple(sorted(
            (child.name, child.read_bytes()) for child in parent.iterdir())))
    mutation_calls = []
    real_xattr_names = go._nofollow_xattr_names
    real_run_bound_git = go._run_bound_git

    def rotating_parent_macl(path):
        if Path(path) == parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_nofollow_xattr_names", rotating_parent_macl)
    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "non-recoverable parent directory metadata xattrs" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert tuple(sorted(
        (child.name, child.read_bytes()) for child in parent.iterdir())) == before[2]
    after_parent = os.lstat(parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(parent, attr_name) == before_xattr


def test_bound_fast_forward_dirty_leaf_keeps_stable_parent_xattr(
        cloned_repo, monkeypatch):
    """A dirty content rewrite keeps its path and cannot empty the parent."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    stable_parent = clone / "dirty-stable-parent"
    stable_parent.mkdir()
    dirty_leaf = stable_parent / "leaf.txt"
    dirty_leaf.write_text("baseline\n")
    _git(clone, "add", "dirty-stable-parent/leaf.txt")
    _git(clone, "commit", "-m", "add dirty parent baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    remote_leaf = work / "remote-only" / "leaf.txt"
    remote_leaf.parent.mkdir()
    remote_leaf.write_text("remote\n")
    _git(work, "add", "remote-only/leaf.txt")
    _git(work, "commit", "-m", "remote disjoint advance")
    _git(work, "push")

    dirty_leaf.write_bytes(b"dirty local bytes\x00\xff")
    attr_name = ("com.tm-mode.dirty-stable-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.dirty-stable-parent")
    _write_test_xattr(
        stable_parent, attr_name, b"dirty stable metadata\x00\xff")
    before_xattr = _read_test_xattr(stable_parent, attr_name)
    before_parent = os.lstat(stable_parent)
    before_status = _git(
        clone, "status", "--porcelain=v1", "-z").stdout
    identity, target = _bound_main_identity_and_target(clone)
    real_xattr_names = go._nofollow_xattr_names

    def stable_parent_macl(path):
        if Path(path) == stable_parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    monkeypatch.setattr(go, "_nofollow_xattr_names", stable_parent_macl)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is True, result.detail
    assert result.action == "fast-forward"
    assert dirty_leaf.read_bytes() == b"dirty local bytes\x00\xff"
    assert _git(clone, "status", "--porcelain=v1", "-z").stdout == before_status
    assert (clone / remote_leaf.relative_to(work)).read_text() == "remote\n"
    after_parent = os.lstat(stable_parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(stable_parent, attr_name) == before_xattr


def test_bound_rebase_defers_xattr_on_parent_that_may_be_recreated(
        cloned_repo, monkeypatch):
    """A parent absent at the upstream reset point still needs xattr safety."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    volatile_parent = clone / "volatile-parent"
    volatile_parent.mkdir()
    base_file = volatile_parent / "base.txt"
    base_file.write_text("base\n")
    _git(clone, "add", "volatile-parent/base.txt")
    _git(clone, "commit", "-m", "add volatile parent baseline")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    _git(work, "rm", "volatile-parent/base.txt")
    _git(work, "commit", "-m", "remove volatile parent upstream")
    _git(work, "push")

    local_file = volatile_parent / "local.txt"
    local_file.write_text("local replay\n")
    _git(clone, "add", "volatile-parent/local.txt")
    _git(clone, "commit", "-m", "recreate volatile parent locally")

    attr_name = ("com.tm-mode.volatile-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.volatile-parent")
    requested_xattr = b"must not be lost\x00\xff"
    _write_test_xattr(volatile_parent, attr_name, requested_xattr)
    before_xattr = _read_test_xattr(volatile_parent, attr_name)
    before_parent = os.lstat(volatile_parent)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (identity["head"], index_path.read_bytes())
    mutation_calls = []
    real_run_bound_git = go._run_bound_git
    real_xattr_names = go._nofollow_xattr_names

    def volatile_parent_macl(path):
        if Path(path) == volatile_parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_nofollow_xattr_names", volatile_parent_macl)
    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "non-recoverable parent directory metadata xattrs" in result.detail
    assert mutation_calls == []
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert base_file.read_text() == "base\n"
    assert local_file.read_text() == "local replay\n"
    after_parent = os.lstat(volatile_parent)
    assert (after_parent.st_dev, after_parent.st_ino) == (
        before_parent.st_dev, before_parent.st_ino)
    assert _read_test_xattr(volatile_parent, attr_name) == before_xattr


def test_bound_metadata_rejects_casefold_file_directory_prefix_alias(
        cloned_repo):
    """A stable Foo/child cannot hide an incoming file named foo."""
    clone = cloned_repo.clone
    alias_child = clone / "Foo" / "child.txt"
    alias_child.parent.mkdir()
    alias_child.write_text("tracked child\n")
    _git(clone, "add", "Foo/child.txt")
    _git(clone, "commit", "-m", "add prefix alias fixture")
    _git(clone, "config", "core.ignorecase", "true")
    identity, _target = _bound_main_identity_and_target(clone)
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail

    try:
        issue = go._bound_worktree_metadata_issue(
            str(clone), txn,
            deadline=go.time.monotonic() + go.DEFAULT_TIMEOUT,
            mutation_paths=("foo",), stable_parent_paths=("Foo",))
        assert issue == "ambiguous filesystem path aliases"
    finally:
        assert go._remove_bound_tx_dir(txn)
        go._release_bound_lock(txn)


def test_bound_rebase_checks_metadata_on_change_then_revert_replay_path(
        cloned_repo, monkeypatch):
    """Rebase touches every replayed commit path, even with equal endpoint trees."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    replayed = clone / "replayed-metadata.txt"
    replayed.write_text("baseline\n")
    _git(clone, "add", replayed.name)
    _git(clone, "commit", "-m", "add replay metadata fixture")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-replay.txt").write_text("remote\n")
    _git(work, "add", "remote-replay.txt")
    _git(work, "commit", "-m", "remote replay advance")
    _git(work, "push")

    replayed.write_text("transient local value\n")
    _git(clone, "add", replayed.name)
    _git(clone, "commit", "-m", "change replayed metadata path")
    replayed.write_text("baseline\n")
    _git(clone, "add", replayed.name)
    _git(clone, "commit", "-m", "revert replayed metadata path")

    real_xattr_names = go._nofollow_xattr_names

    def replayed_path_xattrs(path):
        if Path(path) == replayed:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    monkeypatch.setattr(go, "_nofollow_xattr_names", replayed_path_xattrs)
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(), replayed.read_bytes())
    mutation_calls = []
    real_run_bound_git = go._run_bound_git

    def observe_mutation(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["reset", "--hard"]
                or (args and args[0] == "merge")
                or "rebase" in args):
            mutation_calls.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", observe_mutation)

    result = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert result.ok is False
    assert "non-recoverable tracked path xattrs" in result.detail
    assert mutation_calls == []
    assert _git(
        clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert replayed.read_bytes() == before[2]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin gid inheritance")
@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_reconcile_defers_parent_gid_recreation_before_mutation(
        cloned_repo, reconcile_mode):
    """A replacement inode must not inherit a different parent directory gid."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    parent = clone / "gid-parent"
    parent.mkdir()
    tracked = parent / "tracked.txt"
    tracked.write_text("baseline\n")
    tracked.chmod(0o644)
    _git(clone, "add", "gid-parent/tracked.txt")
    _git(clone, "commit", "-m", "add gid fixture")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "gid-parent" / "tracked.txt").write_text("remote replacement\n")
    _git(work, "add", "gid-parent/tracked.txt")
    _git(work, "commit", "-m", "replace gid fixture")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-gid.txt").write_text("local\n")
        _git(clone, "add", "local-gid.txt")
        _git(clone, "commit", "-m", "local gid advance")

    secondary_gid = next(
        (gid for gid in os.getgroups() if gid != os.getgid()), None)
    if secondary_gid is None:
        pytest.skip("no secondary group for gid inheritance fixture")
    try:
        os.chown(parent, -1, secondary_gid)
    except OSError as exc:
        pytest.skip(f"cannot set parent group fixture: {exc}")
    assert os.lstat(tracked).st_gid == os.getgid()
    assert os.lstat(parent).st_gid == secondary_gid
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = (
        identity["head"], index_path.read_bytes(), tracked.read_bytes(),
        os.lstat(tracked).st_gid, os.lstat(parent).st_gid)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert "parent" in res.detail and "metadata" in res.detail
    assert _git(clone, "rev-parse", "refs/heads/main").stdout.strip() == before[0]
    assert index_path.read_bytes() == before[1]
    assert tracked.read_bytes() == before[2]
    assert os.lstat(tracked).st_gid == before[3]
    assert os.lstat(parent).st_gid == before[4]


@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
@pytest.mark.parametrize(
    "candidate_parent_state", ["unsafe-existing", "missing-safe"])
def test_bound_reconcile_checks_upstream_rename_candidate_parent_metadata(
        cloned_repo, reconcile_mode, candidate_parent_state):
    """An upstream rename target parent is part of the metadata proof."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    source = clone / "rename-metadata-source.txt"
    source.write_text("rename baseline\n")
    source.chmod(0o644)
    _git(clone, "add", source.name)
    _git(clone, "commit", "-m", "add rename metadata fixture")
    _git(clone, "push")

    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "candidate-parent").mkdir()
    _git(
        work, "mv", source.name,
        "candidate-parent/rename-metadata-target.txt")
    _git(work, "commit", "-m", "rename into candidate parent")
    _git(work, "push")
    upstream_oid = _git(work, "rev-parse", "HEAD").stdout.strip()
    if reconcile_mode == "rebase":
        (clone / "rename-local.txt").write_text("local\n")
        _git(clone, "add", "rename-local.txt")
        _git(clone, "commit", "-m", "local rename advance")

    candidate_parent = clone / "candidate-parent"
    checkout_umask = go._probe_process_umask(go.DEFAULT_TIMEOUT)
    assert checkout_umask is not None
    expected_dir_mode = 0o777 & ~checkout_umask
    if candidate_parent_state == "unsafe-existing":
        candidate_parent.mkdir()
        unsafe_mode = 0o700 if expected_dir_mode != 0o700 else 0o755
        candidate_parent.chmod(unsafe_mode)
    else:
        unsafe_mode = None

    target_path = candidate_parent / "rename-metadata-target.txt"
    identity, target = _bound_main_identity_and_target(clone)
    index_path = _real_index_path(clone)
    before = {
        "head": identity["head"],
        "index": index_path.read_bytes(),
        "source": source.read_bytes(),
        "parent_mode": (
            stat.S_IMODE(os.lstat(candidate_parent).st_mode)
            if candidate_parent.exists() else None),
    }

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    if candidate_parent_state == "unsafe-existing":
        assert res.ok is False
        assert "parent" in res.detail and "metadata" in res.detail
        assert _git(
            clone, "rev-parse", "refs/heads/main").stdout.strip() == before["head"]
        assert index_path.read_bytes() == before["index"]
        assert source.read_bytes() == before["source"]
        assert candidate_parent.is_dir()
        assert stat.S_IMODE(os.lstat(candidate_parent).st_mode) == unsafe_mode
        assert not target_path.exists()
    else:
        assert res.ok is True, res.detail
        assert res.action == (
            "fast-forward" if reconcile_mode == "fast-forward" else "rebased")
        assert not source.exists()
        assert target_path.read_text() == "rename baseline\n"
        assert stat.S_IMODE(
            os.lstat(candidate_parent).st_mode) == expected_dir_mode
        assert _git(
            clone, "merge-base", "--is-ancestor", upstream_oid,
            "HEAD").returncode == 0
        assert not list(index_path.parent.glob(".tm-mode-reconcile-*"))


def test_bound_metadata_scan_rechecks_shared_deadline_per_entry(
        cloned_repo, monkeypatch):
    clone = cloned_repo.clone
    for index in range(12):
        (clone / f"deadline-{index}.txt").write_text("tracked\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "add deadline fixtures")
    identity, _target = _bound_main_identity_and_target(clone)
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail
    clock = {"now": 1000.0}

    def advancing_monotonic():
        clock["now"] += 1.0
        return clock["now"]

    monkeypatch.setattr(go.time, "monotonic", advancing_monotonic)
    try:
        issue = go._bound_worktree_metadata_issue(
            str(clone), txn, deadline=1005.0,
            mutation_paths=tuple(
                f"deadline-{index}.txt" for index in range(12)))
        assert issue is None
        assert clock["now"] >= 1005.0
    finally:
        assert go._remove_bound_tx_dir(txn)
        go._release_bound_lock(txn)


def test_bound_untracked_collision_rechecks_deadline_during_cpu_scan(
        monkeypatch):
    """Large in-memory path scans must not run past the shared hook budget."""
    outputs = iter((
        "\0".join(f"untracked-{index}" for index in range(64)) + "\0",
        "", "", ""))

    def fake_bound_git(_root, _txn, args, _timeout, **_kwargs):
        if _git_subcommand(args) == "config":
            return 1, "", ""
        return 0, next(outputs), ""

    clock_calls = {"count": 0}

    def expiring_monotonic():
        clock_calls["count"] += 1
        return 1000.0 if clock_calls["count"] <= 5 else 1003.0

    monkeypatch.setattr(go, "_run_bound_git", fake_bound_git)
    monkeypatch.setattr(go.time, "monotonic", expiring_monotonic)

    collision = go._bound_untracked_tree_collision(
        "/unused", object(), "local", "upstream", deadline=1003.0,
        mutation_paths=tuple(f"replay-{index}" for index in range(64)))

    assert collision is None
    assert clock_calls["count"] > 5


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin provenance xattr")
@pytest.mark.parametrize("reconcile_mode", ["fast-forward", "rebase"])
def test_bound_reconcile_preserves_allowlisted_provenance_xattr(
        cloned_repo, reconcile_mode):
    """The sole ubiquitous xattr allowlist entry needs a real round-trip proof."""
    clone, work = cloned_repo.clone, cloned_repo.work
    _git(clone, "pull", "--ff-only")
    tracked = clone / "provenance.bin"
    tracked.write_bytes(b"baseline\n")
    tracked.chmod(0o644)
    _git(clone, "add", tracked.name)
    _git(clone, "commit", "-m", "add provenance fixture")
    _git(clone, "push")
    _git(work, "fetch", "origin")
    _git(work, "reset", "--hard", "origin/main")
    (work / "remote-provenance.txt").write_text("remote\n")
    _git(work, "add", "remote-provenance.txt")
    _git(work, "commit", "-m", "remote provenance advance")
    _git(work, "push")
    if reconcile_mode == "rebase":
        (clone / "local-provenance.txt").write_text("local\n")
        _git(clone, "add", "local-provenance.txt")
        _git(clone, "commit", "-m", "local provenance advance")

    tracked.write_bytes(b"dirty bytes preserved with provenance\x00\xff")
    requested = b"tm-mode provenance\x00\xff"
    wrote = subprocess.run(
        ["/usr/bin/xattr", "-w", "-x", "--", "com.apple.provenance",
         requested.hex(), str(tracked)], capture_output=True, text=True,
        check=False)
    if wrote.returncode != 0:
        pytest.skip(f"provenance xattr unavailable: {wrote.stderr}")
    before_attr = subprocess.run(
        ["/usr/bin/xattr", "-p", "-x", "--", "com.apple.provenance",
         str(tracked)], capture_output=True, text=True, check=False)
    assert before_attr.returncode == 0, before_attr.stderr
    # macOS owns this attribute and may canonicalize the requested payload.
    expected = bytes.fromhex("".join(before_attr.stdout.split()))
    assert expected
    identity, target = _bound_main_identity_and_target(clone)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is True, res.detail
    read = subprocess.run(
        ["/usr/bin/xattr", "-p", "-x", "--", "com.apple.provenance",
         str(tracked)], capture_output=True, text=True, check=False)
    assert read.returncode == 0, read.stderr
    actual = bytes.fromhex("".join(read.stdout.split()))
    assert actual == expected
    assert tracked.read_bytes() == b"dirty bytes preserved with provenance\x00\xff"


def test_run_git_env_overrides_are_process_local(cloned_repo, tmp_path,
                                                  monkeypatch):
    """Private-index env applies to one child without mutating os.environ."""
    clone = cloned_repo.clone
    private_index = tmp_path / "private-index"
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    real_popen = go.subprocess.Popen
    child_index_envs = []

    def observing_popen(*args, **kwargs):
        assert "GIT_INDEX_FILE" not in os.environ
        child_index_envs.append((kwargs.get("env") or {}).get("GIT_INDEX_FILE"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(go.subprocess, "Popen", observing_popen)

    rc, out, err = go.run_git(
        ["-C", str(clone), "rev-parse", "--git-path", "index"],
        timeout=go.DEFAULT_TIMEOUT,
        env_overrides={"GIT_INDEX_FILE": str(private_index)})

    assert rc == 0, err
    assert Path(out.strip()) == private_index
    assert "GIT_INDEX_FILE" not in os.environ
    rc, normal, err = go.run_git(
        ["-C", str(clone), "rev-parse", "--git-path", "index"],
        timeout=go.DEFAULT_TIMEOUT)
    assert rc == 0, err
    assert Path(normal.strip()) != private_index
    assert child_index_envs == [str(private_index), None]


def test_repo_scoped_git_ignores_ambient_redirect_to_another_repo(
        cloned_repo, tmp_path, monkeypatch):
    """`-C requested` must win over ambient GIT_DIR/GIT_WORK_TREE redirects."""
    requested = cloned_repo.clone
    redirected = tmp_path / "redirected"
    _git(tmp_path, "init", str(redirected))
    monkeypatch.setenv("GIT_DIR", str(redirected / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(redirected))

    rc, out, err = go.run_git(
        ["-C", str(requested), "rev-parse", "--show-toplevel"],
        timeout=go.DEFAULT_TIMEOUT)

    assert rc == 0, err
    assert Path(out.strip()).resolve() == requested.resolve()


def test_repo_scoped_git_strips_redirect_env_but_preserves_config_files(
        cloned_repo, tmp_path, monkeypatch):
    """Repository/object/config injection vars are child-local denylisted."""
    redirected = tmp_path / "redirected"
    _git(tmp_path, "init", str(redirected))
    redirect_env = {
        "GIT_DIR": str(redirected / ".git"),
        "GIT_WORK_TREE": str(redirected),
        "GIT_COMMON_DIR": str(redirected / ".git"),
        "GIT_INDEX_FILE": str(redirected / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(redirected / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(redirected / "alternate"),
        "GIT_NAMESPACE": "redirected",
        "GIT_SHALLOW_FILE": str(redirected / "shallow"),
        "GIT_GRAFT_FILE": str(redirected / "grafts"),
        "GIT_REPLACE_REF_BASE": "refs/replace-hostile/",
        "GIT_ATTR_SOURCE": "refs/heads/hostile-attributes",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_PARAMETERS": "'core.bare=false'",
        "GIT_CONFIG_KEY_0": "core.bare",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_7": "core.hooksPath",
        "GIT_CONFIG_VALUE_7": str(redirected),
    }
    for name, value in redirect_env.items():
        monkeypatch.setenv(name, value)
    expected_config_files = {
        name: os.environ[name]
        for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                     "GIT_CONFIG_NOSYSTEM")
    }
    real_popen = go.subprocess.Popen
    child_envs = []

    def observing_popen(*args, **kwargs):
        child_envs.append(dict(kwargs.get("env") or {}))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(go.subprocess, "Popen", observing_popen)
    rc, out, err = go.run_git(
        ["-C", str(cloned_repo.clone), "rev-parse", "--show-toplevel"],
        timeout=go.DEFAULT_TIMEOUT)

    assert child_envs
    child_env = child_envs[-1]
    assert set(redirect_env).isdisjoint(child_env)
    assert {name: child_env.get(name) for name in expected_config_files} == (
        expected_config_files)
    assert rc == 0, err
    assert Path(out.strip()).resolve() == cloned_repo.clone.resolve()


def test_is_git_worktree_requires_exact_team_root(cloned_repo):
    nested = cloned_repo.clone / "nested"
    nested.mkdir()

    assert go.is_git_worktree(str(nested)) is False
    assert go.is_git_worktree(str(cloned_repo.clone)) is True


def test_bound_state_proof_preserves_distinct_invalid_utf8_bytes(cloned_repo):
    """Different undecodable worktree bytes must not collapse to replacement chars."""
    clone = cloned_repo.clone
    raw_path = clone / "raw.txt"
    raw_path.write_bytes(b"base\n")
    _git(clone, "add", raw_path.name)
    _git(clone, "commit", "-m", "raw fixture")
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail
    try:
        deadline = go.time.monotonic() + 20
        raw_path.write_bytes(b"\xff\n")
        first = go._capture_bound_user_state(str(clone), txn, deadline)
        raw_path.write_bytes(b"\xfe\n")
        second = go._capture_bound_user_state(str(clone), txn, deadline)

        assert first is not None and second is not None
        assert first != second
        assert "\udcff" in first.unstaged_diff
        assert "\udcfe" in second.unstaged_diff
    finally:
        go._remove_bound_tx_dir(txn)
        go._release_bound_lock(txn)


def _remove_test_tree_known_files(path):
    """Test-only exact cleanup; never recursively follows or deletes unknown trees."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_file() or path.is_symlink():
        path.unlink()
        return
    for child in tuple(path.iterdir()):
        assert child.is_file() or child.is_symlink(), child
        child.unlink()
    path.rmdir()


@pytest.mark.parametrize("replacement_kind", ["directory", "owned-file"])
def test_bound_tx_cleanup_preserves_replaced_foreign_paths(
        cloned_repo, replacement_kind):
    clone = cloned_repo.clone
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail
    displaced = txn.tx_dir.with_name(f"{txn.tx_dir.name}-displaced")
    foreign_marker = None
    try:
        if replacement_kind == "directory":
            txn.tx_dir.rename(displaced)
            txn.tx_dir.mkdir(mode=0o700)
            foreign_marker = txn.tx_dir / "foreign.txt"
            foreign_marker.write_text("foreign", encoding="utf-8")
        else:
            displaced = txn.tx_dir / "displaced-original-index"
            txn.original_index.rename(displaced)
            foreign_marker = txn.original_index
            foreign_marker.write_text("foreign", encoding="utf-8")

        assert go._remove_bound_tx_dir(txn) is False
        assert foreign_marker.exists()
        assert foreign_marker.read_text(encoding="utf-8") == "foreign"
    finally:
        go._release_bound_lock(txn)
        if replacement_kind == "directory":
            _remove_test_tree_known_files(txn.tx_dir)
            _remove_test_tree_known_files(displaced)
        else:
            _remove_test_tree_known_files(txn.tx_dir)


def test_bound_tx_begin_nonlock_fileexists_is_not_reported_as_foreign_lock(
        cloned_repo, monkeypatch):
    clone = cloned_repo.clone
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    lock_path = Path(f"{_real_index_path(clone)}.lock")
    monkeypatch.setattr(
        go.tempfile, "mkdtemp",
        lambda **_kwargs: (_ for _ in ()).throw(FileExistsError("tx collision")))

    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)

    assert txn is None
    assert "locked by another Git operation" not in detail
    assert "tx collision" in detail
    assert not lock_path.exists()


def test_bound_tx_begin_failure_preserves_replaced_empty_foreign_directory(
        cloned_repo, monkeypatch):
    clone = cloned_repo.clone
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    real_apply = go._apply_index_metadata
    captured = {}

    def replace_directory_then_fail(path, metadata):
        if not captured:
            tx_dir = Path(path).parent
            displaced = tx_dir.with_name(f"{tx_dir.name}-displaced")
            real_apply(path, metadata)
            tx_dir.rename(displaced)
            tx_dir.mkdir(mode=0o700)
            captured.update(tx_dir=tx_dir, displaced=displaced)
            raise OSError("simulated begin failure after directory replacement")
        return real_apply(path, metadata)

    monkeypatch.setattr(go, "_apply_index_metadata", replace_directory_then_fail)
    try:
        txn, detail = go._begin_bound_index_tx(
            str(clone), identity, go.DEFAULT_TIMEOUT)

        assert txn is None
        assert "simulated begin failure" in detail
        assert captured["tx_dir"].is_dir()
    finally:
        if captured:
            _remove_test_tree_known_files(captured["tx_dir"])
            _remove_test_tree_known_files(captured["displaced"])


@pytest.mark.parametrize(
    "failure_kind", ["timeout-created", "timeout-foreign", "exec-error"])
def test_recovery_ref_failure_never_escapes_and_uses_exact_oid_cleanup(
        cloned_repo, monkeypatch, failure_kind):
    clone = cloned_repo.clone
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    foreign_oid = _git(clone, "rev-parse", "HEAD^{tree}").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    target = go._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")
    real_run_bound_git = go._run_bound_git
    observed = {}

    def failing_recovery_ref(team_root, txn, args, timeout, **kwargs):
        if (args[:2] == ["update-ref", txn.head_ref]
                and not observed.get("failed")):
            observed.update(failed=True, txn=txn)
            if failure_kind == "timeout-created":
                result = real_run_bound_git(
                    team_root, txn, args, timeout, **kwargs)
                assert result[0] == 0
                raise subprocess.TimeoutExpired(args, timeout)
            if failure_kind == "timeout-foreign":
                foreign_args = ["update-ref", txn.head_ref, foreign_oid, ""]
                result = real_run_bound_git(
                    team_root, txn, foreign_args, timeout, **kwargs)
                assert result[0] == 0
                raise subprocess.TimeoutExpired(args, timeout)
            raise OSError("simulated update-ref exec error")
        return real_run_bound_git(team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(go, "_run_bound_git", failing_recovery_ref)
    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert observed
    txn = observed["txn"]
    assert res.ok is False
    assert not txn.lock_path.exists()
    actual_ref = _git(
        clone, "rev-parse", "--verify", "--quiet", txn.head_ref,
        check=False)
    if failure_kind == "timeout-foreign":
        assert actual_ref.returncode == 0
        assert actual_ref.stdout.strip() == foreign_oid
        assert txn.tx_dir.exists()
        _git(clone, "update-ref", "-d", txn.head_ref, foreign_oid)
        assert go._remove_bound_tx_dir(txn) is True
    else:
        assert actual_ref.returncode != 0
        assert not txn.tx_dir.exists()


def test_bound_fast_forward_preserves_canonical_index_mode_and_owner(cloned_repo):
    clone = cloned_repo.clone
    index_path = _real_index_path(clone)
    os.chmod(index_path, 0o660)
    before = os.stat(index_path, follow_symlinks=False)
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    target = go._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is True, res.detail
    after = os.stat(index_path, follow_symlinks=False)
    assert stat.S_IMODE(after.st_mode) == 0o660
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)


def test_bound_fast_forward_preserves_canonical_index_xattrs(cloned_repo):
    clone = cloned_repo.clone
    index_path = _real_index_path(clone)
    attr_name = ("com.tm-mode.transaction-test"
                 if sys.platform == "darwin" else "user.tm-mode.transaction-test")
    expected = b"preserve-me\x00\xff"
    native = all(
        hasattr(os, name) for name in ("listxattr", "getxattr", "setxattr"))
    xattr_cli = (
        "/usr/bin/xattr"
        if sys.platform == "darwin" and Path("/usr/bin/xattr").is_file()
        else "")
    if native:
        try:
            os.setxattr(index_path, attr_name, expected, follow_symlinks=False)
        except OSError as exc:
            pytest.skip(f"filesystem xattrs unavailable: {exc}")
    elif xattr_cli:
        wrote = subprocess.run(
            [xattr_cli, "-w", "-x", attr_name, expected.hex(), str(index_path)],
            capture_output=True, check=False)
        assert wrote.returncode == 0, wrote.stderr
    else:
        pytest.skip("no real xattr writer available on this platform")
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    target = go._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is True, res.detail
    if native:
        actual = os.getxattr(
            index_path, attr_name, follow_symlinks=False)
    else:
        read = subprocess.run(
            [xattr_cli, "-p", "-x", attr_name, str(index_path)],
            capture_output=True, check=False)
        assert read.returncode == 0, read.stderr
        actual = bytes.fromhex(b"".join(read.stdout.split()).decode("ascii"))
    assert actual == expected


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin raw xattr names")
def test_bound_fast_forward_preserves_newline_xattr_name(cloned_repo):
    """Darwin listxattr must keep a valid newline-containing raw name exactly."""
    clone = cloned_repo.clone
    index_path = _real_index_path(clone)
    xattr_cli = "/usr/bin/xattr"
    attr_name = "\n"
    expected = b"newline-name-value\x00\xff"
    wrote = subprocess.run(
        [xattr_cli, "-w", "-x", "-s", "--", attr_name, expected.hex(),
         str(index_path)], capture_output=True, check=False)
    assert wrote.returncode == 0, wrote.stderr
    identity, target = _bound_main_identity_and_target(clone)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is True, res.detail
    read = subprocess.run(
        [xattr_cli, "-p", "-x", "-s", "--", attr_name, str(index_path)],
        capture_output=True, check=False)
    assert read.returncode == 0, read.stderr
    actual = bytes.fromhex(b"".join(read.stdout.split()).decode("ascii"))
    assert actual == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX fail-closed contract")
def test_bound_index_transaction_fails_without_safe_xattr_backend(
        cloned_repo, monkeypatch):
    """POSIX promotion cannot silently drop metadata it cannot inspect."""
    for name in ("listxattr", "getxattr", "setxattr", "removexattr"):
        monkeypatch.delattr(go.os, name, raising=False)
    monkeypatch.setattr(go, "_darwin_xattr_libc", lambda: None, raising=False)
    clone = cloned_repo.clone
    identity, _target = _bound_main_identity_and_target(clone)

    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)

    if txn is not None:
        go._remove_bound_tx_dir(txn)
        go._release_bound_lock(txn)
    assert txn is None
    assert "xattr" in detail.lower()


def test_index_metadata_snapshot_applies_available_xattrs(tmp_path, monkeypatch):
    source = tmp_path / "source-index"
    destination = tmp_path / "destination-index"
    source.write_bytes(b"source")
    destination.write_bytes(b"destination")
    attrs = {
        os.fspath(source): {"user.tm-mode": b"preserve"},
        os.fspath(destination): {"user.foreign": b"remove"},
    }

    def listxattr(path, *, follow_symlinks=False):
        assert follow_symlinks is False
        return list(attrs.setdefault(os.fspath(path), {}))

    def getxattr(path, name, *, follow_symlinks=False):
        assert follow_symlinks is False
        return attrs[os.fspath(path)][name]

    def setxattr(path, name, value, *, follow_symlinks=False):
        assert follow_symlinks is False
        attrs.setdefault(os.fspath(path), {})[name] = value

    def removexattr(path, name, *, follow_symlinks=False):
        assert follow_symlinks is False
        del attrs[os.fspath(path)][name]

    monkeypatch.setattr(go.os, "listxattr", listxattr, raising=False)
    monkeypatch.setattr(go.os, "getxattr", getxattr, raising=False)
    monkeypatch.setattr(go.os, "setxattr", setxattr, raising=False)
    monkeypatch.setattr(go.os, "removexattr", removexattr, raising=False)

    metadata = go._capture_index_metadata(source)
    go._apply_index_metadata(destination, metadata)

    assert attrs[os.fspath(destination)] == {"user.tm-mode": b"preserve"}


def test_darwin_libc_xattrs_retry_erange_and_preserve_raw_names(
        tmp_path, monkeypatch):
    path = tmp_path / "index"
    path.write_bytes(b"index")
    raw_names = b"\n\0a\nb\0"
    values = {b"\n": b"\x00\xff", b"a\nb": b"value"}

    class FakeLibc:
        def __init__(self):
            self.erange_once = True
            self.options = []
            self.set_calls = []
            self.remove_calls = []

        def listxattr(self, _path, buffer, size, options):
            self.options.append(options)
            if buffer is None:
                return len(raw_names)
            if self.erange_once:
                self.erange_once = False
                ctypes.set_errno(go.errno.ERANGE)
                return -1
            ctypes.memmove(buffer, raw_names, min(size, len(raw_names)))
            return len(raw_names)

        def getxattr(self, _path, name, buffer, size, _position, options):
            self.options.append(options)
            value = values[name]
            if buffer is None:
                return len(value)
            ctypes.memmove(buffer, value, min(size, len(value)))
            return len(value)

        def setxattr(self, _path, name, _buffer, size, _position, options):
            self.options.append(options)
            self.set_calls.append((name, size))
            return 0

        def removexattr(self, _path, name, options):
            self.options.append(options)
            self.remove_calls.append(name)
            return 0

    libc = FakeLibc()
    monkeypatch.setattr(go, "_darwin_xattr_libc", lambda: libc)

    attrs = go._capture_xattrs_with_backend(path, go._DARWIN_XATTR_BACKEND)
    go._darwin_set_xattr(path, b"\n", b"\x00\xff", libc)
    go._darwin_remove_xattr(path, b"a\nb", libc)

    assert attrs == ((b"\n", b"\x00\xff"), (b"a\nb", b"value"))
    assert libc.erange_once is False
    assert libc.options and set(libc.options) == {go._DARWIN_XATTR_NOFOLLOW}
    assert libc.set_calls == [(b"\n", 2)]
    assert libc.remove_calls == [b"a\nb"]


def test_index_promotion_commit_point_surfaces_durability_without_rollback(
        cloned_repo, monkeypatch):
    """A post-replace failure is strong failure, but never pre-commit rollback."""
    clone = cloned_repo.clone
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail
    real_matches = go._index_metadata_matches
    canonical_checks = []

    def fail_only_post_promotion(path, metadata):
        if Path(path) == txn.index_path:
            canonical_checks.append(True)
            if len(canonical_checks) == 2:
                return False
        return real_matches(path, metadata)

    monkeypatch.setattr(
        go, "_index_metadata_matches", fail_only_post_promotion)
    try:
        promoted, detail = go._promote_bound_index(txn)

        assert txn.promoted is True
        assert promoted is False
        assert "durability" in detail and "post-promotion" in detail
    finally:
        go._remove_bound_tx_dir(txn)
        go._release_bound_lock(txn)


def _bound_recovery_evidence(repo):
    refs = _git(
        repo, "for-each-ref", "--format=%(refname)%00%(objectname)",
        "refs/tm-mode/reconcile").stdout.splitlines()
    admin = _real_index_path(repo).parent
    tx_dirs = sorted(admin.glob(".tm-mode-reconcile-*"))
    return refs, tx_dirs


def _make_foreign_ref_race_commit(repo):
    """Create an unrelated commit that a raw ref writer can move main onto."""
    _git(repo, "checkout", "-b", "foreign-race", "main")
    (repo / "foreign.txt").write_text("foreign\n")
    _git(repo, "add", "foreign.txt")
    _git(repo, "commit", "-m", "foreign race")
    foreign_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    return foreign_oid


def test_bound_post_promotion_ref_race_fails_closed_and_retains_recovery(
        cloned_repo, monkeypatch):
    """A raw ref writer after index promotion must never become the result OID."""
    clone = cloned_repo.clone
    foreign_oid = _make_foreign_ref_race_commit(clone)
    before = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": before}
    target = go._PublicationTarget(
        remote="origin", destination="refs/heads/main",
        reconcile_ref="refs/remotes/origin/main")
    real_promote = go._promote_bound_index

    def race_after_promotion(txn):
        result = real_promote(txn)
        assert result[0], result
        raced = _git(clone, "reset", "--soft", foreign_oid, check=False)
        assert raced.returncode == 0, raced.stderr
        return result

    monkeypatch.setattr(go, "_promote_bound_index", race_after_promotion)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert res.final_identity is None
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == foreign_oid
    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert refs or tx_dirs
    assert go.publication_blocker_detail(str(clone))


def test_auto_commit_never_publishes_or_binds_pending_to_post_promotion_race(
        cloned_repo, monkeypatch):
    """The captured auto-commit stays pending when a raw writer moves its branch."""
    clone = cloned_repo.clone
    foreign_oid = _make_foreign_ref_race_commit(clone)
    (clone / "session.txt").write_text("session\n")
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    real_promote = go._promote_bound_index
    real_run_git = go.run_git
    commit_oids = []
    pushes = []

    def race_after_promotion(txn):
        result = real_promote(txn)
        assert result[0], result
        raced = _git(clone, "reset", "--soft", foreign_oid, check=False)
        assert raced.returncode == 0, raced.stderr
        return result

    def capture_commit_and_push(args, timeout, **kwargs):
        subcommand = _git_subcommand(args)
        if subcommand == "push":
            pushes.append(list(args))
        result = real_run_git(args, timeout, **kwargs)
        if subcommand == "commit" and result[0] == 0:
            commit_oids.append(
                _git(clone, "rev-parse", "refs/heads/main").stdout.strip())
        return result

    monkeypatch.setattr(go, "_promote_bound_index", race_after_promotion)
    monkeypatch.setattr(go, "run_git", capture_commit_and_push)

    res = go.do_commit(
        str(clone), "session", push=True, paths=["session.txt"],
        reconcile_before_push=True, _allow_bound_mutation=True)

    assert res.committed is True and res.pushed is False
    assert len(commit_oids) == 1 and commit_oids[0] != foreign_oid
    assert res.pending_identity == {
        "key": "branch:main", "branch": "main", "head": commit_oids[0],
    }
    assert pushes == []
    assert _git(
        cloned_repo.upstream, "rev-parse",
        "refs/heads/main").stdout.strip() == remote_before
    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert any(commit_oids[0] in record for record in refs) or tx_dirs
    assert go.publication_blocker_detail(str(clone))


def test_bound_reconcile_preserves_untracked_replacement_of_staged_delete(
        cloned_repo):
    """A reset must never erase untracked bytes hidden below a staged delete."""
    clone = cloned_repo.clone
    _git(clone, "rm", "a.txt")
    replacement = clone / "a.txt"
    replacement.mkdir()
    secret = replacement / "secret.bin"
    secret.write_bytes(b"must survive\x00\xff")
    identity, target = _bound_main_identity_and_target(clone)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert res.ok is False
    assert secret.read_bytes() == b"must survive\x00\xff"
    assert "untracked" in res.detail.lower()
    assert _git(clone, "diff", "--cached", "--name-status").stdout == "D\ta.txt\n"


def test_bound_reconcile_defers_if_worktree_changes_after_recovery_snapshot(
        cloned_repo, monkeypatch):
    """A late Claude/Codex write must not be replaced by the earlier stash."""
    clone = cloned_repo.clone
    edited = clone / "a.txt"
    edited.write_text("early edit\n")
    identity, target = _bound_main_identity_and_target(clone)
    real_create_ref = go._create_bound_recovery_ref
    injected = {"done": False}

    def write_after_snapshot(team_root, txn, ref, expected_oid, deadline, reserve):
        result = real_create_ref(
            team_root, txn, ref, expected_oid, deadline, reserve)
        if ref == txn.head_ref and result[0] and not injected["done"]:
            edited.write_text("late edit\n")
            injected["done"] = True
        return result

    monkeypatch.setattr(go, "_create_bound_recovery_ref", write_after_snapshot)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    assert injected["done"] is True
    assert res.ok is False
    assert "user state changed before mutation" in res.detail.lower()
    assert edited.read_text() == "late edit\n"
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == identity["head"]


@pytest.mark.parametrize("failure_kind", ["metadata", "parent-fsync"])
def test_bound_post_promotion_durability_failure_retains_evidence_and_blocks_push(
        cloned_repo, monkeypatch, failure_kind):
    clone = cloned_repo.clone
    edited = clone / "session.txt"
    edited.write_text("session\n")
    index_path = _real_index_path(clone)
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    real_replace = go.os.replace
    real_matches = go._index_metadata_matches
    real_fsync = go._fsync_parent_dir
    promoted = {"seen": False, "failed": False}
    pushes = []
    real_run_git = go.run_git

    def observing_replace(source, destination):
        result = real_replace(source, destination)
        if Path(destination) == index_path:
            promoted["seen"] = True
        return result

    def metadata_check(path, metadata):
        if (failure_kind == "metadata" and promoted["seen"]
                and Path(path) == index_path):
            return False
        return real_matches(path, metadata)

    def parent_fsync(path):
        if (failure_kind == "parent-fsync" and promoted["seen"]
                and not promoted["failed"] and Path(path) == index_path):
            promoted["failed"] = True
            return False
        return real_fsync(path)

    def record_push(args, timeout, **kwargs):
        if "push" in args:
            pushes.append(list(args))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go.os, "replace", observing_replace)
    monkeypatch.setattr(go, "_index_metadata_matches", metadata_check)
    monkeypatch.setattr(go, "_fsync_parent_dir", parent_fsync)
    monkeypatch.setattr(go, "run_git", record_push)

    res = go.do_commit(
        str(clone), "session", push=True, paths=[edited.name],
        reconcile_before_push=True, _allow_bound_mutation=True)

    actual_head = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert promoted["seen"]
    assert res.committed is True and res.pushed is False
    assert "durability" in res.detail or "metadata" in res.detail
    assert pushes == []
    assert res.pending_identity == {
        "key": "branch:main", "branch": "main", "head": actual_head,
    }
    assert refs and tx_dirs
    assert not Path(f"{index_path}.lock").exists()
    assert _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip() == (
            remote_before)


@pytest.mark.parametrize("failure_kind", ["refs", "txdir"])
def test_bound_cleanup_failure_retains_evidence_and_blocks_push(
        cloned_repo, monkeypatch, failure_kind):
    clone = cloned_repo.clone
    edited = clone / "session.txt"
    edited.write_text("session\n")
    pushes = []
    real_run_git = go.run_git
    if failure_kind == "refs":
        monkeypatch.setattr(go, "_cleanup_bound_refs", lambda *_a, **_k: False)
    else:
        monkeypatch.setattr(go, "_remove_bound_tx_dir", lambda _txn: False)

    def record_push(args, timeout, **kwargs):
        if "push" in args:
            pushes.append(list(args))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "run_git", record_push)
    res = go.do_commit(
        str(clone), "session", push=True, paths=[edited.name],
        reconcile_before_push=True, _allow_bound_mutation=True)

    actual_head = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert res.committed is True and res.pushed is False
    assert "cleanup" in res.detail
    assert pushes == []
    assert res.pending_identity == {
        "key": "branch:main", "branch": "main", "head": actual_head,
    }
    assert refs and tx_dirs


def test_bound_cleanup_post_rmdir_fsync_failure_keeps_observable_anchor(
        cloned_repo, monkeypatch):
    """A real txdir removal side effect cannot erase the last recovery proof."""
    clone = cloned_repo.clone
    real_begin = go._begin_bound_index_tx
    real_fsync = go._fsync_parent_dir
    captured = {}
    failed = {"post_rmdir": False}

    def capture_begin(*args, **kwargs):
        txn, detail = real_begin(*args, **kwargs)
        captured["txn"] = txn
        return txn, detail

    def fail_after_physical_rmdir(path):
        txn = captured.get("txn")
        if (txn is not None and Path(path) == txn.tx_dir
                and not txn.tx_dir.exists() and not failed["post_rmdir"]):
            failed["post_rmdir"] = True
            return False
        return real_fsync(path)

    # This recreates the former worst case: after refs were deleted and the
    # txdir was physically removed, a failed restore left no anchor at all.
    monkeypatch.setattr(go, "_begin_bound_index_tx", capture_begin)
    monkeypatch.setattr(go, "_fsync_parent_dir", fail_after_physical_rmdir)
    monkeypatch.setattr(go, "_restore_bound_refs", lambda *_a, **_k: False)
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()

    res = go.do_reconcile(
        str(clone), expected_identity=_bound_main_identity_and_target(clone)[0],
        _target=_bound_main_identity_and_target(clone)[1],
        _allow_bound_mutation=True)

    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert failed["post_rmdir"] is True
    assert res.ok is False and "cleanup" in res.detail
    assert refs or tx_dirs
    assert "recovery evidence retained" in res.detail
    assert go.publication_blocker_detail(str(clone))
    assert _git(
        cloned_repo.upstream, "rev-parse",
        "refs/heads/main").stdout.strip() == remote_before


@pytest.mark.parametrize("failure_kind", ["unlink", "parent-fsync"])
def test_bound_lock_release_failure_retains_evidence_and_blocks_push(
        cloned_repo, monkeypatch, failure_kind):
    clone = cloned_repo.clone
    edited = clone / "session.txt"
    edited.write_text("session\n")
    index_path = _real_index_path(clone)
    lock_path = Path(f"{index_path}.lock")
    pushes = []
    real_run_git = go.run_git
    real_unlink = go.os.unlink
    real_fsync = go._fsync_parent_dir
    lock_fsync_calls = {"count": 0}

    def failing_unlink(path, *args, **kwargs):
        if failure_kind == "unlink" and Path(path) == lock_path:
            raise OSError("simulated lock unlink failure")
        return real_unlink(path, *args, **kwargs)

    def failing_fsync(path):
        if Path(path) == lock_path:
            lock_fsync_calls["count"] += 1
            if failure_kind == "parent-fsync" and lock_fsync_calls["count"] >= 2:
                return False
        return real_fsync(path)

    def record_push(args, timeout, **kwargs):
        if "push" in args:
            pushes.append(list(args))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go.os, "unlink", failing_unlink)
    monkeypatch.setattr(go, "_fsync_parent_dir", failing_fsync)
    monkeypatch.setattr(go, "run_git", record_push)
    res = go.do_commit(
        str(clone), "session", push=True, paths=[edited.name],
        reconcile_before_push=True, _allow_bound_mutation=True)

    actual_head = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert res.committed is True and res.pushed is False
    assert "lock release" in res.detail
    assert pushes == []
    assert res.pending_identity == {
        "key": "branch:main", "branch": "main", "head": actual_head,
    }
    assert refs and tx_dirs
    assert lock_path.exists() is (failure_kind == "unlink")


def test_bound_pre_mutation_release_failure_keeps_transaction_evidence(
        cloned_repo, monkeypatch):
    """A fail-closed preparation path must release before deleting evidence."""
    clone = cloned_repo.clone
    _git(clone, "update-index", "--assume-unchanged", "a.txt")
    (clone / "a.txt").write_bytes(b"hidden local bytes\x00\xff")
    identity, target = _bound_main_identity_and_target(clone)
    real_begin = go._begin_bound_index_tx
    real_release = go._release_bound_lock
    captured = {}

    def capture_begin(*args, **kwargs):
        txn, detail = real_begin(*args, **kwargs)
        captured["txn"] = txn
        return txn, detail

    def fail_after_physical_release(txn):
        ok, detail = real_release(txn)
        assert ok, detail
        return False, "simulated lock release durability failure"

    monkeypatch.setattr(go, "_begin_bound_index_tx", capture_begin)
    monkeypatch.setattr(go, "_release_bound_lock", fail_after_physical_release)

    res = go.do_reconcile(
        str(clone), expected_identity=identity, _target=target,
        _allow_bound_mutation=True)

    txn = captured["txn"]
    assert res.ok is False
    assert "lock release" in res.detail
    assert txn.tx_dir.exists()


def test_bound_rollback_proven_release_failure_keeps_recovery_anchors(
        cloned_repo, monkeypatch):
    """Even proven rollback cannot erase anchors before lock release succeeds."""
    _prepare_dirty_rebase(cloned_repo, conflict=True)
    clone = cloned_repo.clone
    (clone / "a.txt").write_text("local conflict\n")
    real_begin = go._begin_bound_index_tx
    real_release = go._release_bound_lock
    captured = {}

    def capture_begin(*args, **kwargs):
        txn, detail = real_begin(*args, **kwargs)
        captured["txn"] = txn
        return txn, detail

    def fail_after_physical_release(txn):
        ok, detail = real_release(txn)
        assert ok, detail
        return False, "simulated lock release durability failure"

    monkeypatch.setattr(go, "_begin_bound_index_tx", capture_begin)
    monkeypatch.setattr(go, "_release_bound_lock", fail_after_physical_release)

    res = go.do_commit(
        str(clone), "local conflict", push=True, paths=["a.txt"],
        reconcile_before_push=True, _allow_bound_mutation=True)

    txn = captured["txn"]
    refs, tx_dirs = _bound_recovery_evidence(clone)
    assert res.committed is True and res.pushed is False
    assert "lock release" in res.detail
    assert txn.tx_dir in tx_dirs
    assert any(txn.head_ref in record for record in refs)


def test_bound_unproven_rollback_propagates_observed_identity_to_commit(
        cloned_repo, monkeypatch):
    """Pending must bind to the rebased OID left by a failed rollback."""
    clone = cloned_repo.clone
    (clone / "session.txt").write_text("local session\n")
    before = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    real_capture = go._capture_bound_user_state
    real_bound_git = go._run_bound_git
    real_reconcile = go.do_reconcile
    captures = []
    phase = {"rebased": False}
    observed = {}

    def fail_success_postcondition(*args, **kwargs):
        state = real_capture(*args, **kwargs)
        captures.append(state)
        # original snapshot + final pre-mutation proof precede the success proof.
        if len(captures) == 3:
            return None
        return state

    def fail_rollback_reset(team_root, txn, args, timeout, **kwargs):
        if ("rebase" in args and "--abort" not in args
                and not phase["rebased"]):
            result = real_bound_git(
                team_root, txn, args, timeout, **kwargs)
            assert result[0] == 0, result
            phase["rebased"] = True
            return result
        if phase["rebased"] and "reset" in args and "--hard" in args:
            return 1, "", "simulated rollback reset failure"
        return real_bound_git(team_root, txn, args, timeout, **kwargs)

    def capture_reconcile(*args, **kwargs):
        result = real_reconcile(*args, **kwargs)
        observed["result"] = result
        return result

    monkeypatch.setattr(
        go, "_capture_bound_user_state", fail_success_postcondition)
    monkeypatch.setattr(go, "_run_bound_git", fail_rollback_reset)
    monkeypatch.setattr(go, "do_reconcile", capture_reconcile)

    res = go.do_commit(
        str(clone), "local session", push=True, paths=["session.txt"],
        reconcile_before_push=True, _allow_bound_mutation=True)

    actual = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
    sync = observed["result"]
    assert phase["rebased"] and actual != before
    assert sync.ok is False and "rollback not proven" in sync.detail
    assert sync.final_identity == {
        "key": "branch:main", "branch": "main", "head": actual,
    }
    assert res.committed is True and res.pushed is False
    assert res.pending_identity == sync.final_identity


@pytest.mark.parametrize("blocker_kind", ["index-lock", "reconcile-ref", "txdir"])
def test_foreground_explicit_push_refuses_reconcile_residue_after_sync(
        cloned_repo, monkeypatch, blocker_kind):
    """Foreground publication must re-probe immediately before network push."""
    clone = cloned_repo.clone
    _git(clone, "pull", "--ff-only")
    edited = clone / "session.txt"
    edited.write_text(f"session {blocker_kind}\n")
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    index_path = _real_index_path(clone)
    admin = index_path.parent
    lock_path = Path(f"{index_path}.lock")
    tx_dir = admin / ".tm-mode-reconcile-review-stale"
    recovery_ref = "refs/tm-mode/reconcile/review-stale/head"
    real_reconcile = go.do_reconcile
    real_run_git = go.run_git
    pushes = []

    def install_blocker():
        if blocker_kind == "index-lock":
            lock_path.write_text("stale reconcile\n", encoding="utf-8")
        elif blocker_kind == "reconcile-ref":
            _git(clone, "update-ref", recovery_ref, "HEAD")
        else:
            tx_dir.mkdir()

    def reconcile_then_block(*args, **kwargs):
        result = real_reconcile(*args, **kwargs)
        assert result.ok, result.detail
        install_blocker()
        return result

    def record_push(args, timeout, **kwargs):
        if _git_subcommand(args) == "push":
            pushes.append(list(args))
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(go, "do_reconcile", reconcile_then_block)
    monkeypatch.setattr(go, "run_git", record_push)
    try:
        res = go.do_commit(
            str(clone), "foreground blocker", push=True,
            paths=[edited.name], reconcile_before_push=True)

        local_head = _git(clone, "rev-parse", "refs/heads/main").stdout.strip()
        assert res.committed is True and res.pushed is False
        assert "reconcile blocker" in res.detail
        assert pushes == []
        assert res.pending_identity == {
            "key": "branch:main", "branch": "main", "head": local_head,
        }
        assert _git(
            cloned_repo.upstream, "rev-parse",
            "refs/heads/main").stdout.strip() == remote_before
    finally:
        lock_path.unlink(missing_ok=True)
        if tx_dir.exists():
            tx_dir.rmdir()
        _git(clone, "update-ref", "-d", recovery_ref, check=False)


def test_unbound_session_reconcile_refuses_existing_transaction_residue(
        cloned_repo):
    """SessionStart's legacy FF path shares the same mutation interlock."""
    clone = cloned_repo.clone
    head_before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    index_before = _real_index_path(clone).read_bytes()
    tx_dir = (_real_index_path(clone).parent
              / ".tm-mode-reconcile-session-stale")
    tx_dir.mkdir()

    res = go.do_reconcile(str(clone), _allow_bound_mutation=True)

    assert res.ok is False
    assert "transaction" in res.detail or "blocker" in res.detail
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == head_before
    assert _real_index_path(clone).read_bytes() == index_before
    assert tx_dir.is_dir()


@pytest.mark.parametrize("blocker_kind", ["reconcile-ref", "txdir"])
def test_legacy_foreground_push_refuses_reconcile_residue(
        cloned_repo, blocker_kind):
    """Default do_commit callers stop before commit on unresolved recovery."""
    clone = cloned_repo.clone
    _git(clone, "pull", "--ff-only")
    (clone / "legacy-session.txt").write_text("legacy local\n")
    admin = _real_index_path(clone).parent
    tx_dir = admin / ".tm-mode-reconcile-legacy-stale"
    recovery_ref = "refs/tm-mode/reconcile/legacy-stale/head"
    if blocker_kind == "reconcile-ref":
        _git(clone, "update-ref", recovery_ref, "HEAD")
    else:
        tx_dir.mkdir()
    remote_before = _git(
        cloned_repo.upstream, "rev-parse", "refs/heads/main").stdout.strip()
    local_before = _git(
        clone, "rev-parse", "refs/heads/main").stdout.strip()
    index_before = _real_index_path(clone).read_bytes()

    res = go.do_commit(
        str(clone), "legacy blocker", push=True,
        paths=["legacy-session.txt"])

    assert res.ok is False
    assert res.committed is False and res.pushed is False
    assert "blocker" in res.detail
    assert res.pending_identity is None
    assert _git(
        clone, "rev-parse", "refs/heads/main").stdout.strip() == local_before
    assert _real_index_path(clone).read_bytes() == index_before
    assert _git(
        cloned_repo.upstream, "rev-parse",
        "refs/heads/main").stdout.strip() == remote_before


@pytest.mark.parametrize("blocker_kind", ["reconcile-ref", "txdir"])
@pytest.mark.parametrize(
    "push,reconcile_before_push",
    [(False, False), (True, False), (True, True)],
    ids=["local-only", "legacy-publish", "bound-publish"])
def test_do_commit_refuses_reconcile_residue_before_staging(
        cloned_repo, blocker_kind, push, reconcile_before_push):
    """Every local commit phase must stop before touching an uncertain repo."""
    clone = cloned_repo.clone
    edited = clone / "precommit-blocked.txt"
    edited.write_bytes(b"must remain unstaged\x00\xff")
    admin = _real_index_path(clone).parent
    tx_dir = admin / ".tm-mode-reconcile-precommit-stale"
    recovery_ref = "refs/tm-mode/reconcile/precommit-stale/head"
    if blocker_kind == "reconcile-ref":
        _git(clone, "update-ref", recovery_ref, "HEAD")
    else:
        tx_dir.mkdir()
    index_path = _real_index_path(clone)
    before = {
        "head": _git(clone, "rev-parse", "HEAD").stdout.strip(),
        "count": _git(clone, "rev-list", "--count", "HEAD").stdout.strip(),
        "index": index_path.read_bytes(),
        "status": _git(clone, "status", "--porcelain=v1", "-z").stdout,
        "raw": edited.read_bytes(),
        "remote": _git(
            cloned_repo.upstream, "rev-parse",
            "refs/heads/main").stdout.strip(),
    }

    res = go.do_commit(
        str(clone), "must not commit", push=push,
        paths=[edited.name], reconcile_before_push=reconcile_before_push)

    assert res.ok is False
    assert res.committed is False and res.pushed is False
    assert "blocker" in res.detail
    assert res.pending_identity is None
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before["head"]
    assert _git(
        clone, "rev-list", "--count", "HEAD").stdout.strip() == before["count"]
    assert index_path.read_bytes() == before["index"]
    assert _git(
        clone, "status", "--porcelain=v1", "-z").stdout == before["status"]
    assert edited.read_bytes() == before["raw"]
    assert _git(
        cloned_repo.upstream, "rev-parse",
        "refs/heads/main").stdout.strip() == before["remote"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock contention probe")
def test_do_commit_waits_for_common_interlock_before_staging(cloned_repo):
    """A cross-process reconcile lease excludes add/commit, not only push."""
    clone = cloned_repo.clone
    edited = clone / "precommit-contended.txt"
    edited.write_text("must remain unstaged\n")
    index_path = _real_index_path(clone)
    common_raw = Path(
        _git(clone, "rev-parse", "--git-common-dir").stdout.strip())
    common = common_raw if common_raw.is_absolute() else clone / common_raw
    lock_path = common / ".tm-mode-publication.lock"
    holder_code = (
        "import fcntl,sys\n"
        "f=open(sys.argv[1], 'a+b')\n"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "print('ready', flush=True)\n"
        "sys.stdin.read(1)\n")
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    before = {
        "head": _git(clone, "rev-parse", "HEAD").stdout.strip(),
        "count": _git(clone, "rev-list", "--count", "HEAD").stdout.strip(),
        "index": index_path.read_bytes(),
        "status": _git(clone, "status", "--porcelain=v1", "-z").stdout,
        "raw": edited.read_bytes(),
        "remote": _git(
            cloned_repo.upstream, "rev-parse",
            "refs/heads/main").stdout.strip(),
    }
    try:
        res = go.do_commit(
            str(clone), "must wait", push=True, paths=[edited.name],
            reconcile_before_push=True)
    finally:
        assert holder.stdin is not None
        holder.stdin.write("x")
        holder.stdin.flush()
        holder.wait(timeout=5)

    assert res.ok is False
    assert res.committed is False and res.pushed is False
    assert "publication interlock contention" in res.detail
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before["head"]
    assert _git(
        clone, "rev-list", "--count", "HEAD").stdout.strip() == before["count"]
    assert index_path.read_bytes() == before["index"]
    assert _git(
        clone, "status", "--porcelain=v1", "-z").stdout == before["status"]
    assert edited.read_bytes() == before["raw"]
    assert _git(
        cloned_repo.upstream, "rev-parse",
        "refs/heads/main").stdout.strip() == before["remote"]
    assert _publication_lock_probe(clone) == "acquired"


def test_bound_index_private_copy_uses_one_canonical_snapshot(
        cloned_repo, monkeypatch):
    clone = cloned_repo.clone
    index_path = _real_index_path(clone)
    original_bytes = index_path.read_bytes()
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    identity = {"key": "branch:main", "branch": "main", "head": head}
    real_copy = go._secure_copy_regular
    copy_sources = []

    def race_canonical_after_first_copy(source, destination):
        real_copy(source, destination)
        copy_sources.append((Path(source), Path(destination)))
        if len(copy_sources) == 1:
            index_path.write_bytes(b"foreign raw index replacement")

    monkeypatch.setattr(
        go, "_secure_copy_regular", race_canonical_after_first_copy)
    txn = None
    try:
        txn, detail = go._begin_bound_index_tx(
            str(clone), identity, go.DEFAULT_TIMEOUT)
        assert txn is not None, detail
        assert copy_sources == [
            (index_path, txn.original_index),
            (txn.original_index, txn.work_index),
        ]
        assert txn.original_index.read_bytes() == original_bytes
        assert txn.work_index.read_bytes() == original_bytes
    finally:
        index_path.write_bytes(original_bytes)
        if txn is not None:
            go._remove_bound_tx_dir(txn)
            go._release_bound_lock(txn)


def test_bound_transaction_uses_one_token_for_lock_dir_and_recovery_refs(
        cloned_repo):
    clone = cloned_repo.clone
    identity, _target = _bound_main_identity_and_target(clone)
    txn = None
    try:
        txn, detail = go._begin_bound_index_tx(
            str(clone), identity, go.DEFAULT_TIMEOUT)
        assert txn is not None, detail
        assert txn.token
        assert txn.lock_path.read_text(encoding="ascii") == (
            f"tm-mode bound reconcile {txn.token}\n")
        assert txn.tx_dir.name.startswith(
            f".tm-mode-reconcile-{txn.token}-")
        assert txn.head_ref == f"refs/tm-mode/reconcile/{txn.token}/head"
        assert txn.stash_ref == f"refs/tm-mode/reconcile/{txn.token}/stash"
    finally:
        if txn is not None:
            go._remove_bound_tx_dir(txn)
            go._release_bound_lock(txn)


@pytest.mark.parametrize("durability_target", ["txdir", "admin"])
def test_bound_transaction_fails_closed_when_recovery_mapping_not_durable(
        cloned_repo, monkeypatch, durability_target):
    clone = cloned_repo.clone
    identity, _target = _bound_main_identity_and_target(clone)
    real_fsync = go._fsync_parent_dir
    failed = {"done": False}

    def fail_mapping_fsync(path):
        candidate = Path(path)
        target = (
            candidate.name == "work-index" if durability_target == "txdir"
            else candidate.name.startswith(".tm-mode-reconcile-"))
        if target and not failed["done"]:
            failed["done"] = True
            return False
        return real_fsync(path)

    monkeypatch.setattr(go, "_fsync_parent_dir", fail_mapping_fsync)

    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)

    assert failed["done"]
    assert txn is None
    assert "durability" in detail
    index_path = _real_index_path(clone)
    assert not Path(f"{index_path}.lock").exists()
    assert not list(index_path.parent.glob(".tm-mode-reconcile-*"))


@pytest.mark.parametrize("with_dirty_state", [False, True])
def test_bound_post_mutation_steps_keep_full_recovery_reserve(
        cloned_repo, monkeypatch, with_dirty_state):
    """With only 24s left, success proof/apply must rollback before consuming reserve."""
    _prepare_dirty_rebase(cloned_repo)
    clone = cloned_repo.clone
    if with_dirty_state:
        _make_staged_and_unstaged_changes(clone)
    edited = clone / "session.txt"
    edited.write_text("session\n")
    clock = {"now": 0.0}
    real_run_bound_git = go._run_bound_git
    after_rebase_commands = []
    phase = {"after_rebase": False}

    def timed_bound_git(team_root, txn, args, timeout, **kwargs):
        command = _git_subcommand(args)
        if phase["after_rebase"] and command in {"stash", "reset"}:
            after_rebase_commands.append(command)
        result = real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)
        if command == "rebase" and "--abort" not in args:
            assert result[0] == 0, result
            # do_commit gives reconcile an absolute deadline of 40.  Leave 24s:
            # less than the full 25s rollback reserve, more than the old 19s.
            clock["now"] = 16.0
            phase["after_rebase"] = True
        return result

    monkeypatch.setattr(go.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(go, "_run_bound_git", timed_bound_git)
    res = go.do_commit(
        str(clone), "session under deadline", push=True,
        paths=[edited.name], reconcile_before_push=True,
        _allow_bound_mutation=True)

    assert res.committed is True and res.pushed is False
    assert "budget" in res.detail or "postcondition" in res.detail
    if with_dirty_state:
        assert after_rebase_commands
        assert after_rebase_commands[0] == "reset"
    assert _git(clone, "status", "--porcelain=v1").returncode == 0
    assert not (clone / ".git" / "index.lock").exists()
    assert _git(
        clone, "for-each-ref", "--format=%(refname)",
        "refs/tm-mode/reconcile").stdout.strip() == ""


def test_bound_rollback_windows_kill_drain_and_one_second_steps_fit_reserve(
        cloned_repo, monkeypatch):
    """After a 7s Windows kill/drain tail, actual rollback fits the 25s reserve."""
    clone = cloned_repo.clone
    identity, _target = _bound_main_identity_and_target(clone)
    txn, detail = go._begin_bound_index_tx(
        str(clone), identity, go.DEFAULT_TIMEOUT)
    assert txn is not None, detail
    txn.stash_oid = "a" * 40
    rebase_dir = txn.index_path.parent / "rebase-merge"
    rebase_dir.mkdir()
    original_state = go._BoundUserState("", "", "")
    clock = {"now": 7.0}
    timeouts = []

    def fake_bound_git(_root, _txn, args, timeout, **_kwargs):
        timeouts.append(timeout)
        clock["now"] += timeout
        if "rebase" in args and "--abort" in args:
            rebase_dir.rmdir()
            return 0, "", ""
        if "symbolic-ref" in args:
            return 0, "main\n", ""
        if "rev-parse" in args:
            return 0, identity["head"] + "\n", ""
        if "reset" in args or ("stash" in args and "apply" in args):
            return 0, "", ""
        if "update-ref" in args and "AUTO_MERGE" in args:
            return 0, "", ""
        if "status" in args or "diff" in args:
            return 0, "", ""
        raise AssertionError(args)

    monkeypatch.setattr(go.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(go, "_run_bound_git", fake_bound_git)
    try:
        proven, rollback_detail, restored_identity = go._rollback_bound_reconcile(
            str(clone), txn, identity, original_state, deadline=25.0,
            mode="rebase")

        assert proven is True, rollback_detail
        assert restored_identity == identity
        assert timeouts and max(timeouts) <= 1
        assert clock["now"] <= 25.0
    finally:
        rebase_dir.rmdir() if rebase_dir.exists() else None
        go._remove_bound_tx_dir(txn)
        go._release_bound_lock(txn)


def test_bound_conflict_rollback_allows_unrelated_linked_worktree_commit(
        cloned_repo, monkeypatch):
    """Another branch advancing is not part of target-branch rollback proof."""
    _prepare_dirty_rebase(cloned_repo, conflict=True)
    clone = cloned_repo.clone
    linked = clone.parent / "linked-other"
    _git(clone, "branch", "other", "main")
    _git(clone, "worktree", "add", str(linked), "other")
    _git(linked, "config", "user.name", "t")
    _git(linked, "config", "user.email", "t@t")
    _make_staged_and_unstaged_changes(clone)
    (clone / "a.txt").write_text("local conflict\n")
    real_run_bound_git = go._run_bound_git
    linked_commit = {}

    def commit_other_after_conflict(team_root, txn, args, timeout, **kwargs):
        result = real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)
        if ("rebase" in args and "--abort" not in args
                and result[0] != 0 and not linked_commit):
            (linked / "other.txt").write_text("concurrent linked worktree\n")
            _git(linked, "add", "other.txt")
            _git(linked, "commit", "-m", "concurrent other branch")
            linked_commit["head"] = _git(
                linked, "rev-parse", "HEAD").stdout.strip()
        return result

    monkeypatch.setattr(go, "_run_bound_git", commit_other_after_conflict)
    res = go.do_commit(
        str(clone), "local conflict", push=True,
        paths=["a.txt"], reconcile_before_push=True,
        _allow_bound_mutation=True)

    assert linked_commit
    assert res.committed is True and res.pushed is False
    assert "aborted" in res.detail
    assert "rollback not proven" not in res.detail
    assert _git(clone, "rev-parse", "refs/heads/other").stdout.strip() == (
        linked_commit["head"])
    assert _git(
        clone, "for-each-ref", "--format=%(refname)",
        "refs/tm-mode/reconcile").stdout.strip() == ""
    assert not (clone / ".git" / "index.lock").exists()


def test_do_commit_partial_push_autostash_recovers_with_dirty_tracked(cloned_repo):
    """주 패턴 회귀가드: partial-commit(paths=) 으로 한 파일만 커밋 + push 하는데
    워킹트리에 **다른 추적파일의 미커밋 변경(dirty)** 이 남아있어도, non-ff 복구
    rebase 가 --autostash 로 그 dirty 를 흡수하고 재push 까지 성공해야 한다.

    이게 auto-commit 의 실제 패턴(세션로그만 partial-commit, 코드 등 다른 추적파일은
    워킹트리에 dirty 로 남음). 평문 rebase 였다면 "unstaged changes" 로 거부돼 복구가
    매번 불발한다 — 이 테스트가 그 회귀를 잡는다.
    """
    clone = cloned_repo.clone
    work = cloned_repo.work
    # clone 은 c2 만큼 behind. 추적파일 a.txt 를 unstaged-dirty 로 둔다(스테이지 안 함).
    (clone / "a.txt").write_text("locally edited a — uncommitted\n")
    # 다른(새) 파일만 paths= 로 partial-commit + push.
    (clone / "log.txt").write_text("session log line\n")
    res = go.do_commit(str(clone), "session log", push=True, paths=["log.txt"])
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is True, f"autostash 복구로 재push 성공해야 함: {res.detail}"
    # upstream(bare)에 partial 커밋이 반영됐는지 확인.
    _git(work, "fetch", "origin")
    log = _git(work, "log", "--oneline", "origin/main").stdout
    assert "session log" in log
    # dirty 추적파일 변경이 보존됨(autostash pop 으로 복원 — 유실 0).
    assert (clone / "a.txt").read_text() == "locally edited a — uncommitted\n"
    # 커밋은 log.txt 만 — a.txt 의 dirty 변경은 커밋에 휩쓸리지 않음(여전히 unstaged).
    porcelain = _git(clone, "status", "--porcelain", "--", "a.txt").stdout
    assert porcelain.strip().startswith(("M", " M")), \
        f"a.txt 가 여전히 미커밋 변경이어야 함: {porcelain!r}"
    # autostash 잔여 stash 없음(pop 으로 정리됨).
    assert _git(clone, "stash", "list").stdout.strip() == ""


def test_do_commit_push_rebase_conflict_autostash_no_residue(cloned_repo):
    """충돌로 rebase 가 실패해 abort 할 때, --autostash 로 stash 했던 dirty 가
    깨끗이 원복되고 **stash 잔여가 0** 이어야 한다(어정쩡한 상태 금지).
    """
    clone = cloned_repo.clone
    work = cloned_repo.work
    # upstream 이 a.txt 를 바꾼다(충돌 소스).
    (work / "a.txt").write_text("work version of a\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "work edit a")
    _git(work, "push")
    # clone 에 추적파일 e.txt 를 로컬 커밋(rebase 로 재생될 깨끗한 베이스).
    (clone / "e.txt").write_text("e base\n")
    _git(clone, "add", "e.txt")
    _git(clone, "commit", "-m", "add e")
    # e.txt 를 unstaged-dirty 로 둔다 → autostash 대상.
    (clone / "e.txt").write_text("e DIRTY uncommitted\n")
    # a.txt 를 다르게 바꿔 partial-commit → rebase 시 upstream 과 충돌.
    (clone / "a.txt").write_text("clone version of a\n")
    res = go.do_commit(str(clone), "clone edit a", push=True, paths=["a.txt"])
    assert res.ok is True
    assert res.committed is True
    assert res.pushed is False
    assert "aborted" in res.detail, f"abort 경로 표식 없음: {res.detail!r}"
    # rebase 진행중 흔적 없음.
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()
    # autostash 가 abort 로 원복 — dirty e.txt 보존, stash 잔여 0.
    assert (clone / "e.txt").read_text() == "e DIRTY uncommitted\n"
    assert _git(clone, "stash", "list").stdout.strip() == "", "stash 잔여 누수"


def test_is_non_fast_forward_ignores_server_hook_decline():
    """음성 가드: 서버훅/보호브랜치 거부(`[remote rejected] ... declined`)는 non-ff 가
    아니다 → False. 누가 패턴을 느슨하게 고쳐 훅거부에 rebase 를 걸면 이 테스트가 막는다
    (rebase+재push 가 보호브랜치를 영원히 두드리는 회귀 방지).
    """
    msg = " ! [remote rejected] main -> main (pre-receive hook declined)"
    assert go._is_non_fast_forward(msg) is False, \
        f"서버훅 거부를 non-ff 로 오탐: {msg!r}"


# ── git_ops.do_pull (이관된 안전장치) ──

def test_git_ops_exposes_do_pull(cloned_repo):
    res = go.do_pull(str(cloned_repo.clone))
    assert res.ok is True
    assert (cloned_repo.clone / "b.txt").exists()


def test_git_ops_do_pull_non_git_no_raise(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = go.do_pull(str(plain))
    assert res.ok is False  # 예외 없이 실패 표현


def test_git_ops_has_safety_primitives():
    # 안전장치 함수들이 공통 모듈에 존재(재사용 대상)
    for name in ("_run_git", "_git_env", "_kill_group", "_is_git_worktree",
                 "PullResult"):
        assert hasattr(go, name), f"git_ops 에 {name} 없음"


# ── auto_pull 이 git_ops 를 재사용 (드리프트 방지) ──

def test_auto_pull_reuses_git_ops():
    import auto_pull as ap
    # auto_pull 의 do_pull/PullResult 는 git_ops 와 동일 객체여야 한다(중복 정의 아님)
    assert ap.do_pull is go.do_pull
    assert ap.PullResult is go.PullResult


# ── `pull` 동사 (엔진 노출) ──

def _run_engine(root, *argv, env=None):
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(Path(root) / ".teammode-settings.json"), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          timeout=60)  # 러너 보호 하드캡(#36 flaky 진단)

def _hang_remote(tmp_path, repo, remote="origin"):
    """결정적 hang 원격(#36 flaky 진단) — TEST-NET 실 TCP 대체(부하 flaky 차단)."""
    bin_dir = tmp_path / "hang-bin"
    bin_dir.mkdir(exist_ok=True)
    helper = bin_dir / "git-remote-sleep"
    helper.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    helper.chmod(0o755)
    _git(repo, "remote", "set-url", remote, "sleep::repo")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH','')}"}



def test_pull_verb_ff_forwards(cloned_repo):
    r = _run_engine(cloned_repo.clone, "pull")
    assert r.returncode == 0, r.stderr
    assert (cloned_repo.clone / "b.txt").exists()  # 실제로 최신화됨


def test_pull_verb_english_for_en_locale_team(cloned_repo):
    """i18n(적대검수 — long tail, cmd_pull): en 팀(locale=en_US)은 pull 출력이
    영어이고 한글이 섞이지 않는다."""
    import re
    (cloned_repo.clone / "team.config.json").write_text(
        json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    r = _run_engine(cloned_repo.clone, "pull")
    assert r.returncode == 0, r.stderr
    assert "updated" in r.stdout
    assert not re.search(r"[가-힣]", r.stdout), f"en 팀 출력에 한글 섞임: {r.stdout!r}"


def test_pull_verb_non_git_graceful(tmp_path):
    # git 레포가 아니어도 우아하게 처리 — 작업 차단 금지(비치명 종료)
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run_engine(plain, "pull")
    # 실패해도 크래시(traceback) 아님. 비치명 처리.
    assert "Traceback" not in r.stderr


def test_pull_verb_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "pull"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


@pytest.mark.skipif(os.name == "nt", reason="git-remote-sleep 셸 helper 는 POSIX 전제")
def test_pull_verb_offline_no_hang(tmp_path):
    """원격이 비라우팅 IP 면 타임아웃으로 끊겨야 한다(hang 금지)."""
    work = tmp_path / "off"
    _git(tmp_path, "init", str(work))
    (work / "x").write_text("x")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c")
    # 결정적 hang 원격(remote helper) — 실 TCP blackhole 은 부하 flaky(#36 진단)
    _git(work, "remote", "add", "origin", "placeholder")
    env = _hang_remote(work.parent, work)
    _git(work, "branch", "--set-upstream-to=origin/main", check=False)
    import time
    t0 = time.time()
    r = _run_engine(work, "pull", env=env)
    elapsed = time.time() - t0
    # 하한: helper hang 이 실제로 걸렸음을 증명(codex 검수 — `git pull` 은 upstream
    # 미설정이어도 fetch 를 먼저 수행해 helper 를 태운다. 미래 git 변경으로 이 경로가
    # 공동화되면 하한이 깨져 테스트가 알려준다). 상한: killpg 가 hang 을 자른다.
    assert elapsed >= 5, f"helper hang 미발동({elapsed:.1f}s) — 테스트 공동화"
    assert elapsed < 20, f"pull 이 {elapsed:.1f}s 매달림 (hang)"
    assert "Traceback" not in r.stderr


@pytest.mark.skipif(os.name == "nt", reason="git-remote-sleep 셸 helper 는 POSIX 전제")
def test_do_pull_timeout_no_orphan_grandchild(tmp_path, monkeypatch):
    """역사적 버그 회귀 락: 타임아웃 시 손자 git-remote-http(s) 고아 누수 0.

    do_pull(짧은 timeout) 으로 비라우팅 원격에 pull → killpg 가 손자까지 죽이는지.
    git_ops 로 이관 후에도 안전장치가 살아있음을 실측한다.
    """
    import re
    import time
    work = tmp_path / "orphan"
    _git(tmp_path, "init", str(work))
    (work / "x").write_text("x")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c")
    _git(work, "remote", "add", "origin", "placeholder")
    env = _hang_remote(work.parent, work)
    monkeypatch.setenv("PATH", env["PATH"])  # in-proc do_pull 의 git 이 helper 를 찾게

    def _count_remote_http():
        # 손자 = git-remote-sleep(helper) — killpg 가 이걸 죽이는지 감시(결정적)
        try:
            out = subprocess.run(["pgrep", "-af", "git-remote-sleep"],
                                 capture_output=True, text=True).stdout
        except OSError:
            return 0
        return len([l for l in out.splitlines() if l.strip()])

    before = _count_remote_http()
    _t0 = time.time()
    res = go.do_pull(str(work), timeout=2)
    _elapsed = time.time() - _t0
    assert res.ok is False  # 타임아웃 — 예외 전파 0
    # 하한: helper hang 발동 증명(미발동이면 killpg 회귀락이 공동화 — codex 검수)
    assert _elapsed >= 1.5, f"helper hang 미발동({_elapsed:.1f}s)"
    time.sleep(1.5)  # 고아가 있었다면 이 시점까지 살아있을 것
    after = _count_remote_http()
    assert after <= before, f"손자 git-remote-http 고아 누수: before={before} after={after}"
