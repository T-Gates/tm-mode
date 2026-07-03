"""clone-and-go PR1 — install dry-run plan(계획) helper 계약 테스트.

계약(codex 2R 수렴): dry-run 이 동의 게이트로 충분하도록 계획을 **구조 데이터**로
계산(plan_install)하고 렌더(render_install_plan)한다. 계획은 실 wire 가 쓰는 것과
**단일 소스**(_AGENT_WIRE·agent_*_path·wire_agents 순서·ENV_VAR·profile_path_for·
manifest)에서 뽑는다(하드코딩 목록 금지 — 드리프트 방지).

모든 테스트 tmp_path 격리 — 실 호스트 무접촉.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def _plan(tmp_path, **over):
    kw = dict(
        team_root=tmp_path / "team",
        agents=["claude", "codex"],
        member_name="alice",
        role="member",
        team_name_default="acme",
        home=tmp_path / "home",
        settings_override=None,
        shell="zsh",
        platform="darwin",
        real_host_install=True,
    )
    kw.update(over)
    return il.plan_install(**kw)


# ── plan_install 구조 계약 ──────────────────────────────────────────

def test_host_paths_from_agent_wire_single_source(tmp_path):
    """host_paths 는 _AGENT_WIRE/agent_*_path 에서 — claude·codex settings·skills 포함."""
    plan = _plan(tmp_path)
    by_agent = {h["agent"]: h for h in plan.host_paths}
    assert set(by_agent) == {"claude", "codex"}
    assert ".claude/settings.json" in by_agent["claude"]["settings"]
    assert ".claude/skills" in by_agent["claude"]["skills"]
    assert ".codex/config.toml" in by_agent["codex"]["settings"]
    assert ".codex/skills" in by_agent["codex"]["skills"]


def test_wire_steps_match_real_order(tmp_path):
    """wire_steps 는 실제 wire_agents 순서 — install-mcp → sync → install-skills."""
    plan = _plan(tmp_path)
    steps = {w["agent"]: w["steps"] for w in plan.wire_steps}
    assert steps["claude"] == ["install-mcp", "sync --on", "install-skills"]


def test_hooks_from_manifest(tmp_path):
    """hooks 는 manifest.json 기준 — 낯선 레포 훅 배선을 승인 판단할 재료."""
    plan = _plan(tmp_path)
    scripts = {h["script"] for h in plan.hooks}
    assert "session-start.py" in scripts and "auto-commit.py" in scripts
    assert all("event" in h for h in plan.hooks)


def test_env_includes_teammode_vars(tmp_path):
    """env 는 ENV_VAR(TEAMMODE_HOME)+TEAMMODE_MEMBER+shell profile 대상."""
    plan = _plan(tmp_path)
    assert plan.env.get("TEAMMODE_HOME")
    assert plan.env.get("TEAMMODE_MEMBER") == "alice"
    assert ".zshrc" in plan.env.get("profile", "")


def test_repo_writes_include_scaffold_targets(tmp_path):
    """repo_writes 는 scaffold 대상 — members.md·team.config.json·upstream remote."""
    plan = _plan(tmp_path)
    joined = " ".join(str(w) for w in plan.repo_writes)
    assert "members.md" in joined and "team.config.json" in joined
    assert "upstream" in joined.lower()


def test_autopush_condition(tmp_path):
    """autopush: --yes 실설치에서만 scaffold 자동 커밋/push 시도(비치명)."""
    plan = _plan(tmp_path, real_host_install=True)
    assert plan.autopush["enabled_on_yes"] is True
    assert "yes" in plan.autopush["condition"].lower()


def test_trust_note_only_when_codex(tmp_path):
    """trust_note: codex 배선 시에만 non-null(Codex TUI Trust 1회 안내)."""
    assert _plan(tmp_path, agents=["claude", "codex"]).trust_note
    assert _plan(tmp_path, agents=["claude"]).trust_note is None


def test_member_blocker_when_unset(tmp_path):
    """member_name 미정 → member_blocker + repo_writes 에 <member> placeholder."""
    plan = _plan(tmp_path, member_name=None)
    assert plan.member_blocker and "member-name" in plan.member_blocker
    joined = " ".join(str(w) for w in plan.repo_writes)
    assert "<member>" in joined


def test_settings_override_marks_host_env_skipped(tmp_path):
    """--settings 격리면 실호스트 env 미주입 표시(real host write 없음)."""
    plan = _plan(tmp_path, settings_override=tmp_path / "iso", real_host_install=False)
    assert plan.env.get("real_host_env_skipped") is True


# ── render_install_plan: 출력·경로 축약 계약 ─────────────────────────

def test_render_abbreviates_home(tmp_path):
    """HOME 하위 경로는 ~ 축약(로그 개인경로 유출 완화), HOME 밖은 원경로."""
    home = tmp_path / "home"
    plan = _plan(tmp_path, home=home)
    lines = il.render_install_plan(plan, home=home)
    text = "\n".join(lines)
    assert "~/.claude/settings.json" in text
    assert str(home) not in text  # 절대 홈경로가 그대로 새지 않는다


def test_render_contains_approval_gate_items(tmp_path):
    """렌더 출력에 동의 게이트 필수 항목 — 호스트 경로·wire·env·autopush·trust."""
    home = tmp_path / "home"
    text = "\n".join(il.render_install_plan(_plan(tmp_path, home=home), home=home))
    for token in ("install-mcp", "TEAMMODE_HOME", "push", "Trust", "settings.json"):
        assert token in text, f"렌더에 {token!r} 없음"


def test_render_member_blocker_visible(tmp_path):
    """멤버명 미정이면 렌더에 blocker 가 눈에 띄게 나온다."""
    home = tmp_path / "home"
    text = "\n".join(il.render_install_plan(
        _plan(tmp_path, member_name=None, home=home), home=home))
    assert "member-name" in text


# ── install.py --dry-run 통합: 계획 출력 + 무접촉 유지 ───────────────

import runpy
import subprocess


def test_dry_run_cli_prints_plan_and_touches_nothing(tmp_path, monkeypatch):
    """--dry-run: 보강된 계획(호스트/wire/env/autopush/trust)이 출력되고
    memory/·config·settings 는 여전히 생성되지 않는다(기존 계약 유지)."""
    team = tmp_path / "team"
    team.mkdir()
    subprocess.run(["git", "init", "-q", str(team)], capture_output=True)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)  # claude 감지
    (home / ".codex").mkdir(parents=True)   # codex 감지

    import io, contextlib
    monkeypatch.setenv("HOME", str(home))
    mod = runpy.run_path(str(REPO / "infra" / "install.py"),
                         run_name="__dry_run_test__")
    argv = ["--root", str(team), "--dry-run", "--member-name", "alice"]
    opts = mod["parse_args"](argv) if "parse_args" in mod else None
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = mod["bootstrap"](il.parse_args(argv), home=home,
                              python_version=(3, 12))
    text = buf_out.getvalue() + buf_err.getvalue()
    assert rc == 0, text
    # 보강된 계획 항목들
    for token in ("[plan/host]", "[plan/wire]", "[plan/hooks]", "[plan/env]",
                  "[plan/autopush]", "install-mcp", "TEAMMODE_HOME"):
        assert token in text, f"dry-run 출력에 {token!r} 없음:\n{text}"
    # ~ 축약(개인 홈경로 비유출) — 호스트 경로 줄에서 절대 home 이 안 새는지
    assert "~/.claude/settings.json" in text
    # 기존 계약: 무접촉
    assert not (team / "memory").exists()
    assert not (team / "team.config.json").exists()
    assert "[dry-run] 변경 없음" in text


# ── codex 적대검수 반영 (P2·P3×3) ───────────────────────────────────

def test_wire_step_names_match_wire_agents_implementation(tmp_path):
    """[P3] 동어반복 차단: WIRE_STEP_NAMES 를 실 wire_agents 호출 순서와 대조.

    spy run_adapter 로 실제 호출 동사 순서를 기록해 상수와 비교 — 구현 순서가
    바뀌면 상수만 남은 계획이 거짓말하는 것을 테스트가 잡는다.
    """
    calls = []

    def spy(agent, verb, flag, path, extra):
        calls.append(verb)
        return 0

    il.wire_agents(["claude"], home=tmp_path / "home",
                   settings_override=tmp_path / "iso",
                   run_adapter=spy, team_root=tmp_path / "team",
                   member_name="alice")
    expected = [s.split()[0] for s in il.WIRE_STEP_NAMES]
    assert calls == expected, (
        f"wire_agents 실호출 {calls} ≠ WIRE_STEP_NAMES {expected} — 계획 드리프트")


def test_wire_failure_skips_downstream_steps(tmp_path):
    """[재검수] install-mcp 실패 조기 continue: sync 뿐 아니라 install-skills 도 생략
    — 계획의 순서 계약이 실패 경로에서도 유지됨을 고정."""
    calls = []

    def failing(agent, verb, flag, path, extra):
        calls.append(verb)
        return 1 if verb == "install-mcp" else 0

    res = il.wire_agents(["claude"], home=tmp_path / "home",
                         settings_override=tmp_path / "iso",
                         run_adapter=failing, team_root=tmp_path / "team",
                         member_name="alice")
    assert calls == ["install-mcp"], (
        f"install-mcp 실패 후 후속 단계가 실행됨: {calls}")
    assert res.ok is False


def test_claude_mcp_display_matches_adapter_default_instance(tmp_path, monkeypatch):
    """[P3 강화] claude 실호스트 MCP 표시가 **어댑터 실인스턴스 기본**과 일치.

    소스 텍스트 앵커는 주석 오염에 취약(codex 재검수) — fake HOME 으로 Adapter 를
    실제 생성해 mcp_config_path 기본값과 plan 표시를 비교한다. 어댑터 기본이
    바뀌면 즉시 깨져 plan 표시를 함께 고치게 강제한다.
    """
    import runpy as _rp
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ad = _rp.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                      run_name="__mcp_default_test__")
    adapter = ad["Adapter"](
        agent_dir=str(REPO / "infra" / "agents" / "claude"),
        manifest_path=str(REPO / "infra" / "hooks" / "manifest.json"),
        settings_path=str(home / ".claude" / "settings.json"),
        team_root=str(tmp_path / "team"),
    )
    plan_display = Path(home) / il._AGENT_WIRE["claude"]["mcp_rel"]
    assert Path(adapter.mcp_config_path) == plan_display, (
        f"어댑터 기본 {adapter.mcp_config_path} ≠ plan 표시 {plan_display} — "
        f"dry-run 이 거짓 경로를 보여주게 됨")


def test_render_abbreviates_team_root_under_home(tmp_path):
    """[P3] team_root 가 HOME 하위(~/teammode/x 일반 설치)여도 절대 홈경로 비유출."""
    home = tmp_path / "home"
    team = home / "teammode" / "myrepo"
    plan = _plan(tmp_path, team_root=team, home=home)
    text = "\n".join(il.render_install_plan(plan, home=home))
    assert str(home) not in text, f"TEAMMODE_HOME 렌더에서 홈경로 유출:\n{text}"
    assert "~/teammode/myrepo" in text


def test_dry_run_without_yes_is_honest_about_skips(tmp_path, monkeypatch):
    """[P2] --yes 없는 dry-run: 같은 인자의 실 실행처럼 'env 미주입·autopush 꺼짐'
    으로 정직하게 렌더(--yes 를 붙였을 때와 계약이 다름을 표시)."""
    import io, contextlib, runpy as _rp
    team = tmp_path / "team"
    team.mkdir()
    subprocess.run(["git", "init", "-q", str(team)], capture_output=True)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    mod = _rp.run_path(str(REPO / "infra" / "install.py"), run_name="__dr2__")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = mod["bootstrap"](
            il.parse_args(["--root", str(team), "--dry-run",
                           "--member-name", "alice"]),
            home=home, python_version=(3, 12))
    assert rc == 0
    assert "미주입" in buf.getvalue(), (
        "--yes 없는 dry-run 이 실호스트 env 주입처럼 렌더됨(정직성 위반)")
