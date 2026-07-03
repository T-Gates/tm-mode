#!/usr/bin/env python3
"""push-worker — auto-commit 이 detach 로 띄우는 단발 push 프로세스 (#45).

철칙:
  - **plain-push-only**: 로컬 히스토리 무접촉(rebase/fetch 복구 금지). worker 가
    rebase 복구 중일 때 사용자가 편집하면 다음 auto-commit 훅의 add/commit 이
    index.lock 으로 실패하고 훅은 예외를 삼켜 exit 0 → **편집 커밋 조용한 유실**.
    push 지연보다 명백히 나쁜 회귀라 경합 표면을 push 로 한정한다.
    non-ff 정합 복구는 기존 채널(session-start do_reconcile·teammode pull)에 위임.
  - **per-team lock 단일 실행**: 같은 팀 루트에 worker 1개만(중복 push·경합 방지).
    lock 은 pending ledger 경로 + ".lock" (O_CREAT|O_EXCL). 120s 넘은 잔재는
    stale 로 간주해 1회 회수(크래시 잔재 자기치유).
  - **drain loop(최대 3)**: push 성공 후 pending 재확인 — push 도중 auto-commit 이
    새 커밋+pending 을 썼으면 이어서 push(연타 커밋 자연 배칭). pending clear 는
    **push 성공 + ahead==0 확인 후에만**(push 중 새 커밋 유실 방지).
  - **훅 캡 밖**: detach 프로세스라 manifest timeout 의 지배를 받지 않는다 —
    PUSH_TOTAL_BUDGET 클램프 불필요, 호출당 NET_TIMEOUT 만.
  - correctness 는 ledger 가 담당: detach 생존(특히 Windows)은 신뢰 대상이 아니다 —
    worker 가 죽어도 pending 파일이 남아 session-start recovery 가 재kick 한다.
  - 절대 예외를 전파하지 않는다. stdout/stderr 는 스포너가 DEVNULL 로 버린다.

호출: python push-worker.py --root <팀루트>   (env 무신뢰 — 명시 인자만)
"""
from __future__ import annotations

import os
import sys
import time

_MAX_DRAIN_LOOPS = 3
_LOCK_STALE_SECONDS = 120


def _acquire_lock(lock_path: str) -> bool:
    """O_CREAT|O_EXCL 원자 획득. 실패(존재/권한)면 False."""
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("ascii", "replace"))
        finally:
            os.close(fd)
        return True
    except OSError:  # FileExistsError 포함 — 이미 다른 worker 실행 중
        return False


def main(argv: list) -> int:
    root = None
    it = iter(argv)
    for a in it:
        if a == "--root":
            root = next(it, None)
    if not root:
        return 2
    root = os.path.normpath(root)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import git_ops  # noqa: E402 — infra/ 경로 주입 후 import

    lock_path = git_ops.push_pending_path(root) + ".lock"
    if not _acquire_lock(lock_path):
        # stale 잔재(크래시)만 1회 회수 — 살아있는 lock 이면 조용히 양보(그가 drain).
        try:
            age = time.time() - os.stat(lock_path).st_mtime
        except OSError:
            age = 0.0
        if age <= _LOCK_STALE_SECONDS or not (
                _remove_quiet(lock_path) and _acquire_lock(lock_path)):
            return 0

    try:
        max_loops = _MAX_DRAIN_LOOPS
        env_loops = os.environ.get("TEAMMODE_WORKER_MAX_LOOPS")
        if env_loops is not None:  # 테스트 seam — 소진 경로 재현용
            try:
                max_loops = max(0, int(env_loops))
            except ValueError:
                pass
        for _ in range(max_loops):
            if not git_ops.read_push_pending(root):
                break  # 잔여 없음 — 정상 종료
            # clear race 가드(codex P1): push 시작 전 pending 스냅샷 — clear 는
            # "그때 그 pending" 이 그대로일 때만. push 도중 auto-commit 이 새 pending
            # 을 재기록했으면 지우지 않고 loop 가 이어서 push 한다.
            try:
                snapshot_ns = os.stat(
                    git_ops.push_pending_path(root)).st_mtime_ns
            except OSError:
                snapshot_ns = None
            pushed, detail = git_ops.push_plain(root, git_ops.NET_TIMEOUT)
            if not pushed:
                # 실패 = sync-warning detail, pending 유지(recovery 채널이 잇는다).
                if detail == "non-fast-forward":
                    git_ops.write_sync_warning(
                        root, "push pending; non-fast-forward — "
                              "세션 시작 reconcile 에 위임")
                else:
                    git_ops.write_sync_warning(root, f"push pending; {detail}")
                break
            # 판정불가(무 upstream/git 오류)를 (0,0)으로 접는 ahead_behind 대신
            # raw 를 사용 — has_upstream 이 입증될 때만 clear(codex P1 오판 차단).
            ahead, _behind, has_upstream = git_ops._ahead_behind_raw(
                root, git_ops.DEFAULT_TIMEOUT)
            if has_upstream and ahead == 0 and snapshot_ns is not None:
                if git_ops.clear_push_pending_if_unchanged(root, snapshot_ns):
                    git_ops.clear_sync_warning(root)
            # clear 거부(재기록됨)·ahead>0·판정불가 → loop 가 재확인/재push.
        else:
            # drain 한도 소진(P3): pending 잔존이면 즉시 표면화 — 10분 age 경고를
            # 기다리지 않는다.
            if git_ops.read_push_pending(root):
                git_ops.write_sync_warning(
                    root, "push pending; worker drain limit reached — "
                          "커밋 폭주 또는 반복 재기록(세션 시작 recovery 가 재시도)")
        return 0
    except Exception:  # noqa: BLE001 — 훅 철칙: worker 실패가 아무것도 막지 않는다
        return 0
    finally:
        _remove_quiet(lock_path)


def _remove_quiet(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
