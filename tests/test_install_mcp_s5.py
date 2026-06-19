"""S5 — install-mcp 확장 + 공존 전략 테스트.

검증 항목:
  1. claude: handlers/ 있으면 teammode alias 등록 + 기존 provider alias 공존
  2. claude: handlers/ 없으면 teammode alias 미등록
  3. claude: handlers/ 있어도 기존 linear 등 alias 소멸 안 됨 (desired_aliases 공존)
  4. claude: teammode 등록 entry에 command/args/cwd 포함
  5. claude: cwd = team_root 절대경로
  6. codex: handlers/ 있으면 teammode 블록 등록 + 기존 provider 블록 공존
  7. codex: handlers/ 없으면 teammode 블록 미등록
  8. codex: teammode entry에 command/args/cwd 포함
  9. 공존 멱등: 이미 teammode + linear 있으면 재실행 시 둘 다 유지
  10. handlers/ 디렉토리 있지만 .py 파일 없으면 teammode 미등록

모든 테스트 tmp_path 격리 — 실 ~/.claude.json·~/.codex/config.toml 무접촉.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import runpy

_CLAUDE = runpy.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                         run_name="__claude_s5__")
_CODEX = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                        run_name="__codex_s5__")
ClaudeAdapter = _CLAUDE["Adapter"]
CodexAdapter = _CODEX["Adapter"]


# ── 공용 픽스처 헬퍼 ──

def _scaffold(tmp_path, services=None, with_handlers=False, handlers_empty=False):
    """tmp 팀 루트 생성. with_handlers=True면 handlers/ 디렉토리 + 더미 .py 파일 생성."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "hooks" / "manifest.json",
                root / "infra" / "hooks" / "manifest.json")
    shutil.copy(REPO / "infra" / "agents" / "claude" / "events.json",
                root / "infra" / "agents" / "claude" / "events.json")
    shutil.copy(REPO / "infra" / "agents" / "codex" / "events.json",
                root / "infra" / "agents" / "codex" / "events.json")
    shutil.copy(REPO / "infra" / "agents" / "claude" / "adapter.py",
                root / "infra" / "agents" / "claude" / "adapter.py")
    (root / "infra" / "agents" / "claude" / "normalize.py").write_text("# stub\n")
    (root / "infra" / "agents" / "codex" / "normalize.py").write_text("# stub\n")

    cfg = {"spec_version": "0.1", "team": {"name": "tgates"}}
    if services is not None:
        cfg["services"] = services
        (root / "team.config.json").write_text(json.dumps(cfg))

    if with_handlers:
        handlers_dir = root / "handlers"
        handlers_dir.mkdir(exist_ok=True)
        if not handlers_empty:
            # 더미 핸들러 파일 (내용 무관 — 존재 여부만 S5 체크)
            (handlers_dir / "issues.py").write_text(
                "def issues_create(title): return {'id': '1', 'title': title}\n")
    elif handlers_empty:
        # handlers/ 디렉토리만 있고 .py 파일 없음
        (root / "handlers").mkdir(exist_ok=True)

    return root


def _claude(root, tmp_path, name="settings"):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / f"{name}.json"),
        python="python3", team_root=str(root),
        mcp_config_path=str(tmp_path / f"{name}.claude.json"),
        providers_dir=str(REPO / "providers"),
    )


def _codex(root, tmp_path, name="codex"):
    return CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / f"{name}.config.toml"),
        python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"),
    )


LINEAR_CONNECTED = {"issues": {"provider": "linear", "scope": "personal"}}


# ─────────────────────────────────────────────────────
# Claude adapter — teammode 등록
# ─────────────────────────────────────────────────────

def test_claude_registers_teammode_when_handlers_exist(tmp_path):
    """handlers/ + .py 파일 있으면 teammode alias 등록."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "teammode" in data["mcpServers"], \
        f"teammode 가 mcpServers 에 없음: {list(data['mcpServers'].keys())}"
    assert data["mcpServers"]["teammode"]["_teammode_managed"] is True


def test_claude_teammode_entry_has_command_args_cwd(tmp_path):
    """teammode entry에 command, args, cwd 포함 (실 서버 실행 가능)."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    entry = data["mcpServers"]["teammode"]
    assert "command" in entry, "teammode entry에 command 없음"
    assert "args" in entry, "teammode entry에 args 없음"
    assert "cwd" in entry, "teammode entry에 cwd 없음"


def test_claude_teammode_cwd_is_absolute_team_root(tmp_path):
    """teammode entry의 cwd가 team_root 절대경로."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    entry = data["mcpServers"]["teammode"]
    assert Path(entry["cwd"]).is_absolute(), f"cwd가 절대경로 아님: {entry['cwd']}"
    assert Path(entry["cwd"]) == root, f"cwd={entry['cwd']} ≠ root={root}"


def test_claude_teammode_args_contain_team_and_handlers_dir(tmp_path):
    """teammode args에 --team과 --handlers-dir 포함."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    entry = data["mcpServers"]["teammode"]
    args_str = " ".join(str(a) for a in entry["args"])
    assert "--team" in args_str, f"--team 없음: {args_str}"
    assert "--handlers-dir" in args_str, f"--handlers-dir 없음: {args_str}"


def test_claude_no_teammode_when_no_handlers_dir(tmp_path):
    """handlers/ 없으면 teammode alias 미등록."""
    root = _scaffold(tmp_path, services={}, with_handlers=False)
    ad = _claude(root, tmp_path)
    out = ad.install_mcp()
    if Path(ad.mcp_config_path).is_file():
        data = json.loads(Path(ad.mcp_config_path).read_text())
        assert "teammode" not in data.get("mcpServers", {}), \
            "handlers/ 없는데 teammode 등록됨"


def test_claude_no_teammode_when_handlers_dir_empty(tmp_path):
    """handlers/ 디렉토리만 있고 .py 파일 없으면 teammode 미등록."""
    root = _scaffold(tmp_path, services={}, handlers_empty=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    if Path(ad.mcp_config_path).is_file():
        data = json.loads(Path(ad.mcp_config_path).read_text())
        assert "teammode" not in data.get("mcpServers", {}), \
            "handlers/ 빈 디렉토리인데 teammode 등록됨"


# ─────────────────────────────────────────────────────
# Claude adapter — 공존 (기존 provider alias 소멸 안 됨)
# ─────────────────────────────────────────────────────

def test_claude_teammode_and_linear_coexist(tmp_path):
    """handlers/ 있고 linear 연결 → teammode + linear 둘 다 mcpServers에 존재."""
    root = _scaffold(tmp_path, services=LINEAR_CONNECTED, with_handlers=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    servers = data["mcpServers"]
    assert "linear" in servers, f"linear 소멸됨: {list(servers.keys())}"
    assert "teammode" in servers, f"teammode 없음: {list(servers.keys())}"


def test_claude_existing_provider_not_deleted_when_teammode_added(tmp_path):
    """기존 linear 등록 후 teammode 추가 → linear 소멸 안 됨."""
    root = _scaffold(tmp_path, services=LINEAR_CONNECTED, with_handlers=False)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    # linear 등록 확인
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "linear" in data["mcpServers"]

    # 이제 handlers/ 추가
    handlers_dir = root / "handlers"
    handlers_dir.mkdir(exist_ok=True)
    (handlers_dir / "issues.py").write_text("def issues_create(title): return {}\n")

    # 재실행 — linear 유지 + teammode 추가
    ad2 = _claude(root, tmp_path)
    ad2.install_mcp()
    data2 = json.loads(Path(ad.mcp_config_path).read_text())
    assert "linear" in data2["mcpServers"], \
        "handlers/ 추가 후 install-mcp 재실행에서 linear 소멸됨"
    assert "teammode" in data2["mcpServers"], \
        "handlers/ 추가 후 teammode 미등록"


def test_claude_coexistence_idempotent(tmp_path):
    """teammode + linear 공존 상태에서 install-mcp 재실행 → 동일 상태 유지."""
    root = _scaffold(tmp_path, services=LINEAR_CONNECTED, with_handlers=True)
    _claude(root, tmp_path).install_mcp()
    first = Path(tmp_path / "settings.claude.json").read_text()
    _claude(root, tmp_path).install_mcp()
    second = Path(tmp_path / "settings.claude.json").read_text()
    assert first == second, "공존 상태 멱등 위반"


def test_claude_canonical_server_field_is_teammode(tmp_path):
    """teammode entry의 _canonical_server 값이 'teammode'."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    entry = data["mcpServers"]["teammode"]
    assert entry.get("_canonical_server") == "teammode", \
        f"_canonical_server={entry.get('_canonical_server')!r} ≠ 'teammode'"


# ─────────────────────────────────────────────────────
# Codex adapter — teammode 등록
# ─────────────────────────────────────────────────────

def test_codex_registers_teammode_when_handlers_exist(tmp_path):
    """codex: handlers/ + .py 있으면 teammode 블록 등록."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    txt = Path(ad.settings_path).read_text()
    assert "[mcp_servers.teammode]" in txt, \
        f"[mcp_servers.teammode] 없음:\n{txt}"
    assert "_teammode_managed = true" in txt


def test_codex_teammode_entry_has_command_args_cwd(tmp_path):
    """codex teammode 블록에 command, args, cwd 포함."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    txt = Path(ad.settings_path).read_text()
    assert "command" in txt, "codex teammode 블록에 command 없음"
    assert "args" in txt, "codex teammode 블록에 args 없음"
    assert "cwd" in txt, "codex teammode 블록에 cwd 없음"


def test_codex_no_teammode_when_no_handlers_dir(tmp_path):
    """codex: handlers/ 없으면 teammode 블록 미등록."""
    root = _scaffold(tmp_path, services={}, with_handlers=False)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    txt = Path(ad.settings_path).read_text() if Path(ad.settings_path).is_file() else ""
    assert "[mcp_servers.teammode]" not in txt, \
        "handlers/ 없는데 codex teammode 블록 등록됨"


def test_codex_teammode_and_linear_coexist(tmp_path):
    """codex: handlers/ 있고 linear 연결 → teammode 블록 + linear 블록 둘 다 존재."""
    root = _scaffold(tmp_path, services=LINEAR_CONNECTED, with_handlers=True)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    txt = Path(ad.settings_path).read_text()
    assert "[mcp_servers.linear]" in txt, f"linear 블록 소멸됨:\n{txt}"
    assert "[mcp_servers.teammode]" in txt, f"teammode 블록 없음:\n{txt}"


def test_codex_coexistence_idempotent(tmp_path):
    """codex: teammode + linear 공존 상태 멱등."""
    root = _scaffold(tmp_path, services=LINEAR_CONNECTED, with_handlers=True)
    _codex(root, tmp_path).install_mcp()
    first = Path(tmp_path / "codex.config.toml").read_text()
    _codex(root, tmp_path).install_mcp()
    second = Path(tmp_path / "codex.config.toml").read_text()
    assert first == second, "codex 공존 멱등 위반"


def test_codex_teammode_cwd_is_absolute_team_root(tmp_path):
    """codex teammode 블록의 cwd가 team_root 절대경로."""
    root = _scaffold(tmp_path, services={}, with_handlers=True)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    txt = Path(ad.settings_path).read_text()
    # cwd 행 파싱: cwd = '/...' 형태
    import re
    m = re.search(r'cwd\s*=\s*[\'"]([^\'"]+)[\'"]', txt)
    assert m is not None, f"cwd 행 없음:\n{txt}"
    cwd = Path(m.group(1))
    assert cwd.is_absolute(), f"cwd가 절대경로 아님: {cwd}"
    assert cwd == root, f"cwd={cwd} ≠ root={root}"


# ─────────────────────────────────────────────────────
# 크로스에이전트 — claude + codex 둘 다
# ─────────────────────────────────────────────────────

def test_cross_agent_both_register_teammode_and_linear(tmp_path):
    """같은 팀 루트에서 claude·codex 둘 다 teammode + linear 공존."""
    root = _scaffold(tmp_path, services=LINEAR_CONNECTED, with_handlers=True)
    _claude(root, tmp_path).install_mcp()
    _codex(root, tmp_path).install_mcp()

    claude_data = json.loads(Path(tmp_path / "settings.claude.json").read_text())
    codex_txt = Path(tmp_path / "codex.config.toml").read_text()

    assert "linear" in claude_data["mcpServers"]
    assert "teammode" in claude_data["mcpServers"]
    assert "[mcp_servers.linear]" in codex_txt
    assert "[mcp_servers.teammode]" in codex_txt
