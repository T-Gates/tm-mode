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
- 엔진 업데이트 알림 추가(계속 켜둔 인스턴스 갭 메움): `tm on`을 계속 켜둔 상태로
  두는 인스턴스는 auto_update_on_start(cmd_on 전용)를 다시 타지 않아 엔진이
  뒤처져도 알림이 없었다. 이 훅에서 로컬 NOTICE.md 와 upstream/main 의 NOTICE.md
  를 비교해 다르면 `tm-mode update` 안내를 한 줄 추가한다.
- upstream 캐시 최신화 추가(적대검수 발견 — 최초 버전의 치명 결함 수정): 위 비교는
  로컬에 캐시된 upstream/main 오브젝트만 읽는데, 그 캐시를 최신화하는 유일한 경로
  (sync_from_upstream)가 cmd_on/cmd_update 안에만 있어 계속 켜둔 인스턴스에서는
  fetch 시점 이후의 새 upstream 변화를 영원히 못 봤다(바로 위 항목이 고치려던 갭이
  최초 구현에선 실제로 안 막혔던 것). 이제 세션 시작마다(스로틀 적용, 기본 24h,
  auto_pull.should_pull 재사용) `git_ops.fetch_upstream` 로 fetch 만(merge·checkout
  없음) 짧게 새로 고친다. 무raise·타임아웃(killpg)은 fetch_upstream 자체 계약을
  그대로 물려받고, 호출부도 한 번 더 감싼다 — 오프라인이면 그냥 스킵, 세션은 안 막힘.
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
# 세션로그 규칙 단일 소스(compact hook context) — 시블링 모듈(auto_pull 과 동일 패턴).
# 리마인더(session-log-remind, compact 기본)의 "(규칙: 세션 시작 주입 참조)"가 가리키는
# 블록을 여기서 세션당 1회 주입한다. 부재 시 규칙 블록만 생략(advisory — 주입은 계속).
try:
    import _slog_rules as _slog_rules_mod  # type: ignore
except ImportError:
    _slog_rules_mod = None
# i18n(PR-i1) — 팀 locale(team.config.json team.locale)에 따라 주입 라벨 ko/en 선택.
# io_encoding 과 동일한 infra/ sys.path 재사용 패턴. 부재(부분 배포) 시 ko 강등(무해).
try:
    import i18n as _i18n  # type: ignore
except ImportError:
    _i18n = None


def _hook_lang(root: str) -> str:
    """팀 locale → 주입 언어("ko"|"en"). i18n 부재/실패 시 ko(종전 거동 보존)."""
    if _i18n is None:
        return "ko"
    try:
        return _i18n.team_lang(root)
    except Exception:  # noqa: BLE001 — locale 해석 실패가 주입을 막지 않는다
        return "ko"


def _t(key: str, lang: str, ko: str, **fmt) -> str:
    """주입 문자열 선택 — ko 원문은 호출부 리터럴이 단일 소스(구팀 무변화 계약),
    en 은 i18n 카탈로그(hook_* 키). i18n 부재 시 ko 폴백."""
    if lang == "en" and _i18n is not None:
        return _i18n.t(key, "en", **fmt)
    return ko.format(**fmt) if fmt else ko
# kb-write-guard — 세션 id relay 규약의 단일 소스(A2). 파일명이 하이픈이라 importlib 로
# 로드한다(top-level 은 정의뿐이라 부작용 없음). 부재/실패 시 relay 만 생략(advisory).
try:
    import importlib.util as _ilu
    _kb_spec = _ilu.spec_from_file_location(
        "_kb_write_guard_relay", str(_HOOKS / "kb-write-guard.py"))
    _kb_guard = _ilu.module_from_spec(_kb_spec)
    _kb_spec.loader.exec_module(_kb_guard)
except Exception:  # noqa: BLE001 — relay 는 부가 기능, 세션 주입을 막지 않는다
    _kb_guard = None


def _team_root() -> str:
    """런타임 훅의 팀 루트 = TEAMMODE_HOME (없으면 cwd).

    ⚠️ 엔진(teammode.py)과 달리 런타임 훅은 env 를 읽는다 — 하니스가 발동해 --root CLI
    통로가 없기 때문(스펙 01 §1.2 필수 env). read-only 이라 P1 사고 표면 아님
    (session-log-remind 와 동일 근거).
    """
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


# 팀 레포 표식 — install_lib.has_team_marker(_TEAM_MARKERS)와 동일 규약(드리프트 주의).
_TEAM_MARKERS = (".git", "team.config.json", "memory")

# Manifest 60s보다 먼저 맥락 JSON을 내보내기 위한 hook 전체 hard budget. 앞 40s는
# origin reconcile/pending recovery/upstream refresh가 공유하고, 마지막 10s는 로컬
# memory context + 선택적 Git 장식에 예약한다. 남은 시간이 없으면 optional Git 작업은
# 새 1s floor subprocess를 시작하지 않고 건너뛴다.
_SESSION_START_TOTAL_BUDGET = 50
_SESSION_CONTEXT_RESERVE = 10


def _remaining_timeout(deadline, cap: int, reserve: int = 0) -> int:
    """absolute deadline에서 reserve를 뺀 호출 timeout. 예산 없음은 0(skip)."""
    if deadline is None:
        return max(1, cap)
    remaining = int(deadline - time.monotonic() - reserve)
    if remaining < 1:
        return 0
    return min(max(1, cap), remaining)


def _split_timeout(deadline, calls: int, cap: int, reserve: int = 0) -> int:
    """연속 로컬 Git calls가 deadline 안에 들도록 균등한 per-call timeout을 계산."""
    if deadline is None:
        return max(1, cap)
    available = int(deadline - time.monotonic() - reserve)
    if calls < 1 or available < calls:
        return 0
    return min(max(1, cap), max(1, available // calls))


def _warn_if_stale_home(root: str) -> None:
    """TEAMMODE_HOME 이 설정됐는데 유효한 팀 루트가 아니면 stderr 한 줄 경고 (이슈 #9a).

    레포 이동/이름변경 후 env 가 옛 경로를 가리키면 훅이 조용히 죽어(.teammode-active
    부재 exit 0) 원인 진단이 불가했다. stdout 은 훅 출력(JSON)으로 소비되므로 경고는
    stderr 로만, 한 줄로 내고 거동(exit 0)은 바꾸지 않는다. 팀 표식이 있는데
    .teammode-active 만 없는 정상 off 상태는 종전대로 침묵한다.
    """
    if not os.environ.get("TEAMMODE_HOME"):
        return
    if any(os.path.exists(os.path.join(root, m)) for m in _TEAM_MARKERS):
        return
    try:
        print(_t("hook_ss_stale_home_warn", _hook_lang(root),
                 "[teammode] TEAMMODE_HOME이 유효한 팀 루트가 아닙니다: {root} — "
                 "레포 이동/이름변경 시 셸 프로파일의 TEAMMODE_HOME을 갱신하세요",
                 root=root),
              file=sys.stderr)
    except (OSError, UnicodeError):
        pass  # 경고 실패가 훅을 막지 않는다(advisory)


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


def _maybe_auto_pull(team_root: str, deadline=None) -> None:
    """맥락 주입 **이전에** 팀 레포를 세션당 1회 정합(최신 상태로 맥락 주입).

    이슈 #23: 종전엔 auto_pull(`pull --ff-only`)만 했는데, 로컬이 diverge(ahead&behind)
    하면 ff-only 가 **조용히 실패**해 멀티유저 환경에서 로컬 커밋만 누적됐다. 이제
    git_ops.do_reconcile 로 fetch + ff/rebase 까지 **실제 정합**하고, diverge·충돌·실패는
    sync-warning 마커 + stderr 로 **표면화**한다(조용히 넘기지 않음).

    세션당 1회는 auto_pull 의 스로틀(should_pull + state 파일)을 그대로 재사용해 보장한다
    (급격한 세션 재시작도 throttle 창당 1회). git_ops/auto_pull 부재 시 종전 경로로 폴백.
    실패는 절대 세션·주입을 막지 않는다(철칙) — 어떤 예외도 삼킨다.
    """
    # i18n(적대검수 — long tail): 이 함수 이하의 print/마커는 main() 의 lang 해석보다
    # 먼저 도므로 여기서 한 번 독자적으로 해석해 _recover_push_pending 에도 넘긴다
    # (session-start 는 세션당 1회라 재해석 비용 무해 — _hook_lang 자체의 문서 근거).
    lang = _hook_lang(team_root)
    try:
        # 폴백: 새 정합 경로의 의존(git_ops·auto_pull)이 없으면 종전 ff-only auto_pull.
        if _git_ops is None or _auto_pull is None:
            if _auto_pull is not None:
                throttle = int(os.environ.get(
                    "TEAMMODE_PULL_THROTTLE", _auto_pull.DEFAULT_THROTTLE_SECONDS))
                _auto_pull.auto_pull(team_root, _pull_state_path(),
                                     now=time.time(), throttle_seconds=throttle)
            return

        throttle = int(os.environ.get("TEAMMODE_PULL_THROTTLE",
                                      _auto_pull.DEFAULT_THROTTLE_SECONDS))
        state = _pull_state_path()
        now = time.time()
        # 스로틀(세션당 1회) — auto_pull 의 공개 판정 재사용(드리프트 방지).
        if not _auto_pull.should_pull(state, now, throttle):
            return
        # 시도 단위 기록: 원격 장애 시에도 throttle 창당 1회만 비용(do_reconcile 은 무raise).
        _record_pull_time(state, now)

        if deadline is not None:
            if not _remaining_timeout(
                    deadline, _git_ops.DEFAULT_TIMEOUT):
                return
            res = _git_ops.do_reconcile(team_root, deadline=deadline)
        else:
            res = _git_ops.do_reconcile(team_root)

        # ── 표면화: diverge/충돌/실패는 마커 + stderr(조용히 넘기지 않음) ──
        # ⚠️ write_sync_warning 의 detail 은 나중에 hook_ss_sync_warn(이미 i18n 라우팅)의
        # {warn} 자리에 그대로 삽입된다 — 여기서 lang 에 안 맞게 쓰면 en 래퍼 안에 ko
        # 상세가 섞인다(적대검수 발견). 그래서 마커 내용도 lang 을 따른다.
        if res.action == "conflict":
            _git_ops.write_sync_warning(
                team_root, _t("hook_ss_reconcile_conflict_marker", lang,
                             "세션 시작 정합 충돌(rebase abort) — 수동 정리 필요: {detail}",
                             detail=res.detail))
            print(_t("hook_ss_reconcile_conflict_print", lang,
                     "[teammode] 세션 정합 실패: origin 과 diverge 후 rebase 충돌 — "
                     "수동 정리 필요. behind={behind} ahead={ahead}",
                     behind=res.behind, ahead=res.ahead), file=sys.stderr)
        elif res.action in ("fetch-failed", "error"):
            # 네트워크/일시 오류 — 묵은 push 마커는 건드리지 않고 정보만(비치명).
            print(_t("hook_ss_reconcile_skipped", lang,
                     "[teammode] 세션 정합 건너뜀(비치명): {action} — {detail}",
                     action=res.action, detail=res.detail),
                  file=sys.stderr)
        elif res.action in ("up-to-date", "fast-forward", "rebased") and res.ahead == 0:
            # **실제 origin 정합이 입증된** 경우에만 마커 제거(codex 리뷰). no-upstream 도
            # ok=True·ahead=0 을 주지만(추적 upstream 없음), 그건 직전 push 실패가 미해결인
            # 채로 정합을 못 한 상태다 — 여기서 지우면 #23 의 push 실패 가시성이 깨진다.
            # ahead-only/fetch-failed/conflict/error 도 미해결이므로 마커를 보존한다.
            if deadline is None:
                _git_ops.clear_sync_warning_if_fully_published(team_root)
            else:
                clear_timeout = _remaining_timeout(
                    deadline, _git_ops.DEFAULT_TIMEOUT)
                if clear_timeout:
                    _git_ops.clear_sync_warning_if_fully_published(
                        team_root, timeout=clear_timeout)
    except Exception:  # noqa: BLE001 — 철칙: 무슨 일이 있어도 세션·주입을 막지 않는다
        pass
    finally:
        # #45 pending recovery 는 pull 스로틀과 **독립**이지만, reconcile 과는 직렬화한다.
        # 먼저 worker 를 시작하면 non-ff 로 실패한 뒤 이어진 rebase 결과를 다시 push 할
        # 기회가 없다. 폴백·스로틀·예외 경로를 포함해 정합 시도 뒤 마지막에 항상 재kick.
        _recover_push_pending(team_root, lang, deadline=deadline)


def _upstream_fetch_state_path() -> str:
    """마지막 upstream(제품) fetch 시각 상태 파일 — _pull_state_path 와 같은 디렉터리,
    다른 파일명(last-upstream-fetch). origin pull 스로틀과는 독립적으로 관리한다 —
    upstream(제품) 은 origin(팀 공유)보다 훨씬 느리게 움직이므로 훨씬 긴 주기가 맞다.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "teammode", "last-upstream-fetch")


_UPSTREAM_FETCH_THROTTLE_SECONDS = 86400  # 기본 24h — 엔진 릴리스 빈도에 맞춘 보수적 기본


def _maybe_fetch_upstream(team_root: str, deadline=None) -> None:
    """세션 시작마다(스로틀 적용) upstream(제품)을 **fetch 만** 한다 — merge·checkout 없음.

    왜 필요한가(적대검수 발견 — 치명 설계 결함): 엔진 업데이트 알림(_build_context 의
    read_upstream_notice 비교)은 **로컬에 캐시된** upstream/main 오브젝트만 읽는다
    (그 자체는 의도된 설계 — 훅에서 새 네트워크 호출을 만들지 않으려던 것). 문제는 그
    캐시를 누가 최신화하냐다: upstream 을 실제로 fetch 하는 sync_from_upstream 은
    cmd_on/cmd_update 안에서만 돈다. `tm on`을 한 번 켠 뒤 계속 켜둔 채(이 알림이
    존재하는 이유 그 자체인 시나리오) cmd_on 을 다시 안 타는 인스턴스는 upstream/main
    캐시가 fetch 시점에 영원히 멈춘다 — 즉 알림이 "그 이후의" 새 upstream 변경을
    **영원히** 못 본다. 그래서 세션 시작마다(단, 스로틀) 짧게 fetch 만 해서 캐시를
    새로 고친다. merge/checkout 은 여전히 안 한다(적용은 여전히 `tm-mode update` 몫 —
    이 훅은 감지만, 사람 승인 있는 적용 경로와 분리 유지).

    스로틀·상태파일 재사용: auto_pull.should_pull/_record_pull_time 은 remote 무관한
    범용 함수라 그대로 재사용한다(중복 구현 금지) — origin pull 스로틀과는 별도의
    state 파일(_upstream_fetch_state_path)을 써서 서로 간섭하지 않는다.
    fetch_upstream 자체가 이미 무raise·타임아웃(killpg, git_ops.run_git 공유)이지만,
    호출부도 한 번 더 감싼다(기존 _maybe_auto_pull 과 동형 — 철칙: 어떤 예외도 세션을
    막지 않는다).
    """
    if _git_ops is None or _auto_pull is None:
        return
    try:
        throttle = int(os.environ.get(
            "TEAMMODE_UPSTREAM_FETCH_THROTTLE", _UPSTREAM_FETCH_THROTTLE_SECONDS))
        state = _upstream_fetch_state_path()
        now = time.time()
        if not _auto_pull.should_pull(state, now, throttle):
            return
        # 시도 단위 기록 — 오프라인/원격 무등록이어도 스로틀 창당 1회만 비용(무raise).
        _record_pull_time(state, now)
        if deadline is None:
            _git_ops.fetch_upstream(team_root)
        else:
            # fetch_upstream 내부의 is-worktree + remote-list 두 로컬 probe 몫을 먼저
            # 남기고, 실제 network fetch만 shared deadline의 나머지로 clamp한다.
            fetch_timeout = _remaining_timeout(
                deadline, _git_ops.NET_TIMEOUT,
                reserve=2 * _git_ops.DEFAULT_TIMEOUT)
            if fetch_timeout:
                _git_ops.fetch_upstream(team_root, timeout=fetch_timeout)
    except Exception:  # noqa: BLE001 — 철칙: 어떤 예외도 세션을 막지 않는다
        pass


def _recover_push_pending(team_root: str, lang: str = "ko", deadline=None) -> None:
    """#45 pending recovery — worker 유실(머신 슬립·Windows detach 실패·크래시) 복원.

    ledger 가 correctness 의 단일 소스: pending 존재 시 age 무관 ahead 조합 판정 —
      - 판정불가(no upstream/git 오류) → 보수 경고(clear 하지 않음).
      - ahead > 0 → 경고 + worker **재kick 만**(세션 시작을 push 로 무겁히지 않는다 —
        직접 push 금지가 계약).
      - ahead == 0 → stale pending 자동 clear(이미 push 됨 — worker 가 clear 전에 죽음).
    무raise — 세션·주입을 막지 않는다. lang 은 호출부(_maybe_auto_pull)가 한 번
    해석해 넘긴다(적대검수 — long tail).
    """
    if _git_ops is None:
        return
    try:
        # legacy bind/current-checkout/ahead 판정과 state lock 꼬리를 위한 최소 여유.
        # 부족하면 ledger를 그대로 보존해 다음 auto-commit/세션이 재시도하게 한다.
        if (deadline is not None
                and not _remaining_timeout(
                    deadline, _git_ops.DEFAULT_TIMEOUT,
                    reserve=2 * _git_ops.DEFAULT_TIMEOUT + 2)):
            return
        pending_state = _git_ops.read_push_pending_state(team_root)
        if not pending_state.available:
            return  # lock/state 판정불가 — 보수적으로 pending/warning 을 보존
        pending_snapshot = _git_ops.bind_legacy_pending_to_current_checkout(
            team_root, pending_state.content)
        if not pending_snapshot:
            return
        pending_target_key = _git_ops.pending_entry_key_for_current_checkout(
            team_root, pending_snapshot)
        if not pending_target_key:
            targets = _git_ops.pending_target_summary(pending_snapshot, team_root)
            _git_ops.write_sync_warning(
                team_root, _t("hook_ss_push_pending_checkout_mismatch", lang,
                             "push pending 대상 checkout 불일치 — 현재 branch에서는 "
                             "자동 처리하지 않음: {targets}", targets=targets))
            print(_t("hook_ss_push_pending_checkout_mismatch_print", lang,
                     "[teammode] 다른 checkout의 push pending을 보존했습니다. "
                     "해당 branch로 전환해 재시도하세요: {targets}", targets=targets),
                  file=sys.stderr)
            return
        ahead_timeout = _remaining_timeout(
            deadline, _git_ops.DEFAULT_TIMEOUT) if deadline is not None \
            else _git_ops.DEFAULT_TIMEOUT
        if not ahead_timeout:
            return
        ahead, _behind, has_upstream = _git_ops._ahead_behind_raw(
            team_root, ahead_timeout)
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "push-worker.py")
        if not has_upstream:
            # 판정불가 = 무 upstream(신규 브랜치) 또는 git 오류 — 구분 불가하므로
            # 보수 경고 + **kick**(codex P2: worker 의 push_plain 이 no-upstream 을
            # `push -u` 로 처리한다 — kick 없이 경고만 반복하면 영구 잔존 UX).
            print(_t("hook_ss_push_pending_no_upstream", lang,
                     "[teammode] push 미완(pending)이 있는데 원격 판정 불가 — "
                     "worker 를 재시작합니다(신규 브랜치면 push -u 로 처리)."),
                  file=sys.stderr)
            if not _git_ops.kick_push_worker(team_root, worker):
                print(_t("hook_ss_push_worker_restart_failed", lang,
                         "[teammode] worker 재시작 실패 — 다음 커밋/세션에서 "
                         "재시도됩니다."), file=sys.stderr)
            return
        if ahead > 0:
            print(_t("hook_ss_push_pending_ahead", lang,
                     "[teammode] 이전 세션의 push 미완(pending, ahead={ahead}) — "
                     "worker 를 재시작합니다.", ahead=ahead), file=sys.stderr)
            if not _git_ops.kick_push_worker(team_root, worker):
                print(_t("hook_ss_push_worker_restart_failed", lang,
                         "[teammode] worker 재시작 실패 — 다음 커밋/세션에서 "
                         "재시도됩니다."), file=sys.stderr)
            return
        # ahead == 0: push 는 이미 됐는데 clear 전에 worker 가 죽은 잔재 — 자동 정리.
        if _git_ops.clear_push_pending_if_unchanged(
                team_root, pending_snapshot, pending_target_key):
            if deadline is None:
                _git_ops.clear_sync_warning_if_fully_published(team_root)
            else:
                clear_timeout = _remaining_timeout(
                    deadline, _git_ops.DEFAULT_TIMEOUT)
                if clear_timeout:
                    _git_ops.clear_sync_warning_if_fully_published(
                        team_root, timeout=clear_timeout)
    except Exception:  # noqa: BLE001 — 철칙
        pass


def _persist_session_relay(data: dict) -> None:
    """정규 stdin 세션 id 를 세션별 relay 파일로 영속(A2 writer relay).

    Codex 세션은 CLAUDE_*_SESSION_ID env 가 없어 엔진 `memory unlock begin|end` 가
    세션 id 를 알 길이 없었다 — SessionStart 훅이 받는 정규 stdin session_id
    (normalize 가 raw session_id/sessionId 에서 승격)를 `<relay_dir>/<session_id>`
    파일로 남겨 엔진이 최신 mtime 파일로 읽게 한다. 경로 규약·id 검증은
    kb-write-guard 모듈(session_relay_dir/_valid_session_id)을 단일 소스로 재사용.

    같은 기회에 TTL(SESSION_RELAY_TTL_SECONDS) 지난 스테일 항목을 프루닝한다.
    relay 오선택은 guard 가 fail-closed(스퓨리어스 deny)로 어차피 막으므로 이
    파일은 가용성 장치이지 보안 경계가 아니다. 실패는 무해(advisory) — 어떤
    예외도 세션·맥락 주입을 막지 않는다.
    """
    if _kb_guard is None:
        return
    try:
        sid = _kb_guard._valid_session_id(data.get("session_id"))
        if not sid:
            return  # 세션 id 없음/malformed → relay 생략(비치명)
        # root 는 guard 와 동일하게 __file__ 기준(이 훅이 설치된 팀 레포) — guard 의
        # root_hash 와 일치해야 relay→flag 경로가 맞는다(TEAMMODE_HOME 무신뢰).
        team_root = str(_INFRA.parent)
        relay_dir = _kb_guard.session_relay_dir(team_root)
        os.makedirs(relay_dir, exist_ok=True)
        relay_path = os.path.join(relay_dir, sid)
        with open(relay_path, "w", encoding="utf-8") as f:
            json.dump({"schema": "session-relay/1", "session_id": sid,
                       "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "created_by_pid": os.getpid()}, f, ensure_ascii=False)
        try:
            os.chmod(relay_path, 0o600)  # 내용은 진단용이지만 개인 상태 — 비공개
        except OSError:
            pass
        # 기회적 프루닝 — 스테일(≥TTL) 항목 제거로 디렉토리 무한 성장 방지.
        now = time.time()
        for name in os.listdir(relay_dir):
            if name == sid:
                continue
            p = os.path.join(relay_dir, name)
            try:
                if now - os.stat(p).st_mtime >= _kb_guard.SESSION_RELAY_TTL_SECONDS:
                    os.unlink(p)
            except OSError:
                continue  # 개별 항목 실패는 무시(advisory)
    except Exception:  # noqa: BLE001 — 철칙: relay 실패가 세션을 막지 않는다
        pass


def _build_context(root: Path, lang: str = "ko", deadline=None) -> str | None:
    """INDEX + 멤버별 최근 세션로그 summary 를 주입 문자열로 조립.

    엔진 _collect_members/_read_index 재사용. 수집 결과가 비어도(빈 팀) 유효 구조의
    안내를 돌려준다(I1 — 빈 상태라도 L1 데이터를 '읽어냄'). 엔진 부재 시 None.

    i18n(PR-i1): **엔진 소유 라벨만** lang 분기 — 팀 작성물(INDEX 본문·summary 내용·
    팀 커스텀 guidelines)은 절대 번역하지 않는다.
    """
    if _engine is None:
        return None
    index_text = _engine._read_index(root)
    members = _engine._collect_members(root)

    lines = [_t("hook_ss_header", lang,
                "[teammode] 팀 모드 활성 — 세션 시작 맥락:")]

    # ── 동기화 상태(이슈 #23): origin(팀 공유) vs upstream(템플릿) 분리 표시 ──
    # push 실패 마커가 있으면 크게 경고(로컬 커밋이 origin 에 안 올라간 상태). 이어서
    # origin 대비 ahead/behind 한 줄. read-only — 정합은 _maybe_auto_pull 이 이미 수행.
    if _git_ops is not None:
        try:
            warn = _git_ops.read_sync_warning(str(root))
            if warn:
                lines.append("")
                lines.append(_t(
                    "hook_ss_sync_warn", lang,
                    "⚠️ [동기화 경고] 로컬 커밋이 origin 에 push 되지 않았습니다 "
                    "— 팀원과 분기(divergence) 위험. 확인 후 `teammode pull`/수동 "
                    "정리 필요: {warn}", warn=warn))
            ahead_timeout = _remaining_timeout(deadline, _git_ops.DEFAULT_TIMEOUT)
            ahead, behind = ((0, 0) if not ahead_timeout else
                             _git_ops.ahead_behind(
                                 str(root), timeout=ahead_timeout))
            if ahead or behind:
                lines.append("")
                lines.append(_t(
                    "hook_ss_sync_status", lang,
                    "--- origin 동기화 상태(팀 공유) --- ahead {ahead} / "
                    "behind {behind}", ahead=ahead, behind=behind)
                    + (_t("hook_ss_sync_ahead_suffix", lang,
                          " (push 안 된 로컬 커밋 있음)") if ahead else ""))
        except Exception:  # noqa: BLE001 — 상태 표시 실패가 맥락 주입을 막지 않는다
            pass

    # ── 엔진 업데이트 알림(upstream 템플릿, 위 origin 상태와 별개) ──
    # `tm on` 을 계속 켜둔 인스턴스는 auto_update_on_start(cmd_on 전용)를 다시 타지
    # 않아 엔진이 뒤처져도 아무도 알려주지 않는다(갭). 이 비교 자체는 fetch 하지 않고
    # — 이미 로컬에 캐시된 upstream/main 오브젝트만 읽는 두 read-only 함수를 재사용:
    #   _engine._read_local_notice(로컬 NOTICE.md 파일 읽기) 와
    #   _git_ops.read_upstream_notice(`git show upstream/main:NOTICE.md` — 로컬 git
    #   오브젝트 DB 조회. detect_default_branch 도 로컬 ref 전용, infra/git_ops.py 의
    #   두 함수 docstring 참고). **그 캐시 자체**는 이 함수 호출보다 앞서 main() 에서
    #   부른 _maybe_fetch_upstream(스로틀 적용 fetch, 위 docstring 참고)이 새로 고친다
    #   — 여기서는 그 결과를 read-only 로 읽기만 한다. 내용이 다르면(=새 엔진 업데이트가
    #   upstream 에 있으면) tm-mode update 안내 한 줄만 덧붙인다.
    if _git_ops is not None and _engine is not None:
        try:
            local_notice = _engine._read_local_notice(root)
            # is-worktree + default-branch(symbolic-ref 실패 시 rev-parse fallback까지
            # 2회) + show의 최대 4개 로컬 Git call이 context deadline을 각각 새로
            # 받지 않도록 균등 분할한다. 출력 1s는 남긴다.
            notice_timeout = _split_timeout(
                deadline, calls=4, cap=_git_ops.DEFAULT_TIMEOUT, reserve=1)
            upstream_notice = (_git_ops.read_upstream_notice(
                str(root), timeout=notice_timeout) if notice_timeout else "")
            if upstream_notice and upstream_notice != local_notice:
                lines.append("")
                lines.append(_t(
                    "hook_ss_engine_update_available", lang,
                    "[teammode] 엔진 업데이트가 upstream 에 있습니다 — "
                    "`tm-mode update`로 적용하세요."))
        except Exception:  # noqa: BLE001 — 알림 실패가 맥락 주입을 막지 않는다
            pass

    # guidelines 주입: 범용(root/infra/ 우선, fallback _INFRA) + 팀 커스텀.
    # en 팀은 guidelines.en.md 를 먼저 찾고, 없으면(구배포) ko 판으로 폴백.
    # 팀 커스텀(memory/team/guidelines.md)은 팀 작성물 — 번역 없이 그대로 주입.
    _gl_names = ("guidelines.en.md", "guidelines.md") if lang == "en" \
        else ("guidelines.md",)
    _infra_gl = None
    for _name in _gl_names:
        for _base in (root / "infra", _INFRA):
            if (_base / _name).is_file():
                _infra_gl = _base / _name
                break
        if _infra_gl is not None:
            break
    for _gl_path in (_infra_gl, root / "memory" / "team" / "guidelines.md"):
        if _gl_path is not None and _gl_path.is_file():
            lines.append("")
            lines.append(_gl_path.read_text(encoding="utf-8").rstrip())

    # 세션로그 규칙 — 세션당 1회, 압축 블록(≤6줄). 리마인더(compact)가 매번 장문
    # 룰셋을 싣는 대신 이 블록을 "(규칙: 세션 시작 주입 참조)"로 가리킨다(단일 소스
    # _slog_rules — 드리프트 방지, ko/en 동일 모듈). 모듈 부재 시 생략(advisory).
    if _slog_rules_mod is not None:
        _rules_fn = getattr(_slog_rules_mod, "session_log_rules", None)
        _rules = _rules_fn(lang) if callable(_rules_fn) else getattr(
            _slog_rules_mod, "SESSION_LOG_RULES", None)  # 구모듈 하위호환
        if _rules:
            lines.append("")
            lines.append(_rules)

    if index_text.strip():
        lines.append("")
        lines.append(_t("hook_ss_index_header", lang, "--- 팀 메모리 INDEX ---"))
        lines.append(index_text.rstrip())
    lines.append("")
    lines.append(_t("hook_ss_members_header", lang,
                    "--- 멤버별 최근 작업 (summary) ---"))
    if members:
        for m in members:
            summ = m["summary"] if m["summary"] else _t(
                "hook_ss_no_summary", lang, "(summary 없음 — 구로그)")
            lines.append(f"- {m['author']} [{m['date']}]: {summ}")
            lines.append(f"    file: {m['file']}")
    else:
        lines.append(_t("hook_ss_no_logs", lang,
                        "(아직 세션로그 없음 — 첫 작업부터 "
                        "memory/team/sessions/<이름>/ 에 기록하세요.)"))
    return "\n".join(lines)


def main() -> int:
    deadline = time.monotonic() + _SESSION_START_TOTAL_BUDGET
    _ensure_utf8_io()  # 한글 json 출력이 Windows cp949 stdout 에서 크래시 방지
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # 입력 오류로 세션을 막지 않는다(advisory)

    if data.get("event") != "SessionStart":
        return 0

    root = Path(_team_root())
    _warn_if_stale_home(str(root))  # 스테일 TEAMMODE_HOME 표면화(이슈 #9a) — 거동 불변
    # 팀 모드 활성 시에만 주입
    if not (root / ".teammode-active").is_file():
        return 0

    # A2: 세션 id relay 영속 — env 없는 에이전트(Codex)도 엔진 `memory unlock` 이
    # 세션 id 를 알 수 있게 한다. advisory(실패 무해).
    _persist_session_relay(data)

    # 세션당 1회 레포 최신화 — 맥락 주입 전에(최신 상태로 주입). 실패 무해(철칙).
    sync_deadline = deadline - _SESSION_CONTEXT_RESERVE
    _maybe_auto_pull(str(root), deadline=sync_deadline)
    # upstream(제품) 캐시도 스로틀 적용해 새로 고침 — 안 하면 계속 켜둔 인스턴스에서
    # 엔진 업데이트 알림이 fetch 시점 이후의 변화를 영원히 못 본다(위 함수 docstring).
    _maybe_fetch_upstream(str(root), deadline=sync_deadline)

    # 팀 locale → 주입 언어(PR-i1). config 1회 읽기 — 실패는 ko/en 폴백 계약이 흡수.
    # locale 판정용 config 1회 open — session-start 는 세션당 1회만 실행되므로
    # 무해하다(매 프롬프트 도는 session-log-remind 와 달리 재사용 최적화 불요).
    lang = _hook_lang(str(root))

    try:
        context = _build_context(root, lang, deadline=deadline)
    except Exception:  # noqa: BLE001 — 수집 실패가 세션을 막지 않는다
        return 0

    if context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
