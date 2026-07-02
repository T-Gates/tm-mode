#!/usr/bin/env python3
"""auto_pull — 상시 레포 최신화 (throttled auto-pull) 순수 함수 모듈.

설계(은수 새벽 합의):
  UserPromptSubmit 훅이 매 프롬프트마다 팀 레포를 최신화하되,
  ① 스로틀로 과부하를 막고, ② **실패는 절대 작업을 막지 않는다(철칙)**.

V.3 리팩토링: git 작업 안전장치(do_pull·손자 killpg·ff-only·타임아웃·자격증명 차단)는
공통 모듈 `infra/git_ops.py` 로 이관됐다. 이 모듈은 스로틀(should_pull)·시각 기록·조립
(auto_pull)만 담당하고 git 작업은 git_ops 를 **재사용**한다(중복=드리프트 방지). 기존
공개 API(do_pull/PullResult/DEFAULT_TIMEOUT)는 git_ops 의 것을 re-export 해 호환 유지.

철칙(실패 무해)의 구현 원칙:
  - 외부에 노출되는 do_pull/auto_pull 은 **절대 예외를 전파하지 않는다**. 모든 실패는
    결과 객체(ok=False)로 표현된다. 작업(사용자 프롬프트 처리)을 막을 수 있는 경로 0.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# git 작업 공통 모듈(infra/git_ops.py)을 재사용 — 형제 디렉토리(infra/)에서 import.
# 훅은 hooks/ 에서 직접 실행될 수도, infra/ 가 sys.path 인 채로 import 될 수도 있어
# 양쪽 경로를 보강한 뒤 import 한다.
_INFRA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _INFRA not in sys.path:
    sys.path.insert(0, _INFRA)
import git_ops as _git_ops  # noqa: E402

# 공개 API re-export(호환): 기존 호출부·테스트가 auto_pull.do_pull/PullResult 를 쓴다.
PullResult = _git_ops.PullResult
do_pull = _git_ops.do_pull
DEFAULT_TIMEOUT = _git_ops.DEFAULT_TIMEOUT  # ⚠️ 로컬용 2s(호환 유지) — 네트워크 호출(pull/push/fetch)엔 NET_TIMEOUT 을 쓸 것
NET_TIMEOUT = _git_ops.NET_TIMEOUT

# 기본 스로틀 — 5분. 호출부가 명시 주입할 수 있다(테스트는 항상 주입).
DEFAULT_THROTTLE_SECONDS = 300


@dataclass
class AutoPullResult:
    ok: bool
    attempted: bool
    detail: str = ""
    warnings: list = field(default_factory=list)


def should_pull(state_path: str, now: float, throttle_seconds: int) -> bool:
    """스로틀 판정: 마지막 pull 시각(state_path) 과 now 를 비교.

    - 상태 파일 없음        → True (한 번도 pull 안 함)
    - 경과 ≥ throttle       → True
    - 경과 < throttle       → False (스로틀)
    - 상태 파일 손상/읽기 실패 → True (스로틀을 모르면 보수적으로 막지 않음)

    어떤 경우에도 예외를 던지지 않는다.
    """
    try:
        with open(state_path, encoding="utf-8") as f:
            last = float(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return True
    return (now - last) >= throttle_seconds


def _record_pull_time(state_path: str, now: float) -> None:
    """마지막 pull 시각을 기록. 실패해도 예외를 전파하지 않는다(철칙)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(repr(now))
    except OSError:
        pass  # 상태 기록 실패는 작업을 막지 않는다 — 다음 프롬프트에서 재시도될 뿐


def auto_pull(team_root: str, state_path: str, now: float,
              throttle_seconds: int = DEFAULT_THROTTLE_SECONDS,
              timeout: int = NET_TIMEOUT) -> AutoPullResult:
    """조립: 스로틀 판정 → pull(git_ops) → 성공/시도 시각 기록.

    **철칙**: 어떤 단계에서 무슨 일이 나도 예외를 전파하지 않는다 — 사용자 프롬프트
    처리를 막을 수 있는 경로를 0으로 만든다. 모든 결과는 AutoPullResult 로 표현.
    """
    warnings: list = []
    try:
        if not should_pull(state_path, now, throttle_seconds):
            return AutoPullResult(ok=True, attempted=False, detail="throttled")

        # 스로틀은 **시도(attempt) 단위**로 기록한다 — 성공만 기록하면 원격이 죽어 있을
        # 때 매 프롬프트가 (최대 timeout 초) pull 을 재시도하며 작업에 세금을 물린다.
        # 시도 직전에 시각을 박아 두면, 원격 장애 시에도 throttle 창당 1회만 비용을 낸다.
        # (do_pull 은 절대 raise 하지 않으므로 기록 후 호출이 안전하다.)
        _record_pull_time(state_path, now)

        result = do_pull(team_root, timeout=timeout)
        if not result.ok:
            warnings.append(f"[auto-pull] skipped: {result.detail}")
        return AutoPullResult(ok=result.ok, attempted=True,
                              detail=result.detail, warnings=warnings)
    except Exception as exc:  # noqa: BLE001 — 철칙: 무슨 예외든 작업을 막지 않는다
        return AutoPullResult(ok=False, attempted=True,
                              detail=f"unexpected: {exc}",
                              warnings=[f"[auto-pull] unexpected error: {exc}"])
