"""L2-G — 미구현 훅 2개(auto-commit.py·confirm-action.py) 테스트.

안전 철칙(반드시):
  - 실 레포(teammode-repo)에 절대 커밋하지 않는다. 모든 git 작업은 tmp fake repo 에서만.
  - 실 ~/.claude·실 git·셸 프로파일 무접촉(conftest 가드가 추가로 보증).
  - auto-commit 의 `.teammode-active` 가드가 "마커 없으면 no-op"임을 실증(빌드 오염 0).

호출 모델: 정규 스키마 JSON(stdin) + TEAMMODE_HOME 으로 팀 루트 지정(런타임 훅 계약 §1.2).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "infra" / "hooks"
AUTO_COMMIT = HOOKS / "auto-commit.py"
CONFIRM = HOOKS / "confirm-action.py"
CLAUDE_NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
PY = sys.executable


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Keep helper and hook product Git subprocesses off host configuration."""
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


@pytest.fixture
def fake_repo(tmp_path):
    """tmp 팀 루트 = fake git repo + 초기 커밋. (실 레포 절대 무접촉)"""
    root = tmp_path / "team"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@t")
    (root / "init.txt").write_text("init\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def _run_hook(script, payload, root, args=None, cwd=None):
    argv = [PY, str(script)]
    if args:
        argv += args
    return subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(root)},
        cwd=cwd,
    )


def _head(root):
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit_count(root):
    return int(_git(root, "rev-list", "--count", "HEAD").stdout.strip())


NATIVE_EDIT_TOOLS = [
    ("claude", "Edit"),
    ("claude", "Write"),
    ("codex", "apply_patch"),
]


@pytest.fixture
def local_origin(fake_repo, tmp_path):
    """Publish the isolated main branch to a local bare remote only."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(fake_repo, "remote", "add", "origin", str(origin))
    _git(fake_repo, "push", "-u", "origin", "main")
    return origin


def _run_native_edit(agent, tool, root):
    """Deliver a completed native edit with normal per-tool identifiers."""
    if tool == "apply_patch":
        tool_input = {"command": (
            "*** Begin Patch\n*** Update File: init.txt\n@@\n"
            "-init\n+edited\n*** End Patch\n")}
    elif tool == "Edit":
        tool_input = {
            "file_path": str(root / "init.txt"),
            "old_string": "init\n", "new_string": "edited\n",
        }
    else:
        tool_input = {
            "file_path": str(root / "init.txt"), "content": "edited\n",
        }
    payload = {
        "hook_event_name": "PostToolUse", "tool_name": tool,
        "tool_input": tool_input, "cwd": str(root),
        "session_id": "session-alice", "tool_use_id": "call-alice",
    }
    normalize = REPO / "infra" / "agents" / agent / "normalize.py"
    return _run_hook(
        normalize, payload, root, args=["auto-commit.py"], cwd=root)


@pytest.mark.parametrize("agent,tool", NATIVE_EDIT_TOOLS)
@pytest.mark.parametrize("remote_ahead", [False, True])
def test_native_edit_without_pre_hook_commits_and_syncs(
        fake_repo, local_origin, tmp_path, agent, tool, remote_ahead):
    """Normal host IDs need no removed PreToolUse hook to publish the edit."""
    remote_before = _head(local_origin)
    if remote_ahead:
        peer = tmp_path / "peer"
        _git(tmp_path, "clone", str(local_origin), str(peer))
        (peer / "remote.md").write_text("peer update\n", encoding="utf-8")
        _git(peer, "add", "--", "remote.md")
        _git(peer, "commit", "-m", "peer update")
        _git(peer, "push", "origin", "main")
        remote_before = _head(local_origin)

    (fake_repo / ".teammode-active").touch()
    (fake_repo / "init.txt").write_text("edited\n", encoding="utf-8")
    unrelated = fake_repo / "unrelated.env"
    unrelated.write_text("leave this local\n", encoding="utf-8")

    proc = _run_native_edit(agent, tool, fake_repo)

    assert proc.returncode == 0
    assert _head(fake_repo) != remote_before, proc.stderr
    assert _head(fake_repo) == _head(local_origin), proc.stderr
    assert _git(local_origin, "show", "main:init.txt").stdout == "edited\n"
    assert _git(fake_repo, "show", "--format=", "--name-only", "HEAD").stdout == (
        "init.txt\n")
    assert _git(fake_repo, "rev-list", "--left-right", "--count",
                "HEAD...origin/main").stdout.strip() == "0\t0"
    assert _git(fake_repo, "status", "--porcelain=v1", "--",
                "unrelated.env").stdout == "?? unrelated.env\n"
    assert unrelated.read_text(encoding="utf-8") == "leave this local\n"
    assert _git(local_origin, "ls-tree", "--name-only", "main", "--",
                "unrelated.env", ".teammode-active").stdout == ""
    if remote_ahead:
        assert _git(local_origin, "show", "main:remote.md").stdout == (
            "peer update\n")
        assert _git(fake_repo, "merge-base", "--is-ancestor",
                    remote_before, "HEAD", check=False).returncode == 0
        assert _git(fake_repo, "rev-list", "--merges",
                    remote_before + "..HEAD").stdout == ""


@pytest.mark.parametrize("agent,tool", NATIVE_EDIT_TOOLS)
def test_native_edit_without_active_marker_keeps_local_and_remote_unchanged(
        fake_repo, local_origin, agent, tool):
    """Host IDs must not bypass the team-mode off guard."""
    before = _head(fake_repo)
    (fake_repo / "init.txt").write_text("edited\n", encoding="utf-8")
    status_before = _git(fake_repo, "status", "--porcelain=v1").stdout

    proc = _run_native_edit(agent, tool, fake_repo)

    assert proc.returncode == 0
    assert _head(fake_repo) == before
    assert _head(local_origin) == before
    assert _git(fake_repo, "status", "--porcelain=v1").stdout == status_before
    assert (fake_repo / "init.txt").read_text(encoding="utf-8") == "edited\n"
    assert not (fake_repo / ".git" / "FETCH_HEAD").exists()


# ════════════════════════════════════════════════════════════════════
# auto-commit.py
# ════════════════════════════════════════════════════════════════════

def test_auto_commit_no_marker_is_noop(fake_repo):
    """빌드 안전 핵심: .teammode-active 없으면 절대 커밋하지 않는다(no-op exit 0)."""
    (fake_repo / "edited.txt").write_text("change\n")
    before = _head(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(fake_repo / "edited.txt")], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo, cwd=fake_repo)
    assert proc.returncode == 0
    # HEAD 불변 = 커밋 안 생김. 워킹트리 변경은 스테이징조차 안 됨.
    assert _head(fake_repo) == before
    assert _git(fake_repo, "status", "--short").stdout.strip().startswith("??")


def test_auto_commit_active_commits(fake_repo):
    """.teammode-active 있으면 발동 — 지목 파일이 커밋된다."""
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "doc.md").write_text("hello\n")
    before = _commit_count(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(fake_repo / "doc.md")], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
    assert proc.returncode == 0
    assert _commit_count(fake_repo) == before + 1
    assert "doc.md" in _git(fake_repo, "show", "--name-only", "HEAD").stdout


def test_auto_commit_accepts_normalized_relative_file(fake_repo):
    """Codex apply_patch normalize 가 내는 repo-relative files 도 커밋한다."""
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "relative.md").write_text("normalized relative path\n")
    before = _commit_count(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": ["relative.md"], "agent": "codex"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo, cwd=fake_repo)
    assert proc.returncode == 0
    assert _commit_count(fake_repo) == before + 1
    assert "relative.md" in _git(
        fake_repo, "show", "--name-only", "HEAD").stdout


def test_auto_commit_rejects_relative_parent_traversal(fake_repo, tmp_path):
    """상대경로 지원이 team root 밖 파일로 확장되면 안 된다."""
    (fake_repo / ".teammode-active").write_text("")
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    before = _head(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": ["../outside.md"], "agent": "codex"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo, cwd=fake_repo)
    assert proc.returncode == 0
    assert _head(fake_repo) == before


def test_auto_commit_relative_path_uses_hook_cwd_not_team_root_alias(
        fake_repo, tmp_path):
    """외부 cwd의 상대경로가 team repo 동명 dirty 파일로 바뀌면 안 된다."""
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "README.md").write_text("team secret dirty content\n")
    external = tmp_path / "external-project"
    external.mkdir()
    (external / "README.md").write_text("external edit\n")
    before = _head(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": ["README.md"], "agent": "codex"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo, cwd=external)
    assert proc.returncode == 0
    assert _head(fake_repo) == before
    assert "README.md" in _git(fake_repo, "status", "--short").stdout


def test_auto_commit_commits_deleted_repo_file_with_literal_pathspec(fake_repo):
    """literal 검증이 삭제 파일의 auto-commit 을 막지 않는다."""
    (fake_repo / ".teammode-active").write_text("")
    deleted = fake_repo / "init.txt"
    deleted.unlink()
    before = _commit_count(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(deleted)], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
    assert proc.returncode == 0
    assert _commit_count(fake_repo) == before + 1
    assert _git(fake_repo, "show", "--format=", "--name-status", "HEAD").stdout == (
        "D\tinit.txt\n")


def test_auto_commit_stages_only_named_files_not_add_all(fake_repo):
    """add -A 금지: 정규스키마가 지목한 파일만 스테이징, 무관/토큰 파일 제외."""
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "target.md").write_text("commit me\n")
    # 토큰패턴/무관 파일들 — 함께 커밋되면 안 된다.
    _ghp_dummy = "ghp" + "_SHOULD_NOT_BE_COMMITTED"
    (fake_repo / "secret.token").write_text(_ghp_dummy + "\n")
    (fake_repo / "unrelated.txt").write_text("leave me\n")
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(fake_repo / "target.md")], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
    assert proc.returncode == 0
    committed = _git(fake_repo, "show", "--name-only", "HEAD").stdout
    assert "target.md" in committed
    assert "secret.token" not in committed
    assert "unrelated.txt" not in committed
    # 무관 파일들은 여전히 untracked 로 남아있다(스테이징 안 됨).
    status = _git(fake_repo, "status", "--short").stdout
    assert "secret.token" in status and "unrelated.txt" in status


def test_auto_commit_rejects_pathspec_magic_without_staging_secret(fake_repo):
    """hook payload 의 Git pathspec magic 이 repo 전체를 stage/push 하면 안 된다."""
    (fake_repo / ".teammode-active").write_text("")
    secret = fake_repo / "secret.env"
    secret.write_text("TOP_SECRET=value\n")
    before = _head(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [":(top,glob)**"], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
    assert proc.returncode == 0
    assert _head(fake_repo) == before
    status = _git(fake_repo, "status", "--short").stdout
    assert "secret.env" in status
    assert "secret.env" not in _git(fake_repo, "show", "--name-only", "HEAD").stdout


def test_auto_commit_pushes_nonblocking(fake_repo, monkeypatch, tmp_path):
    """A sync failure preserves the local commit and records the last error.

    No remote exists, so publication fails after the scoped commit. Normal host
    IDs must still allow the hook to commit without a preceding mutex lease.
    """
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "p.md").write_text("x\n")
    sys.path.insert(0, str(REPO / "infra"))
    import git_ops as go  # noqa: E402

    calls = {}
    real = go.do_commit

    def spy(team_root, message, push=False, timeout=go.NET_TIMEOUT,
            paths=None, **kwargs):
        calls["push"] = push
        calls["paths"] = paths
        return real(
            team_root, message, push=push, timeout=timeout, paths=paths,
            **kwargs)

    # Inspect the scoped publication call in-process while using real Git.
    monkeypatch.setattr(go, "do_commit", spy)
    monkeypatch.setenv("TEAMMODE_HOME", str(fake_repo))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("auto_commit_mod", AUTO_COMMIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_git_ops", go)
    payload = {
        "event": "PostToolUse", "action": "file_edit",
        "files": [str(fake_repo / "p.md")], "agent": "claude",
        "session_id": "session-push", "tool_use_id": "tool-push",
    }
    monkeypatch.setattr("sys.stdin", _StdinStub(json.dumps(payload)))
    rc = mod.main()
    assert rc == 0
    assert calls.get("push") is True
    assert calls.get("paths") == [":(literal)p.md"]
    # The local commit survives the non-blocking publication failure.
    committed = _git(fake_repo, "show", "--name-only", "HEAD").stdout
    assert "p.md" in committed
    assert go.read_last_sync_error(str(fake_repo))


def test_auto_commit_retries_index_lock_then_publishes_scoped_edit(
        fake_repo, local_origin, monkeypatch):
    """A transient index lock must not strand a normal identified edit."""
    import importlib.util
    from types import SimpleNamespace

    sys.path.insert(0, str(REPO / "infra"))
    import git_ops as go  # noqa: E402

    (fake_repo / ".teammode-active").touch()
    (fake_repo / "init.txt").write_text("edited\n", encoding="utf-8")
    unrelated = fake_repo / "unrelated.env"
    unrelated.write_text("leave this local\n", encoding="utf-8")
    before = _head(fake_repo)
    index_lock = fake_repo / ".git" / "index.lock"
    index_lock.touch()

    spec = importlib.util.spec_from_file_location("auto_commit_retry", AUTO_COMMIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_git_ops", go)
    monkeypatch.setenv("TEAMMODE_HOME", str(fake_repo))
    payload = {
        "event": "PostToolUse", "action": "file_edit", "agent": "claude",
        "files": [str(fake_repo / "init.txt")],
        "session_id": "session-alice", "tool_use_id": "call-alice",
    }
    monkeypatch.setattr("sys.stdin", _StdinStub(json.dumps(payload)))
    waits = []

    def release_index_lock(delay):
        waits.append(delay)
        assert "index.lock" in go.read_last_sync_error(str(fake_repo))
        assert _head(fake_repo) == before
        assert _head(local_origin) == before
        assert _git(fake_repo, "diff", "--cached", "--name-only").stdout == ""
        index_lock.unlink()

    # Replace only this hook's delay, keeping real Git and mutex operations.
    monkeypatch.setattr(mod, "_time", SimpleNamespace(sleep=release_index_lock))

    assert mod.main() == 0

    assert waits == [1]
    assert _head(fake_repo) != before
    assert _head(fake_repo) == _head(local_origin)
    assert _git(local_origin, "show", "main:init.txt").stdout == "edited\n"
    assert _git(fake_repo, "show", "--format=", "--name-only", "HEAD").stdout == (
        "init.txt\n")
    assert _git(fake_repo, "status", "--porcelain=v1", "--",
                "unrelated.env").stdout == "?? unrelated.env\n"
    assert unrelated.read_text(encoding="utf-8") == "leave this local\n"
    assert go.read_last_sync_error(str(fake_repo)) == ""


def test_auto_commit_nonblocking_on_git_failure(tmp_path):
    """실패 비차단: git 레포 아닌 곳이어도 예외 없이 exit 0."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / ".teammode-active").write_text("")
    (plain / "f.md").write_text("x\n")
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(plain / "f.md")], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, plain)
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr


def test_auto_commit_ignores_non_file_edit(fake_repo):
    """file_edit 아닌 발동(예 UserPromptSubmit 매처 외)은 무시."""
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "z.md").write_text("x\n")
    before = _commit_count(fake_repo)
    payload = {"event": "PostToolUse", "action": "shell_exec",
               "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
    assert proc.returncode == 0
    assert _commit_count(fake_repo) == before


@pytest.mark.parametrize("active", [False, True])
def test_auto_commit_noop_does_not_release_external_edit_mutex(
        fake_repo, monkeypatch, tmp_path, active):
    """An ignored event cannot release a lease owned outside auto-commit."""
    sys.path.insert(0, str(REPO / "infra"))
    import git_ops as go  # noqa: E402

    if active:
        (fake_repo / ".teammode-active").touch()
    payload = {
        "event": "PostToolUse", "action": "shell_exec", "agent": "codex",
        "session_id": "session-release", "tool_use_id": "tool-release",
    }
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    token = go.hook_edit_mutex_token(payload)
    assert go.acquire_edit_mutex(str(fake_repo), token) is True

    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)

    assert proc.returncode == 0
    assert go.owns_edit_mutex(str(fake_repo), token) is True


def test_auto_commit_no_files_is_noop(fake_repo):
    """files 가 비면 스테이징할 게 없으니 커밋 안 함(우아하게 exit 0)."""
    (fake_repo / ".teammode-active").write_text("")
    before = _commit_count(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
    assert proc.returncode == 0
    assert _commit_count(fake_repo) == before


def test_auto_commit_bad_stdin_no_crash(fake_repo):
    (fake_repo / ".teammode-active").write_text("")
    proc = subprocess.run(
        [PY, str(AUTO_COMMIT)], input="not json{", capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(fake_repo)})
    assert proc.returncode == 0


# ════════════════════════════════════════════════════════════════════
# confirm-action.py
# ════════════════════════════════════════════════════════════════════

def test_raw_mcp_alias_is_denied_through_claude_normalize(fake_repo):
    (fake_repo / ".teammode-active").write_text("")
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__tm-linear__create_issue",
        "tool_input": {"title": "create a real issue"},
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "TEAMMODE_CONFIRM"
    }
    env["TEAMMODE_HOME"] = str(fake_repo)

    proc = subprocess.run(
        [
            PY,
            str(CLAUDE_NORMALIZE),
            "confirm-action.py",
            "teammode-linear-create-allow",
        ],
        input=json.dumps(raw),
        capture_output=True,
        text=True,
        env=env,
        cwd=fake_repo,
    )

    assert proc.returncode == 2, proc.stderr
    decision = json.loads(proc.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"


def test_confirm_no_marker_is_noop(fake_repo):
    """빌드 안전: .teammode-active 없으면 차단도 안 함(exit 0)."""
    payload = {"event": "PreToolUse",
               "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
               "agent": "claude"}
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 0


def test_confirm_blocks_when_active(fake_repo):
    """teammode 활성 + allow 마커 없음 → 차단(exit 2 + deny JSON)."""
    (fake_repo / ".teammode-active").write_text("")
    payload = {"event": "PreToolUse",
               "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
               "agent": "claude"}
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 2  # PreToolUse 차단 시맨틱
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_confirm_allows_with_env_signal(fake_repo):
    """사람이 TEAMMODE_CONFIRM env(모델 비제어 채널)로 marker 를 남기면 통과(exit 0)."""
    (fake_repo / ".teammode-active").write_text("")
    payload = {"event": "PreToolUse",
               "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
               "agent": "claude"}
    proc = subprocess.run(
        [PY, str(CONFIRM), "teammode-linear-create-allow"],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(fake_repo),
             "TEAMMODE_CONFIRM": "teammode-linear-create-allow"})
    assert proc.returncode == 0


def test_confirm_allows_with_fresh_signal_file(fake_repo):
    """사람이 의식적으로 신호 파일(.teammode-confirm/<marker>)을 생성하면 통과(exit 0)."""
    (fake_repo / ".teammode-active").write_text("")
    confirm_dir = fake_repo / ".teammode-confirm"
    confirm_dir.mkdir()
    (confirm_dir / "teammode-linear-create-allow").write_text("")
    payload = {"event": "PreToolUse",
               "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
               "agent": "claude"}
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 0


def test_confirm_stale_signal_file_still_blocks(fake_repo):
    """신호 파일이 신선도(300s)를 넘기면 무효 → 차단(재확인 강제)."""
    import time as _time
    (fake_repo / ".teammode-active").write_text("")
    confirm_dir = fake_repo / ".teammode-confirm"
    confirm_dir.mkdir()
    flag = confirm_dir / "teammode-linear-create-allow"
    flag.write_text("")
    stale = _time.time() - 10_000  # TTL(300s) 한참 초과
    os.utime(flag, (stale, stale))
    payload = {"event": "PreToolUse",
               "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
               "agent": "claude"}
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 2


def test_confirm_model_token_in_raw_cannot_bypass(fake_repo):
    """보안 잠금(P1): 모델이 raw.tool_input 의 임의 필드에 allow 토큰을 넣어도
    **여전히 차단**(exit 2). allow 판정은 모델 비제어 채널만 보므로 우회 불가."""
    (fake_repo / ".teammode-active").write_text("")
    # 모델이 제어 가능한 모든 자리에 토큰을 박아넣은 적대적 페이로드.
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
        "raw": {
            "tool_name": "mcp__linear__create_issue",
            "tool_input": {
                "title": "see teammode-linear-create-allow ticket",
                "description": "teammode-linear-create-allow teammode-linear-create-allow",
                "note": "teammode-linear-create-allow",
            },
        },
    }
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 2  # 우회 불가 — 차단 유지
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_confirm_unrelated_mcp_passes(fake_repo):
    """P2: 대상 액션(linear/create_issue) 이 아닌 무관 MCP(notion/delete_page)는
    server·name 검사로 통과(exit 0) — 무관 액션을 막지 않는다."""
    (fake_repo / ".teammode-active").write_text("")
    payload = {"event": "PreToolUse",
               "tool": {"kind": "mcp", "server": "notion", "name": "delete_page"},
               "agent": "claude"}
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 0


def test_confirm_ignores_non_pretooluse(fake_repo):
    (fake_repo / ".teammode-active").write_text("")
    payload = {"event": "PostToolUse", "agent": "claude"}
    proc = _run_hook(CONFIRM, payload, fake_repo, args=["teammode-linear-create-allow"])
    assert proc.returncode == 0


def test_confirm_bad_stdin_no_block(fake_repo):
    """파싱 불가 입력은 차단하지 않는다(normalize strict 가 상위 게이트)."""
    (fake_repo / ".teammode-active").write_text("")
    proc = subprocess.run(
        [PY, str(CONFIRM), "teammode-linear-create-allow"], input="bad{",
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(fake_repo)})
    assert proc.returncode == 0


class _StdinStub:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text
