import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import i18n as _i18n  # noqa: E402


def test_resolve_lang():
    assert _i18n.resolve_lang("ko_KR") == "ko_KR"
    assert _i18n.resolve_lang("en_US.UTF-8") == "en_US"
    assert _i18n.resolve_lang("ko") == "ko_KR"        # 언어만 매칭
    assert _i18n.resolve_lang("fr_FR") == "en_US"     # 미지원 → 폴백
    assert _i18n.resolve_lang(None) == "en_US"


def test_t_localizes():
    assert "Install complete" in _i18n.t("done_installed", "en_US")
    assert "설치 완료" in _i18n.t("done_installed", "ko_KR")


def test_t_formats():
    assert "members=3" in _i18n.t("verify_ok", "en_US", n=3)
    assert "members=3" in _i18n.t("verify_ok", "ko_KR", n=3)


def test_t_unknown_key_returns_key():
    assert _i18n.t("no_such_key", "en_US") == "no_such_key"
