#!/usr/bin/env python3
"""Shared, exception-safe Git operations for tm-mode.

The auto-sync path is intentionally small: a path-scoped local commit followed
by one serialized main-branch fetch/rebase/push cycle.  Network and credential
prompts are bounded, unrelated staged/worktree content is left untouched, and
sync failures are recorded outside the repository.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 2
NET_TIMEOUT = 10
PUSH_TOTAL_BUDGET = 45

# A timed-out Git child may spend up to five seconds in Windows taskkill and
# two more seconds draining pipes. Keep that tail plus private error-state I/O
# outside every network subprocess timeout.
_PROCESS_KILL_DRAIN_RESERVE = 7
_LOCK_RELEASE_RESERVE = 0.25
_ERROR_STATE_WRITE_RESERVE = DEFAULT_TIMEOUT + _LOCK_RELEASE_RESERVE
_NETWORK_FAILURE_RESERVE = (
    _PROCESS_KILL_DRAIN_RESERVE + _ERROR_STATE_WRITE_RESERVE)
_FINAL_STATE_RESERVE = (
    2 * DEFAULT_TIMEOUT + _LOCK_RELEASE_RESERVE)
# pull --rebase additionally needs a bounded abort and an unmerged-index proof.
_REBASE_FAILURE_RESERVE = 20


@dataclass
class PullResult:
    ok: bool
    attempted: bool = True
    detail: str = ""


@dataclass
class CommitResult:
    ok: bool
    committed: bool = False
    pushed: bool = False
    detail: str = ""


@dataclass
class MainSyncResult:
    ok: bool
    action: str = "noop"
    ahead: int = 0
    behind: int = 0
    detail: str = ""


@dataclass
class FetchResult:
    ok: bool
    detail: str = ""


@dataclass
class SyncResult:
    ok: bool
    changed: bool = False
    paths: tuple = ()
    diff: str = ""
    detail: str = ""
    blocked: bool = False
    pathspecs: tuple = ()


@dataclass
class WorkflowStripResult:
    ok: bool
    changed: bool = False
    committed: bool = False
    pushed: bool = False
    skipped_product: bool = False
    detail: str = ""


@dataclass(frozen=True)
class _PrivateTextRead:
    content: str = ""
    available: bool = True


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
_GIT_DISABLED_HOOKS_PATH = "/dev/null"


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
        # variables inherited from the parent. Remove ambient redirects first;
        # trusted call-specific overrides are applied below.
        for name in tuple(child_env):
            if (name in _REPO_REDIRECT_ENV
                    or _REPO_CONFIG_ENTRY_ENV_RE.fullmatch(name)):
                child_env.pop(name, None)
    # Call-scoped Git knobs belong to this child only. Copying both the process
    # environment and caller mapping avoids mutating either shared object.
    # ``None`` deliberately removes a hostile inherited value.
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
        # Preserve partial and kill-drain output for bounded failure diagnosis.
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
        return PullResult(ok=True, detail=sanitize_git_detail(out or ""))
    return PullResult(
        ok=False, detail=sanitize_git_detail((err or out) or ""))


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



def _state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "teammode")


def _owned_regular(st: os.stat_result) -> bool:
    if not stat.S_ISREG(st.st_mode):
        return False
    return not hasattr(os, "getuid") or st.st_uid == os.getuid()


def _owned_directory(path: Path) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(current.st_mode)
        and not stat.S_ISLNK(current.st_mode)
        and (not hasattr(os, "getuid") or current.st_uid == os.getuid())
    )


def _ensure_private_state_dir() -> bool:
    path = _state_dir()
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.lstat(path)
        is_junction = getattr(os.path, "isjunction", lambda _path: False)
        if (
            not stat.S_ISDIR(st.st_mode)
            or stat.S_ISLNK(st.st_mode)
            or os.path.islink(path)
            or is_junction(path)
        ):
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
    try:
        if os.path.islink(path):
            raise OSError(errno.ELOOP, "path is a symlink")
        before = os.lstat(path)
        if not _owned_regular(before):
            raise OSError(errno.EPERM, "path is not an owned regular file")
    except FileNotFoundError:
        if not (flags & os.O_CREAT):
            raise
    safe_flags = flags | getattr(os, "O_CLOEXEC", 0)
    safe_flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, safe_flags, mode)
    try:
        st = os.fstat(fd)
        if not _owned_regular(st):
            raise OSError(errno.EPERM, "path is not an owned regular file")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        return fd
    except Exception:
        os.close(fd)
        raise


def _fsync_parent_dir(path: str) -> bool:
    if os.name == "nt":
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


def _read_private_text(path: str) -> _PrivateTextRead:
    if not _ensure_private_state_dir():
        return _PrivateTextRead(available=False)
    try:
        fd = _secure_open_regular(
            path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return _PrivateTextRead()
    except OSError:
        return _PrivateTextRead(available=False)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            return _PrivateTextRead(handle.read().strip())
    except (OSError, UnicodeError, ValueError):
        return _PrivateTextRead(available=False)


def _write_private_text(path: str, content: str) -> bool:
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
            prefix=f".{os.path.basename(path)}.", suffix=".tmp",
            dir=_state_dir())
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(str(content))
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
    if not _ensure_private_state_dir():
        return False
    try:
        st = os.lstat(path)
        if not _owned_regular(st):
            return False
        os.remove(path)
        return _fsync_parent_dir(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False


_LOCK_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EBUSY", errno.EAGAIN),
}


@contextmanager
def _advisory_file_lock(path: str, timeout: float):
    handle = None
    acquired = False
    unlock = None
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        try:
            fd = _secure_open_regular(path, os.O_RDWR | os.O_CREAT)
            handle = os.fdopen(fd, "r+b", buffering=0)
            if os.name == "nt":  # pragma: no cover - platform specific
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()

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
                    current = os.lstat(path)
                    if (
                        (opened.st_dev, opened.st_ino)
                        != (current.st_dev, current.st_ino)
                        or not _owned_regular(current)
                    ):
                        break
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in _LOCK_CONTENTION_ERRNOS:
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
        except (OSError, ImportError, ValueError):
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


def _team_key(team_root: str) -> str:
    # Keep the historical key stable because unrelated validation cache and
    # backup paths also use it.
    norm = os.path.normpath(str(team_root))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


@contextmanager
def private_state_lock(
    root: str, purpose: str, timeout: float = DEFAULT_TIMEOUT
):
    """Serialize one repository-specific private-state purpose.

    The context manager yields only an acquisition boolean.
    """
    acquired = False
    if _ensure_private_state_dir():
        material = str(purpose or "").encode("utf-8", errors="replace")
        purpose_key = hashlib.sha256(material).hexdigest()[:24]
        lock_path = os.path.join(
            _state_dir(), f"lock-{_team_key(root)}-{purpose_key}")
        with _advisory_file_lock(lock_path, timeout) as acquired:
            yield acquired
        return
    yield False


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
    text = _ANSI_ESCAPE_RE.sub("", str(detail or ""))
    text = _URL_USERINFO_RE.sub(r"\1[redacted]@", text)
    text = _SECRET_TOKEN_RE.sub("[redacted]", text)
    text = _AUTH_HEADER_RE.sub(r"\1[redacted]", text)
    text = _SECRET_VALUE_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}[redacted]"),
        text,
    )
    text = "".join(
        ch if not unicodedata.category(ch).startswith("C") else " "
        for ch in text
    )
    text = " ".join(text.split())
    return text[: max(1, int(limit))] or "unknown git failure"


def _last_sync_error_path(team_root: str) -> str:
    return os.path.join(
        _state_dir(), f"last-sync-error-{_team_key(team_root)}")


def read_last_sync_error(team_root: str) -> str:
    content = _read_private_text(_last_sync_error_path(team_root)).content
    return sanitize_git_detail(content) if content else ""


def write_last_sync_error(team_root: str, detail: str) -> bool:
    safe_detail = sanitize_git_detail(detail)
    with private_state_lock(
        team_root, "last-sync-error", DEFAULT_TIMEOUT
    ) as acquired:
        if not acquired:
            return False
        return _write_private_text(
            _last_sync_error_path(team_root), safe_detail)


def clear_last_sync_error(team_root: str) -> bool:
    with private_state_lock(
        team_root, "last-sync-error", DEFAULT_TIMEOUT
    ) as acquired:
        if not acquired:
            return False
        return _remove_private_file(_last_sync_error_path(team_root))


def _git_common_dir(team_root: str, timeout: float) -> Path | None:
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "rev-parse", "--git-common-dir"],
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out or "").strip()
    if rc != 0 or not raw or "\n" in raw or "\0" in raw:
        return None
    candidate = Path(raw)
    common = candidate if candidate.is_absolute() else Path(team_root) / candidate
    common = Path(os.path.abspath(common))
    return common if _owned_directory(common) else None


@contextmanager
def _repo_sync_lock(
    team_root: str, timeout: float = DEFAULT_TIMEOUT
):
    """Serialize new and older tm-mode publication code in one Git common dir."""
    lock_deadline = time.monotonic() + max(0.05, float(timeout))
    common_timeout = _remaining_timeout(lock_deadline, DEFAULT_TIMEOUT)
    if common_timeout <= 0:
        yield False
        return
    common = _git_common_dir(team_root, common_timeout)
    if common is None:
        yield False
        return
    lock_path = common / ".tm-mode-publication.lock"
    remaining = max(0.0, lock_deadline - time.monotonic())
    with _advisory_file_lock(str(lock_path), remaining) as acquired:
        yield acquired


def _remaining_timeout(deadline: float, cap: float) -> float:
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(max(0.05, float(cap)), remaining)


def _reserved_timeout(
    deadline: float, cap: float, reserve: float
) -> float:
    remaining = float(deadline) - time.monotonic() - max(0.0, reserve)
    if remaining <= 0:
        return 0.0
    return min(max(0.05, float(cap)), remaining)


def _network_timeout(
    deadline: float,
    cap: float,
    *,
    reserve: float = _NETWORK_FAILURE_RESERVE,
) -> float:
    return _reserved_timeout(deadline, cap, reserve)


def _current_branch(team_root: str, timeout: float) -> str:
    try:
        rc, out, _ = run_git(
            ["-C", team_root, "symbolic-ref", "--quiet", "--short", "HEAD"],
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out or "").strip() if rc == 0 else ""


def _origin_main_counts(
    team_root: str, timeout: float
) -> tuple[int, int] | None:
    try:
        rc, out, _ = run_git(
            [
                "-C",
                team_root,
                "rev-list",
                "--count",
                "--left-right",
                "origin/main...HEAD",
            ],
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = (out or "").split()
    if rc != 0 or len(parts) != 2:
        return None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return ahead, behind


def _rebase_state_paths(
    team_root: str, timeout: float
) -> tuple[Path, Path] | None:
    paths: list[Path] = []
    state_deadline = time.monotonic() + max(0.0, float(timeout))
    for name in ("rebase-merge", "rebase-apply"):
        step_timeout = _remaining_timeout(state_deadline, DEFAULT_TIMEOUT)
        if step_timeout <= 0:
            return None
        try:
            rc, out, _ = run_git(
                ["-C", team_root, "rev-parse", "--git-path", name],
                timeout=step_timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        raw = (out or "").strip()
        if rc != 0 or not raw or "\n" in raw or "\0" in raw:
            return None
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else Path(team_root) / candidate
        paths.append(Path(os.path.abspath(path)))
    return paths[0], paths[1]


def _rebase_markers_present(paths: tuple[Path, Path]) -> bool | None:
    for path in paths:
        try:
            if os.path.lexists(path):
                return True
        except OSError:
            return None
    return False


def _rebase_in_progress(
    team_root: str, timeout: float
) -> bool | None:
    paths = _rebase_state_paths(team_root, timeout)
    return None if paths is None else _rebase_markers_present(paths)


def _abort_new_rebase(
    team_root: str,
    timeout: float,
    rebase_paths: tuple[Path, Path],
) -> tuple[bool, str]:
    """Abort only this call's rebase and prove repository operation state is clear."""
    cleanup_deadline = time.monotonic() + max(0.0, float(timeout))
    abort_timeout = _reserved_timeout(
        cleanup_deadline, DEFAULT_TIMEOUT, _PROCESS_KILL_DRAIN_RESERVE)
    if abort_timeout <= 0:
        return False, "rebase cleanup deadline exhausted before abort"
    abort_ok = False
    abort_detail = ""
    try:
        rc, out, err = run_git(
            ["-C", team_root, "rebase", "--abort"],
            timeout=abort_timeout,
        )
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "rebase abort timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        rc, out, err = -1, "", f"rebase abort error: {exc}"
    if rc == 0:
        abort_ok = True
        abort_detail = "rebase abort completed"
    else:
        abort_detail = (
            "rebase abort failed: " + ((err or out) or "").strip())

    markers = _rebase_markers_present(rebase_paths)
    if markers is None:
        return False, f"{abort_detail}; rebase marker verification unavailable"
    if markers:
        return False, f"{abort_detail}; rebase marker remains"

    verify_timeout = _reserved_timeout(
        cleanup_deadline, DEFAULT_TIMEOUT, _PROCESS_KILL_DRAIN_RESERVE)
    if verify_timeout <= 0:
        return False, f"{abort_detail}; unmerged index verification deadline exhausted"
    try:
        urc, uout, uerr = run_git(
            ["-C", team_root, "ls-files", "--unmerged"],
            timeout=verify_timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{abort_detail}; unmerged index verification timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{abort_detail}; unmerged index verification error: {exc}"
    if urc != 0:
        return False, (
            f"{abort_detail}; unmerged index verification failed: "
            f"{((uerr or uout) or '').strip()}")
    if (uout or "").strip():
        return False, f"{abort_detail}; unmerged index entries remain"
    if not abort_ok:
        return False, abort_detail
    return True, "new rebase aborted; rebase markers and unmerged index clear"


def _is_non_fast_forward(text: str) -> bool:
    low = str(text or "").lower()
    return (
        "non-fast-forward" in low
        or "fetch first" in low
        or "remote contains work that you do not have locally" in low
        or "updates were rejected because the remote contains work" in low
    )


def _sync_failure(
    team_root: str,
    action: str,
    detail: str,
    *,
    ahead: int = 0,
    behind: int = 0,
) -> MainSyncResult:
    safe = sanitize_git_detail(detail)
    write_last_sync_error(team_root, safe)
    return MainSyncResult(
        ok=False,
        action=action,
        ahead=max(0, int(ahead)),
        behind=max(0, int(behind)),
        detail=safe,
    )


def sync_main(
    team_root: str,
    timeout: int = NET_TIMEOUT,
    *,
    deadline: float | None = None,
) -> MainSyncResult:
    """Synchronize the checked-out main branch with origin/main.

    One repository lock covers branch revalidation through final 0/0 proof.
    Only a push non-fast-forward race gets one additional cycle.
    """
    try:
        if not is_git_worktree(team_root):
            return _sync_failure(
                team_root, "error", "not a git work tree")
        # Read-only preflight precedes the transaction lock. A non-main
        # SessionStart must not touch refs, FETCH_HEAD, or the repository lock.
        preflight_branch = _current_branch(team_root, DEFAULT_TIMEOUT)
        if preflight_branch != "main":
            current = preflight_branch or "detached"
            return _sync_failure(
                team_root,
                "not-main",
                f"sync requires main branch (current: {current})",
            )
        if deadline is None:
            deadline = time.monotonic() + PUSH_TOTAL_BUDGET
        else:
            deadline = float(deadline)
        if _reserved_timeout(
            deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE
        ) <= 0:
            return _sync_failure(
                team_root, "error", "main sync deadline exhausted")

        lock_timeout = _reserved_timeout(
            deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
        if lock_timeout <= 0:
            return _sync_failure(
                team_root, "busy", "main sync deadline exhausted")
        with _repo_sync_lock(team_root, lock_timeout) as acquired:
            if not acquired:
                return _sync_failure(
                    team_root, "busy", "repository sync lock unavailable")
            return _sync_main_locked(team_root, timeout, deadline)
    except Exception as exc:  # public API is deliberately non-raising
        return _sync_failure(
            team_root, "error", f"unexpected main sync error: {exc}")


def _sync_main_locked(
    team_root: str, timeout: int, deadline: float
) -> MainSyncResult:
    """Run main sync while the caller holds the repository transaction lock."""
    try:
        branch_timeout = _reserved_timeout(
            deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
        if branch_timeout <= 0:
            return _sync_failure(
                team_root, "error", "main sync deadline exhausted")
        branch = _current_branch(team_root, branch_timeout)
        if branch != "main":
            current = branch or "detached"
            return _sync_failure(
                team_root,
                "not-main",
                f"sync requires main branch (current: {current})",
            )

        rebased = False
        saw_ahead = False
        last_ahead = 0
        last_behind = 0

        for attempt in range(2):
            if attempt:
                branch_timeout = _reserved_timeout(
                    deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
                if branch_timeout <= 0:
                    return _sync_failure(
                        team_root,
                        "error",
                        "main sync deadline exhausted before retry",
                        ahead=last_ahead,
                        behind=last_behind,
                    )
                branch = _current_branch(
                    team_root, branch_timeout)
                if branch != "main":
                    return _sync_failure(
                        team_root,
                        "not-main",
                        "checkout changed during main sync",
                        ahead=last_ahead,
                        behind=last_behind,
                    )

            fetch_timeout = _network_timeout(deadline, timeout)
            if fetch_timeout <= 0:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    "main sync deadline exhausted before fetch",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            try:
                frc, fout, ferr = run_git(
                    [
                        "-C",
                        team_root,
                        *http_timeout_opts(
                            max(1, int(fetch_timeout))),
                        "fetch",
                        "--no-tags",
                        "origin",
                        "main",
                    ],
                    timeout=fetch_timeout,
                )
            except subprocess.TimeoutExpired:
                return _sync_failure(
                    team_root, "fetch-failed", "fetch origin main timeout")
            except (OSError, subprocess.SubprocessError) as exc:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    f"fetch origin main error: {exc}",
                )
            if frc != 0:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    f"fetch origin main failed: "
                    f"{((ferr or fout) or '').strip()}",
                )

            count_timeout = _reserved_timeout(
                deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
            if count_timeout <= 0:
                return _sync_failure(
                    team_root,
                    "error",
                    "main sync deadline exhausted before comparison",
                )
            counts = _origin_main_counts(
                team_root, count_timeout)
            if counts is None:
                return _sync_failure(
                    team_root,
                    "error",
                    "cannot compare origin/main...HEAD",
                )
            last_ahead, last_behind = counts
            saw_ahead = saw_ahead or last_ahead > 0

            if last_behind:
                state_timeout = _reserved_timeout(
                    deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
                if state_timeout <= 0:
                    return _sync_failure(
                        team_root,
                        "pull-failed",
                        "main sync deadline exhausted before rebase",
                        ahead=last_ahead,
                        behind=last_behind,
                    )
                rebase_paths = _rebase_state_paths(
                    team_root, state_timeout)
                preexisting = (
                    None if rebase_paths is None
                    else _rebase_markers_present(rebase_paths))
                if preexisting is None or rebase_paths is None:
                    return _sync_failure(
                        team_root,
                        "pull-failed",
                        "cannot verify rebase state",
                        ahead=last_ahead,
                        behind=last_behind,
                    )
                if preexisting:
                    return _sync_failure(
                        team_root,
                        "conflict",
                        "pre-existing rebase left untouched",
                        ahead=last_ahead,
                        behind=last_behind,
                    )

                pull_timeout = _network_timeout(
                    deadline,
                    timeout,
                    reserve=_REBASE_FAILURE_RESERVE,
                )
                if pull_timeout <= 0:
                    return _sync_failure(
                        team_root,
                        "pull-failed",
                        "main sync deadline exhausted before pull",
                        ahead=last_ahead,
                        behind=last_behind,
                    )
                pull_text = ""
                try:
                    prc, pout, perr = run_git(
                        [
                            "-C",
                            team_root,
                            "-c",
                            "rebase.updateRefs=false",
                            "-c",
                            "rebase.autoStash=false",
                            *http_timeout_opts(
                                max(1, int(pull_timeout))),
                            "pull",
                            "--no-tags",
                            "--rebase",
                            "origin",
                            "main",
                        ],
                        timeout=pull_timeout,
                    )
                    pull_text = ((perr or pout) or "").strip()
                except subprocess.TimeoutExpired as exc:
                    prc = -1
                    pull_text = (
                        "pull --rebase timeout; "
                        + _timeout_detail(exc).strip())
                except (OSError, subprocess.SubprocessError) as exc:
                    prc = -1
                    pull_text = f"pull --rebase error: {exc}"

                if prc != 0:
                    post_state = _rebase_markers_present(rebase_paths)
                    abort_detail = ""
                    new_rebase = post_state is True
                    cleanup_ok = post_state is not None
                    if post_state is None:
                        abort_detail = (
                            "post-failure rebase marker verification "
                            "unavailable; no abort attempted")
                    if new_rebase:
                        cleanup_budget = max(
                            0.0,
                            float(deadline) - time.monotonic()
                            - _ERROR_STATE_WRITE_RESERVE,
                        )
                        cleanup_ok, abort_detail = _abort_new_rebase(
                            team_root, cleanup_budget, rebase_paths)
                    action = (
                        "conflict"
                        if new_rebase
                        or "conflict" in pull_text.lower()
                        else "pull-failed"
                    )
                    detail = (
                        f"pull --rebase origin main failed: "
                        f"{pull_text or 'unknown failure'}")
                    if abort_detail:
                        detail += f"; {abort_detail}"
                    if not cleanup_ok:
                        detail += "; rebase cleanup incomplete"
                    return _sync_failure(
                        team_root,
                        action,
                        detail,
                        ahead=last_ahead,
                        behind=last_behind,
                    )
                rebased = True

            branch_timeout = _reserved_timeout(
                deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
            if branch_timeout <= 0:
                return _sync_failure(
                    team_root,
                    "push-failed",
                    "main sync deadline exhausted before push",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            if _current_branch(team_root, branch_timeout) != "main":
                return _sync_failure(
                    team_root,
                    "not-main",
                    "checkout changed before main push",
                    ahead=last_ahead,
                    behind=last_behind,
                )

            push_timeout = _network_timeout(deadline, timeout)
            if push_timeout <= 0:
                return _sync_failure(
                    team_root,
                    "push-failed",
                    "main sync deadline exhausted before push",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            try:
                prc, pout, perr = run_git(
                    [
                        "-C",
                        team_root,
                        "-c",
                        "push.followTags=false",
                        "-c",
                        "push.recurseSubmodules=check",
                        *http_timeout_opts(
                            max(1, int(push_timeout))),
                        "push",
                        "--no-follow-tags",
                        "--recurse-submodules=check",
                        "origin",
                        "refs/heads/main:refs/heads/main",
                    ],
                    timeout=push_timeout,
                )
            except subprocess.TimeoutExpired:
                return _sync_failure(
                    team_root,
                    "push-failed",
                    "push origin refs/heads/main timeout",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return _sync_failure(
                    team_root,
                    "push-failed",
                    f"push origin refs/heads/main error: {exc}",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            push_text = (perr or "") + "\n" + (pout or "")
            if prc != 0:
                if attempt == 0 and _is_non_fast_forward(push_text):
                    continue
                return _sync_failure(
                    team_root,
                    "push-failed",
                    f"push origin refs/heads/main failed: "
                    f"{push_text.strip()}",
                    ahead=last_ahead,
                    behind=last_behind,
                )

            verify_fetch_timeout = _network_timeout(deadline, timeout)
            if verify_fetch_timeout <= 0:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    "deadline exhausted before final remote verification",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            try:
                vrc, vout, verr = run_git(
                    [
                        "-C",
                        team_root,
                        *http_timeout_opts(
                            max(1, int(verify_fetch_timeout))),
                        "fetch",
                        "--no-tags",
                        "origin",
                        "main",
                    ],
                    timeout=verify_fetch_timeout,
                )
            except subprocess.TimeoutExpired:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    "final fetch origin main timeout",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    f"final fetch origin main error: {exc}",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            if vrc != 0:
                return _sync_failure(
                    team_root,
                    "fetch-failed",
                    f"final fetch origin main failed: "
                    f"{((verr or vout) or '').strip()}",
                    ahead=last_ahead,
                    behind=last_behind,
                )

            verify_timeout = _reserved_timeout(
                deadline, DEFAULT_TIMEOUT, _FINAL_STATE_RESERVE)
            if verify_timeout <= 0:
                return _sync_failure(
                    team_root,
                    "error",
                    "deadline exhausted before final comparison",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            final_counts = _origin_main_counts(
                team_root, verify_timeout)
            if final_counts is None:
                return _sync_failure(
                    team_root,
                    "error",
                    "cannot verify final origin/main...HEAD",
                    ahead=last_ahead,
                    behind=last_behind,
                )
            final_ahead, final_behind = final_counts
            if final_ahead or final_behind:
                return _sync_failure(
                    team_root,
                    "push-failed",
                    "final main sync verification is not 0/0",
                    ahead=final_ahead,
                    behind=final_behind,
                )

            if not clear_last_sync_error(team_root):
                return _sync_failure(
                    team_root,
                    "error",
                    "final main sync is 0/0 but last sync error "
                    "could not be cleared",
                    ahead=0,
                    behind=0,
                )
            if rebased:
                action = "rebased"
            elif saw_ahead:
                action = "pushed"
            else:
                action = "up-to-date"
            return MainSyncResult(
                ok=True,
                action=action,
                ahead=0,
                behind=0,
                detail="main synchronized (ahead 0, behind 0)",
            )

        return _sync_failure(
            team_root,
            "push-failed",
            "push non-fast-forward retry exhausted",
            ahead=last_ahead,
            behind=last_behind,
        )
    except Exception as exc:  # public API is deliberately non-raising
        return _sync_failure(
            team_root, "error", f"unexpected main sync error: {exc}")


def _has_staged_changes(
    team_root: str,
    timeout: int,
    paths: list[str] | None = None,
) -> bool:
    args = ["-C", team_root, "diff", "--cached", "--quiet"]
    if paths is not None:
        args += ["--", *paths]
    try:
        rc, _, _ = run_git(args, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 1


def _record_commit_failure(
    team_root: str, detail: str, *, push: bool
) -> CommitResult:
    safe = sanitize_git_detail(detail)
    if push:
        write_last_sync_error(team_root, safe)
    return CommitResult(ok=False, detail=safe)


def do_commit(
    team_root: str,
    message: str,
    push: bool = False,
    timeout: int = NET_TIMEOUT,
    paths: list | None = None,
) -> CommitResult:
    """Create one local commit and optionally synchronize main.

    Literal pathspecs are passed unchanged to both add and partial commit.
    """
    try:
        if not is_git_worktree(team_root):
            return _record_commit_failure(
                team_root, "not a git work tree", push=push)

        if push:
            branch = _current_branch(team_root, DEFAULT_TIMEOUT)
            if branch != "main":
                current = branch or "detached"
                return _record_commit_failure(
                    team_root,
                    f"sync commit requires main branch (current: {current})",
                    push=True,
                )

        scoped_paths = (
            None if paths is None else [str(path) for path in paths])
        if scoped_paths is not None:
            if not scoped_paths:
                return CommitResult(
                    ok=False,
                    committed=False,
                    detail="no paths to stage",
                )
            if any("\0" in path for path in scoped_paths):
                return _record_commit_failure(
                    team_root, "invalid NUL in pathspec", push=push)

        deadline = (
            time.monotonic() + PUSH_TOTAL_BUDGET if push else None)
        lock_timeout = DEFAULT_TIMEOUT if deadline is None else _reserved_timeout(
            deadline, DEFAULT_TIMEOUT, _ERROR_STATE_WRITE_RESERVE)
        if lock_timeout <= 0:
            return _record_commit_failure(
                team_root, "commit deadline exhausted", push=push)
        with _repo_sync_lock(team_root, lock_timeout) as acquired:
            if not acquired:
                return _record_commit_failure(
                    team_root, "repository sync lock unavailable", push=push)

            if push:
                branch = _current_branch(team_root, DEFAULT_TIMEOUT)
                if branch != "main":
                    current = branch or "detached"
                    return _record_commit_failure(
                        team_root,
                        f"sync commit requires main branch (current: {current})",
                        push=True,
                    )

            add_args = ["-C", team_root, "add"]
            if scoped_paths is None:
                add_args.append("-A")
            else:
                add_args += ["--", *scoped_paths]
            try:
                arc, aout, aerr = run_git(
                    add_args, timeout=DEFAULT_TIMEOUT)
            except subprocess.TimeoutExpired:
                return _record_commit_failure(
                    team_root, "add timeout", push=push)
            except (OSError, subprocess.SubprocessError) as exc:
                return _record_commit_failure(
                    team_root, f"add error: {exc}", push=push)
            if arc != 0:
                return _record_commit_failure(
                    team_root,
                    f"add failed: {((aerr or aout) or '').strip()}",
                    push=push,
                )

            if not _has_staged_changes(
                team_root,
                DEFAULT_TIMEOUT,
                paths=scoped_paths,
            ):
                return CommitResult(
                    ok=False,
                    committed=False,
                    detail="nothing to commit",
                )

            commit_args = [
                "-C", team_root, "commit", "-m", str(message)]
            if scoped_paths is not None:
                commit_args += ["--", *scoped_paths]
            try:
                crc, cout, cerr = run_git(
                    commit_args, timeout=DEFAULT_TIMEOUT)
            except subprocess.TimeoutExpired:
                return _record_commit_failure(
                    team_root, "commit timeout", push=push)
            except (OSError, subprocess.SubprocessError) as exc:
                return _record_commit_failure(
                    team_root, f"commit error: {exc}", push=push)
            if crc != 0:
                return _record_commit_failure(
                    team_root,
                    f"commit failed: {((cerr or cout) or '').strip()}",
                    push=push,
                )

            commit_detail = sanitize_git_detail(
                (cout or "").strip() or "committed")
            if not push:
                return CommitResult(
                    ok=True,
                    committed=True,
                    pushed=False,
                    detail=commit_detail,
                )

            sync = _sync_main_locked(
                team_root,
                timeout=timeout,
                deadline=deadline,
            )
            if sync.ok:
                return CommitResult(
                    ok=True,
                    committed=True,
                    pushed=True,
                    detail=f"committed; {sync.detail}",
                )
            return CommitResult(
                ok=True,
                committed=True,
                pushed=False,
                detail=f"committed; {sync.detail}",
            )
    except Exception as exc:  # public API is deliberately non-raising
        return _record_commit_failure(
            team_root,
            f"unexpected commit error: {exc}",
            push=push,
        )


def _run_publication_push(
    team_root: str, args: list, timeout: int = NET_TIMEOUT
):
    """Compatibility wrapper for the unrelated workflow-strip operation."""
    with _repo_sync_lock(team_root, DEFAULT_TIMEOUT) as acquired:
        if not acquired:
            return 1, "", "repository sync lock unavailable"
        return run_git(args, timeout=timeout)



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
             "fetch", "--quiet", "--no-tags", remote],
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return FetchResult(ok=False, detail="fetch timeout")
    except (OSError, subprocess.SubprocessError) as exc:
        return FetchResult(ok=False, detail=f"fetch exec error: {exc}")
    if rc == 0:
        return FetchResult(ok=True, detail="fetched")
    return FetchResult(
        ok=False, detail=sanitize_git_detail((err or out) or ""))


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
            ["-C", team_root,
             "-c", "push.followTags=false",
             "-c", "push.recurseSubmodules=check",
             *http_timeout_opts(NET_TIMEOUT),
             "push", "--no-follow-tags", "--recurse-submodules=check",
             "origin", "refs/heads/main:refs/heads/main"],
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
            f"push failed: {((perr or pout) or '').strip()}"))


def _workflow_remote_still_contains_message(reason: str) -> str:
    safe_reason = sanitize_git_detail(reason, limit=200)
    return (
        f"{safe_reason}. The remote repository still contains .github/workflows. "
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
            detail=sanitize_git_detail(
                f"add failed: {((err or out) or '').strip()}"))

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
                f"commit failed: {((err or out) or '').strip()}"))

    try:
        prc, pout, perr = _run_publication_push(
            root,
            ["-C", root,
             "-c", "push.followTags=false",
             "-c", "push.recurseSubmodules=check",
             *http_timeout_opts(NET_TIMEOUT),
             "push", "--no-follow-tags", "--recurse-submodules=check",
             "origin", "refs/heads/main:refs/heads/main"],
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
                f"push failed: {((perr or pout) or '').strip()}"))

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
            return False, sanitize_git_detail((err or out) or "")
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
            return False, sanitize_git_detail((err or out) or "")
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
                          detail=sanitize_git_detail(
                              f"checkout 실패: {((err or out) or '').strip()}"))

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
