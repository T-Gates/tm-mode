"""처방 4 — install_skills 심링크 로그 요약 (TDD).

install_skills()가 N개 심링크를 생성할 때
개별 "[skill] X 심링크" N줄 대신 에이전트별 1줄 요약("[skill] N개 심링크 (claude)")을
반환해야 한다.
"""
import json
import os
import runpy
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

_CLAUDE = runpy.run_path(
    str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
    run_name="__rx4_skill_log__",
)
ClaudeAdapter = _CLAUDE["Adapter"]


def _scaffold(tmp_path):
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "adapter.py",
        root / "infra" / "agents" / "claude" / "adapter.py",
    )
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "events.json",
        root / "infra" / "agents" / "claude" / "events.json",
    )
    shutil.copytree(REPO / "infra" / "skills" / "base",
                    root / "infra" / "skills" / "base")
    shutil.copytree(REPO / "infra" / "skills" / "core",
                    root / "infra" / "skills" / "core")
    (root / "infra" / "skills" / "util").mkdir(parents=True, exist_ok=True)
    return root


def _adapter(root, tmp_path):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),
        skills_dir=str(tmp_path / "claude-skills"),
    )


def test_install_skills_base_returns_summary_not_per_line(tmp_path):
    """install_skills(layer='base') 반환값이 개별 [skill] 줄이 아니라 요약 1줄이어야 한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    result = a.install_skills(layer="base")

    # base layer에 스킬이 여러 개 있어야 의미 있는 테스트
    skill_count = len(list((root / "infra" / "skills" / "base").iterdir()))
    assert skill_count > 1, "base 레이어에 스킬이 2개 이상 있어야 한다"

    # [skill] 로 시작하는 줄이 1줄이어야 함 (요약)
    skill_lines = [l for l in result if l.startswith("[skill]")]
    assert len(skill_lines) == 1, (
        f"[skill] 줄이 1줄(요약)이어야 하는데 {len(skill_lines)}줄 반환됨: {result}"
    )

    # 요약 줄에 개수와 에이전트명이 포함되어야 함
    summary = skill_lines[0]
    assert "claude" in summary, f"요약에 'claude'가 없다: {summary!r}"
    # 숫자(N개)가 포함되어야 함
    import re
    assert re.search(r"\d+", summary), f"요약에 숫자가 없다: {summary!r}"


def test_install_skills_core_returns_summary_not_per_line(tmp_path):
    """install_skills(layer='core') 반환값도 요약 1줄이어야 한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    result = a.install_skills(layer="core")

    skill_lines = [l for l in result if l.startswith("[skill]")]
    assert len(skill_lines) == 1, (
        f"core [skill] 줄이 1줄이어야 하는데 {len(skill_lines)}줄: {result}"
    )
    summary = skill_lines[0]
    assert "claude" in summary
    import re
    assert re.search(r"\d+", summary)


def test_install_skills_idempotent_returns_ok(tmp_path):
    """2번째 install_skills는 여전히 [ok] 변경 없음을 반환해야 한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="base")
    again = a.install_skills(layer="base")
    assert again == ["[ok] 변경 없음"]


def test_install_skills_summary_count_matches_actual(tmp_path):
    """요약 줄의 숫자가 실제로 생성된 심링크 수와 일치해야 한다."""
    import re
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    result = a.install_skills(layer="base")

    skill_lines = [l for l in result if l.startswith("[skill]")]
    assert skill_lines, "skill 요약이 없다"
    summary = skill_lines[0]
    match = re.search(r"(\d+)", summary)
    assert match, f"요약에 숫자 없음: {summary!r}"
    reported_count = int(match.group(1))

    # 실제 skills_dir의 심링크 수
    skills_dir = tmp_path / "claude-skills"
    actual_links = [p for p in skills_dir.iterdir()
                    if p.is_symlink() or p.is_dir()]
    assert reported_count == len(actual_links), (
        f"요약 숫자({reported_count}) != 실제 심링크 수({len(actual_links)})"
    )
