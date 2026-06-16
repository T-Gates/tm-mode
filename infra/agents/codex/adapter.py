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
_SEALED = _claude_mod["_SEALED"]  # MCP 등록 파일 봉인 센티넬(N3)

# stdout UTF-8 보장 — sync() 가 한글 [warn]/[ok] print. install.py 디스패치(in-process)
# 는 install.py main 이 이미 보정하나, `python adapter.py sync` 직접 실행 시 cp949 콘솔
# 크래시 방어(일관·방어). infra 미발견 시 no-op(다른 훅과 동일 가드 패턴).
_INFRA_DIR = Path(__file__).resolve().parents[2]
if str(_INFRA_DIR) not in sys.path:
    sys.path.insert(0, str(_INFRA_DIR))
try:
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 어댑터는 동작(보정만 스킵)
        return

BLOCK_START = "# teammode-hooks-start"
BLOCK_END = "# teammode-hooks-end"


class Adapter(BaseAdapter):
    """Codex 어댑터 — 번역 코어는 상속, config 포맷·폴백만 재정의."""

    # install-skills(L2-C): Codex 스킬 경로는 spec 에 명문화돼 있지 않다 — claude 의
    # ~/.claude/skills 와 대칭으로 ~/.codex/skills 를 가정한다(주석으로 추정 명시).
    # 실 경로가 다르면 v0.2 에서 이 상수만 고치면 된다(install_skills 로직은 부모 상속).
    DEFAULT_SKILLS_DIR = "~/.codex/skills"

    def __init__(self, *args, **kwargs):
        # N3: Codex 는 MCP 를 ~/.codex/config.toml 의 [mcp_servers.*] 블록으로 등록하므로
        # 부모(claude)가 상속시키는 mcp_config_path(=~/.claude.json) 를 절대 쓰지 않는다.
        # 상속된 실경로가 latent footgun 으로 새지 않게 봉인 — 부모 _read_mcp_config/
        # install_mcp(claude.json 경로)를 잘못 호출하면 무동작/NotImplementedError 로 막힌다.
        # (codex 는 _read_mcp_servers·install_mcp 를 config.toml 기반으로 전부 재정의함.)
        kwargs["mcp_config_path"] = _SEALED
        super().__init__(*args, **kwargs)

    def sync(self, mode: Optional[str] = None) -> list:
        changes = []
        warnings = []
        infos = []
        wanted = self._wanted_entries(mode)

        # config services 1회 로드 — 빈 슬롯 우선 규칙(§2.9/§7.2)·install-mcp 선행(§2.7).
        # None = config 파일 부재 → 빈 슬롯 규칙 미적용(L1 동작 보존). claude 와 동형.
        services = self._load_services()

        toml_entries = []  # (codex_event, matcher_or_None, command, timeout_s)
        for entry in wanted:
            event = self.translate_event(entry["event"])
            match = entry.get("match")
            fallback = entry.get("fallback", "drop")
            enforcement = entry.get("enforcement", "advisory")

            if event is None:
                # 이벤트 미지원 → drop (+ warn). enforcement=block 이면 차단 상실 명시(§11.11).
                extra = " (block 강제 상실)" if enforcement == "block" else ""
                warnings.append(
                    f"[warn] {entry['script']}: {self.events.get('agent')} "
                    f"미지원(이벤트 {entry['event']}){extra} → 비활성")
                continue

            # ── MCP 매처 전처리(B.2 / §2.9 빈 슬롯 우선 + §2.7 install-mcp 선행) ──
            # services 가 dict 로 주어졌을 때만 적용(파일 부재 시 L1 동작 보존).
            if isinstance(match, dict) and "mcp" in match and isinstance(services, dict):
                canonical = match["mcp"].get("server")
                if not self._mcp_server_connected(canonical, services):
                    infos.append(
                        f"[info] {entry['script']}: '{canonical}' 역할 슬롯 미연결 "
                        f"→ MCP 매처 생략(빈 슬롯, 슬롯 연결 후 sync 재실행)")
                    continue
                if not self._mcp_alias_guaranteed(canonical):
                    warnings.append(
                        f"[warn] {entry['script']}: '{canonical}' MCP 별칭 미보장"
                        f"(install-mcp 선행 필요) → 이 매처만 생략")
                    continue

            matcher, expressible = self.translate_match(match)
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
        for i in infos:
            print(i)
        if not changed and not warnings and not infos:
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

    # ── install-mcp (§2.8) — Codex 방식: config.toml [mcp_servers.*] 관리형 블록 ──
    #
    # Codex 는 MCP 서버를 ~/.codex/config.toml 의 [mcp_servers.<name>] 섹션으로 등록한다
    # (claude 의 ~/.claude.json top-level mcpServers 와 다른 포맷 — 이 차이를 어댑터가 흡수).
    # 훅 블록(# teammode-hooks-*)과 동일 파일이므로 별도 마커 블록(# teammode-mcp-*)으로
    # 격리해 멱등 교체한다. 등록 항목은 claude 와 동일한 보수적 placeholder(소유 마커 +
    # register_hint) — teammode 는 MCP 서버 자체를 제작·유지하지 않는다(§7.4).
    #
    # ⚠️ Codex 한계 정직 표면화: Codex config.toml 의 [mcp_servers.*] 는 실행 커맨드를
    # 요구하는 정적 선언이라, 실 커맨드 미고정인 v0.1 에서는 placeholder 만 둔다. 또한
    # Codex 는 PreToolUse 차단을 표현하지 못하므로(§2.11) MCP 매처 confirm 훅의 강제력은
    # sync 단계에서 이미 [warn] 으로 상실 표면화된다 — install-mcp 는 서버 등록만 책임.

    MCP_BLOCK_START = "# teammode-mcp-start"
    MCP_BLOCK_END = "# teammode-mcp-end"

    def _read_mcp_servers(self) -> dict:
        """config.toml 의 teammode-mcp 블록에서 등록된 정규 서버명 집합을 파싱.

        부모(claude)의 ~/.claude.json 기반 구현을 Codex TOML 블록 기반으로 재정의.
        값은 부모 _mcp_alias_guaranteed 가 보는 `{"_teammode_managed": True}` 형태로 맞춘다.
        """
        existing = self._read_config()
        pattern = re.compile(
            re.escape(self.MCP_BLOCK_START) + r"(.*?)" + re.escape(self.MCP_BLOCK_END),
            re.S)
        m = pattern.search(existing)
        servers: dict = {}
        if not m:
            return servers
        for sm in re.finditer(r"\[mcp_servers\.([^\]]+)\]", m.group(1)):
            name = sm.group(1).strip().strip('"')
            servers[name] = {"_teammode_managed": True}
        return servers

    def _render_mcp_block(self, providers_with_packs: list) -> str:
        """연결 provider 목록 → teammode-mcp TOML 블록 문자열."""
        lines = [self.MCP_BLOCK_START, ""]
        for provider, pack in providers_with_packs:
            hint = pack.mcp.get("register_hint", "") if pack else ""
            lines.append(f"[mcp_servers.{provider}]")
            # teammode 소유 마커 + 안내(사람/LLM 이 실 커맨드 채움). 정규명=별칭(항등, §2.8-2).
            lines.append("_teammode_managed = true")
            lines.append(f"_canonical_server = {self._toml_str(provider)}")
            lines.append(f"_register_hint = {self._toml_str(hint)}")
            lines.append("")
        lines.append(self.MCP_BLOCK_END)
        return "\n".join(lines)

    def _write_mcp_block(self, block: str) -> bool:
        existing = self._read_config()
        pattern = re.compile(
            r"\n*" + re.escape(self.MCP_BLOCK_START) + r".*?"
            + re.escape(self.MCP_BLOCK_END) + r"\n*", re.S)
        m = pattern.search(existing)
        if m:
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

    def install_mcp(self) -> list:
        """config services 의 연결 provider 를 Codex config.toml [mcp_servers.*] 로 등록. 멱등.

        claude 와 동일 계약(services 읽기·정규명 등록·별칭 항등·멱등·빈 슬롯 [info])이되,
        등록 포맷만 Codex TOML 블록으로 재정의.

        ⚠️ v0.1 한계 (N6, §7.4 — 의도된 범위 제약):
          여기서 쓰는 [mcp_servers.<name>] 블록은 `_teammode_managed`·`_canonical_server`·
          `_register_hint` 만 담은 **command 없는 placeholder** 다. Codex 런타임이 이 항목을
          실 MCP 서버로 띄우려 하면 실행 command 부재로 에러날 수 있다(거부 가능).
            - teammode 는 MCP 서버를 제작·유지하지 않는다(§7.4) → 실 command 는 데이터로 안 둔다.
            - wire(install.py) 가 install-mcp 를 실 적용에 노출하더라도, **wire 는 등록(별칭 보장,
              정규명==별칭 항등)까지만** 책임진다. **실행 command 는 v0.1 미보장** — Codex 로 실제
              서버를 띄우려면 사용자가 이 블록에 `command`/`args` 를 직접 보강해야 한다(v0.2 이월).
          즉 이 메서드의 계약은 "별칭 슬롯이 config.toml 에 멱등 존재함" 까지이며, 그 슬롯이
          기동 가능한 서버 정의인지는 v0.1 범위 밖이다(register_hint 가 사람/LLM 에게 안내).
        """
        import providers as _prov  # 부모와 동일 모듈(infra/ on sys.path)
        changes = []
        services = self._load_services()
        connected = []
        if isinstance(services, dict):
            for slot in services.values():
                if isinstance(slot, dict):
                    prov = slot.get("provider")
                    if isinstance(prov, str) and prov.strip() and prov not in connected:
                        connected.append(prov)

        providers_with_packs = []
        aliases = []  # N2: 실제 변경과 무관하게 "등록 대상" provider 별칭 추적.
        for provider in connected:
            try:
                pack = _prov.lookup(provider, providers_dir=self.providers_dir)
            except Exception:
                pack = None
            if pack is None:
                changes.append(f"[info] {provider}: provider 팩 없음 → MCP 등록 생략")
                continue
            alias = self.resolve_server_alias(provider)
            providers_with_packs.append((alias, pack))
            aliases.append(alias)

        if providers_with_packs:
            block = self._render_mcp_block(providers_with_packs)
            changed = self._write_mcp_block(block)
            # N2: claude 와 대칭 — 실제 파일 변경 시에만 [mcp] 등록, 멱등 무변경은
            # [ok]. (_write_mcp_block 의 changed 반환값을 반영, 거짓 등록 보고 금지.)
            if changed:
                for alias in aliases:
                    changes.append(f"[mcp] {alias} 등록")
            else:
                changes.append(f"[ok] 변경 없음 ({len(aliases)}개 provider 등록됨)")
        else:
            # 연결 provider 없음 → 기존 teammode-mcp 블록 제거(멱등 빈상태).
            # 안전(P1-1): 블록이 없으면(부재 config 포함) 파일 무접촉 — pattern.search 가
            # 없을 때 write 하지 않으므로 빈 슬롯에서 config.toml 을 touch 하지 않는다.
            existing = self._read_config()
            pattern = re.compile(
                r"\n*" + re.escape(self.MCP_BLOCK_START) + r".*?"
                + re.escape(self.MCP_BLOCK_END) + r"\n*", re.S)
            if pattern.search(existing):
                self.settings_path.write_text(pattern.sub("\n", existing),
                                              encoding="utf-8")
                changes.append("[remove-mcp] teammode MCP 블록")
        if not changes:
            changes.append("[info] 연결된 MCP provider 없음 (빈 슬롯)")
        return changes

    def uninstall(self) -> list:
        existing = self._read_config()
        pattern = re.compile(
            r"\n?" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?",
            re.S)
        updated = pattern.sub("\n", existing)
        # MCP 블록도 함께 제거(역순 제거 — 등록 흔적 전부)
        mcp_pattern = re.compile(
            r"\n?" + re.escape(self.MCP_BLOCK_START) + r".*?"
            + re.escape(self.MCP_BLOCK_END) + r"\n?", re.S)
        updated2 = mcp_pattern.sub("\n", updated)
        if updated2 != existing:
            self.settings_path.write_text(updated2, encoding="utf-8")
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
    _ensure_utf8_io()  # 한글 [warn]/[ok] print 가 cp949 콘솔에서 크래시 방지(직접 실행 방어)
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="codex-adapter")
    p.add_argument("--config", default=os.path.expanduser("~/.codex/config.toml"))
    # --python 기본 None → 설치 시점 sys.executable 해석 (W-B, BaseAdapter 와 일관)
    p.add_argument("--python", default=None)
    # team.config.json·providers/ 경로 — 기본은 team_root 상대, 테스트는 tmp 주입.
    # (Codex MCP 등록은 --config 의 config.toml 안 블록이므로 별도 --mcp-config 불요.)
    p.add_argument("--team-config", default=None)
    p.add_argument("--providers-dir", default=None)
    # install-skills 스킬 디렉토리 — 기본 None(실호스트 ~/.codex/skills), 격리/테스트는 tmp.
    p.add_argument("--skills-dir", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--on", action="store_true")
    sp.add_argument("--off", action="store_true")
    sub.add_parser("uninstall")
    sub.add_parser("install-mcp")
    sub.add_parser("install-skills")
    args = p.parse_args(argv)

    d = _default_paths()
    adapter = Adapter(
        agent_dir=d["agent_dir"],
        manifest_path=d["manifest_path"],
        settings_path=args.config,
        python=args.python,
        team_root=d["team_root"],
        config_path=args.team_config,
        providers_dir=args.providers_dir,
        skills_dir=args.skills_dir,
    )
    if args.cmd == "sync":
        mode = "on" if args.on else ("off" if args.off else None)
        for c in adapter.sync(mode=mode):
            print(c)
    elif args.cmd == "uninstall":
        for c in adapter.uninstall():
            print(c)
        for c in adapter.uninstall_skills():
            print(c)
    elif args.cmd == "install-mcp":
        for c in adapter.install_mcp():
            print(c)
    elif args.cmd == "install-skills":
        for c in adapter.install_skills():
            print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
