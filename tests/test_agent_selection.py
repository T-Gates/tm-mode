"""에이전트 선택 미전파 버그 수정 테스트.

검증 범위:
- --agent 복수 파싱 (install_lib.parse_args)
- install이 선택 집합을 team.config.json `agents` 필드에 기록
- on이 config.agents 읽어 wire (detect_agents fallback 0)
- config.agents 없는 기존 레포 → on이 detect fallback (회귀 0)
- write_agents_to_config / read_agents_from_config 멱등·비치명
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def _load_engine():
    """infra/teammode.py 를 고유 이름('teammode_engine')으로 파일 경로 직접 로드.

    test_cli_join_wizard.py 가 collection(import) 시점에 sys.modules['teammode'] 를
    pip 런처 패키지(src/teammode) 스텁으로 등록한다 — infra/teammode.py(엔진)와 이름 충돌.
    그냥 `import teammode` 하면 collection 순서에 따라 스텁을 받아 `_adapter_for` 가 없다
    (전체 suite 에서 AttributeError, 격리 실행에선 통과 — 순서 의존 오염).
    별도 이름에 로드하면 sys.modules['teammode'] 와 무관하게 항상 엔진을 얻는다.
    내부 `import install_lib` 는 캐시된 동일 모듈을 재사용하므로 detect_agents 패치도 유효.
    """
    spec = importlib.util.spec_from_file_location(
        "teammode_engine", str(REPO / "infra" / "teammode.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────── parse_args agents ─────────────────────────────

class TestParseArgsAgents:
    """install_lib.parse_args 의 --agent 복수 처리."""

    def test_default_is_empty_list(self):
        """미지정 시 [] (auto)."""
        opts = il.parse_args([])
        assert opts.agents == []

    def test_single_agent(self):
        """--agent claude → ['claude']."""
        opts = il.parse_args(["--agent", "claude"])
        assert opts.agents == ["claude"]

    def test_multi_agent(self):
        """--agent claude --agent codex → ['claude', 'codex']."""
        opts = il.parse_args(["--agent", "claude", "--agent", "codex"])
        assert opts.agents == ["claude", "codex"]

    def test_agent_with_other_flags(self):
        """다른 플래그와 혼합해도 agents 수집 정확."""
        opts = il.parse_args([
            "--root", "/tmp/team",
            "--agent", "claude",
            "--member-name", "eunsu",
            "--agent", "codex",
            "--yes",
        ])
        assert opts.agents == ["claude", "codex"]
        assert opts.root == "/tmp/team"
        assert opts.member_name == "eunsu"
        assert opts.yes is True


# ─────────────────────────── write/read agents config ─────────────────────

class TestWriteReadAgentsConfig:
    """write_agents_to_config / read_agents_from_config 순수 함수."""

    def _minimal_config(self, team_root: Path, team_name: str = "test-team"):
        """테스트용 최소 team.config.json 작성."""
        cfg = {
            "spec_version": "0.2",
            "team": {"name": team_name, "timezone": "Asia/Seoul", "locale": "ko_KR"},
            "services": {},
        }
        (team_root / "team.config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return cfg

    def test_write_records_agents(self, tmp_path):
        """wire 집합을 config에 기록한다."""
        self._minimal_config(tmp_path)
        changed = il.write_agents_to_config(tmp_path, ["claude"])
        assert changed is True
        cfg = json.loads((tmp_path / "team.config.json").read_text(encoding="utf-8"))
        assert cfg["agents"] == ["claude"]

    def test_write_sorts_agents(self, tmp_path):
        """저장 시 정렬(결정적 비교용)."""
        self._minimal_config(tmp_path)
        il.write_agents_to_config(tmp_path, ["codex", "claude"])
        cfg = json.loads((tmp_path / "team.config.json").read_text(encoding="utf-8"))
        assert cfg["agents"] == ["claude", "codex"]

    def test_write_idempotent(self, tmp_path):
        """같은 집합 재기록 시 changed=False(파일 무수정)."""
        self._minimal_config(tmp_path)
        il.write_agents_to_config(tmp_path, ["claude"])
        changed2 = il.write_agents_to_config(tmp_path, ["claude"])
        assert changed2 is False

    def test_write_no_config_returns_false(self, tmp_path):
        """config 부재 시 changed=False(비치명)."""
        changed = il.write_agents_to_config(tmp_path, ["claude"])
        assert changed is False
        assert not (tmp_path / "team.config.json").exists()

    def test_read_returns_list(self, tmp_path):
        """기록된 agents 를 읽는다."""
        self._minimal_config(tmp_path)
        il.write_agents_to_config(tmp_path, ["claude", "codex"])
        result = il.read_agents_from_config(tmp_path)
        assert result == ["claude", "codex"]

    def test_read_no_field_returns_none(self, tmp_path):
        """agents 필드 없으면 None → 호출부 fallback."""
        self._minimal_config(tmp_path)
        result = il.read_agents_from_config(tmp_path)
        assert result is None

    def test_read_no_config_returns_none(self, tmp_path):
        """config 자체가 없으면 None."""
        result = il.read_agents_from_config(tmp_path)
        assert result is None

    def test_read_broken_config_returns_none(self, tmp_path):
        """깨진 JSON이면 None(비치명)."""
        (tmp_path / "team.config.json").write_text("{broken", encoding="utf-8")
        result = il.read_agents_from_config(tmp_path)
        assert result is None

    def test_write_preserves_other_fields(self, tmp_path):
        """agents 기록이 다른 config 키를 덮어쓰지 않는다."""
        cfg = self._minimal_config(tmp_path, team_name="my-team")
        il.write_agents_to_config(tmp_path, ["claude"])
        loaded = json.loads((tmp_path / "team.config.json").read_text(encoding="utf-8"))
        assert loaded["team"]["name"] == "my-team"
        assert loaded["spec_version"] == "0.2"
        assert loaded["agents"] == ["claude"]


# ─────────────────────────── bootstrap agent 필터링 ───────────────────────

class TestBootstrapAgentFiltering:
    """install.bootstrap 이 opts.agents 로 wire 대상을 필터링."""

    def _run_bootstrap(self, tmp_path, agent_argv, detected_agents,
                       wired_agents=None):
        """bootstrap 헬퍼. detected_agents: detect_agents 가 반환할 목록.
        wired_agents: wire_agents 가 wire.wired 로 반환할 집합(None이면 agent_argv 기반).
        run_adapter 는 항상 0 반환(성공).
        """
        # 팀 레포 세팅
        (tmp_path / ".git").mkdir()
        cfg = {
            "spec_version": "0.2",
            "team": {"name": "test-team", "timezone": "Asia/Seoul", "locale": "ko_KR"},
            "services": {},
        }
        (tmp_path / "team.config.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8")
        (tmp_path / "memory").mkdir()

        home = tmp_path / "home"
        home.mkdir()
        # detected_agents 에 따라 홈 디렉토리 생성
        agent_home_dirs = {"claude": ".claude", "codex": ".codex"}
        for ag in detected_agents:
            (home / agent_home_dirs[ag]).mkdir()

        import install as inst
        opts = il.parse_args(["--root", str(tmp_path), "--yes",
                               "--member-name", "eunsu"] + agent_argv)

        warned = []
        messages = []

        def fake_run_adapter(agent, verb, flag, path, extra_args=None):
            return 0

        # wire_agents를 직접 패치해 wired 집합 제어
        actual_wired = wired_agents if wired_agents is not None else \
            ([a for a in opts.agents if a in detected_agents] if opts.agents
             else detected_agents)

        # ⚠️ wire_result.messages 는 out 싱크(messages)와 **반드시 다른 리스트**여야 한다.
        # bootstrap 이 `for m in wire.messages: out(m)` 를 도는데 out=messages.append 라
        # 같은 리스트면 순회 중 append → 무한루프(행). 별도 리스트 + 비-빈 마커로 두어
        # 포워딩(loop 가 실제로 out 에 흘리는지)도 검증 가능하게 한다.
        wire_result = il.WireResult(ok=True, exit_code=0, wired=actual_wired,
                                     messages=["[wire] test forward marker"])

        with patch.object(il, "wire_agents", return_value=wire_result) as mock_wire, \
             patch.object(il, "inject_env", return_value={"injected": False,
                                                            "reason": "test", "profile": None}), \
             patch("install._ensure_upstream", return_value=False), \
             patch("install._make_run_adapter", return_value=fake_run_adapter), \
             patch("install.shutil.which", return_value="/usr/bin/git"), \
             patch("install._git", return_value="origin"):
            rc = inst.bootstrap(
                opts, home=home,
                python_version=(3, 11),
                shell="bash",
                out=messages.append,
                err=warned.append,
            )
        return rc, mock_wire, messages, warned

    def test_single_agent_filter(self, tmp_path):
        """--agent claude 만 → claude 만 wire."""
        rc, mock_wire, messages, warned = self._run_bootstrap(
            tmp_path,
            agent_argv=["--agent", "claude"],
            detected_agents=["claude", "codex"],
        )
        assert rc == 0
        called_agents = mock_wire.call_args[0][0]
        assert called_agents == ["claude"]
        assert not any("codex" in w for w in warned if "warn" in w.lower())
        # wire.messages 포워딩 살아있음 확인(루프 동작 + 무한루프 회귀 가드).
        assert "[wire] test forward marker" in messages

    def test_multi_agent_both(self, tmp_path):
        """--agent claude --agent codex → 둘 다 wire."""
        rc, mock_wire, _, _ = self._run_bootstrap(
            tmp_path,
            agent_argv=["--agent", "claude", "--agent", "codex"],
            detected_agents=["claude", "codex"],
        )
        assert rc == 0
        called_agents = mock_wire.call_args[0][0]
        assert set(called_agents) == {"claude", "codex"}

    def test_auto_wires_all_detected(self, tmp_path):
        """--agent 미지정 → 감지된 전부 wire."""
        rc, mock_wire, _, _ = self._run_bootstrap(
            tmp_path,
            agent_argv=[],
            detected_agents=["claude", "codex"],
        )
        assert rc == 0
        called_agents = mock_wire.call_args[0][0]
        assert set(called_agents) == {"claude", "codex"}

    def test_uninstalled_agent_warns(self, tmp_path):
        """선택했지만 미설치 에이전트 → [warn] + 제외."""
        rc, mock_wire, _, warned = self._run_bootstrap(
            tmp_path,
            agent_argv=["--agent", "codex"],
            detected_agents=["claude"],  # codex 미설치
        )
        assert rc == 0
        called_agents = mock_wire.call_args[0][0]
        assert "codex" not in called_agents
        assert any("codex" in w for w in warned)

    def test_config_agents_written_after_wire(self, tmp_path):
        """wire.wired 집합이 team.config.json agents 에 기록된다."""
        rc, mock_wire, _, _ = self._run_bootstrap(
            tmp_path,
            agent_argv=["--agent", "claude"],
            detected_agents=["claude", "codex"],
            wired_agents=["claude"],
        )
        assert rc == 0
        # bootstrap 이 필터링한 집합으로 wire_agents 를 호출했는지 직접 확인 —
        # config 기록이 wire 결과(wire.wired) 기반임을 보장(write 가 wire 앞·무관이 아님).
        assert mock_wire.call_args[0][0] == ["claude"]
        cfg = json.loads((tmp_path / "team.config.json").read_text(encoding="utf-8"))
        assert cfg.get("agents") == ["claude"]


# ─────────────────────────── cmd_on config.agents 읽기 ───────────────────

class TestCmdOnAgentsFromConfig:
    """teammode.cmd_on 이 config.agents 를 읽어 wire (detect fallback 0)."""

    def _make_team_root(self, tmp_path, agents_in_config=None):
        """팀 레포 세팅(team.config.json + memory/ + .git)."""
        (tmp_path / ".git").mkdir()
        cfg = {
            "spec_version": "0.2",
            "team": {"name": "test-team", "timezone": "Asia/Seoul", "locale": "ko_KR"},
            "services": {},
        }
        if agents_in_config is not None:
            cfg["agents"] = agents_in_config
        (tmp_path / "team.config.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8")
        (tmp_path / "memory").mkdir()

    def test_cmd_on_reads_config_agents(self, tmp_path):
        """config.agents = ['claude'] → detect_agents 호출 없이 claude 만 wire."""
        self._make_team_root(tmp_path, agents_in_config=["claude"])
        settings = tmp_path / "settings.json"
        wired = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()  # sys.modules['teammode'] 스텁 오염 회피(파일 직접 로드)

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            adp.install_skills = MagicMock()
            wired.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(tm, "_migrate_legacy_credentials"), \
             patch.object(tm, "auto_update_on_start"), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.write_text = MagicMock()
            rc = tm.cmd_on(tmp_path, str(settings), install=True)

        assert rc == 0
        assert wired == ["claude"]

    def test_cmd_on_both_agents_from_config(self, tmp_path):
        """config.agents = ['claude', 'codex'] → 둘 다 wire."""
        self._make_team_root(tmp_path, agents_in_config=["claude", "codex"])
        settings = tmp_path / "settings.json"
        wired = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()  # sys.modules['teammode'] 스텁 오염 회피(파일 직접 로드)

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            adp.install_skills = MagicMock()
            wired.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(tm, "_migrate_legacy_credentials"), \
             patch.object(tm, "auto_update_on_start"), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.write_text = MagicMock()
            rc = tm.cmd_on(tmp_path, str(settings), install=True)

        assert rc == 0
        assert set(wired) == {"claude", "codex"}

    def test_cmd_on_propagates_member_to_codex_only(self, tmp_path):
        """cmd_on(member=...) → codex _adapter_for 에 member 전파, claude 에는 미전파(issue #26).

        회귀 벡터: member 가 codex 어댑터에 안 닿으면 sync 가 hook command prefix 를 떨군다.
        claude 는 settings.json env 로 따로 주입하므로 member kwarg 를 받으면 안 된다(TypeError).
        """
        self._make_team_root(tmp_path, agents_in_config=["claude", "codex"])
        settings = tmp_path / "settings.json"
        seen = {}  # agent_name → _adapter_for 에 넘어온 member kwarg

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()  # sys.modules['teammode'] 스텁 오염 회피(파일 직접 로드)

        def fake_adapter_for(agent_name, *args, **kwargs):
            seen[agent_name] = kwargs.get("member")
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            adp.install_skills = MagicMock()
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(tm, "_migrate_legacy_credentials"), \
             patch.object(tm, "auto_update_on_start"), \
             patch.object(tm, "_read_util_skills", return_value=[]), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.write_text = MagicMock()
            rc = tm.cmd_on(tmp_path, str(settings), member="leejhy", install=True)

        assert rc == 0
        assert seen.get("codex") == "leejhy", f"codex 에 member 미전파: {seen}"
        # claude 는 member 미전파(positional 만, member kwarg 없음 → None)
        assert seen.get("claude") is None, f"claude 에 member 가 샘: {seen}"

    def test_cmd_on_no_config_agents_fallback_detect(self, tmp_path):
        """config.agents 없는 기존 레포 → detect_agents fallback (회귀 0)."""
        self._make_team_root(tmp_path, agents_in_config=None)
        settings = tmp_path / "settings.json"

        # 홈에 .claude 만 존재
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        wired = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()  # sys.modules['teammode'] 스텁 오염 회피(파일 직접 로드)
        import install_lib as _il_mod

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            adp.install_skills = MagicMock()
            wired.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(tm, "_migrate_legacy_credentials"), \
             patch.object(tm, "auto_update_on_start"), \
             patch.object(_il_mod, "detect_agents", return_value=["claude"]), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.write_text = MagicMock()
            rc = tm.cmd_on(tmp_path, str(settings), install=True)

        assert rc == 0
        assert wired == ["claude"]


# ─────────────────────────── cmd_off config.agents 읽기 ───────────────────

class TestCmdOffAgentsFromConfig:
    """teammode.cmd_off 도 config.agents 를 읽어 unwire (cmd_on 과 대칭, 버그픽스 경로)."""

    def _make_team_root(self, tmp_path, agents_in_config=None):
        (tmp_path / ".git").mkdir()
        cfg = {
            "spec_version": "0.2",
            "team": {"name": "test-team", "timezone": "Asia/Seoul", "locale": "ko_KR"},
            "services": {},
        }
        if agents_in_config is not None:
            cfg["agents"] = agents_in_config
        (tmp_path / "team.config.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8")
        (tmp_path / "memory").mkdir()

    def test_cmd_off_config_agents_beat_detect(self, tmp_path):
        """config.agents=['claude','codex'] 가 detect(['claude']) 를 이긴다 → 둘 다 unwire."""
        self._make_team_root(tmp_path, agents_in_config=["claude", "codex"])
        settings = tmp_path / "settings.json"
        unwired = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()
        import install_lib as _il_mod  # noqa: E402

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            unwired.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_uninstall_layer"), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(_il_mod, "detect_agents", return_value=["claude"]), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.exists.return_value = True
            rc = tm.cmd_off(tmp_path, str(settings), install=True)

        assert rc == 0
        # config 가 detect 를 이겨야 한다(둘 다). detect 가 이겼다면 claude 만 unwire 됐을 것.
        assert set(unwired) == {"claude", "codex"}

    def test_cmd_off_propagates_member_to_codex_only(self, tmp_path):
        """cmd_off(member=...) → codex _adapter_for 에 member 전파, claude 미전파(issue #26).

        off resync(mode=off)도 codex hook command 를 다시 쓰므로 member 가 닿아야 prefix 보존.
        """
        self._make_team_root(tmp_path, agents_in_config=["claude", "codex"])
        settings = tmp_path / "settings.json"
        seen = {}

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()

        def fake_adapter_for(agent_name, *args, **kwargs):
            seen[agent_name] = kwargs.get("member")
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_uninstall_layer"), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.exists.return_value = True
            rc = tm.cmd_off(tmp_path, str(settings), member="leejhy", install=True)

        assert rc == 0
        assert seen.get("codex") == "leejhy", f"codex 에 member 미전파: {seen}"
        assert seen.get("claude") is None, f"claude 에 member 가 샘: {seen}"

    def test_cmd_off_no_config_fallback_detect(self, tmp_path):
        """config.agents 없는 기존 레포 → detect fallback (회귀 0)."""
        self._make_team_root(tmp_path, agents_in_config=None)
        settings = tmp_path / "settings.json"
        unwired = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()
        import install_lib as _il_mod  # noqa: E402

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp.sync = MagicMock()
            unwired.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(tm, "_uninstall_layer"), \
             patch.object(tm, "_read_team_field", return_value=None), \
             patch.object(_il_mod, "detect_agents", return_value=["claude"]), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.exists.return_value = True
            rc = tm.cmd_off(tmp_path, str(settings), install=True)

        assert rc == 0
        assert unwired == ["claude"]


# ─────────────────────────── cmd_util config.agents 읽기 ──────────────────

class TestCmdUtilAgentsFromConfig:
    """teammode.cmd_util 즉시반영(active+install)도 config.agents 우선 (cmd_on/off 와 일관)."""

    def _make_team_with_util_skill(self, tmp_path, agents_in_config, skill="test-util"):
        (tmp_path / ".git").mkdir()
        cfg = {
            "spec_version": "0.2",
            "team": {"name": "test-team", "timezone": "Asia/Seoul", "locale": "ko_KR"},
            "services": {},
        }
        if agents_in_config is not None:
            cfg["agents"] = agents_in_config
        (tmp_path / "team.config.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8")
        (tmp_path / "memory").mkdir()
        sk = tmp_path / "infra" / "skills" / "util" / skill
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: test util skill.\n---\n", encoding="utf-8")

    def test_cmd_util_add_config_agents_beat_detect(self, tmp_path):
        """active+install 시 util add 가 config.agents(['claude','codex'])로 심링크 — detect 안 짐."""
        self._make_team_with_util_skill(tmp_path, agents_in_config=["claude", "codex"])
        settings = tmp_path / "settings.json"
        linked = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()
        import install_lib as _il_mod  # noqa: E402

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            adp._link_one_skill = MagicMock()
            linked.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(_il_mod, "detect_agents", return_value=["claude"]), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.exists.return_value = True
            rc = tm.cmd_util(tmp_path, "add", "eunsu", "test-util",
                             skills_dir=str(tmp_path / "skills"),
                             settings_path=str(settings), install=True)

        assert rc == 0
        # config 가 detect(['claude'])를 이겨 codex 도 심링크 대상이 됐는지.
        assert set(linked) == {"claude", "codex"}

    def test_cmd_util_remove_config_agents_beat_detect(self, tmp_path):
        """remove 경로도 config.agents 우선 — config(['claude','codex'])로 에이전트 루프."""
        self._make_team_with_util_skill(tmp_path, agents_in_config=["claude", "codex"])
        settings = tmp_path / "settings.json"
        resolved = []

        sys.path.insert(0, str(REPO / "infra"))
        tm = _load_engine()
        import install_lib as _il_mod  # noqa: E402

        def fake_adapter_for(agent_name, *args, **kwargs):
            adp = MagicMock()
            adp.skills_dir = tmp_path / f".{agent_name}" / "skills"
            adp.skills_dir.mkdir(parents=True, exist_ok=True)
            resolved.append(agent_name)
            return adp

        with patch.object(tm, "_adapter_for", side_effect=fake_adapter_for), \
             patch.object(_il_mod, "detect_agents", return_value=["claude"]), \
             patch.object(tm, "_active_marker") as mock_marker:
            mock_marker.return_value = MagicMock()
            mock_marker.return_value.exists.return_value = True
            rc = tm.cmd_util(tmp_path, "remove", "eunsu", "test-util",
                             skills_dir=str(tmp_path / "skills"),
                             settings_path=str(settings), install=True)

        assert rc == 0
        # config 가 detect(['claude'])를 이겨 codex 도 에이전트 루프 대상이 됐는지.
        assert set(resolved) == {"claude", "codex"}
