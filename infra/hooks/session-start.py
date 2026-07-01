#!/usr/bin/env python3
"""session-start — 세션 시작 시 팀 맥락 주입 (공통 스크립트, 정규 스키마 전용).

스펙 02 §3.1·스펙 04 §4⑦·B1: install 이 아니라 **이 SessionStart 훅이** 다음 세션에
팀 최근 맥락을 *실제 주입*한다. manifest 에 등록돼 있으나 teammode-repo 에 파일이
부재했던 갭(L1-E) — 이 파일이 그 payoff 다.

정규 입력(stdin):  { "event": "SessionStart", "agent": "claude", "raw": {...} }
출력(stdout):       Claude additionalContext 형식 JSON — INDEX + 멤버별 최근 로그 summary.

규약:
- 팀 모드 활성(.teammode-active) 시에만 주입. 비활성이면 무동작(exit 0).
- 팀 루트 = TEAMMODE_HOME(런타임 훅이라 env 정당, session-log-remind 와 동일 근거).
- 맥락 수집은 엔진(teammode._collect_members/_read_index)을 단일 소스로 재사용 — 드리프트
  방지. 요약은 하지 않는다(엔진 철학: 기계적 재료손질, 요약은 스킬·에이전트 몫).
- 어떤 예외도 세션을 막지 않는다(advisory) — 입력 오류·수집 실패 시 조용히 exit 0.

레포 최신화(2026-06-17, P0 hook hang 수정):
- 맥락 주입 **전에** 팀 레포를 세션당 1회 정합한다(git_ops 안전장치 공유 — 손자
  killpg·타임아웃·자격증명 차단). 의도가 "상시 최신화(매 프롬프트)"에서 "세션 시작
  1회"로 바뀐 것 — UserPromptSubmit 동기 블로킹 훅의 매 프롬프트 pull 이 hang
  트리거였다(session-log-remind 에서 제거). 세션 중 최신화는 `teammode pull` 수동.
  SessionStart 가 세션당 1회 발화하고 auto_pull 의 스로틀이 급격한 세션 재시작도 가드.
  실패는 절대 세션·주입을 막지 않는다(철칙).
- 정합 강화(2026-06-29, 이슈 #23): 종전 `pull --ff-only` 는 로컬 diverge 시 조용히
  실패해 멀티유저 환경에서 로컬 커밋만 쌓였다. 이제 git_ops.do_reconcile 로 fetch +
  ff/rebase 까지 실제 정합하고, diverge·충돌·push 실패는 sync-warning 마커 + 주입
  맥락(아래 _build_context)으로 **표면화**한다. origin(팀 공유) 동기화 상태는
  upstream(템플릿) 업데이트 상태와 분리해 'ahead/behind' 한 줄로 보여준다.
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
# git_ops — do_reconcile(fetch+ff/rebase)·sync-warning 마커·ahead/behind(이슈 #23).
try:
    import git_ops as _git_ops  # type: ignore
except ImportError:  # 부재여도 맥락 주입은 동작해야 한다(실패 무해)
    _git_ops = None
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


def _record_pull_time(state_path: str, now: float) -> None:
    """마지막 정합 시각 기록(스로틀=시도 단위). auto_pull.should_pull 의 reader 와
    같은 포맷(repr(float))을 쓴다 — 같은 state 파일을 공유해 '세션당 1회'를 보장한다.
    실패해도 예외 전파 없음(철칙)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(repr(now))
    except OSError:
        pass


def _maybe_auto_pull(team_root: str) -> None:
    """맥락 주입 **이전에** 팀 레포를 세션당 1회 정합(최신 상태로 맥락 주입).

    이슈 #23: 종전엔 auto_pull(`pull --ff-only`)만 했는데, 로컬이 diverge(ahead&behind)
    하면 ff-only 가 **조용히 실패**해 멀티유저 환경에서 로컬 커밋만 누적됐다. 이제
    git_ops.do_reconcile 로 fetch + ff/rebase 까지 **실제 정합**하고, diverge·충돌·실패는
    sync-warning 마커 + stderr 로 **표면화**한다(조용히 넘기지 않음).

    세션당 1회는 auto_pull 의 스로틀(should_pull + state 파일)을 그대로 재사용해 보장한다
    (급격한 세션 재시작도 throttle 창당 1회). git_ops/auto_pull 부재 시 종전 경로로 폴백.
    실패는 절대 세션·주입을 막지 않는다(철칙) — 어떤 예외도 삼킨다.
    """
    # 폴백: 새 정합 경로의 의존(git_ops·auto_pull)이 없으면 종전 ff-only auto_pull.
    if _git_ops is None or _auto_pull is None:
        if _auto_pull is not None:
            try:
                throttle = int(os.environ.get(
                    "TEAMMODE_PULL_THROTTLE", _auto_pull.DEFAULT_THROTTLE_SECONDS))
                _auto_pull.auto_pull(team_root, _pull_state_path(),
                                     now=time.time(), throttle_seconds=throttle)
            except Exception:  # noqa: BLE001 — 철칙: 세션을 막지 않는다
                pass
        return
    try:
        throttle = int(os.environ.get("TEAMMODE_PULL_THROTTLE",
                                      _auto_pull.DEFAULT_THROTTLE_SECONDS))
        state = _pull_state_path()
        now = time.time()
        # 스로틀(세션당 1회) — auto_pull 의 공개 판정 재사용(드리프트 방지).
        if not _auto_pull.should_pull(state, now, throttle):
            return
        # 시도 단위 기록: 원격 장애 시에도 throttle 창당 1회만 비용(do_reconcile 은 무raise).
        _record_pull_time(state, now)

        res = _git_ops.do_reconcile(team_root)

        # ── 표면화: diverge/충돌/실패는 마커 + stderr(조용히 넘기지 않음) ──
        if res.action == "conflict":
            _git_ops.write_sync_warning(
                team_root, f"세션 시작 정합 충돌(rebase abort) — 수동 정리 필요: {res.detail}")
            print(f"[teammode] 세션 정합 실패: origin 과 diverge 후 rebase 충돌 — "
                  f"수동 정리 필요. behind={res.behind} ahead={res.ahead}", file=sys.stderr)
        elif res.action in ("fetch-failed", "error"):
            # 네트워크/일시 오류 — 묵은 push 마커는 건드리지 않고 정보만(비치명).
            print(f"[teammode] 세션 정합 건너뜀(비치명): {res.action} — {res.detail}",
                  file=sys.stderr)
        elif res.action in ("up-to-date", "fast-forward", "rebased") and res.ahead == 0:
            # **실제 origin 정합이 입증된** 경우에만 마커 제거(codex 리뷰). no-upstream 도
            # ok=True·ahead=0 을 주지만(추적 upstream 없음), 그건 직전 push 실패가 미해결인
            # 채로 정합을 못 한 상태다 — 여기서 지우면 #23 의 push 실패 가시성이 깨진다.
            # ahead-only/fetch-failed/conflict/error 도 미해결이므로 마커를 보존한다.
            _git_ops.clear_sync_warning(team_root)
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

    # ── 동기화 상태(이슈 #23): origin(팀 공유) vs upstream(템플릿) 분리 표시 ──
    # push 실패 마커가 있으면 크게 경고(로컬 커밋이 origin 에 안 올라간 상태). 이어서
    # origin 대비 ahead/behind 한 줄. read-only — 정합은 _maybe_auto_pull 이 이미 수행.
    if _git_ops is not None:
        try:
            warn = _git_ops.read_sync_warning(str(root))
            if warn:
                lines.append("")
                lines.append("⚠️ [동기화 경고] 로컬 커밋이 origin 에 push 되지 않았습니다 "
                             "— 팀원과 분기(divergence) 위험. 확인 후 `teammode pull`/수동 "
                             f"정리 필요: {warn}")
            ahead, behind = _git_ops.ahead_behind(str(root))
            if ahead or behind:
                lines.append("")
                lines.append(f"--- origin 동기화 상태(팀 공유) --- ahead {ahead} / "
                             f"behind {behind}"
                             + (" (push 안 된 로컬 커밋 있음)" if ahead else ""))
        except Exception:  # noqa: BLE001 — 상태 표시 실패가 맥락 주입을 막지 않는다
            pass

    # guidelines 주입: 범용(root/infra/ 우선, fallback _INFRA) + 팀 커스텀
    _infra_gl = root / "infra" / "guidelines.md"
    if not _infra_gl.is_file():
        _infra_gl = _INFRA / "guidelines.md"
    for _gl_path in (_infra_gl, root / "memory" / "team" / "guidelines.md"):
        if _gl_path.is_file():
            lines.append("")
            lines.append(_gl_path.read_text(encoding="utf-8").rstrip())

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
    if not (root / ".teammode-active").is_file():
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
