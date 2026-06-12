"""테스트 안전 가드 — 실 에이전트 설정 파일 오염 방지.

자율 빌드 규약: 실 환경(~/.claude/settings.json, ~/.codex/config.toml 등)은
절대 건드리지 않는다. 테스트는 tmp_path 픽스처만 써야 한다.

이 conftest 는 매 테스트 전후로 실 설정 경로의 존재/내용을 스냅샷해, 테스트가
실수로 실 파일을 생성·변경하면 즉시 실패시킨다. (과거 누수 재발 방지)
"""
import os
from pathlib import Path

import pytest

_GUARDED = [
    Path(os.path.expanduser("~/.claude/settings.json")),
    Path(os.path.expanduser("~/.codex/config.toml")),
    Path(os.path.expanduser("~/.codex")),
    Path(os.path.expanduser("~/.claude")),
]


def _snapshot():
    snap = {}
    for p in _GUARDED:
        if p.is_file():
            snap[p] = ("file", p.read_bytes())
        elif p.is_dir():
            snap[p] = ("dir", None)
        else:
            snap[p] = ("absent", None)
    return snap


@pytest.fixture(autouse=True)
def _no_real_config_pollution():
    before = _snapshot()
    yield
    after = _snapshot()
    for p in _GUARDED:
        b, a = before[p], after[p]
        # 디렉토리(~/.claude 등)는 다른 도구가 만들 수 있으니 파일 내용 변화만 엄격 검사
        if p.suffix and b != a:
            pytest.fail(
                f"실 설정 파일 오염 감지: {p} (before={b[0]}, after={a[0]}). "
                f"테스트는 tmp_path 만 사용해야 한다.")
