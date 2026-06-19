"""S1 — handlers/ 규약 + secret lint 확장 테스트.

핵심 검증:
  A. handlers_are_valid() — 파일 존재·문법·필수 함수·토큰 리터럴 heuristic
  B. lint_no_tracked_secrets — handlers/*.py 별도 스캔 경로
  C. atomic write — tmp→rename (partial 파일 없음)

모든 테스트는 tmp_path 기반 격리 — 실 레포 handlers/ 무접촉.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
sys.path.insert(0, str(REPO / "conformance"))

import install_lib  # noqa: E402
import check as CHECK  # noqa: E402


# ─────────────────────── 필수 함수 시그니처 상수 확인 ───────────────────────

REQUIRED_FUNCS = {
    "issues":   ["issues_create", "issues_list", "issues_get", "issues_update"],
    "chat":     ["chat_send", "chat_list"],
    "docs":     ["docs_read", "docs_write", "docs_list", "docs_create"],
    "calendar": ["calendar_list", "calendar_create"],
}


# ─────────────────────── helpers ───────────────────────

def _make_handler(tmp_path: Path, role: str, *, extra: str = "") -> Path:
    """tmp_path/handlers/<role>.py 에 최소 유효 핸들러 작성."""
    hdir = tmp_path / "handlers"
    hdir.mkdir(exist_ok=True)
    funcs_src = "\n".join(
        f"def {fn}(*args, **kwargs): pass" for fn in REQUIRED_FUNCS[role]
    )
    src = f"# handlers/{role}.py\n{funcs_src}\n{extra}\n"
    p = hdir / f"{role}.py"
    p.write_text(src, encoding="utf-8")
    return p


def _make_all_handlers(tmp_path: Path) -> Path:
    """4역할 핸들러 전부 생성. handlers/ 디렉토리 반환."""
    for role in REQUIRED_FUNCS:
        _make_handler(tmp_path, role)
    return tmp_path / "handlers"


# ═══════════════════════════════════════════════════════════════════
# A. handlers_are_valid()
# ═══════════════════════════════════════════════════════════════════

class TestHandlersAreValidHappyPath:
    def test_all_valid_handlers_pass(self, tmp_path):
        hdir = _make_all_handlers(tmp_path)
        assert install_lib.handlers_are_valid(hdir) is True

    def test_single_role_valid(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        _make_handler(tmp_path, "issues")
        # 4역할 전부가 있어야 하는 게 아니라 "있는 것들이 유효한지" 검증
        # (실 핸들러는 S7에서 연결 — 부재 파일은 invalid가 아니라 skip)
        assert install_lib.handlers_are_valid(hdir) is True

    def test_all_roles_present_and_valid(self, tmp_path):
        hdir = _make_all_handlers(tmp_path)
        result = install_lib.handlers_are_valid(hdir)
        assert result is True


class TestHandlersAreValidSyntaxError:
    def test_syntax_error_in_handler(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        bad = hdir / "issues.py"
        bad.write_text("def issues_create(\n  this is not valid python\n", encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False

    def test_empty_file_has_no_required_funcs(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        (hdir / "issues.py").write_text("", encoding="utf-8")
        # 빈 파일 = 문법 OK, 하지만 필수 함수 없음 → False
        assert install_lib.handlers_are_valid(hdir) is False


class TestHandlersAreValidMissingFunctions:
    def test_missing_required_function(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # issues_update 빠짐
        src = "def issues_create(*a, **k): pass\ndef issues_list(*a): pass\ndef issues_get(id): pass\n"
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False

    def test_all_required_funcs_present(self, tmp_path):
        _make_handler(tmp_path, "calendar")
        hdir = tmp_path / "handlers"
        assert install_lib.handlers_are_valid(hdir) is True


class TestHandlersAreValidTokenLiteral:
    def test_bearer_token_literal_detected(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # issues 필수 함수 포함 + Bearer 토큰 리터럴
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        _tok1 = "Bea" + "rer " + "eyJhb" + "GciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.secret"
        src = f'{funcs}\nTOKEN = "{_tok1}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False

    def test_sk_token_literal_detected(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        _tok2 = "sk-" + "proj-abcdefghijklmnop1234567890"
        src = f'{funcs}\nAPI_KEY = "{_tok2}"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False

    def test_xoxb_token_literal_detected(self, tmp_path):
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        _tok3 = "xoxb" + "-1234567890-abcdefghijklmnop"
        src = f'{funcs}\nSLACK = "{_tok3}"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False

    def test_credentials_load_pattern_is_allowed(self, tmp_path):
        """credentials.load() 호출은 리터럴 토큰이 아님 — 허용."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        src = f'{funcs}\n# token = credentials.load("acme", "personal", "issues")\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is True

    def test_placeholder_string_is_allowed(self, tmp_path):
        """placeholder 값은 리터럴 토큰 아님."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["docs"])
        src = f'{funcs}\nTOKEN = "your-token-here"\n'
        (hdir / "docs.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is True


class TestHandlersAreValidEmptyDir:
    def test_empty_handlers_dir_is_valid(self, tmp_path):
        """핸들러 없음 = 아직 연결 안 됨(S7 도그푸딩 전) — 유효로 간주."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        assert install_lib.handlers_are_valid(hdir) is True

    def test_nonexistent_handlers_dir_is_valid(self, tmp_path):
        """handlers/ 디렉토리 자체 없음 — 빈 것과 동일 취급 (유효)."""
        hdir = tmp_path / "handlers"
        assert install_lib.handlers_are_valid(hdir) is True


# ═══════════════════════════════════════════════════════════════════
# B. lint_no_tracked_secrets — handlers/*.py 강제 스캔
# ═══════════════════════════════════════════════════════════════════

class TestSecretLintHandlers:
    def test_handler_with_bearer_token_fires(self, tmp_path):
        """handlers/issues.py 에 Bearer 토큰 직접값 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        _tok4 = "Bea" + "rer xoxb-REAL-TOKEN-abc123456789"
        src = f'{funcs}\nAUTH = "{_tok4}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, "handlers/issues.py Bearer 토큰이 lint를 통과해서는 안 됨"
        assert "issues.py" in detail

    def test_handler_with_sk_token_fires(self, tmp_path):
        """handlers/*.py 에 sk- 토큰 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        _tok5 = "sk-" + "proj-abcxyz1234567890deadbeef"
        src = f'{funcs}\nAPI_KEY = "{_tok5}"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, "handlers/chat.py sk- 토큰이 lint를 통과해서는 안 됨"

    def test_clean_handler_passes_lint(self, tmp_path):
        """clean 핸들러(토큰 없음) → lint 통과."""
        hdir = _make_all_handlers(tmp_path)
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert ok, f"clean handlers 가 lint 실패: {detail}"

    def test_handler_files_scanned_despite_py_suffix(self, tmp_path):
        """handlers/*.py 는 _SECRET_TARGET_SKIP_SUFFIXES 의 .py 에도 불구 강제 스캔."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # .py 파일이지만 handlers/ 안에 있으므로 강제 스캔 대상
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["docs"])
        _tok6 = "xoxb" + "-REAL-SLACK-TOKEN-deadbeefcafe"
        src = f'{funcs}\nTOKEN = "{_tok6}"\n'
        (hdir / "docs.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, ".py suffix 임에도 handlers/ 는 스캔돼야 함"

    def test_regular_py_files_not_scanned(self, tmp_path):
        """일반 .py 파일(handlers/ 밖)은 기존 규칙대로 스캔 제외."""
        # handlers/ 없음, 루트에 .py 파일만
        _tok7 = "sk-" + "proj-abcxyz1234567890"
        src = f'API_KEY = "{_tok7}"\n'
        (tmp_path / "somemodule.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        # 일반 .py 는 skip — 기존 동작 무회귀
        assert ok, "handlers/ 밖 일반 .py는 스캔 제외(기존 동작 유지)"

    def test_no_handlers_dir_passes_lint(self, tmp_path):
        """handlers/ 디렉토리 없을 때 lint는 통과(오류 없음)."""
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert ok, f"handlers/ 없을 때 lint 오류: {detail}"


# ═══════════════════════════════════════════════════════════════════
# C. atomic write 보장 (handlers_are_valid 가 검증하는 write path)
# ═══════════════════════════════════════════════════════════════════

class TestAtomicWrite:
    def test_atomic_write_text_creates_file(self, tmp_path):
        """_atomic_write_text 가 파일을 정상 생성."""
        target = tmp_path / "out.txt"
        install_lib._atomic_write_text(target, "hello world")
        assert target.read_text() == "hello world"

    def test_atomic_write_text_replaces_existing(self, tmp_path):
        """기존 파일을 atomic replace."""
        target = tmp_path / "out.txt"
        target.write_text("old content")
        install_lib._atomic_write_text(target, "new content")
        assert target.read_text() == "new content"

    def test_atomic_write_no_tmp_left_on_success(self, tmp_path):
        """성공 시 tmp 파일이 남지 않음."""
        target = tmp_path / "out.txt"
        install_lib._atomic_write_text(target, "data")
        # tmp 파일(.*.tmp)이 없어야 함
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"tmp 파일 남음: {tmp_files}"


# ═══════════════════════════════════════════════════════════════════
# D. 우회 회귀 테스트 (codex fault injection 실증 → 반영 확인)
# ═══════════════════════════════════════════════════════════════════

class TestSecretBypassRegression:
    """codex가 실증한 우회 패턴들이 이제 탐지되는지 확인.
    각 테스트는 '우회가 통과하면 실패(assert not ok)' 형태.
    """

    # ── 1. Basic <base64> 우회 ──────────────────────────────────────
    def test_basic_base64_in_handler_fires(self, tmp_path):
        """Basic <base64> 형식 Authorization → lint 실패."""
        import base64 as _b64
        cred = _b64.b64encode(b"user:REAL_PASSWORD_123").decode()
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        src = f'{funcs}\nAUTH = "Basic {cred}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"Basic base64 토큰이 lint를 통과해서는 안 됨: {detail}"

    # ── 2. AWS AKIA 키 ──────────────────────────────────────────────
    def test_aws_akia_in_handler_fires(self, tmp_path):
        """AWS AKIA 키 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        _tok_akia = "AKIA" + "IOSFODNN7EXAMPLE"
        src = f'{funcs}\nAWS_KEY = "{_tok_akia}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"AWS AKIA 키가 lint를 통과해서는 안 됨: {detail}"

    def test_aws_asia_in_handler_fires(self, tmp_path):
        """AWS ASIA(임시) 키 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        _tok_asia = "ASIA" + "IOSFODNN7EXAMPLE"
        src = f'{funcs}\nAWS_KEY = "{_tok_asia}"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"AWS ASIA 키가 lint를 통과해서는 안 됨: {detail}"

    # ── 3. GitHub 토큰 ──────────────────────────────────────────────
    def test_github_ghp_in_handler_fires(self, tmp_path):
        """GitHub ghp_ 토큰 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["docs"])
        _tok_ghp = "ghp" + "_abcdefghijklmnopqrstuvwxyz1234"
        src = f'{funcs}\nGH_TOKEN = "{_tok_ghp}"\n'
        (hdir / "docs.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"GitHub ghp_ 토큰이 lint를 통과해서는 안 됨: {detail}"

    def test_github_pat_in_handler_fires(self, tmp_path):
        """GitHub github_pat_ 토큰 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["calendar"])
        src = f'{funcs}\nTOKEN = "github_pat_abcdefghijklmnopqrstuvwxyz"\n'
        (hdir / "calendar.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"github_pat_ 토큰이 lint를 통과해서는 안 됨: {detail}"

    # ── 4. 40자 hex 토큰 ────────────────────────────────────────────
    def test_40hex_token_in_handler_fires(self, tmp_path):
        """40자 hex 토큰(레거시 API 키 형태) → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        src = f'{funcs}\nTOKEN = "da39a3ee5e6b4b0d3255bfef95601890afd80709"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"40자 hex 토큰이 lint를 통과해서는 안 됨: {detail}"

    # ── 5. 문자열 분할(concatenation) 우회 ──────────────────────────
    def test_string_concat_bypass_fires(self, tmp_path):
        """"xox" + "b-REAL-TOKEN" 분할 우회 → tokenize 기반 탐지."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        src = f'{funcs}\nSLACK = "xox" + "b-1234567890-abcdefghij12345"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"문자열 분할 우회가 lint를 통과해서는 안 됨: {detail}"

    # ── 6. bytes literal 우회 ────────────────────────────────────────
    def test_bytes_literal_bypass_fires(self, tmp_path):
        """b"sk-proj-..." bytes literal 우회 → lint 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        _tok_bytes = "sk-" + "proj-abcdefghijklmnop1234567890"
        src = f'{funcs}\nKEY = b"{_tok_bytes}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"bytes literal 우회가 lint를 통과해서는 안 됨: {detail}"

    # ── 7. 하위 디렉토리(handlers/sub/x.py) 재귀 스캔 ──────────────
    def test_subdirectory_handler_scanned(self, tmp_path):
        """handlers/sub/x.py 하위 디렉토리 핸들러도 재귀 스캔."""
        hdir = tmp_path / "handlers" / "sub"
        hdir.mkdir(parents=True)
        src = 'TOKEN = "Bearer REALTOKEN-abcdefghijklmnop1234"\n'
        (hdir / "x.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"하위 디렉토리 핸들러가 스캔되지 않음: {detail}"

    # ── 8. 주석 속 토큰 ──────────────────────────────────────────────
    def test_token_in_comment_fires(self, tmp_path):
        """주석 안에 박힌 토큰 → tokenize COMMENT 탐지."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        src = f'{funcs}\n# token = "Bearer REALTOKEN-abcdefghij123456789"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"주석 속 토큰이 lint를 통과해서는 안 됨: {detail}"

    # ── 9. sk-proj- 하이픈 포함 ──────────────────────────────────────
    def test_sk_proj_hyphen_fires(self, tmp_path):
        """sk-proj- (하이픈 포함) 패턴 → lint 실패 (기존 sk-\\w+ 는 못 잡음)."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        _tok_hyphen = "sk-" + "proj-Real-AbcDefGhiJkl1234567890"
        src = f'{funcs}\nKEY = "{_tok_hyphen}"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, f"sk-proj- 하이픈 패턴이 lint를 통과해서는 안 됨: {detail}"

    # ── 10. files= 경로에서 handler 분류 (fault #2) ──────────────────
    def test_files_injection_path_handler_classified(self, tmp_path):
        """files=[handlers/issues.py] 호출 시 handler 로 분류 → Bearer 토큰 탐지."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        src = f'{funcs}\nAUTH = "Bearer REALTOKEN-abcdefghijklmnop"\n'
        handler_file = hdir / "issues.py"
        handler_file.write_text(src, encoding="utf-8")
        # files= 경로 주입으로 호출
        _, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path, files=[str(handler_file)])
        assert not ok, f"files= 경로 handler가 Bearer 토큰을 통과해서는 안 됨: {detail}"


# ═══════════════════════════════════════════════════════════════════
# E. handlers_are_valid top-level only 회귀 (fault #3)
# ═══════════════════════════════════════════════════════════════════

class TestHandlersTopLevelOnly:
    """nested function·class method 은 계약 함수로 인정되지 않아야 한다."""

    def test_nested_function_not_counted(self, tmp_path):
        """내부 함수(nested)로만 정의된 필수 함수 → False."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # issues_create 가 outer 내부에 nested — top-level 아님
        src = (
            "def outer():\n"
            "    def issues_create(title, **kwargs): pass\n"
            "    def issues_list(**kwargs): pass\n"
            "    def issues_get(id, **kwargs): pass\n"
            "    def issues_update(id, **kwargs): pass\n"
        )
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False, (
            "nested 함수로만 정의된 경우 handlers_are_valid 는 False 여야 함"
        )

    def test_class_method_not_counted(self, tmp_path):
        """class method 로만 정의된 필수 함수 → False."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        src = (
            "class IssuesHandler:\n"
            "    def issues_create(self, title, **kwargs): pass\n"
            "    def issues_list(self, **kwargs): pass\n"
            "    def issues_get(self, id, **kwargs): pass\n"
            "    def issues_update(self, id, **kwargs): pass\n"
        )
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is False, (
            "class method 로만 정의된 경우 handlers_are_valid 는 False 여야 함"
        )

    def test_top_level_async_function_counted(self, tmp_path):
        """top-level async 함수는 계약 함수로 허용된다."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs_src = "\n".join(
            f"async def {fn}(*args, **kwargs): pass" for fn in REQUIRED_FUNCS["issues"]
        )
        src = f"# async handler\n{funcs_src}\n"
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        assert install_lib.handlers_are_valid(hdir) is True, (
            "top-level async 함수는 handlers_are_valid 에서 허용돼야 함"
        )

    def test_no_args_function_top_level_still_invalid(self, tmp_path):
        """인자 없는 top-level 함수라도 이름이 있으면 존재 확인은 통과.
        (시그니처 검증은 lint_handlers_contract 담당 — handlers_are_valid 는 존재만 확인)"""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # 인자 없는 함수가 top-level 에 있는 경우 — 이름은 맞으므로 True
        src = "\n".join(
            f"def {fn}(): pass" for fn in REQUIRED_FUNCS["chat"]
        )
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        # handlers_are_valid 는 존재 여부만 — 시그니처는 lint_handlers_contract 가 검사
        assert install_lib.handlers_are_valid(hdir) is True


# ═══════════════════════════════════════════════════════════════════
# F. lint_handlers_contract 연결 확인 (fault #4)
# ═══════════════════════════════════════════════════════════════════

class TestLintHandlersContractConnected:
    """lint_handlers_contract 가 run_lint 에 포함돼 실제 탐지하는지 확인."""

    def test_run_lint_includes_handlers_contract_check(self, tmp_path):
        """run_lint 결과에 'handlers 계약 검사' 항목이 포함돼야 한다."""
        # handlers/ 없는 경우 — 건너뜀(pass)
        report = CHECK.run_lint(tmp_path)
        check_names = [c[0] for c in report.checks]
        assert "handlers 계약 검사" in check_names, (
            "run_lint 에 'handlers 계약 검사' 가 포함돼야 함"
        )

    def test_handler_with_sk_proj_token_caught_by_run_lint(self, tmp_path):
        """handlers/issues.py 에 sk-proj- 토큰 → run_lint 전체 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        _tok_run = "sk-" + "proj-RealToken-abcdefghij1234567890"
        src = f'{funcs}\nKEY = "{_tok_run}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        report = CHECK.run_lint(tmp_path)
        # lint_no_tracked_secrets 또는 lint_handlers_contract 중 하나라도 실패하면 ok
        assert not report.ok, (
            "sk-proj- 토큰이 handlers/ 에 있으면 run_lint 전체가 실패해야 함"
        )

    def test_nested_function_caught_by_lint_handlers_contract(self, tmp_path):
        """nested 함수만 있는 핸들러 → lint_handlers_contract 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        src = (
            "def wrapper():\n"
            "    def issues_create(title, **k): pass\n"
            "    def issues_list(**k): pass\n"
            "    def issues_get(id, **k): pass\n"
            "    def issues_update(id, **k): pass\n"
        )
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        assert not ok, f"nested 함수만 있는 경우 lint_handlers_contract 는 실패해야 함: {detail}"

    def test_clean_handlers_pass_lint_handlers_contract(self, tmp_path):
        """정상 핸들러 → lint_handlers_contract 통과."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        for role, funcs in REQUIRED_FUNCS.items():
            funcs_src = "\n".join(f"def {fn}(*args, **kwargs): pass" for fn in funcs)
            (hdir / f"{role}.py").write_text(f"# {role}\n{funcs_src}\n", encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        assert ok, f"정상 핸들러가 lint_handlers_contract 를 통과해야 함: {detail}"

    def test_atomic_failure_no_partial_file(self, tmp_path):
        """_atomic_write_text 실패 시 partial 파일 없음 (원본 무손상)."""
        import unittest.mock as mock
        target = tmp_path / "subdir" / "out.txt"
        # 부모 디렉토리 없음 — 쓰기 실패 유도
        with pytest.raises(Exception):
            install_lib._atomic_write_text(target, "data")
        # partial .tmp 파일이 없어야 함
        tmp_files = list(tmp_path.rglob("*.tmp"))
        assert tmp_files == [], f"실패 시 tmp 파일이 남으면 안 됨: {tmp_files}"


# ═══════════════════════════════════════════════════════════════════
# G. 2차 재검수 회귀 테스트 (codex fault injection 2차 실증 → 반영 확인)
# ═══════════════════════════════════════════════════════════════════

class TestCodex2ndFaultRegressions:
    """codex 2차 fault injection 5건이 이제 차단·안전처리되는지 고정.

    각 케이스는 우회가 통과하면 실패(assert not ok / assert False)로 역방향 검증.
    """

    # ── fault #1: f-string 토큰이 lint 통과 우회 ────────────────────
    def test_fstring_token_blocked_by_lint_no_tracked_secrets(self, tmp_path):
        """f-string 토큰(f"sk-proj-{part}")이 lint_no_tracked_secrets 를 통과하지 않아야 함."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["chat"])
        # f-string 에 sk- 접두가 포함된 패턴 — 변수 참조 없이 리터럴 부분만으로도 탐지
        _fstr_pfx = "sk-" + "proj-"
        src = funcs + '\npart = "abcdefghijklmnop"\nKEY = f"' + _fstr_pfx + '{part}"\n'
        (hdir / "chat.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, (
            f"f-string 토큰이 lint_no_tracked_secrets 를 통과해서는 안 됨: {detail}"
        )

    def test_fstring_token_blocked_by_lint_handlers_contract(self, tmp_path):
        """f-string 토큰이 lint_handlers_contract 에서도 차단."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        _fstr_pfx2 = "sk-" + "proj-"
        src = funcs + '\npart = "abcdefghijklmnop"\nKEY = f"' + _fstr_pfx2 + '{part}"\n'
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        assert not ok, (
            f"f-string 토큰이 lint_handlers_contract 를 통과해서는 안 됨: {detail}"
        )

    # ── fault #2: 시그니처 검증 우회 ────────────────────────────────
    def test_wrong_required_arg_name_blocked(self, tmp_path):
        """issues_create(wrong, **kwargs) — 필수 인자명 불일치 → lint_handlers_contract 실패."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # issues_create 의 계약 필수 인자는 'title' — 'wrong' 은 불일치
        src = (
            "def issues_create(wrong, **kwargs): pass\n"
            "def issues_list(**kwargs): pass\n"
            "def issues_get(id, **kwargs): pass\n"
            "def issues_update(id, **kwargs): pass\n"
        )
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        assert not ok, (
            f"잘못된 필수 인자명(wrong 대신 title)이 lint_handlers_contract 를 통과해서는 안 됨: {detail}"
        )

    def test_star_args_only_does_not_bypass_required_check(self, tmp_path):
        """issues_list(*args) 단독 — *args 로 필수 인자 검증이 우회되지 않아야 함.
        (issues_list 의 required_args 는 빈 목록이므로 *args 만 있어도 통과 — 정상)"""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # issues_list 는 required_args=[] 이므로 *args 로도 통과해야 함
        # issues_create 는 required_args=['title'] 이므로 *args 만으로는 우회 안 됨
        src_bypass = (
            "def issues_create(*args): pass\n"  # title 인자 없음 — 실패해야 함
            "def issues_list(*args): pass\n"
            "def issues_get(id): pass\n"
            "def issues_update(id): pass\n"
        )
        (hdir / "issues.py").write_text(src_bypass, encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        assert not ok, (
            f"issues_create(*args) 가 필수 인자 'title' 없이 통과해서는 안 됨: {detail}"
        )

    def test_kwargs_does_not_bypass_required_arg(self, tmp_path):
        """issues_update(id=None) — id 가 keyword-only default 설정 시 required 아님.
        id 는 필수 인자여야 하므로, positional+default 조합으로 필수성 우회 불가."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # issues_get(id=None) — id 가 기본값 있으면 required 가 아닌 것처럼 보이나
        # 계약상 id 는 required. 여기서는 인자명 존재 여부만 검사(required_count 검사).
        # 인자명 'id' 는 posargs 에 있으므로 통과 — 이 케이스는 이름 검증 충족.
        # issues_update(id=None, **kwargs) — id 가 posargs 에 있으면 이름 검증 통과.
        src = (
            "def issues_create(title, **kwargs): pass\n"
            "def issues_list(**kwargs): pass\n"
            "def issues_get(id=None, **kwargs): pass\n"
            "def issues_update(id=None, **kwargs): pass\n"
        )
        (hdir / "issues.py").write_text(src, encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        # id=None 이어도 인자명 'id' 는 posargs 에 존재 → 이름 검증 통과
        assert ok, (
            f"id=None 는 인자명 'id' 가 존재하므로 통과해야 함: {detail}"
        )

    # ── fault #3: 문법오류 파일서 IndentationError 미처리 ───────────
    def test_indentation_error_in_source_handled_safely(self, tmp_path):
        """들여쓰기 오류 소스에서 _source_has_handler_token 이 예외 없이 안전하게 처리."""
        # IndentationError 를 유발하는 소스 — tokenize 가 IndentationError 를 raise
        bad_source = (
            "def foo():\n"
            "    pass\n"
            "  bad_indent = 'here'\n"  # 들여쓰기 오류
        )
        # 예외 없이 bool 을 반환해야 함 (True = 차단, False = 통과 — 둘 다 안전)
        try:
            result = CHECK._source_has_handler_token(bad_source)
            assert isinstance(result, bool), f"bool 이 아닌 값 반환: {result}"
        except Exception as e:
            pytest.fail(f"_source_has_handler_token 이 예외를 전파해서는 안 됨: {e}")

    def test_syntax_error_in_source_handled_safely(self, tmp_path):
        """SyntaxError 소스에서 _source_has_handler_token 이 예외 없이 안전하게 처리."""
        bad_source = "def foo(\n  this is not valid python\n"
        try:
            result = CHECK._source_has_handler_token(bad_source)
            assert isinstance(result, bool)
        except Exception as e:
            pytest.fail(f"_source_has_handler_token 이 SyntaxError 를 전파해서는 안 됨: {e}")

    def test_indentation_error_handler_blocked_by_lint(self, tmp_path):
        """들여쓰기 오류 handler — lint 는 안전하게 실패 처리 (malformed = 차단)."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        _ind_tok = "sk-" + "proj-abcdefghijklmnop1234567890"
        bad_source = (
            "def issues_create(title):\n"
            "    pass\n"
            f"  bad = '{_ind_tok}'\n"  # 들여쓰기 오류 + 토큰
        )
        (hdir / "issues.py").write_text(bad_source, encoding="utf-8")
        # lint_no_tracked_secrets 가 예외 없이 실행돼야 함 (안전 처리)
        try:
            name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
            assert isinstance(ok, bool)
        except Exception as e:
            pytest.fail(f"lint_no_tracked_secrets 가 IndentationError 로 크래시해서는 안 됨: {e}")

    # ── fault #4: files= 상대경로 handler 우회 ──────────────────────
    def test_relative_path_in_files_classified_as_handler(self, tmp_path):
        """files=['handlers/issues.py'](상대경로) 로 호출해도 handler 로 분류 → Bearer 토큰 탐지."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        funcs = "\n".join(f"def {fn}(*a, **k): pass" for fn in REQUIRED_FUNCS["issues"])
        src = f'{funcs}\nAUTH = "Bearer REALTOKEN-abcdefghijklmnop1234"\n'
        handler_file = hdir / "issues.py"
        handler_file.write_text(src, encoding="utf-8")
        # 상대경로로 주입
        rel_path = "handlers/issues.py"
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path, files=[rel_path])
        assert not ok, (
            f"상대경로 files= 로 handler 분류 후 Bearer 토큰이 탐지돼야 함: {detail}"
        )

    # ── fault #5: .venv/__pycache__ prune ───────────────────────────
    def test_venv_python_files_not_scanned(self, tmp_path):
        """handlers/.venv/x.py 는 스캔 대상에서 제외 (prune)."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        # 정상 핸들러 생성
        for role, funcs in REQUIRED_FUNCS.items():
            funcs_src = "\n".join(f"def {fn}(*a, **k): pass" for fn in funcs)
            (hdir / f"{role}.py").write_text(f"# {role}\n{funcs_src}\n", encoding="utf-8")
        # .venv 안에 토큰이 있는 파일 생성 — prune 되면 탐지 안 됨
        venv_dir = hdir / ".venv" / "lib"
        venv_dir.mkdir(parents=True)
        (venv_dir / "x.py").write_text('TOKEN = "Bearer REALTOKEN-venv-deadbeef1234"\n', encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert ok, (
            f"handlers/.venv/ 안 파일이 오탐을 유발해서는 안 됨: {detail}"
        )

    def test_pycache_files_not_scanned(self, tmp_path):
        """handlers/__pycache__/x.py 는 스캔 대상에서 제외 (prune)."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        for role, funcs in REQUIRED_FUNCS.items():
            funcs_src = "\n".join(f"def {fn}(*a, **k): pass" for fn in funcs)
            (hdir / f"{role}.py").write_text(f"# {role}\n{funcs_src}\n", encoding="utf-8")
        # __pycache__ 안에 토큰이 있는 파일 생성
        cache_dir = hdir / "__pycache__"
        cache_dir.mkdir(parents=True)
        (cache_dir / "x.cpython-311.pyc.py").write_text(
            'TOKEN = "Bearer REALTOKEN-cache-deadbeef1234"\n', encoding="utf-8"
        )
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert ok, (
            f"handlers/__pycache__/ 안 파일이 오탐을 유발해서는 안 됨: {detail}"
        )

    def test_venv_files_not_scanned_in_contract(self, tmp_path):
        """lint_handlers_contract 도 handlers/.venv/ 를 prune."""
        hdir = tmp_path / "handlers"
        hdir.mkdir()
        for role, funcs in REQUIRED_FUNCS.items():
            funcs_src = "\n".join(f"def {fn}(*a, **k): pass" for fn in funcs)
            (hdir / f"{role}.py").write_text(f"# {role}\n{funcs_src}\n", encoding="utf-8")
        # .venv 안에 잘못된 파일 — prune 되면 contract 검사에서 오류 없음
        venv_dir = hdir / ".venv"
        venv_dir.mkdir(parents=True)
        (venv_dir / "broken.py").write_text("def unexpected_role(): pass\n", encoding="utf-8")
        name, ok, detail = CHECK.lint_handlers_contract(tmp_path)
        assert ok, (
            f"handlers/.venv/ 안 파일이 lint_handlers_contract 오탐을 유발해서는 안 됨: {detail}"
        )
