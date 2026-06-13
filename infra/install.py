#!/usr/bin/env python3
"""teammode 디스패처 — 얇은 위임자 (스펙 02 §2 불변식 3).

  install.py --<agent> sync [--on|--off]
  install.py --<agent> uninstall

디스패처는 분기 로직을 갖지 않는다. --<agent> 플래그로 agents/<name>/adapter.py 를
찾아 그 CLI에 그대로 위임할 뿐이다. 에이전트 고유 지식은 전부 어댑터 안에 있다.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent
AGENTS = INFRA / "agents"


def _split_agent(argv):
    """argv 앞쪽 --<agent> 플래그 1개를 떼어내 (agent_name, 나머지 argv)."""
    agent = None
    rest = []
    for arg in argv:
        if agent is None and arg.startswith("--") and (AGENTS / arg[2:]).is_dir():
            agent = arg[2:]
        else:
            rest.append(arg)
    return agent, rest


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    agent, rest = _split_agent(argv)
    if agent is None:
        avail = sorted(p.name for p in AGENTS.iterdir() if p.is_dir())
        print(f"[error] 에이전트를 지정하세요: --<agent>. 사용 가능: {avail}",
              file=sys.stderr)
        return 2

    adapter_path = AGENTS / agent / "adapter.py"
    if not adapter_path.is_file():
        print(f"[error] {agent} 어댑터 없음: {adapter_path}", file=sys.stderr)
        return 2

    # L1-0 P2 가드(엔진 _resolve_settings 계승): 어댑터에 위임하기 전에, 실 호스트
    # 설정(예: ~/.claude/settings.json) 오염을 막는다. 어댑터의 --settings 기본값이
    # 실 경로이므로, 디스패처 단계에서 명시(--settings <경로>) 또는 실설치 의사
    # (--install) 둘 중 하나를 요구한다. 둘 다 없으면 추측 오염 거부(exit 2).
    if "--settings" not in rest and "--install" not in rest:
        print("[error] --settings <경로> (격리) 또는 --install (실설치) 중 하나가 "
              "필요합니다. 명시 없이 실 호스트 설정에 쓰지 않습니다.", file=sys.stderr)
        return 2
    # --install 은 디스패처 전용 플래그 — 어댑터로 넘기지 않는다(어댑터는 --settings 만 안다).
    if "--install" in rest:
        rest = [a for a in rest if a != "--install"]

    # 어댑터 CLI 에 그대로 위임 (분기 로직 없음)
    sys.argv = [str(adapter_path)] + rest
    mod = runpy.run_path(str(adapter_path), run_name="__teammode_adapter__")
    return mod["main"](rest)


if __name__ == "__main__":
    raise SystemExit(main())
