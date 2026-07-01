#!/usr/bin/env python3
"""provider 팩 (providers/<name>.json) load·validate·lookup — L2-A (SPEC §7, 부록 B-1).

provider 팩 = 한 provider(linear·slack·notion·google …)가 teammode 슬롯에 연결될 때
필요한 **데이터**(역할·연결방식·scope 성향·config 가 요구하는 인스턴스 필드·토큰 안내·
MCP 등록 스펙). 번역표·연결성향을 코드 분기에 숨기지 않고 데이터로 둔다(events.json 과
같은 정신).

핵심 불변식(SPEC §2.5):
- **항등 불변식**: `provider` 필드 == 파일이 선언하는 정규 서버명. v0.1 은 둘을 분리하지
  않으므로(canonical_server 미도입) `provider` 하나가 정규 서버명을 겸한다. 위반 시 reject.

mcp(L2 재설계, 2026-06-25 — 등록 스펙):
- L2 = 슬롯에 공식/자작 MCP 를 꽂는 등록기. mcp 필드가 그 등록 스펙을 담는다.
  - `register_hint` (필수): 등록 안내 문자열(사람·LLM 용).
  - `source` (선택): "official"(공식 레포 가져옴) | "custom"(자작). 있으면 값 검증.
  - `repo` (선택): 공식 MCP 의 git 레포 URL/식별자(official 일 때).
  - `command`·`args` (선택): MCP 서버 기동 커맨드(등록 시 사용). args 는 리스트.
  - `path` (선택): 자작/벤더드 official 의 배치 경로(infra/mcp/<provider>/).
  - `transport` (선택, issue #20): "http"|"stdio". "http" 면 공식 호스티드
    (streamable HTTP) MCP 를 `url` 로 등록(notion/linear 등). 미지정=stdio.
  - `url` (선택, issue #20): transport="http" 일 때 호스티드 MCP 엔드포인트 URL.
- 검증은 "있으면 타입 체크" 수준(register_hint 외 전부 선택). 정확한 패키지명을 모르면
  추측으로 박지 않고 register_hint 에 서술 + source 만 둔다(추측이 더 위험).

설계 원칙(install_lib 와 동일): 호스트 무접촉. 디렉토리는 인자 주입(테스트는 tmp).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# providers/ 기본 위치 = 레포 루트/providers (이 파일은 infra/ 안에 있다).
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROVIDERS_DIR = _REPO_ROOT / "providers"

# 허용 auth 값 (부록 B-1). 데이터로 둬서 tm-connect 가 하드코딩 안 하게.
VALID_AUTH = {"api_key", "oauth", "bot_token"}
# 허용 scope 값 (SPEC §7.1).
VALID_SCOPE = {"team", "personal"}
# 허용 mcp.source 값 (L2 등록 스펙). 공식 레포 가져옴 vs 자작.
VALID_MCP_SOURCE = {"official", "custom"}
# 허용 mcp.transport 값 (issue #20). "http" = 공식 호스티드(streamable HTTP) MCP 를
# URL 로 등록(notion/linear 등). "stdio" = 로컬 기동 커맨드(command/path). 미지정이면
# stdio 로 본다(하위호환).
VALID_MCP_TRANSPORT = {"http", "stdio"}

# 스키마 필수 키 (부록 B-1).
_REQUIRED_KEYS = {
    "provider",
    "token_guide",
    "default_scope",
    "auth",
    "services",
    "resource_fields",
    "mcp",
}
# 선택 키 — 존재해도 거부하지 않으나, 그 외 미지 키는 거부(오타 검출).
# (L2 재설계: action_map 폐기 — 등록 스펙은 mcp 필드 하위로 이동.)
_OPTIONAL_KEYS: set = set()
_KNOWN_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS


class ProviderValidationError(ValueError):
    """provider 팩 스키마/불변식 위반 — load 시 reject."""


@dataclass
class ProviderPack:
    """검증을 통과한 provider 팩(읽기 전용 표현)."""

    provider: str
    token_guide: dict
    default_scope: str
    auth: str
    services: list
    resource_fields: list
    mcp: dict
    raw: dict | None = None

    # 정규 서버명 == provider (항등 불변식, §2.5). 별 메서드로 의도 명시.
    @property
    def canonical_server(self) -> str:
        return self.provider


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ProviderValidationError(msg)


def validate_pack(data, *, expected_name: str | None = None) -> ProviderPack:
    """provider 팩 dict 를 검증 → ProviderPack. 위반 시 ProviderValidationError.

    expected_name 주입 시(파일명 기반 호출) **항등 불변식**까지 강제:
    `provider` 필드 == expected_name(= 파일명 == 정규 서버명). 위반 reject.
    """
    _require(isinstance(data, dict), "provider 팩은 object 여야 합니다.")

    missing = sorted(_REQUIRED_KEYS - data.keys())
    _require(not missing, f"필수 키 누락: {missing}")

    unknown = sorted(set(data.keys()) - _KNOWN_KEYS)
    _require(not unknown, f"알 수 없는 키(오타 의심): {unknown}")

    provider = data["provider"]
    _require(isinstance(provider, str) and provider.strip(),
             "provider 는 비어있지 않은 문자열이어야 합니다.")

    # 항등 불변식(§2.5) — provider == 정규 서버명(== 파일명).
    if expected_name is not None:
        _require(
            provider == expected_name,
            f"항등 불변식 위반: provider='{provider}' != 정규 서버명(파일명)"
            f"='{expected_name}'. v0.1 은 provider==정규서버명 항등이 강제입니다(§2.5).")

    token_guide = data["token_guide"]
    _require(isinstance(token_guide, dict), "token_guide 는 object 여야 합니다.")
    _require(isinstance(token_guide.get("url"), str) and token_guide.get("url"),
             "token_guide.url 은 비어있지 않은 문자열이어야 합니다.")
    _require(isinstance(token_guide.get("steps"), list),
             "token_guide.steps 는 리스트여야 합니다.")

    default_scope = data["default_scope"]
    _require(default_scope in VALID_SCOPE,
             f"default_scope 는 {sorted(VALID_SCOPE)} 중 하나여야 합니다 "
             f"(받음: {default_scope!r}).")

    auth = data["auth"]
    _require(auth in VALID_AUTH,
             f"auth 는 {sorted(VALID_AUTH)} 중 하나여야 합니다 (받음: {auth!r}).")

    services = data["services"]
    _require(isinstance(services, list) and services
             and all(isinstance(s, str) and s for s in services),
             "services 는 비어있지 않은 역할(문자열) 리스트여야 합니다.")

    resource_fields = data["resource_fields"]
    _require(isinstance(resource_fields, list)
             and all(isinstance(f, str) and f for f in resource_fields),
             "resource_fields 는 (빈 리스트 허용) 문자열 리스트여야 합니다.")

    mcp = data["mcp"]
    _require(isinstance(mcp, dict), "mcp 는 object 여야 합니다.")
    _require(isinstance(mcp.get("register_hint"), str)
             and mcp.get("register_hint"),
             "mcp.register_hint 은 비어있지 않은 문자열이어야 합니다.")

    # mcp 등록 스펙(L2) — 전부 선택. "있으면 타입 체크"만(official/custom 따라 다름).
    if "source" in mcp:
        _require(mcp["source"] in VALID_MCP_SOURCE,
                 f"mcp.source 는 {sorted(VALID_MCP_SOURCE)} 중 하나여야 합니다 "
                 f"(받음: {mcp['source']!r}).")
    for _k in ("repo", "command", "path", "url"):
        if _k in mcp:
            _require(isinstance(mcp[_k], str) and mcp[_k].strip(),
                     f"mcp.{_k} 은 비어있지 않은 문자열이어야 합니다.")
    if "args" in mcp:
        _require(isinstance(mcp["args"], list)
                 and all(isinstance(a, str) for a in mcp["args"]),
                 "mcp.args 는 문자열 리스트여야 합니다.")
    if "transport" in mcp:
        _require(mcp["transport"] in VALID_MCP_TRANSPORT,
                 f"mcp.transport 는 {sorted(VALID_MCP_TRANSPORT)} 중 하나여야 합니다 "
                 f"(받음: {mcp['transport']!r}).")
        # transport=="http" 는 호스티드 엔드포인트가 본질 — url 없으면 무의미(등록 시
        # placeholder 로 빠져 조용히 비동작). 추측 대신 명시 거부(codex review P2-b).
        if mcp["transport"] == "http":
            _require(isinstance(mcp.get("url"), str) and mcp.get("url", "").strip(),
                     "mcp.transport=='http' 이면 mcp.url(호스티드 MCP 엔드포인트)이 "
                     "필수입니다.")

    return ProviderPack(
        provider=provider,
        token_guide=token_guide,
        default_scope=default_scope,
        auth=auth,
        services=list(services),
        resource_fields=list(resource_fields),
        mcp=mcp,
        raw=data,
    )


def load_pack(path) -> ProviderPack:
    """단일 provider 팩 파일 load + validate. 파일명(stem)으로 항등 불변식 강제.

    파일 부재/깨진 JSON → ProviderValidationError(크래시 대신 명시적 거부).
    """
    p = Path(path)
    _require(p.is_file(), f"provider 팩 파일이 없습니다: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProviderValidationError(f"provider 팩 JSON 파싱 실패({p}): {e}") from e
    return validate_pack(data, expected_name=p.stem)


def load_all(providers_dir=None) -> dict:
    """providers_dir 의 모든 <name>.json 을 load → {provider: ProviderPack}.

    디렉토리 부재 → 빈 dict(빈 슬롯 = 1급 시민; provider 팩 없음은 정상).
    """
    d = Path(providers_dir) if providers_dir is not None else DEFAULT_PROVIDERS_DIR
    out: dict = {}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        pack = load_pack(f)
        out[pack.provider] = pack
    return out


def lookup(provider: str, providers_dir=None) -> ProviderPack | None:
    """정규 서버명으로 provider 팩 조회. 없으면 None(추측 금지)."""
    d = Path(providers_dir) if providers_dir is not None else DEFAULT_PROVIDERS_DIR
    f = d / f"{provider}.json"
    if not f.is_file():
        return None
    return load_pack(f)
