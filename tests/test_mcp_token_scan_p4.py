"""P4-B — 자작 MCP(infra/mcp/**/*.py) 토큰 리터럴 딥스캔 재부착 (conformance/check.py).

P1 에서 handlers/ 폐기와 함께 handlers/*.py 전용 토큰 딥스캔을 지웠다. L2 재설계에서
infra/mcp/<provider>/ 는 자작 MCP 보관소이므로, 토큰 리터럴이 소스에 embed 되는 사고를
P1 폐기 딥스캔과 **동등**하게 잡아야 한다(대상만 handlers/ → infra/mcp/).

검증:
  - infra/mcp/ 하위 .py 에 Bearer·sk-·xoxb- 등 토큰 리터럴 → lint FAIL.
  - tokenize 우회(문자열 분할·bytes·base64·f-string) → lint FAIL.
  - credentials.load() 패턴·placeholder 값 → 통과(거짓양성 없음).
  - .py 라서 일반 데이터-린트는 건너뛰지만 infra/mcp/ 강제 스캔으로 잡힘.
  - infra/mcp/ 부재/빈 디렉토리 → no-op(대상 없으면 통과).
모든 테스트는 tmp_path 전용 — 실 레포 무접촉.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "conformance"))

import check  # noqa: E402


def _write_mcp(tmp_path, relpath, content):
    """tmp 팀 루트의 infra/mcp/<relpath> 에 자작 MCP 소스를 쓴다."""
    p = tmp_path / "infra" / "mcp" / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _lint(tmp_path):
    return check.lint_no_tracked_secrets(tmp_path)


# ── 토큰 리터럴 직접 embed → FAIL ──

def test_bearer_token_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "linear/server.py",
               'AUTH = "Bearer sk_live_abcdef1234567890"\n')
    _, ok, detail = _lint(tmp_path)
    assert not ok
    assert "server.py" in detail


def test_sk_token_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "notion/srv.py", 'KEY = "sk-proj-ABCDEFGHIJ1234567890"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


def test_xoxb_token_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "slack/srv.py", 'TOK = "xoxb-12345-67890-abcdefghij"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


def test_github_pat_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "x/s.py", 'T = "github_pat_11ABCDEFG0abcdefghij"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


def test_aws_akia_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "x/s.py", 'K = "AKIAIOSFODNN7ABCDEFG"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


def test_subdirectory_mcp_scanned(tmp_path):
    """infra/mcp/<provider>/sub/ 하위까지 재귀 스캔."""
    _write_mcp(tmp_path, "google/sub/deep.py", 'T = "Bearer abcdEFGH12345678"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


# ── tokenize 우회 → FAIL ──

def test_string_concat_bypass_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "x/s.py", 'T = "xox" + "b-12345-67890-abcdefghij"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


def test_bytes_literal_bypass_in_mcp_fires(tmp_path):
    _write_mcp(tmp_path, "x/s.py", 'T = b"sk-proj-ABCDEFGHIJ1234567890"\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


def test_token_in_comment_fires(tmp_path):
    _write_mcp(tmp_path, "x/s.py", '# real token: xoxb-12345-67890-abcdefghij\n')
    _, ok, _ = _lint(tmp_path)
    assert not ok


# ── 정상 코드 → 통과(거짓양성 없음) ──

def test_credentials_load_pattern_is_allowed(tmp_path):
    """credentials.load() 호출·변수 참조는 리터럴 토큰이 아님 — 허용(거짓양성 없음).

    NOTE: 딥스캔은 P1 폐기본과 동일하게 'Bearer <값>' **리터럴**(예 "Bearer %s")도
    보수적으로 의심한다. 자작 MCP 는 토큰뿐 아니라 스킴+토큰 리터럴 조립을 피하고
    헤더 어휘를 분리/상수화해야 한다(아래는 그 안전 패턴).
    """
    _write_mcp(tmp_path, "linear/server.py",
               'import credentials\n'
               'AUTH_SCHEME = "bearer"  # 스킴만, 토큰 미포함\n'
               'token = credentials.load(team, scope, "issues")\n'
               'def auth_header(tok):\n'
               '    return {"Authorization": AUTH_SCHEME.title() + " " + tok}\n')
    _, ok, detail = _lint(tmp_path)
    assert ok, detail


def test_placeholder_value_is_allowed(tmp_path):
    _write_mcp(tmp_path, "x/s.py", 'TOKEN = "changeme"\nKEY = "your-token-here"\n')
    _, ok, detail = _lint(tmp_path)
    assert ok, detail


# ── Bearer 과탐 완화 (P4 검수 WARN): 안전한 헤더 조립은 통과, 실토큰은 FAIL ──

def test_fstring_bearer_interpolation_is_allowed(tmp_path):
    """f"Bearer {token}" — 보간 placeholder 헤더는 실토큰 아님 → 통과."""
    _write_mcp(tmp_path, "linear/server.py",
               'def auth_header(token):\n'
               '    return {"Authorization": f"Bearer {token}"}\n')
    _, ok, detail = _lint(tmp_path)
    assert ok, detail


def test_format_bearer_interpolation_is_allowed(tmp_path):
    '''"Bearer {}".format(tok) / "Bearer {tok}".format() — 안전 조립 → 통과.'''
    _write_mcp(tmp_path, "linear/srv.py",
               'def h1(tok):\n'
               '    return "Bearer {}".format(tok)\n'
               'def h2(tok):\n'
               '    return "Bearer {tok}".format(tok=tok)\n')
    _, ok, detail = _lint(tmp_path)
    assert ok, detail


def test_percent_bearer_interpolation_is_allowed(tmp_path):
    '''"Bearer %s" % tok — printf 보간 헤더 → 통과.'''
    _write_mcp(tmp_path, "x/s.py", 'H = "Bearer %s" % tok\n')
    _, ok, detail = _lint(tmp_path)
    assert ok, detail


def test_real_bearer_token_literal_still_fires(tmp_path):
    """실토큰 리터럴 'Bearer sk-abc123...' 은 여전히 FAIL — 완화가 구멍 아님."""
    _write_mcp(tmp_path, "linear/server.py",
               'AUTH = "Bearer sk-abc123def456ghi789jkl"\n')
    _, ok, detail = _lint(tmp_path)
    assert not ok, detail


def test_real_bearer_token_in_fstring_still_fires(tmp_path):
    """f-string 안에 박힌 실토큰 f"Bearer sk-..." 도 FAIL."""
    _write_mcp(tmp_path, "x/s.py",
               'AUTH = f"Bearer sk-abc123def456ghi789xyz"\n')
    _, ok, detail = _lint(tmp_path)
    assert not ok, detail


def test_py_suffix_does_not_exempt_mcp(tmp_path):
    """일반 데이터-린트는 .py 를 skip 하지만 infra/mcp/ 강제 스캔으로 잡힌다."""
    # 같은 토큰을 infra/ 밖 일반 .py 에 두면(데이터-린트 skip) 안 잡히는 게 정상.
    (tmp_path / "other.py").write_text('T = "Bearer abcdEFGH12345678"\n')
    _, ok_outside, _ = _lint(tmp_path)
    assert ok_outside  # infra/mcp 밖 .py 는 데이터-린트 대상 아님
    # infra/mcp 안이면 잡힘
    _write_mcp(tmp_path, "x/s.py", 'T = "Bearer abcdEFGH12345678"\n')
    _, ok_inside, _ = _lint(tmp_path)
    assert not ok_inside


# ── 대상 없음 → no-op ──

def test_no_mcp_dir_passes(tmp_path):
    """infra/mcp/ 부재 → 딥스캔 대상 없음 → 통과."""
    _, ok, _ = _lint(tmp_path)
    assert ok


def test_empty_mcp_dir_passes(tmp_path):
    (tmp_path / "infra" / "mcp").mkdir(parents=True)
    _, ok, _ = _lint(tmp_path)
    assert ok


def test_clean_mcp_server_passes(tmp_path):
    _write_mcp(tmp_path, "linear/server.py",
               'def list_issues(client):\n'
               '    return client.issues.all()\n')
    _, ok, detail = _lint(tmp_path)
    assert ok, detail


# ── files= 주입 경로도 우회되지 않음 ──

def test_files_injection_path_scans_mcp(tmp_path):
    p = _write_mcp(tmp_path, "x/s.py", 'T = "xoxb-12345-67890-abcdefghij"\n')
    _, ok, _ = check.lint_no_tracked_secrets(tmp_path, files=[str(p)])
    assert not ok
