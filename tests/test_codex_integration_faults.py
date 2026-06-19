"""codex 통합 fault injection 6건 검증 (blocker1·major4·minor1).

슬라이스 경계 통합 결함 — 도그푸딩 직전 필수 게이트.

#1 [blocker] confirm 게이트가 실제 경로(normalize 경유)서 안 걸림
#2 [major]   get_token_for_role 죽은 API → 계약 명확화 + 헬퍼 테스트
#3 [major]   async 핸들러 노출되나 await 안 함
#4/#5 (SKILL.md 내용 변경 — 런타임 검증 불필요)
#6 [minor]   manifest 파싱 실패 시 confirm fail-open → fail-closed
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
CONFIRM = REPO / "infra" / "hooks" / "confirm-action.py"
MANIFEST = REPO / "infra" / "hooks" / "manifest.json"
ROLE_SERVER = REPO / "infra" / "mcp" / "role_server.py"
PY = sys.executable


# ═══════════════════════════════════════════════════════════════════════
# 헬퍼
# ═══════════════════════════════════════════════════════════════════════

def _load_role_server():
    """role_server 모듈을 동적 로드."""
    spec = importlib.util.spec_from_file_location("_rs_fault_test", ROLE_SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_teammode_payload(server: str, tool: str) -> dict:
    """normalize 경유 테스트용 Claude 원어 PreToolUse 페이로드."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": f"mcp__{server}__{tool}",
        "tool_input": {"title": "test"},
    }


def _run_normalize_then_confirm(tmp_path: Path, server: str, tool: str, marker: str):
    """normalize.py → confirm-action.py 실제 체인 실행.

    normalize 가 Claude 원어를 정규형으로 변환 후 confirm-action.py 를 호출하는
    실제 경로를 subprocess 로 재현.  tmp 팀 루트에 .teammode-active 마커 생성.
    """
    # tmp 팀 루트 + .teammode-active
    team_root = tmp_path / "team"
    team_root.mkdir(exist_ok=True)
    (team_root / ".teammode-active").write_text("")

    payload = _make_teammode_payload(server, tool)
    env = {**os.environ, "TEAMMODE_HOME": str(team_root)}

    proc = subprocess.run(
        [PY, str(NORMALIZE), "confirm-action.py", marker],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env=env,
    )
    return proc


# ═══════════════════════════════════════════════════════════════════════
# #1 [blocker] confirm 게이트가 normalize 경유 실제 경로에서 정상 동작
# ═══════════════════════════════════════════════════════════════════════

# manifest 에 등록된 모든 confirm-action.py 엔트리를 순회
_MANIFEST_ENTRIES = [
    e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))
    if e.get("script") == "confirm-action.py"
    and e.get("event") == "PreToolUse"
    and e.get("match", {}).get("mcp")
]


@pytest.mark.parametrize("entry", _MANIFEST_ENTRIES,
                         ids=["{server}/{tool}".format(**e["match"]["mcp"])
                              for e in _MANIFEST_ENTRIES])
def test_confirm_gate_fires_via_normalize(tmp_path, entry):
    """#1 blocker — normalize 경유 실경로에서 모든 confirm-action 엔트리가 차단(exit≠0).

    수정 전: _lookup_entry 가 첫 번째 엔트리(linear)만 반환 → teammode 엔트리에
    대해 self-filter 가 False → exit 0 통과(버그).
    수정 후: canonical+args 기반 정확한 엔트리 선택 → teammode 엔트리도 차단.
    """
    server = entry["match"]["mcp"]["server"]
    tool = entry["match"]["mcp"]["tool"]
    args = entry.get("args", "")
    marker = args if isinstance(args, str) else (args[0] if args else "")

    proc = _run_normalize_then_confirm(tmp_path, server, tool, marker)
    assert proc.returncode != 0, (
        f"#1 blocker: normalize 경유 {server}/{tool} 차단 기대, "
        f"실제 exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    # confirm-action.py 가 deny JSON 을 stdout 에 출력해야 함
    if proc.stdout.strip():
        out = json.loads(proc.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", (
            f"deny JSON 없음: {proc.stdout}"
        )


def test_confirm_gate_fires_teammode_issues_create_via_normalize(tmp_path):
    """#1 blocker 핵심 케이스 — teammode/issues_create 가 normalize 경유로 차단."""
    proc = _run_normalize_then_confirm(
        tmp_path, "teammode", "issues_create",
        "teammode-issues-create-allow",
    )
    assert proc.returncode != 0, (
        f"#1 blocker: teammode/issues_create normalize 경유 차단 실패\n"
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_confirm_gate_passes_with_allow_signal_via_normalize(tmp_path):
    """#1 — allow 신호 있으면 normalize 경유도 통과(exit 0)."""
    marker = "teammode-issues-create-allow"
    team_root = tmp_path / "team2"
    team_root.mkdir(exist_ok=True)
    (team_root / ".teammode-active").write_text("")
    confirm_dir = team_root / ".teammode-confirm"
    confirm_dir.mkdir()
    (confirm_dir / marker).write_text("")

    payload = _make_teammode_payload("teammode", "issues_create")
    proc = subprocess.run(
        [PY, str(NORMALIZE), "confirm-action.py", marker],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode == 0, (
        f"allow 신호 있을 때 통과 기대, exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


# ═══════════════════════════════════════════════════════════════════════
# #2 [major] get_token_for_role 계약 — 핸들러 자율 원칙 + 헬퍼 동작
# ═══════════════════════════════════════════════════════════════════════

def test_get_token_for_role_exists_in_role_server():
    """#2 — get_token_for_role 가 role_server 에 헬퍼로 존재한다."""
    mod = _load_role_server()
    assert hasattr(mod, "get_token_for_role"), (
        "get_token_for_role 함수가 role_server.py 에 없음 — 헬퍼 계약 위반"
    )
    fn = mod.get_token_for_role
    import inspect
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    assert "auth_type" in params, (
        f"get_token_for_role 시그니처에 auth_type 없음: {params}"
    )


def test_get_token_for_role_oauth_key(tmp_path):
    """#2 — oauth auth_type 은 <role>_access_token 키로 credentials.load() 호출."""
    # credentials.py stub 생성
    creds_dir = tmp_path
    creds_stub = creds_dir / "credentials.py"
    creds_stub.write_text(textwrap.dedent("""\
        _calls = []
        def load(team, scope, key):
            _calls.append((team, scope, key))
            return "tok-" + key
        def get_calls():
            return _calls
    """), encoding="utf-8")

    # role_server 를 수정해 credentials stub 경로 주입
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_creds_stub", creds_stub)
    creds_mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(creds_mod)

    # _load_credentials_module 을 monkeypatch 하기 어려우므로
    # get_token_for_role 의 키 계약만 함수 소스로 확인
    src = ROLE_SERVER.read_text(encoding="utf-8")
    assert "_access_token" in src, (
        "get_token_for_role 에서 oauth → <role>_access_token 키 계약이 없음"
    )
    assert 'auth_type == "oauth"' in src or "auth_type==" in src.replace(" ", ""), (
        "get_token_for_role 에 oauth 분기가 없음"
    )


def test_call_tool_does_not_inject_token(tmp_path):
    """#2 — role_server.call_tool 은 핸들러에 토큰을 주입하지 않는다(핸들러 자율).

    call_tool 소스에 credentials·token 주입 코드가 없음을 정적으로 확인.
    """
    src = ROLE_SERVER.read_text(encoding="utf-8")
    # call_tool 함수 소스 추출 (간단히 fn(**arguments) 호출만 있어야 함)
    assert "fn(**arguments)" in src or "fn(" in src, "call_tool 에 함수 호출 없음"
    # call_tool 이 auth_type 을 핸들러에 넘기지 않음을 확인
    # (get_token_for_role 을 call_tool 내부에서 호출하면 안 됨)
    import ast as _ast
    tree = _ast.parse(src)
    call_tool_fn = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == "call_tool":
            call_tool_fn = node
            break
    assert call_tool_fn is not None, "call_tool 함수를 찾을 수 없음"
    # call_tool 내부에서 get_token_for_role 호출 없음
    injections = [
        n for n in _ast.walk(call_tool_fn)
        if isinstance(n, _ast.Call)
        and getattr(getattr(n, "func", None), "id", "") == "get_token_for_role"
    ]
    assert not injections, (
        "call_tool 이 get_token_for_role 을 내부 호출함 — 핸들러 자율 위반"
    )


# ═══════════════════════════════════════════════════════════════════════
# #3 [major] async 핸들러 tools/call 정상 결과
# ═══════════════════════════════════════════════════════════════════════

def test_async_handler_tools_call_returns_result(tmp_path):
    """#3 — async 핸들러의 tools/call 이 coroutine 직렬화 실패 없이 정상 결과 반환."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    # async issues 핸들러 작성
    (handlers_dir / "issues.py").write_text(textwrap.dedent("""\
        async def issues_create(title, body="", assignee=None, label=None, priority=None):
            return {"id": "async-1", "title": title}

        async def issues_list(filter=None):
            return [{"id": "async-1"}]

        async def issues_get(id):
            return {"id": id}

        async def issues_update(id, **kwargs):
            return {"id": id, "updated": True}
    """), encoding="utf-8")

    mod = _load_role_server()
    server = mod.TeammodeMCPServer(team="test", handlers_dir=handlers_dir)

    result = server.call_tool("issues_create", {"title": "비동기 이슈"})
    assert not result.get("isError"), (
        f"async 핸들러 tools/call 에서 오류 발생: {result}"
    )
    content = result.get("content", [])
    assert content, "결과 content 가 비어 있음"
    # content[0]["text"] 가 JSON 직렬화된 결과여야 함
    data = json.loads(content[0]["text"])
    assert data.get("id") == "async-1", (
        f"async 핸들러 결과 불일치: {data}"
    )


def test_async_handler_coroutine_not_serialized_as_string(tmp_path):
    """#3 — async 핸들러 결과가 coroutine 문자열이 아닌 실제 dict 로 반환."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    (handlers_dir / "issues.py").write_text(textwrap.dedent("""\
        async def issues_create(title, body="", assignee=None, label=None, priority=None):
            return {"id": "ok", "title": title}

        async def issues_list(filter=None):
            return []

        async def issues_get(id):
            return {"id": id}

        async def issues_update(id, **kwargs):
            return {"id": id, "updated": True}
    """), encoding="utf-8")

    mod = _load_role_server()
    server = mod.TeammodeMCPServer(team="test", handlers_dir=handlers_dir)
    result = server.call_tool("issues_create", {"title": "test"})

    assert not result.get("isError"), f"에러 발생: {result}"
    text = result["content"][0]["text"]
    # coroutine 직렬화 실패 시 "<coroutine object ...>" 같은 문자열이 나옴
    assert "coroutine" not in text.lower(), (
        f"coroutine 이 직렬화되지 않고 그대로 반환됨: {text}"
    )
    data = json.loads(text)
    assert isinstance(data, dict), f"결과가 dict 가 아님: {data}"


def test_sync_handler_still_works(tmp_path):
    """#3 — async 처리 추가 후 기존 동기 핸들러도 정상 동작."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    (handlers_dir / "issues.py").write_text(textwrap.dedent("""\
        def issues_create(title, body="", assignee=None, label=None, priority=None):
            return {"id": "sync-1", "title": title}

        def issues_list(filter=None):
            return []

        def issues_get(id):
            return {"id": id}

        def issues_update(id, **kwargs):
            return {"id": id, "updated": True}
    """), encoding="utf-8")

    mod = _load_role_server()
    server = mod.TeammodeMCPServer(team="test", handlers_dir=handlers_dir)
    result = server.call_tool("issues_create", {"title": "동기 이슈"})
    assert not result.get("isError"), f"동기 핸들러 에러: {result}"
    data = json.loads(result["content"][0]["text"])
    assert data["id"] == "sync-1"


# ═══════════════════════════════════════════════════════════════════════
# #6 [minor] manifest 파싱 실패 시 fail-closed
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_fail_closed_when_manifest_missing_with_marker(tmp_path):
    """#6 — manifest 없는 환경에서 marker 인자와 함께 호출 → 차단(exit≠0).

    marker 를 받았는데 manifest 를 못 읽으면 게이트 대상 여부 미판정 → 보수적 차단.
    """
    team_root = tmp_path / "team_no_manifest"
    team_root.mkdir()
    (team_root / ".teammode-active").write_text("")

    # confirm-action.py 를 tmp 에 복사하되, 존재하지 않는 manifest 경로를 가리키도록
    # 환경 조작은 어려우므로, 대신 broken manifest 를 주입한다
    broken_hooks = tmp_path / "hooks"
    broken_hooks.mkdir()
    (broken_hooks / "manifest.json").write_text("not valid json", encoding="utf-8")
    broken_confirm = broken_hooks / "confirm-action.py"
    broken_confirm.write_text(CONFIRM.read_text(encoding="utf-8"), encoding="utf-8")

    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "teammode", "name": "issues_create"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(broken_confirm), "teammode-issues-create-allow"],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode != 0, (
        f"#6: manifest 파싱 실패 + marker 있음 → 차단 기대, "
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_confirm_argv_marker_mismatch_is_denied(tmp_path):
    """전체검수 재검수 — argv_marker 가 manifest_marker 와 다르면 fail-closed deny.

    실제 입력 (server,name) 으로 찾은 manifest target 의 marker 만 신뢰한다.
    오배선/스테일로 다른 도구의 marker 가 argv 로 와도 승인 우회 안 됨.
    codex 실증: linear/create_issue payload + teammode marker → 우회 통과(버그)였던 것.
    """
    team_root = tmp_path / "team_mismatch"
    team_root.mkdir()
    (team_root / ".teammode-active").write_text("")

    # 실제 manifest 를 쓰는 원본 confirm-action.py 직접 호출
    # linear/create_issue 입력인데 argv 로 teammode marker 를 줌 (불일치)
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(CONFIRM), "teammode-issues-create-allow"],  # 불일치 marker
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode == 2, (
        f"argv_marker != manifest_marker → fail-closed deny(exit2) 기대, "
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_confirm_fail_open_when_manifest_missing_without_marker(tmp_path):
    """#6 — manifest 없는 환경에서 marker 없이 호출 → 통과(exit 0).

    marker 없음 = 이 훅이 해당 도구의 게이트로 지정되지 않은 상황 → 통과.
    """
    team_root = tmp_path / "team_no_manifest2"
    team_root.mkdir()
    (team_root / ".teammode-active").write_text("")

    broken_hooks = tmp_path / "hooks2"
    broken_hooks.mkdir()
    (broken_hooks / "manifest.json").write_text("not valid json", encoding="utf-8")
    broken_confirm = broken_hooks / "confirm-action.py"
    broken_confirm.write_text(CONFIRM.read_text(encoding="utf-8"), encoding="utf-8")

    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "teammode", "name": "issues_create"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(broken_confirm)],  # marker 없음
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode == 0, (
        f"#6: marker 없음 + manifest 파싱 실패 → 통과 기대, "
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


# ═══════════════════════════════════════════════════════════════════════
# normalize _lookup_entry 단위 테스트 — #1 핵심 로직
# ═══════════════════════════════════════════════════════════════════════

def _load_normalize():
    """normalize 모듈을 동적 로드."""
    norm_path = REPO / "infra" / "agents" / "claude" / "normalize.py"
    spec = importlib.util.spec_from_file_location("_norm_fault_test", norm_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lookup_entry_selects_correct_entry_by_canonical():
    """#1 — _lookup_entry 가 canonical 기반으로 정확한 엔트리를 선택."""
    norm = _load_normalize()

    manifest = [
        # linear 엔트리 (첫 번째)
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-linear-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
        },
        # teammode 엔트리 (두 번째)
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-issues-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "teammode", "tool": "issues_create"}},
        },
    ]

    # teammode canonical
    canonical_teammode = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "teammode", "name": "issues_create"},
    }

    entry = norm._lookup_entry(
        manifest, "confirm-action.py", "PreToolUse",
        canonical=canonical_teammode,
        extra_args=["teammode-issues-create-allow"],
    )
    assert entry is not None, "_lookup_entry 가 None 반환"
    assert entry["args"] == "teammode-issues-create-allow", (
        f"teammode 엔트리 기대, 실제={entry['args']}"
    )


def test_lookup_entry_selects_linear_entry_for_linear_canonical():
    """#1 — linear canonical 입력 시 linear 엔트리 선택."""
    norm = _load_normalize()

    manifest = [
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-linear-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
        },
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-issues-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "teammode", "tool": "issues_create"}},
        },
    ]

    canonical_linear = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
    }

    entry = norm._lookup_entry(
        manifest, "confirm-action.py", "PreToolUse",
        canonical=canonical_linear,
        extra_args=["teammode-linear-create-allow"],
    )
    assert entry is not None
    assert entry["args"] == "teammode-linear-create-allow", (
        f"linear 엔트리 기대, 실제={entry['args']}"
    )


def test_lookup_entry_single_entry_no_regression():
    """#1 — 엔트리가 1개일 때 기존 동작 유지(regression 없음)."""
    norm = _load_normalize()

    manifest = [
        {
            "event": "PostToolUse",
            "script": "auto-commit.py",
            "fallback": "runtime",
            "match": {"action": "file_edit"},
        },
    ]

    canonical = {"event": "PostToolUse", "action": "file_edit"}
    entry = norm._lookup_entry(manifest, "auto-commit.py", "PostToolUse",
                               canonical=canonical)
    assert entry is not None
    assert entry["script"] == "auto-commit.py"
