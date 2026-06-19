"""teammode MCP 서버 — stdlib JSON-RPC 2.0 over stdin/stdout.

실행:
    python -m infra.mcp.role_server --team <team> --handlers-dir /abs/path/handlers

도구명 형식: {role}_{action}
  Claude:  mcp__teammode__issues_create
  Codex:   teammode.issues_create

의존성: stdlib 전용 (json, sys, importlib, argparse, pathlib, tempfile, shutil)

핸들러 신뢰 모델:
  핸들러(.py)는 trusted code — 서버가 직접 import해 실행한다.
  단, infra/install_lib.py의 handlers_are_valid()로 이중 방어:
  install 시 1차 검증 + 서버 로드 경로에서 2차 fail-closed 게이트.
  검증 실패 역할은 tools/list 노출 및 _load_handler 로드 모두 차단된다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# infra/install_lib.py 의 handlers_are_valid 를 동적 import
# (sys.path 오염 없이 spec 기반 로드)
def _import_handlers_are_valid():
    """infra/install_lib.py 의 handlers_are_valid 함수를 반환. 실패 시 None.

    보안 원칙 (fail-CLOSED):
      - 이 함수가 None 을 반환하면 _role_handler_valid() 는 False 를 반환한다.
      - 즉, 검증기 자체를 로드할 수 없으면 모든 핸들러를 차단한다.
    sys.modules 오염 방어:
      - '_teammode_install_lib_v2' 이름이 이미 sys.modules 에 있으면
        __file__ 이 실제 install_lib.py 와 일치하는지 확인한다.
        불일치(오염된 엔트리)면 신뢰하지 않고 None 반환.
    """
    try:
        _lib_path = Path(__file__).resolve().parents[1] / "install_lib.py"
        if not _lib_path.exists():
            return None
        import importlib.util as _ilu
        # 더 고유한 모듈명 사용 (v2 suffix) — 이전 '_teammode_install_lib' 충돌 방지
        _mod_name = "_teammode_install_lib_v2"
        # 이미 로드된 경우: __file__ 일치 여부로 오염 검사
        if _mod_name in sys.modules:
            _cached = sys.modules[_mod_name]
            _cached_file = getattr(_cached, "__file__", None)
            if _cached_file is None or Path(_cached_file).resolve() != _lib_path:
                # 오염된 엔트리 — 신뢰 불가 → fail-CLOSED
                return None
            return getattr(_cached, "handlers_are_valid", None)
        _spec = _ilu.spec_from_file_location(_mod_name, _lib_path)
        if _spec is None or _spec.loader is None:
            return None
        _mod = _ilu.module_from_spec(_spec)
        # dataclass 등 __module__ 참조를 위해 sys.modules 에 먼저 등록
        sys.modules[_mod_name] = _mod
        try:
            _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        except Exception:
            del sys.modules[_mod_name]
            raise
        return getattr(_mod, "handlers_are_valid", None)
    except Exception:
        return None

_handlers_are_valid = _import_handlers_are_valid()

# ── 역할·도구 정의 ────────────────────────────────────────────────────────────

ROLES: list[str] = ["issues", "chat", "docs", "calendar"]

ROLE_TOOLS: dict[str, list[dict]] = {
    "issues": [
        {
            "name": "issues_create",
            "description": "이슈 생성",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "이슈 제목"},
                    "body": {"type": "string", "description": "이슈 본문"},
                    "assignee": {"type": "string", "description": "담당자"},
                    "label": {"type": "string", "description": "레이블"},
                    "priority": {"type": "string", "description": "우선순위"},
                },
                "required": ["title"],
            },
        },
        {
            "name": "issues_list",
            "description": "이슈 목록 조회",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "필터 쿼리"},
                },
            },
        },
        {
            "name": "issues_get",
            "description": "이슈 상세 조회",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "이슈 ID"},
                },
                "required": ["id"],
            },
        },
        {
            "name": "issues_update",
            "description": "이슈 수정",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "이슈 ID"},
                },
                "required": ["id"],
            },
        },
    ],
    "chat": [
        {
            "name": "chat_send",
            "description": "메시지 전송",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "전송할 메시지"},
                    "channel": {"type": "string", "description": "채널 이름"},
                },
                "required": ["message"],
            },
        },
        {
            "name": "chat_list",
            "description": "메시지 목록 조회",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "채널 이름"},
                    "limit": {"type": "integer", "description": "최대 개수"},
                },
            },
        },
    ],
    "docs": [
        {
            "name": "docs_read",
            "description": "문서 읽기",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "문서 ID"},
                },
                "required": ["id"],
            },
        },
        {
            "name": "docs_write",
            "description": "문서 쓰기",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "문서 ID"},
                    "content": {"type": "string", "description": "문서 내용"},
                },
                "required": ["id", "content"],
            },
        },
        {
            "name": "docs_list",
            "description": "문서 목록 조회",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색 쿼리"},
                },
            },
        },
        {
            "name": "docs_create",
            "description": "문서 생성",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "문서 제목"},
                    "content": {"type": "string", "description": "문서 내용"},
                },
                "required": ["title"],
            },
        },
    ],
    "calendar": [
        {
            "name": "calendar_list",
            "description": "일정 목록 조회",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "시작 날짜 (ISO 8601)"},
                    "end": {"type": "string", "description": "종료 날짜 (ISO 8601)"},
                },
                "required": ["start"],
            },
        },
        {
            "name": "calendar_create",
            "description": "일정 생성",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "일정 제목"},
                    "start": {"type": "string", "description": "시작 시각 (ISO 8601)"},
                    "end": {"type": "string", "description": "종료 시각 (ISO 8601)"},
                    "description": {"type": "string", "description": "일정 설명"},
                },
                "required": ["title", "start"],
            },
        },
    ],
}

# MCP 프로토콜 버전
MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 에러 코드
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


# ── 서버 클래스 ───────────────────────────────────────────────────────────────

class TeammodeMCPServer:
    """단일 teammode MCP 서버.

    - alias: teammode (Claude: mcp__teammode__*, Codex: teammode.*)
    - 핸들러 lazy import: handlers_dir/<role>.py
    - JSON-RPC 2.0 over stdin/stdout
    """

    def __init__(self, team: str, handlers_dir: Path) -> None:
        self.team = team
        self.handlers_dir = Path(handlers_dir)
        self._loaded: dict[str, Any] = {}       # role → 모듈 (성공)
        self._failed: dict[str, Exception] = {}  # role → 실패 원인
        self._invalid_roles: set[str] = set()   # handlers_are_valid 실패 역할

    # ── 핸들러 로드 ───────────────────────────────────────────────────────────

    def _role_handler_valid(self, role: str) -> bool:
        """역할 핸들러 파일 하나를 handlers_are_valid()로 검증.

        _handlers_are_valid 가 None 이면(install_lib 로드 실패/오염) fail-CLOSED (False 반환).
        검증기를 로드할 수 없으면 핸들러도 차단 — 모르면 막는다.
        결과는 _invalid_roles 캐시에 기록한다.
        """
        if role in self._invalid_roles:
            return False
        if _handlers_are_valid is None:
            # 검증기 import 실패 → fail-CLOSED (차단)
            self._invalid_roles.add(role)
            return False

        handler_path = self.handlers_dir / f"{role}.py"
        if not handler_path.exists():
            return True  # 파일 없음 = 미연결 역할 — valid 로 취급 (list_tools 에서 자연히 제외됨)

        # 역할 파일 하나만 있는 임시 디렉터리로 검증 (handlers_are_valid 는 dir 단위)
        tmp_dir = Path(tempfile.mkdtemp(prefix="tmmode_validate_"))
        try:
            shutil.copy2(handler_path, tmp_dir / f"{role}.py")
            ok = _handlers_are_valid(tmp_dir)
        except Exception:
            ok = False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if not ok:
            self._invalid_roles.add(role)
        return ok

    def _load_handler(self, role: str) -> Any | None:
        """역할 핸들러 모듈을 lazy import. 실패 시 None 반환 (예외 억제).

        handlers_are_valid() 게이트 통과 못 한 핸들러는 로드하지 않는다.
        """
        if role in self._loaded:
            return self._loaded[role]
        if role in self._failed:
            return None

        handler_path = self.handlers_dir / f"{role}.py"
        if not handler_path.exists():
            return None

        # #3 보안 게이트: 검증 실패 핸들러는 로드 차단 (top-level 코드 실행 방지)
        if not self._role_handler_valid(role):
            self._failed[role] = ImportError(f"handlers_are_valid 검증 실패: {role}.py")
            return None

        try:
            spec = importlib.util.spec_from_file_location(
                f"teammode_handler_{role}", handler_path
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"spec 생성 실패: {handler_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            self._loaded[role] = mod
            return mod
        except Exception as exc:
            self._failed[role] = exc
            return None

    def _handler_error(self, role: str) -> str:
        """핸들러 로드 실패 원인 문자열 반환."""
        exc = self._failed.get(role)
        return str(exc) if exc else f"핸들러 파일 없음: {role}.py"

    # ── 도구 목록 ─────────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """연결된 역할의 도구만 반환 (핸들러 파일 없거나 검증 실패 역할 제외).

        #3 보안 게이트: handlers_are_valid() 통과한 역할만 노출.
        """
        tools: list[dict] = []
        for role in ROLES:
            if (self.handlers_dir / f"{role}.py").exists():
                # 검증 실패 핸들러는 도구 목록에서도 제외 (fail-closed)
                if self._role_handler_valid(role):
                    tools.extend(ROLE_TOOLS[role])
        return tools

    # ── 도구 호출 ─────────────────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict) -> dict:
        """도구 이름으로 핸들러 함수 호출, 결과 반환.

        실패 시 {"isError": True, "content": [...]} 형식.
        """
        # role 분해: issues_create → role=issues, action=create
        # 단, 함수명 자체가 role_action 이므로 첫 _ 기준
        parts = name.split("_", 1)
        if len(parts) != 2:
            return _tool_error(f"잘못된 도구명: {name!r}")

        role = parts[0]
        if role not in ROLES:
            return _tool_error(f"알 수 없는 역할: {role!r}")

        # 도구가 해당 역할에 정의돼 있는지 확인
        role_tool_names = {t["name"] for t in ROLE_TOOLS.get(role, [])}
        if name not in role_tool_names:
            return _tool_error(f"알 수 없는 도구: {name!r}")

        # 핸들러 로드
        handler_mod = self._load_handler(role)
        if handler_mod is None:
            return _tool_error(self._handler_error(role))

        # 함수 조회
        fn = getattr(handler_mod, name, None)
        if fn is None:
            return _tool_error(f"함수 없음: {name!r} in {role}.py")

        # 호출 — async 핸들러도 지원 (S3 await 처리)
        try:
            result = fn(**arguments)
            # coroutine(async def) 이면 asyncio.run() 으로 실행
            if inspect.isawaitable(result):
                result = asyncio.run(result)
        except Exception as exc:
            return _tool_error(f"핸들러 예외: {exc}")

        return _tool_ok(result)

    # ── JSON-RPC 요청 처리 ────────────────────────────────────────────────────

    def handle_request(self, req: dict) -> dict | None:
        """JSON-RPC 2.0 요청 처리, 응답 반환.

        #2 규격 검증 (notification 판정은 기본 검증 통과 후에만):
          - jsonrpc == "2.0" 필수 — 불일치면 id 유무와 무관하게 id:null -32600
          - method 는 string 필수 — 아니면 id 유무와 무관하게 id:null -32600
          - 위 두 조건 통과한 뒤: "id" 키 없음 = notification (응답 없음)
          - params 가 있으면 object(dict) 여야 함 (배열 = -32600)
          - id:null 은 일반 요청 (notification 아님)

        즉, 기본 검증(jsonrpc+method) 실패 시 id 없어도 id:null 에러를 반환한다.
        notification 여부는 기본 검증 통과 후에만 판단한다.
        """
        req_id = req.get("id")
        has_id = "id" in req

        # jsonrpc 버전 검증 — 기본 검증 실패: id 유무 무관하게 에러 응답
        if req.get("jsonrpc") != "2.0":
            return _jsonrpc_error(req_id if has_id else None,
                                  JSONRPC_INVALID_REQUEST,
                                  "jsonrpc 버전은 '2.0' 이어야 함")

        method = req.get("method")

        # method 는 string 필수 — 기본 검증 실패: id 유무 무관하게 에러 응답
        if not isinstance(method, str) or not method:
            return _jsonrpc_error(req_id if has_id else None,
                                  JSONRPC_INVALID_REQUEST,
                                  "method 는 비어있지 않은 string 이어야 함")

        # 기본 검증 통과 후 notification 판별: "id" 키 자체가 없으면 notification
        is_notification = not has_id

        # params 타입 검증: 있으면 object(dict) 여야 함
        params = req.get("params")
        if params is not None and not isinstance(params, dict):
            if is_notification:
                return None
            return _jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "params 는 object 이어야 함 (배열 불허)")

        try:
            result = self._dispatch(method, params or {})
        except _MethodNotFoundError as exc:
            if is_notification:
                return None
            return _jsonrpc_error(req_id, JSONRPC_METHOD_NOT_FOUND, str(exc))
        except _InvalidParamsError as exc:
            if is_notification:
                return None
            return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, str(exc))
        except Exception as exc:
            if is_notification:
                return None
            return _jsonrpc_error(req_id, JSONRPC_INTERNAL_ERROR, str(exc))

        if is_notification:
            return None

        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _dispatch(self, method: str, params: dict) -> dict:
        """메서드 라우팅."""
        if method == "initialize":
            return self._handle_initialize(params)
        elif method == "tools/list":
            return self._handle_tools_list(params)
        elif method == "tools/call":
            return self._handle_tools_call(params)
        else:
            raise _MethodNotFoundError(method)

    def _handle_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "teammode",
                "version": "1.0.0",
            },
        }

    def _handle_tools_list(self, params: dict) -> dict:
        return {"tools": self.list_tools()}

    def _handle_tools_call(self, params: dict) -> dict:
        name = params.get("name", "")
        # name 은 string 필수
        if not isinstance(name, str) or not name:
            raise _InvalidParamsError("tools/call: name 은 비어있지 않은 string 이어야 함")
        arguments = params.get("arguments")
        # arguments 가 있으면 object(dict) 이어야 함 (#2: 배열이면 -32602)
        if arguments is not None and not isinstance(arguments, dict):
            raise _InvalidParamsError("tools/call: arguments 는 object 이어야 함 (배열 불허)")
        return self.call_tool(name, arguments or {})

    # ── stdin/stdout 루프 ─────────────────────────────────────────────────────

    def run(self) -> None:
        """MCP JSON-RPC over stdin/stdout 루프 (EOF까지 실행).

        #1 안전성: 어떤 입력(배열·스칼라·비-dict)에도 서버가 죽지 않는다.
          - JSON 배열(batch): 각 요소를 개별 처리해 배열 응답 반환
            (notification-only batch 면 빈 배열이므로 출력 안 함)
          - JSON 스칼라(null·숫자·문자열): -32600 Invalid Request
          - JSON object: 정상 처리
        """
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                # Parse error — id 알 수 없으므로 null
                resp = _jsonrpc_error(None, JSONRPC_PARSE_ERROR, f"JSON 파싱 실패: {exc}")
                _write_response(resp)
                continue

            if isinstance(parsed, list):
                # JSON-RPC 2.0 batch 처리
                # 빈 배열은 Invalid Request (JSON-RPC 2.0 규격)
                if not parsed:
                    _write_response(_jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "빈 batch 배열은 허용되지 않음"))
                else:
                    responses = []
                    for item in parsed:
                        if isinstance(item, dict):
                            r = self.handle_request(item)
                        else:
                            # 배열 내 비-dict 요소 → Invalid Request (id 불명 → null)
                            r = _jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "배열 내 요소가 object 가 아님")
                        if r is not None:
                            responses.append(r)
                    if responses:
                        _write_response_raw(json.dumps(responses, ensure_ascii=False))
            elif isinstance(parsed, dict):
                resp = self.handle_request(parsed)
                if resp is not None:
                    _write_response(resp)
            else:
                # 스칼라(null, 숫자, 문자열) → Invalid Request
                resp = _jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "요청은 object 또는 배열이어야 함")
                _write_response(resp)


# ── 예외 ──────────────────────────────────────────────────────────────────────

class _MethodNotFoundError(Exception):
    def __init__(self, method: str) -> None:
        super().__init__(f"메서드 없음: {method!r}")
        self.method = method


class _InvalidParamsError(Exception):
    """JSON-RPC -32602 Invalid Params."""


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def _tool_ok(result: Any) -> dict:
    """도구 성공 응답 (MCP content 형식)."""
    text = json.dumps(result, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }


def _tool_error(message: str) -> dict:
    """도구 실패 응답 (MCP isError=True 형식)."""
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    """JSON-RPC 에러 응답."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _write_response(resp: dict) -> None:
    """stdout에 JSON-RPC 응답 한 줄 출력 (flush 포함)."""
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _write_response_raw(text: str) -> None:
    """stdout에 임의 문자열 한 줄 출력 (flush 포함). batch 응답용."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# ── 토큰 로드 분기 (S4 크레덴셜 키 계약) ────────────────────────────────────

def _load_credentials_module():
    """infra/credentials.py 를 동적 로드 (sys.path 오염 없이). 실패 시 None."""
    try:
        _lib_path = Path(__file__).resolve().parents[1] / "credentials.py"
        if not _lib_path.exists():
            return None
        import importlib.util as _ilu
        _mod_name = "_teammode_credentials_role_server"
        if _mod_name in sys.modules:
            cached = sys.modules[_mod_name]
            if getattr(cached, "__file__", None) and \
               Path(cached.__file__).resolve() == _lib_path:
                return cached
        _spec = _ilu.spec_from_file_location(_mod_name, _lib_path)
        if _spec is None or _spec.loader is None:
            return None
        _mod = _ilu.module_from_spec(_spec)
        sys.modules[_mod_name] = _mod
        try:
            _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        except Exception:
            del sys.modules[_mod_name]
            raise
        return _mod
    except Exception:
        return None


def get_token_for_role(
    team: str,
    scope: str,
    role: str,
    auth_type: str,
) -> Any | None:
    """auth 타입에 따라 올바른 키로 금고에서 토큰 로드.

    크레덴셜 키 계약 (S4):
      - api_key   : credentials.load(team, scope, role)               ← 기존 계약
      - bot_token : credentials.load(team, scope, role)               ← 기존 계약
      - oauth     : credentials.load(team, scope, f"{role}_access_token") ← 신규

    반환: token 문자열, 없으면 None.
    """
    creds = _load_credentials_module()
    if creds is None:
        return None

    if auth_type == "oauth":
        return creds.load(team, scope, f"{role}_access_token")
    else:
        # api_key, bot_token → 기존 역할 이름 키
        return creds.load(team, scope, role)


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="teammode MCP 서버 (JSON-RPC 2.0 over stdin/stdout)"
    )
    parser.add_argument("--team", required=True, help="팀 이름")
    parser.add_argument(
        "--handlers-dir", required=True, help="핸들러 디렉토리 절대 경로"
    )
    args = parser.parse_args(argv)

    handlers_dir = Path(args.handlers_dir)
    if not handlers_dir.exists():
        sys.stderr.write(f"handlers-dir 없음: {handlers_dir}\n")
        sys.exit(1)

    server = TeammodeMCPServer(team=args.team, handlers_dir=handlers_dir)
    server.run()


if __name__ == "__main__":
    main()
