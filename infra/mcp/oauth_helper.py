"""oauth_helper — teammode OAuth 공통 헬퍼 (S4, stdlib 전용).

PKCE 플로우와 refresh_token 갱신을 담당한다. 토큰은 금고(credentials.py)에만 저장하며
stdout/로그/예외에 절대 노출되지 않는다 (마스킹 원칙).

크레덴셜 키 계약:
  - api_key  : credentials.store(team, scope, "<역할>", token)          ← 기존 계약 유지
  - bot_token: credentials.store(team, scope, "<역할>", token)          ← 기존 계약 유지
  - oauth    : credentials.store(team, scope, "<역할>_access_token",  ...) ← 신규
              credentials.store(team, scope, "<역할>_refresh_token", ...) ← 신규 (있을 때)

의존성: stdlib 전용 (http.server, urllib, hashlib, secrets, base64, json, threading)
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import http.server
import importlib.util
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional


# ── credentials 모듈 동적 로드 ────────────────────────────────────────────────

def _load_credentials():
    """infra/credentials.py 를 동적 로드 (sys.path 오염 없이)."""
    _lib_path = Path(__file__).resolve().parents[1] / "credentials.py"
    if not _lib_path.exists():
        raise ImportError(f"credentials.py 없음: {_lib_path}")
    _mod_name = "_teammode_credentials_oauth"
    if _mod_name in sys.modules:
        cached = sys.modules[_mod_name]
        if getattr(cached, "__file__", None) and Path(cached.__file__).resolve() == _lib_path:
            return cached
    spec = importlib.util.spec_from_file_location(_mod_name, _lib_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"credentials spec 생성 실패: {_lib_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        del sys.modules[_mod_name]
        raise
    return mod


# ── PKCE 헬퍼 ─────────────────────────────────────────────────────────────────

_CODE_VERIFIER_BYTES = 64  # 512 bit — RFC 7636 권고 43~128 char (base64url 후)


def _make_pkce_pair() -> tuple[str, str]:
    """code_verifier, code_challenge(S256) 쌍 생성."""
    raw = secrets.token_bytes(_CODE_VERIFIER_BYTES)
    verifier = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _find_free_port() -> int:
    """사용 가능한 랜덤 로컬 포트 반환."""
    import socket as _socket
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── PKCE 콜백 서버 ────────────────────────────────────────────────────────────

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """localhost OAuth 콜백 핸들러.

    첫 번째 GET 요청에서 ?code= 와 ?state= 를 추출한 뒤 서버를 종료한다.
    사용자에게 간단한 성공/실패 메시지를 반환한다.
    """

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        code = params.get("code")
        state = params.get("state", "")
        error = params.get("error")

        if error:
            body = ("<h1>OAuth Error</h1><p>" + error + "</p>").encode("utf-8")
            self.server._callback_error = error  # type: ignore[attr-defined]
        elif code:
            body = "<h1>Auth complete</h1><p>You may close this tab.</p>".encode("utf-8")
            self.server._callback_code = code  # type: ignore[attr-defined]
            self.server._callback_state = state  # type: ignore[attr-defined]
        else:
            body = b"<h1>Invalid callback</h1>"
            self.server._callback_error = "no_code"  # type: ignore[attr-defined]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        # 서버 종료 신호 (별도 스레드에서 shutdown 호출)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, fmt, *args):  # noqa: N802
        """HTTP 로그 억제 (토큰 노출 방지 부수 효과)."""
        pass


def _exchange_code_for_token(
    token_url: str,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict:
    """authorization code → token endpoint 교환 (POST, stdlib urllib)."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }).encode("utf-8")

    req = urllib.request.Request(
        token_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # 에러 본문에 토큰값이 없으므로 HTTP 상태만 노출
        raise RuntimeError(f"token endpoint HTTP 오류: {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"token endpoint 연결 오류: {e.reason}") from None

    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError("token endpoint 응답 JSON 파싱 실패") from None


def _exchange_refresh(
    token_url: str,
    client_id: str,
    refresh_token_value: str,
) -> dict:
    """refresh_token → 새 access_token 교환 (POST, stdlib urllib)."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": client_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        token_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"refresh endpoint HTTP 오류: {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"refresh endpoint 연결 오류: {e.reason}") from None

    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise RuntimeError("refresh endpoint 응답 JSON 파싱 실패") from None


# ── 공개 API ──────────────────────────────────────────────────────────────────

def pkce_flow(
    team: str,
    role: str,
    auth_url: str,
    token_url: str,
    client_id: str,
    scope: str,
    *,
    timeout: int = 120,
) -> str:
    """PKCE Authorization Code 플로우.

    localhost 임시 포트 서버를 열고 사용자가 브라우저에서 허용하면
    callback 을 받아 token endpoint 와 교환, 금고에 저장한다.

    반환: access_token (str)

    보안:
      - code_verifier: secrets 모듈, 충분한 엔트로피
      - code_challenge: SHA-256 (S256)
      - state: CSRF 방지 (랜덤 16 바이트)
      - 콜백 서버는 첫 요청 처리 후 즉시 종료
      - 토큰은 금고에만 저장 (stdout/로그 노출 금지)
    """
    creds = _load_credentials()

    code_verifier, code_challenge = _make_pkce_pair()
    state = secrets.token_urlsafe(16)

    # 콜백 서버를 먼저 bind (포트 0 → OS 자동 할당, SO_REUSEADDR)
    # 서버 생성 후 실제 포트를 읽어 redirect_uri 구성 (경쟁 조건 없음)
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"

    server._callback_code = None   # type: ignore[attr-defined]
    server._callback_state = None  # type: ignore[attr-defined]
    server._callback_error = None  # type: ignore[attr-defined]
    server.timeout = timeout

    # 인증 URL 구성 (서버 포트 확정 후)
    auth_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    full_auth_url = f"{auth_url}?{auth_params}"

    # 브라우저 열기 (서버가 이미 bind/listen 상태)
    webbrowser.open(full_auth_url)

    # 콜백 대기 (timeout 초 내 첫 요청까지)
    server.handle_request()

    # 에러 확인
    if server._callback_error:  # type: ignore[attr-defined]
        raise RuntimeError(f"OAuth 콜백 오류: {server._callback_error}")

    code = server._callback_code  # type: ignore[attr-defined]
    if not code:
        raise RuntimeError("OAuth 콜백에서 authorization code 를 받지 못함 (타임아웃 또는 거부)")

    received_state = server._callback_state  # type: ignore[attr-defined]

    # CSRF 방지: state 검증
    if received_state != state:
        raise RuntimeError(
            "OAuth state mismatch — CSRF 공격 가능성. 플로우를 중단합니다."
        )

    # Token 교환
    token_data = _exchange_code_for_token(
        token_url=token_url,
        client_id=client_id,
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )

    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("token endpoint 응답에 access_token 없음")

    # 금고 저장 (토큰값은 인자로만, 로그/출력 금지)
    creds.store(team, "personal", f"{role}_access_token", access_token)

    new_refresh = token_data.get("refresh_token")
    if new_refresh:
        creds.store(team, "personal", f"{role}_refresh_token", new_refresh)

    return access_token


def refresh_token(
    team: str,
    role: str,
    token_url: str,
    client_id: str,
) -> str:
    """refresh_token 으로 access_token 갱신 → 금고 재저장.

    반환: 새 access_token (str)
    """
    creds = _load_credentials()

    rt = creds.load(team, "personal", f"{role}_refresh_token")
    if not rt:
        raise RuntimeError(
            f"역할 '{role}' 의 refresh_token 이 금고에 없음. "
            "pkce_flow 를 먼저 실행하거나 수동으로 토큰을 저장하세요."
        )

    token_data = _exchange_refresh(
        token_url=token_url,
        client_id=client_id,
        refresh_token_value=rt,
    )

    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("refresh endpoint 응답에 access_token 없음")

    # 금고 재저장
    creds.store(team, "personal", f"{role}_access_token", access_token)

    # refresh_token rotation 지원
    new_rt = token_data.get("refresh_token")
    if new_rt:
        creds.store(team, "personal", f"{role}_refresh_token", new_rt)

    return access_token
