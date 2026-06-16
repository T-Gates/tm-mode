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

레포 최신화(2026-06-17, P0 hook hang 수정):
- 맥락 주입 **전에** 팀 레포를 세션당 1회 git pull 한다(auto_pull 모듈 재사용 — 손자
  killpg·ff-only·타임아웃·자격증명 차단 안전장치 공유). 의도가 "상시 최신화(매
  프롬프트)"에서 "세션 시작 1회"로 바뀐 것 — UserPromptSubmit 동기 블로킹 훅의 매
  프롬프트 pull 이 hang 트리거였다(session-log-remind 에서 제거). 세션 중 최신화는
  `teammode pull` 수동. SessionStart 가 세션당 1회 발화하고 auto_pull 의 스로틀이
  급격한 세션 재시작도 가드한다. 실패는 절대 세션·주입을 막지 않는다(철칙).
"""
from __future__ import annotations

import json
import os
import sys
import time
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
# auto_pull 은 같은 hooks/ 디렉토리의 형제 모듈 — 세션당 1회 레포 최신화(슬라이스 U 이전).
try:
    import auto_pull as _auto_pull  # type: ignore
except ImportError:  # 모듈 부재여도 맥락 주입은 동작해야 한다(실패 무해)
    _auto_pull = None
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


def _pull_state_path() -> str:
    """마지막 auto-pull 시각 상태 파일 — **팀 루트 밖** 사용자 상태 디렉토리에 둔다.

    팀 루트(memory/ 등)를 오염시키지 않기 위해 $XDG_STATE_HOME 또는 ~/.local/state 사용.
    환경변수 미주입 시 합리적 기본값으로 폴백한다(런타임 훅은 인자 통로가 없으므로 env
    참조가 정당 — read-only/상태격리 목적이라 P1 사고 표면 아님). 종전 session-log-remind
    가 쓰던 경로와 동일(seamless 이전 — 의도만 매프롬프트→세션시작 1회로 바뀜).
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "teammode", "last-pull")


def _maybe_auto_pull(team_root: str) -> None:
    """맥락 주입 **이전에** 팀 레포를 세션당 1회 최신화(최신 상태로 맥락 주입).

    실패는 절대 세션·주입을 막지 않는다(철칙) — auto_pull 은 예외를 전파하지 않으며,
    여기서도 어떤 예외도 삼킨다. 느린 네트워크는 auto_pull 내부 타임아웃으로 가드된다.
    auto_pull 의 스로틀(state 파일)이 급격한 세션 재시작에도 throttle 창당 1회만 비용을
    물게 한다("세션당 1회"의 실질 보장 + 재시작 폭주 가드).
    """
    if _auto_pull is None:
        return
    try:
        throttle = int(os.environ.get("TEAMMODE_PULL_THROTTLE",
                                      _auto_pull.DEFAULT_THROTTLE_SECONDS))
        _auto_pull.auto_pull(
            team_root, _pull_state_path(), now=time.time(),
            throttle_seconds=throttle)
    except Exception:  # noqa: BLE001 — 철칙: 무슨 일이 있어도 세션·주입을 막지 않는다
        pass


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

    # 세션당 1회 레포 최신화 — 맥락 주입 전에(최신 상태로 주입). 실패 무해(철칙).
    _maybe_auto_pull(str(root))

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
