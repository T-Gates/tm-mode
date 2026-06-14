"""W-B — 어댑터 훅 명령 크로스플랫폼 (CHECKLIST 🪟 W-B).

adapter 의 python="python3" 하드코딩 제거 → sys.executable(절대경로) 기본.
build_command·is_owned 이 그 명령으로 일관(소유 판정 무파손). 윈도우 경로/따옴표 안전.

파이=Linux → 윈도우 경로는 임의 python 경로 주입으로 모사(실 윈도우 불필요).
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra" / "agents" / "claude"))

import adapter as claude_adapter  # noqa: E402


def _events_json():
    return {
        "agent": "claude",
        "config_file": "~/.claude/settings.json",
        "events": {"SessionStart": "SessionStart", "PostToolUse": "PostToolUse"},
        "actions": {"file_edit": "Write|Edit"},
        "mcp_tool_format": "mcp__{server}__{tool}",
    }


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "claude"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_events_json()))
    (agent_dir / "normalize.py").write_text("# stub\n")

    class Env:
        pass
    e = Env()
    e.root = root
    e.agent_dir = agent_dir
    e.hooks_dir = hooks_dir
    e.settings = tmp_path / "settings.json"
    return e


def _make(env, python=None):
    return claude_adapter.Adapter(
        agent_dir=str(env.agent_dir),
        manifest_path=str(env.hooks_dir / "manifest.json"),
        settings_path=str(env.settings),
        python=python,
        team_root=str(env.root),
    )


# ─────────────────── default_python: sys.executable 기반 ───────────────────

def test_default_python_is_sys_executable():
    """모듈 헬퍼 default_python() = sys.executable(절대경로, 가장 견고)."""
    assert claude_adapter.default_python() == sys.executable
    assert Path(claude_adapter.default_python()).is_absolute()


def test_adapter_python_none_resolves_to_sys_executable(env):
    """python=None(기본) → sys.executable 로 해석(python3 하드코딩 제거)."""
    ad = _make(env, python=None)
    assert ad.python == sys.executable


def test_build_command_uses_sys_executable_not_python3(env):
    """build_command 가 'python3' 리터럴이 아니라 실 인터프리터 절대경로 사용."""
    ad = _make(env, python=None)
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert sys.executable in cmd
    # 'python3' 리터럴 토큰으로 시작하지 않음(절대경로 사용)
    assert not cmd.startswith("python3 ")
    assert "normalize.py" in cmd
    assert "auto-commit.py" in cmd


def test_argparse_python_default_is_none(env):
    """CLI --python 기본값 None → 설치 시점 sys.executable 로 해석(하드코딩 제거)."""
    import argparse  # 어댑터 main 의 파서를 직접 재현하지 않고, default 노출 검증
    # main 의 파서 default 를 간접 검증: --python 미지정 시 Adapter.python == sys.executable
    # (run_adapter 가 --python 안 넘김 → 기본값이 결정한다)
    saved = sys.argv[:]
    try:
        # uninstall 은 settings 만 건드림 — sync 대신 가벼운 경로로 default 확인 불가하니
        # 파서 default 자체를 점검: main 모듈의 build 함수가 없으므로 source 가드.
        src = (REPO / "infra" / "agents" / "claude" / "adapter.py").read_text()
        assert 'default="python3"' not in src, "argparse --python 기본이 아직 python3 하드코딩"
    finally:
        sys.argv = saved


# ─────────────────── 윈도우 경로/따옴표 안전 ───────────────────

WIN_PY = r"C:\Program Files\Python\python.exe"  # 공백 포함 윈도우 경로


def test_build_command_quotes_windows_python_path(env):
    """공백 든 윈도우 python 경로 → 따옴표로 감싸 셸 안전."""
    ad = _make(env, python=WIN_PY)
    cmd = ad.build_command({"script": "auto-commit.py"})
    # 공백 경로는 따옴표로 보호돼야 함(안 그러면 셸이 토큰 분리)
    assert f'"{WIN_PY}"' in cmd


def test_build_command_no_quotes_for_simple_python(env):
    """공백 없는 단순 명령(python3)은 따옴표 불필요(기존 동작 보존)."""
    ad = _make(env, python="python3")
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert cmd.startswith("python3 ")
    assert '"python3"' not in cmd


# ─────────────────── is_owned 일관성 (소유 판정 무파손) ───────────────────

def test_is_owned_consistent_with_build_command(env):
    """build_command 가 만든 명령을 is_owned 가 우리 것으로 인식(소유 판정 일관)."""
    for py in (None, "python3", WIN_PY):
        ad = _make(env, python=py)
        cmd = ad.build_command({"script": "auto-commit.py"})
        assert ad.is_owned(cmd) is True, f"python={py!r} 명령을 소유 인식 못함"


def test_is_owned_rejects_foreign(env):
    ad = _make(env, python=None)
    assert ad.is_owned("my-own-script.sh") is False
    assert ad.is_owned("") is False


# ─────────────────── codex 어댑터도 동일(상속) ───────────────────

def test_codex_inherits_cross_platform_python(env, tmp_path):
    sys.path.insert(0, str(REPO / "infra" / "agents" / "codex"))
    import runpy
    codex_path = REPO / "infra" / "agents" / "codex" / "adapter.py"
    mod = runpy.run_path(str(codex_path), run_name="__codex_win_test__")
    CodexAdapter = mod["Adapter"]
    # codex events.json 사용
    ad = CodexAdapter(
        agent_dir=str(REPO / "infra" / "agents" / "codex"),
        manifest_path=str(env.hooks_dir / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python=None,
        team_root=str(REPO),
    )
    assert ad.python == sys.executable
    # codex source 도 python3 하드코딩 제거 확인
    src = codex_path.read_text()
    assert 'default="python3"' not in src
