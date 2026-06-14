#!/usr/bin/env python3
"""Claude Code 어댑터 — 설치 시점 번역기 (스펙 02 §5).

정규형 manifest(infra/hooks/manifest.json) + events.json(번역표)을 읽어
Claude Code의 settings.json hooks에 멱등 동기화한다.

CLI:
  adapter.py sync [--on|--off]   manifest → settings.json (멱등)
  adapter.py uninstall           teammode 등록 훅 역순 제거

설계 불변식(스펙 02 §2):
  - 공통 스크립트는 직접 등록하지 않는다 — 반드시 normalize.py 경유로 배선(§5.1-2).
  - 에이전트 고유 지식(이벤트·action·mcp 형식)은 전부 events.json에만. 코드 분기 0.
  - teammode 소유 마커 = 커맨드가 팀 루트 하위 agents/<name>/normalize.py 를 가리킴(§5.1-5).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional


def default_python() -> str:
    """훅 명령에 쓸 파이썬 인터프리터 (크로스플랫폼, W-B).

    sys.executable(현재 인터프리터의 **절대경로**)을 쓴다 = 가장 견고:
    - Windows: 'python3' 가 PATH 에 없을 수 있으나 절대경로는 항상 유효.
    - venv/conda 등 비표준 설치도 정확히 그 인터프리터로 훅 실행(드리프트 0).
    normalize.py 도 child 실행에 sys.executable 을 쓰므로 체인 전체가 일관.
    sys.executable 이 비어 있으면(드문 임베드) 플랫폼별 폴백.
    """
    if sys.executable:
        return sys.executable
    return "python" if os.name == "nt" else "python3"


def _quote_arg(s: str) -> str:
    """셸 명령 토큰 안전 인용 — 공백/특수문자 있으면 따옴표(윈도우 경로 대비).

    이미 따옴표로 감싼 토큰은 그대로. 단순 토큰(공백·따옴표 없음)은 인용 안 함
    (기존 'python3' 동작·테스트 보존).
    """
    if not s:
        return '""'
    if s[0] in ('"', "'") and s[-1] == s[0]:
        return s  # 이미 인용됨
    if any(c in s for c in ' \t"'):
        return '"' + s.replace('"', '\\"') + '"'
    return s


class Adapter:
    def __init__(self, agent_dir, manifest_path, settings_path,
                 python=None, team_root=None, events=None):
        self.agent_dir = Path(agent_dir)
        self.manifest_path = Path(manifest_path)
        self.settings_path = Path(settings_path)
        # python=None → 설치 시점 인터프리터 절대경로(W-B, python3 하드코딩 제거).
        self.python = python if python is not None else default_python()
        self.team_root = Path(team_root) if team_root else self.agent_dir.parents[2]
        self.events = events or self._load_events()
        self.normalize_path = self.agent_dir / "normalize.py"

    # ── 로드 ──

    def _load_events(self) -> dict:
        return json.loads((self.agent_dir / "events.json").read_text(encoding="utf-8"))

    def _load_manifest(self) -> list:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    # ── 번역 ──

    def translate_event(self, canonical_event: str):
        """정규 이벤트 → 이 에이전트의 이벤트명. 미지원이면 None(§4)."""
        events = self.events.get("events", {})
        if canonical_event not in events:
            # events.json 불완전 — 명시 누락은 lint 영역. 여기선 미지원 취급.
            return None
        return events[canonical_event]

    def translate_match(self, match: Optional[dict]):
        """정규 매처 → (matcher_str, expressible).

        match 없음          → (None, True)  전체 매칭
        {action: ...}        → events.actions[...] 문자열
        {mcp: {server,tool}} → mcp_tool_format 치환 (server=실제 등록 별칭)
        표현 불가            → (None, False)
        """
        if not match:
            return (None, True)
        if "action" in match:
            actions = self.events.get("actions", {})
            mapped = actions.get(match["action"])
            if mapped is None:
                return (None, False)
            return (mapped, True)
        if "mcp" in match:
            fmt = self.events.get("mcp_tool_format")
            if not fmt:
                return (None, False)
            mcp = match["mcp"]
            # 별칭 매핑: v0.1 기본 규칙 = 정규 서버명과 동일 별칭(§5.2-2).
            server = self.resolve_server_alias(mcp["server"])
            matcher = fmt.format(server=server, tool=mcp["tool"])
            return (matcher, True)
        return (None, False)

    def resolve_server_alias(self, canonical_server: str) -> str:
        """정규 서버명 → 실제 등록 별칭. v0.1 기본 = 동일(§5.2-2)."""
        return canonical_server

    # ── 커맨드 배선 (§5.1-2: normalize 경유 필수) ──

    def build_command(self, entry: dict) -> str:
        script = entry["script"]
        parts = [
            _quote_arg(str(self.python)),     # 윈도우 python 경로(공백) 안전 인용
            _quote_arg(str(self.normalize_path)),
            _quote_arg(script),
        ]
        if entry.get("args"):
            parts.append(entry["args"])
        return " ".join(parts)

    def is_owned(self, command: str) -> bool:
        """teammode 소유 훅인지 — 팀 루트 하위 normalize.py 지시 여부(§5.1-5).

        단순 'agents/' 부분문자열 판정 금지(사용자 무관 경로 오인 삭제 방지).
        """
        if not command:
            return False
        marker = str(self.normalize_path)
        # 절대경로 또는 상대형 둘 다 허용 — agents/<name>/normalize.py 꼬리 일치
        tail = os.path.join("agents", self.events.get("agent", ""), "normalize.py")
        return marker in command or tail in command

    # ── sync ──

    def _wanted_entries(self, mode: Optional[str]) -> list:
        manifest = self._load_manifest()
        base = [e for e in manifest if not e.get("mode")]
        on = [e for e in manifest if e.get("mode") == "on"]
        if mode == "on":
            return base + on
        # off 또는 무플래그(최초 off 간주, §5) → base 만
        return base

    def _read_settings(self) -> dict:
        if self.settings_path.is_file():
            try:
                return json.loads(self.settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _write_settings(self, settings: dict, original_text: str):
        new_text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        if new_text == original_text:
            return False  # 멱등: 무변경
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(new_text, encoding="utf-8")
        return True

    def sync(self, mode: Optional[str] = None) -> list:
        changes = []
        warnings = []
        wanted = self._wanted_entries(mode)

        settings = self._read_settings()
        original_text = (self.settings_path.read_text(encoding="utf-8")
                         if self.settings_path.is_file() else "")
        hooks = settings.setdefault("hooks", {})

        # 원하는 (event, matcher, command) 집합 산출
        desired = []  # (event, matcher_or_None, command, timeout)
        for entry in wanted:
            event = self.translate_event(entry["event"])
            matcher, expressible = self.translate_match(entry.get("match"))
            fallback = entry.get("fallback", "drop")

            if event is None:
                # 이벤트 미지원 → drop 동작 (+ warn). runtime이어도 이벤트 없으면 drop(§7).
                warnings.append(
                    f"[warn] {entry['script']}: {self.events.get('agent')} "
                    f"미지원(이벤트 {entry['event']}) → 비활성")
                continue
            if not expressible:
                if fallback == "runtime":
                    # 무매처 등록 + normalize 자가 필터로 의미 보존(§7)
                    matcher = None
                else:
                    warnings.append(
                        f"[warn] {entry['script']}: {self.events.get('agent')} "
                        f"매처 표현 불가 → 비활성")
                    continue

            command = self.build_command(entry)
            timeout = entry.get("timeout")
            desired.append((event, matcher, command, timeout))

        wanted_commands = {d[2] for d in desired}

        # upsert
        for event, matcher, command, timeout in desired:
            arr = hooks.setdefault(event, [])
            found = False
            for entry_obj in arr:
                if not isinstance(entry_obj, dict):
                    continue
                if entry_obj.get("matcher") != (matcher or ""):
                    # matcher 없는 엔트리는 "" 로 보관 → 정규화 비교
                    if not (matcher is None and not entry_obj.get("matcher")):
                        continue
                inner = entry_obj.get("hooks", [])
                # 소유 훅만 갱신 대상
                if inner and self.is_owned(inner[0].get("command", "")):
                    if inner[0].get("command") != command:
                        inner[0]["command"] = command
                        if timeout:
                            inner[0]["timeout"] = timeout
                        changes.append(f"[update] {event}")
                    found = True
                    break
            if not found:
                hook_def = {"type": "command", "command": command}
                if timeout:
                    hook_def["timeout"] = timeout
                new_entry = {"hooks": [hook_def]}
                if matcher is not None:
                    new_entry["matcher"] = matcher
                arr.append(new_entry)
                changes.append(f"[add] {event}")

        # 제거: 소유 훅이지만 wanted 에 없는 것
        for event in list(hooks.keys()):
            arr = hooks[event]
            keep = []
            for entry_obj in arr:
                if not isinstance(entry_obj, dict):
                    keep.append(entry_obj)
                    continue
                inner = entry_obj.get("hooks", [])
                cmd = inner[0].get("command", "") if inner else ""
                if self.is_owned(cmd) and cmd not in wanted_commands:
                    changes.append(f"[remove] {event}")
                    continue
                keep.append(entry_obj)
            if keep:
                hooks[event] = keep
            else:
                del hooks[event]

        changed = self._write_settings(settings, original_text)
        for w in warnings:
            print(w)
        if not changed and not warnings:
            changes.append("[ok] 변경 없음")
        return changes

    def uninstall(self) -> list:
        """teammode 소유 훅 전부 제거."""
        changes = []
        settings = self._read_settings()
        original_text = (self.settings_path.read_text(encoding="utf-8")
                         if self.settings_path.is_file() else "")
        hooks = settings.get("hooks", {})
        for event in list(hooks.keys()):
            arr = hooks[event]
            keep = [e for e in arr if not (
                isinstance(e, dict) and e.get("hooks")
                and self.is_owned(e["hooks"][0].get("command", "")))]
            removed = len(arr) - len(keep)
            if removed:
                changes.append(f"[remove] {event} x{removed}")
            if keep:
                hooks[event] = keep
            else:
                del hooks[event]
        self._write_settings(settings, original_text)
        return changes


# ── CLI (디스패처가 호출) ──

def _default_paths():
    here = Path(__file__).resolve().parent
    team_root = here.parents[2]  # agents/claude → agents → infra → root
    return {
        "agent_dir": str(here),
        "manifest_path": str(team_root / "infra" / "hooks" / "manifest.json"),
        "team_root": str(team_root),
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="claude-adapter")
    p.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    # --python 기본 None → 설치 시점 sys.executable(절대경로) 해석 (W-B, 크로스플랫폼)
    p.add_argument("--python", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync")
    sp.add_argument("--on", action="store_true")
    sp.add_argument("--off", action="store_true")
    sub.add_parser("uninstall")

    args = p.parse_args(argv)

    d = _default_paths()
    adapter = Adapter(
        agent_dir=d["agent_dir"],
        manifest_path=d["manifest_path"],
        settings_path=args.settings,
        python=args.python,
        team_root=d["team_root"],
    )

    if args.cmd == "sync":
        mode = "on" if args.on else ("off" if args.off else None)
        for c in adapter.sync(mode=mode):
            print(c)
    elif args.cmd == "uninstall":
        for c in adapter.uninstall():
            print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
