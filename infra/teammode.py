#!/usr/bin/env python3
"""teammode 엔진 CLI — 슬라이스 2 수직 슬라이스 (on/off 만 실배선).

골든 시나리오(conformance/scenarios)의 인수 테스트를 GREEN으로 만들어가는 엔진.
슬라이스 2에서는 on/off → Claude 어댑터 sync 배선 + 배너·상태 마커까지만 구현한다.
context/issue/log 동사는 후속 슬라이스 (현재는 미구현 → 해당 시나리오 RED 유지).

  teammode.py on  --root <팀루트> [--settings <경로>|--install]   팀 모드 켜기
  teammode.py off --root <팀루트> [--settings <경로>|--install]   팀 모드 끄기

팀 루트는 **명시 인자 `--root`로만** 받는다. 환경변수(TEAMMODE_HOME 등)는 절대 읽지
않는다 — ambient env 신뢰가 호스트 오염 사고의 근본 원인이었기 때문이다(P1, BUILD-LOG).
`--root` 미지정 시 즉시 에러로 종료한다(정책 A): 엔진이 어느 폴더를 건드릴지 추측하지
않게 하는 것이 사고의 근본 처방이다.

settings 경로도 명시로만 받는다(P2): `--settings <경로>`(격리 모드) 또는 실설치를
뜻하는 `--install`(→ ~/.claude/settings.json) 중 하나가 **필수**다. 둘 다 없으면 실
`~/.claude`를 추측 오염하지 않도록 거부한다.
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

INFRA = Path(__file__).resolve().parent       # 설치 위치 (manifest·adapter 소재)

# workday(06시컷) 순수 함수 — 같은 디렉토리 형제 모듈. drift 방지로 컷 계산을 단일 소스화.
sys.path.insert(0, str(INFRA))
import workday as _workday  # noqa: E402
# git 작업 공통 모듈 — pull/commit 동사가 auto_pull 과 같은 안전장치를 재사용(V.3).
import git_ops as _git_ops  # noqa: E402
# provider 팩 lookup — issue 동사가 issues 슬롯 연결을 확인할 때 사용(추측 금지·미지 reject).
import providers as _providers  # noqa: E402
# stdout/stderr UTF-8 보장 — Windows native 인코딩(cp949 등)에서 한글 print 크래시 방지.
from io_encoding import ensure_utf8_io  # noqa: E402


def _active_marker(team_root: Path) -> Path:
    return team_root / ".teammode-active"


def _banner_file(team_root: Path) -> Path:
    return team_root / "memory" / "banner.txt"


def render_name_banner(team_root: Path, text: str, font: str = "ansi_shadow") -> str | None:
    """팀명 text 를 글리프 타일로 stitch 해 ASCII 아트로 렌더한다(의존성 0).

    빌드타임에 뽑아둔 infra/banners/glyphs/<font>.json 의 글자별 글리프(고정 height)를
    가로로 이어붙인다. ansi_shadow 는 smushing 이 없어 per-char stitch = native figlet(실측).
    text 에 글리프 테이블에 없는 글자(비-ASCII 등)가 하나라도 있으면 None → 호출부 폴백.
    """
    text = text.strip()
    if not text:
        return None
    glyph_path = team_root / "infra" / "banners" / "glyphs" / f"{font}.json"
    try:
        data = json.loads(glyph_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    glyphs = data.get("glyphs") if isinstance(data, dict) else None
    height = data.get("height") if isinstance(data, dict) else None
    if not isinstance(glyphs, dict) or not isinstance(height, int) or height <= 0:
        return None
    cols = []
    for ch in text:
        g = glyphs.get(ch)
        if not isinstance(g, list) or len(g) != height:
            return None  # 미지원 글자 → 폴백
        cols.append(g)
    return "\n".join("".join(col[r] for col in cols) for r in range(height))


def default_banner_content(team_root: Path, team_name: str) -> str:
    """기본 배너 content — install_lib.write_banner 와 _personality_customized 공용 단일소스.

    우선순위: ① 팀명 글리프 아트(render_name_banner) → ② 고정 ansi_shadow.txt 아트
    → ③ 팀명 plain fallback. 팀명이 ASCII면 ①로 '팀명 그 자체'가 배너가 된다.
    이 함수가 단일소스라 '기본 배너인가' 판정과 '기본 배너 기록'이 어긋나지 않는다(#2).
    """
    art = render_name_banner(team_root, team_name)
    if art is not None:
        return art + "\n💡 팀색 입히기: tm-customize\n"
    ansi_shadow = team_root / "infra" / "banners" / "ansi_shadow.txt"
    if ansi_shadow.is_file():
        art = ansi_shadow.read_text(encoding="utf-8").rstrip("\n")
        return art + "\n💡 팀색 입히기: tm-customize\n"
    return f"=== {team_name} ===\n"


def _adapter_for(agent_name, settings_path=None, skills_dir=None, member=None):
    """에이전트별 어댑터 팩토리.

    agent_name: 'claude' 또는 'codex' (agents/ 하위 디렉토리명).
    settings_path: None이면 에이전트 기본 경로 파생.
      - claude → ~/.claude/settings.json
      - codex  → install_lib.agent_settings_path("codex", home) 활용(~/.codex/config.toml)
    skills_dir: None이면 settings_path 부모/skills 파생(P0-1).
    member: codex hook command 에 TEAMMODE_MEMBER prefix 로 박을 멤버명(issue #26).
      **codex 어댑터만** 받는다 — claude Adapter 는 member kwarg 가 없고(settings.json env
      경로로 따로 주입), 넘기면 TypeError. None 이면 미전달. codex 어댑터는 member 가 None
      이어도 sync 시 기존 config.toml prefix 를 self-healing 으로 보존한다.
    ⚠️ codex에 claude settings 경로를 넘기면 사고 — 각 에이전트는 자기 기본 경로 파생.
    """
    import runpy
    mod = runpy.run_path(str(INFRA / "agents" / agent_name / "adapter.py"),
                         run_name="__teammode_engine__")
    Adapter = mod["Adapter"]

    if settings_path is not None:
        resolved_settings = settings_path
    elif agent_name == "claude":
        resolved_settings = os.path.expanduser("~/.claude/settings.json")
    else:
        # codex 등 다른 에이전트: install_lib.agent_settings_path 활용
        try:
            import install_lib as _il
            resolved_settings = str(
                _il.agent_settings_path(agent_name, home=Path.home()))
        except Exception:
            # fallback: ~/.codex/config.toml
            resolved_settings = os.path.expanduser(f"~/.{agent_name}/config.toml")

    # skills_dir 격리 파생(P0-1):
    #   명시 주입이 없으면 settings_path 의 부모 디렉토리 아래 "skills" 를 사용한다.
    #   규칙: <settings_path 부모>/skills
    #   예) --settings /tmp/x/settings.json → skills_dir=/tmp/x/skills  (격리 자동)
    #       실설치(~/.claude/settings.json) → skills_dir=~/.claude/skills (실호스트)
    #   이 파생으로 `--settings <tmp>` 만 줘도 실호스트 ~/.claude/skills 무접촉.
    if skills_dir is None:
        skills_dir = str(Path(resolved_settings).parent / "skills")

    adapter_kwargs = dict(
        agent_dir=str(INFRA / "agents" / agent_name),
        manifest_path=str(INFRA / "hooks" / "manifest.json"),
        settings_path=resolved_settings,
        # 어댑터의 team_root = 설치 위치(normalize.py 소유 마커 기준). 메모리 쓰기의
        # 팀 루트(_team_root, cwd)와는 별개 축이다.
        team_root=str(INFRA.parent),
        skills_dir=skills_dir,
    )
    # member 는 codex 어댑터만 받는다(claude Adapter 는 member kwarg 미지원 — TypeError 방지).
    # codex 에 전달하면 sync(mode=on/off) 가 hook command 에 TEAMMODE_MEMBER prefix 를
    # 재렌더해 `tm on/off` resync 회귀를 막는다(issue #26).
    if agent_name == "codex" and member is not None:
        adapter_kwargs["member"] = member
    return Adapter(**adapter_kwargs)


def _adapter(settings_path=None, skills_dir=None):
    """하위호환 래퍼 — claude 어댑터 단일 반환. 기존 테스트·호출 코드 무회귀."""
    return _adapter_for("claude", settings_path, skills_dir)


def _render_banner(team_root: Path) -> str:
    """배너 캐시를 읽거나, 없으면 팀 이름 기반 최소 배너를 생성·캐시한다(§11.5)."""
    banner_file = _banner_file(team_root)
    if banner_file.is_file():
        return banner_file.read_text(encoding="utf-8")
    team_name = os.environ.get("TEAMMODE_TEAM_NAME", "teammode")
    banner = f"=== {team_name} ===\n"
    banner_file.parent.mkdir(parents=True, exist_ok=True)
    banner_file.write_text(banner, encoding="utf-8")
    return banner


def _read_team_field(team_root: Path, field: str) -> str | None:
    """team.config.json 의 team.<field> 문자열을 읽는다(시작멘트/끝맺음말 — §3.1·§4.4).

    config 읽기는 비치명: 부재·파싱실패·타입불일치면 None(on/off 를 막지 않는다).
    어떤 예외도 삼킨다 — personality 출력이 핵심 경로(배너·sync·마커)를 막아선 안 됨.
    """
    try:
        cfg_path = team_root / "team.config.json"
        if not cfg_path.is_file():
            return None
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return None
        team = cfg.get("team")
        if not isinstance(team, dict):
            return None
        value = team.get(field)
        return value if isinstance(value, str) and value else None
    except Exception:  # noqa: BLE001 — config 읽기는 on/off 를 절대 막지 않는다
        return None


def _migrate_legacy_credentials(team_root: Path) -> None:
    """단일 금고 전환(2026-06-21) 전에 팀명-키(`<team.name>.json`)로 저장한 L2 토큰을
    단일 금고(default.json)로 1회 이전한다. 비치명 — 실패해도 on 을 막지 않는다.

    멀티팀 미지원 결정으로 금고를 단일 파일로 바꿨다. 그 전에 L2 를 연결한 팀의 토큰이
    옛 파일명에 고아로 남는 걸 막는다. default.json 이 이미 있으면 no-op(멱등).
    """
    try:
        team_name = _read_team_field(team_root, "name")
        if team_name:
            import credentials  # infra/ 가 sys.path[0] (스크립트 디렉토리)
            credentials.migrate_legacy_vault(team_name)
    except Exception:  # noqa: BLE001 — 토큰 이전 실패가 on 을 막아선 안 됨
        pass


def _personality_customized(team_root: Path) -> bool:
    """팀 personality 가 기본값과 다른지 결정적으로 판정한다.

    판정 기준 (OR 조건):
    1. memory/banner.txt 내용이 기본 배너(default_banner_content)와 다르다 (배너 커스텀됨).
       ⚠️ '존재'가 아니라 '내용 비교' — install 이 fresh 팀에도 기본 banner.txt 를 깔기
       때문에, 존재만으로 판정하면 미커스텀 팀이 전부 커스텀으로 오판된다(#2).
    2. team.config.json 의 greeting 이 기본 공식 `f"{name} 팀모드 ON"` 과 다르다.
    3. team.config.json 의 farewell 이 기본 공식 `f"수고하셨습니다 — {name}"` 과 다르다.

    config 부재·파싱 실패 → False (기본값으로 간주). 판정은 비치명 — 어떤 예외도 False.
    기본 배너·greeting/farewell 공식은 install_lib.write_banner / default_banner_content 와 동기화.
    """
    try:
        # config 먼저 — name 은 배너 fallback 비교·greeting/farewell 공식에 공용으로 쓰인다.
        cfg_path = team_root / "team.config.json"
        cfg = (json.loads(cfg_path.read_text(encoding="utf-8"))
               if cfg_path.is_file() else {})
        if not isinstance(cfg, dict):
            cfg = {}
        team = cfg.get("team")
        if not isinstance(team, dict):
            team = {}
        name = team.get("name")
        name = name if isinstance(name, str) and name else None

        # 1. banner: 내용이 기본과 다르면 커스텀 (존재만으로는 판정 안 함)
        banner_file = _banner_file(team_root)
        if banner_file.is_file():
            actual = banner_file.read_text(encoding="utf-8")
            if actual != default_banner_content(team_root, name or ""):
                return True

        # 2,3. greeting/farewell — name 있을 때만 기본 공식과 비교
        if name:
            greeting = team.get("greeting")
            farewell = team.get("farewell")
            if isinstance(greeting, str) and greeting != f"{name} 팀모드 ON":
                return True
            if isinstance(farewell, str) and farewell != f"수고하셨습니다 — {name}":
                return True
        return False
    except Exception:  # noqa: BLE001 — personality 판정은 context 동사를 막지 않는다
        return False


UPSTREAM_REMOTE = "upstream"
UPSTREAM_REF = "upstream/main"


def _read_local_notice(team_root: Path) -> str:
    """로컬 NOTICE.md 내용을 읽는다(없으면 빈 문자열). 예외 전파 없음."""
    notice_path = team_root / "NOTICE.md"
    try:
        if notice_path.is_file():
            return notice_path.read_text(encoding="utf-8")
    except OSError:
        pass
    return ""



def auto_update_on_start(team_root: Path) -> None:
    """tm ON 시 upstream 엔진 자동 동기화 + 자동 커밋 (작업 D).

    설계(docs/archive/2026-06-18-night-skill-layers.md §작업 D):
      - fetch 실패·remote 없음·offline → **0으로 흡수**(on 막지 않음, 조용히 skip).
        기존 cmd_update 는 fetch 실패에 return 1 로 on 을 죽이므로 재사용 금지.
      - dirty(대상 경로 미커밋 변경) → 적용·커밋 둘 다 skip + 알림만(사람 판단 요청).
      - changed=True 일 때만 do_commit(paths=res.paths) — paths 한정(add -A/paths=None 금지).
      - do_commit 실패(ok=False) → on 계속 성공, staged 잔존 경고 1줄.
      - 멱등: changed=False 면 do_commit 호출 안 함.
      - push=True(6/23 자동push 철학: 푸시는 사람결정 폐기). **push 실패는 비차단** —
        do_commit 이 push 실패해도 커밋을 보존(ok=True·pushed=False)하고 on 은 계속 성공.
      - 적용되면 "엔진 업데이트됨: <NOTICE 첫 불릿>" 한 줄 출력.

    모든 예외를 삼킨다 — on 의 핵심 경로를 자동 update 가 막아선 안 된다.
    """
    try:
        res = _git_ops.sync_from_upstream(str(team_root), remote=UPSTREAM_REMOTE)

        if res.blocked:
            # dirty 가드: 적용·커밋 둘 다 skip + 사람 알림
            print(f"[auto-update] 대상 경로에 커밋 안 된 변경이 있어 자동 업데이트 skip — "
                  f"검토 후 커밋하거나 되돌리면 다음 on 에서 자동 적용됩니다.")
            return

        if not res.ok:
            # fetch 실패·remote 없음·offline — 조용히 skip(on 막지 않음)
            return

        if not res.changed:
            # 멱등: 변경 없음 — do_commit 호출 안 함
            return

        # 변경 있음: paths 한정 자동 커밋 + 자동 push(6/23 철학). push 실패는 비차단.
        commit_res = _git_ops.do_commit(
            str(team_root),
            message="chore: sync teammode engine from upstream [auto]",
            push=True,
            paths=list(res.paths),
        )

        if not commit_res.ok:
            # 커밋 실패(충돌·권한 등) → on 계속 성공, staged 잔존 경고
            print(f"[auto-update] 자동 커밋 실패(staged 잔존) — "
                  f"검토 후 직접 커밋하세요: {commit_res.detail}")
        else:
            # 적용 성공 — NOTICE 첫 불릿 표시(은수 원래 의도: 켤 때 소식 보여주기)
            local_notice = _read_local_notice(team_root)
            first_bullet = ""
            for line in local_notice.splitlines():
                line = line.strip()
                if line.startswith("- ") or line.startswith("* "):
                    first_bullet = line[2:].strip()
                    break
            summary = first_bullet[:80] if first_bullet else ""
            if summary:
                print(f"엔진 업데이트됨: {summary}")
            else:
                print("엔진 업데이트됨")

    except Exception:  # noqa: BLE001 — 자동 update 는 on 을 절대 막지 않는다
        pass


def cmd_on(team_root: Path, settings_path: str, member: str | None = None,
           skills_dir: str | None = None, install: bool = False) -> int:
    """팀 모드를 켠다.

    install=True (--install 플래그)일 때만 detect_agents loop 로 전 에이전트 배선.
    install=False (--settings <격리경로> 모드)면 claude 만 배선 — 실호스트
    ~/.codex 등 무접촉. 경로 비교는 보조 진단으로만 사용하고 정책 판정에 쓰지 않는다.
    """
    # 배너는 엔진이 stdout 에 찍지 않는다(toolkit 패턴) — 에이전트가 memory/banner.txt 를
    # Read 해 코드펜스(```)로 감싸 웰컴 메시지의 첫 번째 요소로 출력한다.
    # 단, 에이전트가 Read 할 수 있도록 banner.txt 캐시는 보장한다(없으면 fallback 생성).
    _render_banner(team_root)  # side-effect: banner.txt 보장 (반환값 미사용 — stdout 출력 안 함)
    # 시작 멘트(greeting): config 에 있으면 출력(없으면 미출력 — §3.1).
    greeting = _read_team_field(team_root, "greeting")
    if greeting:
        print(greeting)
    # 단일 금고 전환(2026-06-21) 전에 팀명-키로 저장한 L2 토큰을 default.json 으로 1회 이전.
    _migrate_legacy_credentials(team_root)
    # D: upstream 자동 동기화(fetch + 변경 시 자동 커밋). 실패는 on 을 막지 않는다.
    # 순서: auto_update 먼저 → 그 다음 심링크 토글(새 core 스킬 반영 위해).
    auto_update_on_start(team_root)
    # 멀티에이전트 detect loop:
    #   install=True 시 config.agents(install이 기록한 선택 집합) 우선,
    #     없으면 detect_agents fallback(기존 레포 회귀 0). 감지/config 모두 없으면 claude 기본.
    #   install=False (격리/--settings 모드) 시 claude 만 배선 — 실호스트 ~/.codex 등 무접촉.
    #   ⚠️ settings_path/skills_dir(격리 테스트 인자)은 claude에만 적용 —
    #   다른 에이전트는 None(자기 기본 경로 파생). codex에 claude 경로 주입 사고 방지.
    #   정책 판정은 install 플래그로만. 경로 비교(_is_isolated)는 보조 진단 목적.
    _real_claude_settings = os.path.expanduser("~/.claude/settings.json")
    _is_isolated_diag = (settings_path is not None and
                         os.path.abspath(settings_path) != os.path.abspath(_real_claude_settings))
    if install:
        try:
            import install_lib as _il
            # config.agents 에 선택 집합이 기록돼 있으면 그걸 쓴다(install 미전파 버그 해소).
            # 없으면(기존 레포 또는 config 미기록) detect fallback — 회귀 0.
            _from_config = _il.read_agents_from_config(team_root)
            if _from_config is not None:
                _detected = _from_config
            else:
                _detected = _il.detect_agents(Path.home())
        except Exception:
            _detected = []
        _agents_to_wire = _detected or ["claude"]
    else:
        # --settings 격리 모드: claude 만 배선 (실호스트 무접촉)
        _agents_to_wire = ["claude"]
    _all_adapters: list = []  # 생성된 어댑터 전부 보관 — util replay 를 전부에 적용
    _failed_agents: list = []  # 실패 에이전트 수집 (지적3: 부분 실패 처리)
    for _ag in _agents_to_wire:
        try:
            if _ag == "claude":
                _ag_adapter = _adapter_for("claude", settings_path, skills_dir)
            else:
                # 자기 기본 경로 파생 + member 전파(codex hook prefix 유지 — issue #26).
                _ag_adapter = _adapter_for(_ag, member=member)
            _ag_adapter.sync(mode="on")
            _ag_adapter.install_skills(layer="core")
            _all_adapters.append(_ag_adapter)
        except Exception as _exc:  # noqa: BLE001 — 한 에이전트 실패가 나머지를 막지 않는다
            _failed_agents.append((_ag, str(_exc)))
    # 마커 정책: 최소 하나 성공 시 생성. 전부 실패하면 마커 미생성(엔진 off 상태 유지).
    # 근거: 부분 ON 이라도 팀 기능 활성화는 가능. 전부 실패면 ON 의미 없음.
    _primary_adapter = _all_adapters[0] if _all_adapters else None
    if _primary_adapter is not None:
        _active_marker(team_root).write_text("", encoding="utf-8")
    # 실패 에이전트 경고 출력
    for _ag_name, _ag_err in _failed_agents:
        print(f"[warn] {_ag_name} 에이전트 배선 실패 → skip: {_ag_err}")
    # 대표 어댑터가 없는 경우(전부 실패) 방어 — util replay 건너뜀
    if _primary_adapter is None:
        return 1
    # 멤버별 util 스킬 설치 (--member 지정 시): 감지된 에이전트 전부에 적용(지적2).
    if member is not None:
        util_skills = _read_util_skills(team_root, member)
        for skill_name in util_skills:
            # P0-2: traversal 가드 — util-skills.json 의 installed 문자열을 검증 없이
            # src/target 에 쓰면 "../foo" 같은 값이 skills dir 밖으로 탈출한다.
            # add 경로(_validate_author)와 동일 규칙으로 on 읽기 경로도 필터.
            err = _validate_author(skill_name)
            if err is not None:
                print(f"[warn] util 스킬 '{skill_name}' 무효(traversal 위험) → skip: {err}")
                continue
            # infra/skills/util/<name> 실재 확인 (on 읽기 경로도 이중 방어)
            src = team_root / "infra" / "skills" / "util" / skill_name
            if not src.is_dir() or not (src / "SKILL.md").is_file():
                print(f"[warn] util 스킬 '{skill_name}' 소스 없음 → skip")
                continue
            # 지적2: 모든 어댑터에 util 심링크 적용 (기존: 대표 어댑터만)
            # util link 실패가 cmd_on 전체를 crash시키지 않도록 어댑터별 try/except.
            for _adp in _all_adapters:
                try:
                    target = _adp.skills_dir / skill_name
                    _adp._link_one_skill(src, target, layer="util")
                except Exception as _util_exc:  # noqa: BLE001
                    print(f"[warn] util 스킬 '{skill_name}' 링크 실패({_adp.skills_dir}) → skip: {_util_exc}")
    return 0


def cmd_update(team_root: Path, dry_run: bool = False) -> int:
    """upstream(템플릿)의 엔진 파일을 **파일 동기화**로 적용 — 슬라이스 T2.

    왜 merge 가 아닌가: 도입 레포는 GitHub *template* 으로 생성돼 upstream 과 공통
    조상이 0(unrelated histories)이라 `git merge`/`pull --ff-only` 가 영원히
    `refusing to merge unrelated histories` 로 막힌다. → merge 를 버리고 upstream 의
    **엔진 경로(SYNC_PATHS=infra/)만** `git checkout` 으로 덮어쓴다. 히스토리 관계와
    무관하게 동작한다. ⚠️ memory/·team.config.json·팀 소유 파일은 동기화 대상이 아니다.

    동작(git_ops.sync_from_upstream 에 위임 — 모든 git 안전장치 재사용):
      - upstream remote 미등록 → 안내 후 중단(exit 1, install 이 등록함·수동 등록법).
      - 변경 없음 → "이미 최신" 후 exit 0(멱등).
      - dirty 가드: 대상 경로에 커밋 안 된 로컬 변경이 있으면 덮어쓰기로 유실되므로
        경고 후 중단(exit 1, 사람 판단 요청).
      - --dry-run: 변경 미리보기만 출력, 실제 변경 0(exit 0).
      - 적용 시 working tree 덮어쓰기(staged). **자동 commit·push 절대 안 함** —
        무엇이 바뀌었는지 사람이 검토 후 직접 커밋한다.
    """
    res = _git_ops.sync_from_upstream(
        str(team_root), remote=UPSTREAM_REMOTE, dry_run=dry_run)
    paths = ", ".join(res.paths) if res.paths else "infra"

    # dirty 가드 — 사람 판단 요청(추측 수리 금지)
    if res.blocked:
        print(f"tm-mode update — 중단: {res.detail}.\n"
              f"  동기화 대상({paths})에 커밋 안 된 변경이 있습니다. 덮어쓰면 유실됩니다.\n"
              f"  먼저 변경을 커밋하거나 되돌린 뒤 다시 실행하세요(사람 판단 필요).",
              file=sys.stderr)
        if res.diff:
            print(res.diff, file=sys.stderr)
        return 1

    if not res.ok:
        # upstream 미등록·오프라인·git 아님 등 — 비치명. install 이 upstream 을 등록한다.
        print(f"tm-mode update — 건너뜀(비치명): {res.detail}.\n"
              f"  upstream remote 가 없으면 install.py 가 등록합니다. 수동 등록:\n"
              f"  git remote add {UPSTREAM_REMOTE} {_install_upstream_url()}",
              file=sys.stderr)
        return 1

    # dry-run: sync 가 changed=False(적용 스킵) + diff(있으면 채움)로 돌려주므로
    # changed 가 아니라 **diff 유무**로 분기한다. (changed 로 분기하면 변경이 있어도
    # "이미 최신"으로 잘못 출력되는 P2 버그가 난다 — 적대검수 발견.)
    if dry_run:
        if res.diff:
            print(f"tm-mode update [dry-run] — 동기화하면 바뀔 파일({paths}):")
            print(res.diff)
            print("  (미리보기만 — 실제 변경 없음. 적용하려면 --dry-run 빼고 다시 실행.)")
        else:
            print("tm-mode update [dry-run] — 이미 최신입니다(변경 없음).")
        return 0

    if not res.changed:
        print("tm-mode update — 이미 최신입니다.")
        return 0

    # 적용됨(staged) — 무엇이 바뀌었나 사람이 읽는 요약. push·commit 안 함.
    print(f"tm-mode update — 엔진 파일 동기화 완료({paths}, staged). 바뀐 파일:")
    print(res.diff)
    print("  변경은 스테이지됨(자동 커밋·push 안 함). 검토 후 직접 커밋하세요:\n"
          "  git commit -m 'chore: sync teammode engine from upstream'")
    return 0


def _install_upstream_url() -> str:
    """install.py 의 UPSTREAM_URL 을 읽어 수동 등록 안내에 쓴다(상수 단일 소스).

    실패(import 불가 등)는 비치명 — 일반 안내 URL 폴백.
    """
    try:
        import install as _install  # noqa: PLC0415 — 안내용 lazy import
        return _install.UPSTREAM_URL
    except Exception:  # noqa: BLE001
        return "https://github.com/T-Gates/tm-mode.git"


def _uninstall_layer(adapter, layer: str) -> None:
    """Remove all skills installed from a given layer."""
    if not adapter.skills_dir.is_dir():
        return
    for child in sorted(adapter.skills_dir.iterdir()):
        if adapter._is_layer_skill(child, layer):
            adapter._remove_skill(child)


def _util_skills_path(team_root: Path, member: str) -> Path:
    """Path to member's util-skills.json."""
    return team_root / "memory" / "team" / "sessions" / member / "util-skills.json"


def _read_util_skills(team_root: Path, member: str) -> list:
    """Read util-skills.json for a member. Returns list of skill names. Errors → []."""
    path = _util_skills_path(team_root, member)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                installed = data.get("installed", [])
                if isinstance(installed, list):
                    return [s for s in installed if isinstance(s, str)]
    except (ValueError, OSError):
        pass
    return []


def _write_util_skills(team_root: Path, member: str, skills: list) -> None:
    """Atomically write util-skills.json for a member."""
    import tempfile
    path = _util_skills_path(team_root, member)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"installed": skills}, ensure_ascii=False, indent=2) + "\n"
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(tmp_fd, data.encode("utf-8"))
        os.close(tmp_fd)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            os.close(tmp_fd)
        except Exception:
            pass
        try:
            Path(tmp_name).unlink()
        except Exception:
            pass
        raise


def cmd_off(team_root: Path, settings_path: str, member: str | None = None,
            skills_dir: str | None = None, install: bool = False) -> int:
    """팀 모드를 끈다.

    install=True (--install 플래그)일 때만 detect_agents loop 로 전 에이전트 해제.
    install=False (--settings 격리 모드)면 claude 만 해제 — 실호스트 무접촉.
    정책 판정은 install 플래그로만. 경로 비교는 보조 진단 목적.
    부분 실패(지적3): 에이전트별 작업을 try/except 로 감싸 전부 시도. 실패 [warn] 보고.
    """
    # 정책: install 플래그 기준으로 배선 대상 결정.
    # config.agents(install이 기록한 선택 집합) 우선, 없으면 detect fallback(회귀 0).
    if install:
        try:
            import install_lib as _il
            _from_config = _il.read_agents_from_config(team_root)
            if _from_config is not None:
                _detected = _from_config
            else:
                _detected = _il.detect_agents(Path.home())
        except Exception:
            _detected = []
        _agents_to_wire = _detected or ["claude"]
    else:
        _agents_to_wire = ["claude"]
    _primary_adapter = None
    _failed_agents: list = []
    for _ag in _agents_to_wire:
        try:
            if _ag == "claude":
                _ag_adapter = _adapter_for("claude", settings_path, skills_dir)
            else:
                # member 전파 — off resync 도 codex hook prefix 를 떨구지 않게(issue #26).
                _ag_adapter = _adapter_for(_ag, member=member)
            _ag_adapter.sync(mode="off")
            _uninstall_layer(_ag_adapter, "core")
            _uninstall_layer(_ag_adapter, "util")
            if _primary_adapter is None:
                _primary_adapter = _ag_adapter
        except Exception as _exc:  # noqa: BLE001 — 한 에이전트 실패가 나머지를 막지 않는다
            _failed_agents.append((_ag, str(_exc)))
    # 실패 에이전트 경고 출력
    for _ag_name, _ag_err in _failed_agents:
        print(f"[warn] {_ag_name} 에이전트 해제 실패 → skip: {_ag_err}")
    # 마커 정책(cmd_on 대칭): 최소 하나 성공 시 삭제. 전부 실패면 마커 유지 + rc=1.
    # 근거: 전부 실패면 실제 해제가 이뤄지지 않았으므로 active 상태가 남아 있어야 한다.
    marker = _active_marker(team_root)
    if _primary_adapter is None:
        return 1
    if marker.exists():
        marker.unlink()
    # 배너는 엔진이 stdout 에 찍지 않는다(toolkit 패턴, ON 과 동일) — 에이전트가
    # memory/banner.txt 를 Read 해 farewell 앞에 코드펜스로 출력한다.
    # 끝맺음 말(farewell): config 에 있으면 그걸, 없으면 "상태 저장됨" 폴백(§3.1).
    farewell = _read_team_field(team_root, "farewell")
    print(farewell if farewell else "tm-mode off — 상태 저장됨")
    return 0


def _validate_author(author: str) -> str | None:
    """author 가 안전한 단일 디렉토리 세그먼트인지 검증. 위반 시 에러 메시지 반환.

    적대 표면(경로 traversal·이상 author): members.md 영문 이름은 소문자 단일 세그먼트
    (스펙 01 §2.1). 슬래시·`..`·절대경로·빈 문자열·널은 팀 루트 밖 쓰기로 이어질 수
    있으므로 거부한다. 화이트리스트(영숫자·`-`·`_`)로 좁혀 OS별 특수문자도 차단한다.
    """
    if not author:
        return "author 가 비어 있습니다."
    if "/" in author or "\\" in author:
        return f"author 에 경로 구분자가 포함될 수 없습니다: {author!r}"
    if author in (".", ".."):
        return f"author 로 {author!r} 는 허용되지 않습니다."
    if os.path.isabs(author):
        return f"author 는 절대 경로일 수 없습니다: {author!r}"
    # 선두 '-' 거부: '-rf'·'--root' 같은 이름은 다운스트림 git/rm/glob 에서 플래그로
    # 오인되는 footgun(적대 검수 지적). members.md 영문 이름은 식별자이지 플래그가 아니다.
    if author[0] in "-_":
        return f"author 는 영숫자로 시작해야 합니다: {author!r}"
    # isascii() 강제: 파이썬 isalnum() 은 유니코드라 한글 등 비ASCII 가 통과한다.
    # author·filename(파일명 되는 값)은 ASCII 범위 영문/숫자/제한기호만 허용한다.
    if not author.isascii():
        return f"author 는 ASCII 문자(영문/숫자/허용기호)만 사용할 수 있습니다: {author!r}"
    # 화이트리스트: members.md 영문 이름 규약(소문자 단일 세그먼트)에 부합하는 문자만
    if not all(c.isalnum() or c in "-_" for c in author):
        return f"author 에 허용되지 않는 문자가 있습니다: {author!r}"
    return None


def _frontmatter(author: str, date_str: str, summary: str) -> str:
    """세션로그 frontmatter(필수 3필드: author/date/summary) 생성 (스펙 01 §3.3)."""
    return (f"---\n"
            f"author: {author}\n"
            f"date: {date_str}\n"
            f"summary: {summary}\n"
            f"---\n")


def cmd_log(team_root: Path, author: str, text: str, now: datetime) -> int:
    """세션로그를 작업일(06시컷) 파일에 생성/append 한다 — 기계적 재료손질.

    엔진은 요약하지 않는다(--text 그대로 보존). 하루 1파일 append, frontmatter 자동.
    summary 는 첫 기록의 첫 줄로 초기화(스킬/사람이 갱신; 엔진은 교체 판단 안 함).
    """
    err = _validate_author(author)
    if err is not None:
        print(f"[error] {err}", file=sys.stderr)
        return 2

    print("[deprecated] log 동사 대신 세션로그를 Read(끝 offset)+Edit 로 직접 쓰세요 "
          "(컨텍스트 절약·충실도). 이 동사는 하위호환으로만 유지됩니다.", file=sys.stderr)

    date_str = _workday.workday_str(now)
    sessions_dir = team_root / "memory" / "team" / "sessions" / author
    log_path = sessions_dir / f"{date_str}.md"
    # 방어: 정규화 후 경로가 sessions_dir 밖으로 새지 않는지 재확인(이중 방어).
    resolved = log_path.resolve()
    if not str(resolved).startswith(str(sessions_dir.resolve())):
        print("[error] 로그 경로가 세션 디렉토리를 벗어납니다.", file=sys.stderr)
        return 2

    sessions_dir.mkdir(parents=True, exist_ok=True)
    time_label = now.astimezone(_workday.KST).strftime("%H:%M")
    entry = f"\n## {time_label}\n\n{text}\n"

    if log_path.exists():
        # 하루 1파일: 기존 파일에 이어 쓴다(append) — frontmatter 재작성 금지.
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        # 첫 기록: frontmatter + 첫 항목. summary 는 첫 '의미있는' 줄(100자)로 초기화.
        # 마크다운 헤더(`## 작업 내역` 등)·빈 줄은 건너뛴다 — 헤더가 summary 로 박히면
        # 웰컴·맥락주입에 무의미한 헤더만 보인다(#3). 리스트 마커(`- `)는 본문이라 채택.
        summary = ""
        for _line in text.strip().splitlines():
            _s = _line.strip()
            if not _s or _s.startswith("#"):
                continue
            summary = _s[:100]
            break
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(_frontmatter(author, date_str, summary))
            f.write(entry)

    print(f"tm-mode log — {author}/{date_str}.md 기록됨")
    return 0


def _parse_skill_description(team_root: Path, skill_name: str) -> str:
    """Parse first 'description:' line from SKILL.md frontmatter. Empty if missing."""
    skill_md = team_root / "infra" / "skills" / "util" / skill_name / "SKILL.md"
    try:
        if skill_md.is_file():
            text = skill_md.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("description:"):
                    return stripped[len("description:"):].strip()
    except OSError:
        pass
    return ""


def cmd_util(team_root: Path, action: str | None, member: str | None,
             skill_name: str | None, skills_dir: str | None = None,
             settings_path: str | None = None, install: bool = False) -> int:
    """util 동사 — util 스킬 목록 관리 (list/add/remove).

    엔진 기계역할: json 갱신 + 즉시 심링크 반영(on 상태면). 판단은 스킬(tm-manage-utils) 몫.
    settings_path: 즉시반영 심링크 경로 파생에 쓴다(P0-1). None → 실호스트 폴백.
    install: --install 플래그. True 면 detect_agents loop, False 면 claude 만.
    """
    util_dir = team_root / "infra" / "skills" / "util"

    if action == "list":
        # List available util skills + installed for member
        available = []
        if util_dir.is_dir():
            for d in sorted(util_dir.iterdir()):
                if d.is_dir() and (d / "SKILL.md").is_file():
                    desc = _parse_skill_description(team_root, d.name)
                    available.append({"name": d.name, "description": desc})
        installed = []
        if member is not None:
            err = _validate_author(member)
            if err is not None:
                print(f"[error] {err}", file=sys.stderr)
                return 2
            installed = _read_util_skills(team_root, member)
        result = {"available": available, "installed": installed}
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if action in ("add", "remove"):
        if member is None:
            print(f"[error] util {action}: --member <이름> 가 필요합니다.", file=sys.stderr)
            return 2
        if skill_name is None:
            print(f"[error] util {action}: --skill <스킬명> 가 필요합니다.", file=sys.stderr)
            return 2
        # Validate member and skill name
        err = _validate_author(member)
        if err is not None:
            print(f"[error] --member: {err}", file=sys.stderr)
            return 2
        err = _validate_author(skill_name)
        if err is not None:
            print(f"[error] --skill: {err}", file=sys.stderr)
            return 2

        if action == "add":
            # Check skill exists
            skill_src = util_dir / skill_name
            if not skill_src.is_dir() or not (skill_src / "SKILL.md").is_file():
                print(f"[error] util add: '{skill_name}' 은 존재하지 않는 util 스킬입니다.",
                      file=sys.stderr)
                return 2
            # Source containment guard: resolved 경로가 util_dir 하위여야 한다.
            # 심링크 자체(skill_src)가 util_dir 안에 있으면 충분 — resolved 까지
            # 따라갈 필요는 없다. 단 .. 등 traversal 이 있으면 _validate_author 가
            # 이미 막았으므로 여기서는 정규화된 절대경로 비교만 한다.
            try:
                skill_src.resolve().relative_to(util_dir.resolve())
            except ValueError:
                print(f"[error] util add: '{skill_name}' 소스 경로가 "
                      f"util 디렉터리 밖을 가리킵니다(containment 거부).",
                      file=sys.stderr)
                return 2
            # Update json
            skills = _read_util_skills(team_root, member)
            if skill_name not in skills:
                skills.append(skill_name)
                _write_util_skills(team_root, member, skills)
            # Immediate symlink if on (active marker exists).
            # P0-2: settings_path 가 None(격리 컨텍스트 불명)이면 즉시반영을 skip —
            # 실호스트 ~/.claude/skills 는 절대 건드리지 않는다. json 갱신은 유지되어
            # 다음 `on` 시 자동 반영된다.
            if _active_marker(team_root).exists():
                if settings_path is None and skills_dir is None:
                    print("teammode util add — 즉시반영 skip "
                          "(--settings 또는 --install 필요; 다음 on 에서 반영)",
                          file=sys.stderr)
                else:
                    # 멀티에이전트 loop: install 플래그 기준으로 배선 대상 결정.
                    # ⚠️ settings_path/skills_dir 은 claude에만 적용.
                    # 정책 판정은 install 플래그로만. 경로 비교는 보조 진단 목적.
                    if install:
                        try:
                            import install_lib as _il
                            # config.agents(install 이 기록한 선택 집합) 우선 — cmd_on/off 와 일관.
                            # 없으면(기존 레포·미기록) detect_agents fallback(회귀 0).
                            _from_config = _il.read_agents_from_config(team_root)
                            _util_detected = (_from_config if _from_config is not None
                                              else _il.detect_agents(Path.home()))
                        except Exception:
                            _util_detected = []
                        _util_agents = _util_detected or ["claude"]
                    else:
                        _util_agents = ["claude"]
                    for _uag in _util_agents:
                        if _uag == "claude":
                            the_skills_dir = Path(skills_dir) if skills_dir else None
                            _uadapter = _adapter_for("claude", settings_path,
                                                      the_skills_dir)
                        else:
                            _uadapter = _adapter_for(_uag)
                        target = _uadapter.skills_dir / skill_name
                        _uadapter._link_one_skill(skill_src, target, layer="util")
            print(f"teammode util add — {skill_name} 등록됨 (member: {member})")
            return 0

        if action == "remove":
            skills = _read_util_skills(team_root, member)
            if skill_name in skills:
                skills.remove(skill_name)
                _write_util_skills(team_root, member, skills)
            # Remove symlink if on and exists.
            # P0-2: settings_path 가 None이면 즉시반영 skip — 실호스트 무접촉.
            if _active_marker(team_root).exists():
                if settings_path is None and skills_dir is None:
                    print("teammode util remove — 즉시반영 skip "
                          "(--settings 또는 --install 필요; 다음 on 에서 반영)",
                          file=sys.stderr)
                else:
                    # 멀티에이전트 loop: install 플래그 기준으로 배선 대상 결정.
                    if install:
                        try:
                            import install_lib as _il
                            # config.agents(install 이 기록한 선택 집합) 우선 — cmd_on/off 와 일관.
                            # 없으면(기존 레포·미기록) detect_agents fallback(회귀 0).
                            _from_config = _il.read_agents_from_config(team_root)
                            _util_detected = (_from_config if _from_config is not None
                                              else _il.detect_agents(Path.home()))
                        except Exception:
                            _util_detected = []
                        _util_agents = _util_detected or ["claude"]
                    else:
                        _util_agents = ["claude"]
                    for _uag in _util_agents:
                        if _uag == "claude":
                            the_skills_dir = Path(skills_dir) if skills_dir else None
                            _uadapter = _adapter_for("claude", settings_path,
                                                      the_skills_dir)
                        else:
                            _uadapter = _adapter_for(_uag)
                        target = _uadapter.skills_dir / skill_name
                        if target.exists() or target.is_symlink():
                            if _uadapter._is_layer_skill(target, "util"):
                                _uadapter._remove_skill(target)
            print(f"teammode util remove — {skill_name} 제거됨 (member: {member})")
            return 0

    print(f"[error] util: 알 수 없는 action: {action!r}. list/add/remove 중 하나.",
          file=sys.stderr)
    return 2


# 값을 받는 옵션 플래그 화이트리스트. 여기 없는 `--flag` 는 부울/무시로 다룬다 —
# 알 수 없는 플래그의 다음 토큰을 값으로 삼키지 않게 해 verb 손실을 막는다(§3:366).
# issue 동사의 정규 입력 필드(--title/--body/--assignee/--label/--priority)도 값 플래그.
_VALUE_FLAGS = ("--root", "--settings", "--author", "--text", "--now", "--message",
                "--title", "--body", "--assignee", "--label", "--priority", "--paths",
                "--member", "--skills-dir", "--skill",
                # memory 동사 플래그
                "--folder", "--filename", "--content", "--weight", "--path", "--date",
                # memory route 동사 플래그 (루트 2열 라우팅 맵 설명 칸)
                "--desc")


# ──────────────────────────────────────────────────────────────────
# 작업 C — memory 동사 (기계 전담: frontmatter·파일 쓰기/삭제·INDEX 갱신·커밋)
# ──────────────────────────────────────────────────────────────────

# 메모리 파일이 놓일 수 있는 허용 폴더 목록 (sessions/meeting 은 다른 경로)
_KNOWLEDGE_ALLOWED_FOLDERS = (
    "product",
    "team",
    "team/decisions",
    "soma",
)

# 명시 차단 폴더 — 허용 목록에서 제외되는 경로 (훅 자동·tm-context 관리)
# team/sessions: 세션로그(훅 자동), team/meeting: 회의록(tm-context 관리)
_KNOWLEDGE_BLOCKED_FOLDERS = (
    "team/sessions",
    "team/meeting",
)

# INDEX 행 구분자 — 파이프 표 형식
_INDEX_TABLE_HEADER = "| 가중치 | 경로 | 내용 | 편집일 |"
_INDEX_TABLE_SEP    = "|--------|------|------|--------|"
_INDEX_LEGEND       = "> 가중치: 🔥 핵심 · 📌 중요 · 📎 참고"


# weight 허용값 (3-enum 검증용)
_KNOWLEDGE_VALID_WEIGHTS = ("🔥", "📌", "📎")


def _escape_index_cell(value: str) -> str:
    """INDEX 파이프 표 셀 값 이스케이핑 — |·줄바꿈·백틱 처리."""
    # 줄바꿈 → 공백, 파이프 → ⎪(시각 유사 문자로 대체), 백틱 → 작은따옴표
    return value.replace("\n", " ").replace("\r", " ").replace("|", "⎪").replace("`", "'")


def _validate_filename_chars(filename: str) -> str | None:
    """filename 문자 검증. 위반 시 에러 메시지 반환.

    - 빈 문자열 거부.
    - 경로 구분자(/ \\) 거부.
    - '..' 또는 '.' 로 시작 거부.
    - 공백·파이프·제어문자(ord < 32) 거부.
    - 0x7F(DEL) 거부.
    - ASCII 외(비ASCII) 거부 — write/author 와 동일 정책.
    - kebab-case(영숫자·-·_) 검증(_validate_author 재사용).
    """
    if not filename:
        return "filename 이 비어 있습니다."
    if "/" in filename or "\\" in filename:
        return f"filename 에 경로 구분자가 포함될 수 없습니다: {filename!r}"
    if ".." in filename or filename.startswith("."):
        return f"filename 이 허용되지 않습니다: {filename!r}"
    # 제어문자·공백·파이프·DEL·비ASCII 거부 (write 와 동일 정책)
    for ch in filename:
        cp = ord(ch)
        if cp < 32 or cp == 0x7F or ch in (" ", "|"):
            return f"filename 에 허용되지 않는 문자가 있습니다: {filename!r}"
    if not filename.isascii():
        return f"filename 은 ASCII 문자만 사용할 수 있습니다: {filename!r}"
    # kebab-case 검증 (확장자 제거 후)
    base = filename.removesuffix(".md") if filename.endswith(".md") else filename
    err = _validate_author(base)
    if err is not None:
        return f"filename 검증 실패: {err}"
    return None


def _validate_knowledge_path(team_root: Path, folder: str, filename: str) -> str | None:
    """folder/filename 이 안전한지 검증. 위반 시 에러 메시지 반환.

    - folder: _KNOWLEDGE_ALLOWED_FOLDERS 안의 슬래시 포함 경로 (예: 'team/decisions').
      하위 경로 분리자(/)는 허용, 상위 이탈(..)은 불허.
    - 허용 폴더 목록(_KNOWLEDGE_ALLOWED_FOLDERS) 또는 그 하위 폴더여야 한다.
    - filename: 단순 파일명(슬래시·절대경로 불허). 공백·파이프·제어문자 거부.
    - 정규화 후 team_root/memory/<folder>/<filename> 이 memory/ 하위여야 한다.

    P0: memory/ 자체가 team_root 밖 symlink 여도 탈출 불가하도록
        memory.resolve() 가 team_root.resolve() 하위인지 먼저 검증.
    """
    # ── P0: symlink 탈출 가드 ─────────────────────────────────────
    # memory 디렉토리가 team_root 바깥을 가리키는 symlink 라면 containment 자체가 무력화된다.
    real_root = team_root.resolve()
    memory_dir = (team_root / "memory").resolve()
    try:
        memory_dir.relative_to(real_root)
    except ValueError:
        return (f"memory/ 가 team_root 밖을 가리킵니다(심링크 탈출 차단): "
                f"{memory_dir} not under {real_root}")

    # ── 허용 폴더 검증 (P1-1) ─────────────────────────────────────
    # 정규화된 folder 가 허용 목록의 하나이거나 그 하위여야 한다.
    # 단, 명시 차단 폴더(_KNOWLEDGE_BLOCKED_FOLDERS)는 허용 목록보다 우선 거부.
    norm_folder = folder.replace("\\", "/").rstrip("/")

    # 먼저 명시 차단 목록 검사
    for bf in _KNOWLEDGE_BLOCKED_FOLDERS:
        if norm_folder == bf or norm_folder.startswith(bf + "/"):
            return (f"folder '{folder}' 는 메모리 저장 대상이 아닙니다(훅/tm-context 관리 경로): "
                    f"차단 목록: {', '.join(_KNOWLEDGE_BLOCKED_FOLDERS)}")

    allowed = False
    for af in _KNOWLEDGE_ALLOWED_FOLDERS:
        if norm_folder == af or norm_folder.startswith(af + "/"):
            allowed = True
            break
    if not allowed:
        return (f"folder '{folder}' 는 허용되지 않습니다. "
                f"허용: {', '.join(_KNOWLEDGE_ALLOWED_FOLDERS)} (및 그 하위)")

    # ── folder: .. 세그먼트 금지 ──────────────────────────────────
    # isascii() 강제: isalnum() 은 유니코드라 전각문자(Ａ 등)·한글이 통과한다.
    # author/filename 과 동일 원인이므로 folder 세그먼트에도 isascii 적용(S1 지적 4).
    parts = norm_folder.split("/")
    for seg in parts:
        if seg in ("", ".", ".."):
            return f"folder 에 허용되지 않는 세그먼트: {seg!r} in {folder!r}"
        if not seg.isascii():
            return f"folder 세그먼트는 ASCII 문자만 사용할 수 있습니다: {seg!r}"
        if not all(c.isalnum() or c in "-_" for c in seg):
            return f"folder 세그먼트에 허용되지 않는 문자: {seg!r}"

    # ── filename 검증 (_validate_filename_chars 재사용) ─────────────
    fn_err = _validate_filename_chars(filename)
    if fn_err is not None:
        return fn_err

    # ── containment: 정규화된 절대경로가 memory/ 하위여야 한다 ────
    candidate = (team_root / "memory" / folder / filename).resolve()
    try:
        candidate.relative_to(memory_dir)
    except ValueError:
        return f"경로가 memory/ 를 벗어납니다: {folder}/{filename}"
    return None


def _index_get_edit_date(index_path: Path, rel_path: str) -> str | None:
    """INDEX.md 의 해당 경로 행에서 편집일(마지막 컬럼)을 읽는다. 없으면 None.

    편집일 보존 전략(P1-3): 본문 미변경 재write 시 git log subject 파싱 없이
    기존 INDEX 행에서 편집일을 그대로 가져와 보존한다.
    """
    if not index_path.is_file():
        return None
    path_marker = f"`{rel_path}`"
    try:
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if path_marker in line and line.strip().startswith("|"):
                # 파이프 표 행: | weight | `path` | desc | date |
                cells = [c.strip() for c in line.split("|")]
                # cells[0]='' cells[1]=weight cells[2]=path cells[3]=desc cells[4]=date cells[5]=''
                if len(cells) >= 5:
                    return cells[4] if cells[4] else None
    except OSError:
        pass
    return None


def _knowledge_frontmatter(author: str, weight: str, created_at: str,
                            updated_at: str) -> str:
    """메모리 파일 frontmatter(4필드: created_at/updated_at/author/weight) 생성."""
    return (f"---\n"
            f"created_at: {created_at}\n"
            f"updated_at: {updated_at}\n"
            f"author: {author}\n"
            f"weight: {weight}\n"
            f"---\n")


def _parse_knowledge_frontmatter(text: str) -> dict:
    """메모리 파일 frontmatter 파싱. 없거나 깨지면 빈 dict.

    P2: 알 수 없는 필드도 보존(재작성이 4필드만 덮어쓰지 않도록).
    """
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _rebuild_frontmatter(fm: dict, author: str, weight: str,
                          created_at: str, updated_at: str) -> str:
    """기존 frontmatter dict 를 받아 4필드(created_at/updated_at/author/weight)를
    갱신하되 나머지 알 수 없는 필드는 보존해 재조립한다(P2: 추가필드 보존).

    4필드가 가장 앞에 오고, 그 다음 나머지 필드가 원래 순서대로.
    """
    known = {"created_at", "updated_at", "author", "weight"}
    extra_lines = [
        f"{k}: {v}" for k, v in fm.items() if k not in known
    ]
    lines = [
        "---",
        f"created_at: {created_at}",
        f"updated_at: {updated_at}",
        f"author: {author}",
        f"weight: {weight}",
    ] + extra_lines + ["---", ""]
    return "\n".join(lines)


def _knowledge_body(text: str) -> str:
    """frontmatter 이후 본문 반환. frontmatter 없으면 전체 반환."""
    if not text.startswith("---"):
        return text
    idx = text.find("---", 3)
    if idx == -1:
        return text
    return text[idx + 3:].lstrip("\n")


def _index_upsert(index_path: Path, rel_path: str, weight: str,
                  description: str, edit_date: str) -> bool:
    """INDEX.md 의 파이프 표에 행을 upsert(삽입 또는 갱신). 변경됐으면 True.

    형식:
      > 가중치: 🔥 핵심 · 📌 중요 · 📎 참고

      | 가중치 | 경로 | 내용 | 편집일 |
      |--------|------|------|--------|
      | 🔥 | `product/tech/foo.md` | 설명 | 2026-06-18 |

    - 헤더/범례 없으면 파일 끝에 추가.
    - 같은 경로(rel_path) 행이 있으면 갱신, 없으면 삽입.
    - INDEX.md 자신에는 frontmatter 붙이지 않는다(원칙).
    """
    existing = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""

    # P2: cell 값 이스케이핑 — |·줄바꿈·백틱에 표가 깨지지 않도록
    safe_weight = _escape_index_cell(weight)
    safe_desc = _escape_index_cell(description)
    safe_date = _escape_index_cell(edit_date)
    new_row = f"| {safe_weight} | `{rel_path}` | {safe_desc} | {safe_date} |"

    # 기존 행 검색 (rel_path 로 식별)
    path_marker = f"`{rel_path}`"
    lines = existing.splitlines(keepends=True)
    row_idx = None
    for i, line in enumerate(lines):
        if path_marker in line and line.strip().startswith("|"):
            row_idx = i
            break

    if row_idx is not None:
        old_row = lines[row_idx].rstrip("\n")
        if old_row == new_row:
            return False  # 변경 없음(멱등)
        lines[row_idx] = new_row + "\n"
        content = "".join(lines)
    else:
        # 헤더 위치 찾기
        header_idx = None
        sep_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == _INDEX_TABLE_HEADER:
                header_idx = i
            if header_idx is not None and stripped == _INDEX_TABLE_SEP:
                sep_idx = i
                break

        if header_idx is not None and sep_idx is not None:
            # sep 다음에 삽입
            lines.insert(sep_idx + 1, new_row + "\n")
            content = "".join(lines)
        else:
            # 표 자체 없음 — 범례+표 새로 추가
            suffix = "\n" if existing and not existing.endswith("\n") else ""
            block = (
                f"\n{_INDEX_LEGEND}\n\n"
                f"{_INDEX_TABLE_HEADER}\n"
                f"{_INDEX_TABLE_SEP}\n"
                f"{new_row}\n"
            )
            content = existing + suffix + block

    index_path.parent.mkdir(parents=True, exist_ok=True)
    # atomic write: 임시파일에 쓴 뒤 os.replace — 부분 변경 상태로 INDEX 가 남지 않도록
    import tempfile as _tempfile
    _idx_tmp: "Path | None" = None
    try:
        with _tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=index_path.parent,
            delete=False,
            suffix=".idx.tmp",
        ) as _itf:
            _idx_tmp = Path(_itf.name)  # name 을 write() 전에 저장
            _itf.write(content)
        os.replace(str(_idx_tmp), str(index_path))
        _idx_tmp = None  # 이동 완료 → 정리 불필요
    finally:
        if _idx_tmp is not None:
            try:
                _idx_tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return True


def _index_remove_row(index_path: Path, rel_path: str) -> bool:
    """INDEX.md 에서 해당 경로의 행을 제거. 변경됐으면 True."""
    if not index_path.is_file():
        return False
    path_marker = f"`{rel_path}`"
    lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = [l for l in lines
                 if not (path_marker in l and l.strip().startswith("|"))]
    if new_lines == lines:
        return False
    index_path.write_text("".join(new_lines), encoding="utf-8")
    return True


# ──────────────────────────────────────────────────────────────────
# 루트 라우팅 맵 (2열) 전용 헬퍼 — `_index_upsert` 미러, 4열 folder-INDEX 무회귀
# ──────────────────────────────────────────────────────────────────
# 루트 `memory/INDEX.md` 는 `| 경로 | 여기에 넣는 것 |` 2열 표 + 큐레이션 산문.
# 4열 헬퍼(_index_upsert/_index_remove_row)는 가중치·편집일 칸을 전제하므로 재사용 불가.
# 같은 패턴(헤더 토큰 탐색 · 백틱 경로 마커 · atomic write)을 2열용으로 따로 둔다.

# 루트 2열 표 구분자 — 폴더 라우팅 맵(`memory/INDEX.md`) 정본 헤더와 동일 토큰.
_ROOT_INDEX_TABLE_HEADER = "| 경로 | 여기에 넣는 것 |"
_ROOT_INDEX_TABLE_SEP    = "|---|---|"


def _root_index_find_row(lines: list, path: str, prefix: bool = False) -> "int | None":
    """루트 INDEX 표에서 백틱 경로 토큰으로 행을 찾는다. 없으면 None.

    - prefix=False(기본): 정확 토큰 `` `<path>` `` 매칭 — 백틱 경계가 폴더행
      `product/brand/` 과 파일행 `product/brand/philosophy.md` 를 구분(오매칭 없음).
    - prefix=True: 열림 백틱 + 접두 `` `<path>... `` 매칭 — 최상위 폴더 커버 여부
      판정용. stock 템플릿은 파일행(`team/members.md` 등)만 등재하므로 정확 토큰만
      보면 기본 설치의 매 write 가 "미등재" 오탐이 된다(#7 힌트).
    """
    marker = f"`{path}" + ("" if prefix else "`")
    for i, line in enumerate(lines):
        if marker in line and line.strip().startswith("|"):
            return i
    return None


def _root_index_covers_top(index_path: Path, top_folder: str) -> bool:
    """루트 라우팅 맵이 최상위 폴더(`team/` 등)를 커버하는지 — 접두 백틱 토큰 매칭.

    폴더행 `` `team/` `` 자체뿐 아니라 하위 행(`team/members.md`,
    `team/sessions/<이름>/`)도 커버 증거로 본다. 판정 불가(파일 없음 제외한
    읽기 실패)면 True — 힌트는 advisory 라 오탐 억제가 우선.
    """
    if not index_path.is_file():
        return False
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except (OSError, PermissionError):
        return True  # advisory: 읽기 실패 시 힌트 억제
    return _root_index_find_row(lines, top_folder, prefix=True) is not None


def _root_index_upsert(index_path: Path, path: str, desc: str) -> bool:
    """루트 `memory/INDEX.md` 2열 표에 행을 upsert(삽입 또는 갱신). 변경됐으면 True.

    형식:
      | 경로 | 여기에 넣는 것 |
      |---|---|
      | `product/brand/` | 설명 한 줄 |

    - 표 위/주변 산문(주입 안내·"새 폴더 등재 필수"·팀 루트 안내)은 보존 — 헤더 토큰
      `| 경로 | 여기에 넣는 것 |` 을 찾아 **그 표에만** 작용한다.
    - 행 식별: 백틱 토큰 `` `<path>` `` 매칭. 백틱 경계가 폴더행 `product/brand/` 과
      파일행 `product/brand/philosophy.md` 를 구분해 오매칭이 없다(테스트로 고정).
    - 같은 path 행이 있으면 desc 만 갱신, 없으면 sep 다음에 삽입. 표 없으면 새로 생성.
    - 멱등: 같은 내용 재호출 → 변경 없음(False).
    - INDEX.md 자신에는 frontmatter 붙이지 않는다(원칙).
    - atomic write: 임시파일 + os.replace(_index_upsert 미러) — 부분 변경 상태 방지.
    """
    existing = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""

    # P2: 설명 칸 이스케이핑 — |·줄바꿈·백틱에 표가 깨지지 않도록.
    # path 는 백틱 마커 식별에 쓰이므로 raw 로 둔다(_index_upsert 와 동일 정책 — 호출부가 검증).
    safe_desc = _escape_index_cell(desc)
    new_row = f"| `{path}` | {safe_desc} |"

    # 기존 행 검색 (백틱 경로 토큰으로 식별 — _root_index_find_row 공용 헬퍼)
    lines = existing.splitlines(keepends=True)
    row_idx = _root_index_find_row(lines, path)

    if row_idx is not None:
        old_row = lines[row_idx].rstrip("\n")
        if old_row == new_row:
            return False  # 변경 없음(멱등)
        lines[row_idx] = new_row + "\n"
        content = "".join(lines)
    else:
        # 헤더 위치 찾기
        header_idx = None
        sep_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == _ROOT_INDEX_TABLE_HEADER:
                header_idx = i
            if header_idx is not None and stripped == _ROOT_INDEX_TABLE_SEP:
                sep_idx = i
                break

        if header_idx is not None and sep_idx is not None:
            # sep 다음에 삽입
            lines.insert(sep_idx + 1, new_row + "\n")
            content = "".join(lines)
        else:
            # 표 자체 없음 — 헤더+sep+행 새로 추가(산문은 그대로 위에 보존)
            suffix = "\n" if existing and not existing.endswith("\n") else ""
            block = (
                f"\n{_ROOT_INDEX_TABLE_HEADER}\n"
                f"{_ROOT_INDEX_TABLE_SEP}\n"
                f"{new_row}\n"
            )
            content = existing + suffix + block

    index_path.parent.mkdir(parents=True, exist_ok=True)
    # atomic write: 임시파일에 쓴 뒤 os.replace — 부분 변경 상태로 INDEX 가 남지 않도록
    import tempfile as _tempfile
    _idx_tmp: "Path | None" = None
    try:
        with _tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=index_path.parent,
            delete=False,
            suffix=".idx.tmp",
        ) as _itf:
            _idx_tmp = Path(_itf.name)  # name 을 write() 전에 저장
            _itf.write(content)
        os.replace(str(_idx_tmp), str(index_path))
        _idx_tmp = None  # 이동 완료 → 정리 불필요
    finally:
        if _idx_tmp is not None:
            try:
                _idx_tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return True


def _root_index_remove_row(index_path: Path, path: str) -> bool:
    """루트 `memory/INDEX.md` 2열 표에서 해당 경로 행을 제거. 변경됐으면 True.

    백틱 토큰 `` `<path>` `` 으로 식별 — 폴더행/파일행 오매칭 없음(_root_index_upsert 와 동일).
    부재 시 무변경(False). _index_remove_row 미러.
    """
    if not index_path.is_file():
        return False
    path_marker = f"`{path}`"
    lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = [l for l in lines
                 if not (path_marker in l and l.strip().startswith("|"))]
    if new_lines == lines:
        return False
    index_path.write_text("".join(new_lines), encoding="utf-8")
    return True


# ──────────────────────────────────────────────────────────────────
# 양방향 백링크 (L2): memory 문서 ↔ 현재 세션로그
# ──────────────────────────────────────────────────────────────────
# memory write/delete 성공 직후 엔진이 기계적으로 두 방향 링크를 건다:
#   세션로그 → 문서:  세션로그에 `📝 생성: [[<rel>]]` 한 줄 append.
#   문서 → 세션로그:  문서 frontmatter 에 `session: ...` 필드 추가.
# 비차단(advisory)·멱등. memory 핵심 쓰기(파일/INDEX/커밋)는 이미 끝난 뒤라
# 백링크 실패가 본작업을 롤백시키지 않는다.

def _session_log_path(team_root: Path, author: str, workday: str) -> Path:
    """현재 author 의 오늘(작업일) 세션로그 경로."""
    return team_root / "memory" / "team" / "sessions" / author / f"{workday}.md"


def _backlink_session_to_doc(session_path: Path, author: str, workday: str,
                              rel_path: str, verb: str) -> bool:
    """세션로그 → 문서 백링크: 세션로그에 `<아이콘> <동작>: [[<rel>]]` 한 줄 append.

    멱등: 같은 (verb, rel_path) 줄이 이미 있으면 skip.
    세션로그 파일이 없으면 frontmatter + 첫 줄로 생성한다.
    blocked 폴더(sessions/)라 memory.write API 를 우회해 직접 쓴다.
    비차단: 어떤 예외도 밖으로 던지지 않는다(advisory).

    반환: 세션로그 파일이 백링크 반영 상태로 존재하면 True(이미 멱등 줄 포함 포함),
    실패(예외)하면 False. 호출부는 True 일 때만 커밋 paths 에 세션로그를 포함한다.
    """
    label = {"write-new": "📝 생성", "write-update": "✏️ 수정",
             "delete": "🗑️ 삭제"}.get(verb, "📝 기록")
    # 위키링크는 Obsidian vault 루트(memory/) 기준 상대경로여야 클릭이 작동한다 (#21).
    # 호출부는 INDEX·git 용으로 memory/ 접두 경로(rel_for_git/rel_for_index)를 넘기는데,
    # 그대로 박으면 [[memory/...]] 가 되어 vault 가 memory/memory/... 로 해석 → 깨진 링크.
    # 호출자 무관하게 방어적으로 memory/ 접두사를 벗긴다.
    wiki_path = rel_path[len("memory/"):] if rel_path.startswith("memory/") else rel_path
    link_line = f"- {label}: [[{wiki_path}]]"
    try:
        if session_path.is_file():
            existing = session_path.read_text(encoding="utf-8")
            # 멱등: 같은 동작+경로 줄이 이미 있으면 skip
            if link_line in existing.splitlines():
                return True
            sep = "" if existing.endswith("\n") else "\n"
            with open(session_path, "a", encoding="utf-8") as f:
                f.write(f"{sep}{link_line}\n")
        else:
            # 세션로그 없으면 frontmatter + 메모리 변경 섹션으로 생성
            session_path.parent.mkdir(parents=True, exist_ok=True)
            summary = f"{label}: {rel_path}"
            body = (f"\n## 메모리 변경\n\n{link_line}\n")
            with open(session_path, "w", encoding="utf-8") as f:
                f.write(_frontmatter(author, workday, summary))
                f.write(body)
        return True
    except (OSError, PermissionError):
        return False  # advisory — 백링크 실패는 비차단(커밋 paths 에서 제외)


def _doc_add_session_field(doc_path: Path, session_rel: str) -> bool:
    """문서 → 세션로그 백링크: 문서 frontmatter 에 `session: <rel>` 필드 추가.

    _rebuild_frontmatter 의 extra-field 보존을 재사용한다(4필드 뒤에 session 추가).
    멱등: 이미 같은 session 값이면 재작성하지 않는다.
    비차단: 예외를 밖으로 던지지 않는다(advisory).

    반환: 문서가 session 필드 반영 상태이면 True(이미 같은 값으로 멱등 포함),
    frontmatter 없음/예외이면 False. 호출부는 True 일 때만 커밋 paths 에 문서를 (다시) 포함.
    """
    try:
        if not doc_path.is_file():
            return False
        text = doc_path.read_text(encoding="utf-8")
        fm = _parse_knowledge_frontmatter(text)
        if not fm:
            return False  # frontmatter 없는 문서는 건드리지 않음(write 경로가 항상 스탬프함)
        if fm.get("session") == session_rel:
            return True  # 멱등 — 이미 반영됨
        body = _knowledge_body(text)
        # 4 known 필드는 보존, session 만 갱신/추가
        new_fm_dict = dict(fm)
        new_fm_dict["session"] = session_rel
        new_full = _rebuild_frontmatter(
            new_fm_dict,
            new_fm_dict.get("author", ""),
            new_fm_dict.get("weight", ""),
            new_fm_dict.get("created_at", ""),
            new_fm_dict.get("updated_at", ""),
        ) + body
        if new_full == text:
            return True
        doc_path.write_text(new_full, encoding="utf-8")
        return True
    except (OSError, PermissionError):
        return False  # advisory


def _emit_chat_summary(verb: str, rel_path: str, weight: str | None,
                        author: str | None, description: str | None) -> None:
    """chat 통지용 한 줄 요약을 stdout 에 출력(A안: 엔진은 MCP 호출 안 함).

    스킬/AI 가 이 요약을 받아 chat 슬롯 벤더 MCP 도구로 직접 통지한다.
    엔진은 재료(요약 문자열)만 제공한다.
    """
    action_ko = {"write-new": "추가", "write-update": "수정",
                 "delete": "삭제"}.get(verb, "변경")
    parts = [f"[chat-notify] memory {action_ko}: {rel_path}"]
    if weight:
        parts.append(f"weight={weight}")
    if author:
        parts.append(f"author={author}")
    if description:
        parts.append(f"요약={description}")
    print(" · ".join(parts))


def cmd_knowledge(team_root: Path, action: str | None,
                  folder: str | None, filename: str | None,
                  content: str | None, author: str | None,
                  weight: str | None, rel_path: str | None,
                  date_str: str | None) -> int:
    """memory 동사 — 메모리 파일 write/delete (기계 전담).

    write:  frontmatter 스탬프 · 파일 write · folder INDEX 행 upsert · 양방향 백링크
            · do_commit(paths, push=True).
    delete: 파일 삭제 · INDEX 행 제거 · 세션로그 백링크 · do_commit(paths, push=True).

    엔진 불변:
      - weight 는 인자로만 받는다(추측 금지). 스킬이 사용자에게 확인한 값을 전달.
      - push=True(위키=팀공유): 매 memory 변경 즉시 push. **push 실패는 비차단** —
        로컬 커밋·memory 변경은 유지, 경고만(RC 영향 없음). do_commit 이 push 실패해도
        ok=True·pushed=False 로 커밋을 보존한다.
      - 양방향 백링크(세션로그 append + 문서 session: frontmatter)는 do_commit *전*에
        수행해 **같은 커밋에 포함**(advisory·비차단: 실패해도 본작업·커밋은 진행).
      - traversal 차단(_validate_knowledge_path).
      - 멱등: 같은 내용 재호출 → 변경 없음(커밋 안 생김).
      - 대상 범위: product/·team/·team/decisions/·soma/ 등 _KNOWLEDGE_ALLOWED_FOLDERS.
        sessions/·meeting/ 은 제외.
    """
    if action == "write":
        # 필수 인자 검증
        if not folder:
            print("[error] memory write: --folder 가 필요합니다.", file=sys.stderr)
            return 2
        if not filename:
            print("[error] memory write: --filename 이 필요합니다.", file=sys.stderr)
            return 2
        if content is None:
            print("[error] memory write: --content 가 필요합니다.", file=sys.stderr)
            return 2
        if not author:
            print("[error] memory write: --author 가 필요합니다.", file=sys.stderr)
            return 2
        if not weight:
            print("[error] memory write: --weight 가 필요합니다(추측 금지).", file=sys.stderr)
            return 2

        # weight 3-enum 검증 (P2)
        if weight not in _KNOWLEDGE_VALID_WEIGHTS:
            print(f"[error] memory write: --weight 는 {_KNOWLEDGE_VALID_WEIGHTS} 중 하나여야 합니다: {weight!r}",
                  file=sys.stderr)
            return 2

        # author traversal 가드
        err = _validate_author(author)
        if err is not None:
            print(f"[error] memory write: --author: {err}", file=sys.stderr)
            return 2

        # folder/filename traversal + containment 가드 + 허용 폴더 검증 (P1-1 포함)
        err = _validate_knowledge_path(team_root, folder, filename)
        if err is not None:
            print(f"[error] memory write: {err}", file=sys.stderr)
            return 2

        # content 제어문자 거부 (개행·탭·CR 은 허용 — 문서 포맷에 필수)
        # 거부 대상:
        #   - unicodedata.category() 기준 Cc(C0/C1 제어), Cf(포맷 제어), Cs(surrogate)
        #   - 단, \n(U+000A)·\r(U+000D)·\t(U+0009) 만 명시 허용.
        # surrogate 는 Python str 에 고립 surrogate 로 들어올 수 있으며
        # unicodedata.category() 호출 시 UnicodeEncodeError 를 유발하므로 코드포인트로 직접 처리.
        _ALLOWED_CTRL = {"\n", "\r", "\t"}
        for _ch in content:
            if _ch in _ALLOWED_CTRL:
                continue
            _cp = ord(_ch)
            # 고립 surrogate (U+D800–U+DFFF): category() 호출 전 직접 거부
            if 0xD800 <= _cp <= 0xDFFF:
                print(f"[error] memory write: --content 에 허용되지 않는 문자가 "
                      f"있습니다(surrogate U+{_cp:04X}). 제어·포맷·surrogate 문자는 거부됩니다.",
                      file=sys.stderr)
                return 2
            cat = unicodedata.category(_ch)
            # Cc=제어(C0+C1), Cf=포맷(ZWJ·ZWNJ·BOM 등), Cs=surrogate(이미 위에서 처리)
            if cat in ("Cc", "Cf", "Cs"):
                print(f"[error] memory write: --content 에 허용되지 않는 문자가 "
                      f"있습니다(U+{_cp:04X}, category={cat}). "
                      f"제어·포맷·surrogate 문자는 거부됩니다.",
                      file=sys.stderr)
                return 2

        target_path = team_root / "memory" / folder / filename
        today = date_str or datetime.now().strftime("%Y-%m-%d")

        # 기존 파일 확인
        content_changed = True  # 신규 파일이면 항상 변경
        if target_path.is_file():
            existing_text = target_path.read_text(encoding="utf-8")
            fm = _parse_knowledge_frontmatter(existing_text)
            created_at = fm.get("created_at", today)
            updated_at = today
            # P2: 추가 필드 보존 — 4필드 재조립
            new_fm = _rebuild_frontmatter(fm, author, weight, created_at, updated_at)
            new_full = new_fm + content
            # 멱등: 같은 내용이면 무변경
            if new_full == existing_text:
                print(f"teammode memory write — 변경 없음(멱등): {folder}/{filename}")
                return 0
            # 본문이 실제로 바뀌었는지 확인 (편집일 결정용)
            old_body = _knowledge_body(existing_text)
            content_changed = (content != old_body)
        else:
            fm = {}
            # 신규 파일
            created_at = today
            updated_at = today
            new_fm = _rebuild_frontmatter(fm, author, weight, created_at, updated_at)
            new_full = new_fm + content

        # ── 파일 I/O (OSError/PermissionError → exit 2 + 친화 메시지) ─────────
        # 긴 파일명(255자↑ → OSError)·권한 문제(PermissionError) 등 OS 예외를
        # 트레이스백 + exit 1 대신 친화 메시지 + exit 2 로 처리한다.
        # 기존 검증 실패(입력검증 exit 2) 규약과 일치: 사람이 고칠 수 있는 입력 문제.
        #
        # 정합성(S1 적대검수 지적): 파일 write 성공 후 INDEX 갱신 실패 시 파일만 남는
        # 부분 실패를 막기 위해 2단계 처리한다.
        #   1단계: 임시파일+os.replace 로 파일 원자 write.
        #   2단계: INDEX upsert. 실패 시 1단계를 롤백(파일 복원/삭제).
        is_new_file = not target_path.is_file()
        _old_file_content: str | None = None
        if not is_new_file:
            try:
                _old_file_content = target_path.read_text(encoding="utf-8")
            except (OSError, PermissionError):
                _old_file_content = None  # 읽기 실패 시 롤백 포기, 이후 write 에서 실패

        _tmp_path = None  # write() 호출 전에 None 초기화 → finally 에서 항상 정리 가능
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            # atomic write: 임시파일에 쓴 뒤 os.replace 로 교체
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8",
                dir=target_path.parent,
                delete=False,
                suffix=".tmp",
            ) as _tf:
                _tmp_path = Path(_tf.name)  # name 을 write() 호출 전에 저장
                _tf.write(new_full)
            os.replace(str(_tmp_path), str(target_path))
            _tmp_path = None  # os.replace 성공 → 파일이 target 으로 이동됐으므로 정리 불필요
        except (OSError, PermissionError) as exc:
            print(f"[error] memory write: 파일 쓰기 실패 — {exc}", file=sys.stderr)
            return 2
        finally:
            # 예외 발생 여부와 무관하게 임시파일 잔류 방지
            if _tmp_path is not None:
                try:
                    _tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

        # 편집일 계산 (P1-3: subject-substring 의존 제거):
        # 본문이 바뀌었으면 today 를 편집일로 사용.
        # 본문 미변경(weight/author 만 변경)이면 기존 INDEX 행의 편집일을 그대로 보존.
        # git log subject 파싱은 신뢰 불가(메타커밋 오판) → 제거.
        rel_for_git = str(Path("memory") / folder / filename)
        if content_changed:
            edit_date = today
        else:
            index_path_for_date = team_root / "memory" / folder / "INDEX.md"
            edit_date = _index_get_edit_date(index_path_for_date, rel_for_git) or today

        # INDEX 행 upsert (rel_path = memory/ 포함 표준 경로)
        # 실패 시 파일을 롤백하여 부분 실패 정합성 보장.
        index_path = team_root / "memory" / folder / "INDEX.md"
        description = content.strip().splitlines()[0][:60] if content.strip() else filename
        try:
            _index_upsert(index_path, rel_for_git, weight, description, edit_date)
        except (OSError, PermissionError) as exc:
            # INDEX 갱신 실패 → 파일 롤백 (부분 실패 정합성)
            try:
                if is_new_file:
                    target_path.unlink(missing_ok=True)
                elif _old_file_content is not None:
                    target_path.write_text(_old_file_content, encoding="utf-8")
            except Exception as rb_exc:
                print(f"[error] memory write: INDEX 갱신 실패 + 파일 롤백도 실패 — "
                      f"INDEX: {exc} / 롤백: {rb_exc}", file=sys.stderr)
                return 2
            print(f"[error] memory write: INDEX 갱신 실패(파일 롤백됨) — {exc}",
                  file=sys.stderr)
            return 2

        # ── 양방향 백링크(advisory·비차단): do_commit *전*에 수행해 같은 커밋에 포함 ──
        # 정합성 순서: 파일 write → INDEX upsert(롤백 가드) → **백링크** → do_commit.
        # 백링크는 핵심 쓰기 끝난 뒤라 실패해도 본작업(파일/INDEX)을 롤백하지 않는다.
        # 성공 시에만 해당 경로를 커밋 paths 에 넣어 한 커밋에 같이 스테이징·커밋시킨다
        # (검수 BLOCK: 종전엔 do_commit 뒤라 세션로그·frontmatter 가 영영 미커밋됐다).
        session_workday = date_str or _workday.workday_str(_workday.now_kst())
        session_path = _session_log_path(team_root, author, session_workday)
        session_rel = str(Path("team") / "sessions" / author / f"{session_workday}.md")
        verb = "write-new" if is_new_file else "write-update"
        sess_ok = _backlink_session_to_doc(session_path, author, session_workday,
                                           rel_for_git, verb)
        doc_ok = _doc_add_session_field(target_path, session_rel)

        # do_commit(paths 한정, push=True) — 백링크 성공분을 같은 커밋에 포함.
        # push=True: 위키=팀공유이므로 매 변경 즉시 push(아래 push 실패는 비차단).
        changed_paths = [str(target_path), str(index_path)]
        if sess_ok:
            changed_paths.append(str(session_path))
        # doc_ok 면 target_path 는 백링크로 다시 변경됐으니 이미 목록에 있는 그대로 커밋된다
        # (실패 시에도 INDEX upsert 로 이미 변경돼 있어 target_path 는 유지 — 무해).
        commit_result = _git_ops.do_commit(
            str(team_root),
            message=f"docs(memory): write {folder}/{filename}",
            push=True,
            paths=changed_paths,
        )
        # 정상 케이스(ok=False 여도 경고 불필요):
        #   "nothing to commit"  — 멱등(이미 커밋됨 혹은 변경 없음)
        #   "no paths to stage"  — 빈 경로 목록
        #   "not a git work tree"— git 없는 환경(파일 쓰기는 완료)
        # 그 외 ok=False → 실제 git 실패(add 실패·commit 실패·timeout 등) → 경고 + non-zero
        # push 실패는 do_commit 이 ok=True·pushed=False 로 보존(비차단) — 아래 경고만.
        _COMMIT_SILENT_DETAILS = ("nothing to commit", "no paths to stage",
                                  "not a git work tree")
        commit_failed = (not commit_result.ok
                         and commit_result.detail not in _COMMIT_SILENT_DETAILS)

        # ── chat 통지 재료(A안: 엔진은 요약만 stdout 출력, MCP 호출은 AI) ──────
        _emit_chat_summary(verb, rel_for_git, weight, author, description)

        # ── 루트 라우팅 맵 미등재 힌트(#7, advisory) ──────────────────────
        # 루트 INDEX.md 는 "새 폴더 등재 필수" 인데 write 흐름이 등재 동사
        # (`memory route upsert` — #12/#16)를 안내하지 않아 발견 불가였다.
        # 자동 등재는 하지 않는다 — 설명 한 줄(라우팅 맵 품질)은 사람/AI 몫.
        # 최상위 폴더가 루트 2열 표에 커버(폴더행 또는 하위 행)돼 있지 않으면
        # 한 줄 힌트만 출력한다. 비차단: 판정 실패해도 write 결과는 그대로.
        try:
            top_folder = folder.split("/")[0] + "/"
            root_index_path = team_root / "memory" / "INDEX.md"
            if not _root_index_covers_top(root_index_path, top_folder):
                print(f"[hint] '{top_folder}'가 루트 INDEX에 미등재 — 등록: "
                      f"python infra/teammode.py memory route upsert "
                      f"--root <루트> --path {top_folder} "
                      f"--desc \"<한 줄 설명>\" --author {author}")
        except Exception:
            pass  # advisory — 힌트 실패가 write 를 막지 않는다

        if commit_failed:
            print(f"[warning] memory write: 커밋 실패 — {commit_result.detail}",
                  file=sys.stderr)
            print(f"teammode memory write — {folder}/{filename} 완료(커밋 안 됨)")
            return 1

        # push 실패는 비차단: 로컬 커밋·memory 변경은 유지, 경고만 출력(RC=0).
        if commit_result.committed and not commit_result.pushed:
            print(f"[warning] memory write: push 실패(로컬 커밋은 유지) — "
                  f"{commit_result.detail}", file=sys.stderr)

        print(f"teammode memory write — {folder}/{filename} 완료")
        return 0

    if action == "delete":
        # 필수 인자 검증
        if not rel_path:
            print("[error] memory delete: --path <memory/상대경로> 가 필요합니다.",
                  file=sys.stderr)
            return 2
        if not author:
            print("[error] memory delete: --author 가 필요합니다.", file=sys.stderr)
            return 2

        # author traversal 가드
        err = _validate_author(author)
        if err is not None:
            print(f"[error] memory delete: --author: {err}", file=sys.stderr)
            return 2

        # .. 세그먼트 명시 차단 (early: resolve 전에)
        if ".." in rel_path:
            print(f"[error] memory delete: 경로에 '..' 이 포함될 수 없습니다: {rel_path!r}",
                  file=sys.stderr)
            return 2

        # ── P0: symlink 탈출 가드 ──────────────────────────────────
        real_root = team_root.resolve()
        memory_dir = (team_root / "memory").resolve()
        try:
            memory_dir.relative_to(real_root)
        except ValueError:
            print(f"[error] memory delete: memory/ 가 team_root 밖을 가리킵니다(심링크 탈출 차단)",
                  file=sys.stderr)
            return 2

        # rel_path 는 "memory/..." 형식일 수도 있고 "team/decisions/foo.md" 형식일 수도 있다.
        # NUL 등 포함 경로는 Path.resolve() 에서 ValueError 를 던진다 — exit 2 로 처리.
        try:
            if rel_path.startswith("memory/"):
                candidate = (team_root / rel_path).resolve()
                rel_for_index = rel_path
                # 내부 folder 추출 (허용 폴더 검증용)
                inner = rel_path[len("memory/"):]
            else:
                candidate = (team_root / "memory" / rel_path).resolve()
                rel_for_index = "memory/" + rel_path
                inner = rel_path
        except ValueError as exc:
            print(f"[error] memory delete: 경로에 허용되지 않는 문자가 있습니다 — {exc}",
                  file=sys.stderr)
            return 2

        # ── P1-1: 허용 폴더 검증 (write 와 동일한 blocked/allowed 규칙) ─────
        # INDEX.md 자신(root) 삭제 거부 + blocked/allowed 폴더 검증
        filename_part = inner.split("/")[-1] if "/" in inner else inner
        folder_part = "/".join(inner.split("/")[:-1]) if "/" in inner else ""

        # INDEX.md 삭제 거부 (root-level INDEX 는 특히)
        if filename_part == "INDEX.md":
            print(f"[error] memory delete: INDEX.md 는 직접 삭제할 수 없습니다: {rel_path!r}",
                  file=sys.stderr)
            return 2

        # ── filename 문자 검증 (write 와 동일한 정책) ──────────────────
        # 제어문자·전각문자·비ASCII filename 거부 (NUL 등은 ValueError 전에 차단)
        fn_err = _validate_filename_chars(filename_part)
        if fn_err is not None:
            print(f"[error] memory delete: --path 의 filename 검증 실패 — {fn_err}",
                  file=sys.stderr)
            return 2

        # 허용 폴더 검증 — write 와 동일한 blocked/allowed 규칙 (P1-1)
        # folder_part 가 비어 있으면(root memory 파일) 허용 목록에 없으므로 거부
        norm_folder = folder_part.replace("\\", "/").rstrip("/") if folder_part else ""
        if not norm_folder:
            # memory/ 바로 아래 파일 — 허용 폴더 목록에 없음 → 거부
            print(f"[error] memory delete: 허용 폴더 하위의 파일만 삭제할 수 있습니다. "
                  f"허용: {', '.join(_KNOWLEDGE_ALLOWED_FOLDERS)}",
                  file=sys.stderr)
            return 2
        # 명시 차단 목록 먼저(blocked 우선 — write 와 동일 규칙)
        for bf in _KNOWLEDGE_BLOCKED_FOLDERS:
            if norm_folder == bf or norm_folder.startswith(bf + "/"):
                print(f"[error] memory delete: folder '{folder_part}' 는 삭제 대상이 아닙니다"
                      f"(훅/tm-context 관리 경로)",
                      file=sys.stderr)
                return 2
        del_allowed = False
        for af in _KNOWLEDGE_ALLOWED_FOLDERS:
            if norm_folder == af or norm_folder.startswith(af + "/"):
                del_allowed = True
                break
        if not del_allowed:
            print(f"[error] memory delete: folder '{folder_part}' 는 허용되지 않습니다. "
                  f"허용: {', '.join(_KNOWLEDGE_ALLOWED_FOLDERS)}",
                  file=sys.stderr)
            return 2

        # containment 가드
        try:
            candidate.relative_to(memory_dir)
        except ValueError:
            print(f"[error] memory delete: 경로가 memory/ 를 벗어납니다: {rel_path!r}",
                  file=sys.stderr)
            return 2

        target_path = candidate

        if not target_path.is_file():
            print(f"teammode memory delete — 파일 없음(멱등): {rel_path}")
            return 0

        # ── 파일 I/O (OSError/PermissionError → exit 2 + 친화 메시지) ─────────
        # 정합성(S1 적대검수 지적): index_remove 성공 후 unlink 실패 → INDEX 행만 사라지는
        # 부분 실패를 막기 위해 2단계 처리한다.
        #   1단계: INDEX 행 제거 (INDEX 원본 내용 백업 후).
        #   2단계: 파일 unlink. 실패 시 1단계를 롤백(INDEX 원본 복원).
        folder_path = target_path.parent
        index_path = folder_path / "INDEX.md"

        # INDEX 원본 백업 (롤백용)
        _index_backup: str | None = None
        if index_path.is_file():
            try:
                _index_backup = index_path.read_text(encoding="utf-8")
            except (OSError, PermissionError):
                _index_backup = None

        try:
            _index_remove_row(index_path, rel_for_index)
        except (OSError, PermissionError) as exc:
            print(f"[error] memory delete: INDEX 갱신 실패 — {exc}", file=sys.stderr)
            return 2

        try:
            target_path.unlink()
        except (OSError, PermissionError) as exc:
            # unlink 실패 → INDEX 롤백 (부분 실패 정합성)
            try:
                if _index_backup is not None:
                    index_path.write_text(_index_backup, encoding="utf-8")
            except Exception as rb_exc:
                print(f"[error] memory delete: 파일 삭제 실패 + INDEX 롤백도 실패 — "
                      f"unlink: {exc} / 롤백: {rb_exc}", file=sys.stderr)
                return 2
            print(f"[error] memory delete: 파일 삭제 실패(INDEX 롤백됨) — {exc}",
                  file=sys.stderr)
            return 2

        # ── 세션로그 백링크(advisory·비차단): do_commit *전*에 수행해 같은 커밋에 포함 ──
        # 삭제 대상 문서는 사라졌으므로 문서→세션로그 방향은 없다. 세션로그→문서만.
        # 성공 시에만 세션로그를 커밋 paths 에 넣어 삭제 커밋에 같이 들어가게 한다.
        session_workday = _workday.workday_str(_workday.now_kst())
        session_path = _session_log_path(team_root, author, session_workday)
        sess_ok = _backlink_session_to_doc(session_path, author, session_workday,
                                           rel_for_index, "delete")

        # do_commit(paths 한정, push=True) — 위키=팀공유. push 실패는 비차단(아래 경고만).
        # index_path 는 **존재할 때만** 스테이징 — INDEX.md 없는 폴더(엔진 외부에서 채워진
        # 폴더, 예: product/design/)에서 삭제 시 `git add <없는 INDEX.md>` 가 pathspec 매칭
        # 실패로 커밋 전체를 abort 시키는 버그 차단. 파일은 이미 unlink 됐으므로 그땐 삭제가
        # 커밋에 안 들어가는 부분 실패가 됐다. (write 분기는 _index_upsert 가 항상 생성하므로 무해.)
        changed_paths = [str(target_path)]
        if index_path.is_file():
            changed_paths.append(str(index_path))
        if sess_ok:
            changed_paths.append(str(session_path))
        commit_result = _git_ops.do_commit(
            str(team_root),
            message=f"docs(memory): delete {rel_path}",
            push=True,
            paths=changed_paths,
        )
        # 정상 케이스(ok=False 여도 경고 불필요):
        #   "nothing to commit"   — 멱등
        #   "no paths to stage"   — 빈 경로 목록
        #   "not a git work tree" — git 없는 환경(파일 삭제는 완료)
        # 그 외 ok=False → 실제 git 실패(add 실패·commit 실패·timeout 등) → 경고 + non-zero
        # push 실패는 do_commit 이 ok=True·pushed=False 로 보존(비차단) — 아래 경고만.
        _COMMIT_SILENT_DETAILS = ("nothing to commit", "no paths to stage",
                                  "not a git work tree")
        commit_failed = (not commit_result.ok
                         and commit_result.detail not in _COMMIT_SILENT_DETAILS)

        # ── chat 통지 재료(A안) ────────────────────────────────────────────
        _emit_chat_summary("delete", rel_for_index, None, author, None)

        if commit_failed:
            print(f"[warning] memory delete: 커밋 실패 — {commit_result.detail}",
                  file=sys.stderr)
            print(f"teammode memory delete — {rel_path} 삭제됨(커밋 안 됨)")
            return 1

        # push 실패는 비차단: 로컬 커밋·memory 변경은 유지, 경고만 출력(RC=0).
        if commit_result.committed and not commit_result.pushed:
            print(f"[warning] memory delete: push 실패(로컬 커밋은 유지) — "
                  f"{commit_result.detail}", file=sys.stderr)

        print(f"teammode memory delete — {rel_path} 삭제됨")
        return 0

    print(f"[error] memory: 알 수 없는 action: {action!r}. write/delete 중 하나.",
          file=sys.stderr)
    return 2


def _validate_route_path(team_root: Path, path: str) -> str | None:
    """루트 라우팅 맵 `--path` 경량 traversal 가드. 위반 시 에러 메시지 반환.

    `_validate_knowledge_path` 는 folder/filename 분리 + allowed-folders 검증을 전제하므로
    루트 맵(폴더행 `product/foo/` · 파일행 `product/brand/philosophy.md` 모두 허용)에는
    부적합 — 정규화 후 memory/ 이탈만 차단하는 경량 동등 검증을 둔다(설계 §4.4).

    - 빈 문자열·절대경로·'..' 세그먼트 거부.
    - 정규화(resolve) 후 team_root/memory/ 하위여야 한다(심링크 탈출 포함 차단).
    - 표 행을 깨뜨리는 제어문자·파이프·개행·백틱 거부(2열 표 정합성).
    """
    if not path:
        return "경로가 비어 있습니다."
    if path.startswith("/") or path.startswith("\\"):
        return f"절대경로는 허용되지 않습니다: {path!r}"
    if ".." in path:
        return f"경로에 '..' 이 포함될 수 없습니다: {path!r}"
    # 표 행(`| `<path>` | ... |`)을 깨거나 백틱 마커를 교란하는 문자 거부.
    for ch in path:
        if ord(ch) < 32 or ch in ("|", "`"):
            return f"경로에 허용되지 않는 문자가 있습니다: {path!r}"

    # ── 정규화 후 memory/ 하위 containment 검증 (심링크 탈출 포함) ──────
    real_root = team_root.resolve()
    memory_dir = (team_root / "memory").resolve()
    try:
        memory_dir.relative_to(real_root)
    except ValueError:
        return "memory/ 가 team_root 밖을 가리킵니다(심링크 탈출 차단)."
    try:
        candidate = (team_root / "memory" / path).resolve()
    except (ValueError, OSError) as exc:
        return f"경로 정규화 실패 — {exc}"
    try:
        candidate.relative_to(memory_dir)
    except ValueError:
        return f"경로가 memory/ 를 벗어납니다: {path!r}"
    return None


def cmd_route(team_root: Path, sub_action: str | None,
              path: str | None, desc: str | None, author: str | None) -> int:
    """memory route 동사 — 루트 라우팅 맵(`memory/INDEX.md` 2열 표) CRUD (기계 전담).

    upsert: 행 삽입/갱신(`_root_index_upsert`) → do_commit([memory/INDEX.md], push=True).
    remove: 행 제거(`_root_index_remove_row`) → 변경 시 동일 커밋.

    엔진 불변(memory write/delete 와 동일 규약):
      - 대상 파일은 루트 `memory/INDEX.md` 고정(인자 없음). 4열 folder-INDEX 무회귀.
      - push=True(위키=팀공유): **push 실패는 비차단**(로컬 커밋 보존, 경고만).
      - traversal 차단(_validate_route_path — folder/filename 분리 없는 경량 가드).
      - 멱등: 같은 내용 재호출 → 변경 없음(커밋 안 생김), exit 0.
      - 엔진이 python 직접 write → kb-write-guard 우회(unlock 불필요).
    """
    index_path = team_root / "memory" / "INDEX.md"

    if sub_action == "upsert":
        # 필수 인자 검증 (2열 산문은 자동 추출 불가 → --desc 필수)
        if not path:
            print("[error] memory route upsert: --path 가 필요합니다.", file=sys.stderr)
            return 2
        if desc is None:
            print("[error] memory route upsert: --desc 가 필요합니다(2열 설명은 추측 금지).",
                  file=sys.stderr)
            return 2
        if not author:
            print("[error] memory route upsert: --author 가 필요합니다.", file=sys.stderr)
            return 2

        # author traversal 가드
        err = _validate_author(author)
        if err is not None:
            print(f"[error] memory route upsert: --author: {err}", file=sys.stderr)
            return 2

        # path traversal 가드 (경량 — memory/ 이탈 차단)
        err = _validate_route_path(team_root, path)
        if err is not None:
            print(f"[error] memory route upsert: {err}", file=sys.stderr)
            return 2

        # 2열 표 행 upsert (atomic). I/O 예외 → exit 2 + 친화 메시지(트레이스백 아님).
        try:
            changed = _root_index_upsert(index_path, path, desc)
        except (OSError, PermissionError) as exc:
            print(f"[error] memory route upsert: INDEX 갱신 실패 — {exc}", file=sys.stderr)
            return 2

        if not changed:
            print(f"teammode memory route upsert — 변경 없음(멱등): {path}")
            return 0

        return _route_commit(team_root, index_path,
                             message=f"docs(memory): route upsert {path}",
                             done_msg=f"teammode memory route upsert — {path} 등재")

    if sub_action == "remove":
        # 필수 인자 검증 (remove 는 --desc 불필요)
        if not path:
            print("[error] memory route remove: --path 가 필요합니다.", file=sys.stderr)
            return 2
        if not author:
            print("[error] memory route remove: --author 가 필요합니다.", file=sys.stderr)
            return 2

        err = _validate_author(author)
        if err is not None:
            print(f"[error] memory route remove: --author: {err}", file=sys.stderr)
            return 2

        err = _validate_route_path(team_root, path)
        if err is not None:
            print(f"[error] memory route remove: {err}", file=sys.stderr)
            return 2

        try:
            changed = _root_index_remove_row(index_path, path)
        except (OSError, PermissionError) as exc:
            print(f"[error] memory route remove: INDEX 갱신 실패 — {exc}", file=sys.stderr)
            return 2

        if not changed:
            print(f"teammode memory route remove — 행 없음(멱등): {path}")
            return 0

        return _route_commit(team_root, index_path,
                             message=f"docs(memory): route remove {path}",
                             done_msg=f"teammode memory route remove — {path} 제거")

    print(f"[error] memory route: 알 수 없는 서브액션: {sub_action!r}. "
          f"upsert/remove 중 하나.", file=sys.stderr)
    return 2


def _route_commit(team_root: Path, index_path: Path,
                  message: str, done_msg: str) -> int:
    """루트 라우팅 맵 변경 후 do_commit([memory/INDEX.md], push=True) — memory write 미러.

    push 실패는 비차단(로컬 커밋 보존, 경고만). 단일 파일 변경이라 부분 실패 없음.
    """
    commit_result = _git_ops.do_commit(
        str(team_root),
        message=message,
        push=True,
        paths=[str(index_path)],
    )
    # 정상 케이스(ok=False 여도 경고 불필요): 멱등·빈 경로·git 없는 환경.
    _COMMIT_SILENT_DETAILS = ("nothing to commit", "no paths to stage",
                              "not a git work tree")
    commit_failed = (not commit_result.ok
                     and commit_result.detail not in _COMMIT_SILENT_DETAILS)

    if commit_failed:
        print(f"[warning] memory route: 커밋 실패 — {commit_result.detail}",
              file=sys.stderr)
        print(f"{done_msg}(커밋 안 됨)")
        return 1

    # push 실패는 비차단: 로컬 커밋·맵 변경은 유지, 경고만 출력(RC=0).
    if commit_result.committed and not commit_result.pushed:
        print(f"[warning] memory route: push 실패(로컬 커밋은 유지) — "
              f"{commit_result.detail}", file=sys.stderr)

    print(done_msg)
    return 0


def cmd_pull(team_root: Path) -> int:
    """팀 레포를 `git pull --ff-only`로 최신화 — git_ops 공통 안전장치 재사용(V.3).

    auto_pull 과 같은 do_pull(손자 killpg·ff-only·타임아웃·자격증명 차단)을 쓴다. 실패는
    비치명(우아한 축소): git 아님·오프라인·ff불가·타임아웃 → exit 1 + 안내, 크래시 없음.
    엔진은 절대 워킹트리를 오염시키지 않는다(ff-only).
    """
    result = _git_ops.do_pull(str(team_root))
    if result.ok:
        print(f"tm-mode pull — 최신화됨: {result.detail or 'up-to-date'}")
        return 0
    # 비치명: 작업을 막지 않되, 무엇이 안 됐는지 알린다(스킬/사람이 판단).
    print(f"tm-mode pull — 건너뜀(비치명): {result.detail}", file=sys.stderr)
    return 1


def cmd_commit(team_root: Path, message: str, push: bool,
               paths: list | None = None) -> int:
    """git add/commit/(push) 묶음 — git_ops 공통 안전장치 재사용(V.4).

    실패 무해(우아한 축소): 변경 없음·git 아님·push 실패 모두 비치명. push 실패는
    로컬 커밋을 되돌리지 않는다(커밋 보존). exit code 로 결과를 구분하되 크래시 0.

    paths: 스테이징 범위 한정 경로 목록. None이면 git add -A(전체), 지정하면 해당
    경로만 stage(세션로그 단독 커밋 등 안전 모드). do_commit 의 paths 인자로 그대로 전달.
    """
    result = _git_ops.do_commit(str(team_root), message=message, push=push, paths=paths)
    if result.ok:
        suffix = " (pushed)" if result.pushed else (
            " (push 실패·커밋은 보존)" if push else "")
        print(f"tm-mode commit — 커밋됨{suffix}: {result.detail}")
        return 0
    # 변경 없음/git 아님 등 — 비치명. 작업을 막지 않되 사유를 알린다.
    print(f"tm-mode commit — 건너뜀(비치명): {result.detail}", file=sys.stderr)
    return 1


def _is_session_log_name(stem: str) -> bool:
    """세션로그 네임스페이스 = YYYY-MM-DD 로 시작하는 .md (스펙 01 §2.1)."""
    return len(stem) >= 10 and stem[:4].isdigit() and stem[4] == "-" and stem[7] == "-"


def _parse_frontmatter(text: str) -> dict:
    """세션로그 frontmatter(--- ... ---)를 단순 파싱. 요약 안 함 — 값만 그대로 추출.

    구로그(summary 없음)·frontmatter 없음도 안전하게 처리(빈 dict). 스펙 §3.3.
    """
    fm: dict = {}
    if not text.startswith("---"):
        return fm
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _collect_members(team_root: Path) -> list:
    """멤버별 가장 최근 작업일 세션로그 1파일을 수집 (스펙 §4.1 기본 단위).

    엔진은 요약하지 않는다 — frontmatter 의 summary/date 를 그대로 옮길 뿐.
    summary 없는 구로그는 summary 를 빈 문자열로 둔다(전문 폴백 금지, §4.1).
    """
    sessions = team_root / "memory" / "team" / "sessions"
    members: list = []
    if not sessions.is_dir():
        return members
    for member_dir in sorted(p for p in sessions.iterdir() if p.is_dir()):
        logs = [p for p in member_dir.glob("*.md") if _is_session_log_name(p.stem)]
        if not logs:
            continue  # 로그 0개 멤버(보조파일만/빈 디렉토리)는 건너뜀
        # 파일명(=작업일 YYYY-MM-DD)으로 최근 1파일 선택. 사전식 정렬 = 날짜 정렬.
        latest = max(logs, key=lambda p: p.stem)
        try:
            text = latest.read_text(encoding="utf-8")
        except OSError:
            text = ""
        fm = _parse_frontmatter(text)
        members.append({
            "author": member_dir.name,
            "date": fm.get("date", latest.stem),
            "summary": fm.get("summary", ""),
            "file": str(latest.relative_to(team_root)),
        })
    return members


def _sanitize_line(text: str) -> str:
    """한 줄 표기용 새니타이즈 — 개행·제어문자를 공백으로 치환(P2-1 방어 이중화).

    role 같은 자유문자열을 context 텍스트 라인에 박을 때, `\\n`/`\\r`/널 등 control char 가
    남아 있으면 멤버 라인을 쪼개 가짜 라인(`- FAKE [...] summary: ...`)을 주입할 수 있다.
    어휘(한글·공백)는 보존하고 라인 구조를 깨는 문자만 공백으로 바꾼다.
    """
    return "".join(" " if (c == "\x7f" or ord(c) < 0x20) else c for c in str(text))


def _member_roles(team_root: Path) -> dict:
    """team.config.json 의 members 배열 → {name: role} (A2.3 — context 역할 표시).

    config 읽기는 비치명: 부재·파싱실패·타입불일치·members 키 없음이면 빈 dict.
    엔진은 추측·검증하지 않는다 — 형식이 이상하면 그 엔트리만 건너뛴다(context 동사는
    role 판정과 무관, 출력 보강일 뿐). 어떤 예외도 삼킨다.
    """
    try:
        cfg_path = team_root / "team.config.json"
        if not cfg_path.is_file():
            return {}
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return {}
        members = cfg.get("members")
        if not isinstance(members, list):
            return {}
        roles = {}
        for entry in members:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            role = entry.get("role")
            if isinstance(name, str) and isinstance(role, str) and role.strip():
                roles[name] = role
        return roles
    except Exception:  # noqa: BLE001 — config 읽기는 context 수집을 막지 않는다
        return {}


def _read_index(team_root: Path) -> str:
    """INDEX.md 내용을 그대로 읽는다(없으면 빈 문자열). 스펙 §2.1 단일 진입점."""
    index = team_root / "memory" / "INDEX.md"
    if index.is_file():
        try:
            return index.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def cmd_context(team_root: Path, as_json: bool) -> int:
    """전원 세션로그·INDEX·상태를 긁어 구조화 출력 — 기계적 수집(요약은 스킬 몫).

    텍스트 모드: 사람/에이전트가 읽는 섹션 구조(INDEX / 상태 / 멤버별 summary).
    JSON 모드(--json): 스킬이 파싱하는 구조화 데이터.
    """
    index_text = _read_index(team_root)
    members = _collect_members(team_root)
    # config.members 의 role 을 author 로 매칭해 보강 (A2.3 — 죽은필드 방지).
    # role 없는 멤버(config 미등재 or role 생략)는 role=None — 출력에서 생략된다.
    roles = _member_roles(team_root)
    for m in members:
        m["role"] = roles.get(m["author"])
    active = (team_root / ".teammode-active").exists()
    state = "on (active)" if active else "off"

    if as_json:
        print(json.dumps({
            "state": "on" if active else "off",
            "index": index_text,
            "members": members,
            "personality_customized": _personality_customized(team_root),
        }, ensure_ascii=False))
        return 0

    lines = ["=== tm-mode context ===", f"state: {state}", "", "--- INDEX ---"]
    lines.append(index_text.rstrip() if index_text else "(INDEX.md 없음)")
    lines.append("")
    lines.append("--- members (멤버별 최근 작업일 1파일 summary) ---")
    if members:
        for m in members:
            summ = m["summary"] if m["summary"] else "(summary 없음 — 구로그)"
            # role 있으면 "이름(role)" 표기 (A2.3) — 없으면 이름만(무회귀).
            # role 을 한 줄로 새니타이즈(개행·제어문자 → 공백): config 검증을 우회한
            # role 이라도 텍스트 출력에서 가짜 멤버 라인을 주입하지 못하게 방어 이중화(P2-1).
            who = (f"{m['author']}({_sanitize_line(m['role'])})"
                   if m.get("role") else m["author"])
            lines.append(f"- {who} [{m['date']}] summary: {summ}")
            lines.append(f"    file: {m['file']}")
    else:
        lines.append("(세션로그 없음 — summary 수집 대상 0)")
    print("\n".join(lines))
    return 0


# issue 동사가 받는 정규 입력 스키마 필드 — argv 의 동사별 플래그를 정규 어휘로 모은다.
# (action_map 해석·페이로드 변환은 여기서 절대 하지 않는다 — 어댑터/스킬 몫, §3 "엔진은
#  판단 안 함". 엔진은 입력을 정규 스키마로 정리해 echo 할 뿐이다 — B-4 altitude.)
_ISSUE_INPUT_FLAGS = ("title", "body", "assignee", "label", "priority")


def _resolve_issue_provider(team_root: Path):
    """team.config.json 의 issues 슬롯 연결 provider 를 확인한다(context 동사와 같은 altitude).

    반환: (provider_name | None). 슬롯 부재·config 부재·provider 미지(providers/ 에 팩
    없음) → None(=빈 슬롯). 엔진은 추측하지 않는다 — 팩이 없으면 연결로 보지 않는다.
    어떤 예외도 None 으로 흡수한다(조회는 비치명 — issue 동사를 크래시시키지 않음).
    """
    try:
        cfg_path = team_root / "team.config.json"
        if not cfg_path.is_file():
            return None
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return None
        services = cfg.get("services")
        if not isinstance(services, dict):
            return None
        slot = services.get("issues")
        if not isinstance(slot, dict):
            return None
        provider = slot.get("provider")
        if not isinstance(provider, str) or not provider:
            return None
        # providers/ 에 해당 팩이 실재해야 연결로 인정(미지 provider 추측 금지).
        if _providers.lookup(provider) is None:
            return None
        return provider
    except Exception:  # noqa: BLE001 — 슬롯 조회는 issue 동사를 막지 않는다
        return None


def cmd_issue(team_root: Path, action: str | None, fields: dict) -> int:
    """issues 슬롯 연결을 확인하고 정규 입력 스키마를 stdout JSON 으로 echo 한다 — B-4.

    altitude(context 동사와 동일): issues 슬롯 provider 확인 후 **정규 입력 스키마 echo
    까지만**. action_map 해석·페이로드 변환·실 MCP 호출은 절대 하지 않는다(어댑터/스킬
    몫 — §3 "엔진은 판단 안 함"). 빈 슬롯이면 `[info]` + exit 0(비치명). 연결 슬롯이면
    정규 스키마 echo + exit 0.

    인젝션 면역(V.4 회귀락 계승): 사용자 텍스트는 json.dumps 로만 직렬화한다 — 셸/JSON
    인젝션이 일어나지 않는다(엔진은 페이로드를 셸·다른 JSON 문맥에 보간하지 않음).
    """
    provider = _resolve_issue_provider(team_root)
    # 정규 입력 스키마(echo 대상). action 은 첫 positional(예: create), 나머지는 정규
    # 어휘 필드. 엔진은 이 스키마를 해석하지 않는다 — 그대로 정리해 내보낸다.
    schema = {
        "verb": "issue",
        "action": action,
        "service": "issues",
        "provider": provider,
        "input": {k: v for k, v in fields.items() if v is not None},
    }
    if provider is None:
        # 빈 슬롯 = 1급 시민(§7.2). 비치명 안내 후 exit 0 — 작업을 막지 않는다.
        print("[info] issues 슬롯이 연결돼 있지 않습니다. "
              "team.config.json 의 services.issues 를 연결하세요(tm-connect).")
        return 0
    # 연결 슬롯: 정규 입력 스키마를 JSON 으로 echo(action_map 변환 없음 — 어댑터/스킬 몫).
    print(json.dumps(schema, ensure_ascii=False))
    return 0


def _parse_args(argv):
    """argv → (verb, opts dict). 알 수 없는 플래그는 무시(후속 슬라이스 확장 여지).

    의도적으로 argparse 대신 손파싱한다 — `--root`/`--settings` 부재를 동사별 정책
    메시지로 명확히 다루기 위함(특히 정책 A 에러 문구 일관성). 동사별 추가 플래그
    (--author/--text/--now 등)도 같은 통로로 모은다.
    """
    verb = None
    opts: dict = {"install": False, "json": False, "push": False,
                  "dry_run": False, "positionals": []}
    it = iter(argv)
    for a in it:
        if a in _VALUE_FLAGS:
            opts[a.lstrip("-")] = next(it, None)
        elif a == "--install":
            opts["install"] = True
        elif a == "--json":
            opts["json"] = True
        elif a == "--push":
            opts["push"] = True
        elif a == "--dry-run":
            opts["dry_run"] = True
        elif not a.startswith("-"):
            # 첫 non-flag = verb, 이후 non-flag = positional(서브액션 등). 하니스가
            # `issue --root <root> create …` 처럼 --root 를 verb 와 서브액션 사이에
            # 끼워도 정상 파싱된다 — value 플래그는 위에서 토큰쌍으로 소비되므로 그
            # 다음의 `create` 가 positional 로 남는다(P0-1).
            if verb is None:
                verb = a
            else:
                opts["positionals"].append(a)
        # 그 외 토큰(알 수 없는 부울 플래그 등)은 무시
    return verb, opts


def _resolve_settings(settings_path, install) -> str:
    """settings 경로를 명시 인자에서만 해석한다(P2).

    --settings 지정 → 그 경로(격리 모드). --install → 실설치(~/.claude/settings.json).
    둘 다 없으면 None 반환 → 호출부가 거부한다(실 ~/.claude 추측 오염 방지).
    """
    if settings_path is not None:
        return settings_path
    if install:
        return os.path.expanduser("~/.claude/settings.json")
    return None


def _parse_now(now_str):
    """--now ISO8601 문자열을 datetime 으로. 미지정/파싱실패 시 실시각(KST)."""
    if now_str:
        try:
            return datetime.fromisoformat(now_str)
        except ValueError:
            pass
    return _workday.now_kst()


# settings(어댑터 sync)를 필요로 하는 동사 — on/off 만. log/context 등 메모리/조회
# 동사는 ~/.claude 를 건드리지 않으므로 settings 요구가 무의미하다.
_SETTINGS_VERBS = ("on", "off")
_KNOWN_VERBS = ("on", "off", "log", "context", "pull", "commit", "update", "issue",
                "util", "memory")


def main(argv=None) -> int:
    # 한글 출력(에러 메시지·context json)이 비-UTF8 stdout(Windows cp949)에서 크래시하지
    # 않도록 진입 즉시 UTF-8 보장. Linux/macOS·테스트 캡처엔 무영향(io_encoding 참조).
    ensure_utf8_io()
    argv = list(sys.argv[1:] if argv is None else argv)
    verb, opts = _parse_args(argv)

    if verb not in _KNOWN_VERBS:
        if verb is None:
            print("usage: teammode.py {on|off|log|context|pull|commit|update|issue} "
                  "--root <팀루트> ...", file=sys.stderr)
            return 2
        # 미구현 동사 — 후속 슬라이스 (시나리오 RED 유지)
        print(f"[unimplemented] {verb}", file=sys.stderr)
        return 127

    # 정책 A: 팀 루트는 명시 인자 --root 로만. env 폴백·cwd 추측 금지 (P1-a).
    root = opts.get("root")
    if root is None:
        print("[error] --root <팀루트> 가 필수입니다. 엔진은 환경변수(TEAMMODE_HOME)를 "
              "읽지 않으며 작업 폴더를 추측하지 않습니다.", file=sys.stderr)
        return 2
    team_root = Path(root).resolve()

    if verb == "log":
        author = opts.get("author")
        text = opts.get("text")
        if author is None:
            print("[error] log: --author <이름> 가 필요합니다.", file=sys.stderr)
            return 2
        if text is None:
            print("[error] log: --text <내용> 가 필요합니다.", file=sys.stderr)
            return 2
        return cmd_log(team_root, author, text, _parse_now(opts.get("now")))

    if verb == "context":
        return cmd_context(team_root, opts["json"])

    if verb == "pull":
        return cmd_pull(team_root)

    if verb == "commit":
        message = opts.get("message")
        if not message:
            print("[error] commit: --message <메시지> 가 필요합니다.", file=sys.stderr)
            return 2
        # --paths "memory/ docs/" 형태(공백 구분 문자열)를 리스트로 분리.
        # 미지정 시 None → do_commit 이 add -A(전체 워킹트리) 처리.
        paths_raw = opts.get("paths")
        paths = paths_raw.split() if paths_raw else None
        return cmd_commit(team_root, message, opts["push"], paths=paths)

    if verb == "update":
        return cmd_update(team_root, dry_run=opts["dry_run"])

    if verb == "issue":
        # 첫 positional = 서브액션(예: create). --root 가 verb 와 서브액션 사이에
        # 끼워져도 정상 파싱된다(P0-1). 정규 입력 필드(--title 등)를 schema 로 모은다.
        positionals = opts.get("positionals") or []
        action = positionals[0] if positionals else None
        fields = {f: opts.get(f) for f in _ISSUE_INPUT_FLAGS}
        return cmd_issue(team_root, action, fields)

    if verb == "util":
        positionals = opts.get("positionals") or []
        action = positionals[0] if positionals else None
        # P0-1: util 즉시반영 심링크 경로 파생을 위해 settings_path 를 함께 전달.
        util_settings = _resolve_settings(opts.get("settings"), opts["install"])
        return cmd_util(team_root, action, opts.get("member"),
                        opts.get("skill"), skills_dir=opts.get("skills-dir"),
                        settings_path=util_settings, install=opts["install"])

    if verb == "memory":
        positionals = opts.get("positionals") or []
        action = positionals[0] if positionals else None
        # route 서브액션: 루트 2열 라우팅 맵 CRUD — 4열 folder-INDEX 경로와 분리.
        if action == "route":
            sub_action = positionals[1] if len(positionals) > 1 else None
            return cmd_route(
                team_root,
                sub_action=sub_action,
                path=opts.get("path"),
                desc=opts.get("desc"),
                author=opts.get("author"),
            )
        return cmd_knowledge(
            team_root,
            action=action,
            folder=opts.get("folder"),
            filename=opts.get("filename"),
            content=opts.get("content"),
            author=opts.get("author"),
            weight=opts.get("weight"),
            rel_path=opts.get("path"),
            date_str=opts.get("date"),
        )

    # on/off: P2 settings 경로도 명시로만. 둘 다 없으면 실 ~/.claude 추측 오염 거부.
    resolved_settings = _resolve_settings(opts.get("settings"), opts["install"])
    if resolved_settings is None:
        print("[error] --settings <경로> (격리 모드) 또는 --install (실설치) 중 "
              "하나가 필요합니다. 명시 없이 실 ~/.claude/settings.json 에 쓰지 않습니다.",
              file=sys.stderr)
        return 2

    if verb == "on":
        member = opts.get("member")
        if member is not None:
            err = _validate_author(member)
            if err is not None:
                print(f"[error] --member: {err}", file=sys.stderr)
                return 2
        return cmd_on(team_root, resolved_settings, member=member,
                      skills_dir=opts.get("skills-dir"), install=opts["install"])
    return cmd_off(team_root, resolved_settings, member=opts.get("member"),
                   skills_dir=opts.get("skills-dir"), install=opts["install"])


if __name__ == "__main__":
    raise SystemExit(main())
