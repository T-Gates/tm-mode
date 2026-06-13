#!/usr/bin/env python3
"""install.py 부트스트랩 코어 — 순수/주입 가능 함수 (spec/04).

설계 원칙(§1.2):
- **결정적**: 같은 입력 → 같은 결과. LLM 즉흥 판단 0.
- **env 불신뢰(§10, P1)**: 팀 루트·HOME·git 값은 전부 **명시 인자로 주입**받는다.
  ambient `TEAMMODE_HOME`/`LEGACY_TOOL_HOME` 을 코드가 신뢰하지 않는다(사고 근본 처방).
- 부작용(파일 쓰기·subprocess)은 install.py 오케스트레이터가, 판정·계산은 여기서.

여기 함수들은 환경을 직접 읽지 않고 주입받으므로 단위 테스트가 호스트를 건드리지 않는다.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 엔진 _validate_author 재사용 (m1) — 이름 검증 단일 소스(드리프트 방지).
_INFRA = Path(__file__).resolve().parent
if str(_INFRA) not in sys.path:
    sys.path.insert(0, str(_INFRA))
import teammode as _engine  # noqa: E402

# Python 버전 하한 (§12-1 미결 — 타깃 머신 분포 근거 나오면 확정).
# 보수적으로 3.9 로 둔다(현행 런타임 훅·엔진이 3.9+ 문법 사용).
MIN_PYTHON = (3, 9)

# 팀 데이터가 따르는 스펙 묶음 버전 (스펙 01 §6, 01~03 공통 0.1).
SPEC_VERSION = "0.1"

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
    agent: str = "auto"
    member_name: str | None = None
    settings: str | None = None
    yes: bool = False
    update: bool = False
    dry_run: bool = False


_VALUE_FLAGS = {"--root", "--agent", "--member-name", "--settings"}


def parse_args(argv) -> Options:
    """argv → Options. 손파싱(엔진과 동일 스타일) — 동사별 정책 메시지 일관성.

    알 수 없는 플래그는 무시(후속 슬라이스 확장 여지). --<agent> 디스패치 흡수는
    install.py 오케스트레이터가 parse_args 전에 분기하므로 여기선 부트스트랩 플래그만.
    """
    opts = Options()
    it = iter(argv)
    for a in it:
        if a == "--root":
            opts.root = next(it, None)
        elif a == "--agent":
            opts.agent = next(it, None) or "auto"
        elif a == "--member-name":
            opts.member_name = next(it, None)
        elif a == "--settings":
            opts.settings = next(it, None)
        elif a == "--yes":
            opts.yes = True
        elif a == "--update":
            opts.update = True
        elif a == "--dry-run":
            opts.dry_run = True
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


def config_is_valid(cfg) -> bool:
    """필수키(spec_version·team.name) 유효성 (§4 ③, M3).

    ※ services 채움 여부로 가르지 않는다 — 빈 슬롯은 정상(스펙02 §9.2).
    team.name 이 placeholder/미초기화 표식이면 유효하지 않음(=도입자).
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


def detect_role(team_root: Path) -> str:
    """'member'(config 유효) 또는 'introducer'(부재·미초기화) (§4 ③)."""
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

| 경로 | 여기에 넣는 것 |
|---|---|
| `team/members.md` | 멤버 명부 — 영문 이름(소문자)·역할·연락 정보의 단일 소스 |
| `team/sessions/<이름>/` | 멤버별 세션로그 (`YYYY-MM-DD.md`) |
| `team/decisions/current.md` | 활성 결정사항 |
| `team/decisions/archive/` | 과거 결정 |
| `team/meeting/summary/` | 회의록 요약본 |
| `team/meeting/raw/` | 회의 원본 (STT·텍스트) |
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

    멱등: 이미 있으면 덮어쓰지 않는다.
    """
    banner_file = team_root / "memory" / "banner.txt"
    if banner_file.is_file():
        return
    banner_file.parent.mkdir(parents=True, exist_ok=True)
    banner_file.write_text(f"=== {team_name} team mode ON ===\n",
                           encoding="utf-8")


def _write_if_absent(path: Path, content: str):
    """멱등 쓰기 — 파일 없을 때만(재실행 시 사용자 편집 보존, §7)."""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def scaffold_memory(team_root: Path, *, member_name: str, role: str,
                    team_name: str, timezone=None, locale=None,
                    identity=None) -> dict:
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

    return {"member_added": added}
