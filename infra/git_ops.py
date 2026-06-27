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
# 2초: pull/fetch 가 2초 초과면 비치명 실패(로컬 commit/checkout 도 2초 충분).
DEFAULT_TIMEOUT = 2


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
class SyncResult:
    ok: bool                       # 동기화 성공(덮어쓰기 완료) 또는 이미 최신
    changed: bool = False          # 실제로 working tree 가 바뀌었는지
    paths: tuple = ()              # 동기화 대상 경로(SYNC_PATHS)
    diff: str = ""                 # 변경 미리보기(dry-run) 또는 적용된 변경 요약
    detail: str = ""               # 사람이 읽는 메시지/사유
    blocked: bool = False          # dirty 가드 등으로 중단됐는지(사람 판단 필요)


def git_env() -> dict:
    """git 호출 환경 — 자격증명 프롬프트·SSH 프롬프트 차단(hang 방지).

    ⚠️ credential.helper 는 절대 끄지 않는다 — 끄면 캐시된 정상 자격증명까지 깨져
    멀쩡한 인증이 실패한다. 여기서 막는 건 **대화형 GUI 대기**(윈도우 GCM 팝업·터미널
    프롬프트·SSH 프롬프트)뿐이다. 목표: "인증 막혀도 즉시 실패 + 정상 인증은 동작".
    """
    env = dict(os.environ)
    # 로케일 고정(C) — git 의 사람용 메시지(push 거부·hint 등)를 영어로 못박는다. 비영어
    # 로케일에선 "Updates were rejected ..." 가 번역돼 _is_non_fast_forward 가 놓치고(자동
    # 복구 미발동) detail 파싱도 흔들린다. teammode 의 모든 git 호출은 결과를 코드로만 쓰고
    # (사람용 출력 의존 0) detail 은 디버그 요약이라, 전역 C 고정이 가장 견고하고 안전하다.
    env["LC_ALL"] = "C"
    env["GIT_TERMINAL_PROMPT"] = "0"          # https 자격증명 프롬프트 차단
    env.setdefault("GIT_SSH_COMMAND",
                   "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new "
                   "-oConnectTimeout=5")
    env.setdefault("GIT_ASKPASS", "true")     # askpass 도 즉시 빈 응답
    # 윈도우 Git Credential Manager(GCM) 의 GUI 인증 대기 차단(hang 방지). credential.helper
    # 자체는 건드리지 않으므로 캐시된 정상 자격증명은 그대로 쓰인다 — 막히면 즉시 실패만.
    env["GCM_INTERACTIVE"] = "0"
    env["GCM_GUI_PROMPT"] = "0"
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
                  stdin=subprocess.DEVNULL, text=True,
                  encoding="utf-8", errors="replace", env=git_env())
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True  # 자식을 새 프로세스 그룹 리더로
    # credential.interactive=false: 자격증명 helper 의 **대화형 프롬프트**만 끈다(helper
    # 자체는 유지 — 캐시된 정상 자격증명은 그대로). git_env 의 GCM_* 차단과 이중 방어로
    # "인증 막혀도 즉시 실패 + 정상 인증 동작"을 보장한다. 모든 git 호출에 선행 적용.
    proc = subprocess.Popen(
        ["git", "-c", "credential.interactive=false", *args], **kwargs)
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


def _is_non_fast_forward(text: str) -> bool:
    """push 출력(stderr/stdout)이 **non-fast-forward 거부**인지 판정. 무raise.

    behind(다른 기기가 먼저 push) 로 로컬이 뒤처지면 git 은 push 를 거부한다 — 이때만
    fetch+rebase 자동 복구를 트리거한다. 인증·네트워크 실패 등 다른 거부와 구분하려고
    git 의 거부 메시지 패턴으로 좁게 감지한다(오탐 시 멀쩡한 실패에 rebase 를 걸 위험).
    감지 패턴: `[rejected]`, `non-fast-forward`, `fetch first`, `Updates were rejected`.
    """
    if not text:
        return False
    low = text.lower()
    return ("non-fast-forward" in low
            or "fetch first" in low
            or "updates were rejected" in low
            or "[rejected]" in low)


def _abort_rebase(team_root: str, timeout: int) -> None:
    """진행중 rebase 를 취소해 원상복구(`git rebase --abort`). 무raise(best-effort).

    rebase 가 충돌·타임아웃·예외로 실패하면 `.git/rebase-merge` 같은 진행중 상태가
    남아 레포가 어정쩡해진다. 비차단 반환 전에 반드시 호출해 로컬 커밋/워킹트리를
    원래대로 되돌린다. abort 자체의 실패도 삼킨다(더 할 수 있는 게 없으므로).
    """
    try:
        run_git(["-C", team_root, "rebase", "--abort"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass


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
    - `paths=[...]` → **지목된 경로만** `add -- <paths>` + **`commit -- <paths>`(pathspec
      partial commit)**. add 로 그 경로만 스테이징할 뿐 아니라, commit 도 pathspec 으로
      한정해 **사용자가 미리 staged 해 둔 다른 경로(코드 등)는 커밋에서 제외**한다
      (tm off 가 "세션로그만 커밋"을 보장 — 의도 안 한 워킹트리 휩쓸기 방지).
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

    # 3) commit — paths 지정 시 pathspec partial commit(미리 staged 된 다른 경로 제외).
    commit_args = ["-C", team_root, "commit", "-m", message]
    if paths:
        commit_args += ["--", *[str(p) for p in paths]]
    try:
        rc, out, err = run_git(commit_args, timeout=timeout)
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

    # 4-1) non-ff 거부면 자동 복구: fetch → rebase → 재push 1회.
    #      다른 기기가 먼저 push 해 로컬이 behind 일 때 발생. non-ff 가 아닌 실패(인증·
    #      네트워크 등)는 자동 복구 대상이 아니므로 기존대로 비차단 반환한다.
    #      partial-commit(paths=) 시 워킹트리에 커밋 안 된 다른 추적파일 변경이 남을 수
    #      있다(auto-commit 의 주 패턴). 평문 rebase 는 그 dirty 상태를 "unstaged changes"
    #      로 거부하므로, --autostash 로 stash→rebase→pop 해 dirty 를 흡수·보존한다(충돌·
    #      abort 시에도 autostash 가 자동 원복).
    if _is_non_fast_forward((perr or "") + "\n" + (pout or "")):
        # fetch (push 와 동일하게 http 타임아웃 옵션 적용). 실패해도 예외 전파 0.
        try:
            frc, _, ferr = run_git(
                ["-C", team_root, *http_timeout_opts(timeout), "fetch"],
                timeout=timeout)
        except subprocess.TimeoutExpired:
            return CommitResult(ok=True, committed=True, pushed=False,
                                detail="committed; rebase fetch timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return CommitResult(ok=True, committed=True, pushed=False,
                                detail=f"committed; rebase fetch exec error: {exc}")
        if frc == 0:
            # rebase (추적 upstream 위로). 충돌 등 실패 시 반드시 --abort 로 원상복구.
            try:
                rrc, _, rerr = run_git(
                    ["-C", team_root, "rebase", "--autostash"], timeout=timeout)
            except subprocess.TimeoutExpired:
                _abort_rebase(team_root, timeout)
                return CommitResult(ok=True, committed=True, pushed=False,
                                    detail="committed; rebase timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                _abort_rebase(team_root, timeout)
                return CommitResult(ok=True, committed=True, pushed=False,
                                    detail=f"committed; rebase exec error: {exc}")
            if rrc == 0:
                # rebase 성공 → 재push 1회.
                try:
                    p2rc, p2out, p2err = run_git(
                        ["-C", team_root, *http_timeout_opts(timeout), "push"],
                        timeout=timeout)
                except subprocess.TimeoutExpired:
                    return CommitResult(ok=True, committed=True, pushed=False,
                                        detail="committed; rebased but re-push timeout")
                except (OSError, subprocess.SubprocessError) as exc:
                    return CommitResult(ok=True, committed=True, pushed=False,
                                        detail=f"committed; rebased but re-push exec error: {exc}")
                if p2rc == 0:
                    return CommitResult(ok=True, committed=True, pushed=True,
                                        detail="committed; rebased and pushed")
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail=f"committed; rebased but re-push failed: {((p2err or p2out) or '').strip()[:200]}")
            # rebase 실패(충돌 등) → abort 로 원상복구 후 비차단 반환.
            _abort_rebase(team_root, timeout)
            return CommitResult(
                ok=True, committed=True, pushed=False,
                detail=f"committed; rebase failed (aborted): {(rerr or '').strip()[:200]}")

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

    **merge 하지 않는다** — 적용은 명시적 update 동사 몫(은수 합의: fetch 만 자동).
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


def has_common_ancestor(team_root: str, upstream_ref: str = "upstream/main",
                        timeout: int = DEFAULT_TIMEOUT) -> bool:
    """HEAD 와 upstream_ref 사이에 공통 조상이 있는지 확인. 알 수 없으면 True(보수적).

    `git merge-base --is-ancestor` 대신 `git merge-base HEAD <ref>` 를 써서 exit code 로
    판정한다 — exit 0 = 공통 조상 있음, exit 1 = 없음(unrelated histories), 그 외(bad
    ref·git 오류 등) = **알 수 없음 → 보수적으로 True**(억제 안 함). GitHub template 으로
    생성한 레포는 upstream 과 공통 조상이 0이라 exit 1 → False.
    """
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "merge-base", "HEAD", upstream_ref],
            timeout=timeout)
        if rc == 0:
            return True   # 공통 조상 있음
        if rc == 1:
            return False  # unrelated histories(공통 조상 없음) — template 레포
        return True       # bad ref·기타 git 오류 → 알 수 없음, 보수적으로 억제 안 함
    except (OSError, subprocess.SubprocessError):
        return True  # 알 수 없으면 보수적으로 True(억제 안 함)


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


# ──────────────────────────────────────────────────────────────────
# 슬라이스 T2 — 파일 동기화 기반 update (merge 대체)
# ──────────────────────────────────────────────────────────────────
#
# 왜 merge 가 아니라 파일 동기화인가:
#   도입 레포는 GitHub *template* 으로 생성돼 upstream(T-Gates/tm-mode)과 공통 조상이
#   0이다(unrelated histories). 그래서 `git merge`/`pull --ff-only` 는 영원히
#   `fatal: refusing to merge unrelated histories` 로 막힌다. → merge 를 버리고
#   upstream 에서 **엔진 파일만** `git checkout` 으로 덮어쓰는 파일 동기화로 바꾼다.
#   히스토리 관계(공통 조상)와 무관하게 동작한다.

# 동기화 대상 = 엔진 경로(infra/) + 업스트림 소유 공지(NOTICE.md).
# ⚠️ memory/·team.config.json·.git·팀 소유 파일은 절대 제외.
# NOTICE.md 는 **업스트림(템플릿) 소유** 파일 — update 가 갱신해야 로컬 NOTICE 가 upstream 과
# 같아져 tm ON 의 "최신 업데이트" 알림이 (받은 뒤) 조용해진다. 빠지면 영구 도배(P1).
# 나중에 확장 가능하게 모듈 상수로 둔다(예: 새 엔진 디렉토리 추가 시 여기만 고친다).
SYNC_PATHS = ["infra", "NOTICE.md"]


def detect_default_branch(team_root: str, remote: str = "upstream",
                          timeout: int = DEFAULT_TIMEOUT) -> str:
    """upstream 의 기본 브랜치명을 감지(로컬 ref 우선·네트워크 없음). 폴백 'main'.

    탐지 순서(전부 로컬·무raise — hang 금지):
      1. `git symbolic-ref refs/remotes/<remote>/HEAD` → `refs/remotes/<remote>/main`
         (clone/fetch 가 설정해두는 origin/HEAD 류). 끝 세그먼트가 브랜치명.
      2. 그래도 모르면 `refs/remotes/<remote>/main` 이 존재하면 'main'.
      3. 둘 다 실패 → 'main' 폴백(은수 결정: main 가정하되 가능하면 감지).
    `git remote show`(네트워크·hang 위험)는 쓰지 않는다.
    """
    # 1) symbolic-ref (로컬, clone 이 설정)
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "symbolic-ref",
             f"refs/remotes/{remote}/HEAD"], timeout=timeout)
        if rc == 0:
            ref = (out or "").strip()
            # refs/remotes/upstream/main → main
            prefix = f"refs/remotes/{remote}/"
            if ref.startswith(prefix):
                branch = ref[len(prefix):]
                if branch:
                    return branch
    except (OSError, subprocess.SubprocessError):
        pass
    # 2) main ref 존재 확인
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "rev-parse", "--verify", "--quiet",
             f"refs/remotes/{remote}/main"], timeout=timeout)
        if rc == 0:
            return "main"
    except (OSError, subprocess.SubprocessError):
        pass
    # 3) 폴백
    return "main"


def _paths_dirty(team_root: str, paths: list, timeout: int) -> bool:
    """대상 경로에 커밋 안 된 로컬 변경(staged+unstaged+untracked)이 있는지.

    `git status --porcelain -- <paths>` 가 비어 있지 않으면 dirty. 덮어쓰기로 유실될
    변경을 사전에 잡는 가드용. 예외/실패는 보수적으로 dirty 로 본다(중단이 안전).
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "status", "--porcelain", "--",
             *[str(p) for p in paths]], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return True  # 알 수 없으면 보수적으로 dirty 취급(덮어쓰기 막음)
    if rc != 0:
        return True
    return bool((out or "").strip())


def diff_paths(team_root: str, ref: str, paths: list,
               timeout: int = DEFAULT_TIMEOUT) -> str:
    """working tree(HEAD) 대비 <ref> 의 대상 경로 변경 요약(name-status). 무raise.

    `git diff --name-status <ref> -- <paths>` — 어떤 파일이 추가/수정/삭제되는지.
    dry-run 미리보기와 적용 후 요약에 함께 쓴다(엔진은 요약 안 함 — git 원본 전달).
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "diff", "--name-status", ref, "--",
             *[str(p) for p in paths]], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if rc != 0:
        return ""
    return (out or "").strip()


def _path_in_ref(team_root: str, ref: str, path: str, timeout: int) -> bool:
    """<ref> 에 <path>(파일/디렉토리)가 존재하는지. `git cat-file -e <ref>:<path>`. 무raise.

    NOTICE.md 등은 옛 upstream 엔 없을 수 있다. 없는 pathspec 으로 checkout 하면 "did
    not match" 에러가 나므로, 동기화 전에 실재 경로만 골라 옛 upstream 과도 호환시킨다.
    """
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "cat-file", "-e", f"{ref}:{path}"], timeout=timeout)
        return rc == 0
    except (OSError, subprocess.SubprocessError):
        return False


def sync_from_upstream(team_root: str, remote: str = "upstream",
                       branch: str | None = None,
                       paths: list | None = None,
                       dry_run: bool = False,
                       timeout: int = DEFAULT_TIMEOUT) -> SyncResult:
    """upstream 의 엔진 경로(SYNC_PATHS)를 working tree 로 덮어써 동기화. 무raise(철칙).

    merge 를 쓰지 않으므로 unrelated histories 와 무관하게 동작한다. 흐름:
      1. fetch <remote> (fetch_upstream 재사용 — 안전장치 공유).
      2. 기본 브랜치 감지(branch 미지정 시 detect_default_branch).
      3. diff 로 변경 유무 판단 — 없으면 멱등(ok=True, changed=False, "이미 최신").
      4. dirty 가드: 대상 경로에 커밋 안 된 로컬 변경이 있으면 **중단**
         (blocked=True, ok=False) — 덮어쓰기로 유실되므로 사람 판단 요청.
      5. dry_run 이면 diff 만 채워 반환(실제 변경 0).
      6. `git checkout <remote>/<branch> -- <paths>` 로 덮어쓰기(staged 됨).
         ※ 자동 commit/push 는 하지 않는다 — staged 로 두고 사람 검토(상위 정책).
    """
    if paths is None:
        paths = SYNC_PATHS

    if not is_git_worktree(team_root):
        return SyncResult(ok=False, paths=tuple(paths),
                          detail="not a git work tree")

    # 1) fetch — 재사용(자격증명 차단·killpg·http 타임아웃 등 안전장치 공유)
    fr = fetch_upstream(team_root, remote=remote, timeout=timeout)
    if not fr.ok:
        return SyncResult(ok=False, paths=tuple(paths),
                          detail=f"fetch 실패: {fr.detail}")

    # 2) 기본 브랜치 감지
    if branch is None:
        branch = detect_default_branch(team_root, remote=remote, timeout=timeout)
    ref = f"{remote}/{branch}"

    # ref 가 실재하는지 확인(감지 폴백이 빗나갔을 수 있음)
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "rev-parse", "--verify", "--quiet", ref],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        rc = 1
    if rc != 0:
        return SyncResult(ok=False, paths=tuple(paths),
                          detail=f"upstream 브랜치를 찾을 수 없습니다: {ref}")

    # 2.5) upstream 에 실재하는 경로만 동기화 — NOTICE.md 등은 옛 upstream 에 없을 수
    #      있고, 없는 pathspec 으로 checkout 하면 매칭 0 에러가 난다. 존재 경로만 골라
    #      옛 upstream 과도 호환(infra 는 받고, 없는 NOTICE 는 조용히 건너뜀).
    paths = [p for p in paths if _path_in_ref(team_root, ref, p, timeout)]
    if not paths:
        return SyncResult(ok=True, changed=False, paths=(),
                          detail="이미 최신")

    # 3) 변경 유무 — 없으면 멱등 종료
    diff = diff_paths(team_root, ref, paths, timeout=timeout)
    if not diff:
        return SyncResult(ok=True, changed=False, paths=tuple(paths),
                          detail="이미 최신")

    # 4) dirty 가드 — 덮어쓰기로 유실될 로컬 변경 차단(사람 판단 요청)
    if _paths_dirty(team_root, paths, timeout):
        return SyncResult(ok=False, blocked=True, paths=tuple(paths), diff=diff,
                          detail="대상 경로에 커밋 안 된 로컬 변경이 있습니다")

    # 5) dry-run — 미리보기만, 실제 변경 0
    if dry_run:
        return SyncResult(ok=True, changed=False, paths=tuple(paths), diff=diff,
                          detail="dry-run: 변경 미리보기")

    # 6) checkout 덮어쓰기(staged). 자동 commit/push 없음.
    try:
        rc, out, err = run_git(
            ["-C", team_root, "checkout", ref, "--",
             *[str(p) for p in paths]], timeout=timeout)
    except subprocess.TimeoutExpired:
        return SyncResult(ok=False, paths=tuple(paths), detail="checkout timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return SyncResult(ok=False, paths=tuple(paths),
                          detail=f"checkout exec error: {exc}")
    if rc != 0:
        return SyncResult(ok=False, paths=tuple(paths),
                          detail=f"checkout 실패: {((err or out) or '').strip()[:200]}")

    return SyncResult(ok=True, changed=True, paths=tuple(paths), diff=diff,
                      detail="동기화 완료(staged)")


# ──────────────────────────────────────────────────────────────────
# 슬라이스 T3 — upstream NOTICE 읽기 (공지 파일 기반 알림)
# ──────────────────────────────────────────────────────────────────
#
# 왜 git 커밋 비교를 안 하나:
#   GitHub template 생성 레포는 upstream 과 공통 조상이 0(unrelated histories)이라
#   `git rev-list HEAD..upstream` 이 upstream 의 모든 커밋을 반환한다. behind 숫자가
#   실제 "뒤처진 커밋 수"를 뜻하지 않으므로 대신 upstream 에 있는 NOTICE.md 파일을
#   직접 읽어 비교한다 — `git show <remote>/<branch>:NOTICE.md`. 공통 조상 없어도 동작.

def read_upstream_notice(team_root: str, remote: str = "upstream",
                         branch: str | None = None,
                         timeout: int = DEFAULT_TIMEOUT) -> str:
    """upstream 의 NOTICE.md 내용을 읽는다. 무raise(없거나 오류면 빈 문자열).

    `git show <remote>/<branch>:NOTICE.md` 를 사용한다 — `git checkout`(파일 수정) 없이
    upstream 의 파일 내용만 읽는다. unrelated histories 와 무관하게 동작한다.
    fetch 는 호출부 책임(fetch_upstream 재사용). 파일 없음·오류는 조용히 빈 문자열 반환.
    """
    try:
        if not is_git_worktree(team_root):
            return ""
        if branch is None:
            branch = detect_default_branch(team_root, remote=remote, timeout=timeout)
        ref = f"{remote}/{branch}:NOTICE.md"
        rc, out, _ = run_git(
            ["-C", team_root, "show", ref],
            timeout=timeout)
        if rc != 0:
            return ""
        return (out or "")
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return ""
