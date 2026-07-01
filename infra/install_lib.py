#!/usr/bin/env python3
"""install.py 부트스트랩 코어 — 순수/주입 가능 함수 (spec/04).

설계 원칙(§1.2):
- **결정적**: 같은 입력 → 같은 결과. LLM 즉흥 판단 0.
- **env 불신뢰(§10, P1)**: 팀 루트·HOME·git 값은 전부 **명시 인자로 주입**받는다.
  ambient `TEAMMODE_HOME` 을 코드가 신뢰하지 않는다(사고 근본 처방).
- 부작용(파일 쓰기·subprocess)은 install.py 오케스트레이터가, 판정·계산은 여기서.

여기 함수들은 환경을 직접 읽지 않고 주입받으므로 단위 테스트가 호스트를 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# 엔진 _validate_author 재사용 (m1) — 이름 검증 단일 소스(드리프트 방지).
_INFRA = Path(__file__).resolve().parent
if str(_INFRA) not in sys.path:
    sys.path.insert(0, str(_INFRA))
import providers as _providers  # noqa: E402
import teammode as _engine  # noqa: E402

# ⚠️ 테스트 순서 견고성: test_cli_join_wizard 가 collection 시점에 sys.modules['teammode']
# 를 pip 런처 패키지(src/teammode) 스텁으로 등록한다(infra/teammode.py 엔진과 이름 충돌).
# 기본 수집 순서에선 install_lib 가 더 일찍 import 돼 실제 엔진에 바인딩되지만, 역순/-k 등에선
# 스텁(=_validate_author 없음)에 바인딩될 수 있다. 모듈레벨 바인딩은 유지하되(run-time 스텁
# 상태와 무관해야 함), 스텁이 잡히면 실제 엔진을 파일에서 직접 로드해 교정한다.
if not hasattr(_engine, "_validate_author"):
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_tm_engine_real", str(_INFRA / "teammode.py"))
    _engine = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_engine)

# Python 버전 하한 (§12-1 미결 — 타깃 머신 분포 근거 나오면 확정).
# 보수적으로 3.9 로 둔다(현행 런타임 훅·엔진이 3.9+ 문법 사용).
MIN_PYTHON = (3, 9)

# 팀 데이터가 따르는 스펙 묶음 버전 (SPEC §6, §0.4). 0.2 — issue 동사(§3.5) 추가로
# 엔진 동사 계약이 minor bump 됨. 이 툴킷은 0.2 계약을 구현한다.
SPEC_VERSION = "0.2"

# 도입자 판정용 placeholder/미초기화 표식 (§4 ③). team.name 이 이 중 하나면 미초기화.
_PLACEHOLDER_NAMES = {"", "changeme", "todo", "your-team-name", "team-name",
                      "tbd", "placeholder"}

# 팀 레포 표식 (§2.2, §10): .git 또는 team.config.json 또는 memory/ 중 하나.
# install.py 는 "이미 받아진 레포 안에서" 실행된다고 가정(§1.3).
_TEAM_MARKERS = (".git", "team.config.json", "memory")


# ─────────────────────────── CLI 인자 (§3) ───────────────────────────

@dataclass
class Options:
    root: str | None = None
    agents: list = field(default_factory=list)  # [] = auto(감지 전부). 하위호환: agent(str) 제거.
    member_name: str | None = None
    role: str | None = None
    settings: str | None = None
    yes: bool = False
    update: bool = False
    dry_run: bool = False
    register_obsidian: bool = False
    obsidian_config: str | None = None
    team_name: str | None = None  # init 위저드 팀명(team.name·배너·배지 소스). 미지정 시 레포명 폴백.
    role_intent: str | None = None  # 동사 의도: 'introducer'(init)/'member'(join). 역할 파일추론 대체(1f).


_VALUE_FLAGS = {"--root", "--agent", "--member-name", "--role", "--settings",
                "--obsidian-config", "--team-name", "--role-intent"}


def parse_args(argv) -> Options:
    """argv → Options. 손파싱(엔진과 동일 스타일) — 동사별 정책 메시지 일관성.

    알 수 없는 플래그는 무시(후속 슬라이스 확장 여지). --<agent> 디스패치 흡수는
    install.py 오케스트레이터가 parse_args 전에 분기하므로 여기선 부트스트랩 플래그만.

    --agent 는 복수 허용(append). 단일 `--agent x` → opts.agents = ["x"].
    복수 `--agent claude --agent codex` → ["claude", "codex"].
    미지정 시 [] → auto(감지 전부).
    """
    opts = Options()
    it = iter(argv)
    for a in it:
        if a == "--root":
            opts.root = next(it, None)
        elif a == "--agent":
            val = next(it, None)
            if val:
                opts.agents.append(val)
        elif a == "--member-name":
            opts.member_name = next(it, None)
        elif a == "--role":
            opts.role = next(it, None)
        elif a == "--settings":
            opts.settings = next(it, None)
        elif a == "--yes":
            opts.yes = True
        elif a == "--update":
            opts.update = True
        elif a == "--dry-run":
            opts.dry_run = True
        elif a == "--register-obsidian":
            opts.register_obsidian = True
        elif a == "--obsidian-config":
            opts.obsidian_config = next(it, None)
        elif a == "--team-name":
            opts.team_name = next(it, None)
        elif a == "--role-intent":
            opts.role_intent = next(it, None)
        # 그 외 토큰은 무시
    return opts


# ─────────────────────────── preflight (§4 ①) ───────────────────────────

@dataclass
class PreflightResult:
    ok: bool
    exit_code: int
    message: str = ""
    warnings: list = field(default_factory=list)


def has_team_marker(team_root: Path) -> bool:
    """team_root 가 팀 레포 표식을 갖는지 (§2.2, §10 — 추측 금지)."""
    return any((team_root / m).exists() for m in _TEAM_MARKERS)


def preflight(team_root: Path, python_version, git_present: bool,
              remote_authed: bool) -> PreflightResult:
    """전제 검사 (§4 ①). 값은 전부 주입 — 호스트를 직접 읽지 않는다.

    - Python 하한 미달 / git 바이너리 부재 / 팀 루트 표식 부재 → exit 2 (무변경).
    - 원격 인증만 부재 → 경고(로컬 L1 은 진행, m3·I6b). 종료하지 않는다.
    """
    if tuple(python_version) < tuple(MIN_PYTHON):
        return PreflightResult(
            ok=False, exit_code=2,
            message=f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ 필요 "
                    f"(현재 {python_version[0]}.{python_version[1]}).")
    if not git_present:
        return PreflightResult(
            ok=False, exit_code=2,
            message="git 바이너리가 필요합니다(메모리가 git 기반).")
    if not has_team_marker(team_root):
        return PreflightResult(
            ok=False, exit_code=2,
            message=f"팀 레포 표식(.git/team.config.json/memory)을 {team_root} 에서 "
                    f"찾지 못했습니다. install.py 는 팀 레포 안에서 실행돼야 합니다.")
    warnings = []
    if not remote_authed:
        warnings.append("git 원격 인증이 없습니다 — 로컬 L1 은 진행하나 "
                        "push/pull 시점에 막힙니다(협업 시 인증 필요).")
    return PreflightResult(ok=True, exit_code=0, warnings=warnings)


# ─────────────────────────── role 판정 (§4 ③, M3) ───────────────────────────

def load_config(team_root: Path):
    """team.config.json 을 읽어 dict 반환. 부재/깨짐 → None (크래시 금지)."""
    cfg_path = team_root / "team.config.json"
    if not cfg_path.is_file():
        return None
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def config_is_valid(cfg, *, providers_dir=None) -> bool:
    """role 판정용 필수키(spec_version·team.name) 유효성 (§4 ③, M3).

    ※ **services 스키마/​provider팩 존재에 의존하지 않는다** (적대검수 P1-1).
      provider 팩이 삭제·미동기화돼도 valid 멤버 config 가 introducer 로 강등돼
      덮어쓰기 당하는 데이터손실 경로를 끊는다. role 판정(파괴적 분기)은
      `spec_version` + `team.name` 비-placeholder 만으로 결정한다(원래 M3 의미).
      services 스키마 위반은 services_are_valid 로 설치/검증 시점에 [warn] 으로만
      표면화하며 role 을 뒤집거나 config 를 덮어쓰지 않는다.

    ※ services 채움 여부로도 가르지 않는다 — 빈 슬롯은 정상(스펙02 §9.2).
    team.name 이 placeholder/미초기화 표식이면 유효하지 않음(=도입자).

    providers_dir 인자는 호출부 시그니처 호환을 위해 받되 무시한다(이 함수는
    더 이상 provider 팩에 의존하지 않는다).
    """
    if not isinstance(cfg, dict):
        return False
    if "spec_version" not in cfg or not cfg.get("spec_version"):
        return False
    team = cfg.get("team")
    if not isinstance(team, dict):
        return False
    name = team.get("name")
    if not isinstance(name, str):
        return False
    if name.strip().lower() in _PLACEHOLDER_NAMES:
        return False
    return True


# 정규 역할 어휘 (SPEC §7.1). config services 키는 이 중 하나여야 한다.
_CANONICAL_ROLES = {"issues", "chat", "docs", "calendar"}


def services_are_valid(services, *, providers_dir=None) -> bool:
    """config `services` 블록 스키마 검증 (B-2, 확장 가능 object).

    규칙(빈 슬롯 = 1급 시민, §7.2):
    - None / `{}`(키 생략) → valid (빈 슬롯, 부분채움 허용).
    - 채운 슬롯(역할 키 존재) = object `{provider, scope, <인스턴스 필드…>}`.
      - 역할 키는 정규 어휘(issues/chat/docs/calendar) 여야 함(오타 거부).
      - `provider` 필수 + providers/ 에 해당 팩 존재해야 함(미지 provider 거부).
      - `scope` 있으면 team|personal (없으면 provider팩 default_scope 로 보충 가능 — 여기선 미강제).
      - **provider팩 resource_fields 가 요구하는 인스턴스 값이 전부 채워져야 함**
        (notion 인데 database_id 없음 → invalid). 빈 문자열/None 은 누락으로 본다.
    - **확장 가능**: 선언 안 한 추가 키는 허용(v0.2 무중단).

    호스트 무접촉: providers_dir 미지정 시 레포 기본 providers/. 테스트는 tmp 주입.
    """
    if services is None:
        return True  # 빈 슬롯 — 키 생략
    if not isinstance(services, dict):
        return False
    if not services:
        return True  # 명시적 {} — 전부 빈 슬롯
    for role, slot in services.items():
        if role not in _CANONICAL_ROLES:
            return False  # 비정규 역할 어휘(오타) 거부
        if not isinstance(slot, dict):
            return False
        provider = slot.get("provider")
        if not (isinstance(provider, str) and provider.strip()):
            return False
        pack = _providers.lookup(provider, providers_dir=providers_dir)
        if pack is None:
            return False  # providers/ 에 없는 provider — 추측 금지
        scope = slot.get("scope")
        if scope is not None and scope not in _providers.VALID_SCOPE:
            return False
        # 채운 슬롯이면 provider팩이 요구하는 인스턴스 필드가 전부 채워져야 함.
        for field_name in pack.resource_fields:
            val = slot.get(field_name)
            if not (isinstance(val, str) and val.strip()):
                return False
    return True


# 권장 역할 어휘 (SPEC §1.1·부록 B members.md 역할필드). 권장일 뿐 — config.members
# 의 role 은 자유문자열도 허용한다(확장 가능 object). 검증은 "있으면 형식만"이며
# 어휘 위반으로 거부하지 않는다(v0.2 무중단·팀 자율).
_SUGGESTED_MEMBER_ROLES = {"developer", "pm", "designer", "researcher",
                           "marketer", "ops", "lead"}


def _role_has_control_char(role: str) -> bool:
    """role 문자열에 개행·제어문자가 있으면 True (P2-1 — context 줄 위조 차단).

    role 은 자유문자열(한글·공백·유니코드 어휘 자유)이되, **개행(`\\n` `\\r`)·널·기타
    control char 만** 거부한다. 이런 문자가 verbatim 저장되면 `context` 텍스트 출력의
    멤버 라인을 줄바꿈으로 쪼개 `- FAKE [...] summary: pwned` 같은 가짜 라인을 주입할
    수 있다(실증된 적대 표면). name 의 _validate_author 처럼 어휘를 좁히지는 않는다 —
    오직 라인 구조를 깨는 control char 만 막는다.
    """
    return any(c == "\x7f" or ord(c) < 0x20 for c in role)


def members_are_valid(members) -> bool:
    """config `members` 블록 스키마 검증 (A2.1, 확장 가능 object).

    ⚠️ **config_is_valid(=role 판정, 파괴적 분기)와 완전 분리** — 이 함수는 role 을
       뒤집지 않는다(A의 P0-1/P1-1 교훈: provider팩 누락처럼 멤버를 도입자로 강등시키는
       경로를 원천 차단). members 스키마 위반은 설치/검증 시점 [warn] 발화용일 뿐이다.

    규칙(빈 슬롯 = 1급 시민과 동형):
    - None / members 키 없음(load 시 None) → valid (기존 0.1/0.2 config 무회귀).
    - `[]`(빈 배열) → valid (멤버 0명도 정상).
    - 채운 엔트리 = object `{name 필수, role 선택, <추가 키 허용>}`.
      - `name` 필수 + 엔진 _validate_author 규약 통과(경로 traversal·선두dash footgun 차단).
      - `role` 선택 — 있으면 비어있지 않은 str(권장 어휘 or 자유문자열). 어휘 강제 안 함.
      - **확장 가능**: 선언 안 한 추가 키는 허용(v0.2 무중단).
    """
    if members is None:
        return True  # members 키 없음 — 기존 config 무회귀
    if not isinstance(members, list):
        return False
    if not members:
        return True  # 빈 배열 — 멤버 0명
    for entry in members:
        if not isinstance(entry, dict):
            return False
        name = entry.get("name")
        if not (isinstance(name, str) and name.strip()):
            return False
        if _engine._validate_author(name) is not None:
            return False  # traversal/선두dash 등 — name 은 식별자(footgun 차단)
        role = entry.get("role")
        if role is not None:
            if not (isinstance(role, str) and role.strip()):
                return False  # role 있으면 비어있지 않은 str(자유문자열 허용, 어휘 미강제)
            if _role_has_control_char(role):
                return False  # 개행·제어문자 거부(context 줄 위조 차단, P2-1)
    return True


def upsert_member_role(team_root: Path, name: str, role=None) -> dict:
    """config.members 에 **자기 {name, role} 엔트리만** upsert (A2.2 — 각자 upsert).

    은수 결정(2026-06-16): config "도입자 쓰기·팀원 읽기" 원칙을 **"자기 name 엔트리만
    upsert"로 완화**. 각 멤버가 install 시 자기것만 추가/갱신하고 **타인 name 엔트리는
    절대 안 건드린다**(register_member identity 충돌판정과 정합: 같은 name=자기갱신,
    타인 name=무접촉).

    멱등: 같은 name+role 재실행 시 config 무변경(changed=False).
    안전:
    - name 은 _validate_author 재사용(경로/footgun 차단) — 위반 시 InvalidNameError.
    - config 부재/깨짐이면 무작업(role 판정·도입자 config 작성은 호출부 책임 — 여기선
      members 만 손대며 spec_version/team 등 다른 키는 절대 안 만진다).
    - role=None 이면 role 키 생략(또는 기존 엔트리의 role 제거).

    반환: {"changed": bool}. config 부재 등으로 못 쓰면 changed=False.
    """
    validate_name(name)  # traversal/선두dash 즉시 거부(타 키와 동일 footgun 가드)
    if role is not None and isinstance(role, str) and _role_has_control_char(role):
        # role 개행·제어문자 거부(P2-1) — context 줄 위조를 config 진입에서 차단.
        raise InvalidNameError(f"role 에 개행·제어문자가 포함될 수 없습니다: {role!r}")
    cfg_path = team_root / "team.config.json"
    cfg = load_config(team_root)
    if not isinstance(cfg, dict):
        return {"changed": False}  # config 없음/깨짐 — members 만 다루므로 무작업

    members = cfg.get("members")
    if members is None:
        members = []
    elif not isinstance(members, list):
        # 기존 members 가 list 가 아니면(손상) 자기것만 다루는 계약상 덮어쓰지 않는다.
        return {"changed": False}

    # 자기 엔트리만 찾는다 — 타인 name 엔트리는 인덱스로도 안 건드림.
    own_idx = None
    for i, entry in enumerate(members):
        if isinstance(entry, dict) and entry.get("name") == name:
            own_idx = i
            break

    desired = {"name": name}
    if role is not None and str(role).strip():
        desired["role"] = role

    if own_idx is None:
        # 신규 — 추가(타인 엔트리 순서·내용 무변경, append).
        new_members = list(members) + [desired]
    else:
        existing = members[own_idx]
        # 멱등: 자기 엔트리가 정확히 desired 면(추가 키까지 포함) 무변경.
        # 추가 키 보존: 기존 엔트리의 다른 키는 살리고 name/role 만 갱신한다.
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged["name"] = name
        if role is not None and str(role).strip():
            merged["role"] = role
        else:
            merged.pop("role", None)
        if merged == existing:
            return {"changed": False}  # 멱등 — 같은 name+role 재실행
        new_members = list(members)
        new_members[own_idx] = merged

    cfg["members"] = new_members
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return {"changed": True}


_LOCALE_ENV_ORDER = ("LC_ALL", "LC_MESSAGES", "LANG")


def detect_host_locale() -> str:
    """호스트 UI 언어 감지(stdlib). LC_ALL→LC_MESSAGES→LANG, 'll_CC'만 취함.

    실패/C/POSIX → 'en_US'(국제 공개 기본, 1b 결정 2026-07-01).
    """
    for var in _LOCALE_ENV_ORDER:
        raw = os.environ.get(var)
        if raw:
            base = raw.split(".")[0].split("@")[0].strip()
            if base and base.lower() not in ("c", "posix"):
                return base
    return "en_US"


def detect_host_timezone() -> str:
    """호스트 IANA 타임존 감지(stdlib, best-effort). TZ → /etc/localtime → 'UTC'.

    Windows 등 감지 불가 시 'UTC' 폴백(v1 허용).
    """
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def detect_role(team_root: Path, forced: str | None = None) -> str:
    """'member'/'introducer' 판정 (§4 ③).

    forced 가 유효 역할('introducer'/'member')이면 파일 휴리스틱을 건너뛰고 그대로
    반환한다 — 역할은 CLI 동사(init/join)가 결정한다(1f, footgun 제거). forced 가
    없거나 무효면 기존 휴리스틱(유효 config → member).
    """
    if forced in ("introducer", "member"):
        return forced
    cfg = load_config(team_root)
    return "member" if config_is_valid(cfg) else "introducer"


# ─────────────────────────── detect (§4 ②) ───────────────────────────

# 감지 대상 에이전트: 홈의 점-디렉토리 존재로 판정 (§8 --agent auto).
_AGENT_HOME_DIRS = {"claude": ".claude", "codex": ".codex"}


def detect_agents(home: Path) -> list:
    """홈에 설치된 에이전트를 디렉토리 존재로 감지 (§8). 정렬된 리스트 반환."""
    found = [name for name, d in _AGENT_HOME_DIRS.items()
             if (home / d).is_dir()]
    return sorted(found)


def write_agents_to_config(team_root: Path, agents: list) -> bool:
    """wire한 최종 에이전트 집합을 team.config.json 의 `agents` 필드에 기록.

    멱등: 같은 값이면 무변경(False 반환). config 부재/깨짐이면 무작업(False).
    `agents` 필드는 on/off 가 detect 재감지 없이 읽는 단일 소스가 된다(선택 미전파 버그 해소).
    """
    cfg_path = team_root / "team.config.json"
    cfg = load_config(team_root)
    if not isinstance(cfg, dict):
        return False
    normalized = sorted(agents)
    if cfg.get("agents") == normalized:
        return False
    cfg["agents"] = normalized
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return True


def read_agents_from_config(team_root: Path) -> list | None:
    """team.config.json 의 `agents` 필드를 읽는다.

    반환:
      - list: config 에 `agents` 필드가 있고 유효 리스트면 그 값.
      - None: config 부재·깨짐·필드 없음 → 호출부가 detect_agents fallback 처리.
    비치명: 어떤 예외도 삼킨다(on/off 가 막히면 안 됨).
    """
    try:
        cfg = load_config(team_root)
        if not isinstance(cfg, dict):
            return None
        val = cfg.get("agents")
        if isinstance(val, list):
            return [a for a in val if isinstance(a, str) and a]
        return None
    except Exception:  # noqa: BLE001
        return None


def suggest_member_name(git_user_name) -> str | None:
    """git user.name → members.md 영문 이름 제안(소문자·영숫자만).

    추측이 아니라 *제안*일 뿐 — 최종 결정은 --member-name/대화/--yes 정책(§3·m1).
    공백·특수문자 제거 후 비면 None(신원 추측 금지).
    """
    if not git_user_name:
        return None
    cleaned = re.sub(r"[^a-z0-9]", "", git_user_name.lower())
    return cleaned or None


def repo_name_from_remote(remote_url) -> str | None:
    """git remote URL → repo 명 추출 (도입자 team.name 기본값, §5-1)."""
    if not remote_url:
        return None
    # 끝의 .git 제거 후 마지막 경로 세그먼트
    url = remote_url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    url = url.rstrip("/")
    # scp 형(git@host:org/repo) 과 https 형 모두 마지막 '/' 또는 ':' 뒤
    seg = re.split(r"[/:]", url)
    name = seg[-1] if seg else ""
    return name or None


# ─────────────────────────── scaffold (§4④·§5·§6, M1·M2·M4) ───────────────────────────

class InvalidNameError(ValueError):
    """member 이름이 엔진 _validate_author 규약을 위반(traversal·선두dash 등, m1)."""


class ConflictError(RuntimeError):
    """members.md 이름이 *다른 사람*으로 이미 등재 — 사람이 해소(exit 3, M4)."""


# memory/ 코어 구조 (스펙 01 §2.1). 세션 경로는 엔진 단일소스(teammode.py:191):
# memory/team/sessions/<author>/ — memory/sessions/ 아님(M1).
_MEMORY_DIRS = [
    "memory/team",
    "memory/team/decisions/archive",
    "memory/team/meeting/summary",
    "memory/team/meeting/raw",
]

_INDEX_MD = """\
# 팀 메모리 인덱스 (INDEX.md)

세션 시작 시 주입되는 단일 진입점(스펙 01 §2.1). 새 폴더를 만들면 여기 등재한다(필수).

팀 루트: `$TEAMMODE_HOME` (또는 `teammode.py --root <경로>` 명시). 이 파일은 팀 루트의
`memory/INDEX.md` 에 위치한다.

| 경로 | 여기에 넣는 것 |
|---|---|
| `team/members.md` | 멤버 명부 — 영문 이름(소문자)·역할·연락 정보의 단일 소스 |
| `team/sessions/<이름>/` | 멤버별 세션로그 (`YYYY-MM-DD.md`) |
| `team/decisions/current.md` | 활성 결정사항 |
| `team/decisions/archive/` | 과거 결정 |
| `team/meeting/summary/` | 회의록 요약본 |
| `team/meeting/raw/` | 회의 원본 (STT·텍스트) |
| `team/ground-rules.md` | 팀 운영 방식·작업 리듬·소통 규칙 |
| `team/code-conventions.md` | 코드·커밋·PR 컨벤션 |
| `product/brand/philosophy.md` | 브랜드 철학·핵심 고객·차별화 |
| `product/tech/stack.md` | 기술 스택·아키텍처·제약 |
| `product/tech/features.md` | 피쳐 목록·MVP·로드맵 |
| `product/design/guide.md` | 디자인 가이드·UI 원칙 |
"""

_MEMBERS_HEADER = """\
# 팀 멤버 명부 (members.md)

영문 이름은 소문자·팀 내 고유 — 폴더명·세션로그 frontmatter 가 이 이름을 그대로 쓴다
(스펙 01 §2.1). 코드·훅·스킬은 이름을 하드코딩하지 말고 이 파일을 참조한다(필수).

"""

_MEMBER_LINE_PREFIX = "- "


def validate_name(name: str) -> str:
    """엔진 _validate_author 재사용. 위반 시 InvalidNameError(m1)."""
    err = _engine._validate_author(name)
    if err is not None:
        raise InvalidNameError(err)
    return name


# 멤버 항목에 식별자(git email 등)를 주석으로 붙여 동일인/타인을 결정적으로 가른다.
# `- name  <!-- id: alice@x -->` 형. 식별자 없이 등재된 레거시 항목과 호환(아래 참조).
_ID_RE = re.compile(r"<!--\s*id:\s*(?P<id>.*?)\s*-->")


def _member_entries(members_file: Path) -> dict:
    """members.md → {name: identity_or_None}. `- name [<!-- id: X -->]` 라인 파싱."""
    if not members_file.is_file():
        return {}
    entries = {}
    for line in members_file.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith(_MEMBER_LINE_PREFIX):
            continue
        body = s[len(_MEMBER_LINE_PREFIX):].strip()
        m = _ID_RE.search(body)
        ident = m.group("id") if m else None
        # 이름 = id 주석 앞의 첫 토큰
        name = _ID_RE.sub("", body).split()[0].strip() if _ID_RE.sub("", body).split() else ""
        if name:
            entries[name] = ident or None
    return entries


def _member_names(members_file: Path) -> list:
    """등재된 영문 이름 목록(테스트·하위호환)."""
    return list(_member_entries(members_file).keys())


def register_member(members_file: Path, name: str, identity=None) -> bool:
    """members.md 에 이름 등재 — 결정적 충돌 정책(M4).

    - 이름 검증(엔진 _validate_author 재사용, 위반 시 InvalidNameError).
    - 같은 이름 + 같은(또는 미상) identity → **추가 안 함**(멱등, 본인 항목) → False.
    - 같은 이름 + **다른 identity** → ConflictError(exit 3, I8 — "나인가 남인가" 추측 금지).
      ※ identity 미상(레거시 항목 또는 identity 미주입)이면 충돌로 보지 않는다(멱등).
    - 없으면 추가 → True.
    """
    validate_name(name)
    members_file.parent.mkdir(parents=True, exist_ok=True)
    if not members_file.is_file():
        members_file.write_text(_MEMBERS_HEADER, encoding="utf-8")
    entries = _member_entries(members_file)
    if name in entries:
        existing_id = entries[name]
        # 둘 다 식별자가 있고 서로 다르면 = 다른 사람이 같은 이름 점유(I8).
        if identity and existing_id and identity != existing_id:
            raise ConflictError(
                f"members.md 의 '{name}' 는 다른 식별자({existing_id})로 등재돼 "
                f"있습니다. 당신({identity})과 충돌 — --member-name 으로 다른 이름을 "
                f"쓰거나 사람이 해소하세요.")
        return False  # 멱등 — 동일인 재설치/다른 머신(M4)
    suffix = f"  <!-- id: {identity} -->" if identity else ""
    with members_file.open("a", encoding="utf-8") as f:
        f.write(f"{_MEMBER_LINE_PREFIX}{name}{suffix}\n")
    return True


def _levenshtein(a: str, b: str) -> int:
    """편집거리 (반복형, O(len(a)*len(b)))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def find_similar_names(name: str, existing, *, max_distance: int = 2) -> list:
    """기존 이름 중 새 이름과 혼동될 만큼 비슷한 것들을 반환 (시안 사태 방지).

    휴리스틱(완벽 판정 불가 — 의심 후보를 띄워 사람이 판단):
      - Levenshtein 편집거리 <= max_distance, 또는
      - 공통 프리픽스 길이 >= 짧은쪽 길이의 80%
    동일 이름은 제외(그건 register_member 의 UNIQUE 가 처리).
    junhyun↔junhyung(거리1) 같은 케이스를 잡는다.
    """
    n = name.strip().lower()
    out = []
    for other in existing:
        o = str(other).strip().lower()
        if not o or o == n:
            continue
        if _levenshtein(n, o) <= max_distance:
            out.append(other)
            continue
        plen = 0
        for ca, cb in zip(n, o):
            if ca != cb:
                break
            plen += 1
        if plen >= max(1, int(min(len(n), len(o)) * 0.8)):
            out.append(other)
    return out


def inject_member_env_settings(settings_path: Path, member_name: str) -> bool:
    """Claude Code settings.json 의 env 에 TEAMMODE_MEMBER 를 박는다.

    셸 프로파일 env 주입(TEAMMODE_HOME, §9)과 달리 settings.json env 는
    Claude Code 가 훅·도구 환경에 주입한다 — 가드훅(kb-write-guard)이
    TEAMMODE_MEMBER 로 본인 세션로그를 판정하려면 이 경로라야 닿는다.

    멱등: 같은 값이면 무변경(False), 새로 박거나 바뀌면 True.
    """
    try:
        data = (json.loads(settings_path.read_text(encoding="utf-8"))
                if settings_path.is_file() else {})
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    if env.get("TEAMMODE_MEMBER") == member_name:
        return False
    env["TEAMMODE_MEMBER"] = member_name
    data["env"] = env
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def write_introducer_config(team_root: Path, *, team_name: str,
                            admin_contact: str, timezone=None, locale=None):
    """도입자 최소 config 작성 (§5-1). services = 전부 빈 슬롯(키 생략).

    멱등: 이미 유효한 config 가 있으면 덮어쓰지 않는다(데이터 무접촉).
    """
    cfg_path = team_root / "team.config.json"
    if config_is_valid(load_config(team_root)):
        return  # 이미 유효 — 무수정(멱등, 팀원 경로도 여기로 안 옴)
    cfg = {
        "spec_version": SPEC_VERSION,
        "team": {
            "name": team_name,
            "timezone": timezone or "Asia/Seoul",
            "locale": locale or "ko_KR",
            # 시작 멘트·끝맺음 말 기본값(§4.4·부록 A.3). 엔진 on/off 가 그대로 읽어
            # 출력한다. 온보딩 opt-in 으로 교체 가능(tm-onboard). 팀 이름을 펼쳐 둠.
            "greeting": f"{team_name} 팀모드 ON",
            "farewell": f"수고하셨습니다 — {team_name}",
        },
        "admin_contact": admin_contact,
        "members_file": "memory/team/members.md",
        "banner_file": "memory/banner.txt",
        # services: 전부 빈 슬롯 — 키 자체를 생략(스펙02 §9.2, 빈 슬롯 1급 시민).
        "services": {},
    }
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def write_banner(team_root: Path, team_name: str):
    """banner.txt 선기록 (M4) — 엔진은 파일 있으면 그대로 읽으므로 엔진 무수정.

    기본 배너: infra/banners/ansi_shadow.txt + 팀색 입히기 안내 한 줄.
    팀명 무관 아트라 배너↔config 비동기 해소.
    멱등: 이미 있으면 덮어쓰지 않는다.
    """
    banner_file = team_root / "memory" / "banner.txt"
    if banner_file.is_file():
        return
    banner_file.parent.mkdir(parents=True, exist_ok=True)
    # 단일소스 — _personality_customized 의 '기본 배너' 판정과 어긋나지 않게(#2).
    content = _engine.default_banner_content(team_root, team_name)
    banner_file.write_text(content, encoding="utf-8")


def _write_if_absent(path: Path, content: str):
    """멱등 쓰기 — 파일 없을 때만(재실행 시 사용자 편집 보존, §7)."""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def scaffold_memory(team_root: Path, *, member_name: str, role: str,
                    team_name: str, timezone=None, locale=None,
                    identity=None, member_role=None) -> dict:
    """memory/ 구조·config·members·banner 스캐폴딩 (§4④·§5·§6).

    멱등(§7, I3). 세션 경로는 엔진 단일소스 memory/team/sessions/<author>/ (M1).
    **첫 세션로그는 쓰지 않는다(M2)** — 디렉토리만 만든다.
    이름은 엔진 _validate_author 재사용(m1). 잘못된 이름 → InvalidNameError.
    identity(git email 등) 주입 시 동일이름·타식별자 충돌을 ConflictError 로 검출(I8).
    """
    validate_name(member_name)  # traversal/선두dash 등 즉시 거부

    # 코어 디렉토리
    for d in _MEMORY_DIRS:
        (team_root / d).mkdir(parents=True, exist_ok=True)
    # 세션 디렉토리 (엔진 경로) — 로그 파일은 안 만든다(M2)
    (team_root / "memory" / "team" / "sessions" / member_name).mkdir(
        parents=True, exist_ok=True)

    # INDEX.md (멱등)
    _write_if_absent(team_root / "memory" / "INDEX.md", _INDEX_MD)
    _write_if_absent(team_root / "memory" / "team" / "decisions" / "current.md",
                     "# 활성 결정사항\n")

    # preset scaffold 파일 복사 (infra/scaffolds/memory/ → memory/) — 멱등
    _scaffolds = Path(__file__).resolve().parent / "scaffolds" / "memory"
    if _scaffolds.is_dir():
        for _src in _scaffolds.rglob("*"):
            if _src.is_file():
                _dst = team_root / "memory" / _src.relative_to(_scaffolds)
                _write_if_absent(_dst, _src.read_text(encoding="utf-8"))

    # 도입자만 config 작성 (§5-1). 팀원은 읽기만(§6-1) — 무수정.
    if role == "introducer":
        write_introducer_config(team_root, team_name=team_name,
                                admin_contact=member_name,
                                timezone=timezone, locale=locale)

    # banner 선기록 (M4) — 도입자/팀원 공통(엔진 무수정 우회)
    write_banner(team_root, team_name)

    # members.md 등재 (충돌정책 M4·I8) — identity 로 동일인/타인 결정적 판정
    members_file = team_root / "memory" / "team" / "members.md"
    added = register_member(members_file, member_name, identity=identity)

    # config.members 자기 엔트리만 upsert (A2.2 — 각자 upsert, 은수 결정).
    # 도입자도 자기것 upsert(도입자 config 작성 직후라 유효 config 존재). 팀원은
    # 도입자가 만든 유효 config 에 자기것만 추가 — 타인 엔트리는 무접촉.
    # config 부재/깨짐이면 upsert 가 무작업(role 판정에 영향 0).
    role_upsert = upsert_member_role(team_root, member_name, role=member_role)

    return {"member_added": added, "role_upserted": role_upsert["changed"]}


# ─────────────────────────── wire (§4⑤·§8, M5) ───────────────────────────

# 에이전트별 settings 쓰기 타깃: 플래그명·실호스트 기본 경로가 다르다.
#   claude → --settings ~/.claude/settings.json   (MCP 등록은 별도 ~/.claude.json)
#   codex  → --config   ~/.codex/config.toml       (MCP 등록도 같은 config.toml 블록)
# mcp_flag/mcp_rel:
#   install-mcp 가 MCP 서버를 쓰는 실호스트 파일이 sync 의 settings 와 다른 경우만 별도
#   플래그로 격리 경로를 명시한다(§2.8). claude 는 ~/.claude.json(settings 와 별개 파일)이라
#   격리 모드에서 sync 의 --settings 경로를 암묵 재활용하면 안 되고(D.1 게이트), 전용
#   --mcp-config 격리 경로를 줘야 실 ~/.claude.json 을 안 건드린다(N3). codex 는 MCP 도
#   --config(config.toml) 안 블록이라 mcp_flag 가 없다(sync 와 동일 파일이 정답).
# cfg_flag: 어댑터가 team.config.json(services) 을 읽는 플래그(에이전트마다 이름 다름).
#   claude → --config(team config), codex → --team-config(--config 는 codex 에선 settings).
# install 은 --root 로 팀 루트를 알고 있으므로 어댑터가 자기 __file__ 기준 team_root 를
# 추정하게 두지 않고 명시 전달(install-mcp/sync 가 올바른 services 를 읽게 — D.1).
_AGENT_WIRE = {
    "claude": {"flag": "--settings", "home_rel": ".claude/settings.json",
               "mcp_flag": "--mcp-config", "mcp_rel": ".claude.json",
               "cfg_flag": "--config",
               "skills_flag": "--skills-dir", "skills_rel": ".claude/skills"},
    "codex":  {"flag": "--config",   "home_rel": ".codex/config.toml",
               "mcp_flag": None,      "mcp_rel": None,
               "cfg_flag": "--team-config",
               "skills_flag": "--skills-dir", "skills_rel": ".codex/skills"},
}


def agent_settings_path(agent: str, *, home: Path, settings_override=None) -> Path:
    """에이전트의 settings 쓰기 타깃 결정 (§10 M1).

    settings_override(격리 디렉토리) 지정 시 그 하위 에이전트별 파일(격리 테스트·CI).
    미지정 시 실호스트 기본(home/.claude/settings.json 등).
    """
    spec = _AGENT_WIRE[agent]
    if settings_override is not None:
        # 격리: <override>/<agent>/<basename> — 에이전트별 독립 파일
        base = Path(spec["home_rel"]).name
        return Path(settings_override) / agent / base
    return Path(home) / spec["home_rel"]


def agent_mcp_path(agent: str, *, home: Path, settings_override=None):
    """install-mcp 가 MCP 서버를 등록하는 파일 경로(§2.8) — sync settings 와 별개.

    반환:
      - (mcp_flag, path) 튜플: 에이전트가 별도 MCP 등록 파일을 쓰는 경우(claude).
      - None: sync 의 settings 파일이 곧 MCP 등록 파일인 경우(codex → config.toml 블록).
              이 경우 wire 는 install-mcp 에 추가 경로 인자를 주지 않는다(sync 와 동일 게이트).

    **격리 게이트(D.1)**: settings_override 지정 시 MCP 격리 경로도 그 하위로 명시한다.
    sync 의 --settings 경로를 install-mcp 가 암묵 재활용하지 않게(claude 는 settings.json 과
    ~/.claude.json 이 서로 다른 파일이므로). 미지정(실호스트)이면 None 을 반환해 어댑터가
    자기 기본(~/.claude.json)을 쓰게 둔다 — 실설치는 --yes 동의로 이미 게이트 통과.
    """
    spec = _AGENT_WIRE[agent]
    mcp_flag = spec.get("mcp_flag")
    if mcp_flag is None:
        return None
    if settings_override is not None:
        # 격리: <override>/<agent>/<mcp basename> — sync settings 와 같은 디렉토리지만 다른 파일
        base = Path(spec["mcp_rel"]).name
        return (mcp_flag, Path(settings_override) / agent / base)
    # 실호스트: 경로를 명시하지 않음 → 어댑터 기본(~/.claude.json). --yes 게이트로 진입.
    return None


def agent_skills_path(agent: str, *, home: Path, settings_override=None):
    """install-skills 가 스킬 심링크를 거는 디렉토리(§2.7 L2-C) — 에이전트별로 다름.

    반환:
      - (skills_flag, path) 튜플: wire 가 어댑터에 명시 전달할 격리/실 스킬경로.
      - 실호스트 모드(settings_override 미지정)에서도 wire 는 경로를 **명시 전달**한다
        (home 기준 .claude/skills · .codex/skills). 어댑터 기본(os.path.expanduser)도
        같은 값이지만, wire 가 주입한 home 을 권위로 삼아 테스트 monkeypatch HOME 이
        그대로 반영되게 한다(실 ~/.claude/skills 무접촉 실증 — N3 동형).

    **격리 게이트(D.1)**: settings_override 지정 시 그 하위 <override>/<agent>/skills 로.
    sync 의 settings 경로를 install-skills 가 암묵 재활용하지 않는다(동사별 게이트).
    """
    spec = _AGENT_WIRE[agent]
    flag = spec["skills_flag"]
    if settings_override is not None:
        return (flag, Path(settings_override) / agent / "skills")
    return (flag, Path(home) / spec["skills_rel"])


@dataclass
class WireResult:
    ok: bool
    exit_code: int
    wired: list = field(default_factory=list)        # 성공 에이전트
    failed: list = field(default_factory=list)       # (agent, error) 튜플
    messages: list = field(default_factory=list)


def wire_agents(agents, *, home: Path, settings_override=None,
                run_adapter=None, team_root=None, member_name=None) -> WireResult:
    """감지된 에이전트마다 어댑터 동사 호출(MCP 등록 → 훅 sync) — §4⑤·§8·§2.7·§2.8.

    **다동사(D.1)**: 에이전트마다 **install-mcp → sync(--on) → install-skills 순**으로
    호출한다. install-mcp 가 services 의 연결 provider MCP 서버를 등록(§2.8)하고, 그 뒤
    sync 가 MCP 매처의 별칭을 보장된 것으로 배선하며(install-mcp 미선행이면 sync 가 해당
    매처만 [warn] 생략 — §2.7), install-skills 가 스킬 디렉토리에 심링크(폴백 복사)한다
    (L2-C). install-skills 는 어댑터 동사이고 wire 는 호출만 한다(직접 심링크 금지).

    **동사별 게이트(D.1)**: sync 의 settings 경로(--settings/--config)를 install-mcp 가
    암묵 재활용하지 않는다. claude 처럼 MCP 등록 파일(~/.claude.json)이 settings.json 과
    다른 파일이면 install-mcp 는 **전용 격리 경로(--mcp-config)** 로만 호출한다(미지정 격리
    모드에서 실 ~/.claude.json 무접촉 보장 — N3). codex 는 MCP 도 --config(config.toml)
    안 블록이라 sync 와 같은 게이트(같은 파일)를 공유한다.

    **에이전트별 독립(M5)**: 한 에이전트 실패가 다른 배선을 막지 않는다. 하나라도
    실패 시 exit 3 + 어느 에이전트가 막혔는지. 성공분은 롤백 안 함(멱등 재시도).
    한 에이전트 안에서 install-mcp 가 실패하면 그 에이전트는 실패로 집계하고 sync 를
    건너뛴다(그 에이전트만 — 다른 에이전트는 계속).

    run_adapter(agent, verb, settings_flag, settings_path, extra_args) -> int 를 주입받아
    실제 어댑터 호출을 추상화(테스트가 호스트·subprocess 를 건드리지 않게). extra_args 는
    동사별 추가 글로벌 플래그(예: install-mcp 의 --mcp-config <격리경로>) 리스트.

    member_name: Codex 어댑터에 `--member` 로 전달해 hook command 에 TEAMMODE_MEMBER 를
    prefix 로 박게 한다(멀티멤버 식별 — issue #26). Claude 는 install 이 settings.json env
    (inject_member_env_settings)로 따로 주입하므로 여기서 넘기지 않는다(중복 방지). None 이면
    어느 어댑터에도 넘기지 않는다(하위호환).
    """
    if run_adapter is None:
        raise ValueError("run_adapter 콜러블이 필요합니다(부작용 추상화).")
    # 명시 team_root 가 빈 문자열/공백이면 조용히 "." 로 변질되지 않도록 거부.
    if team_root is not None and str(team_root).strip() == "":
        raise ValueError(
            "wire_agents: team_root 에 빈 문자열/공백을 지정할 수 없습니다. "
            "생략하거나 유효한 절대경로를 전달하세요."
        )
    res = WireResult(ok=True, exit_code=0)
    for agent in agents:
        if agent not in _AGENT_WIRE:
            res.failed.append((agent, "지원하지 않는 에이전트"))
            res.ok = False
            continue
        spec = _AGENT_WIRE[agent]
        path = str(agent_settings_path(agent, home=home,
                                       settings_override=settings_override))
        # 팀 config 경로 + team-root 명시 전달(어댑터가 __file__ 기준 추정하지 않게 — D.1, S0).
        cfg_extra = []
        if team_root is not None:
            cfg_extra = [
                "--team-root", str(Path(team_root)),
                spec["cfg_flag"], str(Path(team_root) / "team.config.json"),
            ]
        # codex 만 멤버 env 를 hook command prefix 로 받는다(issue #26). claude 는 install 이
        # settings.json env 로 따로 주입하므로 여기 미전달(중복 방지). 글로벌 플래그라 동사
        # (install-mcp/sync/install-skills) 무관하게 안전(어댑터가 저장만, build_command 만 사용).
        if agent == "codex" and member_name:
            cfg_extra = cfg_extra + ["--member", str(member_name)]
        # install-mcp 동사의 전용 게이트 경로(claude→--mcp-config 격리, codex→없음).
        mcp = agent_mcp_path(agent, home=home, settings_override=settings_override)
        mcp_extra = cfg_extra + ([mcp[0], str(mcp[1])] if mcp is not None else [])
        try:
            # ① install-mcp (§2.8) — services 연결 provider MCP 등록(빈 슬롯이면 [info]만).
            rc_mcp = run_adapter(agent, "install-mcp", spec["flag"], path, mcp_extra)
            if rc_mcp != 0:
                res.ok = False
                res.failed.append((agent, f"install-mcp rc={rc_mcp}"))
                res.messages.append(
                    f"[wire] {agent} install-mcp 실패(rc={rc_mcp}) — sync 생략, 다른 배선은 계속")
                continue
            res.messages.append(f"[wire] {agent} MCP 등록 동기화 완료")
            # ② sync --on (§2.7) — 훅 등록. MCP 매처는 install-mcp 선행 상태로 별칭 보장.
            #    sync 도 빈 슬롯 우선 규칙(§2.9)·install-mcp 선행 판정에 services 가 필요 →
            #    같은 팀 config 경로 전달. 단 MCP 별칭 보장 판정은 sync 가 ~/.claude.json(claude)
            #    을 본다 — 여기선 격리 mcp_extra 를 다시 줘야 install-mcp 가 쓴 격리 파일을 읽는다.
            rc = run_adapter(agent, "sync", spec["flag"], path, mcp_extra)
            if rc != 0:
                res.ok = False
                res.failed.append((agent, f"sync rc={rc}"))
                res.messages.append(f"[wire] {agent} 실패(rc={rc}) — 다른 배선은 계속")
                continue
            res.messages.append(f"[wire] {agent} 훅 동기화 완료 → {path}")
            # ③ install-skills (§2.7 L2-C) — 스킬 디렉토리에 심링크(폴백 복사). 동사별
            #    게이트(--skills-dir <격리/실 경로>)를 cfg_extra 와 함께 전달 — sync 의 settings
            #    경로 암묵 재활용 금지. 각 어댑터가 자기 스킬경로(claude=~/.claude/skills,
            #    codex=~/.codex/skills)에 직접 심링크하고, wire 는 호출만 한다(install-mcp 동형).
            skills = agent_skills_path(agent, home=home,
                                       settings_override=settings_override)
            skills_extra = cfg_extra + [skills[0], str(skills[1])]
            rc_sk = run_adapter(agent, "install-skills", spec["flag"], path, skills_extra)
            if rc_sk == 0:
                res.wired.append(agent)
                res.messages.append(f"[wire] {agent} 스킬 심링크 완료 → {skills[1]}")
            else:
                res.ok = False
                res.failed.append((agent, f"install-skills rc={rc_sk}"))
                res.messages.append(
                    f"[wire] {agent} install-skills 실패(rc={rc_sk}) — 다른 배선은 계속")
        except Exception as e:  # noqa: BLE001 — 한 에이전트 실패가 전체를 막지 않게(M5)
            res.ok = False
            res.failed.append((agent, str(e)))
            res.messages.append(f"[wire] {agent} 예외: {e} — 다른 배선은 계속")
    if not res.ok:
        res.exit_code = 3  # 부분 실패 → exit 3(어느 에이전트가 막혔는지 호출부가 출력)
    return res


# ─────────────────────────── env 주입 (§9, m2) ───────────────────────────

# 변수명은 스펙01 §1.2 reference 값 TEAMMODE_HOME (런타임 훅 코드와 일치, m2).
# ⚠️ 의도적 호출(install/on/off)은 env 를 신뢰하지 않는다(§10). env 는 런타임 훅 전용.
ENV_VAR = "TEAMMODE_HOME"

# 셸별 프로파일·export 문법. fish 는 set -gx, posix 계열은 export.
_SHELL_PROFILES = {
    "bash": (".bashrc", 'export {var}="{val}"'),
    "zsh": (".zshrc", 'export {var}="{val}"'),
    "fish": (".config/fish/config.fish", 'set -gx {var} "{val}"'),
}

# 멱등 주입을 식별하는 마커(라인 끝 주석). 재실행 시 이 마커로 중복 판정.
_ENV_MARKER = "# teammode (env injection, §9)"


# ─────────────────────── 플랫폼 감지 (W-A, 값 주입) ───────────────────────

def is_windows(platform: str | None = None) -> bool:
    """현재(또는 주입된) 플랫폼이 Windows 인가.

    platform 미지정 시 sys.platform 사용. 값 주입 가능 — 테스트가 nt 분기를 모킹.
    win32/cygwin(둘 다 Windows OS 위) → True. linux/darwin → False.
    """
    p = platform if platform is not None else sys.platform
    return p.startswith("win") or p.startswith("cygwin")


# Windows 영구 user env 는 셸 프로파일이 아니라 레지스트리(HKCU\Environment)에 산다.
# 설치: `setx TEAMMODE_HOME "<abs>"` (새 프로세스부터 반영). 제거: reg delete.
# ⚠️ 실 setx/reg 는 파이(Linux)에 없고 호스트 오염 위험 → runner 주입으로 모킹 테스트.
def _default_runner(argv, **kwargs):
    """subprocess.run 기본 러너 — 테스트는 runner 를 주입해 실행을 대체한다."""
    return subprocess.run(argv, **kwargs)


def inject_env_windows(team_root: Path, *, runner=None) -> dict:
    """Windows: `setx TEAMMODE_HOME "<절대경로>"` 로 영구 user env 주입 (§9).

    - setx 는 HKCU\\Environment 에 영구 기록(새 프로세스부터 반영). 셸 프로파일 무관.
    - team_root 는 절대경로로 정규화(레지스트리 값은 절대라야 의미).
    - 비치명: rc!=0 이거나 setx 부재(raise)면 injected False + reason. raise 안 함.

    ⚠️ runner 주입 = 실 setx 실행 안 함(모킹). 합격은 명령·인자 정확성으로 판정.
    """
    run = runner if runner is not None else _default_runner
    abs_root = str(Path(team_root).resolve())
    try:
        res = run(["setx", ENV_VAR, abs_root],
                  capture_output=True, text=True,
                  encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 — setx 부재 등은 비치명(L1 핵심은 메모리+훅)
        return {"injected": False, "reason": f"setx 실행 실패: {e}",
                "profile": None}
    if getattr(res, "returncode", 1) != 0:
        return {"injected": False,
                "reason": f"setx 비정상 종료(rc={getattr(res, 'returncode', '?')})",
                "profile": None}
    return {"injected": True, "reason": "setx 영구 user env 주입(HKCU\\Environment)",
            "profile": f"HKCU\\Environment\\{ENV_VAR}"}


def remove_injected_env_windows(*, runner=None) -> bool:
    """Windows: `reg delete HKCU\\Environment /v TEAMMODE_HOME /f` 로 제거 (역함수).

    - 멱등·비치명: 변수가 이미 없으면 reg delete rc!=0 → False(raise 안 함).
    - reg 부재(raise) 도 흡수 → False.
    반환: 실제로 제거했으면 True, 아니면 False.
    """
    run = runner if runner is not None else _default_runner
    try:
        res = run(["reg", "delete", "HKCU\\Environment",
                   "/v", ENV_VAR, "/f"],
                  capture_output=True, text=True,
                  encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — reg 부재 등 비치명(이미 없는 것 제거는 무동작)
        return False
    return getattr(res, "returncode", 1) == 0


def detect_shell(shell_path) -> str | None:
    """$SHELL 경로 → 셸 종류(bash/zsh/fish). 미지/미지원 → None.

    값 주입(shell_path) — 호스트 env 를 직접 읽지 않는다.
    """
    if not shell_path:
        return None
    name = Path(shell_path).name
    for shell in _SHELL_PROFILES:
        if shell in name:
            return shell
    return None


def profile_path_for(shell: str, home: Path) -> Path | None:
    """셸 → 프로파일 파일 경로(home 기준). 미지원 셸 → None."""
    spec = _SHELL_PROFILES.get(shell)
    if spec is None:
        return None
    return Path(home) / spec[0]


def _env_line(shell: str, team_root: Path) -> str:
    tmpl = _SHELL_PROFILES[shell][1]
    return f"{tmpl.format(var=ENV_VAR, val=str(team_root))}  {_ENV_MARKER}"


def inject_env(shell: str, home: Path, team_root: Path,
               *, platform: str | None = None, runner=None) -> dict:
    """TEAMMODE_HOME 영구 env 주입 (§9, m2) — 플랫폼별 분기.

    - Windows(is_windows): `setx TEAMMODE_HOME "<abs>"` (HKCU\\Environment). 셸 프로파일 무관.
    - POSIX: 셸 프로파일에 멱등 1줄. 같은 마커 라인 있으면 추가 안 함, 팀루트 바뀌면
      그 라인만 교체(중복 금지). 미지원 셸 → {'injected': False, 'reason': ...}.

    platform/runner 는 값 주입(테스트가 nt 분기·subprocess 를 모킹). 미지정 시 sys.platform.

    ⚠️ 테스트는 monkeypatch HOME=tmp + fake 프로파일(POSIX) / runner 주입(Windows) 으로만(B1).
    실 프로파일·실 setx 무접촉.
    """
    if is_windows(platform):
        return inject_env_windows(team_root, runner=runner)
    profile = profile_path_for(shell, home)
    if profile is None:
        return {"injected": False, "reason": f"미지원 셸: {shell}",
                "profile": None}
    new_line = _env_line(shell, team_root)
    profile.parent.mkdir(parents=True, exist_ok=True)
    existing = profile.read_text(encoding="utf-8") if profile.is_file() else ""
    lines = existing.splitlines()

    # 기존 teammode 마커 라인 탐색
    marker_idx = [i for i, ln in enumerate(lines) if _ENV_MARKER in ln]
    if marker_idx:
        # 이미 주입돼 있음 — 동일하면 멱등(무변경), 다르면 그 라인만 교체(중복 금지).
        idx = marker_idx[0]
        # 마커 라인이 2개 이상이면 첫 줄만 남기고 정리(과거 버그 방어).
        if lines[idx] == new_line and len(marker_idx) == 1:
            return {"injected": False, "reason": "이미 최신(멱등)",
                    "profile": str(profile)}
        # 모든 마커 라인 제거 후 새 라인 1개만
        lines = [ln for i, ln in enumerate(lines) if i not in marker_idx]
        lines.append(new_line)
        profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"injected": True, "reason": "갱신(팀루트 변경)",
                "profile": str(profile)}

    # 신규 주입 — 끝에 1줄 append(기존 내용 보존)
    body = existing if existing.endswith("\n") or existing == "" else existing + "\n"
    profile.write_text(body + new_line + "\n", encoding="utf-8")
    return {"injected": True, "reason": "신규 주입", "profile": str(profile)}


# ─────────────────────────── Obsidian 볼트 등록 (spec/05, opt-in) ───────────────────────────
#
# 스펙 05: Obsidian 뷰 = 키0(메모리 그대로 열기). 자동등록은 opt-in·merge·host-write.
# 호스트 안전(P1 교훈): 중앙 설정 경로는 *주입 가능*(테스트). id/ts 도 주입(결정적).
# Date.now/random 을 코드가 직접 호출하지 않는다 — 오케스트레이터가 주입.
# 어떤 경우도 raise 로 install 흐름을 막지 않는다(비치명). merge 절대 clobber 금지.

# .obsidian/ core-plugins — ⚠️ obsidian 은 이 배열에 *없는* 코어 플러그인을 전부 끈다.
# file-explorer(좌측 파일 목록)가 빠지면 볼트를 열어도 파일 패널이 안 떠 "파일이 안 보인다"
# (E2E 도그푸딩으로 발견). 그래서 file-explorer + 기본 UX(검색·빠른전환·명령 팔레트·아웃라인)를
# 반드시 포함한다. 없어도 빈 .obsidian/ 만으로 볼트 인식되므로 쓰기 실패는 비치명.
_OBSIDIAN_CORE_PLUGINS = [
    "file-explorer", "global-search", "switcher", "command-palette",
    "outline", "graph", "backlink",
]
_OBSIDIAN_COMMUNITY_PLUGINS = ["dataview"]


def obsidian_config_path(platform: str, *, home: Path, appdata=None) -> Path:
    """Obsidian 중앙 설정 obsidian.json 경로를 플랫폼별로 해석 (주입 가능, P1).

    - linux : <home>/.config/obsidian/obsidian.json
    - mac   : <home>/Library/Application Support/obsidian/obsidian.json
    - win   : <appdata>/obsidian/obsidian.json  (appdata 미지정 시 <home>/AppData/Roaming)

    platform 은 sys.platform 류 문자열(linux/linux2/darwin/win32). 값 주입 — ambient 무신뢰.
    """
    home = Path(home)
    if platform.startswith("darwin"):
        return home / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if platform.startswith("win"):
        base = Path(appdata) if appdata is not None else home / "AppData" / "Roaming"
        return base / "obsidian" / "obsidian.json"
    # linux 및 그 외 posix 기본
    return home / ".config" / "obsidian" / "obsidian.json"


def ensure_obsidian_vault(memory_dir: Path) -> bool:
    """memory/ 를 Obsidian 볼트화 — .obsidian/ 없으면 생성. 생성했으면 True(멱등).

    최소 구성(core/community plugins)을 동봉하나 쓰기 실패는 비치명(빈 .obsidian/ 라도 OK).
    """
    memory_dir = Path(memory_dir)
    dot = memory_dir / ".obsidian"
    if dot.is_dir():
        return False
    dot.mkdir(parents=True, exist_ok=True)
    # 최소 구성 동봉(선택) — 실패해도 무시(빈 .obsidian/ 만으로 볼트 인식).
    try:
        (dot / "core-plugins.json").write_text(
            json.dumps(_OBSIDIAN_CORE_PLUGINS, indent=2) + "\n", encoding="utf-8")
        (dot / "community-plugins.json").write_text(
            json.dumps(_OBSIDIAN_COMMUNITY_PLUGINS, indent=2) + "\n",
            encoding="utf-8")
    except OSError:
        pass
    return True


def _atomic_write_text(path: Path, content: str) -> None:
    """temp 파일에 쓰고 os.replace 로 원자 교체 — 쓰기 중 실패해도 원본 무손상.

    - 같은 디렉토리에 tmp 생성(→ os.replace 가 같은 파일시스템 내 원자 rename 보장).
    - flush + fsync 후 os.replace 로 커밋. 실패 시 tmp 정리(원본은 절대 truncate 안 됨).
    - **심링크 보존**: path 가 심링크면 실타깃에 replace 해 링크 자체를 유지(Case E).
      tmp 는 실타깃 디렉토리에 만든다(replace 가 cross-device 가 되지 않게).
    """
    # 심링크면 실타깃을 대상으로 — replace 는 링크를 끊고 실타깃을 새 파일로 갈음하므로
    # 실타깃에 직접 replace 해야 링크가 유지된다(Case E: 링크 따라 실타깃 merge·링크 유지).
    target = Path(os.path.realpath(path)) if path.is_symlink() else path
    dir_ = target.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".obsidian-", suffix=".tmp", dir=str(dir_))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(target))
    except Exception:
        # 커밋 실패 — tmp 정리 후 재전파(원본은 손대지 않았으므로 무손상).
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def register_obsidian_vault(memory_dir: Path, *, config_path: Path,
                            vault_id: str, ts: int) -> dict:
    """obsidian.json 에 memory/ 볼트를 merge 등록 (opt-in·host-write, spec/05).

    호스트 안전·비치명 보장:
    - config_path 부모 디렉토리 부재 = Obsidian 미설치 → skip(생성 안 함, 무raise).
    - 기존 obsidian.json 읽고 → vaults merge → 쓰기. **기존 vaults 전부 보존(clobber 0)**.
    - 같은 path 이미 등록돼 있으면 skip(멱등) — 신규 항목 추가 안 함.
    - 깨진 obsidian.json 등 어떤 오류도 raise 하지 않고 registered=False 반환(비치명).

    항목 = {"<16hex id>": {"path": <memory 절대경로>, "ts": <epoch ms>, "open": false}}.
    id/ts 는 주입(결정적 테스트·Date.now/random 직접 호출 금지).
    """
    memory_dir = Path(memory_dir)
    config_path = Path(config_path)
    try:
        # 미설치 판정: obsidian.json 부모 디렉토리 부재 → skip(생성 안 함).
        if not config_path.parent.is_dir():
            return {"registered": False,
                    "reason": "Obsidian 미설치(설정 디렉토리 부재) — skip"}

        # 볼트화(.obsidian/) — 부수효과지만 등록의 일부.
        ensure_obsidian_vault(memory_dir)

        target_path = str(memory_dir.resolve())

        # 기존 obsidian.json 읽기(부재 → 빈 dict 로 시작, clobber 아님).
        data = {}
        if config_path.is_file():
            try:
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # 깨진 설정 — 우리가 덮어쓰면 사용자 데이터 손실 위험. 비치명 skip.
                return {"registered": False,
                        "reason": "obsidian.json 파싱 실패 — 안전을 위해 skip"}
            # 유효 JSON 이나 object 가 아님(최상위 배열 등) → broken 과 동일 skip.
            # 폐기·덮어쓰기(clobber) 금지 — data-loss 비대칭 제거.
            if not isinstance(loaded, dict):
                return {"registered": False,
                        "reason": "obsidian.json 이 object 가 아님 — 안전을 위해 skip"}
            data = loaded

        vaults = data.get("vaults", {})
        # vaults 가 dict 가 아니면(예: list) 사용자 데이터일 수 있음 → broken 과 동일 skip.
        if not isinstance(vaults, dict):
            return {"registered": False,
                    "reason": "obsidian.json 의 vaults 가 dict 가 아님 — 안전을 위해 skip"}

        # 멱등: 같은 path 이미 등록 → skip(중복 0, clobber 0).
        for v in vaults.values():
            if isinstance(v, dict) and v.get("path") == target_path:
                return {"registered": False, "reason": "이미 등록됨(멱등) — skip"}

        # clobber 방어: 주입된 vault_id 가 *다른 path* 의 기존 항목과 충돌하면
        # 그 볼트를 덮어쓰지 않는다(랜덤 16hex 라 실질 0확률이지만 방어). skip.
        if vault_id in vaults:
            return {"registered": False,
                    "reason": "vault_id 충돌(기존 다른 볼트) — clobber 방지 skip"}

        # 신규 항목 merge(기존 전부 보존).
        vaults[vault_id] = {"path": target_path, "ts": ts, "open": False}
        data["vaults"] = vaults

        _atomic_write_text(
            config_path,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return {"registered": True, "reason": "등록 완료(merge)",
                "vault_id": vault_id, "path": target_path}
    except Exception as e:  # noqa: BLE001 — 어떤 경우도 install 흐름 안 막음(비치명)
        return {"registered": False, "reason": f"비치명 오류 — skip: {e}"}


# ─────────────────────────── 호스트 되돌리기 (install.py --uninstall, 신규) ───────────────────────────
#
# install 이 호스트에 더한 흔적을 **우리 표식만** 골라 안전하게 제거하는 역함수들.
# 호스트 철칙: 남의 줄·남의 볼트·최상위 키는 절대 건드리지 않는다. 전부 멱등·비치명
# (이미 없는 것 제거 시도 OK, raise 금지).
#
# 설계 대칭:
#   - remove_injected_env       ↔  inject_env             : 셸 프로파일에서 우리 표식 줄만 제거
#   - unregister_obsidian_vault ↔  register_obsidian_vault: 해당 path 볼트만 삭제(merge-safe)
#
# ⚠️ inject_env 는 마커 라인 끝에 _ENV_MARKER("# teammode (env injection, §9)") 를 붙인다.
# 역함수는 그 마커의 안정 접두부(_ENV_MARKER_PREFIX)로 우리 줄을 식별한다 — 주석 위치·
# 앞부분 export 내용·미래의 마커 꼬리 변경에 무관하게 우리 표식 줄만 골라낸다.
_ENV_MARKER_PREFIX = "# teammode (env injection"


def remove_injected_env(profile_path, *, platform: str | None = None,
                        runner=None) -> bool:
    """teammode 가 주입한 env 제거 — inject_env 의 역함수. 플랫폼별 분기.

    - Windows(is_windows): `reg delete HKCU\\Environment /v TEAMMODE_HOME /f`.
      profile_path 는 무시(레지스트리 기반). 변수 없으면 무동작.
    - POSIX: 셸 프로파일에서 우리 마커(_ENV_MARKER_PREFIX) 든 줄만 삭제.
      남의 export/alias 무접촉. 마커/파일 없으면 무동작(raise 금지).
    - 반환: 실제로 변경했으면 True, 아니면 False.

    platform/runner 는 값 주입(테스트가 nt·subprocess 모킹). 미지정 시 sys.platform.

    ⚠️ 테스트는 fake 프로파일(POSIX) / runner 주입(Windows) 으로만(B1) — 실 호스트 무접촉.
    """
    if is_windows(platform):
        return remove_injected_env_windows(runner=runner)
    p = Path(profile_path)
    try:
        if not p.is_file():
            return False
        original = p.read_text(encoding="utf-8")
    except OSError:
        return False

    lines = original.splitlines(keepends=True)
    kept = [ln for ln in lines if _ENV_MARKER_PREFIX not in ln]
    if len(kept) == len(lines):
        return False  # 우리 줄 없음 — 무동작(남의 줄 무접촉)

    try:
        p.write_text("".join(kept), encoding="utf-8")
    except OSError:
        return False
    return True


def unregister_obsidian_vault(config_path, vault_path) -> bool:
    """obsidian.json 에서 이 팀 볼트 항목만 제거 — register_obsidian_vault 의 역함수.

    merge-safe: 해당 path 와 일치하는 볼트 항목만 삭제하고, 다른 볼트·최상위 키는
    전부 보존한다. 미설치(파일 없음)·깨짐·해당 볼트 없음 → 무동작(raise 금지).
    반환: 실제로 변경했으면 True, 아니면 False.
    """
    cfg_path = Path(config_path)
    try:
        if not cfg_path.is_file():
            return False
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False  # 미설치/깨짐 — 안전을 위해 skip(register 와 동일 정신)
    if not isinstance(data, dict):
        return False

    vaults = data.get("vaults")
    if not isinstance(vaults, dict):
        return False

    # path 정규화 비교 — 같은 볼트의 표기 차이(끝 슬래시 등) 흡수.
    target = str(Path(vault_path))
    to_remove = [
        vid for vid, v in vaults.items()
        if isinstance(v, dict) and v.get("path") is not None
        and str(Path(str(v["path"]))) == target
    ]
    if not to_remove:
        return False  # 해당 볼트 없음 — 무동작(다른 볼트 보존)

    for vid in to_remove:
        del vaults[vid]

    try:
        cfg_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError:
        return False
    return True
