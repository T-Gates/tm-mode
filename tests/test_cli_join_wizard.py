"""cli.py join wizard 8단계 — TDD.

테스트 범위:
- 비-TTY 경로: input 호출 0, 인자 그대로 전달
- 대화형 8단계: isatty mock + input mock
- 빈슬러그 fallback (TTY / 비-TTY)
- 폴더 존재 분기 (다른 위치 / clone skip)
- 0개 에이전트 경고 후 진행
- 7단계 요약 n → 재시작 → y 완료
- --agent 복수 비-TTY
- --role / --obsidian 비-TTY
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import importlib.util
import types

import pytest

# cli.py 를 파일 경로로 직접 load — infra/teammode.py 와 이름 충돌 방지.
# pytest 전체 suite 에서 infra/ 가 sys.path 에 먼저 들어가도 영향 없다.
# patch("teammode.cli.*") 가 동작하려면 sys.modules 에 "teammode" 패키지와
# "teammode.cli" 를 모두 등록해야 한다.
#
# ⚠️ sys.modules 오염 방지: 이 모듈이 스텁 "teammode" 패키지를 등록하면 후속 테스트의
# `import teammode`(infra/teammode.py)가 스텁을 받아 깨진다. 따라서 원래 값을 저장하고
# 이 모듈의 테스트가 끝난 뒤 복구하는 autouse fixture 를 module 스코프로 제공한다.
_ORIG_TEAMMODE = sys.modules.get("teammode")
_ORIG_TEAMMODE_CLI = sys.modules.get("teammode.cli")

if "teammode" not in sys.modules or not hasattr(sys.modules["teammode"], "__path__"):
    _pkg = types.ModuleType("teammode")
    _pkg.__path__ = []  # type: ignore[attr-defined]
    _pkg.__package__ = "teammode"
    sys.modules["teammode"] = _pkg

_CLI_PATH = Path(__file__).resolve().parent.parent / "src" / "teammode" / "cli.py"
_spec = importlib.util.spec_from_file_location("teammode.cli", _CLI_PATH)
cli = importlib.util.module_from_spec(_spec)
cli.__package__ = "teammode"
sys.modules["teammode.cli"] = cli
_spec.loader.exec_module(cli)  # type: ignore[union-attr]


# ─── sys.modules 오염 복구 픽스처 ─────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _restore_teammode_modules():
    """이 모듈의 테스트가 모두 끝난 뒤 sys.modules["teammode"]를 원래대로 복구.

    복구하지 않으면 후속 테스트 파일에서 `import teammode` 가 infra/teammode.py 대신
    여기서 등록한 스텁 패키지를 반환해 AttributeError 가 터진다(전체 suite 오염).
    """
    yield  # 이 모듈의 모든 테스트 실행
    # 복구: 원래 값이 있으면 복원, 없으면 키 제거
    if _ORIG_TEAMMODE is not None:
        sys.modules["teammode"] = _ORIG_TEAMMODE
    else:
        sys.modules.pop("teammode", None)
    if _ORIG_TEAMMODE_CLI is not None:
        sys.modules["teammode.cli"] = _ORIG_TEAMMODE_CLI
    else:
        sys.modules.pop("teammode.cli", None)


# ─── 공통 픽스처 ────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_repo(tmp_path):
    """infra/install.py가 있는 가짜 팀 레포."""
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "install.py").write_text("# fake")
    return tmp_path


@pytest.fixture()
def members_file(fake_repo):
    """memory/team/members.md 에 팀원 2명."""
    d = fake_repo / "memory" / "team"
    d.mkdir(parents=True)
    f = d / "members.md"
    f.write_text("- alice\n- bob\n")
    return f


# ─── 비-TTY 경로 ────────────────────────────────────────────────────────────

class TestNonTtyPath:
    """sys.stdin.isatty()=False: input 절대 호출 안 함."""

    def _run_join(self, tmp_path, url="https://github.com/org/team.git",
                  member_name=None, agent=None, role=None, obsidian=False,
                  extra_argv=None):
        """비-TTY cmd_join 실행 헬퍼 — subprocess.run 전부 mock."""
        dest = tmp_path / "team"
        dest.mkdir()
        (dest / "infra").mkdir()
        (dest / "infra" / "install.py").write_text("# fake")

        argv = ["join", url, "--dir", str(dest)]
        if member_name:
            argv += ["--member-name", member_name]
        if agent:
            for ag in agent:
                argv += ["--agent", ag]
        if role:
            argv += ["--role", role]
        if obsidian:
            argv += ["--obsidian"]
        if extra_argv:
            argv += extra_argv

        with patch.object(sys.stdin, "isatty", return_value=False), \
             patch("teammode.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = cli.main(argv)
        return rc, mock_run

    def test_no_input_called(self, tmp_path):
        """비-TTY에서 input() 한 번도 안 불림."""
        with patch("builtins.input") as mock_input:
            self._run_join(tmp_path, member_name="alice")
        mock_input.assert_not_called()

    def test_member_name_forwarded(self, tmp_path):
        """--member-name 이 install.py argv 로 전달됨."""
        rc, mock_run = self._run_join(tmp_path, member_name="alice")
        assert rc == 0
        # _delegate_install 호출 = 마지막 subprocess.run(install.py 포함)
        install_call_args = None
        for c in mock_run.call_args_list:
            args_list = c[0][0] if c[0] else c.kwargs.get("args", [])
            if isinstance(args_list, list) and any("install.py" in str(a) for a in args_list):
                install_call_args = args_list
        assert install_call_args is not None, "install.py 위임 호출 없음"
        assert "--member-name" in install_call_args
        assert "alice" in install_call_args

    def test_agent_forwarded(self, tmp_path):
        """--agent claude 가 install.py 로 전달됨."""
        rc, mock_run = self._run_join(tmp_path, member_name="alice", agent=["claude"])
        assert rc == 0
        install_args = None
        for c in mock_run.call_args_list:
            a = c[0][0] if c[0] else []
            if isinstance(a, list) and any("install.py" in str(x) for x in a):
                install_args = a
        assert install_args is not None
        assert "--agent" in install_args
        assert "claude" in install_args

    def test_role_forwarded(self, tmp_path):
        """--role developer 가 install.py 로 전달됨."""
        rc, mock_run = self._run_join(tmp_path, member_name="alice", role="developer")
        assert rc == 0
        install_args = None
        for c in mock_run.call_args_list:
            a = c[0][0] if c[0] else []
            if isinstance(a, list) and any("install.py" in str(x) for x in a):
                install_args = a
        assert install_args is not None
        assert "--role" in install_args
        assert "developer" in install_args

    def test_obsidian_forwarded(self, tmp_path):
        """--obsidian 이 install.py --register-obsidian 으로 전달됨."""
        rc, mock_run = self._run_join(tmp_path, member_name="alice", obsidian=True)
        assert rc == 0
        install_args = None
        for c in mock_run.call_args_list:
            a = c[0][0] if c[0] else []
            if isinstance(a, list) and any("install.py" in str(x) for x in a):
                install_args = a
        assert install_args is not None
        assert "--register-obsidian" in install_args

    def test_multi_agent_forwarded(self, tmp_path):
        """--agent claude --agent codex 둘 다 전달됨."""
        rc, mock_run = self._run_join(tmp_path, member_name="alice",
                                     agent=["claude", "codex"])
        assert rc == 0
        install_args = None
        for c in mock_run.call_args_list:
            a = c[0][0] if c[0] else []
            if isinstance(a, list) and any("install.py" in str(x) for x in a):
                install_args = a
        assert install_args is not None
        idxs = [i for i, x in enumerate(install_args) if x == "--agent"]
        assert len(idxs) == 2
        agents_passed = [install_args[i + 1] for i in idxs]
        assert "claude" in agents_passed
        assert "codex" in agents_passed


# ─── 빈 슬러그 fallback ─────────────────────────────────────────────────────

class TestEmptySlugFallback:
    def test_tty_empty_slug_forces_reentry(self):
        """TTY + git user.name 없음 → 빈 슬러그 → 입력 강제 루프 → 이름 반환."""
        with patch("teammode.cli._git_user_name", return_value=None), \
             patch.object(sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", side_effect=["", "", "myname"]) as mock_input:
            result = cli._resolve_member(None)
        assert result == "myname"

    def test_non_tty_empty_slug_falls_back_to_email(self):
        """비-TTY + 빈 슬러그 → git email local-part fallback."""
        with patch("teammode.cli._git_user_name", return_value=None), \
             patch("teammode.cli._git_user_email_local_part", return_value="john-doe"), \
             patch.object(sys.stdin, "isatty", return_value=False):
            result = cli._resolve_member(None)
        assert result == "john-doe"

    def test_non_tty_both_empty_returns_none(self):
        """비-TTY + git name/email 모두 없음 → None."""
        with patch("teammode.cli._git_user_name", return_value=None), \
             patch("teammode.cli._git_user_email_local_part", return_value=None), \
             patch.object(sys.stdin, "isatty", return_value=False):
            result = cli._resolve_member(None)
        assert result is None

    def test_email_local_part_strips_special(self):
        """이메일 local-part 에서 ASCII 영숫자·하이픈만 남김."""
        with patch("teammode.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="John.Doe+tag@example.com\n", returncode=0)
            result = cli._git_user_email_local_part()
        assert result == "john-doe-tag"


# ─── 대화형 wizard 8단계 ────────────────────────────────────────────────────

class TestWizardTty:
    """_wizard_join 직접 호출 (subprocess.run, detect_agents mock)."""

    def _run_wizard(self, tmp_path, inputs, installed_agents=("claude",),
                    members=None):
        """wizard 실행 헬퍼. inputs: input() 반환값 순서 리스트.

        members 를 지정하면, inputs[0] 이 가리키는 dest 에 members.md 를 미리 만든다.
        dest 는 inputs 의 첫 번째 값으로 결정된다.
        """
        # members.md 는 wizard 가 읽을 dest 폴더 안에 있어야 한다.
        # inputs[0] 이 dest 경로 — 미리 생성.
        if members is not None:
            dest_path = Path(inputs[0]).expanduser().resolve()
            md = dest_path / "memory" / "team"
            md.mkdir(parents=True, exist_ok=True)
            (md / "members.md").write_text(
                "\n".join(f"- {m}" for m in members) + "\n")

        with patch("teammode.cli._detect_agents_from_install_lib",
                   return_value=list(installed_agents)), \
             patch("teammode.cli._git_user_name", return_value="Eunsu Jang"), \
             patch.object(sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", side_effect=inputs) as mock_input:
            dest, member, extra, clone_skip = cli._wizard_join(
                "https://github.com/org/team.git", MagicMock())
        # return (dest, member, extra, mock_input, clone_skip) as namedtuple-like tuple
        return dest, member, extra, mock_input, clone_skip

    def test_basic_happy_path(self, tmp_path):
        """8단계 기본 흐름 — Enter로 기본값 모두 선택."""
        inputs = [
            str(tmp_path / "new-dest"),  # 1) 위치
            "",                           # 2) 에이전트 (Enter=전부)
            "1",                          # 3) 새 팀원
            "eunsu-jang",                 # 4) 이름
            "",                           # 5) 역할 (생략)
            "N",                          # 6) Obsidian
            "Y",                          # 7) 확인
        ]
        dest, member, extra, _, _cs = self._run_wizard(tmp_path, inputs)
        assert member == "eunsu-jang"
        assert "--register-obsidian" not in extra

    def test_agent_toggle(self, tmp_path):
        """2단계: 번호 입력으로 에이전트 토글."""
        inputs = [
            str(tmp_path / "dest"),
            "2",      # codex 토글 off (installed=[claude,codex] 기준으로 codex 제거)
            "1",      # 새 팀원
            "alice",
            "",
            "N",
            "Y",
        ]
        dest, member, extra, _, _cs = self._run_wizard(
            tmp_path, inputs, installed_agents=["claude", "codex"])
        # codex 토글 off → claude 만 남음
        agent_args = [extra[i + 1] for i, x in enumerate(extra) if x == "--agent"]
        assert "claude" in agent_args
        assert "codex" not in agent_args

    def test_role_passed(self, tmp_path):
        """5단계: 역할 입력 → --role extra."""
        inputs = [
            str(tmp_path / "dest"),
            "",
            "1",
            "alice",
            "developer",
            "N",
            "Y",
        ]
        dest, member, extra, _, _cs = self._run_wizard(tmp_path, inputs)
        assert "--role" in extra
        assert "developer" in extra

    def test_obsidian_yes(self, tmp_path):
        """6단계: y 입력 → --register-obsidian extra."""
        inputs = [
            str(tmp_path / "dest"),
            "",
            "1",
            "alice",
            "",
            "y",
            "Y",
        ]
        dest, member, extra, _, _cs = self._run_wizard(tmp_path, inputs)
        assert "--register-obsidian" in extra

    def test_existing_member_pick(self, tmp_path):
        """3단계: 기존 팀원 2 선택 → members.md 파싱 후 번호 선택."""
        # dest 를 pre-populate 하면 step1 에서 "비어있지 않음" 분기가 뜬다 → 2(재설치) 선택
        inputs = [
            str(tmp_path / "dest"),
            "2",      # ② 기존에 재설치(clone skip) — folder not empty
            "",       # 2) 에이전트
            "2",      # 3) 기존 팀원
            "1",      # 4) alice (1번)
            "",       # 5) 역할
            "N",      # 6) Obsidian
            "Y",      # 7) 확인
        ]
        dest, member, extra, _, _cs = self._run_wizard(
            tmp_path, inputs, members=["alice", "bob"])
        assert member == "alice"

    def test_summary_n_restarts(self, tmp_path):
        """7단계 n → 처음부터 재시작 → y 완료."""
        inputs = [
            # 1회차 (n으로 재시작)
            str(tmp_path / "dest1"),
            "",
            "1",
            "alice",
            "",
            "N",
            "n",       # 재시작!
            # 2회차 (y 확인)
            str(tmp_path / "dest2"),
            "",
            "1",
            "bob",
            "pm",
            "N",
            "Y",
        ]
        dest, member, extra, _, _cs = self._run_wizard(tmp_path, inputs)
        assert member == "bob"
        assert "--role" in extra
        assert "pm" in extra

    def test_zero_agents_continues(self, tmp_path, capsys):
        """0개 에이전트 감지 → 경고 출력 후 진행 (abort 아님)."""
        inputs = [
            str(tmp_path / "dest"),
            "",
            "1",
            "alice",
            "",
            "N",
            "Y",
        ]
        dest, member, extra, _, _cs = self._run_wizard(
            tmp_path, inputs, installed_agents=[])
        out = capsys.readouterr().out
        assert "감지" in out or "에이전트" in out

    def test_folder_exists_nonempty_other_location(self, tmp_path):
        """1단계: 기존 비어있지 않은 폴더 → 1번(다른 위치) 선택 → 새 위치 지정."""
        # 이미 있는 폴더에 파일 넣기
        existing = tmp_path / "existing-dest"
        existing.mkdir()
        (existing / "somefile.txt").write_text("x")

        new_dest = tmp_path / "new-dest"

        inputs = [
            str(existing),          # 1) 위치 → 이미 있음
            "1",                    # ① 다른 위치 입력
            str(new_dest),          # 새 위치
            "",                     # 2) 에이전트
            "1",                    # 3) 새 팀원
            "alice",                # 4) 이름
            "",                     # 5) 역할
            "N",                    # 6) Obsidian
            "Y",                    # 7) 확인
        ]
        dest, member, extra, _, clone_skip = self._run_wizard(tmp_path, inputs)
        assert dest == new_dest.resolve()
        assert clone_skip is False

    def test_folder_exists_nonempty_reuse(self, tmp_path):
        """1단계: 기존 비어있지 않은 폴더 → 2번(재설치, clone skip)."""
        existing = tmp_path / "existing-dest"
        existing.mkdir()
        (existing / "somefile.txt").write_text("x")

        inputs = [
            str(existing),   # 1) 위치 → 이미 있음
            "2",             # ② 기존에 재설치(clone skip)
            "",              # 2) 에이전트
            "1",             # 3) 새 팀원
            "alice",         # 4) 이름
            "",              # 5) 역할
            "N",             # 6) Obsidian
            "Y",             # 7) 확인
        ]
        dest, member, extra, _, clone_skip = self._run_wizard(tmp_path, inputs)
        assert dest == existing.resolve()
        assert clone_skip is True


# ─── cmd_join TTY 통합 ──────────────────────────────────────────────────────

class TestCmdJoinTtyIntegration:
    """cmd_join + TTY: clone + wizard + delegate 흐름."""

    def test_tty_clone_and_install(self, tmp_path):
        """TTY cmd_join: clone 후 install.py 위임 확인."""
        dest = tmp_path / "team"

        def fake_run(cmd, *a, **kw):
            # clone 시 폴더·infra 생성
            if isinstance(cmd, list) and "clone" in cmd:
                dest.mkdir(exist_ok=True)
                (dest / "infra").mkdir(exist_ok=True)
                (dest / "infra" / "install.py").write_text("# fake")
            return MagicMock(returncode=0)

        wizard_inputs = [
            str(dest),      # 1) 위치
            "",             # 2) 에이전트
            "1",            # 3) 새 팀원
            "alice",        # 4) 이름
            "",             # 5) 역할
            "N",            # 6) Obsidian
            "Y",            # 7) 확인
        ]
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch("teammode.cli._detect_agents_from_install_lib", return_value=["claude"]), \
             patch("teammode.cli._git_user_name", return_value="Alice"), \
             patch("teammode.cli.subprocess.run", side_effect=fake_run), \
             patch("builtins.input", side_effect=wizard_inputs):
            rc = cli.main(["join", "https://github.com/org/team.git"])
        assert rc == 0

    def test_tty_clone_skip_skips_git_clone(self, tmp_path):
        """TTY: clone skip 선택 시 git clone subprocess 호출 안 함."""
        # 이미 있는 비어있지 않은 폴더
        existing = tmp_path / "existing"
        existing.mkdir()
        (existing / "somefile.txt").write_text("x")
        (existing / "infra").mkdir()
        (existing / "infra" / "install.py").write_text("# fake")

        wizard_inputs = [
            str(existing),   # 1) 위치
            "2",             # ② 재설치(clone skip)
            "",              # 2) 에이전트
            "1",             # 3) 새 팀원
            "alice",         # 4) 이름
            "",              # 5) 역할
            "N",             # 6) Obsidian
            "Y",             # 7) 확인
        ]
        cloned = []

        def fake_run(cmd, *a, **kw):
            if isinstance(cmd, list) and "clone" in cmd:
                cloned.append(True)
            return MagicMock(returncode=0)

        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch("teammode.cli._detect_agents_from_install_lib", return_value=["claude"]), \
             patch("teammode.cli._git_user_name", return_value="Alice"), \
             patch("teammode.cli.subprocess.run", side_effect=fake_run), \
             patch("builtins.input", side_effect=wizard_inputs):
            rc = cli.main(["join", "https://github.com/org/team.git"])
        assert rc == 0
        assert not cloned, "clone skip 인데 git clone 이 호출됨"


class TestCmdInitDelegatesToJoin:
    """init = 레포 '생성'만(--clone 없이) → 곧바로 cmd_join 으로 넘어감(생성 ↔ 참여 분리)."""

    def test_init_creates_then_joins(self):
        runs = []

        def fake_run(cmd, *a, **kw):
            runs.append(cmd)
            return MagicMock(returncode=0, stdout="")

        join_calls = []

        def fake_join(args, **kw):
            join_calls.append((args, kw))
            return 0

        with patch("teammode.cli._have", return_value=True), \
             patch("teammode.cli.subprocess.run", side_effect=fake_run), \
             patch("teammode.cli.cmd_join", side_effect=fake_join):
            rc = cli.main(["init", "myorg/myteam", "--public"])

        assert rc == 0
        # gh repo create 호출에 --clone 이 없어야(생성만).
        creates = [c for c in runs if isinstance(c, list) and "create" in c]
        assert creates, "gh repo create 가 호출되지 않음"
        assert "--clone" not in creates[0]
        assert "myorg/myteam" in creates[0]
        # cmd_join 으로 넘어갔고 레포 URL + created=True(생성자 경유) 가 전달됐다.
        assert len(join_calls) == 1
        _jargs, _jkw = join_calls[0]
        assert _jargs.url == "https://github.com/myorg/myteam.git"
        assert _jkw.get("created") is True

    def test_init_create_fail_does_not_join(self):
        """레포 생성 실패면 join 으로 넘어가지 않고 비정상 종료."""
        def fake_run(cmd, *a, **kw):
            if isinstance(cmd, list) and "create" in cmd:
                return MagicMock(returncode=1, stdout="")  # 생성 실패
            return MagicMock(returncode=0, stdout="")      # auth status 등

        join_calls = []

        with patch("teammode.cli._have", return_value=True), \
             patch("teammode.cli.subprocess.run", side_effect=fake_run), \
             patch("teammode.cli.cmd_join",
                   side_effect=lambda a, **k: join_calls.append(a) or 0):
            rc = cli.main(["init", "myorg/myteam"])

        assert rc != 0
        assert join_calls == [], "생성 실패인데 join 으로 넘어감"


def test_done_message_created_vs_joined(capsys):
    """init 경유(created=True)면 '생성 완료', join 직접이면 '합류 완료'."""
    cli._done(Path("/x/team"), created=True)
    out = capsys.readouterr().out
    assert "팀 생성 완료" in out and "팀 합류 완료" not in out

    cli._done(Path("/x/team"), created=False)
    out = capsys.readouterr().out
    assert "팀 합류 완료" in out and "팀 생성 완료" not in out


def test_init_injects_join_attrs_for_nontty():
    """cmd_init 이 cmd_join 에 비-TTY 경로가 참조하는 속성을 다 채워 넘긴다(AttributeError 방지)."""
    join_args = []
    with patch("teammode.cli._have", return_value=True), \
         patch("teammode.cli.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")), \
         patch("teammode.cli.cmd_join",
               side_effect=lambda a, **k: join_args.append(a) or 0):
        cli.main(["init", "o/r"])
    assert len(join_args) == 1
    a = join_args[0]
    for attr in ("url", "member_name", "dir", "obsidian", "agent", "role"):
        assert hasattr(a, attr), f"cmd_join 이 참조하는 args.{attr} 누락"
