"""S2 — --check-mcp query CLI 테스트.

검증 묶음:
  - connected=true: tmp MCP config에 _teammode_managed 항목 있을 때
  - connected=false: 파일 없음, 항목 없음, _teammode_managed 아닌 alias
  - --agent 미지정 → exit!=0, stderr에 에러
  - --agent 잘못된 값 → exit!=0
  - claude·codex 양쪽 지원
  - 출력은 JSON (stdout)

모든 테스트는 tmp_path만 사용. 실 ~/.claude.json·~/.codex/config.toml 무접촉.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as install_mod  # noqa: E402


# ── 헬퍼 ──

def _run(argv, *, tmp_path):
    """install.main(argv) 호출, (exit_code, stdout_lines) 반환.

    stdout 캡처를 위해 install_mod.cmd_check_mcp 를 직접 호출(main 은 UTF-8 설정 등 사이드이펙트).
    """
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = install_mod.cmd_check_mcp(argv)
    return code, buf.getvalue().strip()


def _claude_mcp_file(tmp_path: Path) -> Path:
    """격리 claude MCP 파일 경로(settings_override=tmp_path 기준)."""
    p = tmp_path / "claude" / ".claude.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _codex_settings_file(tmp_path: Path) -> Path:
    """격리 codex settings 파일 경로."""
    p = tmp_path / "codex" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── claude 테스트 ──

def test_claude_connected_true(tmp_path):
    """claude MCP 파일에 tm-linear _teammode_managed 항목 → provider linear 조회 시 connected."""
    mcp = _claude_mcp_file(tmp_path)
    mcp.write_text(json.dumps({
        "mcpServers": {
            # 실제 등록 키는 tm-<provider> 별칭, _canonical_server 에 정규명.
            "tm-linear": {"_teammode_managed": True, "_canonical_server": "linear"}
        }
    }))
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "claude", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is True
    assert data["alias"] == "tm-linear"


def test_claude_connected_false_no_file(tmp_path):
    """MCP 파일 없음 → connected=false, 크래시 없음."""
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "claude", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


def test_claude_connected_false_alias_missing(tmp_path):
    """파일 있지만 linear 항목 없음 → connected=false."""
    mcp = _claude_mcp_file(tmp_path)
    mcp.write_text(json.dumps({
        "mcpServers": {
            "slack": {"_teammode_managed": True}
        }
    }))
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "claude", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


def test_claude_connected_false_not_teammode_managed(tmp_path):
    """같은 alias지만 _teammode_managed 없음(사용자가 직접 등록) → connected=false."""
    mcp = _claude_mcp_file(tmp_path)
    mcp.write_text(json.dumps({
        "mcpServers": {
            "linear": {"command": "npx", "args": ["-y", "@linear/mcp"]}
        }
    }))
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "claude", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


def test_claude_connected_false_parse_error(tmp_path):
    """MCP 파일이 깨진 JSON → connected=false (graceful, 크래시 없음)."""
    mcp = _claude_mcp_file(tmp_path)
    mcp.write_text("NOT VALID JSON {{{")
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "claude", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


# ── codex 테스트 ──

def test_codex_connected_true(tmp_path):
    """codex config.toml에 teammode-mcp 블록 + tm-linear 항목 → provider linear 조회 시 connected."""
    cfg = _codex_settings_file(tmp_path)
    cfg.write_text(
        "# teammode-mcp-start\n"
        "[mcp_servers.tm-linear]\n"
        "_teammode_managed = true\n"
        "# teammode-mcp-end\n"
    )
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "codex", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is True
    assert data["alias"] == "tm-linear"


def test_codex_connected_false_no_file(tmp_path):
    """codex 파일 없음 → connected=false."""
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "codex", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


def test_codex_connected_false_no_block(tmp_path):
    """codex config.toml에 teammode-mcp 블록 자체가 없음 → connected=false."""
    cfg = _codex_settings_file(tmp_path)
    cfg.write_text("[model]\nname = \"o4-mini\"\n")
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "codex", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


def test_codex_connected_false_alias_missing(tmp_path):
    """teammode-mcp 블록 있지만 linear 없음 → connected=false."""
    cfg = _codex_settings_file(tmp_path)
    cfg.write_text(
        "# teammode-mcp-start\n"
        "[mcp_servers.slack]\n"
        "_teammode_managed = true\n"
        "# teammode-mcp-end\n"
    )
    code, out = _run(
        ["--check-mcp", "linear", "--root", str(tmp_path),
         "--agent", "codex", "--settings", str(tmp_path)],
        tmp_path=tmp_path,
    )
    assert code == 0
    data = json.loads(out)
    assert data["connected"] is False


# ── 에러 케이스 ──

def test_agent_missing_exits_nonzero(tmp_path, capsys):
    """--agent 미지정 → exit!=0, stderr에 에러 메시지."""
    code = install_mod.cmd_check_mcp(
        ["--check-mcp", "linear", "--root", str(tmp_path)]
    )
    assert code != 0
    captured = capsys.readouterr()
    assert "agent" in captured.err.lower() or "에이전트" in captured.err


def test_agent_invalid_exits_nonzero(tmp_path, capsys):
    """--agent 잘못된 값(unknown) → exit!=0, stderr에 에러 메시지."""
    code = install_mod.cmd_check_mcp(
        ["--check-mcp", "linear", "--root", str(tmp_path), "--agent", "unknown"]
    )
    assert code != 0
    captured = capsys.readouterr()
    assert "agent" in captured.err.lower() or "에이전트" in captured.err


def test_provider_missing_exits_nonzero(tmp_path, capsys):
    """--check-mcp 값 미지정(빈 provider) → exit!=0."""
    code = install_mod.cmd_check_mcp(
        ["--check-mcp", "", "--root", str(tmp_path), "--agent", "claude"]
    )
    assert code != 0
