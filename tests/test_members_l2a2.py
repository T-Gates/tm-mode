"""L2-A2 — 멤버 역할 (config.members 배열 + 각자 upsert, 은수 결정 2026-06-16).

- members 스키마 검증: 정상 / name 누락 / 빈 배열 valid / 없음(None) valid / role 자유문자열
- 각자 upsert: 자기 엔트리만 추가/갱신, 타인 엔트리 무접촉, 멱등
- context 동사 role 표시(텍스트 "이름(role)" + --json role 필드)
- 기존 config(members 없는 0.1/0.2) 무회귀
- ⚠️ members 추가가 role 판정(config_is_valid)을 뒤집지 않음(A P0-1/P1-1 교훈)
- 이름충돌 정합: 타인 name 엔트리 무접촉

호스트 무접촉: 전부 tmp_path. 엔진 호출도 --root tmp 격리.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402

ENGINE = REPO / "infra" / "teammode.py"


def _write_cfg(team_root: Path, cfg: dict):
    (team_root / "team.config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_cfg(team_root: Path) -> dict:
    return json.loads((team_root / "team.config.json").read_text(encoding="utf-8"))


def _valid_member_cfg(members=None) -> dict:
    cfg = {"spec_version": "0.2", "team": {"name": "tgates"},
           "admin_contact": "alice", "services": {}}
    if members is not None:
        cfg["members"] = members
    return cfg


# ─────────────────────────── A2.1 members 스키마 검증 ───────────────────────────

def test_members_none_valid():
    # members 키 없음 → valid (기존 config 무회귀).
    assert il.members_are_valid(None)


def test_members_empty_list_valid():
    assert il.members_are_valid([])


def test_members_normal_valid():
    assert il.members_are_valid([{"name": "alice", "role": "developer"}])


def test_members_role_optional_valid():
    # role 없는 엔트리도 valid (role 은 선택).
    assert il.members_are_valid([{"name": "alice"}])


def test_members_role_free_string_valid():
    # 권장 어휘 밖 자유문자열도 허용(어휘 미강제).
    assert il.members_are_valid([{"name": "alice", "role": "데이터엔지니어"}])


def test_members_extra_keys_valid():
    # 확장 가능 object — 선언 안 한 키 허용.
    assert il.members_are_valid([{"name": "alice", "role": "pm", "email": "x@y"}])


def test_members_name_missing_invalid():
    assert not il.members_are_valid([{"role": "pm"}])


def test_members_name_empty_invalid():
    assert not il.members_are_valid([{"name": "", "role": "pm"}])


def test_members_name_traversal_invalid():
    # name 은 _validate_author 규약 — traversal/경로구분자 거부(footgun).
    assert not il.members_are_valid([{"name": "../etc"}])
    assert not il.members_are_valid([{"name": "-rf"}])


def test_members_role_empty_string_invalid():
    # role 있으면 비어있지 않은 str.
    assert not il.members_are_valid([{"name": "alice", "role": ""}])
    assert not il.members_are_valid([{"name": "alice", "role": 5}])


def test_members_not_list_invalid():
    assert not il.members_are_valid({"name": "alice"})
    assert not il.members_are_valid("alice")


def test_members_entry_not_dict_invalid():
    assert not il.members_are_valid(["alice"])


# ───────── ⚠️ A P0-1/P1-1 교훈: members 가 role 판정(config_is_valid)을 뒤집지 않음 ─────────

def test_config_is_valid_ignores_members_block():
    # members 가 깨졌어도(invalid) role 판정은 member 그대로 — 강등 없음.
    bad = _valid_member_cfg(members=[{"role": "pm"}])  # name 누락 = members invalid
    assert not il.members_are_valid(bad["members"])
    assert il.config_is_valid(bad)  # role 판정은 spec_version+team.name 만 본다


def test_config_is_valid_unaffected_by_missing_members():
    # 기존 config(members 키 없음) — 여전히 member.
    cfg = _valid_member_cfg()  # members 키 없음
    assert "members" not in cfg
    assert il.config_is_valid(cfg)


def test_existing_legacy_config_no_regression(tmp_path):
    # 0.1 레거시 config(members·services 키 없음) — detect_role member 유지.
    _write_cfg(tmp_path, {"spec_version": "0.1", "team": {"name": "acme"},
                          "admin_contact": "bob"})
    assert il.detect_role(tmp_path) == "member"
    # upsert 가 members 만 추가하고 다른 키는 무변경.
    before = _read_cfg(tmp_path)
    il.upsert_member_role(tmp_path, "bob", role="developer")
    after = _read_cfg(tmp_path)
    assert after["spec_version"] == before["spec_version"]
    assert after["team"] == before["team"]
    assert after["admin_contact"] == before["admin_contact"]
    assert after["members"] == [{"name": "bob", "role": "developer"}]
    assert il.detect_role(tmp_path) == "member"  # 여전히 member


# ─────────────────────────── A2.2 각자 upsert ───────────────────────────

def test_upsert_adds_own_entry(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg())
    res = il.upsert_member_role(tmp_path, "alice", role="developer")
    assert res["changed"]
    assert _read_cfg(tmp_path)["members"] == [{"name": "alice", "role": "developer"}]


def test_upsert_role_omitted_when_none(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg())
    il.upsert_member_role(tmp_path, "alice", role=None)
    assert _read_cfg(tmp_path)["members"] == [{"name": "alice"}]


def test_upsert_idempotent_same_name_role(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg())
    assert il.upsert_member_role(tmp_path, "alice", role="developer")["changed"]
    before = _read_cfg(tmp_path)
    # 재실행 — 무변경.
    assert il.upsert_member_role(tmp_path, "alice", role="developer")["changed"] is False
    assert _read_cfg(tmp_path) == before


def test_upsert_updates_own_role(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg([{"name": "alice", "role": "pm"}]))
    res = il.upsert_member_role(tmp_path, "alice", role="developer")
    assert res["changed"]
    assert _read_cfg(tmp_path)["members"] == [{"name": "alice", "role": "developer"}]


def test_upsert_does_not_touch_others(tmp_path):
    # ⚠️ 핵심 실증: 타인 name 엔트리는 절대 안 건드림(각자 upsert).
    _write_cfg(tmp_path, _valid_member_cfg([
        {"name": "bob", "role": "pm"},
        {"name": "carol", "role": "designer", "email": "c@x"},
    ]))
    il.upsert_member_role(tmp_path, "alice", role="developer")
    members = _read_cfg(tmp_path)["members"]
    # bob·carol 엔트리 원형 보존(순서·추가키 포함), alice 만 append.
    assert {"name": "bob", "role": "pm"} in members
    assert {"name": "carol", "role": "designer", "email": "c@x"} in members
    assert {"name": "alice", "role": "developer"} in members
    assert len(members) == 3


def test_upsert_own_update_preserves_others_and_extra_keys(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg([
        {"name": "bob", "role": "pm"},
        {"name": "alice", "role": "pm", "email": "a@x"},
    ]))
    il.upsert_member_role(tmp_path, "alice", role="developer")
    members = _read_cfg(tmp_path)["members"]
    # bob 무접촉.
    assert {"name": "bob", "role": "pm"} in members
    # alice 의 role 만 갱신, 추가키(email) 보존.
    alice = next(m for m in members if m["name"] == "alice")
    assert alice == {"name": "alice", "role": "developer", "email": "a@x"}


def test_upsert_noop_when_config_absent(tmp_path):
    # config 부재 → 무작업(role 판정·도입자 config 작성은 호출부 책임).
    res = il.upsert_member_role(tmp_path, "alice", role="developer")
    assert res["changed"] is False
    assert not (tmp_path / "team.config.json").exists()


def test_upsert_rejects_bad_name(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg())
    import pytest
    with pytest.raises(il.InvalidNameError):
        il.upsert_member_role(tmp_path, "../evil", role="x")


# ───── scaffold_memory 통합: 도입자도 자기것 upsert ─────

def test_scaffold_introducer_upserts_own_role(tmp_path):
    res = il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                             team_name="tgates", member_role="developer")
    assert res["role_upserted"]
    cfg = _read_cfg(tmp_path)
    assert {"name": "alice", "role": "developer"} in cfg["members"]


def test_scaffold_member_upserts_into_introducer_config(tmp_path):
    # 도입자가 먼저 config·자기 엔트리 작성.
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="tgates", member_role="pm")
    # 팀원이 같은 root 에 install(role=member) — 자기것만 추가, alice 무접촉.
    il.scaffold_memory(tmp_path, member_name="bob", role="member",
                       member_role="developer", team_name="tgates")
    members = _read_cfg(tmp_path)["members"]
    assert {"name": "alice", "role": "pm"} in members
    assert {"name": "bob", "role": "developer"} in members
    assert len(members) == 2


def test_scaffold_no_role_omits_role_key(tmp_path):
    il.scaffold_memory(tmp_path, member_name="alice", role="introducer",
                       team_name="tgates")  # member_role 미지정
    cfg = _read_cfg(tmp_path)
    assert {"name": "alice"} in cfg["members"]


# ─────────────────────────── A2.3 context role 표시 ───────────────────────────

def _run_engine(team_root: Path, *args):
    return subprocess.run(
        [sys.executable, str(ENGINE), *args, "--root", str(team_root)],
        capture_output=True, text=True, cwd=str(team_root))


def _write_log(team_root: Path, author: str, date: str, summary: str):
    d = team_root / "memory" / "team" / "sessions" / author
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date}.md").write_text(
        f"---\nauthor: {author}\ndate: {date}\nsummary: {summary}\n---\n본문\n",
        encoding="utf-8")


def test_context_text_shows_role(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg([{"name": "eunsu", "role": "developer"}]))
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    r = _run_engine(tmp_path, "context")
    assert r.returncode == 0
    assert "eunsu(developer)" in r.stdout


def test_context_json_has_role(tmp_path):
    _write_cfg(tmp_path, _valid_member_cfg([{"name": "eunsu", "role": "developer"}]))
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    r = _run_engine(tmp_path, "context", "--json")
    data = json.loads(r.stdout)
    eunsu = next(m for m in data["members"] if m["author"] == "eunsu")
    assert eunsu["role"] == "developer"


def test_context_member_without_config_role_shows_plain(tmp_path):
    # config.members 미등재 멤버 → role=None, "이름만" 표기(무회귀).
    _write_cfg(tmp_path, _valid_member_cfg([{"name": "eunsu", "role": "developer"}]))
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    _write_log(tmp_path, "ghost", "2026-06-13", "유령작업")  # config 미등재
    r = _run_engine(tmp_path, "context")
    assert "eunsu(developer)" in r.stdout
    # ghost 는 role 없이 이름만.
    assert "ghost [" in r.stdout
    assert "ghost(" not in r.stdout
    rj = json.loads(_run_engine(tmp_path, "context", "--json").stdout)
    ghost = next(m for m in rj["members"] if m["author"] == "ghost")
    assert ghost["role"] is None


def test_context_no_config_no_crash(tmp_path):
    # config 부재 — context 무크래시, role 전부 None.
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    r = _run_engine(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["members"][0]["role"] is None


def test_context_bad_members_block_no_crash(tmp_path):
    # members 가 손상(list 아님)이어도 context 는 무크래시(role 전부 None).
    _write_cfg(tmp_path, _valid_member_cfg(members="garbage"))
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    r = _run_engine(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["members"][0]["role"] is None


# ───── P2-1: role 개행/제어문자로 context 출력 줄 위조 차단(이중 방어) ─────

# context 텍스트 줄을 위조하도록 설계된 적대 role: 개행 뒤에 가짜 멤버 라인을 심는다.
_FORGED_ROLE = "dev]\n- FAKE [2099-01-01] summary: pwned"


def test_members_role_newline_invalid():
    # 개행 포함 role → invalid(members_are_valid 거부).
    assert not il.members_are_valid([{"name": "alice", "role": _FORGED_ROLE}])


def test_members_role_control_char_invalid():
    # 캐리지리턴·널·탭 등 제어문자 role → invalid.
    assert not il.members_are_valid([{"name": "alice", "role": "dev\rpm"}])
    assert not il.members_are_valid([{"name": "alice", "role": "dev\x00pm"}])
    assert not il.members_are_valid([{"name": "alice", "role": "dev\tpm"}])


def test_members_role_korean_with_space_still_valid():
    # 정상 role(한글·공백 포함)은 control char 가 없으므로 계속 valid(어휘 미강제 유지).
    assert il.members_are_valid([{"name": "alice", "role": "데이터 엔지니어"}])
    assert il.members_are_valid([{"name": "bob", "role": "데이터엔지니어"}])


def test_upsert_rejects_newline_role(tmp_path):
    # upsert 에서도 개행·제어문자 role 거부(config 진입 차단).
    _write_cfg(tmp_path, _valid_member_cfg())
    import pytest
    with pytest.raises(il.InvalidNameError):
        il.upsert_member_role(tmp_path, "alice", role=_FORGED_ROLE)
    # config.members 에 alice 가 들어가지 않았다(거부 후 무기록).
    assert "members" not in _read_cfg(tmp_path) or not _read_cfg(tmp_path)["members"]


def test_upsert_accepts_korean_role_with_space(tmp_path):
    # 정상 한글+공백 role 은 upsert 통과(자유문자열 정신 유지).
    _write_cfg(tmp_path, _valid_member_cfg())
    res = il.upsert_member_role(tmp_path, "alice", role="데이터 엔지니어")
    assert res["changed"]
    assert _read_cfg(tmp_path)["members"] == [
        {"name": "alice", "role": "데이터 엔지니어"}]


def test_context_text_no_forged_line_injection(tmp_path):
    # 방어 이중화 실증: config.members 에 개행 role 이 (검증 우회로) 박혀 있어도
    # context 텍스트 출력에 가짜 멤버 라인이 안 생긴다(role 한 줄 새니타이즈).
    # 검증을 우회한 상황을 모사하기 위해 config 를 직접 손으로 쓴다.
    _write_cfg(tmp_path, _valid_member_cfg([{"name": "eunsu", "role": _FORGED_ROLE}]))
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    r = _run_engine(tmp_path, "context")
    assert r.returncode == 0
    # 위조 라인(`- FAKE [...] summary: pwned`)이 한 줄로 떨어져 나오지 않는다.
    for line in r.stdout.splitlines():
        assert not line.startswith("- FAKE")
    assert "summary: pwned" not in r.stdout or "\n- FAKE" not in r.stdout
    # eunsu 라인은 여전히 하나로 유지(개행이 공백으로 치환됨).
    eunsu_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("- eunsu(")]
    assert len(eunsu_lines) == 1


def test_context_text_korean_role_with_space_renders(tmp_path):
    # 정상 한글+공백 role 은 context 텍스트에 그대로 표기된다.
    _write_cfg(tmp_path, _valid_member_cfg([{"name": "eunsu", "role": "데이터 엔지니어"}]))
    _write_log(tmp_path, "eunsu", "2026-06-13", "작업")
    r = _run_engine(tmp_path, "context")
    assert r.returncode == 0
    assert "eunsu(데이터 엔지니어)" in r.stdout
