#!/usr/bin/env python3
"""auto-commit — PostToolUse/file_edit 자동 커밋 훅 (공통 스크립트, 정규 스키마 전용).

스펙 §2.10: 이 스크립트는 **정규 입력 스키마(§2.10)만 인지**하며 특정 에이전트를 모른다.
normalize 심(§2.10)이 원어를 정규형으로 바꿔 stdin 으로 넘긴다. file_edit 발동 시,
정규스키마가 **지목한 파일만** 스테이징해 팀 레포에 자동 커밋한다(로컬만, push 금지).

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
  - **push 실패 가시화(이슈 #23)**: 비차단은 유지하되 **조용히 묻지 않는다**. push 못 한
    채 커밋만 쌓이면(committed & not pushed) sync-warning 마커 기록 + stderr 경고를 남겨
    다음 세션 시작(session-start)이 크게 표면화한다. push 성공 시 마커를 지운다.
    **커밋 거동 자체는 불변** — 가시화만 추가.
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
        print(f"[teammode] TEAMMODE_HOME이 유효한 팀 루트가 아닙니다: {root} — "
              "레포 이동/이름변경 시 셸 프로파일의 TEAMMODE_HOME을 갱신하세요",
              file=sys.stderr)
    except (OSError, UnicodeError):
        pass  # 경고 실패가 훅을 막지 않는다(철칙: 비차단)


def _kick_push_worker(root: str) -> None:
    """push-worker 를 detach 로 kick(#45). 무raise — kick 실패는 커밋을 막지 않는다.

    - POSIX: start_new_session=True 로 세션 분리(훅 프로세스 종료와 무관하게 생존).
    - Windows: DETACHED_PROCESS 시도하되 **detach 생존을 correctness 로 믿지 않는다**
      — spawn 실패/조기사망해도 pending ledger 가 남아 session-start recovery 가
      재kick 한다(ledger 가 안전장치, 코드 계약).
    - stdout/stderr DEVNULL: worker 는 조용한 백그라운드 — 가시화는 ledger/마커 몫.
    - 테스트 훅: TEAMMODE_DISABLE_PUSH_WORKER=1 이면 kick 생략(커밋·ledger 만 관찰).
    """
    if os.environ.get("TEAMMODE_DISABLE_PUSH_WORKER") == "1":
        return
    try:
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "push-worker.py")
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": root,
        }
        if os.name == "nt":  # pragma: no cover — Windows 는 CI 밖(계약: ledger 폴백)
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, worker, "--root", root], **kwargs)
    except Exception:  # noqa: BLE001 — spawn 실패 시 경고 1줄 + 빠른 반환(ledger 폴백)
        try:
            print("[teammode] push-worker 시작 실패 — pending 은 세션 시작 시 "
                  "재시도됩니다.", file=sys.stderr)
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

    # ── 1. 빌드 안전 핵심: .teammode-active 없으면 즉시 no-op ──
    # 어떤 git 작업보다 먼저. 마커 부재 = teammode off = 자동 커밋 절대 금지.
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        return 0

    # ── 2. file_edit 발동만 처리 ──
    if data.get("action") != "file_edit":
        return 0

    if _git_ops is None:
        return 0  # git_ops 부재 → 무동작(실패 무해)

    try:
        # ── 3. 정규스키마가 지목한 파일만 스테이징 (add -A 금지) ──
        files = data.get("files") or []
        # 절대경로 문자열만 신뢰. 없으면 스테이징할 게 없으니 우아하게 종료.
        paths = [f for f in files if isinstance(f, str) and f]
        if not paths:
            return 0

        kst = timezone(timedelta(hours=9))
        stamp = datetime.now(kst).strftime("%Y-%m-%d %H:%M")
        message = f"chore(teammode): auto-commit {stamp} KST"

        # ── 3.5 잔존 pending 즉시 가시화(#45) — '한 편집 늦은' 경고 1줄 ──
        # 이전 편집의 push 가 아직 미완이면(worker 지연/실패) 조용히 묻지 않는다.
        # 비차단: 경고만 남기고 커밋은 정상 진행(worker 가 이번 것까지 drain).
        if _git_ops.read_push_pending(root):
            print("[teammode] 이전 auto-commit 의 push 미완(pending) — "
                  "worker 가 재시도합니다.", file=sys.stderr)

        # ── 4. paths 만 스테이징 + 커밋(동기는 여기까지 — push 는 worker 위임, #45) ──
        # 종전 push=True 동기 push 는 훅 지연(statusMessage 고정 ~26s)·manifest 캡
        # 경합의 원인이었다. 이제 훅은 로컬 커밋만 완주하고, push 는 pending ledger +
        # detach worker(plain-push-only)가 담당한다. correctness 는 ledger 가 단일
        # 소스 — worker 유실(슬립·Windows detach 실패)에도 session-start 가 복원한다.
        result = _git_ops.do_commit(root, message=message, push=False, paths=paths)

        # index.lock 경합(다른 git 프로세스와 겹침)은 1s 후 1회만 재시도(#45).
        if (not getattr(result, "committed", False)
                and "index.lock" in (getattr(result, "detail", "") or "")):
            _time.sleep(1)
            result = _git_ops.do_commit(root, message=message, push=False,
                                        paths=paths)

        # ── 5. 커밋 성공 → pending 원자 기록 + worker detach kick(#45) ──
        # push 실패 가시화(이슈 #23)는 이제 worker(sync-warning)와 session-start
        # (pending×ahead 판정)가 담당한다 — 훅은 ledger 기록까지만.
        if getattr(result, "committed", False):
            _git_ops.write_push_pending(root)
            _kick_push_worker(root)
    except Exception:  # noqa: BLE001 — 철칙: 자동 커밋·push 실패가 작업을 막지 않는다
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
