"""멀티에이전트 detect loop 테스트 — _adapter_for factory + cmd_on/cmd_off 루프.

검증 목록:
  1. detect_agents(['claude','codex']) mock 시 cmd_on 에서 양쪽 adapter.sync 호출 확인
  2. detect_agents(['claude','codex']) mock 시 cmd_off 에서 양쪽 adapter.sync 호출 확인
  3. ['claude'] 만이면 기존과 완전 동일 (회귀 0) — 기존 on/off 동작 유지
  4. codex adapter 가 자기 경로(config.toml) 받는지 확인 (_adapter_for("codex"))
  5. _adapter 하위호환 래퍼 — _adapter_for("claude", ...) 와 동일 결과
  6. 격리 모드(tmp settings_path) 에서는 claude 만 배선

모든 테스트 tmp_path 격리 — 실 ~/.claude, ~/.codex 무접촉.

주의: teammode.py 는 runpy.run_path 로 로드되며, 반환 dict 와 함수의 __globals__ 가
다른 객체다. 따라서 patch.dict(mod) 는 함수 내부에서 보이지 않는다.
함수의 __globals__ 를 직접 패치해야 한다 (_patch_globals 헬퍼 참조).
"""
import contextlib
import json
import os
import runpy
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

ENGINE_PY = REPO / "infra" / "teammode.py"


# ── 모듈 로드 ────────────────────────────────────────────────────────

_MOD_CACHE: dict | None = None


def _load_engine():
    """teammode.py 를 독립 네임스페이스로 로드 (캐시)."""
    global _MOD_CACHE
    if _MOD_CACHE is None:
        _MOD_CACHE = runpy.run_path(str(ENGINE_PY), run_name="__detect_loop_test__")
    return _MOD_CACHE


def _engine_globals() -> dict:
    """teammode.py 함수들의 실제 __globals__ dict 반환."""
    mod = _load_engine()
    # 어떤 함수든 같은 __globals__ 를 공유한다
    return mod["_adapter"].__globals__


@contextlib.contextmanager
def _patch_globals(**kwargs):
    """teammode.py 함수들이 실제로 보는 globals dict 를 일시 교체."""
    g = _engine_globals()
    old = {}
    for k, v in kwargs.items():
        old[k] = g.get(k)
        g[k] = v
    try:
        yield g
    finally:
        for k, v in old.items():
            if v is None:
                g.pop(k, None)
            else:
                g[k] = v


# ── scaffold: 최소 팀 루트 픽스처 ──────────────────────────────────────

def _scaffold_team_root(tmp_path):
    """cmd_on/cmd_off 가 실행될 수 있는 최소 구조."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks",
                 "infra/skills/base", "infra/skills/core", "infra/skills/util",
                 "memory/team/sessions"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "infra" / "hooks" / "manifest.json").write_text("[]", encoding="utf-8")
    _copy_adapter(root, "claude")
    _copy_adapter(root, "codex")
    return root


def _copy_adapter(root: Path, agent: str):
    src_dir = REPO / "infra" / "agents" / agent
    dst_dir = root / "infra" / "agents" / agent
    dst_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("adapter.py", "events.json", "normalize.py"):
        src = src_dir / fname
        if src.is_file():
            shutil.copy(src, dst_dir / fname)


def _mock_adapter(skills_dir_path):
    """sync/install_skills 를 MagicMock 으로 가진 더미 어댑터."""
    m = MagicMock()
    m.skills_dir = skills_dir_path
    return m


# ─────────────────────────────────────────────────────────────────────
# 1. detect_agents(['claude','codex']) → cmd_on: 양쪽 sync(mode='on') 호출
# ─────────────────────────────────────────────────────────────────────

def test_cmd_on_loops_all_detected_agents(tmp_path):
    """detect_agents 가 ['claude','codex'] 반환 시 install=True 면 양쪽 adapter.sync('on') 호출."""
    root = _scaffold_team_root(tmp_path)
    # install=True(--install 플래그) 가 멀티에이전트 배선의 정책 결정자.
    # 경로 비교는 더 이상 정책 판정에 쓰이지 않는다(지적1 반영).
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    codex_adapter  = _mock_adapter(tmp_path / "codex-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        call_log.append(agent_name)
        if agent_name == "claude":
            return claude_adapter
        elif agent_name == "codex":
            return codex_adapter
        raise ValueError(f"unexpected agent: {agent_name}")

    # install_lib.detect_agents 가 실제로 호출될 때 mock 값 반환
    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "claude" in call_log, f"claude factory 미호출: {call_log}"
    assert "codex" in call_log, f"codex factory 미호출: {call_log}"
    claude_adapter.sync.assert_called_with(mode="on")
    codex_adapter.sync.assert_called_with(mode="on")
    claude_adapter.install_skills.assert_called_with(layer="core")
    codex_adapter.install_skills.assert_called_with(layer="core")


# ─────────────────────────────────────────────────────────────────────
# 2. detect_agents(['claude','codex']) → cmd_off: 양쪽 sync(mode='off') 호출
# ─────────────────────────────────────────────────────────────────────

def test_cmd_off_loops_all_detected_agents(tmp_path):
    """detect_agents 가 ['claude','codex'] 반환 시 install=True 면 양쪽 adapter.sync('off') 호출."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    codex_adapter  = _mock_adapter(tmp_path / "codex-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        call_log.append(agent_name)
        if agent_name == "claude":
            return claude_adapter
        elif agent_name == "codex":
            return codex_adapter
        raise ValueError(f"unexpected agent: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_off"](root, settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "claude" in call_log, f"claude factory 미호출: {call_log}"
    assert "codex" in call_log, f"codex factory 미호출: {call_log}"
    claude_adapter.sync.assert_called_with(mode="off")
    codex_adapter.sync.assert_called_with(mode="off")


# ─────────────────────────────────────────────────────────────────────
# 3. detect_agents(['claude']) → 기존 on/off 동작과 완전 동일 (회귀 0)
# ─────────────────────────────────────────────────────────────────────

def test_cmd_on_with_only_claude_regression(tmp_path):
    """detect_agents(['claude']) + install=True 시 claude만 배선 — 회귀 0."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        call_log.append(agent_name)
        if agent_name == "claude":
            return claude_adapter
        raise ValueError(f"unexpected agent in single-claude mode: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "codex" not in call_log, f"['claude'] 만인데 codex factory 호출됨: {call_log}"
    claude_adapter.sync.assert_called_with(mode="on")
    claude_adapter.install_skills.assert_called_with(layer="core")


def test_cmd_off_with_only_claude_regression(tmp_path):
    """detect_agents(['claude']) + install=True 시 claude만 해제 — 회귀 0."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        call_log.append(agent_name)
        if agent_name == "claude":
            return claude_adapter
        raise ValueError(f"unexpected agent in single-claude mode: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude"]
        try:
            mod = _load_engine()
            rc = mod["cmd_off"](root, settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "codex" not in call_log, f"['claude'] 만인데 codex factory 호출됨: {call_log}"
    claude_adapter.sync.assert_called_with(mode="off")


# ─────────────────────────────────────────────────────────────────────
# 4. _adapter_for("codex") → settings_path 가 config.toml 파생인지 확인
# ─────────────────────────────────────────────────────────────────────

def test_adapter_for_codex_uses_config_toml_path(tmp_path):
    """_adapter_for('codex') 는 ~/.codex/config.toml 경로를 settings_path 로 파생한다."""
    root = _scaffold_team_root(tmp_path)
    mod = _load_engine()
    captured = {}

    original_run_path_global = mod["_adapter_for"].__globals__.get("runpy")

    # runpy.run_path 를 가로채 codex Adapter 생성 인자를 캡처
    mock_codex_adapter_class = MagicMock()
    mock_codex_instance = MagicMock()
    mock_codex_instance.skills_dir = tmp_path / "codex-skills"
    mock_codex_adapter_class.return_value = mock_codex_instance

    import runpy as _runpy
    original_run_path = _runpy.run_path

    def mock_run_path(path, run_name=None):
        if "codex" in str(path):
            captured["kwargs_log"] = []
            real_class = mock_codex_adapter_class

            class CapturingAdapter:
                def __init__(self, *a, **kw):
                    captured["settings_path"] = kw.get("settings_path") or (a[2] if len(a) > 2 else None)
                    captured["agent_dir"] = kw.get("agent_dir") or (a[0] if a else None)

                def sync(self, *a, **kw): pass
                def install_skills(self, *a, **kw): pass
                def uninstall_skills(self, *a, **kw): return []
                @property
                def skills_dir(self): return tmp_path / "codex-skills"

            return {"Adapter": CapturingAdapter}
        return original_run_path(path, run_name=run_name)

    import builtins
    _runpy.run_path = mock_run_path
    try:
        result = mod["_adapter_for"]("codex")
    finally:
        _runpy.run_path = original_run_path

    assert "settings_path" in captured, "codex Adapter() 가 호출되지 않았습니다"
    sp = captured["settings_path"]
    assert sp is not None, f"settings_path 가 None: {captured}"
    assert "config.toml" in str(sp), (
        f"codex adapter settings_path 가 config.toml 이 아님: {sp!r}")


# ─────────────────────────────────────────────────────────────────────
# 5. _adapter 하위호환 래퍼 — _adapter_for("claude", ...) 와 동일
# ─────────────────────────────────────────────────────────────────────

def test_adapter_backward_compat_wraps_adapter_for_claude(tmp_path):
    """_adapter(settings, skills_dir) 는 _adapter_for('claude', settings, skills_dir) 와 동일."""
    call_log = []

    def capturing_adapter_for(agent_name, settings_path=None, skills_dir=None):
        call_log.append((agent_name, settings_path, skills_dir))
        return MagicMock()

    settings = str(tmp_path / "settings.json")
    sd = str(tmp_path / "skills")

    with _patch_globals(_adapter_for=capturing_adapter_for):
        mod = _load_engine()
        mod["_adapter"](settings, sd)

    assert len(call_log) == 1, f"_adapter_for 가 {len(call_log)}번 호출됨"
    agent_name, sp, skd = call_log[0]
    assert agent_name == "claude", f"_adapter 가 claude 대신 {agent_name!r} 를 요청"
    assert sp == settings
    assert skd == sd


# ─────────────────────────────────────────────────────────────────────
# 6. 격리 모드(tmp settings_path) 에서는 codex 가 실 경로를 건드리지 않음
# ─────────────────────────────────────────────────────────────────────

def test_isolated_mode_only_wires_claude(tmp_path):
    """격리 모드(tmp settings_path)에서 detect_agents(['claude','codex'])라도 claude만 배선."""
    root = _scaffold_team_root(tmp_path)
    # tmp 경로 → 격리 모드 트리거 (실 ~/.claude/settings.json 이 아님)
    settings = tmp_path / "isolated" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)

    wired_agents = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        wired_agents.append(agent_name)
        m = MagicMock()
        m.skills_dir = tmp_path / f"{agent_name}-skills"
        return m

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, str(settings))
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "codex" not in wired_agents, (
        f"격리 모드인데 codex 가 배선됨: {wired_agents}")
    assert "claude" in wired_agents, "claude 가 배선되지 않음"


# ─────────────────────────────────────────────────────────────────────
# 지적1: install=False(--settings 격리) → codex 배선 안 됨, install=True → 양쪽
# ─────────────────────────────────────────────────────────────────────

def test_install_false_does_not_wire_codex(tmp_path):
    """install=False(기본값) 이면 detect_agents(['claude','codex'])라도 claude만 배선 — 경로 무관."""
    root = _scaffold_team_root(tmp_path)
    # 실 ~/.claude/settings.json 과 동일한 경로여도 install=False 면 claude만
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)

    wired_agents = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        wired_agents.append(agent_name)
        m = MagicMock()
        m.skills_dir = tmp_path / f"{agent_name}-skills"
        return m

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            # install=False 명시 — 경로가 실경로여도 claude만 배선돼야 함
            rc = mod["cmd_on"](root, real_claude_settings, install=False)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "codex" not in wired_agents, (
        f"install=False인데 codex 가 배선됨: {wired_agents}")
    assert "claude" in wired_agents, "claude 가 배선되지 않음"


def test_install_true_wires_all_agents(tmp_path):
    """install=True(--install) 이면 detect_agents의 모든 에이전트가 배선된다."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)

    wired_agents = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        wired_agents.append(agent_name)
        m = MagicMock()
        m.skills_dir = tmp_path / f"{agent_name}-skills"
        return m

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, real_claude_settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "claude" in wired_agents, f"claude 가 배선되지 않음: {wired_agents}"
    assert "codex" in wired_agents, f"install=True인데 codex 가 배선되지 않음: {wired_agents}"


# ─────────────────────────────────────────────────────────────────────
# 지적2: on --member 시 util 스킬이 양쪽 skills_dir에 link되는지
# ─────────────────────────────────────────────────────────────────────

def test_cmd_on_member_util_applied_to_all_adapters(tmp_path):
    """on --member 시 util 스킬이 감지된 모든 에이전트의 skills_dir 에 link된다."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)

    # util 스킬 소스 준비
    util_skill_dir = root / "infra" / "skills" / "util" / "myutil"
    util_skill_dir.mkdir(parents=True, exist_ok=True)
    (util_skill_dir / "SKILL.md").write_text("---\ndescription: test util\n---\n",
                                              encoding="utf-8")

    # member util-skills.json 준비
    member = "alice"
    sessions_dir = root / "memory" / "team" / "sessions" / member
    sessions_dir.mkdir(parents=True, exist_ok=True)
    util_json = sessions_dir / "util-skills.json"
    util_json.write_text(json.dumps({"installed": ["myutil"]}), encoding="utf-8")

    claude_skills_dir = tmp_path / "claude-skills"
    codex_skills_dir  = tmp_path / "codex-skills"
    claude_skills_dir.mkdir()
    codex_skills_dir.mkdir()

    link_calls: list = []  # (skills_dir, skill_name)

    def make_mock_adapter(agent_name, sd):
        m = MagicMock()
        m.skills_dir = sd

        def _link_one_skill(src, target, layer=None):
            link_calls.append((str(sd), src.name))

        m._link_one_skill = _link_one_skill
        return m

    claude_adapter = make_mock_adapter("claude", claude_skills_dir)
    codex_adapter  = make_mock_adapter("codex",  codex_skills_dir)

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        if agent_name == "claude":
            return claude_adapter
        elif agent_name == "codex":
            return codex_adapter
        raise ValueError(f"unexpected agent: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, real_claude_settings, member=member, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    linked_dirs = {d for d, _ in link_calls}
    assert str(claude_skills_dir) in linked_dirs, (
        f"claude skills_dir 에 util link 없음: {link_calls}")
    assert str(codex_skills_dir) in linked_dirs, (
        f"codex skills_dir 에 util link 없음 (지적2): {link_calls}")


# ─────────────────────────────────────────────────────────────────────
# 지적3: codex.sync throw 시 claude 처리 + 마커 정책 + warn 보고
# ─────────────────────────────────────────────────────────────────────

def test_partial_failure_claude_succeeds_codex_fails(tmp_path, capsys):
    """codex.sync() throw 시 claude 는 성공 처리, 마커 생성, [warn] 출력."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        if agent_name == "claude":
            return claude_adapter
        elif agent_name == "codex":
            m = MagicMock()
            m.skills_dir = tmp_path / "codex-skills"
            m.sync.side_effect = RuntimeError("codex sync boom")
            return m
        raise ValueError(f"unexpected: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, real_claude_settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    # claude 는 성공했으므로 rc=0
    assert rc == 0, f"codex 실패해도 claude 성공이면 rc=0 이어야 함: rc={rc}"
    # 마커 생성 확인 (최소 하나 성공 정책)
    assert (root / ".teammode-active").exists(), "최소 1개 성공 시 마커 생성 정책 위반"
    # [warn] 출력 확인
    captured = capsys.readouterr()
    assert "[warn]" in captured.out, f"[warn] 미출력: stdout={captured.out!r}"
    assert "codex" in captured.out, f"실패 에이전트명(codex) 미출력: {captured.out!r}"


def test_all_agents_fail_no_marker(tmp_path):
    """모든 에이전트 실패 시 마커 미생성, rc=1."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        m = MagicMock()
        m.skills_dir = tmp_path / f"{agent_name}-skills"
        m.sync.side_effect = RuntimeError(f"{agent_name} boom")
        return m

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, real_claude_settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 1, f"전부 실패 시 rc=1 이어야 함: rc={rc}"
    assert not (root / ".teammode-active").exists(), "전부 실패 시 마커 미생성 정책 위반"


def test_cmd_off_partial_failure_warn(tmp_path, capsys):
    """cmd_off 에서 codex 실패해도 claude 해제 + [warn] 출력."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)
    # 마커 사전 생성 (off 가 실행되는 상황)
    (root / ".teammode-active").write_text("", encoding="utf-8")

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        if agent_name == "claude":
            return claude_adapter
        elif agent_name == "codex":
            m = MagicMock()
            m.skills_dir = tmp_path / "codex-skills"
            m.sync.side_effect = RuntimeError("codex off boom")
            return m
        raise ValueError(f"unexpected: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_off"](root, real_claude_settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0, f"claude 해제 성공이면 rc=0: rc={rc}"
    claude_adapter.sync.assert_called_with(mode="off")
    captured = capsys.readouterr()
    assert "[warn]" in captured.out, f"[warn] 미출력: {captured.out!r}"
    assert "codex" in captured.out, f"실패 에이전트명 미출력: {captured.out!r}"


# ─────────────────────────────────────────────────────────────────────
# 지적1 cmd_off: 전부 실패 → marker 유지 + rc=1 + [warn]
# ─────────────────────────────────────────────────────────────────────

def test_cmd_off_all_agents_fail_marker_kept(tmp_path, capsys):
    """cmd_off 에서 모든 에이전트 실패 시 marker 유지 + rc=1 + [warn] 출력."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)
    # 마커 사전 생성 (off 가 실행되는 상황)
    marker = root / ".teammode-active"
    marker.write_text("", encoding="utf-8")

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        m = MagicMock()
        m.skills_dir = tmp_path / f"{agent_name}-skills"
        m.sync.side_effect = RuntimeError(f"{agent_name} off boom")
        return m

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_off"](root, real_claude_settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 1, f"전부 실패 시 rc=1 이어야 함: rc={rc}"
    assert marker.exists(), "전부 실패 시 marker 는 유지돼야 한다"
    captured = capsys.readouterr()
    assert "[warn]" in captured.out, f"[warn] 미출력: {captured.out!r}"


# ─────────────────────────────────────────────────────────────────────
# 지적1 cmd_off: 부분 실패(1성공) → marker 삭제 + rc=0
# ─────────────────────────────────────────────────────────────────────

def test_cmd_off_partial_failure_marker_removed(tmp_path, capsys):
    """cmd_off 에서 claude 성공·codex 실패 시 marker 삭제 + rc=0."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)
    marker = root / ".teammode-active"
    marker.write_text("", encoding="utf-8")

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        if agent_name == "claude":
            return claude_adapter
        m = MagicMock()
        m.skills_dir = tmp_path / f"{agent_name}-skills"
        m.sync.side_effect = RuntimeError(f"{agent_name} off boom")
        return m

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_off"](root, real_claude_settings, install=True)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0, f"claude 해제 성공이면 rc=0: rc={rc}"
    assert not marker.exists(), "1개 이상 성공 시 marker 는 삭제돼야 한다"


# ─────────────────────────────────────────────────────────────────────
# 지적2 util replay: 한쪽 _link_one_skill 실패 → crash 안 함, [warn], marker 유지, 나머지 처리
# ─────────────────────────────────────────────────────────────────────

def test_cmd_on_util_link_partial_failure_no_crash(tmp_path, capsys):
    """on --member 시 util link 한 어댑터 실패 → crash 없음, [warn] 출력, marker 생성, 나머지 처리."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    (root / "memory").mkdir(exist_ok=True)

    # util 스킬 소스 준비
    util_skill_dir = root / "infra" / "skills" / "util" / "myutil"
    util_skill_dir.mkdir(parents=True, exist_ok=True)
    (util_skill_dir / "SKILL.md").write_text("---\ndescription: test util\n---\n",
                                              encoding="utf-8")

    member = "bob"
    sessions_dir = root / "memory" / "team" / "sessions" / member
    sessions_dir.mkdir(parents=True, exist_ok=True)
    util_json = sessions_dir / "util-skills.json"
    util_json.write_text(json.dumps({"installed": ["myutil"]}), encoding="utf-8")

    claude_skills_dir = tmp_path / "claude-skills"
    codex_skills_dir  = tmp_path / "codex-skills"
    claude_skills_dir.mkdir()
    codex_skills_dir.mkdir()

    link_calls: list = []

    def make_mock_adapter(agent_name, sd, fail_link=False):
        m = MagicMock()
        m.skills_dir = sd

        def _link_one_skill(src, target, layer=None):
            if fail_link:
                raise RuntimeError(f"{agent_name} link boom")
            link_calls.append((str(sd), src.name))

        m._link_one_skill = _link_one_skill
        return m

    claude_adapter = make_mock_adapter("claude", claude_skills_dir, fail_link=False)
    # codex _link_one_skill 이 예외를 던짐
    codex_adapter  = make_mock_adapter("codex",  codex_skills_dir, fail_link=True)

    def fake_adapter_for(agent_name, s=None, sd=None, member=None):
        if agent_name == "claude":
            return claude_adapter
        elif agent_name == "codex":
            return codex_adapter
        raise ValueError(f"unexpected: {agent_name}")

    import install_lib as _il
    with _patch_globals(_adapter_for=fake_adapter_for,
                        _adapter=lambda s=None, sd=None: fake_adapter_for("claude", s, sd)):
        orig_detect = _il.detect_agents
        _il.detect_agents = lambda home: ["claude", "codex"]
        try:
            mod = _load_engine()
            rc = mod["cmd_on"](root, real_claude_settings, member=member, install=True)
        finally:
            _il.detect_agents = orig_detect

    # crash 없이 완료
    assert rc == 0, f"util link 실패해도 rc=0 이어야 함: rc={rc}"
    # marker 생성됨 (core sync 성공)
    assert (root / ".teammode-active").exists(), "core sync 성공 시 marker 있어야 함"
    # claude 에는 link 성공
    assert any(str(claude_skills_dir) in d for d, _ in link_calls), (
        f"claude util link 없음: {link_calls}")
    # [warn] 출력
    captured = capsys.readouterr()
    assert "[warn]" in captured.out, f"[warn] 미출력: {captured.out!r}"
