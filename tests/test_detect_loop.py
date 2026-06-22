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
    """detect_agents 가 ['claude','codex'] 반환 시 양쪽 adapter.sync('on') 호출."""
    root = _scaffold_team_root(tmp_path)
    # 실 ~/.claude/settings.json 과 동일한 경로 사용 → "실설치 모드" 트리거
    # (격리 모드 판정이 False 가 되어 detected agents 전부 배선)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    codex_adapter  = _mock_adapter(tmp_path / "codex-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None):
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
            rc = mod["cmd_on"](root, settings)
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
    """detect_agents 가 ['claude','codex'] 반환 시 양쪽 adapter.sync('off') 호출."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    codex_adapter  = _mock_adapter(tmp_path / "codex-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None):
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
            rc = mod["cmd_off"](root, settings)
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
    """detect_agents(['claude']) 반환 시 기존 단일 adapter 동작과 동일 — 회귀 0."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None):
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
            rc = mod["cmd_on"](root, settings)
        finally:
            _il.detect_agents = orig_detect

    assert rc == 0
    assert "codex" not in call_log, f"['claude'] 만인데 codex factory 호출됨: {call_log}"
    claude_adapter.sync.assert_called_with(mode="on")
    claude_adapter.install_skills.assert_called_with(layer="core")


def test_cmd_off_with_only_claude_regression(tmp_path):
    """detect_agents(['claude']) 반환 시 기존 단일 off 동작과 동일 — 회귀 0."""
    root = _scaffold_team_root(tmp_path)
    real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    settings = real_claude_settings
    (root / "memory").mkdir(exist_ok=True)

    claude_adapter = _mock_adapter(tmp_path / "claude-skills")
    call_log = []

    def fake_adapter_for(agent_name, s=None, sd=None):
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
            rc = mod["cmd_off"](root, settings)
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

    def fake_adapter_for(agent_name, s=None, sd=None):
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
