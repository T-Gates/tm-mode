"""clone-and-go PR2 doc and skill contract anchor tests.

Contract: AGENTS.md first contact = install detection + bootstrap
(dry-run -> chat approval -> --yes -> Trust -> tm-onboard routing).
tm-onboard keeps the no-install contract and only adds one bootstrap route.
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
        assert token in text, f"AGENTS.md first-contact section is missing {token!r}"
    # No host writes before approval.
    assert "Before approval" in text and "must not write" in text
    # Installation-state detection must not use .teammode-active.
    assert ".teammode-active" in text and "not used for installation-state detection" in text


def test_tm_onboard_keeps_no_install_contract_with_routing():
    text = _read("infra/skills/base/tm-onboard/SKILL.md")
    # Keep the no-install contract.
    assert "Do not install or ask setup questions" in text
    assert "calling `install.py` directly" in text
    # Keep one bootstrap routing line.
    assert "AGENTS.md" in text and "bootstrap" in text
    # The skill itself must not install.
    assert "never runs installation in any case" in text


def test_install_docs_offer_clone_and_go_path():
    # Single bilingual README (2026-07-06): English body + Korean section below.
    text = _read("README.md")
    assert "clone" in text.lower()
    assert "set up this repo" in text and "셋업해줘" in text, "bilingual clone-and-go path is missing"
    install = _read("INSTALL.md")
    assert "셋업해줘" in install and "set this up" in install


def test_spec_entry_contract_updated():
    text = _read("docs/spec/onboarding.md")
    assert "clone-and-go" in text
    # The spec is English-first; the approval-gate anchor is now "chat approval".
    assert "chat approval" in text


def test_approval_dry_run_includes_yes_flag():
    """[codex P1] Approval-gate dry-run includes --yes; approved plan equals execution."""
    text = _read("AGENTS.md")
    assert "--dry-run --yes" in text, (
        "approval dry-run is missing --yes, so it would approve a non-install plan")
    assert "real-install basis" in text


def test_readme_no_stale_anchor():
    """Reject stale broken anchors; remaining self anchors must target real headings.

    The install section was consolidated into one top-level block, so stale
    introduction anchors must be gone and remaining self links must resolve.
    """
    import re
    text = _read("README.md")
    assert "#도입은-이-한-줄" not in text      # stale refactor residue
    assert "#도입--두-가지-길" not in text      # section anchor removed by consolidation
    # Remaining self-anchor links, if any, must resolve to real heading slugs.
    heading_slugs = set()
    for line in text.splitlines():
        m = re.match(r'#{1,6}\s+(.*)', line)
        if m:
            slug = re.sub(r'[^\w가-힣\s-]', '', m.group(1).lower()).strip()
            heading_slugs.add(re.sub(r'\s', '-', slug))
    for anchor in re.findall(r'\]\(#([^)]+)\)', text):
        assert anchor in heading_slugs, f"broken self anchor: #{anchor}"


def test_no_stale_cli_only_stop_contract():
    """[codex P2] Reject stale stop-only contracts without in-repo bootstrap routing."""
    for rel in ("infra/skills/base/tm-onboard/SKILL.md", "docs/spec/skills.md"):
        text = _read(rel)
        for line in text.splitlines():
            if ("멈춘다" in line or "멈춤" in line) and (
                    "init" in line and "join" in line):
                assert "AGENTS" in line or "bootstrap" in line, (
                    f"{rel} retains stale CLI-only stop contract: {line!r}")


def test_readme_agent_oneliner_entrypoint():
    """URL one-line entrypoint: copy-paste prompt plus For-AI-agents procedure."""
    text = _read("README.md")
    # Copy-paste line for giving this repo URL to an agent, in English and Korean.
    assert "Read https://github.com/T-Gates/tm-mode and set up tm-mode" in text
    assert "읽고 tm-mode 세팅해줘" in text
    # Deterministic agent procedure; the approval gate is the core contract.
    assert "## For AI agents" in text
    agent_sec = text[text.index("## For AI agents"):text.index("## Layout")]
    # [codex P1 regression lock] Agent procedure must not include executable curl|sh commands.
    # Mentioning curl in a prohibition is okay; the bootstrap contract is clone -> dry-run approval -> --yes.
    assert "curl -fsSL" not in agent_sec
    assert "--dry-run --yes" in agent_sec
    assert "without `--dry-run`" in agent_sec
    assert "--member-name" in agent_sec  # non-interactive member-name contract (P2)
    # Human curl one-liner remains in the install section.
    human_sec = text[:text.index("## For AI agents")]
    assert "sh -s -- join" in human_sec and "init" in human_sec


def test_agents_md_url_entry_routing():
    """AGENTS.md input-form routing covers product URL, team URL, and in-repo paths."""
    text = _read("AGENTS.md")
    assert "Input-form classification" in text
    assert "For AI agents" in text          # product URL delegates to README procedure
    assert "git clone" in text              # team URL clones, then bootstraps
