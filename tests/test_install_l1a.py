"""L1-A — install.py CLI·preflight·detect·role 테스트 (spec/04 §3·§4·§5·§6).

순수 함수(install_lib)를 단위 테스트한다. 호스트 무접촉 철칙: HOME 변형은
monkeypatch + tmp_path 로만, 실 git config·실 ~/.claude·실 셸 프로파일 무접촉.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


# ─────────────────────────── CLI 인자 파서 (§3) ───────────────────────────

def test_parse_args_defaults():
    opts = il.parse_args([])
    assert opts.root is None
    assert opts.agents == []  # [] = auto(미지정 시 감지 전부)
    assert opts.member_name is None
    assert opts.settings is None
    assert opts.yes is False
    assert opts.update is False
    assert opts.dry_run is False


def test_parse_args_all_flags():
    opts = il.parse_args([
        "--root", "/team", "--agent", "claude", "--member-name", "alice",
        "--settings", "/tmp/s.json", "--yes", "--update", "--dry-run",
    ])
    assert opts.root == "/team"
    assert opts.agents == ["claude"]  # 단일 --agent → list
    assert opts.member_name == "alice"
    assert opts.settings == "/tmp/s.json"
    assert opts.yes is True
    assert opts.update is True
    assert opts.dry_run is True


# ─────────────────────────── preflight (§4 ①) ───────────────────────────

def test_preflight_python_below_min_fails(monkeypatch):
    monkeypatch.setattr(il, "MIN_PYTHON", (99, 0))
    res = il.preflight(team_root=REPO, python_version=(3, 13),
                       git_present=True, remote_authed=True)
    assert res.ok is False
    assert res.exit_code == 2
    assert "python" in res.message.lower()


def test_preflight_no_git_binary_fails():
    res = il.preflight(team_root=REPO, python_version=(3, 13),
                       git_present=False, remote_authed=True)
    assert res.ok is False
    assert res.exit_code == 2
    assert "git" in res.message.lower()


def test_preflight_no_remote_auth_warns_not_fatal(tmp_path):
    # 팀 루트 표식(.git) 있어야 통과 — git remote 인증만 부재 (m3, I6b)
    (tmp_path / ".git").mkdir()
    res = il.preflight(team_root=tmp_path, python_version=(3, 13),
                       git_present=True, remote_authed=False)
    assert res.ok is True            # 종료 안 함 — 로컬 L1 진행
    assert res.exit_code == 0
    assert any("인증" in w or "remote" in w.lower() for w in res.warnings)


def test_preflight_no_team_root_marker_fails(tmp_path):
    # cwd/root 에 팀 레포 표식(.git) 없으면 에러 (§2.2, §10)
    res = il.preflight(team_root=tmp_path, python_version=(3, 13),
                       git_present=True, remote_authed=True)
    assert res.ok is False
    assert res.exit_code == 2


def test_preflight_ok_with_git_marker(tmp_path):
    (tmp_path / ".git").mkdir()
    res = il.preflight(team_root=tmp_path, python_version=(3, 13),
                       git_present=True, remote_authed=True)
    assert res.ok is True
    assert res.exit_code == 0


# ─────────────────────────── role 판정 (§4 ③, M3) ───────────────────────────

def test_role_introducer_when_config_absent(tmp_path):
    assert il.detect_role(tmp_path) == "introducer"


def test_role_member_when_config_valid(tmp_path):
    cfg = {"spec_version": "0.1", "team": {"name": "acme"}}
    (tmp_path / "team.config.json").write_text(json.dumps(cfg))
    assert il.detect_role(tmp_path) == "member"


def test_role_introducer_when_team_name_placeholder(tmp_path):
    # team.name 이 placeholder/미초기화 표식 → 도입자 (§4 ③)
    for placeholder in ("", "CHANGEME", "TODO", "your-team-name"):
        cfg = {"spec_version": "0.1", "team": {"name": placeholder}}
        (tmp_path / "team.config.json").write_text(json.dumps(cfg))
        assert il.detect_role(tmp_path) == "introducer", placeholder


def test_role_introducer_when_required_keys_missing(tmp_path):
    # spec_version 누락 → 유효하지 않음 → 도입자
    cfg = {"team": {"name": "acme"}}
    (tmp_path / "team.config.json").write_text(json.dumps(cfg))
    assert il.detect_role(tmp_path) == "introducer"


def test_role_does_not_depend_on_services(tmp_path):
    # M3: services 채움 여부로 가르지 않는다 — 빈 슬롯이어도 member
    cfg = {"spec_version": "0.1", "team": {"name": "acme"}, "services": {}}
    (tmp_path / "team.config.json").write_text(json.dumps(cfg))
    assert il.detect_role(tmp_path) == "member"


def test_role_introducer_when_config_malformed(tmp_path):
    # 깨진 JSON → 안전하게 도입자로 간주(크래시 금지)
    (tmp_path / "team.config.json").write_text("{ not json ")
    assert il.detect_role(tmp_path) == "introducer"


# ─────────────────────────── detect (§4 ②) ───────────────────────────

def test_detect_agents_finds_claude_and_codex(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    found = il.detect_agents(home=home)
    assert "claude" in found
    assert "codex" in found


def test_detect_agents_empty_when_none(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert il.detect_agents(home=home) == []


def test_detect_member_name_from_git_user(tmp_path):
    # git user.name → 이름 제안 (영문 소문자 정규화). 실 git config 무접촉(주입).
    assert il.suggest_member_name(git_user_name="Alice Kim") == "alicekim"
    assert il.suggest_member_name(git_user_name="bob") == "bob"
    assert il.suggest_member_name(git_user_name=None) is None
    assert il.suggest_member_name(git_user_name="") is None


def test_detect_team_name_from_remote():
    # git remote URL → repo 명 추출 (도입자 team.name 기본값)
    assert il.repo_name_from_remote(
        "git@github.com:T-Gates/teammode.git") == "teammode"
    assert il.repo_name_from_remote(
        "https://github.com/T-Gates/teammode") == "teammode"
    assert il.repo_name_from_remote(None) is None


# ─────────────────────── bootstrap 오케스트레이터 (§4, I-dry/I6b) ───────────────────────

import runpy  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__install_test__")


def _git_init(path: Path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(path),
                   check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(path),
                   check=True)


def test_bootstrap_requires_root_or_marker(tmp_path, monkeypatch, capsys):
    """--root 미지정 + cwd 팀표식 없음 → exit 2 (env 불신뢰·추측 금지, §10)."""
    mod = _load_install()
    opts = il.parse_args([])  # --root 없음
    monkeypatch.chdir(tmp_path)  # 표식 없는 cwd
    rc = mod["bootstrap"](opts, home=tmp_path, python_version=(3, 13))
    assert rc == 2
    assert "root" in capsys.readouterr().err.lower()


def test_bootstrap_ignores_ambient_teammode_home(tmp_path, monkeypatch, capsys):
    """ambient TEAMMODE_HOME 가 set 돼도 install 부트스트랩은 읽지 않는다(I4, P1)."""
    victim = tmp_path / "victim"
    victim.mkdir()
    monkeypatch.setenv("TEAMMODE_HOME", str(victim))
    monkeypatch.setenv("LEGACY_TOOL_HOME", str(victim))
    mod = _load_install()
    opts = il.parse_args([])
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)  # 표식 없음 → root 미해결이어야 함(victim 채택 금지)
    rc = mod["bootstrap"](opts, home=tmp_path, python_version=(3, 13))
    assert rc == 2  # victim 을 ambient 로 줍지 않았다 → root 미해결

def test_bootstrap_dry_run_no_side_effects(tmp_path, monkeypatch, capsys):
    """--dry-run: 계획만 출력, memory·settings·env 무접촉(I-dry)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--dry-run"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run" in out.lower()
    # 무접촉: memory/ 생성 안 됨 (L1-A 는 아직 scaffold 안 함 — 그래도 단언으로 고정)
    assert not (team / "memory").exists()


def test_bootstrap_role_introducer_no_config(tmp_path, monkeypatch, capsys):
    """config 부재 레포 → role=introducer 로 계획 출력(§5)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    assert "role=introducer" in capsys.readouterr().out


def test_bootstrap_python_below_min_exits_2(tmp_path, capsys):
    """Python 하한 미달 → exit 2, 무변경(I6)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=tmp_path, python_version=(2, 7))
    assert rc == 2
    assert "python" in capsys.readouterr().err.lower()
