"""#51 PR-B — tm-import-memory 스킬 계약 테스트.

스킬은 산문이라 실행 검증 불가 — 계약 문구의 존재를 고정한다(드리프트 방지).
계약(이슈 #51 설계 확정): 외부 문서(docs 슬롯) 대량 import 전담 —
preview 단일 확인 게이트(=weight 일괄 승인), 상한 20페이지/깊이 2,
주제별 병합(~10파일), 저장은 엔진 memory write 경유(직접 Edit/Write 금지),
본문 `## 출처` 절, 신규 최상위 폴더는 route upsert 선행, 🔥 자동 제안 금지.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "infra" / "skills" / "core" / "tm-import-memory" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_exists_with_frontmatter():
    assert SKILL.is_file(), "core/tm-import-memory/SKILL.md 부재"
    text = _text()
    m = re.match(r"^---\nname: tm-import-memory\ndescription: (.+?)\n---\n", text, re.S)
    assert m, "frontmatter(name/description) 형식 위반"


def test_triggers_include_memory_upload():
    desc = _text().split("---")[1]
    for kw in ("메모리 업로드", "노션"):
        assert kw in desc, f"트리거에 {kw!r} 부재"


def test_contract_phrases_present():
    text = _text()
    for phrase in (
        "memory write",          # 저장은 엔진 동사 경유
        "직접 Edit/Write",       # 직접 쓰기 금지 명문
        "20페이지",              # 기본 페이지 상한
        "깊이 2",                # 기본 깊이 상한
        "preview",               # 확인 게이트
        "## 출처",               # 본문 출처 절
        "tm-connect",            # 미연결 시 안내
        "tm-manage-memory",      # 경계 명시
        "📎",                    # 기본 weight
        "route upsert",          # 신규 최상위 폴더 등재 경로
    ):
        assert phrase in text, f"계약 문구 {phrase!r} 부재"


def test_no_fire_weight_auto_proposal():
    """🔥 자동 제안 금지가 명문화되어 있는지."""
    text = _text()
    assert "🔥" in text
    assert "자동 제안 금지" in text


def test_fanout_subagent_boundaries():
    """fan-out 계약: 본문은 서브에이전트만, 서브는 파일을 직접 쓰지 않음."""
    text = _text()
    assert "서브에이전트" in text
    assert "파일을 직접 쓰지 않는다" in text
