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

# providers 팩 로더(L2-A) — install-mcp 가 연결 provider 를 식별하고,
# sync 가 빈 슬롯 우선 규칙(§2.9/§7.2)에서 provider→역할 매핑을 읽는 데 쓴다.
# infra/ 가 sys.path 에 없을 수 있으므로 방어적으로 추가(테스트는 직접 import).
_INFRA_DIR = Path(__file__).resolve().parents[2]
if str(_INFRA_DIR) not in sys.path:
    sys.path.insert(0, str(_INFRA_DIR))
try:
    import providers as _providers  # type: ignore
except Exception:  # pragma: no cover - providers 부재 시에도 sync 는 동작
    _providers = None


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
                 python=None, team_root=None, events=None,
                 config_path=None, providers_dir=None, mcp_config_path=None):
        self.agent_dir = Path(agent_dir)
        self.manifest_path = Path(manifest_path)
        self.settings_path = Path(settings_path)
        # python=None → 설치 시점 인터프리터 절대경로(W-B, python3 하드코딩 제거).
        self.python = python if python is not None else default_python()
        self.team_root = Path(team_root) if team_root else self.agent_dir.parents[2]
        self.events = events or self._load_events()
        self.normalize_path = self.agent_dir / "normalize.py"
        # config_path=None(기본): team_root/team.config.json. 빈 슬롯 우선 규칙(§2.9)·
        # install-mcp(§2.8) 가 services 를 읽는 단일 소스. 테스트는 tmp 주입.
        # ⚠️ "config 파일 부재" 와 "config 의 빈 services({})" 는 다르게 다룬다:
        #   - 파일 부재 → services 정보 미지(None) → 빈 슬롯 규칙 미적용(L1 동작 보존).
        #   - 파일 존재 + 역할 슬롯 미연결 → 빈 슬롯 규칙 적용(생략 + [info]).
        self.config_path = (Path(config_path) if config_path is not None
                            else self.team_root / "team.config.json")
        # providers_dir=None: providers.DEFAULT_PROVIDERS_DIR(레포 providers/). 테스트는 tmp.
        self.providers_dir = Path(providers_dir) if providers_dir is not None else None
        # MCP 등록 파일 — Claude 는 ~/.claude.json(top-level mcpServers). 테스트는 tmp 주입.
        self.mcp_config_path = (Path(mcp_config_path) if mcp_config_path is not None
                                else Path(os.path.expanduser("~/.claude.json")))

    # ── 로드 ──

    def _load_events(self) -> dict:
        return json.loads((self.agent_dir / "events.json").read_text(encoding="utf-8"))

    def _load_manifest(self) -> list:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _load_services(self):
        """team.config.json 의 services 블록을 읽는다.

        반환:
          - dict: config 파일이 있고 services 가 object (빈 {} 포함).
          - None: config 파일 부재 또는 깨짐 또는 services 키 없음 →
                  services 정보 미지(빈 슬롯 규칙 미적용, L1 동작 보존).
        """
        if not self.config_path.is_file():
            return None
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if not isinstance(cfg, dict):
            return None
        services = cfg.get("services")
        if services is None or not isinstance(services, dict):
            return None
        return services

    def _provider_roles(self, provider: str):
        """provider 팩의 services(이 provider 가 채울 수 있는 역할 목록) 조회.

        팩이 없거나 providers 모듈 부재면 None(추측 금지) — 호출부가 보수적으로 처리.
        """
        if _providers is None:
            return None
        try:
            pack = _providers.lookup(provider, providers_dir=self.providers_dir)
        except Exception:
            return None
        if pack is None:
            return None
        return list(pack.services)

    def _mcp_server_connected(self, canonical_server: str, services) -> bool:
        """정규 서버명(=provider)이 config services 에서 연결된 역할 슬롯을 갖는지.

        provider 팩의 services(역할 목록) 중 하나라도 config services 에서 같은
        provider 로 채워져 있으면 연결됨. services 가 dict 이어야 호출된다.
        """
        roles = self._provider_roles(canonical_server)
        if not roles:
            # provider 팩을 못 찾으면 역할 매핑 불가 → 보수적으로 "미연결" 취급
            # (빈 슬롯 우선: 잘못 등록하느니 생략 + [info]). 단 이 분기는 services 가
            # dict 로 주어졌을 때만 도달한다(파일 부재 시엔 아예 규칙 미적용).
            for slot in services.values():
                if isinstance(slot, dict) and slot.get("provider") == canonical_server:
                    return True
            return False
        for role in roles:
            slot = services.get(role)
            if isinstance(slot, dict) and slot.get("provider") == canonical_server:
                return True
        return False

    def _mcp_alias_guaranteed(self, canonical_server: str) -> bool:
        """install-mcp 선행 여부 — 정규 서버명이 MCP 등록 파일에 teammode 항목으로 있는지.

        §2.8 별칭 보장. 미선행이면 매처 문자열의 별칭이 보장 안 되므로 sync 가 해당
        매처만 [warn] 생략(§2.7). resolve_server_alias 가 항등이므로 alias=정규명.
        MCP 등록 파일 부재/깨짐 → 미선행으로 본다.
        """
        alias = self.resolve_server_alias(canonical_server)
        servers = self._read_mcp_servers()
        entry = servers.get(alias)
        return isinstance(entry, dict) and entry.get("_teammode_managed") is True

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

    # ── install-mcp (§2.8) ──

    def _read_mcp_config(self) -> dict:
        """MCP 등록 파일(~/.claude.json) 전체를 읽는다. 부재/깨짐 → {}.

        실 ~/.claude.json 은 mcpServers 외에도 projects 등 사용자 데이터를 담으므로
        **전체를 보존**하고 mcpServers 만 머지한다(사용자 데이터 무접촉).
        """
        if self.mcp_config_path.is_file():
            try:
                data = json.loads(self.mcp_config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (ValueError, OSError):
                return {}
        return {}

    def _read_mcp_servers(self) -> dict:
        data = self._read_mcp_config()
        servers = data.get("mcpServers")
        return servers if isinstance(servers, dict) else {}

    def _is_owned_mcp(self, entry) -> bool:
        """teammode 가 등록한 MCP 서버 항목인지 — 소유 마커로 식별(사용자 항목 무접촉)."""
        return isinstance(entry, dict) and entry.get("_teammode_managed") is True

    def _build_mcp_entry(self, provider: str, pack) -> dict:
        """provider 팩 → MCP 서버 등록 항목(자기 방식 = Claude ~/.claude.json shape).

        ⚠️ teammode 는 MCP 서버 자체를 제작·유지하지 않는다(§7.4) — 실제 실행
        커맨드는 provider/환경마다 다르고 v0.1 스펙에 미고정이다. 따라서 보수적으로
        **소유 마커 + provider 팩 register_hint(사람·LLM 이 채울 안내)** 를 담은
        관리형 placeholder 항목을 등록한다. 정규 서버명으로 키가 잡히는 것(별칭 항등,
        §2.8-2)·멱등·소유권이 핵심 계약이며, 구체 실행 커맨드는 v0.2 확장 여지.
        """
        return {
            "_teammode_managed": True,
            "_canonical_server": provider,
            "_register_hint": pack.mcp.get("register_hint", "") if pack else "",
        }

    def install_mcp(self) -> list:
        """config services 의 연결 provider 를 MCP 서버로 등록(자기 방식). 멱등.

        - services 부재(파일 없음/빈 {}) → 등록할 것 없음, [info] 후 종료(빈 슬롯 1급).
        - 채운 슬롯의 provider 마다 정규 서버명으로 등록(별칭=정규명, resolve 항등).
        - 같은 provider 가 여러 역할에 쓰여도 1회만 등록(정규명 dedup).
        - teammode 소유 항목만 추가·갱신. 사용자 항목 무접촉.
        """
        changes = []
        services = self._load_services()
        # 연결된 provider 집합(정규 서버명) 산출.
        connected = []
        if isinstance(services, dict):
            for slot in services.values():
                if isinstance(slot, dict):
                    prov = slot.get("provider")
                    if isinstance(prov, str) and prov.strip() and prov not in connected:
                        connected.append(prov)

        data = self._read_mcp_config()
        original_text = (self.mcp_config_path.read_text(encoding="utf-8")
                         if self.mcp_config_path.is_file() else "")
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            servers = data["mcpServers"] = {}

        desired_aliases = set()
        for provider in connected:
            pack = None
            if _providers is not None:
                try:
                    pack = _providers.lookup(provider, providers_dir=self.providers_dir)
                except Exception:
                    pack = None
            if pack is None:
                # provider 팩 없음 — 추측 금지, 등록 생략 + [info](빈 슬롯과 동일 정신).
                changes.append(f"[info] {provider}: provider 팩 없음 → MCP 등록 생략")
                continue
            alias = self.resolve_server_alias(provider)  # 별칭=정규명(항등, §2.8-2)
            desired_aliases.add(alias)
            entry = self._build_mcp_entry(provider, pack)
            existing = servers.get(alias)
            if self._is_owned_mcp(existing) and existing == entry:
                continue  # 멱등: 변경 없음
            if existing is not None and not self._is_owned_mcp(existing):
                # 사용자가 직접 등록한 동명 서버 — 무접촉(소유권, §2.7-5 정신).
                changes.append(f"[warn] {alias}: 사용자 등록 MCP 서버 존재 → 무접촉")
                desired_aliases.discard(alias)
                continue
            servers[alias] = entry
            changes.append(f"[mcp] {alias} 등록")

        # 제거: teammode 소유지만 더 이상 연결되지 않는 항목.
        for alias in list(servers.keys()):
            if self._is_owned_mcp(servers[alias]) and alias not in desired_aliases:
                del servers[alias]
                changes.append(f"[remove-mcp] {alias}")

        new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if new_text != original_text:
            self.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
            self.mcp_config_path.write_text(new_text, encoding="utf-8")
        if not changes:
            changes.append("[info] 연결된 MCP provider 없음 (빈 슬롯)")
        return changes

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

        # config services 1회 로드 — 빈 슬롯 우선 규칙(§2.9/§7.2)·install-mcp 선행(§2.7).
        # None = config 파일 부재(services 정보 미지) → 빈 슬롯 규칙 미적용(L1 동작 보존).
        services = self._load_services()

        # 원하는 (event, matcher, command) 집합 산출
        desired = []  # (event, matcher_or_None, command, timeout)
        infos = []    # [info] 메시지(빈 슬롯 생략 — 에러 아님, §7.2)
        for entry in wanted:
            event = self.translate_event(entry["event"])
            match = entry.get("match")
            fallback = entry.get("fallback", "drop")

            if event is None:
                # 이벤트 미지원 → drop 동작 (+ warn). runtime이어도 이벤트 없으면 drop(§7).
                warnings.append(
                    f"[warn] {entry['script']}: {self.events.get('agent')} "
                    f"미지원(이벤트 {entry['event']}) → 비활성")
                continue

            # ── MCP 매처 전처리(B.2 / §2.9 빈 슬롯 우선 + §2.7 install-mcp 선행) ──
            # services 가 dict 로 주어졌을 때만 적용(파일 부재 시 L1 동작 보존).
            if isinstance(match, dict) and "mcp" in match and isinstance(services, dict):
                canonical = match["mcp"].get("server")
                if not self._mcp_server_connected(canonical, services):
                    # 빈 슬롯 우선 규칙: fallback 무관 등록 생략 + [info] (에러 아님).
                    infos.append(
                        f"[info] {entry['script']}: '{canonical}' 역할 슬롯 미연결 "
                        f"→ MCP 매처 생략(빈 슬롯, 슬롯 연결 후 sync 재실행)")
                    continue
                # 연결됨 → install-mcp 선행(별칭 보장) 확인. 미선행이면 [warn] 생략(§2.7).
                if not self._mcp_alias_guaranteed(canonical):
                    warnings.append(
                        f"[warn] {entry['script']}: '{canonical}' MCP 별칭 미보장"
                        f"(install-mcp 선행 필요) → 이 매처만 생략")
                    continue

            matcher, expressible = self.translate_match(match)
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
        for i in infos:
            print(i)
        if not changed and not warnings and not infos:
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
    # MCP 등록 파일·config·providers 경로 — 기본은 실 경로지만 테스트는 tmp 주입(격리).
    p.add_argument("--mcp-config", default=None)
    p.add_argument("--config", default=None)
    p.add_argument("--providers-dir", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync")
    sp.add_argument("--on", action="store_true")
    sp.add_argument("--off", action="store_true")
    sub.add_parser("uninstall")
    sub.add_parser("install-mcp")

    args = p.parse_args(argv)

    d = _default_paths()
    adapter = Adapter(
        agent_dir=d["agent_dir"],
        manifest_path=d["manifest_path"],
        settings_path=args.settings,
        python=args.python,
        team_root=d["team_root"],
        config_path=args.config,
        providers_dir=args.providers_dir,
        mcp_config_path=args.mcp_config,
    )

    if args.cmd == "sync":
        mode = "on" if args.on else ("off" if args.off else None)
        for c in adapter.sync(mode=mode):
            print(c)
    elif args.cmd == "uninstall":
        for c in adapter.uninstall():
            print(c)
    elif args.cmd == "install-mcp":
        for c in adapter.install_mcp():
            print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
