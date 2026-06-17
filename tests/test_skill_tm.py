"""tm 스킬 conformance 테스트.

검증 목록:
  - infra/skills/base/tm/SKILL.md 존재
  - frontmatter(name · description) 파싱 유효
  - ON/OFF 절차 키워드 존재(엔진 동사 인터페이스 계약)
  - install_skills 가 tm 을 심링크 대상에 포함
  - uninstall 이 tm 심링크를 제거(소유 판정)

모든 테스트는 tmp_path 격리 — 실 ~/.claude/skills 무접촉.
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_MD = REPO / "infra" / "skills" / "base" / "tm" / "SKILL.md"

sys.path.insert(0, str(REPO / "infra"))

import runpy
_CLAUDE = runpy.run_path(
    str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
    run_name="__tm_conformance__",
)
ClaudeAdapter = _CLAUDE["Adapter"]


# ── SKILL.md 파일 자체 ──

def test_skill_md_exists():
    assert SKILL_MD.is_file(), "infra/skills/base/tm/SKILL.md 가 없다"


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


def test_frontmatter_name():
    fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    assert fm.get("name") == "tm", f"name 필드가 'tm' 이어야 한다. 실제: {fm.get('name')!r}"


def test_frontmatter_description_nonempty():
    fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    desc = fm.get("description", "")
    assert desc, "description 이 비어 있다"
    assert len(desc) > 10, "description 이 너무 짧다"


# ── ON/OFF 엔진 동사 계약 키워드 ──

@pytest.mark.parametrize("keyword", [
    "teammode.py on",
    "teammode.py off",
    "teammode.py pull",
    "teammode.py log",
    "teammode.py commit",
    "teammode.py context",
    "--root",
    "--install",
])
def test_skill_md_contains_keyword(keyword):
    text = SKILL_MD.read_text(encoding="utf-8")
    assert keyword in text, (
        f"SKILL.md 에 '{keyword}' 가 없다 — "
        f"ON/OFF 절차에서 해당 엔진 동사/플래그를 명시해야 한다"
    )


def test_skill_md_no_push():
    """tm 은 push 금지 — --push 플래그를 절차 명령으로 쓰지 않는다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    # commit 명령에 --push 플래그가 들어가면 안 됨
    import re
    # "teammode.py commit ... --push" 형태 탐지
    assert not re.search(r"teammode\.py commit[^\n]*--push", text), (
        "SKILL.md 의 commit 명령에 --push 가 포함되어 있다 — tm 은 push 금지"
    )


def test_skill_md_has_off_confirmation():
    """OFF 절차에 사용자 확인 단계가 있어야 한다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "확인" in text, "OFF 절차에 사용자 확인 요구가 없다"


# ── install_skills 포함 검증 ──

def _scaffold(tmp_path):
    """tmp 팀 루트 — 실 infra/skills/base 전체 복사."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "adapter.py",
        root / "infra" / "agents" / "claude" / "adapter.py",
    )
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "events.json",
        root / "infra" / "agents" / "claude" / "events.json",
    )
    shutil.copytree(
        REPO / "infra" / "skills" / "base",
        root / "infra" / "skills" / "base",
    )
    return root


def _claude_adapter(root, tmp_path):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),
        skills_dir=str(tmp_path / "claude-skills"),
    )


def test_tm_in_source_skills(tmp_path):
    """adapter._skill_sources() 가 tm 을 목록에 포함한다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    names = {s.name for s in a._skill_sources()}
    assert "tm" in names, f"_skill_sources 에 tm 이 없다. 실제 목록: {names}"


def test_install_skills_creates_tm_link(tmp_path):
    """install_skills 실행 후 tm 심링크가 생성된다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills()
    link = tmp_path / "claude-skills" / "tm"
    assert link.exists() or link.is_symlink(), "tm 심링크/복사본이 생성되지 않았다"
    assert (link / "SKILL.md").is_file(), "tm/SKILL.md 가 없다"


def test_install_skills_tm_points_to_source(tmp_path):
    """tm 심링크가 실제 소스 디렉토리를 가리킨다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills()
    link = tmp_path / "claude-skills" / "tm"
    if link.is_symlink():
        assert link.resolve() == (root / "infra" / "skills" / "base" / "tm").resolve()


def test_uninstall_removes_tm(tmp_path):
    """uninstall_skills 가 tm 심링크를 제거한다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills()
    a.uninstall_skills()
    assert not (tmp_path / "claude-skills" / "tm").exists(), (
        "uninstall 후 tm 이 남아 있다 — 소유 판정 오류"
    )
