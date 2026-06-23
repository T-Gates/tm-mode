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

# stdout UTF-8 보장 — sync() 가 한글 [warn]/[ok] print. install.py 디스패치(in-process)
# 는 install.py main 이 이미 보정하나, `python adapter.py sync` 직접 실행 시 cp949 콘솔
# 크래시 방어(일관·방어). infra 미발견 시 no-op(다른 훅과 동일 가드 패턴).
try:
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 어댑터는 동작(보정만 스킵)
        return


# MCP 등록 파일 봉인 센티넬(N3). mcp_config_path=_SEALED 면 이 어댑터는 ~/.claude.json
# 류 실경로를 절대 읽지/쓰지 않는다 — codex 처럼 부모 경로를 안 쓰는 서브클래스가 상속된
# 실경로를 latent footgun 으로 흘리지 않게 정적 봉인. None(미지정)과 구분: None=기본 실경로.
_SEALED = object()


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


def _to_slash(s: str) -> str:
    """윈도우 백슬래시 경로 → forward slash (훅 escape 버그 방지).

    윈도우 Claude Code 가 훅 커맨드를 **Git Bash(bash)** 로 실행하면 백슬래시가
    escape 처리돼 경로가 깨진다(`C:\\Users\\...\\python.exe` → `C:Users...python.exe`
    → command not found). slash 경로(`C:/Users/.../python.exe`)는 bash·cmd·PowerShell
    모두 윈도우에서 정상 인식하고 escape 문제가 없으므로, 커맨드를 만드는 시점에
    백슬래시를 아예 안 내보낸다.

    Linux/macOS 경로엔 백슬래시가 없어 무영향(이미 slash). 공백 대응 따옴표는
    이후 _quote_arg 가 별도로 처리(슬래시로 바꿔도 공백 경로는 여전히 따옴표 필요).
    """
    return s.replace("\\", "/")


def _quote_arg(s: str) -> str:
    """셸 명령 토큰 안전 인용 — POSIX single-quote escape (Git Bash / bash 안전).

    double-quote(`"$foo"`)는 bash에서 `$`·backtick이 확장돼 경로가 변조될 수 있다.
    Git Bash(윈도우 Claude Code 실행 환경) + Linux/macOS bash 모두 POSIX
    single-quote를 지원하므로 single-quote 방식을 사용한다:
      - 특수문자(공백·$·backtick·! 등) 포함 토큰 → 'token' (확장 없음)
      - 토큰 안에 ' 자체가 있으면 POSIX 이스케이프: foo'bar → 'foo'"'"'bar'
      - 단순 토큰(공백·특수문자 없음)은 인용 안 함 (기존 동작·테스트 보존)
      - 빈 문자열 → ''

    NOTE: PowerShell-only 윈도우(Git Bash 없음)는 미지원 — statusLine 자동설치는
    Git Bash 전제다. PowerShell call operator(& "path") 분기는 BACKLOG 참조.
    """
    if not s:
        return "''"
    # 이미 single-quote로 감싼 토큰은 그대로
    if s[0] == "'" and s[-1] == "'":
        return s
    # 특수문자가 없는 단순 토큰은 인용 안 함
    _SPECIAL = set(' \t"\'$`!\\')
    if not any(c in _SPECIAL for c in s):
        return s
    # POSIX single-quote escape: ' → '"'"'
    escaped = s.replace("'", "'\"'\"'")
    return "'" + escaped + "'"


class Adapter:
    # install-skills 가 심링크를 거는 실호스트 기본 스킬 디렉토리(에이전트별로 다름).
    # claude = ~/.claude/skills. codex 서브클래스가 ~/.codex/skills 로 재정의(L2-C).
    # 테스트·격리(--settings)는 skills_dir 인자로 tmp 주입 — 실호스트 무접촉.
    DEFAULT_SKILLS_DIR = "~/.claude/skills"

    def __init__(self, agent_dir, manifest_path, settings_path,
                 python=None, team_root=None, events=None,
                 config_path=None, providers_dir=None, mcp_config_path=None,
                 skills_dir=None):
        self.agent_dir = Path(agent_dir)
        self.manifest_path = Path(manifest_path)
        self.settings_path = Path(settings_path)
        # python=None → 설치 시점 인터프리터 절대경로(W-B, python3 하드코딩 제거).
        self.python = python if python is not None else default_python()
        if team_root is not None and str(team_root).strip() == "":
            raise ValueError(
                "--team-root 에 빈 문자열/공백을 지정할 수 없습니다. "
                "생략하거나 유효한 경로를 전달하세요."
            )
        self.team_root = Path(team_root).resolve() if team_root else self.agent_dir.parents[2]
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
        # 센티넬 _SEALED → MCP 등록 파일 봉인(N3): 이 어댑터는 ~/.claude.json 류 실경로를
        # 쓰면 안 됨(예: codex 가 상속받되 자기 config.toml 블록으로 재정의하므로 부모 경로
        # 무사용). 봉인 시 install_mcp/_read_mcp_config 가 실경로 접근 없이 NotImplementedError
        # 또는 무동작 — 상속된 ~/.claude.json 이 footgun 으로 새는 것을 정적으로 차단.
        if mcp_config_path is _SEALED:
            self.mcp_config_path = _SEALED
        elif mcp_config_path is not None:
            self.mcp_config_path = Path(mcp_config_path)
        else:
            self.mcp_config_path = Path(os.path.expanduser("~/.claude.json"))
        # 스킬 심링크 타깃 디렉토리 — skills_dir=None 이면 에이전트별 실호스트 기본
        # (claude DEFAULT_SKILLS_DIR=~/.claude/skills, codex=~/.codex/skills). 격리·테스트는
        # 명시 주입. 소스는 team_root/infra/skills/<layer>/<name>/ (L2-C).
        if skills_dir is not None:
            self.skills_dir = Path(skills_dir)
        else:
            self.skills_dir = Path(os.path.expanduser(self.DEFAULT_SKILLS_DIR))
        self.skills_src_dir = self.team_root / "infra" / "skills" / "base"

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

        S6 일반화: `teammode` 단일 MCP 서버는 provider 팩 시스템과 별개로 handlers/ 존재
        여부로 연결 판정한다. handlers/ 에 .py 파일이 하나라도 있으면 "연결됨".
        """
        # S6: teammode 단일 서버는 handlers/ 존재 여부로 연결 판정 (provider 팩 무관)
        if canonical_server == "teammode":
            return self._has_handlers()

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
        # 슬래시 정규화 우선(백슬래시를 아예 안 내보냄), 그 다음 공백 따옴표.
        # sys.executable·normalize.py·스크립트 경로 셋 다 적용 — 윈도우 bash escape 방지.
        parts = [
            _quote_arg(_to_slash(str(self.python))),
            _quote_arg(_to_slash(str(self.normalize_path))),
            _quote_arg(_to_slash(script)),
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
        # 슬래시 정규화 후 비교 — build_command 가 slash 경로를 내보내므로(윈도우 bash
        # escape 방지) 마커도 동일 정규화. 기존 백슬래시로 등록된 훅도 같은 정규화로
        # 인식돼 재sync 시 slash 경로로 갱신된다(마이그레이션 일관).
        command = _to_slash(command)
        marker = _to_slash(str(self.normalize_path))
        # 절대경로 또는 상대형 둘 다 허용 — agents/<name>/normalize.py 꼬리 일치
        tail = _to_slash(os.path.join("agents", self.events.get("agent", ""), "normalize.py"))
        return marker in command or tail in command

    # ── install-mcp (§2.8) ──

    def _read_mcp_config(self) -> dict:
        """MCP 등록 파일(~/.claude.json) 전체를 읽는다. 부재/깨짐 → {}.

        실 ~/.claude.json 은 mcpServers 외에도 projects 등 사용자 데이터를 담으므로
        **전체를 보존**하고 mcpServers 만 머지한다(사용자 데이터 무접촉).
        """
        if self.mcp_config_path is _SEALED:
            # 봉인(N3): 이 어댑터는 ~/.claude.json 류 실경로를 안 본다(서브클래스가
            # 자기 방식으로 재정의해야 함). 부모 구현을 잘못 호출하면 무동작 {} 반환 —
            # 실경로 접근 0. (codex 는 _read_mcp_servers·install_mcp 를 전부 재정의.)
            return {}
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

    def _build_teammode_entry(self) -> dict:
        """teammode 단일 MCP 서버 등록 항목 (S5).

        handlers/ 디렉토리가 있을 때만 호출된다. 실 command/args/cwd 를 포함해
        role_server.py 를 기동할 수 있는 완전한 항목을 반환한다.
        """
        team_root = self.team_root  # 절대경로 (Adapter.__init__ 에서 resolve 됨)
        handlers_dir = team_root / "handlers"
        # team name: team.config.json의 team.name 필드, 없으면 팀루트 디렉토리 이름 사용
        team_name = team_root.name
        if self.config_path.is_file():
            try:
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                team_name = cfg.get("team", {}).get("name", team_name) or team_name
            except (ValueError, OSError, AttributeError):
                pass
        return {
            "_teammode_managed": True,
            "_canonical_server": "teammode",
            "command": "python",
            "args": [
                "-m", "infra.mcp.role_server",
                "--team", str(team_name),
                "--handlers-dir", str(handlers_dir),
            ],
            "cwd": str(team_root),
        }

    def _has_handlers(self) -> bool:
        """team_root/handlers/ 에 .py 파일이 하나 이상 있으면 True."""
        handlers_dir = self.team_root / "handlers"
        if not handlers_dir.is_dir():
            return False
        return any(handlers_dir.glob("*.py"))

    def install_mcp(self) -> list:
        """config services 의 연결 provider 를 MCP 서버로 등록(자기 방식). 멱등.

        - services 부재(파일 없음/빈 {}) → 등록할 것 없음, [info] 후 종료(빈 슬롯 1급).
        - 채운 슬롯의 provider 마다 정규 서버명으로 등록(별칭=정규명, resolve 항등).
        - 같은 provider 가 여러 역할에 쓰여도 1회만 등록(정규명 dedup).
        - teammode 소유 항목만 추가·갱신. 사용자 항목 무접촉.
        """
        if self.mcp_config_path is _SEALED:
            # 봉인된 어댑터가 부모 install_mcp 를 호출하면 안 된다(서브클래스가 재정의해야).
            # 실경로 무접촉을 위해 즉시 거부 — silent footgun 대신 명시 실패(N3).
            raise NotImplementedError(
                "MCP 등록 파일이 봉인됨 — 이 어댑터는 install_mcp 를 재정의해야 합니다.")
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
        # ⚠️ 안전(P1-1): mcpServers 키를 setdefault 로 무조건 주입하면 빈 슬롯에서도
        # 부재 파일을 {"mcpServers":{}} 로 새로 만들고, 기존 사용자 ~/.claude.json 에도
        # 빈 mcpServers 를 주입+리인덴트해 touch 한다(실 ~/.claude.json 0바이트 무접촉 위반).
        # → 원본에 mcpServers 가 있었는지 보존하고, 실제 서버 변경이 있을 때만 키를 만든다.
        had_servers_key = isinstance(data.get("mcpServers"), dict)
        servers = data["mcpServers"] if had_servers_key else {}

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

        # S5: teammode 단일 MCP 서버 등록 (공존 전략).
        # handlers/ 디렉토리에 .py 파일이 있으면 teammode alias 를 desired 에 추가.
        # 기존 provider alias(linear 등) 와 공존 — desired_aliases 에 둘 다 포함.
        if self._has_handlers():
            tm_alias = "teammode"
            tm_entry = self._build_teammode_entry()
            desired_aliases.add(tm_alias)
            existing_tm = servers.get(tm_alias)
            if self._is_owned_mcp(existing_tm) and existing_tm == tm_entry:
                pass  # 멱등: 변경 없음
            elif existing_tm is not None and not self._is_owned_mcp(existing_tm):
                # 사용자가 직접 등록한 teammode 서버 — 무접촉.
                changes.append(f"[warn] {tm_alias}: 사용자 등록 MCP 서버 존재 → 무접촉")
                desired_aliases.discard(tm_alias)
            else:
                servers[tm_alias] = tm_entry
                changes.append(f"[mcp] {tm_alias} 등록")

        # 제거: teammode 소유지만 더 이상 연결되지 않는 항목.
        for alias in list(servers.keys()):
            if self._is_owned_mcp(servers[alias]) and alias not in desired_aliases:
                del servers[alias]
                changes.append(f"[remove-mcp] {alias}")

        # 안전(P1-1): 등록/제거할 teammode 서버가 0이면 파일을 절대 건드리지 않는다.
        #   - servers 가 비었고 원본에 mcpServers 키가 없었으면 키 주입 자체를 생략
        #     (부재→부재 유지, 기존 사용자 파일은 바이트 동일).
        #   - 실질 서버 변경이 있을 때만 data 에 키를 반영하고 직렬화·비교한다.
        connected_providers = len(desired_aliases)
        if servers or had_servers_key:
            # 등록할 서버가 있거나(servers 비어있지 않음) 원본에 이미 mcpServers 가
            # 있던 경우엔 키를 반영해 정규 직렬화. (빈 슬롯+키부재면 이 분기 미진입.)
            data["mcpServers"] = servers
            new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            if new_text != original_text:
                self.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
                self.mcp_config_path.write_text(new_text, encoding="utf-8")
        # else: 빈 슬롯 + mcpServers 키 부재 → 파일 무접촉(부재→부재).
        if not changes:
            # N1: 연결 provider 0 = 진짜 빈 슬롯, >0 = 멱등 무변경(거짓 빈슬롯 메시지 금지).
            if connected_providers:
                changes.append(
                    f"[ok] 변경 없음 ({connected_providers}개 provider 등록됨)")
            else:
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
                    # S6: teammode 단일 서버는 handlers/ 없으면 완전 미설정 상태(opt-in).
                    # handlers/ 없음 = 팀이 아직 tm-connect 를 실행하지 않은 것 → 조용히 생략.
                    # provider 기반 서버와 달리 [info] 메시지 없음(팀 모르게 silent skip).
                    if canonical == "teammode":
                        continue
                    # 기존 provider 기반 서버: 빈 슬롯 우선 규칙 + [info]
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
            # manifest 의 timeout 은 **초** 단위(직접 기록). Claude Code hook timeout 도
            # 초 단위이므로 변환 없이 그대로 settings.json 에 쓴다(변환 드리프트 제거).
            timeout = entry.get("timeout") or None
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
                    needs_update = False
                    if inner[0].get("command") != command:
                        inner[0]["command"] = command
                        needs_update = True
                    # P1: timeout 변경도 갱신(기존 5000 잔존 방지 — command 동일이어도
                    # desired timeout 과 다르면 upsert 한다).
                    existing_timeout = inner[0].get("timeout")
                    if timeout and existing_timeout != timeout:
                        inner[0]["timeout"] = timeout
                        needs_update = True
                    elif not timeout and "timeout" in inner[0]:
                        del inner[0]["timeout"]
                        needs_update = True
                    if needs_update:
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

        # ── statusLine 관리 (settings.json "statusLine" 키) ──
        # 개인 statusLine 없음 → 단독설치. 있음(teammode 아닌 것) → 무접촉 + 안내.
        # 이미 teammode 설치됨 → 멱등 skip. off → _teammode_managed이면 제거.
        changes += self._sync_status_line(settings, mode, warnings)

        changed = self._write_settings(settings, original_text)
        for w in warnings:
            print(w)
        for i in infos:
            print(i)
        if not changed and not warnings and not infos:
            changes.append("[ok] 변경 없음")
        return changes

    def _get_team_name(self) -> str:
        """team.config.json 의 team.name 을 읽어 반환. 파싱 실패 시 팀루트 디렉토리명 폴백.

        팀명 하드코딩 금지(스펙 제약) — team.config.json 에서 동적 생성.
        파일 부재 / 파싱 실패 시 팀루트 디렉토리명을 폴백으로 사용한다.
        """
        fallback = self.team_root.name  # 팀루트 디렉토리명 폴백
        if self.config_path.is_file():
            try:
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                name = cfg.get("team", {}).get("name") if isinstance(cfg, dict) else None
                if name and isinstance(name, str):
                    return name
            except (ValueError, OSError, AttributeError):
                pass
        return fallback

    @staticmethod
    def _is_bash_compatible(command: str, *, resolve_shebang: bool = True) -> bool:
        """command 가 bash 호환 셸 스크립트인지 판정.

        True (bash 호환 확실):
          - command 토큰에 '.sh' 포함
          - command 가 'sh ' 또는 'bash ' 로 시작
          - 첫 토큰이 실제 파일이고 shebang 첫 줄에 'sh' 또는 'bash' 포함

        False (불확실 또는 확실히 비호환):
          - command 에 'powershell', 'pwsh', '.ps1' 포함
          - 위 True 조건에 해당하지 않는 경우 (보수적 = False)

        파일 읽기 실패(부재·권한 없음) → shebang 확인 생략(비치명적).
        """
        if not command:
            return False
        cmd_lower = command.lower()

        # 확실히 비호환: PowerShell
        if any(kw in cmd_lower for kw in ("powershell", "pwsh", ".ps1")):
            return False

        # .sh 토큰 포함
        if ".sh" in command:
            return True

        # 실행형 bash / sh 인식 — 첫 토큰 basename 또는 env 다음 토큰
        # /bin/bash, /usr/bin/sh, env bash, /usr/bin/env bash 등을 모두 인식.
        # 기존 'bash '/'sh ' prefix 단순 비교는 /bin/bash 등을 놓쳐서 basename 비교로 교체.
        try:
            import shlex as _shlex_check
            _tokens_check = _shlex_check.split(command)
        except ValueError:
            _tokens_check = command.split()
        if _tokens_check:
            _first = _tokens_check[0]
            _basename_first = os.path.basename(_first)
            if _basename_first in ("bash", "sh"):
                return True
            # env bash / /usr/bin/env bash 패턴
            if _basename_first == "env" and len(_tokens_check) >= 2:
                _second = _tokens_check[1]
                if _second in ("bash", "sh"):
                    return True

        # 첫 토큰이 파일 → shebang 확인
        if resolve_shebang:
            try:
                import shlex as _shlex
                tokens = _shlex.split(command)
            except ValueError:
                tokens = command.split()
            if tokens:
                first = tokens[0]
                try:
                    p = Path(first)
                    if p.is_file():
                        first_line = p.read_text(encoding="utf-8", errors="replace").split("\n")[0]
                        if first_line.startswith("#!"):
                            # shebang 인터프리터 basename이 정확히 bash 또는 sh 일 때만 True.
                            # 'sh' 부분일치(zsh/csh/fish 오판 방지) 금지 — basename 단어 단위 비교.
                            _shebang_parts = first_line[2:].strip().split()
                            if _shebang_parts:
                                _shebang_basename = os.path.basename(_shebang_parts[0])
                                if _shebang_basename in ("bash", "sh"):
                                    return True
                                # env bash / env sh 패턴 (#!/usr/bin/env sh 등)
                                if _shebang_basename == "env" and len(_shebang_parts) >= 2:
                                    if _shebang_parts[1] in ("bash", "sh"):
                                        return True
                except (OSError, ValueError):
                    pass  # 파일 읽기 실패 → skip

        return False

    def _can_wrap_statusline(self, command: str,
                            *, is_windows: Optional[bool] = None) -> bool:
        """teammode wrapper 로 이 개인 statusLine 을 감쌀 수 있는가.

        wrapper(teammode_statusline.py)는 원본을 `bash -c <command>` 로 실행한다
        (_run_wrapped). → mac/linux 는 bash 가 항상 있어 **명령 종류 무관**(파이썬·노드·
        바이너리 등) 감쌀 수 있다. `bash -c` 로 못 돌리는 PowerShell(.ps1/pwsh)만 제외.
        Windows 는 Git Bash 유무가 불확실하므로 보수적 — _is_bash_compatible(확실한 bash)만.

        is_windows: 테스트 주입용(미지정 시 os.name 으로 판정).
        """
        if not command:
            return False
        if any(kw in command.lower() for kw in ("powershell", "pwsh", ".ps1")):
            return False  # bash -c 로 못 돌림
        if is_windows is None:
            is_windows = os.name == "nt"
        if not is_windows:
            return True  # mac/linux: bash -c 로 어떤 명령이든 wrap 가능
        return self._is_bash_compatible(command)  # Windows: 확실한 bash 만

    def _build_status_line_entry(self, wrapped_command: Optional[str] = None,
                                original_entry: Optional[dict] = None) -> dict:
        """teammode 소유 statusLine 항목 생성.

        sys.executable + teammode_statusline.py 절대경로로 command 구성.
        크로스OS 패턴(기존 훅 명령과 동일): sys.executable 절대경로 사용,
        Windows bash escape 방지를 위해 slash 경로 정규화(_to_slash 사용).
        개인 bash statusLine과의 셸 미스매치를 피하기 위해
        subprocess 재실행 없는 단독 Python 스크립트 실행 방식을 사용한다.

        wrapped_command 가 주어지면 wrapper 항목을 생성한다:
          command = <python> <teammode_statusline.py> --wrapped <quoted_original_command>
          반환 dict에 _teammode_wrapped=True, _wrapped_command=<original_command> 포함.
          original_entry 가 주어지면 _wrapped_entry 에 원본 dict 전체를 보존한다 — off 복원 시
          padding·custom 등 원본 속성이 손실 없이 복원된다.

        NOTE(플랫폼 지원 범위): statusLine 자동설치는 **Git Bash 전제**.
        PowerShell-only 윈도우(Git Bash 미설치)에서는 quoted executable이
        call operator(`&`) 없이 실행되지 않아 동작하지 않는다.
        후속 대응은 BACKLOG의 "statusLine PowerShell call operator 셸분기" 참조.
        """
        script_path = self.agent_dir / "teammode_statusline.py"
        python = _to_slash(str(self.python))
        script = _to_slash(str(script_path))
        # 공백 포함 경로 따옴표 처리
        if wrapped_command is not None:
            command = f"{_quote_arg(python)} {_quote_arg(script)} --wrapped {_quote_arg(wrapped_command)}"
            entry = {
                "type": "command",
                "command": command,
                "_teammode_managed": True,
                "_teammode_wrapped": True,
                "_wrapped_command": wrapped_command,
            }
            if original_entry is not None:
                entry["_wrapped_entry"] = original_entry
            return entry
        command = f"{_quote_arg(python)} {_quote_arg(script)}"
        return {
            "type": "command",
            "command": command,
            "_teammode_managed": True,
        }

    def _is_team_command(self, command: str) -> bool:
        """command 문자열이 teammode_statusline.py를 가리키는지 판정."""
        return "teammode_statusline.py" in _to_slash(command)

    def _sync_status_line(self, settings: dict, mode: Optional[str],
                          warnings: list) -> list:
        """settings dict 의 statusLine 키를 mode 에 맞게 동기화. 변경 로그 반환.

        | 개인 statusLine 상태                         | --on 동작                                    | --off 동작                                |
        |---------------------------------------------|----------------------------------------------|-------------------------------------------|
        | 없음                                        | 단독설치 (_teammode_managed)                 | no-op                                     |
        | 있음(managed=True, wrapped=True)             | 멱등 확인(rebuild+비교), 변경 시 갱신         | _wrapped_command 원복                     |
        | 있음(managed=True, standalone)              | stale 체크 → 갱신                            | teammode 명령 가리키면 제거               |
        | 있음(teammode 아닌 것, bash 호환)            | auto-wrap → wrapper 항목으로 교체             | no-op                                     |
        | 있음(teammode 아닌 것, bash 불명)            | 무접촉 + 판단필요 경고                       | no-op                                     |
        | managed marker True + 외부 command           | 갱신(현재 올바른 경로로)                     | 경고+보존 (외부 명령 삭제 금지)           |

        NOTE: statusLine 자동설치는 Git Bash 전제(PowerShell-only 윈도우 미지원).
        BACKLOG "statusLine PowerShell call operator 셸분기" 참조.
        """
        changes = []
        existing_sl = settings.get("statusLine")

        if mode == "on":
            if existing_sl is None:
                # 없음 → 단독설치
                settings["statusLine"] = self._build_status_line_entry()
                changes.append("[statusline] teammode statusLine 설치")
            elif isinstance(existing_sl, dict) and existing_sl.get("_teammode_managed") is True:
                if existing_sl.get("_teammode_wrapped") is True:
                    # wrapper managed 항목 — 멱등 확인 (double-wrap 방지)
                    original_cmd = existing_sl.get("_wrapped_command", "")
                    wanted = self._build_status_line_entry(wrapped_command=original_cmd)
                    if existing_sl.get("command") != wanted["command"]:
                        settings["statusLine"] = wanted
                        changes.append("[statusline] teammode statusLine 갱신(stale 수정)")
                    # else: 동일 → 멱등 skip
                else:
                    # standalone managed 항목 — command stale 체크
                    wanted = self._build_status_line_entry()
                    if existing_sl.get("command") != wanted["command"]:
                        settings["statusLine"] = wanted
                        changes.append("[statusline] teammode statusLine 갱신(stale 수정)")
                    # else: 동일 → 멱등 skip
            else:
                # 개인 statusLine 있음
                personal_cmd = (existing_sl.get("command", "")
                                if isinstance(existing_sl, dict) else "")
                if self._can_wrap_statusline(personal_cmd):
                    # wrap 가능(비-Windows 거의 전부 / Windows 는 확실한 bash) → auto-wrap.
                    # wrapper 가 bash -c 로 원본을 돌리므로 파이썬 statusLine 도 감싸진다.
                    # original_entry=existing_sl: padding·custom 등 모든 속성을 _wrapped_entry에 보존 → off 시 원상복원
                    settings["statusLine"] = self._build_status_line_entry(
                        wrapped_command=personal_cmd,
                        original_entry=existing_sl,
                    )
                    changes.append("[statusline] 개인 statusLine을 teammode wrapper로 감쌌습니다(원본 보존)")
                else:
                    # wrap 불가(PowerShell / Windows bash 불명) → 무접촉 + 판단필요 경고
                    warnings.append(
                        "[판단필요] 개인 statusLine 을 자동 감쌀 수 없습니다(PowerShell 또는 "
                        "Windows bash 불명) — AI가 확인 후 수동 연동하세요."
                    )
        else:
            # off (또는 None)
            if isinstance(existing_sl, dict) and existing_sl.get("_teammode_managed") is True:
                if existing_sl.get("_teammode_wrapped") is True:
                    # wrapper 항목 → 원본 복원:
                    # _wrapped_entry(전체 dict) 우선 복원 — padding·custom 등 원본 속성 보존.
                    # _wrapped_entry 없으면 _wrapped_command 로 최소 재생성(구버전 호환).
                    original_entry = existing_sl.get("_wrapped_entry")
                    original_cmd = existing_sl.get("_wrapped_command")
                    if original_entry is not None:
                        settings["statusLine"] = original_entry
                        changes.append("[statusline] 원본 statusLine 복원")
                    elif original_cmd:
                        settings["statusLine"] = {"type": "command", "command": original_cmd}
                        changes.append("[statusline] 원본 statusLine 복원")
                    else:
                        # _wrapped_command 없음 (비정상) → 그냥 제거
                        del settings["statusLine"]
                        changes.append("[statusline] teammode statusLine 제거")
                else:
                    cmd = existing_sl.get("command", "")
                    if self._is_team_command(cmd):
                        # teammode_statusline.py를 가리키는 managed entry → 제거
                        del settings["statusLine"]
                        changes.append("[statusline] teammode statusLine 제거")
                    else:
                        # marker=True인데 외부 command → 삭제 금지, 경고+보존
                        warnings.append(
                            "[warn] statusLine: _teammode_managed=True 이지만 command가 "
                            "teammode_statusline.py 가 아닌 외부 경로입니다 — 삭제하지 않고 보존합니다. "
                            f"command={cmd!r}"
                        )
            # 개인 statusLine(managed 아닌 것)이면 no-op (무접촉)

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

    # ── install-skills (§2.7 CLI, L2-C) — 스킬 디렉토리 심링크 ──
    #
    # infra/skills/<layer>/<name>/ (base: 항상 설치, core: on시, util: 멤버별)을 에이전트의
    # 스킬 경로(claude=~/.claude/skills, codex=~/.codex/skills)에 <name> 으로 심링크한다.
    # 윈도우 등 os.symlink 권한 실패(OSError) 시 디렉토리 복사로 폴백. 멱등(이미 올바른
    # 심링크/복사면 무변경). 소유 판정(is_owned_skill)으로 teammode 가 건 것만 관리하고
    # 사용자가 직접 둔 동명 스킬은 무접촉. uninstall_skills 는 역(소유분만 제거).
    #
    # v0.1 단순화(L2-C): 오버라이드 해석·requires 게이트·traversal 가드 없음(v0.2 이월).
    # 심링크/복사 + 멱등 + is_owned 만.

    def _skills_src_dir(self, layer: str) -> Path:
        """Get skill source directory for given layer (base|core|util)."""
        return self.team_root / "infra" / "skills" / layer

    def _skill_sources(self, layer: str = "base") -> list:
        """소스 스킬 디렉토리 목록 — infra/skills/<layer>/<name>/ 중 SKILL.md 보유한 것만."""
        src_dir = self._skills_src_dir(layer)
        if not src_dir.is_dir():
            return []
        out = []
        for child in sorted(src_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                out.append(child)
        return out

    def is_owned_skill(self, target: Path, src: Path) -> bool:
        """teammode 소유 스킬인지 — target 이 base/core/util 소스 중 하나를 가리키는지.

        심링크: 링크가 base+core+util 소스 중 하나를 가리키면 소유(절대경로 비교).
        복사(폴백): 우리 소유 마커 파일(_teammode_skill)이 있으면 소유.
        사용자가 직접 둔 동명 디렉토리(마커 없음·다른 링크 타깃)는 무접촉.
        """
        try:
            if target.is_symlink():
                link = os.readlink(target)
                link_abs = (target.parent / link) if not os.path.isabs(link) else Path(link)
                link_real = os.path.realpath(str(link_abs))
                # Check all layers
                for layer in ("base", "core", "util"):
                    layer_src = self._skills_src_dir(layer) / target.name
                    if link_real == os.path.realpath(str(layer_src)):
                        return True
                return False
            if target.is_dir():
                # 윈도우 정션: is_symlink 은 False 지만 realpath 가 우리 소스로 resolve → 소유.
                if os.name == "nt":
                    target_real = os.path.realpath(str(target))
                    for layer in ("base", "core", "util"):
                        layer_src = self._skills_src_dir(layer) / target.name
                        if target_real == os.path.realpath(str(layer_src)):
                            return True
                return (target / "_teammode_skill").is_file()
        except OSError:
            return False
        return False

    def _is_layer_skill(self, target: Path, layer: str) -> bool:
        """Is this skill installed by the given layer specifically?"""
        src_guess = self._skills_src_dir(layer) / target.name
        try:
            if target.is_symlink():
                link = os.readlink(target)
                link_abs = (target.parent / link) if not os.path.isabs(link) else Path(link)
                return os.path.realpath(str(link_abs)) == os.path.realpath(str(src_guess))
            if target.is_dir():
                if os.name == "nt" and \
                        os.path.realpath(str(target)) == os.path.realpath(str(src_guess)):
                    return True
                # Copy fallback: marker exists → check layer info
                marker = target / self._SKILL_MARKER
                if marker.is_file():
                    content = marker.read_text(encoding="utf-8")
                    if f"layer={layer}" in content:
                        return True
                    # Old-style marker (no layer info) → treat as base layer
                    if "layer=" not in content and layer == "base":
                        return True
        except OSError:
            return False
        return False

    _SKILL_MARKER = "_teammode_skill"

    def _link_one_skill(self, src: Path, target: Path, layer: str = "base") -> str:
        """소스 스킬을 target 에 심링크(폴백: 복사). 변경 종류 문자열 반환(없으면 '')."""
        import shutil

        # 멱등: 이미 우리 소유(올바른 심링크/복사)면 무변경.
        if target.exists() or target.is_symlink():
            if self.is_owned_skill(target, src):
                # 심링크가 이미 정확하면 무변경. 복사본은 소스가 바뀌었을 수 있으나
                # v0.1 은 마커 존재만으로 멱등 처리(재복사 안 함 — 단순화).
                return ""
            # 사용자(또는 무관) 동명 항목 — 무접촉(소유권).
            return f"[skip] {target.name}: 사용자 스킬 존재 → 무접촉"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(src), str(target), target_is_directory=True)
            return f"[skill] {target.name} 심링크"
        except OSError:
            # 윈도우 심링크 권한 실패(개발자모드/관리자 필요) → 정션(junction) 시도.
            # 정션은 권한 불필요 + 링크라 pull 시 소스 갱신 반영(복사와 달리 stale 없음).
            # py3.9 라 _winapi.CreateJunction(3.12+) 대신 subprocess mklink /J.
            if os.name == "nt":
                import subprocess
                # ⚠️ cmd /c mklink 는 cmd.exe 가 /c 뒤를 재파싱하므로, subprocess 리스트
                # 호출이어도 경로의 cmd 메타문자(& | < > ^ " %)가 명령 주입 표면이 된다.
                # 메타문자 없는 경로만 정션 시도, 있으면 안전하게 복사 폴백으로 떨군다.
                _paths = str(src) + str(target)
                if not any(c in _paths for c in '&|<>^"%'):
                    try:
                        subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(src)],
                                       check=True, capture_output=True)
                        return f"[skill] {target.name} 정션"
                    except (OSError, subprocess.SubprocessError):
                        pass  # 정션 실패 → 복사 최후 폴백
            # 최후 폴백: 디렉토리 복사 + 소유 마커(무겁고 갱신 안 됨 — 정션 실패 시에만).
            shutil.copytree(str(src), str(target))
            (target / self._SKILL_MARKER).write_text(
                f"teammode-managed skill copy (layer={layer}, name={target.name})\n",
                encoding="utf-8")
            return f"[skill] {target.name} 복사(폴백)"

    def install_skills(self, layer: str = "base") -> list:
        """infra/skills/<layer>/<name>/ → 스킬 디렉토리에 심링크(폴백: 복사). 멱등.

        layer 기본값 "base" — install.py wire, 어댑터 CLI install-skills, 기존 테스트 무변경.
        core/util 은 on(엔진)이 install_skills(layer="core") 등으로 별도 호출.

        고아 청소: 이 계층(layer)이 관리하는 소유 스킬만 대상. 다른 계층 스킬은 무접촉.
        """
        raw_changes = []
        sources = self._skill_sources(layer=layer)
        wanted_names = {src.name for src in sources}
        for src in sources:
            target = self.skills_dir / src.name
            msg = self._link_one_skill(src, target, layer=layer)
            if msg:
                raw_changes.append(msg)
        # 정리: 이 계층이 소유하지만 소스에서 사라진 스킬만 제거(계층 한정 고아청소).
        if self.skills_dir.is_dir():
            for child in sorted(self.skills_dir.iterdir()):
                if child.name in wanted_names:
                    continue
                if self._is_layer_skill(child, layer):
                    self._remove_skill(child)
                    raw_changes.append(f"[remove-skill] {child.name}")
        if not raw_changes:
            return ["[ok] 변경 없음"]

        # 심링크 생성 로그 요약: "[skill] X 심링크/정션/복사(폴백)" 줄을 1줄 요약으로 집계.
        # 그 외([skip], [remove-skill] 등)는 그대로 유지.
        agent_name = self.events.get("agent", "")
        skill_msgs = [m for m in raw_changes if m.startswith("[skill]")]
        other_msgs = [m for m in raw_changes if not m.startswith("[skill]")]
        changes: list = list(other_msgs)
        if skill_msgs:
            n = len(skill_msgs)
            # 복사(폴백)가 하나라도 있으면 요약에 복사 표기
            has_copy = any("복사" in m for m in skill_msgs)
            suffix = " 심링크/복사(폴백)" if has_copy else " 심링크"
            changes.append(f"[skill] {n}개{suffix} ({agent_name})")
        return changes

    def _remove_skill(self, target: Path):
        import shutil
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            # 윈도우 정션은 is_symlink=False·is_dir=True 라 rmtree 면 링크를 따라가 원본까지
            # 지울 위험. rmdir 은 정션 링크만 제거(원본 무접촉). 실디렉(복사)은 비어있지
            # 않아 rmdir 이 실패 → rmtree 로 폴백.
            if os.name == "nt":
                try:
                    os.rmdir(str(target))
                    return
                except OSError:
                    pass
            shutil.rmtree(str(target))

    def uninstall_skills(self) -> list:
        """teammode 소유 스킬 심링크/복사 전부 제거(역). 사용자 스킬 무접촉."""
        changes = []
        if not self.skills_dir.is_dir():
            return ["[ok] 제거할 스킬 없음"]
        for child in sorted(self.skills_dir.iterdir()):
            src_guess = self.skills_src_dir / child.name
            if self.is_owned_skill(child, src_guess):
                self._remove_skill(child)
                changes.append(f"[remove-skill] {child.name}")
        if not changes:
            changes.append("[ok] 제거할 스킬 없음")
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
    _ensure_utf8_io()  # 한글 [warn]/[ok] print 가 cp949 콘솔에서 크래시 방지(직접 실행 방어)
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="claude-adapter")
    p.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    # --python 기본 None → 설치 시점 sys.executable(절대경로) 해석 (W-B, 크로스플랫폼)
    p.add_argument("--python", default=None)
    # --team-root: 팀 레포 루트 절대경로. wire_agents 가 명시 전달해 __file__ 추론 어긋남 해소
    # (S0). 없으면 기존 기본값(here.parents[2]) 유지 — 하위 호환.
    p.add_argument("--team-root", default=None)
    # MCP 등록 파일·config·providers 경로 — 기본은 실 경로지만 테스트는 tmp 주입(격리).
    p.add_argument("--mcp-config", default=None)
    p.add_argument("--config", default=None)
    p.add_argument("--providers-dir", default=None)
    # install-skills 가 심링크를 거는 스킬 디렉토리 — 기본 None(에이전트별 실호스트),
    # 격리/테스트는 tmp 주입(실 ~/.claude/skills 무접촉).
    p.add_argument("--skills-dir", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync")
    sp.add_argument("--on", action="store_true")
    sp.add_argument("--off", action="store_true")
    sub.add_parser("uninstall")
    sub.add_parser("install-mcp")
    sub.add_parser("install-skills")

    args = p.parse_args(argv)

    # 명시적 --team-root 가 빈 문자열/공백이면 조용히 기본값으로 폴백하지 않고 명확히 거부.
    if args.team_root is not None and args.team_root.strip() == "":
        print("[error] --team-root 에 빈 문자열/공백을 지정할 수 없습니다.", file=sys.stderr)
        return 1

    d = _default_paths()
    adapter = Adapter(
        agent_dir=d["agent_dir"],
        manifest_path=d["manifest_path"],
        settings_path=args.settings,
        python=args.python,
        team_root=args.team_root if args.team_root is not None else d["team_root"],
        config_path=args.config,
        providers_dir=args.providers_dir,
        mcp_config_path=args.mcp_config,
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
