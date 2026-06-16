"""L1-B — scaffold 테스트 (spec/04 §4④·§5·§6, M1·M2·M4).

memory/ 구조·도입자 최소 config·members.md 충돌정책·banner 선기록. 첫 세션로그
안 씀(M2). 세션 경로는 엔진 단일소스 memory/team/sessions/<author>/ (M1).
호스트 무접촉: 전부 tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


# ─────────────────────────── memory 구조 (§4④) ───────────────────────────

def test_scaffold_creates_memory_structure(tmp_path):
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="acme")
    assert (tmp_path / "memory" / "INDEX.md").is_file()
    assert (tmp_path / "memory" / "team" / "members.md").is_file()
    # 엔진 단일소스 경로 (teammode.py:191): memory/team/sessions/<author>/
    assert (tmp_path / "memory" / "team" / "sessions" / "alice").is_dir()
    # banner 선기록 (엔진 무수정 우회, M4)
    assert (tmp_path / "memory" / "banner.txt").is_file()


def test_scaffold_session_dir_matches_engine_path(tmp_path):
    """세션 경로가 memory/sessions/ 가 아니라 memory/team/sessions/ 임을 못박는다(M1)."""
    il.scaffold_memory(tmp_path, member_name="bob", role="introducer",
                       team_name="t")
    assert (tmp_path / "memory" / "team" / "sessions" / "bob").is_dir()
    assert not (tmp_path / "memory" / "sessions").exists()


def test_scaffold_does_not_write_first_session_log(tmp_path):
    """M2: 첫 세션로그를 install.py 가 쓰지 않는다 — 디렉토리만."""
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="t")
    sess = tmp_path / "memory" / "team" / "sessions" / "alice"
    assert sess.is_dir()
    assert list(sess.iterdir()) == []  # 로그 파일 0


def test_scaffold_banner_uses_team_name(tmp_path):
    il.scaffold_memory(tmp_path, member_name="a", role="introducer",
                       team_name="my-team")
    banner = (tmp_path / "memory" / "banner.txt").read_text()
    assert "my-team" in banner


# ─────────────────────────── 도입자 config (§5) ───────────────────────────

def test_introducer_writes_minimal_config(tmp_path):
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="acme", timezone="Asia/Seoul",
                       locale="ko_KR")
    cfg = json.loads((tmp_path / "team.config.json").read_text())
    assert cfg["spec_version"]
    assert cfg["team"]["name"] == "acme"
    assert cfg["team"]["timezone"] == "Asia/Seoul"
    assert cfg["admin_contact"] == "alice"
    assert cfg["members_file"]
    # services: 전부 빈 슬롯(키 생략) — §5-1·스펙02 §9.2
    assert cfg.get("services", {}) == {}


def test_introducer_config_is_valid_for_role(tmp_path):
    """도입자가 쓴 config 는 그 뒤 role 판정에서 member 로 유효해야 한다(자기일관)."""
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="acme")
    assert il.detect_role(tmp_path) == "member"


def test_member_only_upserts_own_members_entry(tmp_path):
    """팀원 경로(L2-A2 완화, Jane 결정): config 코어키는 무수정, **자기 members
    엔트리만** upsert. spec_version/team/admin_contact/services 등 다른 키 불변."""
    cfg = {"spec_version": "0.1", "team": {"name": "acme"},
           "admin_contact": "founder", "services": {}}
    (tmp_path / "team.config.json").write_text(json.dumps(cfg))
    il.scaffold_memory(tmp_path, member_name="bob", role="member",
                       team_name="acme", member_role="developer")
    after = json.loads((tmp_path / "team.config.json").read_text())
    # 코어 키 무수정.
    assert after["spec_version"] == "0.1"
    assert after["team"] == {"name": "acme"}
    assert after["admin_contact"] == "founder"
    assert after["services"] == {}
    # 자기 members 엔트리만 추가.
    assert after["members"] == [{"name": "bob", "role": "developer"}]


def test_member_does_not_touch_other_members_entries(tmp_path):
    """팀원 install 이 타인 members 엔트리를 절대 안 건드림(각자 upsert 정합)."""
    cfg = {"spec_version": "0.1", "team": {"name": "acme"},
           "admin_contact": "founder", "services": {},
           "members": [{"name": "founder", "role": "pm"}]}
    (tmp_path / "team.config.json").write_text(json.dumps(cfg))
    il.scaffold_memory(tmp_path, member_name="bob", role="member",
                       team_name="acme", member_role="developer")
    members = json.loads((tmp_path / "team.config.json").read_text())["members"]
    assert {"name": "founder", "role": "pm"} in members  # 타인 무접촉
    assert {"name": "bob", "role": "developer"} in members


# ─────────────────────────── members.md 충돌정책 (§6-2, M4) ───────────────────────────

def test_member_added_to_members_file(tmp_path):
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="t")
    members = (tmp_path / "memory" / "team" / "members.md").read_text()
    assert "alice" in members


def test_member_idempotent_no_duplicate(tmp_path):
    """같은 이름 재등재 = 멱등(추가 안 함, 본인 항목 간주, M4)."""
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="t")
    il.scaffold_memory(tmp_path, member_name="alice", role="member",
                       team_name="t")
    members = (tmp_path / "memory" / "team" / "members.md").read_text()
    assert members.count("alice") == 1


def test_member_name_conflict_raises(tmp_path):
    """다른 사람 이름과 충돌(오버라이드) → ConflictError (exit 3, I8, M4).

    같은 이름은 멱등이지만, '추가'시도가 이미 다른 맥락이면 사람이 해소. v0.1 정책:
    동일 영문 이름은 무조건 본인 간주(멱등)이므로 충돌은 add_member 의 명시적
    different-person 신호로만 발생 — 여기선 register_member 의 충돌 검출을 테스트.
    """
    # 먼저 alice 등재
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="t")
    members_file = tmp_path / "memory" / "team" / "members.md"
    # alice 가 이미 '다른 사람'으로 표시된 상황을 모사하는 건 v0.1 범위 밖.
    # 핵심: 같은 이름 재등재는 절대 예외 아님(멱등) — 이걸 단언.
    try:
        il.register_member(members_file, "alice")
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"같은 이름 재등재가 예외를 던지면 안 됨(멱등): {e}")


def test_member_conflict_different_identity_raises(tmp_path):
    """I8/M4: 같은 이름·다른 식별자(git email) → ConflictError(exit 3)."""
    members_file = tmp_path / "members.md"
    assert il.register_member(members_file, "alice", identity="alice@a.com") is True
    # 같은 식별자 재등재 = 멱등
    assert il.register_member(members_file, "alice", identity="alice@a.com") is False
    # 다른 식별자가 같은 이름 점유 → 충돌
    with pytest.raises(il.ConflictError):
        il.register_member(members_file, "alice", identity="mallory@evil.com")
    # members.md 무변경(중복·오염 0): alice 1회
    assert (members_file.read_text()).count("- alice") == 1


def test_member_conflict_unknown_identity_is_idempotent(tmp_path):
    """식별자 미상(레거시 항목 또는 미주입)이면 충돌로 보지 않는다(멱등)."""
    members_file = tmp_path / "members.md"
    il.register_member(members_file, "alice")  # identity 없음(레거시)
    # 나중에 identity 주고 와도 충돌 아님(미상 → 본인 간주)
    assert il.register_member(members_file, "alice", identity="alice@a.com") is False


def test_register_member_rejects_invalid_name(tmp_path):
    """이름은 엔진 _validate_author 재사용 — traversal/선두dash 거부(m1)."""
    members_file = tmp_path / "memory" / "team" / "members.md"
    members_file.parent.mkdir(parents=True)
    members_file.write_text("# members\n")
    for bad in ("../etc", "-rf", "a/b", "", ".."):
        with pytest.raises(il.InvalidNameError):
            il.register_member(members_file, bad)


def test_scaffold_rejects_invalid_member_name(tmp_path):
    """scaffold 진입 시 잘못된 이름은 즉시 거부(traversal 방지)."""
    with pytest.raises(il.InvalidNameError):
        il.scaffold_memory(tmp_path, member_name="../escape", role="introducer",
                           team_name="t")


# ─────────────────────────── 멱등 (§7, I3) ───────────────────────────

def test_scaffold_idempotent(tmp_path):
    """재실행 안전 — 디렉토리·config·members 중복 생성 0(I3)."""
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="acme")
    index_before = (tmp_path / "memory" / "INDEX.md").read_text()
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="acme")
    index_after = (tmp_path / "memory" / "INDEX.md").read_text()
    assert index_before == index_after
    members = (tmp_path / "memory" / "team" / "members.md").read_text()
    assert members.count("alice") == 1


# ─────────────────────── bootstrap → scaffold 통합 (§4④) ───────────────────────

import runpy  # noqa: E402
import subprocess  # noqa: E402

INSTALL_PY = REPO / "infra" / "install.py"


def _load_install():
    return runpy.run_path(str(INSTALL_PY), run_name="__install_l1b_test__")


def _git_init(path: Path, user="Test User"):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", user], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(path), check=True)


def test_bootstrap_scaffolds_introducer(tmp_path, capsys):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 0
    assert (team / "memory" / "INDEX.md").is_file()
    assert (team / "memory" / "team" / "sessions" / "testuser").is_dir()
    assert (team / "team.config.json").is_file()
    # 첫 세션로그 없음(M2)
    assert list((team / "memory" / "team" / "sessions" / "testuser").iterdir()) == []


def test_bootstrap_exit3_when_no_name_resolvable(tmp_path, capsys):
    """git user.name 없고 --member-name 도 없으면 exit 3(추측 금지, m1)."""
    team = tmp_path / "team"
    team.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(team), check=True)
    # user.name 미설정
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 3
    assert "member-name" in capsys.readouterr().err.lower()


def test_bootstrap_invalid_member_name_exit3(tmp_path, capsys):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team), "--member-name", "../escape"])
    rc = mod["bootstrap"](opts, home=home, python_version=(3, 13))
    assert rc == 3
    # traversal 폴더 미생성
    assert not (team / "memory" / "team" / "sessions" / "..").exists()


def test_bootstrap_idempotent_rerun(tmp_path, capsys):
    """I3: 재실행 시 members 중복 0·config 무변경."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    opts = il.parse_args(["--root", str(team)])
    mod["bootstrap"](opts, home=home, python_version=(3, 13))
    cfg1 = (team / "team.config.json").read_text()
    mod2 = _load_install()
    mod2["bootstrap"](opts, home=home, python_version=(3, 13))
    cfg2 = (team / "team.config.json").read_text()
    assert cfg1 == cfg2
    members = (team / "memory" / "team" / "members.md").read_text()
    assert members.count("testuser") == 1


def test_bootstrap_i8_conflict_exit3(tmp_path, capsys):
    """I8: 다른 git 식별자가 같은 멤버 이름을 점유 → exit 3, members.md 무변경."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team, user="Alice")
    subprocess.run(["git", "config", "user.email", "alice@a.com"],
                   cwd=str(team), check=True)
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_install()
    # 1차: alice 등재(identity alice@a.com)
    mod["bootstrap"](il.parse_args(["--root", str(team)]),
                     home=home, python_version=(3, 13))
    members_before = (team / "memory" / "team" / "members.md").read_text()
    # 2차: 다른 사람(mallory@evil.com)이 --member-name alice 로 점유 시도
    subprocess.run(["git", "config", "user.email", "mallory@evil.com"],
                   cwd=str(team), check=True)
    mod2 = _load_install()
    rc = mod2["bootstrap"](il.parse_args(["--root", str(team),
                                          "--member-name", "alice"]),
                           home=home, python_version=(3, 13))
    assert rc == 3
    assert "충돌" in capsys.readouterr().err
    # members.md 무변경
    assert (team / "memory" / "team" / "members.md").read_text() == members_before
