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
    """셋업 직후 '다음 한 걸음'(tm on) 안내가 살아 있어야 한다.

    새 계약에서 옛 '## 다음 단계' 섹션은 제거됐고(설치·메뉴 로직은 CLI/각 스킬 몫),
    다음 한 걸음은 §② 가치 전달 안의 'tm on' 권유로 흡수됐다 —
    헤더 문자열이 아니라 그 의도(다음 행동 안내)를 검증한다.
    """
    text = _text()
    assert "tm on" in text, "다음 한 걸음(tm on) 안내가 SKILL.md에서 사라졌다"
