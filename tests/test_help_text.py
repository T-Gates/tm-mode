import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as _install  # noqa: E402


def test_help_documents_new_flags():
    assert "--team-name" in _install._HELP_TEXT
    assert "--role-intent" in _install._HELP_TEXT
