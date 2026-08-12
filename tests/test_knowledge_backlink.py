"""Memory-write backlink integration boundary."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"
DATE = "2026-06-25"


def _run(root: Path, *argv: str):
    return subprocess.run(
        [sys.executable, str(ENGINE), *argv, "--root", str(root)],
        capture_output=True,
        text=True,
    )


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(root)],
                   check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def test_memory_write_commits_document_index_and_session_backlink(tmp_path):
    _init_git(tmp_path)

    result = _run(
        tmp_path,
        "memory",
        "write",
        "--folder",
        "team",
        "--filename",
        "rule.md",
        "--content",
        "team rule",
        "--author",
        "bob",
        "--weight",
        "📌",
        "--date",
        DATE,
    )
    assert result.returncode == 0, result.stderr

    changed = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "memory/team/rule.md" in changed
    assert "memory/team/INDEX.md" in changed
    assert f"memory/team/sessions/bob/{DATE}.md" in changed

    document = (tmp_path / "memory" / "team" / "rule.md").read_text(
        encoding="utf-8"
    )
    assert f"session: team/sessions/bob/{DATE}.md" in document

    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""
