#!/usr/bin/env python3
"""teammode 엔진 CLI — 슬라이스 2 수직 슬라이스 (on/off 만 실배선).

골든 시나리오(conformance/scenarios)의 인수 테스트를 GREEN으로 만들어가는 엔진.
슬라이스 2에서는 on/off → Claude 어댑터 sync 배선 + 배너·상태 마커까지만 구현한다.
context/issue/log 동사는 후속 슬라이스 (현재는 미구현 → 해당 시나리오 RED 유지).

  teammode.py on    팀 모드 켜기  — 배너 출력 + 어댑터 sync --on + .acme-active 생성
  teammode.py off   팀 모드 끄기  — 어댑터 sync --off + .acme-active 제거
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent       # 설치 위치 (manifest·adapter 소재)


def _team_root() -> Path:
    """팀 루트 = 엔진이 동작하는 대상 레포. 환경변수 우선, 없으면 현재 작업 디렉토리.

    (스펙 01 §2.4: 메모리 쓰기는 항상 팀 루트 memory/ 에. 설치 위치와 분리.)
    호출 시점에 해석한다 — subprocess·테스트 각각의 cwd를 정확히 반영하기 위함.
    """
    return Path(os.environ.get("LEGACY_TOOL_HOME", os.getcwd())).resolve()


def _active_marker() -> Path:
    return _team_root() / ".acme-active"


def _banner_file() -> Path:
    return _team_root() / "memory" / "banner.txt"


def _adapter(settings_path=None):
    import runpy
    mod = runpy.run_path(str(INFRA / "agents" / "claude" / "adapter.py"),
                         run_name="__teammode_engine__")
    Adapter = mod["Adapter"]
    return Adapter(
        agent_dir=str(INFRA / "agents" / "claude"),
        manifest_path=str(INFRA / "hooks" / "manifest.json"),
        settings_path=settings_path or os.path.expanduser("~/.claude/settings.json"),
        # 어댑터의 team_root = 설치 위치(normalize.py 소유 마커 기준). 메모리 쓰기의
        # 팀 루트(_team_root, cwd)와는 별개 축이다.
        team_root=str(INFRA.parent),
    )


def _render_banner() -> str:
    """배너 캐시를 읽거나, 없으면 팀 이름 기반 최소 배너를 생성·캐시한다(§11.5)."""
    banner_file = _banner_file()
    if banner_file.is_file():
        return banner_file.read_text(encoding="utf-8")
    team_name = os.environ.get("ACME_TEAM_NAME", "acme")
    banner = f"=== {team_name} team mode ON ===\n"
    banner_file.parent.mkdir(parents=True, exist_ok=True)
    banner_file.write_text(banner, encoding="utf-8")
    return banner


def cmd_on(settings_path=None) -> int:
    print(_render_banner(), end="")
    _adapter(settings_path).sync(mode="on")
    _active_marker().write_text("", encoding="utf-8")
    return 0


def cmd_off(settings_path=None) -> int:
    _adapter(settings_path).sync(mode="off")
    marker = _active_marker()
    if marker.exists():
        marker.unlink()
    print("teammode off — 상태 저장됨")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    settings_path = None
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--settings":
            settings_path = next(it)
        else:
            rest.append(a)
    if not rest:
        print("usage: teammode.py {on|off}", file=sys.stderr)
        return 2
    verb = rest[0]
    if verb == "on":
        return cmd_on(settings_path)
    if verb == "off":
        return cmd_off(settings_path)
    # 미구현 동사 — 후속 슬라이스 (시나리오 RED 유지)
    print(f"[unimplemented] {verb}", file=sys.stderr)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
