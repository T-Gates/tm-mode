import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def test_parse_args_role_intent():
    assert il.parse_args([]).role_intent is None
    assert il.parse_args(["--role-intent", "introducer"]).role_intent == "introducer"
    assert il.parse_args(["--role-intent", "member"]).role_intent == "member"


def test_role_intent_does_not_collide_with_role():
    # --role 은 직함(job title), --role-intent 는 도입자/멤버 — 서로 독립.
    opts = il.parse_args(["--role", "developer", "--role-intent", "member"])
    assert opts.role == "developer"
    assert opts.role_intent == "member"


def test_detect_role_forced_overrides_heuristic(tmp_path):
    # 유효 config 가 있어도 forced=introducer 면 introducer.
    (tmp_path / "team.config.json").write_text(
        '{"spec_version":"0.1","team":{"name":"acme"}}', encoding="utf-8")
    assert il.detect_role(tmp_path) == "member"                    # 휴리스틱
    assert il.detect_role(tmp_path, forced="introducer") == "introducer"
    # config 가 없어도 forced=member 면 member.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert il.detect_role(empty) == "introducer"                   # 휴리스틱
    assert il.detect_role(empty, forced="member") == "member"


def test_detect_role_ignores_invalid_forced(tmp_path):
    # 잘못된 forced 값은 무시하고 휴리스틱으로.
    assert il.detect_role(tmp_path, forced="garbage") == "introducer"
    assert il.detect_role(tmp_path, forced=None) == "introducer"
