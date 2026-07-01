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
    # 등록 별칭 = tm-linear (resolve_server_alias). 런타임 도구명·매처 둘 다 별칭.
    return "mcp__tm-linear__create_issue" in json.dumps(settings)


# ────────────────────────── install-mcp 등록 (claude) ──────────────────────────

def test_claude_install_mcp_registers_connected_provider(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude(root, tmp_path)
    ad.install_mcp()
    data = json.loads(Path(ad.mcp_config_path).read_text())
    assert "tm-linear" in data["mcpServers"]         # tm-<provider> 별칭으로 등록
    assert data["mcpServers"]["tm-linear"]["_teammode_managed"] is True
    assert data["mcpServers"]["tm-linear"]["_canonical_server"] == "linear"


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
    assert "tm-linear" not in data.get("mcpServers", {})


def test_claude_install_mcp_coexists_with_user_server(tmp_path):
    # 사용자가 직접 등록한 `linear` 서버는 무접촉, teammode 는 `tm-linear` 로 공존 등록.
    # tm-<provider> 네임스페이스 분리 핵심: 동명 충돌 없이 둘 다 살아남는다.
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    mcp_path = tmp_path / "settings.claude.json"
    mcp_path.write_text(json.dumps(
        {"mcpServers": {"linear": {"command": "user-own"}}, "projects": {"x": 1}}))
    out = _claude(root, tmp_path).install_mcp()
    data = json.loads(mcp_path.read_text())
    assert data["mcpServers"]["linear"] == {"command": "user-own"}  # 사용자 것 무접촉
    assert data["mcpServers"]["tm-linear"]["_teammode_managed"] is True  # teammode 공존
    assert data["projects"] == {"x": 1}                              # 사용자 데이터 보존
    assert any("tm-linear" in c for c in out)


def test_resolve_server_alias_prefixes_tm(tmp_path):
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude(root, tmp_path)
    for name in ("linear", "slack", "notion", "google"):
        assert ad.resolve_server_alias(name) == "tm-" + name  # tm-<provider>(§2.8-2)
    # 멱등: 이미 접두 붙은 별칭은 중복 부착 안 함
    assert ad.resolve_server_alias("tm-linear") == "tm-linear"


# ────────────── 공식 호스티드 HTTP MCP 실등록 (issue #20) ──────────────

NOTION_CONNECTED = {"docs": {"provider": "notion", "scope": "team",
                            "database_id": "db1"}}
SLACK_CONNECTED = {"chat": {"provider": "slack", "scope": "team",
                           "channel_id": "C1"}}


def test_claude_install_mcp_notion_registers_http(tmp_path):
    # notion 은 공식 호스티드 MCP → claude http shape({type:http,url})로 실등록.
    root = _scaffold(tmp_path, NOTION_CONNECTED)
    out = _claude(root, tmp_path).install_mcp()
    entry = json.loads(Path(tmp_path / "settings.claude.json").read_text())[
        "mcpServers"]["tm-notion"]
    assert entry["type"] == "http"
    assert entry["url"] == "https://mcp.notion.com/mcp"
    assert entry["_teammode_managed"] is True
    assert entry["_canonical_server"] == "notion"
    assert "command" not in entry  # 추측 기동 커맨드 박지 않음
    assert any("호스티드" in c for c in out)


def test_codex_install_mcp_notion_registers_http_url(tmp_path):
    # codex 는 streamable HTTP → [mcp_servers.tm-notion] 에 url 라인.
    root = _scaffold(tmp_path, NOTION_CONNECTED)
    out = _codex(root, tmp_path).install_mcp()
    toml = Path(tmp_path / "codex.config.toml").read_text()
    assert "[mcp_servers.tm-notion]" in toml
    assert "url = 'https://mcp.notion.com/mcp'" in toml
    assert "command =" not in toml  # 호스티드는 기동 커맨드 미기재
    assert any("호스티드" in c for c in out)


def test_claude_install_mcp_slack_placeholder_with_manual_guidance(tmp_path):
    # slack 은 공식 호스티드 URL 없음 → placeholder(자리만) + 수동 등록 안내 메시지.
    root = _scaffold(tmp_path, SLACK_CONNECTED)
    out = _claude(root, tmp_path).install_mcp()
    entry = json.loads(Path(tmp_path / "settings.claude.json").read_text())[
        "mcpServers"]["tm-slack"]
    assert entry["_teammode_managed"] is True
    assert "type" not in entry and "url" not in entry and "command" not in entry
    # codex review P2-a: 안내는 관리 별칭(tm-slack) 기준 + placeholder 가 연결 안 됨을
    # 정직하게. provider 명(slack)으로 add 하라고 하면 별개 서버 생겨 불일치/오해.
    msg = " ".join(out)
    assert "claude mcp add tm-slack" in msg          # 관리 별칭으로 안내
    assert "claude mcp add slack -- " not in msg     # provider 명 단독 안내 금지
    assert "연결되지 않" in msg                       # placeholder 비동작 정직 표면화


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
    # kb-write-guard 는 서비스 슬롯 무관하게 항상 PreToolUse 에 등록(거버넌스 강제)
    assert "PreToolUse" in settings.get("hooks", {})
    assert "kb-write-guard" in json.dumps(settings.get("hooks", {}).get("PreToolUse", []))
    assert not _has_linear_matcher(settings.get("hooks", {}).get("PreToolUse", []))
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
    assert "[mcp_servers.tm-linear]" in txt          # tm-<provider> 별칭 섹션
    assert "_canonical_server = 'linear'" in txt     # 정규명은 별칭이 아닌 provider
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
    assert "tm-linear" in claude_data["mcpServers"]  # claude: top-level mcpServers
    assert "[mcp_servers.tm-linear]" in codex_txt    # codex: config.toml 블록


# ─────────────── P4-A: mcp.source 반영 (공식 command/args · 자작 path) ───────────────
#
# 실 providers/ 의 팩들은 P2 에서 command/repo/path 를 미기재("추측금지")라 placeholder
# 만 등록한다(위 기존 테스트가 그 경로를 커버). 여기서는 **데이터가 있을 때** install-mcp
# 가 그 기동 커맨드를 실제 등록에 반영하는지를 tmp provider 팩으로 검증한다.
# archive "MCP 마련"·§2.8: 공식/자작은 동일 처리(분기는 마련 방법뿐, 등록 경로 같음).

def _providers_dir_with(tmp_path, provider, mcp_extra):
    """tmp providers/ 디렉토리에 <provider>.json 한 개를 작성하고 경로 반환.

    mcp_extra = mcp 필드에 추가로 넣을 키(command/args/path/source 등). register_hint 는 필수.
    """
    pdir = tmp_path / "providers_custom"
    pdir.mkdir(exist_ok=True)
    pack = {
        "provider": provider,
        "token_guide": {"url": "https://example/tok", "steps": ["s1"]},
        "default_scope": "team",
        "auth": "api_key",
        "services": ["issues"],
        "resource_fields": [],
        "mcp": {"register_hint": "테스트 팩", **mcp_extra},
    }
    (pdir / f"{provider}.json").write_text(json.dumps(pack))
    return str(pdir)


def _claude_pd(root, tmp_path, providers_dir, name="settings"):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / f"{name}.json"),
        python="python3", team_root=str(root),
        mcp_config_path=str(tmp_path / f"{name}.claude.json"),
        providers_dir=providers_dir,
    )


def _codex_pd(root, tmp_path, providers_dir, name="codex"):
    return CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / f"{name}.config.toml"),
        python="python3", team_root=str(root),
        providers_dir=providers_dir,
    )


def test_claude_install_mcp_reflects_official_command(tmp_path):
    """팩 mcp.source=official + command/args 있음 → claude entry 에 command·args 실제 등록."""
    pd = _providers_dir_with(
        tmp_path, "linear",
        {"source": "official", "command": "npx",
         "args": ["-y", "@vendor/linear-mcp"]})
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    out = _claude_pd(root, tmp_path, pd).install_mcp()
    data = json.loads(Path(tmp_path / "settings.claude.json").read_text())
    entry = data["mcpServers"]["tm-linear"]
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@vendor/linear-mcp"]
    assert entry["_teammode_managed"] is True           # 소유 마커 유지
    assert entry.get("_mcp_source") == "official"
    assert any("기동 커맨드" in c for c in out)


def test_claude_install_mcp_reflects_custom_path(tmp_path):
    """팩 mcp.source=custom + path(infra/mcp/<provider>/) → <python> <path> 로 등록."""
    pd = _providers_dir_with(
        tmp_path, "linear",
        {"source": "custom", "path": "infra/mcp/linear/server.py"})
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    ad = _claude_pd(root, tmp_path, pd)
    ad.install_mcp()
    data = json.loads(Path(tmp_path / "settings.claude.json").read_text())
    entry = data["mcpServers"]["tm-linear"]
    assert entry["command"] == "python3"                # adapter python
    # path 는 team_root 기준 절대화돼 args 에 들어간다
    assert entry["args"][0].endswith("infra/mcp/linear/server.py")
    assert str(root) in entry["args"][0]
    assert entry.get("_mcp_source") == "custom"


def test_claude_install_mcp_no_launch_data_is_placeholder(tmp_path):
    """command/path 둘 다 없으면(P2 미기재) → 추측 없이 placeholder(자리만)로 등록."""
    pd = _providers_dir_with(tmp_path, "linear", {"source": "official"})
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    out = _claude_pd(root, tmp_path, pd).install_mcp()
    entry = json.loads(
        Path(tmp_path / "settings.claude.json").read_text())["mcpServers"]["tm-linear"]
    assert "command" not in entry                        # 추측 커맨드 없음
    assert entry["_teammode_managed"] is True
    assert entry["_register_hint"] == "테스트 팩"
    assert any("연결되지 않" in c for c in out)  # placeholder 비동작 정직 표면화(P2-a)


def test_codex_install_mcp_reflects_official_command(tmp_path):
    """codex: 팩 command/args 있음 → TOML 블록에 command·args 실제 등록."""
    pd = _providers_dir_with(
        tmp_path, "linear",
        {"source": "official", "command": "npx",
         "args": ["-y", "@vendor/linear-mcp"]})
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    out = _codex_pd(root, tmp_path, pd).install_mcp()
    txt = Path(tmp_path / "codex.config.toml").read_text()
    assert "[mcp_servers.tm-linear]" in txt
    assert "command = 'npx'" in txt
    assert "args = ['-y', '@vendor/linear-mcp']" in txt
    assert "_mcp_source = 'official'" in txt
    assert any("기동 커맨드" in c for c in out)


def test_codex_install_mcp_no_launch_data_is_placeholder(tmp_path):
    """codex: command/path 없음 → command 행 없이 placeholder TOML 블록."""
    pd = _providers_dir_with(tmp_path, "linear", {"source": "official"})
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    out = _codex_pd(root, tmp_path, pd).install_mcp()
    txt = Path(tmp_path / "codex.config.toml").read_text()
    assert "[mcp_servers.tm-linear]" in txt
    assert "command =" not in txt                        # 추측 커맨드 없음
    assert "_register_hint = '테스트 팩'" in txt
    assert any("연결되지 않" in c for c in out)  # placeholder 비동작 정직 표면화(P2-a)


def test_install_mcp_launch_command_is_idempotent(tmp_path):
    """기동 커맨드 등록도 멱등(두 번 돌려 바이트 동일)."""
    pd = _providers_dir_with(
        tmp_path, "linear",
        {"source": "official", "command": "npx", "args": ["-y", "x"]})
    root = _scaffold(tmp_path, LINEAR_CONNECTED)
    _claude_pd(root, tmp_path, pd).install_mcp()
    first = Path(tmp_path / "settings.claude.json").read_text()
    _claude_pd(root, tmp_path, pd).install_mcp()
    assert first == Path(tmp_path / "settings.claude.json").read_text()
