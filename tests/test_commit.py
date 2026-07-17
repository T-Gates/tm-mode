"""V.4 `commit` 동사 — git add/commit/push 묶음 테스트.

설계: git_ops 에 do_commit 추가(auto_pull 안전장치 패턴 따름 — 타임아웃·자격증명 차단·
무raise). add → commit → push 묶음. push 는 실제 원격 없으면 우아하게 처리(커밋은 성공).
실패 무해: 변경 없음·원격 없음·푸시 실패 모두 비치명.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·실 ~/.claude 무접촉.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
import git_ops as go  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Keep helper and product Git subprocesses off host configuration."""
    for name in list(os.environ):
        if (name in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
                or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))):
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


def _load_engine():
    """infra engine을 고유 이름으로 로드해 pip `teammode` stub 오염을 피한다."""
    spec = importlib.util.spec_from_file_location(
        "teammode_engine_commit", str(REPO / "infra" / "teammode.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tm = _load_engine()

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


def _git_subcommand(args):
    """Return the command after run_git's global -C/-c options."""
    iterator = iter(args)
    for arg in iterator:
        if arg in ("-C", "-c"):
            next(iterator, None)
            continue
        if str(arg).startswith("-"):
            continue
        return str(arg)
    return ""


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


def _prepare_tracking_other_with_local_commit(repo_with_remote):
    """Create tracking `other` with one local-only commit, then return to main."""
    clone = repo_with_remote.clone
    _git(clone, "checkout", "-b", "other", "main")
    (clone / "other-base.txt").write_text("remote other base\n", encoding="utf-8")
    _git(clone, "add", "other-base.txt")
    _git(clone, "commit", "-m", "add remote other base")
    _git(clone, "push", "-u", "origin", "other")
    remote_head = _git(
        repo_with_remote.upstream, "rev-parse", "refs/heads/other").stdout.strip()

    (clone / "other-local-only.txt").write_text(
        "must stay local\n", encoding="utf-8")
    _git(clone, "add", "other-local-only.txt")
    _git(clone, "commit", "-m", "local-only other commit")
    local_head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "main")
    return remote_head, local_head


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


def test_do_commit_opt_in_fetches_before_push(repo_with_remote, monkeypatch):
    clone = repo_with_remote.clone
    (clone / "ordered.txt").write_text("ordered\n", encoding="utf-8")
    calls = []
    real_run_git = go.run_git

    def recording_run_git(args, timeout):
        subcommand = _git_subcommand(args)
        if subcommand in {"commit", "fetch", "push"}:
            calls.append(subcommand)
        return real_run_git(args, timeout)

    monkeypatch.setattr(go, "run_git", recording_run_git)
    result = go.do_commit(
        str(clone), "ordered publication", push=True,
        reconcile_before_push=True)

    assert result.committed is True and result.pushed is True
    assert calls.count("commit") == 1
    assert calls.count("fetch") == 1
    assert calls.count("push") == 1
    assert calls.index("commit") < calls.index("fetch") < calls.index("push")


def test_do_commit_opt_in_disables_bound_worktree_mutation(
        repo_with_remote, monkeypatch):
    """PostToolUse publication may fetch, but must not reset/rebase the checkout."""
    clone = repo_with_remote.clone
    (clone / "safe-preflight.txt").write_text("safe\n", encoding="utf-8")
    observed = []

    def preflight_only(*_args, **kwargs):
        observed.append(kwargs.get("_allow_bound_mutation"))
        identity = kwargs["expected_identity"]
        return go.ReconcileResult(
            ok=True, action="ahead-only", ahead=1,
            final_identity=identity)

    monkeypatch.setattr(go, "do_reconcile", preflight_only)

    result = go.do_commit(
        str(clone), "safe publication", push=True,
        reconcile_before_push=True)

    assert observed == [False]
    assert result.committed is True and result.pushed is True


def test_do_commit_opt_in_without_push_skips_reconcile_network(
        local_repo, monkeypatch):
    (local_repo / "local-only.txt").write_text(
        "local only\n", encoding="utf-8")
    calls = []
    real_run_git = go.run_git

    def recording_run_git(args, timeout):
        subcommand = _git_subcommand(args)
        if subcommand in {"commit", "fetch", "push"}:
            calls.append(subcommand)
        return real_run_git(args, timeout)

    def unexpected_reconcile(*_args, **_kwargs):
        raise AssertionError("push=False must ignore reconcile_before_push")

    monkeypatch.setattr(go, "run_git", recording_run_git)
    monkeypatch.setattr(go, "do_reconcile", unexpected_reconcile)
    result = go.do_commit(
        str(local_repo), "local opt-in ignored", push=False,
        reconcile_before_push=True)

    assert result.ok is True and result.committed is True
    assert result.pushed is False
    assert calls == ["commit"]
    assert "local opt-in ignored" in _git(
        local_repo, "log", "-1", "--format=%s").stdout


@pytest.mark.parametrize(
    "commit_kwargs", ({}, {"reconcile_before_push": False}),
    ids=("default", "explicit-opt-out"))
def test_do_commit_push_opt_out_does_not_fetch(
        repo_with_remote, monkeypatch, commit_kwargs):
    clone = repo_with_remote.clone
    (clone / "default.txt").write_text("default\n", encoding="utf-8")
    calls = []
    real_run_git = go.run_git

    def recording_run_git(args, timeout):
        if "fetch" in args:
            calls.append("fetch")
        if "push" in args:
            calls.append("push")
        return real_run_git(args, timeout)

    monkeypatch.setattr(go, "run_git", recording_run_git)
    result = go.do_commit(
        str(clone), "opt-out publication", push=True, **commit_kwargs)

    assert result.committed is True and result.pushed is True
    assert "push" in calls
    assert "fetch" not in calls


def test_do_commit_opt_in_reconcile_failure_preserves_pending_identity(
        repo_with_remote, monkeypatch):
    clone = repo_with_remote.clone
    (clone / "deferred.txt").write_text("deferred\n", encoding="utf-8")
    push_attempts = []
    real_run_git = go.run_git

    def recording_run_git(args, timeout):
        if "push" in args:
            push_attempts.append(list(args))
        return real_run_git(args, timeout)

    monkeypatch.setattr(go, "run_git", recording_run_git)
    monkeypatch.setattr(
        go, "do_reconcile",
        lambda *_args, **_kwargs: go.ReconcileResult(
            ok=False, action="conflict", detail="simulated reconcile conflict"))

    result = go.do_commit(
        str(clone), "deferred publication", push=True,
        reconcile_before_push=True)
    committed_head = _git(clone, "rev-parse", "HEAD").stdout.strip()

    assert push_attempts == []
    assert result.ok is True
    assert result.committed is True
    assert result.pushed is False
    assert result.pending_identity == {
        "key": "branch:main", "branch": "main", "head": committed_head,
    }
    assert result.pending_target == {
        "remote": "origin",
        "destination": "refs/heads/main",
        "reconcile_ref": "refs/remotes/origin/main",
        "set_upstream": False,
        "remote_fingerprint": go._remote_push_fingerprint(
            str(clone), "origin"),
    }
    assert "pre-push reconcile conflict" in result.detail
    assert "simulated reconcile conflict" in result.detail


def test_do_commit_reconcile_stops_when_checkout_switches_after_fetch(
        repo_with_remote, tmp_path, monkeypatch):
    """A post-fetch checkout switch must not mutate or publish either branch."""
    clone = repo_with_remote.clone
    upstream = repo_with_remote.upstream
    _remote_other_base, local_other_head = (
        _prepare_tracking_other_with_local_commit(repo_with_remote))

    # Make `other` diverged so a reconcile that silently follows the switched
    # checkout would rebase and publish it, not merely perform a harmless push.
    peer = tmp_path / "other-peer"
    _git(tmp_path, "clone", "-b", "other", str(upstream), str(peer))
    _git(peer, "config", "user.name", "t")
    _git(peer, "config", "user.email", "t@t")
    (peer / "other-remote-only.txt").write_text(
        "remote peer change\n", encoding="utf-8")
    _git(peer, "add", "other-remote-only.txt")
    _git(peer, "commit", "-m", "remote-only other commit")
    _git(peer, "push")
    remote_other_before = _git(
        upstream, "rev-parse", "refs/heads/other").stdout.strip()
    assert remote_other_before != local_other_head

    remote_main_before = _git(
        upstream, "rev-parse", "refs/heads/main").stdout.strip()
    edited = clone / "main-after-fetch.txt"
    edited.write_text("main publication\n", encoding="utf-8")
    real_run_git = go.run_git
    switched = False
    main_head_at_fetch = ""
    reconcile_mutations = []
    push_commands = []

    def racing_run_git(args, timeout):
        nonlocal switched, main_head_at_fetch
        subcommand = _git_subcommand(args)
        if subcommand == "fetch" and not switched:
            main_head_at_fetch = _git(
                clone, "rev-parse", "refs/heads/main").stdout.strip()
            result = real_run_git(args, timeout)
            _git(clone, "checkout", "other")
            switched = True
            return result
        if switched and subcommand in {"merge", "rebase"}:
            reconcile_mutations.append(list(args))
        if switched and subcommand == "push":
            push_commands.append(list(args))
        return real_run_git(args, timeout)

    monkeypatch.setattr(go, "run_git", racing_run_git)
    result = go.do_commit(
        str(clone), "publish captured main", push=True,
        paths=[edited.name], reconcile_before_push=True)

    assert switched is True
    assert _git(
        clone, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() == "other"
    assert main_head_at_fetch != ""
    assert _git(
        clone, "rev-parse", "refs/heads/main").stdout.strip() == main_head_at_fetch
    assert _git(
        clone, "rev-parse", "refs/heads/other").stdout.strip() == local_other_head
    assert reconcile_mutations == []
    assert push_commands == []
    assert result.committed is True and result.pushed is False
    assert result.pending_identity == {
        "key": "branch:main", "branch": "main", "head": main_head_at_fetch,
    }
    assert _git(
        upstream, "rev-parse", "refs/heads/main").stdout.strip() == remote_main_before
    assert _git(
        upstream, "cat-file", "-e", "refs/heads/main:main-after-fetch.txt",
        check=False).returncode != 0
    assert _git(
        upstream, "rev-parse", "refs/heads/other").stdout.strip() == remote_other_before


def test_do_commit_push_uses_captured_main_refspec_after_checkout_switch(
        repo_with_remote, monkeypatch):
    """A switch at push invocation must still publish main, never current other."""
    clone = repo_with_remote.clone
    upstream = repo_with_remote.upstream
    remote_other_before, _local_other_head = (
        _prepare_tracking_other_with_local_commit(repo_with_remote))
    edited = clone / "main-at-push.txt"
    edited.write_text("captured main publication\n", encoding="utf-8")
    real_run_git = go.run_git
    push_commands = []
    main_head_at_push = ""

    def racing_run_git(args, timeout):
        nonlocal main_head_at_push
        if _git_subcommand(args) == "push":
            push_commands.append(list(args))
            if len(push_commands) == 1:
                main_head_at_push = _git(
                    clone, "rev-parse", "refs/heads/main").stdout.strip()
                _git(clone, "checkout", "other")
        return real_run_git(args, timeout)

    monkeypatch.setattr(go, "run_git", racing_run_git)
    result = go.do_commit(
        str(clone), "publish main across checkout race", push=True,
        paths=[edited.name], reconcile_before_push=True)

    assert len(push_commands) == 1
    push_tail = push_commands[0][push_commands[0].index("push") + 1:]
    separator = push_tail.index("--")
    assert push_tail[:separator] == [
        "--no-follow-tags", "--recurse-submodules=check"]
    endpoint_alias, refspec = push_tail[separator + 1:]
    assert endpoint_alias.startswith("tm-mode-exact-")
    source, destination = refspec.rsplit(":", 1)
    assert source == main_head_at_push
    assert destination == "refs/heads/main"
    assert result.committed is True and result.pushed is True
    assert result.pending_identity == {
        "key": "branch:main", "branch": "main", "head": main_head_at_push,
    }
    assert _git(
        upstream, "rev-parse", "refs/heads/main").stdout.strip() == main_head_at_push
    assert _git(
        upstream, "cat-file", "-e", "refs/heads/main:main-at-push.txt").returncode == 0
    assert _git(
        upstream, "rev-parse", "refs/heads/other").stdout.strip() == remote_other_before


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


def test_commit_verb_requires_message_english_for_en_locale_team(local_repo):
    """i18n(적대검수 — long tail, main()): en 팀(locale=en_US)의 --message 누락
    에러는 영어이고 한글이 섞이지 않는다."""
    import json
    import re
    (local_repo / "team.config.json").write_text(
        json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    (local_repo / "i2.txt").write_text("v\n")
    r = _run_engine(local_repo, "commit")
    assert r.returncode != 0
    assert "--message" in r.stderr
    assert not re.search(r"[가-힣]", r.stderr), f"en 팀 출력에 한글 섞임: {r.stderr!r}"


def test_commit_verb_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "commit", "--message", "x"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


def test_commit_verb_no_changes_graceful(local_repo):
    r = _run_engine(local_repo, "commit", "--message", "nothing to do")
    # 변경 없음 → 비치명(크래시 없음)
    assert "Traceback" not in r.stderr


def test_commit_verb_push_no_remote_records_recovery_state(
        local_repo, tmp_path, monkeypatch):
    """OFF fallback push 실패도 pending/warning을 남겨 SessionStart가 재시도한다."""
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    env = {**os.environ, "TEAMMODE_DISABLE_PUSH_WORKER": "1"}
    (local_repo / "session.md").write_text("session\n", encoding="utf-8")

    r = _run_engine(local_repo, "commit", "--message", "session fallback",
                    "--paths", "session.md", "--push", env=env)

    assert r.returncode == 0, r.stderr
    root = str(local_repo.resolve())
    pending = go.read_push_pending_state(root)
    assert pending.available is True
    assert go.pending_targets_current_checkout(root, pending.content) is True
    warning = go.read_sync_warning(root)
    assert warning
    assert "push" in warning.lower()


def test_cmd_commit_push_failure_records_sanitized_warning_and_kicks_worker(
        monkeypatch, tmp_path, capsys):
    """복구 ledger 기록 성공 시 rc0 계약을 유지하고 worker를 kick한다."""
    raw = ("fatal https://alice:password@example.com/repo "
           "Authorization: Bearer bearer-secret")
    calls = {}
    identity = {"key": "branch:session", "branch": "session", "head": "a" * 40}
    monkeypatch.setattr(
        tm._git_ops, "do_commit",
        lambda *args, **kwargs: go.CommitResult(
            ok=True, committed=True, pushed=False, detail=raw,
            pending_identity=identity))
    monkeypatch.setattr(
        tm._git_ops, "write_push_pending",
        lambda root, seen: calls.__setitem__("pending", (root, seen)) or True)
    monkeypatch.setattr(
        tm._git_ops, "write_sync_warning",
        lambda root, detail: calls.__setitem__("warning", (root, detail)))
    monkeypatch.setattr(
        tm._git_ops, "kick_push_worker",
        lambda root, worker: calls.__setitem__("kick", (root, worker)) or True)

    rc = tm.cmd_commit(tmp_path, "session fallback", push=True,
                       paths=["session.md"])

    rendered = calls["warning"][1] + "\n" + capsys.readouterr().out
    assert rc == 0
    assert calls["pending"] == (str(tmp_path), identity)
    assert calls["kick"][0] == str(tmp_path)
    assert calls["kick"][1].endswith("infra/hooks/push-worker.py")
    assert "password" not in rendered
    assert "bearer-secret" not in rendered
    assert "[redacted]" in rendered


def test_cmd_commit_pending_write_failure_is_nonzero_and_visible(
        monkeypatch, tmp_path, capsys):
    """ledger를 못 쓰면 성공처럼 끝내지 않고 sanitized 경고와 rc1을 낸다."""
    (tmp_path / "team.config.json").write_text(
        '{"team":{"locale":"ko_KR"}}', encoding="utf-8")
    raw = "fatal token=super-secret-value"
    calls = {}
    identity = {"key": "branch:session", "branch": "session", "head": "b" * 40}
    monkeypatch.setattr(
        tm._git_ops, "do_commit",
        lambda *args, **kwargs: go.CommitResult(
            ok=True, committed=True, pushed=False, detail=raw,
            pending_identity=identity))
    monkeypatch.setattr(
        tm._git_ops, "write_push_pending", lambda _root, _identity: False)
    monkeypatch.setattr(
        tm._git_ops, "write_sync_warning",
        lambda root, detail: calls.__setitem__("warning", detail))
    monkeypatch.setattr(
        tm._git_ops, "kick_push_worker",
        lambda *_args: calls.__setitem__("kick", True) or True)

    rc = tm.cmd_commit(tmp_path, "session fallback", push=True,
                       paths=["session.md"])

    captured = capsys.readouterr()
    rendered = calls["warning"] + "\n" + captured.out + "\n" + captured.err
    assert rc == 1
    assert "push-pending" in captured.err
    assert "push-pending 상태를 안전하게 갱신하지 못했습니다" in rendered
    assert "커밋은 보존됐지만 자동 push 복구는 예약되지 않았습니다" in rendered
    assert "super-secret-value" not in rendered
    assert "[redacted]" in rendered
    assert "XDG" not in rendered
    assert "권한" not in rendered
    assert "kick" not in calls


@pytest.mark.parametrize(
    ("push", "result"),
    [
        (False, go.CommitResult(ok=True, committed=True, pushed=False,
                                detail="committed")),
        (True, go.CommitResult(ok=True, committed=True, pushed=True,
                               detail="committed and pushed")),
    ],
)
def test_cmd_commit_does_not_schedule_recovery_without_push_failure(
        monkeypatch, tmp_path, push, result):
    """commit-only와 push 성공 계약에는 pending/worker 부작용이 없다."""
    monkeypatch.setattr(tm._git_ops, "do_commit", lambda *args, **kwargs: result)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("recovery state must not be touched")

    monkeypatch.setattr(tm._git_ops, "write_push_pending", unexpected)
    monkeypatch.setattr(tm._git_ops, "write_sync_warning", unexpected)
    monkeypatch.setattr(tm._git_ops, "kick_push_worker", unexpected)

    assert tm.cmd_commit(tmp_path, "normal", push=push) == 0


@pytest.mark.skipif(os.name == "nt", reason="git-remote-sleep 셸 helper 는 POSIX 전제")
def test_commit_verb_offline_push_no_hang(repo_with_remote, tmp_path):
    # 원격을 결정적 hang(remote helper)으로 바꿔 push 가 hang 하지 않는지(타임아웃)
    env = _hang_remote(tmp_path, repo_with_remote.clone)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    env["TEAMMODE_DISABLE_PUSH_WORKER"] = "1"
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


@pytest.mark.skipif(os.name == "nt", reason="git-remote-sleep 셸 helper 는 POSIX 전제")
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
