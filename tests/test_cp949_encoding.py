"""Windows non-UTF8 stdout E2E through the real normalize IO shim."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
EMITTED = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "INDEX 완료 ✅ 🚀 한글",
    }
}


@pytest.fixture
def normalize_env(tmp_path: Path) -> Path:
    infra = tmp_path / "infra"
    hooks = infra / "hooks"
    agent_dir = infra / "agents" / "claude"
    hooks.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    source_agent = REPO / "infra" / "agents" / "claude"
    (agent_dir / "normalize.py").write_bytes(
        (source_agent / "normalize.py").read_bytes()
    )
    (agent_dir / "events.json").write_bytes(
        (source_agent / "events.json").read_bytes()
    )
    (infra / "io_encoding.py").write_bytes(
        (REPO / "infra" / "io_encoding.py").read_bytes()
    )
    emitted_bytes = json.dumps(EMITTED, ensure_ascii=False).encode("utf-8")
    (hooks / "emit-stub.py").write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        f"sys.stdout.buffer.write({emitted_bytes!r})\n",
        encoding="utf-8",
    )
    (hooks / "manifest.json").write_text(
        json.dumps([{"event": "SessionStart", "script": "emit-stub.py"}]),
        encoding="utf-8",
    )
    return tmp_path


def test_normalize_reemits_child_utf8_under_cp949_stdout(
    normalize_env: Path,
) -> None:
    env = {
        **os.environ,
        "PYTHONIOENCODING": "cp949:strict",
        "TEAMMODE_HOME": str(normalize_env),
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(normalize_env / "infra" / "agents" / "claude" / "normalize.py"),
            "emit-stub.py",
        ],
        input=json.dumps({"hook_event_name": "SessionStart"}).encode("ascii"),
        capture_output=True,
        cwd=normalize_env,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert json.loads(proc.stdout.decode("utf-8")) == EMITTED
