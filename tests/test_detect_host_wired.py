import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as _install  # noqa: E402


def test_detect_populates_locale_timezone(monkeypatch, tmp_path):
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("TZ", "America/New_York")
    # tmp_path 는 git repo 가 아니어서 _git 호출은 None 반환(비치명).
    det = _install._detect(tmp_path, tmp_path)
    assert det["locale"] == "en_US"
    assert det["timezone"] == "America/New_York"
