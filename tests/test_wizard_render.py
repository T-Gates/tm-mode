"""clack 풍 리스킨 렌더 계약 회귀락 (2026-07-06 Vivid).

pty 없이 렌더 프리미티브를 직접 검증 — 커서 산술·NO_COLOR 게이트·wrap 자르기.
"""
import io
import os
import runpy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _mod():
    return runpy.run_path(str(REPO / "src" / "teammode" / "cli.py"),
                          run_name="__wiztest__")


def test_fit_truncates_by_visible_width_ignoring_ansi():
    m = _mod()
    _fit, _hi, _vis = m["_fit"], m["_hi"], m["_vis_len"]
    out = _fit("x" * 200, width=40)
    assert _vis(out) <= 40           # 가시폭 상한(괄호로 precedence 고정)
    assert out.rstrip("\x1b[0m").endswith("…") or out.endswith("…")
    # ANSI 는 길이에서 제외 — 색 입힌 짧은 문자열은 안 잘린다
    short = _hi("hello")
    assert _fit(short, width=40) == short


def test_fit_no_color_omits_reset_on_truncate(monkeypatch):
    """[재검수 P3] NO_COLOR 면 truncate 꼬리에 ANSI reset 을 붙이지 않는다."""
    monkeypatch.setenv("NO_COLOR", "1")
    m = _mod()
    out = m["_fit"]("y" * 200, width=30)
    assert "\x1b[0m" not in out and out.endswith("…")


def test_render_menu_line_count_titleless():
    """[재검수 P3] title 없는 경로: hint + choices = len+1 (collapse 산술)."""
    import io, sys
    m = _mod()
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        m["_render_menu"]("", "(hint)", ["a", "b"], 0, first=True)
    finally:
        sys.stdout = old
    assert buf.getvalue().count("\n") == 3  # hint + 2 = len+1


def test_render_menu_line_count_matches_collapse_arithmetic():
    """_render_menu 출력 줄수 = 접힘 산술(title 유 len+2 / 무 len+1)."""
    m = _mod()
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        m["_render_menu"]("Title", "(hint)", ["a", "b", "c"], 1, first=True)
    finally:
        sys.stdout = old
    # title + hint + 3 항목 = 5 줄
    assert buf.getvalue().count("\n") == 5


def test_no_color_suppresses_cursor_highlight(monkeypatch):
    """NO_COLOR 면 커서줄 배경(SGR)도 안 나온다 — off 계약."""
    monkeypatch.setenv("NO_COLOR", "1")
    m = _mod()
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        m["_render_menu"]("", "(h)", ["one", "two"], 0, first=True)
    finally:
        sys.stdout = old
    assert "\x1b[48" not in buf.getvalue()  # 배경 SGR 없음
    assert "\x1b[7m" not in buf.getvalue()  # 반전도 없음
    assert "❯ one" in buf.getvalue()        # 심볼 위계는 유지


def test_rail_done_uses_green_symbol_and_bold_value_when_color(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    m = _mod()
    # isatty 강제
    monkeypatch.setattr(m["sys"].stdout, "isatty", lambda: True, raising=False)
    buf = io.StringIO(); buf.isatty = lambda: True
    old = m["sys"].stdout
    m["sys"].stdout = buf
    try:
        m["_rail_done"]("You", "jun")
    finally:
        m["sys"].stdout = old
    out = buf.getvalue()
    assert "\x1b[32m" in out   # ◇ 초록
    assert "\x1b[1m" in out    # 값 볼드
