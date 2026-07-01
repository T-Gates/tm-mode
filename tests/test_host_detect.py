import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def test_detect_host_locale_from_lang(monkeypatch):
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert il.detect_host_locale() == "en_US"


def test_detect_host_locale_priority_and_ko(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "ko_KR.UTF-8")   # LC_ALL 우선
    assert il.detect_host_locale() == "ko_KR"


def test_detect_host_locale_fallback(monkeypatch):
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    assert il.detect_host_locale() == "en_US"       # 정보 없음 → 폴백
    monkeypatch.setenv("LANG", "C")                 # C/POSIX 는 무시
    assert il.detect_host_locale() == "en_US"


def test_detect_host_timezone_from_tz_env(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    assert il.detect_host_timezone() == "Asia/Seoul"


def test_detect_host_timezone_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("TZ", raising=False)
    # /etc/localtime 이 없거나 심링크 아님 → UTC (실 파일 무의존 위해 readlink 실패 유도)
    monkeypatch.setattr(il.os, "readlink",
                        lambda p: (_ for _ in ()).throw(OSError()))
    assert il.detect_host_timezone() == "UTC"
