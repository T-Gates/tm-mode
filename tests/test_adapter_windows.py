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
    """공백 든 윈도우 python 경로 → slash 정규화 + 따옴표로 감싸 셸 안전.

    백슬래시는 slash 로 정규화(bash escape 방지)하되, 공백은 여전히 따옴표 필요.
    인용 방식은 single-quote 또는 double-quote 모두 허용(어느 쪽이든 셸 안전).
    """
    ad = _make(env, python=WIN_PY)
    cmd = ad.build_command({"script": "auto-commit.py"})
    slash_path = WIN_PY.replace("\\", "/")
    # slash 정규화된 공백 경로가 어떤 형태로든 인용돼야 함(백슬래시 노출 금지)
    assert slash_path in cmd, f"slash 정규화 경로가 command에 없음: {cmd!r}"
    assert "\\" not in cmd
    # 공백 경로는 따옴표로 감싸져야 함 (single or double quote 모두 허용)
    assert ('"' + slash_path + '"' in cmd or "'" + slash_path + "'" in cmd), \
        f"공백 경로가 따옴표로 감싸지지 않음: {cmd!r}"


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


# ─────────────────── 윈도우 백슬래시 → slash 정규화 (bash escape 버그) ───────────────────
#
# 버그: 윈도우 Claude Code 가 훅 커맨드를 Git Bash(bash) 로 실행하면 백슬래시가
# escape 처리돼 경로가 깨진다(`C:\Users\...\python.exe` → `C:Users...python.exe`
# → command not found). 수정: 커맨드 생성 시 백슬래시를 전부 forward slash 로 정규화.

# 백슬래시 윈도우 경로 — sys.executable·normalize.py 둘 다 모킹
WIN_PY_BS = r"C:\Users\bob\AppData\Local\Programs\Python\Python312\python.exe"


def _bash_escape(s: str) -> str:
    """Git Bash 가 따옴표 없는 토큰의 백슬래시 escape 를 소실시키는 동작 시뮬.

    `\\U` `\\g` `\\A` 등 인식 못하는 escape 시퀀스는 백슬래시가 사라진다.
    """
    return s.replace("\\", "")


def test_build_command_no_backslash_in_windows_path(env, monkeypatch):
    """윈도우 백슬래시 경로(sys.executable·normalize.py 둘 다) → 커맨드에 백슬래시 0개.

    보정 전(RED): str(python)/str(normalize_path) 가 백슬래시 노출.
    보정 후(GREEN): 전부 slash. mutation 의미.
    """
    # normalize.py 경로를 백슬래시로 보이게 → agent_dir 가 백슬래시 윈도우 경로인 척
    ad = _make(env, python=WIN_PY_BS)
    # normalize_path 를 윈도우 백슬래시 경로로 모킹(스크립트 경로도 정규화 대상)
    ad.normalize_path = type(ad.normalize_path)(
        r"C:\Users\bob\team\infra\agents\claude\normalize.py")
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert "\\" not in cmd, f"커맨드에 백슬래시 노출(bash escape 깨짐): {cmd!r}"
    # slash 경로가 들어있어야
    assert "C:/Users/bob/AppData/Local/Programs/Python/Python312/python.exe" in cmd
    assert "C:/Users/bob/team/infra/agents/claude/normalize.py" in cmd


def test_windows_command_survives_bash_escape(env):
    """slash 정규화된 커맨드는 bash escape 를 거쳐도 경로가 안 깨진다(실측 버그 재현)."""
    ad = _make(env, python=WIN_PY_BS)
    cmd = ad.build_command({"script": "auto-commit.py"})
    escaped = _bash_escape(cmd)
    # slash 경로엔 백슬래시가 없으므로 escape 가 아무것도 소실시키지 않음
    assert escaped == cmd
    # 실측 깨짐 문자열이 나오면 안 됨
    assert "C:UsersbobAppData" not in escaped


def test_is_owned_recognizes_slash_normalized_command(env):
    """is_owned 가 slash 경로 커맨드를 소유 인식(빌드 결과와 일관)."""
    ad = _make(env, python=WIN_PY_BS)
    ad.normalize_path = type(ad.normalize_path)(
        r"C:\Users\bob\team\infra\agents\claude\normalize.py")
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert ad.is_owned(cmd) is True


def test_is_owned_migrates_legacy_backslash_command(env):
    """기존 백슬래시로 등록된 훅도 소유 인식(재sync 시 slash 로 갱신되도록)."""
    ad = _make(env, python=WIN_PY_BS)
    ad.normalize_path = type(ad.normalize_path)(
        r"C:\Users\bob\team\infra\agents\claude\normalize.py")
    legacy = (r'C:\Users\bob\AppData\python.exe '
              r'C:\Users\bob\team\infra\agents\claude\normalize.py auto-commit.py')
    assert ad.is_owned(legacy) is True


def test_is_owned_idempotent_slash(env):
    """slash 커맨드 멱등 — 같은 입력 두 번 동일 판정."""
    ad = _make(env, python=WIN_PY_BS)
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert ad.is_owned(cmd) is ad.is_owned(cmd) is True


def test_linux_macos_no_regression(env):
    """Linux/macOS slash 경로는 무회귀 — 백슬래시 0개, 정규화가 no-op."""
    ad = _make(env, python="/usr/bin/python3")
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert "\\" not in cmd
    assert "/usr/bin/python3" in cmd
    assert ad.is_owned(cmd) is True


def test_to_slash_helper():
    """_to_slash: 백슬래시 → slash, slash·단순 토큰 무영향."""
    assert claude_adapter._to_slash(r"C:\a\b") == "C:/a/b"
    assert claude_adapter._to_slash("/usr/bin/python3") == "/usr/bin/python3"
    assert claude_adapter._to_slash("python3") == "python3"


def test_windows_path_with_space_still_quoted(env):
    """슬래시로 바꿔도 공백 경로는 여전히 따옴표 필요(둘 다 적용).
    인용 방식은 single-quote 또는 double-quote 모두 허용.
    """
    slash_path = "C:/Program Files/Python/python.exe"
    ad = _make(env, python=r"C:\Program Files\Python\python.exe")
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert "\\" not in cmd
    # slash + 공백 → 어떤 따옴표로든 보호돼야 함
    assert ('"' + slash_path + '"' in cmd or "'" + slash_path + "'" in cmd), \
        f"공백 경로가 따옴표로 감싸지지 않음: {cmd!r}"


def test_codex_inherits_slash_normalization(env, tmp_path):
    """codex 어댑터도 상속으로 slash 정규화 — 백슬래시 0개."""
    import runpy
    codex_path = REPO / "infra" / "agents" / "codex" / "adapter.py"
    mod = runpy.run_path(str(codex_path), run_name="__codex_slash_test__")
    CodexAdapter = mod["Adapter"]
    ad = CodexAdapter(
        agent_dir=str(REPO / "infra" / "agents" / "codex"),
        manifest_path=str(env.hooks_dir / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python=WIN_PY_BS,
        team_root=str(REPO),
    )
    ad.normalize_path = type(ad.normalize_path)(
        r"C:\Users\bob\team\infra\agents\codex\normalize.py")
    cmd = ad.build_command({"script": "auto-commit.py"})
    assert "\\" not in cmd, f"codex 커맨드 백슬래시 노출: {cmd!r}"
    assert ad.is_owned(cmd) is True


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
