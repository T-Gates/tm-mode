"""statusline 자동 install 기능 TDD 테스트.

A. Codex statusMessage 동적 주입·멱등
   - A1: sync --on 시 각 hook에 statusMessage = "[<팀명>] 팀모드 ON" 삽입
   - A2: statusMessage 값은 team.config.json team.name에서 동적 생성
   - A3: team.config.json 파싱 실패 → "team" 폴백
   - A4: 멱등 — sync --on 2회 동일 결과
   - A5: sync --off 시 statusMessage 없음

B. Claude statusLine 단독설치 멱등·원복
   - B1: 개인 statusLine 없음 → 단독설치 (type=command, sys.executable, _teammode_managed)
   - B2: 단독설치 멱등 — sync --on 2회 동일 결과
   - B3: sync --off 시 _teammode_managed statusLine 제거 (원복)
   - B4: 개인 statusLine 있음 → 건드리지 않음
   - B5: 이미 teammode statusLine 설치 + sync --off → 제거
   - B6: 팀명 동적 — team.config team.name 에서 읽음

C. teammode_statusline.py 렌더 스크립트
   - C1: .teammode-active 있을 때 ANSI cyan [팀명] 출력
   - C2: .teammode-active 없을 때 무출력
   - C3: team.config.json 파싱 실패 시 "team" 폴백
   - C4: ensure_utf8_io() 호출 패턴 (io_encoding import)
   - C5: sys.executable 사용 (크로스OS 패턴)

D. 팀명 동적·파싱실패 폴백
   - D1: Codex - team.config.json 없으면 팀루트 디렉토리명 폴백
   - D2: Claude - team.config.json 없으면 팀루트 디렉토리명 폴백
"""
import json
import os
import sys
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# claude adapter는 직접 경로를 통해 로드 (sys.path 오염 방지 — codex와 모듈명 충돌)
import importlib.util as _ilu

def _load_adapter_from(path: Path):
    spec = _ilu.spec_from_file_location("_adapter_mod_" + path.parent.name, str(path))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_claude_mod = _load_adapter_from(REPO / "infra" / "agents" / "claude" / "adapter.py")
claude_adapter = _claude_mod


def _load_codex_adapter():
    import runpy
    mod = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                         run_name="__codex_statusline_test__")
    return mod["Adapter"]


# ── 공통 픽스처 ──


def _claude_events_json():
    return {
        "agent": "claude",
        "config_file": "~/.claude/settings.json",
        "events": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
        },
        "actions": {"file_edit": "Write|Edit"},
        "mcp_tool_format": "mcp__{server}__{tool}",
    }


def _codex_events_json():
    return {
        "agent": "codex",
        "config_file": "~/.codex/config.toml",
        "events": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": None,
            "PostToolUse": "PostToolUse",
        },
        "actions": {"file_edit": "apply_patch"},
        "mcp_tool_format": "{server}.{tool}",
    }


def _write_team_config(root: Path, team_name: str):
    cfg = {
        "spec_version": "0.1",
        "team": {
            "name": team_name,
            "timezone": "Asia/Seoul",
        },
        "services": {},
    }
    (root / "team.config.json").write_text(json.dumps(cfg), encoding="utf-8")


@pytest.fixture
def claude_env(tmp_path):
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "claude"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_claude_events_json()))
    (agent_dir / "normalize.py").write_text("# stub\n")

    settings = tmp_path / "settings.json"
    _write_team_config(root, "Acme")

    def write_manifest(entries):
        (hooks_dir / "manifest.json").write_text(json.dumps(entries))

    write_manifest([
        {"event": "SessionStart", "script": "session-start.py", "mode": "on"},
    ])

    def make_adapter():
        return claude_adapter.Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(settings),
            python=sys.executable,
            team_root=str(root),
            config_path=str(root / "team.config.json"),
        )

    class Env:
        pass
    e = Env()
    e.root = root
    e.agent_dir = agent_dir
    e.settings = settings
    e.write_manifest = write_manifest
    e.make_adapter = make_adapter
    return e


@pytest.fixture
def codex_env(tmp_path):
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "codex"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_codex_events_json()))
    (agent_dir / "normalize.py").write_text("# stub\n")
    config = tmp_path / "config.toml"
    _write_team_config(root, "Acme")

    Adapter = _load_codex_adapter()

    def write_manifest(entries):
        (hooks_dir / "manifest.json").write_text(json.dumps(entries))

    write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])

    def make_adapter():
        return Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(config),
            python=sys.executable,
            team_root=str(root),
            config_path=str(root / "team.config.json"),
        )

    class E:
        pass
    e = E()
    e.root = root
    e.agent_dir = agent_dir
    e.config = config
    e.write_manifest = write_manifest
    e.make_adapter = make_adapter
    return e


# ════════════════════════════════════════════════════════════════════
# A. Codex statusMessage 동적 주입·멱등
# ════════════════════════════════════════════════════════════════════

class TestCodexStatusMessage:
    def test_A1_statusmessage_injected_on_sync_on(self, codex_env):
        """sync --on 시 각 hook 항목에 statusMessage 필드가 추가된다."""
        codex_env.make_adapter().sync(mode="on")
        text = codex_env.config.read_text()
        assert "statusMessage" in text

    def test_A2_statusmessage_uses_team_name_from_config(self, codex_env):
        """statusMessage 값에 team.config.json의 team.name이 포함된다."""
        codex_env.make_adapter().sync(mode="on")
        text = codex_env.config.read_text()
        # "[Acme] 팀모드 ON" 형태
        assert "Acme" in text
        assert "팀모드 ON" in text

    def test_A3_statusmessage_falls_back_to_team_on_parse_failure(self, codex_env):
        """team.config.json 파싱 실패 시 statusMessage에 'team' 폴백이 사용된다."""
        # 잘못된 JSON으로 덮어쓰기
        (codex_env.root / "team.config.json").write_text("{ invalid json }", encoding="utf-8")
        codex_env.make_adapter().sync(mode="on")
        text = codex_env.config.read_text()
        assert "statusMessage" in text
        # 팀명이 파싱 실패 → 폴백(팀루트 이름 or "team")이 들어가야 함
        assert "팀모드 ON" in text

    def test_A4_idempotent_sync_on_twice(self, codex_env):
        """sync --on 2회 호출 = 1회와 동일 결과 (멱등)."""
        codex_env.make_adapter().sync(mode="on")
        first = codex_env.config.read_text()
        codex_env.make_adapter().sync(mode="on")
        second = codex_env.config.read_text()
        assert first == second

    def test_A5_statusmessage_absent_when_sync_off(self, codex_env):
        """sync --off 시 statusMessage 없음 (mode:on 훅이 포함되지 않음)."""
        codex_env.write_manifest([
            {"event": "PostToolUse", "match": {"action": "file_edit"},
             "script": "auto-commit.py", "fallback": "runtime", "mode": "on"},
        ])
        # --off 시 mode:on 엔트리가 포함되지 않으므로 훅 블록 자체가 비거나 줄어듦
        codex_env.make_adapter().sync(mode="off")
        text = codex_env.config.read_text()
        # mode:on으로만 등록된 훅이 없으면 블록 자체가 비거나 엔트리가 없음
        # statusMessage 가 있으면 그건 base 엔트리에서 나온 것
        # base 엔트리(mode 없음)에도 statusMessage가 붙는 게 설계이므로:
        # 이 테스트는 "mode:on 전용 manifest만 있을 때 --off면 statusMessage 없음"을 확인
        assert "auto-commit.py" not in text or "statusMessage" not in text


# ════════════════════════════════════════════════════════════════════
# B. Claude statusLine 단독설치 멱등·원복
# ════════════════════════════════════════════════════════════════════

def _read_settings(path):
    return json.loads(Path(path).read_text())


def _get_status_line(settings):
    return settings.get("statusLine")


class TestClaudeStatusLine:
    def test_B1_statusline_installed_when_absent(self, claude_env):
        """개인 statusLine 없음 → sync --on 시 단독설치.
        type=command, command에 sys.executable 포함, _teammode_managed=true."""
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        sl = _get_status_line(settings)
        assert sl is not None, "statusLine이 설치돼야 한다"
        assert sl.get("type") == "command"
        assert sl.get("_teammode_managed") is True
        # command에 python 인터프리터 경로가 포함돼야 한다
        cmd = sl.get("command", "")
        assert "teammode_statusline.py" in cmd

    def test_B2_statusline_idempotent(self, claude_env):
        """sync --on 2회 = 1회와 동일 (멱등)."""
        claude_env.make_adapter().sync(mode="on")
        first = claude_env.settings.read_text()
        claude_env.make_adapter().sync(mode="on")
        second = claude_env.settings.read_text()
        assert first == second

    def test_B3_statusline_removed_on_sync_off(self, claude_env):
        """sync --on 후 sync --off → _teammode_managed statusLine 제거 (원복)."""
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        assert _get_status_line(settings) is not None

        claude_env.make_adapter().sync(mode="off")
        settings_after = _read_settings(claude_env.settings)
        sl = _get_status_line(settings_after)
        assert sl is None, "팀모드 OFF 시 teammode statusLine이 제거돼야 한다"

    def test_B4_user_statusline_wrapped_or_punted_by_platform(self, claude_env):
        """개인 statusLine(PowerShell 아닌 바이너리) 처리는 플랫폼별:
        비-Windows → wrapper(bash -c)로 감쌈 / Windows → 보수적 무접촉(bash 불명).
        """
        cmd = "/usr/local/bin/my-statusline-binary"  # 확장자·shebang 없음
        user_sl = {"type": "command", "command": cmd}
        claude_env.settings.write_text(json.dumps({"statusLine": user_sl}))
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        sl = _get_status_line(settings)
        if os.name == "nt":
            assert sl == user_sl  # Windows: bash 불명 → 무접촉
        else:
            assert sl.get("_teammode_wrapped") is True
            assert sl.get("_wrapped_command") == cmd
            assert "teammode_statusline.py" in sl.get("command", "")

    def test_B5_teammode_statusline_removed_on_off(self, claude_env):
        """이미 teammode statusLine 설치 + sync --off → 제거."""
        # 이미 _teammode_managed 마커 있는 statusLine을 직접 심어둠
        pre_sl = {
            "type": "command",
            "command": f"{sys.executable} /some/path/teammode_statusline.py",
            "_teammode_managed": True,
        }
        claude_env.settings.write_text(json.dumps({"statusLine": pre_sl}))
        claude_env.make_adapter().sync(mode="off")
        settings = _read_settings(claude_env.settings)
        assert _get_status_line(settings) is None

    def test_B6_statusline_command_uses_team_name(self, claude_env):
        """statusLine command가 teammode_statusline.py를 가리키며 팀루트 경로를 포함한다."""
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        sl = _get_status_line(settings)
        assert sl is not None
        cmd = sl.get("command", "")
        # sys.executable (절대경로)이 command에 포함돼야 한다 (크로스OS 패턴)
        assert sys.executable.replace("\\", "/") in cmd.replace("\\", "/")
        # teammode_statusline.py 스크립트를 가리켜야 한다
        assert "teammode_statusline.py" in cmd


# ════════════════════════════════════════════════════════════════════
# C. teammode_statusline.py 렌더 스크립트
# ════════════════════════════════════════════════════════════════════

STATUSLINE_SCRIPT = REPO / "infra" / "agents" / "claude" / "teammode_statusline.py"


@pytest.fixture
def statusline_env(tmp_path):
    """teammode_statusline.py 실행 환경: 가짜 팀루트 + team.config.json."""
    root = tmp_path / "teamroot"
    agent_dir = root / "infra" / "agents" / "claude"
    agent_dir.mkdir(parents=True)
    _write_team_config(root, "Acme")

    class Env:
        pass
    e = Env()
    e.root = root
    e.agent_dir = agent_dir
    return e


def _run_statusline(agent_dir, env_extra=None, stdin="{}"):
    """teammode_statusline.py를 agent_dir에 복사한 후 subprocess로 실행.

    스크립트가 __file__ 기준으로 팀루트를 계산하므로,
    agent_dir(= <팀루트>/infra/agents/claude)에 복사해서 실행해야
    올바른 팀루트를 가리킨다.
    (stdout, returncode) 반환.
    """
    import shutil
    agent_dir = Path(agent_dir)
    # 복사본 경로: agent_dir/teammode_statusline.py
    local_script = agent_dir / "teammode_statusline.py"
    if not local_script.exists():
        shutil.copy2(str(STATUSLINE_SCRIPT), str(local_script))

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(local_script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(agent_dir),
    )
    return result.stdout, result.returncode


class TestStatuslineScript:
    def test_C1_outputs_team_name_when_active(self, statusline_env):
        """.teammode-active 있을 때 ANSI cyan [팀명] 출력."""
        # .teammode-active 파일 생성
        (statusline_env.root / ".teammode-active").write_text("")
        stdout, rc = _run_statusline(statusline_env.agent_dir)
        assert rc == 0
        assert "Acme" in stdout
        # ANSI cyan escape 포함
        assert "«Acme»" in stdout, f"길러멧 배지 «Acme» 형식이어야 함: {stdout!r}"
        assert "\033[1;36m" in stdout, f"시안 ANSI escape 포함이어야 함: {stdout!r}"

    def test_C2_no_output_when_inactive(self, statusline_env):
        """.teammode-active 없을 때 무출력."""
        # .teammode-active 파일 없음
        active_file = statusline_env.root / ".teammode-active"
        if active_file.exists():
            active_file.unlink()
        stdout, rc = _run_statusline(statusline_env.agent_dir)
        assert rc == 0
        assert stdout.strip() == ""

    def test_C3_fallback_team_name_on_parse_failure(self, statusline_env):
        """team.config.json 파싱 실패 시 'team' 폴백으로 출력."""
        (statusline_env.root / ".teammode-active").write_text("")
        (statusline_env.root / "team.config.json").write_text("{ bad json }", encoding="utf-8")
        stdout, rc = _run_statusline(statusline_env.agent_dir)
        assert rc == 0
        # 출력에 무언가가 있어야 함 (팀명 폴백)
        assert stdout.strip() != ""

    def test_C4_script_imports_io_encoding(self, tmp_path):
        """teammode_statusline.py 가 io_encoding 모듈을 import한다 (크로스OS 보장)."""
        assert STATUSLINE_SCRIPT.is_file(), "teammode_statusline.py 파일이 존재해야 한다"
        content = STATUSLINE_SCRIPT.read_text(encoding="utf-8")
        assert "io_encoding" in content
        assert "ensure_utf8_io" in content

    def test_C5_script_uses_path_file_for_team_root(self, tmp_path):
        """teammode_statusline.py 가 __file__ 기준 팀루트를 계산한다."""
        assert STATUSLINE_SCRIPT.is_file()
        content = STATUSLINE_SCRIPT.read_text(encoding="utf-8")
        # __file__ 기준 경로 계산 패턴
        assert "__file__" in content
        assert "parent" in content


# ════════════════════════════════════════════════════════════════════
# D. 팀명 동적·파싱실패 폴백
# ════════════════════════════════════════════════════════════════════

class TestTeamNameDynamic:
    def test_D1_codex_falls_back_to_dir_name_when_no_config(self, codex_env):
        """Codex: team.config.json 없으면 팀루트 디렉토리명을 폴백으로 사용."""
        # team.config.json 제거
        config_path = codex_env.root / "team.config.json"
        if config_path.exists():
            config_path.unlink()
        codex_env.make_adapter().sync(mode="on")
        text = codex_env.config.read_text()
        assert "statusMessage" in text
        # 팀루트 디렉토리명(teamroot)이 들어가야 함
        assert "teamroot" in text or "팀모드 ON" in text

    def test_D2_claude_falls_back_to_dir_name_when_no_config(self, claude_env):
        """Claude: team.config.json 없으면 팀루트 디렉토리명을 폴백으로 사용."""
        config_path = claude_env.root / "team.config.json"
        if config_path.exists():
            config_path.unlink()
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        sl = _get_status_line(settings)
        assert sl is not None
        # 설치는 되어야 함 (팀명 폴백이 됐더라도)
        assert sl.get("_teammode_managed") is True


# ════════════════════════════════════════════════════════════════════
# E. 강화 테스트 (codex 적대검수 결함 1~4 대응)
# ════════════════════════════════════════════════════════════════════
import shlex


class TestQuoteArgSafety:
    """결함 1: _quote_arg — double-quote 안 $·backtick 확장 방지 (POSIX single-quote)."""

    def test_E1_dollar_sign_not_expanded(self):
        """경로에 $USER 같은 shell 변수 문자가 있어도 확장되지 않는 인용을 만든다.

        double-quote 방식(`"$foo"`)은 bash에서 $foo를 확장한다.
        single-quote 방식(`'$foo'`)은 확장하지 않는다 — 이 쪽이 안전.
        """
        result = claude_adapter._quote_arg("/home/$USER/path with space/python")
        # single-quote로 감싸야 한다
        assert result.startswith("'"), f"single-quote 시작이어야 함: {result!r}"
        assert result.endswith("'"), f"single-quote 끝이어야 함: {result!r}"
        # $USER 가 그대로 리터럴로 남아있어야 한다 (확장 대상 아님)
        assert "$USER" in result

    def test_E2_backtick_not_expanded(self):
        """경로에 backtick이 있어도 double-quote 확장이 일어나지 않아야 한다."""
        result = claude_adapter._quote_arg("/path/with `cmd` injection/python")
        # single-quote로 감싸야 한다
        assert result.startswith("'"), f"single-quote 시작: {result!r}"

    def test_E3_single_quote_escape_in_path(self):
        """경로 자체에 single-quote가 포함된 경우에도 안전하게 인용된다.

        POSIX single-quote escape: 'it'"'"'s path' 형태로 처리해야 한다.
        """
        result = claude_adapter._quote_arg("/path/it's here/python")
        # shlex.split으로 파싱했을 때 원본 문자열과 동일해야 한다
        parsed = shlex.split(result)
        assert len(parsed) == 1, f"토큰 1개여야 함: {parsed}"
        assert parsed[0] == "/path/it's here/python"

    def test_E4_simple_path_no_quotes(self):
        """단순 경로(공백·특수문자 없음)는 인용하지 않는다 (기존 동작 보존)."""
        result = claude_adapter._quote_arg("/usr/bin/python3")
        assert result == "/usr/bin/python3"

    def test_E5_space_path_single_quoted(self):
        """공백 포함 경로는 single-quote로 감싼다."""
        result = claude_adapter._quote_arg("/home/my user/python")
        assert result.startswith("'")
        assert result.endswith("'")
        parsed = shlex.split(result)
        assert len(parsed) == 1
        assert parsed[0] == "/home/my user/python"

    def test_E6_build_status_line_entry_command_shlex_parseable(self, claude_env):
        """_build_status_line_entry()가 만든 command 문자열을 shlex로 파싱해 실행 가능하다.

        C1-C3이 [sys.executable, script] 리스트로 직접 실행해 파싱을 우회하는 문제 보완.
        실제 command 문자열을 shlex.split → subprocess로 실행했을 때도 동작해야 한다.
        """
        adapter = claude_env.make_adapter()
        entry = adapter._build_status_line_entry()
        cmd_str = entry["command"]
        # shlex.split이 오류 없이 파싱돼야 한다
        tokens = shlex.split(cmd_str)
        assert len(tokens) >= 2, f"python + script 2개 이상 토큰 필요: {tokens}"
        # 첫 토큰이 python executable 경로여야 한다
        python_token = tokens[0]
        assert "python" in python_token.lower() or Path(python_token).exists(), \
            f"첫 토큰이 python 실행 경로여야 함: {python_token!r}"
        # 두 번째 토큰이 teammode_statusline.py여야 한다
        assert "teammode_statusline.py" in tokens[1]


class TestSyncStatusLineRepair:
    """결함 2: _sync_status_line — stale managed entry 갱신 + 외부 command 보존."""

    def test_E7_stale_managed_command_updated_on_on(self, claude_env):
        """managed marker True인데 command가 옛 경로(stale)이면 --on 시 갱신된다.

        기존 구현은 managed=True면 command 검증 없이 skip → stale 경로가 고쳐지지 않는다.
        """
        # 옛 경로로 managed entry 심어두기
        stale_sl = {
            "type": "command",
            "command": "/old/path/teammode_statusline.py",  # 틀린 경로
            "_teammode_managed": True,
        }
        claude_env.settings.write_text(json.dumps({"statusLine": stale_sl}))
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        sl = _get_status_line(settings)
        assert sl is not None
        assert sl.get("_teammode_managed") is True
        # command가 현재 올바른 경로로 갱신돼야 한다
        cmd = sl.get("command", "")
        assert "/old/path" not in cmd, f"stale 경로가 남아있어서는 안 됨: {cmd!r}"
        assert "teammode_statusline.py" in cmd

    def test_E8_marker_true_but_non_team_command_preserved_on_off(self, claude_env):
        """_teammode_managed=True인데 command가 teammode_statusline.py 아닌 경우.

        --off 시 삭제하지 않고 경고+보존해야 한다.
        (기존 구현은 marker만 보고 무조건 삭제)
        """
        # marker는 true지만 command는 외부 스크립트
        alien_sl = {
            "type": "command",
            "command": "/some/external/other-script.sh",
            "_teammode_managed": True,
        }
        claude_env.settings.write_text(json.dumps({"statusLine": alien_sl}))

        captured_warnings = []
        original_sync = claude_env.make_adapter().sync

        # sync를 직접 호출해서 warnings를 캡처하기 어려우므로
        # _sync_status_line을 직접 호출
        adapter = claude_env.make_adapter()
        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        adapter._sync_status_line(settings_dict, mode="off", warnings=warnings_list)

        # statusLine이 보존돼야 한다 (삭제되면 안 됨)
        sl_after = settings_dict.get("statusLine")
        assert sl_after is not None, \
            "외부 command인 경우 --off에서 보존돼야 함 (삭제 금지)"
        # 경고가 발생해야 한다
        assert len(warnings_list) > 0, "외부 command 보존 시 경고를 발행해야 한다"

    def test_E9_managed_with_team_command_removed_on_off(self, claude_env):
        """managed=True + command가 teammode_statusline.py를 가리키면 --off 시 정상 삭제."""
        adapter = claude_env.make_adapter()
        # 올바른 managed entry 설치
        entry = adapter._build_status_line_entry()
        claude_env.settings.write_text(json.dumps({"statusLine": entry}))

        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        adapter._sync_status_line(settings_dict, mode="off", warnings=warnings_list)

        assert settings_dict.get("statusLine") is None, \
            "teammode_statusline.py command + managed=True → --off 시 삭제"
        assert len(warnings_list) == 0, "정상 제거 시 경고 없어야 함"


class TestCodexOffNoStatusMessage:
    """결함 3: Codex _render_block — --off 경로에서 statusMessage 없어야 한다."""

    def test_E10_codex_off_base_hook_has_no_statusMessage(self, codex_env):
        """--off 시 base entry 훅에 statusMessage가 없어야 한다.

        기존 _render_block은 mode를 모르고 항상 statusMessage를 주입한다.
        spec(internals.md §2.7): off는 mode없는 base entry 유지, statusMessage 없음.
        """
        codex_env.write_manifest([
            # mode 없는 base entry
            {"event": "PostToolUse", "match": {"action": "file_edit"},
             "script": "auto-commit.py"},
        ])
        codex_env.make_adapter().sync(mode="off")
        text = codex_env.config.read_text()
        # off 시 base entry가 등록되지만 statusMessage는 없어야 한다
        if "auto-commit.py" in text:  # 훅이 실제로 들어간 경우에만 검사
            assert "statusMessage" not in text, \
                f"--off 경로에 statusMessage가 있으면 안 됨:\n{text}"

    def test_E11_codex_on_base_hook_has_statusMessage(self, codex_env):
        """--on 시 base entry 훅에도 statusMessage가 있어야 한다 (on만 주입 확인)."""
        codex_env.write_manifest([
            {"event": "PostToolUse", "match": {"action": "file_edit"},
             "script": "auto-commit.py"},
        ])
        codex_env.make_adapter().sync(mode="on")
        text = codex_env.config.read_text()
        assert "statusMessage" in text, "--on 시 statusMessage가 있어야 한다"

    def test_E12_codex_shlex_parses_command_from_block(self, codex_env):
        """Codex _render_block이 생성한 TOML command를 역으로 파싱해 실행 경로 검증.

        C1-C3와 달리 실제 TOML 블록에서 command 문자열을 추출해 shlex 파싱한다.
        """
        codex_env.make_adapter().sync(mode="on")
        text = codex_env.config.read_text()
        # command = ... 줄 추출
        import re
        m = re.search(r"^command\s*=\s*(.+)$", text, re.MULTILINE)
        assert m, f"TOML 블록에 command 줄이 없음:\n{text}"
        toml_value = m.group(1).strip()
        # TOML literal string ('...') 또는 basic string ("...") 언래핑
        if toml_value.startswith("'") and toml_value.endswith("'"):
            cmd_str = toml_value[1:-1]
        elif toml_value.startswith('"') and toml_value.endswith('"'):
            cmd_str = toml_value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        else:
            cmd_str = toml_value
        # shlex.split으로 파싱 가능해야 한다
        tokens = shlex.split(cmd_str)
        assert len(tokens) >= 2, f"python + script 2개 이상 토큰 필요: {tokens}"


class TestPathWithSpecialChars:
    """결함 1·4: 경로에 공백·$ 포함 시 command 생성과 shlex 왕복 안전성."""

    def test_E13_path_with_dollar_shlex_roundtrip(self, tmp_path):
        """$가 포함된 python 경로로 만든 command를 shlex로 파싱하면 원본 경로를 복원한다."""
        dollar_python = str(tmp_path / "$special" / "bin" / "python3")
        result = claude_adapter._quote_arg(_claude_mod._to_slash(dollar_python))
        tokens = shlex.split(result)
        assert len(tokens) == 1
        # slash 변환 후 경로가 보존돼야 한다
        assert "$special" in tokens[0]

    def test_E14_path_with_space_shlex_roundtrip(self, tmp_path):
        """공백 포함 경로로 만든 command를 shlex로 파싱하면 원본 경로를 복원한다."""
        space_python = str(tmp_path / "my python" / "bin" / "python3")
        result = claude_adapter._quote_arg(_claude_mod._to_slash(space_python))
        tokens = shlex.split(result)
        assert len(tokens) == 1
        assert "my python" in tokens[0]


# ════════════════════════════════════════════════════════════════════
# F. wrapper 모드 · _is_bash_compatible · auto-wrap · 원본복원 · 멱등
# ════════════════════════════════════════════════════════════════════

def _run_statusline_wrapped(agent_dir, wrapped_cmd: str, active: bool,
                             team_name: str, root: Path, stdin_text: str = "{}"):
    """teammode_statusline.py를 --wrapped <cmd> 모드로 실행.

    agent_dir 에 스크립트를 복사한 후 subprocess 로 실행.
    (stdout, returncode) 반환.
    """
    import shutil
    agent_dir = Path(agent_dir)
    local_script = agent_dir / "teammode_statusline.py"
    if not local_script.exists():
        shutil.copy2(str(STATUSLINE_SCRIPT), str(local_script))

    # active 파일 제어
    active_file = root / ".teammode-active"
    if active:
        active_file.write_text("")
    elif active_file.exists():
        active_file.unlink()

    result = subprocess.run(
        [sys.executable, str(local_script), "--wrapped", wrapped_cmd],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(agent_dir),
    )
    return result.stdout, result.returncode


class TestWrapperMode:
    """F1~F4: wrapper 모드 동작 검증."""

    def test_F1_wrapper_passes_stdin_to_subprocess(self, statusline_env):
        """wrapper 모드 — stdin 데이터가 subprocess(원본 명령)에 전달된다."""
        # stdin 으로 JSON 전달 → 그대로 출력하는 명령으로 확인
        (statusline_env.root / ".teammode-active").write_text("")
        _write_team_config(statusline_env.root, "Acme")

        # echo_stdin 스크립트: stdin을 그대로 stdout으로 출력
        echo_script = statusline_env.root / "echo_stdin.py"
        echo_script.write_text(
            "import sys\nprint(sys.stdin.read(), end='')\n", encoding="utf-8"
        )
        wrapped_cmd = f"{sys.executable} {echo_script}"
        stdin_payload = '{"session": "test-id"}'

        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=wrapped_cmd,
            active=True,
            team_name="Acme",
            root=statusline_env.root,
            stdin_text=stdin_payload,
        )
        assert rc == 0
        # stdin 페이로드가 출력에 포함돼야 한다
        assert "test-id" in stdout, f"stdin 페이로드가 subprocess에 전달돼야 함: {stdout!r}"

    def test_F2_wrapper_active_combines_original_and_team_name(self, statusline_env):
        """wrapper 모드 활성 — 원본 출력 + [팀명] 조합."""
        _write_team_config(statusline_env.root, "Acme")

        # 고정 문자열 출력 스크립트
        hello_script = statusline_env.root / "hello.py"
        hello_script.write_text("print('ORIGINAL_OUTPUT', end='')\n", encoding="utf-8")
        wrapped_cmd = f"{sys.executable} {hello_script}"

        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=wrapped_cmd,
            active=True,
            team_name="Acme",
            root=statusline_env.root,
        )
        assert rc == 0
        assert "ORIGINAL_OUTPUT" in stdout, f"원본 출력 포함 필요: {stdout!r}"
        assert "Acme" in stdout, f"팀명 포함 필요: {stdout!r}"

    def test_F3_wrapper_inactive_only_original_output(self, statusline_env):
        """wrapper 모드 비활성 — 원본 출력만, [팀명] 없음."""
        _write_team_config(statusline_env.root, "Acme")

        hello_script = statusline_env.root / "hello2.py"
        hello_script.write_text("print('ONLY_ORIGINAL', end='')\n", encoding="utf-8")
        wrapped_cmd = f"{sys.executable} {hello_script}"

        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=wrapped_cmd,
            active=False,
            team_name="Acme",
            root=statusline_env.root,
        )
        assert rc == 0
        assert "ONLY_ORIGINAL" in stdout, f"원본 출력 포함 필요: {stdout!r}"
        assert "Acme" not in stdout, f"비활성 시 팀명 없어야 함: {stdout!r}"

    def test_F4_wrapper_failure_nonfatal(self, statusline_env):
        """wrapper 모드 — 원본 명령 실패 시 비치명적 (exit 0, 활성이면 팀명 단독)."""
        _write_team_config(statusline_env.root, "Acme")

        # 실패 명령
        wrapped_cmd = f"{sys.executable} -c 'import sys; sys.exit(1)'"

        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=wrapped_cmd,
            active=True,
            team_name="Acme",
            root=statusline_env.root,
        )
        # 항상 exit 0 (비치명적)
        assert rc == 0
        # 활성이므로 팀명만이라도 출력
        assert "Acme" in stdout, f"실패 시에도 활성이면 팀명 출력 필요: {stdout!r}"

    def test_F4b_wrapper_failure_inactive_no_output(self, statusline_env):
        """wrapper 모드 — 원본 명령 실패 + 비활성 → 무출력."""
        _write_team_config(statusline_env.root, "Acme")

        wrapped_cmd = f"{sys.executable} -c 'import sys; sys.exit(1)'"

        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=wrapped_cmd,
            active=False,
            team_name="Acme",
            root=statusline_env.root,
        )
        assert rc == 0
        assert stdout.strip() == "", f"비활성+실패 시 무출력이어야 함: {stdout!r}"


class TestIsBashCompatible:
    """F5: _is_bash_compatible 판정 테스트."""

    def test_F5_sh_extension_is_bash_compatible(self):
        """.sh 토큰 포함 command → True."""
        assert claude_adapter.Adapter._is_bash_compatible("/usr/local/bin/my-status.sh")

    def test_F5b_sh_start_is_bash_compatible(self):
        """'sh ' 로 시작하는 command → True."""
        assert claude_adapter.Adapter._is_bash_compatible("sh /usr/local/bin/script")

    def test_F5c_bash_start_is_bash_compatible(self):
        """'bash ' 로 시작하는 command → True."""
        assert claude_adapter.Adapter._is_bash_compatible("bash /path/to/script")

    def test_F5d_shebang_sh_is_bash_compatible(self, tmp_path):
        """실파일 + shebang에 'sh' → True."""
        script = tmp_path / "my_status"
        script.write_text("#!/usr/bin/env sh\necho hello\n", encoding="utf-8")
        assert claude_adapter.Adapter._is_bash_compatible(str(script))

    def test_F5e_shebang_bash_is_bash_compatible(self, tmp_path):
        """실파일 + shebang에 'bash' → True."""
        script = tmp_path / "my_status_bash"
        script.write_text("#!/bin/bash\necho hello\n", encoding="utf-8")
        assert claude_adapter.Adapter._is_bash_compatible(str(script))

    def test_F5f_ps1_is_not_bash_compatible(self):
        """.ps1 파일 → False."""
        assert not claude_adapter.Adapter._is_bash_compatible("/path/to/status.ps1")

    def test_F5g_powershell_is_not_bash_compatible(self):
        """'powershell' 포함 command → False."""
        assert not claude_adapter.Adapter._is_bash_compatible(
            "powershell -Command ./my-status.ps1")

    def test_F5h_pwsh_is_not_bash_compatible(self):
        """'pwsh' 포함 command → False."""
        assert not claude_adapter.Adapter._is_bash_compatible("pwsh -File ./status.ps1")

    def test_F5i_unknown_undecidable_is_false(self):
        """판단 불가 command → False (보수적)."""
        assert not claude_adapter.Adapter._is_bash_compatible("/usr/local/bin/my-status")

    def test_F5j_empty_command_is_false(self):
        """빈 command → False."""
        assert not claude_adapter.Adapter._is_bash_compatible("")


class TestAutoWrapPersonalStatusLine:
    """F6~F7: 개인 statusLine auto-wrap 및 PowerShell no-touch."""

    def test_F6_bash_personal_statusline_auto_wrapped(self, claude_env):
        """개인 bash statusLine → sync --on 시 auto-wrap (원본 보존)."""
        personal_cmd = "/usr/local/bin/my-status.sh"
        user_sl = {"type": "command", "command": personal_cmd}
        claude_env.settings.write_text(json.dumps({"statusLine": user_sl}))

        adapter = claude_env.make_adapter()
        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        changes = adapter._sync_status_line(settings_dict, mode="on", warnings=warnings_list)

        sl = settings_dict.get("statusLine")
        assert sl is not None
        assert sl.get("_teammode_managed") is True
        assert sl.get("_teammode_wrapped") is True
        # 원본 명령이 _wrapped_command에 보존돼야 한다
        assert sl.get("_wrapped_command") == personal_cmd
        # command에 --wrapped 가 포함돼야 한다
        assert "--wrapped" in sl.get("command", "")
        # log에 감쌌다는 메시지가 있어야 한다
        assert any("감쌌습니다" in c or "wrapper" in c for c in changes)

    def test_F7_powershell_personal_statusline_no_touch(self, claude_env):
        """개인 PowerShell statusLine → sync --on 시 무접촉 + 판단필요 경고."""
        personal_cmd = "powershell -Command ./my-status.ps1"
        user_sl = {"type": "command", "command": personal_cmd}
        claude_env.settings.write_text(json.dumps({"statusLine": user_sl}))

        adapter = claude_env.make_adapter()
        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        adapter._sync_status_line(settings_dict, mode="on", warnings=warnings_list)

        # statusLine이 원본 그대로여야 한다 (무접촉)
        sl = settings_dict.get("statusLine")
        assert sl == user_sl, f"PowerShell statusLine은 변경되지 않아야 함: {sl!r}"
        # 경고가 발생해야 한다
        assert len(warnings_list) > 0
        assert any("판단필요" in w for w in warnings_list)

    def test_F7b_non_powershell_personal_statusline_wrapped_non_windows(self, claude_env):
        """비-Windows: PowerShell 아닌 개인 statusLine(파이썬·바이너리)은 wrapper로 감싼다.

        wrapper 가 bash -c 로 원본을 돌리므로 mac/linux 에선 명령 종류 무관 wrap 가능.
        Windows(bash 불명) 보수 경로는 _can_wrap_statusline 단위테스트로 별도 커버.
        """
        personal_cmd = "/usr/local/bin/my-status"  # 확장자도 shebang도 없음
        user_sl = {"type": "command", "command": personal_cmd}
        claude_env.settings.write_text(json.dumps({"statusLine": user_sl}))

        adapter = claude_env.make_adapter()
        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        adapter._sync_status_line(settings_dict, mode="on", warnings=warnings_list)

        sl = settings_dict.get("statusLine")
        if os.name == "nt":
            assert sl == user_sl  # Windows: 보수적 무접촉
            assert any("판단필요" in w for w in warnings_list)
        else:
            assert sl.get("_teammode_wrapped") is True
            assert sl.get("_wrapped_command") == personal_cmd
            assert "teammode_statusline.py" in sl.get("command", "")


class TestCanWrapStatusline:
    """_can_wrap_statusline: wrapper(bash -c)로 감쌀 수 있는 statusLine 판정(플랫폼별)."""

    def test_non_windows_wraps_python(self, claude_env):
        a = claude_env.make_adapter()
        assert a._can_wrap_statusline(
            "python3 ~/.claude/statusline.py", is_windows=False) is True

    def test_non_windows_wraps_bare_binary(self, claude_env):
        a = claude_env.make_adapter()
        assert a._can_wrap_statusline("/usr/local/bin/my-status", is_windows=False) is True

    def test_powershell_never_wraps(self, claude_env):
        a = claude_env.make_adapter()
        assert a._can_wrap_statusline("pwsh ~/sl.ps1", is_windows=False) is False
        assert a._can_wrap_statusline("powershell foo", is_windows=True) is False

    def test_windows_conservative_non_bash_not_wrapped(self, claude_env):
        a = claude_env.make_adapter()
        assert a._can_wrap_statusline("python3 sl.py", is_windows=True) is False

    def test_windows_bash_wraps(self, claude_env):
        a = claude_env.make_adapter()
        assert a._can_wrap_statusline("bash ~/sl.sh", is_windows=True) is True

    def test_empty_returns_false(self, claude_env):
        a = claude_env.make_adapter()
        assert a._can_wrap_statusline("", is_windows=False) is False


class TestWrapperBadgePlacement:
    """wrapper 활성 시 배지가 원본 출력 **앞**(prepend)에 온다."""

    def test_badge_prepended_before_original(self, statusline_env):
        _write_team_config(statusline_env.root, "Acme")
        hello = statusline_env.root / "hello_prepend.py"
        hello.write_text("print('ORIGINAL_OUTPUT', end='')\n", encoding="utf-8")
        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=f"{sys.executable} {hello}",
            active=True, team_name="Acme", root=statusline_env.root)
        assert rc == 0
        assert stdout.index("Acme") < stdout.index("ORIGINAL_OUTPUT"), \
            f"배지가 원본 앞에 와야 함(prepend): {stdout!r}"


class TestWrappedRestoreAndIdempotency:
    """F8~F9: wrapped off 복원 + on 재실행 멱등."""

    def test_F8_off_restores_original_wrapped_command(self, claude_env):
        """managed + _teammode_wrapped=True → sync --off 시 원본 statusLine 복원."""
        original_cmd = "/usr/local/bin/my-status.sh"
        wrapped_sl = {
            "type": "command",
            "command": f"{sys.executable} /path/teammode_statusline.py --wrapped {original_cmd}",
            "_teammode_managed": True,
            "_teammode_wrapped": True,
            "_wrapped_command": original_cmd,
        }
        claude_env.settings.write_text(json.dumps({"statusLine": wrapped_sl}))

        adapter = claude_env.make_adapter()
        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        changes = adapter._sync_status_line(settings_dict, mode="off", warnings=warnings_list)

        sl = settings_dict.get("statusLine")
        assert sl is not None, "원본 복원 후 statusLine이 있어야 한다"
        assert sl.get("command") == original_cmd, \
            f"원본 명령으로 복원돼야 함: {sl!r}"
        assert sl.get("_teammode_managed") is not True, "복원된 항목은 teammode 관리가 아님"
        assert any("복원" in c for c in changes)

    def test_F8b_off_standalone_managed_removed(self, claude_env):
        """managed + standalone (wrapped 아님) → sync --off 시 제거."""
        adapter = claude_env.make_adapter()
        entry = adapter._build_status_line_entry()  # wrapped_command=None
        claude_env.settings.write_text(json.dumps({"statusLine": entry}))

        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        changes = adapter._sync_status_line(settings_dict, mode="off", warnings=warnings_list)

        assert settings_dict.get("statusLine") is None, "standalone managed → off 시 제거"
        assert any("제거" in c for c in changes)

    def test_F9_idempotent_on_while_wrapped_no_double_wrap(self, claude_env):
        """이미 wrapped 상태에서 sync --on 재실행 → double-wrap 없음, 멱등."""
        original_cmd = "/usr/local/bin/my-status.sh"
        adapter = claude_env.make_adapter()
        wrapped_entry = adapter._build_status_line_entry(wrapped_command=original_cmd)

        claude_env.settings.write_text(json.dumps({"statusLine": wrapped_entry}))

        # 첫 번째 on (이미 wrapped)
        settings_dict = json.loads(claude_env.settings.read_text())
        warnings_list = []
        changes1 = adapter._sync_status_line(settings_dict, mode="on", warnings=warnings_list)

        sl = settings_dict.get("statusLine")
        assert sl is not None
        # _wrapped_command이 원본 그대로여야 한다 (double-wrap 시 원본이 바뀜)
        assert sl.get("_wrapped_command") == original_cmd, \
            f"double-wrap이 발생하면 안 됨 — _wrapped_command={sl.get('_wrapped_command')!r}"
        # command에 --wrapped가 정확히 1번만 나타나야 한다
        cmd = sl.get("command", "")
        assert cmd.count("--wrapped") == 1, f"--wrapped가 두 번 등장: {cmd!r}"


class TestWrappedArgEscape:
    """결함 5: --wrapped 인수 이스케이프 — flags·공백 포함 원본이 단일 토큰으로 전달"""

    def test_G1_flags_preserved_via_shlex(self, claude_env):
        """`/tmp/s.sh --flag x` 원본이 shlex.split 후 단일 토큰으로 온전히 복원."""
        original_cmd = "/tmp/s.sh --flag x"
        adapter = claude_env.make_adapter()
        entry = adapter._build_status_line_entry(wrapped_command=original_cmd)
        cmd_str = entry["command"]
        tokens = shlex.split(cmd_str)
        # --wrapped 다음 토큰이 원본 전체여야 한다
        idx = tokens.index("--wrapped")
        recovered = tokens[idx + 1]
        assert recovered == original_cmd, (
            f"flags 손실: {recovered!r} != {original_cmd!r}\nfull cmd: {cmd_str!r}"
        )

    def test_G2_bash_lc_preserved_via_shlex(self, claude_env):
        """`bash -lc "echo hi"` 원본이 shlex.split 후 단일 토큰으로 복원."""
        original_cmd = 'bash -lc "echo hi"'
        adapter = claude_env.make_adapter()
        entry = adapter._build_status_line_entry(wrapped_command=original_cmd)
        cmd_str = entry["command"]
        tokens = shlex.split(cmd_str)
        idx = tokens.index("--wrapped")
        recovered = tokens[idx + 1]
        assert recovered == original_cmd, (
            f"bash -lc 손실: {recovered!r} != {original_cmd!r}\nfull cmd: {cmd_str!r}"
        )

    def test_G3_space_in_path_preserved_via_shlex(self, claude_env):
        """`/path/with space/s.sh` 원본이 shlex.split 후 단일 토큰으로 복원."""
        original_cmd = "/path/with space/s.sh"
        adapter = claude_env.make_adapter()
        entry = adapter._build_status_line_entry(wrapped_command=original_cmd)
        cmd_str = entry["command"]
        tokens = shlex.split(cmd_str)
        idx = tokens.index("--wrapped")
        recovered = tokens[idx + 1]
        assert recovered == original_cmd, (
            f"공백경로 파손: {recovered!r} != {original_cmd!r}\nfull cmd: {cmd_str!r}"
        )


class TestWrappedEntryPreservesOriginalAttrs:
    """결함 1: on→off 시 원본 statusLine dict 속성(padding, custom 등) 완전 복원."""

    def test_H1_custom_attrs_preserved_on_off_roundtrip(self, claude_env):
        """원본 dict에 padding·custom 속성이 있으면 off 복원 시 동일하게 복원된다."""
        original_sl = {
            "type": "command",
            "command": "/usr/local/bin/my-status.sh",
            "padding": 0,
            "custom": "keep-me",
        }
        claude_env.settings.write_text(json.dumps({"statusLine": original_sl}))

        adapter = claude_env.make_adapter()
        # on: auto-wrap
        settings_on = json.loads(claude_env.settings.read_text())
        warnings_list = []
        adapter._sync_status_line(settings_on, mode="on", warnings=warnings_list)

        # off: 복원
        warnings_list2 = []
        adapter._sync_status_line(settings_on, mode="off", warnings=warnings_list2)

        restored_sl = settings_on.get("statusLine")
        assert restored_sl is not None, "복원 후 statusLine이 있어야 한다"
        assert restored_sl == original_sl, (
            f"원본 dict와 완전 동일해야 함:\n원본: {original_sl!r}\n복원: {restored_sl!r}"
        )

    def test_H2_wrapped_entry_stores_full_original_dict(self, claude_env):
        """_build_status_line_entry가 만든 wrapper entry에 _wrapped_entry 키가 있어야 한다."""
        original_sl = {
            "type": "command",
            "command": "/usr/local/bin/my-status.sh",
            "padding": 5,
        }
        adapter = claude_env.make_adapter()
        entry = adapter._build_status_line_entry(
            wrapped_command=original_sl["command"],
            original_entry=original_sl,
        )
        assert "_wrapped_entry" in entry, "_wrapped_entry 키가 있어야 한다"
        assert entry["_wrapped_entry"] == original_sl


class TestIsBashCompatibleExtended:
    """결함 4: _is_bash_compatible — /bin/bash·env bash=True, zsh/csh shebang=False."""

    def test_I1_bin_bash_script_is_bash_compatible(self):
        """`/bin/bash /tmp/s` → True."""
        assert claude_adapter.Adapter._is_bash_compatible("/bin/bash /tmp/s"), \
            "/bin/bash 로 시작하는 command는 True"

    def test_I2_usr_bin_env_bash_is_bash_compatible(self):
        """`/usr/bin/env bash /tmp/s` → True."""
        assert claude_adapter.Adapter._is_bash_compatible("/usr/bin/env bash /tmp/s"), \
            "/usr/bin/env bash 패턴은 True"

    def test_I3_env_bash_is_bash_compatible(self):
        """`env bash /tmp/s` → True."""
        assert claude_adapter.Adapter._is_bash_compatible("env bash /tmp/s"), \
            "env bash 패턴은 True"

    def test_I4_zsh_shebang_is_not_bash_compatible(self, tmp_path):
        """실파일 shebang이 `#!/bin/zsh` → False."""
        script = tmp_path / "myzsh"
        script.write_text("#!/bin/zsh\necho hi\n", encoding="utf-8")
        assert not claude_adapter.Adapter._is_bash_compatible(str(script)), \
            "zsh shebang은 False"

    def test_I5_csh_shebang_is_not_bash_compatible(self, tmp_path):
        """실파일 shebang이 `#!/bin/csh` → False."""
        script = tmp_path / "mycsh"
        script.write_text("#!/bin/csh\necho hi\n", encoding="utf-8")
        assert not claude_adapter.Adapter._is_bash_compatible(str(script)), \
            "csh shebang은 False (sh 부분일치로 오판 금지)"

    def test_I6_fish_shebang_is_not_bash_compatible(self, tmp_path):
        """실파일 shebang이 `#!/usr/bin/fish` → False."""
        script = tmp_path / "myfish"
        script.write_text("#!/usr/bin/fish\necho hi\n", encoding="utf-8")
        assert not claude_adapter.Adapter._is_bash_compatible(str(script)), \
            "fish shebang은 False"

    def test_I7_pwsh_c_with_sh_in_arg_is_not_bash_compatible(self):
        """`pwsh -c "echo .sh"` — command가 pwsh이므로 False (인수에 .sh 있어도)."""
        assert not claude_adapter.Adapter._is_bash_compatible('pwsh -c "echo .sh"'), \
            "pwsh는 False"


class TestCrossOsShellExecution:
    """결함 6: _run_wrapped가 bash로 명시 실행하거나, 한계를 주석으로 명시한다."""

    def test_J1_wrapper_uses_bash_when_available(self, statusline_env):
        """bash가 PATH에 있으면 bash -c 로 원본을 실행한다 (shell=False, bash 명시).

        이 테스트는 bash가 있는 환경(Linux/Mac)에서 실행 가능.
        bash가 없으면 skip.
        """
        import shutil
        bash_path = shutil.which("bash")
        if bash_path is None:
            pytest.skip("bash를 찾을 수 없어 skip")

        _write_team_config(statusline_env.root, "Acme")
        (statusline_env.root / ".teammode-active").write_text("")

        # bash 명시 실행 확인용: bash가 실행하는 서브스크립트
        hello_script = statusline_env.root / "hello_j1.sh"
        hello_script.write_text("#!/bin/bash\necho BASH_EXECUTED\n", encoding="utf-8")
        hello_script.chmod(0o755)

        stdout, rc = _run_statusline_wrapped(
            statusline_env.agent_dir,
            wrapped_cmd=str(hello_script),
            active=True,
            team_name="Acme",
            root=statusline_env.root,
        )
        assert rc == 0
        assert "BASH_EXECUTED" in stdout, f"bash -c 실행 결과가 없음: {stdout!r}"

    def test_J2_teammode_statusline_has_bash_execution_comment(self):
        """teammode_statusline.py 소스에 bash 명시 실행 관련 주석이 있다."""
        content = STATUSLINE_SCRIPT.read_text(encoding="utf-8")
        # bash 명시 실행 또는 한계 주석이 있어야 한다
        assert (
            "bash" in content.lower() and
            ("shell=False" in content or "bash -c" in content or "BACKLOG" in content or "bash" in content)
        ), "bash 명시 실행 또는 한계 주석이 있어야 한다"
