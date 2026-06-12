#!/usr/bin/env python3
"""auto_pull — 상시 레포 최신화 (throttled auto-pull) 순수 함수 모듈.

설계(Jane 새벽 합의):
  UserPromptSubmit 훅이 매 프롬프트마다 팀 레포를 최신화하되,
  ① 스로틀로 과부하를 막고, ② **실패는 절대 작업을 막지 않는다(철칙)**.

훅 본체에 인라인하지 않고 테스트 가능한 순수 함수로 둔다(P1 교훈: 시각·경로·스로틀초를
전부 인자로 받아 env 무조건 신뢰를 피한다). session-log-remind.py 가 이 모듈의
`auto_pull()`을 리마인드 판정 **이전에** 호출한다(최신 상태로 리마인드 판단).

철칙(실패 무해)의 구현 원칙:
  - 외부에 노출되는 do_pull/auto_pull 은 **절대 예외를 전파하지 않는다**. 모든 실패는
    PullResult(ok=False)로 표현된다. 작업(사용자 프롬프트 처리)을 막을 수 있는 경로 0.
  - git pull 은 항상 `--ff-only`(충돌 시 워킹트리 오염 회피) + 자격증명 프롬프트 차단
    (GIT_TERMINAL_PROMPT=0) + 네트워크 타임아웃(기본 5s, hang 방지).
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass, field

# 기본 스로틀 — 5분. 호출부가 명시 주입할 수 있다(테스트는 항상 주입).
DEFAULT_THROTTLE_SECONDS = 300
# git 네트워크 작업의 기본 타임아웃(초) — hang 으로 프롬프트 처리를 막지 않게 한다.
DEFAULT_TIMEOUT = 5


@dataclass
class PullResult:
    ok: bool                       # pull 이 실제로 성공(ff-forward 또는 already up-to-date)
    attempted: bool = True         # 스로틀 통과해 pull 을 시도했는지
    detail: str = ""               # 디버그용 메시지(stderr 등 요약)


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


def _is_git_worktree(team_root: str) -> bool:
    """team_root 가 git 워킹트리인지 확인(자동 pull 대상이 명확한 레포여야 함, 설계 §5).

    아니면 조용히 스킵하기 위한 가드. 예외 전파 없음.
    """
    try:
        rc, out, _ = _run_git(
            ["-C", team_root, "rev-parse", "--is-inside-work-tree"],
            timeout=DEFAULT_TIMEOUT)
        return rc == 0 and out.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _git_env() -> dict:
    """git 호출 환경 — 자격증명 프롬프트·SSH 프롬프트 차단(hang 방지)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"          # https 자격증명 프롬프트 차단
    env.setdefault("GIT_SSH_COMMAND",
                   "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new "
                   "-oConnectTimeout=5")
    env.setdefault("GIT_ASKPASS", "true")     # askpass 도 즉시 빈 응답
    return env


def _http_timeout_opts(timeout: int) -> list:
    """git 자체의 네트워크 타임아웃 옵션(defense-in-depth).

    subprocess timeout 은 직접 자식(git)만 죽일 뿐 git 이 띄운 손자(git-remote-https)는
    살아남아 비라우팅 호스트에 매달릴 수 있다. git 에게도 저속/무응답을 스스로 끊게 한다.
    """
    return [
        "-c", "http.lowSpeedLimit=1000",
        "-c", f"http.lowSpeedTime={timeout}",
    ]


def _run_git(args: list, timeout: int):
    """git 을 **자체 프로세스 그룹**으로 실행하고, 타임아웃 시 그룹 전체를 죽인다.

    이유: `subprocess.run(timeout=)` 은 직접 자식(git)에만 SIGKILL 을 보내, git 이 fork 한
    git-remote-https 같은 손자가 고아로 남아 네트워크에 매달린다(적대 검수에서 실측). 새
    세션(setsid)으로 띄워 동일 PGID 로 묶고 타임아웃 시 killpg 로 손자까지 일괄 종료한다.
    """
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  stdin=subprocess.DEVNULL, text=True, env=_git_env())
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True  # 자식을 새 프로세스 그룹 리더로
    proc = subprocess.Popen(["git", *args], **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=2)
        except (subprocess.SubprocessError, OSError):
            pass
        raise


def _kill_group(proc: subprocess.Popen) -> None:
    """프로세스 그룹 전체(손자 포함)를 종료. 실패해도 예외 전파 없음."""
    try:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def do_pull(team_root: str, timeout: int = DEFAULT_TIMEOUT) -> PullResult:
    """`git pull --ff-only` 실행. 절대 예외를 전파하지 않는다(철칙).

    실패(네트워크 없음·ff 불가·충돌·타임아웃·git 아님) → PullResult(ok=False).
    """
    if not _is_git_worktree(team_root):
        return PullResult(ok=False, detail="not a git work tree")
    try:
        rc, out, err = _run_git(
            ["-C", team_root, *_http_timeout_opts(timeout),
             "pull", "--ff-only", "--no-rebase", "--no-edit"],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return PullResult(ok=False, detail="timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return PullResult(ok=False, detail=f"exec error: {exc}")
    if rc == 0:
        return PullResult(ok=True, detail=(out or "").strip()[:200])
    return PullResult(ok=False, detail=((err or out) or "").strip()[:200])


def auto_pull(team_root: str, state_path: str, now: float,
              throttle_seconds: int = DEFAULT_THROTTLE_SECONDS,
              timeout: int = DEFAULT_TIMEOUT) -> AutoPullResult:
    """조립: 스로틀 판정 → pull → 성공 시 시각 기록.

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
