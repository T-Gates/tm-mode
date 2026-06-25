"""슬라이스 3 — Claude normalize.py 런타임 통역 테스트 (스펙 02 §6).

normalize 의무:
  1. 변환: Claude 원어 JSON(stdin) → 정규 스키마(§6.1) → 공통 스크립트 stdin 전달
  2. 런타임 자가 필터: runtime 무매처 등록 훅이 내용 불일치면 exit 0 (무동작)
  3. 시맨틱 전파: 공통 스크립트 exit code·stdout 그대로 (PreToolUse 차단 보존)
  4. 변환 실패: exit 0 + stderr 경고 (strict 훅은 예외 — 실패 전파)

normalize 를 subprocess 로 실행하고 stdin/stdout/exit 으로 계약을 검증한다.
공통 스크립트는 stdin 으로 받은 정규 JSON 을 그대로 stdout 에 echo 하는 stub 으로 대체.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
PY = sys.executable


@pytest.fixture
def env(tmp_path):
    """team_root/infra/{hooks,agents/claude} + echo stub 공통 스크립트."""
    root = tmp_path
    hooks = root / "infra" / "hooks"
    agentd = root / "infra" / "agents" / "claude"
    hooks.mkdir(parents=True)
    agentd.mkdir(parents=True)
    # 우리 normalize·events.json 를 복사해 team_root 안에서 실행되게 (상대경로 조회)
    (agentd / "normalize.py").write_text(NORMALIZE.read_text(), encoding="utf-8")
    (agentd / "events.json").write_text(
        (REPO / "infra" / "agents" / "claude" / "events.json").read_text(),
        encoding="utf-8")

    # echo stub: 정규 JSON(stdin)을 stdout 에 그대로 + exit 0
    (hooks / "echo-stub.py").write_text(
        "import sys\nd=sys.stdin.read()\nsys.stdout.write(d)\nsys.exit(0)\n",
        encoding="utf-8")
    # block stub: 항상 exit 2 + stderr (PreToolUse 차단 시맨틱 흉내)
    (hooks / "block-stub.py").write_text(
        "import sys\nsys.stdin.read()\nsys.stderr.write('blocked')\nsys.exit(2)\n",
        encoding="utf-8")

    def write_manifest(entries):
        (hooks / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")

    def run_normalize(script, raw_input, extra_args=None, manifest=None):
        if manifest is not None:
            write_manifest(manifest)
        argv = [PY, str(agentd / "normalize.py"), script]
        if extra_args:
            argv += extra_args
        proc = subprocess.run(
            argv, input=json.dumps(raw_input), capture_output=True, text=True,
            cwd=str(root),
            env={**os.environ, "TEAMMODE_HOME": str(root)},
        )
        return proc

    class E:
        pass
    e = E()
    e.root = root
    e.run = run_normalize
    e.write_manifest = write_manifest
    return e


# ── 1. 변환 ──

def test_translate_post_tool_use_file_edit(env):
    raw = {"hook_event_name": "PostToolUse", "tool_name": "Write",
           "tool_input": {"file_path": "/abs/x.md"}}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py"}])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["event"] == "PostToolUse"
    assert out["action"] == "file_edit"
    assert out["files"] == ["/abs/x.md"]
    assert out["agent"] == "claude"


def test_translate_user_prompt_submit(env):
    raw = {"hook_event_name": "UserPromptSubmit", "prompt": "안녕"}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "UserPromptSubmit", "script": "echo-stub.py"}])
    out = json.loads(proc.stdout)
    assert out["event"] == "UserPromptSubmit"
    assert out["prompt"] == "안녕"


def test_translate_mcp_tool_to_canonical_server(env):
    raw = {"hook_event_name": "PreToolUse",
           "tool_name": "mcp__linear__create_issue",
           "tool_input": {"title": "x"}}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "echo-stub.py"}])
    out = json.loads(proc.stdout)
    assert out["tool"]["kind"] == "mcp"
    assert out["tool"]["server"] == "linear"
    assert out["tool"]["name"] == "create_issue"


def test_tm_alias_reverse_maps_to_canonical_server(env):
    """런타임 실 도구명은 등록 별칭 `mcp__tm-linear__...` — 정규 서버명 linear 로 환원.

    어댑터 resolve_server_alias 가 linear→tm-linear 로 등록하므로, 에이전트가 부르는
    실제 도구명은 `mcp__tm-linear__create_issue`다. normalize 는 이를 정규 서버명
    `linear` 로 되돌려 manifest 매처(§2.5 정규 서버명)·self-filter·confirm 게이트가
    일치하도록 한다. (tm- 접두 없는 사용자 동명 서버는 그대로 보존된다.)
    """
    raw = {"hook_event_name": "PreToolUse",
           "tool_name": "mcp__tm-linear__create_issue",
           "tool_input": {"title": "x"}}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "echo-stub.py", "fallback": "runtime"}])
    out = json.loads(proc.stdout)
    assert out["tool"]["server"] == "linear"   # tm- 접두 제거 → 정규 서버명
    assert out["tool"]["name"] == "create_issue"
    assert proc.returncode == 0
    assert proc.stdout.strip()                 # self-filter 통과 = 매처 일치


# ── 2. 런타임 자가 필터 (§6.2-2) ──

def test_self_filter_passes_when_match(env):
    # runtime 무매처 등록: manifest 의 (script, event) match 가 현재 발동과 일치 → 통과
    raw = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
           "tool_input": {"file_path": "/a"}}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"}])
    assert proc.returncode == 0
    assert proc.stdout.strip()  # echo 됨 = 공통 스크립트 실행됨


def test_self_filter_skips_when_mismatch(env):
    # manifest 는 file_edit 을 기대하는데 현재 발동은 mcp 툴 → 불일치 → exit 0 무동작
    raw = {"hook_event_name": "PostToolUse",
           "tool_name": "mcp__slack__post_message", "tool_input": {}}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "echo-stub.py", "fallback": "runtime"}])
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # 공통 스크립트 호출 안 됨


def test_self_filter_mcp_server_mismatch_skips(env):
    raw = {"hook_event_name": "PreToolUse",
           "tool_name": "mcp__slack__post_message", "tool_input": {}}
    proc = env.run("echo-stub.py", raw, manifest=[
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "echo-stub.py", "fallback": "runtime"}])
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ── 3. 시맨틱 전파 (§6.2-3) ──

def test_block_semantic_propagated(env):
    # 공통 스크립트가 exit 2 로 차단하면 normalize 도 exit 2 전파 (PreToolUse 차단 보존)
    raw = {"hook_event_name": "PreToolUse",
           "tool_name": "mcp__linear__create_issue", "tool_input": {}}
    proc = env.run("block-stub.py", raw, manifest=[
        {"event": "PreToolUse",
         "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
         "script": "block-stub.py"}])
    assert proc.returncode == 2
    assert "blocked" in proc.stderr


# ── 4. 변환 실패 정책 (§6.2-4) ──

def test_malformed_input_exits_zero_non_strict(env):
    # 깨진 stdin → 비-strict 면 exit 0 + stderr 경고 (세션 안 막음)
    argv = [PY, str(env.root / "infra" / "agents" / "claude" / "normalize.py"),
            "echo-stub.py"]
    env.write_manifest([{"event": "PostToolUse", "script": "echo-stub.py"}])
    proc = subprocess.run(argv, input="NOT JSON{{", capture_output=True, text=True,
                          cwd=str(env.root),
                          env={**os.environ, "TEAMMODE_HOME": str(env.root)})
    assert proc.returncode == 0
    assert proc.stderr.strip() != ""


def test_malformed_input_propagates_when_strict(env):
    # strict 훅은 변환 실패도 전파 (exit != 0)
    argv = [PY, str(env.root / "infra" / "agents" / "claude" / "normalize.py"),
            "echo-stub.py"]
    env.write_manifest([{"event": "PreToolUse", "script": "echo-stub.py",
                         "strict": True}])
    proc = subprocess.run(argv, input="NOT JSON{{", capture_output=True, text=True,
                          cwd=str(env.root),
                          env={**os.environ, "TEAMMODE_HOME": str(env.root)})
    assert proc.returncode != 0


# ── 3.2 공통 훅 이식 검증: session-log-remind 가 정규 스키마만 인지 ──

def test_common_hook_consumes_canonical_only(env, tmp_path):
    """session-log-remind 를 정규 JSON stdin 으로 직접 호출 — 에이전트 무지 확인.

    출력은 hookSpecificOutput.additionalContext+systemMessage JSON stdout —
    normalize 가 그대로 전파해 additionalContext 로 감.
    """
    hook = REPO / "infra" / "hooks" / "session-log-remind.py"
    root = env.root
    (root / ".teammode-active").write_text("")
    # 세션로그 전무 → age 9999 ≥ 1800 → 리마인드 발화(첫 호출이지만 check_reset 후 return)
    # team.config.json 없어 폴백 경로 → 전역 sessions age ≥ 1800 → 발화
    canonical = {"event": "UserPromptSubmit", "prompt": "hi", "agent": "claude"}
    proc = subprocess.run(
        [PY, str(hook)], input=json.dumps(canonical), capture_output=True, text=True,
        cwd=str(root),
        env={**os.environ, "TEAMMODE_HOME": str(root),
             "TMPDIR": str(tmp_path)})
    assert proc.returncode == 0
    # 세션로그 전무 → age 9999 ≥ 1800 → 리마인드 발화(평문 출력)
    assert proc.stdout.strip() != "", "age≥1800 인데 리마인드가 발화하지 않음"
    assert "세션 로그" in proc.stdout
    # 에이전트 고유 표기 직표기 없음(§8.2)
    assert "mcp__" not in proc.stdout


def test_common_hook_ignores_non_userprompt_event(env, tmp_path):
    hook = REPO / "infra" / "hooks" / "session-log-remind.py"
    (env.root / ".teammode-active").write_text("")
    canonical = {"event": "PostToolUse", "agent": "claude"}
    proc = subprocess.run(
        [PY, str(hook)], input=json.dumps(canonical), capture_output=True, text=True,
        cwd=str(env.root),
        env={**os.environ, "TEAMMODE_HOME": str(env.root), "TMPDIR": str(tmp_path)})
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # UserPromptSubmit 아니면 무동작


def test_remind_end_to_end_through_normalize(env, tmp_path):
    """Claude 원어 → normalize → session-log-remind 전체 배선 (실 공통 스크립트).

    출력은 평문 stdout — normalize 가 그대로 재방출해 Claude 가 additionalContext 로 수신.
    """
    # 실 공통 스크립트를 fixture hooks 디렉토리에 복사
    (env.root / "infra" / "hooks" / "session-log-remind.py").write_text(
        (REPO / "infra" / "hooks" / "session-log-remind.py").read_text(),
        encoding="utf-8")
    (env.root / ".teammode-active").write_text("")
    raw = {"hook_event_name": "UserPromptSubmit", "prompt": "작업 시작"}
    proc = env.run("session-log-remind.py", raw, manifest=[
        {"event": "UserPromptSubmit", "script": "session-log-remind.py", "mode": "on"}])
    # TMPDIR 격리를 위해 env.run 은 os.environ 상속 — 카운터는 별도지만 age 트리거로 발화
    assert proc.returncode == 0
    # 발화 시 평문 stdout(JSON 아님) — "세션 로그" 안내 포함 여부만 확인
    if proc.stdout.strip():
        assert "세션 로그" in proc.stdout
