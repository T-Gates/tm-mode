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
MANIFEST = HOOKS / "manifest.json"
PY = sys.executable


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
    _git(root, "init")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@t")
    (root / "init.txt").write_text("init\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def _run_hook(script, payload, root, args=None):
    argv = [PY, str(script)]
    if args:
        argv += args
    return subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(root)},
    )


def _head(root):
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit_count(root):
    return int(_git(root, "rev-list", "--count", "HEAD").stdout.strip())


# ════════════════════════════════════════════════════════════════════
# auto-commit.py
# ════════════════════════════════════════════════════════════════════

def test_auto_commit_no_marker_is_noop(fake_repo):
    """빌드 안전 핵심: .teammode-active 없으면 절대 커밋하지 않는다(no-op exit 0)."""
    (fake_repo / "edited.txt").write_text("change\n")
    before = _head(fake_repo)
    payload = {"event": "PostToolUse", "action": "file_edit",
               "files": [str(fake_repo / "edited.txt")], "agent": "claude"}
    proc = _run_hook(AUTO_COMMIT, payload, fake_repo)
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


def test_auto_commit_pushes_nonblocking(fake_repo, monkeypatch, tmp_path):
    """6/23 자동push 철학: auto-commit 은 do_commit(push=True) 로 호출하고,
    push 실패해도 비차단(exit 0)·로컬 커밋 보존이다.

    fake_repo 는 원격이 없어 push 가 실패하지만, do_commit 이 커밋을 보존하므로
    훅은 exit 0 으로 끝나고 변경은 커밋된다.
    """
    (fake_repo / ".teammode-active").write_text("")
    (fake_repo / "p.md").write_text("x\n")
    sys.path.insert(0, str(REPO / "infra"))
    import git_ops as go  # noqa: E402

    calls = {}
    real = go.do_commit

    def spy(team_root, message, push=False, timeout=go.DEFAULT_TIMEOUT, paths=None):
        calls["push"] = push
        return real(team_root, message, push=push, timeout=timeout, paths=paths)

    # 서브프로세스가 아닌 in-proc 로 훅 main 을 직접 호출해 do_commit 인자를 검사한다.
    monkeypatch.setattr(go, "do_commit", spy)
    monkeypatch.setenv("TEAMMODE_HOME", str(fake_repo))
    import importlib.util
    spec = importlib.util.spec_from_file_location("auto_commit_mod", AUTO_COMMIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_git_ops", go)
    monkeypatch.setattr("sys.stdin", _StdinStub(json.dumps({
        "event": "PostToolUse", "action": "file_edit",
        "files": [str(fake_repo / "p.md")], "agent": "claude"})))
    rc = mod.main()
    # push 실패(원격 없음)에도 비차단: exit 0
    assert rc == 0
    # 자동 push 철학: do_commit 은 push=True 로 호출됨
    assert calls.get("push") is True
    # 비차단: push 가 실패해도 로컬 커밋은 보존(p.md 가 HEAD 에 들어감)
    committed = _git(fake_repo, "show", "--name-only", "HEAD").stdout
    assert "p.md" in committed


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


# ════════════════════════════════════════════════════════════════════
# manifest 정합 (선언 ↔ 파일 일치)
# ════════════════════════════════════════════════════════════════════

def test_manifest_declared_scripts_exist():
    """manifest 가 선언한 모든 script 파일이 hooks/ 에 실재한다(G.3 정합)."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for e in entries:
        script = e.get("script")
        assert script, f"manifest 엔트리에 script 누락: {e}"
        assert (HOOKS / script).is_file(), f"선언된 script 파일 부재: {script}"


def test_manifest_includes_both_l2g_hooks():
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scripts = {e.get("script") for e in entries}
    assert "auto-commit.py" in scripts
    assert "confirm-action.py" in scripts


def test_manifest_no_duplicate_event_script_pairs():
    """normalize 자가필터 전제: 같은 (event, script, match) 조합 중복 금지(§2.10-2, lint 대상).

    S6 이후 confirm-action.py 는 도구별로 여러 엔트리를 가질 수 있다(서버/도구마다 별도 엔트리).
    중복 금지 키는 (event, script, match_json) 3-tuple 로 정밀화 — 같은 매처가 중복 등록되는
    것을 막되, 다른 도구의 엔트리는 허용한다.
    """
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    seen = set()
    for e in entries:
        # match 를 정렬된 JSON 문자열로 직렬화하여 내용 동등성 비교
        match_key = json.dumps(e.get("match"), sort_keys=True)
        key = (e.get("event"), e.get("script"), match_key)
        assert key not in seen, f"중복 (event, script, match): {key}"
        seen.add(key)


class _StdinStub:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text
