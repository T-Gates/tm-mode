"""P1 핫픽스 S3 — install.py --help 윈도우 미세갭 테스트.

--help / -h 를 줬을 때 --root 없이 exit 0 + usage 를 출력해야 한다.
이전 동작: 손파싱이라 --help 무시 → bootstrap 진입 → team_root is None → exit 2.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _run_install(argv):
    """install.py 를 runpy 로 로드해 main(argv) 호출. sys.argv 오염 방지."""
    import runpy
    saved = sys.argv[:]
    try:
        mod = runpy.run_path(str(REPO / "infra" / "install.py"),
                             run_name="__test_help__")
        return mod["main"](argv)
    finally:
        sys.argv = saved


def test_help_exits_zero(capsys):
    """--help 는 --root 없이 exit 0."""
    rc = _run_install(["--help"])
    assert rc == 0


def test_help_prints_usage(capsys):
    """--help 출력에 usage 가 포함돼야 한다."""
    _run_install(["--help"])
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "--root" in out


def test_short_h_exits_zero(capsys):
    """-h 도 exit 0."""
    rc = _run_install(["-h"])
    assert rc == 0


def test_short_h_prints_usage(capsys):
    """-h 도 usage 출력."""
    _run_install(["-h"])
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_help_does_not_trigger_bootstrap(capsys):
    """--help 는 bootstrap 에 진입하지 않는다 — stdout 에 [plan] 이 없어야 함."""
    _run_install(["--help"])
    out = capsys.readouterr().out
    assert "[plan]" not in out
