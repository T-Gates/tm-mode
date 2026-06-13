#!/usr/bin/env python3
"""git_ops — teammode 의 git 작업 공통 모듈 (pull/commit/auto-pull 공유 안전장치).

설계(슬라이스 V): 어젯밤 auto_pull.py 에 박은 do_pull 안전장치(손자 git-remote-https
killpg·`--ff-only`·subprocess+git 양쪽 타임아웃·자격증명/SSH 프롬프트 차단)를 **단일
소스**로 끌어올린다. pull 동사·commit 동사·상시 auto-pull 이 같은 안전장치를 재사용해
드리프트(같은 버그를 여러 곳에서 따로 고치는 사고)를 막는다. **신규 git 코드 작성 금지**가
이 모듈의 존재 이유다.

철칙(실패 무해): 외부 노출 함수(do_pull 등)는 **절대 예외를 전파하지 않는다**. 모든 실패는
결과 객체(ok=False)로 표현된다. 작업(사용자 프롬프트 처리·동사 실행)을 막는 경로 0.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass

# git 네트워크 작업의 기본 타임아웃(초) — hang 으로 작업을 막지 않게 한다.
DEFAULT_TIMEOUT = 5


@dataclass
class PullResult:
    ok: bool                       # pull 성공(ff-forward 또는 already up-to-date)
    attempted: bool = True         # 스로틀 통과해 pull 을 시도했는지(auto-pull 용)
    detail: str = ""               # 디버그용 메시지(stderr 등 요약)


@dataclass
class CommitResult:
    ok: bool                       # commit 성공(스테이지된 변경이 커밋됨)
    committed: bool = False        # 실제 커밋이 생성됐는지(변경 없으면 False)
    pushed: bool = False           # push 까지 성공했는지(push=True 일 때만 의미)
    detail: str = ""               # 디버그용 메시지(stderr 등 요약)


def git_env() -> dict:
    """git 호출 환경 — 자격증명 프롬프트·SSH 프롬프트 차단(hang 방지)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"          # https 자격증명 프롬프트 차단
    env.setdefault("GIT_SSH_COMMAND",
                   "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new "
                   "-oConnectTimeout=5")
    env.setdefault("GIT_ASKPASS", "true")     # askpass 도 즉시 빈 응답
    return env


# 하위호환 별칭(auto_pull 내부 명명 _git_env 와 동치). 검수 가시성 위해 _ 별칭 유지.
_git_env = git_env


def http_timeout_opts(timeout: int) -> list:
    """git 자체의 네트워크 타임아웃 옵션(defense-in-depth).

    subprocess timeout 은 직접 자식(git)만 죽일 뿐 git 이 띄운 손자(git-remote-https)는
    살아남아 비라우팅 호스트에 매달릴 수 있다. git 에게도 저속/무응답을 스스로 끊게 한다.
    """
    return [
        "-c", "http.lowSpeedLimit=1000",
        "-c", f"http.lowSpeedTime={timeout}",
    ]


_http_timeout_opts = http_timeout_opts


def run_git(args: list, timeout: int):
    """git 을 **자체 프로세스 그룹**으로 실행하고, 타임아웃 시 그룹 전체를 죽인다.

    이유: `subprocess.run(timeout=)` 은 직접 자식(git)에만 SIGKILL 을 보내, git 이 fork 한
    git-remote-https 같은 손자가 고아로 남아 네트워크에 매달린다(적대 검수에서 실측). 새
    세션(setsid)으로 띄워 동일 PGID 로 묶고 타임아웃 시 killpg 로 손자까지 일괄 종료한다.
    """
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  stdin=subprocess.DEVNULL, text=True, env=git_env())
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True  # 자식을 새 프로세스 그룹 리더로
    proc = subprocess.Popen(["git", *args], **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        kill_group(proc)
        try:
            proc.communicate(timeout=2)
        except (subprocess.SubprocessError, OSError):
            pass
        raise


_run_git = run_git


def kill_group(proc: subprocess.Popen) -> None:
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


_kill_group = kill_group


def is_git_worktree(team_root: str) -> bool:
    """team_root 가 git 워킹트리인지 확인(작업 대상이 명확한 레포여야 함).

    아니면 조용히 스킵하기 위한 가드. 예외 전파 없음.
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-parse", "--is-inside-work-tree"],
            timeout=DEFAULT_TIMEOUT)
        return rc == 0 and out.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


_is_git_worktree = is_git_worktree


def do_pull(team_root: str, timeout: int = DEFAULT_TIMEOUT) -> PullResult:
    """`git pull --ff-only` 실행. 절대 예외를 전파하지 않는다(철칙).

    실패(네트워크 없음·ff 불가·충돌·타임아웃·git 아님) → PullResult(ok=False).
    """
    if not is_git_worktree(team_root):
        return PullResult(ok=False, detail="not a git work tree")
    try:
        rc, out, err = run_git(
            ["-C", team_root, *http_timeout_opts(timeout),
             "pull", "--ff-only", "--no-rebase", "--no-edit"],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return PullResult(ok=False, detail="timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return PullResult(ok=False, detail=f"exec error: {exc}")
    if rc == 0:
        return PullResult(ok=True, detail=(out or "").strip()[:200])
    return PullResult(ok=False, detail=((err or out) or "").strip()[:200])


def _has_staged_changes(team_root: str, timeout: int) -> bool:
    """스테이지에 커밋할 변경이 있는지(`git diff --cached --quiet` rc!=0 == 변경 있음)."""
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "diff", "--cached", "--quiet"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc != 0


def do_commit(team_root: str, message: str, push: bool = False,
              timeout: int = DEFAULT_TIMEOUT) -> CommitResult:
    """`git add -A && git commit -m` (+ 선택 push). 절대 예외를 전파하지 않는다(철칙).

    auto_pull/do_pull 과 같은 안전장치 재사용(git_env 자격증명 차단·killpg 타임아웃).
    - 변경 없음 → committed=False, ok=False (비치명: 레포 무손상).
    - push=True 이고 원격 없음/오프라인 → **커밋은 보존**, push 만 실패(ok 은 commit 성공
      기준으로 True, pushed=False). push 실패가 로컬 커밋을 되돌리지 않는다.
    """
    if not is_git_worktree(team_root):
        return CommitResult(ok=False, detail="not a git work tree")

    # 1) stage 전부
    try:
        rc, _, err = run_git(["-C", team_root, "add", "-A"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommitResult(ok=False, detail="add timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return CommitResult(ok=False, detail=f"add exec error: {exc}")
    if rc != 0:
        return CommitResult(ok=False, detail=f"add failed: {(err or '').strip()[:200]}")

    # 2) 변경 없으면 비치명 종료(빈 커밋 만들지 않음)
    if not _has_staged_changes(team_root, timeout):
        return CommitResult(ok=False, committed=False,
                            detail="nothing to commit")

    # 3) commit
    try:
        rc, out, err = run_git(
            ["-C", team_root, "commit", "-m", message], timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommitResult(ok=False, detail="commit timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return CommitResult(ok=False, detail=f"commit exec error: {exc}")
    if rc != 0:
        return CommitResult(ok=False, committed=False,
                            detail=f"commit failed: {((err or out) or '').strip()[:200]}")

    if not push:
        return CommitResult(ok=True, committed=True, pushed=False,
                            detail=(out or "").strip()[:200])

    # 4) push (선택). 실패해도 **커밋은 보존** — ok 은 commit 성공 기준으로 유지.
    try:
        prc, pout, perr = run_git(
            ["-C", team_root, *http_timeout_opts(timeout), "push"],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommitResult(ok=True, committed=True, pushed=False,
                            detail="committed; push timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return CommitResult(ok=True, committed=True, pushed=False,
                            detail=f"committed; push exec error: {exc}")
    if prc == 0:
        return CommitResult(ok=True, committed=True, pushed=True,
                            detail="committed and pushed")
    return CommitResult(ok=True, committed=True, pushed=False,
                        detail=f"committed; push failed: {((perr or pout) or '').strip()[:200]}")
