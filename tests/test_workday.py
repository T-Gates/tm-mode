"""workday — 06시 컷 작업일 계산 순수 함수 테스트 (스펙 01 §3.2).

경계가 핵심: 05:59(전날)·06:00(당일)·자정(전날)·월·연 경계.
모든 시각은 명시 주입(P1: 실시각 무조건 신뢰 금지) → 결정적 검증.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import workday as wd  # noqa: E402

KST = timezone(timedelta(hours=9))


def _kst(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=KST)


def test_after_six_is_same_day():
    assert wd.workday_str(_kst(2026, 6, 13, 6, 0)) == "2026-06-13"
    assert wd.workday_str(_kst(2026, 6, 13, 14, 30)) == "2026-06-13"
    assert wd.workday_str(_kst(2026, 6, 13, 23, 59)) == "2026-06-13"


def test_before_six_is_previous_day():
    # 00:00~05:59 → 전날
    assert wd.workday_str(_kst(2026, 6, 13, 0, 0)) == "2026-06-12"
    assert wd.workday_str(_kst(2026, 6, 13, 5, 59)) == "2026-06-12"


def test_six_oclock_boundary_exact():
    # 05:59 전날, 06:00 당일 — 경계 1분차
    assert wd.workday_str(_kst(2026, 6, 13, 5, 59)) == "2026-06-12"
    assert wd.workday_str(_kst(2026, 6, 13, 6, 0)) == "2026-06-13"


def test_month_boundary_before_six():
    # 6/1 03:00 → 5/31 작업일 (월 경계)
    assert wd.workday_str(_kst(2026, 6, 1, 3, 0)) == "2026-05-31"


def test_year_boundary_before_six():
    # 1/1 02:00 → 전년 12/31 (연 경계)
    assert wd.workday_str(_kst(2026, 1, 1, 2, 0)) == "2025-12-31"


def test_naive_datetime_treated_as_kst():
    # tz 없는 naive 도 KST 로 간주 (방어적)
    assert wd.workday_str(datetime(2026, 6, 13, 5, 0)) == "2026-06-12"
    assert wd.workday_str(datetime(2026, 6, 13, 7, 0)) == "2026-06-13"


def test_non_kst_tz_is_converted():
    # UTC 21:00 == KST 06:00 다음날 → 당일 처리 확인
    utc = timezone.utc
    # UTC 2026-06-12 21:00 == KST 2026-06-13 06:00
    assert wd.workday_str(datetime(2026, 6, 12, 21, 0, tzinfo=utc)) == "2026-06-13"
    # UTC 2026-06-12 20:00 == KST 2026-06-13 05:00 → 전날
    assert wd.workday_str(datetime(2026, 6, 12, 20, 0, tzinfo=utc)) == "2026-06-12"


def test_workday_returns_midnight_datetime():
    w = wd.workday(_kst(2026, 6, 13, 14, 0))
    assert (w.year, w.month, w.day, w.hour, w.minute) == (2026, 6, 13, 0, 0)
