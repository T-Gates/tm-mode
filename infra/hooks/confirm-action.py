#!/usr/bin/env python3
"""confirm-action — PreToolUse 차단 훅 (공통 스크립트, 정규 스키마 전용).

스펙 §2.10 / S6 일반화: 정규 입력 스키마만 인지하며 에이전트를 모른다. normalize 심이
원어를 정규형으로 바꿔 stdin 으로 넘긴다. manifest 에 side-effect 도구별 PreToolUse
엔트리를 등록하면 **어떤 server/tool 이든** 이 스크립트 하나로 처리한다(하드코딩 없음).

정규 입력(stdin):
  { "event": "PreToolUse",
    "tool": { "kind": "mcp", "server": "<서버>", "name": "<도구>" },
    "agent": "claude", "raw": {...} }

manifest args 첫 인자 = marker (예 "teammode-issues-create-allow").
이 훅은 manifest 로 등록된 (server, tool) 쌍에만 차단을 적용한다.
판정 흐름:
  1) 입력 tool.server / tool.name 추출
  2) manifest 를 런타임 읽기 → 현재 입력과 일치하는 PreToolUse 엔트리 탐색
  3) 일치 엔트리 있으면 해당 엔트리의 args(marker) 를 사용해 allow 판정
  4) allow 신호 없으면 차단(exit 2 + deny JSON)
  5) 일치 엔트리 없으면 통과(exit 0) — 대상 아님

역할: 되돌리기 어려운 외부 부수효과 동작(예: 벤더 MCP 의 linear/create_issue 처럼
이슈를 실제 생성하는 도구)을 사람이 의식적으로 확인하도록 **기본 차단**한다. 사람이
의식적으로 남긴 allow 신호(아래)가 있을 때만 통과. read 계열(list/get 등)은 manifest
미등록 → 이 훅 미도달. (L2: 동작은 AI 가 벤더 MCP 도구를 직접 호출하고, confirm 게이트는
그 벤더 도구에 manifest 엔트리로 붙는다 — teammode 단일 서버 매처는 P2 에서 폐기됨.)

차단 시맨틱(§2.10-3 시맨틱 전파):
  Claude Code PreToolUse 차단은 **exit 2 + stdout 의 permissionDecision="deny" JSON** 두
  경로 모두로 표현한다(normalize 가 exit code·stdout 을 그대로 전파 → 차단 보존).

────────────────────────────────────────────────────────────────────────────
⚠️ `.teammode-active` 가드(빌드 안전): teammode 비활성 시 즉시 no-op exit 0.
   teammode 가 꺼진 채 일상 작업 중에는 차단을 걸지 않는다.

⚠️ Codex: Codex hooks 는 `PreToolUse` 를 지원하므로 이 차단 훅도 config.toml 에 등록된다.
   normalize 가 Codex 원어 입력을 정규형으로 바꾼 뒤 이 스크립트의 exit-2 차단을 그대로
   전파한다. 에이전트별 차이는 어댑터/events.json/normalize 에만 둔다.

────────────────────────────────────────────────────────────────────────────
allow 신호 = **모델 비제어 채널** (보안 핵심):
   marker(=manifest `args` 의 첫 인자, 예 "teammode-issues-create-allow")는
   settings.json 에 노출되는 **공개 고정값**이라 모델이 안다. 따라서 모델이 만들 수 있는
   입력(raw 의 tool_input — 이슈 제목·본문 등)에서 marker 를 찾으면 **차단 우회**가 된다
   (모델이 title 에 토큰을 써넣으면 통과). 그래서 allow 판정은 raw 본문을 절대 보지 않고
   사람만 남길 수 있는 두 채널만 본다(둘 중 하나라도 있으면 통과):

   1) 환경변수  `TEAMMODE_CONFIRM` 값이 marker 와 일치 (사람이 셸/래퍼에서 export).
   2) 신호 파일  `<team_root>/.teammode-confirm/<marker>` 가 존재하고 신선함
      (사람이 의식적으로 생성; 모델은 tool_input 으로 이 경로를 만들 수 없다).
      신선도: 기본 300초(tgates-toolkit 원형의 confirm TTL 과 동일). 만료되면 무효(재확인 필요).

   tgates-toolkit 원형(infra/hooks/confirm-action.py)도 allow 를 **파일시스템 confirm
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


# allow 신호 파일의 신선도 한계(초). tgates-toolkit 원형의 confirm TTL 과 동일.
CONFIRM_TTL_SECONDS = 300

# ── S6 일반화: TARGET_SERVER/TARGET_NAME 하드코딩 제거 ──────────────────────
# 대상 판정은 manifest.json 의 (server, tool) 엔트리 기반으로 동적으로 수행한다.
# 하드코딩된 상수 없음 — 새 도구 추가는 manifest 엔트리만 수정하면 된다.


def _team_root() -> str:
    """런타임 훅의 팀 루트 = 환경변수 TEAMMODE_HOME (없으면 cwd). session-start 와 동일."""
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


# 팀 레포 표식 — install_lib.has_team_marker(_TEAM_MARKERS)와 동일 규약(드리프트 주의).
_TEAM_MARKERS = (".git", "team.config.json", "memory")


def _warn_if_stale_home(root: str) -> None:
    """TEAMMODE_HOME 이 설정됐는데 유효한 팀 루트가 아니면 stderr 한 줄 경고 (이슈 #9a).

    레포 이동/이름변경 후 env 가 옛 경로를 가리키면 이 차단 훅이 조용히 죽어
    (.teammode-active 부재 exit 0 = 게이트가 소리 없이 열림) 원인 진단이 불가했다.
    stdout 은 deny JSON 채널이므로 경고는 stderr 로만, 한 줄로 내고 거동(통과 exit 0)은
    바꾸지 않는다. 팀 표식이 있는데 .teammode-active 만 없는 정상 off 상태는 침묵한다.
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
        pass  # 경고 실패가 훅을 막지 않는다


def _load_manifest_targets() -> dict[tuple[str, str], str] | None:
    """manifest.json 에서 PreToolUse confirm-action 대상 (server, tool) → marker 매핑을 반환.

    manifest 를 런타임에 읽어 동적으로 대상을 결정한다. 하드코딩 없음.
    반환: {("linear", "create_issue"): "teammode-linear-create-allow", ...}

    manifest 파싱 실패 시 None 반환 — 호출자가 fail-closed 처리를 결정한다.
    이 함수가 호출되는 시점은 이미 .teammode-active 가드를 통과한 후이다.
    """
    try:
        from pathlib import Path as _Path
        manifest_path = _Path(__file__).resolve().parent / "manifest.json"
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    targets: dict[tuple[str, str], str] = {}
    for entry in entries:
        if entry.get("event") != "PreToolUse":
            continue
        if entry.get("script") != "confirm-action.py":
            continue
        match = entry.get("match") or {}
        mcp = match.get("mcp") or {}
        server = mcp.get("server")
        tool = mcp.get("tool")
        if not server or not tool:
            continue
        # args: manifest では 文字列(単一 marker)または リスト先頭要素
        args = entry.get("args") or ""
        marker = args if isinstance(args, str) else (args[0] if args else "")
        targets[(server, tool)] = marker
    return targets


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
    argv_marker = sys.argv[1] if len(sys.argv) > 1 else ""

    # ── 0. 입력 파싱 ── (strict 변환 실패는 normalize 가 처리; 여기 도달하면 정규형)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        # 파싱 불가 → 차단 훅이므로 보수적으로 막지 않고 통과(normalize strict 가 상위 게이트).
        return 0

    if data.get("event") != "PreToolUse":
        return 0

    root = _team_root()
    _warn_if_stale_home(root)  # 스테일 TEAMMODE_HOME 표면화(이슈 #9a) — 거동 불변

    # ── 1. .teammode-active 가드: teammode 비활성 시 차단하지 않음(빌드 안전) ──
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        return 0

    # ── 2. 정규 스키마로 대상 확인 (manifest 기반 동적 판정 — S6 일반화) ──
    # 런타임 자가 필터(§2.10-2)가 normalize 단에서 이미 매처 불일치를 걸러주지만,
    # 공통 스크립트도 정규 스키마로 한 번 더 자기 대상(server·name)을 확인한다
    # (방어적 정확화 — 무관 MCP 는 통과).
    #
    # S6: TARGET_SERVER/TARGET_NAME 하드코딩 제거 → manifest.json 을 런타임에 읽어
    # (server, tool) → marker 매핑을 동적으로 구성한다. 새 도구 추가 시 manifest 만 수정.
    tool = data.get("tool") or {}
    if tool.get("kind") != "mcp":
        return 0

    server = tool.get("server") or ""
    name = tool.get("name") or ""

    if not server or not name:
        return 0

    # manifest 에 등록된 (server, tool) 쌍인지 확인
    # manifest 로드 실패 시 None 반환:
    #   - marker 인자가 있으면 → 보수적 차단(fail-closed). marker 를 받았는데 manifest 를
    #     못 읽으면 게이트 대상 여부를 판정할 수 없어 안전하게 막는다(#6).
    #   - marker 없이 호출됐으면 → 통과(훅이 이 도구의 게이트로 지정되지 않은 경우).
    targets = _load_manifest_targets()
    if targets is None:
        if argv_marker:
            # marker 있고 manifest 로드 실패 → fail-closed
            _deny("manifest 로드 실패로 안전 차단(fail-closed). manifest.json 을 확인하세요.")
            return 2
        return 0  # marker 없음 → 게이트 대상 미지정 → 통과
    if (server, name) not in targets:
        return 0  # 대상 액션이 아님 → 차단 안 함(통과)

    # allow 판정은 **manifest 의 marker 를 기준**으로 한다. 실제 입력 (server, name) 으로
    # 찾은 manifest target 의 marker 만 신뢰한다 — argv_marker 는 배선(normalize→hook)이
    # 넘긴 값이라, 스테일/오배선 시 다른 도구의 marker 로 승인 우회가 가능하다.
    # argv_marker 가 있는데 manifest_marker 와 다르면 fail-closed deny(오배선 차단).
    manifest_marker = targets[(server, name)]
    if argv_marker and argv_marker != manifest_marker:
        _deny(
            f"marker 불일치 — argv={argv_marker!r} != manifest={manifest_marker!r} "
            "(오배선 의심, fail-closed 차단)."
        )
        return 2

    # ── 3. 사람의 명시적 allow 신호(모델 비제어 채널)가 있으면 통과, 없으면 차단 ──
    if _has_human_allow(root, manifest_marker):
        return 0

    _deny(
        f"{server}/{name} 은 사람 확인이 필요합니다(teammode confirm-action). "
        "의도한 동작이면 명시적으로 승인 후 재시도하세요."
    )
    return 2  # PreToolUse 차단 — normalize 가 exit code 를 그대로 전파


if __name__ == "__main__":
    raise SystemExit(main())
