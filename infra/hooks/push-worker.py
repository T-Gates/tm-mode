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
    # i18n(적대검수 — B 지적: sync-warning 마커의 4번째 writer 가 스캐폴딩 없이
    # 한국어 하드코딩이었다). 다른 훅(auto-commit 등)과 동일한 defensive import +
    # _hook_lang/_t 계약 — 여기서만 git_ops 와 같은 지연 import 지점에 둔다(이
    # 파일의 sys.path 주입 자체가 main() 안에서만 일어나는 기존 구조를 따름).
    try:
        import i18n as _i18n  # type: ignore # noqa: E402
    except ImportError:
        _i18n = None

    def _hook_lang(team_root: str) -> str:
        if _i18n is None:
            return "ko"
        try:
            return _i18n.team_lang(team_root)
        except Exception:  # noqa: BLE001 — locale 해석 실패는 ko 폴백
            return "ko"

    def _t(key: str, lang: str, ko: str, **fmt) -> str:
        if lang == "en" and _i18n is not None:
            return _i18n.t(key, "en", **fmt)
        return ko.format(**fmt) if fmt else ko

    lang = _hook_lang(root)

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
            pending = git_ops.read_push_pending_state(root)
            if not pending.available:
                break  # lock/state 판정불가 — 보수적으로 ledger 와 warning 을 보존
            snapshot = git_ops.bind_legacy_pending_to_current_checkout(
                root, pending.content)
            if not snapshot:
                break  # 잔여 없음 — 정상 종료
            target_key = git_ops.pending_entry_key_for_current_checkout(
                root, snapshot)
            if not target_key:
                targets = git_ops.pending_target_summary(snapshot, root)
                git_ops.write_sync_warning(
                    root, _t("push_worker_checkout_mismatch_marker", lang,
                            "push pending 대상 checkout 불일치 — 현재 branch에서는 "
                            "보존만 함: {targets}", targets=targets))
                break
            # clear race 가드(codex P1): push 시작 전 pending **내용** 스냅샷 —
            # clear 는 "그때 그 pending"(고유 nonce 포함) 이 그대로일 때만.
            # push 도중 auto-commit 이 재기록했으면 지우지 않고 loop 가 이어 push.
            pushed, detail = git_ops.push_plain(root, git_ops.NET_TIMEOUT)
            if not pushed:
                # 실패 = sync-warning detail, pending 유지(recovery 채널이 잇는다).
                if detail == "non-fast-forward":
                    # foreground rebase가 남긴 dirty-overlap/conflict/rollback 상세가
                    # 있으면 generic worker 문구로 덮지 않는다. SessionStart가 같은
                    # worker를 재kick해도 사람이 해결할 원인이 유지돼야 한다.
                    git_ops.write_sync_warning_if_empty(
                        root, _t("push_worker_non_ff_marker", lang,
                                 "push pending; non-fast-forward — "
                                 "세션 시작 reconcile 에 위임"))
                else:
                    # 실패 상세는 credential/control-code 를 제거한 뒤 local marker 에 기록.
                    safe_detail = git_ops.sanitize_git_detail(detail)
                    git_ops.write_sync_warning(root, f"push pending; {safe_detail}")
                break
            # push 도중 checkout 이 바뀌면 성공은 다른 branch의 성공일 수 있다.
            # 시작 target과 현재 target이 동일할 때만 아래 clear 판정을 허용한다.
            if (git_ops.pending_entry_key_for_current_checkout(root, snapshot)
                    != target_key):
                targets = git_ops.pending_target_summary(snapshot, root)
                git_ops.write_sync_warning(
                    root, _t("push_worker_checkout_mismatch_marker", lang,
                            "push pending 대상 checkout 불일치 — 현재 branch에서는 "
                            "보존만 함: {targets}", targets=targets))
                break
            # push 중 새 commit 이 생겨 origin 보다 ahead 가 됐으면 old pending 도
            # 보존한다. upstream+ahead==0 이 입증될 때만 compare-and-delete 한다.
            ahead, _behind, has_upstream = git_ops._ahead_behind_raw(
                root, git_ops.DEFAULT_TIMEOUT)
            if has_upstream and ahead == 0:
                if git_ops.clear_push_pending_if_unchanged(
                        root, snapshot, target_key):
                    git_ops.clear_sync_warning_if_fully_published(root)
            # clear 거부(재기록됨)·ahead>0·판정불가 → loop 가 재확인/재push.
        else:
            # drain 한도 소진(P3): pending 잔존이면 즉시 표면화 — 10분 age 경고를
            # 기다리지 않는다.
            pending = git_ops.read_push_pending_state(root)
            if pending.available and pending.content:
                git_ops.write_sync_warning(
                    root, _t("push_worker_drain_limit_marker", lang,
                            "push pending; worker drain limit reached — "
                            "커밋 폭주 또는 반복 재기록(세션 시작 recovery 가 재시도)"))
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
