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


@dataclass
class FetchResult:
    ok: bool                       # fetch 성공
    detail: str = ""               # 디버그용 메시지


@dataclass
class UpdateResult:
    ok: bool                       # update(merge) 성공 또는 이미 최신
    merged: bool = False           # 실제 머지가 일어났는지
    detail: str = ""               # 디버그용 메시지


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
    """프로세스 그룹/트리 전체(손자 포함)를 종료. 실패해도 예외 전파 없음."""
    try:
        if os.name == "nt":
            # 윈도우: setsid/killpg 부재. git 이 띄운 손자(git-remote-https·credential
            # helper)가 stdout 파이프를 잡은 채 남으면 communicate 가 hang(윈도우 도그푸딩서
            # UserPromptSubmit 훅 7분 멈춤으로 실측). PID 트리 전체(/T)를 강제 종료한다.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=5)
        elif hasattr(os, "killpg") and hasattr(os, "getpgid"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError, subprocess.SubprocessError):
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
              timeout: int = DEFAULT_TIMEOUT, paths: list | None = None) -> CommitResult:
    """`git add` + `git commit -m` (+ 선택 push). 절대 예외를 전파하지 않는다(철칙).

    auto_pull/do_pull 과 같은 안전장치 재사용(git_env 자격증명 차단·killpg 타임아웃).
    - 변경 없음 → committed=False, ok=False (비치명: 레포 무손상).
    - push=True 이고 원격 없음/오프라인 → **커밋은 보존**, push 만 실패(ok 은 commit 성공
      기준으로 True, pushed=False). push 실패가 로컬 커밋을 되돌리지 않는다.

    스테이징 범위(L2-G P1-4):
    - `paths=None`(기본) → 종래대로 `add -A`(commit 동사용 — 사용자가 의도적으로 호출).
    - `paths=[...]` → **지목된 경로만** `add -- <paths>`(자동 훅용 — 무차별 스테이징 금지).
      auto-commit.py 가 정규스키마의 `files` 만 넘겨 토큰패턴 등 무관 파일 오염을 막는다.
      빈 리스트(`[]`)는 스테이징할 파일이 없는 것 → 변경 없음으로 우아하게 종료.
    """
    if not is_git_worktree(team_root):
        return CommitResult(ok=False, detail="not a git work tree")

    # 1) stage — paths 지정 시 그 경로만, None 이면 전부(add -A)
    if paths is None:
        add_args = ["-C", team_root, "add", "-A"]
    else:
        if not paths:
            return CommitResult(ok=False, committed=False,
                                detail="no paths to stage")
        # `--` 로 경로 인자를 옵션과 분리(선두 대시 파일명이 옵션으로 오인되지 않게).
        add_args = ["-C", team_root, "add", "--", *[str(p) for p in paths]]
    try:
        rc, _, err = run_git(add_args, timeout=timeout)
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


# ──────────────────────────────────────────────────────────────────
# 슬라이스 T — 템플릿 풀 (upstream fetch + 명시적 update)
# ──────────────────────────────────────────────────────────────────

def _has_remote(team_root: str, remote: str, timeout: int) -> bool:
    """remote 가 설정돼 있는지. 예외 전파 없음."""
    try:
        rc, out, _ = run_git(["-C", team_root, "remote"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    if rc != 0:
        return False
    return remote in out.split()


def fetch_upstream(team_root: str, remote: str = "upstream",
                   timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    """upstream(템플릿 원본)을 **fetch 만** 한다. 절대 예외 전파 없음(철칙).

    **merge 하지 않는다** — 적용은 명시적 update 동사 몫(Jane 합의: fetch 만 자동).
    upstream remote 미설정·오프라인·git 아님 → ok=False (우아한 축소, on 막지 않음).
    """
    if not is_git_worktree(team_root):
        return FetchResult(ok=False, detail="not a git work tree")
    if not _has_remote(team_root, remote, timeout):
        return FetchResult(ok=False, detail=f"no '{remote}' remote")
    try:
        rc, out, err = run_git(
            ["-C", team_root, *http_timeout_opts(timeout),
             "fetch", "--quiet", remote],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return FetchResult(ok=False, detail="fetch timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return FetchResult(ok=False, detail=f"fetch exec error: {exc}")
    if rc == 0:
        return FetchResult(ok=True, detail="fetched")
    return FetchResult(ok=False, detail=((err or out) or "").strip()[:200])


def count_behind(team_root: str, upstream_ref: str = "upstream/main",
                 timeout: int = DEFAULT_TIMEOUT) -> int:
    """HEAD 가 upstream_ref 대비 몇 커밋 behind 인지. 알 수 없으면 0(보수적·무raise).

    `git rev-list --count HEAD..upstream_ref` — upstream 에만 있는 커밋 수.
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-list", "--count", f"HEAD..{upstream_ref}"],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 0
    if rc != 0:
        return 0
    try:
        return int((out or "0").strip())
    except ValueError:
        return 0


def upstream_changes(team_root: str, upstream_ref: str = "upstream/main",
                     limit: int = 20, timeout: int = DEFAULT_TIMEOUT) -> str:
    """upstream 에만 있는 들어올 커밋들의 한 줄 로그(변경목록). 무raise(실패 시 빈 문자열).

    엔진은 요약하지 않는다 — git log 원본을 그대로 옮긴다(판단은 스킬/사람).
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "log", "--oneline", f"--max-count={limit}",
             f"HEAD..{upstream_ref}"],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if rc != 0:
        return ""
    return (out or "").strip()


def update_from_upstream(team_root: str, upstream_ref: str = "upstream/main",
                         allow_unrelated: bool = False,
                         timeout: int = DEFAULT_TIMEOUT) -> UpdateResult:
    """upstream_ref 를 **명시적으로** merge 한다(merge --ff-only 우선). 무raise(철칙).

    fetch 와 분리된 의도적 적용 단계(자동 merge 금지 원칙). ff 불가(divergent)면 워킹트리를
    오염시키지 않고 실패로 알린다. 첫 병합(unrelated histories)은 allow_unrelated 로 옵트인.
    이미 최신이면 ok=True, merged=False.
    """
    if not is_git_worktree(team_root):
        return UpdateResult(ok=False, detail="not a git work tree")

    behind = count_behind(team_root, upstream_ref, timeout)
    if behind == 0:
        # 이미 최신이거나 upstream_ref 를 모름 — 둘 다 비치명. merge 시도 안 함.
        return UpdateResult(ok=True, merged=False, detail="already up-to-date or no upstream")

    args = ["-C", team_root, "merge", "--ff-only", "--no-edit"]
    if allow_unrelated:
        args.append("--allow-unrelated-histories")
    args.append(upstream_ref)
    try:
        rc, out, err = run_git(args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return UpdateResult(ok=False, detail="merge timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return UpdateResult(ok=False, detail=f"merge exec error: {exc}")
    if rc == 0:
        return UpdateResult(ok=True, merged=True, detail=(out or "").strip()[:200])
    # ff 불가(divergent)·기타 — 워킹트리 무오염, 비치명 실패.
    return UpdateResult(ok=False, merged=False,
                        detail=((err or out) or "").strip()[:200])
