"""S7 — tm-connect 스킬 재작성 검증 (codex⑫ 해소).

검증 목록:
  1. SKILL.md 존재 + frontmatter 유효
  2. lint_skill_canonical 통과 (mcp__ · 제품명 직표기 0)
  3. 우선순위 판정 흐름 정합성:
     - ① 재사용 경로 키워드/문구 존재
     - ② 흡수 경로 (--check-mcp CLI 언급)
     - ③ 수제 경로
  4. 핸들러 생성 시 사람 확인 게이트 명시
  5. 역할 어휘만 (issues / chat / docs / calendar) 사용
  6. credentials.load() / credentials.store() 경유 토큰 저장 명시
  7. providers 팩 데이터 기반 안내 (하드코딩 금지 원칙 명시)
  8. lint_skill_canonical — tmp 격리 파일로도 통과 확인

모든 테스트는 tmp_path 격리 — 실 ~/.claude/skills 무접촉.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_MD = REPO / "infra" / "skills" / "base" / "tm-connect" / "SKILL.md"

sys.path.insert(0, str(REPO / "conformance"))
import check  # noqa: E402


# ──────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict:
    """--- ... --- 블록에서 key: value 단순 파싱."""
    fm: dict = {}
    if not text.startswith("---"):
        return fm
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# 1. 파일 존재 + frontmatter
# ──────────────────────────────────────────────────────────────────

def test_skill_md_exists():
    assert SKILL_MD.is_file(), "infra/skills/base/tm-connect/SKILL.md 가 없다"


def test_frontmatter_name():
    fm = _parse_frontmatter(_skill_text())
    assert fm.get("name") == "tm-connect", \
        f"name 필드가 'tm-connect' 이어야 한다. 실제: {fm.get('name')!r}"


def test_frontmatter_description_nonempty():
    fm = _parse_frontmatter(_skill_text())
    desc = fm.get("description", "")
    assert len(desc) > 10, "description 필드가 너무 짧거나 비어 있다"


# ──────────────────────────────────────────────────────────────────
# 2. lint_skill_canonical 통과 — mcp__ · 제품명 직표기 0
# ──────────────────────────────────────────────────────────────────

def test_lint_skill_canonical_passes():
    """실제 레포 루트를 대상으로 lint_skill_canonical 을 실행한다."""
    name, ok, detail = check.lint_skill_canonical(REPO)
    assert ok, f"lint_skill_canonical 실패: {detail}"


def test_no_mcp_double_underscore_in_skill():
    """SKILL.md 본문에 mcp__ 직표기가 없어야 한다 (codex⑫)."""
    text = _skill_text()
    hits = [
        f"line {i+1}: {line.rstrip()}"
        for i, line in enumerate(text.splitlines())
        if "mcp__" in line
    ]
    assert not hits, f"mcp__ 직표기 발견:\n" + "\n".join(hits)


def test_no_product_names_in_skill():
    """SKILL.md 본문에 제품명 직표기가 없어야 한다 (codex⑫).

    check.py 의 lint_skill_canonical 과 동일한 제품명 목록을 사용한다.
    """
    # providers 팩에서 제품명 수집 (check.py 와 동일 로직)
    products = check._provider_product_names(REPO)
    prod_re = re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(p) for p in sorted(products)) + r")(?![A-Za-z])",
        re.IGNORECASE,
    ) if products else None

    if prod_re is None:
        pytest.skip("providers/ 제품명이 없어 이 검사는 건너뜀")

    text = _skill_text()
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in prod_re.finditer(line):
            hits.append(f"line {i}: '{m.group(1)}' — {line.rstrip()}")

    assert not hits, f"제품명 직표기 발견:\n" + "\n".join(hits)


def test_lint_skill_canonical_with_tmp_isolation(tmp_path):
    """tmp 격리 환경에서도 lint_skill_canonical 이 통과한다.

    providers/ 팩이 없는 최소 환경 — mcp__ 거부만 동작하는 것을 확인.
    """
    # tmp providers 디렉토리 없이 SKILL.md 만 복사
    skill_dir = tmp_path / "infra" / "skills" / "base" / "tm-connect"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_text(), encoding="utf-8")
    # providers 없는 환경 (제품명 목록 비어 있음)
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok, f"tmp 격리 lint_skill_canonical 실패: {detail}"


# ──────────────────────────────────────────────────────────────────
# 3. 우선순위 판정 흐름 정합성
# ──────────────────────────────────────────────────────────────────

def test_reuse_path_mentioned():
    """재사용 경로 ①: handlers/<역할>.py 존재 시 재사용 강제 문구가 있어야 한다."""
    text = _skill_text()
    # 핸들러 존재 → 재사용 강제를 설명하는 키워드
    assert "재사용" in text, "재사용 경로 키워드가 없다"
    assert "handlers/" in text, "handlers/ 경로 언급이 없다"


def test_absorb_path_check_mcp_mentioned():
    """흡수 경로 ②: --check-mcp CLI 사용이 명시돼야 한다."""
    text = _skill_text()
    assert "--check-mcp" in text, "--check-mcp CLI 언급이 없다 (흡수 판정 수단)"


def test_handcraft_path_mentioned():
    """수제 경로 ③: API 파악 → 핸들러 생성 흐름이 언급돼야 한다."""
    text = _skill_text()
    # 수제 경로 관련 키워드
    assert "수제" in text or "신규 서비스" in text or "API 를 파악" in text, \
        "수제 경로 흐름이 언급되지 않았다"


def test_priority_order_in_skill():
    """판정 우선순위(재사용 > 흡수 > 수제)가 순서대로 언급돼야 한다."""
    text = _skill_text()
    pos_reuse = text.find("재사용")
    pos_absorb = text.find("흡수")
    pos_handcraft = text.find("수제")
    assert pos_reuse != -1, "재사용 언급 없음"
    assert pos_absorb != -1, "흡수 언급 없음"
    assert pos_handcraft != -1, "수제 언급 없음"
    assert pos_reuse < pos_absorb < pos_handcraft, \
        f"우선순위 순서가 잘못됨: 재사용@{pos_reuse}, 흡수@{pos_absorb}, 수제@{pos_handcraft}"


# ──────────────────────────────────────────────────────────────────
# 4. 핸들러 생성 시 사람 확인 게이트 명시
# ──────────────────────────────────────────────────────────────────

def test_human_gate_mentioned():
    """핸들러 생성 시 사람 확인 게이트가 명시돼야 한다."""
    text = _skill_text()
    # 사람 확인 게이트를 명시하는 키워드들
    has_gate = (
        "사람 확인" in text
        or "확인 게이트" in text
        or "사람의 명시적 확인" in text
    )
    assert has_gate, "핸들러 생성 시 사람 확인 게이트 문구가 없다"


def test_no_commit_without_human_approval():
    """커밋 전 사람 수락 순서가 명시돼야 한다."""
    text = _skill_text()
    # "확인 후: git commit" 또는 "수락 → 커밋" 같은 흐름
    has_commit_gate = (
        "git commit" in text
        and ("확인 후" in text or "수락" in text or "확인 게이트" in text)
    )
    assert has_commit_gate, "사람 확인 → 커밋 순서가 명시되지 않았다"


# ──────────────────────────────────────────────────────────────────
# 5. 역할 어휘 사용 확인
# ──────────────────────────────────────────────────────────────────

def test_role_vocabulary_present():
    """역할 어휘(issues / chat / docs / calendar)가 모두 존재해야 한다."""
    text = _skill_text()
    for role in ("issues", "chat", "docs", "calendar"):
        assert role in text, f"역할 어휘 '{role}' 가 SKILL.md 에 없다"


# ──────────────────────────────────────────────────────────────────
# 6. credentials.load() / credentials.store() 경유 명시
# ──────────────────────────────────────────────────────────────────

def test_credentials_store_mentioned():
    """토큰 저장 방법으로 credentials.store() 가 언급돼야 한다."""
    text = _skill_text()
    assert "credentials.store" in text, "credentials.store() 언급이 없다"


def test_credentials_load_mentioned():
    """핸들러에서 credentials.load() 경유를 명시해야 한다."""
    text = _skill_text()
    assert "credentials.load" in text, "credentials.load() 언급이 없다"


def test_no_plaintext_token_in_output():
    """토큰을 stdout·로그에 출력하지 않는다는 경고가 있어야 한다."""
    text = _skill_text()
    has_warning = (
        "평문 토큰" in text
        or "stdout" in text
        and "출력" in text
    )
    assert has_warning, "평문 토큰 노출 방지 경고가 없다"


# ──────────────────────────────────────────────────────────────────
# 7. providers 팩 데이터 기반 안내 원칙
# ──────────────────────────────────────────────────────────────────

def test_providers_pack_data_driven():
    """발급 안내는 providers 팩에서 읽어야 한다는 원칙이 명시돼야 한다."""
    text = _skill_text()
    assert "providers/" in text, "providers/ 팩 경로 언급이 없다"
    assert "token_guide" in text or "하드코딩" in text, \
        "providers 팩 데이터 기반 안내 원칙이 없다"


def test_auth_types_covered():
    """auth 타입(api_key / oauth / bot_token) 세 가지가 모두 언급돼야 한다."""
    text = _skill_text()
    for auth_type in ("api_key", "oauth", "bot_token"):
        assert auth_type in text, f"auth 타입 '{auth_type}' 언급이 없다"


# ──────────────────────────────────────────────────────────────────
# 8. 핸들러 생성 규칙 (토큰 리터럴 금지 명시)
# ──────────────────────────────────────────────────────────────────

def test_token_literal_prohibition_mentioned():
    """핸들러 코드에 토큰 리터럴 embed 금지가 명시돼야 한다."""
    text = _skill_text()
    has_prohibition = (
        "토큰 리터럴 금지" in text
        or "직접 embed" in text
        or ("토큰" in text and "금지" in text)
    )
    assert has_prohibition, "핸들러 코드 토큰 리터럴 금지 명시가 없다"


def test_handlers_are_valid_mentioned():
    """handlers_are_valid() 검증 함수가 언급돼야 한다."""
    text = _skill_text()
    assert "handlers_are_valid" in text, "handlers_are_valid() 언급이 없다"


# ──────────────────────────────────────────────────────────────────
# 9. 재배선 + 첫 가치 검증 흐름
# ──────────────────────────────────────────────────────────────────

def test_rewiring_mentioned():
    """재배선(install.py --yes) 단계가 명시돼야 한다."""
    text = _skill_text()
    assert "install.py" in text, "재배선용 install.py 언급이 없다"
    assert "--yes" in text, "install.py --yes 언급이 없다"


def test_first_value_dogfood_mentioned():
    """첫 가치 검증(도그푸딩) 체크포인트가 언급돼야 한다."""
    text = _skill_text()
    has_dogfood = (
        "도그푸딩" in text
        or "첫 가치" in text
        or "첫 도그푸딩" in text
    )
    assert has_dogfood, "첫 가치 검증(도그푸딩) 체크포인트가 없다"


# ──────────────────────────────────────────────────────────────────
# 10. 전체 lint 통과 (run_lint)
# ──────────────────────────────────────────────────────────────────

def test_run_lint_all_pass():
    """check.run_lint(REPO) 의 모든 검사가 통과해야 한다."""
    report = check.run_lint(REPO)
    failures = [(name, detail) for name, ok, detail in report.checks if not ok]
    assert not failures, \
        f"lint 실패 항목:\n" + "\n".join(f"  [{name}] {detail}" for name, detail in failures)
