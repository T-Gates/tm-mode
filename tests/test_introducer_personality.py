"""도입자 config 의 team.greeting·team.farewell 기본값 — SPEC §4.4·부록 A.3.

도입자 경로(write_introducer_config)는 최소 config 에 시작멘트·끝맺음말 기본값을
team.greeting / team.farewell 로 기록한다. 팀원 경로는 config 무수정(읽기만).
멱등: 이미 유효한 config 가 있으면 덮어쓰지 않는다(사용자 커스텀 보존).
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def _load(team_root: Path) -> dict:
    return json.loads((team_root / "team.config.json").read_text(encoding="utf-8"))


def test_introducer_config_has_greeting_default(tmp_path):
    il.write_introducer_config(tmp_path, team_name="acme",
                               admin_contact="jane-doe")
    team = _load(tmp_path)["team"]
    assert "greeting" in team
    assert isinstance(team["greeting"], str) and team["greeting"]
    # 기본값은 팀 이름을 담는다(템플릿)
    assert "acme" in team["greeting"]


def test_introducer_config_has_farewell_default(tmp_path):
    il.write_introducer_config(tmp_path, team_name="acme",
                               admin_contact="jane-doe")
    team = _load(tmp_path)["team"]
    assert "farewell" in team
    assert isinstance(team["farewell"], str) and team["farewell"]
    assert "acme" in team["farewell"]


def test_introducer_config_greeting_farewell_roundtrip_with_engine(tmp_path):
    """도입자 config 의 기본값을 엔진이 그대로 읽어 출력 가능해야 한다."""
    il.write_introducer_config(tmp_path, team_name="acme",
                               admin_contact="jane-doe")
    sys.path.insert(0, str(REPO / "infra"))
    import teammode as tm  # noqa: E402
    assert tm._read_team_field(tmp_path, "greeting") is not None
    assert tm._read_team_field(tmp_path, "farewell") is not None


def test_introducer_idempotent_preserves_custom_personality(tmp_path):
    """이미 유효 config(커스텀 greeting/farewell)면 덮어쓰지 않음(멱등)."""
    cfg = {
        "spec_version": "0.1",
        "team": {"name": "acme", "timezone": "Asia/Seoul",
                 "locale": "ko_KR",
                 "greeting": "내 커스텀 인사", "farewell": "내 커스텀 작별"},
        "services": {},
    }
    (tmp_path / "team.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    il.write_introducer_config(tmp_path, team_name="acme",
                               admin_contact="jane-doe")
    team = _load(tmp_path)["team"]
    assert team["greeting"] == "내 커스텀 인사"
    assert team["farewell"] == "내 커스텀 작별"


def test_member_path_does_not_modify_config(tmp_path):
    """팀원 경로 = 유효 config 존재 → write_introducer_config 가 무수정(읽기만)."""
    cfg = {
        "spec_version": "0.1",
        "team": {"name": "acme", "timezone": "Asia/Seoul", "locale": "ko_KR"},
        "services": {"linear": {"key": "x"}},
    }
    raw = json.dumps(cfg, ensure_ascii=False)
    (tmp_path / "team.config.json").write_text(raw, encoding="utf-8")
    il.write_introducer_config(tmp_path, team_name="acme",
                               admin_contact="someone")
    # 바이트 동일 — 팀원 config 무접촉
    assert (tmp_path / "team.config.json").read_text(encoding="utf-8") == raw
