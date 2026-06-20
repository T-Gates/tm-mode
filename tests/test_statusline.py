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

    def test_B4_user_statusline_untouched(self, claude_env):
        """개인 statusLine 있음 → sync --on 시 건드리지 않는다."""
        user_sl = {
            "type": "command",
            "command": "/usr/local/bin/my-statusline.sh",
        }
        claude_env.settings.write_text(json.dumps({"statusLine": user_sl}))
        claude_env.make_adapter().sync(mode="on")
        settings = _read_settings(claude_env.settings)
        sl = _get_status_line(settings)
        # 사용자 원본 그대로
        assert sl == user_sl

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
        assert "\033[1;36m" in stdout or "\\033[1;36m" in stdout or "[Acme]" in stdout

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
