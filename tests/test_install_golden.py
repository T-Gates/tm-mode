"""L1-F — install.py 골든 시나리오 (spec/04 §11: I1·I2·I2b·I3·I4·I4b·I-dry).

스펙 04 §11 합격 기준을 실행 가능한 인수 테스트로. install.py 를 subprocess 로
끝까지 돌려(부트스트랩 전 경로) 외부 계약을 검증한다. 호스트 무접촉:
HOME=tmp + --settings 격리 + TEAMMODE_HOME ambient 주입해도 무시 확인.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
INSTALL = REPO / "infra" / "install.py"
ENGINE = REPO / "infra" / "hooks" / "session-start.py"  # I2b 용


def _env(home: Path, extra=None):
    e = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if "XDG_STATE_HOME" in os.environ:
        e["XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    if extra:
        e.update(extra)
    return e


def _git_init(path: Path, name="Heidi", email="h@h.com"):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(path)})
    for k, v in (("user.name", name), ("user.email", email)):
        subprocess.run(["git", "config", k, v], cwd=str(path), check=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(path)})


def _run_install(team: Path, home: Path, args, extra_env=None):
    return subprocess.run(
        [PY, str(INSTALL), "--root", str(team)] + args,
        capture_output=True, text=True, env=_env(home, extra_env), timeout=60)


# ─────────────────────────── I1 — 빈/엔진만 레포 (도입자) ───────────────────────────

def test_I1_introducer_full_run(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0, proc.stderr
    # memory/·config(빈 슬롯)·sessions/<이름>/·배너 생성
    assert (team / "memory" / "INDEX.md").is_file()
    assert (team / "team.config.json").is_file()
    cfg = json.loads((team / "team.config.json").read_text())
    assert cfg["services"] == {}
    assert (team / "memory" / "team" / "sessions" / "heidi").is_dir()
    assert (team / "memory" / "banner.txt").is_file()
    # 첫 세션로그 미생성(M2)
    assert list((team / "memory" / "team" / "sessions" / "heidi").iterdir()) == []
    # verify: context --json 이 L1 데이터를 읽어냄 + active 마커
    assert "[verify] L1 데이터 읽힘" in proc.stdout
    assert (team / ".acme-active").is_file()


# ─────────────────────────── I2 — 유효 config 레포 (팀원) ───────────────────────────

def test_I2_member_does_not_modify_config(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team, name="Ivan", email="ivan@x.com")
    cfg = {"spec_version": "0.1", "team": {"name": "preset-team"},
           "admin_contact": "founder", "services": {}}
    (team / "team.config.json").write_text(json.dumps(cfg, indent=2))
    before = (team / "team.config.json").read_text()
    # 기존 팀원 1명 등재된 상태
    md = team / "memory" / "team"
    md.mkdir(parents=True)
    (md / "members.md").write_text("# members\n- founder\n")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0, proc.stderr
    # config 무수정(팀원 경로)
    assert (team / "team.config.json").read_text() == before
    # 본인 이름 등재
    assert "ivan" in (md / "members.md").read_text()
    assert "[verify] L1 데이터 읽힘" in proc.stdout


# ─────────────────────────── I2b — 다음 세션 SessionStart 주입 ───────────────────────────

def test_I2b_next_session_injects_context(tmp_path):
    """I1/I2 직후 새 세션 → SessionStart 훅이 맥락 실제 주입(install 아님)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    _run_install(team, home, ["--settings", str(iso)])
    # 멤버 세션로그 하나 적재(첫 작업 시뮬레이션) — 그래야 주입 내용이 생김
    sess = team / "memory" / "team" / "sessions" / "heidi"
    (sess / "2026-06-14.md").write_text(
        "---\nauthor: heidi\ndate: 2026-06-14\nsummary: 부트스트랩 검증\n---\n")
    # 새 세션 = SessionStart 훅 호출
    proc = subprocess.run(
        [PY, str(REPO / "infra" / "hooks" / "session-start.py")],
        input=json.dumps({"event": "SessionStart", "agent": "claude"}),
        capture_output=True, text=True,
        env=_env(home, {"TEAMMODE_HOME": str(team)}))
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "부트스트랩 검증" in out["hookSpecificOutput"]["additionalContext"]


# ─────────────────────────── I3 — 멱등 재실행 ───────────────────────────

def test_I3_idempotent_rerun(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    _run_install(team, home, ["--settings", str(iso)])
    cfg1 = (team / "team.config.json").read_text()
    members1 = (team / "memory" / "team" / "members.md").read_text()
    proc2 = _run_install(team, home, ["--settings", str(iso)])
    assert proc2.returncode == 0
    assert (team / "team.config.json").read_text() == cfg1
    assert (team / "memory" / "team" / "members.md").read_text() == members1
    assert members1.count("heidi") == 1


# ─────────────────────────── I4 — ambient TEAMMODE_HOME 무시 ───────────────────────────

def test_I4_ambient_teammode_home_ignored(tmp_path):
    """실호스트 가리키는 ambient TEAMMODE_HOME set 상태 → 그 경로 무접촉(P1 회귀)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    victim = tmp_path / "victim"
    victim.mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)],
                        extra_env={"TEAMMODE_HOME": str(victim),
                                   "LEGACY_TOOL_HOME": str(victim)})
    assert proc.returncode == 0
    # victim 무접촉: memory·marker 미생성
    assert not (victim / "memory").exists()
    assert not (victim / ".acme-active").exists()
    # 작업은 명시 --root(team)에만
    assert (team / "memory").is_dir()


# ─────────────────────────── I4b — --settings 격리 ───────────────────────────

def test_I4b_settings_isolation(tmp_path):
    """--settings 격리 지정 → 실 ~/.claude/settings.json 무접촉, 격리에만 씀."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0
    # 실호스트(fake home) ~/.claude/settings.json 무생성
    assert not (home / ".claude" / "settings.json").exists()
    # 격리 경로에만 배선
    assert (iso / "claude" / "settings.json").is_file()
    # 격리는 env 까지 격리 — 실 셸 프로파일에 TEAMMODE_HOME 안 샘(도그푸딩 회귀).
    for name in (".bashrc", ".zshrc", ".profile", ".bash_profile"):
        p = home / name
        if p.is_file():
            assert "TEAMMODE_HOME" not in p.read_text(), \
                f"격리(--settings)인데 {name} 에 env 가 샜다"


# ─────────────────────────── I-dry — dry-run 무접촉 ───────────────────────────

def test_Idry_no_side_effects(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso), "--dry-run"])
    assert proc.returncode == 0
    assert "dry-run" in proc.stdout.lower()
    # 무접촉: memory·config·marker·iso settings 전부 미생성
    assert not (team / "memory").exists()
    assert not (team / "team.config.json").exists()
    assert not (team / ".acme-active").exists()
    assert not iso.exists()
    assert not (home / ".bashrc").exists()
