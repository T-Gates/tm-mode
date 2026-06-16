#!/usr/bin/env python3
"""session-start — 세션 시작 시 팀 맥락 주입 (공통 스크립트, 정규 스키마 전용).

스펙 02 §3.1·스펙 04 §4⑦·B1: install 이 아니라 **이 SessionStart 훅이** 다음 세션에
팀 최근 맥락을 *실제 주입*한다. manifest 에 등록돼 있으나 teammode-repo 에 파일이
부재했던 갭(L1-E) — 이 파일이 그 payoff 다.

정규 입력(stdin):  { "event": "SessionStart", "agent": "claude", "raw": {...} }
출력(stdout):       Claude additionalContext 형식 JSON — INDEX + 멤버별 최근 로그 summary.

규약:
- 팀 모드 활성(.acme-active) 시에만 주입. 비활성이면 무동작(exit 0).
- 팀 루트 = TEAMMODE_HOME(런타임 훅이라 env 정당, session-log-remind 와 동일 근거).
- 맥락 수집은 엔진(teammode._collect_members/_read_index)을 단일 소스로 재사용 — 드리프트
  방지. 요약은 하지 않는다(엔진 철학: 기계적 재료손질, 요약은 스킬·에이전트 몫).
- 어떤 예외도 세션을 막지 않는다(advisory) — 입력 오류·수집 실패 시 조용히 exit 0.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 엔진 맥락 수집 재사용 — 같은 INFRA 루트의 teammode.py. 경로 추가 후 import.
_HOOKS = Path(__file__).resolve().parent
_INFRA = _HOOKS.parent
if str(_INFRA) not in sys.path:
    sys.path.insert(0, str(_INFRA))
try:
    import teammode as _engine  # type: ignore
except ImportError:
    _engine = None
# stdout UTF-8 보장 — 한글 additionalContext json 이 Windows cp949 stdout 에서 크래시 방지.
try:
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 훅은 동작(보정만 스킵)
        return


def _team_root() -> str:
    """런타임 훅의 팀 루트 = TEAMMODE_HOME (없으면 cwd).

    ⚠️ 엔진(teammode.py)과 달리 런타임 훅은 env 를 읽는다 — 하니스가 발동해 --root CLI
    통로가 없기 때문(스펙 01 §1.2 필수 env). read-only 이라 P1 사고 표면 아님
    (session-log-remind 와 동일 근거).
    """
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


def _build_context(root: Path) -> str | None:
    """INDEX + 멤버별 최근 세션로그 summary 를 주입 문자열로 조립.

    엔진 _collect_members/_read_index 재사용. 수집 결과가 비어도(빈 팀) 유효 구조의
    안내를 돌려준다(I1 — 빈 상태라도 L1 데이터를 '읽어냄'). 엔진 부재 시 None.
    """
    if _engine is None:
        return None
    index_text = _engine._read_index(root)
    members = _engine._collect_members(root)

    lines = ["[teammode] 팀 모드 활성 — 세션 시작 맥락:"]
    if index_text.strip():
        lines.append("")
        lines.append("--- 팀 메모리 INDEX ---")
        lines.append(index_text.rstrip())
    lines.append("")
    lines.append("--- 멤버별 최근 작업 (summary) ---")
    if members:
        for m in members:
            summ = m["summary"] if m["summary"] else "(summary 없음 — 구로그)"
            lines.append(f"- {m['author']} [{m['date']}]: {summ}")
            lines.append(f"    file: {m['file']}")
    else:
        lines.append("(아직 세션로그 없음 — 첫 작업부터 "
                     "memory/team/sessions/<이름>/ 에 기록하세요.)")
    return "\n".join(lines)


def main() -> int:
    _ensure_utf8_io()  # 한글 json 출력이 Windows cp949 stdout 에서 크래시 방지
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # 입력 오류로 세션을 막지 않는다(advisory)

    if data.get("event") != "SessionStart":
        return 0

    root = Path(_team_root())
    # 팀 모드 활성 시에만 주입
    if not (root / ".acme-active").is_file():
        return 0

    try:
        context = _build_context(root)
    except Exception:  # noqa: BLE001 — 수집 실패가 세션을 막지 않는다
        return 0

    if context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
