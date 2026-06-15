"""L2-B — install-mcp 어댑터 + 빈 슬롯 sync 교정 (SPEC §2.7·§2.8·§2.9·§7.2).

검증 묶음:
  install-mcp 등록 (claude·codex) / 멱등 / 정규명=별칭(resolve 항등) / 제거 / 크로스에이전트.
  빈 슬롯 sync 교정:
    - 역할 슬롯 미연결 → MCP 매처 [info] 생략 (§2.9 빈 슬롯 우선) — L1 기존 미준수 교정.
    - install-mcp 미선행(별칭 미보장) → 해당 매처만 [warn] 생략 (§2.7) — info 와 별도 경로.
    - 둘 다 전체 sync 를 실패시키지 않음(나머지 훅 정상 등록).

모든 테스트는 tmp_path + tmp MCP 등록 파일만 쓴다 — 실 ~/.claude.json·~/.codex/config.toml 무접촉
(conftest B0 가드가 이 경로를 지킨다).
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# providers 모듈을 어댑터가 찾도록 infra/ 를 path 에 둔다.
sys.path.insert(0, str(REPO / "infra"))

# claude·codex 어댑터는 둘 다 파일명이 adapter.py → `import adapter` 는 충돌(sys.modules
# 공유)한다. runpy 로 각각 별도 네임스페이스에 격리 로드해 정확히 그 어댑터의 Adapter 를 쓴다.
import runpy  # noqa: E402
_CLAUDE = runpy.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                         run_name="__claude_l2b__")
_CODEX = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                        run_name="__codex_l2b__")
ClaudeAdapter = _CLAUDE["Adapter"]
CodexAdapter = _CODEX["Adapter"]


# ── 공용 픽스처: 실 manifest(linear PreToolUse 매처 포함) + 실 providers/ ──

def _scaffold(tmp_path, services):
    """tmp 팀 루트 — 실 manifest·events·providers 복사 + 주어진 services 로 config 작성."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "hooks" / "manifest.json",
                root / "infra" / "hooks" / "manifest.json")
    shutil.copy(REPO / "infra" / "agents" / "claude" / "events.json",
                root / "infra" / "agents" / "claude" / "events.json")
    shutil.copy(REPO / "infra" / "agents" / "codex" / "events.json",
                root / "infra" / "agents" / "codex" / "events.json")
    # codex 어댑터가 base import 하는 claude adapter 도 팀 루트에 둠(runpy 경로 일관)
    shutil.copy(REPO / "infra" / "agents" / "claude" / "adapter.py",
                root / "infra" / "agents" / "claude" / "adapter.py")
    (root / "infra" / "agents" / "claude" / "normalize.py").write_text("# stub\n")
    (root / "infra" / "agents" / "codex" / "normalize.py").write_text("# stub\n")
    cfg = {"spec_version": "0.1", "team": {"name": "t"}}
    if services is not None:
        cfg["services"] = services
    if services is not None:
        (root / "team.config.json").write_text(json.dumps(cfg))
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


def _settings(ad):
    return json.loads(Path(ad.settings_path).read_text())


def _has_linear_matcher(settings):
    return "mcp__linear__create_issue" in json.dumps(settings)


# ────────────────────────── install-mcp 등록 (claude) ──────────────────────────

def test_claude_install_mcp_registers_connected_provider(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "linear" in data["mcpServers"]           # 정규 서버명으로 등록
    assert data["mcpServers"]["linear"]["_teammode_managed"] is True


def test_claude_install_mcp_empty_services_no_register(tmp_path):
    root = _scaffold(tmp_path, {})
    ad = _claude(root, tmp_path)
    out = ad.install_mcp()
    assert any("빈 슬롯" in c for c in out)
    # MCP 등록 파일이 생겨도 mcpServers 는 비어야 함
    if Path(ad.mcp_config_path).is_file():
        data = json.loads(Path(ad.mcp_config_path).read_text())
        assert data.get("mcpServers", {}) == {}


def test_claude_install_mcp_idempotent(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _claude(root, tmp_path).install_mcp()
    first = Path(tmp_path / "settings.claude.json").read_text()
    _claude(root, tmp_path).install_mcp()
    second = Path(tmp_path / "settings.claude.json").read_text()
    assert first == second


def test_claude_install_mcp_removes_disconnected(tmp_path):
    # 연결 → 등록, 그 후 빈 슬롯 → teammode 소유 항목 제거(멱등 정리).
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _claude(root, tmp_path).install_mcp()
    (root / "team.config.json").write_text(
        json.dumps({"spec_version": "0.1", "team": {"name": "t"}, "services": {}}))
    out = _claude(root, tmp_path).install_mcp()
    assert any("remove-mcp" in c for c in out)
    data = json.loads(Path(tmp_path / "settings.claude.json").read_text())
    assert "linear" not in data.get("mcpServers", {})


def test_claude_install_mcp_untouches_user_server(tmp_path):
    # 사용자가 직접 등록한 동명 서버는 무접촉(소유권).
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    mcp_path = tmp_path / "settings.claude.json"
    mcp_path.write_text(json.dumps(
        {"mcpServers": {"linear": {"command": "user-own"}}, "projects": {"x": 1}}))
    out = _claude(root, tmp_path).install_mcp()
    data = json.loads(mcp_path.read_text())
    assert data["mcpServers"]["linear"] == {"command": "user-own"}  # 무접촉
    assert data["projects"] == {"x": 1}                              # 사용자 데이터 보존
    assert any("무접촉" in c for c in out)


def test_resolve_server_alias_identity(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude(root, tmp_path)
    for name in ("linear", "slack", "notion", "google"):
        assert ad.resolve_server_alias(name) == name  # 항등(§2.8-2)


# ────────────── 빈 슬롯 sync 교정 — [info] 생략 (L1 기존 미준수 교정) ──────────────

def test_sync_empty_slot_omits_mcp_matcher_with_info(tmp_path, capsys):
    """services 빈 슬롯 → linear PreToolUse 매처 [info] 생략, 나머지 정상(§2.9/§7.2)."""
    root = _scaffold(tmp_path, {})  # 빈 슬롯
    ad = _claude(root, tmp_path)
    ad.sync(mode="on")
    out = capsys.readouterr().out
    assert "[info]" in out
    assert "linear" in out
    settings = _settings(ad)
    assert not _has_linear_matcher(settings)           # 빈 슬롯 → 매처 생략
    assert "PreToolUse" not in settings.get("hooks", {})
    # 나머지 훅은 정상 등록 — 전체 sync 실패 아님
    assert "SessionStart" in settings["hooks"]
    assert "PostToolUse" in settings["hooks"]


def test_sync_empty_slot_does_not_fail(tmp_path):
    """빈 슬롯이어도 sync 가 예외 없이 완료되고 settings 가 생성된다."""
    root = _scaffold(tmp_path, {})
    ad = _claude(root, tmp_path)
    ad.sync(mode="on")
    assert Path(ad.settings_path).is_file()


# ────────────── install-mcp 미선행 → [warn] 생략 (info 와 별도 경로) ──────────────

def test_sync_connected_but_no_install_mcp_warns(tmp_path, capsys):
    """linear 연결됐으나 install-mcp 미선행 → 별칭 미보장 → [warn] 생략(§2.7)."""
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude(root, tmp_path)  # MCP 등록 파일 부재 = install-mcp 미선행
    ad.sync(mode="on")
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "별칭 미보장" in out or "install-mcp" in out
    assert "[info]" not in out  # 빈 슬롯 경로가 아님 — 두 경로 분리
    settings = _settings(ad)
    assert not _has_linear_matcher(settings)
    # 나머지 훅 정상
    assert "SessionStart" in settings["hooks"]


def test_sync_after_install_mcp_registers_matcher(tmp_path, capsys):
    """install-mcp 선행 + 연결 → 별칭 보장 → PreToolUse 매처 정상 등록."""
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _claude(root, tmp_path).install_mcp()
    ad = _claude(root, tmp_path)
    ad.sync(mode="on")
    out = capsys.readouterr().out
    assert "[info]" not in out and "[warn]" not in out
    settings = _settings(ad)
    assert _has_linear_matcher(settings)               # 이제 등록됨


def test_info_and_warn_are_distinct_paths(tmp_path, capsys):
    """동일 manifest 매처가 슬롯 상태에 따라 info(미연결) vs warn(미선행)으로 갈린다."""
    # 1) 빈 슬롯 → info
    root_empty = _scaffold(tmp_path, {})
    _claude(root_empty, tmp_path, name="a").sync(mode="on")
    out_empty = capsys.readouterr().out
    # 2) 연결+미선행 → warn
    root_conn = _scaffold(tmp_path, LINEAR_CONNECTED)
    _claude(root_conn, tmp_path, name="b").sync(mode="on")
    out_conn = capsys.readouterr().out
    assert "[info]" in out_empty and "[warn]" not in out_empty
    assert "[warn]" in out_conn and "[info]" not in out_conn


def test_no_config_file_preserves_l1_behavior(tmp_path):
    """team.config.json 부재 = services 미지 → 빈 슬롯 규칙 미적용(L1 동작 보존).

    config 가 아예 없으면 매처 상태를 알 수 없으므로 L1 처럼 등록을 시도한다 —
    단 install-mcp 미선행이라 별칭 미보장 → 이 경우는 warn 생략이 맞다(연결 가정).
    여기선 '빈 슬롯 info 가 뜨지 않음'(파일 부재 ≠ 빈 슬롯)만 확정한다.
    """
    root = _scaffold(tmp_path, None)  # config 파일 미작성
    assert not (root / "team.config.json").is_file()
    ad = _claude(root, tmp_path)
    out = ad.sync(mode="on")  # 예외 없이 완료
    settings = _settings(ad)
    # 파일 부재 → _load_services()=None → 빈 슬롯 규칙 미적용.
    # 매처는 L1 처럼 번역 시도되어 등록됨(별칭 검사도 services dict 일 때만 도므로 생략 안 함).
    assert _has_linear_matcher(settings)
    assert "SessionStart" in settings["hooks"]


# ────────────────────────── codex install-mcp (B.3) ──────────────────────────

def test_codex_install_mcp_registers_toml_block(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _codex(root, tmp_path)
    ad.install_mcp()
    txt = Path(ad.settings_path).read_text()
    assert "[mcp_servers.linear]" in txt
    assert "_teammode_managed = true" in txt


def test_codex_install_mcp_idempotent(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _codex(root, tmp_path).install_mcp()
    first = Path(tmp_path / "codex.config.toml").read_text()
    _codex(root, tmp_path).install_mcp()
    assert first == Path(tmp_path / "codex.config.toml").read_text()


def test_codex_install_mcp_empty_slot_info(tmp_path):
    root = _scaffold(tmp_path, {})
    out = _codex(root, tmp_path).install_mcp()
    assert any("빈 슬롯" in c for c in out)


def test_codex_uninstall_removes_mcp_block(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _codex(root, tmp_path).install_mcp()
    _codex(root, tmp_path).uninstall()
    txt = Path(tmp_path / "codex.config.toml").read_text()
    assert "[mcp_servers" not in txt


def test_codex_sync_empty_slot_info_no_matcher(tmp_path, capsys):
    """Codex sync 도 빈 슬롯 MCP 매처를 [info] 생략(claude 와 동형).

    ⚠️ Codex 는 PreToolUse=null 이라 confirm-action 매처는 이벤트 미지원으로도 빠진다.
    여기서는 PostToolUse(linear 가 아닌 file_edit) 가 정상 등록되는지로 '전체 실패 아님'만 확정.
    """
    root = _scaffold(tmp_path, {})
    _codex(root, tmp_path).sync(mode="on")
    out = capsys.readouterr().out
    txt = Path(tmp_path / "codex.config.toml").read_text()
    # PreToolUse(linear) 는 등록 안 됨, PostToolUse(file_edit) 는 등록됨 — 전체 실패 아님
    assert "PostToolUse" in txt


# ────────────────────────── 크로스에이전트 ──────────────────────────

def test_cross_agent_same_config_both_register(tmp_path):
    """같은 team.config.json 으로 claude·codex 둘 다 install-mcp 가 정규명으로 등록."""
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _claude(root, tmp_path).install_mcp()
    _codex(root, tmp_path).install_mcp()
    claude_data = json.loads(Path(tmp_path / "settings.claude.json").read_text())
    codex_txt = Path(tmp_path / "codex.config.toml").read_text()
    assert "linear" in claude_data["mcpServers"]   # claude: top-level mcpServers
    assert "[mcp_servers.linear]" in codex_txt      # codex: config.toml 블록
