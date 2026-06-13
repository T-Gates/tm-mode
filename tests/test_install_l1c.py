"""L1-C — wire (훅 sync만) 테스트 (spec/04 §4⑤·§8, M5).

에이전트별 독립 배선·부분실패 exit3·성공분 무롤백·멱등. 스킬 심링크 제외(M2).
호스트 무접촉: run_adapter 주입으로 부작용 추상화 + 격리 settings 경로.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def test_agent_settings_path_isolated(tmp_path):
    """settings_override 시 에이전트별 격리 파일 경로."""
    p = il.agent_settings_path("claude", home=tmp_path,
                               settings_override=tmp_path / "iso")
    assert p == tmp_path / "iso" / "claude" / "settings.json"
    p2 = il.agent_settings_path("codex", home=tmp_path,
                                settings_override=tmp_path / "iso")
    assert p2 == tmp_path / "iso" / "codex" / "config.toml"


def test_agent_settings_path_real_host(tmp_path):
    """override 없으면 실호스트 기본(home 하위)."""
    p = il.agent_settings_path("claude", home=tmp_path)
    assert p == tmp_path / ".claude" / "settings.json"
    p2 = il.agent_settings_path("codex", home=tmp_path)
    assert p2 == tmp_path / ".codex" / "config.toml"


def test_wire_uses_correct_flag_per_agent(tmp_path):
    """claude→--settings, codex→--config 로 어댑터 호출."""
    calls = []

    def run_adapter(agent, flag, path):
        calls.append((agent, flag))
        return 0

    res = il.wire_agents(["claude", "codex"], home=tmp_path,
                         settings_override=tmp_path / "iso",
                         run_adapter=run_adapter)
    assert res.ok and res.exit_code == 0
    assert ("claude", "--settings") in calls
    assert ("codex", "--config") in calls
    assert set(res.wired) == {"claude", "codex"}


def test_wire_independent_failure_exit3(tmp_path):
    """M5: 한 에이전트 실패가 다른 배선을 막지 않는다. 부분실패 → exit 3."""
    def run_adapter(agent, flag, path):
        if agent == "codex":
            raise RuntimeError("codex 어댑터 폭발")
        return 0

    res = il.wire_agents(["claude", "codex"], home=tmp_path,
                         settings_override=tmp_path / "iso",
                         run_adapter=run_adapter)
    assert res.ok is False
    assert res.exit_code == 3
    assert "claude" in res.wired          # 성공분 보존(롤백 안 함)
    assert any(a == "codex" for a, _ in res.failed)


def test_wire_nonzero_rc_is_failure(tmp_path):
    """어댑터 rc!=0 → 해당 에이전트 실패로 집계(exit 3)."""
    def run_adapter(agent, flag, path):
        return 0 if agent == "claude" else 2

    res = il.wire_agents(["claude", "codex"], home=tmp_path,
                         settings_override=tmp_path / "iso",
                         run_adapter=run_adapter)
    assert res.exit_code == 3
    assert "claude" in res.wired
    assert any(a == "codex" for a, _ in res.failed)


def test_wire_empty_agents_ok(tmp_path):
    """감지 에이전트 0 → ok(빈 배선도 정상, 빈 슬롯 1급 시민 정신)."""
    res = il.wire_agents([], home=tmp_path, run_adapter=lambda *a: 0)
    assert res.ok is True
    assert res.exit_code == 0
    assert res.wired == []


def test_wire_unknown_agent_failure(tmp_path):
    """지원하지 않는 에이전트 → 실패 집계(다른 배선은 계속)."""
    res = il.wire_agents(["claude", "weirdagent"], home=tmp_path,
                         settings_override=tmp_path / "iso",
                         run_adapter=lambda *a: 0)
    assert res.exit_code == 3
    assert "claude" in res.wired
    assert any(a == "weirdagent" for a, _ in res.failed)


def test_wire_requires_run_adapter():
    with pytest.raises(ValueError):
        il.wire_agents(["claude"], home=Path("/tmp"))


# ─────────────────────── bootstrap → wire 통합 (격리 settings) ───────────────────────

import json  # noqa: E402
import runpy  # noqa: E402
import subprocess  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__install_l1c_test__")


def _git_init(path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Carol"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "c@c"], cwd=str(path), check=True)


def test_bootstrap_wires_detected_agent_isolated(tmp_path):
    """detect 된 claude 를 격리 settings 에 배선(실 호스트 무접촉)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)  # claude 감지됨
    iso = tmp_path / "iso"
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--settings", str(iso)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    # 격리 경로에 settings 작성(실 ~/.claude 아님)
    written = iso / "claude" / "settings.json"
    assert written.is_file()
    data = json.loads(written.read_text())
    assert "hooks" in data
    # SessionStart 등 manifest 훅 등록 + normalize 경유
    assert "normalize.py" in json.dumps(data)


def test_bootstrap_no_agents_still_ok(tmp_path):
    """에이전트 0 감지 → scaffold·wire 무에이전트로 rc0(빈 슬롯 정상)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()  # .claude/.codex 없음
    iso = tmp_path / "iso"
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--settings", str(iso)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    # 에이전트 배선은 0이지만, verify(⑦)가 격리 settings 에 on 을 적용하므로
    # iso 하위엔 verify-settings.json 만 생기고 에이전트별 디렉토리는 없다.
    assert not (iso / "claude").exists()
    assert not (iso / "codex").exists()
