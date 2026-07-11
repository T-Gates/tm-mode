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

import hashlib
import json
import os
import signal
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# git 로컬 작업의 기본 타임아웃(초) — hang 으로 작업을 막지 않게 한다.
# 2초: 로컬 commit/checkout/rev-list 류는 2초면 충분(세션 시작 스냅함 유지).
DEFAULT_TIMEOUT = 2

# git 네트워크 작업(push/pull/fetch/ls-remote)의 기본 타임아웃(초).
# 실 GitHub SSH 왕복은 평시에도 2~3초+ 걸려 2초 컷이 멀쩡한 push/pull 을 죽였다
# (이슈 #33). 로컬 동사는 DEFAULT_TIMEOUT(2s) 유지 — 세션 시작을 굼뜨게 하지 않는다.
# ⚠️ http_timeout_opts(http.lowSpeedTime)는 HTTPS 전용이라 SSH 원격에선 무력 —
# subprocess killpg(run_git)가 SSH 의 **유일한** hang 가드다.
NET_TIMEOUT = 10

# do_commit(push=True)의 **진입 앵커 벽시계 총예산**(초) — 데드라인은 함수 **진입**
# 시점에 시작돼 로컬 단계(rev-parse·add·staged-diff·commit, 최악 ~8s)도 벽시계 예산을
# 소모하고, 네트워크 단계(push·복구 체인 push→push -u→fetch→rebase→push -u, 최악
# NET_TIMEOUT 10s ×5 순차 ~50s)는 **남은 예산만** 쓴다. 로컬 하위호출 자체는 예산으로
# 개별 클램프/중단하지 않는다(로컬 커밋은 항상 완주·보존 — 건너뛸 수 있는 건 네트워크뿐).
# 22 인 이유: NET_TIMEOUT(10s) 두 다리 + rebase 의 복구 체인은 살리면서(20 이면 fetch+
# rebase+재push 복구가 굶는다), 22 + 드문 kill-drain/abort 꼬리(~6s) + 훅 기동이 훅
# manifest 캡(40s) 아래 머문다 — 초과 시 hook runner 가 프로세스를 죽여 로컬 커밋/rebase
# 뒤의 sync-warning 마커가 유실된다(codex 재리뷰 P1·A1).
PUSH_TOTAL_BUDGET = 22


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
class ReconcileResult:
    ok: bool                       # 정합 성공(이미 최신 포함) 또는 정합 불필요
    action: str = "noop"           # up-to-date|fast-forward|rebased|ahead-only|
    #                                no-upstream|fetch-failed|conflict|not-worktree|error
    ahead: int = 0                 # 정합 후 로컬이 upstream 보다 앞선(미push) 커밋 수
    behind: int = 0                # 정합 전 behind(진단·표면화용)
    diverged: bool = False         # 정합 전 ahead>0 & behind>0(rebase 가 필요했음)
    detail: str = ""               # 사람이 읽는 사유/요약


@dataclass
class SyncResult:
    ok: bool                       # 동기화 성공(덮어쓰기 완료) 또는 이미 최신
    changed: bool = False          # 실제로 working tree 가 바뀌었는지
    paths: tuple = ()              # positive 경로(표시/논리용 — cmd_update 출력)
    diff: str = ""                 # 변경 미리보기(dry-run) 또는 적용된 변경 요약
    detail: str = ""               # 사람이 읽는 메시지/사유
    blocked: bool = False          # dirty 가드 등으로 중단됐는지(사람 판단 필요)
    pathspecs: tuple = ()          # git 실행용(positive + :(exclude)... — #36).
                                   # do_commit(paths=res.pathspecs)·checkout·diff·dirty 가
                                   # 이걸 써서 infra/skills/util(인스턴스 소유)을 보존한다.


@dataclass
class WorkflowStripResult:
    ok: bool
    changed: bool = False
    committed: bool = False
    pushed: bool = False
    skipped_product: bool = False
    detail: str = ""


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

    하한 1s(codex A1과 같은 클램프 불변식): timeout<=0 이 그대로 들어가면
    lowSpeedTime=0 은 curl 의 저속 감지를 **끄고**, 음수는 config 값으로 부적합하다
    — 이 defense-in-depth 가 조용히 무력화되지 않게 여기서도 바닥을 깐다.
    """
    return [
        "-c", "http.lowSpeedLimit=1000",
        "-c", f"http.lowSpeedTime={max(1, timeout)}",
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


def do_pull(team_root: str, timeout: int = NET_TIMEOUT) -> PullResult:
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


def _ahead_behind_raw(team_root: str, timeout: int):
    """(ahead, behind, has_upstream) 를 반환. 무raise.

    `git rev-list --count --left-right @{u}...HEAD` — left=@{u}만 가진 커밋(=behind),
    right=HEAD만 가진 커밋(=ahead). 추적 upstream 미설정·git 오류 → (0, 0, False).
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-list", "--count", "--left-right",
             "@{u}...HEAD"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return (0, 0, False)
    if rc != 0:
        return (0, 0, False)   # 보통 추적 upstream 없음(@{u} 해석 실패)
    parts = (out or "").split()
    if len(parts) != 2:
        return (0, 0, False)
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return (0, 0, False)
    return (ahead, behind, True)


def ahead_behind(team_root: str, timeout: int = DEFAULT_TIMEOUT):
    """추적 upstream(origin) 대비 (ahead, behind) 커밋 수. 무raise(모르면 (0,0)).

    read-only 진단용 — 배너/세션 맥락에 'origin 동기화: ahead N/behind M' 한 줄로
    upstream(템플릿) 업데이트 상태와 **분리** 표시하기 위함(이슈 #23).
    """
    ahead, behind, _ = _ahead_behind_raw(team_root, timeout)
    return (ahead, behind)


def do_reconcile(team_root: str, timeout: int = NET_TIMEOUT) -> ReconcileResult:
    """fetch 후 추적 upstream 과 **실제 정합**(ff 또는 rebase --autostash). 무raise(철칙).

    do_pull 의 `pull --ff-only` 는 로컬이 diverge(ahead>0 & behind>0)면 조용히 실패해
    멀티유저 환경에서 로컬 커밋만 쌓이게 만든다(이슈 #23). do_reconcile 은 diverge 도
    rebase 로 정합하고, 충돌이면 **abort 후 conflict 로 표면화**(조용히 넘기지 않음).
    세션 시작 1회용 — 호출 빈도는 상위(session-start 스로틀)가 통제한다.

    분기:
      - 추적 upstream 없음 → no-upstream(정합 불필요, ok=True).
      - behind==0 → up-to-date(ahead 0) 또는 ahead-only(미push 로컬만 있음). ok=True.
      - ahead==0 & behind>0 → fast-forward(`merge --ff-only @{u}`). ok=True.
      - ahead>0 & behind>0(diverge) → `rebase --autostash @{u}`.
          성공 → rebased(남은 ahead 재계산). 충돌/실패 → abort 후 conflict(ok=False).
    """
    if not is_git_worktree(team_root):
        return ReconcileResult(ok=False, action="not-worktree",
                               detail="not a git work tree")

    # 1) fetch — push/pull 과 동일 안전장치(http 타임아웃·killpg·자격증명 차단) 재사용.
    try:
        frc, _, ferr = run_git(
            ["-C", team_root, *http_timeout_opts(timeout), "fetch"],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return ReconcileResult(ok=False, action="fetch-failed", detail="fetch timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return ReconcileResult(ok=False, action="fetch-failed",
                               detail=f"fetch exec error: {exc}")
    if frc != 0:
        return ReconcileResult(ok=False, action="fetch-failed",
                               detail=(ferr or "").strip()[:200])

    # 2) ahead/behind 측정 — 추적 upstream 유무 판정 포함.
    ahead, behind, has_up = _ahead_behind_raw(team_root, timeout)
    if not has_up:
        return ReconcileResult(ok=True, action="no-upstream",
                               detail="추적 upstream 없음(정합 불필요)")

    # 3) 이미 정합(behind==0)
    if behind == 0:
        action = "ahead-only" if ahead > 0 else "up-to-date"
        return ReconcileResult(ok=True, action=action, ahead=ahead, behind=0)

    # 4) 순수 behind → fast-forward
    if ahead == 0:
        try:
            rc, _, err = run_git(
                ["-C", team_root, "merge", "--ff-only", "@{u}"], timeout=timeout)
        except subprocess.TimeoutExpired:
            return ReconcileResult(ok=False, action="error", behind=behind,
                                   detail="ff merge timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return ReconcileResult(ok=False, action="error", behind=behind,
                                   detail=f"ff merge exec error: {exc}")
        if rc == 0:
            return ReconcileResult(ok=True, action="fast-forward", behind=behind)
        return ReconcileResult(ok=False, action="error", behind=behind,
                               detail=(err or "").strip()[:200])

    # 5) diverge(ahead>0 & behind>0) → rebase --autostash. 실패 시 반드시 abort.
    try:
        rc, _, rerr = run_git(
            ["-C", team_root, "rebase", "--autostash", "@{u}"], timeout=timeout)
    except subprocess.TimeoutExpired:
        _abort_rebase(team_root, timeout)
        return ReconcileResult(ok=False, action="conflict", ahead=ahead,
                               behind=behind, diverged=True,
                               detail="rebase timeout(aborted)")
    except (OSError, subprocess.SubprocessError) as exc:
        _abort_rebase(team_root, timeout)
        return ReconcileResult(ok=False, action="conflict", ahead=ahead,
                               behind=behind, diverged=True,
                               detail=f"rebase exec error(aborted): {exc}")
    if rc == 0:
        # 정합 후 남은 ahead(미push 로컬 커밋) 재계산.
        a2, _, _ = _ahead_behind_raw(team_root, timeout)
        return ReconcileResult(ok=True, action="rebased", ahead=a2,
                               behind=behind, diverged=True,
                               detail="rebased onto upstream")
    _abort_rebase(team_root, timeout)
    return ReconcileResult(ok=False, action="conflict", ahead=ahead,
                           behind=behind, diverged=True,
                           detail=(rerr or "").strip()[:200])


# ──────────────────────────────────────────────────────────────────
# sync-warning 마커 — push 실패/정합 충돌의 **머신 로컬** 가시화 상태(이슈 #23)
# ──────────────────────────────────────────────────────────────────
#
# 왜 팀 루트(memory/) 가 아니라 XDG_STATE_HOME 인가:
#   push 실패는 "이 클론이 origin 에 못 올렸다"는 **머신 로컬** 사실이다. memory/ 는
#   팀 공유라 마커를 거기 두면 git add 로 다른 클론까지 새어 들어가(.gitignore 철학에
#   어긋남 — auto_pull throttle state 와 동일 사유). 그래서 팀 루트 밖 XDG 에 둔다.
#
# 왜 team_root 별 파일인가(codex 리뷰 P2):
#   마커를 단일 파일로 두면 한 머신에 팀 레포가 둘일 때 repo B 의 성공적 push/reconcile
#   이 부르는 clear 가 repo A 의 **미해결** push-실패 마커까지 지워, repo A 의 다음 세션이
#   "로컬 커밋 미push"를 못 띄운다(교차팀 격리 붕괴 + write 경합 "마지막이 이김"). 그래서
#   파일명에 team_root 안정 해시를 넣어 팀마다 독립 파일을 쓰고, write/read/clear 모두
#   team_root 를 받아 **자기 파일만** 다룬다. 파일 자체가 팀별이라 내부 root 대조는 불필요.

def _state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "teammode")


def _team_key(team_root: str) -> str:
    """team_root 의 안정 해시(파일명용). normpath 로 정규화해 raw env('/x/')·str(Path)
    ('/x') 표기차를 흡수한 뒤 sha1 앞 16 hex — 팀별 마커 파일을 결정적으로 가른다."""
    norm = os.path.normpath(str(team_root))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def sync_warning_path(team_root: str) -> str:
    """team_root 별 push/정합 실패 가시화 마커 경로(팀 루트 밖 머신 로컬 상태).

    파일명에 team_root 해시를 넣어 한 머신의 여러 팀 레포가 서로의 마커를 덮어쓰거나
    (write 경합) 교차 삭제(clear)하지 못하게 한다(codex 리뷰 P2).
    """
    return os.path.join(_state_dir(), f"sync-warning-{_team_key(team_root)}")


def write_sync_warning(team_root: str, detail: str) -> None:
    """push/정합 실패를 team_root 전용 마커로 남긴다(session-start 가 읽어 표면화). 무raise."""
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        with open(sync_warning_path(team_root), "w", encoding="utf-8") as f:
            f.write(detail)
    except OSError:
        pass  # 마커 기록 실패는 작업을 막지 않는다(가시화는 best-effort)


def read_sync_warning(team_root: str) -> str:
    """team_root 전용 sync-warning 마커 내용(없으면 ''). 무raise."""
    try:
        with open(sync_warning_path(team_root), encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return ""


def clear_sync_warning(team_root: str) -> None:
    """team_root 전용 sync-warning 마커만 제거(push/정합이 회복되면 호출). 무raise.

    자기 팀 파일만 지우므로 같은 머신의 다른 팀 레포 마커를 건드리지 않는다(P2 수정 핵심).
    """
    try:
        os.remove(sync_warning_path(team_root))
    except OSError:
        pass


# ── push-pending ledger (#45 async push) ───────────────────────────
# auto-commit 훅의 foreground publication 이 실패했을 때 detach push-worker 가 fallback
# 을 맡는다. 이 ledger 가 "커밋됐지만 아직 push 안 됨" 상태의 correctness 소스 —
# 팀별 파일로 남겨 worker 유실(머신 슬립·Windows detach 실패·크래시)에도 session-start
# recovery 가 상태를 복원한다. detach 생존은 신뢰 대상이 아니다.

def push_pending_path(team_root: str) -> str:
    """team_root 별 push-pending ledger 경로(sync-warning 과 동일 팀별 격리 규약)."""
    return os.path.join(_state_dir(), f"push-pending-{_team_key(team_root)}")


_PUSH_PENDING_LOCK_WAIT_SECONDS = 1.0
_PUSH_PENDING_LOCK_POLL_SECONDS = 0.01
_PUSH_PENDING_LOCK_UNAVAILABLE = "<pending-ledger-lock-unavailable>"


@contextmanager
def _push_pending_ledger_lock(team_root: str):
    """pending ledger 의 짧은 read/write/conditional-delete 임계구역.

    worker 의 장기 네트워크 lock(`.lock`)과 분리된 OS advisory lock이다. 파일은
    남아도 descriptor close/crash 때 OS lock 은 자동 해제되므로 stale lock 회수로
    살아 있는 임계구역을 깨지 않는다. 획득은 최대 1초만 재시도해 훅 비차단 계약을
    지킨다. 획득 실패 시 호출부는 보수적으로 pending 을 보존한다.
    """
    handle = None
    acquired = False
    unlock = None
    try:
        try:
            os.makedirs(_state_dir(), exist_ok=True)
            handle = open(push_pending_path(team_root) + ".state.lock", "a+b")
            deadline = time.monotonic() + _PUSH_PENDING_LOCK_WAIT_SECONDS

            if os.name == "nt":  # pragma: no cover — Windows CI 부재, stdlib 경로
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)

                def try_lock():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

                def unlock():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                def try_lock():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                def unlock():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            while True:
                try:
                    try_lock()
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_PUSH_PENDING_LOCK_POLL_SECONDS)
        except (OSError, ImportError):
            acquired = False

        yield acquired
    finally:
        if acquired and unlock is not None:
            try:
                unlock()
            except OSError:
                pass
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def write_push_pending(team_root: str) -> bool:
    """pending 마커 원자 기록(임시파일 + os.replace — 부분 쓰기 상태 방지). 무raise.

    root·기록시각은 진단용이고 nonce 는 compare-and-delete 동시성 판별자다.
    반환: 기록 성공 여부(codex P1 — 실패를 호출부가 모르면 "커밋됨·push 안 됨·
    pending 없음·마커 없음" 무음 유실 상태가 된다. 호출부는 False 에 fallback 가시화).
    """
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        try:
            payload = json.dumps(
                {"root": os.path.normpath(str(team_root)),
                 "written_at": datetime.now().isoformat(timespec="seconds"),
                 # nonce: compare-and-delete 식별자 — coarse mtime FS(1s 해상도)에서
                 # 같은 초 내 재기록을 mtime 으로 구분 못 하는 문제의 해법(codex 재검수).
                 "nonce": os.urandom(8).hex()},
                ensure_ascii=False)
            tmp = push_pending_path(team_root) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, push_pending_path(team_root))
            return True
        except OSError:
            return False  # ledger 기록 실패는 커밋을 막지 않는다 — 가시화는 호출부 몫


def read_push_pending(team_root: str) -> str:
    """pending 마커 내용(없으면 ''). 무raise."""
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            # 잠금 실패를 "pending 없음"으로 오판하면 warning/ledger 를 지울 수 있다.
            return _PUSH_PENDING_LOCK_UNAVAILABLE
        try:
            with open(push_pending_path(team_root), encoding="utf-8") as f:
                return f.read().strip()
        except (OSError, ValueError):
            return ""


def clear_push_pending(team_root: str) -> None:
    """pending 마커 제거(멱등·무raise). push 성공 + ahead==0 확인 후에만 호출할 것 —
    push 도중 새 커밋이 생겼으면 pending 을 유지해야 유실이 없다(#45 정정)."""
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return
        try:
            os.remove(push_pending_path(team_root))
        except OSError:
            pass


def clear_push_pending_if_unchanged(team_root: str, snapshot_content: str) -> bool:
    """스냅샷 이후 pending 이 재기록되지 않았을 때만 clear (codex P1 — clear race 차단).

    worker 가 push 성공 → ahead==0 확인 → clear 직전에 auto-commit 이 새 커밋의
    pending 을 재기록하면, 무조건 clear 는 그 새 pending 을 삼켜 "ahead 인데 pending
    없음" 유실 상태를 만든다. 판별자는 **파일 내용**(payload 에 매 기록 고유 nonce
    포함) — mtime(_ns) 비교는 coarse mtime FS(1s 해상도)에서 같은 초 내 재기록을
    놓친다(codex 재검수). 내용이 스냅샷과 같을 때만 지운다(다르면 False —
    호출부 drain loop 가 이어서 push).
    짧은 ledger OS lock 안에서 compare+remove 를 한 임계구역으로 묶어, 비교 직후
    writer 가 새 nonce 를 replace 한 뒤 old clear 가 삭제하는 TOCTOU 를 막는다.
    반환: 실제로 지웠으면 True.
    """
    if not snapshot_content:
        return False
    path = push_pending_path(team_root)
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        try:
            with open(path, encoding="utf-8") as f:
                if f.read().strip() != snapshot_content.strip():
                    return False
            os.remove(path)
            return True
        except OSError:
            return False


def push_pending_age_seconds(team_root: str):
    """pending 마커 나이(초). 없으면 None. UserPromptSubmit 초경량 검사용 —
    stat 1회만 수행한다(장수 세션에서 매 발화 비용 최소화)."""
    try:
        return max(0.0, float(time.time() - os.stat(push_pending_path(team_root)).st_mtime))
    except OSError:
        return None


def kick_push_worker(team_root: str, worker_path: str) -> bool:
    """push-worker detach spawn (#45). 무raise — 반환: spawn 시도 성공 여부.

    auto-commit(커밋 직후)과 session-start(pending recovery 재kick)가 **같은 함수**를
    쓴다 — 훅별 spawn 코드 중복이 만들 플랫폼 분기 드리프트를 차단.
    - POSIX: start_new_session=True(훅 종료와 무관하게 생존).
    - Windows: DETACHED_PROCESS 시도하되 detach 생존을 correctness 로 믿지 않는다 —
      실패해도 pending ledger 가 남아 recovery 가 다시 부른다.
    - TEAMMODE_DISABLE_PUSH_WORKER=1 이면 생략(테스트 관찰용 kill-switch).
    """
    if os.environ.get("TEAMMODE_DISABLE_PUSH_WORKER") == "1":
        return False
    try:
        import sys as _sys
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": team_root,
        }
        if os.name == "nt":  # pragma: no cover — Windows 는 ledger 폴백이 계약
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([_sys.executable, worker_path, "--root", team_root],
                         **kwargs)
        return True
    except Exception:  # noqa: BLE001 — spawn 실패는 비차단(ledger 가 안전장치)
        return False


def push_plain(team_root: str, timeout: int = NET_TIMEOUT):
    """**plain push only** — push-worker 전용 (#45 정정: plain-push-only).

    worker 는 로컬 히스토리를 절대 건드리지 않는다(rebase/fetch 복구 금지) —
    worker 가 rebase 복구 중일 때 사용자가 편집하면 다음 auto-commit 훅의
    add/commit 이 index.lock 으로 실패하고, 훅은 예외를 삼켜 exit 0 이므로
    **편집 커밋이 조용히 유실**된다. push 지연보다 명백히 나쁜 회귀라 경합
    표면을 push 로 한정한다. 정합 복구는 기존 채널(session-start do_reconcile·
    teammode pull)에 위임한다.

    - 성공 → (True, detail)
    - upstream 미설정만 `push -u origin HEAD` 1회 (이슈 #34 와 동일 사유 —
      새 브랜치 평문 push 는 영원히 실패하므로).
    - non-ff → (False, "non-fast-forward") — 복구 없음, 마커만(호출부 몫).
    - 그 외 실패/타임아웃 → (False, detail). 절대 예외를 전파하지 않는다.
    """
    try:
        prc, pout, perr = run_git(
            ["-C", team_root, *http_timeout_opts(timeout), "push"],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "push timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"push exec error: {exc}"
    if prc == 0:
        return True, "pushed"

    combined = (perr or "") + "\n" + (pout or "")
    if _is_no_upstream(combined):
        try:
            urc, uout, uerr = run_git(
                ["-C", team_root, *http_timeout_opts(timeout),
                 "push", "-u", "origin", "HEAD"],
                timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "push -u timeout"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"push -u exec error: {exc}"
        if urc == 0:
            return True, "pushed (set upstream)"
        ucombined = (uerr or "") + "\n" + (uout or "")
        if _is_non_fast_forward(ucombined):
            return False, "non-fast-forward"
        return False, f"push -u failed: {ucombined.strip()[:200]}"

    if _is_non_fast_forward(combined):
        return False, "non-fast-forward"
    return False, f"push failed: {combined.strip()[:200]}"


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


def _is_no_upstream(text: str) -> bool:
    """push 출력(stderr/stdout)이 **upstream 미설정 거부**인지 판정. 무raise.

    새 브랜치(`checkout -b`)에서 평문 `git push` 는 push.default=simple 아래
    "fatal: The current branch X has no upstream branch. ... use
    git push --set-upstream origin X" 로 영원히 실패한다(이슈 #34). 이때만
    `push -u origin HEAD` 1회 재시도를 트리거한다. non-ff·인증 실패와 겹치지
    않도록 git 의 거부 메시지 패턴으로 좁게 감지한다(LC_ALL=C 로 영어 고정됨).
    """
    if not text:
        return False
    low = text.lower()
    return "no upstream branch" in low or "--set-upstream" in low


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
              timeout: int = NET_TIMEOUT, paths: list | None = None) -> CommitResult:
    """`git add` + `git commit -m` (+ 선택 push). 절대 예외를 전파하지 않는다(철칙).

    auto_pull/do_pull 과 같은 안전장치 재사용(git_env 자격증명 차단·killpg 타임아웃).

    timeout 파라미터(기본 NET_TIMEOUT)는 **네트워크 호출(push·복구 fetch/rebase/재push)
    전용**이다. 내부 로컬 하위호출(add·staged-diff·commit)은 DEFAULT_TIMEOUT 고정 —
    함수 timeout 을 그대로 쓰면 push=False(네트워크 0) 경로까지 10s 로 승격돼
    "로컬 동사는 2s(세션 스냅함)" 선언이 깨진다(codex 리뷰 P2-2).
    또한 push=True 흐름 전체는 **진입 앵커** 벽시계 예산 PUSH_TOTAL_BUDGET(22s)로
    캡된다 — 데드라인이 함수 진입에서 시작돼 로컬 단계(최악 ~8s)도 예산을 소모하고
    네트워크 단계는 남은 만큼만 쓴다(로컬 하위호출은 개별 클램프 없음 — 로컬 커밋은
    항상 완주·보존). 복구 체인(최악 네트워크 5회 순차)이 훅 manifest 캡(40s)을 넘기
    전에 **항상** 스스로 반환해, 호출부가 sync-warning 마커를 쓸 수 있게 한다
    (codex 재리뷰 P1 — 종전엔 데드라인이 push 직전 시작이라 로컬 최악 8s + 25s 가
    캡을 넘을 수 있었다: A1).
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
    # 진입 앵커 데드라인(A1): 벽시계 예산을 함수 진입에서 시작해 로컬 단계
    # (rev-parse·add·staged-diff·commit)도 소모하게 한다. 로컬 하위호출은 이 예산으로
    # 클램프/중단하지 않는다(DEFAULT_TIMEOUT 고정 — 로컬 커밋은 항상 완주·보존).
    # 예산은 네트워크 단계가 "남은 만큼만" 쓰게 하는 상한일 뿐이다.
    _deadline = time.monotonic() + PUSH_TOTAL_BUDGET

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
        rc, _, err = run_git(add_args, timeout=DEFAULT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return CommitResult(ok=False, detail="add timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return CommitResult(ok=False, detail=f"add exec error: {exc}")
    if rc != 0:
        return CommitResult(ok=False, detail=f"add failed: {(err or '').strip()[:200]}")

    # 2) 변경 없으면 비치명 종료(빈 커밋 만들지 않음)
    if not _has_staged_changes(team_root, DEFAULT_TIMEOUT):
        return CommitResult(ok=False, committed=False,
                            detail="nothing to commit")

    # 3) commit — paths 지정 시 pathspec partial commit(미리 staged 된 다른 경로 제외).
    commit_args = ["-C", team_root, "commit", "-m", message]
    if paths:
        commit_args += ["--", *[str(p) for p in paths]]
    try:
        rc, out, err = run_git(commit_args, timeout=DEFAULT_TIMEOUT)
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
    #
    # 공유 데드라인(codex 재리뷰 P1 → A1 진입 앵커): 함수 **진입**에서 시작한
    # _deadline 하나를 이 지점 이후의 **모든** 네트워크 호출(push·push -u·fetch·
    # rebase·재push)이 나눠 쓴다 — 로컬 단계가 이미 소모한 벽시계만큼 네트워크
    # 몫이 준다. 개별 호출마다 NET_TIMEOUT 을 새로 주면 복구 체인이 최악 ~50s 까지
    # 늘어져 훅 manifest 캡(40s)이 프로세스를 먼저 죽이고, 그러면 호출부가
    # CommitResult 를 받지 못해 sync-warning 마커를 못 쓴다. 예산이 바닥나면 즉시
    # 비차단 반환한다(커밋은 이미 보존됨 — push 미완만 detail 로 표면화).

    def _net_t() -> int:
        """남은 예산으로 클램프한 네트워크 타임아웃(하한 1s — 0/음수 방지).

        하한(max)은 **바깥**에서 강제한다(codex A1): 종전
        min(timeout, max(1, 남은예산)) 은 caller 가 timeout<=0 을 주면 min 이
        그 0/음수를 그대로 통과시켜 '하한 1s' 문서 계약이 깨졌다 — 커밋만 남고
        push 가 즉시 TimeoutExpired 로 죽는다.
        """
        return max(1, min(timeout, int(_deadline - time.monotonic())))

    def _budget_ok(reserve: int = 1) -> bool:
        """남은 예산 검사. rebase 같은 다단계 진입 전엔 reserve 를 크게 줘
        '1초 남기고 rebase 시작 → abort 까지 캡 초과' 경로를 차단한다(#codex-P1)."""
        return (_deadline - time.monotonic()) >= reserve

    def _budget_stop(step: str) -> CommitResult:
        return CommitResult(ok=True, committed=True, pushed=False,
                            detail=f"committed; push budget exhausted ({step})")

    # preflight(A1): 로컬 단계가 예산을 이미 소진했으면 push 를 아예 시작하지 않는다 —
    # _net_t 의 하한(1s) 때문에 소진 상태에서도 1s 짜리 헛 push 가 나가는 걸 차단.
    # 이 결과 모양(committed=True/pushed=False)이 auto-commit 훅의 sync-warning
    # 마커 기록 조건이다.
    if not _budget_ok(reserve=2):
        return _budget_stop("before push")

    try:
        prc, pout, perr = run_git(
            ["-C", team_root, *http_timeout_opts(timeout), "push"],
            timeout=_net_t())
    except subprocess.TimeoutExpired:
        return CommitResult(ok=True, committed=True, pushed=False,
                            detail="committed; push timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return CommitResult(ok=True, committed=True, pushed=False,
                            detail=f"committed; push exec error: {exc}")
    if prc == 0:
        return CommitResult(ok=True, committed=True, pushed=True,
                            detail="committed and pushed")

    # 4-0) upstream 미설정 거부면 자동 복구: `push -u origin HEAD` 1회 재시도(이슈 #34).
    #      새 브랜치에서 평문 push 는 영원히 실패하므로 upstream 을 심으며 push 한다.
    #      성공 시 이후 커밋부턴 평문 push 가 그냥 동작한다.
    #      ⚠️ 첫 push 의 no-upstream 과 -u 재시도의 non-ff 는 상호 배타가 **아니다** —
    #      원격에 **같은 이름 브랜치가 이미 앞서** 존재하는데 로컬만 upstream 연결이
    #      없으면 -u 재시도가 non-ff 로 거부된다(codex 리뷰 P2-1). 이 경로엔 @{u} 가
    #      아직 없어 4-1 복구(@{u} 기준 rebase)를 못 타므로 여기서 인라인 복구한다.
    if _is_no_upstream((perr or "") + "\n" + (pout or "")):
        if not _budget_ok():
            return _budget_stop("before push -u")
        try:
            urc, uout, uerr = run_git(
                ["-C", team_root, *http_timeout_opts(timeout),
                 "push", "-u", "origin", "HEAD"],
                timeout=_net_t())
        except subprocess.TimeoutExpired:
            return CommitResult(ok=True, committed=True, pushed=False,
                                detail="committed; push -u timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return CommitResult(ok=True, committed=True, pushed=False,
                                detail=f"committed; push -u exec error: {exc}")
        if urc == 0:
            return CommitResult(ok=True, committed=True, pushed=True,
                                detail="committed and pushed (set upstream)")
        # 4-0-1) -u 재시도가 non-ff 거부 → fetch → origin/<현재 브랜치> 위로 rebase →
        #        `push -u origin HEAD` 1회 더. rebase 는 --autostash(partial-commit 의
        #        dirty 워킹트리 흡수·충돌 시 자동 원복 — 4-1 과 동일 사유).
        if _is_non_fast_forward((uerr or "") + "\n" + (uout or "")):
            if not _budget_ok():
                return _budget_stop("before push -u rebase fetch")
            try:
                frc, _, ferr = run_git(
                    ["-C", team_root, *http_timeout_opts(timeout), "fetch"],
                    timeout=_net_t())
            except subprocess.TimeoutExpired:
                return CommitResult(ok=True, committed=True, pushed=False,
                                    detail="committed; push -u rebase fetch timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail=f"committed; push -u rebase fetch exec error: {exc}")
            if frc != 0:
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail=f"committed; push -u rebase fetch failed: "
                           f"{(ferr or '').strip()[:200]}")
            # rebase 기준은 @{u}(없음)가 아니라 origin/<현재 브랜치> — 브랜치명은
            # 로컬 동사(rev-parse)로 해석. detached HEAD 면 복구 불가(비차단 반환).
            branch = ""
            try:
                brc, bout, _ = run_git(
                    ["-C", team_root, "rev-parse", "--abbrev-ref", "HEAD"],
                    timeout=DEFAULT_TIMEOUT)
                if brc == 0:
                    branch = (bout or "").strip()
            except (OSError, subprocess.SubprocessError):
                branch = ""
            if not branch or branch == "HEAD":
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail="committed; push -u rejected (non-ff) and current "
                           "branch unresolvable — manual sync needed")
            if not _budget_ok(reserve=4):
                return _budget_stop("before push -u rebase")
            try:
                rrc, _, rerr = run_git(
                    ["-C", team_root, "rebase", "--autostash",
                     f"origin/{branch}"], timeout=_net_t())
            except subprocess.TimeoutExpired:
                _abort_rebase(team_root, DEFAULT_TIMEOUT)  # abort는 로컬 — 예산 밖 고정(#codex-P1)
                return CommitResult(ok=True, committed=True, pushed=False,
                                    detail="committed; push -u rebase timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                _abort_rebase(team_root, DEFAULT_TIMEOUT)  # abort는 로컬 — 예산 밖 고정(#codex-P1)
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail=f"committed; push -u rebase exec error: {exc}")
            if rrc != 0:
                _abort_rebase(team_root, DEFAULT_TIMEOUT)  # abort는 로컬 — 예산 밖 고정(#codex-P1)
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail=f"committed; push -u rebase failed (aborted): "
                           f"{(rerr or '').strip()[:200]}")
            if not _budget_ok():
                return _budget_stop("before push -u after rebase")
            try:
                u2rc, u2out, u2err = run_git(
                    ["-C", team_root, *http_timeout_opts(timeout),
                     "push", "-u", "origin", "HEAD"],
                    timeout=_net_t())
            except subprocess.TimeoutExpired:
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail="committed; rebased but push -u timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                return CommitResult(
                    ok=True, committed=True, pushed=False,
                    detail=f"committed; rebased but push -u exec error: {exc}")
            if u2rc == 0:
                return CommitResult(
                    ok=True, committed=True, pushed=True,
                    detail="committed and pushed (set upstream after rebase)")
            return CommitResult(
                ok=True, committed=True, pushed=False,
                detail=f"committed; rebased but push -u failed: "
                       f"{((u2err or u2out) or '').strip()[:200]}")
        return CommitResult(
            ok=True, committed=True, pushed=False,
            detail=f"committed; push failed: {((uerr or uout) or '').strip()[:200]}")

    # 4-1) non-ff 거부면 자동 복구: fetch → rebase → 재push 1회.
    #      다른 기기가 먼저 push 해 로컬이 behind 일 때 발생. non-ff 가 아닌 실패(인증·
    #      네트워크 등)는 자동 복구 대상이 아니므로 기존대로 비차단 반환한다.
    #      partial-commit(paths=) 시 워킹트리에 커밋 안 된 다른 추적파일 변경이 남을 수
    #      있다(auto-commit 의 주 패턴). 평문 rebase 는 그 dirty 상태를 "unstaged changes"
    #      로 거부하므로, --autostash 로 stash→rebase→pop 해 dirty 를 흡수·보존한다(충돌·
    #      abort 시에도 autostash 가 자동 원복).
    if _is_non_fast_forward((perr or "") + "\n" + (pout or "")):
        # fetch (push 와 동일하게 http 타임아웃 옵션 적용). 실패해도 예외 전파 0.
        if not _budget_ok():
            return _budget_stop("before rebase fetch")
        try:
            frc, _, ferr = run_git(
                ["-C", team_root, *http_timeout_opts(timeout), "fetch"],
                timeout=_net_t())
        except subprocess.TimeoutExpired:
            return CommitResult(ok=True, committed=True, pushed=False,
                                detail="committed; rebase fetch timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return CommitResult(ok=True, committed=True, pushed=False,
                                detail=f"committed; rebase fetch exec error: {exc}")
        if frc == 0:
            # rebase (추적 upstream 위로). 충돌 등 실패 시 반드시 --abort 로 원상복구.
            if not _budget_ok(reserve=4):
                return _budget_stop("before rebase")
            try:
                rrc, _, rerr = run_git(
                    ["-C", team_root, "rebase", "--autostash"], timeout=_net_t())
            except subprocess.TimeoutExpired:
                _abort_rebase(team_root, DEFAULT_TIMEOUT)  # abort는 로컬 — 예산 밖 고정(#codex-P1)
                return CommitResult(ok=True, committed=True, pushed=False,
                                    detail="committed; rebase timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                _abort_rebase(team_root, DEFAULT_TIMEOUT)  # abort는 로컬 — 예산 밖 고정(#codex-P1)
                return CommitResult(ok=True, committed=True, pushed=False,
                                    detail=f"committed; rebase exec error: {exc}")
            if rrc == 0:
                # rebase 성공 → 재push 1회.
                if not _budget_ok():
                    return _budget_stop("before re-push")
                try:
                    p2rc, p2out, p2err = run_git(
                        ["-C", team_root, *http_timeout_opts(timeout), "push"],
                        timeout=_net_t())
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
            _abort_rebase(team_root, DEFAULT_TIMEOUT)  # abort는 로컬 — 예산 밖 고정(#codex-P1)
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
                   timeout: int = NET_TIMEOUT) -> FetchResult:
    """upstream(템플릿 원본)을 **fetch 만** 한다. 절대 예외 전파 없음(철칙).

    **merge 하지 않는다** — 적용은 명시적 update 동사 몫(팀 합의: fetch 만 자동).
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

# 엔진 동기화에서 **제외**할 경로(#36): infra/skills/util 은 인스턴스 소유(각 팀이
# 이식한 util 스킬)라 upstream checkout 이 덮으면 유실된다. SYNC_PATHS 에 직접 넣지
# 않고(positive 존재확인용 유지) git 실행 시에만 `_sync_pathspecs()` 가 조합한다.
SYNC_EXCLUDE_PATHS = ["infra/skills/util"]

# Product CI/release workflows belong to the upstream product repository, not to
# team instances. Keep this denylist hard even if a future sync scope expands to
# `.github` or a caller explicitly asks for `.github/workflows`.
SYNC_DENY_PREFIXES = (".github/workflows",)

TEAM_INSTANCE_WORKFLOWS = ".github/workflows"
PRODUCT_REPO_OWNER = "T-Gates"
PRODUCT_REPO_NAME = "tm-mode"
_WORKFLOW_STRIP_MESSAGE = (
    "chore(teammode): remove product workflows from team instance")
_WORKFLOW_STRIP_IDENTITY = (
    "-c", "user.name=tm-mode",
    "-c", "user.email=tm-mode@users.noreply.github.com",
)


def _normalize_remote_repo(url: str | None) -> tuple[str, str] | None:
    """Git remote URL 에서 (owner, repo) 추출. 로컬 path 는 owner="" 로 반환."""
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    lower = raw.lower()
    path = None
    if "://" not in lower:
        for marker in ("@github.com:", "@www.github.com:"):
            idx = lower.find(marker)
            if idx >= 0:
                path = raw[idx + len(marker):]
                break
        if path is None:
            for marker in ("github.com:", "www.github.com:"):
                if lower.startswith(marker):
                    path = raw[len(marker):]
                    break
    if path is not None:
        pass
    else:
        parsed = urlparse(raw)
        host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower()
        if host in ("github.com", "www.github.com"):
            path = parsed.path.lstrip("/")
        elif not parsed.netloc:
            repo = Path(parsed.path).name
            if repo.lower().endswith(".git"):
                repo = repo[:-4]
            return "", repo
        else:
            return None
    path = path.rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[-2], parts[-1]


def _is_github_remote(url: str | None) -> bool:
    if not url:
        return False
    raw = str(url).strip()
    lower = raw.lower()
    if "://" not in lower:
        return (
            "@github.com:" in lower
            or "@www.github.com:" in lower
            or lower.startswith("github.com:")
            or lower.startswith("www.github.com:")
        )
    parsed = urlparse(raw)
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    return host in ("github.com", "www.github.com")


def _remote_url(team_root: str, remote: str, timeout: int) -> str | None:
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "remote", "get-url", remote], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0:
        return None
    return (out or "").strip() or None


def is_product_repo_checkout(team_root: str,
                             timeout: int = DEFAULT_TIMEOUT) -> bool:
    """product repo/fork 로 보이는 checkout 은 team workflow strip 대상에서 제외.

    최종 정책: preserve ⇔ origin 이 github.com 이고
    (`T-Gates/tm-mode` exact 또는 repo 이름이 `tm-mode`). upstream 은 신호로 쓰지 않는다.
    이유: developer fork(origin=alice/tm-mode + upstream=T-Gates/tm-mode)와 team instance 는
    remote 만으로 구분 불가능하고, product fork workflow 삭제가 더 치명적인 방향이다.
    따라서 repo name 이 이긴다. 잔여 fail-open(팀 인스턴스가 GitHub 에서 정확히 tm-mode 라는
    이름을 쓴 경우)은 workflow job-level `github.repository == 'T-Gates/tm-mode'` guard 가
    no-op 으로 막는다.
    """
    origin_url = _remote_url(team_root, "origin", timeout)
    origin = _normalize_remote_repo(origin_url)
    product = (PRODUCT_REPO_OWNER.lower(), PRODUCT_REPO_NAME.lower())
    if not origin or not _is_github_remote(origin_url):
        return False
    owner, repo = origin[0].lower(), origin[1].lower()
    if (owner, repo) == product:
        return True
    return repo == PRODUCT_REPO_NAME


def _workflow_path_exists(path: Path) -> bool:
    return os.path.lexists(str(path))


def _remove_workflow_path(path: Path) -> tuple[bool, str]:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif _workflow_path_exists(path):
            path.unlink()
        return True, ""
    except OSError as exc:
        return False, f"failed to remove {TEAM_INSTANCE_WORKFLOWS}: {exc}"


def _push_existing_workflow_strip_commit(team_root: str,
                                         timeout: int) -> WorkflowStripResult:
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "log", "-1", "--format=%s"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return WorkflowStripResult(ok=True, detail="no workflows")
    if rc != 0 or (out or "").strip() != _WORKFLOW_STRIP_MESSAGE:
        return WorkflowStripResult(ok=True, detail="no workflows")
    try:
        prc, pout, perr = run_git(
            ["-C", team_root, *http_timeout_opts(NET_TIMEOUT), "push"],
            timeout=NET_TIMEOUT)
    except subprocess.TimeoutExpired:
        return WorkflowStripResult(
            ok=False, changed=True, committed=True, pushed=False,
            detail=_workflow_remote_still_contains_message("push timeout"))
    except (OSError, subprocess.SubprocessError) as exc:
        return WorkflowStripResult(
            ok=False, changed=True, committed=True, pushed=False,
            detail=_workflow_remote_still_contains_message(
                f"push exec error: {exc}"))
    if prc == 0:
        return WorkflowStripResult(
            ok=True, changed=False, committed=True, pushed=True,
            detail="previous workflow removal commit pushed")
    return WorkflowStripResult(
        ok=False, changed=True, committed=True, pushed=False,
        detail=_workflow_remote_still_contains_message(
            f"push failed: {((perr or pout) or '').strip()[:200]}"))


def _workflow_remote_still_contains_message(reason: str) -> str:
    return (
        f"{reason}. The remote repository still contains .github/workflows. "
        "Fix: re-run the setup after git push works, or delete .github/workflows "
        "from the repository on GitHub manually.")


def strip_template_workflows(team_root: str,
                             timeout: int = DEFAULT_TIMEOUT) -> WorkflowStripResult:
    """팀 인스턴스에서 product CI/release workflow 를 제거하고 push 한다.

    product repo/fork 는 절대 건드리지 않는다. `.github/ISSUE_TEMPLATE` 등 다른
    GitHub 설정은 보존하고 `.github/workflows` path 의 모든 shape(dir/file/symlink/
    broken symlink)만 제거한다. 실패는 예외 대신 정직한 결과 객체로 반환한다.
    """
    root = str(team_root)
    if not is_git_worktree(root):
        return WorkflowStripResult(ok=False, detail="not a git work tree")
    if is_product_repo_checkout(root, timeout=timeout):
        return WorkflowStripResult(
            ok=True, skipped_product=True,
            detail="product repo checkout — workflows preserved")

    workflows = Path(root) / TEAM_INSTANCE_WORKFLOWS
    if not _workflow_path_exists(workflows):
        return _push_existing_workflow_strip_commit(root, timeout)

    ok, detail = _remove_workflow_path(workflows)
    if not ok:
        return WorkflowStripResult(ok=False, detail=detail)

    try:
        rc, out, err = run_git(
            ["-C", root, "add", "-A", "--", TEAM_INSTANCE_WORKFLOWS],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return WorkflowStripResult(
            ok=False, changed=True, detail="add timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return WorkflowStripResult(
            ok=False, changed=True, detail=f"add exec error: {exc}")
    if rc != 0:
        return WorkflowStripResult(
            ok=False, changed=True,
            detail=f"add failed: {((err or out) or '').strip()[:200]}")

    if not _has_staged_changes(root, timeout):
        return WorkflowStripResult(
            ok=True, changed=True, committed=False, pushed=False,
            detail="workflows removed locally; nothing to commit")

    try:
        rc, out, err = run_git(
            ["-C", root, *_WORKFLOW_STRIP_IDENTITY, "commit", "-m",
             _WORKFLOW_STRIP_MESSAGE, "--", TEAM_INSTANCE_WORKFLOWS],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return WorkflowStripResult(
            ok=False, changed=True,
            detail=_workflow_remote_still_contains_message("commit timeout"))
    except (OSError, subprocess.SubprocessError) as exc:
        return WorkflowStripResult(
            ok=False, changed=True,
            detail=_workflow_remote_still_contains_message(
                f"commit exec error: {exc}"))
    if rc != 0:
        return WorkflowStripResult(
            ok=False, changed=True,
            detail=_workflow_remote_still_contains_message(
                f"commit failed: {((err or out) or '').strip()[:200]}"))

    try:
        prc, pout, perr = run_git(
            ["-C", root, *http_timeout_opts(NET_TIMEOUT), "push"],
            timeout=NET_TIMEOUT)
    except subprocess.TimeoutExpired:
        return WorkflowStripResult(
            ok=False, changed=True, committed=True, pushed=False,
            detail=_workflow_remote_still_contains_message("push timeout"))
    except (OSError, subprocess.SubprocessError) as exc:
        return WorkflowStripResult(
            ok=False, changed=True, committed=True, pushed=False,
            detail=_workflow_remote_still_contains_message(
                f"push exec error: {exc}"))
    if prc != 0:
        return WorkflowStripResult(
            ok=False, changed=True, committed=True, pushed=False,
            detail=_workflow_remote_still_contains_message(
                f"push failed: {((perr or pout) or '').strip()[:200]}"))

    return WorkflowStripResult(
        ok=True, changed=True, committed=True, pushed=True,
        detail="removed and pushed .github/workflows")

# ── 2층 validation 동기화(#36 PR2) ──────────────────────────────────
# conformance/·tests/ 는 upstream 소유 검증층이지만 인스턴스가 손댈 수 있다(예약
# 경로 tests/local·conformance/local, 강화판 check.py). 파일 단위 blob-history 판정:
# 로컬 무수정=safe 갱신 / 커밋 안 된 수정(dirty)·로컬 커밋 수정=skip 보존.
VALIDATION_PATHS = ["conformance", "tests"]
VALIDATION_EXCLUDE_PREFIXES = ("tests/local/", "conformance/local/")
VALIDATION_EXCLUDE_SEGMENTS = ("__pycache__", ".pytest_cache")
VALIDATION_EXCLUDE_SUFFIXES = (".pyc",)


@dataclass(frozen=True)
class ValidationSkip:
    path: str
    reason: str          # dirty | local-unclassified | local-only | shallow
    blob: str = ""       # local HEAD blob (untracked/shallow 는 "")
    status: str = ""     # dirty 일 때 porcelain XY, 그 외 ""


@dataclass(frozen=True)
class ValidationDelete:
    path: str
    blob: str = ""
    reason: str = "upstream-deleted"   # upstream-deleted | upstream-renamed
    renamed_to: str = ""


@dataclass(frozen=True)
class ValidationPlan:
    ref: str
    safe_paths: tuple = ()
    skipped: tuple = ()          # ValidationSkip[]
    local_only: tuple = ()       # ValidationSkip[]
    up_to_date: tuple = ()
    safe_deletes: tuple = ()     # ValidationDelete[] — v2(#36 절단② 해소)
    skip_hash: str = ""          # skipped+local_only 만(삭제는 action 대상 — 미포함)
    shallow: bool = False
    detail: str = ""


@dataclass(frozen=True)
class ValidationApplyResult:
    ok: bool
    changed: bool = False
    applied: tuple = ()
    forced: tuple = ()
    deleted: tuple = ()          # v2 — staged 삭제된 path
    skipped: tuple = ()
    backup_path: str = ""        # force patch 또는 삭제 raw-copy 디렉토리
    diff: str = ""
    detail: str = ""


def _validation_excluded(path: str) -> bool:
    if path.startswith(VALIDATION_EXCLUDE_PREFIXES):
        return True
    if path.endswith(VALIDATION_EXCLUDE_SUFFIXES):
        return True
    parts = path.split("/")
    return any(seg in parts for seg in VALIDATION_EXCLUDE_SEGMENTS)


def _is_shallow_repo(team_root: str, timeout: int) -> bool:
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-parse", "--is-shallow-repository"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return True  # 알 수 없으면 보수적으로 shallow 취급(validation skip)
    return rc == 0 and (out or "").strip() == "true"


def _ls_tree_map(team_root: str, ref: str, timeout: int) -> dict:
    """`git ls-tree -r -z <ref> -- conformance tests` → {path: (mode,type,blob)}. 무raise."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "ls-tree", "-r", "-z", ref, "--", *VALIDATION_PATHS],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return {}
    result = {}
    if rc != 0:
        return result
    for entry in (out or "").split("\0"):
        if not entry:
            continue
        meta, _tab, path = entry.partition("\t")
        cols = meta.split()
        if len(cols) >= 3 and path:
            result[path] = (cols[0], cols[1], cols[2])
    return result


_ZERO_BLOB = "0" * 40


def _ref_history_index(team_root: str, ref: str, timeout: int):
    """upstream 역사 인덱스 — (history_by_path, latest_by_path). 무raise.

    - history_by_path: {path: set(blob)} — 크로스패스 충돌 차단(codex P1). R/C 는
      old/new 양쪽 path 에 양쪽 blob 등록. zero-blob 제외.
    - latest_by_path: {path: ("deleted"|"renamed-away"|"renamed-into"|<kind>, renamed_to)}
      — log 는 최신→과거 순이므로 **첫 관측**이 ref 기준 최신 이벤트(setdefault).
      terminal removal(D/R-away) 판정에 사용(v2 safe_deletes — blob 존재만으론
      "과거에 지워진 적 있음"과 "지금 없음"을 구분 못 함).
    - `-M` 필수: rename 이 D+A 로 쪼개지면 R-away 를 못 본다.
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "log", "--format=", "--raw", "-M", "--no-abbrev",
             ref, "--", *VALIDATION_PATHS], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return {}, {}
    by_path: dict = {}
    latest: dict = {}
    if rc != 0:
        return by_path, latest
    for line in (out or "").splitlines():
        if not line.startswith(":"):
            continue
        meta, *paths = line.split("\t")
        cols = meta[1:].split()
        if len(cols) < 5 or not paths:
            continue
        blobs = [tok for tok in cols[2:4]
                 if len(tok) == 40 and tok != _ZERO_BLOB
                 and all(c in "0123456789abcdef" for c in tok)]
        kind = cols[4][0]
        if kind in ("R", "C") and len(paths) >= 2:
            old_p, new_p = paths[0], paths[1]
            latest.setdefault(old_p, ("renamed-away", new_p))
            latest.setdefault(new_p, ("renamed-into", old_p))
        elif kind == "D":
            latest.setdefault(paths[0], ("deleted", ""))
        else:
            latest.setdefault(paths[0], (kind, ""))
        for path in paths:
            if path:
                by_path.setdefault(path, set()).update(blobs)
    return by_path, latest


def _validation_dirty_paths(team_root: str, timeout: int) -> dict:
    """`git status --porcelain=v1 -z --untracked-files=all` → {path: XY}. 무raise.

    커밋 안 된 수정·staged·untracked 를 잡는다 — checkout 이 덮으면 유실될 로컬 변경
    (blob-history 판정보다 우선 skip). rename/copy 는 old/new path 양쪽 등록.
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "status", "--porcelain=v1", "-z",
             "--untracked-files=all", "--", *VALIDATION_PATHS], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return {}
    dirty = {}
    if rc != 0:
        return dirty
    tokens = (out or "").split("\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok:
            i += 1
            continue
        xy = tok[:2]
        path = tok[3:] if len(tok) > 3 else ""
        if path:
            dirty[path] = xy
        # rename/copy(R/C): 다음 토큰이 원본(old) path
        if xy and xy[0] in ("R", "C"):
            i += 1
            if i < len(tokens) and tokens[i]:
                dirty[tokens[i]] = xy
        i += 1
    return dirty


def _validation_skip_hash(skipped, local_only) -> str:
    import json as _json
    items = sorted((s.path, s.reason, s.blob)
                   for s in list(skipped) + list(local_only))
    return hashlib.sha256(
        _json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def plan_validation_sync(team_root: str, ref: str,
                         timeout: int = DEFAULT_TIMEOUT) -> ValidationPlan:
    """validation 층(conformance/·tests/) 파일 단위 동기화 계획. 무raise(철칙).

    판정 순서: excluded? → dirty? → up_to_date? → blob-history safe? → skip.
    - dirty(커밋 안 된 수정/staged/untracked): 무조건 skip(덮으면 유실).
    - local==current(mode/type/blob): up_to_date.
    - upstream 만 있음(local 없음): safe addition.
    - local 만 있음(current 없음): local_only(v1 삭제 안 함).
    - 둘 다 있고 다름 + local blob 이 upstream 역사에 있음: safe(뒤처짐).
    - 그 외: skip(local-unclassified — 로컬 수정으로 간주 보존).
    """
    if _is_shallow_repo(team_root, timeout):
        return ValidationPlan(ref=ref, shallow=True,
                              detail="shallow clone — validation 전체 skip(엔진은 정상)")

    local = _ls_tree_map(team_root, "HEAD", timeout)
    current = _ls_tree_map(team_root, ref, timeout)
    history, latest = _ref_history_index(team_root, ref, timeout)
    dirty = _validation_dirty_paths(team_root, timeout)

    safe, skipped, local_only, up_to_date = [], [], [], []
    safe_deletes = []
    for path in sorted(set(local) | set(current)):
        if _validation_excluded(path):
            continue
        lmeta = local.get(path)
        cmeta = current.get(path)
        lblob = lmeta[2] if lmeta else ""
        if path in dirty:
            skipped.append(ValidationSkip(path, "dirty", lblob, dirty[path]))
            continue
        if lmeta and cmeta and lmeta == cmeta:
            up_to_date.append(path)
            continue
        if not lmeta and cmeta:
            safe.append(path)  # upstream 신규
            continue
        if lmeta and not cmeta:
            # v2(#36 절단② 해소): upstream 유래 + terminal removal 이면 safe_delete.
            # blob∈경로역사 = 이 파일은 upstream 이 준 그대로(무수정) / latest 가
            # D·R-away = upstream 이 실제로 없앤 것. 둘 다 충족해야 삭제 후보 —
            # 로컬 창작·하이브리드는 blob 불일치로 보존(사람 정리 후보 표시만).
            ev, ren_to = latest.get(path, ("", ""))
            if lblob in history.get(path, ()) and ev in ("deleted", "renamed-away"):
                safe_deletes.append(ValidationDelete(
                    path, blob=lblob,
                    reason=("upstream-renamed" if ev == "renamed-away"
                            else "upstream-deleted"),
                    renamed_to=ren_to))
            elif ev in ("deleted", "renamed-away"):
                # terminal removal 인데 blob 불일치 — 자동 삭제 금지, 후보 명시(codex R2)
                local_only.append(ValidationSkip(
                    path, "local-only-removed-upstream", lblob))
            else:
                local_only.append(ValidationSkip(path, "local-only", lblob))
            continue
        # 둘 다 있고 다름 — **같은 경로의** 역사에 있어야 safe(크로스패스 차단)
        if lblob in history.get(path, ()):
            safe.append(path)  # 이 경로의 upstream 역사에 있는 blob = 뒤처짐(무수정)
        else:
            skipped.append(ValidationSkip(path, "local-unclassified", lblob))

    return ValidationPlan(
        ref=ref, safe_paths=tuple(safe), skipped=tuple(skipped),
        local_only=tuple(local_only), up_to_date=tuple(up_to_date),
        safe_deletes=tuple(safe_deletes),
        skip_hash=_validation_skip_hash(skipped, local_only), shallow=False)


def validation_cache_path(team_root: str) -> str:
    """validation skip-cache 경로($XDG_STATE_HOME/teammode/sync/<team_key>.json)."""
    return os.path.join(_state_dir(), "sync", f"{_team_key(team_root)}.json")


def validation_skip_seen(team_root: str, skip_hash: str) -> bool:
    """이 skip_hash 가 직전 기록과 같은지(반복 skip 축약 판정용). 무raise."""
    if not skip_hash:
        return False
    try:
        import json as _json
        with open(validation_cache_path(team_root), encoding="utf-8") as f:
            return _json.load(f).get("skip_hash") == skip_hash
    except (OSError, ValueError):
        return False


def record_validation_skip(team_root: str, skip_hash: str, counts=None) -> None:
    """skip-cache 기록(원자). last_warned 는 정보용(판정엔 skip_hash 만). 무raise."""
    try:
        import json as _json
        from datetime import datetime as _dt
        path = validation_cache_path(team_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = _json.dumps({
            "version": 1,
            "repo_id": _team_key(team_root),
            "root": os.path.normpath(str(team_root)),
            "skip_hash": skip_hash,
            "last_warned": _dt.now().isoformat(timespec="seconds"),
            "counts": counts or {},
        }, ensure_ascii=False)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError:
        pass


def _checkout_chunks(team_root: str, ref: str, paths: list, timeout: int):
    """paths 를 200개 단위 chunk 로 checkout(argv 길이·구버전 git 호환). (ok, err) 반환."""
    for i in range(0, len(paths), 200):
        chunk = [str(p) for p in paths[i:i + 200]]
        try:
            rc, out, err = run_git(
                ["-C", team_root, "checkout", ref, "--", *chunk], timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "checkout timeout"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"checkout exec error: {exc}"
        if rc != 0:
            return False, ((err or out) or "").strip()[:200]
    return True, ""


def apply_validation_sync(team_root: str, ref: str, plan: ValidationPlan,
                          force: bool = False, backup: bool = True,
                          timeout: int = DEFAULT_TIMEOUT) -> ValidationApplyResult:
    """validation 계획 적용 — safe_paths 만 checkout(staged). 자동 commit/push 없음.

    force: skip(로컬 수정) 중 upstream ref 에 존재하는 path 도 덮는다 — backup=True 면
    먼저 로컬 diff 를 patch 로 XDG 아래 백업. local_only(ref 부재)는 force 도 삭제 안 함(v1).
    무raise(철칙).
    """
    if plan.shallow:
        return ValidationApplyResult(ok=True, changed=False, skipped=plan.skipped,
                                     detail="shallow — validation 적용 skip")

    # stale plan 가드(codex P1): plan 과 apply 는 분리 API — 그 사이에 생긴 편집을
    # checkout 이 덮으면 유실이다. 적용 직전 dirty 를 재수집해 safe 에서 제외한다.
    dirty_now = _validation_dirty_paths(team_root, timeout)
    late_skips = []
    targets = []
    for path in plan.safe_paths:
        if path in dirty_now:
            late_skips.append(ValidationSkip(path, "dirty", "", dirty_now[path]))
        else:
            targets.append(path)

    forced = []
    backup_path = ""
    if force:
        # 강제 대상 = skip 중 **ref 에 실재하는** 것만(codex P2 — local_only 나
        # dirty untracked 처럼 ref 부재인 path 를 넣으면 checkout 전체가 실패).
        current = _ls_tree_map(team_root, ref, timeout)
        # untracked(??) 는 force 도 제외(codex 재검수) — upstream 신규와 같은 path 의
        # untracked 로컬 파일은 `git diff ref` 패치에 안 담겨 백업이 비어 버린다.
        # v1 은 보존이 안전선(강제하려면 raw copy 백업이 필요 — v2).
        forced = [s.path for s in plan.skipped
                  if s.path in current and s.status != "??"]
        if backup and forced:
            ok_backup, backup_path = _write_validation_backup(
                team_root, ref, forced, timeout)
            if not ok_backup:
                # 백업 실패면 덮지 않는다(codex P1) — 유실 방지가 백업의 존재 이유.
                return ValidationApplyResult(
                    ok=False, skipped=plan.skipped,
                    detail="force 중단: 백업 기록 실패(XDG state 쓰기 확인) — "
                           "덮어쓰기 진행 안 함")
        targets = targets + forced

    # ── v2: safe_deletes — 백업(raw copy) 선행 후 git rm(staged) ──
    deleted = []
    delete_backup = ""
    del_targets = []
    for d in plan.safe_deletes:  # 적용 직전 dirty 재검사(삭제도 동일)
        if d.path in dirty_now:
            # 조용히 빠지면 delete-only 계획에서 출력 없이 끝난다(codex P3) — 가시화
            late_skips.append(ValidationSkip(d.path, "dirty", d.blob,
                                             dirty_now[d.path]))
        else:
            del_targets.append(d)
    if del_targets:
        ok_b, delete_backup = _write_validation_delete_backup(
            team_root, ref, [d.path for d in del_targets], timeout)
        if not ok_b:
            return ValidationApplyResult(
                ok=False, skipped=plan.skipped,
                detail="삭제 중단: 백업 기록 실패(XDG state 쓰기 확인) — "
                       "삭제 진행 안 함")
        ok_rm, rm_err = _git_rm_chunks(
            team_root, [d.path for d in del_targets], timeout)
        if not ok_rm:
            return ValidationApplyResult(
                ok=False, skipped=plan.skipped, backup_path=delete_backup,
                detail=f"git rm 실패: {rm_err}")
        deleted = [d.path for d in del_targets]

    if not targets and not deleted:
        return ValidationApplyResult(
            ok=True, changed=False,
            skipped=tuple(list(plan.skipped) + late_skips),
            detail="적용할 safe 파일 없음")
    if not targets:
        return ValidationApplyResult(
            ok=True, changed=True, deleted=tuple(deleted),
            skipped=tuple(list(plan.skipped) + late_skips),
            backup_path=delete_backup, detail="validation 삭제 적용")

    diff = diff_paths(team_root, ref, targets, timeout=timeout)
    ok, err = _checkout_chunks(team_root, ref, targets, timeout)
    if not ok:
        # 삭제가 이미 staged 됐을 수 있다(codex P2) — deleted·백업 경로를 잃지 않는다
        return ValidationApplyResult(ok=False, changed=bool(deleted),
                                     deleted=tuple(deleted),
                                     skipped=plan.skipped,
                                     backup_path=backup_path or delete_backup,
                                     detail=f"checkout 실패: {err}")
    return ValidationApplyResult(
        ok=True, changed=True,
        applied=tuple(p for p in plan.safe_paths if p in set(targets)),
        forced=tuple(forced), deleted=tuple(deleted),
        skipped=tuple(list(plan.skipped) + late_skips),
        backup_path=backup_path or delete_backup,
        diff=diff, detail="validation sync applied")


def _write_validation_delete_backup(team_root: str, ref: str, paths: list,
                                    timeout: int):
    """삭제 전 raw copy 백업 디렉토리 생성. 반환 (ok, dir_path). 무raise.

    구조(codex R2 확정): backup-<key>-<ts>-safe-deletes/{manifest.json, restore.patch,
    files/<원경로>}. canonical source 는 files/ 원본 복사 — restore.patch 는
    `git diff --binary <ref> -- paths`(ref 에 없고 로컬에 있으므로 재추가 patch).
    셋 중 하나라도 실패하면 (False, "") — 호출부는 삭제를 중단해야 한다.
    """
    try:
        import json as _json
        import shutil as _shutil
        from datetime import datetime as _dt
        stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
        bdir = os.path.join(_state_dir(), "sync",
                            f"backup-{_team_key(team_root)}-{stamp}-safe-deletes")
        files_dir = os.path.join(bdir, "files")
        os.makedirs(files_dir, exist_ok=True)
        for rel in paths:
            src = os.path.join(team_root, rel)
            dst = os.path.join(files_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # follow_symlinks=False: raw copy 계약 — 심링크는 링크 자체를 보존
            # (기본값은 target 내용 복사·broken link 실패 — codex P2)
            _shutil.copy2(src, dst, follow_symlinks=False)
        rc, out, _ = run_git(
            ["-C", team_root, "diff", "--binary", ref, "--",
             *[str(p) for p in paths]], timeout=timeout)
        if rc != 0:
            return False, ""
        with open(os.path.join(bdir, "restore.patch"), "w", encoding="utf-8") as f:
            f.write(out or "")
        with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write(_json.dumps({
                "version": 1, "kind": "safe-deletes", "ref": ref,
                "root": os.path.normpath(str(team_root)),
                "paths": list(paths),
                "restore": "git apply restore.patch 또는 files/ 내용 복사",
            }, ensure_ascii=False, indent=1))
        return True, bdir
    except (OSError, subprocess.SubprocessError):
        return False, ""


def _git_rm_chunks(team_root: str, paths: list, timeout: int):
    """`git rm -- <paths>` 200개 chunk(staged 삭제 — --cached 금지: 워킹트리 잔존이
    바로 '잔존 테스트 실행' 문제라 파일 자체를 지워야 한다). (ok, err) 반환."""
    for i in range(0, len(paths), 200):
        chunk = [str(p) for p in paths[i:i + 200]]
        try:
            rc, out, err = run_git(
                ["-C", team_root, "rm", "-q", "--", *chunk], timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "git rm timeout"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"git rm exec error: {exc}"
        if rc != 0:
            return False, ((err or out) or "").strip()[:200]
    return True, ""


def _write_validation_backup(team_root: str, ref: str, paths: list,
                             timeout: int):
    """force 덮기 전 로컬 변경을 patch 로 백업. 반환 (ok, path). 무raise.

    tri-state(codex P1): 빈 diff = 백업할 것 없음(ok=True, path="") — 안전 진행.
    diff/쓰기 실패 = ok=False — 호출부는 **덮지 않고 중단**해야 한다.
    """
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "diff", "--binary", ref, "--",
             *[str(p) for p in paths]], timeout=timeout)
        if rc != 0:
            return False, ""
        if not (out or "").strip():
            return True, ""  # 백업할 로컬 diff 없음 — 덮어도 잃을 것 없음
        from datetime import datetime as _dt
        bdir = os.path.join(_state_dir(), "sync")
        os.makedirs(bdir, exist_ok=True)
        stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
        bpath = os.path.join(bdir, f"backup-{_team_key(team_root)}-{stamp}.patch")
        with open(bpath, "w", encoding="utf-8") as f:
            f.write(out)
        return True, bpath
    except (OSError, subprocess.SubprocessError):
        return False, ""


def _norm_sync_path(path: str) -> str:
    return str(path).replace("\\", "/").strip("/").rstrip("/")


def _under_prefix(path: str, prefix: str) -> bool:
    path = _norm_sync_path(path)
    prefix = _norm_sync_path(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _allowed_sync_paths(paths: list) -> list[str]:
    """Drop protected positive paths before building git pathspecs."""
    allowed: list[str] = []
    for path in paths:
        norm = _norm_sync_path(str(path))
        if not norm:
            continue
        if any(_under_prefix(norm, denied) for denied in SYNC_DENY_PREFIXES):
            continue
        allowed.append(norm)
    return allowed


def _sync_pathspecs(paths: list) -> list:
    """positive paths → git pathspec 목록(positive + 해당 exclude). #36.

    exclude(`:(exclude)X`)는 X 의 **조상 positive 가 있고** X 자체가 명시 positive 로
    들어오지 않았을 때만 붙인다 — 기본 SYNC_PATHS(infra)는 util 보호, 향후 caller 가
    util 을 의도적으로 sync 하려 명시하면 상쇄하지 않는다. exclude 는 절대 단독 금지
    (positive 없이 넣으면 범위가 넓어짐).
    """
    specs = _allowed_sync_paths(paths)
    norm = {s.rstrip("/") for s in specs}
    for ex in SYNC_EXCLUDE_PATHS:
        ex = ex.rstrip("/")
        if ex in norm:
            continue  # 명시 positive 로 들어옴 — 상쇄 금지
        # ex 의 조상 positive 가 있나(예: 'infra' 는 'infra/skills/util' 의 조상)
        has_ancestor = any(
            ex == p or ex.startswith(p.rstrip("/") + "/") for p in norm)
        if has_ancestor:
            specs.append(f":(exclude){ex}")
    for ex in SYNC_DENY_PREFIXES:
        ex = ex.rstrip("/")
        has_ancestor = any(
            ex == p or ex.startswith(p.rstrip("/") + "/") for p in norm)
        if has_ancestor:
            specs.append(f":(exclude){ex}")
    return specs


def detect_default_branch(team_root: str, remote: str = "upstream",
                          timeout: int = DEFAULT_TIMEOUT) -> str:
    """upstream 의 기본 브랜치명을 감지(로컬 ref 우선·네트워크 없음). 폴백 'main'.

    탐지 순서(전부 로컬·무raise — hang 금지):
      1. `git symbolic-ref refs/remotes/<remote>/HEAD` → `refs/remotes/<remote>/main`
         (clone/fetch 가 설정해두는 origin/HEAD 류). 끝 세그먼트가 브랜치명.
      2. 그래도 모르면 `refs/remotes/<remote>/main` 이 존재하면 'main'.
      3. 둘 다 실패 → 'main' 폴백(팀 결정: main 가정하되 가능하면 감지).
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
                       timeout: int = NET_TIMEOUT) -> SyncResult:
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

    # 2.5) 보호 경로를 제거한 뒤 upstream 에 실재하는 경로만 동기화 — NOTICE.md 등은 옛 upstream 에 없을 수
    #      있고, 없는 pathspec 으로 checkout 하면 매칭 0 에러가 난다. 존재 경로만 골라
    #      옛 upstream 과도 호환(infra 는 받고, 없는 NOTICE 는 조용히 건너뜀).
    # 존재 필터는 **positive 만**(_path_in_ref 는 cat-file 존재확인 — pathspec 아님, #36).
    paths = [p for p in _allowed_sync_paths(paths) if _path_in_ref(team_root, ref, p, timeout)]
    if not paths:
        return SyncResult(ok=True, changed=False, paths=(), pathspecs=(),
                          detail="이미 최신")

    # git 실행용 pathspec — positive + :(exclude)infra/skills/util (인스턴스 소유 보존).
    pathspecs = _sync_pathspecs(paths)

    # 3) 변경 유무 — 없으면 멱등 종료 (util 제외 후 판정 — util 변경은 "변경"이 아님)
    diff = diff_paths(team_root, ref, pathspecs, timeout=timeout)
    if not diff:
        return SyncResult(ok=True, changed=False, paths=tuple(paths),
                          pathspecs=tuple(pathspecs), detail="이미 최신")

    # 4) dirty 가드 — 덮어쓰기로 유실될 로컬 변경 차단(util 제외라 util 로컬 변경은 무block)
    if _paths_dirty(team_root, pathspecs, timeout):
        return SyncResult(ok=False, blocked=True, paths=tuple(paths),
                          pathspecs=tuple(pathspecs), diff=diff,
                          detail="대상 경로에 커밋 안 된 로컬 변경이 있습니다")

    # 5) dry-run — 미리보기만, 실제 변경 0
    if dry_run:
        return SyncResult(ok=True, changed=False, paths=tuple(paths),
                          pathspecs=tuple(pathspecs), diff=diff,
                          detail="dry-run: 변경 미리보기")

    # 6) checkout 덮어쓰기(staged). pathspec 으로 util 제외. 자동 commit/push 없음.
    try:
        rc, out, err = run_git(
            ["-C", team_root, "checkout", ref, "--",
             *[str(p) for p in pathspecs]], timeout=timeout)
    except subprocess.TimeoutExpired:
        return SyncResult(ok=False, paths=tuple(paths),
                          pathspecs=tuple(pathspecs), detail="checkout timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return SyncResult(ok=False, paths=tuple(paths),
                          pathspecs=tuple(pathspecs),
                          detail=f"checkout exec error: {exc}")
    if rc != 0:
        return SyncResult(ok=False, paths=tuple(paths),
                          pathspecs=tuple(pathspecs),
                          detail=f"checkout 실패: {((err or out) or '').strip()[:200]}")

    return SyncResult(ok=True, changed=True, paths=tuple(paths),
                      pathspecs=tuple(pathspecs), diff=diff,
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
