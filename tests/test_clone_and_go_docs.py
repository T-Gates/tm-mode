"""clone-and-go PR2 — 문서·스킬 계약 앵커 테스트 (토큰 최소화 — 문안 리팩터 내성).

계약: AGENTS.md 첫 접촉 = 설치 판정 + bootstrap(dry-run→대화 승인→--yes→Trust→
tm-onboard 라우팅). tm-onboard 는 설치 금지 계약 유지 + bootstrap 라우팅 1줄만.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_agents_md_first_contact_bootstrap_contract():
    text = _read("AGENTS.md")
    for token in ("--dry-run", "--yes", "--member-name", "tm-onboard",
                  "Trust", "team.config.json", "members.md"):
        assert token in text, f"AGENTS.md 첫 접촉에 {token!r} 부재"
    # 승인 전 무접촉 계약
    assert "승인 전에는" in text and "쓰지 않는다" in text
    # 설치 판정에 .teammode-active 를 쓰지 않는 계약
    assert ".teammode-active" in text and "설치 판정에 쓰지 않는다" in text


def test_tm_onboard_keeps_no_install_contract_with_routing():
    text = _read("infra/skills/base/tm-onboard/SKILL.md")
    # 설치 금지 계약 불변
    assert "설치·질문을 하지 않는다" in text
    assert "install.py` 직접 호출" in text or "install.py 직접 호출" in text
    # bootstrap 라우팅 1줄
    assert "AGENTS.md" in text and "bootstrap" in text
    # 스킬 자신은 설치 안 함 명시
    assert "설치를 실행하지 않는다" in text


def test_install_docs_offer_clone_and_go_path():
    for rel in ("README.md", "INSTALL.md"):
        text = _read(rel)
        assert "clone" in text.lower() and "셋업해줘" in text, f"{rel} clone-and-go 경로 부재"


def test_spec_entry_contract_updated():
    text = _read("docs/spec/onboarding.md")
    assert "clone-and-go" in text
    assert "대화 승인" in text


def test_approval_dry_run_includes_yes_flag():
    """[codex P1] 승인 게이트의 dry-run 은 --yes 동반 — 승인한 계획=실행 계약."""
    text = _read("AGENTS.md")
    assert "--dry-run --yes" in text, (
        "승인용 dry-run 에 --yes 부재 — 비실설치 계획을 승인시키는 결함")
    assert "실설치 기준" in text


def test_readme_no_stale_anchor():
    """[codex P3] 옛 앵커(#도입은-이-한-줄) 잔존 금지 + 새 앵커 참조."""
    text = _read("README.md")
    assert "#도입은-이-한-줄" not in text
    assert "#도입--두-가지-길" in text


def test_no_stale_cli_only_stop_contract():
    """[codex P2] tm-onboard·spec 에 '레포 안=bootstrap 라우팅' 없는 옛 멈춤 계약 잔존 금지."""
    for rel in ("infra/skills/base/tm-onboard/SKILL.md", "docs/spec/skills.md"):
        text = _read(rel)
        for line in text.splitlines():
            if ("멈춘다" in line or "멈춤" in line) and (
                    "init" in line and "join" in line):
                assert "AGENTS" in line or "bootstrap" in line, (
                    f"{rel} 옛 CLI-only 멈춤 계약 잔존: {line!r}")
