"""stdout/stderr UTF-8 보장 — Windows native 인코딩 크래시 방지(크로스플랫폼 안전).

실 Windows 에서 기본 stdout/stderr 인코딩이 비-UTF8(cp949 등)이면, 엔진/스크립트가
한글(비-ASCII)을 print 할 때 UnicodeEncodeError 로 크래시한다. 실측: teammode.py
`context --json`(한글 INDEX 포함 json.dumps) → 크래시(rc=1) → install verify 단계
연쇄 실패(exit 3). `PYTHONIOENCODING=utf-8` 주면 정상 → 인코딩이 근본 원인.

해법: 한글을 출력하는 **모든 진입점**의 main 진입부에서 이 함수를 호출해, stdout/stderr
가 비-UTF8 텍스트 스트림이면 UTF-8 로 reconfigure 한다.

크로스플랫폼·무회귀 보장:
- 이미 UTF-8(Linux/macOS 기본)이면 무동작 — 기존 동작 그대로.
- reconfigure 불가 스트림(pytest capsys 의 StringIO, 일부 파이프 래퍼 등 reconfigure
  속성 부재)은 건드리지 않는다 — 테스트 캡처(capsys/capfd) 무파손.
- reconfigure 가능한 TextIOWrapper 만, 그것도 인코딩이 비-UTF8 일 때만 보정.
"""
from __future__ import annotations

import codecs
import sys


def _is_utf8(enc) -> bool:
    """인코딩 이름이 UTF-8 별칭(utf-8/utf8/UTF8 …)인지 정규화해 판정."""
    if not enc:
        # 인코딩 미상(None) — 안전하게 비-UTF8 취급해 보정 시도.
        return False
    try:
        return codecs.lookup(enc).name == "utf-8"
    except (LookupError, TypeError):
        return False


def _reconfigure_stream(stream) -> None:
    """단일 텍스트 스트림을 UTF-8 로 보정(가능·필요할 때만). 실패는 조용히 무시."""
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        # StringIO(capsys)·일부 래퍼 — reconfigure 없음. 건드리지 않는다(캡처 보호).
        return
    if _is_utf8(getattr(stream, "encoding", None)):
        # 이미 UTF-8 — 무동작(Linux/macOS 무영향, 무회귀).
        return
    try:
        # errors 미지정 → 스트림 기존 errors 정책 유지(기본 'strict'면 strict 유지).
        # UTF-8 은 모든 유니코드를 표현하므로 strict 여도 한글 유실 없음.
        reconfigure(encoding="utf-8")
    except (ValueError, OSError, AttributeError):
        # 이미 detach 됐거나 reconfigure 불가 상태 — 보정 포기(크래시보다 낫다).
        return


def ensure_utf8_io() -> None:
    """stdout·stderr 를 UTF-8 텍스트 스트림으로 보장(필요·가능 시에만).

    한글/비-ASCII 를 stdout·stderr 로 출력하는 모든 진입점의 main 진입부에서 호출한다.
    멱등(여러 번 호출해도 안전)이고 크로스플랫폼 안전하다.
    """
    _reconfigure_stream(sys.stdout)
    _reconfigure_stream(sys.stderr)
