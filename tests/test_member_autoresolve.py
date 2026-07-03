"""issue #41 R2 — member 자동 해석 체인 (`--member` 플래그를 몰라도 resync 가 수렴).

`tm on/off --install`(member 미지정)이 prefix 를 발명하지 못해 pre-#28 설치자 전원이
수동 플래그를 알아야 했던 경로를 자동화한다. 엔진 공용 헬퍼 _resolve_member_fallback
의 해석 순서:
  1. (어댑터측, 여기서 안 다룸) 기존 config prefix — codex 어댑터 자가치유가 항상 우선
  2. 현재 프로세스 env TEAMMODE_MEMBER
  3. claude settings.json 의 env.TEAMMODE_MEMBER (settings 파일이 실재할 때만 — 격리 안전)
  4. 전부 실패 → None + [warn] 1줄(`--member` 안내)
명시 --member 는 절대 오버라이드(체인 미가동).

호스트 무접촉: 모든 경로 tmp_path. conftest 가 TEAMMODE_MEMBER env 를 기본 제거하므로
env 소스는 monkeypatch.setenv 로만 주입한다.
"""
from __future__ import annotations

import contextlib
import json
import os
import runpy
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

ENGINE_PY = REPO / "infra" / "teammode.py"

_MOD_CACHE: dict | None = None


def _load_engine():
    global _MOD_CACHE
    if _MOD_CACHE is None:
        _MOD_CACHE = runpy.run_path(str(ENGINE_PY), run_name="__member_autoresolve_test__")
    return _MOD_CACHE


def _engine_globals() -> dict:
    mod = _load_engine()
    return mod["_adapter"].__globals__


@contextlib.contextmanager
def _patch_globals(**kwargs):
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


def _scaffold_team_root(tmp_path):
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks",
                "infra/skills/base", "infra/skills/core", "infra/skills/util",
                "memory/team/sessions"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "infra" / "hooks" / "manifest.json").write_text("[]", encoding="utf-8")
    for agent in ("claude", "codex"):
        src_dir = REPO / "infra" / "agents" / agent
        dst_dir = root / "infra" / "agents" / agent
        for fname in ("adapter.py", "events.json", "normalize.py"):
            src = src_dir / fname
            if src.is_file():
                shutil.copy(src, dst_dir / fname)
    return root


def _settings_with_member(tmp_path, name):
    s = tmp_path / "iso-settings.json"
    s.write_text(json.dumps({"env": {"TEAMMODE_MEMBER": name}}), encoding="utf-8")
    return s


# ── 헬퍼 단위: _resolve_member_fallback ─────────────────────────────────

def test_env_var_resolves(tmp_path, monkeypatch):
    """소스 2: 현재 프로세스 env TEAMMODE_MEMBER."""
    monkeypatch.setenv("TEAMMODE_MEMBER", "envguy")
    mod = _load_engine()
    got = mod["_resolve_member_fallback"](str(tmp_path / "absent.json"))
    assert got == "envguy"


def test_settings_json_resolves(tmp_path):
    """소스 3: claude settings.json env.TEAMMODE_MEMBER (env 미설정 시)."""
    s = _settings_with_member(tmp_path, "diskguy")
    mod = _load_engine()
    assert mod["_resolve_member_fallback"](str(s)) == "diskguy"


def test_env_beats_settings(tmp_path, monkeypatch):
    """소스 순서: env(2) 가 settings.json(3) 보다 우선."""
    monkeypatch.setenv("TEAMMODE_MEMBER", "envguy")
    s = _settings_with_member(tmp_path, "diskguy")
    mod = _load_engine()
    assert mod["_resolve_member_fallback"](str(s)) == "envguy"


def test_invalid_env_falls_through_to_settings(tmp_path, monkeypatch):
    """무효 env 값(_validate_author 거부)은 건너뛰고 다음 소스로."""
    monkeypatch.setenv("TEAMMODE_MEMBER", "bad/name")
    s = _settings_with_member(tmp_path, "diskguy")
    mod = _load_engine()
    assert mod["_resolve_member_fallback"](str(s)) == "diskguy"


def test_all_fail_returns_none_and_warns(tmp_path, capsys):
    """전부 실패 → None + [warn] 1줄에 --member 안내(현행 '발명 금지' 유지)."""
    mod = _load_engine()
    got = mod["_resolve_member_fallback"](str(tmp_path / "absent.json"))
    assert got is None
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "--member" in out
    assert out.count("[warn]") == 1


def test_missing_or_broken_settings_are_safe(tmp_path, capsys):
    """settings 부재/깨진 JSON/env 비-dict 전부 비치명 — None 으로 강등."""
    mod = _load_engine()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert mod["_resolve_member_fallback"](str(broken)) is None
    nondict = tmp_path / "nondict.json"
    nondict.write_text(json.dumps({"env": "oops"}), encoding="utf-8")
    assert mod["_resolve_member_fallback"](str(nondict)) is None
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"env": {"TEAMMODE_MEMBER": "../evil"}}),
                       encoding="utf-8")
    assert mod["_resolve_member_fallback"](str(invalid)) is None


# ── 통합: cmd_on 만 codex 어댑터에 폴백을 배선(off 는 preserve-only — codex P3) ──

def _capture_adapters(tmp_path):
    """_adapter_for 대역 — 호출 kwargs 를 기록하고 MagicMock 어댑터를 돌려준다."""
    calls = []

    def fake_adapter_for(agent_name, s=None, sd=None, member=None,
                         member_fallback=None):
        calls.append({"agent": agent_name, "member": member,
                      "member_fallback": member_fallback})
        m = MagicMock()
        m.skills_dir = tmp_path / f"unused-skills-{agent_name}"
        return m

    return calls, fake_adapter_for


def _run_cmd(tmp_path, verb, member=None, settings=None, env_member=None,
             monkeypatch=None):
    root = _scaffold_team_root(tmp_path)
    (root / "memory").mkdir(exist_ok=True)
    if env_member is not None:
        monkeypatch.setenv("TEAMMODE_MEMBER", env_member)
    settings_path = str(settings) if settings else str(tmp_path / "absent.json")
    calls, fake = _capture_adapters(tmp_path)
    import install_lib as _il
    orig_detect = _il.detect_agents
    _il.detect_agents = lambda home: ["claude", "codex"]
    try:
        with _patch_globals(_adapter_for=fake,
                            _adapter=lambda s=None, sd=None: fake("claude", s, sd)):
            mod = _load_engine()
            rc = mod["cmd_" + verb](root, settings_path, member=member, install=True)
    finally:
        _il.detect_agents = orig_detect
    assert rc == 0
    return calls


def _codex_call(calls):
    got = [c for c in calls if c["agent"] == "codex"]
    assert got, f"codex 어댑터 미배선: {calls}"
    return got[0]


def test_cmd_on_passes_env_member_fallback_to_codex(tmp_path, monkeypatch):
    calls = _run_cmd(tmp_path, "on", env_member="envguy", monkeypatch=monkeypatch)
    c = _codex_call(calls)
    assert c["member"] is None            # 발명 금지 — 명시 member 아님
    assert c["member_fallback"] == "envguy"


def test_cmd_off_never_passes_member_fallback(tmp_path, monkeypatch, capsys):
    """codex P3: off 는 preserve-only — 해석 체인 미가동, 폴백으로 prefix 발명 금지.

    env 에 member 가 있어도 off 는 member_fallback 을 어댑터에 넘기지 않는다(기존
    prefix 는 어댑터 자가치유가 보존). 체인이 안 돌므로 [warn] 노이즈도 없다.
    """
    calls = _run_cmd(tmp_path, "off", env_member="envguy", monkeypatch=monkeypatch)
    c = _codex_call(calls)
    assert c["member"] is None
    assert c["member_fallback"] is None
    assert "[warn]" not in capsys.readouterr().out


def test_cmd_off_no_prefix_config_stays_no_prefix(tmp_path, capsys):
    """off --install: member 미지정 + 해석 소스 없음 → 폴백 미전달(발명 금지) + 무경고."""
    calls = _run_cmd(tmp_path, "off", settings=tmp_path / "absent.json")
    c = _codex_call(calls)
    assert c["member"] is None
    assert c["member_fallback"] is None
    assert "[warn]" not in capsys.readouterr().out


def test_cmd_off_explicit_member_still_honored(tmp_path, monkeypatch):
    """off 에서도 명시 --member 는 그대로 전달(절대 오버라이드 — preserve-only 예외)."""
    calls = _run_cmd(tmp_path, "off", member="cli", env_member="envguy",
                     monkeypatch=monkeypatch)
    c = _codex_call(calls)
    assert c["member"] == "cli"
    assert c["member_fallback"] is None


def test_cmd_on_settings_member_fallback_to_codex(tmp_path, monkeypatch):
    s = _settings_with_member(tmp_path, "diskguy")
    calls = _run_cmd(tmp_path, "on", settings=s, monkeypatch=monkeypatch)
    assert _codex_call(calls)["member_fallback"] == "diskguy"


def test_cmd_on_explicit_member_is_absolute_override(tmp_path, monkeypatch):
    """--member 명시 시 체인 미가동 — 어댑터엔 member 로만 전달."""
    calls = _run_cmd(tmp_path, "on", member="cli", env_member="envguy",
                     monkeypatch=monkeypatch)
    c = _codex_call(calls)
    assert c["member"] == "cli"
    assert c["member_fallback"] is None


def test_cmd_on_all_fail_warns_once_with_member_hint(tmp_path, capsys):
    calls = _run_cmd(tmp_path, "on", settings=tmp_path / "absent.json")
    assert _codex_call(calls)["member_fallback"] is None
    out = capsys.readouterr().out
    assert "--member" in out
    assert out.count("[warn]") == 1


def test_cmd_on_claude_only_isolated_mode_no_chain_no_warn(tmp_path, capsys):
    """격리(--settings) 모드는 claude 만 배선 — 체인 미가동, [warn] 노이즈 없음."""
    root = _scaffold_team_root(tmp_path)
    (root / "memory").mkdir(exist_ok=True)
    calls, fake = _capture_adapters(tmp_path)
    with _patch_globals(_adapter_for=fake,
                        _adapter=lambda s=None, sd=None: fake("claude", s, sd)):
        mod = _load_engine()
        rc = mod["cmd_on"](root, str(tmp_path / "iso-settings.json"), install=False)
    assert rc == 0
    assert all(c["agent"] == "claude" for c in calls)
    assert "[warn]" not in capsys.readouterr().out
