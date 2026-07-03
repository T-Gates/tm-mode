"""#3 잔여 — 비호스티드 provider(slack/google)의 기존 MCP 서버 감지.

install-mcp 는 호스티드 URL·기동 커맨드 없는 provider 에 죽은 placeholder 를
등록하고 수동 안내만 했다 — 사용자가 이미 동작하는 자기 MCP(예: 다른 이름의
slack MCP)를 가져도 미감지.

계약(codex 문답 2026-07-03 수렴):
  - 감지는 placeholder 대상 provider 에만 적용(호스티드/기동커맨드 실등록은 유지).
  - 감지 규칙(추측 최소·pack 스키마 확장 없음): 서버 키 토큰 매칭(비영숫자 구분,
    case-insensitive, tm- 네임스페이스 제외) / url 부분문자열 / command+args 부분문자열.
  - 감지되면 placeholder 미등록 + "[info] 기존 서버 발견 — tm-<provider> 별칭으로
    재사용하려면 …" 안내만(자동 채택은 소유권 마커 없어 금지).
  - stale managed placeholder 는 기존 remove-mcp 경로로 제거.
  - 미감지면 기존 동작(placeholder + 수동 안내) 그대로.

모든 테스트 tmp_path — 실 ~/.claude.json·~/.codex/config.toml 무접촉.
"""
from __future__ import annotations

import json
import runpy
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

_CLAUDE = runpy.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                         run_name="__claude_detect__")
_CODEX = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                        run_name="__codex_detect__")
ClaudeAdapter = _CLAUDE["Adapter"]
CodexAdapter = _CODEX["Adapter"]

SLACK_CONNECTED = {"chat": {"provider": "slack", "scope": "team"}}
LINEAR_CONNECTED = {"issues": {"provider": "linear", "scope": "personal"}}


def _scaffold(tmp_path, services):
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "hooks" / "manifest.json",
                root / "infra" / "hooks" / "manifest.json")
    for ag in ("claude", "codex"):
        shutil.copy(REPO / "infra" / "agents" / ag / "events.json",
                    root / "infra" / "agents" / ag / "events.json")
        (root / "infra" / "agents" / ag / "normalize.py").write_text("# stub\n")
    (root / "team.config.json").write_text(json.dumps(
        {"spec_version": "0.2", "team": {"name": "t"}, "services": services}))
    return root


def _claude(root, tmp_path):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3", team_root=str(root),
        mcp_config_path=str(tmp_path / "mcp.claude.json"),
        providers_dir=str(REPO / "providers"),
    )


def _codex(root, tmp_path):
    return CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "codex.config.toml"),
        python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"),
    )


# ── claude: 감지 → placeholder 미등록 + 안내 ──

def test_claude_detects_existing_slack_server_by_key(tmp_path):
    """서버 키 이름 토큰('my-slack')으로 감지 → placeholder 미등록 + [info] 안내."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _claude(root, tmp_path)
    Path(ad.mcp_config_path).write_text(json.dumps({
        "mcpServers": {"my-slack": {"command": "npx",
                                    "args": ["-y", "some-slack-mcp"]}}
    }))
    out = ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-slack" not in data["mcpServers"], "감지 시 placeholder 미등록"
    assert "my-slack" in data["mcpServers"], "사용자 서버 무접촉"
    blob = "\n".join(out)
    assert "[info]" in blob and "my-slack" in blob and "tm-slack" in blob, (
        f"기존 서버 발견 안내가 있어야 함: {out}")


def test_claude_detects_by_command_hint(tmp_path):
    """키 이름은 무관('team-chat')이지만 command/args 에 provider 명 → 감지."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _claude(root, tmp_path)
    Path(ad.mcp_config_path).write_text(json.dumps({
        "mcpServers": {"team-chat": {"command": "npx",
                                     "args": ["-y", "@acme/slack-mcp-server"]}}
    }))
    out = ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-slack" not in data["mcpServers"]
    assert any("team-chat" in c for c in out)


def test_claude_no_existing_server_keeps_placeholder(tmp_path):
    """미감지(관련 서버 없음) → 기존 동작: placeholder + 수동 안내."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _claude(root, tmp_path)
    Path(ad.mcp_config_path).write_text(json.dumps({
        "mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp"]}}
    }))
    out = ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-slack" in data["mcpServers"], "미감지면 placeholder 유지(회귀 없음)"
    assert any("placeholder" in c for c in out)


def test_claude_own_tm_alias_not_self_detected(tmp_path):
    """자기 네임스페이스(tm-slack placeholder 기등록)는 감지 대상 아님 — 멱등 유지."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _claude(root, tmp_path)
    ad.install_mcp()  # 1회: placeholder 등록
    out2 = ad.install_mcp()  # 2회: 자기 placeholder 를 '기존 서버'로 오인하면 안 됨
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-slack" in data["mcpServers"], "멱등: placeholder 유지"
    assert any("[ok]" in c or "placeholder" in c for c in out2)


def test_claude_detection_removes_stale_placeholder(tmp_path):
    """과거 placeholder 가 있는데 이후 사용자 서버가 생기면 → stale placeholder 제거."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _claude(root, tmp_path)
    ad.install_mcp()  # placeholder 등록
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-slack" in data["mcpServers"]
    # 사용자가 자기 slack MCP 를 등록
    data["mcpServers"]["my-slack"] = {"command": "npx", "args": ["slack-mcp"]}
    Path(ad.mcp_config_path).write_text(json.dumps(data, indent=2) + "\n")
    out = ad.install_mcp()
    data2 = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-slack" not in data2["mcpServers"], "감지 후 stale placeholder 제거"
    assert any("[remove-mcp]" in c for c in out)
    assert any("my-slack" in c for c in out)


def test_claude_hosted_provider_unaffected_by_user_server(tmp_path):
    """실등록 가능한 provider(linear, 호스티드 URL)는 사용자 동명 서버가 있어도 auto 등록 유지."""
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude(root, tmp_path)
    Path(ad.mcp_config_path).write_text(json.dumps({
        "mcpServers": {"my-linear": {"command": "npx", "args": ["linear-mcp"]}}
    }))
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-linear" in data["mcpServers"], "호스티드 provider 는 감지 무관 실등록"
    assert data["mcpServers"]["tm-linear"].get("type") == "http"


# ── codex: TOML config 전체 [mcp_servers.*] 스캔 ──

def test_codex_detects_existing_slack_server(tmp_path):
    """블록 밖 사용자 [mcp_servers.my-slack] 감지 → placeholder 미등록 + [info] 안내."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _codex(root, tmp_path)
    Path(ad.settings_path).write_text(
        "[mcp_servers.my-slack]\n"
        "command = 'npx'\n"
        "args = ['-y', 'some-slack-mcp']\n"
    )
    out = ad.install_mcp()
    text = Path(ad.settings_path).read_text()
    assert "[mcp_servers.tm-slack]" not in text, "감지 시 placeholder 미등록"
    assert "[mcp_servers.my-slack]" in text, "사용자 서버 무접촉"
    blob = "\n".join(out)
    assert "[info]" in blob and "my-slack" in blob and "tm-slack" in blob


def test_codex_no_existing_server_keeps_placeholder(tmp_path):
    """미감지 → 기존 동작: placeholder 블록 등록 + 수동 안내."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _codex(root, tmp_path)
    out = ad.install_mcp()
    text = Path(ad.settings_path).read_text()
    assert "[mcp_servers.tm-slack]" in text
    assert any("placeholder" in c for c in out)


def test_codex_own_tm_alias_not_self_detected(tmp_path):
    """자기 관리 블록 안 tm-slack placeholder 는 감지 대상 아님 — 멱등 유지."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    out2 = ad.install_mcp()
    text = Path(ad.settings_path).read_text()
    assert "[mcp_servers.tm-slack]" in text
    assert any("[ok]" in c for c in out2)


def test_codex_detection_removes_stale_placeholder(tmp_path):
    """placeholder 등록 후 사용자 서버 등장 → 재실행 시 placeholder 제거 + 안내."""
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    text = Path(ad.settings_path).read_text()
    assert "[mcp_servers.tm-slack]" in text
    Path(ad.settings_path).write_text(
        text + "\n[mcp_servers.my-slack]\ncommand = 'npx'\nargs = ['slack-mcp']\n")
    out = ad.install_mcp()
    text2 = Path(ad.settings_path).read_text()
    assert "[mcp_servers.tm-slack]" not in text2
    assert "[mcp_servers.my-slack]" in text2
    assert any("my-slack" in c for c in out)
