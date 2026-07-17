#!/usr/bin/env python3
"""auto-commit — PostToolUse/file_edit 자동 커밋 훅 (공통 스크립트, 정규 스키마 전용).

스펙 §2.10: 이 스크립트는 **정규 입력 스키마(§2.10)만 인지**하며 특정 에이전트를 모른다.
normalize 심(§2.10)이 원어를 정규형으로 바꿔 stdin 으로 넘긴다. file_edit 발동 시,
정규스키마가 **지목한 파일만** 스테이징해 팀 레포에 자동 커밋하고 전경 push 한다.

정규 입력(stdin):
  { "event": "PostToolUse", "action": "file_edit",
    "files": ["/abs/path", ...], "agent": "claude", "raw": {...} }

  (위 요약의 "push 금지"는 6/23 자동push 철학 전환으로 폐기 — 아래 철칙 참조.)

────────────────────────────────────────────────────────────────────────────
⚠️ 빌드 안전 핵심 — `.teammode-active` 가드 (L2-G):
  팀 루트에 `.teammode-active` 마커가 없으면(teammode off) **즉시 no-op exit 0**.
  아무 git 작업도 하지 않는다. 이 가드가 견고해야, 도그푸딩 설치된 호스트에서
  teammode 가 꺼진 채 일상 편집을 할 때 작업 레포가 자동 커밋으로 오염되지 않는다.
  (session-start.py·session-log-remind.py 의 동일 패턴.)

설계 철칙:
  - **자동 push(6/23 철학)**: do_commit(push=True) — "원격 동기화는 사람 결정" 폐기.
    팀 레포는 공유 자산이라 매 자동 커밋 즉시 push 한다. **push 실패는 비차단** —
    do_commit 이 push 실패해도 로컬 커밋을 보존(ok=True·pushed=False)하고 hook 은 exit 0.
  - **foreground reconcile + fallback(#19·#45)**: do_commit 의 bounded publication이
    먼저 fetch/status 정합을 확인한다. 안전한 worktree mutation 예약을 확보하지 못하거나
    non-ff이면 상세 sync-warning + pending ledger 를 기록하고 immutable-target worker 를
    kick 한다. worker 는 히스토리와 현재 checkout을 건드리지 않는다.
  - **push 실패 가시화(이슈 #23)**: 비차단은 유지하되 **조용히 묻지 않는다**. push 못 한
    채 커밋만 쌓이면(committed & not pushed) 상세 sync-warning 마커 + stderr 경고를 남겨
    다음 세션 시작(session-start)이 크게 표면화한다. 확인된 push 성공 시에만 지운다.
  - **add -A 금지(P1-4)**: do_commit 에 paths= 로 정규스키마가 지목한 `files` 만 넘긴다.
    무차별 스테이징(add -A)은 토큰패턴 파일·무관 변경까지 끌어와 오염·유출 위험.
  - **실패 비차단**: 어떤 예외도 삼키고 항상 exit 0. 자동 커밋·push 실패가 작업을 막지 않는다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta, timezone

# git_ops 는 infra/ 에 있다(이 파일은 infra/hooks/). 단일 소스 안전장치 재사용.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import git_ops as _git_ops  # type: ignore
except ImportError:  # git_ops 부재여도 작업을 막지 않는다(실패 무해)
    _git_ops = None
# stderr UTF-8 보장 — 한글 경고(_warn_if_stale_home)가 cp949 환경에서 mojibake 되지
# 않게(codex P2). 형제 훅(session-start 등)과 동일 패턴.
try:
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 훅은 동작(보정만 스킵)
        pass
_ensure_utf8_io()

# i18n(적대검수 — long tail) — 형제 훅(session-start 등)과 동일한 defensive import +
# _hook_lang/_t 계약. 이 파일만 스캐폴딩이 없어 [warn] 류가 전부 하드코딩 한국어였다.
try:
    import i18n as _i18n  # type: ignore
except ImportError:
    _i18n = None


def _hook_lang(root: str) -> str:
    """팀 locale → 경고 언어("ko"|"en"). i18n 부재/실패 시 ko(종전 거동 보존)."""
    if _i18n is None:
        return "ko"
    try:
        return _i18n.team_lang(root)
    except Exception:  # noqa: BLE001 — locale 해석 실패가 자동 커밋을 막지 않는다
        return "ko"


def _t(key: str, lang: str, ko: str, **fmt) -> str:
    """경고 문자열 선택 — ko 원문은 호출부 리터럴이 단일 소스(구팀 무변화 계약),
    en 은 i18n 카탈로그(hook_* 키). i18n 부재 시 ko 폴백."""
    if lang == "en" and _i18n is not None:
        return _i18n.t(key, "en", **fmt)
    return ko.format(**fmt) if fmt else ko


def _team_root() -> str:
    """런타임 훅의 팀 루트 = 환경변수 TEAMMODE_HOME (없으면 cwd).

    런타임 훅은 에이전트 하니스가 발동하므로 `--root` 인자 통로가 없다(§1.2). read-only
    가 아닌 쓰기 훅이지만, `.teammode-active` 가드가 활성 팀 루트에서만 동작을 허용하므로
    ambient env 누수가 임의 폴더를 커밋하게 만들지 못한다. session-log-remind 와 동일.
    """
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


# 팀 레포 표식 — install_lib.has_team_marker(_TEAM_MARKERS)와 동일 규약(드리프트 주의).
_TEAM_MARKERS = (".git", "team.config.json", "memory")


def _warn_if_stale_home(root: str) -> None:
    """TEAMMODE_HOME 이 설정됐는데 유효한 팀 루트가 아니면 stderr 한 줄 경고 (이슈 #9a).

    레포 이동/이름변경 후 env 가 옛 경로를 가리키면 훅이 조용히 죽어(.teammode-active
    부재 exit 0) 원인 진단이 불가했다. stdout 은 훅 출력 채널이므로 경고는 stderr 로만,
    한 줄로 내고 거동(exit 0)은 바꾸지 않는다. 팀 표식이 있는데 .teammode-active 만
    없는 정상 off 상태는 종전대로 침묵한다.
    """
    if not os.environ.get("TEAMMODE_HOME"):
        return
    if any(os.path.exists(os.path.join(root, m)) for m in _TEAM_MARKERS):
        return
    try:
        print(_t("hook_ss_stale_home_warn", _hook_lang(root),
                 "[teammode] TEAMMODE_HOME이 유효한 팀 루트가 아닙니다: {root} — "
                 "레포 이동/이름변경 시 셸 프로파일의 TEAMMODE_HOME을 갱신하세요",
                 root=root),
              file=sys.stderr)
    except (OSError, UnicodeError):
        pass  # 경고 실패가 훅을 막지 않는다(철칙: 비차단)


_WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "push-worker.py")


def _literal_repo_pathspecs(root: str, files) -> list[str]:
    """정규 hook 파일을 repo 내부 Git literal pathspec 으로 제한한다.

    ``--`` 는 option 만 막고 ``:(top,glob)**`` 같은 Git pathspec magic 은 막지 못한다.
    따라서 절대경로 또는 hook cwd 기준 상대경로를 repo 내부·비디렉터리로 검증한 뒤
    ``:(literal)`` 로 전달한다.
    삭제된 파일도 realpath/relpath 만으로 허용해 deletion auto-commit 을 보존한다.
    """
    root_real = os.path.realpath(root)
    paths: list[str] = []
    seen: set[str] = set()
    for raw in files if isinstance(files, list) else []:
        if not isinstance(raw, str) or not raw or "\0" in raw:
            continue
        candidate = os.path.realpath(
            raw if os.path.isabs(raw) else os.path.join(os.getcwd(), raw))
        try:
            if os.path.commonpath((root_real, candidate)) != root_real:
                continue
        except (OSError, ValueError):
            continue
        if candidate == root_real or os.path.isdir(candidate):
            continue
        relative = os.path.relpath(candidate, root_real)
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            continue
        literal = ":(literal)" + relative.replace(os.sep, "/")
        if literal not in seen:
            seen.add(literal)
            paths.append(literal)
    return paths


def _kick_push_worker(root: str) -> None:
    """push-worker detach kick(#45) — 공용 git_ops.kick_push_worker 위임(드리프트 방지).

    spawn 실패/조기사망해도 pending ledger 가 남아 session-start recovery 가
    재kick 한다(ledger 가 안전장치). kill-switch 로 생략된 경우는 경고 없이 침묵.
    """
    if _git_ops is None:
        return
    ok = _git_ops.kick_push_worker(root, _WORKER_PATH)
    if not ok:
        try:
            lang = _hook_lang(root)
            if os.environ.get("TEAMMODE_DISABLE_PUSH_WORKER") == "1":
                # codex P2: kill-switch 가 프로덕션 셸에 남으면 무음 pending 만
                # 쌓인다 — 비활성 사실을 확실히 표면화(테스트도 이 줄은 무해).
                print(_t("hook_ac_push_worker_disabled", lang,
                         "[teammode] push-worker 비활성(TEAMMODE_DISABLE_PUSH_WORKER)"
                         " — push 는 세션 시작 recovery 에 위임됩니다."),
                      file=sys.stderr)
            else:
                print(_t("hook_ac_push_worker_start_failed", lang,
                         "[teammode] push-worker 시작 실패 — pending 은 세션 시작 시 "
                         "재시도됩니다."), file=sys.stderr)
        except (OSError, UnicodeError):
            pass


def main() -> int:
    # ── 0. 입력 파싱 (실패해도 세션 무차단) ──
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0

    if data.get("event") != "PostToolUse":
        return 0

    root = _team_root()
    _warn_if_stale_home(root)  # 스테일 TEAMMODE_HOME 표면화(이슈 #9a) — 거동 불변
    lang = _hook_lang(root)  # i18n(적대검수 — long tail): 이하 경고들이 공유
    lease_owner = (
        _git_ops.hook_edit_lease_owner(data) if _git_ops is not None else "")

    # ── 1. 빌드 안전 핵심: .teammode-active 없으면 즉시 no-op ──
    # 어떤 git 작업보다 먼저. 마커 부재 = teammode off = 자동 커밋 절대 금지.
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        if lease_owner:
            _git_ops.end_hook_edit_lease(root, lease_owner)
        return 0

    # ── 2. file_edit 발동만 처리 ──
    if data.get("action") != "file_edit":
        if lease_owner:
            _git_ops.end_hook_edit_lease(root, lease_owner)
        return 0

    if _git_ops is None:
        return 0  # git_ops 부재 → 무동작(실패 무해)

    try:
        # ── 3. 정규스키마가 지목한 파일만 스테이징 (add -A 금지) ──
        files = data.get("files") or []
        # 정규화된 절대/상대경로 중 repo 내부 파일만 literal pathspec 으로 바꾼다.
        paths = _literal_repo_pathspecs(root, files)
        if not paths:
            return 0

        kst = timezone(timedelta(hours=9))
        stamp = datetime.now(kst).strftime("%Y-%m-%d %H:%M")
        message = f"chore(teammode): auto-commit {stamp} KST"

        # ── 3.5 잔존 pending 즉시 가시화(#45) — '한 편집 늦은' 경고 1줄 ──
        # 이전 편집의 push 가 아직 미완이면(worker 지연/실패) 조용히 묻지 않는다.
        # 비차단: 경고만 남기고 이번 전경 commit/push 복구는 정상 진행한다.
        pending_state = _git_ops.read_push_pending_state(root)
        pending_snapshot = (
            _git_ops.bind_legacy_pending_to_current_checkout(
                root, pending_state.content)
            if pending_state.available else "")
        pending_target_key = (
            _git_ops.pending_entry_key_for_current_checkout(root, pending_snapshot)
            if pending_snapshot else "")
        if pending_target_key:
            print(_t("hook_ac_prior_push_pending", lang,
                     "[teammode] 이전 auto-commit 의 push 미완(pending) — "
                     "전경 publication 이 재시도하고 worker 는 fallback 으로 "
                     "대기합니다."), file=sys.stderr)
        elif pending_snapshot:
            targets = _git_ops.pending_target_summary(pending_snapshot, root)
            print(_t("hook_ac_prior_push_other_checkout", lang,
                     "[teammode] 다른 checkout의 push pending을 보존합니다. "
                     "현재 편집은 별도로 publication합니다: {targets}",
                     targets=targets), file=sys.stderr)

        # ── 4. paths 만 스테이징 + bounded foreground publication(#19) ──
        # fetch/status와 exact push를 한 예산 안에서 수행한다. exact PreToolUse lease가
        # 있고, ledger를 읽을 수 있으며, 현재 checkout을 가리키는 immutable pending이
        # 없을 때만 ff/rebase한다. 기존 H1 pending을 둔 채 H2를 rebase하면 H1의 OID가
        # 바뀌어 worker가 영구 non-ff가 되므로 해당 경로는 commit+pending으로 보존한다.
        may_reconcile_worktree = bool(
            lease_owner
            and pending_state.available
            and _git_ops.pending_allows_current_checkout_reconcile(
                root, pending_snapshot))
        mutation_kwargs = ({
            "_allow_bound_mutation": True,
            "_edit_lease_owner": lease_owner,
        } if may_reconcile_worktree else {})
        result = _git_ops.do_commit(
            root, message=message, push=True, paths=paths,
            reconcile_before_push=True, **mutation_kwargs)

        # index.lock 경합(다른 git 프로세스와 겹침)은 1s 후 1회만 재시도(#45).
        if (not getattr(result, "committed", False)
                and "index.lock" in (getattr(result, "detail", "") or "")):
            _time.sleep(1)
            result = _git_ops.do_commit(
                root, message=message, push=True, paths=paths,
                reconcile_before_push=True, **mutation_kwargs)

        # ── 5. 커밋 성공 → foreground 성공 정리 또는 worker fallback(#45) ──
        if getattr(result, "committed", False):
            if getattr(result, "pushed", False):
                # 시작 때 본 pending 만 compare-and-delete 한다. 그 사이 다른 훅이 새
                # nonce 를 썼다면 절대 지우지 않는다(#45 clear race 차단).
                if (pending_target_key
                        and _git_ops.pending_entry_covered_by_publication(
                            root, pending_snapshot, pending_target_key,
                            getattr(result, "pending_identity", None),
                            getattr(result, "pending_target", None))):
                    _git_ops.clear_push_pending_if_unchanged(
                        root, pending_snapshot, pending_target_key)
                success_detail = getattr(result, "detail", "") or ""
                if ("upstream setup skipped:" in success_detail
                        or "tracking update skipped:" in success_detail):
                    _git_ops.write_sync_warning(
                        root, _git_ops.sanitize_git_detail(success_detail))
                else:
                    _git_ops.clear_sync_warning_after_exact_publication(
                        root, getattr(result, "pending_identity", None),
                        getattr(result, "pending_target", None))
            else:
                detail = _git_ops.sanitize_git_detail(
                    getattr(result, "detail", "") or
                    "unknown auto-commit push failure")
                if _git_ops.write_push_pending(
                        root, getattr(result, "pending_identity", None) or {},
                        target=getattr(result, "pending_target", None)):
                    _git_ops.write_sync_warning(
                        root, _t("hook_ac_push_failed_marker", lang,
                                 "auto-commit push 실패(커밋 보존): {detail}",
                                 detail=detail))
                    print(_t(
                        "hook_ac_push_failed_print", lang,
                        "[teammode] auto-commit push 실패 — 커밋은 보존했고 "
                        "pending 재시도를 기록했습니다: {detail}", detail=detail),
                        file=sys.stderr)
                    _kick_push_worker(root)
                else:
                    # False includes history/target/CAS refusal, not only I/O;
                    # keep the original push detail without guessing a cause.
                    # 마커는 나중에 session-start 의 locale wrapper 안에 삽입되므로
                    # marker content 자체도 현재 팀 locale 로 렌더링한다.
                    _git_ops.write_sync_warning(
                        root, _t("hook_ac_pending_write_failed_marker", lang,
                                "push-pending 상태를 안전하게 갱신하지 못했습니다 — "
                                "커밋은 보존됐지만 자동 push 복구는 예약되지 "
                                "않았습니다; 원래 push 실패: {detail}",
                                detail=detail))
                    print(_t(
                        "hook_ac_pending_write_failed_print", lang,
                        "[teammode] push-pending 상태를 안전하게 갱신하지 "
                        "못했습니다 — 커밋은 보존됐지만 자동 push 복구는 "
                        "예약되지 않았습니다. 원래 push 실패: {detail}",
                        detail=detail),
                        file=sys.stderr)
        else:
            # Pre-commit interlock/blocker and real add/commit failures have no
            # commit identity, so a push-pending entry would be false.  They
            # still must not disappear silently: retain a sanitized diagnostic
            # for SessionStart and tell the current hook caller immediately.
            raw_detail = getattr(result, "detail", "") or "unknown commit failure"
            if ("nothing to commit" not in raw_detail.lower()
                    and "no paths to stage" not in raw_detail.lower()):
                detail = _git_ops.sanitize_git_detail(raw_detail)
                _git_ops.write_sync_warning(
                    root, _t(
                        "hook_ac_commit_deferred_marker", lang,
                        "auto-commit 보류(변경 미커밋): {detail}",
                        detail=detail))
                print(_t(
                    "hook_ac_commit_deferred_print", lang,
                    "[teammode] auto-commit 보류 — 변경은 커밋되지 않았습니다: "
                    "{detail}", detail=detail), file=sys.stderr)
    except Exception:  # noqa: BLE001 — 철칙: 자동 커밋·push 실패가 작업을 막지 않는다
        return 0
    finally:
        if lease_owner:
            try:
                _git_ops.end_hook_edit_lease(root, lease_owner)
            except Exception:  # noqa: BLE001 — cleanup failure must not block work
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
