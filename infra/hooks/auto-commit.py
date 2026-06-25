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
  - **add -A 금지(P1-4)**: do_commit 에 paths= 로 정규스키마가 지목한 `files` 만 넘긴다.
    무차별 스테이징(add -A)은 토큰패턴 파일·무관 변경까지 끌어와 오염·유출 위험.
  - **실패 비차단**: 어떤 예외도 삼키고 항상 exit 0. 자동 커밋·push 실패가 작업을 막지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

# git_ops 는 infra/ 에 있다(이 파일은 infra/hooks/). 단일 소스 안전장치 재사용.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import git_ops as _git_ops  # type: ignore
except ImportError:  # git_ops 부재여도 작업을 막지 않는다(실패 무해)
    _git_ops = None


def _team_root() -> str:
    """런타임 훅의 팀 루트 = 환경변수 TEAMMODE_HOME (없으면 cwd).

    런타임 훅은 에이전트 하니스가 발동하므로 `--root` 인자 통로가 없다(§1.2). read-only
    가 아닌 쓰기 훅이지만, `.teammode-active` 가드가 활성 팀 루트에서만 동작을 허용하므로
    ambient env 누수가 임의 폴더를 커밋하게 만들지 못한다. session-log-remind 와 동일.
    """
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


def main() -> int:
    # ── 0. 입력 파싱 (실패해도 세션 무차단) ──
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0

    if data.get("event") != "PostToolUse":
        return 0

    root = _team_root()

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

        # ── 4. paths 만 스테이징 + 자동 push(6/23 철학) ──
        # push 실패는 비차단: do_commit 이 커밋을 보존(ok=True·pushed=False)하므로 무해.
        _git_ops.do_commit(root, message=message, push=True, paths=paths)
    except Exception:  # noqa: BLE001 — 철칙: 자동 커밋·push 실패가 작업을 막지 않는다
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
