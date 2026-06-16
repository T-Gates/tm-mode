#!/usr/bin/env python3
"""confirm-action — PreToolUse 차단 훅 (공통 스크립트, 정규 스키마 전용).

스펙 §2.10: 정규 입력 스키마만 인지하며 에이전트를 모른다. normalize 심이 원어를
정규형으로 바꿔 stdin 으로 넘긴다. manifest 는 이 훅을 linear/create_issue 의 PreToolUse
매처로 등록한다(strict, fallback runtime, enforcement block).

정규 입력(stdin):
  { "event": "PreToolUse",
    "tool": { "kind": "mcp", "server": "linear", "name": "create_issue" },
    "agent": "claude", "raw": {...} }

역할: linear 이슈 생성처럼 되돌리기 어려운 외부 부수효과 동작을 사람이 의식적으로
확인하도록 **기본 차단**한다. 사람이 의식적으로 남긴 allow 신호(아래)가 있을 때만 통과.

차단 시맨틱(§2.10-3 시맨틱 전파):
  Claude Code PreToolUse 차단은 **exit 2 + stdout 의 permissionDecision="deny" JSON** 두
  경로 모두로 표현한다(normalize 가 exit code·stdout 을 그대로 전파 → 차단 보존).

────────────────────────────────────────────────────────────────────────────
⚠️ `.acme-active` 가드(빌드 안전): 마커 없으면(teammode off) 즉시 no-op exit 0.
   teammode 가 꺼진 채 일상 작업 중에는 차단을 걸지 않는다.

⚠️ Codex 한계(N5/§2.11): Codex 는 events.json 에서 `PreToolUse: null` 이라 이 차단 훅이
   **애초 등록되지 않는다**(어댑터 sync 가 enforcement:block 의 "차단 강제 상실"을 [warn]
   으로 표면화 — 무음 누락 0). 즉 Codex 에서는 차단이 불가하며 경고만 가능하다. 이
   스크립트의 exit-2 차단은 Claude 처럼 PreToolUse 차단을 표현하는 에이전트에서만 발효한다.

────────────────────────────────────────────────────────────────────────────
allow 신호 = **모델 비제어 채널** (보안 핵심):
   marker(=manifest `args` 의 첫 인자, 예 "acme-linear-create-allow")는 settings.json 에
   노출되는 **공개 고정값**이라 모델이 안다. 따라서 모델이 만들 수 있는 입력
   (raw 의 tool_input — 이슈 제목·본문 등)에서 marker 를 찾으면 **차단 우회**가 된다
   (모델이 title 에 토큰을 써넣으면 통과). 그래서 allow 판정은 raw 본문을 절대 보지 않고
   사람만 남길 수 있는 두 채널만 본다(둘 중 하나라도 있으면 통과):

   1) 환경변수  `TEAMMODE_CONFIRM` 값이 marker 와 일치 (사람이 셸/래퍼에서 export).
   2) 신호 파일  `<team_root>/.teammode-confirm/<marker>` 가 존재하고 신선함
      (사람이 의식적으로 생성; 모델은 create_issue tool_input 으로 이 경로를 만들 수 없다).
      신선도: 기본 300초(acme-toolkit 원형의 confirm TTL 과 동일). 만료되면 무효(재확인 필요).

   acme-toolkit 원형(infra/hooks/confirm-action.py)도 allow 를 **파일시스템 confirm
   플래그**(/tmp/<flag>-<USER>)로 읽지, tool 페이로드에서 읽지 않는다 — 그 안전 패턴을 따른다.
"""
from __future__ import annotations

import json
import os
import sys
import time

# stdout UTF-8 보장 — 한글 차단 사유 json 이 Windows cp949 stdout 에서 크래시 방지.
try:
    from pathlib import Path as _Path
    _infra = str(_Path(__file__).resolve().parent.parent)
    if _infra not in sys.path:
        sys.path.insert(0, _infra)
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 훅은 동작(보정만 스킵)
        return


# allow 신호 파일의 신선도 한계(초). acme-toolkit 원형의 confirm TTL 과 동일.
CONFIRM_TTL_SECONDS = 300

# 이 훅이 차단 대상으로 삼는 정규 액션(P2 defense-in-depth). N5 확장 시 여기에 추가.
TARGET_SERVER = "linear"
TARGET_NAME = "create_issue"


def _team_root() -> str:
    """런타임 훅의 팀 루트 = 환경변수 TEAMMODE_HOME (없으면 cwd). session-start 와 동일."""
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


def _has_human_allow(root: str, marker: str) -> bool:
    """사람이 의식적으로 남긴 allow 신호가 있는지 — **모델 비제어 채널만** 본다.

    절대 data(raw 포함)를 보지 않는다: raw 에는 모델이 작성한 tool_input(이슈 제목·본문)이
    실려 있어 공개 고정값인 marker 를 거기 써넣어 우회할 수 있기 때문이다(보안 결함 보정).
    사람만 만들 수 있는 두 경로만 신뢰한다.
    """
    if not marker:
        return False

    # 채널 1: 환경변수 (사람이 셸/래퍼에서 export). 정확 일치만 허용.
    if os.environ.get("TEAMMODE_CONFIRM") == marker:
        return True

    # 채널 2: 신호 파일 (사람이 의식적으로 생성). 신선할 때만 유효.
    flag_path = os.path.join(root, ".teammode-confirm", marker)
    try:
        age = time.time() - os.path.getmtime(flag_path)
    except OSError:
        return False
    return 0 <= age < CONFIRM_TTL_SECONDS


def _deny(reason: str) -> None:
    """Claude PreToolUse 차단 결정 JSON 을 stdout 으로 출력(+ 호출부가 exit 2)."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.stderr.write(f"[teammode] 차단: {reason}\n")


def main() -> int:
    _ensure_utf8_io()  # 한글 차단 사유 json 출력이 Windows cp949 stdout 에서 크래시 방지
    # allow marker = 어댑터가 넘긴 첫 인자(manifest args). 없을 수도 있다.
    marker = sys.argv[1] if len(sys.argv) > 1 else ""

    # ── 0. 입력 파싱 ── (strict 변환 실패는 normalize 가 처리; 여기 도달하면 정규형)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        # 파싱 불가 → 차단 훅이므로 보수적으로 막지 않고 통과(normalize strict 가 상위 게이트).
        return 0

    if data.get("event") != "PreToolUse":
        return 0

    root = _team_root()

    # ── 1. .acme-active 가드: 마커 없으면 차단도 안 함(빌드 안전) ──
    if not os.path.isfile(os.path.join(root, ".acme-active")):
        return 0

    # ── 2. 정규 스키마로 대상 확인 (linear/create_issue) ──
    # 런타임 자가 필터(§2.10-2)가 normalize 단에서 이미 매처 불일치를 걸러주지만,
    # 공통 스크립트도 정규 스키마로 한 번 더 자기 대상(server·name)을 확인한다
    # (방어적 정확화 — 무관 MCP 는 통과. N5 확장 시 TARGET_* 추가).
    tool = data.get("tool") or {}
    if tool.get("kind") != "mcp":
        return 0
    if not (tool.get("server") == TARGET_SERVER and tool.get("name") == TARGET_NAME):
        return 0  # 대상 액션이 아님 → 차단 안 함(통과)

    # ── 3. 사람의 명시적 allow 신호(모델 비제어 채널)가 있으면 통과, 없으면 차단 ──
    if _has_human_allow(root, marker):
        return 0

    _deny("linear 이슈 생성은 사람 확인이 필요합니다(teammode confirm-action). "
          "의도한 동작이면 명시적으로 승인 후 재시도하세요.")
    return 2  # PreToolUse 차단 — normalize 가 exit code 를 그대로 전파


if __name__ == "__main__":
    raise SystemExit(main())
