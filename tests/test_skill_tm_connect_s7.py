"""S7 — tm-connect 스킬 (L2 등록기, A안) 정합성 검증.

2026-06-25 L2 재설계: tm-connect 는 역할 슬롯에 공식 벤더 MCP를 *연결(등록)*만
하는 등록기다. 동작(이슈 생성·일정 추가)은 AI가 등록된 벤더 MCP 도구를 직접 호출한다.
핸들러 생성·role_server 프록시·역할 추상화 동사·"재사용>흡수>수제" 우선순위 판정은 폐기됐다.
기준: docs/archive/2026-06-25-L2-redesign.md, docs/spec/skills.md §5.4.1, internals.md §2.8.

검증 목록:
  1. SKILL.md 존재 + frontmatter 유효
  2. lint_skill_canonical 통과 (mcp__ · 제품명 직표기 0)
  3. 등록기 흐름 존재: 슬롯 선택 → provider(config 우선/첫등록자 선택) → MCP 마련
     → 토큰/금고 → config 기록 → install-mcp alias 등록 → AI 직접 호출
  4. 폐기 잔재 부재: handlers_are_valid · role_server · issues_create 류 추상 동사
     · "재사용>흡수>수제" 우선순위 판정 · 동작 CLI 래퍼
  5. 역할 어휘만 (issues / chat / docs / calendar)
  6. credentials.store() 경유 토큰 저장 + 평문 노출 금지 경고
  7. providers 팩 데이터 기반 안내 (하드코딩 금지 원칙)
  8. lint_skill_canonical — tmp 격리 파일로도 통과

모든 테스트는 tmp_path 격리 — 실 ~/.claude/skills 무접촉.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_MD = REPO / "infra" / "skills" / "core" / "tm-connect" / "SKILL.md"

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
    assert SKILL_MD.is_file(), "infra/skills/core/tm-connect/SKILL.md 가 없다"


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
    """SKILL.md 본문에 mcp__ 직표기가 없어야 한다 (등록 alias 는 역할어휘로 서술)."""
    text = _skill_text()
    hits = [
        f"line {i+1}: {line.rstrip()}"
        for i, line in enumerate(text.splitlines())
        if "mcp__" in line
    ]
    assert not hits, "mcp__ 직표기 발견:\n" + "\n".join(hits)


def test_no_product_names_in_skill():
    """SKILL.md 본문에 제품명 직표기가 없어야 한다.

    check.py 의 lint_skill_canonical 과 동일한 제품명 목록을 사용한다.
    """
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

    assert not hits, "제품명 직표기 발견:\n" + "\n".join(hits)


def test_lint_skill_canonical_with_tmp_isolation(tmp_path):
    """tmp 격리 환경에서도 lint_skill_canonical 이 통과한다.

    providers/ 팩이 없는 최소 환경 — mcp__ 거부만 동작하는 것을 확인.
    """
    skill_dir = tmp_path / "infra" / "skills" / "core" / "tm-connect"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_text(), encoding="utf-8")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok, f"tmp 격리 lint_skill_canonical 실패: {detail}"


# ──────────────────────────────────────────────────────────────────
# 3. 등록기 흐름 존재
# ──────────────────────────────────────────────────────────────────

def test_slot_selection_step_present():
    """슬롯 선택(issues/chat/docs/calendar)이 첫 단계로 명시돼야 한다."""
    text = _skill_text()
    assert "슬롯" in text, "슬롯 어휘가 없다"
    # 역할 슬롯 4종이 모두 언급
    for role in ("issues", "chat", "docs", "calendar"):
        assert role in text, f"역할 슬롯 '{role}' 가 없다"


def test_provider_from_config_for_followers():
    """후속 멤버는 config 의 services.<역할>.provider 를 읽고 재선택하지 않는다."""
    text = _skill_text()
    assert "services" in text and "provider" in text, \
        "config services.<역할>.provider 참조가 없다"
    assert "재선택" in text, "후속 멤버 provider 재선택 금지 서술이 없다"


def test_first_registrant_chooses_provider():
    """첫 등록자는 사람이 provider 를 고르고, 공식 식별이 보안 게이트다."""
    text = _skill_text()
    assert "첫 등록자" in text or "첫등록자" in text, "첫 등록자 경로 언급이 없다"
    assert "보안 게이트" in text, "공식 식별=보안 게이트 서술이 없다"


def test_mcp_provision_official_first():
    """공식 벤더 MCP를 마련하되 공식 우선, 없으면 자작 흐름이 있어야 한다."""
    text = _skill_text()
    assert "공식" in text, "공식 MCP 우선 서술이 없다"
    assert "자작" in text, "공식 없을 때 자작 대안 서술이 없다"
    assert "infra/mcp/" in text, "마련한 MCP 보관 위치(infra/mcp/<provider>/) 언급이 없다"


def test_self_authored_mcp_not_role_abstraction():
    """자작 MCP는 역할 추상화가 아니라 그 벤더 전용 MCP라는 원칙이 명시돼야 한다."""
    text = _skill_text()
    assert "벤더 전용" in text or "벤더 API" in text, \
        "자작 MCP=벤더 전용 원칙이 없다"
    assert "역할 추상화" in text, "역할 추상화 폐기 경고가 없다"


def test_config_push_team_declaration():
    """첫 등록자가 config 기록 + GitHub push 로 팀 공유 선언하는 단계가 있어야 한다."""
    text = _skill_text()
    assert "push" in text, "GitHub push(팀 공유 선언) 언급이 없다"
    assert "team.config.json" in text, "team.config.json 기록 언급이 없다"


def test_install_mcp_alias_registration():
    """install-mcp(install.py --yes) 로 벤더 MCP alias 를 등록하는 단계가 있어야 한다."""
    text = _skill_text()
    assert "install.py" in text, "재배선용 install.py 언급이 없다"
    assert "--yes" in text, "install.py --yes 언급이 없다"
    assert "alias" in text, "MCP alias 등록 언급이 없다"


def test_action_is_ai_direct_call():
    """동작은 AI가 등록된 벤더 MCP 도구를 직접 호출한다는 A안 서술이 있어야 한다."""
    text = _skill_text()
    assert "직접 호출" in text, "AI 직접 호출(A안) 서술이 없다"
    assert "벤더 MCP" in text, "벤더 MCP 도구 언급이 없다"


# ──────────────────────────────────────────────────────────────────
# 4. 폐기 잔재 부재 (B안/구방식)
# ──────────────────────────────────────────────────────────────────

def _is_abolish_context(line: str) -> bool:
    """그 줄이 '폐기/안티예시' 문맥인지 — 잔재 어휘가 여기서만 등장하면 허용."""
    markers = ("폐기", "부활", "아니다", "않는다", "만들지", "금지", "안티예시",
               "Common Mistakes")
    return any(m in line for m in markers)


def test_no_priority_judgment():
    """'재사용 > 흡수 > 수제' 우선순위 판정이 *현행 절차*로 남아 있으면 안 된다.

    archive 기준: 우선순위 판정(③경로 전부) 폐기. '흡수'·'수제' 어휘가 등장한다면
    오직 '폐기됨' 문맥에서만 허용된다(절차로서는 0).
    """
    text = _skill_text()
    lines = text.splitlines()
    # '수제' 는 우선순위 판정 전용 어휘 — 폐기 문맥 외 등장 0.
    for i, line in enumerate(lines, 1):
        if "수제" in line and not _is_abolish_context(line):
            pytest.fail(f"line {i}: '수제' 가 폐기 문맥 밖에서 등장(우선순위 판정 잔재): {line.strip()}")
    # '재사용 > 흡수 > 수제' 같은 우선순위 명시 표현은 폐기 선언으로만 등장한다.
    for i, line in enumerate(lines, 1):
        if ("재사용 > 흡수" in line or "재사용>흡수" in line) and not _is_abolish_context(line):
            pytest.fail(f"line {i}: 우선순위 판정(재사용>흡수>수제)이 절차로 남아 있다: {line.strip()}")


def test_no_handlers_are_valid():
    """handlers_are_valid() 핸들러 검증 단계가 제거돼야 한다."""
    text = _skill_text()
    assert "handlers_are_valid" not in text, \
        "handlers_are_valid (핸들러 검증, 폐기됨)가 남아 있다"


def test_no_handler_generation():
    """핸들러 생성(handlers/<역할>.py 만들기) 절차가 제거돼야 한다.

    '핸들러 생성' 어휘는 폐기 선언 문맥에서만 허용(절차로서는 0).
    """
    text = _skill_text()
    assert "handlers/<역할>.py" not in text, "핸들러 파일 생성 절차가 남아 있다"
    for i, line in enumerate(text.splitlines(), 1):
        if "핸들러 생성" in line and not _is_abolish_context(line):
            pytest.fail(f"line {i}: '핸들러 생성' 이 폐기 문맥 밖에서 등장: {line.strip()}")


def test_no_role_server():
    """role_server 프록시가 *현행 절차*로 남아 있으면 안 된다.

    'role_server' 어휘는 폐기 선언 문맥에서만 허용(절차로서는 0).
    """
    text = _skill_text()
    for i, line in enumerate(text.splitlines(), 1):
        if "role_server" in line and not _is_abolish_context(line):
            pytest.fail(f"line {i}: 'role_server' 가 폐기 문맥 밖에서 등장(프록시 잔재): {line.strip()}")


def test_no_role_abstraction_verb():
    """issues_create 같은 역할 추상화 동사를 tm-connect 가 만든다는 서술이 없어야 한다."""
    text = _skill_text()
    for verb in ("issues_create", "chat_create", "docs_create", "calendar_create"):
        assert verb not in text, f"역할 추상화 동사 '{verb}' (폐기됨)가 남아 있다"


def test_no_action_cli_wrapper():
    """동작 CLI/래퍼(tm-issues create 류)를 만들지 않는다는 게 지켜져야 한다.

    'tm-issues create' 같은 동작 명령이 *지시*로 등장하면 A안 위반.
    안티예시(만들지 말라)로만 등장하는 것은 허용.
    """
    text = _skill_text()
    # 동작 CLI 래퍼를 만들라는 지시가 없어야 한다 — 모든 등장은 '금지/폐기' 문맥이어야 한다.
    for i, line in enumerate(text.splitlines(), 1):
        if "tm-issues create" in line or "tm-calendar add" in line:
            assert ("만들지" in line or "않는" in line or "폐기" in line
                    or "부활" in line or "금지" in line or "래퍼" in line), \
                f"line {i}: 동작 CLI 래퍼가 금지 문맥 없이 등장 — A안 위반: {line.strip()}"


# ──────────────────────────────────────────────────────────────────
# 5. 역할 어휘 사용 확인
# ──────────────────────────────────────────────────────────────────

def test_role_vocabulary_present():
    """역할 어휘(issues / chat / docs / calendar)가 모두 존재해야 한다."""
    text = _skill_text()
    for role in ("issues", "chat", "docs", "calendar"):
        assert role in text, f"역할 어휘 '{role}' 가 SKILL.md 에 없다"


# ──────────────────────────────────────────────────────────────────
# 6. credentials 저장 + 평문 노출 금지
# ──────────────────────────────────────────────────────────────────

def test_credentials_store_mentioned():
    """토큰 저장 방법으로 credentials.store() 가 언급돼야 한다."""
    text = _skill_text()
    assert "credentials.store" in text, "credentials.store() 언급이 없다"


def test_no_plaintext_token_in_output():
    """토큰을 stdout·로그에 출력하지 않는다는 경고가 있어야 한다."""
    text = _skill_text()
    has_warning = (
        "평문 토큰" in text
        or ("stdout" in text and "출력" in text)
    )
    assert has_warning, "평문 토큰 노출 방지 경고가 없다"


def test_each_member_inputs_token():
    """팀 scope 도 각 멤버가 각자 토큰을 입력한다(자동공유 없음)는 서술이 있어야 한다."""
    text = _skill_text()
    assert "각자 입력" in text or "각자 1회" in text, "각자 입력 원칙이 없다"
    assert "자동공유" in text, "팀 토큰 자동공유 부재 서술이 없다"


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


def test_resource_fields_instance_values():
    """resource_fields 로 인스턴스 값(dbid 등)을 config 에 채우는 서술이 있어야 한다."""
    text = _skill_text()
    assert "resource_fields" in text, "resource_fields 언급이 없다"


# ──────────────────────────────────────────────────────────────────
# 8. 전체 lint 통과 (run_lint)
# ──────────────────────────────────────────────────────────────────

def test_run_lint_all_pass():
    """check.run_lint(REPO) 의 모든 검사가 통과해야 한다."""
    report = check.run_lint(REPO)
    failures = [(name, detail) for name, ok, detail in report.checks if not ok]
    assert not failures, \
        "lint 실패 항목:\n" + "\n".join(f"  [{name}] {detail}" for name, detail in failures)
