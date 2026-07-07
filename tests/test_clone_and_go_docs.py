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
    assert "Do not install or ask setup questions" in text
    assert "calling `install.py` directly" in text
    # bootstrap 라우팅 1줄
    assert "AGENTS.md" in text and "bootstrap" in text
    # 스킬 자신은 설치 안 함 명시
    assert "never runs installation in any case" in text


def test_install_docs_offer_clone_and_go_path():
    # 단일 이중언어 README(2026-07-06): en 본문 + 하단 한국어 절(홈 앵커 점프)
    text = _read("README.md")
    assert "clone" in text.lower()
    assert "set up this repo" in text and "셋업해줘" in text, "양어 clone-and-go 경로 부재"
    assert "셋업해줘" in _read("INSTALL.md")


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
    """옛 깨진 앵커 잔존 금지 + 남아있는 self 앵커 링크는 실제 헤딩을 가리킨다.

    설치 절을 상단 단일 '## 설치' 블록으로 통합하며 self 앵커 참조(#도입…)는
    제거됨 — 옛 깨진 앵커가 없고, 잔존 self 링크가 있으면 실헤딩과 일치함을 검증.
    """
    import re
    text = _read("README.md")
    assert "#도입은-이-한-줄" not in text      # 옛 리팩터 잔재
    assert "#도입--두-가지-길" not in text      # 통합으로 사라진 섹션 앵커
    # 남아있는 self 앵커 링크(있다면) 는 실제 헤딩 slug 로 해석돼야 한다
    heading_slugs = set()
    for line in text.splitlines():
        m = re.match(r'#{1,6}\s+(.*)', line)
        if m:
            slug = re.sub(r'[^\w가-힣\s-]', '', m.group(1).lower()).strip()
            heading_slugs.add(re.sub(r'\s', '-', slug))
    for anchor in re.findall(r'\]\(#([^)]+)\)', text):
        assert anchor in heading_slugs, f"깨진 self 앵커: #{anchor}"


def test_no_stale_cli_only_stop_contract():
    """[codex P2] tm-onboard·spec 에 '레포 안=bootstrap 라우팅' 없는 옛 멈춤 계약 잔존 금지."""
    for rel in ("infra/skills/base/tm-onboard/SKILL.md", "docs/spec/skills.md"):
        text = _read(rel)
        for line in text.splitlines():
            if ("멈춘다" in line or "멈춤" in line) and (
                    "init" in line and "join" in line):
                assert "AGENTS" in line or "bootstrap" in line, (
                    f"{rel} 옛 CLI-only 멈춤 계약 잔존: {line!r}")


def test_readme_agent_oneliner_entrypoint():
    """URL 한 줄 진입점(2026-07-06 사용자 요구): 복붙 문구 + For-AI-agents 절차 존재."""
    text = _read("README.md")
    # 복붙 한 줄(에이전트에게 이 레포 URL 주며 세팅 요청) — en/ko 양쪽
    assert "Read https://github.com/T-Gates/tm-mode and set up tm-mode" in text
    assert "읽고 tm-mode 세팅해줘" in text
    # 에이전트용 결정적 절차 절 — 승인 게이트가 핵심 계약
    assert "## For AI agents" in text
    agent_sec = text[text.index("## For AI agents"):text.index("## Layout")]
    # [codex P1 회귀락] 에이전트 절차에 curl|sh **실행 명령** 금지(비-TTY = 무승인 설치).
    # ("curl 쓰지 마라" 금지 문구의 단어 언급은 허용) — bootstrap 동일 계약: clone → dry-run 승인 → --yes
    assert "curl -fsSL" not in agent_sec
    assert "--dry-run --yes" in agent_sec
    assert "without `--dry-run`" in agent_sec
    assert "--member-name" in agent_sec  # 비대화 멤버명 계약(P2)
    # 사람용 curl 원라이너는 상단 설치 절에 유지(join 리터럴 + init 언급)
    human_sec = text[:text.index("## For AI agents")]
    assert "sh -s -- join" in human_sec and "init" in human_sec


def test_agents_md_url_entry_routing():
    """AGENTS.md 입력 형태 판정: 제품 URL/팀 URL/레포 안 3분기 존재."""
    text = _read("AGENTS.md")
    assert "입력 형태 판정" in text
    assert "For AI agents" in text          # 제품 URL → README 절차 위임
    assert "git clone" in text              # 팀 URL → clone 후 bootstrap
