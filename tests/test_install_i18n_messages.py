import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as _install  # noqa: E402


def test_done_message_localized():
    assert "Install complete" in _install._done_message({"locale": "en_US"})
    assert "설치 완료" in _install._done_message({"locale": "ko_KR"})


def test_done_message_defaults_to_en(monkeypatch):
    # locale 키 없거나 미지원 → en_US
    assert "Install complete" in _install._done_message({})
    assert "Install complete" in _install._done_message({"locale": "fr_FR"})
