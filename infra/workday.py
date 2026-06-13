#!/usr/bin/env python3
"""workday — 작업일/06시 컷 순수 함수 (스펙 01 §3.2의 단일 소스).

설계(P1 정신): 시각을 인자로 받는 순수 함수. env·실시각을 무조건 신뢰하지 않아
06시 컷 경계(05:59 vs 06:00·자정·월경계)를 테스트가 결정적으로 검증할 수 있다.

drift 방지: session-log-remind.py 안내문과 엔진 `log` 동사가 같은 컷을 써야 하므로
계산을 한 곳(여기)에 둔다. 호출부는 `workday_str(now)` 만 쓰면 된다.

스펙 §3.2:
  - 00:00~05:59 → 전날 작업일
  - 06:00 이후 → 당일
  - 판정 시점 = 로그 작성 **시작** 시각
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 팀 timezone — reference 구현은 KST. (스펙상 team.config.json team.timezone 이나
# v0.1 reference 는 KST 고정; 추후 config 주입 가능하게 확장 여지를 둔다.)
KST = timezone(timedelta(hours=9))

# 작업일 경계 시각 (06:00).
CUT_HOUR = 6


def workday(now: datetime) -> datetime:
    """now 의 작업일을 datetime(자정 기준)으로 반환 (06시 컷 적용).

    naive datetime 은 KST 로 간주한다(방어적 — 훅은 aware 를 주지만 직접 호출 대비).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    if now.hour < CUT_HOUR:
        now = now - timedelta(days=1)
    return datetime(now.year, now.month, now.day, tzinfo=KST)


def workday_str(now: datetime) -> str:
    """작업일을 'YYYY-MM-DD' 문자열로 반환 (파일명·frontmatter date 공용)."""
    return workday(now).strftime("%Y-%m-%d")


def now_kst() -> datetime:
    """현재 KST 시각 — CLI 기본값용 (테스트는 항상 명시 주입)."""
    return datetime.now(KST)
