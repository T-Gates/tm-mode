"""foreground auto-commit publication + async fallback worker tests.

확정 스펙(#19 + #45 immutable-target fallback):
  - auto-commit 은 scoped commit 뒤 remote가 앞서면 worktree reconcile을 보류한다.
  - 전경 publication 실패만 XDG pending 원자 기록 + push-worker detach kick.
  - push-worker는 저장 당시 immutable HEAD/remote/destination만 push하고 로컬
    히스토리와 worktree를 건드리지 않는다. non-ff는 pending과 경고로 보존한다.
  - pending clear는 exact push 성공 + ledger snapshot CAS로만 수행한다.
  - 성공 = pending·sync-warning clear / 실패 = sync-warning detail.

모든 테스트는 tmp_path + XDG_STATE_HOME 격리 — 실 호스트 무접촉.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Keep fixture and product Git subprocesses off host configuration."""
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


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    """XDG_STATE_HOME 격리 — 실 ~/.local/state 무접촉."""
    state = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


@pytest.fixture()
def publication_ready(monkeypatch):
    """Isolate ledger-only unit tests from real-repository blocker probing."""
    @git_ops.contextmanager
    def ready_interlock(_root, _timeout=1.0):
        yield True, ""

    monkeypatch.setattr(git_ops, "_publication_interlock", ready_interlock)
    monkeypatch.setattr(git_ops, "publication_blocker_detail", lambda *_a: "")


def _init_repo(path: Path, *, bare: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    # 전역 init.defaultBranch에 기대면 개발자 Mac(main)에서는 통과하고 깨끗한
    # GitHub runner(master)에서는 origin/main fixture가 사라진다. fixture 자체가
    # branch 계약을 결정해 환경과 무관하게 만든다.
    args = ["git", "init", "-q", "--initial-branch=main"] \
        + (["--bare"] if bare else []) + [str(path)]
    subprocess.run(args, check=True, capture_output=True)
    if not bare:
        subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                       check=True, capture_output=True)
    return path


def _clone_pair(tmp_path) -> tuple:
    """bare origin + 작업 클론 (upstream tracking 설정 완료)."""
    origin = _init_repo(tmp_path / "origin.git", bare=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (work / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "."],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-qu", "origin", "HEAD"],
                   check=True, capture_output=True)
    return origin, work


def _commit_file(repo: Path, name: str, content: str = "x") -> None:
    (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"add {name}"],
                   check=True, capture_output=True)


def _commit_auto_session(repo: Path, name: str, content: str = "session\n") -> None:
    """v0.1.3 auto-commit과 같은 subject/path만 가진 안전한 legacy 후보."""
    relative = Path("memory") / "team" / "sessions" / "tester" / name
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(relative)],
                   check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm",
         "chore(teammode): auto-commit 2026-07-11 12:00 KST"],
        check=True, capture_output=True)


def _clone_other(origin: Path, path: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(path)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "o@o.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "O"],
                   check=True, capture_output=True)
    return path


def _git_text(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _bare_git_text(origin: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(origin), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _git_rc(repo: Path, *args: str) -> int:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True).returncode


def _external_rebase_pending_head(
        origin: Path, work: Path, other_path: Path, *, prefix: str,
        rebase: bool = True) -> tuple:
    """Push remote R and optionally rewrite unpublished H1 with a real rebase."""
    old_head = _git_text(work, "rev-parse", "HEAD")
    other = _clone_other(origin, other_path)
    _commit_file(other, f"{prefix}-remote-r.md", "remote R\n")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    remote_head = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    if not rebase:
        return old_head, old_head, remote_head
    subprocess.run(
        ["git", "-C", str(work), "-c", "rebase.backend=apply",
         "pull", "--rebase"], check=True, capture_output=True)
    rewritten_head = _git_text(work, "rev-parse", "HEAD")
    assert old_head != rewritten_head
    assert _git_rc(work, "merge-base", "--is-ancestor",
                   old_head, rewritten_head) == 1
    assert _git_text(work, "cherry", rewritten_head, old_head) == f"- {old_head}"
    return old_head, rewritten_head, remote_head


def _rewrite_pending_with_other_entry(tmp_path: Path) -> tuple:
    """Create another-checkout entry plus main H1 rewritten to H1' and H2."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb",
                    "other-pending"], check=True, capture_output=True)
    _commit_file(work, "other-pending.md", "other checkout\n")
    assert git_ops.write_push_pending(str(work)) is True
    other_key = "branch:other-pending"

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    _commit_file(work, "rewrite-write-h1.md", "pending H1\n")
    assert git_ops.write_push_pending(str(work)) is True
    old_snapshot = git_ops.read_push_pending(str(work))
    main_key = git_ops.pending_entry_key_for_current_checkout(
        str(work), old_snapshot)
    old_entries = git_ops._pending_entries(old_snapshot)
    _external_rebase_pending_head(
        origin, work, tmp_path / "rewrite-write-other", prefix="rewrite-write")
    _commit_file(work, "rewrite-write-h2.md", "local H2\n")
    return work, main_key, other_key, old_entries


_RECONCILE_BLOCKER_KINDS = (
    "index-lock",
    "rebase-merge",
    "rebase-apply",
    "merge-head",
    "merge-autostash",
    "auto-merge",
    "reconcile-ref",
    "transaction-dir",
)


def _git_path(repo: Path, name: str) -> Path:
    """Resolve a per-worktree Git administrative path for race fixtures."""
    raw = Path(_git_text(repo, "rev-parse", "--git-path", name))
    return raw if raw.is_absolute() else repo / raw


def _install_reconcile_blocker(repo: Path, kind: str) -> None:
    """Materialize one unresolved bound-reconcile artifact without running Git."""
    admin = _git_path(repo, "index").parent
    if kind == "index-lock":
        Path(f"{_git_path(repo, 'index')}.lock").write_text(
            "unresolved reconcile\n", encoding="utf-8")
    elif kind in {"rebase-merge", "rebase-apply"}:
        (admin / kind).mkdir()
    elif kind in {"merge-head", "merge-autostash", "auto-merge"}:
        name = {
            "merge-head": "MERGE_HEAD",
            "merge-autostash": "MERGE_AUTOSTASH",
            "auto-merge": "AUTO_MERGE",
        }[kind]
        (admin / name).write_text("unresolved reconcile\n", encoding="utf-8")
    elif kind == "reconcile-ref":
        subprocess.run(
            ["git", "-C", str(repo), "update-ref",
             "refs/tm-mode/reconcile/red-test/head", "HEAD"],
            check=True, capture_output=True)
    elif kind == "transaction-dir":
        (admin / ".tm-mode-reconcile-red-test").mkdir()
    else:  # pragma: no cover - parameter list is the closed fixture contract
        raise AssertionError(f"unknown reconcile blocker: {kind}")


# ── pending ledger ──────────────────────────────────────────────────

def test_pending_ledger_roundtrip(xdg, tmp_path, publication_ready):
    """write → read(truthy) → snapshot clear — 팀별 파일, XDG 하위."""
    root = str(tmp_path / "team")
    assert git_ops.read_push_pending(root) == ""
    git_ops.write_push_pending(root)
    assert git_ops.read_push_pending(root) != ""
    p = Path(git_ops.push_pending_path(root))
    assert p.is_file() and str(xdg) in str(p)
    snapshot = git_ops.read_push_pending(root)
    assert git_ops.clear_push_pending_if_unchanged(root, snapshot) is True
    assert git_ops.read_push_pending(root) == ""
    assert git_ops.clear_push_pending_if_unchanged(root, snapshot) is False


def test_pending_ledger_is_per_team(xdg, tmp_path, publication_ready):
    """팀 A 의 clear 가 팀 B 마커를 건드리지 않는다(sync-warning 과 동일 규약)."""
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    git_ops.write_push_pending(a)
    git_ops.write_push_pending(b)
    snapshot = git_ops.read_push_pending(a)
    assert git_ops.clear_push_pending_if_unchanged(a, snapshot) is True
    assert git_ops.read_push_pending(a) == ""
    assert git_ops.read_push_pending(b) != ""


def test_pending_age_seconds(xdg, tmp_path):
    """age: 없으면 None, 있으면 0 이상 float — UserPromptSubmit 경량검사용."""
    root = str(tmp_path / "team")
    assert git_ops.push_pending_age_seconds(root) is None
    git_ops.write_push_pending(root)
    age = git_ops.push_pending_age_seconds(root)
    assert isinstance(age, float) and age >= 0.0


# ── push_plain (plain-push-only) ────────────────────────────────────

def test_push_plain_success(xdg, tmp_path):
    """로컬 ahead 1 → plain push 성공."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail
    ahead, behind = git_ops.ahead_behind(str(work))
    assert ahead == 0


def test_push_plain_non_ff_no_recovery(xdg, tmp_path):
    """non-ff: 복구(rebase/fetch) 없이 pushed=False + 'non-fast-forward' 분류.

    로컬 히스토리 무접촉이 계약 — worker 가 rebase 를 하면 index.lock 경합으로
    사용자 편집 커밋이 조용히 유실될 수 있다(#45 정정의 근거).
    """
    origin, work = _clone_pair(tmp_path)
    # 다른 클론이 먼저 push → work 는 non-ff
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "O"],
                   capture_output=True)
    _commit_file(other, "theirs.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"],
                   capture_output=True)

    _commit_file(work, "mine.md")
    head_before = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is False
    assert "non-fast-forward" in detail
    # 로컬 히스토리 무접촉(HEAD 불변 — rebase 안 함)
    head_after = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    assert head_after == head_before


def test_push_plain_no_upstream_sets_u_once(xdg, tmp_path):
    """upstream 미설정 브랜치: `push -u origin HEAD` 1회로 성공."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "feat/x"],
                   capture_output=True)
    _commit_file(work, "b.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail


def test_push_plain_retargets_mismatched_upstream_to_same_name_branch(
        xdg, tmp_path):
    """`checkout -b feat origin/main`의 simple-push mismatch도 origin/feat로 복구."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "feat/session",
                    "origin/main"], check=True, capture_output=True)
    _commit_file(work, "feature-session.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail
    assert _bare_git_text(origin, "show", "feat/session:feature-session.md") == "x"
    upstream = _git_text(work, "rev-parse", "--abbrev-ref", "@{u}")
    assert upstream == "origin/feat/session"


@pytest.mark.parametrize("blocker", _RECONCILE_BLOCKER_KINDS)
def test_push_plain_refuses_unresolved_reconcile_artifact(
        xdg, tmp_path, blocker):
    """Plain fallback must not publish while a reconcile transaction is unresolved."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "must-stay-local.md", blocker)
    assert git_ops.write_push_pending(str(work)) is True
    warning_before = f"actionable warning before {blocker}"
    git_ops.write_sync_warning(str(work), warning_before)
    pending_before = git_ops.read_push_pending(str(work))
    remote_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    _install_reconcile_blocker(work, blocker)

    pushed, detail = git_ops.push_plain(str(work))

    assert pushed is False
    assert detail, "blocker refusal must remain diagnosable"
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == remote_before
    assert git_ops.read_push_pending(str(work)) == pending_before
    assert git_ops.read_sync_warning(str(work)) == warning_before


def test_push_plain_fails_closed_when_reconcile_blocker_probe_unavailable(
        tmp_path, monkeypatch):
    """An unavailable blocker probe is not equivalent to a clean repository."""
    calls = []

    def fake_run_git(args, timeout, **kwargs):
        calls.append((args, timeout, kwargs))
        return 0, "", ""

    monkeypatch.setattr(
        git_ops, "publication_blocker_detail",
        lambda _root, timeout=git_ops.DEFAULT_TIMEOUT: "blocker probe unavailable",
        raising=False)
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)

    pushed, detail = git_ops.push_plain(str(tmp_path / "team"))

    assert pushed is False
    assert "unavailable" in detail
    assert all("push" not in args for args, _timeout, _kwargs in calls), (
        "a failed blocker probe must prevent the network push call")


@pytest.mark.parametrize(
    "blocker", ("index-lock", "rebase-merge", "transaction-dir"))
def test_publication_blocker_scans_other_linked_worktree_admin(
        tmp_path, blocker):
    """A sibling worktree's private admin residue blocks the shared repo."""
    _origin, work = _clone_pair(tmp_path)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(work), "worktree", "add", "-qb", "linked-review",
         str(linked)], check=True, capture_output=True)
    _install_reconcile_blocker(linked, blocker)

    detail = git_ops.publication_blocker_detail(str(work))

    assert detail
    assert "blocker" in detail or "rebase" in detail or "transaction" in detail


# ── push-worker (drain loop · plain-push-only) ──────────────────────

WORKER = REPO / "infra" / "hooks" / "push-worker.py"


def _run_worker(root: Path, env_extra: dict | None = None):
    env = os.environ.copy()
    env_extra = env_extra or {}
    env.update(env_extra)
    return subprocess.run([sys.executable, str(WORKER), "--root", str(root)],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("mode", ("artifact", "probe-unavailable"))
def test_kick_push_worker_fails_closed_before_detach_spawn(
        xdg, tmp_path, monkeypatch, mode):
    """A blocked/probe-unknown repository must not even detach a worker."""
    _, work = _clone_pair(tmp_path)
    if mode == "artifact":
        _install_reconcile_blocker(work, "index-lock")
    else:
        monkeypatch.setattr(
            git_ops, "publication_blocker_detail",
            lambda _root, timeout=git_ops.DEFAULT_TIMEOUT:
                "reconcile blocker probe unavailable",
                raising=False)
    spawned = []
    real_popen = git_ops.subprocess.Popen

    def fake_popen(*args, **kwargs):
        command = args[0] if args else kwargs.get("args", [])
        if command and Path(command[0]).name == "git":
            return real_popen(*args, **kwargs)
        spawned.append((args, kwargs))
        return object()

    monkeypatch.delenv("TEAMMODE_DISABLE_PUSH_WORKER", raising=False)
    monkeypatch.setattr(git_ops.subprocess, "Popen", fake_popen)

    assert git_ops.kick_push_worker(str(work), str(WORKER)) is False
    assert spawned == []


def test_worker_pushes_and_clears_pending(xdg, tmp_path, monkeypatch):
    """pending 존재 + ahead 1 → push 성공 → pending·sync-warning clear."""
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    git_ops.write_push_pending(str(work))
    git_ops.write_sync_warning(str(work), "이전 실패 잔재")
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0, r.stderr
    assert git_ops.read_push_pending(str(work)) == ""
    assert git_ops.read_sync_warning(str(work)) == ""
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 0


def test_worker_pushes_recorded_immutable_head_after_branch_reset(xdg, tmp_path):
    """A pending H1 is published even if the checked-out branch is reset to H0."""
    origin, work = _clone_pair(tmp_path)
    base = _git_text(work, "rev-parse", "HEAD")
    _commit_file(work, "pending-h1.md", "pending H1\n")
    pending_head = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True

    subprocess.run(
        ["git", "-C", str(work), "reset", "--hard", base],
        check=True, capture_output=True)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert r.returncode == 0, r.stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == pending_head
    assert _git_text(work, "rev-parse", "HEAD") == base
    assert git_ops.read_push_pending(str(work)) == ""


def test_worker_uses_recorded_remote_and_destination_after_config_change(
        xdg, tmp_path):
    """Pending publication is bound to its original remote/ref, not current config."""
    origin, work = _clone_pair(tmp_path)
    fork = _init_repo(tmp_path / "fork.git", bare=True)
    subprocess.run(
        ["git", "-C", str(work), "remote", "add", "fork", str(fork)],
        check=True, capture_output=True)
    _commit_file(work, "fork-only.md", "fork publication\n")
    pending_head = _git_text(work, "rev-parse", "HEAD")
    identity = git_ops._checkout_identity(str(work))
    target = {
        "remote": "fork",
        "destination": "refs/heads/team-sync",
        "reconcile_ref": "refs/remotes/fork/team-sync",
        "set_upstream": False,
        "remote_fingerprint": git_ops._remote_push_fingerprint(
            str(work), "fork"),
    }
    assert git_ops.write_push_pending(
        str(work), identity, target=target) is True
    origin_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")

    # A later config change must not retarget the already-recorded publication.
    subprocess.run(
        ["git", "-C", str(work), "config", "remote.pushDefault", "origin"],
        check=True, capture_output=True)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert r.returncode == 0, r.stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == origin_before
    assert _bare_git_text(fork, "rev-parse", "refs/heads/team-sync") == pending_head
    assert git_ops.read_push_pending(str(work)) == ""


def test_worker_refuses_remote_name_retargeted_to_different_url(xdg, tmp_path):
    """Stored remote names cannot be rebound to a different repository URL."""
    origin, work = _clone_pair(tmp_path)
    replacement = _init_repo(tmp_path / "replacement.git", bare=True)
    _commit_file(work, "original-destination.md", "must stay bound\n")
    assert git_ops.write_push_pending(str(work)) is True
    pending_before = git_ops.read_push_pending(str(work))
    original_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")

    subprocess.run(
        ["git", "-C", str(work), "remote", "set-url", "origin",
         str(replacement)], check=True, capture_output=True)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert r.returncode == 0, r.stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == original_before
    replacement_refs = _bare_git_text(
        replacement, "for-each-ref", "--format=%(refname)", "refs/heads")
    assert replacement_refs == ""
    assert git_ops.read_push_pending(str(work)) == pending_before
    assert "remote" in git_ops.read_sync_warning(str(work)).lower()


@pytest.mark.parametrize("rewrite_key", ("insteadOf", "pushInsteadOf"))
def test_exact_pending_push_ignores_late_url_rewrite_config(
        xdg, tmp_path, monkeypatch, rewrite_key):
    """A captured endpoint cannot be rewritten by Git config at push time."""
    origin, work = _clone_pair(tmp_path)
    replacement = _init_repo(tmp_path / f"rewrite-{rewrite_key}.git", bare=True)
    _commit_file(work, "rewrite-race.md", "captured endpoint only\n")
    pending_head = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    target_key = git_ops.pending_entry_key_for_current_checkout(
        str(work), snapshot)
    real_push = git_ops._run_exact_publication_push

    def race_config(root, endpoint, destination, tracking_ref, head, timeout):
        subprocess.run(
            ["git", "-C", root, "config",
             f"url.{replacement}.{rewrite_key}", endpoint],
            check=True, capture_output=True)
        return real_push(
            root, endpoint, destination, tracking_ref, head, timeout)

    monkeypatch.setattr(git_ops, "_run_exact_publication_push", race_config)
    pushed, detail = git_ops.push_pending_entry(
        str(work), snapshot, target_key)

    assert pushed is True, detail
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == pending_head
    assert _bare_git_text(
        replacement, "for-each-ref", "--format=%(refname)", "refs/heads") == ""


def test_worker_refuses_multiple_push_urls(xdg, tmp_path):
    """One remote name mapping to multiple endpoints is never auto-published."""
    origin, work = _clone_pair(tmp_path)
    first = _init_repo(tmp_path / "first-pushurl.git", bare=True)
    second = _init_repo(tmp_path / "second-pushurl.git", bare=True)
    _commit_file(work, "ambiguous-pushurl.md", "must remain pending\n")
    assert git_ops.write_push_pending(str(work)) is True
    pending_before = git_ops.read_push_pending(str(work))
    origin_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    subprocess.run(
        ["git", "-C", str(work), "config", "--add",
         "remote.origin.pushurl", str(first)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "--add",
         "remote.origin.pushurl", str(second)], check=True, capture_output=True)

    result = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert result.returncode == 0, result.stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == origin_before
    for endpoint in (first, second):
        assert _bare_git_text(
            endpoint, "for-each-ref", "--format=%(refname)", "refs/heads") == ""
    assert git_ops.read_push_pending(str(work)) == pending_before
    assert "remote" in git_ops.read_sync_warning(str(work)).lower()


def test_exact_pending_push_does_not_follow_annotated_tags(xdg, tmp_path):
    """push.followTags cannot expand an immutable branch publication."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "branch-only.md", "branch publication\n")
    pending_head = _git_text(work, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "-C", str(work), "tag", "-a", "private-tag", "-m", "private"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "push.followTags", "true"],
        check=True, capture_output=True)
    assert git_ops.write_push_pending(str(work)) is True

    result = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert result.returncode == 0, result.stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == pending_head
    assert _bare_git_text(
        origin, "for-each-ref", "--format=%(refname)", "refs/tags") == ""


def test_exact_pending_push_respects_pre_push_hook_rejection(xdg, tmp_path):
    """Repository policy hooks reject publication and leave durable retry state."""
    origin, work = _clone_pair(tmp_path)
    remote_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    hook = _git_path(work, "hooks") / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o700)
    _commit_file(work, "policy-rejected.md", "must remain local\n")
    assert git_ops.write_push_pending(str(work)) is True
    pending_before = git_ops.read_push_pending(str(work))

    result = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert result.returncode == 0
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == remote_before
    assert git_ops.read_push_pending(str(work)) == pending_before
    assert "push" in git_ops.read_sync_warning(str(work)).lower()


def test_exact_push_command_checks_submodule_availability(monkeypatch, tmp_path):
    """Exact single-ref publication still honors gitlink availability policy."""
    _, work = _clone_pair(tmp_path)
    observed = []
    monkeypatch.setattr(
        git_ops, "_read_ref_oid", lambda *_args, **_kwargs: (True, ""))

    def capture(args, _timeout):
        observed.extend(args)
        return 1, "", "rejected"

    monkeypatch.setattr(git_ops, "_run_publication_push_locked", capture)

    git_ops._run_exact_publication_push_locked(
        str(work), str(tmp_path / "origin.git"), "refs/heads/main",
        "refs/remotes/origin/main", "a" * 40, 2)

    assert "--no-verify" not in observed
    assert "--recurse-submodules=check" in observed


def test_pending_history_proof_ignores_repo_local_replace_refs(tmp_path):
    """Replace refs cannot turn an unrelated recommit into published history."""
    repo = _init_repo(tmp_path / "replace-proof")
    _commit_file(repo, "base.md", "base\n")
    base = _git_text(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "old", base],
                   check=True, capture_output=True)
    _commit_file(repo, "old.md", "old pending\n")
    old = _git_text(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    _commit_file(repo, "new.md", "unrelated recommit\n")
    new = _git_text(repo, "rev-parse", "HEAD")

    assert not git_ops._pending_head_covered_by_history(str(repo), old, new)
    subprocess.run(
        ["git", "-C", str(repo), "replace", "--graft", new, old],
        check=True, capture_output=True)
    assert _git_rc(repo, "merge-base", "--is-ancestor", old, new) == 0

    assert not git_ops._pending_head_covered_by_history(str(repo), old, new)


def test_pending_history_proof_ignores_repo_local_grafts(tmp_path):
    """Legacy graft files cannot forge pending publication ancestry."""
    repo = _init_repo(tmp_path / "graft-proof")
    _commit_file(repo, "base.md", "base\n")
    base = _git_text(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "old", base],
                   check=True, capture_output=True)
    _commit_file(repo, "old.md", "old pending\n")
    old = _git_text(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    _commit_file(repo, "new.md", "unrelated recommit\n")
    new = _git_text(repo, "rev-parse", "HEAD")

    grafts = _git_path(repo, "info/grafts")
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{new} {old}\n", encoding="ascii")
    assert _git_rc(repo, "merge-base", "--is-ancestor", old, new) == 0

    assert not git_ops._pending_head_covered_by_history(str(repo), old, new)


def test_pending_history_proof_rejects_uncovered_real_git_histories(
        xdg, tmp_path):
    """Real byte/history counterexamples cannot replace or cover pending state."""
    _, work = _clone_pair(tmp_path)
    base = _git_text(work, "rev-parse", "HEAD")
    _commit_file(work, "first-line.md", "first\n")
    assert git_ops.write_push_pending(str(work)) is True
    first_snapshot = git_ops.read_push_pending(str(work))

    subprocess.run(
        ["git", "-C", str(work), "reset", "--hard", base],
        check=True, capture_output=True)
    _commit_file(work, "second-line.md", "second\n")

    assert git_ops.write_push_pending(str(work)) is False
    assert git_ops.read_push_pending(str(work)) == first_snapshot

    def divergent_history(name, old_contents, new_contents):
        repo = _init_repo(tmp_path / name)
        _commit_file(repo, "proof.txt", "base\n")
        subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "old"],
                       check=True, capture_output=True)
        for index, content in enumerate(old_contents):
            if isinstance(content, bytes):
                (repo / "proof.txt").write_bytes(content)
            else:
                (repo / "proof.txt").write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-qam",
                            f"old {index}"], check=True, capture_output=True)
        old = _git_text(repo, "rev-parse", "HEAD")
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"],
                       check=True, capture_output=True)
        for index, content in enumerate(new_contents):
            if isinstance(content, bytes):
                (repo / "proof.txt").write_bytes(content)
            else:
                (repo / "proof.txt").write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-qam",
                            f"new {index}"], check=True, capture_output=True)
        return repo, old, _git_text(repo, "rev-parse", "HEAD")

    whitespace = divergent_history(
        "whitespace-proof", ("a b\n",), ("a\tb\n",))
    multiplicity = divergent_history(
        "multiplicity-proof",
        ("base\nx\n", "base\n", "base\nx\n"),
        ("base\nx\n", "base\n"))
    crlf = divergent_history(
        "crlf-proof", (b"line\r\n",), (b"line\n",))
    invalid_utf8 = divergent_history(
        "invalid-utf8-proof", (b"value:\x80\n",), (b"value:\x81\n",))
    sha256 = tmp_path / "sha256-proof"
    subprocess.run(
        ["git", "init", "-q", "--object-format=sha256",
         "--initial-branch=main", str(sha256)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sha256), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sha256), "config", "user.name", "T"],
                   check=True, capture_output=True)
    _commit_file(sha256, "full-oid.txt", "sha256\n")
    full = _git_text(sha256, "rev-parse", "HEAD")
    abbreviated = full[:40]
    identity = lambda head: {
        "key": "branch:main", "branch": "main", "head": head}
    observed = {
        "space-tab": git_ops._pending_head_covered_by_history(
            str(whitespace[0]), whitespace[1], whitespace[2]),
        "multiplicity": git_ops._pending_head_covered_by_history(
            str(multiplicity[0]), multiplicity[1], multiplicity[2]),
        "crlf-lf": git_ops._pending_head_covered_by_history(
            str(crlf[0]), crlf[1], crlf[2]),
        "invalid-utf8": git_ops._pending_head_covered_by_history(
            str(invalid_utf8[0]), invalid_utf8[1], invalid_utf8[2]),
        "sha256-prefix-identity": git_ops._validated_pending_identity(
            str(sha256), identity(abbreviated)) is not None,
        "sha256-prefix-helper": git_ops._pending_head_covered_by_history(
            str(sha256), abbreviated, abbreviated),
        "sha256-full": (
            git_ops._validated_pending_identity(
                str(sha256), identity(full)) is not None
            and git_ops._pending_head_covered_by_history(
                str(sha256), full, full)),
    }
    assert observed == {
        "space-tab": False, "multiplicity": False,
        "crlf-lf": False, "invalid-utf8": False,
        "sha256-prefix-identity": False, "sha256-prefix-helper": False,
        "sha256-full": True,
    }


def test_pending_write_rejects_replace_forged_reverse_ancestry(xdg, tmp_path):
    """A replace ref cannot make an older pending entry cover a new reset."""
    _, work = _clone_pair(tmp_path)
    base = _git_text(work, "rev-parse", "HEAD")
    _commit_file(work, "old-pending.md", "old pending\n")
    old = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))

    subprocess.run(["git", "-C", str(work), "reset", "--hard", base],
                   check=True, capture_output=True)
    _commit_file(work, "unrelated-new.md", "unrelated new\n")
    new = _git_text(work, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "-C", str(work), "replace", "--graft", old, new],
        check=True, capture_output=True)
    assert _git_rc(work, "merge-base", "--is-ancestor", new, old) == 0

    assert git_ops.write_push_pending(str(work)) is False
    assert git_ops.read_push_pending(str(work)) == snapshot


@pytest.mark.parametrize("mutation", (None, "target-entry", "other-entry"))
def test_pending_write_patch_rewrite_is_cas_safe(
        xdg, tmp_path, monkeypatch, mutation):
    """A patch rewrite advances once, but never overwrites a concurrent nonce."""
    work, main_key, other_key, old = _rewrite_pending_with_other_entry(tmp_path)
    race_key = main_key if mutation == "target-entry" else other_key
    if mutation:
        real_coverage = git_ops._pending_head_covered_by_history

        def prove_then_race(*args, **kwargs):
            covered = real_coverage(*args, **kwargs)
            entries = git_ops._pending_entries(git_ops.read_push_pending(str(work)))
            entries[race_key] = dict(entries[race_key], nonce="raced")
            git_ops._write_private_text(
                git_ops.push_pending_path(str(work)),
                git_ops._serialize_pending_entries(str(work), entries))
            return covered

        monkeypatch.setattr(
            git_ops, "_pending_head_covered_by_history", prove_then_race)

    assert git_ops.write_push_pending(str(work)) is (mutation is None)
    updated = git_ops._pending_entries(git_ops.read_push_pending(str(work)))
    if mutation:
        assert updated[race_key]["nonce"] == "raced"
        assert updated[main_key]["head"] == old[main_key]["head"]
    else:
        fields = ("remote", "destination", "reconcile_ref", "set_upstream",
                  "remote_fingerprint")
        assert updated[other_key] == old[other_key]
        assert updated[main_key]["head"] == _git_text(work, "rev-parse", "HEAD")
        assert updated[main_key]["nonce"] != old[main_key]["nonce"]
        assert all(updated[main_key].get(k) == old[main_key].get(k) for k in fields)


def test_worker_no_pending_is_noop(xdg, tmp_path):
    """pending 없으면 아무것도 안 하고 조용히 종료(push 시도 없음)."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")  # ahead 1 이지만 pending 없음
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 1  # push 하지 않았다 — pending 이 유일한 트리거


@pytest.mark.parametrize("blocker", _RECONCILE_BLOCKER_KINDS)
def test_worker_preserves_publication_state_for_unresolved_reconcile_artifact(
        xdg, tmp_path, blocker):
    """Detached fallback must leave remote/pending/warning intact while blocked."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "worker-must-stay-local.md", blocker)
    assert git_ops.write_push_pending(str(work)) is True
    warning_before = f"actionable warning before {blocker}"
    git_ops.write_sync_warning(str(work), warning_before)
    pending_before = git_ops.read_push_pending(str(work))
    remote_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    _install_reconcile_blocker(work, blocker)

    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert r.returncode == 0, r.stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == remote_before
    assert git_ops.read_push_pending(str(work)) == pending_before
    warning_after = git_ops.read_sync_warning(str(work))
    assert warning_after, "an unresolved blocker must not clear the warning"
    assert (warning_after == warning_before
            or "reconcile" in warning_after.lower()
            or "block" in warning_after.lower())


def test_worker_preserves_pending_from_another_branch(xdg, tmp_path):
    """branch A pending 을 둔 채 B에서 worker가 돌아도 A ledger를 지우지 않는다."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "session-a"],
                   check=True, capture_output=True)
    _commit_file(work, "session-a.md")
    assert git_ops.write_push_pending(str(work)) is True
    pending_a = git_ops.read_push_pending(str(work))

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0, r.stderr
    assert git_ops.read_push_pending(str(work)) == pending_a
    refs = _bare_git_text(origin, "for-each-ref", "--format=%(refname:short)",
                          "refs/heads")
    assert "session-a" not in refs


def test_foreground_success_does_not_clear_other_branch_pending(xdg, tmp_path):
    """B의 성공 auto-commit이 A의 미push pending/warning을 성공으로 오판하지 않는다."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "session-a"],
                   check=True, capture_output=True)
    _commit_file(work, "session-a.md")
    assert git_ops.write_push_pending(str(work)) is True
    pending_a = git_ops.read_push_pending(str(work))
    git_ops.write_sync_warning(str(work), "session-a remains unpublished")

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    _activate(work)
    main_file = work / "main-note.md"
    main_file.write_text("main publication\n", encoding="utf-8")
    r = _run_auto_commit(work, [main_file], xdg,
                         {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0, r.stderr
    assert git_ops.read_push_pending(str(work)) == pending_a
    assert "session-a" in git_ops.read_sync_warning(str(work))
    refs = _bare_git_text(origin, "for-each-ref", "--format=%(refname:short)",
                          "refs/heads")
    assert "session-a" not in refs


@pytest.mark.parametrize("history", ("unrelated", "external-rebase"))
def test_foreground_success_clears_patch_equivalent_rewritten_pending(
        xdg, tmp_path, history):
    """Publication preserves unrelated H1 but clears a patch-rewritten H1."""
    origin, work = _clone_pair(tmp_path)
    base = _git_text(work, "rev-parse", "HEAD")
    _commit_file(work, "old-pending.md", "unpublished old history\n")
    old_head = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    pending_before = git_ops.read_push_pending(str(work))
    if history == "unrelated":
        subprocess.run(["git", "-C", str(work), "reset", "--hard", base],
                       check=True, capture_output=True)
    else:
        _external_rebase_pending_head(
            origin, work, tmp_path / "foreground-other", prefix="foreground")
        key = git_ops.pending_entry_key_for_current_checkout(
            str(work), pending_before)
        entry = git_ops._pending_entries(pending_before)[key]
        current = git_ops._checkout_identity(str(work))
        assert git_ops.pending_entry_covered_by_publication(
            str(work), pending_before, key, current, entry)
        mutations = {
            "remote": "fork",
            "destination": "refs/heads/other",
            "reconcile_ref": "refs/remotes/origin/other",
            "remote_fingerprint": "0" * 64,
        }
        for field, value in mutations.items():
            changed_target = dict(entry, **{field: value})
            assert not git_ops.pending_entry_covered_by_publication(
                str(work), pending_before, key, current, changed_target)
    _activate(work)
    edited = work / "new-history.md"
    edited.write_text("new published history\n", encoding="utf-8")
    payload = _lease_payload("codex", f"session-{history}", f"tool-{history}")
    owner = git_ops.hook_edit_lease_owner(payload)
    assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True
    result = _run_auto_commit(
        work, [edited], xdg, {"TEAMMODE_DISABLE_PUSH_WORKER": "1"}, payload)

    assert result.returncode == 0, result.stderr
    published = _git_text(work, "rev-parse", "HEAD")
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == published
    assert _git_rc(work, "merge-base", "--is-ancestor", old_head, published) == 1
    expected_pending = pending_before if history == "unrelated" else ""
    assert git_ops.read_push_pending(str(work)) == expected_pending
    assert git_ops.end_hook_edit_lease(str(work), owner) is False


def test_pending_ledger_tracks_and_drains_multiple_branches(xdg, tmp_path):
    """A/B 실패가 한 ledger에서 공존하고 각 branch에서 자기 entry만 drain한다."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "session-a"],
                   check=True, capture_output=True)
    _commit_file(work, "session-a.md")
    assert git_ops.write_push_pending(str(work)) is True

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    _commit_file(work, "main-pending.md")
    assert git_ops.write_push_pending(str(work)) is True
    both = git_ops.read_push_pending(str(work))

    r_main = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r_main.returncode == 0, r_main.stderr
    remaining = git_ops.read_push_pending(str(work))
    assert remaining and remaining != both

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "session-a"],
                   check=True, capture_output=True)
    r_a = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r_a.returncode == 0, r_a.stderr
    assert git_ops.read_push_pending(str(work)) == ""
    refs = _bare_git_text(origin, "for-each-ref", "--format=%(refname:short)",
                          "refs/heads")
    assert "main" in refs and "session-a" in refs


def test_pending_entry_survives_verified_branch_rename(xdg, tmp_path):
    """branch rename 뒤에도 기록 당시 immutable destination으로만 publish한다."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "session-old"],
                   check=True, capture_output=True)
    _commit_file(work, "renamed-session.md")
    failed_head = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    assert failed_head in git_ops.read_push_pending(str(work))

    subprocess.run(["git", "-C", str(work), "branch", "-m", "session-new"],
                   check=True, capture_output=True)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0, r.stderr
    assert git_ops.read_push_pending(str(work)) == ""
    assert _bare_git_text(origin, "show", "session-old:renamed-session.md") == "x"
    refs = _bare_git_text(origin, "for-each-ref", "--format=%(refname:short)",
                          "refs/heads")
    assert "session-new" not in refs


def test_failed_commit_identity_binds_pending_after_checkout_changes(xdg, tmp_path):
    """push 실패 결과의 branch/HEAD는 이후 checkout이 바뀌어도 ledger 대상이 된다."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "session-a"],
                   check=True, capture_output=True)
    # Keep the configured endpoint stable while making it temporarily
    # unreachable.  Restoring a differently fingerprinted URL must now be
    # rejected by design, so this fixture restores the same endpoint instead.
    parked_origin = tmp_path / "parked-origin.git"
    origin.rename(parked_origin)
    (work / "session-a.md").write_text("unpublished\n", encoding="utf-8")

    result = git_ops.do_commit(
        str(work), "chore(teammode): auto-commit identity", push=True,
        paths=["session-a.md"])
    failed_head = _git_text(work, "rev-parse", "HEAD")
    assert result.committed is True and result.pushed is False
    assert result.pending_identity == {
        "key": "branch:session-a", "branch": "session-a", "head": failed_head}

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    assert git_ops.write_push_pending(
        str(work), result.pending_identity) is True
    snapshot = git_ops.read_push_pending(str(work))
    assert "branch:session-a" in snapshot and failed_head in snapshot
    assert "branch:main" not in snapshot

    # 현재 main worker는 다른 branch entry를 절대 성공/clear로 오판하지 않는다.
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    assert git_ops.read_push_pending(str(work)) == snapshot
    parked_origin.rename(origin)
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "session-a"],
                   check=True, capture_output=True)
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    assert git_ops.read_push_pending(str(work)) == ""
    assert _bare_git_text(origin, "show", "session-a:session-a.md") == "unpublished"


def test_legacy_pending_recovers_unique_ahead_branch_on_upgrade(xdg, tmp_path):
    """destination 증거가 없는 v1 ledger는 unique branch여도 자동 publish하지 않는다."""
    _, work = _clone_pair(tmp_path)
    _commit_auto_session(work, "legacy-session.md")
    legacy = '{"root":"legacy","nonce":"old-v1"}'
    pending = Path(git_ops.push_pending_path(str(work)))
    pending.parent.mkdir(parents=True, mode=0o700)
    pending.write_text(legacy, encoding="utf-8")
    pending.chmod(0o600)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    assert git_ops.read_push_pending(str(work)) != ""
    assert git_ops.ahead_behind(str(work))[0] == 1
    assert "target" in git_ops.read_sync_warning(str(work)).lower()


def test_stale_legacy_pending_never_publishes_unrelated_private_branch(
        xdg, tmp_path):
    """branch 증거 없는 stale v1 marker가 일반 코드/비공개 실험을 원격에 올리면 안 된다."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb",
                    "private-experiment", "origin/main"],
                   check=True, capture_output=True)
    _commit_auto_session(work, "looks-safe.md")
    _commit_file(work, "private-experiment.py", "do not publish\n")
    legacy = '{"root":"legacy","nonce":"stale-after-old-push"}'
    pending = Path(git_ops.push_pending_path(str(work)))
    pending.parent.mkdir(parents=True, mode=0o700)
    pending.write_text(legacy, encoding="utf-8")
    pending.chmod(0o600)

    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0, r.stderr
    assert git_ops.read_push_pending(str(work)) == legacy
    refs = _bare_git_text(origin, "for-each-ref", "--format=%(refname:short)",
                          "refs/heads")
    assert "private-experiment" not in refs
    assert "unknown" in git_ops.read_sync_warning(str(work))


def test_legacy_pending_preserves_ambiguous_multiple_ahead_branches(xdg, tmp_path):
    """v1 ledger 대상이 두 ahead branch 중 어느 쪽인지 모르면 자동 push/clear 금지."""
    _, work = _clone_pair(tmp_path)
    _commit_auto_session(work, "main-ahead.md")
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "other-ahead",
                    "origin/main"], check=True, capture_output=True)
    _commit_auto_session(work, "other-ahead.md")
    legacy = '{"root":"legacy","nonce":"ambiguous-v1"}'
    pending = Path(git_ops.push_pending_path(str(work)))
    pending.parent.mkdir(parents=True, mode=0o700)
    pending.write_text(legacy, encoding="utf-8")
    pending.chmod(0o600)
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    assert git_ops.read_push_pending(str(work)) == legacy
    assert "unknown" in git_ops.read_sync_warning(str(work))


def test_legacy_unique_ahead_detection_fails_closed_on_branch_probe_error(
        tmp_path, monkeypatch):
    """한 branch 판정 실패를 ahead=0으로 접어 다른 branch에 오바인딩하지 않는다."""
    def fake_run_git(args, timeout, **_kwargs):
        if "for-each-ref" in args:
            return 0, "branch-a\torigin/branch-a\nbranch-b\torigin/branch-b\n", ""
        if any("origin/branch-a..." in str(arg) for arg in args):
            raise subprocess.TimeoutExpired(cmd="git rev-list", timeout=timeout)
        return 0, "0\t1\n", ""

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert git_ops._unique_local_ahead_branch(str(tmp_path / "team")) == ""


def test_migrated_v2_legacy_entry_recovers_when_target_becomes_unique(
        xdg, tmp_path):
    """v2에 감싼 targetless legacy도 checkout에서 destination을 추측하지 않는다."""
    _, work = _clone_pair(tmp_path)
    _commit_auto_session(work, "main-ahead.md")
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "other-ahead",
                    "origin/main"], check=True, capture_output=True)
    _commit_auto_session(work, "other-ahead.md")
    legacy = '{"root":"legacy","nonce":"wrapped-v1"}'
    pending = Path(git_ops.push_pending_path(str(work)))
    pending.parent.mkdir(parents=True, mode=0o700)
    pending.write_text(legacy, encoding="utf-8")
    pending.chmod(0o600)

    # 현재 branch 실패가 새 entry를 upsert하면서 unresolved v1도 v2 legacy entry로 보존.
    assert git_ops.write_push_pending(str(work)) is True
    wrapped = git_ops.read_push_pending(str(work))
    assert "legacy:" in wrapped and "branch:other-ahead" in wrapped
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    remaining = git_ops.read_push_pending(str(work))
    assert remaining != ""  # main 대상 legacy만 잔존
    assert "branch:other-ahead" not in remaining

    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    remaining = git_ops.read_push_pending(str(work))
    assert remaining != "" and '"branch:main"' in remaining
    assert '"destination"' not in remaining
    assert git_ops.ahead_behind(str(work))[0] == 1
    assert "target" in git_ops.read_sync_warning(str(work)).lower()


def test_v2_legacy_merges_into_existing_unique_branch_entry(xdg, tmp_path):
    """unique target entry가 이미 있으면 legacy를 그 publication에 흡수해 고립 방지."""
    origin, work = _clone_pair(tmp_path)
    _commit_auto_session(work, "main-ahead.md")
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "other-ahead",
                    "origin/main"], check=True, capture_output=True)
    _commit_auto_session(work, "other-ahead.md")
    legacy = '{"root":"legacy","nonce":"merge-v1"}'
    pending = Path(git_ops.push_pending_path(str(work)))
    pending.parent.mkdir(parents=True, mode=0o700)
    pending.write_text(legacy, encoding="utf-8")
    pending.chmod(0o600)
    assert git_ops.write_push_pending(str(work)) is True  # legacy + other entry

    # main을 별도로 publish해 other만 unique-ahead로 만든다. ledger는 의도적으로 보존.
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-q"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "other-ahead"],
                   check=True, capture_output=True)

    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    assert git_ops.read_push_pending(str(work)) == ""
    assert _bare_git_text(
        origin, "show",
        "other-ahead:memory/team/sessions/tester/other-ahead.md") == "session"
    assert _git_text(
        work, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{upstream}") == "origin/other-ahead"
    assert git_ops.ahead_behind(str(work)) == (0, 0)


def test_worker_non_ff_keeps_pending_writes_marker(xdg, tmp_path):
    """non-ff: 복구 없이 sync-warning 기록, pending 유지(정합은 세션 시작에 위임)."""
    origin, work = _clone_pair(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "O"],
                   capture_output=True)
    _commit_file(other, "theirs.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"], capture_output=True)

    _commit_file(work, "mine.md")
    git_ops.write_push_pending(str(work))
    head_before = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0  # 실패도 비치명 종료
    assert git_ops.read_push_pending(str(work)) != "", "non-ff 인데 pending 을 지웠다"
    assert "non-fast-forward" in git_ops.read_sync_warning(str(work))
    head_after = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    assert head_after == head_before, "worker 가 로컬 히스토리를 건드렸다(계약 위반)"


def test_pending_reconcile_merges_remote_without_rewriting_recorded_head(
        xdg, tmp_path):
    """Session recovery preserves H1 ancestry, advances the ledger, then publishes."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "pending-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)

    other = _clone_other(origin, tmp_path / "pending-merge-other")
    _commit_file(other, "remote-r.md", "remote R\n")
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)
    remote_r = _bare_git_text(origin, "rev-parse", "refs/heads/main")

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert result.ok and result.action == "merged", result.detail
    merged = _git_text(work, "rev-parse", "HEAD")
    assert merged not in {pending_h1, remote_r}
    assert subprocess.run(
        ["git", "-C", str(work), "merge-base", "--is-ancestor",
         pending_h1, merged], capture_output=True).returncode == 0
    assert subprocess.run(
        ["git", "-C", str(work), "merge-base", "--is-ancestor",
         remote_r, merged], capture_output=True).returncode == 0
    advanced = git_ops.read_push_pending(str(work))
    assert git_ops._pending_entries(advanced)[key]["head"] == merged

    worker = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert worker.returncode == 0
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == merged
    assert git_ops.read_push_pending(str(work)) == ""


@pytest.mark.parametrize("legacy_merge_tree", [False, True])
def test_pending_disjoint_logs_ignore_unrelated_parent_macl_and_preserve_xattr(
        xdg, tmp_path, monkeypatch, legacy_merge_tree):
    """A remote member log must not make Git inspect another member's parent."""
    origin, work = _clone_pair(tmp_path)
    alice_parent = work / "memory" / "team" / "sessions" / "alice"
    alice_parent.mkdir(parents=True)
    alice_log = alice_parent / "2026-07-20.md"
    alice_log.write_text("alice local log\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(work), "add", str(alice_log.relative_to(work))],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-qm", "alice local log"],
        check=True, capture_output=True)
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    git_ops.write_sync_warning(str(work), "remote advanced before recovery")

    attr_name = ("com.tm-mode.unrelated-parent"
                 if sys.platform == "darwin"
                 else "user.tm-mode.unrelated-parent")
    expected_xattr = b"unrelated parent metadata\x00\xff"

    def read_parent_xattr():
        if hasattr(os, "getxattr"):
            return os.getxattr(
                alice_parent, attr_name, follow_symlinks=False)
        read = subprocess.run(
            ["/usr/bin/xattr", "-p", "-x", "--", attr_name,
             str(alice_parent)], capture_output=True, text=True, check=False)
        assert read.returncode == 0, read.stderr
        return bytes.fromhex("".join(read.stdout.split()))

    if all(hasattr(os, name) for name in ("setxattr", "getxattr")):
        try:
            os.setxattr(
                alice_parent, attr_name, expected_xattr,
                follow_symlinks=False)
        except OSError as exc:
            pytest.skip(f"filesystem xattrs unavailable: {exc}")
    elif sys.platform == "darwin" and Path("/usr/bin/xattr").is_file():
        wrote = subprocess.run(
            ["/usr/bin/xattr", "-w", "-x", "--", attr_name,
             expected_xattr.hex(), str(alice_parent)],
            capture_output=True, text=True, check=False)
        if wrote.returncode != 0:
            pytest.skip(f"filesystem xattrs unavailable: {wrote.stderr}")
    else:
        pytest.skip("filesystem xattrs unavailable")
    before_xattr = read_parent_xattr()

    other = _clone_other(origin, tmp_path / "pending-disjoint-bob")
    bob_log = other / "memory" / "team" / "sessions" / "bob" / "2026-07-20.md"
    bob_log.parent.mkdir(parents=True)
    bob_log.write_text("bob remote log\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(other), "add", str(bob_log.relative_to(other))],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(other), "commit", "-qm", "bob remote log"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)

    real_xattr_names = git_ops._nofollow_xattr_names

    def unrelated_parent_macl(path):
        if Path(path) == alice_parent:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    monkeypatch.setattr(
        git_ops, "_nofollow_xattr_names", unrelated_parent_macl)
    if legacy_merge_tree:
        real_run_bound_git = git_ops._run_bound_git

        def emulate_legacy_merge_tree(
                team_root, txn, args, timeout, **kwargs):
            if args[:2] == ["merge-tree", "--write-tree"]:
                return 129, "", "error: unknown option `write-tree'\nusage: git merge-tree"
            return real_run_bound_git(
                team_root, txn, args, timeout, **kwargs)

        monkeypatch.setattr(
            git_ops, "_run_bound_git", emulate_legacy_merge_tree)

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert result.ok and result.action == "merged", result.detail
    merged = _git_text(work, "rev-parse", "HEAD")
    assert _git_rc(work, "merge-base", "--is-ancestor", pending_h1, merged) == 0
    assert alice_log.read_text(encoding="utf-8") == "alice local log\n"
    local_bob_log = work / bob_log.relative_to(other)
    assert local_bob_log.read_text(encoding="utf-8") == "bob remote log\n"
    assert read_parent_xattr() == before_xattr

    worker = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert worker.returncode == 0
    assert git_ops.read_push_pending(str(work)) == ""
    assert git_ops.read_sync_warning(str(work)) == ""
    assert read_parent_xattr() == before_xattr


def test_legacy_merge_tree_fallback_rejects_upstream_directory_rename(
        xdg, tmp_path, monkeypatch):
    """An upstream rename can relocate a local-only path via directory rename."""
    origin, work = _clone_pair(tmp_path)
    base = work / "old" / "base.txt"
    base.parent.mkdir()
    base.write_text("base\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(work), "add", "old/base.txt"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-qm", "directory rename base"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "push", "-q"],
        check=True, capture_output=True)
    other = _clone_other(origin, tmp_path / "legacy-directory-rename-other")

    local_only = work / "old" / "local.txt"
    local_only.write_text("local pending\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(work), "add", "old/local.txt"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-qm", "local pending path"],
        check=True, capture_output=True)
    pending_head = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    index_path = _git_path(work, "index")
    index_before = index_path.read_bytes()

    (other / "new").mkdir()
    subprocess.run(
        ["git", "-C", str(other), "mv", "old/base.txt", "new/base.txt"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(other), "commit", "-qm", "rename directory"],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)

    real_xattr_names = git_ops._nofollow_xattr_names

    def local_path_xattrs(path):
        if Path(path) == local_only:
            return (b"com.apple.macl",)
        return real_xattr_names(path)

    monkeypatch.setattr(git_ops, "_nofollow_xattr_names", local_path_xattrs)
    real_run_bound_git = git_ops._run_bound_git
    merge_attempts = []

    def emulate_legacy_merge_tree(
            team_root, txn, args, timeout, **kwargs):
        if args[:2] == ["merge-tree", "--write-tree"]:
            return 129, "", "error: unknown option `write-tree'\nusage: git merge-tree"
        if args and args[0] == "merge":
            merge_attempts.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(
        git_ops, "_run_bound_git", emulate_legacy_merge_tree)

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert result.ok is False
    assert "mutation-path proof unavailable" in result.detail
    assert merge_attempts == []
    assert _git_text(work, "rev-parse", "HEAD") == pending_head
    assert index_path.read_bytes() == index_before
    assert local_only.read_text(encoding="utf-8") == "local pending\n"
    assert not (work / "new" / "local.txt").exists()
    assert git_ops.read_push_pending(str(work)) == snapshot


def test_pending_reconcile_ledger_failure_is_retryable_without_history_loss(
        xdg, tmp_path, monkeypatch):
    """A post-merge ledger write failure leaves old H1 as an ancestor for retry."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "pending-ledger-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    other = _clone_other(origin, tmp_path / "pending-ledger-other")
    _commit_file(other, "remote-ledger-r.md", "remote R\n")
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)

    real_advance = git_ops._advance_push_pending_if_unchanged
    monkeypatch.setattr(
        git_ops, "_advance_push_pending_if_unchanged",
        lambda *_args, **_kwargs: False)
    first = git_ops.reconcile_current_pending(str(work), snapshot, key)
    merged = _git_text(work, "rev-parse", "HEAD")

    assert not first.ok and first.action == "pending-update-failed"
    assert git_ops.read_push_pending(str(work)) == snapshot
    assert subprocess.run(
        ["git", "-C", str(work), "merge-base", "--is-ancestor",
         pending_h1, merged], capture_output=True).returncode == 0

    monkeypatch.setattr(
        git_ops, "_advance_push_pending_if_unchanged", real_advance)
    second = git_ops.reconcile_current_pending(str(work), snapshot, key)
    assert second.ok and second.action == "ahead-only", second.detail
    advanced = git_ops.read_push_pending(str(work))
    assert git_ops._pending_entries(advanced)[key]["head"] == merged
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    assert git_ops.read_push_pending(str(work)) == ""


def test_pending_reconcile_conflict_rolls_back_and_preserves_ledger(
        xdg, tmp_path, monkeypatch):
    origin, work = _clone_pair(tmp_path)
    shared = work / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "shared.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-qm", "shared base"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q"], check=True)
    other = _clone_other(origin, tmp_path / "pending-conflict-other")

    shared.write_text("local H1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "commit", "-qam", "local H1"], check=True)
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    index_path = _git_path(work, "index")
    index_before = index_path.read_bytes()
    (other / "shared.txt").write_text("remote R\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "commit", "-qam", "remote R"], check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    merge_attempts = []
    real_run_bound_git = git_ops._run_bound_git

    def observe_merge(team_root, txn, args, timeout, **kwargs):
        if args and args[0] == "merge":
            merge_attempts.append(tuple(args))
        return real_run_bound_git(
            team_root, txn, args, timeout, **kwargs)

    monkeypatch.setattr(git_ops, "_run_bound_git", observe_merge)

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert not result.ok and result.action == "conflict"
    assert merge_attempts, "conflict fixture never crossed the mutation boundary"
    assert _git_text(work, "rev-parse", "HEAD") == pending_h1
    assert (work / "shared.txt").read_text(encoding="utf-8") == "local H1\n"
    assert index_path.read_bytes() == index_before
    assert git_ops.read_push_pending(str(work)) == snapshot
    assert not _git_path(work, "MERGE_HEAD").exists()
    assert not _git_path(work, "rebase-merge").exists()
    assert not _git_path(work, "rebase-apply").exists()


def test_pending_reconcile_defers_while_any_edit_lease_is_active(xdg, tmp_path):
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "pending-active-edit.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    other = _clone_other(origin, tmp_path / "pending-active-edit-other")
    _commit_file(other, "remote-active-edit.md", "remote R\n")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    owner = "a" * 64
    assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert not result.ok and result.action == "deferred"
    assert _git_text(work, "rev-parse", "HEAD") == pending_h1
    assert git_ops.read_push_pending(str(work)) == snapshot
    assert git_ops.end_hook_edit_lease(str(work), owner) is True


def test_pending_reconcile_rejects_changed_remote_binding(xdg, tmp_path):
    origin, work = _clone_pair(tmp_path)
    replacement = _init_repo(tmp_path / "pending-replacement.git", bare=True)
    _commit_file(work, "pending-target-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    origin_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    subprocess.run(
        ["git", "-C", str(work), "remote", "set-url", "origin",
         str(replacement)], check=True)

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert not result.ok and result.action == "pending-target-invalid"
    assert _git_text(work, "rev-parse", "HEAD") == pending_h1
    assert git_ops.read_push_pending(str(work)) == snapshot
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == origin_before
    assert _bare_git_text(
        replacement, "for-each-ref", "--format=%(refname)", "refs/heads") == ""


def test_pending_reconcile_fetches_the_captured_push_endpoint(xdg, tmp_path):
    """A separate pushurl is the source of truth for destination reconciliation."""
    origin, work = _clone_pair(tmp_path)
    fork = tmp_path / "pending-pushurl-fork.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(origin), str(fork)],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "remote", "set-url", "--push", "origin",
         str(fork)], check=True)
    origin_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    _commit_file(work, "pending-pushurl-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    other = _clone_other(fork, tmp_path / "pending-pushurl-other")
    _commit_file(other, "fork-remote-r.md", "fork R\n")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    fork_r = _bare_git_text(fork, "rev-parse", "refs/heads/main")

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert result.ok and result.action == "merged", result.detail
    merged = _git_text(work, "rev-parse", "HEAD")
    for ancestor in (pending_h1, fork_r):
        assert subprocess.run(
            ["git", "-C", str(work), "merge-base", "--is-ancestor",
             ancestor, merged], capture_output=True).returncode == 0
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    assert _bare_git_text(fork, "rev-parse", "refs/heads/main") == merged
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == origin_before


@pytest.mark.parametrize("history", ("descendant", "external-rebase"))
def test_pending_reconcile_advances_to_current_descendant_before_worker(
        xdg, tmp_path, history):
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "pending-descendant-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    if history == "descendant":
        _commit_file(work, "pending-descendant-h2.md", "local H2\n")
    else:
        old_head, rewritten_h1, _remote_r = _external_rebase_pending_head(
            origin, work, tmp_path / "pending-descendant-other",
            prefix="pending-descendant")
        assert old_head == pending_h1
        assert rewritten_h1 == _git_text(work, "rev-parse", "HEAD")
    local_h2 = _git_text(work, "rev-parse", "HEAD")

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert result.ok and result.action == "ahead-only", result.detail
    advanced = git_ops.read_push_pending(str(work))
    assert git_ops._pending_entries(advanced)[key]["head"] == local_h2
    assert _run_worker(work, {"XDG_STATE_HOME": str(xdg)}).returncode == 0
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == local_h2
    assert git_ops.read_push_pending(str(work)) == ""


@pytest.mark.parametrize("width", (40, 64))
def test_pending_history_coverage_requires_exact_full_oids(
        tmp_path, monkeypatch, width):
    old, parent, current = "a" * width, "b" * width, "c" * width
    calls = []

    def git(args, timeout, input_text=None, input_bytes=None, **_kwargs):
        calls.append((args, input_text, input_bytes))
        if "rev-parse" in args:
            return 0, f"{args[-1].split('^')[0]}\n", ""
        if "merge-base" in args:
            return 1, "", ""
        if "rev-list" in args:
            commit = old if args[-1] == f"{current}..{old}" else current
            return 0, f"{commit} {parent}\n", ""
        if "diff-tree" in args:
            return 0, input_bytes, b""
        commit = input_bytes.decode("ascii").strip()
        return 0, f"{'d' * width} {commit}\n".encode("ascii"), b""

    monkeypatch.setattr(git_ops, "run_git", git)
    assert git_ops._pending_head_covered_by_history(
        str(tmp_path), old, current, timeout=2)
    assert any("patch-id" in args and "--verbatim" in args
               for args, _text, _bytes in calls)
    calls.clear()
    assert all(not git_ops._pending_head_covered_by_history(
        str(tmp_path), "a" * bad, current, timeout=2)
        for bad in (39, 41, 63, 65))
    assert not calls


@pytest.mark.parametrize("case", (
    "ancestor", "merge", "empty", "partial", "missing", "timeout",
    "oserror", "subprocess-error", "malformed-rev", "malformed-patch",
    "duplicate", "missing-patch"))
def test_pending_history_coverage_fails_closed(case, tmp_path, monkeypatch):
    old, parent, current, first = (c * 40 for c in "abcd")

    def git(args, timeout, input_text=None, input_bytes=None, **_kwargs):
        error = {
            "timeout": subprocess.TimeoutExpired("git", timeout),
            "oserror": OSError("exec"),
            "subprocess-error": subprocess.SubprocessError("probe"),
        }.get(case)
        if error:
            raise error
        if "rev-parse" in args:
            oid = args[-1].split("^")[0]
            return (128, "", "missing") if case == "missing" else (
                0, f"{oid}\n", "")
        if "merge-base" in args:
            return (0 if case == "ancestor" else 1), "", ""
        if "rev-list" in args:
            old_side = args[-1] == f"{current}..{old}"
            output = (f"{current} {parent}\n" if not old_side else {
                "merge": f"{old} {parent} {first}\n",
                "malformed-rev": f"{old[:12]} {parent}\n",
                "partial": f"{first} {parent}\n{old} {first}\n",
            }.get(case, f"{old} {parent}\n"))
            return 0, output, ""
        if "diff-tree" in args:
            return 0, input_bytes, b""
        commits = input_bytes.decode("ascii").splitlines()
        if old in commits:
            if case in {"empty", "missing-patch"}:
                return 0, b"", b""
            if case == "malformed-patch":
                return 0, f"{old[:12]} {old}\n".encode("ascii"), b""
            if case == "duplicate":
                output = f"{'e' * 40} {old}\n{'e' * 40} {old}\n"
                return 0, output.encode("ascii"), b""
        output = "".join(f"{'e' * 40} {commit}\n" for commit in commits)
        return 0, output.encode("ascii"), b""

    monkeypatch.setattr(git_ops, "run_git", git)
    assert git_ops._pending_head_covered_by_history(
        str(tmp_path), old, current, timeout=2) is (case == "ancestor")


def test_pending_merge_respects_required_commit_signing_and_rolls_back(
        xdg, tmp_path):
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "pending-sign-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    snapshot = git_ops.read_push_pending(str(work))
    key = git_ops.pending_entry_key_for_current_checkout(str(work), snapshot)
    other = _clone_other(origin, tmp_path / "pending-sign-other")
    _commit_file(other, "remote-sign-r.md", "remote R\n")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    signer = tmp_path / "reject-signing"
    signer.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    signer.chmod(0o700)
    subprocess.run(
        ["git", "-C", str(work), "config", "commit.gpgSign", "true"], check=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "gpg.program", str(signer)], check=True)

    result = git_ops.reconcile_current_pending(str(work), snapshot, key)

    assert not result.ok and result.action == "conflict"
    assert _git_text(work, "rev-parse", "HEAD") == pending_h1
    assert git_ops.read_push_pending(str(work)) == snapshot
    assert not _git_path(work, "MERGE_HEAD").exists()


def test_worker_non_ff_preserves_actionable_foreground_warning(xdg, tmp_path):
    """plain worker의 generic non-ff가 foreground rebase/dirty 사유를 덮지 않는다."""
    origin, work = _clone_pair(tmp_path)
    other = _clone_other(origin, tmp_path / "other-warning")
    _commit_file(other, "theirs-warning.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"],
                   check=True, capture_output=True)
    _commit_file(work, "mine-warning.md")
    assert git_ops.write_push_pending(str(work)) is True
    actionable = "committed; rebase deferred: dirty paths overlap upstream changes"
    git_ops.write_sync_warning(str(work), actionable)

    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})

    assert r.returncode == 0
    assert git_ops.read_push_pending(str(work)) != ""
    assert git_ops.read_sync_warning(str(work)) == actionable


def test_worker_non_ff_marker_content_english_for_en_locale_team(xdg, tmp_path):
    """i18n(적대검수 — B 지적, FIX-REQUIRED 항목1): push-worker.py 의 sync-warning
    마커도 session-start 의 hook_ss_sync_warn(en-locale) 의 {warn} 자리에 그대로
    삽입되므로, 마커 CONTENT 자체가 en 팀에선 영어여야 한다(session-start/auto-commit
    마커 수정과 동일 클래스의 함정 — test_conflict_marker_content_english_for_en_locale_team
    패턴 미러).
    """
    import json as _json
    import re
    origin, work = _clone_pair(tmp_path)
    (work / "team.config.json").write_text(
        _json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "O"],
                   capture_output=True)
    _commit_file(other, "theirs.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"], capture_output=True)

    _commit_file(work, "mine.md")
    git_ops.write_push_pending(str(work))
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    marker = git_ops.read_sync_warning(str(work))
    assert "non-fast-forward" in marker
    assert "delegated to session-start reconcile" in marker
    assert not re.search(r"[가-힣]", marker), f"en 팀 마커 내용에 한글 섞임: {marker!r}"


def test_worker_reads_one_pending_snapshot_per_drain_loop(tmp_path, monkeypatch):
    """worker 는 한 loop 안에서 같은 ledger snapshot 을 중복 read/lock 하지 않는다."""
    import importlib.util
    from types import SimpleNamespace

    class FakeGitOps:
        NET_TIMEOUT = 10
        DEFAULT_TIMEOUT = 2

        def __init__(self):
            self.content = "nonce-1"
            self.reads = 0

        def push_pending_path(self, _root):
            return str(tmp_path / "pending")

        def _read(self):
            self.reads += 1
            return self.content

        def read_push_pending(self, _root):
            return self._read()

        def read_push_pending_state(self, _root):
            return SimpleNamespace(content=self._read(), available=True)

        def bind_legacy_pending_to_current_checkout(self, _root, snapshot):
            return snapshot

        def push_pending_entry(self, _root, _snapshot, _key, _timeout):
            return True, "pushed"

        def pending_entry_key_for_current_checkout(self, _root, snapshot):
            return "branch:test" if snapshot else ""

        def pending_target_summary(self, _snapshot, _root=None):
            return "branch test"

        def clear_push_pending_if_unchanged(
                self, _root, snapshot, _target_key=None):
            assert snapshot == self.content
            self.content = ""
            return True

        def clear_sync_warning(self, _root):
            return None

        def clear_sync_warning_after_pending_publication(
                self, _root, _snapshot, _target_key):
            return self.content == ""

        def write_sync_warning(self, _root, _detail):
            return None

    fake = FakeGitOps()
    monkeypatch.setitem(sys.modules, "git_ops", fake)
    spec = importlib.util.spec_from_file_location("push_worker_once", WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    assert worker.main(["--root", str(tmp_path / "team")]) == 0
    assert fake.reads == 2  # one nonempty loop + one empty loop that exits


def test_worker_clears_exact_pending_if_checkout_changes_during_push(
        tmp_path, monkeypatch):
    """exact stored OID push 성공은 concurrent checkout 변경과 무관하게 CAS clear한다."""
    import importlib.util
    from types import SimpleNamespace

    class FakeGitOps:
        NET_TIMEOUT = 10
        DEFAULT_TIMEOUT = 2

        def __init__(self):
            self.current_key = "branch:a"
            self.content = "snapshot-a"
            self.clears = 0
            self.warning = ""

        def push_pending_path(self, _root):
            return str(tmp_path / "pending")

        def read_push_pending_state(self, _root):
            return SimpleNamespace(content=self.content, available=True)

        def bind_legacy_pending_to_current_checkout(self, _root, snapshot):
            return snapshot

        def pending_entry_key_for_current_checkout(self, _root, _snapshot):
            return self.current_key

        def pending_target_summary(self, _snapshot, _root=None):
            return "branch a"

        def push_pending_entry(self, _root, _snapshot, _key, _timeout):
            self.current_key = "branch:b"
            return True, "pushed"

        def clear_push_pending_if_unchanged(self, *_args):
            self.clears += 1
            self.content = ""
            return True

        def clear_sync_warning_after_pending_publication(
                self, _root, _snapshot, _target_key):
            return True

        def write_sync_warning(self, _root, detail):
            self.warning = detail

    fake = FakeGitOps()
    monkeypatch.setitem(sys.modules, "git_ops", fake)
    spec = importlib.util.spec_from_file_location("push_worker_switch", WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    assert worker.main(["--root", str(tmp_path / "team")]) == 0
    assert fake.clears == 1
    assert fake.warning == ""


def test_worker_drains_new_pending_written_during_push(xdg, tmp_path):
    """drain: push 성공 후 ahead 가 남아 있으면(새 커밋) 이어서 push — 잔여 0 까지."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    _commit_file(work, "b.md")  # ahead 2 — plain push 한 번에 다 나가긴 하지만
    git_ops.write_push_pending(str(work))
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 0
    assert git_ops.read_push_pending(str(work)) == ""


def test_worker_lock_single_instance(xdg, tmp_path):
    """lock 파일이 살아 있으면 두 번째 worker 는 즉시 조용히 종료(중복 push 방지)."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    git_ops.write_push_pending(str(work))
    # lock 선점 재현 — worker 와 같은 경로 규약으로 직접 생성
    lock = Path(git_ops.push_pending_path(str(work)) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("live", encoding="utf-8")
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    # lock 이 살아 있어 push 하지 않았고 pending 유지
    assert git_ops.read_push_pending(str(work)) != ""
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 1


# ── auto-commit 전경 publication + worker fallback ──────────────────

AUTO_COMMIT = REPO / "infra" / "hooks" / "auto-commit.py"


def _run_auto_commit(
        root: Path, files: list, xdg: Path, extra_env: dict | None = None,
        payload_extra: dict | None = None):
    import json as _json
    env = os.environ.copy()
    env["TEAMMODE_HOME"] = str(root)
    env["XDG_STATE_HOME"] = str(xdg)
    env.update(extra_env or {})
    canonical = {"event": "PostToolUse", "action": "file_edit",
                 "files": [str(f) for f in files]}
    canonical.update(payload_extra or {})
    payload = _json.dumps(canonical)
    return subprocess.run([sys.executable, str(AUTO_COMMIT)],
                          input=payload, capture_output=True, text=True,
                          env=env, timeout=60)


def _activate(root: Path) -> None:
    (root / ".teammode-active").write_text("on", encoding="utf-8")


def test_auto_commit_surfaces_precommit_reconcile_blocker(xdg, tmp_path):
    """A deferred edit must be visible even though no pending commit exists."""
    origin, work = _clone_pair(tmp_path)
    _activate(work)
    edited = work / "blocked-edit.md"
    edited.write_text("must remain uncommitted\n", encoding="utf-8")
    head_before = _git_text(work, "rev-parse", "HEAD")
    remote_before = _bare_git_text(origin, "rev-parse", "refs/heads/main")
    tx_dir = work / ".git" / ".tm-mode-reconcile-hook-stale"
    tx_dir.mkdir()

    r = _run_auto_commit(
        work, [edited], xdg, {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})

    assert r.returncode == 0
    assert "auto-commit" in r.stderr.lower()
    assert "deferred" in r.stderr.lower() or "보류" in r.stderr
    assert "blocker" in r.stderr
    warning = git_ops.read_sync_warning(str(work))
    assert "auto-commit" in warning.lower() and "blocker" in warning
    assert _git_text(work, "rev-parse", "HEAD") == head_before
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == remote_before
    assert "?? blocked-edit.md" in _git_text(
        work, "status", "--porcelain=v1")
    assert git_ops.read_push_pending(str(work)) == ""


def test_auto_commit_pushes_origin_without_pending(xdg, tmp_path):
    """정상 경로는 전경에서 origin publication 을 끝내고 fallback 상태를 남기지 않는다."""
    origin, work = _clone_pair(tmp_path)
    _activate(work)
    f = work / "memory-note.md"
    f.write_text("메모", encoding="utf-8")
    r = _run_auto_commit(work, [f], xdg,
                         {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0, r.stderr
    log = subprocess.run(["git", "-C", str(work), "log", "--oneline", "-1"],
                         capture_output=True, text=True).stdout
    assert "auto-commit" in log
    ahead, behind = git_ops.ahead_behind(str(work))
    assert (ahead, behind) == (0, 0)
    assert _bare_git_text(origin, "show", "HEAD:memory-note.md") == "메모"
    assert git_ops.read_push_pending(str(work)) == ""
    assert git_ops.read_sync_warning(str(work)) == ""


def test_auto_commit_defers_non_ff_without_mutating_dirty_file(xdg, tmp_path):
    """다른 clone이 앞서면 foreground rebase 없이 local commit/dirty를 보존한다."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "unrelated.txt", "baseline\n")
    subprocess.run(["git", "-C", str(work), "push", "-q"],
                   check=True, capture_output=True)
    other = _clone_other(origin, tmp_path / "other")
    _commit_file(other, "theirs.md", "remote first\n")
    subprocess.run(["git", "-C", str(other), "push", "-q"],
                   check=True, capture_output=True)

    _activate(work)
    dirty = work / "unrelated.txt"
    dirty.write_text("local dirty edit\n", encoding="utf-8")
    session_log = work / "session-log.md"
    session_log.write_text("local session\n", encoding="utf-8")

    r = _run_auto_commit(work, [session_log], xdg,
                         {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0, r.stderr
    assert _bare_git_text(origin, "show", "HEAD:theirs.md") == "remote first"
    with pytest.raises(subprocess.CalledProcessError):
        _bare_git_text(origin, "show", "HEAD:session-log.md")
    assert _git_text(work, "show", "HEAD:session-log.md") == "local session"
    ahead, behind = git_ops.ahead_behind(str(work))
    assert ahead >= 1 and behind >= 1
    assert dirty.read_text(encoding="utf-8") == "local dirty edit\n"
    assert "unrelated.txt" in _git_text(work, "status", "--short")
    assert git_ops.read_push_pending(str(work)) != ""
    marker = git_ops.read_sync_warning(str(work))
    assert "foreground worktree reconciliation disabled" in marker
    assert not (work / ".git" / "rebase-merge").exists()
    assert not (work / ".git" / "rebase-apply").exists()
    assert _git_text(work, "stash", "list") == ""


def _lease_payload(agent: str, session: str, tool: str) -> dict:
    return {"agent": agent, "session_id": session, "tool_use_id": tool}


def test_auto_commit_with_exact_edit_lease_reconciles_then_pushes(
        xdg, tmp_path):
    """Hook-correlated PostToolUse performs the requested pull/rebase + push."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "unrelated.txt", "baseline\n")
    subprocess.run(
        ["git", "-C", str(work), "push", "-q"],
        check=True, capture_output=True)
    other = _clone_other(origin, tmp_path / "lease-other")
    _commit_file(other, "theirs.md", "remote first\n")
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)

    _activate(work)
    dirty = work / "unrelated.txt"
    dirty.write_text("local dirty edit\n", encoding="utf-8")
    edited = work / "lease-session.md"
    edited.write_text("lease-backed local edit\n", encoding="utf-8")
    payload = _lease_payload("codex", "session-lease", "tool-lease")
    owner = git_ops.hook_edit_lease_owner(payload)
    assert owner
    assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True

    result = _run_auto_commit(
        work, [edited], xdg,
        {"TEAMMODE_DISABLE_PUSH_WORKER": "1"}, payload)

    assert result.returncode == 0, result.stderr
    assert _bare_git_text(origin, "show", "HEAD:theirs.md") == "remote first"
    assert _bare_git_text(
        origin, "show", "HEAD:lease-session.md") == "lease-backed local edit"
    assert git_ops.ahead_behind(str(work)) == (0, 0)
    assert dirty.read_text(encoding="utf-8") == "local dirty edit\n"
    assert "unrelated.txt" in _git_text(work, "status", "--short")
    assert git_ops.read_push_pending(str(work)) == ""
    assert git_ops.end_hook_edit_lease(str(work), owner) is False


def test_exact_edit_lease_does_not_rewrite_existing_pending_history(
        xdg, tmp_path):
    """An immutable H1 pending entry must survive a later leased H2 attempt.

    Rebase would replace H1/H2 with new object IDs and leave the recorded H1
    permanently non-fast-forward.  The foreground hook may commit H2, but it
    must keep H1 as an ancestor and defer worktree reconciliation.
    """
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "pending-h1.md", "pending H1\n")
    pending_h1 = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True

    other = _clone_other(origin, tmp_path / "pending-race-other")
    _commit_file(other, "remote-race.md", "remote R\n")
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)

    _activate(work)
    edited = work / "pending-h2.md"
    edited.write_text("pending H2\n", encoding="utf-8")
    payload = _lease_payload("codex", "session-pending", "tool-pending")
    owner = git_ops.hook_edit_lease_owner(payload)
    assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True

    result = _run_auto_commit(
        work, [edited], xdg,
        {"TEAMMODE_DISABLE_PUSH_WORKER": "1"}, payload)

    assert result.returncode == 0, result.stderr
    assert subprocess.run(
        ["git", "-C", str(work), "merge-base", "--is-ancestor",
         pending_h1, "HEAD"], capture_output=True).returncode == 0
    assert _git_text(work, "show", "HEAD:pending-h2.md") == "pending H2"
    assert _bare_git_text(origin, "show", "HEAD:remote-race.md") == "remote R"
    with pytest.raises(subprocess.CalledProcessError):
        _bare_git_text(origin, "show", "HEAD:pending-h2.md")
    assert git_ops.read_push_pending(str(work)) != ""
    assert git_ops.end_hook_edit_lease(str(work), owner) is False


def test_other_branch_pending_does_not_block_leased_current_reconcile(
        xdg, tmp_path):
    """A valid branch-A retry does not prevent branch main pull/rebase+push."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(
        ["git", "-C", str(work), "checkout", "-qb", "session-a"],
        check=True, capture_output=True)
    _commit_file(work, "session-a-pending.md", "branch A\n")
    assert git_ops.write_push_pending(str(work)) is True
    pending_a = git_ops.read_push_pending(str(work))
    subprocess.run(
        ["git", "-C", str(work), "checkout", "-q", "main"],
        check=True, capture_output=True)

    other = _clone_other(origin, tmp_path / "other-branch-pending-remote")
    _commit_file(other, "main-remote.md", "remote first\n")
    subprocess.run(
        ["git", "-C", str(other), "push", "-q"],
        check=True, capture_output=True)

    _activate(work)
    edited = work / "main-local.md"
    edited.write_text("local after pull\n", encoding="utf-8")
    payload = _lease_payload("claude", "session-main", "tool-main")
    owner = git_ops.hook_edit_lease_owner(payload)
    assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True

    result = _run_auto_commit(
        work, [edited], xdg,
        {"TEAMMODE_DISABLE_PUSH_WORKER": "1"}, payload)

    assert result.returncode == 0, result.stderr
    assert _bare_git_text(origin, "show", "HEAD:main-remote.md") == "remote first"
    assert _bare_git_text(origin, "show", "HEAD:main-local.md") == (
        "local after pull")
    assert git_ops.ahead_behind(str(work)) == (0, 0)
    assert git_ops.read_push_pending(str(work)) == pending_a
    assert git_ops.end_hook_edit_lease(str(work), owner) is False


def test_other_edit_lease_defers_reconcile_and_exact_post_releases_only_own(
        xdg, tmp_path):
    """Parallel tools preserve both edit and pending; Post removes no foreign lease."""
    origin, work = _clone_pair(tmp_path)
    other_clone = _clone_other(origin, tmp_path / "parallel-other")
    _commit_file(other_clone, "remote-parallel.md", "remote\n")
    subprocess.run(
        ["git", "-C", str(other_clone), "push", "-q"],
        check=True, capture_output=True)
    _activate(work)
    edited = work / "local-parallel.md"
    edited.write_text("local\n", encoding="utf-8")
    own_payload = _lease_payload("claude", "session-a", "tool-a")
    other_payload = _lease_payload("codex", "session-b", "tool-b")
    own = git_ops.hook_edit_lease_owner(own_payload)
    foreign = git_ops.hook_edit_lease_owner(other_payload)
    assert git_ops.begin_hook_edit_lease(str(work), own)[0] is True
    assert git_ops.begin_hook_edit_lease(str(work), foreign)[0] is True

    result = _run_auto_commit(
        work, [edited], xdg,
        {"TEAMMODE_DISABLE_PUSH_WORKER": "1"}, own_payload)

    assert result.returncode == 0
    assert _git_text(work, "show", "HEAD:local-parallel.md") == "local"
    assert git_ops.read_push_pending(str(work)) != ""
    assert git_ops.end_hook_edit_lease(str(work), own) is False
    assert git_ops.end_hook_edit_lease(str(work), foreign) is True


def test_edit_gate_blocks_new_pre_but_publication_lock_does_not(
        xdg, tmp_path):
    """Only local mutation blocks PreToolUse; a network publication lease does not."""
    _origin, work = _clone_pair(tmp_path)
    payload = _lease_payload("codex", "session-gate", "tool-gate")
    owner = git_ops.hook_edit_lease_owner(payload)

    with git_ops._edit_gate(str(work), 0.2) as (acquired, detail):
        assert acquired, detail
        ok, busy = git_ops.begin_hook_edit_lease(str(work), owner, timeout=0.02)
        assert ok is False and "contention" in busy

    with git_ops._publication_interlock(str(work), 0.2) as (acquired, detail):
        assert acquired, detail
        assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True
    assert git_ops.end_hook_edit_lease(str(work), owner) is True


def test_auto_commit_defers_rebase_when_remote_overlaps_dirty_file(xdg, tmp_path):
    """autostash apply 충돌 가능 경로는 rebase/push 대신 clean dirty+pending으로 보존."""
    origin, work = _clone_pair(tmp_path)
    (work / "dirty.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "dirty.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "add dirty base"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-q"],
                   check=True, capture_output=True)

    other = _clone_other(origin, tmp_path / "other-overlap")
    (other / "dirty.md").write_text("remote edit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "commit", "-qam", "remote dirty"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push", "-q"],
                   check=True, capture_output=True)

    _activate(work)
    (work / "dirty.md").write_text("local uncommitted edit\n", encoding="utf-8")
    session_log = work / "session-overlap.md"
    session_log.write_text("must remain retryable\n", encoding="utf-8")
    payload = _lease_payload("codex", "session-overlap", "tool-overlap")
    owner = git_ops.hook_edit_lease_owner(payload)
    assert owner
    assert git_ops.begin_hook_edit_lease(str(work), owner)[0] is True
    r = _run_auto_commit(
        work, [session_log], xdg,
        {"TEAMMODE_DISABLE_PUSH_WORKER": "1"}, payload)

    assert r.returncode == 0
    assert "dirty paths overlap upstream changes" in r.stderr
    assert "foreground worktree reconciliation disabled" not in r.stderr
    assert _git_text(work, "show", "HEAD:session-overlap.md") == (
        "must remain retryable")
    assert (work / "dirty.md").read_text(encoding="utf-8") == (
        "local uncommitted edit\n")
    assert "UU" not in _git_text(work, "status", "--short")
    assert "<<<<<<<" not in (work / "dirty.md").read_text(encoding="utf-8")
    assert _git_text(work, "stash", "list") == ""
    assert git_ops.read_push_pending(str(work)) != ""
    with pytest.raises(subprocess.CalledProcessError):
        _bare_git_text(origin, "show", "HEAD:session-overlap.md")
    assert git_ops.end_hook_edit_lease(str(work), owner) is False


def test_do_commit_rolls_back_autostash_conflict_from_dirty_toctou(
        xdg, tmp_path, monkeypatch):
    """preflight 직후 생긴 dirty overlap도 push하지 않고 원래 상태로 복원한다."""
    origin, work = _clone_pair(tmp_path)
    (work / "dirty.md").write_text("base\n", encoding="utf-8")
    (work / "side.md").write_text("side base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "dirty.md", "side.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "dirty base"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-q"],
                   check=True, capture_output=True)
    other = _clone_other(origin, tmp_path / "other-toctou")
    (other / "dirty.md").write_text("remote edit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "commit", "-qam", "remote dirty"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push", "-q"],
                   check=True, capture_output=True)

    # refs/stash는 linked worktree 전체가 공유한다. 실제 autostash apply 실패 직후
    # 다른 worktree가 같은 메시지의 stash를 top에 올려도 정확 OID만 복원해야 한다.
    side = tmp_path / "linked-side"
    subprocess.run(["git", "-C", str(work), "worktree", "add", "-qb",
                    "concurrent-side", str(side), "HEAD"],
                   check=True, capture_output=True)
    (side / "side.md").write_text("concurrent side stash\n", encoding="utf-8")

    session = work / "session-race.md"
    session.write_text("must stay local\n", encoding="utf-8")
    real_run_git = git_ops.run_git
    injected = False
    concurrent_stashed = False

    def racing_run_git(args, timeout, **kwargs):
        nonlocal injected, concurrent_stashed
        if not injected and "rebase" in args and "--autostash" in args:
            injected = True
            (work / "dirty.md").write_text(
                "local concurrent edit\n", encoding="utf-8")
            result = real_run_git(args, timeout, **kwargs)
            subprocess.run(
                ["git", "-C", str(side), "stash", "push", "-qm", "autostash",
                 "--", "side.md"], check=True, capture_output=True)
            concurrent_stashed = True
            return result
        return real_run_git(args, timeout, **kwargs)

    monkeypatch.setattr(git_ops, "run_git", racing_run_git)
    result = git_ops.do_commit(
        str(work), "chore(teammode): auto-commit race", push=True,
        paths=["session-race.md"])

    assert injected is True
    assert concurrent_stashed is True
    assert result.committed is True and result.pushed is False
    assert (work / "dirty.md").read_text(encoding="utf-8") == (
        "local concurrent edit\n")
    assert "UU" not in _git_text(work, "status", "--short")
    assert "<<<<<<<" not in (work / "dirty.md").read_text(encoding="utf-8")
    assert (work / "side.md").read_text(encoding="utf-8") == "side base\n"
    assert _git_text(work, "stash", "list").lower().count("autostash") >= 2
    assert _git_text(work, "show", "HEAD:session-race.md") == "must stay local"
    remote_tree = subprocess.run(
        ["git", "--git-dir", str(origin), "cat-file", "-e",
         "HEAD:session-race.md"], capture_output=True)
    assert remote_tree.returncode != 0


def test_auto_commit_leftover_pending_warns_stderr(xdg, tmp_path):
    """시작 시 잔존 pending 이 있으면 stderr 1줄('한 편집 늦은' 즉시 가시화)."""
    _, work = _clone_pair(tmp_path)
    _activate(work)
    git_ops.write_push_pending(str(work))
    f = work / "note2.md"
    f.write_text("x", encoding="utf-8")
    r = _run_auto_commit(work, [f], xdg,
                         {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0
    assert "push" in r.stderr and "pending" in r.stderr.lower() or "미완" in r.stderr


# ── session-start pending recovery (#45 가시화 3중의 ①) ─────────────

SESSION_START = REPO / "infra" / "hooks" / "session-start.py"


class _FakeGo:
    """recovery 판정 경로만 검증하는 fake git_ops."""

    def __init__(self, pending: str, ahead: int, has_upstream: bool,
                 blocker: str = ""):
        self._pending = pending
        self._ahead = ahead
        self._has = has_upstream
        self._blocker = blocker
        self.kicked = 0
        self.reconciled = 0
        self.cleared = 0
        self.conditional_clears = []
        self.DEFAULT_TIMEOUT = 2

    def read_push_pending(self, root):
        return self._pending

    def read_push_pending_state(self, root):
        return git_ops.PushPendingRead(self._pending, True)

    def bind_legacy_pending_to_current_checkout(self, root, snapshot):
        return snapshot

    def _ahead_behind_raw(self, root, timeout):
        raise AssertionError(
            "current checkout state must not drive pending recovery")

    def publication_blocker_detail(self, root, timeout=2):
        return self._blocker

    def kick_push_worker(self, root, worker):
        self.kicked += 1
        return True

    def reconcile_current_pending(self, root, snapshot, key, **_kwargs):
        self.reconciled += 1
        return git_ops.ReconcileResult(
            ok=True, action="up-to-date", final_identity={})

    def sanitize_git_detail(self, detail):
        return detail

    def write_sync_warning(self, root, detail):
        self.warning = detail
        return True

    def write_sync_warning_if_empty(self, root, detail):
        if not getattr(self, "warning", ""):
            self.warning = detail
        return True

    def pending_entry_key_for_current_checkout(self, root, snapshot):
        return "branch:test" if snapshot else ""

    def pending_target_summary(self, snapshot, root=None):
        return "branch test"

    def clear_push_pending_if_unchanged(self, root, snapshot, target_key=None):
        self.conditional_clears.append((root, snapshot))
        if self._blocker:
            return False
        self._pending = ""
        return True

    def clear_sync_warning_if_fully_published(self, root):
        return self._has and self._ahead == 0


def _load_session_start():
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_start_mod", SESSION_START)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recover_ahead_rekicks_worker_no_direct_push(tmp_path, capsys, monkeypatch):
    """pending → current ahead probe 없이 worker 재kick만 한다."""
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=2, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    err = capsys.readouterr().err
    assert "push 미완" in err
    assert "ahead=" not in err
    assert fake.kicked == 1
    assert fake.cleared == 0


@pytest.mark.parametrize("history", ("pending-merge", "external-rebase"))
def test_session_start_merges_pending_head_before_worker_recovery(
        xdg, tmp_path, monkeypatch, history):
    """SessionStart advances safe pending history before the actual worker runs."""
    origin, work = _clone_pair(tmp_path)
    _commit_file(work, "local-h1.md", "local pending H1\n")
    pending_head = _git_text(work, "rev-parse", "HEAD")
    assert git_ops.write_push_pending(str(work)) is True
    pending_before = git_ops.read_push_pending(str(work))
    old, _rewritten, remote_head = _external_rebase_pending_head(
        origin, work, tmp_path / "session-start-other", prefix="session-start",
        rebase=history == "external-rebase")
    assert old == pending_head and remote_head != pending_head

    mod = _load_session_start()
    workers = []
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    monkeypatch.setenv("TEAMMODE_PULL_THROTTLE", "0")

    def run_worker_now(root, worker):
        assert Path(worker) == WORKER
        worker_result = _run_worker(
            Path(root), {"XDG_STATE_HOME": str(xdg)})
        workers.append(worker_result)
        return worker_result.returncode == 0

    monkeypatch.setattr(git_ops, "kick_push_worker", run_worker_now)

    mod._maybe_auto_pull(str(work))

    recovered = _git_text(work, "rev-parse", "HEAD")
    assert recovered not in {pending_head, remote_head}
    expected_pending_ancestor_rc = 0 if history == "pending-merge" else 1
    assert _git_rc(work, "merge-base", "--is-ancestor",
                   pending_head, recovered) == expected_pending_ancestor_rc
    assert _git_rc(work, "merge-base", "--is-ancestor", remote_head, recovered) == 0
    assert len(workers) == 1
    assert workers[0].returncode == 0, workers[0].stderr
    assert _bare_git_text(origin, "rev-parse", "refs/heads/main") == recovered
    assert git_ops.read_push_pending(str(work)) == "", pending_before
    assert not _git_path(work, "MERGE_HEAD").exists()
    assert not _git_path(work, "rebase-merge").exists()
    assert not _git_path(work, "rebase-apply").exists()


def test_recover_ahead_english_for_en_locale(tmp_path, capsys, monkeypatch):
    """i18n(적대검수 — long tail): lang="en" 을 명시로 넘기면 한글 없이 영어로만
    경고가 나온다(_recover_push_pending 은 _maybe_auto_pull 이 한 번 해석해 넘긴다)."""
    import re
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=2, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path), "en")
    err = capsys.readouterr().err
    assert "push" in err
    assert "ahead=" not in err
    assert not re.search(r"[가-힣]", err), f"en 팀 출력에 한글 섞임: {err!r}"


def test_recover_ahead_zero_rekicks_worker_without_clearing(
        tmp_path, capsys, monkeypatch):
    """current ahead==0만으로 exact pending을 완료 오판하지 않고 worker에 위임한다."""
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=0, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    assert fake.cleared == 0
    assert fake.conditional_clears == []
    assert fake.kicked == 1
    assert "push 미완" in capsys.readouterr().err


def test_recover_ahead_zero_preserves_pending_without_blocker_probe(
        tmp_path, capsys, monkeypatch):
    """SessionStart는 blocker 상태와 무관하게 exact worker만 clear하게 한다."""
    mod = _load_session_start()
    fake = _FakeGo(
        "pending", ahead=0, has_upstream=True,
        blocker="reconcile blocker probe unavailable")
    monkeypatch.setattr(mod, "_git_ops", fake)

    mod._recover_push_pending(str(tmp_path))

    assert fake.conditional_clears == []
    assert fake.kicked == 1
    assert fake._pending == "pending"
    assert "push 미완" in capsys.readouterr().err


def test_recover_no_pending_silent(tmp_path, capsys, monkeypatch):
    """pending 없으면 완전 침묵(판정·kick 비용 없음)."""
    mod = _load_session_start()
    fake = _FakeGo("", ahead=5, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    assert capsys.readouterr().err == ""
    assert fake.cleared == 0 and fake.kicked == 0


# ── UserPromptSubmit 초경량 pending-age 검사 (#45 가시화 3중의 ②) ───

REMIND = REPO / "infra" / "hooks" / "session-log-remind.py"


def _load_remind():
    import importlib.util
    spec = importlib.util.spec_from_file_location("remind_mod", REMIND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_remind_warns_on_old_pending(xdg, tmp_path, capsys):
    """age > 임계 → stderr 1줄 + warned 마커(30분 스로틀) — 2회째는 침묵."""
    mod = _load_remind()
    root = str(tmp_path / "team")
    git_ops.write_push_pending(root)
    # mtime 을 과거로 조작해 age > 600s 재현
    old = Path(git_ops.push_pending_path(root))
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 700))
    mod._warn_push_pending_age(root)
    err1 = capsys.readouterr().err
    # i18n 갱신(적대검수 — long tail): root 에 team.config.json 이 없어 en 기본
    # (team_lang 계약) — "push" 는 en/ko 두 문구 모두에 그대로 나오는 언어중립 토큰.
    assert "push" in err1
    mod._warn_push_pending_age(root)  # 스로틀 — 재경고 없음
    assert capsys.readouterr().err == ""


def test_remind_silent_on_fresh_or_no_pending(xdg, tmp_path, capsys):
    """pending 없음/신선(age<임계) → 완전 침묵."""
    mod = _load_remind()
    root = str(tmp_path / "team")
    mod._warn_push_pending_age(root)          # 없음
    git_ops.write_push_pending(root)
    mod._warn_push_pending_age(root)          # 신선
    assert capsys.readouterr().err == ""


# ── codex 적대검수 반영 (P1×3·P2×3·P3) ─────────────────────────────

def test_clear_pending_if_unchanged_guard(xdg, tmp_path, publication_ready):
    """[P1] clear race 가드: 스냅샷 이후 pending 이 재기록됐으면 clear 하지 않는다.

    판별자는 파일 내용(고유 nonce) — coarse mtime FS(1s 해상도)에서도 같은 초 내
    재기록을 정확히 구분한다(codex 재검수). 재기록 사이에 sleep 을 두지 않는 것이
    바로 그 검증이다.
    """
    root = str(tmp_path / "team")
    git_ops.write_push_pending(root)
    snap = git_ops.read_push_pending(root)
    # 변경 없음 → clear 성공
    assert git_ops.clear_push_pending_if_unchanged(root, snap) is True
    assert git_ops.read_push_pending(root) == ""
    # 재기록(새 커밋의 pending) — 같은 초 내 연속 기록이어도 nonce 로 구분된다.
    git_ops.write_push_pending(root)
    snap_old = git_ops.read_push_pending(root)
    git_ops.write_push_pending(root)  # 경합: push 도중 새 pending(즉시 재기록)
    assert git_ops.clear_push_pending_if_unchanged(root, snap_old) is False
    assert git_ops.read_push_pending(root) != "", "경합 pending 이 유실됐다"
    # 빈 스냅샷은 항상 거부(보수)
    assert git_ops.clear_push_pending_if_unchanged(root, "") is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock probe")
def test_clear_pending_if_unchanged_holds_lock_through_remove(
        xdg, tmp_path, monkeypatch, publication_ready):
    """compare 직후 remove 시점에도 다른 프로세스가 ledger lock 을 못 얻는다."""
    root = str(tmp_path / "team")
    assert git_ops.write_push_pending(root) is True
    snapshot = git_ops.read_push_pending(root)
    pending_path = git_ops.push_pending_path(root)
    lock_path = pending_path + ".state.lock"
    probe_code = (
        "import fcntl,sys\n"
        "f = open(sys.argv[1], 'a+b')\n"
        "try:\n"
        " fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        " print('acquired')\n"
        "except BlockingIOError:\n"
        " print('blocked')\n"
    )
    real_remove = git_ops.os.remove
    observations = []

    def remove_with_lock_probe(path):
        if os.fspath(path) == pending_path:
            probe = subprocess.run(
                [sys.executable, "-c", probe_code, lock_path], check=True,
                capture_output=True, text=True, timeout=5)
            observations.append(probe.stdout.strip())
        return real_remove(path)

    monkeypatch.setattr(git_ops.os, "remove", remove_with_lock_probe)
    assert git_ops.clear_push_pending_if_unchanged(root, snapshot) is True
    assert observations == ["blocked"]
    assert git_ops.read_push_pending(root) == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock contention fixture")
def test_pending_lock_contention_fails_conservatively(xdg, tmp_path, monkeypatch):
    root = str(tmp_path / "team")
    assert git_ops.write_push_pending(root) is True
    snapshot = git_ops.read_push_pending(root)
    lock_path = git_ops.push_pending_path(root) + ".state.lock"
    ready = tmp_path / "lock-ready"
    release = tmp_path / "lock-release"
    holder_code = (
        "import fcntl,sys,time\n"
        "from pathlib import Path\n"
        "f = open(sys.argv[1], 'a+b')\n"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "Path(sys.argv[2]).write_text('ready', encoding='utf-8')\n"
        "while not Path(sys.argv[3]).exists(): time.sleep(0.005)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, lock_path, str(ready), str(release)])
    try:
        for _ in range(1000):
            if ready.exists():
                break
            time.sleep(0.005)
        assert ready.exists(), "lock holder did not start"
        monkeypatch.setattr(git_ops, "_PUSH_PENDING_LOCK_WAIT_SECONDS", 0.05)
        state = git_ops.read_push_pending_state(root)
        assert state.available is False and state.content == ""
        assert git_ops.write_push_pending(root) is False
        assert git_ops.clear_push_pending_if_unchanged(root, snapshot) is False
    finally:
        release.write_text("release", encoding="utf-8")
        holder.wait(timeout=5)
    assert holder.returncode == 0
    assert git_ops.read_push_pending(root) == snapshot


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock errno contract")
def test_pending_lock_does_not_retry_permanent_os_error(xdg, tmp_path, monkeypatch):
    import errno
    import fcntl

    sleeps = []

    def bad_descriptor(_fd, _operation):
        raise OSError(errno.EBADF, "bad descriptor")

    monkeypatch.setattr(fcntl, "flock", bad_descriptor)
    monkeypatch.setattr(git_ops.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(git_ops, "_PUSH_PENDING_LOCK_WAIT_SECONDS", 0.02)
    with git_ops._push_pending_ledger_lock(str(tmp_path / "team")) as locked:
        assert locked is False
    assert sleeps == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_pending_state_files_are_private(xdg, tmp_path):
    root = str(tmp_path / "team")
    assert git_ops.write_push_pending(root) is True
    git_ops.write_sync_warning(root, "private failure detail")
    state_dir = xdg / "teammode"
    pending = Path(git_ops.push_pending_path(root))
    lock = Path(str(pending) + ".state.lock")
    warning = Path(git_ops.sync_warning_path(root))
    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert pending.stat().st_mode & 0o777 == 0o600
    assert lock.stat().st_mode & 0o777 == 0o600
    assert warning.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink contract")
def test_pending_write_refuses_symlinked_state_paths(xdg, tmp_path, monkeypatch):
    victim_dir = tmp_path / "victim-dir"
    victim_dir.mkdir()
    state_dir = xdg / "teammode"
    state_dir.parent.mkdir(parents=True)
    state_dir.symlink_to(victim_dir, target_is_directory=True)
    root = str(tmp_path / "team")
    assert git_ops.write_push_pending(root) is False
    assert list(victim_dir.iterdir()) == []

    state_dir.unlink()
    state_dir.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch", encoding="utf-8")
    pending = Path(git_ops.push_pending_path(root))
    pending.symlink_to(victim)
    assert git_ops.write_push_pending(root) is False
    assert victim.read_text(encoding="utf-8") == "do not touch"

    pending.unlink()
    lock = Path(str(pending) + ".state.lock")
    lock.unlink(missing_ok=True)
    lock.symlink_to(victim)
    assert git_ops.write_push_pending(root) is False
    assert victim.read_text(encoding="utf-8") == "do not touch"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO contract")
def test_pending_write_refuses_fifo_without_blocking(xdg, tmp_path):
    root = str(tmp_path / "team")
    state_dir = xdg / "teammode"
    state_dir.mkdir(parents=True, mode=0o700)
    pending = git_ops.push_pending_path(root)
    os.mkfifo(pending, mode=0o600)
    assert git_ops.write_push_pending(root) is False


def test_fully_published_cleanup_requires_no_pending(
        xdg, tmp_path, monkeypatch, publication_ready):
    root = str(tmp_path / "team")
    monkeypatch.setattr(git_ops, "_ahead_behind_raw",
                        lambda _root, _timeout: (0, 0, True))
    git_ops.write_push_pending(root)
    git_ops.write_sync_warning(root, "new failure detail")
    assert git_ops.clear_sync_warning_if_fully_published(root) is False
    assert git_ops.read_sync_warning(root) == "new failure detail"

    snapshot = git_ops.read_push_pending(root)
    assert git_ops.clear_push_pending_if_unchanged(root, snapshot) is True
    assert git_ops.clear_sync_warning_if_fully_published(root) is True
    assert git_ops.read_sync_warning(root) == ""


def test_exact_publication_cleanup_uses_push_target_not_pull_upstream(
        xdg, tmp_path):
    """Triangular fork publication clears by fork/main, not origin/main ahead."""
    origin, work = _clone_pair(tmp_path)
    fork = _init_repo(tmp_path / "cleanup-fork.git", bare=True)
    subprocess.run(
        ["git", "-C", str(work), "remote", "add", "fork", str(fork)],
        check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "remote.pushDefault", "fork"],
        check=True, capture_output=True)
    edited = work / "fork-warning.md"
    edited.write_text("published to fork\n", encoding="utf-8")
    git_ops.write_sync_warning(str(work), "old fork publication failure")

    result = git_ops.do_commit(
        str(work), "publish fork target", push=True,
        paths=[edited.name], reconcile_before_push=True)

    assert result.committed is True and result.pushed is True, result.detail
    assert git_ops.ahead_behind(str(work))[0] == 1
    assert git_ops.clear_sync_warning_if_fully_published(str(work)) is False
    assert git_ops.clear_sync_warning_after_exact_publication(
        str(work), result.pending_identity, result.pending_target) is True
    assert git_ops.read_sync_warning(str(work)) == ""
    assert _bare_git_text(
        fork, "rev-parse", "refs/heads/main") == result.pending_identity["head"]
    assert _bare_git_text(
        origin, "rev-parse", "refs/heads/main") != result.pending_identity["head"]


@pytest.mark.parametrize(
    "blocker", ("index-lock", "reconcile-ref", "transaction-dir"))
def test_success_cleanup_preserves_pending_and_warning_while_reconcile_blocked(
        xdg, tmp_path, blocker):
    """A blocker appearing after publication proof must prevent every clear."""
    _origin, work = _clone_pair(tmp_path)
    root = str(work)
    assert git_ops.write_push_pending(root) is True
    git_ops.write_sync_warning(root, f"warning before {blocker}")
    pending_before = git_ops.read_push_pending(root)
    warning_before = git_ops.read_sync_warning(root)
    _install_reconcile_blocker(work, blocker)

    assert git_ops.clear_push_pending_if_unchanged(
        root, pending_before) is False
    assert git_ops.clear_sync_warning_if_fully_published(root) is False
    assert git_ops.read_push_pending(root) == pending_before
    assert git_ops.read_sync_warning(root) == warning_before


def test_warning_writer_serializes_with_success_cleanup(
        xdg, tmp_path, monkeypatch, publication_ready):
    """cleanup remove 직전 시작한 새 warning은 같은 ledger lock 뒤에 남아야 한다."""
    import threading

    root = str(tmp_path / "team")
    git_ops.write_sync_warning(root, "old warning")
    monkeypatch.setattr(git_ops, "_ahead_behind_raw",
                        lambda _root, _timeout: (0, 0, True))
    real_remove = git_ops._remove_private_file
    writer_started = threading.Event()
    writer_finished = threading.Event()
    writers = []

    def write_new_warning():
        writer_started.set()
        git_ops.write_sync_warning(root, "new concurrent warning")
        writer_finished.set()

    def remove_while_writer_waits(path):
        writer = threading.Thread(target=write_new_warning)
        writers.append(writer)
        writer.start()
        assert writer_started.wait(timeout=1)
        time.sleep(0.03)
        assert not writer_finished.is_set(), "warning writer bypassed cleanup lock"
        return real_remove(path)

    monkeypatch.setattr(git_ops, "_remove_private_file", remove_while_writer_waits)
    assert git_ops.clear_sync_warning_if_fully_published(root) is True
    for writer in writers:
        writer.join(timeout=2)
        assert not writer.is_alive()
    assert git_ops.read_sync_warning(root) == "new concurrent warning"


def test_warning_written_during_publication_check_is_not_cleared(
        xdg, tmp_path, monkeypatch, publication_ready):
    """ahead 판정 중 새 warning이 완료되면 cleanup은 snapshot 변경으로 보존한다."""
    root = str(tmp_path / "team")
    git_ops.write_sync_warning(root, "old warning")

    def publish_check_with_new_failure(_root, _timeout):
        git_ops.write_sync_warning(root, "new pending-less failure")
        return 0, 0, True

    monkeypatch.setattr(git_ops, "_ahead_behind_raw",
                        publish_check_with_new_failure)
    assert git_ops.clear_sync_warning_if_fully_published(root) is False
    assert git_ops.read_sync_warning(root) == "new pending-less failure"


@pytest.mark.skipif(os.name == "nt", reason="POSIX fsync durability contract")
def test_pending_atomic_write_fsyncs_file_and_parent_dir(xdg, tmp_path, monkeypatch):
    calls = []
    real_fsync = git_ops.os.fsync

    def spy_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(git_ops.os, "fsync", spy_fsync)
    assert git_ops.write_push_pending(str(tmp_path / "team")) is True
    assert len(calls) >= 2, "pending success requires file and parent-dir fsync"


def test_write_push_pending_returns_bool(xdg, tmp_path, monkeypatch):
    """[P1] ledger 기록 성공 여부를 호출부가 알 수 있다(bool 반환)."""
    root = str(tmp_path / "team")
    assert git_ops.write_push_pending(root) is True
    # 기록 불가 환경(state dir 를 파일로 막음) → False
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "blocked"))
    blocked = tmp_path / "blocked" / "teammode"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("not a dir", encoding="utf-8")
    assert git_ops.write_push_pending(root) is False


def test_auto_commit_ledger_failure_leaves_sync_warning(xdg, tmp_path):
    """[P1] 커밋 성공 + ledger 기록 실패 → sync-warning fallback + stderr(무음 유실 차단)."""
    _, work = _clone_pair(tmp_path)
    _activate(work)
    f = work / "note-lf.md"
    f.write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin",
                    str(tmp_path / "missing-origin.git")], check=True,
                   capture_output=True)
    # write 는 막고 sync-warning 은 살리기: pending 파일 자리를 디렉토리로 선점
    Path(git_ops.push_pending_path(str(work))).parent.mkdir(parents=True, exist_ok=True)
    Path(git_ops.push_pending_path(str(work))).mkdir()
    r = _run_auto_commit(work, [f], xdg, {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0
    # i18n 갱신(적대검수 — long tail): 이 픽스처는 en 기본(team_lang 계약) — 언어중립
    # 마커("push-pending" 은 ko/en 문구 모두에 그대로 나옴)로 확인.
    assert "push-pending" in r.stderr
    assert "pending" in git_ops.read_sync_warning(str(work))


def test_auto_commit_ledger_failure_english_for_en_locale_team(xdg, tmp_path):
    """i18n(적대검수 — long tail, auto-commit 신설 스캐폴딩): en 팀은 ledger 기록
    실패 stderr 도, sync-warning 마커 내용도 전부 영어이고 한글이 섞이지 않는다.

    sync-warning 마커는 나중에 session-start 의 hook_ss_sync_warn(en-locale)의
    {warn} 자리에 그대로 삽입되므로, 마커 자체가 en 이어야 en 팀 출력이 끝까지
    영어로 유지된다(addendum 2 에서 발견한 것과 동일한 클래스의 함정).
    """
    import json as _json
    import re
    _, work = _clone_pair(tmp_path)
    _activate(work)
    (work / "team.config.json").write_text(
        _json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    f = work / "note-lf-en.md"
    f.write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin",
                    str(tmp_path / "missing-origin.git")], check=True,
                   capture_output=True)
    Path(git_ops.push_pending_path(str(work))).parent.mkdir(parents=True, exist_ok=True)
    Path(git_ops.push_pending_path(str(work))).mkdir()
    r = _run_auto_commit(work, [f], xdg, {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0
    assert "push-pending" in r.stderr
    marker = git_ops.read_sync_warning(str(work))
    assert "pending" in marker
    assert not re.search(r"[가-힣]", r.stderr), f"en 팀 stderr 에 한글 섞임: {r.stderr!r}"
    assert not re.search(r"[가-힣]", marker), f"en 팀 마커에 한글 섞임: {marker!r}"


def test_recover_unknown_upstream_still_kicks_worker(tmp_path, capsys, monkeypatch):
    """[P2] current upstream과 무관하게 stored pending worker를 kick한다.

    worker 의 stored target push가 current upstream을 사용하지 않으므로 kick 이 안전하다.
    """
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=0, has_upstream=False)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    err = capsys.readouterr().err
    assert "push 미완" in err
    assert fake.kicked == 1, "worker를 재기동하지 않으면 pending이 영구 잔존"
    assert fake.cleared == 0


def test_recovery_runs_even_when_pull_throttled(tmp_path, monkeypatch, capsys):
    """[P2] pending recovery 는 auto-pull 스로틀과 독립 — throttle 로 조기 return 해도 실행."""
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=1, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)

    class _FakePull:
        DEFAULT_THROTTLE_SECONDS = 600
        @staticmethod
        def should_pull(state, now, throttle):
            return False  # 스로틀에 막힌 상황 재현
    monkeypatch.setattr(mod, "_auto_pull", _FakePull)
    mod._maybe_auto_pull(str(tmp_path))
    assert fake.kicked == 1, "스로틀에 막혀 recovery 가 실행되지 않았다"


def test_worker_drain_exhaustion_writes_marker(xdg, tmp_path):
    """[P3] drain 한도 소진 시(pending 잔존) sync-warning 즉시 표면화."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    git_ops.write_push_pending(str(work))
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg),
                           "TEAMMODE_WORKER_MAX_LOOPS": "0"})
    assert r.returncode == 0
    assert "drain" in git_ops.read_sync_warning(str(work))
    assert git_ops.read_push_pending(str(work)) != ""
