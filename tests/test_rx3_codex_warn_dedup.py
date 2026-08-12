"""Codex adapter warning aggregation boundary."""
from __future__ import annotations

import json
import runpy
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_unsupported_hook_warnings_are_aggregated_by_script(tmp_path, capsys):
    root = tmp_path / "team"
    agent_dir = root / "infra" / "agents" / "codex"
    hooks_dir = root / "infra" / "hooks"
    agent_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)
    (agent_dir / "events.json").write_text(
        json.dumps(
            {
                "agent": "codex",
                "config_file": "~/.codex/config.toml",
                "events": {"PreToolUse": None},
                "actions": {"file_edit": "apply_patch"},
                "mcp_tool_format": "{server}.{tool}",
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "normalize.py").write_text("# stub\n", encoding="utf-8")
    manifest = [
        {
            "event": "PreToolUse",
            "match": {"mcp": {"server": f"service-{index}", "tool": "act"}},
            "script": "confirm-action.py",
            "fallback": "drop",
            "enforcement": "block",
        }
        for index in range(7)
    ]
    (hooks_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    adapter_type = runpy.run_path(
        str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
        run_name="__warn_aggregation__",
    )["Adapter"]
    adapter = adapter_type(
        agent_dir=str(agent_dir),
        manifest_path=str(hooks_dir / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python="python3",
        team_root=str(root),
    )

    adapter.sync(mode="on")

    warning_lines = [
        line for line in capsys.readouterr().out.splitlines() if "[warn]" in line
    ]
    assert len(warning_lines) == 1
    assert "confirm-action.py" in warning_lines[0]
    assert "7" in warning_lines[0]
