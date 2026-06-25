#!/usr/bin/env python3
"""Claude Code normalize 심 — 런타임 통역사 (스펙 02 §6).

흐름: Claude 원어 JSON(stdin) → 정규 스키마(§6.1) → 공통 스크립트에 stdin 전달.
                              ↘ 공통 스크립트 exit code·stdout 을 그대로 전파.

호출(어댑터가 배선): normalize.py <script> [args...]
  <script>  = infra/hooks/ 하위 공통 스크립트
  [args...] = 공통 스크립트에 그대로 넘길 인자

의무(§6.2):
  1. 변환  — 원어 → 정규 스키마
  2. 자가 필터 — runtime 무매처 등록 훅이 manifest 의 (script, event) match 와
                현재 발동이 불일치하면 exit 0(무동작).
  3. 시맨틱 전파 — 공통 스크립트 exit/stdout 보존(PreToolUse 차단 포함)
  4. 변환 실패 — 비-strict: exit 0 + stderr 경고 / strict: 실패 전파
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent          # agents/claude
INFRA = HERE.parents[1]                          # infra
HOOKS_DIR = INFRA / "hooks"
MANIFEST = HOOKS_DIR / "manifest.json"
EVENTS = HERE / "events.json"

# stdout/stderr UTF-8 보장 — 이 래퍼는 내부 훅 stdout/stderr 를 자기 stdout/stderr 로
# 재방출(아래 main)하므로, 한글·이모지 additionalContext 가 Windows cp949 콘솔에서
# 크래시하지 않도록 보정한다. infra 미발견 시 no-op(다른 훅과 동일 가드 패턴).
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))
try:
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 normalize 는 동작(보정만 스킵)
        return


def _load_events() -> dict:
    return json.loads(EVENTS.read_text(encoding="utf-8"))


def _load_manifest() -> list:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _reverse_event(events: dict, raw_event: str):
    """에이전트 이벤트명 → 정규 이벤트명. Claude는 동일명이지만 일반화해 역매핑."""
    mapping = events.get("events", {})
    for canonical, agent_name in mapping.items():
        if agent_name == raw_event:
            return canonical
    return raw_event  # 매핑 표에 없으면 원형 그대로 (Claude=정규 기준)


# teammode 가 등록하는 MCP 별칭 네임스페이스 접두(어댑터 resolve_server_alias 와 대칭).
# 런타임에 에이전트가 부르는 도구명은 등록 별칭(`mcp__tm-linear__create_issue`)이므로,
# 정규 스키마로 환원할 때 이 접두를 떼어 manifest 의 **정규 서버명**(linear)과 맞춘다.
# 그래야 self-filter(§6.2-2)·confirm 게이트가 manifest 매처와 일치한다.
_MCP_ALIAS_PREFIX = "tm-"


def _canonical_server(server: str) -> str:
    """등록 별칭(`tm-<provider>`) → 정규 서버명(<provider>). 접두 없으면 그대로.

    어댑터 resolve_server_alias 의 역(逆). 사용자가 직접 등록한 동명 서버(`linear`)는
    접두가 없으니 무변경 — teammode 별칭만 환원된다.
    """
    if server.startswith(_MCP_ALIAS_PREFIX):
        return server[len(_MCP_ALIAS_PREFIX):]
    return server


def _parse_mcp(events: dict, tool_name: str):
    """Claude tool_name 이 mcp__server__tool 형식이면 (정규서버, tool) 반환, 아니면 None.

    server 는 등록 별칭(`tm-<provider>`)일 수 있으므로 정규 서버명으로 환원해 반환한다
    (manifest 매처·confirm 게이트가 정규 서버명 기준이라 — §2.5).
    """
    fmt = events.get("mcp_tool_format", "mcp__{server}__{tool}")
    # 템플릿을 정규식으로 — {server}/{tool} 을 캡처 그룹으로
    pattern = "^" + re.escape(fmt).replace(
        re.escape("{server}"), r"(?P<server>.+?)").replace(
        re.escape("{tool}"), r"(?P<tool>.+)") + "$"
    m = re.match(pattern, tool_name or "")
    if not m:
        return None
    return (_canonical_server(m.group("server")), m.group("tool"))


def _reverse_action(events: dict, tool_name: str):
    """Claude tool_name → 정규 행위 클래스. actions 매처 문자열에 매칭."""
    for canonical, matcher in events.get("actions", {}).items():
        if not matcher:
            continue
        # 매처는 "Write|Edit" 같은 OR 패턴 — | 로 쪼개 동등 비교
        alts = matcher.split("|")
        if tool_name in alts:
            return canonical
    return None


def normalize(raw: dict, events: dict) -> dict:
    """Claude 원어 → 정규 입력 스키마(§6.1)."""
    raw_event = raw.get("hook_event_name") or raw.get("event") or ""
    event = _reverse_event(events, raw_event)
    out = {"event": event, "agent": events.get("agent", "claude"), "raw": raw}

    if event == "UserPromptSubmit":
        out["prompt"] = raw.get("prompt", "")

    tool_name = raw.get("tool_name", "")
    tool_input = raw.get("tool_input", {}) or {}

    if event in ("PreToolUse", "PostToolUse") and tool_name:
        mcp = _parse_mcp(events, tool_name)
        if mcp:
            server, tool = mcp
            out["tool"] = {"kind": "mcp", "server": server, "name": tool}
        else:
            out["tool"] = {"kind": "builtin", "name": tool_name}
            action = _reverse_action(events, tool_name)
            if action:
                out["action"] = action
                files = []
                fp = tool_input.get("file_path")
                if fp:
                    files = [fp]
                out["files"] = files
    return out


def _matches_filter(entry: dict, canonical: dict) -> bool:
    """manifest 엔트리의 match 와 현재 정규 발동이 일치하는지(§6.2-2 자가 필터)."""
    match = entry.get("match")
    if not match:
        return True  # 무매처 = 전체 매칭
    if "action" in match:
        return canonical.get("action") == match["action"]
    if "mcp" in match:
        tool = canonical.get("tool") or {}
        if tool.get("kind") != "mcp":
            return False
        return (tool.get("server") == match["mcp"]["server"]
                and tool.get("name") == match["mcp"]["tool"])
    return True


def _lookup_entry(manifest: list, script: str, event: str,
                  canonical: dict | None = None,
                  extra_args: list | None = None):
    """(script, 정규 이벤트) + canonical/args 로 manifest 엔트리 정확히 조회(§6.2-2).

    같은 (script, event) 쌍이 여러 엔트리에 등록될 수 있다(예: confirm-action.py 가
    linear/create_issue 와 notion/create_page 등 서로 다른 벤더 MCP 도구를 각각
    처리하는 경우).
    단순히 첫 항목을 반환하면 실제 발동과 무관한 엔트리가 선택돼 자가필터가
    오작동한다 — 이를 방지하기 위해 아래 우선순위로 엔트리를 선택한다:

    1. (script, event) + match(canonical) 가 일치하는 엔트리 — 가장 구체적
    2. (script, event) + args(marker) 가 extra_args 첫 인자와 일치하는 엔트리
    3. (script, event) 만 일치하고 match 가 없는 엔트리(무매처 = 전체 매칭)
    4. (script, event) 만 일치하는 첫 엔트리(기존 동작 — 중복 엔트리 없는 경우)

    canonical 이나 extra_args 를 주지 않으면 기존 동작(첫 매칭)으로 폴백한다.
    """
    candidates = [e for e in manifest
                  if e.get("script") == script and e.get("event") == event]
    if not candidates:
        return None

    # 후보가 하나면 바로 반환 (기존 동작 유지)
    if len(candidates) == 1:
        return candidates[0]

    # 우선순위 1: canonical 로 match 가 정확히 일치하는 엔트리
    if canonical is not None:
        for entry in candidates:
            match = entry.get("match")
            if match and _matches_filter(entry, canonical):
                return entry

    # 우선순위 2: extra_args 첫 인자(marker)가 엔트리 args 와 일치하는 엔트리
    if extra_args:
        marker = extra_args[0]
        for entry in candidates:
            args = entry.get("args") or ""
            entry_marker = args if isinstance(args, str) else (args[0] if args else "")
            if entry_marker and entry_marker == marker:
                return entry

    # 우선순위 3: match 없는 엔트리(무매처 = 전체 매칭)
    for entry in candidates:
        if not entry.get("match"):
            return entry

    # 우선순위 4: 첫 엔트리(기존 동작 폴백)
    return candidates[0]


def main(argv=None) -> int:
    _ensure_utf8_io()  # 내부 훅 stdout/stderr 재방출(아래)이 cp949 콘솔에서 크래시 방지
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("[normalize] script 인자 필요\n")
        return 0
    script = argv[0]
    extra_args = argv[1:]

    events = _load_events()
    manifest = _load_manifest()

    # strict 판정을 위해 먼저 manifest 엔트리 후보를 잡아둔다 (event 모를 땐 script 우선)
    raw_text = sys.stdin.read()

    # ── 1. 변환 ──
    try:
        raw = json.loads(raw_text) if raw_text.strip() else {}
        canonical = normalize(raw, events)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        # ── 4. 변환 실패 정책 ──
        strict = any(e.get("script") == script and e.get("strict")
                     for e in manifest)
        sys.stderr.write(f"[normalize] 변환 실패: {exc}\n")
        return 1 if strict else 0

    event = canonical.get("event", "")
    entry = _lookup_entry(manifest, script, event,
                          canonical=canonical, extra_args=extra_args)

    # ── 2. 런타임 자가 필터 (runtime 무매처 등록인 경우) ──
    if entry is not None and entry.get("fallback") == "runtime":
        if not _matches_filter(entry, canonical):
            return 0  # 무동작 — 현재 발동이 이 훅 대상 아님

    # ── 3. 공통 스크립트 실행 + 시맨틱 전파 ──
    script_path = HOOKS_DIR / script
    proc = subprocess.run(
        [sys.executable, str(script_path)] + extra_args,
        # ensure_ascii(기본 True): stdin 을 순수 ASCII(\uXXXX 이스케이프)로 보내,
        # 자식이 어떤 locale(Windows cp949 등)로 sys.stdin 을 디코드해도 안전하다
        # (자식 json.loads 가 원복). 부모 encoding 만 UTF-8 로 맞춰선 자식 디코드가
        # locale 이라 한글에서 깨진다 — ASCII 로 보내는 게 OS 무관 정답(P0).
        input=json.dumps(canonical),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
