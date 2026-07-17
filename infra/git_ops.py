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

import errno
import ctypes
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
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

# SessionStart do_reconcile 전체 벽시계 예산. manifest(60s)의 hook hard budget(50s)
# 안에서 정상 SSH fetch 뒤 rebase 기회를 살리면서, context용 10s를 별도로 남긴다.
RECONCILE_TOTAL_BUDGET = 40

# reconcile rebase timeout 뒤 cross-platform kill/drain(최대 7s) + exact autostash
# 확인/abort/최악 rollback postcondition(12s)을 끝내기 위한 꼬리 예약.
_RECONCILE_REBASE_RECOVERY_RESERVE = 19

# Bound publication holds the canonical index lock while Git operates on a
# private index.  This reserve is kept before the first reset/rebase so abort,
# exact-state restoration, and proof probes share the original deadline.
_BOUND_RECONCILE_RECOVERY_RESERVE = (
    _RECONCILE_REBASE_RECOVERY_RESERVE + 6)
_BOUND_ROLLBACK_STEP_TIMEOUT = 1

# do_commit(push=True)의 **진입 앵커 벽시계 총예산**(초) — 데드라인은 함수 **진입**
# 시점에 시작돼 로컬 단계(worktree/add/diff/commit + commit identity, 최악 ~16s)도 예산을
# 소모하고, 네트워크 단계(push·복구 체인 push→push -u→fetch→rebase→push -u, 최악
# NET_TIMEOUT 10s ×5 순차 ~50s)는 **남은 예산만** 쓴다. 로컬 하위호출 자체는 예산으로
# 개별 클램프/중단하지 않는다(로컬 커밋은 항상 완주·보존 — 건너뛸 수 있는 건 네트워크뿐).
# 45인 이유: 정상 GitHub SSH 왕복(첫 non-ff push + fetch 약 5s) 뒤에도 rebase와 그
# 최악 rollback 꼬리를 모두 허용한다. 느린 네트워크 경로는 남은 예산만 주고 안전하게
# pending으로 전환한다. 45 + 첫 index.lock 실패/재시도 + abort/ledger 꼬리가 훅
# manifest 캡(70s)
# 아래 머문다 — 초과 시 hook runner 가 프로세스를 죽여 로컬 커밋/rebase
# 뒤의 sync-warning 마커가 유실된다(codex 재리뷰 P1·A1).
PUSH_TOTAL_BUDGET = 45

# do_commit이 네트워크 실패 뒤 CommitResult를 만들 때 현재 checkout을 다시 읽는 두
# 로컬 probe(symbolic-ref + rev-parse)가 각각 DEFAULT_TIMEOUT까지 쓸 수 있다. 모든
# 네트워크 호출은 이 꼬리를 남긴 채 시작해 함수 자체가 총예산 안에서 반환하게 한다.
_COMMIT_RESULT_RESERVE = 2 * DEFAULT_TIMEOUT

# rebase는 timeout/nonzero/rc0+autostash-conflict 뒤에도 run_git의 cross-platform
# kill/drain(Windows 최대 5+2s), exact autostash 확인(2×1s), abort(2s), 최악
# rollback/postcondition(9×1s), CommitResult identity(2×2s)를 끝내야 한다. rebase
# timeout에 이 24초를 실제로 차감하지 않으면 첫 index.lock 재시도까지 포함한
# auto-commit이 manifest 70초 전에 pending ledger를 못 쓸 수 있다.
_REBASE_RECOVERY_RESERVE = 24


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
    # commit 직후 고정한 checkout identity. push 실패 뒤 다른 프로세스가 checkout을
    # 바꿔도 pending ledger를 실패 커밋의 branch/HEAD에 묶기 위한 증거다.
    pending_identity: dict | None = None
    # foreground에서 해석한 exact remote/ref. pending worker가 나중의 Git config나
    # current checkout을 따라 다른 곳으로 게시하지 않도록 identity와 별도 보존한다.
    pending_target: dict | None = None


@dataclass(frozen=True)
class _RebaseGuard:
    """autostash rebase 전 rollback 기준과 기존 stash tip."""

    branch: str
    head: str
    stash_head: str = ""


@dataclass(frozen=True)
class PushPendingRead:
    """pending ledger 읽기 결과.

    ``available=False`` 는 ledger lock/state 경로를 안전하게 읽지 못했다는 뜻이다.
    이 상태를 ``content == ''``(pending 없음)와 분리해야 호출부가 warning 을 지우는
    fail-open 회귀를 막을 수 있다.
    """

    content: str = ""
    available: bool = True
    fingerprint: tuple = ()


@dataclass
class FetchResult:
    ok: bool                       # fetch 성공
    detail: str = ""               # 디버그용 메시지


@dataclass
class ReconcileResult:
    ok: bool                       # 정합 성공(이미 최신 포함) 또는 정합 불필요
    action: str = "noop"           # up-to-date|fast-forward|rebased|merged|ahead-only|
    #                                no-upstream|fetch-failed|conflict|not-worktree|error
    ahead: int = 0                 # 정합 후 로컬이 upstream 보다 앞선(미push) 커밋 수
    behind: int = 0                # 정합 전 behind(진단·표면화용)
    diverged: bool = False         # 정합 전 ahead>0 & behind>0(rebase 가 필요했음)
    detail: str = ""               # 사람이 읽는 사유/요약
    # expected_identity 모드에서 정합 뒤 publication 대상 branch의 immutable identity.
    # rebase/fast-forward로 SHA가 바뀔 수 있으므로 caller가 이후 push를 이 값에 재바인딩한다.
    final_identity: dict | None = None


@dataclass(frozen=True)
class _PublicationTarget:
    """captured branch 하나만 게시하기 위한 검증된 remote/ref 묶음."""

    remote: str
    destination: str
    reconcile_ref: str
    set_upstream: bool = False
    # Hash of every configured push URL. The URL itself may contain credentials,
    # so pending state stores only this binding proof.
    remote_fingerprint: str = ""
    # In-memory only. Exact pushes use this captured endpoint instead of resolving
    # the mutable remote name again; it is deliberately omitted from pending state.
    push_endpoint: str = ""


@dataclass(frozen=True)
class _IndexMetadata:
    """Canonical index metadata that survives private-index promotion."""

    mode: int
    uid: int
    gid: int
    xattrs: tuple[tuple[str | bytes, bytes], ...] = ()
    xattrs_available: bool = False
    xattr_backend: str = ""


@dataclass
class _BoundIndexTxn:
    """One branch-bound reconcile transaction rooted beside the real index."""

    index_path: Path
    lock_path: Path
    lock_fd: int
    tx_dir: Path
    original_index: Path
    work_index: Path
    token: str
    head_ref: str
    stash_ref: str
    original_head: str
    stash_oid: str = ""
    promoted: bool = False
    tx_dir_identity: tuple[int, int] = ()
    original_index_identity: tuple[int, int] = ()
    work_index_identity: tuple[int, int] = ()
    index_metadata: _IndexMetadata | None = None


@dataclass(frozen=True)
class _BoundUserState:
    """Git-visible user state used to prove a rollback restored exact meaning."""

    status: str
    unstaged_diff: str
    staged_diff: str


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


_REPO_REDIRECT_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE",
    "GIT_REPLACE_REF_BASE", "GIT_ATTR_SOURCE",
    "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
})
_REPO_CONFIG_ENTRY_ENV_RE = re.compile(r"^GIT_CONFIG_(?:KEY|VALUE)_\d+$")


def _is_repo_scoped_git(args: list) -> bool:
    """Our repository operations always bind their target with `git -C PATH`."""
    return any(arg == "-C" and index + 1 < len(args)
               for index, arg in enumerate(args))


def run_git(args: list, timeout: int, *, env_overrides: dict | None = None,
            output_errors: str = "replace", input_text: str | None = None,
            input_bytes: bytes | None = None):
    """git 을 **자체 프로세스 그룹**으로 실행하고, 타임아웃 시 그룹 전체를 죽인다.

    이유: `subprocess.run(timeout=)` 은 직접 자식(git)에만 SIGKILL 을 보내, git 이 fork 한
    git-remote-https 같은 손자가 고아로 남아 네트워크에 매달린다(적대 검수에서 실측). 새
    세션(setsid)으로 띄워 동일 PGID 로 묶고 타임아웃 시 killpg 로 손자까지 일괄 종료한다.
    """
    child_env = git_env()
    if _is_repo_scoped_git(args):
        # `git -C requested` does not override repository/object/config redirect
        # variables inherited from the parent.  Remove ambient redirects first;
        # a trusted call-specific override (the transaction private index) is
        # applied below.  User config file selection remains intact.
        for name in tuple(child_env):
            if (name in _REPO_REDIRECT_ENV
                    or _REPO_CONFIG_ENTRY_ENV_RE.fullmatch(name)):
                child_env.pop(name, None)
    # Transaction-scoped Git knobs belong to this child only.  Copying both the
    # process environment (git_env) and iterating the caller mapping avoids
    # mutating either shared object.  ``None`` deliberately removes a hostile
    # inherited value (notably GIT_INDEX_FILE while discovering the real index).
    for key, value in dict(env_overrides or {}).items():
        if not isinstance(key, str):
            raise TypeError("git environment override names must be strings")
        if "\0" in key:
            raise ValueError("NUL is not allowed in git environment overrides")
        if value is None:
            child_env.pop(key, None)
            continue
        if not isinstance(value, str):
            value = os.fspath(value)
        if "\0" in value:
            raise ValueError("NUL is not allowed in git environment overrides")
        child_env[key] = value

    if input_text is not None and input_bytes is not None:
        raise ValueError("input_text and input_bytes are mutually exclusive")
    binary = input_bytes is not None
    kwargs = dict(
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=(subprocess.PIPE if input_text is not None or binary
               else subprocess.DEVNULL), env=child_env)
    if not binary:
        kwargs.update(text=True, encoding="utf-8", errors=output_errors)
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True  # 자식을 새 프로세스 그룹 리더로
    # credential.interactive=false: 자격증명 helper 의 **대화형 프롬프트**만 끈다(helper
    # 자체는 유지 — 캐시된 정상 자격증명은 그대로). git_env 의 GCM_* 차단과 이중 방어로
    # "인증 막혀도 즉시 실패 + 정상 인증 동작"을 보장한다. 모든 git 호출에 선행 적용.
    proc = subprocess.Popen(
        ["git", "-c", "credential.interactive=false", *args], **kwargs)
    try:
        out, err = proc.communicate(
            input=input_bytes if binary else input_text, timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired as exc:
        kill_group(proc)
        try:
            final_out, final_err = proc.communicate(timeout=2)
        except (subprocess.SubprocessError, OSError):
            final_out, final_err = "", ""
        # rebase가 mutation/autostash 출력을 낸 직후 timeout된 경우 caller가 exact OID로
        # rollback할 수 있게 partial + kill-drain 출력을 예외에 보존한다.
        def _text(value):
            if isinstance(value, bytes):
                return value.decode("utf-8", errors=output_errors)
            return str(value or "")

        exc.output = _text(getattr(exc, "output", "")) + _text(final_out)
        exc.stderr = _text(getattr(exc, "stderr", "")) + _text(final_err)
        raise


_run_git = run_git


def _timeout_detail(exc: subprocess.TimeoutExpired) -> str:
    """TimeoutExpired의 partial stdout/stderr를 문자열로 합친다."""
    def _text(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    return _text(getattr(exc, "output", "")) + "\n" + _text(
        getattr(exc, "stderr", ""))


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
            ["-C", team_root, "rev-parse", "--is-inside-work-tree",
             "--show-toplevel"],
            timeout=DEFAULT_TIMEOUT)
        lines = (out or "").splitlines()
        return (rc == 0 and len(lines) == 2 and lines[0] == "true"
                and os.path.realpath(lines[1]) == os.path.realpath(team_root))
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


def _dirty_worktree_paths(team_root: str, timeout: int) -> set[str] | None:
    """tracked/staged/untracked dirty 경로 집합. 판정 실패는 None(fail closed)."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "status", "--porcelain=v1", "-z",
             "--untracked-files=all"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0:
        return None
    records = (out or "").split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            return None
        status_code = record[:2]
        paths.add(os.path.normcase(os.path.normpath(record[3:])))
        if "R" in status_code or "C" in status_code:
            if index >= len(records) or not records[index]:
                return None
            paths.add(os.path.normcase(os.path.normpath(records[index])))
            index += 1
    return paths


def _rebase_dirty_safety_issue(
        team_root: str, upstream: str, timeout: int = DEFAULT_TIMEOUT,
        local_ref: str = "HEAD") -> str:
    """autostash apply conflict가 예상되면 사유를 반환하고 rebase를 시작하지 않는다."""
    dirty = _dirty_worktree_paths(team_root, timeout)
    if dirty is None:
        return "dirty-worktree safety check failed"
    if not dirty:
        return ""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "diff", "--no-renames", "--name-only", "-z",
             f"{local_ref}...{upstream}", "--"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return "upstream-change safety check failed"
    if rc != 0:
        return "upstream-change safety check failed"
    remote_changed = {
        os.path.normcase(os.path.normpath(path))
        for path in (out or "").split("\0") if path
    }
    overlap = sorted(dirty & remote_changed)
    if not overlap:
        return ""
    shown = ", ".join(overlap[:3])
    suffix = " ..." if len(overlap) > 3 else ""
    return f"dirty paths overlap upstream changes: {shown}{suffix}"


def _read_ref_oid(
        team_root: str, ref: str, timeout: int = DEFAULT_TIMEOUT
        ) -> tuple[bool, str]:
    """ref OID를 읽는다. ref 없음(rc=1)은 available empty, 그 외 실패는 unavailable."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-parse", "--verify", "--quiet", ref],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if rc == 0 and (out or "").strip():
        return True, (out or "").strip()
    if rc == 1:
        return True, ""
    return False, ""


def _validated_branch_identity(
        team_root: str, identity: dict | None,
        timeout: int = DEFAULT_TIMEOUT) -> dict[str, str] | None:
    """branch + full commit OID identity를 검증한다(detached/short OID는 거부)."""
    if not isinstance(identity, dict):
        return None
    key = identity.get("key")
    branch = identity.get("branch")
    head = identity.get("head")
    components = branch.split("/") if isinstance(branch, str) else []
    invalid_branch = (
        not branch or branch.startswith(("-", "/")) or branch.endswith(("/", "."))
        or branch == "@" or ".." in branch or "@{" in branch or "//" in branch
        or any(not part or part.startswith(".") or part.endswith(".lock")
               for part in components)
        or bool(re.search(r"[\x00-\x20\x7f~^:?*\[\\]", branch or "")))
    if (not all(isinstance(value, str) for value in (key, branch, head))
            or invalid_branch or key != f"branch:{branch}"
            or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head or "")):
        return None
    return {"key": key, "branch": branch, "head": head.lower()}


def _checkout_matches_identity(
        team_root: str, identity: dict,
        timeout: int = DEFAULT_TIMEOUT) -> bool:
    """현재 symbolic branch와 HEAD가 captured identity와 exact match인지 확인."""
    current = _checkout_identity(team_root, timeout)
    return (current.get("branch") == identity.get("branch")
            and (current.get("head") or "").lower() == identity.get("head"))


def _valid_full_ref(team_root: str, ref: str, timeout: int) -> bool:
    if not isinstance(ref, str) or not ref.startswith("refs/"):
        return False
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "check-ref-format", ref], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0


def _remote_names(team_root: str, timeout: int) -> list[str] | None:
    try:
        rc, out, _ = run_git(["-C", team_root, "remote"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0:
        return None
    return [line for line in (out or "").splitlines() if line]


def _remote_push_binding(
        team_root: str, remote: str,
        timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str] | None:
    """Capture exactly one argv-safe push endpoint and its credential-free hash."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "remote", "get-url", "--push", "--all", remote],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    urls = (out or "").splitlines() if rc == 0 else []
    if len(urls) != 1 or not urls[0]:
        return None
    endpoint = urls[0]
    if re.search(r"[\x00-\x1f\x7f]", endpoint) or "=" in endpoint:
        return None
    parsed = urlparse(endpoint)
    # Direct endpoint argv eliminates remote-name TOCTOU. Do not put HTTP(S)
    # userinfo/query credentials in a process argv; those configurations require
    # manual publication or a credential-helper-backed clean URL.
    if (parsed.password is not None
            or (parsed.scheme.lower() in {"http", "https"}
                and (parsed.username is not None
                     or parsed.query or parsed.fragment))):
        return None
    canonical = endpoint.encode("utf-8", errors="surrogateescape")
    return endpoint, hashlib.sha256(canonical).hexdigest()


def _remote_push_fingerprint(
        team_root: str, remote: str,
        timeout: int = DEFAULT_TIMEOUT) -> str:
    binding = _remote_push_binding(team_root, remote, timeout)
    return binding[1] if binding is not None else ""


def _valid_remote(remote: str, remotes: list[str]) -> bool:
    """argv `--` 뒤에 쓰더라도 control/option-like remote는 fail closed."""
    return (isinstance(remote, str) and remote in remotes
            and not remote.startswith("-")
            and not re.search(r"[\x00-\x20\x7f]", remote))


def _tracking_ref_for_destination(remote: str, destination: str) -> str:
    """Default fetch mapping에서 remote branch의 tracking ref를 계산한다."""
    prefix = "refs/heads/"
    if not destination.startswith(prefix):
        return ""
    return f"refs/remotes/{remote}/{destination[len(prefix):]}"


def _resolve_publication_target(
        team_root: str, identity: dict,
        timeout: int = DEFAULT_TIMEOUT, deadline: float | None = None,
        ) -> tuple[_PublicationTarget | None, str]:
    """Git의 branch별 upstream/push 해석을 explicit single-ref target으로 고정."""
    deadline = (time.monotonic() + max(1, timeout)
                if deadline is None else deadline)

    def _probe_timeout() -> int:
        remaining = int(deadline - time.monotonic())
        return min(max(1, timeout), remaining) if remaining >= 1 else 0

    branch = identity["branch"]
    local_ref = f"refs/heads/{branch}"
    fmt = ("%(refname)%00%(upstream)%00%(upstream:remotename)%00"
           "%(upstream:remoteref)%00%(push)%00%(push:remotename)%00"
           "%(push:remoteref)")
    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return None, "publication target deadline exhausted"
    try:
        rc, out, err = run_git(
            ["-C", team_root, "for-each-ref", f"--format={fmt}", "--", local_ref],
            timeout=probe_timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"publication target exec error: {exc}"
    fields = (out or "").rstrip("\n").split("\0") if rc == 0 else []
    if len(fields) != 7 or fields[0] != local_ref:
        return None, (err or "publication target unavailable").strip()[:200]
    (_refname, upstream_ref, upstream_remote, upstream_remote_ref,
     push_ref, push_remote, push_remote_ref) = fields

    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return None, "publication target deadline exhausted"
    remotes = _remote_names(team_root, probe_timeout)
    if remotes is None:
        return None, "publication remote list unavailable"
    # push atom이 채워져도 push.default=upstream + remote.pushDefault=<다른 remote>
    # 조합은 서로 모순될 수 있다. plain Git이 거부하는 구성을 explicit refspec으로
    # 우회하지 않도록 effective mode를 항상 읽고 atom/remote/destination을 함께 검증한다.
    push_default = "simple"
    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return None, "publication target deadline exhausted"
    try:
        drc, dout, _ = run_git(
            ["-C", team_root, "config", "--get", "push.default"],
            timeout=probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return None, "push.default resolution failed"
    if drc == 0:
        push_default = (dout or "").strip().lower()
    elif drc != 1:
        return None, "push.default resolution failed"
    if push_default not in {"nothing", "current", "upstream", "simple", "matching"}:
        return None, "push.default is invalid"

    def _default_remote() -> str:
        if push_remote:
            return push_remote
        if upstream_remote:
            return upstream_remote
        if "origin" in remotes:
            return "origin"
        return remotes[0] if len(remotes) == 1 else ""

    has_upstream = bool(
        upstream_ref and upstream_remote and upstream_remote_ref)
    if any((upstream_ref, upstream_remote, upstream_remote_ref)) and not has_upstream:
        return None, "configured upstream is incomplete"

    set_upstream = False
    if push_remote_ref:
        # remote.<name>.push가 exact single destination을 만들면 그 원격/목적지만 사용한다.
        if not push_ref:
            return None, "configured push destination is incomplete"
        # Git's %(push:remotename) atom can be blank for an explicit
        # remote.<name>.push refspec.  Infer the same unambiguous default Git
        # would use, but never invent pull tracking for this explicit mapping.
        remote = push_remote or _default_remote()
        destination = push_remote_ref
        reconcile_ref = push_ref
        set_upstream = False
    elif push_default in {"nothing", "matching"}:
        return None, f"push.default={push_default} has no single publication target"
    elif push_default == "upstream":
        if not has_upstream:
            return None, "push.default=upstream requires an upstream branch"
        remote = push_remote or upstream_remote
        if remote != upstream_remote:
            return None, (
                "push.default=upstream push remote does not match upstream remote")
        if push_ref and push_ref != upstream_ref:
            return None, "push.default=upstream push ref does not match upstream"
        remote = upstream_remote
        destination = upstream_remote_ref
        reconcile_ref = upstream_ref
    elif push_default == "simple":
        remote = _default_remote()
        if not has_upstream:
            # 기존 no-upstream 복구 계약: selected remote의 same-name branch를 만들고
            # 성공 뒤 captured local branch에 upstream을 별도 설정한다.
            destination = local_ref
            reconcile_ref = _tracking_ref_for_destination(remote, destination)
            set_upstream = True
        elif remote != upstream_remote:
            # triangular workflow: pull은 origin, push는 fork. Git simple publishes
            # the local same-name branch even when the pull upstream has another
            # name; the pull upstream is intentionally preserved.
            destination = local_ref
            reconcile_ref = _tracking_ref_for_destination(remote, destination)
            if push_ref and push_ref != reconcile_ref:
                return None, "triangular push ref does not match push remote"
        elif upstream_remote_ref == local_ref:
            if push_ref and push_ref != upstream_ref:
                return None, "push.default=simple push ref does not match upstream"
            destination = upstream_remote_ref
            reconcile_ref = upstream_ref
        else:
            # 같은 remote의 name mismatch는 plain simple push의 안내와 동일하게
            # captured branch same-name target을 만들고 새 upstream으로 전환한다.
            destination = local_ref
            reconcile_ref = _tracking_ref_for_destination(remote, destination)
            set_upstream = True
    else:  # push.default=current
        remote = _default_remote()
        destination = local_ref
        reconcile_ref = _tracking_ref_for_destination(remote, destination)
        if push_ref and push_ref != reconcile_ref:
            return None, "push.default=current push ref does not match push remote"
        set_upstream = False

    if not _valid_remote(remote, remotes):
        return None, "publication remote is invalid or unavailable"
    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return None, "publication target deadline exhausted"
    remote_binding = _remote_push_binding(team_root, remote, probe_timeout)
    if remote_binding is None:
        return None, "publication requires one credential-safe push URL"
    push_endpoint, remote_fingerprint = remote_binding
    probe_timeout = _probe_timeout()
    if (not probe_timeout or not destination.startswith("refs/heads/")
            or not _valid_full_ref(team_root, destination, probe_timeout)):
        return None, "publication destination is not a valid branch ref"
    probe_timeout = _probe_timeout()
    if (not probe_timeout or not reconcile_ref.startswith("refs/remotes/")
            or not _valid_full_ref(team_root, reconcile_ref, probe_timeout)):
        return None, "publication tracking ref is invalid"
    expected_tracking = _tracking_ref_for_destination(remote, destination)
    if reconcile_ref != expected_tracking:
        return None, "publication tracking ref does not match push remote/destination"
    return (_PublicationTarget(
        remote=remote, destination=destination,
        reconcile_ref=reconcile_ref, set_upstream=set_upstream,
        remote_fingerprint=remote_fingerprint,
        push_endpoint=push_endpoint), "")


def _ahead_behind_refs(
        team_root: str, upstream_ref: str, local_ref: str,
        timeout: int) -> tuple[int, int, bool]:
    """명시 ref 두 개의 (ahead, behind, available). current HEAD를 읽지 않는다."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-list", "--count", "--left-right",
             f"{upstream_ref}...{local_ref}"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 0, 0, False
    parts = (out or "").split() if rc == 0 else []
    if len(parts) != 2:
        return 0, 0, False
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0, False
    return ahead, behind, True


def _capture_rebase_guard(
        team_root: str, timeout: int = DEFAULT_TIMEOUT) -> _RebaseGuard | None:
    """rebase 직전 branch/HEAD와 기존 stash tip을 fail-closed로 캡처한다."""
    identity = _checkout_identity(team_root, timeout)
    if not identity.get("branch") or not identity.get("head"):
        return None
    stash_available, stash_head = _read_ref_oid(team_root, "refs/stash", timeout)
    if not stash_available:
        return None
    return _RebaseGuard(
        branch=identity["branch"], head=identity["head"], stash_head=stash_head)


def _unmerged_paths(
        team_root: str, timeout: int = DEFAULT_TIMEOUT) -> list[str] | None:
    """현재 unmerged 경로. 판정 실패는 None으로 fail closed."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "diff", "--name-only", "--diff-filter=U", "-z",
             "--"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0:
        return None
    return [path for path in (out or "").split("\0") if path]


def _is_autostash_commit(
        team_root: str, oid: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """새 stash OID가 Git rebase가 남긴 autostash인지 확인한다."""
    if not oid:
        return False
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "show", "-s", "--format=%s", oid],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0 and "autostash" in (out or "").lower()


_CREATED_AUTOSTASH_RE = re.compile(
    r"(?im)^Created autostash:\s*([0-9a-f]{4,64})\s*$")


def _created_autostash_oid(
        team_root: str, output: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """C-locale rebase 출력에서 Git이 실제 생성한 autostash OID를 full OID로 푼다."""
    matches = _CREATED_AUTOSTASH_RE.findall(output or "")
    # TimeoutExpired partial output + kill-drain communicate가 같은 줄을 중복 제공할 수
    # 있다. 서로 다른 OID는 거부하되 동일 OID 반복은 하나의 증거로 정규화한다.
    unique_matches = {match.lower() for match in matches}
    if len(unique_matches) != 1:
        return ""
    short_oid = unique_matches.pop()
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-parse", "--verify", f"{short_oid}^{{commit}}"],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    full_oid = (out or "").strip() if rc == 0 else ""
    if (not re.fullmatch(r"[0-9a-fA-F]{40,64}", full_oid)
            or not _is_autostash_commit(team_root, full_oid, timeout)):
        return ""
    return full_oid.lower()


def _restore_failed_autostash(
        team_root: str, guard: _RebaseGuard, autostash_oid: str,
        timeout: int = DEFAULT_TIMEOUT) -> bool:
    """rc=0 뒤 autostash apply 충돌을 pre-rebase HEAD+dirty/index 상태로 복원한다.

    새 autostash OID가 검증된 경우에만 hard reset을 허용한다. apply 후 stash entry는
    의도적으로 남겨 추가 복구 사본으로 보존한다(동시 stash를 selector로 오삭제 금지).
    """
    current = _checkout_identity(team_root, timeout)
    if current.get("branch") != guard.branch:
        return False
    try:
        rrc, _, _ = run_git(
            ["-C", team_root, "reset", "--hard", guard.head],
            timeout=timeout)
        if rrc != 0:
            return False
        arc, _, _ = run_git(
            ["-C", team_root, "stash", "apply", "--index", autostash_oid],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    unmerged = _unmerged_paths(team_root, timeout)
    restored = _checkout_identity(team_root, timeout)
    return (arc == 0 and unmerged == []
            and restored.get("branch") == guard.branch
            and restored.get("head") == guard.head)


def _verify_rebase_postcondition(
        team_root: str, guard: _RebaseGuard,
        created_autostash_oid: str = "",
        timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """rebase 후 conflict 없음+autostash 정상 적용을 확인하고 실패 시 rollback한다."""
    unmerged = _unmerged_paths(team_root, timeout)
    stash_available, stash_head = _read_ref_oid(
        team_root, "refs/stash", timeout)
    if unmerged is None or not stash_available:
        return False, "rebase postcondition check failed"
    if not unmerged and stash_head == guard.stash_head:
        return True, ""

    # autostash apply 실패 시 Git은 rc=0이어도 새 entry와 UU를 남긴다. linked worktree의
    # 동시 `git stash -m autostash`가 refs/stash top을 바꿀 수 있으므로 subject/top 추론은
    # 금지하고, rebase C-locale 출력의 `Created autostash: <oid>`만 복원 증거로 쓴다.
    if created_autostash_oid:
        if _restore_failed_autostash(
                team_root, guard, created_autostash_oid, timeout):
            return False, "autostash apply conflict restored; backup kept in stash"
        return False, "autostash apply conflict; rollback incomplete — backup kept in stash"
    if unmerged:
        return False, "rebase left unmerged paths; automatic rollback not proven"
    return False, "stash changed during rebase; publication deferred"


def _verify_rebase_rollback_postcondition(
        team_root: str, guard: _RebaseGuard,
        created_autostash_oid: str = "",
        timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """abort 뒤 branch+OID, unmerged, autostash 상태가 모두 복원됐는지 입증."""
    post_ok, detail = _verify_rebase_postcondition(
        team_root, guard, created_autostash_oid, timeout)
    current = _checkout_identity(team_root, timeout)
    restored = (current.get("branch") == guard.branch
                and (current.get("head") or "").lower() == guard.head.lower())
    if not restored:
        identity_detail = "checkout branch/HEAD not restored"
        detail = f"{detail}; {identity_detail}" if detail else identity_detail
    return post_ok and restored, detail


def _rebase_abort_detail(
        prefix: str, abort_ok: bool, rollback_ok: bool,
        post_detail: str, failure_detail: str = "") -> str:
    """abort+rollback 증거가 있을 때만 affirmative `(aborted)`를 만든다."""
    proven = abort_ok and rollback_ok
    status = ("rebase failed (aborted)" if proven
              else "abort attempted; rollback not proven")
    if prefix == "rebase failed":
        detail = status if proven else f"rebase failed; {status}"
    else:
        detail = f"{prefix}; {status}"
    if failure_detail:
        detail += f": {failure_detail}"
    if post_detail:
        detail += f"; {post_detail}"
    return detail


def _deadline_timeout(deadline: float, cap: int, reserve: int = 0) -> int:
    """Clamp one probe to a shared absolute deadline without minting budget."""
    remaining = int(deadline - time.monotonic() - reserve)
    return min(max(1, cap), remaining) if remaining >= 1 else 0


_XATTR_NATIVE_NAMES = ("listxattr", "getxattr", "setxattr", "removexattr")
_DARWIN_XATTR_BACKEND = "darwin-libc"
_DARWIN_XATTR_NOFOLLOW = 0x0001
_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_DARWIN_ACL_FIRST_ENTRY = 0


def _darwin_xattr_libc():
    """Return configured Darwin libc xattr functions or None (fail closed)."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.listxattr.argtypes = [
            ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        libc.listxattr.restype = ctypes.c_ssize_t
        libc.getxattr.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        libc.getxattr.restype = ctypes.c_ssize_t
        libc.setxattr.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        libc.setxattr.restype = ctypes.c_int
        libc.removexattr.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        libc.removexattr.restype = ctypes.c_int
        return libc
    except (AttributeError, OSError, TypeError):
        return None


def _raise_xattr_errno(operation: str) -> None:
    error = ctypes.get_errno() or errno.EIO
    raise OSError(error, f"Darwin {operation} failed")


def _darwin_list_xattrs(path: Path, libc) -> tuple[bytes, ...]:
    encoded_path = os.fsencode(path)
    for _attempt in range(3):
        ctypes.set_errno(0)
        size = libc.listxattr(
            encoded_path, None, 0, _DARWIN_XATTR_NOFOLLOW)
        if size < 0:
            _raise_xattr_errno("listxattr size")
        if size == 0:
            return ()
        buffer = ctypes.create_string_buffer(size)
        ctypes.set_errno(0)
        actual = libc.listxattr(
            encoded_path, buffer, size, _DARWIN_XATTR_NOFOLLOW)
        if actual < 0:
            if ctypes.get_errno() == errno.ERANGE:
                continue
            _raise_xattr_errno("listxattr")
        raw = bytes(buffer.raw[:actual])
        if not raw.endswith(b"\0"):
            raise OSError(errno.EIO, "Darwin listxattr returned malformed names")
        names = tuple(raw[:-1].split(b"\0"))
        if any(not name for name in names):
            raise OSError(errno.EIO, "Darwin listxattr returned empty name")
        return names
    raise OSError(errno.EBUSY, "Darwin xattr names changed during capture")


def _darwin_get_xattr(path: Path, name: bytes, libc) -> bytes:
    encoded_path = os.fsencode(path)
    for _attempt in range(3):
        ctypes.set_errno(0)
        size = libc.getxattr(
            encoded_path, name, None, 0, 0, _DARWIN_XATTR_NOFOLLOW)
        if size < 0:
            _raise_xattr_errno("getxattr size")
        if size == 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        ctypes.set_errno(0)
        actual = libc.getxattr(
            encoded_path, name, buffer, size, 0, _DARWIN_XATTR_NOFOLLOW)
        if actual < 0:
            if ctypes.get_errno() == errno.ERANGE:
                continue
            _raise_xattr_errno("getxattr")
        return bytes(buffer.raw[:actual])
    raise OSError(errno.EBUSY, "Darwin xattr value changed during capture")


def _darwin_set_xattr(path: Path, name: bytes, value: bytes, libc) -> None:
    value_buffer = ctypes.create_string_buffer(value, len(value)) if value else None
    ctypes.set_errno(0)
    result = libc.setxattr(
        os.fsencode(path), name, value_buffer, len(value), 0,
        _DARWIN_XATTR_NOFOLLOW)
    if result != 0:
        _raise_xattr_errno("setxattr")


def _darwin_remove_xattr(path: Path, name: bytes, libc) -> None:
    ctypes.set_errno(0)
    if libc.removexattr(
            os.fsencode(path), name, _DARWIN_XATTR_NOFOLLOW) != 0:
        _raise_xattr_errno("removexattr")


def _native_xattrs_available() -> bool:
    return all(hasattr(os, name) for name in _XATTR_NATIVE_NAMES)


def _capture_xattrs_with_backend(
        path: Path, backend: str) -> tuple[tuple[str | bytes, bytes], ...]:
    if backend == "native":
        names = os.listxattr(path, follow_symlinks=False)
        return tuple(
            (name, os.getxattr(path, name, follow_symlinks=False))
            for name in sorted(names, key=lambda item: os.fsencode(item)))
    if backend == _DARWIN_XATTR_BACKEND:
        libc = _darwin_xattr_libc()
        if libc is None:
            raise OSError(errno.ENOTSUP, "Darwin libc xattr backend unavailable")
        return tuple(
            (name, _darwin_get_xattr(path, name, libc))
            for name in sorted(_darwin_list_xattrs(path, libc)))
    return ()


def _nofollow_xattr_names(path: Path) -> tuple[bytes, ...] | None:
    """Return raw-ish nofollow names, or None when a safe probe is unavailable."""
    try:
        if sys.platform == "darwin":
            libc = _darwin_xattr_libc()
            if libc is None:
                return None
            return tuple(sorted(_darwin_list_xattrs(path, libc)))
        if _native_xattrs_available():
            return tuple(sorted(
                (os.fsencode(name) for name in os.listxattr(
                    path, follow_symlinks=False))))
    except (OSError, TypeError, UnicodeError):
        return None
    return () if os.name == "nt" else None


def _darwin_acl_libc():
    """Return configured Darwin extended-ACL functions or None."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        libc.acl_get_file.restype = ctypes.c_void_p
        libc.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
        libc.acl_get_link_np.restype = ctypes.c_void_p
        libc.acl_get_entry.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p)]
        libc.acl_get_entry.restype = ctypes.c_int
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free.restype = ctypes.c_int
        return libc
    except (AttributeError, OSError, TypeError):
        return None


def _darwin_has_extended_acl(
        path: Path, *, symlink: bool, libc) -> bool | None:
    """Tri-state Darwin extended ACL probe without following a symlink."""
    getter = libc.acl_get_link_np if symlink else libc.acl_get_file
    ctypes.set_errno(0)
    acl = getter(os.fsencode(path), _DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        # Darwin reports ENOENT when the inode has no extended ACL object.
        return False if ctypes.get_errno() == errno.ENOENT else None
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = libc.acl_get_entry(
            acl, _DARWIN_ACL_FIRST_ENTRY, ctypes.byref(entry))
        # Darwin's acl_get_entry(3) returns 0 on success and populates entry.
        if result == 0 and entry.value:
            return True
        if result == 0:
            return False
        return None
    finally:
        libc.acl_free(acl)


def _capture_index_xattrs(
        path: Path) -> tuple[tuple[tuple[str | bytes, bytes], ...], str]:
    if _native_xattrs_available():
        try:
            return _capture_xattrs_with_backend(path, "native"), "native"
        except OSError as exc:
            unsupported = {
                errno.ENOSYS,
                getattr(errno, "ENOTSUP", errno.ENOSYS),
                getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
            }
            if exc.errno not in unsupported:
                raise
    if os.name == "nt":  # Windows ACLs remain an explicit residual.
        return (), ""
    if _darwin_xattr_libc() is None:
        raise OSError(errno.ENOTSUP, "safe xattr backend unavailable", str(path))
    return (_capture_xattrs_with_backend(path, _DARWIN_XATTR_BACKEND),
            _DARWIN_XATTR_BACKEND)


def _apply_index_xattrs(
        path: Path, attrs: tuple[tuple[str | bytes, bytes], ...],
        backend: str) -> None:
    expected = dict(attrs)
    if backend == "native":
        if not _native_xattrs_available():
            raise OSError(errno.ENOTSUP, "native xattr backend unavailable")
        for name in os.listxattr(path, follow_symlinks=False):
            if name not in expected:
                os.removexattr(path, name, follow_symlinks=False)
        for name, value in attrs:
            os.setxattr(path, name, value, follow_symlinks=False)
        return
    if backend == _DARWIN_XATTR_BACKEND:
        libc = _darwin_xattr_libc()
        if libc is None:
            raise OSError(errno.ENOTSUP, "Darwin libc xattr backend unavailable")
        actual = dict(_capture_xattrs_with_backend(path, backend))
        for name in actual:
            if name not in expected:
                _darwin_remove_xattr(path, name, libc)
        for name, value in attrs:
            _darwin_set_xattr(path, name, value, libc)


def _capture_index_metadata(path: Path) -> _IndexMetadata:
    current = os.lstat(path)
    if not stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode):
        raise OSError(errno.EPERM, "unsafe index metadata source", str(path))
    xattrs, xattr_backend = _capture_index_xattrs(path)
    return _IndexMetadata(
        mode=stat.S_IMODE(current.st_mode), uid=current.st_uid,
        gid=current.st_gid, xattrs=xattrs,
        xattrs_available=bool(xattr_backend), xattr_backend=xattr_backend)


def _index_metadata_matches(path: Path, metadata: _IndexMetadata) -> bool:
    try:
        current = os.lstat(path)
        if (not stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)
                or stat.S_IMODE(current.st_mode) != metadata.mode):
            return False
        if os.name != "nt" and (current.st_uid, current.st_gid) != (
                metadata.uid, metadata.gid):
            return False
        if metadata.xattrs_available:
            actual = _capture_xattrs_with_backend(
                path, metadata.xattr_backend)
            if actual != metadata.xattrs:
                return False
        return True
    except OSError:
        return False


def _apply_index_metadata(path: Path, metadata: _IndexMetadata) -> None:
    """Apply and verify stdlib-visible metadata without following symlinks."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode):
            raise OSError(errno.EPERM, "private index is not regular", str(path))
        if hasattr(os, "fchmod"):
            os.fchmod(fd, metadata.mode)
        else:  # pragma: no cover - Windows fallback
            os.chmod(path, metadata.mode)
        if os.name != "nt" and hasattr(os, "fchown"):
            os.fchown(fd, metadata.uid, metadata.gid)
    finally:
        os.close(fd)
    if metadata.xattrs_available:
        _apply_index_xattrs(
            path, metadata.xattrs, metadata.xattr_backend)
    if not _index_metadata_matches(path, metadata):
        raise OSError(errno.EIO, "index metadata verification failed", str(path))


def _secure_copy_regular(source: Path, destination: Path) -> None:
    """Copy one owner-controlled regular file without following symlinks."""
    source_stat = os.lstat(source)
    if (not stat.S_ISREG(source_stat.st_mode)
            or stat.S_ISLNK(source_stat.st_mode)
            or (hasattr(os, "getuid") and source_stat.st_uid != os.getuid())):
        raise OSError(errno.EPERM, "unsafe canonical index", str(source))
    read_fd = write_fd = -1
    destination_identity: tuple[int, int] = ()
    try:
        read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        read_flags |= getattr(os, "O_NOFOLLOW", 0)
        read_fd = os.open(source, read_flags)
        opened = os.fstat(read_fd)
        if ((opened.st_dev, opened.st_ino)
                != (source_stat.st_dev, source_stat.st_ino)):
            raise OSError(errno.EBUSY, "canonical index changed during open")
        write_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                       | getattr(os, "O_CLOEXEC", 0)
                       | getattr(os, "O_NOFOLLOW", 0))
        write_fd = os.open(destination, write_flags, 0o600)
        written = os.fstat(write_fd)
        destination_identity = (written.st_dev, written.st_ino)
        while True:
            chunk = os.read(read_fd, 1024 * 1024)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(write_fd, chunk[offset:])
        os.fsync(write_fd)
    except BaseException:
        if destination_identity:
            try:
                current = os.lstat(destination)
                if ((current.st_dev, current.st_ino) == destination_identity
                        and stat.S_ISREG(current.st_mode)
                        and not stat.S_ISLNK(current.st_mode)):
                    os.unlink(destination)
            except OSError:
                pass
        raise
    finally:
        for fd in (write_fd, read_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _bound_lock_owned(txn: _BoundIndexTxn) -> bool:
    if txn.lock_fd < 0:
        return False
    try:
        opened = os.fstat(txn.lock_fd)
        current = os.lstat(txn.lock_path)
    except OSError:
        return False
    return ((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
            and stat.S_ISREG(current.st_mode) and not stat.S_ISLNK(current.st_mode))


def _release_bound_lock(txn: _BoundIndexTxn) -> tuple[bool, str]:
    """Release only our lock inode and prove unlink plus parent durability."""
    ok = True
    details: list[str] = []
    if not _bound_lock_owned(txn):
        ok = False
        details.append("canonical index lock ownership changed")
    else:
        try:
            os.unlink(txn.lock_path)
        except OSError as exc:
            ok = False
            details.append(f"canonical index lock unlink failed: {exc}")
        else:
            if not _fsync_parent_dir(str(txn.lock_path)):
                ok = False
                details.append("canonical index lock parent fsync failed")
    if txn.lock_fd >= 0:
        try:
            os.close(txn.lock_fd)
        except OSError as exc:
            ok = False
            details.append(f"canonical index lock close failed: {exc}")
        txn.lock_fd = -1
    return ok, "; ".join(details)


def _remove_bound_tx_dir(txn: _BoundIndexTxn) -> bool:
    """Remove only our private same-admin-dir transaction directory."""
    try:
        current = os.lstat(txn.tx_dir)
        if (txn.tx_dir.parent != txn.index_path.parent
                or not stat.S_ISDIR(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != txn.tx_dir_identity
                or (hasattr(os, "getuid") and current.st_uid != os.getuid())):
            return False
        # Prove the directory entries are durable before deleting the last
        # filesystem recovery anchor.  A failed preflight leaves it intact.
        if not _fsync_parent_dir(str(txn.original_index)):
            return False
        for path, expected_identity in (
                (txn.work_index, txn.work_index_identity),
                (txn.original_index, txn.original_index_identity)):
            try:
                child = os.lstat(path)
            except FileNotFoundError:
                continue
            if (not expected_identity
                    or (child.st_dev, child.st_ino) != expected_identity
                    or not stat.S_ISREG(child.st_mode)
                    or stat.S_ISLNK(child.st_mode)
                    or (hasattr(os, "getuid") and child.st_uid != os.getuid())):
                return False
            os.unlink(path)
        os.rmdir(txn.tx_dir)
        return _fsync_parent_dir(str(txn.tx_dir))
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _ensure_bound_filesystem_anchor(txn: _BoundIndexTxn) -> bool:
    """Create a durable common-admin blocker after ref cleanup uncertainty."""
    try:
        try:
            current = os.lstat(txn.tx_dir)
        except FileNotFoundError:
            os.mkdir(txn.tx_dir, mode=0o700)
            current = os.lstat(txn.tx_dir)
        if (txn.tx_dir.parent != txn.index_path.parent
                or not stat.S_ISDIR(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (hasattr(os, "getuid") and current.st_uid != os.getuid())):
            return False
        marker = txn.tx_dir / "RECOVERY"
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        try:
            fd = os.open(marker, flags, 0o600)
        except FileExistsError:
            marker_stat = os.lstat(marker)
            if (not stat.S_ISREG(marker_stat.st_mode)
                    or stat.S_ISLNK(marker_stat.st_mode)
                    or (hasattr(os, "getuid")
                        and marker_stat.st_uid != os.getuid())):
                return False
        else:
            try:
                payload = (
                    f"token={txn.token}\nhead_ref={txn.head_ref}\n"
                    f"stash_ref={txn.stash_ref}\n").encode("utf-8")
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError(errno.EIO, "recovery marker short write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        return (_fsync_parent_dir(str(marker))
                and _fsync_parent_dir(str(txn.tx_dir)))
    except OSError:
        return False


def _begin_bound_index_tx(
        team_root: str, identity: dict[str, str], timeout: int
        ) -> tuple[_BoundIndexTxn | None, str]:
    """Acquire the canonical index lock and make two private index copies."""
    try:
        rc, out, err = run_git(
            ["-C", team_root, "rev-parse", "--git-path", "index"],
            timeout=timeout, env_overrides={"GIT_INDEX_FILE": None})
        if rc != 0 or not (out or "").strip():
            return None, (err or "canonical index unavailable").strip()[:200]
        raw = Path((out or "").strip())
        index_path = raw if raw.is_absolute() else Path(team_root) / raw
        index_path = Path(os.path.abspath(index_path))
        parent_stat = os.lstat(index_path.parent)
        if (not stat.S_ISDIR(parent_stat.st_mode)
                or stat.S_ISLNK(parent_stat.st_mode)
                or (hasattr(os, "getuid") and parent_stat.st_uid != os.getuid())):
            return None, "canonical index admin directory is unsafe"
        index_stat = os.lstat(index_path)
        if (not stat.S_ISREG(index_stat.st_mode)
                or stat.S_ISLNK(index_stat.st_mode)
                or (hasattr(os, "getuid") and index_stat.st_uid != os.getuid())):
            return None, "canonical index is unsafe"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"canonical index discovery failed: {exc}"

    lock_path = Path(f"{index_path}.lock")
    token = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    lock_fd = -1
    tx_dir: Path | None = None
    try:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        lock_fd = os.open(lock_path, flags, 0o600)
    except FileExistsError:
        return None, "canonical index is locked by another Git operation"
    except OSError as exc:
        return None, f"canonical index lock unavailable: {exc}"

    txn: _BoundIndexTxn | None = None
    created_tx_identity: tuple[int, int] = ()
    created_original_identity: tuple[int, int] = ()
    created_work_identity: tuple[int, int] = ()
    try:
        os.write(
            lock_fd, f"tm-mode bound reconcile {token}\n".encode("ascii"))
        os.fsync(lock_fd)
        if not _fsync_parent_dir(str(lock_path)):
            raise OSError(errno.EIO, "bound lock durability unavailable")
        index_metadata = _capture_index_metadata(index_path)
        tx_dir = Path(tempfile.mkdtemp(
            prefix=f".tm-mode-reconcile-{token}-", dir=index_path.parent))
        os.chmod(tx_dir, 0o700)
        tx_dir_stat = os.lstat(tx_dir)
        created_tx_identity = (tx_dir_stat.st_dev, tx_dir_stat.st_ino)
        original_index = tx_dir / "original-index"
        work_index = tx_dir / "work-index"
        _secure_copy_regular(index_path, original_index)
        original_stat = os.lstat(original_index)
        created_original_identity = (original_stat.st_dev, original_stat.st_ino)
        _apply_index_metadata(original_index, index_metadata)
        # The work index must come from the already captured original, never a
        # second read of a lock-unaware writer's canonical replacement.
        _secure_copy_regular(original_index, work_index)
        work_stat = os.lstat(work_index)
        created_work_identity = (work_stat.st_dev, work_stat.st_ino)
        _apply_index_metadata(work_index, index_metadata)
        for durable_index in (original_index, work_index):
            durable_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            durable_flags |= getattr(os, "O_NOFOLLOW", 0)
            durable_fd = os.open(durable_index, durable_flags)
            try:
                durable_stat = os.fstat(durable_fd)
                if not stat.S_ISREG(durable_stat.st_mode):
                    raise OSError(
                        errno.EPERM, "private index durability target is unsafe")
                os.fsync(durable_fd)
            finally:
                os.close(durable_fd)
        if (not _fsync_parent_dir(str(work_index))
                or not _fsync_parent_dir(str(tx_dir))):
            raise OSError(
                errno.EIO, "bound recovery mapping durability unavailable")
        original_stat = os.lstat(original_index)
        work_stat = os.lstat(work_index)
        txn = _BoundIndexTxn(
            index_path=index_path, lock_path=lock_path, lock_fd=lock_fd,
            tx_dir=tx_dir, original_index=original_index,
            work_index=work_index, token=token,
            head_ref=f"refs/tm-mode/reconcile/{token}/head",
            stash_ref=f"refs/tm-mode/reconcile/{token}/stash",
            original_head=identity["head"],
            tx_dir_identity=(tx_dir_stat.st_dev, tx_dir_stat.st_ino),
            original_index_identity=(original_stat.st_dev, original_stat.st_ino),
            work_index_identity=(work_stat.st_dev, work_stat.st_ino),
            index_metadata=index_metadata)
        return txn, ""
    except OSError as exc:
        if txn is None:
            txn = _BoundIndexTxn(
                index_path=index_path, lock_path=lock_path, lock_fd=lock_fd,
                tx_dir=tx_dir or index_path.parent / ".missing-tx",
                original_index=(tx_dir or index_path.parent) / "original-index",
                work_index=(tx_dir or index_path.parent) / "work-index",
                token=token,
                head_ref=f"refs/tm-mode/reconcile/{token}/head",
                stash_ref=f"refs/tm-mode/reconcile/{token}/stash",
                original_head=identity["head"],
                tx_dir_identity=created_tx_identity,
                original_index_identity=created_original_identity,
                work_index_identity=created_work_identity)
        release_ok, release_detail = _release_bound_lock(txn)
        cleanup_ok = True
        if release_ok and tx_dir is not None:
            cleanup_ok = _remove_bound_tx_dir(txn)
        detail = f"bound reconcile transaction unavailable: {exc}"
        if not release_ok:
            detail += (f"; lock release failed: {release_detail}; "
                       f"recovery evidence retained at {txn.tx_dir}")
        elif not cleanup_ok:
            detail += f"; recovery cleanup failed at {txn.tx_dir}"
        return None, detail


def _run_bound_git(
        team_root: str, txn: _BoundIndexTxn, args: list[str], timeout: int,
        *, proof_raw: bool = False, input_text: str | None = None):
    try:
        return run_git(
            ["-C", team_root, *args], timeout=timeout,
            env_overrides={"GIT_INDEX_FILE": str(txn.work_index)},
            output_errors="surrogateescape" if proof_raw else "replace",
            input_text=input_text)
    finally:
        # Git updates an index through `<path>.lock` + rename, so the work-index
        # inode legitimately changes.  Refresh only an owner-controlled regular
        # file at the exact transaction path; cleanup still refuses replacements
        # it never observed through this wrapper.
        try:
            current = os.lstat(txn.work_index)
            if (stat.S_ISREG(current.st_mode) and not stat.S_ISLNK(current.st_mode)
                    and txn.work_index.parent == txn.tx_dir
                    and (not hasattr(os, "getuid") or current.st_uid == os.getuid())):
                txn.work_index_identity = (current.st_dev, current.st_ino)
        except OSError:
            pass


def _capture_bound_user_state(
        team_root: str, txn: _BoundIndexTxn, deadline: float,
        reserve: int = 0, cap: int = DEFAULT_TIMEOUT) -> _BoundUserState | None:
    commands = (
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--"],
        ["diff", "--cached", "--binary", "--no-ext-diff",
         "--no-textconv", "--"],
    )
    outputs: list[str] = []
    for args in commands:
        probe_timeout = _deadline_timeout(
            deadline, cap, reserve=reserve)
        if not probe_timeout:
            return None
        try:
            rc, out, _ = _run_bound_git(
                team_root, txn, list(args), probe_timeout, proof_raw=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0:
            return None
        outputs.append(out or "")
    return _BoundUserState(*outputs)


def _bound_hidden_index_flags(
        team_root: str, txn: _BoundIndexTxn, deadline: float,
        reserve: int = 0) -> bool | None:
    """Return whether any private-index entry hides worktree changes.

    ``ls-files -v`` lower-cases an entry tag for assume-unchanged and uses
    ``S`` for skip-worktree.  Either flag makes status/diff/stash an incomplete
    proof, so the bound transaction must fail before its first reset.
    """
    probe_timeout = _deadline_timeout(
        deadline, DEFAULT_TIMEOUT, reserve=reserve)
    if not probe_timeout:
        return None
    try:
        rc, out, _ = _run_bound_git(
            team_root, txn, ["ls-files", "-v", "-z", "--"],
            probe_timeout, proof_raw=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0:
        return None
    for record in (out or "").split("\0"):
        if not record:
            continue
        if len(record) < 3 or record[1] != " ":
            return None
        tag = record[0]
        if tag == "S" or tag.islower():
            return True
    return False


def _probe_process_umask(timeout: int) -> int | None:
    """Read the inherited checkout umask in a child without changing our process."""
    try:
        result = subprocess.run(
            ["sh", "-c", "umask"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-7]{3,4}", raw):
        return None
    return int(raw, 8)


def _bound_worktree_metadata_issue(
        team_root: str, txn: _BoundIndexTxn, deadline: float,
        reserve: int = 0, *, local_source: str | None = None,
        upstream_source: str | None = None) -> str | None:
    """Prove reset/stash cannot erase tracked-path metadata before mutation.

    Git only reconstructs blob bytes plus its executable/symlink bit.  Exact
    POSIX permissions, ownership, hard-link identity, flags, ACLs, and custom
    xattrs are therefore not recoverable from the stash proof.  Defer instead
    of silently normalizing them.  Missing tracked paths carry no local inode
    metadata and are safe for Git to recreate.
    """
    if os.name == "nt":  # Windows ACL preservation remains documented residual.
        return ""
    probe_timeout = _deadline_timeout(
        deadline, DEFAULT_TIMEOUT, reserve=reserve)
    if not probe_timeout:
        return None
    checkout_umask = _probe_process_umask(probe_timeout)
    if checkout_umask is None:
        return None
    try:
        rc, out, _ = _run_bound_git(
            team_root, txn, ["ls-files", "--stage", "-z", "--"],
            probe_timeout, proof_raw=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0 or (out and not out.endswith("\0")):
        return None

    entries: list[tuple[str, str]] = []
    candidate_paths: list[str] = []
    seen_candidate_paths: set[str] = set()
    for record in (out or "").split("\0"):
        if not record:
            continue
        try:
            header, path = record.split("\t", 1)
            git_mode, object_id, stage = header.split(" ")
        except ValueError:
            return None
        if (stage != "0"
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id)):
            return None
        entries.append((path, git_mode))
        if path not in seen_candidate_paths:
            seen_candidate_paths.add(path)
            candidate_paths.append(path)

    # The current private index is necessary for partial-commit/staged paths,
    # while exact local/upstream trees add rename and newly tracked targets that
    # do not exist in that index yet.  Their parent directories can determine
    # ownership, ACL, flags, xattrs, and creation mode after reset/rebase.
    for source in (local_source, upstream_source):
        if source is None:
            continue
        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT, reserve=reserve)
        if not probe_timeout:
            return None
        try:
            rc, tree_out, _ = _run_bound_git(
                team_root, txn,
                ["ls-tree", "-r", "-z", "--full-tree", source, "--"],
                probe_timeout, proof_raw=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0 or (tree_out and not tree_out.endswith("\0")):
            return None
        for record in (tree_out or "").split("\0"):
            if not record:
                continue
            try:
                header, path = record.split("\t", 1)
                git_mode, object_type, object_id = header.split(" ")
            except ValueError:
                return None
            valid_entry = (
                (git_mode in {"100644", "100755", "120000"}
                 and object_type == "blob")
                or (git_mode == "160000" and object_type == "commit"))
            if (not valid_entry
                    or not re.fullmatch(
                        r"[0-9a-f]{40}|[0-9a-f]{64}", object_id)):
                return None
            if path not in seen_candidate_paths:
                seen_candidate_paths.add(path)
                candidate_paths.append(path)

    acl_libc = None
    checked_parents: set[Path] = set()
    root = Path(os.path.abspath(team_root))
    allowed_xattrs = ({b"com.apple.provenance"}
                      if sys.platform == "darwin" else set())

    def _parent_issue(parent: Path) -> str | None:
        nonlocal acl_libc
        if parent in checked_parents:
            return ""
        if not _deadline_timeout(deadline, DEFAULT_TIMEOUT, reserve=reserve):
            return None
        try:
            before_parent = os.lstat(parent)
        except OSError:
            return None
        if (not stat.S_ISDIR(before_parent.st_mode)
                or stat.S_ISLNK(before_parent.st_mode)):
            return "non-recoverable parent directory metadata type"
        expected_dir_mode = 0o777 & ~checkout_umask
        if (before_parent.st_uid != os.getuid()
                or before_parent.st_gid != os.getgid()
                or stat.S_IMODE(before_parent.st_mode) != expected_dir_mode
                or before_parent.st_mode & stat.S_ISGID):
            return "non-recoverable parent directory metadata ownership or mode"
        if sys.platform == "darwin":
            if not hasattr(before_parent, "st_flags"):
                return None
            if before_parent.st_flags != 0:
                return "non-recoverable parent directory metadata flags"
            if acl_libc is None:
                acl_libc = _darwin_acl_libc()
                if acl_libc is None:
                    return None
            has_acl = _darwin_has_extended_acl(
                parent, symlink=False, libc=acl_libc)
            if has_acl is None:
                return None
            if has_acl:
                return "non-recoverable parent directory metadata ACL"
        parent_xattrs = _nofollow_xattr_names(parent)
        if parent_xattrs is None:
            return None
        if any(name not in allowed_xattrs for name in parent_xattrs):
            return "non-recoverable parent directory metadata xattrs"
        try:
            after_parent = os.lstat(parent)
        except OSError:
            return None
        if ((before_parent.st_dev, before_parent.st_ino,
             before_parent.st_mode, before_parent.st_uid,
             before_parent.st_gid, getattr(before_parent, "st_flags", 0))
                != (after_parent.st_dev, after_parent.st_ino,
                    after_parent.st_mode, after_parent.st_uid,
                    after_parent.st_gid,
                    getattr(after_parent, "st_flags", 0))):
            return None
        checked_parents.add(parent)
        return ""

    # Check every candidate target parent before inspecting current leaf
    # inodes.  Stop at the first missing component: Git will create that suffix
    # with the already-proved process umask beneath the nearest safe existing
    # ancestor.  A file/symlink component is rejected by _parent_issue.
    for relative in candidate_paths:
        if not _deadline_timeout(deadline, DEFAULT_TIMEOUT, reserve=reserve):
            return None
        components = relative.split("/")
        if (not components or any(
                not component or component in {".", ".."}
                for component in components)):
            return None
        cursor = root
        issue = _parent_issue(cursor)
        if issue is None or issue:
            return issue
        for component in components[:-1]:
            cursor = cursor / component
            try:
                os.lstat(cursor)
            except FileNotFoundError:
                break
            except OSError:
                return None
            issue = _parent_issue(cursor)
            if issue is None or issue:
                return issue

    for relative, git_mode in entries:
        if not _deadline_timeout(deadline, DEFAULT_TIMEOUT, reserve=reserve):
            return None
        path = root / relative
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            return None
        is_link = stat.S_ISLNK(before.st_mode)
        if git_mode == "120000":
            type_ok = is_link
        elif git_mode in {"100644", "100755"}:
            expected_mode = 0o644 if git_mode == "100644" else 0o755
            type_ok = (stat.S_ISREG(before.st_mode) and not is_link
                       and stat.S_IMODE(before.st_mode) == expected_mode)
            recreated_mode = ((0o666 if git_mode == "100644" else 0o777)
                              & ~checkout_umask)
            if recreated_mode != expected_mode:
                return "non-recoverable tracked path checkout umask"
        else:
            return f"unsupported tracked entry metadata ({git_mode})"
        if not type_ok:
            return "non-recoverable tracked path type or mode"
        if (before.st_uid != os.getuid()
                or before.st_gid != os.getgid()):
            return "non-recoverable tracked path ownership"
        if before.st_nlink != 1:
            return "non-recoverable tracked path hard links"
        if sys.platform == "darwin":
            if not hasattr(before, "st_flags"):
                return None
            if before.st_flags != 0:
                return "non-recoverable tracked path flags"
            if acl_libc is None:
                acl_libc = _darwin_acl_libc()
                if acl_libc is None:
                    return None
            has_acl = _darwin_has_extended_acl(
                path, symlink=is_link, libc=acl_libc)
            if has_acl is None:
                return None
            if has_acl:
                return "non-recoverable tracked path ACL"
        xattr_names = _nofollow_xattr_names(path)
        if xattr_names is None:
            return None
        if any(name not in allowed_xattrs for name in xattr_names):
            return "non-recoverable tracked path xattrs"
        try:
            after = os.lstat(path)
        except OSError:
            return None
        before_proof = (
            before.st_dev, before.st_ino, before.st_mode, before.st_uid,
            before.st_gid, before.st_nlink,
            getattr(before, "st_flags", 0))
        after_proof = (
            after.st_dev, after.st_ino, after.st_mode, after.st_uid,
            after.st_gid, after.st_nlink,
            getattr(after, "st_flags", 0))
        if after_proof != before_proof:
            return None
    return ""


def _bound_worktree_transform_attrs(
        team_root: str, txn: _BoundIndexTxn, deadline: float,
        *, local_source: str, upstream_source: str,
        reserve: int = 0) -> str | None:
    """Reject tracked paths whose raw bytes are hidden by Git transforms.

    External clean filters, working-tree encodings, and ``ident`` expansion can
    map distinct filesystem bytes to one Git blob.  In that case diff/stash is
    not an exact-byte recovery proof, so defer before the first reset.  The
    path stream and attribute result are NUL-delimited to preserve arbitrary
    valid Git path bytes.
    """
    path_commands = (
        ["ls-files", "-z", "--"],
        ["ls-tree", "-r", "--name-only", "-z", local_source],
        ["ls-tree", "-r", "--name-only", "-z", upstream_source],
    )
    paths: list[str] = []
    seen_paths: set[str] = set()
    for command in path_commands:
        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT, reserve=reserve)
        if not probe_timeout:
            return None
        try:
            rc, path_output, _ = _run_bound_git(
                team_root, txn, list(command), probe_timeout, proof_raw=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0 or (path_output and not path_output.endswith("\0")):
            return None
        for path in (path_output or "").split("\0"):
            if path and path not in seen_paths:
                seen_paths.add(path)
                paths.append(path)
    if not paths:
        return ""
    path_input = "\0".join(paths) + "\0"

    def _remove_candidate_index(
            candidate: Path, expected_identity: tuple[int, int]) -> bool:
        """Unlink only the exact temporary candidate index we observed."""
        try:
            tx_dir_stat = os.lstat(txn.tx_dir)
            if (candidate.parent != txn.tx_dir
                    or not stat.S_ISDIR(tx_dir_stat.st_mode)
                    or stat.S_ISLNK(tx_dir_stat.st_mode)
                    or (tx_dir_stat.st_dev, tx_dir_stat.st_ino)
                    != txn.tx_dir_identity
                    or (hasattr(os, "getuid")
                        and tx_dir_stat.st_uid != os.getuid())):
                return False
            # A surviving Git lock is an unobserved recovery artifact.  Never
            # guess that it belongs to this call or delete it by filename.
            try:
                os.lstat(Path(f"{candidate}.lock"))
            except FileNotFoundError:
                pass
            else:
                return False
            try:
                current = os.lstat(candidate)
            except FileNotFoundError:
                return not expected_identity
            if (not expected_identity
                    or (current.st_dev, current.st_ino) != expected_identity
                    or not stat.S_ISREG(current.st_mode)
                    or stat.S_ISLNK(current.st_mode)
                    or (hasattr(os, "getuid")
                        and current.st_uid != os.getuid())):
                return False
            os.unlink(candidate)
            return _fsync_parent_dir(str(candidate))
        except OSError:
            return False

    def _check_candidate_tree_attrs(source: str, label: str) -> str | None:
        """Old-Git fallback: materialize one exact tree in a private index."""
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
        candidate = txn.tx_dir / f"attr-index-{label}-{nonce}"
        candidate_lock = Path(f"{candidate}.lock")
        candidate_identity: tuple[int, int] = ()
        attrs = ""
        proof_ok = False
        cleanup_ok = False
        try:
            # Both paths must be absent before Git receives the unique name.
            for path in (candidate, candidate_lock):
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                return None
            probe_timeout = _deadline_timeout(
                deadline, DEFAULT_TIMEOUT, reserve=reserve)
            if not probe_timeout:
                return None
            rc, _, _ = run_git(
                ["-C", team_root, "read-tree", source],
                timeout=probe_timeout,
                env_overrides={"GIT_INDEX_FILE": str(candidate)},
                output_errors="surrogateescape")
            try:
                candidate_stat = os.lstat(candidate)
            except FileNotFoundError:
                return None
            if (not stat.S_ISREG(candidate_stat.st_mode)
                    or stat.S_ISLNK(candidate_stat.st_mode)
                    or candidate.parent != txn.tx_dir
                    or (hasattr(os, "getuid")
                        and candidate_stat.st_uid != os.getuid())):
                return None
            candidate_identity = (
                candidate_stat.st_dev, candidate_stat.st_ino)
            if rc != 0:
                return None
            probe_timeout = _deadline_timeout(
                deadline, DEFAULT_TIMEOUT, reserve=reserve)
            if not probe_timeout:
                return None
            rc, attrs, _ = run_git(
                ["-C", team_root, "check-attr", "--cached", "-z",
                 "filter", "working-tree-encoding", "ident", "--stdin"],
                timeout=probe_timeout,
                env_overrides={"GIT_INDEX_FILE": str(candidate)},
                output_errors="surrogateescape", input_text=path_input)
            if rc != 0:
                return None
            # check-attr is read-only; an inode replacement means the proof no
            # longer belongs to the exact read-tree result we captured.
            after = os.lstat(candidate)
            if ((after.st_dev, after.st_ino) != candidate_identity
                    or not stat.S_ISREG(after.st_mode)
                    or stat.S_ISLNK(after.st_mode)
                    or (hasattr(os, "getuid")
                        and after.st_uid != os.getuid())):
                return None
            proof_ok = True
        except (OSError, subprocess.SubprocessError):
            proof_ok = False
        finally:
            cleanup_ok = _remove_candidate_index(
                candidate, candidate_identity)
            if not cleanup_ok:
                # Even a post-unlink directory-fsync failure makes cleanup
                # uncertain.  Materialize a durable blocker so the outer
                # transaction cannot silently erase the evidence.
                _ensure_bound_filesystem_anchor(txn)
        return attrs if proof_ok and cleanup_ok else None

    def _transform_value_present(attrs: str) -> bool | None:
        if attrs and not attrs.endswith("\0"):
            return None
        fields = (attrs[:-1].split("\0") if attrs else [])
        if len(fields) % 3:
            return None
        for _path, attribute, value in zip(
                fields[0::3], fields[1::3], fields[2::3]):
            if attribute not in {"filter", "working-tree-encoding", "ident"}:
                return None
            if value not in {"unspecified", "unset"}:
                return True
        return False

    sources = (
        ("current", None),
        ("local", local_source),
        ("upstream", upstream_source),
    )
    for label, source in sources:
        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT, reserve=reserve)
        if not probe_timeout:
            return None
        args = ["check-attr", "-z"]
        if source is not None:
            args.append(f"--source={source}")
        args += ["filter", "working-tree-encoding", "ident", "--stdin"]
        try:
            rc, attrs, _ = _run_bound_git(
                team_root, txn, args, probe_timeout,
                proof_raw=True, input_text=path_input)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0:
            if source is None:
                return None
            attrs = _check_candidate_tree_attrs(source, label)
            if attrs is None:
                return None
        transform_present = _transform_value_present(attrs)
        if transform_present is None:
            return None
        if transform_present:
            return label
    return ""


def _bound_untracked_tree_collision(
        team_root: str, txn: _BoundIndexTxn, local_ref: str,
        upstream_ref: str, deadline: float, reserve: int = 0) -> bool | None:
    """Detect untracked bytes that reset/merge could replace or remove.

    ``stash create`` does not capture untracked files.  In particular, a staged
    deletion can make ``tracked-file/secret`` appear untracked even though the
    reset target still contains ``tracked-file`` as a blob.  ``reset --hard``
    removes that directory recursively, so any exact or file/directory-prefix
    collision must defer before the first mutation.
    """
    commands = (
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--"],
        ["ls-tree", "-r", "--name-only", "-z", local_ref],
        ["ls-tree", "-r", "--name-only", "-z", upstream_ref],
    )
    outputs: list[str] = []
    for args in commands:
        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT, reserve=reserve)
        if not probe_timeout:
            return None
        try:
            rc, out, _ = _run_bound_git(
                team_root, txn, list(args), probe_timeout, proof_raw=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0 or (out and not out.endswith("\0")):
            return None
        outputs.append(out or "")

    probe_timeout = _deadline_timeout(
        deadline, DEFAULT_TIMEOUT, reserve=reserve)
    if not probe_timeout:
        return None
    try:
        rc, ignorecase_out, _ = _run_bound_git(
            team_root, txn,
            ["config", "--bool", "--get", "core.ignorecase"], probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc == 0:
        raw_ignorecase = (ignorecase_out or "").strip().lower()
        if raw_ignorecase not in {"true", "false"}:
            return None
        ignorecase = raw_ignorecase == "true"
    elif rc == 1:
        ignorecase = False
    else:
        return None

    def _filesystem_key(path: str) -> str:
        normalized = "/".join(
            unicodedata.normalize("NFC", part) for part in path.split("/"))
        return normalized.casefold() if ignorecase else normalized

    untracked = {
        _filesystem_key(path)
        for output in outputs[:2]
        for path in output.split("\0") if path
    }
    checkout_paths = {
        _filesystem_key(path)
        for output in outputs[2:]
        for path in output.split("\0") if path
    }
    for local_path in untracked:
        local_prefix = local_path.rstrip("/") + "/"
        for checkout_path in checkout_paths:
            checkout_prefix = checkout_path.rstrip("/") + "/"
            if (local_path == checkout_path
                    or local_path.startswith(checkout_prefix)
                    or checkout_path.startswith(local_prefix)):
                return True
    return False


def _bound_ignored_upstream_collision(
        team_root: str, txn: _BoundIndexTxn, local_ref: str,
        upstream_ref: str, deadline: float, reserve: int = 0) -> bool | None:
    """Detect ignored local paths that an explicit upstream delta can replace."""
    commands = (
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z",
         "--"],
        ["diff", "--name-only", "--no-renames", "-z",
         f"{local_ref}...{upstream_ref}", "--"],
    )
    outputs: list[str] = []
    for args in commands:
        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT, reserve=reserve)
        if not probe_timeout:
            return None
        try:
            rc, out, _ = _run_bound_git(
                team_root, txn, list(args), probe_timeout, proof_raw=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0:
            return None
        outputs.append(out or "")
    probe_timeout = _deadline_timeout(
        deadline, DEFAULT_TIMEOUT, reserve=reserve)
    if not probe_timeout:
        return None
    try:
        rc, ignorecase_out, _ = _run_bound_git(
            team_root, txn,
            ["config", "--bool", "--get", "core.ignorecase"], probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc == 0:
        raw_ignorecase = (ignorecase_out or "").strip().lower()
        if raw_ignorecase not in {"true", "false"}:
            return None
        ignorecase = raw_ignorecase == "true"
    elif rc == 1:
        ignorecase = False
    else:
        return None

    def _filesystem_key(path: str) -> str:
        normalized = "/".join(
            unicodedata.normalize("NFC", part) for part in path.split("/"))
        return normalized.casefold() if ignorecase else normalized

    ignored = [
        _filesystem_key(path) for path in outputs[0].split("\0") if path]
    upstream = [
        _filesystem_key(path) for path in outputs[1].split("\0") if path]
    for local_path in ignored:
        local_prefix = local_path.rstrip("/") + "/"
        for upstream_path in upstream:
            upstream_prefix = upstream_path.rstrip("/") + "/"
            if (local_path == upstream_path
                    or local_path.startswith(upstream_prefix)
                    or upstream_path.startswith(local_prefix)):
                return True
    return False


def _bound_identity_probe(
        team_root: str, txn: _BoundIndexTxn, branch: str,
        deadline: float, reserve: int = 0,
        cap: int = DEFAULT_TIMEOUT) -> dict[str, str] | None:
    probe_timeout = _deadline_timeout(deadline, cap, reserve=reserve)
    if not probe_timeout:
        return None
    try:
        rc, out, _ = _run_bound_git(
            team_root, txn,
            ["symbolic-ref", "--quiet", "--short", "HEAD"], probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0 or (out or "").strip() != branch:
        return None
    probe_timeout = _deadline_timeout(deadline, cap, reserve=reserve)
    if not probe_timeout:
        return None
    try:
        rc, out, _ = _run_bound_git(
            team_root, txn,
            ["rev-parse", "--verify", f"refs/heads/{branch}"], probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    head = (out or "").strip().lower() if rc == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        return None
    return {"key": f"branch:{branch}", "branch": branch, "head": head}


def _regular_digest(path: Path) -> str:
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise OSError(errno.EPERM, "unsafe transaction index", str(path))
    fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise OSError(errno.EBUSY, "transaction index changed during open")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    finally:
        if fd >= 0:
            os.close(fd)


def _probe_bound_ref_oid(
        team_root: str, txn: _BoundIndexTxn, ref: str,
        deadline: float, reserve: int = 0) -> tuple[bool, str]:
    """Tri-state ref probe: unavailable, available-missing, or available-OID."""
    probe_timeout = _deadline_timeout(
        deadline, _BOUND_ROLLBACK_STEP_TIMEOUT, reserve=reserve)
    if not probe_timeout:
        return False, ""
    try:
        rc, out, _ = _run_bound_git(
            team_root, txn,
            ["rev-parse", "--verify", "--quiet", ref], probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if rc == 0 and (out or "").strip():
        return True, (out or "").strip().lower()
    if rc == 1:
        return True, ""
    return False, ""


def _create_bound_recovery_ref(
        team_root: str, txn: _BoundIndexTxn, ref: str, expected_oid: str,
        deadline: float, reserve: int) -> tuple[bool, bool, bool, str]:
    """Create a recovery ref and resolve timeout ambiguity with an exact probe.

    Returns `(completed, created_by_us, cleanup_safe, detail)`.  A timed-out
    update is never retried: exact expected OID is CAS-cleanable, missing is safe,
    while an unavailable or different OID retains all evidence.
    """
    probe_timeout = _deadline_timeout(
        deadline, DEFAULT_TIMEOUT, reserve=reserve)
    if not probe_timeout:
        return False, False, True, "deadline exhausted before recovery ref"
    failure = ""
    try:
        rc, _, err = _run_bound_git(
            team_root, txn,
            ["update-ref", ref, expected_oid, ""], probe_timeout)
        if rc == 0:
            return True, True, True, ""
        failure = (err or "recovery ref update failed").strip()[:200]
    except subprocess.TimeoutExpired:
        failure = "recovery ref update timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        failure = f"recovery ref update exec error: {exc}"

    available, actual_oid = _probe_bound_ref_oid(
        team_root, txn, ref, deadline, reserve=reserve)
    if not available:
        return False, False, False, f"{failure}; ref state unavailable"
    if actual_oid == expected_oid.lower():
        return False, True, True, f"{failure}; exact ref creation observed"
    if not actual_oid:
        return False, False, True, failure
    return (False, False, False,
            f"{failure}; unexpected recovery ref OID {actual_oid}")


def _cleanup_bound_refs(
        team_root: str, txn: _BoundIndexTxn, deadline: float,
        *, head_created: bool, stash_created: bool) -> bool:
    refs = []
    if stash_created:
        refs.append((txn.stash_ref, txn.stash_oid))
    if head_created:
        refs.append((txn.head_ref, txn.original_head))
    deleted: list[tuple[str, str]] = []
    for ref, expected in refs:
        probe_timeout = _deadline_timeout(
            deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
        if not probe_timeout:
            return False
        try:
            rc, _, _ = _run_bound_git(
                team_root, txn,
                ["update-ref", "-d", ref, expected], probe_timeout)
        except (OSError, subprocess.SubprocessError):
            rc = 1
        if rc != 0:
            _restore_bound_refs(team_root, txn, deadline, deleted)
            return False
        deleted.append((ref, expected))
    return True


def _restore_bound_refs(
        team_root: str, txn: _BoundIndexTxn, deadline: float,
        refs: list[tuple[str, str]]) -> bool:
    """Best-effort create-only restoration after a partial cleanup failure."""
    ok = True
    for ref, expected in refs:
        probe_timeout = _deadline_timeout(
            deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
        if not probe_timeout:
            return False
        try:
            rc, _, _ = _run_bound_git(
                team_root, txn,
                ["update-ref", ref, expected, ""], probe_timeout)
        except (OSError, subprocess.SubprocessError):
            rc = 1
        if rc != 0:
            available, actual = _probe_bound_ref_oid(
                team_root, txn, ref, deadline)
            ok = ok and available and actual == expected.lower()
    return ok


def _bound_rebase_dirs_clear(txn: _BoundIndexTxn) -> bool:
    return (not (txn.index_path.parent / "rebase-merge").exists()
            and not (txn.index_path.parent / "rebase-apply").exists())


def _bound_merge_state_clear(txn: _BoundIndexTxn) -> bool:
    """Prove a failed/successful bound merge left no sequencer state."""
    admin = txn.index_path.parent
    return all(not (admin / name).exists() for name in (
        "MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "MERGE_AUTOSTASH",
        "AUTO_MERGE"))


def _bound_operation_state_clear(txn: _BoundIndexTxn) -> bool:
    return _bound_rebase_dirs_clear(txn) and _bound_merge_state_clear(txn)


def _clear_bound_auto_merge(
        team_root: str, txn: _BoundIndexTxn, deadline: float) -> bool:
    """Delete Git's operation-created AUTO_MERGE pseudo-ref.

    ``publication_blocker_detail`` proves it was absent before the transaction.
    Git may leave it after a successful/aborted rebase on newer versions, so the
    bound transaction owns cleanup and proves its removal before returning.
    """
    probe_timeout = _deadline_timeout(
        deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
    if not probe_timeout:
        return False
    try:
        rc, _, _ = _run_bound_git(
            team_root, txn, ["update-ref", "-d", "AUTO_MERGE"], probe_timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0 and not (txn.index_path.parent / "AUTO_MERGE").exists()


def _promote_bound_index(txn: _BoundIndexTxn) -> tuple[bool, str]:
    """Promote the private index only while our canonical lock is still owned."""
    try:
        if not _bound_lock_owned(txn):
            return False, "canonical index lock ownership changed"
        if (_regular_digest(txn.index_path)
                != _regular_digest(txn.original_index)):
            return False, "canonical index changed outside its lock"
        if txn.index_metadata is None:
            return False, "canonical index metadata snapshot unavailable"
        if not _index_metadata_matches(txn.index_path, txn.index_metadata):
            return False, "canonical index metadata changed outside its lock"
        # Git legitimately replaced work-index via its private lock.  Reapply the
        # canonical metadata to that final inode and verify before promotion.
        _apply_index_metadata(txn.work_index, txn.index_metadata)
        refreshed = os.lstat(txn.work_index)
        txn.work_index_identity = (refreshed.st_dev, refreshed.st_ino)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(txn.work_index, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                return False, "private index is not regular"
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(txn.work_index, txn.index_path)
        txn.promoted = True
        failures = []
        # os.replace is the transaction commit point.  A diagnostic race/failure
        # after it cannot re-enter rollback, whose core invariant is that the
        # canonical index was never promoted.
        if not _index_metadata_matches(txn.index_path, txn.index_metadata):
            failures.append("post-promotion index metadata check failed")
        durable = _fsync_parent_dir(str(txn.index_path))
        if not durable:
            failures.append("post-promotion index parent fsync failed")
        if failures:
            return False, "promotion durability failed: " + "; ".join(failures)
        return True, ""
    except OSError as exc:
        return False, f"private index promotion failed: {exc}"


def _rollback_bound_reconcile(
        team_root: str, txn: _BoundIndexTxn, identity: dict[str, str],
        original_state: _BoundUserState, deadline: float,
        *, mode: str) -> tuple[bool, str, dict[str, str] | None]:
    """Restore through the private index, then prove every rollback invariant."""
    details: list[str] = []
    if mode == "rebase":
        abort_command = ["rebase", "--abort"]
        operation_clear = _bound_rebase_dirs_clear(txn)
    elif mode == "merge":
        abort_command = ["merge", "--abort"]
        operation_clear = _bound_merge_state_clear(txn)
    else:
        abort_command = []
        operation_clear = True
    abort_needed = bool(abort_command) and not operation_clear
    abort_ok = not abort_needed
    if abort_needed:
        probe_timeout = _deadline_timeout(
            deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
        if probe_timeout:
            try:
                rc, _, err = _run_bound_git(
                    team_root, txn, abort_command, probe_timeout)
                abort_ok = rc == 0
                if rc != 0:
                    details.append(
                        (err or f"{mode} abort failed").strip()[:200])
            except (OSError, subprocess.SubprocessError) as exc:
                details.append(f"{mode} abort exec error: {exc}")
        else:
            details.append(f"deadline exhausted before {mode} abort")

    branch_now = _bound_identity_probe(
        team_root, txn, identity["branch"], deadline,
        cap=_BOUND_ROLLBACK_STEP_TIMEOUT)
    reset_ok = branch_now is not None
    if reset_ok:
        probe_timeout = _deadline_timeout(
            deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
        if not probe_timeout:
            reset_ok = False
        else:
            try:
                rc, _, err = _run_bound_git(
                    team_root, txn,
                    ["reset", "--hard", txn.original_head], probe_timeout)
                reset_ok = rc == 0
                if rc != 0:
                    details.append((err or "rollback reset failed").strip()[:200])
            except (OSError, subprocess.SubprocessError) as exc:
                reset_ok = False
                details.append(f"rollback reset exec error: {exc}")
    else:
        details.append("captured branch unavailable during rollback")

    apply_ok = reset_ok
    if apply_ok and txn.stash_oid:
        probe_timeout = _deadline_timeout(
            deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
        if not probe_timeout:
            apply_ok = False
        else:
            try:
                rc, _, err = _run_bound_git(
                    team_root, txn,
                    ["stash", "apply", "--index", txn.stash_oid], probe_timeout)
                apply_ok = rc == 0
                if rc != 0:
                    details.append((err or "rollback stash apply failed").strip()[:200])
            except (OSError, subprocess.SubprocessError) as exc:
                apply_ok = False
                details.append(f"rollback stash apply exec error: {exc}")

    auto_merge_ok = _clear_bound_auto_merge(team_root, txn, deadline)
    if not auto_merge_ok:
        details.append("AUTO_MERGE cleanup failed")

    restored_identity = _bound_identity_probe(
        team_root, txn, identity["branch"], deadline,
        cap=_BOUND_ROLLBACK_STEP_TIMEOUT)
    restored_state = _capture_bound_user_state(
        team_root, txn, deadline, cap=_BOUND_ROLLBACK_STEP_TIMEOUT)
    unmerged_ok = False
    probe_timeout = _deadline_timeout(
        deadline, _BOUND_ROLLBACK_STEP_TIMEOUT)
    if probe_timeout:
        try:
            rc, out, _ = _run_bound_git(
                team_root, txn,
                ["diff", "--name-only", "--diff-filter=U", "-z", "--"],
                probe_timeout)
            unmerged_ok = rc == 0 and not (out or "")
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        canonical_ok = (_regular_digest(txn.index_path)
                        == _regular_digest(txn.original_index))
    except OSError:
        canonical_ok = False
    state_ok = restored_state == original_state
    identity_ok = (restored_identity is not None
                   and restored_identity["head"] == txn.original_head.lower())
    dirs_ok = _bound_operation_state_clear(txn)
    lock_ok = _bound_lock_owned(txn)
    proven = all((abort_ok, reset_ok, apply_ok, auto_merge_ok, state_ok, identity_ok,
                  unmerged_ok, dirs_ok, canonical_ok, lock_ok))
    if not state_ok:
        details.append("user index/worktree state not restored")
    if not identity_ok:
        details.append("captured branch/OID not restored")
    if not unmerged_ok or not dirs_ok:
        details.append("reconcile/unmerged state not cleared")
    if not canonical_ok:
        details.append("canonical index changed during rollback")
    return proven, "; ".join(dict.fromkeys(details)), restored_identity


def _bound_reconcile_transaction_locked(
        team_root: str, identity: dict[str, str], upstream_ref: str,
        upstream_oid: str, local_ref: str, *, mode: str, ahead: int, behind: int,
        timeout: int, deadline: float) -> ReconcileResult:
    """Mutate one captured branch under the canonical lock/private-index pair."""
    is_diverged = mode in {"rebase", "merge"}
    failure_action = "conflict" if is_diverged else "error"
    start_timeout = _deadline_timeout(deadline, DEFAULT_TIMEOUT)
    if not start_timeout:
        return ReconcileResult(
            ok=False, action="error", ahead=ahead, behind=behind,
            diverged=is_diverged,
            detail="reconcile budget exhausted before index transaction")
    txn, begin_detail = _begin_bound_index_tx(
        team_root, identity, start_timeout)
    if txn is None:
        return ReconcileResult(
            ok=False, action=failure_action,
            ahead=ahead, behind=behind, diverged=is_diverged,
            detail=begin_detail)

    head_created = stash_created = False
    recovery_cleanup_safe = True
    lock_finalized = False

    def _created_ref_pairs() -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if stash_created:
            pairs.append((txn.stash_ref, txn.stash_oid))
        if head_created:
            pairs.append((txn.head_ref, txn.original_head))
        return pairs

    def _clean_transaction() -> bool:
        if not recovery_cleanup_safe:
            return False
        # Keep recovery refs live until the filesystem anchor has been removed
        # and its parent directory fsynced.  A post-rmdir fsync failure can no
        # longer leave the repository with neither kind of recovery evidence.
        if not _remove_bound_tx_dir(txn):
            return False
        refs_ok = _cleanup_bound_refs(
            team_root, txn, deadline,
            head_created=head_created, stash_created=stash_created)
        if refs_ok:
            return True
        # Cleanup may have deleted a prefix of the refs before a later CAS or
        # probe failed.  Observe the restoration result and also recreate a
        # durable filesystem blocker so failure never depends on that result.
        _restore_bound_refs(
            team_root, txn, deadline, _created_ref_pairs())
        _ensure_bound_filesystem_anchor(txn)
        return False

    def _evidence_detail() -> str:
        anchors: list[str] = []
        for ref, _expected in _created_ref_pairs():
            available, actual = _probe_bound_ref_oid(
                team_root, txn, ref, deadline)
            if available and actual:
                anchors.append(f"{ref}@{actual[:12]}")
        try:
            tx_stat = os.lstat(txn.tx_dir)
        except OSError:
            tx_stat = None
        if (tx_stat is not None and stat.S_ISDIR(tx_stat.st_mode)
                and not stat.S_ISLNK(tx_stat.st_mode)):
            anchors.append(str(txn.tx_dir))
        if not anchors:
            return "recovery evidence state unavailable"
        return "recovery evidence retained at " + " ".join(anchors)

    def _release_then_maybe_clean(
            detail: str, *, clean: bool) -> tuple[str, bool]:
        """Finalize lock before any destructive evidence cleanup."""
        nonlocal lock_finalized
        release_ok, release_detail = _release_bound_lock(txn)
        lock_finalized = True
        if not release_ok:
            return (f"{detail}; lock release failed: {release_detail}; "
                    f"{_evidence_detail()}"), False
        if clean and not _clean_transaction():
            return f"{detail}; recovery cleanup failed; {_evidence_detail()}", False
        return detail, True

    def _pre_mutation_failure(detail: str) -> ReconcileResult:
        detail, _ = _release_then_maybe_clean(detail, clean=True)
        return ReconcileResult(
            ok=False, action=failure_action,
            ahead=ahead, behind=behind, diverged=is_diverged,
            detail=detail)

    def _mutation_failure(detail: str) -> ReconcileResult:
        proven, rollback_detail, observed_identity = _rollback_bound_reconcile(
            team_root, txn, identity, original_state, deadline,
            mode=mode)
        if proven:
            suffix = {
                "rebase": "rebase failed (aborted)",
                "merge": "merge failed (aborted)",
            }.get(mode, "ff failed (rolled back)")
            message = f"{suffix}: {detail}"
            if rollback_detail:
                message += f"; {rollback_detail}"
            message, _ = _release_then_maybe_clean(message, clean=True)
        else:
            message = f"{detail}; abort attempted; rollback not proven"
            if rollback_detail:
                message += f"; {rollback_detail}"
            message += (f"; recovery refs {txn.head_ref} {txn.stash_ref}"
                        f"; transaction {txn.tx_dir}")
            message, _ = _release_then_maybe_clean(message, clean=False)
        return ReconcileResult(
            ok=False, action=failure_action,
            ahead=ahead, behind=behind, diverged=is_diverged,
            detail=message, final_identity=observed_identity)

    try:
        # Capture semantic user state against the private copy.  Every preparation
        # probe leaves enough of the same deadline for rollback before mutation.
        original_state = _capture_bound_user_state(
            team_root, txn, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if original_state is None:
            return _pre_mutation_failure(
                "bound reconcile user-state snapshot unavailable")

        hidden_flags = _bound_hidden_index_flags(
            team_root, txn, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if hidden_flags is None:
            return _pre_mutation_failure(
                "bound reconcile hidden index flags unavailable")
        if hidden_flags:
            return _pre_mutation_failure(
                "bound reconcile deferred: hidden index flags present")

        transform_source = _bound_worktree_transform_attrs(
            team_root, txn, deadline,
            local_source=txn.original_head, upstream_source=upstream_oid,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if transform_source is None:
            return _pre_mutation_failure(
                "bound reconcile working-tree transform proof unavailable")
        if transform_source:
            return _pre_mutation_failure(
                f"bound reconcile deferred: {transform_source} working-tree "
                "transform attributes present")

        ignored_collision = _bound_ignored_upstream_collision(
            team_root, txn, txn.original_head, upstream_oid, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if ignored_collision is None:
            return _pre_mutation_failure(
                "bound reconcile ignored path collision proof unavailable")
        if ignored_collision:
            return _pre_mutation_failure(
                "bound reconcile deferred: ignored path collision")

        untracked_collision = _bound_untracked_tree_collision(
            team_root, txn, txn.original_head, upstream_oid, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if untracked_collision is None:
            return _pre_mutation_failure(
                "bound reconcile untracked path collision proof unavailable")
        if untracked_collision:
            return _pre_mutation_failure(
                "bound reconcile deferred: untracked path collision")

        metadata_issue = _bound_worktree_metadata_issue(
            team_root, txn, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE,
            local_source=txn.original_head,
            upstream_source=upstream_oid)
        if metadata_issue is None:
            return _pre_mutation_failure(
                "bound reconcile tracked metadata proof unavailable")
        if metadata_issue:
            return _pre_mutation_failure(
                f"bound reconcile deferred: {metadata_issue}")

        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if not probe_timeout:
            return _pre_mutation_failure(
                "reconcile budget exhausted before recovery snapshot")
        try:
            rc, stash_out, stash_err = _run_bound_git(
                team_root, txn,
                ["stash", "create", "tm-mode bound reconcile"], probe_timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return _pre_mutation_failure(
                f"recovery snapshot exec error: {exc}")
        if rc != 0:
            return _pre_mutation_failure(
                f"recovery snapshot failed: {(stash_err or '').strip()[:200]}")
        txn.stash_oid = (stash_out or "").strip().lower()
        if (txn.stash_oid
                and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", txn.stash_oid)):
            return _pre_mutation_failure("recovery snapshot OID is invalid")

        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if not probe_timeout:
            return _pre_mutation_failure(
                "reconcile budget exhausted before recovery refs")
        (ref_completed, head_created, ref_cleanup_safe,
         ref_detail) = _create_bound_recovery_ref(
            team_root, txn, txn.head_ref, txn.original_head, deadline,
            _BOUND_RECONCILE_RECOVERY_RESERVE)
        recovery_cleanup_safe = recovery_cleanup_safe and ref_cleanup_safe
        if not ref_completed:
            return _pre_mutation_failure(
                f"recovery head ref failed: {ref_detail}")
        if txn.stash_oid:
            probe_timeout = _deadline_timeout(
                deadline, DEFAULT_TIMEOUT,
                reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
            if not probe_timeout:
                return _pre_mutation_failure(
                    "reconcile budget exhausted before stash recovery ref")
            (ref_completed, stash_created, ref_cleanup_safe,
             ref_detail) = _create_bound_recovery_ref(
                team_root, txn, txn.stash_ref, txn.stash_oid, deadline,
                _BOUND_RECONCILE_RECOVERY_RESERVE)
            recovery_cleanup_safe = recovery_cleanup_safe and ref_cleanup_safe
            if not ref_completed:
                return _pre_mutation_failure(
                    f"recovery stash ref failed: {ref_detail}")

        # Last pre-mutation gate, under the real index lock: exact symbolic branch,
        # exact ref OID, unchanged canonical index, and owned lock inode.
        current = _bound_identity_probe(
            team_root, txn, identity["branch"], deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        try:
            canonical_unchanged = (
                _regular_digest(txn.index_path)
                == _regular_digest(txn.original_index))
        except OSError:
            canonical_unchanged = False
        if (current != identity or not canonical_unchanged
                or not _bound_lock_owned(txn)):
            return _pre_mutation_failure(
                "checkout or canonical index changed before mutation")

        latest_state = _capture_bound_user_state(
            team_root, txn, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if latest_state is None:
            return _pre_mutation_failure(
                "bound reconcile final user-state proof unavailable")
        if latest_state != original_state:
            return _pre_mutation_failure(
                "bound reconcile deferred: user state changed before mutation")

        mutation_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if not mutation_timeout:
            return _pre_mutation_failure(
                "reconcile budget exhausted before reset mutation")
        try:
            rc, _, err = _run_bound_git(
                team_root, txn,
                ["reset", "--hard", txn.original_head], mutation_timeout)
        except subprocess.TimeoutExpired:
            return _mutation_failure("pre-reconcile reset timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return _mutation_failure(f"pre-reconcile reset exec error: {exc}")
        if rc != 0:
            return _mutation_failure(
                f"pre-reconcile reset failed: {(err or '').strip()[:200]}")

        operation_timeout = _deadline_timeout(
            deadline, timeout,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        if not operation_timeout:
            return _mutation_failure(
                "reconcile budget exhausted before branch mutation")
        if mode == "fast-forward":
            operation_args = [
                "merge", "--no-overwrite-ignore", "--ff-only", upstream_oid]
        elif mode == "rebase":
            operation_args = [
                "-c", "rebase.autoStash=false",
                "-c", "rebase.updateRefs=false",
                "rebase", "--no-autostash", upstream_oid,
            ]
        else:  # pending-preserving divergence recovery
            operation_args = [
                "merge", "--no-overwrite-ignore",
                "--no-ff", "--no-edit", upstream_oid,
            ]
        try:
            rc, operation_out, operation_err = _run_bound_git(
                team_root, txn, operation_args, operation_timeout)
        except subprocess.TimeoutExpired:
            return _mutation_failure(f"{mode} timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return _mutation_failure(f"{mode} exec error: {exc}")
        if rc != 0:
            failure = (operation_err or operation_out or "").strip()[:200]
            return _mutation_failure(f"{mode} failed: {failure}")

        if txn.stash_oid:
            apply_timeout = _deadline_timeout(
                deadline, DEFAULT_TIMEOUT,
                reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
            if not apply_timeout:
                return _mutation_failure(
                    "reconcile budget exhausted before user-state restore")
            try:
                rc, _, apply_err = _run_bound_git(
                    team_root, txn,
                    ["stash", "apply", "--index", txn.stash_oid], apply_timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                return _mutation_failure(f"user-state restore exec error: {exc}")
            if rc != 0:
                return _mutation_failure(
                    f"user-state restore failed: {(apply_err or '').strip()[:200]}")

        if not _clear_bound_auto_merge(team_root, txn, deadline):
            return _mutation_failure("AUTO_MERGE cleanup failed")

        final_before_promote = _bound_identity_probe(
            team_root, txn, identity["branch"], deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        restored_state = _capture_bound_user_state(
            team_root, txn, deadline,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        probe_timeout = _deadline_timeout(
            deadline, DEFAULT_TIMEOUT,
            reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
        unmerged_ok = False
        if probe_timeout:
            try:
                urc, uout, _ = _run_bound_git(
                    team_root, txn,
                    ["diff", "--name-only", "--diff-filter=U", "-z", "--"],
                    probe_timeout)
                unmerged_ok = urc == 0 and not (uout or "")
            except (OSError, subprocess.SubprocessError):
                pass
        state_ok = (restored_state is not None
                    and restored_state.status == original_state.status
                    and restored_state.unstaged_diff == original_state.unstaged_diff
                    and restored_state.staged_diff == original_state.staged_diff)
        if (final_before_promote is None or not state_ok or not unmerged_ok
                or not _bound_operation_state_clear(txn)):
            return _mutation_failure(
                "reconcile postcondition or user-state proof failed")

        promoted, promote_detail = _promote_bound_index(txn)
        if not promoted:
            if txn.promoted:
                detail = promote_detail
                detail, _ = _release_then_maybe_clean(detail, clean=False)
                detail += f"; {_evidence_detail()}"
                return ReconcileResult(
                    ok=False,
                    action=failure_action,
                    ahead=ahead, behind=behind, diverged=is_diverged,
                    detail=detail, final_identity=final_before_promote)
            return _mutation_failure(promote_detail)
        final_identity = _bound_identity_probe(
            team_root, txn, identity["branch"], deadline)
        if final_identity != final_before_promote:
            # The canonical index has crossed its commit point, so a raw ref writer
            # that bypasses both the Git index lock and our publication interlock
            # cannot be rolled back safely.  Never adopt that writer's OID as this
            # transaction's publication identity.  Retain the original recovery
            # refs/txdir so the captured commit remains reachable and every later
            # publication path fails closed until a human repairs the checkout.
            detail, _ = _release_then_maybe_clean(
                "checkout changed after private index promotion", clean=False)
            if "recovery evidence" not in detail:
                detail += f"; {_evidence_detail()}"
            return ReconcileResult(
                ok=False,
                action=failure_action,
                ahead=ahead, behind=behind, diverged=is_diverged,
                detail=detail, final_identity=None)

        release_detail, finalized = _release_then_maybe_clean(
            "bound reconcile completed", clean=False)
        if not finalized:
            return ReconcileResult(
                ok=False,
                action=failure_action,
                ahead=ahead, behind=behind, diverged=is_diverged,
                detail=release_detail,
                final_identity=final_identity)

        cleaned = _clean_transaction()
        if not cleaned:
            return ReconcileResult(
                ok=False,
                action=failure_action,
                ahead=ahead, behind=behind, diverged=is_diverged,
                detail=f"recovery cleanup failed; {_evidence_detail()}",
                final_identity=final_identity)
        final_ahead = ahead
        probe_timeout = _deadline_timeout(deadline, DEFAULT_TIMEOUT)
        if probe_timeout:
            measured, _, available = _ahead_behind_refs(
                team_root, upstream_ref, local_ref, probe_timeout)
            if available:
                final_ahead = measured
        action = {
            "fast-forward": "fast-forward",
            "rebase": "rebased",
            "merge": "merged",
        }[mode]
        detail = {
            "fast-forward": "",
            "rebase": "rebased onto upstream",
            "merge": "merged upstream while preserving pending ancestry",
        }[mode]
        return ReconcileResult(
            ok=True, action=action, ahead=final_ahead, behind=behind,
            diverged=is_diverged, detail=detail,
            final_identity=final_identity)
    finally:
        # Push is reached only after this finally; a foreign/replaced lock inode is
        # never unlinked, while our own real lock is always released on return.
        if not lock_finalized:
            _release_bound_lock(txn)


def _bound_reconcile_transaction(
        team_root: str, identity: dict[str, str], upstream_ref: str,
        upstream_oid: str, local_ref: str, *, mode: str, ahead: int, behind: int,
        timeout: int, deadline: float,
        edit_lease_owner: str | None = None,
        pending_guard: tuple[str, str, dict] | None = None) -> ReconcileResult:
    """Serialize bound mutation against every publication and clear path."""
    is_diverged = mode in {"rebase", "merge"}
    failure_action = "conflict" if is_diverged else "error"
    lock_timeout = _deadline_timeout(
        deadline, 1, reserve=_BOUND_RECONCILE_RECOVERY_RESERVE)
    if not lock_timeout:
        return ReconcileResult(
            ok=False, action=failure_action,
            ahead=ahead, behind=behind, diverged=is_diverged,
            detail="reconcile budget exhausted before publication interlock")
    with _publication_interlock(team_root, lock_timeout) as (acquired, detail):
        if not acquired:
            return ReconcileResult(
                ok=False, action=failure_action,
                ahead=ahead, behind=behind, diverged=is_diverged,
                detail=detail)
        blocker = publication_blocker_detail(team_root, lock_timeout)
        if blocker:
            return ReconcileResult(
                ok=False, action=failure_action,
                ahead=ahead, behind=behind, diverged=is_diverged,
                detail=blocker)
        # The edit gate stays held through local ff/rebase, so a later Pre cannot
        # register and start writing in the mutation window.  Hook-driven
        # mutation requires its exact marker to be the sole editor; an explicit
        # manual/internal mutation requires the marker set to be empty.
        with _edit_gate(team_root, 0.2) as (edit_acquired, edit_detail):
            if not edit_acquired:
                return ReconcileResult(
                    ok=False, action="deferred", ahead=ahead, behind=behind,
                    diverged=is_diverged, detail=edit_detail,
                    final_identity=identity)
            owners = _active_edit_lease_owners_locked(team_root)
            expected_owners = (
                {edit_lease_owner} if edit_lease_owner is not None else set())
            if owners != expected_owners:
                return ReconcileResult(
                    ok=False, action="deferred", ahead=ahead, behind=behind,
                    diverged=is_diverged,
                    detail="worktree edit lease unavailable or shared",
                    final_identity=identity)
            if (pending_guard is not None
                    and not _push_pending_snapshot_is_current(
                        team_root, pending_guard[0], pending_guard[1])):
                return ReconcileResult(
                    ok=False, action="pending-changed", ahead=ahead,
                    behind=behind, diverged=is_diverged,
                    detail="pending ledger changed before reconcile mutation",
                    final_identity=identity)
            result = _bound_reconcile_transaction_locked(
                team_root, identity, upstream_ref, upstream_oid, local_ref,
                mode=mode, ahead=ahead, behind=behind,
                timeout=timeout, deadline=deadline)
            if result.ok and pending_guard is not None:
                snapshot, target_key, target = pending_guard
                if (result.final_identity is None
                        or not _advance_push_pending_if_unchanged(
                            team_root, snapshot, target_key,
                            result.final_identity, target,
                            deadline=deadline)):
                    return ReconcileResult(
                        ok=False, action="pending-update-failed",
                        ahead=result.ahead, behind=result.behind,
                        diverged=result.diverged,
                        detail=("reconciled while preserving pending history, but "
                                "the pending ledger could not be advanced; retry safe"),
                        final_identity=result.final_identity)
            return result


def _finalize_pending_reconcile_without_mutation(
        team_root: str, identity: dict[str, str], *, ahead: int, behind: int,
        action: str, detail: str, deadline: float,
        pending_guard: tuple[str, str, dict],
        edit_lease_owner: str | None = None) -> ReconcileResult:
    """CAS-check/advance a pending entry under the same mutation barriers."""
    lock_timeout = _deadline_timeout(deadline, 1)
    if not lock_timeout:
        return ReconcileResult(
            ok=False, action="error", ahead=ahead, behind=behind,
            detail="reconcile budget exhausted before pending checkpoint",
            final_identity=identity)
    with _publication_interlock(team_root, lock_timeout) as (acquired, lock_detail):
        if not acquired:
            return ReconcileResult(
                ok=False, action="error", ahead=ahead, behind=behind,
                detail=lock_detail, final_identity=identity)
        blocker = publication_blocker_detail(team_root, lock_timeout)
        if blocker:
            return ReconcileResult(
                ok=False, action="error", ahead=ahead, behind=behind,
                detail=blocker, final_identity=identity)
        with _edit_gate(team_root, 0.2) as (edit_acquired, edit_detail):
            if not edit_acquired:
                return ReconcileResult(
                    ok=False, action="deferred", ahead=ahead, behind=behind,
                    detail=edit_detail, final_identity=identity)
            owners = _active_edit_lease_owners_locked(team_root)
            expected = ({edit_lease_owner}
                        if edit_lease_owner is not None else set())
            if owners != expected:
                return ReconcileResult(
                    ok=False, action="deferred", ahead=ahead, behind=behind,
                    detail="worktree edit lease unavailable or shared",
                    final_identity=identity)
            if not _checkout_matches_identity(team_root, identity, lock_timeout):
                return ReconcileResult(
                    ok=False, action="checkout-changed", ahead=ahead,
                    behind=behind,
                    detail="checkout changed before pending checkpoint")
            snapshot, target_key, target = pending_guard
            if not _advance_push_pending_if_unchanged(
                    team_root, snapshot, target_key, identity, target,
                    deadline=deadline):
                return ReconcileResult(
                    ok=False, action="pending-update-failed", ahead=ahead,
                    behind=behind,
                    detail="pending ledger changed or could not be advanced",
                    final_identity=identity)
            return ReconcileResult(
                ok=True, action=action, ahead=ahead, behind=behind,
                detail=detail, final_identity=identity)


def do_reconcile(team_root: str, timeout: int = NET_TIMEOUT,
                 deadline=None, *, expected_identity: dict | None = None,
                 _target: _PublicationTarget | None = None,
                 _allow_bound_mutation: bool = False,
                 _edit_lease_owner: str | None = None,
                 _preserve_pending_ancestry: bool = False,
                 _pending_guard: tuple[str, str, dict] | None = None,
                 ) -> ReconcileResult:
    """fetch 후 추적 upstream 과 **실제 정합**(ff 또는 rebase --autostash). 무raise(철칙).

    do_pull 의 `pull --ff-only` 는 로컬이 diverge(ahead>0 & behind>0)면 조용히 실패해
    멀티유저 환경에서 로컬 커밋만 쌓이게 만든다(이슈 #23). do_reconcile 은 diverge 도
    rebase 로 정합하고, 충돌이면 **abort 후 conflict 로 표면화**(조용히 넘기지 않음).
    호출 빈도와 deadline은 상위가 통제한다. SessionStart는 스로틀하고,
    auto-commit publication은 do_commit의 공유 push 예산 안에서 호출한다.

    분기:
      - 추적 upstream 없음 → no-upstream(정합 불필요, ok=True).
      - behind==0 → up-to-date(ahead 0) 또는 ahead-only(미push 로컬만 있음). ok=True.
      - ahead==0 & behind>0 → fast-forward(`merge --ff-only @{u}`). ok=True.
      - ahead>0 & behind>0(diverge) → `rebase --autostash @{u}`.
          성공 → rebased(남은 ahead 재계산). 충돌/실패 → abort 후 conflict(ok=False).
    """
    own_deadline = time.monotonic() + RECONCILE_TOTAL_BUDGET
    deadline = own_deadline if deadline is None else min(own_deadline, deadline)

    def _remaining(cap: int, reserve: int = 0) -> int:
        remaining = int(deadline - time.monotonic() - reserve)
        if remaining < 1:
            return 0
        return min(max(1, cap), remaining)

    def _budget_ok(reserve: int = 1) -> bool:
        return deadline - time.monotonic() >= reserve

    if not is_git_worktree(team_root):
        return ReconcileResult(ok=False, action="not-worktree",
                               detail="not a git work tree")

    if (_preserve_pending_ancestry
            and (expected_identity is None or _target is None
                 or _pending_guard is None)):
        return ReconcileResult(
            ok=False, action="error",
            detail="pending-preserving reconcile requires bound identity and guard")

    # SessionStart/수동 호출(expected_identity=None)은 종전 current HEAD/@{u} 계약을
    # 그대로 쓴다. auto-commit opt-in만 commit 직후 캡처한 branch+OID와 explicit
    # publication target에 묶여 checkout 경합을 fail closed로 멈춘다.
    bound_identity = None
    local_ref = "HEAD"
    upstream_ref = "@{u}"
    if expected_identity is not None:
        identity_timeout = _remaining(DEFAULT_TIMEOUT)
        if not identity_timeout:
            return ReconcileResult(
                ok=False, action="error",
                detail="reconcile budget exhausted before identity validation")
        bound_identity = _validated_branch_identity(
            team_root, expected_identity, identity_timeout)
        if bound_identity is None:
            return ReconcileResult(
                ok=False, action="checkout-changed",
                detail="captured checkout identity is invalid")
        if not _checkout_matches_identity(
                team_root, bound_identity, identity_timeout):
            return ReconcileResult(
                ok=False, action="checkout-changed",
                detail="checkout changed before reconcile fetch")
        if _target is None:
            _target, target_detail = _resolve_publication_target(
                team_root, bound_identity, identity_timeout,
                deadline=deadline)
            if _target is None:
                return ReconcileResult(
                    ok=False, action="fetch-failed", detail=target_detail)
        local_ref = f"refs/heads/{bound_identity['branch']}"
        upstream_ref = _target.reconcile_ref

    def _checkout_changed(detail: str, *, ahead: int = 0, behind: int = 0,
                          diverged: bool = False) -> ReconcileResult:
        return ReconcileResult(
            ok=False, action="checkout-changed", ahead=ahead, behind=behind,
            diverged=diverged, detail=detail)

    def _bound_match() -> bool | None:
        if bound_identity is None:
            return True
        identity_timeout = _remaining(DEFAULT_TIMEOUT)
        if not identity_timeout:
            return None
        return _checkout_matches_identity(
            team_root, bound_identity, identity_timeout)

    def _success_or_pending_checkpoint(
            action: str, *, ahead: int = 0, behind: int = 0,
            detail: str = "") -> ReconcileResult:
        if (_pending_guard is not None and bound_identity is not None):
            return _finalize_pending_reconcile_without_mutation(
                team_root, bound_identity, ahead=ahead, behind=behind,
                action=action, detail=detail, deadline=deadline,
                pending_guard=_pending_guard,
                edit_lease_owner=_edit_lease_owner)
        return ReconcileResult(
            ok=True, action=action, ahead=ahead, behind=behind,
            detail=detail, final_identity=bound_identity)

    # 1) fetch — push/pull 과 동일 안전장치(http 타임아웃·killpg·자격증명 차단) 재사용.
    match = _bound_match()
    if match is None:
        return ReconcileResult(
            ok=False, action="error", detail="reconcile budget exhausted before fetch")
    if not match:
        return _checkout_changed("checkout changed before reconcile fetch")
    fetch_timeout = _remaining(timeout)
    if not fetch_timeout:
        return ReconcileResult(
            ok=False, action="error", detail="reconcile budget exhausted before fetch")
    fetch_args = ["-C", team_root, *http_timeout_opts(fetch_timeout)]
    if _target is not None and _target.push_endpoint:
        # Reconcile the actual publication endpoint, not merely remote.<name>.url:
        # a separate pushurl may point at a fork whose branch has advanced.  A
        # one-shot alias prevents late url.* rewrite rules from retargeting the
        # captured credential-safe endpoint, mirroring exact push protection.
        endpoint_alias = f"tm-mode-fetch-{os.urandom(16).hex()}://endpoint"
        fetch_args += [
            "-c", f"url.{_target.push_endpoint}.insteadOf={endpoint_alias}",
            "fetch", "--no-tags", "--no-write-fetch-head", "--",
            endpoint_alias,
            f"+{_target.destination}:{_target.reconcile_ref}",
        ]
    else:
        fetch_args += ["fetch"]
        if _target is not None:
            # Compatibility for internal callers that predate endpoint capture.
            fetch_args += ["--", _target.remote]
    try:
        frc, _, ferr = run_git(
            fetch_args, timeout=fetch_timeout)
    except subprocess.TimeoutExpired:
        return ReconcileResult(ok=False, action="fetch-failed", detail="fetch timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return ReconcileResult(ok=False, action="fetch-failed",
                               detail=f"fetch exec error: {exc}")
    if (frc != 0 and _target is not None and _target.push_endpoint
            and "couldn't find remote ref" in (ferr or "").lower()
            and _target.destination in (ferr or "")):
        # Exact endpoint proof says the destination does not exist.  Remove only
        # the tracking OID we just observed (CAS); a concurrent publication that
        # created/advanced it wins and makes this attempt retry instead of being
        # mistaken for a new branch.
        tracking_available, tracked = _read_ref_oid(
            team_root, _target.reconcile_ref, min(DEFAULT_TIMEOUT, fetch_timeout))
        if not tracking_available:
            return ReconcileResult(
                ok=False, action="fetch-failed",
                detail="missing remote branch tracking state unavailable")
        try:
            drc, _, derr = run_git(
                ["-C", team_root, "update-ref", "-d",
                 _target.reconcile_ref, tracked],
                timeout=min(DEFAULT_TIMEOUT, fetch_timeout))
        except (OSError, subprocess.SubprocessError) as exc:
            return ReconcileResult(
                ok=False, action="fetch-failed",
                detail=f"missing remote branch cleanup failed: {exc}")
        if drc != 0:
            return ReconcileResult(
                ok=False, action="fetch-failed",
                detail=(derr or "publication tracking ref changed").strip()[:200])
        frc, ferr = 0, ""
    if frc != 0:
        return ReconcileResult(ok=False, action="fetch-failed",
                               detail=(ferr or "").strip()[:200])

    match = _bound_match()
    if match is None:
        return ReconcileResult(
            ok=False, action="error", detail="reconcile budget exhausted after fetch")
    if not match:
        return _checkout_changed("checkout changed after reconcile fetch")

    # 2) ahead/behind 측정 — 추적 upstream 유무 판정 포함.
    probe_timeout = _remaining(DEFAULT_TIMEOUT)
    if not probe_timeout:
        return ReconcileResult(
            ok=False, action="error", detail="reconcile budget exhausted after fetch")
    if bound_identity is None:
        ahead, behind, has_up = _ahead_behind_raw(team_root, probe_timeout)
    else:
        upstream_available, upstream_oid = _read_ref_oid(
            team_root, upstream_ref, probe_timeout)
        if not upstream_available:
            return ReconcileResult(
                ok=False, action="error",
                detail="publication tracking ref unavailable")
        if not upstream_oid:
            return _success_or_pending_checkpoint(
                "no-upstream", detail="게시 대상 remote branch 없음(정합 불필요)")
        ahead, behind, has_up = _ahead_behind_refs(
            team_root, upstream_ref, local_ref, probe_timeout)
    if not has_up:
        return _success_or_pending_checkpoint(
            "no-upstream", detail="추적 upstream 없음(정합 불필요)")

    # PostToolUse auto-commit runs while another Claude/Codex editor may already
    # be writing the same checkout.  Without an edit lease, any snapshot followed
    # by reset/rebase has an unavoidable TOCTOU window that can erase those bytes.
    # The foreground publication path therefore performs fetch/status only.  A
    # remote advance is kept as a durable local commit + pending marker for the
    # SessionStart/manual reconcile channel, which runs outside the file-edit hook.
    if behind > 0 and not _allow_bound_mutation:
        return ReconcileResult(
            ok=False, action="deferred", ahead=ahead, behind=behind,
            diverged=ahead > 0,
            detail=("remote advanced; foreground worktree reconciliation "
                    "disabled because exact edit-lease and pending-safety "
                    "authorization was not provided"),
            final_identity=bound_identity)

    # 3) 이미 정합(behind==0)
    if behind == 0:
        action = "ahead-only" if ahead > 0 else "up-to-date"
        return _success_or_pending_checkpoint(
            action, ahead=ahead, behind=0)

    # 4) 순수 behind → fast-forward
    if ahead == 0:
        match = _bound_match()
        if match is None:
            return ReconcileResult(
                ok=False, action="error", behind=behind,
                detail="reconcile budget exhausted before ff merge")
        if not match:
            return _checkout_changed(
                "checkout changed before ff merge", behind=behind)
        merge_timeout = _remaining(DEFAULT_TIMEOUT)
        if not merge_timeout:
            return ReconcileResult(
                ok=False, action="error", behind=behind,
                detail="reconcile budget exhausted before ff merge")
        if bound_identity is not None:
            return _bound_reconcile_transaction(
                team_root, bound_identity, upstream_ref, upstream_oid, local_ref,
                mode="fast-forward", ahead=ahead, behind=behind,
                timeout=merge_timeout, deadline=deadline,
                edit_lease_owner=_edit_lease_owner,
                pending_guard=_pending_guard)
        lock_timeout = _remaining(1)
        if not lock_timeout:
            return ReconcileResult(
                ok=False, action="error", behind=behind,
                detail="reconcile budget exhausted before publication interlock")
        with _publication_interlock(
                team_root, lock_timeout) as (acquired, detail):
            if not acquired:
                return ReconcileResult(
                    ok=False, action="error", behind=behind, detail=detail)
            blocker = publication_blocker_detail(team_root, lock_timeout)
            if blocker:
                return ReconcileResult(
                    ok=False, action="error", behind=behind, detail=blocker)
            # Explicit/manual callers have no exact tool owner.  Keep the same
            # PreToolUse barrier held through the entire worktree mutation and
            # require that no editor is registered.
            with _edit_gate(team_root, 0.2) as (edit_acquired, edit_detail):
                if not edit_acquired:
                    return ReconcileResult(
                        ok=False, action="deferred", behind=behind,
                        detail=edit_detail)
                owners = _active_edit_lease_owners_locked(team_root)
                if owners != set():
                    return ReconcileResult(
                        ok=False, action="deferred", behind=behind,
                        detail="worktree edit lease unavailable or shared")
                merge_timeout = _remaining(DEFAULT_TIMEOUT)
                if not merge_timeout:
                    return ReconcileResult(
                        ok=False, action="error", behind=behind,
                        detail="reconcile budget exhausted before ff merge")
                try:
                    rc, _, err = run_git(
                        ["-C", team_root, "merge", "--ff-only", upstream_ref],
                        timeout=merge_timeout)
                except subprocess.TimeoutExpired:
                    return ReconcileResult(
                        ok=False, action="error", behind=behind,
                        detail="ff merge timeout")
                except (OSError, subprocess.SubprocessError) as exc:
                    return ReconcileResult(
                        ok=False, action="error", behind=behind,
                        detail=f"ff merge exec error: {exc}")
                if rc == 0:
                    return ReconcileResult(
                        ok=True, action="fast-forward", behind=behind)
                return ReconcileResult(
                    ok=False, action="error", behind=behind,
                    detail=(err or "").strip()[:200])

    # 5) diverge(ahead>0 & behind>0) → rebase --autostash. dirty 파일과 upstream
    # 변경이 겹치면 autostash 적용이 충돌 상태를 남길 수 있으므로 시작 전에 보류한다.
    if not _budget_ok(reserve=8):
        return ReconcileResult(
            ok=False, action="error", ahead=ahead, behind=behind, diverged=True,
            detail="reconcile budget exhausted before rebase safety checks")
    safety_issue = _rebase_dirty_safety_issue(
        team_root, upstream_ref, _remaining(1), local_ref=local_ref)
    if safety_issue:
        return ReconcileResult(
            ok=False, action="conflict", ahead=ahead, behind=behind,
            diverged=True, detail=f"rebase deferred: {safety_issue}")
    if bound_identity is not None:
        return _bound_reconcile_transaction(
            team_root, bound_identity, upstream_ref, upstream_oid, local_ref,
            mode=("merge" if _preserve_pending_ancestry else "rebase"),
            ahead=ahead, behind=behind,
            timeout=timeout, deadline=deadline,
            edit_lease_owner=_edit_lease_owner,
            pending_guard=_pending_guard)

    def _run_unbound_rebase_locked() -> ReconcileResult:
        # The first safety probe happened before lock acquisition; repeat it in
        # the serialized mutation window so a concurrent residue never slips in.
        locked_safety_issue = _rebase_dirty_safety_issue(
            team_root, upstream_ref, _remaining(1), local_ref=local_ref)
        if locked_safety_issue:
            return ReconcileResult(
                ok=False, action="conflict", ahead=ahead, behind=behind,
                diverged=True,
                detail=f"rebase deferred: {locked_safety_issue}")
        rebase_guard = _capture_rebase_guard(team_root, _remaining(1))
        if rebase_guard is None:
            return ReconcileResult(
                ok=False, action="conflict", ahead=ahead, behind=behind,
                diverged=True,
                detail="rebase deferred: rollback guard unavailable")
        if not _budget_ok(reserve=_RECONCILE_REBASE_RECOVERY_RESERVE + 1):
            return ReconcileResult(
                ok=False, action="error", ahead=ahead, behind=behind,
                diverged=True, detail="reconcile budget exhausted before rebase")
        rebase_timeout = _remaining(
            timeout, reserve=_RECONCILE_REBASE_RECOVERY_RESERVE)
        try:
            rc, rout, rerr = run_git(
                ["-C", team_root, "rebase", "--autostash", upstream_ref],
                timeout=rebase_timeout)
        except subprocess.TimeoutExpired as exc:
            created_autostash = _created_autostash_oid(
                team_root, _timeout_detail(exc), timeout=1)
            abort_ok = _abort_rebase(team_root, 1)
            rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                team_root, rebase_guard, created_autostash, timeout=1)
            return ReconcileResult(
                ok=False, action="conflict", ahead=ahead, behind=behind,
                diverged=True, detail=_rebase_abort_detail(
                    "rebase timeout", abort_ok, rollback_ok, post_detail))
        except (OSError, subprocess.SubprocessError) as exc:
            abort_ok = _abort_rebase(team_root, 1)
            rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                team_root, rebase_guard, timeout=1)
            return ReconcileResult(
                ok=False, action="conflict", ahead=ahead, behind=behind,
                diverged=True, detail=_rebase_abort_detail(
                    f"rebase exec error: {exc}", abort_ok, rollback_ok,
                    post_detail))
        if rc == 0:
            created_autostash = _created_autostash_oid(
                team_root, (rout or "") + "\n" + (rerr or ""), timeout=1)
            post_ok, post_detail = _verify_rebase_postcondition(
                team_root, rebase_guard, created_autostash, timeout=1)
            if not post_ok:
                return ReconcileResult(
                    ok=False, action="conflict", ahead=ahead, behind=behind,
                    diverged=True,
                    detail=f"rebase postcondition failed: {post_detail}")
            final_probe_timeout = _remaining(DEFAULT_TIMEOUT)
            if not final_probe_timeout:
                return ReconcileResult(
                    ok=False, action="error", ahead=ahead, behind=behind,
                    diverged=True,
                    detail="reconciled but budget exhausted before final status")
            a2, _, _ = _ahead_behind_raw(team_root, final_probe_timeout)
            return ReconcileResult(
                ok=True, action="rebased", ahead=a2, behind=behind,
                diverged=True, detail="rebased onto upstream")
        abort_ok = _abort_rebase(team_root, 1)
        created_autostash = _created_autostash_oid(
            team_root, (rout or "") + "\n" + (rerr or ""), timeout=1)
        rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
            team_root, rebase_guard, created_autostash, timeout=1)
        return ReconcileResult(
            ok=False, action="conflict", ahead=ahead, behind=behind,
            diverged=True, detail=_rebase_abort_detail(
                "rebase failed", abort_ok, rollback_ok, post_detail,
                (rerr or "").strip()[:200]))

    lock_timeout = _remaining(
        1, reserve=_RECONCILE_REBASE_RECOVERY_RESERVE)
    if not lock_timeout:
        return ReconcileResult(
            ok=False, action="error", ahead=ahead, behind=behind, diverged=True,
            detail="reconcile budget exhausted before publication interlock")
    with _publication_interlock(
            team_root, lock_timeout) as (acquired, detail):
        if not acquired:
            return ReconcileResult(
                ok=False, action="conflict", ahead=ahead, behind=behind,
                diverged=True, detail=detail)
        blocker = publication_blocker_detail(team_root, lock_timeout)
        if blocker:
            return ReconcileResult(
                ok=False, action="conflict", ahead=ahead, behind=behind,
                diverged=True, detail=blocker)
        with _edit_gate(team_root, 0.2) as (edit_acquired, edit_detail):
            if not edit_acquired:
                return ReconcileResult(
                    ok=False, action="deferred", ahead=ahead, behind=behind,
                    diverged=True, detail=edit_detail)
            owners = _active_edit_lease_owners_locked(team_root)
            if owners != set():
                return ReconcileResult(
                    ok=False, action="deferred", ahead=ahead, behind=behind,
                    diverged=True,
                    detail="worktree edit lease unavailable or shared")
            return _run_unbound_rebase_locked()


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


def _owned_regular(st: os.stat_result) -> bool:
    """현재 사용자 소유 regular file 인지 확인한다(POSIX 외에는 ownership 생략)."""
    if not stat.S_ISREG(st.st_mode):
        return False
    return not hasattr(os, "getuid") or st.st_uid == os.getuid()


def _ensure_private_state_dir() -> bool:
    """XDG teammode state dir 를 실제 디렉터리·0700으로 보장한다. 무raise.

    마지막 경로가 symlink 이면 따라가지 않는다. 상태 경로는 머신 로컬 correctness
    ledger 와 실패 상세를 담으므로, world-readable 기본 umask 에 맡기지 않는다.
    """
    path = _state_dir()
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.lstat(path)
        is_junction = getattr(os.path, "isjunction", lambda _path: False)
        if (not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)
                or os.path.islink(path) or is_junction(path)):
            return False
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return False
        try:
            os.chmod(path, 0o700)
        except OSError:
            if os.name != "nt":
                return False
        return True
    except OSError:
        return False


def _secure_open_regular(path: str, flags: int, mode: int = 0o600) -> int:
    """symlink 을 따라가지 않고 owner-only regular file descriptor 를 연다."""
    try:
        if os.path.islink(path):
            raise OSError(errno.ELOOP, "state path is a symlink")
        before = os.lstat(path)
        if not _owned_regular(before):
            raise OSError(errno.EPERM, "state path is not an owned regular file")
    except FileNotFoundError:
        if not (flags & os.O_CREAT):
            raise
    safe_flags = flags | getattr(os, "O_CLOEXEC", 0)
    safe_flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, safe_flags, mode)
    try:
        st = os.fstat(fd)
        if not _owned_regular(st):
            raise OSError(errno.EPERM, "state path is not an owned regular file")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_private_text(path: str) -> PushPendingRead:
    """owner-only state 파일을 안전하게 읽는다. 없음은 available empty 상태다."""
    if not _ensure_private_state_dir():
        return PushPendingRead(available=False)
    try:
        fd = _secure_open_regular(
            path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return PushPendingRead(fingerprint=("missing",))
    except OSError:
        return PushPendingRead(available=False)
    try:
        st = os.fstat(fd)
        fingerprint = (
            st.st_dev, st.st_ino, st.st_size,
            getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
        )
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            return PushPendingRead(handle.read().strip(), True, fingerprint)
    except (OSError, UnicodeError, ValueError):
        return PushPendingRead(available=False)


def _fsync_parent_dir(path: str) -> bool:
    """atomic rename의 directory entry까지 durable하게 만든다(지원 불가 FS는 허용)."""
    if os.name == "nt":  # os.replace durability is handled by the platform API.
        return True
    fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(os.path.dirname(path), flags)
        os.fsync(fd)
        return True
    except OSError as exc:
        unsupported = {
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }
        return exc.errno in unsupported
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _write_private_text(path: str, content: str) -> bool:
    """같은 디렉터리의 고유 0600 임시파일을 원자 replace 한다. 무raise."""
    if not _ensure_private_state_dir():
        return False
    try:
        try:
            existing = os.lstat(path)
        except FileNotFoundError:
            existing = None
        if existing is not None and not _owned_regular(existing):
            return False

        fd, tmp = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=_state_dir())
        try:
            st = os.fstat(fd)
            if not _owned_regular(st):
                return False
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            tmp = ""
            return _fsync_parent_dir(path)
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except (OSError, TypeError, UnicodeError, ValueError):
        return False


def _remove_private_file(path: str) -> bool:
    """owned regular state 파일만 제거한다. symlink/FIFO 는 보존하며 무raise."""
    if not _ensure_private_state_dir():
        return False
    try:
        st = os.lstat(path)
        if not _owned_regular(st):
            return False
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_URL_USERINFO_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s@]+)@")
_SECRET_TOKEN_RE = re.compile(
    r"(?i)\b(?:gh[pousr]_[a-z0-9_]{10,}|github_pat_[a-z0-9_]{10,}|"
    r"glpat-[a-z0-9_-]{10,})\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(token|access[_-]?token|oauth[_-]?token|password|passwd|secret|"
    r"client[_-]?secret|api[_-]?key|private[_-]?token)\b"
    r"(\s*[:=]\s*)([^\s&,;]+)")
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(authorization\s*:\s*)(?:bearer|basic|token)\s+[^\s,;]+")


def sanitize_git_detail(detail: str, limit: int = 400) -> str:
    """사용자에게 노출·영속해도 되는 bounded Git 실패 상세로 정제한다."""
    text = _ANSI_ESCAPE_RE.sub("", str(detail or ""))
    text = _URL_USERINFO_RE.sub(r"\1[redacted]@", text)
    text = _SECRET_TOKEN_RE.sub("[redacted]", text)
    text = _AUTH_HEADER_RE.sub(r"\1[redacted]", text)
    text = _SECRET_VALUE_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    text = "".join(
        ch if not unicodedata.category(ch).startswith("C") else " " for ch in text)
    text = " ".join(text.split())
    return text[:limit] or "unknown git failure"


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
    safe_detail = sanitize_git_detail(detail)
    with _push_pending_ledger_lock(team_root) as locked:
        if locked:
            _write_private_text(sync_warning_path(team_root), safe_detail)


def write_sync_warning_if_empty(team_root: str, detail: str) -> bool:
    """기존 actionable warning이 없을 때만 generic detail을 원자 기록한다."""
    safe_detail = sanitize_git_detail(detail)
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        current = _read_private_text(sync_warning_path(team_root))
        if not current.available or current.content:
            return False
        return _write_private_text(sync_warning_path(team_root), safe_detail)


def read_sync_warning(team_root: str) -> str:
    """team_root 전용 sync-warning 마커 내용(없으면 ''). 무raise."""
    content = _read_private_text(sync_warning_path(team_root)).content
    return sanitize_git_detail(content) if content else ""


def clear_sync_warning(team_root: str) -> None:
    """team_root 전용 sync-warning 마커만 제거(push/정합이 회복되면 호출). 무raise.

    자기 팀 파일만 지우므로 같은 머신의 다른 팀 레포 마커를 건드리지 않는다(P2 수정 핵심).
    """
    with _push_pending_ledger_lock(team_root) as locked:
        if locked:
            _remove_private_file(sync_warning_path(team_root))


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
_PENDING_IDENTITY_TIMEOUT = 1
_PENDING_TARGET_UNSET = object()
_LOCK_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
}


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
            if _ensure_private_state_dir():
                lock_fd = _secure_open_regular(
                    push_pending_path(team_root) + ".state.lock",
                    os.O_RDWR | os.O_CREAT,
                )
                handle = os.fdopen(lock_fd, "r+b", buffering=0)
                deadline = time.monotonic() + _PUSH_PENDING_LOCK_WAIT_SECONDS
            else:
                deadline = time.monotonic()

            if handle is not None and os.name == "nt":  # pragma: no cover — Windows CI 부재
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
            elif handle is not None:
                import fcntl

                def try_lock():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                def unlock():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            while handle is not None:
                try:
                    try_lock()
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in _LOCK_CONTENTION_ERRNOS:
                        break
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


_PENDING_LEDGER_VERSION = 2


def _checkout_identity(
        team_root: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, str]:
    """현재 checkout을 pending entry key로 바꾼다. git 실패도 안정 key를 반환한다."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "symbolic-ref", "--quiet", "--short", "HEAD"],
            timeout=timeout)
        branch = (out or "").strip() if rc == 0 else ""
    except (OSError, subprocess.SubprocessError):
        branch = ""

    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-parse", "--verify", "HEAD"],
            timeout=timeout)
        head = (out or "").strip() if rc == 0 else ""
    except (OSError, subprocess.SubprocessError):
        head = ""
    if branch:
        # branch 이름은 rename될 수 있으므로 실패 시점의 immutable HEAD도 함께 저장한다.
        # worker는 옛 branch ref가 사라지고 새 branch가 같은 HEAD일 때만 key를 옮긴다.
        return {"key": f"branch:{branch}", "branch": branch, "head": head}
    if head:
        return {"key": f"detached:{head}", "branch": "", "head": head}
    # Non-git roots exist in unit callers; production push failure in a git repo resolves above.
    return {"key": "checkout:unknown", "branch": "", "head": ""}


def _pending_entries(snapshot_content: str) -> dict[str, dict]:
    """v2 ledger entries를 파싱한다. legacy/malformed payload는 빈 dict로 보수 처리."""
    try:
        payload = json.loads(snapshot_content or "{}")
    except (TypeError, ValueError):
        return {}
    if (not isinstance(payload, dict)
            or payload.get("version") != _PENDING_LEDGER_VERSION
            or not isinstance(payload.get("entries"), dict)):
        return {}
    return {
        str(key): value for key, value in payload["entries"].items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _serialize_pending_entries(team_root: str, entries: dict[str, dict]) -> str:
    return json.dumps(
        {"version": _PENDING_LEDGER_VERSION,
         "root": os.path.normpath(str(team_root)),
         "entries": entries},
        ensure_ascii=False, sort_keys=True)


def _legacy_pending_key(snapshot_content: str) -> str:
    """v1 payload면 안정 legacy key를 반환한다. 임의 malformed text는 대상 아님."""
    try:
        payload = json.loads(snapshot_content or "{}")
    except (TypeError, ValueError):
        return ""
    if (not isinstance(payload, dict) or payload.get("version") == 2
            or not isinstance(payload.get("nonce"), str)):
        return ""
    digest = hashlib.sha256(
        snapshot_content.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"legacy:{digest}"


def _unique_local_ahead_branch(team_root: str) -> str:
    """origin/upstream보다 앞선 local branch가 정확히 하나일 때만 그 이름을 반환."""
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "for-each-ref",
             "--format=%(refname:short)\t%(upstream:short)", "refs/heads"],
            timeout=DEFAULT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    if rc != 0:
        return ""
    ahead_branches = []
    for line in (out or "").splitlines():
        branch, _, upstream = line.partition("\t")
        branch, upstream = branch.strip(), upstream.strip()
        if not branch:
            continue
        try:
            if upstream:
                arc, counts, _ = run_git(
                    ["-C", team_root, "rev-list", "--left-right", "--count",
                     f"{upstream}...refs/heads/{branch}"],
                    timeout=DEFAULT_TIMEOUT)
                parts = (counts or "").strip().split()
                if arc != 0 or len(parts) != 2:
                    return ""
                ahead = int(parts[1])
            else:
                arc, count, _ = run_git(
                    ["-C", team_root, "rev-list", "--count",
                    f"refs/heads/{branch}", "--not", "--remotes"],
                    timeout=DEFAULT_TIMEOUT)
                if arc != 0:
                    return ""
                ahead = int((count or "0").strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return ""
        if ahead > 0:
            ahead_branches.append(branch)
    return ahead_branches[0] if len(ahead_branches) == 1 else ""


def _legacy_safe_session_branch(team_root: str) -> str:
    """v1 marker를 bind해도 안전한 유일한 session-log-only branch를 반환한다.

    v1 ledger에는 branch 정보가 없으므로 ``unique ahead``만으로는 증거가 부족하다.
    stale marker 뒤에 만든 private branch를 자동 publish하지 않도록, ahead commit 전부가
    과거 auto-commit subject를 쓰고 canonical session log만 변경한 경우로 제한한다.
    어떤 git 판정이라도 실패하거나 일반 파일이 하나라도 섞이면 빈 문자열(fail closed).
    """
    branch = _unique_local_ahead_branch(team_root)
    if not branch:
        return ""
    try:
        rc, upstream_out, _ = run_git(
            ["-C", team_root, "for-each-ref", "--format=%(upstream:short)",
             f"refs/heads/{branch}"], timeout=DEFAULT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    if rc != 0:
        return ""
    upstream_lines = (upstream_out or "").splitlines()
    if len(upstream_lines) > 1:
        return ""
    upstream = upstream_lines[0].strip() if upstream_lines else ""
    if upstream:
        rev_args = ["-C", team_root, "rev-list", "--reverse",
                    f"{upstream}..refs/heads/{branch}"]
    else:
        rev_args = ["-C", team_root, "rev-list", "--reverse",
                    f"refs/heads/{branch}", "--not", "--remotes"]
    try:
        rc, commits_out, _ = run_git(rev_args, timeout=DEFAULT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    commits = [line.strip() for line in (commits_out or "").splitlines()
               if line.strip()]
    if rc != 0 or not commits:
        return ""

    prefix = "memory/team/sessions/"
    for commit in commits:
        try:
            src, subject, _ = run_git(
                ["-C", team_root, "show", "-s", "--format=%s", commit],
                timeout=DEFAULT_TIMEOUT)
            prc, paths_out, _ = run_git(
                ["-C", team_root, "diff-tree", "--root", "--no-commit-id",
                 "--no-renames", "--name-only", "-r", "-z", commit],
                timeout=DEFAULT_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return ""
        if (src != 0 or prc != 0
                or not (subject or "").strip().startswith(
                    "chore(teammode): auto-commit ")):
            return ""
        paths = [path for path in (paths_out or "").split("\0") if path]
        if not paths:
            return ""
        for path in paths:
            if not path.startswith(prefix) or not path.endswith(".md"):
                return ""
            tail_parts = path[len(prefix):].split("/")
            if (len(tail_parts) < 2
                    or any(part in {"", ".", ".."} for part in tail_parts)):
                return ""
    return branch


def _bind_renamed_pending_to_current_checkout(
        team_root: str, snapshot_content: str) -> str:
    """사라진 old branch entry를 동일 HEAD의 현재 branch key로 CAS 이동한다."""
    entries = _pending_entries(snapshot_content)
    if not entries:
        return snapshot_content
    current = _checkout_identity(team_root)
    if (not current.get("branch") or not current.get("head")
            or current["key"] in entries):
        return snapshot_content
    candidates = []
    for key, entry in entries.items():
        old_branch = str(entry.get("branch") or "")
        if (not old_branch or key != f"branch:{old_branch}"
                or entry.get("head") != current["head"]):
            continue
        try:
            rc, _, _ = run_git(
                ["-C", team_root, "show-ref", "--verify", "--quiet",
                 f"refs/heads/{old_branch}"], timeout=DEFAULT_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return snapshot_content
        if rc == 1:  # old ref가 실제로 사라졌을 때만 rename으로 인정한다.
            candidates.append((key, entry, old_branch))
        elif rc not in (0, 1):
            return snapshot_content
    if len(candidates) != 1:
        return snapshot_content

    old_key, old_entry, old_branch = candidates[0]
    migrated_entries = dict(entries)
    migrated_entries.pop(old_key)
    migrated_entry = dict(old_entry)
    migrated_entry["branch"] = current["branch"]
    migrated_entry["head"] = current["head"]
    migrated_entry["renamed_from"] = old_branch
    migrated_entries[current["key"]] = migrated_entry
    migrated = _serialize_pending_entries(team_root, migrated_entries)
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return snapshot_content
        current_state = _read_private_text(push_pending_path(team_root))
        if (not current_state.available
                or current_state.content != snapshot_content.strip()):
            return current_state.content if current_state.available else snapshot_content
        return migrated if _write_private_text(
            push_pending_path(team_root), migrated) else snapshot_content


def bind_legacy_pending_to_current_checkout(
        team_root: str, snapshot_content: str) -> str:
    """검증된 rename 또는 safe-session legacy entry를 현재 checkout에 CAS bind한다.

    raw v1뿐 아니라 새 failure와 함께 v2에 보존된 ``legacy:true`` entry도 처리한다.
    v1은 branch 정보가 없으므로 유일한 ahead branch의 commit 전부가 canonical session-log
    auto-commit이라는 증거가 있을 때만 bind한다. git 판정은 ledger lock 밖에서 끝내고,
    lock 안에서는 원래 snapshot CAS + atomic replace만 수행한다.
    """
    snapshot_content = _bind_renamed_pending_to_current_checkout(
        team_root, snapshot_content)
    entries = _pending_entries(snapshot_content)
    raw_legacy_key = _legacy_pending_key(snapshot_content)
    if raw_legacy_key:
        try:
            raw_legacy = json.loads(snapshot_content)
        except (TypeError, ValueError):
            return snapshot_content
        legacy_items = [(raw_legacy_key, raw_legacy)]
    else:
        legacy_items = [
            (key, entry) for key, entry in entries.items()
            if key.startswith("legacy:") and entry.get("legacy") is True
        ]
    if len(legacy_items) != 1:
        return snapshot_content
    legacy_key, legacy = legacy_items[0]
    unique_branch = _legacy_safe_session_branch(team_root)
    current = _checkout_identity(team_root)
    if not unique_branch or current.get("branch") != unique_branch:
        return snapshot_content
    migrated_entries = dict(entries)
    migrated_entries.pop(legacy_key, None)
    if current["key"] in migrated_entries:
        # 현재 entry의 push는 이 branch의 모든 선행 commit을 포함한다. legacy가
        # 이미 수동 publish된 다른 branch였든 현재 branch였든 이 publication에
        # 흡수해도 안전하므로, 영구 unknown entry 대신 existing target에 병합한다.
        entry = dict(migrated_entries[current["key"]])
        entry["absorbed_legacy"] = True
    else:
        entry = {
            "branch": unique_branch,
            "head": current.get("head", ""),
            "written_at": str(legacy.get("written_at") or
                              datetime.now().isoformat(timespec="seconds")),
            "nonce": str(legacy.get("nonce") or os.urandom(8).hex()),
            "migrated_from": 1,
        }
    migrated_entries[current["key"]] = entry
    migrated = _serialize_pending_entries(team_root, migrated_entries)
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return snapshot_content
        current_state = _read_private_text(push_pending_path(team_root))
        if (not current_state.available
                or current_state.content != snapshot_content.strip()):
            return current_state.content if current_state.available else snapshot_content
        return migrated if _write_private_text(
            push_pending_path(team_root), migrated) else snapshot_content


def pending_entry_key_for_current_checkout(
        team_root: str, snapshot_content: str) -> str:
    """snapshot에 현재 branch/detached HEAD entry가 있으면 그 immutable key를 반환."""
    current = _checkout_identity(team_root)
    entries = _pending_entries(snapshot_content)
    if current["key"] in entries:
        return current["key"]
    return ""


def pending_targets_current_checkout(team_root: str, snapshot_content: str) -> bool:
    """현재 checkout이 이 pending ledger의 publication 대상 중 하나인지 판정."""
    return bool(pending_entry_key_for_current_checkout(team_root, snapshot_content))


def pending_allows_current_checkout_reconcile(
        team_root: str, snapshot_content: str) -> bool:
    """Return whether pending evidence proves this checkout may be rewritten.

    An immutable pending head for the current checkout cannot survive rebase:
    the rewritten commit gets a new OID while the worker remains bound to the
    old one.  Unknown/legacy/corrupt evidence is equally unsafe.  Valid entries
    for other checkouts do not constrain the current branch.
    """
    if not snapshot_content:
        return True
    entries = _pending_entries(snapshot_content)
    if not entries or _legacy_pending_key(snapshot_content):
        return False
    current = _checkout_identity(team_root)
    if not current.get("key") or current["key"] in entries:
        return False
    for key, entry in entries.items():
        if (key.startswith("legacy:") or not isinstance(entry, dict)
                or entry.get("legacy") is True):
            return False
        identity = _validated_pending_identity(team_root, {
            "key": key,
            "branch": str(entry.get("branch") or ""),
            "head": str(entry.get("head") or ""),
        })
        if identity is None:
            return False
    return True


def pending_entry_covered_by_publication(
        team_root: str, snapshot_content: str, target_key: str,
        publication_identity: dict | None,
        publication_target: dict | None) -> bool:
    """Prove a just-published immutable commit contains one older pending entry."""
    entry = _pending_entries(snapshot_content).get(target_key)
    published = _validated_pending_identity(team_root, publication_identity)
    if not isinstance(entry, dict) or published is None:
        return False
    if published.get("key") != target_key:
        return False
    pending = _validated_pending_identity(
        team_root, {
            "key": target_key,
            "branch": str(entry.get("branch") or ""),
            "head": str(entry.get("head") or ""),
        })
    if pending is None or not isinstance(publication_target, dict):
        return False

    def _signature(payload: dict) -> tuple[str, str, str, str] | None:
        values = tuple(str(payload.get(key) or "") for key in (
            "remote", "destination", "reconcile_ref", "remote_fingerprint"))
        if (not values[0] or not values[1].startswith("refs/heads/")
                or not values[2].startswith("refs/remotes/")
                or not re.fullmatch(r"[0-9a-f]{64}", values[3])):
            return None
        return values

    if (_signature(entry) is None
            or _signature(entry) != _signature(publication_target)):
        return False
    return _pending_head_covered_by_history(
        team_root, pending["head"], published["head"])


def pending_target_summary(snapshot_content: str, team_root: str = "") -> str:
    """경고용 pending target 요약(credential/control-code 정제 포함)."""
    targets = []
    for key, entry in _pending_entries(snapshot_content).items():
        if entry.get("branch"):
            targets.append(f"branch {entry['branch']}")
        elif entry.get("head"):
            targets.append(f"detached {str(entry['head'])[:12]}")
        elif key.startswith("legacy:"):
            targets.append("legacy checkout (unknown branch)")
        else:
            targets.append("unknown checkout")
    if not targets and _legacy_pending_key(snapshot_content):
        branch = _legacy_safe_session_branch(team_root) if team_root else ""
        targets.append(f"legacy checkout ({branch or 'unknown branch'})")
    return sanitize_git_detail(", ".join(targets) or "unknown checkout", limit=200)


def _validated_pending_identity(
        team_root: str, identity: dict | None) -> dict[str, str] | None:
    """명시 identity를 검증하거나, 생략 시 현재 checkout identity를 반환한다."""
    if identity is None:
        return _checkout_identity(team_root)
    if not isinstance(identity, dict):
        return None
    key = identity.get("key")
    branch = identity.get("branch")
    head = identity.get("head")
    if not all(isinstance(value, str) for value in (key, branch, head)):
        return None
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", head or ""):
        return None
    if branch:
        if key != f"branch:{branch}":
            return None
        try:
            rc, _, _ = run_git(
                ["-C", team_root, "check-ref-format", "--branch", branch],
                timeout=_PENDING_IDENTITY_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        if rc != 0:
            return None
    elif key != f"detached:{head}":
        return None
    try:
        rc, resolved, _ = run_git(
            ["-C", team_root, "rev-parse", "--verify", "--end-of-options",
             f"{head}^{{commit}}"],
            timeout=_PENDING_IDENTITY_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if (rc != 0 or (resolved or "").splitlines() != [head.lower()]):
        return None
    return {"key": key, "branch": branch, "head": head.lower()}


def _pending_target_payload(target: _PublicationTarget) -> dict:
    return {
        "remote": target.remote,
        "destination": target.destination,
        "reconcile_ref": target.reconcile_ref,
        "set_upstream": target.set_upstream,
        "remote_fingerprint": target.remote_fingerprint,
    }


def _validated_pending_target(
        team_root: str, target: _PublicationTarget | dict | None,
        timeout: int = _PENDING_IDENTITY_TIMEOUT, *,
        verify_remote_binding: bool = True) -> dict | None:
    """Validate a credential-free exact pending publication destination."""
    if isinstance(target, _PublicationTarget):
        payload = _pending_target_payload(target)
    elif isinstance(target, dict):
        payload = dict(target)
    else:
        return None
    remote = payload.get("remote")
    destination = payload.get("destination")
    reconcile_ref = payload.get("reconcile_ref")
    set_upstream = payload.get("set_upstream", False)
    remote_fingerprint = payload.get("remote_fingerprint")
    if (not all(isinstance(value, str)
                for value in (
                    remote, destination, reconcile_ref, remote_fingerprint))
            or not isinstance(set_upstream, bool)
            or not re.fullmatch(r"[0-9a-f]{64}", remote_fingerprint)):
        return None
    remotes = _remote_names(team_root, timeout)
    if remotes is None or not _valid_remote(remote, remotes):
        return None
    if verify_remote_binding:
        current_fingerprint = _remote_push_fingerprint(
            team_root, remote, timeout)
        if (not current_fingerprint
                or current_fingerprint != remote_fingerprint):
            return None
    if (not destination.startswith("refs/heads/")
            or not _valid_full_ref(team_root, destination, timeout)):
        return None
    if (not reconcile_ref.startswith("refs/remotes/")
            or not _valid_full_ref(team_root, reconcile_ref, timeout)
            or reconcile_ref != _tracking_ref_for_destination(
                remote, destination)):
        return None
    return {
        "remote": remote,
        "destination": destination,
        "reconcile_ref": reconcile_ref,
        "set_upstream": set_upstream,
        "remote_fingerprint": remote_fingerprint,
    }


def _pending_head_ancestry(
        team_root: str, older: str, newer: str,
        timeout: int = _PENDING_IDENTITY_TIMEOUT) -> bool | None:
    """Return whether older is an ancestor of newer; None means unprovable."""
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "merge-base", "--is-ancestor", older, newer],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def _pending_head_covered_by_history(
        team_root: str, older: str, newer: str,
        timeout: int = _PENDING_IDENTITY_TIMEOUT, deadline=None) -> bool:
    """Prove ancestry or an exact, linear, non-empty patch rewrite; fail closed."""
    older, newer = str(older or "").lower(), str(newer or "").lower()
    if (len(older) not in (40, 64) or len(newer) != len(older)
            or any(re.fullmatch(r"[0-9a-f]+", oid) is None
                   for oid in (older, newer))):
        return False
    cap = max(1.0, float(timeout))
    bounded = time.monotonic() + cap
    deadline = bounded if deadline is None else min(bounded, deadline)
    oid_pattern = rf"[0-9a-f]{{{len(older)}}}"

    def _probe(args, input_text=None, input_bytes=None):
        remaining = min(cap, deadline - time.monotonic())
        if remaining <= 0:
            return None
        try:
            return run_git(
                args, timeout=remaining, input_text=input_text,
                input_bytes=input_bytes)
        except (OSError, subprocess.SubprocessError):
            return None

    for oid in (older, newer):
        resolved = _probe([
            "-C", team_root, "rev-parse", "--verify", "--end-of-options",
            f"{oid}^{{commit}}"])
        if (resolved is None or resolved[0] != 0
                or (resolved[1] or "").splitlines() != [oid]):
            return False

    ancestry = _probe([
        "-C", team_root, "merge-base", "--is-ancestor", older, newer])
    if ancestry is None or ancestry[0] not in (0, 1):
        return False
    if ancestry[0] == 0:
        return True

    old_rev_list = _probe([
        "-C", team_root, "rev-list", "--parents", "--reverse",
        f"{newer}..{older}",
    ])
    new_rev_list = _probe([
        "-C", team_root, "rev-list", "--parents", "--reverse",
        f"{older}..{newer}",
    ])
    if (old_rev_list is None or old_rev_list[0] != 0
            or new_rev_list is None or new_rev_list[0] != 0):
        return False
    old_rows = [
        line.lower().split()
        for line in (old_rev_list[1] or "").splitlines()]
    new_rows = [
        line.lower().split()
        for line in (new_rev_list[1] or "").splitlines()]
    if (not old_rows or any(len(row) != 2 or any(
            re.fullmatch(oid_pattern, oid) is None for oid in row)
            for row in old_rows)
            or any(not row or any(
                re.fullmatch(oid_pattern, oid) is None for oid in row)
                for row in new_rows)):
        return False
    old_only = [row[0] for row in old_rows]
    new_only = [row[0] for row in new_rows if len(row) == 2]
    if (len(set(old_only)) != len(old_only)
            or len(set(row[0] for row in new_rows)) != len(new_rows)
            or not new_only):
        return False

    def _patch_fingerprints(commits):
        diff = _probe([
            "-C", team_root, "diff-tree", "--stdin", "--patch", "--binary",
            "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames",
            "--no-color",
        ], input_bytes="".join(
            f"{commit}\n" for commit in commits).encode("ascii"))
        if diff is None or diff[0] != 0:
            return None
        patch_ids = _probe(
            ["-C", team_root, "patch-id", "--verbatim"],
            input_bytes=diff[1] or b"")
        if patch_ids is None or patch_ids[0] != 0:
            return None
        try:
            patch_output = (patch_ids[1] or b"").decode("ascii", errors="strict")
        except (AttributeError, UnicodeDecodeError):
            return None
        records = {}
        for line in patch_output.splitlines():
            match = re.fullmatch(
                rf"({oid_pattern}) ({oid_pattern})", line.lower())
            if match is None:
                return None
            patch_id, commit = match.groups()
            if commit not in commits or commit in records:
                return None
            records[commit] = patch_id
        if set(records) != set(commits):
            return None
        return [records[commit] for commit in commits]

    old_fingerprints = _patch_fingerprints(old_only)
    new_fingerprints = _patch_fingerprints(new_only)
    if old_fingerprints is None or new_fingerprints is None:
        return False
    old_counts, new_counts = Counter(old_fingerprints), Counter(new_fingerprints)
    return all(new_counts[patch_id] >= count
               for patch_id, count in old_counts.items())


def _push_pending_snapshot_is_current(
        team_root: str, snapshot_content: str, target_key: str) -> bool:
    """Short exact ledger precheck; callers use publication interlock for races."""
    if not snapshot_content or not target_key:
        return False
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        current = _read_private_text(push_pending_path(team_root))
        return (current.available
                and current.content == snapshot_content.strip()
                and target_key in _pending_entries(current.content))


def _advance_push_pending_if_unchanged(
        team_root: str, snapshot_content: str, target_key: str,
        final_identity: dict | None, target: dict | None,
        deadline=None) -> bool:
    """CAS-advance H1 to a proven descendant without losing other checkouts.

    The caller holds the publication interlock and edit gate.  Validation and
    ancestry probes happen before the short ledger lock; the byte-exact snapshot
    check inside the lock prevents a concurrent writer from being overwritten.
    """
    final = _validated_pending_identity(team_root, final_identity)
    stored_target = _validated_pending_target(team_root, target)
    snapshot_entries = _pending_entries(snapshot_content)
    old_entry = snapshot_entries.get(target_key)
    if (final is None or final.get("key") != target_key
            or stored_target is None or not isinstance(old_entry, dict)):
        return False
    old_identity = _validated_pending_identity(team_root, {
        "key": target_key,
        "branch": str(old_entry.get("branch") or ""),
        "head": str(old_entry.get("head") or ""),
    })
    old_target = _validated_pending_target(team_root, old_entry)
    if (old_identity is None or old_target != stored_target
            or not _pending_head_covered_by_history(
                team_root, old_identity["head"], final["head"],
                timeout=_PENDING_IDENTITY_TIMEOUT, deadline=deadline)):
        return False

    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        current = _read_private_text(push_pending_path(team_root))
        if (not current.available
                or current.content != snapshot_content.strip()):
            return False
        entries = _pending_entries(current.content)
        entry = entries.get(target_key)
        if not isinstance(entry, dict) or entry != old_entry:
            return False
        if old_identity["head"] == final["head"]:
            return True
        advanced = dict(entry)
        advanced.update({
            "branch": final["branch"],
            "head": final["head"],
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "nonce": os.urandom(8).hex(),
        })
        entries[target_key] = advanced
        return _write_private_text(
            push_pending_path(team_root),
            _serialize_pending_entries(team_root, entries))


def reconcile_current_pending(
        team_root: str, snapshot_content: str, target_key: str,
        timeout: int = NET_TIMEOUT, deadline: float | None = None,
        ) -> ReconcileResult:
    """Safely converge one current-checkout pending entry with its stored target.

    Divergence is merged rather than rebased so the recorded immutable H1 stays
    an ancestor.  The ledger is then CAS-advanced to the final branch head while
    publication/edit barriers are still held, allowing the exact worker to push
    the new head without an H1/non-fast-forward retry wedge.
    """
    entry = _pending_entries(snapshot_content).get(target_key)
    if not isinstance(entry, dict):
        return ReconcileResult(
            ok=False, action="pending-changed",
            detail="pending entry unavailable")
    pending_identity = _validated_pending_identity(team_root, {
        "key": target_key,
        "branch": str(entry.get("branch") or ""),
        "head": str(entry.get("head") or ""),
    })
    current = _validated_branch_identity(team_root, _checkout_identity(team_root))
    if (pending_identity is None or current is None
            or pending_identity.get("key") != current.get("key")):
        return ReconcileResult(
            ok=False, action="checkout-changed",
            detail="pending entry does not match the current branch")
    validated_target = _validated_pending_target(team_root, entry)
    if validated_target is None:
        return ReconcileResult(
            ok=False, action="pending-target-invalid",
            detail="stored pending target or remote binding changed",
            final_identity=current)
    binding = _remote_push_binding(
        team_root, validated_target["remote"],
        timeout=min(DEFAULT_TIMEOUT, max(1, timeout)))
    if (binding is None
            or binding[1] != validated_target["remote_fingerprint"]):
        return ReconcileResult(
            ok=False, action="pending-target-invalid",
            detail="stored pending remote binding changed",
            final_identity=current)
    target = _PublicationTarget(
        remote=validated_target["remote"],
        destination=validated_target["destination"],
        reconcile_ref=validated_target["reconcile_ref"],
        set_upstream=validated_target["set_upstream"],
        remote_fingerprint=validated_target["remote_fingerprint"],
        push_endpoint=binding[0],
    )
    if not _pending_head_covered_by_history(
            team_root, pending_identity["head"], current["head"],
            timeout=min(_PENDING_IDENTITY_TIMEOUT, max(1, timeout)),
            deadline=deadline):
        return ReconcileResult(
            ok=False, action="pending-history-changed",
            detail=("recorded pending head is neither an ancestor nor a proven "
                    "patch-equivalent part of current HEAD"),
            final_identity=current)

    guard = (snapshot_content, target_key, validated_target)
    return do_reconcile(
        team_root, timeout=timeout, deadline=deadline,
        expected_identity=current, _target=target,
        _allow_bound_mutation=True,
        _preserve_pending_ancestry=True, _pending_guard=guard)


def write_push_pending(
        team_root: str, identity: dict | None = None,
        *, target=_PENDING_TARGET_UNSET) -> bool:
    """현재 checkout pending entry를 원자 upsert한다. 다른 branch entry는 보존. 무raise.

    branch/detached HEAD binding은 worker가 다른 branch를 성공으로 오판해 ledger를
    지우는 것을 막는다. nonce는 같은 checkout 재기록의 compare-and-delete 판별자다.
    반환: 기록 성공 여부(codex P1 — 실패를 호출부가 모르면 "커밋됨·push 안 됨·
    pending 없음·마커 없음" 무음 유실 상태가 된다. 호출부는 False 에 fallback 가시화).
    """
    identity = _validated_pending_identity(team_root, identity)
    if identity is None:
        return False
    if target is _PENDING_TARGET_UNSET:
        resolved_target = None
        if identity.get("branch") and identity.get("head"):
            resolved_target, _ = _resolve_publication_target(
                team_root, identity, timeout=DEFAULT_TIMEOUT)
        target_payload = _validated_pending_target(
            team_root, resolved_target, _PENDING_IDENTITY_TIMEOUT)
    elif target is None:
        target_payload = None
    else:
        target_payload = _validated_pending_target(
            team_root, target, _PENDING_IDENTITY_TIMEOUT)
        if target_payload is None:
            return False
    pending_path = push_pending_path(team_root)
    target_keys = (
        "remote", "destination", "reconcile_ref", "set_upstream",
        "remote_fingerprint")

    def _new_entry() -> dict:
        entry = {
            "branch": identity["branch"],
            "head": identity["head"],
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "nonce": os.urandom(8).hex(),
        }
        if target_payload is not None:
            entry.update(target_payload)
        return entry

    try:
        with _push_pending_ledger_lock(team_root) as locked:
            if not locked:
                return False
            current = _read_private_text(push_pending_path(team_root))
            if not current.available:
                return False
            entries = _pending_entries(current.content)
            if current.content and not entries:
                # v1/legacy ledger는 branch가 없어 자동 처리할 수 없다. 새 entry를
                # 추가하되 legacy 자체도 보존해 업그레이드 중 retry state를 잃지 않는다.
                legacy_key = _legacy_pending_key(current.content)
                if not legacy_key:
                    return False
                entries[legacy_key] = {
                    "branch": "", "head": "", "legacy": True,
                    "written_at": datetime.now().isoformat(timespec="seconds"),
                    "nonce": legacy_key.removeprefix("legacy:"),
                }
            existing = entries.get(identity["key"])
            if isinstance(existing, dict):
                existing_head = str(existing.get("head") or "").lower()
                existing_target = {
                    key: existing.get(key) for key in target_keys if key in existing
                }
                if existing_head and existing_head != identity["head"]:
                    snapshot_content = current.content
                    snapshot_entry = dict(existing)
                elif (existing_head == identity["head"] and existing_target
                      and existing_target != target_payload):
                    return False
                else:
                    existing = None
            if not isinstance(existing, dict):
                entries[identity["key"]] = _new_entry()
                return _write_private_text(
                    pending_path, _serialize_pending_entries(team_root, entries))
    except (OSError, ValueError):
        return False  # ledger 기록 실패는 커밋을 막지 않는다 — 가시화는 호출부 몫

    # Probe outside the state lock, then require an exact ledger + entry CAS.
    old_identity = _validated_pending_identity(team_root, {
        "key": identity["key"],
        "branch": str(snapshot_entry.get("branch") or ""),
        "head": str(snapshot_entry.get("head") or ""),
    })
    if old_identity is None or old_identity.get("key") != identity["key"]:
        return False
    existing_target = {
        key: snapshot_entry.get(key) for key in target_keys
        if key in snapshot_entry
    }
    targets_match = (not existing_target or existing_target == target_payload)
    new_in_old = _pending_head_ancestry(
        team_root, identity["head"], old_identity["head"])
    if new_in_old is True:
        if not targets_match:
            return False
        preserve_existing = True
    elif new_in_old is not False:
        return False
    else:
        old_in_new = _pending_head_ancestry(
            team_root, old_identity["head"], identity["head"])
        if old_in_new is True:
            if not targets_match:
                return False
        elif old_in_new is False:
            stored_target = _validated_pending_target(team_root, snapshot_entry)
            if stored_target is None or stored_target != target_payload:
                return False
        else:
            return False
        if not _pending_head_covered_by_history(
                team_root, old_identity["head"], identity["head"]):
            return False
        preserve_existing = False

    try:
        with _push_pending_ledger_lock(team_root) as locked:
            if not locked:
                return False
            current = _read_private_text(pending_path)
            if (not current.available
                    or current.content != snapshot_content):
                return False
            entries = _pending_entries(current.content)
            current_entry = entries.get(identity["key"])
            if (not isinstance(current_entry, dict)
                    or current_entry != snapshot_entry):
                return False
            if preserve_existing:
                return True
            entries[identity["key"]] = _new_entry()
            return _write_private_text(
                pending_path, _serialize_pending_entries(team_root, entries))
    except (OSError, ValueError):
        return False


def read_push_pending_state(team_root: str) -> PushPendingRead:
    """pending 내용과 ledger 가용성을 분리해 반환한다. 무raise."""
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return PushPendingRead(available=False)
        return _read_private_text(push_pending_path(team_root))


def read_push_pending(team_root: str) -> str:
    """호환용 content-only reader. lock 불가도 빈 문자열이므로 삭제 판정에 쓰지 않는다."""
    return read_push_pending_state(team_root).content


def push_pending_entry(
        team_root: str, snapshot_content: str, target_key: str,
        timeout: int = NET_TIMEOUT) -> tuple[bool, str]:
    """Publish one ledger entry by its stored immutable OID and exact target.

    No current branch, upstream, or later Git config participates.  A target-less
    legacy entry is preserved for manual/session recovery rather than guessed.
    """
    entry = _pending_entries(snapshot_content).get(target_key)
    if not isinstance(entry, dict):
        return False, "pending entry unavailable"
    identity = _validated_pending_identity(
        team_root, {
            "key": target_key,
            "branch": str(entry.get("branch") or ""),
            "head": str(entry.get("head") or ""),
        })
    if identity is None:
        return False, "pending identity unavailable"
    target = _validated_pending_target(
        team_root, entry, verify_remote_binding=False)
    if target is None:
        return False, "pending publication target unavailable"
    binding = _remote_push_binding(team_root, target["remote"], timeout)
    if binding is None or binding[1] != target["remote_fingerprint"]:
        return False, "pending remote binding changed"
    endpoint, _fingerprint = binding
    try:
        rc, out, err, tracking_detail = _run_exact_publication_push(
            team_root, endpoint, target["destination"],
            target["reconcile_ref"], identity["head"], timeout)
    except subprocess.TimeoutExpired:
        return False, "pending push timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"pending push exec error: {exc}"
    if rc == 0:
        detail = "pushed pending immutable head"
        if tracking_detail:
            detail += f"; tracking update skipped: {tracking_detail}"
        if target["set_upstream"]:
            stored_target = _PublicationTarget(
                remote=target["remote"],
                destination=target["destination"],
                reconcile_ref=target["reconcile_ref"],
                set_upstream=True,
                remote_fingerprint=target["remote_fingerprint"],
                push_endpoint=endpoint,
            )
            setup_ok, setup_detail = _set_publication_upstream(
                team_root, identity, stored_target,
                timeout=min(DEFAULT_TIMEOUT, max(1, timeout)))
            if setup_ok:
                return True, f"{detail} (set upstream)"
            # The immutable commit is already durable at the exact destination.
            # Never turn a local config race into a duplicate publication retry.
            return True, f"{detail}; upstream setup skipped: {setup_detail}"
        return True, detail
    combined = (err or "") + "\n" + (out or "")
    if _is_non_fast_forward(combined):
        return False, "non-fast-forward"
    return False, f"pending push failed: {combined.strip()[:200]}"


def _clear_push_pending_if_unchanged_locked(
        team_root: str, snapshot_content: str, target_key: str | None = None) -> bool:
    """스냅샷이 그대로일 때 지정 checkout entry만 clear한다.

    worker 가 push 성공 → ahead==0 확인 → clear 직전에 auto-commit 이 새 커밋의
    pending 을 재기록하면, 무조건 clear 는 그 새 pending 을 삼켜 "ahead 인데 pending
    없음" 유실 상태를 만든다. branch별 entry를 두어 다른 checkout의 retry state도
    보존하고, 전체 파일 내용(nonce 포함)이 스냅샷과 같을 때만 대상 entry를 지운다.
    짧은 ledger OS lock 안에서 compare+remove 를 한 임계구역으로 묶어, 비교 직후
    writer 가 새 nonce 를 replace 한 뒤 old clear 가 삭제하는 TOCTOU 를 막는다.
    target_key를 생략하면 호출 시점의 현재 checkout entry를 대상으로 한다.
    """
    if not snapshot_content:
        return False
    key = target_key or _checkout_identity(team_root)["key"]
    path = push_pending_path(team_root)
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        current = _read_private_text(path)
        if not current.available or current.content != snapshot_content.strip():
            return False
        entries = _pending_entries(current.content)
        if not entries:
            legacy_key = _legacy_pending_key(current.content)
            if key == legacy_key and legacy_key:
                return _remove_private_file(path)
            return False
        if key not in entries:
            return False
        del entries[key]
        if not entries:
            return _remove_private_file(path)
        return _write_private_text(path, _serialize_pending_entries(team_root, entries))


def clear_push_pending_if_unchanged(
        team_root: str, snapshot_content: str,
        target_key: str | None = None) -> bool:
    """Interlocked public CAS clear; blockers preserve the pending ledger."""
    with _publication_interlock(team_root, 1) as (acquired, _detail):
        if not acquired or publication_blocker_detail(team_root, 1):
            return False
        return _clear_push_pending_if_unchanged_locked(
            team_root, snapshot_content, target_key)


def _clear_sync_warning_if_fully_published_locked(
        team_root: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """origin publication 과 empty pending 을 함께 입증한 경우에만 warning 을 지운다.

    ahead 판정은 네트워크/하위 프로세스가 없어도 ledger lock 밖에서 수행한다. 이후
    pending 확인과 warning clear 를 같은 ledger 임계구역에 묶는다. 실패 경로가
    ``pending 기록 -> warning 기록`` 순서를 지키면 새 실패 warning 을 성공 경로가
    지우는 경합이 없다.
    """
    warning_before = _read_private_text(sync_warning_path(team_root))
    if not warning_before.available:
        return False
    ahead, _behind, has_upstream = _ahead_behind_raw(team_root, timeout)
    if not has_upstream or ahead != 0:
        return False
    with _push_pending_ledger_lock(team_root) as locked:
        if not locked:
            return False
        pending = _read_private_text(push_pending_path(team_root))
        if not pending.available or pending.content:
            return False
        warning_now = _read_private_text(sync_warning_path(team_root))
        if (not warning_now.available
                or warning_now.fingerprint != warning_before.fingerprint
                or warning_now.content != warning_before.content):
            return False
        return _remove_private_file(sync_warning_path(team_root))


def clear_sync_warning_if_fully_published(
        team_root: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Interlocked public warning clear; blockers preserve diagnostic state."""
    lock_timeout = max(0.05, min(float(timeout), 1.0))
    with _publication_interlock(
            team_root, lock_timeout) as (acquired, _detail):
        if (not acquired
                or publication_blocker_detail(team_root, lock_timeout)):
            return False
        return _clear_sync_warning_if_fully_published_locked(
            team_root, timeout)


def clear_sync_warning_after_exact_publication(
        team_root: str, identity: dict | None, target: dict | None,
        timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Clear a warning only when this exact target/OID is locally proven durable.

    ``@{u}`` is the wrong proof for triangular workflows (pull from origin,
    publish to fork).  Exact publication advances the captured destination's
    tracking ref, so that ref plus an empty pending ledger is the target-aware
    success proof.  Warning content is compare-and-delete guarded to avoid
    erasing a newer concurrent failure.
    """
    lock_timeout = max(0.05, min(float(timeout), 1.0))
    with _publication_interlock(
            team_root, lock_timeout) as (acquired, _detail):
        if (not acquired
                or publication_blocker_detail(team_root, lock_timeout)):
            return False
        published = _validated_pending_identity(team_root, identity)
        destination = _validated_pending_target(
            team_root, target, timeout=min(DEFAULT_TIMEOUT, max(1, timeout)))
        if published is None or destination is None:
            return False
        available, tracked = _read_ref_oid(
            team_root, destination["reconcile_ref"],
            timeout=min(DEFAULT_TIMEOUT, max(1, timeout)))
        if (not available
                or tracked.lower() != published["head"].lower()):
            return False
        warning_before = _read_private_text(sync_warning_path(team_root))
        if not warning_before.available:
            return False
        with _push_pending_ledger_lock(team_root) as locked:
            if not locked:
                return False
            pending = _read_private_text(push_pending_path(team_root))
            if not pending.available or pending.content:
                return False
            warning_now = _read_private_text(sync_warning_path(team_root))
            if (not warning_now.available
                    or warning_now.fingerprint != warning_before.fingerprint
                    or warning_now.content != warning_before.content):
                return False
            return _remove_private_file(sync_warning_path(team_root))


def clear_sync_warning_after_pending_publication(
        team_root: str, snapshot_content: str, target_key: str,
        timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Target-aware warning cleanup for a just-CAS-cleared worker snapshot."""
    entry = _pending_entries(snapshot_content).get(target_key)
    if not isinstance(entry, dict):
        return False
    identity = {
        "key": target_key,
        "branch": str(entry.get("branch") or ""),
        "head": str(entry.get("head") or ""),
    }
    return clear_sync_warning_after_exact_publication(
        team_root, identity, entry, timeout=timeout)


def push_pending_age_seconds(team_root: str):
    """pending 마커 나이(초). 없으면 None. UserPromptSubmit 초경량 검사용 —
    state dir + marker lstat 만 수행한다(장수 세션에서 매 발화 비용 최소화)."""
    try:
        state_dir = os.lstat(_state_dir())
        if (not stat.S_ISDIR(state_dir.st_mode) or stat.S_ISLNK(state_dir.st_mode)
                or os.path.islink(_state_dir())
                or getattr(os.path, "isjunction", lambda _path: False)(_state_dir())
                or (hasattr(os, "getuid") and state_dir.st_uid != os.getuid())):
            return None
        st = os.lstat(push_pending_path(team_root))
        if not _owned_regular(st):
            return None
        return max(0.0, float(time.time() - st.st_mtime))
    except OSError:
        return None


def _owned_directory(path: Path) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(current.st_mode) and not stat.S_ISLNK(current.st_mode)
            and (not hasattr(os, "getuid") or current.st_uid == os.getuid()))


def _git_common_admin_dirs(
        team_root: str, timeout: float
        ) -> tuple[Path | None, tuple[Path, ...], str]:
    """Resolve and owner-validate the common dir plus every worktree admin."""
    try:
        rc, out, err = run_git(
            ["-C", team_root, "rev-parse", "--git-common-dir"],
            timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, (), f"Git common directory unavailable: {exc}"
    raw_common = (out or "").rstrip("\n")
    if rc != 0 or not raw_common or "\n" in raw_common or "\0" in raw_common:
        failure = (err or "Git common directory unavailable").strip()[:200]
        return None, (), failure
    candidate = Path(raw_common)
    common_dir = candidate if candidate.is_absolute() else Path(team_root) / candidate
    common_dir = Path(os.path.abspath(common_dir))
    if not _owned_directory(common_dir):
        return None, (), "Git common directory is not an owned safe directory"

    admins: list[Path] = [common_dir]
    worktrees_dir = common_dir / "worktrees"
    try:
        worktrees_stat = os.lstat(worktrees_dir)
    except FileNotFoundError:
        return common_dir, tuple(admins), ""
    except OSError as exc:
        return None, (), f"linked-worktree admin probe unavailable: {exc}"
    if (not stat.S_ISDIR(worktrees_stat.st_mode)
            or stat.S_ISLNK(worktrees_stat.st_mode)
            or (hasattr(os, "getuid") and worktrees_stat.st_uid != os.getuid())):
        return None, (), "linked-worktree admin root is unsafe"
    try:
        with os.scandir(worktrees_dir) as entries:
            for entry in entries:
                entry_path = worktrees_dir / entry.name
                entry_stat = entry.stat(follow_symlinks=False)
                if (not stat.S_ISDIR(entry_stat.st_mode)
                        or stat.S_ISLNK(entry_stat.st_mode)
                        or (hasattr(os, "getuid")
                            and entry_stat.st_uid != os.getuid())):
                    return None, (), "linked-worktree admin entry is unsafe"
                admins.append(entry_path)
    except OSError as exc:
        return None, (), f"linked-worktree admin scan unavailable: {exc}"
    return common_dir, tuple(admins), ""


@contextmanager
def _publication_interlock(team_root: str, timeout: float = 1.0):
    """Crash-safe common-repo advisory lock for reconcile/push/clear."""
    timeout = max(0.05, min(float(timeout), 1.0))
    common_dir, _admins, layout_detail = _git_common_admin_dirs(
        team_root, timeout)
    if common_dir is None:
        yield False, f"publication interlock unavailable: {layout_detail}"
        return
    lock_path = common_dir / ".tm-mode-publication.lock"
    handle = None
    acquired = False
    unlock = None
    detail = "publication interlock unavailable"
    deadline = time.monotonic() + timeout
    try:
        try:
            lock_fd = _secure_open_regular(
                str(lock_path), os.O_RDWR | os.O_CREAT)
            handle = os.fdopen(lock_fd, "r+b", buffering=0)
            if os.name == "nt":  # pragma: no cover - Windows CI unavailable
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
                    opened = os.fstat(handle.fileno())
                    current = os.lstat(lock_path)
                    if ((opened.st_dev, opened.st_ino)
                            != (current.st_dev, current.st_ino)
                            or not _owned_regular(current)):
                        detail = "publication interlock path identity changed"
                        break
                    acquired = True
                    detail = ""
                    break
                except OSError as exc:
                    if exc.errno not in _LOCK_CONTENTION_ERRNOS:
                        detail = f"publication interlock failed: {exc}"
                        break
                    if time.monotonic() >= deadline:
                        detail = "publication interlock contention"
                        break
                    time.sleep(_PUSH_PENDING_LOCK_POLL_SECONDS)
        except (OSError, ImportError) as exc:
            detail = f"publication interlock unavailable: {exc}"
        yield acquired, detail
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


_HOOK_LEASE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_EDIT_LEASE_PREFIX = "lease-"
_HOOK_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_EDIT_LEASE_TMP_RE = re.compile(
    r"^\.lease-[0-9a-f]{64}\.[A-Za-z0-9_-]{6,64}\.tmp$")
_HOOK_RUNTIME_UNSET = object()


def hook_edit_lease_owner(data: dict | None) -> str:
    """Return an opaque exact Pre/Post tool-call owner, or empty fail-closed."""
    if not isinstance(data, dict):
        return ""
    # The normalized payload is the correlation source.  A leaked/stale
    # CLAUDE_SESSION_ID in a Codex environment must never alias another runtime.
    session_id = str(data.get("session_id") or "").strip()
    tool_use_id = str(data.get("tool_use_id") or "").strip()
    agent = str(data.get("agent") or "").strip().lower()
    if (agent not in {"claude", "codex"}
            or not _HOOK_LEASE_ID_RE.fullmatch(session_id)
            or not _HOOK_LEASE_ID_RE.fullmatch(tool_use_id)):
        return ""
    material = f"{agent}\0{session_id}\0{tool_use_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def hook_edit_lease_scope(data: dict | None) -> str:
    """Return an opaque runtime turn/subagent scope for terminal cleanup.

    Stop is not a process/session exclusivity proof.  Requiring turn_id and
    including agent_id prevents a concurrently resumed thread or background
    subagent from clearing another active editor that shares session_id.
    """
    if not isinstance(data, dict):
        return ""
    agent = str(data.get("agent") or "").strip().lower()
    session_id = str(data.get("session_id") or "").strip()
    turn_id = str(data.get("turn_id") or "").strip()
    agent_id = str(data.get("agent_id") or "").strip()
    if (agent not in {"claude", "codex"}
            or not _HOOK_LEASE_ID_RE.fullmatch(session_id)
            or (agent_id and not _HOOK_LEASE_ID_RE.fullmatch(agent_id))):
        return ""
    if agent == "codex" and not _HOOK_LEASE_ID_RE.fullmatch(turn_id):
        return ""
    # Claude command-hook payloads do not expose turn_id.  Its verified runtime
    # identity separates concurrent resume processes, while agent_id separates
    # root and background subagent editors inside the same runtime/session.
    scoped_turn = turn_id if agent == "codex" else ""
    material = (
        f"{agent}\0{session_id}\0{scoped_turn}\0{agent_id}".encode("utf-8"))
    return hashlib.sha256(material).hexdigest()


def _windows_process_identity(pid: int) -> dict | bool | None:
    """Windows PID, parent and creation token via kernel APIs."""
    try:  # pragma: no cover - exercised on Windows CI/hosts
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ULONG_PTR = wintypes.WPARAM

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ULONG_PTR),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid = ctypes.c_void_p(-1).value
        if snapshot in (None, 0, invalid):
            return None
        parent = None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            found = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while found:
                if int(entry.th32ProcessID) == pid:
                    parent = int(entry.th32ParentProcessID)
                    break
                found = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
        if parent is None:
            return False

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            return False if error == 87 else None  # ERROR_INVALID_PARAMETER
        try:
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                return None
            executable_path = os.path.realpath(buffer.value)
            started = str(
                (int(created.dwHighDateTime) << 32)
                | int(created.dwLowDateTime))
        finally:
            kernel32.CloseHandle(handle)
        return {
            "pid": pid,
            "parent": parent,
            "started": started,
            "executable": hashlib.sha256(
                executable_path.encode("utf-8", "surrogateescape")).hexdigest(),
            "path": executable_path,
        }
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _process_identity(pid: int) -> dict | bool | None:
    """Read PID reuse-safe process identity; False=definitely gone, None=unknown."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    if os.name == "nt":
        return _windows_process_identity(pid)
    if sys.platform.startswith("linux"):
        proc = Path(f"/proc/{pid}")
        try:
            raw = (proc / "stat").read_text(encoding="utf-8")
            close = raw.rfind(")")
            if close < 0:
                return None
            fields = raw[close + 2:].split()
            if len(fields) < 20:
                return None
            parent = int(fields[1])
            started = fields[19]
            executable_path = os.path.realpath(os.readlink(proc / "exe"))
        except FileNotFoundError:
            return False
        except (OSError, UnicodeError, ValueError):
            return None
    elif os.name == "posix":
        try:
            probe = subprocess.run(
                ["/bin/ps", "-ww", "-p", str(pid), "-o", "pid=", "-o",
                 "ppid=", "-o", "lstart=", "-o", "comm="],
                capture_output=True, text=True, timeout=0.2, check=False,
                env={**os.environ, "LC_ALL": "C", "LANG": "C", "TZ": "UTC"})
        except (OSError, subprocess.SubprocessError):
            return None
        row = probe.stdout.strip()
        if probe.returncode != 0 or not row:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except (OSError, PermissionError):
                return None
            return None
        parts = row.split(maxsplit=7)
        if len(parts) != 8:
            return None
        try:
            parent = int(parts[1])
        except ValueError:
            return None
        started = " ".join(parts[2:7])
        executable_path = os.path.realpath(parts[7])
    else:
        return None
    return {
        "pid": pid,
        "parent": parent,
        "started": started,
        "executable": hashlib.sha256(
            executable_path.encode("utf-8", "surrogateescape")).hexdigest(),
        "path": executable_path,
    }


def _runtime_executable_matches(agent: str, executable_path: str) -> bool:
    normalized = executable_path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    if agent == "codex":
        return name in {"codex", "codex.exe"}
    if agent == "claude":
        return (name in {"claude", "claude.exe"}
                or ("/claude/versions/" in normalized
                    and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:\.exe)?", name))))
    return False


def _current_hook_runtime_identity(agent: str) -> dict | None:
    """Find the nearest verified Codex/Claude host ancestor of this hook."""
    if agent not in {"claude", "codex"}:
        return None
    pid = os.getppid()
    seen: set[int] = set()
    for _ in range(16):
        if pid <= 1 or pid in seen:
            return None
        seen.add(pid)
        current = _process_identity(pid)
        if not isinstance(current, dict):
            return None
        if _runtime_executable_matches(agent, str(current.get("path") or "")):
            return {
                "pid": current["pid"],
                "started": current["started"],
                "executable": current["executable"],
            }
        pid = int(current.get("parent") or 0)
    return None


def _validated_hook_runtime(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    pid = value.get("pid")
    started = value.get("started")
    executable = value.get("executable")
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1
            or not isinstance(started, str) or not started
            or len(started) > 160
            or not isinstance(executable, str)
            or not _HOOK_DIGEST_RE.fullmatch(executable)):
        return None
    return {"pid": pid, "started": started, "executable": executable}


def _hook_runtime_liveness(runtime: dict) -> bool | None:
    """True=exact runtime alive, False=dead/PID reused, None=unprovable."""
    expected = _validated_hook_runtime(runtime)
    if expected is None:
        return None
    current = _process_identity(expected["pid"])
    if current is False:
        return False
    if not isinstance(current, dict):
        return None
    if current.get("started") != expected["started"]:
        return False
    if current.get("executable") != expected["executable"]:
        # Same PID birth token is the liveness proof.  Executable path can
        # legitimately drift during an in-place package upgrade (`(deleted)`
        # on Linux); never turn that auxiliary mismatch into active deletion.
        return None
    return True


def hook_edit_lease_metadata(
        data: dict | None, *, _runtime_identity=_HOOK_RUNTIME_UNSET) -> dict | None:
    """Build bounded marker metadata without storing raw session/turn IDs."""
    owner = hook_edit_lease_owner(data)
    if not owner or not isinstance(data, dict):
        return None
    agent = str(data.get("agent") or "").strip().lower()
    runtime = (_current_hook_runtime_identity(agent)
               if _runtime_identity is _HOOK_RUNTIME_UNSET
               else _runtime_identity)
    return {
        "version": 2,
        "owner": owner,
        "scope": hook_edit_lease_scope(data),
        "runtime": _validated_hook_runtime(runtime),
    }


@contextmanager
def _edit_gate(team_root: str, timeout: float = 0.2):
    """Short common-repo lock serializing Pre markers with local mutation.

    This is deliberately separate from the publication interlock: network push
    may hold that lock for seconds, but it does not touch the worktree and must
    not deny an otherwise safe file edit.
    """
    timeout = max(0.02, min(float(timeout), 0.5))
    common_dir, _admins, layout_detail = _git_common_admin_dirs(
        team_root, timeout)
    if common_dir is None:
        yield False, f"edit gate unavailable: {layout_detail}"
        return
    lock_path = common_dir / ".tm-mode-edit-gate.lock"
    handle = None
    acquired = False
    unlock = None
    detail = "edit gate unavailable"
    deadline = time.monotonic() + timeout
    try:
        try:
            lock_fd = _secure_open_regular(
                str(lock_path), os.O_RDWR | os.O_CREAT)
            handle = os.fdopen(lock_fd, "r+b", buffering=0)
            if os.name == "nt":  # pragma: no cover - Windows CI unavailable
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
                    opened = os.fstat(handle.fileno())
                    current = os.lstat(lock_path)
                    if ((opened.st_dev, opened.st_ino)
                            != (current.st_dev, current.st_ino)
                            or not _owned_regular(current)):
                        detail = "edit gate path identity changed"
                        break
                    acquired, detail = True, ""
                    break
                except OSError as exc:
                    if exc.errno not in _LOCK_CONTENTION_ERRNOS:
                        detail = f"edit gate failed: {exc}"
                        break
                    if time.monotonic() >= deadline:
                        detail = "edit gate contention"
                        break
                    time.sleep(_PUSH_PENDING_LOCK_POLL_SECONDS)
        except (OSError, ImportError) as exc:
            detail = f"edit gate unavailable: {exc}"
        yield acquired, detail
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


def _edit_lease_dir(team_root: str, timeout: float = 0.2) -> Path | None:
    common_dir, _admins, _detail = _git_common_admin_dirs(team_root, timeout)
    if common_dir is None:
        return None
    path = common_dir / ".tm-mode-edit-leases"
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        current = os.lstat(path)
        if (not stat.S_ISDIR(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (hasattr(os, "getuid") and current.st_uid != os.getuid())):
            return None
        try:
            os.chmod(path, 0o700)
        except OSError:
            if os.name != "nt":
                return None
        return path
    except OSError:
        return None


def _validated_edit_lease_metadata(
        value: object, expected_owner: str = "") -> dict | None:
    if not isinstance(value, dict) or value.get("version") != 2:
        return None
    owner = value.get("owner")
    scope = value.get("scope")
    runtime_raw = value.get("runtime")
    if (not isinstance(owner, str) or not _HOOK_DIGEST_RE.fullmatch(owner)
            or (expected_owner and owner != expected_owner)
            or not isinstance(scope, str)
            or (scope and not _HOOK_DIGEST_RE.fullmatch(scope))):
        return None
    runtime = None
    if runtime_raw is not None:
        runtime = _validated_hook_runtime(runtime_raw)
        if runtime is None:
            return None
    return {
        "version": 2,
        "owner": owner,
        "scope": scope,
        "runtime": runtime,
    }


def _read_edit_lease_marker(path: Path, expected_owner: str) -> dict | None:
    try:
        fd = _secure_open_regular(
            str(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            return None
        return _validated_edit_lease_metadata(
            json.loads(raw), expected_owner=expected_owner)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _write_edit_lease_marker(path: Path, metadata: dict) -> bool:
    """Atomic, owner-only marker write in the Git common directory."""
    directory = path.parent
    tmp = ""
    fd = -1
    try:
        existing = None
        try:
            existing = os.lstat(path)
        except FileNotFoundError:
            pass
        if existing is not None and not _owned_regular(existing):
            return False
        fd, tmp = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(directory))
        current = os.fstat(fd)
        if not _owned_regular(current):
            return False
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        payload = json.dumps(
            metadata, ensure_ascii=True, sort_keys=True,
            separators=(",", ":")) + "\n"
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = ""
        return _fsync_parent_dir(str(path))
    except (OSError, TypeError, UnicodeError, ValueError):
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _active_edit_lease_owners_locked(
        team_root: str) -> set[str] | None:
    """Read exact tool-call markers while caller holds ``_edit_gate``.

    Age alone is not proof that a file tool stopped writing, so markers are
    never auto-deleted here.  A killed Post hook fails closed by keeping local
    history mutation disabled until the exact cleanup is performed.
    """
    directory = _edit_lease_dir(team_root)
    if directory is None:
        return None
    owners: set[str] = set()
    pruned_temp = False
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if _EDIT_LEASE_TMP_RE.fullmatch(entry.name):
                    # Writers hold _edit_gate for the entire atomic replace, so
                    # a matching temp observed while we hold the same gate can
                    # only be crash residue.  Prune only an owned regular file;
                    # everything else remains fail-closed.
                    current = entry.stat(follow_symlinks=False)
                    if not _owned_regular(current):
                        return None
                    try:
                        os.unlink(entry.path)
                        pruned_temp = True
                    except FileNotFoundError:
                        pass
                    except OSError:
                        return None
                    continue
                if not entry.name.startswith(_EDIT_LEASE_PREFIX):
                    return None
                owner = entry.name[len(_EDIT_LEASE_PREFIX):]
                if not re.fullmatch(r"[0-9a-f]{64}", owner):
                    return None
                current = entry.stat(follow_symlinks=False)
                if not _owned_regular(current):
                    return None
                marker = _read_edit_lease_marker(Path(entry.path), owner)
                if marker is None:
                    return None
                runtime = marker.get("runtime")
                if isinstance(runtime, dict):
                    alive = _hook_runtime_liveness(runtime)
                    if alive is False:
                        try:
                            os.unlink(entry.path)
                        except FileNotFoundError:
                            pass
                        except OSError:
                            return None
                        continue
                owners.add(owner)
        if (pruned_temp
                and not _fsync_parent_dir(str(directory / ".pruned"))):
            return None
        return owners
    except OSError:
        return None


def begin_hook_edit_lease(
        team_root: str, owner: str, timeout: float = 0.2,
        *, metadata: dict | None = None) -> tuple[bool, str]:
    """Register one exact PreToolUse edit before the file tool may run."""
    if not _HOOK_DIGEST_RE.fullmatch(owner or ""):
        return False, "missing exact session/tool identity"
    marker_metadata = _validated_edit_lease_metadata(
        metadata or {
            "version": 2, "owner": owner, "scope": "", "runtime": None,
        }, expected_owner=owner)
    if marker_metadata is None:
        return False, "invalid edit lease metadata"
    with _edit_gate(team_root, timeout) as (acquired, detail):
        if not acquired:
            return False, detail
        owners = _active_edit_lease_owners_locked(team_root)
        directory = _edit_lease_dir(team_root)
        if owners is None or directory is None:
            return False, "edit lease state unavailable"
        marker = directory / f"{_EDIT_LEASE_PREFIX}{owner}"
        try:
            if not _write_edit_lease_marker(marker, marker_metadata):
                return False, "edit lease write failed"
            return True, ""
        except OSError as exc:
            return False, f"edit lease write failed: {exc}"


def end_hook_edit_lease(
        team_root: str, owner: str, timeout: float = 0.2) -> bool:
    """Release only the matching PostToolUse marker; never glob by session."""
    if not re.fullmatch(r"[0-9a-f]{64}", owner or ""):
        return False
    with _edit_gate(team_root, timeout) as (acquired, _detail):
        if not acquired:
            return False
        directory = _edit_lease_dir(team_root)
        if directory is None:
            return False
        marker = directory / f"{_EDIT_LEASE_PREFIX}{owner}"
        try:
            current = os.lstat(marker)
            if not _owned_regular(current):
                return False
            os.unlink(marker)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False


def end_hook_edit_leases_for_scope(
        team_root: str, scope: str, runtime: dict,
        timeout: float = 0.2) -> int:
    """Release only markers owned by this exact turn/subagent runtime.

    A shared session id is intentionally insufficient: concurrent resume
    processes and background subagents can share it while still editing.
    """
    expected_runtime = _validated_hook_runtime(runtime)
    if (not _HOOK_DIGEST_RE.fullmatch(scope or "")
            or expected_runtime is None):
        return 0
    with _edit_gate(team_root, timeout) as (acquired, _detail):
        if not acquired:
            return 0
        directory = _edit_lease_dir(team_root)
        if directory is None:
            return 0
        removed = 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not entry.name.startswith(_EDIT_LEASE_PREFIX):
                        return removed
                    owner = entry.name[len(_EDIT_LEASE_PREFIX):]
                    if not _HOOK_DIGEST_RE.fullmatch(owner):
                        return removed
                    marker = _read_edit_lease_marker(Path(entry.path), owner)
                    if marker is None:
                        continue
                    if (marker.get("scope") != scope
                            or marker.get("runtime") != expected_runtime):
                        continue
                    try:
                        os.unlink(entry.path)
                        removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError:
                        continue
            return removed
        except OSError:
            return removed


def publication_blocker_detail(
        team_root: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Return a fail-closed reason while bound-reconcile residue exists.

    Every publication path and stale-ledger clear shares this probe.  A probe
    failure is itself a blocker: treating an unreadable Git admin area as clean
    could publish or clear the only recovery signal while a transaction is
    unresolved.
    """
    timeout = max(0.1, float(timeout))
    deadline = time.monotonic() + timeout
    common_dir, admin_dirs, layout_detail = _git_common_admin_dirs(
        team_root, timeout)
    if common_dir is None:
        return f"reconcile blocker probe unavailable: {layout_detail}"
    for admin_dir in admin_dirs:
        index_path = admin_dir / "index"
        for label, path in (
                ("canonical index lock", Path(f"{index_path}.lock")),
                ("rebase-merge", admin_dir / "rebase-merge"),
                ("rebase-apply", admin_dir / "rebase-apply"),
                ("merge head", admin_dir / "MERGE_HEAD"),
                ("merge message", admin_dir / "MERGE_MSG"),
                ("merge mode", admin_dir / "MERGE_MODE"),
                ("merge autostash", admin_dir / "MERGE_AUTOSTASH"),
                ("auto merge", admin_dir / "AUTO_MERGE")):
            try:
                os.lstat(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                return f"reconcile blocker probe unavailable ({label}): {exc}"
            else:
                return f"unresolved reconcile blocker: {label}"
        try:
            with os.scandir(admin_dir) as entries:
                for entry in entries:
                    if entry.name.startswith(".tm-mode-reconcile-"):
                        return "unresolved reconcile blocker: transaction directory"
        except OSError as exc:
            return f"reconcile blocker probe unavailable (admin directory): {exc}"

    refs_timeout = deadline - time.monotonic()
    if refs_timeout <= 0:
        return "reconcile blocker probe unavailable: budget exhausted"
    try:
        rc, refs_out, refs_err = run_git(
            ["-C", team_root, "for-each-ref", "--format=%(refname)",
             "refs/tm-mode/reconcile"], timeout=refs_timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"reconcile blocker probe unavailable (recovery refs): {exc}"
    if rc != 0:
        failure = (refs_err or "recovery ref probe failed").strip()[:200]
        return f"reconcile blocker probe unavailable: {failure}"
    if any(line.strip() for line in (refs_out or "").splitlines()):
        return "unresolved reconcile blocker: recovery refs"
    return ""


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
    if publication_blocker_detail(team_root):
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


def _run_publication_push_locked(
        args: list[str], timeout: int) -> tuple[int, str, str]:
    """The single raw network-push callsite; caller owns common interlock."""
    return run_git(args, timeout=timeout)


def _run_publication_push(
        team_root: str, args: list[str], timeout: int,
        *, lock_timeout: float | None = None) -> tuple[int, str, str]:
    """Serialize one network push and re-probe all repo recovery blockers."""
    wait = (max(0.05, min(float(timeout), 1.0))
            if lock_timeout is None else lock_timeout)
    with _publication_interlock(team_root, wait) as (acquired, detail):
        if not acquired:
            return 1, "", detail
        blocker = publication_blocker_detail(team_root, wait)
        if blocker:
            return 1, "", blocker
        return _run_publication_push_locked(args, timeout)


def _advance_tracking_ref_after_exact_push_locked(
        team_root: str, tracking_ref: str, head: str,
        previous: str, timeout: int) -> tuple[bool, str]:
    """CAS the local tracking ref to the commit just accepted by the endpoint."""
    args = ["-C", team_root, "update-ref", tracking_ref, head, previous]
    try:
        rc, _, err = run_git(args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        rc, err = 1, f"tracking ref update exec error: {exc}"
    if rc == 0:
        return True, ""
    available, actual = _read_ref_oid(team_root, tracking_ref, timeout)
    if available and actual.lower() == head.lower():
        return True, ""
    return False, (err or "tracking ref changed during exact push").strip()[:200]


def _run_exact_publication_push_locked(
        team_root: str, endpoint: str, destination: str,
        tracking_ref: str, head: str, timeout: int,
        ) -> tuple[int, str, str, str]:
    """Push one OID/ref to one captured endpoint; caller owns interlock."""
    available, previous = _read_ref_oid(team_root, tracking_ref, timeout)
    if not available:
        return 1, "", "publication tracking ref unavailable", ""
    refspec = f"{head}:{destination}"
    # Git applies url.*.insteadOf / pushInsteadOf even when the endpoint is
    # passed directly on argv.  Passing the endpoint itself is therefore still
    # retargetable by a late repo/global config change.  Instead, pass an
    # unpredictable one-shot alias and map that alias to the captured endpoint
    # with command-line-priority config.  URL rewriting is single-pass, so a
    # hostile rule matching the endpoint is not re-applied to the mapped result;
    # no pre-existing rule can predict the 128-bit alias.  '=' is rejected by
    # _remote_push_binding because it cannot be represented in a `-c name=value`
    # key without ambiguity.
    endpoint_alias = f"tm-mode-exact-{os.urandom(16).hex()}://endpoint"
    rewrite_guards = [
        "-c", f"url.{endpoint}.insteadOf={endpoint_alias}",
        "-c", f"url.{endpoint}.pushInsteadOf={endpoint_alias}",
    ]
    args = [
        "-C", team_root, *http_timeout_opts(timeout), *rewrite_guards, "push",
        "--no-follow-tags", "--recurse-submodules=check",
        "--", endpoint_alias, refspec,
    ]
    rc, out, err = _run_publication_push_locked(args, timeout)
    if rc != 0:
        return rc, out, err, ""
    tracking_ok, tracking_detail = _advance_tracking_ref_after_exact_push_locked(
        team_root, tracking_ref, head, previous, min(DEFAULT_TIMEOUT, timeout))
    return (rc, out, err, "" if tracking_ok else tracking_detail)


def _run_exact_publication_push(
        team_root: str, endpoint: str, destination: str,
        tracking_ref: str, head: str, timeout: int,
        ) -> tuple[int, str, str, str]:
    """Interlocked exact endpoint publication with blocker re-probe."""
    wait = max(0.05, min(float(timeout), 1.0))
    with _publication_interlock(team_root, wait) as (acquired, detail):
        if not acquired:
            return 1, "", detail, ""
        blocker = publication_blocker_detail(team_root, wait)
        if blocker:
            return 1, "", blocker, ""
        return _run_exact_publication_push_locked(
            team_root, endpoint, destination, tracking_ref, head, timeout)


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
    """push 출력이 upstream 미설정/현재 branch명 불일치 거부인지 판정. 무raise.

    새 브랜치(`checkout -b`)에서 평문 `git push` 는 push.default=simple 아래
    "fatal: The current branch X has no upstream branch. ... use
    git push --set-upstream origin X" 로 영원히 실패한다(이슈 #34). 이때만
    `push -u origin HEAD` 1회 재시도를 트리거한다. non-ff·인증 실패와 겹치지
    않도록 git 의 거부 메시지 패턴으로 좁게 감지한다(LC_ALL=C 로 영어 고정됨).
    """
    if not text:
        return False
    low = text.lower()
    return ("no upstream branch" in low
            or "--set-upstream" in low
            or ("upstream branch of your current branch" in low
                and "does not match" in low))


def _abort_rebase(team_root: str, timeout: int) -> bool:
    """진행중 rebase 취소 성공 여부를 반환한다. 무raise(best-effort).

    rebase 가 충돌·타임아웃·예외로 실패하면 `.git/rebase-merge` 같은 진행중 상태가
    남아 레포가 어정쩡해진다. 비차단 반환 전에 반드시 호출해 로컬 커밋/워킹트리를
    원래대로 되돌린다. abort 자체의 실패도 삼키되 거짓 성공 진단을 막기 위해 False다.
    """
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "rebase", "--abort"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0


def _has_staged_changes(team_root: str, timeout: int) -> bool:
    """스테이지에 커밋할 변경이 있는지(`git diff --cached --quiet` rc!=0 == 변경 있음)."""
    try:
        rc, _, _ = run_git(
            ["-C", team_root, "diff", "--cached", "--quiet"], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc != 0


def _set_publication_upstream_locked(
        team_root: str, identity: dict[str, str],
        target: _PublicationTarget, *, timeout: int = DEFAULT_TIMEOUT,
        deadline: float | None = None) -> tuple[bool, str]:
    """Set captured-branch upstream while the publication interlock is held."""
    deadline = (time.monotonic() + max(1, timeout)
                if deadline is None else deadline)

    def _probe_timeout() -> int:
        remaining = int(deadline - time.monotonic())
        return min(max(1, timeout), remaining) if remaining >= 1 else 0

    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return False, "upstream setup deadline exhausted"
    binding = _remote_push_binding(team_root, target.remote, probe_timeout)
    if (binding is None or not target.remote_fingerprint
            or binding[1] != target.remote_fingerprint):
        return False, "publication remote binding changed before upstream setup"

    branch_ref = f"refs/heads/{identity['branch']}"
    fmt = "%(refname)%00%(objectname)"
    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return False, "upstream setup deadline exhausted"
    try:
        rc, out, err = run_git(
            ["-C", team_root, "for-each-ref", f"--format={fmt}", "--",
             branch_ref, target.reconcile_ref],
            timeout=probe_timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"upstream setup identity check failed: {exc}"
    if rc != 0:
        return False, (err or "upstream setup identity check failed").strip()[:200]
    refs = {}
    for line in (out or "").splitlines():
        fields = line.split("\0")
        if len(fields) == 2:
            refs[fields[0]] = fields[1].lower()
    expected = identity["head"].lower()
    if refs.get(branch_ref) != expected:
        return False, "captured branch changed before upstream setup"
    if refs.get(target.reconcile_ref) != expected:
        return False, "published tracking ref does not match captured commit"
    probe_timeout = _probe_timeout()
    if not probe_timeout:
        return False, "upstream setup deadline exhausted"
    try:
        rc, out, err = run_git(
            ["-C", team_root,
             f"branch", f"--set-upstream-to={target.reconcile_ref}",
             "--", identity["branch"]],
            timeout=probe_timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"upstream setup exec error: {exc}"
    if rc != 0:
        return False, f"upstream setup failed: {((err or out) or '').strip()[:200]}"
    return True, ""


def _set_publication_upstream(
        team_root: str, identity: dict[str, str],
        target: _PublicationTarget, *, timeout: int = DEFAULT_TIMEOUT,
        deadline: float | None = None) -> tuple[bool, str]:
    """Immutable push 성공 뒤 captured branch config를 interlock 아래 갱신한다."""
    deadline = (time.monotonic() + max(1, timeout)
                if deadline is None else deadline)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False, "upstream setup deadline exhausted"
    lock_timeout = max(0.05, min(remaining, 1.0))
    with _publication_interlock(
            team_root, lock_timeout) as (acquired, detail):
        if not acquired:
            return False, detail
        blocker = publication_blocker_detail(team_root, lock_timeout)
        if blocker:
            return False, blocker
        return _set_publication_upstream_locked(
            team_root, identity, target, timeout=timeout, deadline=deadline)


def _do_commit_impl(
        team_root: str, message: str, push: bool = False,
        timeout: int = NET_TIMEOUT, paths: list | None = None,
        reconcile_before_push: bool = False, *,
        _allow_bound_mutation: bool = False,
        _edit_lease_owner: str | None = None,
        _publication_leases: list, _commit_state: dict) -> CommitResult:
    """`git add` + `git commit -m` (+ 선택 push). 절대 예외를 전파하지 않는다(철칙).

    auto_pull/do_pull 과 같은 안전장치 재사용(git_env 자격증명 차단·killpg 타임아웃).

    timeout 파라미터(기본 NET_TIMEOUT)는 **네트워크 호출(push·복구 fetch/rebase/재push)
    전용**이다. 내부 로컬 하위호출(add·staged-diff·commit)은 DEFAULT_TIMEOUT 고정 —
    함수 timeout 을 그대로 쓰면 push=False(네트워크 0) 경로까지 10s 로 승격돼
    "로컬 동사는 2s(세션 스냅함)" 선언이 깨진다(codex 리뷰 P2-2).
    또한 push=True 흐름 전체는 **진입 앵커** 벽시계 예산 PUSH_TOTAL_BUDGET(45s)로
    캡된다 — 데드라인이 함수 진입에서 시작돼 로컬 단계(최악 ~16s)도 예산을 소모하고
    네트워크 단계는 남은 만큼만 쓴다(로컬 하위호출은 개별 클램프 없음 — 로컬 커밋은
    항상 완주·보존). 복구 체인(최악 네트워크 5회 순차)이 훅 manifest 캡(70s)을 넘기
    전에 **항상** 스스로 반환해, 호출부가 sync-warning 마커를 쓸 수 있게 한다
    (codex 재리뷰 P1 — 종전엔 데드라인이 push 직전 시작이라 로컬 시간 + 25s 가
    캡을 넘을 수 있었다: A1).
    - 변경 없음 → committed=False, ok=False (비치명: 레포 무손상).
    - push=True 이고 원격 없음/오프라인 → **커밋은 보존**, push 만 실패(ok 은 commit 성공
      기준으로 True, pushed=False). push 실패가 로컬 커밋을 되돌리지 않는다.
    - reconcile_before_push=True → 로컬 커밋 뒤 첫 push 전에 기존 안전 정합을 수행한다.
      push=False 일 때는 이 옵션을 무시한다.

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

    def _local_commit_phase(
            ) -> tuple[CommitResult | None, str, dict | None]:
        """Stage, commit, and capture identity while caller owns the lease."""
        # 1) stage — paths 지정 시 그 경로만, None 이면 전부(add -A)
        if paths is None:
            add_args = ["-C", team_root, "add", "-A"]
        else:
            if not paths:
                return (CommitResult(
                    ok=False, committed=False, detail="no paths to stage"),
                    "", None)
            # `--` 로 경로 인자를 옵션과 분리(선두 대시 파일명이 옵션으로 오인되지 않게).
            add_args = [
                "-C", team_root, "add", "--", *[str(p) for p in paths]]
        try:
            rc, _, err = run_git(add_args, timeout=DEFAULT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return CommitResult(ok=False, detail="add timeout"), "", None
        except (OSError, subprocess.SubprocessError) as exc:
            return (CommitResult(
                ok=False, detail=f"add exec error: {exc}"), "", None)
        if rc != 0:
            return (CommitResult(
                ok=False,
                detail=f"add failed: {(err or '').strip()[:200]}"), "", None)

        # 2) 변경 없으면 비치명 종료(빈 커밋 만들지 않음)
        if not _has_staged_changes(team_root, DEFAULT_TIMEOUT):
            return (CommitResult(
                ok=False, committed=False, detail="nothing to commit"),
                "", None)

        # 3) commit — paths 지정 시 pathspec partial commit(미리 staged 된 다른 경로 제외).
        commit_start_identity = _checkout_identity(team_root)
        commit_args = ["-C", team_root, "commit", "-m", message]
        if paths:
            commit_args += ["--", *[str(p) for p in paths]]
        try:
            rc, out, err = run_git(commit_args, timeout=DEFAULT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return CommitResult(ok=False, detail="commit timeout"), "", None
        except (OSError, subprocess.SubprocessError) as exc:
            return (CommitResult(
                ok=False, detail=f"commit exec error: {exc}"), "", None)
        if rc != 0:
            return (CommitResult(
                ok=False, committed=False,
                detail=f"commit failed: {((err or out) or '').strip()[:200]}"),
                "", None)

        # Mark the durable commit before any diagnostic identity probe so the
        # public exception shell never reports committed=False after HEAD moved.
        _commit_state.update(committed=True, identity=None)
        commit_identity = _checkout_identity(team_root)
        if (not commit_identity.get("head")
                or commit_identity.get("head")
                == commit_start_identity.get("head")
                or commit_identity.get("branch")
                != commit_start_identity.get("branch")):
            # commit 전후 checkout 일관성을 입증하지 못하면 later-current checkout에
            # pending을 오바인딩하지 않는다. 호출부가 ledger 실패를 즉시 표면화한다.
            commit_identity = None
        _commit_state.update(identity=commit_identity)
        return None, (out or "").strip()[:200], commit_identity

    # All local history/index mutation shares the same common-repository lock
    # order as bound and unbound reconcile: publication lease first, then Git's
    # canonical index lock.  Re-probe after acquisition so stale refs/txdirs
    # block before `git add`, including push=False callers.  This lease ends
    # before any reconcile/publication lease below, avoiding nested flock.
    with _publication_interlock(team_root, 1.0) as (
            commit_phase_acquired, commit_phase_detail):
        if not commit_phase_acquired:
            return CommitResult(
                ok=False, committed=False, detail=commit_phase_detail)
        commit_phase_blocker = publication_blocker_detail(team_root, 1.0)
        if commit_phase_blocker:
            return CommitResult(
                ok=False, committed=False, detail=commit_phase_blocker)
        local_error, commit_out, publication_identity = _local_commit_phase()
        if local_error is not None:
            return local_error

    publication_interlock_cm = None
    publication_target: _PublicationTarget | None = None

    def _committed_result(pushed: bool, detail: str) -> CommitResult:
        nonlocal publication_interlock_cm
        identity = publication_identity
        if identity is not None and not (push and reconcile_before_push):
            current = _checkout_identity(team_root)
            # 같은 branch에서 rebase로 commit SHA가 바뀐 경우 최신 HEAD를 고정한다.
            # 반환 직전 checkout이 바뀌었다면 원래 commit 직후 identity를 유지한다.
            if (current.get("branch") == identity.get("branch")
                    and current.get("head")):
                identity = current
        result = CommitResult(
            ok=True, committed=True, pushed=pushed, detail=detail,
            pending_identity=identity,
            pending_target=(
                _pending_target_payload(publication_target)
                if publication_target is not None else None))
        if publication_interlock_cm is not None:
            lease = publication_interlock_cm
            publication_interlock_cm = None
            if lease in _publication_leases:
                _publication_leases.remove(lease)
            try:
                lease.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - result must remain non-raising
                pass
        return result

    if not push:
        return _committed_result(False, commit_out)

    # 4) push (선택). 실패해도 **커밋은 보존** — ok 은 commit 성공 기준으로 유지.
    #
    # 공유 데드라인(codex 재리뷰 P1 → A1 진입 앵커): 함수 **진입**에서 시작한
    # _deadline 하나를 이 지점 이후의 **모든** 네트워크 호출(push·push -u·fetch·
    # rebase·재push)이 나눠 쓴다 — 로컬 단계가 이미 소모한 벽시계만큼 네트워크
    # 몫이 준다. 개별 호출마다 NET_TIMEOUT 을 새로 주면 복구 체인이 최악 ~50s 까지
    # 늘어져 훅 manifest 캡(70s)이 프로세스를 먼저 죽이고, 그러면 호출부가
    # CommitResult 를 받지 못해 sync-warning 마커를 못 쓴다. 예산이 바닥나면 즉시
    # 비차단 반환한다(커밋은 이미 보존됨 — push 미완만 detail 로 표면화).

    def _net_t(reserve: int = _COMMIT_RESULT_RESERVE) -> int:
        """정리 꼬리를 뺀 남은 예산으로 네트워크 timeout을 클램프한다.

        기본 reserve는 반환 identity probe 두 개 몫이다. rebase는 autostash
        rollback/postcondition까지 필요하므로 더 큰 _REBASE_RECOVERY_RESERVE를
        명시한다. 호출 직전 _budget_ok(reserve + 1)로 최소 1초 실행 몫을 확인한다.

        하한(max)은 **바깥**에서 강제한다(codex A1): 종전
        min(timeout, max(1, 남은예산)) 은 caller 가 timeout<=0 을 주면 min 이
        그 0/음수를 그대로 통과시켜 '하한 1s' 문서 계약이 깨졌다 — 커밋만 남고
        push 가 즉시 TimeoutExpired 로 죽는다.
        """
        remaining = int(_deadline - time.monotonic() - reserve)
        return max(1, min(timeout, remaining))

    def _budget_ok(reserve: int = _COMMIT_RESULT_RESERVE + 1) -> bool:
        """남은 예산 검사. rebase 같은 다단계 진입 전엔 reserve 를 크게 줘
        '1초 남기고 rebase 시작 → abort 까지 캡 초과' 경로를 차단한다(#codex-P1)."""
        return (_deadline - time.monotonic()) >= reserve

    def _budget_stop(step: str) -> CommitResult:
        return _committed_result(
            False, f"committed; push budget exhausted ({step})")

    if reconcile_before_push:
        # Opt-in publication은 commit 직후의 branch+OID에 영구 바인딩한다. 이후 current
        # checkout은 검증 대상으로만 읽고, push/retry source나 pending identity로 쓰지 않는다.
        bound = _validated_branch_identity(team_root, publication_identity)
        if bound is None or not _checkout_matches_identity(team_root, bound):
            return _committed_result(
                False, "committed; captured checkout identity unavailable")
        publication_identity = bound
        if not _budget_ok():
            return _budget_stop("before pre-push reconcile")
        publication_target, target_detail = _resolve_publication_target(
            team_root, bound, deadline=_deadline)
        target = publication_target
        if target is None:
            return _committed_result(
                False, f"committed; publication target unavailable: {target_detail}")
        if not _budget_ok():
            return _budget_stop("after publication target resolution")
        reconcile_deadline = _deadline - (_COMMIT_RESULT_RESERVE + 1)

        def _adopt_reconciled_identity(
                result: ReconcileResult, label: str) -> str:
            nonlocal publication_identity
            if result.final_identity is None:
                if result.ok and result.action in {"fast-forward", "rebased"}:
                    return f"{label} identity unavailable"
                return ""
            candidate = _validated_branch_identity(
                team_root, result.final_identity)
            if (candidate is None
                    or candidate.get("branch") != bound.get("branch")):
                return f"{label} identity invalid"
            publication_identity = candidate
            return ""

        sync = do_reconcile(
            team_root, timeout=timeout, deadline=reconcile_deadline,
            expected_identity=bound, _target=target,
            _allow_bound_mutation=_allow_bound_mutation,
            _edit_lease_owner=_edit_lease_owner)
        identity_error = _adopt_reconciled_identity(sync, "reconciled")
        if identity_error:
            return _committed_result(False, f"committed; {identity_error}")
        if not sync.ok:
            detail = sync.detail or sync.action
            return _committed_result(
                False,
                f"committed; pre-push reconcile {sync.action}: {detail}")
        if not _budget_ok():
            return _budget_stop("before push after reconcile")

        def _explicit_push() -> tuple[int, str, str, str] | None:
            lock_timeout = _deadline_timeout(
                _deadline, 1, reserve=_COMMIT_RESULT_RESERVE)
            if not lock_timeout:
                return 1, "", "publication interlock budget exhausted", ""
            with _publication_interlock(
                    team_root, lock_timeout) as (acquired, detail):
                if not acquired:
                    return 1, "", detail, ""
                blocker = publication_blocker_detail(
                    team_root, lock_timeout)
                if blocker:
                    return 1, "", blocker, ""
                if not _checkout_matches_identity(
                        team_root, publication_identity):
                    return None
                # Hold the common-repo interlock through the network call and use
                # the endpoint captured during target resolution. Re-reading the
                # remote name here would reintroduce a config TOCTOU.
                push_timeout = _net_t()
                if not target.push_endpoint:
                    return 1, "", "captured push endpoint unavailable", ""
                return _run_exact_publication_push_locked(
                    team_root, target.push_endpoint, target.destination,
                    target.reconcile_ref, publication_identity["head"],
                    push_timeout)

        def _explicit_push_success(detail: str) -> CommitResult:
            if not target.set_upstream:
                return _committed_result(True, detail)
            setup_ok, setup_detail = _set_publication_upstream(
                team_root, publication_identity, target, deadline=_deadline)
            if setup_ok:
                return _committed_result(True, f"{detail} (set upstream)")
            # Remote publication is already durable. Never report pushed=False or retry
            # the immutable commit merely because the local tracking config raced/failed.
            return _committed_result(
                True, f"{detail}; upstream setup skipped: {setup_detail}")

        try:
            push_result = _explicit_push()
        except subprocess.TimeoutExpired:
            return _committed_result(False, "committed; explicit push timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return _committed_result(
                False, f"committed; explicit push exec error: {exc}")
        if push_result is None:
            return _committed_result(
                False, "committed; checkout-changed before explicit push")
        prc, pout, perr, tracking_detail = push_result
        if prc == 0:
            detail = "committed and pushed"
            if tracking_detail:
                detail += f"; tracking update skipped: {tracking_detail}"
            return _explicit_push_success(detail)

        combined = (perr or "") + "\n" + (pout or "")
        if not _is_non_fast_forward(combined):
            return _committed_result(
                False, f"committed; explicit push failed: "
                       f"{combined.strip()[:200]}")

        # Remote가 initial reconcile 직후 전진한 경우에도 retry 전체를 같은 A identity,
        # remote, destination에 묶는다. current B/HEAD/plain push로 강등하지 않는다.
        if not _budget_ok():
            return _budget_stop("before explicit non-ff reconcile")
        retry_deadline = _deadline - (_COMMIT_RESULT_RESERVE + 1)
        retry_sync = do_reconcile(
            team_root, timeout=timeout, deadline=retry_deadline,
            expected_identity=publication_identity, _target=target,
            _allow_bound_mutation=_allow_bound_mutation,
            _edit_lease_owner=_edit_lease_owner)
        identity_error = _adopt_reconciled_identity(
            retry_sync, "retry reconcile")
        if identity_error:
            return _committed_result(False, f"committed; {identity_error}")
        if not retry_sync.ok:
            detail = retry_sync.detail or retry_sync.action
            return _committed_result(
                False, f"committed; explicit non-ff reconcile "
                       f"{retry_sync.action}: {detail}")
        if not _budget_ok():
            return _budget_stop("before explicit re-push")
        try:
            retry_result = _explicit_push()
        except subprocess.TimeoutExpired:
            return _committed_result(
                False, "committed; rebased but explicit re-push timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return _committed_result(
                False, f"committed; explicit re-push exec error: {exc}")
        if retry_result is None:
            return _committed_result(
                False, "committed; checkout-changed before explicit re-push")
        rrc, rout, rerr, retry_tracking_detail = retry_result
        if rrc == 0:
            detail = "committed; rebased and pushed"
            if retry_tracking_detail:
                detail += (
                    f"; tracking update skipped: {retry_tracking_detail}")
            return _explicit_push_success(detail)
        return _committed_result(
            False, f"committed; explicit re-push failed: "
                   f"{((rerr or rout) or '').strip()[:200]}")

    # preflight(A1): 로컬 단계가 예산을 이미 소진했으면 push 를 아예 시작하지 않는다 —
    # _net_t 의 하한(1s) 때문에 소진 상태에서도 1s 짜리 헛 push 가 나가는 걸 차단.
    # 이 결과 모양(committed=True/pushed=False)이 auto-commit 훅의 sync-warning
    # 마커 기록 조건이다.
    if not _budget_ok():
        return _budget_stop("before push")

    # Legacy publication is one serialized transaction: initial rejection,
    # fetch/rebase/abort proof, and final retry all share this same lease.  A
    # worker can never publish an intermediate rebased HEAD between calls.
    lock_timeout = _deadline_timeout(
        _deadline, 1, reserve=_COMMIT_RESULT_RESERVE)
    if not lock_timeout:
        return _budget_stop("before publication interlock")
    lease = _publication_interlock(team_root, lock_timeout)
    try:
        acquired, interlock_detail = lease.__enter__()
    except Exception as exc:  # noqa: BLE001 - public operation is non-raising
        return _committed_result(
            False, f"committed; publication interlock unavailable: {exc}")
    if not acquired:
        lease.__exit__(None, None, None)
        return _committed_result(False, f"committed; {interlock_detail}")
    publication_interlock_cm = lease
    _publication_leases.append(lease)
    blocker = publication_blocker_detail(team_root, lock_timeout)
    if blocker:
        return _committed_result(False, f"committed; {blocker}")

    try:
        prc, pout, perr = _run_publication_push_locked(
            ["-C", team_root, *http_timeout_opts(timeout), "push"],
            timeout=_net_t())
    except subprocess.TimeoutExpired:
        return _committed_result(False, "committed; push timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return _committed_result(False, f"committed; push exec error: {exc}")
    if prc == 0:
        return _committed_result(True, "committed and pushed")

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
            urc, uout, uerr = _run_publication_push_locked(
                ["-C", team_root, *http_timeout_opts(timeout),
                 "push", "-u", "origin", "HEAD"],
                timeout=_net_t())
        except subprocess.TimeoutExpired:
            return _committed_result(False, "committed; push -u timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return _committed_result(
                False, f"committed; push -u exec error: {exc}")
        if urc == 0:
            return _committed_result(True, "committed and pushed (set upstream)")
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
                return _committed_result(
                    False, "committed; push -u rebase fetch timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                return _committed_result(
                    False, f"committed; push -u rebase fetch exec error: {exc}")
            if frc != 0:
                return _committed_result(
                    False, f"committed; push -u rebase fetch failed: "
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
                return _committed_result(
                    False, "committed; push -u rejected (non-ff) and current "
                           "branch unresolvable — manual sync needed")
            if not _budget_ok(reserve=8):
                return _budget_stop("before push -u rebase safety checks")
            safety_issue = _rebase_dirty_safety_issue(
                team_root, f"origin/{branch}", 1)
            if safety_issue:
                return _committed_result(
                    False, f"committed; push -u rebase deferred: {safety_issue}")
            if not _budget_ok(reserve=6):
                return _budget_stop("before push -u rollback guard")
            rebase_guard = _capture_rebase_guard(team_root, 1)
            if rebase_guard is None:
                return _committed_result(
                    False, "committed; push -u rebase deferred: "
                           "rollback guard unavailable")
            if not _budget_ok(reserve=_REBASE_RECOVERY_RESERVE + 1):
                return _budget_stop("before push -u rebase")
            try:
                rrc, rout, rerr = run_git(
                    ["-C", team_root, "rebase", "--autostash",
                     f"origin/{branch}"],
                    timeout=_net_t(reserve=_REBASE_RECOVERY_RESERVE))
            except subprocess.TimeoutExpired as exc:
                created_autostash = _created_autostash_oid(
                    team_root, _timeout_detail(exc), timeout=1)
                abort_ok = _abort_rebase(
                    team_root, DEFAULT_TIMEOUT)  # 로컬 — 예산 밖 고정(#codex-P1)
                rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                    team_root, rebase_guard, created_autostash, timeout=1)
                return _committed_result(
                    False, "committed; push -u " + _rebase_abort_detail(
                        "rebase timeout", abort_ok, rollback_ok, post_detail))
            except (OSError, subprocess.SubprocessError) as exc:
                abort_ok = _abort_rebase(
                    team_root, DEFAULT_TIMEOUT)  # 로컬 — 예산 밖 고정(#codex-P1)
                rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                    team_root, rebase_guard, timeout=1)
                return _committed_result(
                    False, "committed; push -u " + _rebase_abort_detail(
                        f"rebase exec error: {exc}", abort_ok, rollback_ok,
                        post_detail))
            if rrc != 0:
                abort_ok = _abort_rebase(
                    team_root, DEFAULT_TIMEOUT)  # 로컬 — 예산 밖 고정(#codex-P1)
                created_autostash = _created_autostash_oid(
                    team_root, (rout or "") + "\n" + (rerr or ""), timeout=1)
                rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                    team_root, rebase_guard, created_autostash, timeout=1)
                return _committed_result(
                    False, "committed; push -u " + _rebase_abort_detail(
                        "rebase failed", abort_ok, rollback_ok, post_detail,
                        (rerr or "").strip()[:200]))
            created_autostash = _created_autostash_oid(
                team_root, (rout or "") + "\n" + (rerr or ""), timeout=1)
            post_ok, post_detail = _verify_rebase_postcondition(
                team_root, rebase_guard, created_autostash, timeout=1)
            if not post_ok:
                return _committed_result(
                    False, f"committed; push -u rebase postcondition failed: "
                    f"{post_detail}")
            if not _budget_ok():
                return _budget_stop("before push -u after rebase")
            try:
                u2rc, u2out, u2err = _run_publication_push_locked(
                    ["-C", team_root, *http_timeout_opts(timeout),
                     "push", "-u", "origin", "HEAD"],
                    timeout=_net_t())
            except subprocess.TimeoutExpired:
                return _committed_result(
                    False, "committed; rebased but push -u timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                return _committed_result(
                    False, f"committed; rebased but push -u exec error: {exc}")
            if u2rc == 0:
                return _committed_result(
                    True, "committed and pushed (set upstream after rebase)")
            return _committed_result(
                False, f"committed; rebased but push -u failed: "
                f"{((u2err or u2out) or '').strip()[:200]}")
        return _committed_result(
            False, f"committed; push failed: "
            f"{((uerr or uout) or '').strip()[:200]}")

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
            return _committed_result(False, "committed; rebase fetch timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            return _committed_result(
                False, f"committed; rebase fetch exec error: {exc}")
        if frc == 0:
            # rebase (추적 upstream 위로). dirty 파일이 upstream 변경과 겹치면
            # autostash apply가 성공 rc 뒤에도 conflict를 남길 수 있어 선제 보류한다.
            if not _budget_ok(reserve=8):
                return _budget_stop("before rebase safety checks")
            safety_issue = _rebase_dirty_safety_issue(
                team_root, "@{u}", 1)
            if safety_issue:
                return _committed_result(
                    False, f"committed; rebase deferred: {safety_issue}")
            if not _budget_ok(reserve=6):
                return _budget_stop("before rollback guard")
            rebase_guard = _capture_rebase_guard(team_root, 1)
            if rebase_guard is None:
                return _committed_result(
                    False, "committed; rebase deferred: rollback guard unavailable")
            if not _budget_ok(reserve=_REBASE_RECOVERY_RESERVE + 1):
                return _budget_stop("before rebase")
            try:
                rrc, rout, rerr = run_git(
                    ["-C", team_root, "rebase", "--autostash"],
                    timeout=_net_t(reserve=_REBASE_RECOVERY_RESERVE))
            except subprocess.TimeoutExpired as exc:
                created_autostash = _created_autostash_oid(
                    team_root, _timeout_detail(exc), timeout=1)
                abort_ok = _abort_rebase(
                    team_root, DEFAULT_TIMEOUT)  # 로컬 — 예산 밖 고정(#codex-P1)
                rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                    team_root, rebase_guard, created_autostash, timeout=1)
                return _committed_result(
                    False, "committed; " + _rebase_abort_detail(
                        "rebase timeout", abort_ok, rollback_ok, post_detail))
            except (OSError, subprocess.SubprocessError) as exc:
                abort_ok = _abort_rebase(
                    team_root, DEFAULT_TIMEOUT)  # 로컬 — 예산 밖 고정(#codex-P1)
                rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                    team_root, rebase_guard, timeout=1)
                return _committed_result(
                    False, "committed; " + _rebase_abort_detail(
                        f"rebase exec error: {exc}", abort_ok, rollback_ok,
                        post_detail))
            if rrc == 0:
                created_autostash = _created_autostash_oid(
                    team_root, (rout or "") + "\n" + (rerr or ""), timeout=1)
                post_ok, post_detail = _verify_rebase_postcondition(
                    team_root, rebase_guard, created_autostash, timeout=1)
                if not post_ok:
                    return _committed_result(
                        False, f"committed; rebase postcondition failed: {post_detail}")
                # rebase 성공 → 재push 1회.
                if not _budget_ok():
                    return _budget_stop("before re-push")
                try:
                    p2rc, p2out, p2err = _run_publication_push_locked(
                        ["-C", team_root, *http_timeout_opts(timeout), "push"],
                        timeout=_net_t())
                except subprocess.TimeoutExpired:
                    return _committed_result(
                        False, "committed; rebased but re-push timeout")
                except (OSError, subprocess.SubprocessError) as exc:
                    return _committed_result(
                        False, f"committed; rebased but re-push exec error: {exc}")
                if p2rc == 0:
                    return _committed_result(True, "committed; rebased and pushed")
                return _committed_result(
                    False, f"committed; rebased but re-push failed: "
                    f"{((p2err or p2out) or '').strip()[:200]}")
            # rebase 실패(충돌 등) → abort 로 원상복구 후 비차단 반환.
            abort_ok = _abort_rebase(
                team_root, DEFAULT_TIMEOUT)  # 로컬 — 예산 밖 고정(#codex-P1)
            created_autostash = _created_autostash_oid(
                team_root, (rout or "") + "\n" + (rerr or ""), timeout=1)
            rollback_ok, post_detail = _verify_rebase_rollback_postcondition(
                team_root, rebase_guard, created_autostash, timeout=1)
            return _committed_result(
                False, "committed; " + _rebase_abort_detail(
                    "rebase failed", abort_ok, rollback_ok, post_detail,
                    (rerr or "").strip()[:200]))

    return _committed_result(
        False, f"committed; push failed: {((perr or pout) or '').strip()[:200]}")


def do_commit(team_root: str, message: str, push: bool = False,
              timeout: int = NET_TIMEOUT, paths: list | None = None,
              reconcile_before_push: bool = False, *,
              _allow_bound_mutation: bool = False,
              _edit_lease_owner: str | None = None) -> CommitResult:
    """Exception-safe public shell that always releases publication leases."""
    leases: list = []
    commit_state: dict = {"committed": False, "identity": None}
    try:
        return _do_commit_impl(
            team_root, message, push=push, timeout=timeout, paths=paths,
            reconcile_before_push=reconcile_before_push,
            _allow_bound_mutation=_allow_bound_mutation,
            _edit_lease_owner=_edit_lease_owner,
            _publication_leases=leases, _commit_state=commit_state)
    except Exception as exc:  # noqa: BLE001 - public operation is non-raising
        committed = bool(commit_state["committed"])
        return CommitResult(
            ok=committed, committed=committed, pushed=False,
            detail=f"{'committed; ' if committed else ''}unexpected git error: {exc}",
            pending_identity=commit_state["identity"])
    finally:
        exc_info = sys.exc_info()
        while leases:
            lease = leases.pop()
            try:
                lease.__exit__(*exc_info)
            except Exception:  # noqa: BLE001 - release is best effort/non-raising
                pass


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
    # remote 목록은 로컬 probe다. 네트워크 fetch용 timeout(최대 10s)을 그대로 주면
    # SessionStart shared deadline에서 로컬 한 번이 10s를 별도로 소비할 수 있다.
    if not _has_remote(team_root, remote, DEFAULT_TIMEOUT):
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
        prc, pout, perr = _run_publication_push(
            team_root,
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
        prc, pout, perr = _run_publication_push(
            root,
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
