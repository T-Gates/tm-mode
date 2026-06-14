#!/usr/bin/env python3
"""Codex CLI 어댑터 — 설치 시점 번역기 (스펙 02 §5).

정규형 manifest + codex/events.json 을 읽어 Codex 의 config.toml hooks 블록에
멱등 동기화한다. 번역 코어(events.json 기반, 에이전트 무관)는 Claude 어댑터와
공유하고, 이 파일은 **Codex 고유의 config 포맷(TOML 블록) + 폴백·enforcement 축소**만
담당한다.

Codex 특성(events.json 으로 데이터 표현 — 코드 분기 하드코딩 금지, §4):
  - PreToolUse = null (미지원) → §7 폴백(drop + [warn], 무음 스킵 금지)
  - actions.file_edit = "apply_patch"
  - mcp_tool_format = "{server}.{tool}"

enforcement 축소(§11.11): block 훅이 이 에이전트에서 표현 불가하면, 폴백으로 비활성되며
[warn] 으로 "차단 강제 상실"을 알린다 — 무음 누락 금지. (Codex 가 PreToolUse 차단을
지원하지 못하는 현 상황의 정직한 표면화.)

CLI:
  adapter.py sync [--on|--off]   manifest → config.toml (멱등)
  adapter.py uninstall           teammode 블록 제거
"""
from __future__ import annotations

import argparse
import os
import re
import runpy
import sys
from pathlib import Path
from typing import Optional


# 번역 코어를 Claude 어댑터에서 재사용 (events.json 기반이라 에이전트 무관)
_CLAUDE_ADAPTER = (Path(__file__).resolve().parents[1] / "claude" / "adapter.py")
_claude_mod = runpy.run_path(str(_CLAUDE_ADAPTER), run_name="__codex_base__")
BaseAdapter = _claude_mod["Adapter"]

BLOCK_START = "# teammode-hooks-start"
BLOCK_END = "# teammode-hooks-end"


class Adapter(BaseAdapter):
    """Codex 어댑터 — 번역 코어는 상속, config 포맷·폴백만 재정의."""

    def sync(self, mode: Optional[str] = None) -> list:
        changes = []
        warnings = []
        wanted = self._wanted_entries(mode)

        toml_entries = []  # (codex_event, matcher_or_None, command, timeout_s)
        for entry in wanted:
            event = self.translate_event(entry["event"])
            matcher, expressible = self.translate_match(entry.get("match"))
            fallback = entry.get("fallback", "drop")
            enforcement = entry.get("enforcement", "advisory")

            if event is None:
                # 이벤트 미지원 → drop (+ warn). enforcement=block 이면 차단 상실 명시(§11.11).
                extra = " (block 강제 상실)" if enforcement == "block" else ""
                warnings.append(
                    f"[warn] {entry['script']}: {self.events.get('agent')} "
                    f"미지원(이벤트 {entry['event']}){extra} → 비활성")
                continue
            if not expressible:
                if fallback == "runtime":
                    matcher = None
                else:
                    warnings.append(
                        f"[warn] {entry['script']}: {self.events.get('agent')} "
                        f"매처 표현 불가 → 비활성")
                    continue

            command = self.build_command(entry)
            timeout_ms = entry.get("timeout", 5000)
            timeout_s = max(1, timeout_ms // 1000)
            toml_entries.append((event, matcher, command, timeout_s))

        block = self._render_block(toml_entries)
        changed = self._write_block(block)
        if changed:
            changes.append(f"[sync] Codex 훅 {len(toml_entries)}개 등록")

        for w in warnings:
            print(w)
        if not changed and not warnings:
            changes.append("[ok] 변경 없음")
        return changes

    def _render_block(self, entries: list) -> str:
        lines = [BLOCK_START, ""]
        for event, matcher, command, timeout_s in entries:
            lines.append(f"[[hooks.{event}]]")
            if matcher:
                lines.append(f'matcher = "{matcher}"')
            lines.append("")
            lines.append(f"[[hooks.{event}.hooks]]")
            lines.append('type = "command"')
            # 커맨드는 normalize 경유(§5.1-2). TOML 문자열로 그대로.
            lines.append(f"command = {self._toml_str(command)}")
            lines.append(f"timeout = {timeout_s}")
            lines.append("")
        lines.append(BLOCK_END)
        return "\n".join(lines)

    @staticmethod
    def _toml_str(s: str) -> str:
        # 큰따옴표가 들어있는 커맨드는 TOML literal(작은따옴표) 문자열로 안전 표현
        if "'" not in s:
            return "'" + s + "'"
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _read_config(self) -> str:
        if self.settings_path.is_file():
            return self.settings_path.read_text(encoding="utf-8")
        return ""

    def _write_block(self, block: str) -> bool:
        existing = self._read_config()
        pattern = re.compile(
            r"\n*" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n*",
            re.S)
        m = pattern.search(existing)
        if m:
            # 블록 앞에 사용자 콘텐츠가 있으면 두 줄 띄움, 없으면 선행 개행 없음 — 멱등 보장
            prefix = "\n\n" if existing[:m.start()].strip() else ""
            updated = existing[:m.start()] + prefix + block + "\n" + existing[m.end():]
        else:
            base = existing.rstrip()
            updated = (base + "\n\n" + block + "\n") if base else (block + "\n")
        if updated == existing:
            return False
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(updated, encoding="utf-8")
        return True

    def uninstall(self) -> list:
        existing = self._read_config()
        pattern = re.compile(
            r"\n?" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?",
            re.S)
        updated = pattern.sub("\n", existing)
        if updated != existing:
            self.settings_path.write_text(updated, encoding="utf-8")
            return ["[remove] teammode 훅 블록"]
        return ["[ok] 제거할 블록 없음"]


# ── CLI (디스패처가 호출) ──

def _default_paths():
    here = Path(__file__).resolve().parent
    team_root = here.parents[2]
    return {
        "agent_dir": str(here),
        "manifest_path": str(team_root / "infra" / "hooks" / "manifest.json"),
        "team_root": str(team_root),
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="codex-adapter")
    p.add_argument("--config", default=os.path.expanduser("~/.codex/config.toml"))
    # --python 기본 None → 설치 시점 sys.executable 해석 (W-B, BaseAdapter 와 일관)
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
        settings_path=args.config,
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
