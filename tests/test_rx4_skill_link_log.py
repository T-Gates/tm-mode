"""Skill-install summary boundary."""
from __future__ import annotations

import re
import runpy
import shutil
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_install_skills_reports_one_summary_with_actual_link_count(tmp_path):
    root = tmp_path / "team"
    agent_dir = root / "infra" / "agents" / "claude"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    shutil.copy(REPO / "infra" / "agents" / "claude" / "adapter.py", agent_dir)
    shutil.copy(REPO / "infra" / "agents" / "claude" / "events.json", agent_dir)
    shutil.copytree(
        REPO / "infra" / "skills" / "base", root / "infra" / "skills" / "base"
    )
    skills_dir = tmp_path / "skills"
    adapter_type = runpy.run_path(
        str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
        run_name="__skill_summary__",
    )["Adapter"]
    adapter = adapter_type(
        agent_dir=str(agent_dir),
        manifest_path=str(hooks_dir / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),
        skills_dir=str(skills_dir),
    )

    result = adapter.install_skills(layer="base")

    summary_lines = [line for line in result if line.startswith("[skill]")]
    assert len(summary_lines) == 1
    reported = int(re.search(r"\d+", summary_lines[0]).group())
    installed = sum(1 for path in skills_dir.iterdir() if path.exists())
    assert reported == installed
