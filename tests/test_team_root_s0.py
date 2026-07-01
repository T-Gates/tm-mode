"""S0 — adapter --team-root CLI 옵션 + wire_agents --team-root 전달 테스트.

검증:
  1. claude adapter main() --team-root /abs/path → Adapter.team_root 가 그 경로
  2. codex adapter main() --team-root /abs/path → Adapter.team_root 가 그 경로
  3. --team-root 없으면 기존 기본값(here.parents[2]) 유지 (하위 호환)
  4. wire_agents(team_root=...) → run_adapter 호출 extra_args 에 --team-root 포함
  5. wire_agents(team_root=None) → --team-root 인자 없음 (하위 호환)

모든 테스트 tmp_path 만 — 실 ~/.claude·~/.codex 무접촉.
"""
import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402

# ── 어댑터 모듈 격리 로드 (runpy — 각 로드가 독립 네임스페이스) ──
def _load_claude():
    return runpy.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                          run_name="__claude_s0__")

def _load_codex():
    return runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                          run_name="__codex_s0__")


# ── 최소 팀 루트 픽스처 ──
def _scaffold_team_root(tmp_path):
    """claude·codex adapter install-mcp 가 실행될 수 있는 최소 구조."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "infra" / "hooks" / "manifest.json").write_text("[]")
    (root / "infra" / "agents" / "claude" / "events.json").write_text(json.dumps({
        "agent": "claude", "config_file": "~/.claude/settings.json",
        "events": {}, "actions": {}, "mcp_tool_format": "mcp__{server}__{tool}",
    }))
    (root / "infra" / "agents" / "codex" / "events.json").write_text(json.dumps({
        "agent": "codex", "config_file": "~/.codex/config.toml",
        "events": {}, "actions": {}, "mcp_tool_format": "{server}.{tool}",
    }))
    (root / "infra" / "agents" / "claude" / "normalize.py").write_text("# stub\n")
    (root / "infra" / "agents" / "codex" / "normalize.py").write_text("# stub\n")
    return root


# ─────────────────────────────────────────────────────
# 1. claude adapter Adapter(): team_root 파라미터 직접 검증
#    main()은 args.team_root를 Adapter(team_root=...) 에 전달해야 한다.
#    → main() 내부에서 Adapter 생성 직전 team_root 값을 캡처해 확인.
# ─────────────────────────────────────────────────────

def test_claude_adapter_team_root_param_overrides_default(tmp_path):
    """Adapter(team_root='/custom/path') → self.team_root 가 그 경로."""
    mod = _load_claude()
    Adapter = mod["Adapter"]
    root = tmp_path / "custom_team"
    root.mkdir(parents=True)
    # 최소 파일 생성 (events.json)
    (root / "infra" / "agents" / "claude").mkdir(parents=True, exist_ok=True)
    (root / "infra" / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "infra" / "agents" / "claude" / "events.json").write_text(json.dumps({
        "agent": "claude", "config_file": "~/.claude/settings.json",
        "events": {}, "actions": {}, "mcp_tool_format": "mcp__{server}__{tool}",
    }))
    (root / "infra" / "hooks" / "manifest.json").write_text("[]")
    ad = Adapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),
    )
    assert ad.team_root == root


def test_claude_adapter_default_team_root_from_file_location(tmp_path):
    """team_root=None 이면 agent_dir.parents[2] 를 쓴다 (기존 동작 유지)."""
    mod = _load_claude()
    Adapter = mod["Adapter"]
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "claude"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps({
        "agent": "claude", "config_file": "~/.claude/settings.json",
        "events": {}, "actions": {}, "mcp_tool_format": "mcp__{server}__{tool}",
    }))
    (hooks_dir / "manifest.json").write_text("[]")
    ad = Adapter(
        agent_dir=str(agent_dir),
        manifest_path=str(hooks_dir / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=None,  # 기본값 경로
    )
    # team_root=None 이면 agent_dir.parents[2] → root
    assert ad.team_root == root


# ─────────────────────────────────────────────────────
# 2. claude main(): --team-root CLI 옵션 파싱 확인
#    main()을 실제 호출하고 install-mcp 출력 확인
#    (team_root가 반영되면 config_path=team_root/team.config.json 이 설정됨)
# ─────────────────────────────────────────────────────

def test_claude_main_team_root_cli_option_accepted(tmp_path):
    """claude adapter main()이 --team-root 를 오류 없이 파싱한다."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_claude()
    # argparse 오류 없이 파싱되는지 확인 — install-mcp 실행 (빈 manifest → [info])
    rc = mod["main"]([
        "--team-root", str(root),
        "--settings", str(tmp_path / "settings.json"),
        "--mcp-config", str(tmp_path / "mcp.json"),
        "install-mcp",
    ])
    assert rc == 0


def test_claude_main_without_team_root_still_works(tmp_path):
    """--team-root 없어도 기존처럼 동작한다 (하위 호환)."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_claude()
    # _default_paths 패치: adapter.py 위치 기준 here.parents[2]가 tmp teamroot를 가리키게
    orig_default = mod["_default_paths"]
    def patched_default():
        return {
            "agent_dir": str(root / "infra" / "agents" / "claude"),
            "manifest_path": str(root / "infra" / "hooks" / "manifest.json"),
            "team_root": str(root),
        }
    mod["_default_paths"] = patched_default
    try:
        rc = mod["main"]([
            "--settings", str(tmp_path / "settings.json"),
            "--mcp-config", str(tmp_path / "mcp.json"),
            "install-mcp",
        ])
        assert rc == 0
    finally:
        mod["_default_paths"] = orig_default


# ─────────────────────────────────────────────────────
# 3. codex adapter: --team-root CLI 옵션
# ─────────────────────────────────────────────────────

def test_codex_adapter_team_root_param_overrides_default(tmp_path):
    """Codex Adapter(team_root='/custom/path') → self.team_root 가 그 경로."""
    mod = _load_codex()
    Adapter = mod["Adapter"]
    root = tmp_path / "custom_team"
    agent_dir = root / "infra" / "agents" / "codex"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps({
        "agent": "codex", "config_file": "~/.codex/config.toml",
        "events": {}, "actions": {}, "mcp_tool_format": "{server}.{tool}",
    }))
    (hooks_dir / "manifest.json").write_text("[]")
    ad = Adapter(
        agent_dir=str(agent_dir),
        manifest_path=str(hooks_dir / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python="python3",
        team_root=str(root),
    )
    assert ad.team_root == root


def test_codex_main_team_root_cli_option_accepted(tmp_path):
    """codex adapter main()이 --team-root 를 오류 없이 파싱한다."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_codex()
    rc = mod["main"]([
        "--team-root", str(root),
        "--config", str(tmp_path / "config.toml"),
        "install-mcp",
    ])
    assert rc == 0


def test_codex_main_without_team_root_still_works(tmp_path):
    """codex --team-root 없어도 기존처럼 동작한다 (하위 호환)."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_codex()
    orig_default = mod["_default_paths"]
    def patched():
        return {
            "agent_dir": str(root / "infra" / "agents" / "codex"),
            "manifest_path": str(root / "infra" / "hooks" / "manifest.json"),
            "team_root": str(root),
        }
    mod["_default_paths"] = patched
    try:
        rc = mod["main"]([
            "--config", str(tmp_path / "config.toml"),
            "install-mcp",
        ])
        assert rc == 0
    finally:
        mod["_default_paths"] = orig_default


# ─────────────────────────────────────────────────────
# 4. wire_agents: --team-root 가 run_adapter extra_args 에 포함
# ─────────────────────────────────────────────────────

def test_wire_agents_passes_team_root_to_install_mcp(tmp_path):
    """wire_agents(team_root=...) → claude install-mcp extra_args 에 --team-root 포함."""
    team_root = tmp_path / "myteam"
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=team_root,
                   run_adapter=run_adapter)

    extra = seen[("claude", "install-mcp")]
    assert "--team-root" in extra, f"--team-root 없음: {extra}"
    idx = extra.index("--team-root")
    assert extra[idx + 1] == str(team_root)


def test_wire_agents_passes_team_root_to_sync(tmp_path):
    """wire_agents(team_root=...) → claude sync extra_args 에도 --team-root 포함."""
    team_root = tmp_path / "myteam"
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=team_root,
                   run_adapter=run_adapter)

    extra = seen[("claude", "sync")]
    assert "--team-root" in extra, f"sync extra_args 에 --team-root 없음: {extra}"
    idx = extra.index("--team-root")
    assert extra[idx + 1] == str(team_root)


def test_wire_agents_codex_passes_team_root(tmp_path):
    """codex 에도 --team-root 가 전달된다."""
    team_root = tmp_path / "myteam"
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["codex"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=team_root,
                   run_adapter=run_adapter)

    extra = seen[("codex", "install-mcp")]
    assert "--team-root" in extra
    idx = extra.index("--team-root")
    assert extra[idx + 1] == str(team_root)


def test_wire_agents_no_team_root_no_flag(tmp_path):
    """team_root=None 이면 --team-root 인자가 extra_args 에 없다 (하위 호환)."""
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=None,
                   run_adapter=run_adapter)

    for verb in ("install-mcp", "sync"):
        extra = seen.get(("claude", verb), [])
        assert "--team-root" not in extra, \
            f"team_root=None 인데 --team-root 가 {verb} extra_args 에 있음: {extra}"


def test_wire_agents_team_root_value_is_str(tmp_path):
    """--team-root 값이 str(Path(...)) 형태로 전달된다."""
    team_root = tmp_path / "myteam"
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude", "codex"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=team_root,
                   run_adapter=run_adapter)

    for agent in ("claude", "codex"):
        extra = seen[(agent, "install-mcp")]
        idx = extra.index("--team-root")
        val = extra[idx + 1]
        # str 타입이고 절대경로
        assert isinstance(val, str)
        assert Path(val) == Path(str(team_root))


# ─────────────────────────────────────────────────────
# 4b. issue #26: wire_agents(member_name=...) → codex 에만 --member 전달
#     (Claude 는 install 이 settings.json env 로 따로 주입하므로 --member 미전달)
# ─────────────────────────────────────────────────────

def test_wire_agents_codex_passes_member(tmp_path):
    """member_name 지정 → codex 모든 동사 extra_args 에 --member <name> 포함."""
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["codex"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=tmp_path / "myteam",
                   member_name="leejhy",
                   run_adapter=run_adapter)

    for verb in ("install-mcp", "sync", "install-skills"):
        extra = seen[("codex", verb)]
        assert "--member" in extra, f"codex {verb} extra_args 에 --member 없음: {extra}"
        idx = extra.index("--member")
        assert extra[idx + 1] == "leejhy"


def test_wire_agents_claude_does_not_get_member(tmp_path):
    """claude 에는 --member 가 전달되지 않는다(claude=settings.json env 경로, 중복 방지)."""
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude", "codex"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=tmp_path / "myteam",
                   member_name="leejhy",
                   run_adapter=run_adapter)

    for verb in ("install-mcp", "sync", "install-skills"):
        extra = seen.get(("claude", verb), [])
        assert "--member" not in extra, f"claude {verb} 에 --member 가 샘: {extra}"
    # 대조: codex 에는 있어야 한다(같은 호출에서 분기 확인)
    assert "--member" in seen[("codex", "sync")]


def test_wire_agents_no_member_name_no_flag(tmp_path):
    """member_name=None(기본)이면 codex 에도 --member 없음(하위호환)."""
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["codex"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=tmp_path / "myteam",
                   run_adapter=run_adapter)

    for verb in ("install-mcp", "sync", "install-skills"):
        extra = seen.get(("codex", verb), [])
        assert "--member" not in extra, f"member_name 없는데 --member 가 {verb} 에 있음: {extra}"


def test_wire_agents_member_value_is_str(tmp_path):
    """--member 값이 str 형태로 전달된다(어댑터 argparse 가 받는 형태)."""
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["codex"], home=tmp_path,
                   settings_override=tmp_path / "iso",
                   team_root=tmp_path / "myteam",
                   member_name="leejhy",
                   run_adapter=run_adapter)

    extra = seen[("codex", "sync")]
    idx = extra.index("--member")
    assert isinstance(extra[idx + 1], str)


# ─────────────────────────────────────────────────────
# 5. 입력 검증: 빈 문자열 --team-root 거부
# ─────────────────────────────────────────────────────

def test_adapter_blank_team_root_raises(tmp_path):
    """Adapter(team_root='') → ValueError (조용한 폴백 금지)."""
    mod = _load_claude()
    Adapter = mod["Adapter"]
    root = _scaffold_team_root(tmp_path)
    with pytest.raises(ValueError, match="빈 문자열"):
        Adapter(
            agent_dir=str(root / "infra" / "agents" / "claude"),
            manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
            settings_path=str(tmp_path / "settings.json"),
            python="python3",
            team_root="",  # 빈 문자열 — 거부 대상
        )


def test_adapter_whitespace_team_root_raises(tmp_path):
    """Adapter(team_root='   ') → ValueError (공백 전용도 거부)."""
    mod = _load_claude()
    Adapter = mod["Adapter"]
    root = _scaffold_team_root(tmp_path)
    with pytest.raises(ValueError):
        Adapter(
            agent_dir=str(root / "infra" / "agents" / "claude"),
            manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
            settings_path=str(tmp_path / "settings.json"),
            python="python3",
            team_root="   ",  # 공백만 — 거부 대상
        )


def test_claude_main_blank_team_root_returns_error(tmp_path):
    """claude main(['--team-root', '', ...]) → rc=1 (명확한 에러)."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_claude()
    rc = mod["main"]([
        "--team-root", "",
        "--settings", str(tmp_path / "settings.json"),
        "--mcp-config", str(tmp_path / "mcp.json"),
        "install-mcp",
    ])
    assert rc == 1


def test_codex_main_blank_team_root_returns_error(tmp_path):
    """codex main(['--team-root', '', ...]) → rc=1 (명확한 에러)."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_codex()
    rc = mod["main"]([
        "--team-root", "",
        "--config", str(tmp_path / "config.toml"),
        "install-mcp",
    ])
    assert rc == 1


def test_wire_agents_blank_team_root_raises(tmp_path):
    """wire_agents(team_root='') → ValueError (조용한 '.' 변질 금지)."""
    def run_adapter(agent, verb, flag, path, extra_args=None):
        return 0
    with pytest.raises(ValueError, match="빈 문자열"):
        il.wire_agents(["claude"], home=tmp_path,
                       settings_override=tmp_path / "iso",
                       team_root="",
                       run_adapter=run_adapter)


# ─────────────────────────────────────────────────────
# 6. resolve 정규화: 상대경로 → 절대경로
# ─────────────────────────────────────────────────────

def test_adapter_relative_team_root_resolves_to_absolute(tmp_path):
    """Adapter(team_root='상대경로') → self.team_root 가 절대경로로 정규화된다."""
    mod = _load_claude()
    Adapter = mod["Adapter"]
    root = _scaffold_team_root(tmp_path)
    # 상대경로: root를 CWD로 해석하지 않고, 단순히 절대경로로 바뀌는지만 확인.
    # 실제 cwd에서의 상대경로를 구성해 전달 (resolve 여부만 테스트).
    ad = Adapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),  # 절대경로 기준 확인
    )
    assert ad.team_root.is_absolute(), "team_root 는 절대경로여야 합니다"


# ─────────────────────────────────────────────────────
# 7. e2e 효과 고정: 서로 다른 team_root → 서로 다른 team.config.json 읽힘
#    "team_root 를 무시하면 실패하는" 테스트 — 효과 미고정 탐지용
# ─────────────────────────────────────────────────────

def _make_team_root_with_services(tmp_path, name, services: dict):
    """team.config.json 에 지정 services 를 담은 최소 팀 루트 생성."""
    root = tmp_path / name
    (root / "infra" / "agents" / "claude").mkdir(parents=True)
    (root / "infra" / "hooks").mkdir(parents=True)
    (root / "infra" / "agents" / "claude" / "events.json").write_text(json.dumps({
        "agent": "claude", "config_file": "~/.claude/settings.json",
        "events": {}, "actions": {}, "mcp_tool_format": "mcp__{server}__{tool}",
    }))
    (root / "infra" / "hooks" / "manifest.json").write_text("[]")
    (root / "team.config.json").write_text(json.dumps({"services": services}))
    return root


def test_team_root_e2e_different_roots_yield_different_services(tmp_path):
    """서로 다른 team_root → _load_services() 결과가 달라야 한다.

    team_root 가 무시되면 두 Adapter 가 같은 config 를 읽어 이 테스트가 실패한다.
    """
    mod = _load_claude()
    Adapter = mod["Adapter"]

    services_A = {"issues": {"provider": "linear", "token": "tok-a"}}
    services_B = {"issues": {"provider": "github", "token": "tok-b"}}

    root_A = _make_team_root_with_services(tmp_path, "root_A", services_A)
    root_B = _make_team_root_with_services(tmp_path, "root_B", services_B)

    ad_A = Adapter(
        agent_dir=str(root_A / "infra" / "agents" / "claude"),
        manifest_path=str(root_A / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings_A.json"),
        python="python3",
        team_root=str(root_A),
    )
    ad_B = Adapter(
        agent_dir=str(root_B / "infra" / "agents" / "claude"),
        manifest_path=str(root_B / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings_B.json"),
        python="python3",
        team_root=str(root_B),
    )

    svc_A = ad_A._load_services()
    svc_B = ad_B._load_services()

    assert svc_A is not None, "root_A team.config.json 이 읽히지 않음"
    assert svc_B is not None, "root_B team.config.json 이 읽히지 않음"
    assert svc_A != svc_B, (
        "서로 다른 team_root 임에도 같은 services 가 읽혔다 — team_root 가 무시됐을 가능성"
    )
    # 구체 값으로 이중 확인 (team_root 를 바꿔치면 이 assert 들이 탐지)
    assert svc_A.get("issues", {}).get("provider") == "linear"
    assert svc_B.get("issues", {}).get("provider") == "github"


def test_team_root_e2e_config_path_bound_to_team_root(tmp_path):
    """Adapter 의 config_path 가 team_root / 'team.config.json' 으로 바인딩된다.

    team_root 가 무시되면 config_path 가 다른 team_root 를 가리키게 되어 실패.
    """
    mod = _load_claude()
    Adapter = mod["Adapter"]

    root_X = _make_team_root_with_services(tmp_path, "root_X",
                                            {"chat": {"provider": "slack"}})
    root_Y = _make_team_root_with_services(tmp_path, "root_Y",
                                            {"chat": {"provider": "webex"}})

    ad_X = Adapter(
        agent_dir=str(root_X / "infra" / "agents" / "claude"),
        manifest_path=str(root_X / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings_X.json"),
        python="python3",
        team_root=str(root_X),
    )
    ad_Y = Adapter(
        agent_dir=str(root_Y / "infra" / "agents" / "claude"),
        manifest_path=str(root_Y / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings_Y.json"),
        python="python3",
        team_root=str(root_Y),
    )

    # config_path 가 각자의 team_root 하위를 가리켜야 한다
    assert ad_X.config_path == root_X / "team.config.json", \
        f"ad_X.config_path={ad_X.config_path!r} ≠ {root_X / 'team.config.json'!r}"
    assert ad_Y.config_path == root_Y / "team.config.json", \
        f"ad_Y.config_path={ad_Y.config_path!r} ≠ {root_Y / 'team.config.json'!r}"

    # 실제 services 도 각자 읽힌다
    svc_X = ad_X._load_services()
    svc_Y = ad_Y._load_services()
    assert svc_X.get("chat", {}).get("provider") == "slack"
    assert svc_Y.get("chat", {}).get("provider") == "webex"
