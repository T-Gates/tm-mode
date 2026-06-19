"""S3 — 단일 teammode MCP 서버 테스트.

검증 묶음:
  - initialize 핸드셰이크
  - tools/list: 핸들러 있는 역할 도구만 반환 (빈 슬롯 제외)
  - tools/call: 핸들러 함수 호출 및 결과 반환
  - 에러 경로: 없는 도구, 없는 함수, 핸들러 import 실패, malformed JSON
  - JSON-RPC 프로토콜: unknown 메서드, notification(id 없음) 무시
  - stdlib 전용 (외부 의존성 없음)

모든 테스트는 tmp_path만 사용. 실 서비스 호출 없음 (핸들러를 stub으로).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

REPO = Path(__file__).resolve().parents[1]
SERVER_MOD = REPO / "infra" / "mcp" / "role_server.py"


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _load_server():
    """role_server 모듈을 동적 로드 (sys.path 오염 없이)."""
    spec = importlib.util.spec_from_file_location("role_server", SERVER_MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_handler(tmp_path: Path, role: str, extra_funcs: str = "") -> Path:
    """지정 역할의 stub 핸들러 파일 생성, 경로 반환."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir(exist_ok=True)

    stubs = {
        "issues": dedent("""\
            def issues_create(title, body="", assignee=None, label=None, priority=None):
                return {"id": "1", "title": title}

            def issues_list(filter=None):
                return [{"id": "1"}]

            def issues_get(id):
                return {"id": id}

            def issues_update(id, **kwargs):
                return {"id": id, "updated": True}
        """),
        "chat": dedent("""\
            def chat_send(message, channel=None):
                return {"ok": True}

            def chat_list(channel=None, limit=None):
                return []
        """),
        "docs": dedent("""\
            def docs_read(id):
                return {"id": id, "content": "hello"}

            def docs_write(id, content):
                return {"id": id}

            def docs_list(query=None):
                return []

            def docs_create(title, content=None):
                return {"id": "new", "title": title}
        """),
        "calendar": dedent("""\
            def calendar_list(start, end=None):
                return []

            def calendar_create(title, start, end=None, description=None):
                return {"id": "ev1", "title": title}
        """),
    }

    body = stubs.get(role, f"# stub for {role}\n") + "\n" + extra_funcs
    handler_path = handlers_dir / f"{role}.py"
    handler_path.write_text(body, encoding="utf-8")
    return handlers_dir


def _rpc(method: str, params: dict | None = None, id: int | None = 1) -> dict:
    """JSON-RPC 2.0 요청 딕셔너리 생성."""
    req: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        req["params"] = params
    if id is not None:
        req["id"] = id
    return req


def _call_server(mod, server, requests: list[dict]) -> list[dict]:
    """서버의 handle_request를 사용해 요청 리스트를 처리, 응답 리스트 반환.

    id가 없는 notification은 None 응답 → 필터링해서 반환.
    """
    results = []
    for req in requests:
        resp = server.handle_request(req)
        if resp is not None:
            results.append(resp)
    return results


# ── 모듈 로드 테스트 ──────────────────────────────────────────────────────────

def test_module_importable():
    """infra/mcp/role_server.py 가 존재하고 import 가능해야 한다."""
    assert SERVER_MOD.exists(), f"role_server.py 없음: {SERVER_MOD}"
    mod = _load_server()
    assert hasattr(mod, "TeammodeMCPServer"), "TeammodeMCPServer 클래스 없음"


def test_no_external_dependencies():
    """role_server.py 가 stdlib 만 import해야 한다 (외부 패키지 없음)."""
    src = SERVER_MOD.read_text(encoding="utf-8")
    forbidden = ["import httpx", "import requests", "import fastmcp", "import mcp"]
    for f in forbidden:
        assert f not in src, f"외부 의존성 발견: {f}"


# ── initialize 핸드셰이크 ─────────────────────────────────────────────────────

def test_initialize_response(tmp_path):
    """initialize 요청 → serverInfo.name='teammode' 포함 응답."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("initialize", {
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "test", "version": "0"},
        "capabilities": {},
    }))

    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["serverInfo"]["name"] == "teammode"
    assert "protocolVersion" in result
    assert "capabilities" in result


def test_initialize_missing_id_is_notification(tmp_path):
    """id 없는 initialize (notification) → None 반환 (응답 없음)."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({"jsonrpc": "2.0", "method": "initialize", "params": {}})
    assert resp is None


# ── tools/list ────────────────────────────────────────────────────────────────

def test_tools_list_with_issues_only(tmp_path):
    """issues.py 만 있을 때 tools/list → issues_* 4개만 반환."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/list"))
    assert resp["id"] == 1
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}

    assert "issues_create" in names
    assert "issues_list" in names
    assert "issues_get" in names
    assert "issues_update" in names
    # 다른 역할은 없어야 한다
    assert not any(n.startswith("chat_") for n in names)
    assert not any(n.startswith("docs_") for n in names)
    assert not any(n.startswith("calendar_") for n in names)


def test_tools_list_empty_handlers_dir(tmp_path):
    """핸들러 없는 빈 디렉토리 → tools 빈 리스트."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/list"))
    tools = resp["result"]["tools"]
    assert tools == []


def test_tools_list_all_roles(tmp_path):
    """4역할 모두 있을 때 → 전체 도구(12개) 반환."""
    mod = _load_server()
    handlers_dir = None
    for role in ["issues", "chat", "docs", "calendar"]:
        handlers_dir = _make_handler(tmp_path, role)

    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)
    resp = server.handle_request(_rpc("tools/list"))
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}

    # 총 12개 도구: issues×4, chat×2, docs×4, calendar×2
    assert len(tools) == 12
    assert "issues_create" in names
    assert "chat_send" in names
    assert "docs_read" in names
    assert "calendar_list" in names


def test_tools_list_inputschema_has_type(tmp_path):
    """각 도구에 inputSchema.type == 'object' 가 있어야 한다."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/list"))
    for tool in resp["result"]["tools"]:
        assert "inputSchema" in tool, f"{tool['name']} inputSchema 없음"
        assert tool["inputSchema"].get("type") == "object"


# ── tools/call ────────────────────────────────────────────────────────────────

def test_tools_call_issues_create(tmp_path):
    """issues_create 호출 → 핸들러 함수 결과 반환."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "버그 수정", "body": "내용"},
    }))

    assert resp["id"] == 1
    content = resp["result"]["content"]
    # content는 list[{"type":"text","text":...}] 형식
    assert isinstance(content, list)
    assert len(content) >= 1
    data = json.loads(content[0]["text"])
    assert data["title"] == "버그 수정"


def test_tools_call_issues_list(tmp_path):
    """issues_list 호출 → list 반환."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_list",
        "arguments": {},
    }))
    content = resp["result"]["content"]
    data = json.loads(content[0]["text"])
    assert isinstance(data, list)


def test_tools_call_calendar_create(tmp_path):
    """calendar_create 호출 → 핸들러 결과."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "calendar")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "calendar_create",
        "arguments": {"title": "팀 회의", "start": "2026-06-19T10:00:00"},
    }))
    content = resp["result"]["content"]
    data = json.loads(content[0]["text"])
    assert data["title"] == "팀 회의"


# ── 에러 경로 ─────────────────────────────────────────────────────────────────

def test_tools_call_unknown_tool(tmp_path):
    """없는 도구명 → JSON-RPC error 응답 (서버 죽지 않음)."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "nonexistent_tool",
        "arguments": {},
    }))
    assert "error" in resp or ("result" in resp and resp["result"].get("isError"))


def test_tools_call_missing_handler(tmp_path):
    """핸들러 파일 없는 역할의 도구 호출 → 에러 응답."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    # issues.py 없이 issues_create 호출
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "test"},
    }))
    assert "error" in resp or ("result" in resp and resp["result"].get("isError"))


def test_tools_call_handler_import_error(tmp_path):
    """핸들러에 문법 오류 → import 실패 → 에러 응답 (서버 죽지 않음)."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    (handlers_dir / "issues.py").write_text("def issues_create(title\n    pass\n")  # syntax error

    mod = _load_server()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "test"},
    }))
    assert "error" in resp or ("result" in resp and resp["result"].get("isError"))


def test_tools_call_handler_raises_exception(tmp_path):
    """핸들러 함수가 예외 발생 → JSON-RPC error 로 변환 (서버 죽지 않음)."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    (handlers_dir / "issues.py").write_text(dedent("""\
        def issues_create(title, body="", assignee=None, label=None, priority=None):
            raise RuntimeError("서비스 연결 실패")

        def issues_list(filter=None):
            return []

        def issues_get(id):
            return {}

        def issues_update(id, **kwargs):
            return {}
    """))

    mod = _load_server()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "test"},
    }))
    assert "error" in resp or ("result" in resp and resp["result"].get("isError"))


def test_unknown_jsonrpc_method(tmp_path):
    """알 수 없는 JSON-RPC 메서드 → -32601 Method not found 에러."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("unknown/method", {}))
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_malformed_request_no_method(tmp_path):
    """method 없는 요청 → -32600 Invalid Request 에러."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({"jsonrpc": "2.0", "id": 1})
    assert "error" in resp
    assert resp["error"]["code"] in (-32600, -32601)


# ── JSON-RPC stdin/stdout 루프 ────────────────────────────────────────────────

def test_run_loop_via_subprocess(tmp_path):
    """subprocess로 role_server 실행, stdin에 initialize+tools/list 전송 → 정상 응답."""
    handlers_dir = _make_handler(tmp_path, "issues")

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    requests = [
        _rpc("initialize", {"protocolVersion": "2024-11-05", "clientInfo": {"name": "t"}, "capabilities": {}}, id=1),
        _rpc("tools/list", id=2),
    ]

    stdin_data = "\n".join(json.dumps(r) for r in requests) + "\n"
    stdout_data, stderr_data = proc.communicate(input=stdin_data.encode(), timeout=10)

    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 2, f"응답 부족: {lines}\nstderr: {stderr_data.decode()}"

    resp1 = json.loads(lines[0])
    assert resp1["id"] == 1
    assert resp1["result"]["serverInfo"]["name"] == "teammode"

    resp2 = json.loads(lines[1])
    assert resp2["id"] == 2
    tools = resp2["result"]["tools"]
    assert any(t["name"] == "issues_create" for t in tools)


def test_run_loop_tools_call_via_subprocess(tmp_path):
    """subprocess로 tools/call 전송 → 핸들러 함수 결과 수신."""
    handlers_dir = _make_handler(tmp_path, "issues")

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    req = _rpc("tools/call", {"name": "issues_create", "arguments": {"title": "서브프로세스 테스트"}}, id=42)
    stdout_data, stderr_data = proc.communicate(
        input=(json.dumps(req) + "\n").encode(), timeout=10
    )

    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1
    resp = json.loads(lines[0])
    assert resp["id"] == 42
    content = resp["result"]["content"]
    data = json.loads(content[0]["text"])
    assert data["title"] == "서브프로세스 테스트"


# ── #1 blocker: batch / non-object JSON 입력 ────────────────────────────────

def test_batch_request_returns_array_response(tmp_path):
    """JSON 배열(batch) 입력 → 서버가 죽지 않고 배열 응답 반환."""
    handlers_dir = _make_handler(tmp_path, "issues")

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    batch = [
        {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1},
        {"jsonrpc": "2.0", "method": "tools/list", "id": 2},
    ]
    stdin_data = json.dumps(batch) + "\n"
    stdout_data, stderr_data = proc.communicate(input=stdin_data.encode(), timeout=10)

    assert proc.returncode == 0, f"서버가 rc={proc.returncode}으로 종료됨\nstderr: {stderr_data.decode()}"
    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1, "batch 에 대한 응답이 없음"
    # 응답은 JSON 배열이어야 함
    parsed = json.loads(lines[0])
    assert isinstance(parsed, list), "batch 응답이 배열이 아님"
    ids = {r.get("id") for r in parsed}
    assert 1 in ids and 2 in ids


def test_scalar_null_input_returns_invalid_request(tmp_path):
    """JSON null 입력 → -32600 Invalid Request (서버 죽지 않음)."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    stdout_data, stderr_data = proc.communicate(input=b"null\n", timeout=10)
    assert proc.returncode == 0, f"서버 비정상 종료 rc={proc.returncode}"
    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1
    resp = json.loads(lines[0])
    assert "error" in resp
    assert resp["error"]["code"] == -32600


def test_scalar_number_input_does_not_crash(tmp_path):
    """JSON 숫자 입력 → 서버가 죽지 않고 에러 응답."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    stdout_data, stderr_data = proc.communicate(input=b"42\n", timeout=10)
    assert proc.returncode == 0, f"서버 비정상 종료 rc={proc.returncode}"
    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1
    resp = json.loads(lines[0])
    assert "error" in resp
    assert resp["error"]["code"] == -32600


# ── #2 major: JSON-RPC 2.0 규격 검증 ─────────────────────────────────────────

def test_id_null_gets_response(tmp_path):
    """id:null 요청 → notification이 아니므로 응답 있어야 함."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({"jsonrpc": "2.0", "id": None, "method": "tools/list"})
    assert resp is not None, "id:null 은 notification이 아니므로 응답이 있어야 함"
    assert resp.get("id") is None
    # 에러 없이 정상 결과여야 함
    assert "result" in resp


def test_jsonrpc_version_1_0_returns_invalid_request(tmp_path):
    """jsonrpc:'1.0' → -32600 Invalid Request."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({"jsonrpc": "1.0", "id": 1, "method": "tools/list"})
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32600


def test_params_as_array_returns_invalid_request(tmp_path):
    """params 가 배열이면 → -32600 Invalid Request."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/list",
        "params": ["invalid", "array"],
    })
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32600


def test_arguments_as_array_returns_invalid_params(tmp_path):
    """tools/call 에서 arguments 가 배열이면 → -32602 Invalid Params."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": "issues_create", "arguments": ["bad", "array"]},
    })
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602


def test_method_not_found_returns_32601(tmp_path):
    """알 수 없는 메서드 → 정확히 -32601."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("no/such/method", {}))
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_parse_error_returns_32700_via_subprocess(tmp_path):
    """malformed JSON → -32700 Parse Error (subprocess 경로)."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    stdout_data, stderr_data = proc.communicate(input=b"{bad json\n", timeout=10)
    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1
    resp = json.loads(lines[0])
    assert "error" in resp
    assert resp["error"]["code"] == -32700


# ── #3 major: handlers_are_valid 게이트 ──────────────────────────────────────

def test_invalid_handler_missing_funcs_not_listed(tmp_path):
    """필수 함수 빠진 핸들러 → tools/list 에서 해당 역할 도구 제외."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    # issues_list, issues_get, issues_update 누락
    (handlers_dir / "issues.py").write_text(dedent("""\
        def issues_create(title, **kwargs):
            return {"id": "1", "title": title}
        # 나머지 필수 함수 누락
    """))

    mod = _load_server()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)
    resp = server.handle_request(_rpc("tools/list"))
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert not any(n.startswith("issues_") for n in names), \
        "필수 함수 빠진 핸들러가 tools/list 에 노출됨"


def test_invalid_handler_toplevel_code_not_executed(tmp_path):
    """필수 함수 빠진 핸들러 → tools/call 시 top-level 코드 실행 안 됨 (marker 파일 미생성)."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    marker_file = tmp_path / "marker_executed.txt"

    # top-level에서 marker 파일을 생성하는 악성 핸들러 (필수 함수 빠짐)
    (handlers_dir / "issues.py").write_text(dedent(f"""\
        import pathlib
        pathlib.Path({str(marker_file)!r}).write_text("executed")

        def issues_create(title, **kwargs):
            return {{"id": "1"}}
        # issues_list, issues_get, issues_update 누락 → 검증 실패 예상
    """))

    mod = _load_server()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)
    # tools/list 로 게이트 트리거
    server.handle_request(_rpc("tools/list"))
    # tools/call 로 로드 시도
    server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "test"},
    }))

    assert not marker_file.exists(), \
        "검증 실패 핸들러의 top-level 코드가 실행됨 (marker 파일 생성됨)"


def test_valid_handler_still_works_after_gate(tmp_path):
    """유효한 핸들러는 게이트 통과 후 정상 동작."""
    mod = _load_server()
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/list"))
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "issues_create" in names

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "게이트 통과 테스트"},
    }))
    assert "result" in resp
    assert not resp["result"].get("isError")


def test_syntax_error_handler_not_executed(tmp_path):
    """문법 오류 핸들러 → 로드 차단, top-level 코드 실행 안 됨."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    # 문법 오류가 있어서 handlers_are_valid 가 False 를 반환해야 함
    (handlers_dir / "issues.py").write_text("def issues_create(title\n    pass\n")

    mod = _load_server()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "test"},
    }))
    # 에러 응답 (서버 죽지 않음)
    assert "error" in resp or ("result" in resp and resp["result"].get("isError"))


# ── 2차 재검수 #1: fail-CLOSED 보안 게이트 ────────────────────────────────────

def test_fail_closed_when_import_lib_missing(tmp_path, monkeypatch):
    """install_lib.py 가 없는 환경에서 핸들러가 차단(fail-CLOSED)되어야 한다.

    _handlers_are_valid=None 이면 모든 핸들러를 차단해야 한다 (fail-OPEN 아님).
    """
    # 유효한 핸들러 파일 생성
    handlers_dir = _make_handler(tmp_path, "issues")

    # role_server 를 fresh 로드 후 _handlers_are_valid 를 None 으로 강제 주입
    spec = importlib.util.spec_from_file_location("role_server_fc", SERVER_MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 검증기를 None 으로 패치 (import 실패 시뮬레이션)
    monkeypatch.setattr(mod, "_handlers_are_valid", None)

    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    # tools/list: 검증기 없으면 차단 → 도구 없음
    resp = server.handle_request(_rpc("tools/list"))
    tools = resp["result"]["tools"]
    assert tools == [], (
        "검증기(install_lib) 로드 실패 시 fail-OPEN 됨 — 차단(fail-CLOSED)이어야 함"
    )

    # tools/call: 마커 파일 미생성 확인
    marker = tmp_path / "fail_closed_marker.txt"
    # 마커를 생성하는 핸들러로 교체
    (handlers_dir / "issues.py").write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('executed')\n"
        "def issues_create(title, **kwargs): return {}\n"
        "def issues_list(filter=None): return []\n"
        "def issues_get(id): return {}\n"
        "def issues_update(id, **kwargs): return {}\n"
    )

    server2 = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)
    server2.handle_request(_rpc("tools/call", {
        "name": "issues_create",
        "arguments": {"title": "test"},
    }))
    assert not marker.exists(), (
        "검증기 없는 환경에서 핸들러 top-level 코드가 실행됨 — fail-CLOSED 위반"
    )


def test_fail_closed_when_sysmodules_polluted(tmp_path, monkeypatch):
    """sys.modules 에 오염된 '_teammode_install_lib_v2' 엔트리가 있을 때 차단.

    codex 실증: sys.modules['_teammode_install_lib'] = object() 주입 → fail-OPEN.
    수정 후: 오염된 엔트리(file 불일치)는 신뢰하지 않고 None 반환 → fail-CLOSED.
    """
    # 오염 객체: __file__ 이 설정되지 않거나 엉뚱한 경로
    class _FakeLib:
        __file__ = "/tmp/fake_lib.py"
        @staticmethod
        def handlers_are_valid(path):
            return True  # 항상 통과 — 오염 공격

    # 오염 주입 (새 모듈명 기준)
    monkeypatch.setitem(sys.modules, "_teammode_install_lib_v2", _FakeLib())

    # role_server 를 fresh import — 모듈 레벨에서 _import_handlers_are_valid() 호출
    spec = importlib.util.spec_from_file_location("role_server_polluted", SERVER_MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # _handlers_are_valid 가 None 이어야 함 (오염 엔트리 거부)
    assert mod._handlers_are_valid is None, (
        "오염된 sys.modules 엔트리를 신뢰함 — __file__ 불일치 시 None 반환이어야 함"
    )

    # 결과적으로 유효한 핸들러도 차단되어야 함 (fail-CLOSED)
    handlers_dir = _make_handler(tmp_path, "issues")
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)
    resp = server.handle_request(_rpc("tools/list"))
    tools = resp["result"]["tools"]
    assert tools == [], "오염된 sys.modules 엔트리로 인해 fail-OPEN 됨"


# ── 2차 재검수 #2: malformed/빈 batch -32600 응답 ────────────────────────────

def test_malformed_object_no_jsonrpc_returns_invalid_request(tmp_path):
    """{'foo':'boo'} — jsonrpc 필드 없음 → id:null -32600 응답 (무응답 아님)."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({"foo": "boo"})
    assert resp is not None, "malformed 요청에 응답이 없음 (무응답 버그)"
    assert "error" in resp, "에러 응답이어야 함"
    assert resp["error"]["code"] == -32600
    assert resp.get("id") is None, "id 를 알 수 없으므로 id:null 이어야 함"


def test_malformed_method_integer_returns_invalid_request(tmp_path):
    """{'jsonrpc':'2.0','method':1} — method 가 int → id:null -32600 (무응답 아님)."""
    mod = _load_server()
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()
    server = mod.TeammodeMCPServer(team="acme", handlers_dir=handlers_dir)

    resp = server.handle_request({"jsonrpc": "2.0", "method": 1})
    assert resp is not None, "method 타입 오류 요청에 응답이 없음"
    assert "error" in resp
    assert resp["error"]["code"] == -32600
    assert resp.get("id") is None


def test_empty_batch_returns_invalid_request_via_subprocess(tmp_path):
    """빈 배열 [] → id:null -32600 단일 에러 응답 (무응답 아님)."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    stdout_data, stderr_data = proc.communicate(input=b"[]\n", timeout=10)
    assert proc.returncode == 0, f"서버 비정상 종료 rc={proc.returncode}"
    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1, f"빈 batch 에 응답 없음\nstderr: {stderr_data.decode()}"
    resp = json.loads(lines[0])
    assert "error" in resp, "빈 batch 응답이 에러가 아님"
    assert resp["error"]["code"] == -32600
    assert resp.get("id") is None


def test_batch_with_malformed_item_and_notification_returns_error(tmp_path):
    """[{'foo':'boo'}, valid notification] — malformed 요소 → -32600 응답 있어야 함."""
    handlers_dir = tmp_path / "handlers"
    handlers_dir.mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra.mcp.role_server",
         "--team", "acme",
         "--handlers-dir", str(handlers_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )

    # {"foo":"boo"} 은 malformed (jsonrpc 없음) → -32600 응답 필요
    # notification (id 없는 유효 요청) → 응답 없음
    batch = [
        {"foo": "boo"},
        {"jsonrpc": "2.0", "method": "tools/list"},  # valid notification
    ]
    stdin_data = json.dumps(batch) + "\n"
    stdout_data, stderr_data = proc.communicate(input=stdin_data.encode(), timeout=10)

    assert proc.returncode == 0
    lines = [l.strip() for l in stdout_data.decode().splitlines() if l.strip()]
    assert len(lines) >= 1, "malformed 배열 요소에 대한 응답이 없음"
    # 배열 응답이어야 함
    parsed = json.loads(lines[0])
    assert isinstance(parsed, list), f"배열 응답이어야 함: {parsed}"
    # malformed 요소에 대한 -32600 응답이 포함되어야 함
    error_codes = [r.get("error", {}).get("code") for r in parsed if "error" in r]
    assert -32600 in error_codes, f"-32600 응답 없음: {parsed}"
