"""처방 2 — tm-onboard personality 게이트 제거 (TDD).

체크표에서 '팀 personality' 행이 없어야 하고,
다음 단계 ② personality 조건 구절이 없어야 하며,
tm-customize 안내는 여전히 존재해야 한다.
personality_customized 판정 코드(teammode.py)는 건드리지 않는다.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ONBOARD = REPO / "infra" / "skills" / "base" / "tm-onboard" / "SKILL.md"


def _text():
    return ONBOARD.read_text(encoding="utf-8")


def test_no_personality_in_checklist():
    """체크표 행에 '팀 personality' 항목이 없어야 한다."""
    text = _text()
    # 체크표는 | 로 시작하는 행으로 구성됨
    table_rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    for row in table_rows:
        assert "팀 personality" not in row, (
            f"체크표에 '팀 personality' 행이 아직 남아 있다: {row!r}"
        )


def test_no_personality_customized_condition_in_next_steps():
    """다음 단계 목록에 personality_customized 조건 구절이 없어야 한다."""
    text = _text()
    assert "personality_customized" not in text, (
        "personality_customized 조건 구절이 SKILL.md에 남아 있다"
    )


def test_tm_customize_always_guided():
    """tm-customize 안내가 다음 단계에 여전히 존재해야 한다 (나중 합류 팀원을 위해)."""
    text = _text()
    assert "tm-customize" in text, "tm-customize 안내가 SKILL.md에서 사라졌다"


def test_next_steps_still_functional():
    """다음 단계 섹션이 여전히 존재하고 비어 있지 않다."""
    text = _text()
    assert "## 다음 단계" in text, "다음 단계 섹션이 없다"
