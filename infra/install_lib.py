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
from dataclasses import dataclass, field
from pathlib import Path

# Python 버전 하한 (§12-1 미결 — 타깃 머신 분포 근거 나오면 확정).
# 보수적으로 3.9 로 둔다(현행 런타임 훅·엔진이 3.9+ 문법 사용).
MIN_PYTHON = (3, 9)

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
