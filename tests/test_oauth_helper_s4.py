"""S4 — OAuth 공통 헬퍼 테스트 (test_oauth_helper_s4.py).

검증 묶음:
  - 4-A: 크레덴셜 키 계약 (api_key/bot_token=<역할>, oauth=<역할>_access/refresh_token)
  - 4-B: pkce_flow — mock localhost 콜백·mock token endpoint (실 네트워크 0)
  - 4-B: refresh_token — mock token endpoint, 금고 재저장
  - 보안: 토큰 stdout/예외 메시지 누출 없음 (마스킹)
  - 의존성: stdlib 전용 (외부 import 없음)

모든 테스트는 tmp_path·XDG_DATA_HOME 격리만 사용. 실 금고·실 네트워크 무접촉.

[PKCE 테스트 설계 주의사항]
pkce_flow는 이미 bind/listen된 서버에서 webbrowser.open을 호출한다.
mock_browser_open은 그 시점에 서버가 이미 listen 상태임을 이용해
스레드에서 바로 HTTP 요청을 보낸다.
socket.create_connection 폴링을 쓰면 테스트 연결이 실제 연결을
선점(handle_request가 1개만 처리)해 _callback_code=None이 될 수 있으므로
절대 사용하지 않는다. 대신 짧은 sleep(0.05s)로 handle_request가
accept 대기 상태에 들어가기를 기다린다.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import time
import threading
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
HELPER_MOD = REPO / "infra" / "mcp" / "oauth_helper.py"
CREDENTIALS_MOD = REPO / "infra" / "credentials.py"


# ── 모듈 로더 ─────────────────────────────────────────────────────────────────

def _load_mod(path: Path, name: str):
    """지정 파일을 동적 로드 (sys.path 오염 없이)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def creds(tmp_path, monkeypatch):
    """격리된 XDG_DATA_HOME 안에 credentials 모듈을 로드."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    return _load_mod(CREDENTIALS_MOD, "_test_creds_s4")


@pytest.fixture()
def helper(tmp_path, monkeypatch):
    """격리된 XDG_DATA_HOME 안에 oauth_helper 모듈을 로드."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    return _load_mod(HELPER_MOD, "_test_oauth_helper_s4")


# ── PKCE 테스트용 공통 mock_browser_open 팩토리 ───────────────────────────────

def _make_mock_browser_open(code: str | None = "fake-auth-code", use_wrong_state: bool = False):
    """webbrowser.open mock 생성기.

    pkce_flow 는 webbrowser.open 호출 전에 서버를 bind/listen 한다.
    이 mock 은 auth URL 에서 redirect_uri·state 를 추출해 스레드에서 HTTP 요청을 보낸다.

    설계 원칙:
      - socket.create_connection 폴링 금지 — handle_request 가 첫 요청만 처리하므로
        폴링 연결이 그것을 선점한다. 대신 짧은 sleep 으로 handle_request accept 대기를 기다린다.
    """
    def mock_browser_open(url):
        import http.client, urllib.parse, threading, time

        parsed = urllib.parse.urlparse(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        redirect_uri = params.get("redirect_uri", "")
        real_state = params.get("state", "")
        port = urllib.parse.urlparse(redirect_uri).port

        send_state = "tampered-state-xxxxx" if use_wrong_state else real_state

        def send_callback():
            # handle_request() 가 accept 대기 상태에 들어갈 시간 제공
            # (webbrowser.open 호출 직후 handle_request가 실행되므로 짧은 sleep으로 충분)
            time.sleep(0.05)
            if code is None:
                return  # code 없는 시나리오는 타임아웃 유도
            cb_path = f"/?code={urllib.parse.quote(code)}&state={urllib.parse.quote(send_state)}"
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", cb_path)
                conn.getresponse()
            except Exception:
                pass
            finally:
                conn.close()

        threading.Thread(target=send_callback, daemon=True).start()
        return True

    return mock_browser_open


def _make_mock_urlopen(token_resp: dict):
    """urllib.request.urlopen mock — token endpoint 응답."""
    def mock_urlopen(req, *args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(token_resp).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp
    return mock_urlopen


# ── 4-A. 크레덴셜 키 계약 ─────────────────────────────────────────────────────

class TestCredentialKeyContract:
    """api_key/bot_token 은 <역할> 키, oauth 는 <역할>_access/refresh_token 키."""

    def test_api_key_stored_as_role_key(self, creds):
        """api_key 는 기존 방식 — 역할 이름이 그대로 키."""
        creds.store("acme", "personal", "issues", "sk-abc123")
        assert creds.load("acme", "personal", "issues") == "sk-abc123"

    def test_bot_token_stored_as_role_key(self, creds):
        """bot_token 도 역할 이름이 키."""
        _bot_tok = "xoxb" + "-bot-token"
        creds.store("acme", "personal", "chat", _bot_tok)
        assert creds.load("acme", "personal", "chat") == _bot_tok

    def test_oauth_access_token_key_pattern(self, creds):
        """oauth access_token 은 <역할>_access_token 키."""
        creds.store("acme", "personal", "docs_access_token", "ya29.access-xyz")
        assert creds.load("acme", "personal", "docs_access_token") == "ya29.access-xyz"

    def test_oauth_refresh_token_key_pattern(self, creds):
        """oauth refresh_token 은 <역할>_refresh_token 키."""
        creds.store("acme", "personal", "docs_refresh_token", "1//refresh-xyz")
        assert creds.load("acme", "personal", "docs_refresh_token") == "1//refresh-xyz"

    def test_api_key_and_oauth_coexist_for_different_roles(self, creds):
        """api_key 역할과 oauth 역할이 같은 금고에 공존."""
        creds.store("acme", "personal", "issues", "sk-api-key")
        creds.store("acme", "personal", "calendar_access_token", "ya29.cal-access")
        creds.store("acme", "personal", "calendar_refresh_token", "1//cal-refresh")

        assert creds.load("acme", "personal", "issues") == "sk-api-key"
        assert creds.load("acme", "personal", "calendar_access_token") == "ya29.cal-access"
        assert creds.load("acme", "personal", "calendar_refresh_token") == "1//cal-refresh"


# ── 4-B. pkce_flow ─────────────────────────────────────────────────────────────

class TestPkceFlow:
    """pkce_flow: mock localhost 콜백 + mock token endpoint."""

    def test_pkce_flow_returns_access_token(self, helper, monkeypatch):
        """pkce_flow 가 access_token 을 반환한다."""
        import urllib.request, webbrowser

        token_resp = {"access_token": "ya29.fake-access", "refresh_token": "1//fake-refresh",
                      "token_type": "bearer"}

        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(token_resp))
        monkeypatch.setattr(helper.webbrowser, "open", _make_mock_browser_open("fake-code"))

        result = helper.pkce_flow(
            team="acme",
            role="calendar",
            auth_url="https://auth.example.com/oauth/authorize",
            token_url="https://auth.example.com/oauth/token",
            client_id="test-client-id",
            scope="read write",
        )

        assert result == "ya29.fake-access"

    def test_pkce_flow_stores_access_token_in_vault(self, helper, monkeypatch):
        """pkce_flow 완료 후 access_token 이 금고에 저장된다."""
        import urllib.request, webbrowser

        token_resp = {"access_token": "ya29.stored-access", "refresh_token": "1//stored-refresh",
                      "token_type": "bearer"}

        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(token_resp))
        monkeypatch.setattr(helper.webbrowser, "open", _make_mock_browser_open("code-123"))

        helper.pkce_flow(
            team="acme",
            role="calendar",
            auth_url="https://auth.example.com/oauth/authorize",
            token_url="https://auth.example.com/oauth/token",
            client_id="test-client-id",
            scope="read",
        )

        # 금고에서 직접 확인
        creds_mod = _load_mod(CREDENTIALS_MOD, "_test_creds_check_s4")
        at = creds_mod.load("acme", "personal", "calendar_access_token")
        rt = creds_mod.load("acme", "personal", "calendar_refresh_token")
        assert at == "ya29.stored-access"
        assert rt == "1//stored-refresh"

    def test_pkce_flow_no_refresh_token_ok(self, helper, monkeypatch):
        """refresh_token 없는 응답도 정상 처리 (access_token 만 저장)."""
        import urllib.request, webbrowser

        token_resp = {"access_token": "ya29.no-refresh", "token_type": "bearer"}

        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(token_resp))
        monkeypatch.setattr(helper.webbrowser, "open", _make_mock_browser_open("code-no-rt"))

        result = helper.pkce_flow(
            team="acme",
            role="docs",
            auth_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id="cli",
            scope="read",
        )
        assert result == "ya29.no-refresh"

    def test_pkce_flow_csrf_state_mismatch_raises(self, helper, monkeypatch):
        """콜백의 state 가 다르면 에러 — CSRF 방지."""
        import urllib.request, webbrowser

        def mock_urlopen_not_called(req, *a, **k):
            raise AssertionError("token endpoint should not be called on CSRF mismatch")

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_not_called)
        monkeypatch.setattr(
            helper.webbrowser, "open",
            _make_mock_browser_open("csrf-code", use_wrong_state=True),
        )

        with pytest.raises(Exception, match="[Ss]tate|[Cc]SRF|[Ss]ecurity|[Mm]ismatch"):
            helper.pkce_flow(
                team="acme",
                role="issues",
                auth_url="https://auth.example.com/authorize",
                token_url="https://auth.example.com/token",
                client_id="cli",
                scope="read",
            )

    def test_pkce_flow_token_exchange_failure_raises(self, helper, monkeypatch):
        """token endpoint 실패(HTTP 400 등) 시 명확한 에러 (크래시 아님)."""
        import urllib.request, urllib.error, webbrowser

        def mock_urlopen_fail(req, *args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://auth.example.com/token",
                code=400,
                msg="Bad Request",
                hdrs=None,  # type: ignore
                fp=None,
            )

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)
        monkeypatch.setattr(helper.webbrowser, "open", _make_mock_browser_open("code-x"))

        with pytest.raises(Exception):
            helper.pkce_flow(
                team="acme",
                role="issues",
                auth_url="https://auth.example.com/authorize",
                token_url="https://auth.example.com/token",
                client_id="cli",
                scope="read",
            )

    def test_pkce_challenge_is_s256(self, helper, monkeypatch):
        """PKCE code_challenge_method 가 S256 인지 확인 (브라우저로 열리는 URL 검사)."""
        import urllib.request, webbrowser

        captured_url: list[str] = []

        def mock_browser_capture_and_send(url):
            """URL 을 캡처하고 즉시 콜백을 보내 (다음 flow 가 실패해도 URL 만 검사)."""
            captured_url.append(url)
            import http.client, urllib.parse, time, threading

            parsed = urllib.parse.urlparse(url)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            redir = params.get("redirect_uri", "")
            state = params.get("state", "")
            port = urllib.parse.urlparse(redir).port

            def send_cb():
                time.sleep(0.05)
                try:
                    cb_path = f"/?code=c&state={urllib.parse.quote(state)}"
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    conn.request("GET", cb_path)
                    conn.getresponse()
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            threading.Thread(target=send_cb, daemon=True).start()
            return True

        def mock_urlopen_fail(req, *a, **k):
            raise TimeoutError("token exchange not expected in this test")

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)
        monkeypatch.setattr(helper.webbrowser, "open", mock_browser_capture_and_send)

        try:
            helper.pkce_flow(
                team="t",
                role="r",
                auth_url="https://auth.example.com/authorize",
                token_url="https://auth.example.com/token",
                client_id="c",
                scope="s",
            )
        except Exception:
            pass  # token exchange 실패는 괜찮음 — URL 만 검사

        assert captured_url, "webbrowser.open 이 호출되지 않음"
        parsed = urllib.parse.urlparse(captured_url[0])
        params = dict(urllib.parse.parse_qsl(parsed.query))
        assert params.get("code_challenge_method") == "S256", (
            f"PKCE S256 필요. 실제 params: {params}"
        )


# ── 4-B. refresh_token ────────────────────────────────────────────────────────

class TestRefreshToken:
    """refresh_token 함수: 금고의 refresh_token 으로 access_token 갱신."""

    def test_refresh_returns_new_access_token(self, helper, monkeypatch, tmp_path):
        """refresh_token 호출 시 새 access_token 을 반환."""
        import urllib.request

        # 먼저 기존 refresh_token 을 금고에 저장
        creds_mod = _load_mod(CREDENTIALS_MOD, "_creds_for_refresh_test")
        creds_mod.store("acme", "personal", "docs_refresh_token", "1//old-refresh")

        new_token_resp = {
            "access_token": "ya29.new-access",
            "token_type": "bearer",
            "refresh_token": "1//new-refresh",
        }

        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(new_token_resp))

        result = helper.refresh_token(
            team="acme",
            role="docs",
            token_url="https://auth.example.com/token",
            client_id="test-client",
        )

        assert result == "ya29.new-access"

    def test_refresh_stores_new_access_token_in_vault(self, helper, monkeypatch):
        """refresh 완료 후 새 access_token 이 금고에 재저장된다."""
        import urllib.request

        creds_mod = _load_mod(CREDENTIALS_MOD, "_creds_for_refresh_store_test")
        creds_mod.store("acme", "personal", "issues_refresh_token", "1//refresh-old")
        creds_mod.store("acme", "personal", "issues_access_token", "ya29.old-access")

        new_resp = {"access_token": "ya29.fresh-access", "token_type": "bearer"}
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(new_resp))

        helper.refresh_token(
            team="acme",
            role="issues",
            token_url="https://auth.example.com/token",
            client_id="cli",
        )

        refreshed = creds_mod.load("acme", "personal", "issues_access_token")
        assert refreshed == "ya29.fresh-access"

    def test_refresh_updates_refresh_token_if_rotated(self, helper, monkeypatch):
        """refresh 응답에 새 refresh_token 포함 시 금고 갱신 (rotation 지원)."""
        import urllib.request

        creds_mod = _load_mod(CREDENTIALS_MOD, "_creds_rotation_test")
        creds_mod.store("acme", "personal", "chat_refresh_token", "1//old-rt")

        rotated_resp = {
            "access_token": "ya29.rotated-access",
            "refresh_token": "1//new-rt",
            "token_type": "bearer",
        }
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(rotated_resp))

        helper.refresh_token(
            team="acme",
            role="chat",
            token_url="https://auth.example.com/token",
            client_id="cli",
        )

        new_rt = creds_mod.load("acme", "personal", "chat_refresh_token")
        assert new_rt == "1//new-rt"

    def test_refresh_no_vault_refresh_token_raises(self, helper):
        """금고에 refresh_token 없을 때 명확한 에러."""
        with pytest.raises(Exception, match="[Rr]efresh|[Tt]oken|[Vv]ault"):
            helper.refresh_token(
                team="acme",
                role="nonexistent",
                token_url="https://auth.example.com/token",
                client_id="cli",
            )

    def test_refresh_token_failure_raises(self, helper, monkeypatch):
        """token endpoint 실패 시 에러 (크래시 아님)."""
        import urllib.request, urllib.error

        creds_mod = _load_mod(CREDENTIALS_MOD, "_creds_rt_fail_test")
        creds_mod.store("acme", "personal", "calendar_refresh_token", "1//rt")

        def mock_urlopen_fail(req, *args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://auth.example.com/token",
                code=401,
                msg="Unauthorized",
                hdrs=None,  # type: ignore
                fp=None,
            )

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)

        with pytest.raises(Exception):
            helper.refresh_token(
                team="acme",
                role="calendar",
                token_url="https://auth.example.com/token",
                client_id="cli",
            )


# ── 보안: 토큰 누출 방지 ──────────────────────────────────────────────────────

class TestTokenSecurity:
    """토큰 값이 stdout·예외 메시지·로그에 누출되지 않아야 한다."""

    def test_refresh_error_does_not_leak_token_value(self, helper, monkeypatch, capsys):
        """refresh 실패 에러 메시지에 토큰 평문 없음."""
        import urllib.request, urllib.error

        creds_mod = _load_mod(CREDENTIALS_MOD, "_creds_leak_test")
        secret_token = "super-secret-refresh-token-12345"
        creds_mod.store("acme", "personal", "issues_refresh_token", secret_token)

        def mock_urlopen_fail(req, *args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://auth.example.com/token",
                code=401,
                msg="Unauthorized",
                hdrs=None,  # type: ignore
                fp=None,
            )

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)

        try:
            helper.refresh_token(
                team="acme",
                role="issues",
                token_url="https://auth.example.com/token",
                client_id="cli",
            )
        except Exception as e:
            assert secret_token not in str(e), f"토큰 누출: {e}"

        captured = capsys.readouterr()
        assert secret_token not in captured.out, "stdout 토큰 누출"
        assert secret_token not in captured.err, "stderr 토큰 누출"


# ── stdlib 전용 확인 ──────────────────────────────────────────────────────────

class TestStdlibOnly:
    """oauth_helper.py 는 stdlib 만 써야 한다 (외부 의존성 0)."""

    def test_oauth_helper_imports_only_stdlib(self):
        """oauth_helper.py 내 import 가 모두 stdlib 이다."""
        _STDLIB = {
            "base64", "hashlib", "http", "http.server", "http.client",
            "importlib", "importlib.util", "json", "os", "pathlib", "re",
            "secrets", "socket", "sys", "threading", "time", "typing",
            "urllib", "urllib.error", "urllib.parse", "urllib.request",
            "webbrowser", "__future__",
        }
        import ast

        src = HELPER_MOD.read_text(encoding="utf-8")
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in _STDLIB or top.startswith("_"), (
                        f"외부 의존성 감지: {alias.name!r} — stdlib 만 허용"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top in _STDLIB or top.startswith("_"), (
                        f"외부 의존성 감지: from {node.module} — stdlib 만 허용"
                    )


# [P1 삭제] role_server 폐기 — TestRoleServerTokenBranching(get_token_for_role 토큰
# 로드 분기 4건)은 제거됐다. OAuth 헬퍼 자체 테스트는 위에 그대로 보존된다.
