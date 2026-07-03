"""issue #9 절반 (b) — TEAMMODE_HOME 을 에이전트 설정에 핀(pin).

셸 프로파일 주입(§9)만으로는 훅 env 가 '어떤 셸로 에이전트를 띄웠나'에 종속되고
(zsh/bash/fish 분기), 이미 떠 있는 장수 에이전트 프로세스에는 프로파일 수정이
닿지 않는다(스냅샷 스테일). 그래서 install 이:

  1. Claude: settings.json env 에 TEAMMODE_HOME 을 TEAMMODE_MEMBER 와 동일
     시맨틱(멱등·자가치유)으로 핀 — JSON 이라 셸 쿼팅 문제 없음.
  2. Codex: hook command 의 `env VAR=val` prefix(PR #28 채널)에 TEAMMODE_HOME 을
     shlex.quote 로 안전 쿼팅해 핀 — command 는 셸로 실행되므로(어댑터 문서 근거)
     공백/따옴표/비ASCII 경로도 정확히 전달된다. 값은 어댑터 생성자가 항상 받는
     team_root 에서 파생(멤버와 달리 self-healing 파서 불요 — resync 마다 재파생).
  3. uninstall 은 install 이 박은 것을 제거(대칭): settings.json env 우리 키만
     제거, codex 는 블록 제거로 함께 사라짐.

호스트 철칙: 전부 tmp 격리 — 실 ~/.claude·~/.codex·셸 프로파일 무접촉.
"""
import json
import runpy
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
sys.path.insert(0, str(INFRA))

import install_lib as il  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# 1. Claude 채널 — install_lib.inject_env_settings (TEAMMODE_MEMBER 와 동일 시맨틱)
# ═══════════════════════════════════════════════════════════════════════════

class TestInjectEnvSettings:
    """settings.json env 에 여러 키를 멱등 핀하는 일반화 함수."""

    def test_pins_member_and_home(self, tmp_path):
        settings = tmp_path / "settings.json"
        changed = il.inject_env_settings(settings, {
            "TEAMMODE_MEMBER": "eunsu",
            "TEAMMODE_HOME": str(tmp_path / "teamroot"),
        })
        assert changed is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["TEAMMODE_MEMBER"] == "eunsu"
        assert data["env"]["TEAMMODE_HOME"] == str(tmp_path / "teamroot")

    def test_idempotent_same_values(self, tmp_path):
        settings = tmp_path / "settings.json"
        env_map = {"TEAMMODE_MEMBER": "eunsu", "TEAMMODE_HOME": "/team/root"}
        il.inject_env_settings(settings, env_map)
        before = settings.read_text(encoding="utf-8")
        assert il.inject_env_settings(settings, env_map) is False
        assert settings.read_text(encoding="utf-8") == before

    def test_self_healing_updates_moved_home(self, tmp_path):
        """레포 이동 후 재설치 → 새 경로로 갱신(자가치유, MEMBER 갱신과 동일)."""
        settings = tmp_path / "settings.json"
        il.inject_env_settings(settings, {"TEAMMODE_HOME": "/old/root"})
        assert il.inject_env_settings(
            settings, {"TEAMMODE_HOME": "/new/root"}) is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["TEAMMODE_HOME"] == "/new/root"

    def test_preserves_other_env_and_top_level_keys(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "env": {"OTHER_VAR": "hello"},
            "hooks": {"SessionStart": []},
        }), encoding="utf-8")
        il.inject_env_settings(settings, {"TEAMMODE_HOME": "/team/root"})
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["OTHER_VAR"] == "hello"
        assert data["hooks"]["SessionStart"] == []
        assert data["env"]["TEAMMODE_HOME"] == "/team/root"

    def test_home_with_space_and_nonascii_is_verbatim(self, tmp_path):
        """JSON 채널은 쿼팅 문제 자체가 없다 — 공백/비ASCII 경로 그대로."""
        settings = tmp_path / "settings.json"
        weird = str(tmp_path / "팀 루트 (space)")
        il.inject_env_settings(settings, {"TEAMMODE_HOME": weird})
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["TEAMMODE_HOME"] == weird

    def test_member_wrapper_backcompat(self, tmp_path):
        """inject_member_env_settings 는 기존 시그니처 그대로 동작(하위호환)."""
        settings = tmp_path / "settings.json"
        assert il.inject_member_env_settings(settings, "eunsu") is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["TEAMMODE_MEMBER"] == "eunsu"
        assert il.inject_member_env_settings(settings, "eunsu") is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. Claude 채널 역함수 — install_lib.remove_env_settings (uninstall 대칭)
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveEnvSettings:
    KEYS = ("TEAMMODE_MEMBER", "TEAMMODE_HOME")

    def test_removes_our_keys_preserves_others(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "env": {"TEAMMODE_MEMBER": "eunsu", "TEAMMODE_HOME": "/team/root",
                    "OTHER_VAR": "hello"},
            "hooks": {"SessionStart": []},
        }), encoding="utf-8")
        assert il.remove_env_settings(settings, self.KEYS) is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "TEAMMODE_MEMBER" not in data["env"]
        assert "TEAMMODE_HOME" not in data["env"]
        assert data["env"]["OTHER_VAR"] == "hello"     # 남의 키 보존
        assert data["hooks"]["SessionStart"] == []      # 최상위 키 보존

    def test_drops_env_block_when_emptied(self, tmp_path):
        """우리 키만 있던 env 블록은 흔적 없이 제거(흔적 0 대칭)."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "env": {"TEAMMODE_MEMBER": "eunsu", "TEAMMODE_HOME": "/r"},
            "hooks": {},
        }), encoding="utf-8")
        il.remove_env_settings(settings, self.KEYS)
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "env" not in data
        assert "hooks" in data

    def test_idempotent_and_noop_without_our_keys(self, tmp_path):
        settings = tmp_path / "settings.json"
        body = json.dumps({"env": {"OTHER": "x"}})
        settings.write_text(body, encoding="utf-8")
        assert il.remove_env_settings(settings, self.KEYS) is False
        assert settings.read_text(encoding="utf-8") == body  # 무접촉

    def test_missing_or_broken_file_noop(self, tmp_path):
        missing = tmp_path / "nope" / "settings.json"
        assert il.remove_env_settings(missing, self.KEYS) is False
        assert not missing.exists()
        broken = tmp_path / "settings.json"
        broken.write_text("{not json", encoding="utf-8")
        assert il.remove_env_settings(broken, self.KEYS) is False
        assert broken.read_text(encoding="utf-8") == "{not json"


# ═══════════════════════════════════════════════════════════════════════════
# 3. uninstall 통합 — settings.json env 의 우리 키가 제거된다
# ═══════════════════════════════════════════════════════════════════════════

def _run_install(argv):
    saved = sys.argv[:]
    try:
        mod = runpy.run_path(str(INFRA / "install.py"), run_name="__pin_home_test__")
        return mod["main"](argv)
    finally:
        sys.argv = saved


def test_uninstall_removes_settings_env_keys(tmp_path):
    """--uninstall 이 install 이 박은 TEAMMODE_MEMBER·TEAMMODE_HOME env 를 제거한다."""
    team_root = tmp_path / "teamroot"
    team_root.mkdir()
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "env": {"TEAMMODE_MEMBER": "eunsu",
                "TEAMMODE_HOME": str(team_root),
                "MY_OWN": "keep"},
    }), encoding="utf-8")
    rc = _run_install(["--uninstall", "--root", str(team_root),
                       "--settings", str(settings)])
    assert rc == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    env = data.get("env", {})
    assert "TEAMMODE_MEMBER" not in env
    assert "TEAMMODE_HOME" not in env
    assert env.get("MY_OWN") == "keep"  # 남의 env 키 보존


# ═══════════════════════════════════════════════════════════════════════════
# 4. Codex 채널 — build_command 의 env prefix 에 TEAMMODE_HOME (shlex.quote)
# ═══════════════════════════════════════════════════════════════════════════

def _load_codex_adapter():
    mod = runpy.run_path(str(INFRA / "agents" / "codex" / "adapter.py"),
                         run_name="__pin_home_codex__")
    return mod["Adapter"]


def _codex_events():
    return {
        "agent": "codex",
        "config_file": "~/.codex/config.toml",
        "events": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
        },
        "actions": {"file_edit": "apply_patch"},
        "mcp_tool_format": "mcp__{server}__{tool}",
    }


def _make_env(tmp_path, root_name="teamroot"):
    """test_adapter_codex.py 의 env 픽스처와 동형 — root 이름만 주입 가능(공백/비ASCII)."""
    root = tmp_path / root_name
    agent_dir = root / "infra" / "agents" / "codex"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(json.dumps(_codex_events()),
                                           encoding="utf-8")
    (agent_dir / "normalize.py").write_text("# stub\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    Adapter = _load_codex_adapter()

    def write_manifest(entries):
        (hooks_dir / "manifest.json").write_text(json.dumps(entries),
                                                 encoding="utf-8")

    def make_adapter(member=None):
        return Adapter(
            agent_dir=str(agent_dir),
            manifest_path=str(hooks_dir / "manifest.json"),
            settings_path=str(config),
            python="python3",
            team_root=str(root),
            member=member,
        )

    class E:
        pass
    e = E()
    e.root = root
    e.config = config
    e.write_manifest = write_manifest
    e.make_adapter = make_adapter
    return e


def _remind_entry():
    return {"script": "session-log-remind.py"}


def _env_assignments(cmd: str) -> dict:
    """셸이 보는 그대로(shlex.split) `env` prefix 의 VAR=val 할당을 파싱 — 라운드트립 검증."""
    tokens = shlex.split(cmd)
    assert tokens and tokens[0] == "env", cmd
    out = {}
    for t in tokens[1:]:
        if "=" not in t:
            break
        k, v = t.split("=", 1)
        out[k] = v
    return out


def test_build_command_pins_home_safe_path(tmp_path):
    """평범한 경로 → env prefix 에 TEAMMODE_HOME=<abs team root> 가 박힌다."""
    e = _make_env(tmp_path)
    cmd = e.make_adapter(member=None).build_command(_remind_entry())
    assert cmd.startswith("env "), cmd
    assigns = _env_assignments(cmd)
    assert assigns.get("TEAMMODE_HOME") == str(e.root.resolve())
    assert "normalize.py" in cmd and "session-log-remind.py" in cmd


def test_build_command_member_first_then_home(tmp_path):
    """member 지정 시 기존 형식 유지: env TEAMMODE_MEMBER=<m> 가 앞(파서 하위호환)."""
    e = _make_env(tmp_path)
    cmd = e.make_adapter(member="leejhy").build_command(_remind_entry())
    assert cmd.startswith("env TEAMMODE_MEMBER=leejhy "), cmd
    assigns = _env_assignments(cmd)
    assert assigns.get("TEAMMODE_MEMBER") == "leejhy"
    assert assigns.get("TEAMMODE_HOME") == str(e.root.resolve())


@pytest.mark.parametrize("root_name", [
    "team root",          # 공백
    "팀루트",              # 비ASCII
    "team'root",          # 작은따옴표
    "team root (v2)",     # 공백 + 셸 메타문자
])
def test_build_command_quotes_unsafe_home(tmp_path, root_name):
    """공백/따옴표/비ASCII 경로도 shlex.quote 로 셸에 정확히 라운드트립된다."""
    e = _make_env(tmp_path, root_name=root_name)
    cmd = e.make_adapter(member="leejhy").build_command(_remind_entry())
    assigns = _env_assignments(cmd)
    assert assigns.get("TEAMMODE_HOME") == str(e.root.resolve()), cmd


def test_build_command_home_with_newline_not_pinned(tmp_path):
    """TOML 한 줄 문자열로 표현 불가한 제어문자 경로는 핀 생략(프로파일 폴백, fail-safe)."""
    e = _make_env(tmp_path)
    a = e.make_adapter(member="leejhy")
    a.team_root = Path(str(e.root) + "\nX")  # 병리적 경로 강제 주입
    cmd = a.build_command(_remind_entry())
    assert "TEAMMODE_HOME" not in cmd, cmd
    assert cmd.startswith("env TEAMMODE_MEMBER=leejhy "), cmd  # member 는 유지


def test_sync_pins_home_into_config_toml(tmp_path):
    """sync(mode=on) 후 config.toml 의 hook command 에 TEAMMODE_HOME 이 들어간다."""
    e = _make_env(tmp_path)
    e.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
        {"event": "UserPromptSubmit", "script": "session-log-remind.py",
         "mode": "on"},
    ])
    e.make_adapter(member="leejhy").sync(mode="on")
    text = e.config.read_text(encoding="utf-8")
    assert "TEAMMODE_HOME=" in text, text
    assert "env TEAMMODE_MEMBER=leejhy" in text, text


def test_resync_without_member_keeps_home_and_member(tmp_path):
    """member 없는 resync(tm on/off)에도 home 은 team_root 에서 재파생 — 유실 없음."""
    e = _make_env(tmp_path)
    e.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    e.make_adapter(member="leejhy").sync(mode="on")
    e.make_adapter(member=None).sync(mode="on")   # tm on resync
    t = e.config.read_text(encoding="utf-8")
    assert "TEAMMODE_HOME=" in t, t
    assert "env TEAMMODE_MEMBER=leejhy" in t, t   # 기존 self-healing 도 유지
    e.make_adapter(member=None).sync(mode="off")  # tm off resync
    t = e.config.read_text(encoding="utf-8")
    assert "TEAMMODE_HOME=" in t, t


def test_sync_idempotent_rerun(tmp_path):
    """같은 상태로 재sync → 무변경(멱등)."""
    e = _make_env(tmp_path)
    e.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    e.make_adapter(member="leejhy").sync(mode="on")
    before = e.config.read_text(encoding="utf-8")
    changes = e.make_adapter(member="leejhy").sync(mode="on")
    assert e.config.read_text(encoding="utf-8") == before
    assert not any(c.startswith("[sync]") for c in changes), changes


def test_sync_space_path_toml_roundtrip(tmp_path):
    """공백 경로: TOML 인코딩(_toml_str) → 디코드 → shlex 라운드트립이 원경로 보존."""
    e = _make_env(tmp_path, root_name="team root")
    e.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    e.make_adapter(member="leejhy").sync(mode="on")
    text = e.config.read_text(encoding="utf-8")
    # command = ... 라인 추출 후 TOML 문자열 디코드(_toml_str 의 역)
    line = next(ln for ln in text.splitlines() if ln.startswith("command = "))
    raw = line[len("command = "):]
    if raw.startswith("'"):
        command = raw[1:-1]
    else:
        assert raw.startswith('"') and raw.endswith('"')
        body = raw[1:-1]
        out, i = [], 0
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body):
                out.append(body[i + 1])
                i += 2
            else:
                out.append(body[i])
                i += 1
        command = "".join(out)
    assigns = _env_assignments(command)
    assert assigns.get("TEAMMODE_HOME") == str(e.root.resolve())


def test_uninstall_removes_block_including_home(tmp_path):
    """codex uninstall(블록 제거)이 home 핀도 함께 지운다(대칭 — 블록 소속이므로)."""
    e = _make_env(tmp_path)
    e.write_manifest([
        {"event": "PostToolUse", "match": {"action": "file_edit"},
         "script": "auto-commit.py", "fallback": "runtime"},
    ])
    e.make_adapter(member="leejhy").sync(mode="on")
    assert "TEAMMODE_HOME=" in e.config.read_text(encoding="utf-8")
    e.make_adapter(member=None).uninstall()
    text = e.config.read_text(encoding="utf-8")
    assert "TEAMMODE_HOME" not in text
    assert "TEAMMODE_MEMBER" not in text


def test_is_owned_holds_with_home_prefix(tmp_path):
    """home 핀이 붙어도 is_owned(normalize.py 마커) 판정이 깨지지 않는다."""
    e = _make_env(tmp_path, root_name="team root")
    a = e.make_adapter(member="leejhy")
    cmd = a.build_command(_remind_entry())
    assert a.is_owned(cmd) is True, cmd


def test_member_parser_ignores_member_like_substring_in_quoted_home(tmp_path):
    """quoted HOME 값 안의 member-모양 부분문자열을 member로 오인하지 않는다(codex P2).

    home-only prefix + 병리 경로에서 memberless resync가 'evil'을 발명하면 안 됨.
    """
    e = _make_env(tmp_path)
    evil_home = "/tmp/env TEAMMODE_MEMBER=evil/team"
    e.config.write_text(
        "# teammode-hooks-start\n"
        f"command = 'env TEAMMODE_HOME={shlex.quote(evil_home)} python3 hook.py'\n"
        "# teammode-hooks-end\n", encoding="utf-8")
    ad = e.make_adapter(member=None)
    assert ad._existing_member_prefix() is None


def test_member_parser_reads_real_assignment_next_to_quoted_home(tmp_path):
    e = _make_env(tmp_path, root_name="team root")  # 공백 경로 — quoted HOME 유발
    e.config.write_text(
        "# teammode-hooks-start\n"
        f"command = 'env TEAMMODE_MEMBER=bob TEAMMODE_HOME={shlex.quote(str(e.root))} python3 hook.py'\n"
        "# teammode-hooks-end\n", encoding="utf-8")
    ad = e.make_adapter(member=None)
    assert ad._existing_member_prefix() == "bob"
