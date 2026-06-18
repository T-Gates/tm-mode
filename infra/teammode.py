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
    return team_root / ".tgates-active"


def _banner_file(team_root: Path) -> Path:
    return team_root / "memory" / "banner.txt"


def _adapter(settings_path=None, skills_dir=None):
    import runpy
    mod = runpy.run_path(str(INFRA / "agents" / "claude" / "adapter.py"),
                         run_name="__teammode_engine__")
    Adapter = mod["Adapter"]
    resolved_settings = settings_path or os.path.expanduser("~/.claude/settings.json")
    # skills_dir 격리 파생(P0-1):
    #   명시 주입이 없으면 settings_path 의 부모 디렉토리 아래 "skills" 를 사용한다.
    #   규칙: <settings_path 부모>/skills
    #   예) --settings /tmp/x/settings.json → skills_dir=/tmp/x/skills  (격리 자동)
    #       실설치(~/.claude/settings.json) → skills_dir=~/.claude/skills (실호스트)
    #   이 파생으로 `--settings <tmp>` 만 줘도 실호스트 ~/.claude/skills 무접촉.
    if skills_dir is None:
        skills_dir = str(Path(resolved_settings).parent / "skills")
    return Adapter(
        agent_dir=str(INFRA / "agents" / "claude"),
        manifest_path=str(INFRA / "hooks" / "manifest.json"),
        settings_path=resolved_settings,
        # 어댑터의 team_root = 설치 위치(normalize.py 소유 마커 기준). 메모리 쓰기의
        # 팀 루트(_team_root, cwd)와는 별개 축이다.
        team_root=str(INFRA.parent),
        skills_dir=skills_dir,
    )


def _render_banner(team_root: Path) -> str:
    """배너 캐시를 읽거나, 없으면 팀 이름 기반 최소 배너를 생성·캐시한다(§11.5)."""
    banner_file = _banner_file(team_root)
    if banner_file.is_file():
        return banner_file.read_text(encoding="utf-8")
    team_name = os.environ.get("TGATES_TEAM_NAME", "tgates")
    banner = f"=== {team_name} team mode ON ===\n"
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
      - push 자동 절대 금지.
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

        # 변경 있음: paths 한정 자동 커밋(push 절대 금지)
        commit_res = _git_ops.do_commit(
            str(team_root),
            message="chore: sync teammode engine from upstream [auto]",
            push=False,
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
           skills_dir: str | None = None) -> int:
    print(_render_banner(team_root), end="")
    # 시작 멘트(greeting): 배너 직후, config 에 있으면 출력(없으면 미출력 — §3.1).
    greeting = _read_team_field(team_root, "greeting")
    if greeting:
        print(greeting)
    # D: upstream 자동 동기화(fetch + 변경 시 자동 커밋). 실패는 on 을 막지 않는다.
    # 순서: auto_update 먼저 → 그 다음 심링크 토글(새 core 스킬 반영 위해).
    auto_update_on_start(team_root)
    adapter = _adapter(settings_path, skills_dir=skills_dir)
    adapter.sync(mode="on")
    _active_marker(team_root).write_text("", encoding="utf-8")
    # core 스킬 설치 (tm-context 등 — on 시 활성)
    adapter.install_skills(layer="core")
    # 멤버별 util 스킬 설치 (--member 지정 시)
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
            target = adapter.skills_dir / skill_name
            adapter._link_one_skill(src, target, layer="util")
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
        print(f"teammode update — 중단: {res.detail}.\n"
              f"  동기화 대상({paths})에 커밋 안 된 변경이 있습니다. 덮어쓰면 유실됩니다.\n"
              f"  먼저 변경을 커밋하거나 되돌린 뒤 다시 실행하세요(사람 판단 필요).",
              file=sys.stderr)
        if res.diff:
            print(res.diff, file=sys.stderr)
        return 1

    if not res.ok:
        # upstream 미등록·오프라인·git 아님 등 — 비치명. install 이 upstream 을 등록한다.
        print(f"teammode update — 건너뜀(비치명): {res.detail}.\n"
              f"  upstream remote 가 없으면 install.py 가 등록합니다. 수동 등록:\n"
              f"  git remote add {UPSTREAM_REMOTE} {_install_upstream_url()}",
              file=sys.stderr)
        return 1

    # dry-run: sync 가 changed=False(적용 스킵) + diff(있으면 채움)로 돌려주므로
    # changed 가 아니라 **diff 유무**로 분기한다. (changed 로 분기하면 변경이 있어도
    # "이미 최신"으로 잘못 출력되는 P2 버그가 난다 — 적대검수 발견.)
    if dry_run:
        if res.diff:
            print(f"teammode update [dry-run] — 동기화하면 바뀔 파일({paths}):")
            print(res.diff)
            print("  (미리보기만 — 실제 변경 없음. 적용하려면 --dry-run 빼고 다시 실행.)")
        else:
            print("teammode update [dry-run] — 이미 최신입니다(변경 없음).")
        return 0

    if not res.changed:
        print("teammode update — 이미 최신입니다.")
        return 0

    # 적용됨(staged) — 무엇이 바뀌었나 사람이 읽는 요약. push·commit 안 함.
    print(f"teammode update — 엔진 파일 동기화 완료({paths}, staged). 바뀐 파일:")
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
        return "https://github.com/T-Gates/teammode.git"


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
            skills_dir: str | None = None) -> int:
    adapter = _adapter(settings_path, skills_dir=skills_dir)
    adapter.sync(mode="off")
    marker = _active_marker(team_root)
    if marker.exists():
        marker.unlink()
    # core/util 스킬 제거
    _uninstall_layer(adapter, "core")
    _uninstall_layer(adapter, "util")
    # 끝맺음 말(farewell): config 에 있으면 그걸, 없으면 "상태 저장됨" 폴백(§3.1).
    farewell = _read_team_field(team_root, "farewell")
    print(farewell if farewell else "teammode off — 상태 저장됨")
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
        # 첫 기록: frontmatter + 첫 항목. summary 는 text 첫 줄(100자)로 초기화.
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        summary = first_line[:100]
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(_frontmatter(author, date_str, summary))
            f.write(entry)

    print(f"teammode log — {author}/{date_str}.md 기록됨")
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
             settings_path: str | None = None) -> int:
    """util 동사 — util 스킬 목록 관리 (list/add/remove).

    엔진 기계역할: json 갱신 + 즉시 심링크 반영(on 상태면). 판단은 스킬(tm-manage-utils) 몫.
    settings_path: 즉시반영 심링크 경로 파생에 쓴다(P0-1). None → 실호스트 폴백.
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
                    the_skills_dir = Path(skills_dir) if skills_dir else None
                    # settings_path 전달 → _adapter 내부에서 skills_dir 격리 파생.
                    adapter = _adapter(settings_path, skills_dir=the_skills_dir)
                    target = adapter.skills_dir / skill_name
                    adapter._link_one_skill(skill_src, target, layer="util")
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
                    the_skills_dir = Path(skills_dir) if skills_dir else None
                    # settings_path 전달 → _adapter 내부에서 skills_dir 격리 파생.
                    adapter = _adapter(settings_path, skills_dir=the_skills_dir)
                    target = adapter.skills_dir / skill_name
                    if target.exists() or target.is_symlink():
                        if adapter._is_layer_skill(target, "util"):
                            adapter._remove_skill(target)
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
                # knowledge 동사 플래그
                "--folder", "--filename", "--content", "--weight", "--path", "--date")


# ──────────────────────────────────────────────────────────────────
# 작업 C — knowledge 동사 (기계 전담: frontmatter·파일 쓰기/삭제·INDEX 갱신·커밋)
# ──────────────────────────────────────────────────────────────────

# 지식 파일이 놓일 수 있는 허용 폴더 목록 (sessions/meeting 은 다른 경로)
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
            return (f"folder '{folder}' 는 지식 저장 대상이 아닙니다(훅/tm-context 관리 경로): "
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
    parts = norm_folder.split("/")
    for seg in parts:
        if seg in ("", ".", ".."):
            return f"folder 에 허용되지 않는 세그먼트: {seg!r} in {folder!r}"
        if not all(c.isalnum() or c in "-_" for c in seg):
            return f"folder 세그먼트에 허용되지 않는 문자: {seg!r}"

    # ── filename 검증 (P2: dead-code err 실제 반환) ───────────────
    if not filename:
        return "filename 이 비어 있습니다."
    if "/" in filename or "\\" in filename:
        return f"filename 에 경로 구분자가 포함될 수 없습니다: {filename!r}"
    if ".." in filename or filename.startswith("."):
        return f"filename 이 허용되지 않습니다: {filename!r}"
    # 공백·파이프·제어문자 거부 (P2: filename whitelist)
    for ch in filename:
        if ch in (" ", "\t", "|") or ord(ch) < 32:
            return f"filename 에 허용되지 않는 문자가 있습니다: {filename!r}"
    # kebab-case 검증 (확장자 제거 후)
    base = filename.removesuffix(".md") if filename.endswith(".md") else filename
    err = _validate_author(base)
    if err is not None:
        return f"filename 검증 실패: {err}"

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
    """지식 파일 frontmatter(4필드: created_at/updated_at/author/weight) 생성."""
    return (f"---\n"
            f"created_at: {created_at}\n"
            f"updated_at: {updated_at}\n"
            f"author: {author}\n"
            f"weight: {weight}\n"
            f"---\n")


def _parse_knowledge_frontmatter(text: str) -> dict:
    """지식 파일 frontmatter 파싱. 없거나 깨지면 빈 dict.

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
    index_path.write_text(content, encoding="utf-8")
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


def cmd_knowledge(team_root: Path, action: str | None,
                  folder: str | None, filename: str | None,
                  content: str | None, author: str | None,
                  weight: str | None, rel_path: str | None,
                  date_str: str | None) -> int:
    """knowledge 동사 — 지식 파일 write/delete (기계 전담).

    write:  frontmatter 스탬프 · 파일 write · folder INDEX 행 upsert · do_commit(paths).
    delete: 파일 삭제 · INDEX 행 제거 · do_commit(paths).

    엔진 불변:
      - weight 는 인자로만 받는다(추측 금지). 스킬이 사용자에게 확인한 값을 전달.
      - push 절대 금지(push=False 고정).
      - traversal 차단(_validate_knowledge_path).
      - 멱등: 같은 내용 재호출 → 변경 없음(커밋 안 생김).
      - 대상 범위: product/·team/·team/decisions/·soma/ 등 _KNOWLEDGE_ALLOWED_FOLDERS.
        sessions/·meeting/ 은 제외.
    """
    if action == "write":
        # 필수 인자 검증
        if not folder:
            print("[error] knowledge write: --folder 가 필요합니다.", file=sys.stderr)
            return 2
        if not filename:
            print("[error] knowledge write: --filename 이 필요합니다.", file=sys.stderr)
            return 2
        if content is None:
            print("[error] knowledge write: --content 가 필요합니다.", file=sys.stderr)
            return 2
        if not author:
            print("[error] knowledge write: --author 가 필요합니다.", file=sys.stderr)
            return 2
        if not weight:
            print("[error] knowledge write: --weight 가 필요합니다(추측 금지).", file=sys.stderr)
            return 2

        # weight 3-enum 검증 (P2)
        if weight not in _KNOWLEDGE_VALID_WEIGHTS:
            print(f"[error] knowledge write: --weight 는 {_KNOWLEDGE_VALID_WEIGHTS} 중 하나여야 합니다: {weight!r}",
                  file=sys.stderr)
            return 2

        # author traversal 가드
        err = _validate_author(author)
        if err is not None:
            print(f"[error] knowledge write: --author: {err}", file=sys.stderr)
            return 2

        # folder/filename traversal + containment 가드 + 허용 폴더 검증 (P1-1 포함)
        err = _validate_knowledge_path(team_root, folder, filename)
        if err is not None:
            print(f"[error] knowledge write: {err}", file=sys.stderr)
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
                print(f"teammode knowledge write — 변경 없음(멱등): {folder}/{filename}")
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
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(new_full, encoding="utf-8")
        except (OSError, PermissionError) as exc:
            print(f"[error] knowledge write: 파일 쓰기 실패 — {exc}", file=sys.stderr)
            return 2

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
        index_path = team_root / "memory" / folder / "INDEX.md"
        description = content.strip().splitlines()[0][:60] if content.strip() else filename
        try:
            _index_upsert(index_path, rel_for_git, weight, description, edit_date)
        except (OSError, PermissionError) as exc:
            print(f"[error] knowledge write: INDEX 갱신 실패 — {exc}", file=sys.stderr)
            return 2

        # do_commit(paths 한정, push=False) — P1-2: CommitResult 확인 + 실패 알림
        changed_paths = [str(target_path), str(index_path)]
        commit_result = _git_ops.do_commit(
            str(team_root),
            message=f"docs(memory): write {folder}/{filename}",
            push=False,
            paths=changed_paths,
        )
        # 정상 케이스(ok=False 여도 경고 불필요):
        #   "nothing to commit"  — 멱등(이미 커밋됨 혹은 변경 없음)
        #   "no paths to stage"  — 빈 경로 목록
        #   "not a git work tree"— git 없는 환경(파일 쓰기는 완료)
        # 그 외 ok=False → 실제 git 실패(add 실패·commit 실패·timeout 등) → 경고 + non-zero
        _COMMIT_SILENT_DETAILS = ("nothing to commit", "no paths to stage",
                                  "not a git work tree")
        if not commit_result.ok and commit_result.detail not in _COMMIT_SILENT_DETAILS:
            print(f"[warning] knowledge write: 커밋 실패 — {commit_result.detail}",
                  file=sys.stderr)
            print(f"teammode knowledge write — {folder}/{filename} 완료(커밋 안 됨)")
            return 1

        print(f"teammode knowledge write — {folder}/{filename} 완료")
        return 0

    if action == "delete":
        # 필수 인자 검증
        if not rel_path:
            print("[error] knowledge delete: --path <memory/상대경로> 가 필요합니다.",
                  file=sys.stderr)
            return 2
        if not author:
            print("[error] knowledge delete: --author 가 필요합니다.", file=sys.stderr)
            return 2

        # author traversal 가드
        err = _validate_author(author)
        if err is not None:
            print(f"[error] knowledge delete: --author: {err}", file=sys.stderr)
            return 2

        # .. 세그먼트 명시 차단 (early: resolve 전에)
        if ".." in rel_path:
            print(f"[error] knowledge delete: 경로에 '..' 이 포함될 수 없습니다: {rel_path!r}",
                  file=sys.stderr)
            return 2

        # ── P0: symlink 탈출 가드 ──────────────────────────────────
        real_root = team_root.resolve()
        memory_dir = (team_root / "memory").resolve()
        try:
            memory_dir.relative_to(real_root)
        except ValueError:
            print(f"[error] knowledge delete: memory/ 가 team_root 밖을 가리킵니다(심링크 탈출 차단)",
                  file=sys.stderr)
            return 2

        # rel_path 는 "memory/..." 형식일 수도 있고 "team/decisions/foo.md" 형식일 수도 있다.
        if rel_path.startswith("memory/"):
            candidate = (team_root / rel_path).resolve()
            rel_for_index = rel_path
            # 내부 folder 추출 (허용 폴더 검증용)
            inner = rel_path[len("memory/"):]
        else:
            candidate = (team_root / "memory" / rel_path).resolve()
            rel_for_index = "memory/" + rel_path
            inner = rel_path

        # ── P1-1: 허용 폴더 검증 (write 와 동일한 blocked/allowed 규칙) ─────
        # INDEX.md 자신(root) 삭제 거부 + blocked/allowed 폴더 검증
        filename_part = inner.split("/")[-1] if "/" in inner else inner
        folder_part = "/".join(inner.split("/")[:-1]) if "/" in inner else ""

        # INDEX.md 삭제 거부 (root-level INDEX 는 특히)
        if filename_part == "INDEX.md":
            print(f"[error] knowledge delete: INDEX.md 는 직접 삭제할 수 없습니다: {rel_path!r}",
                  file=sys.stderr)
            return 2

        # 허용 폴더 검증 — write 와 동일한 blocked/allowed 규칙 (P1-1)
        # folder_part 가 비어 있으면(root memory 파일) 허용 목록에 없으므로 거부
        norm_folder = folder_part.replace("\\", "/").rstrip("/") if folder_part else ""
        if not norm_folder:
            # memory/ 바로 아래 파일 — 허용 폴더 목록에 없음 → 거부
            print(f"[error] knowledge delete: 허용 폴더 하위의 파일만 삭제할 수 있습니다. "
                  f"허용: {', '.join(_KNOWLEDGE_ALLOWED_FOLDERS)}",
                  file=sys.stderr)
            return 2
        # 명시 차단 목록 먼저(blocked 우선 — write 와 동일 규칙)
        for bf in _KNOWLEDGE_BLOCKED_FOLDERS:
            if norm_folder == bf or norm_folder.startswith(bf + "/"):
                print(f"[error] knowledge delete: folder '{folder_part}' 는 삭제 대상이 아닙니다"
                      f"(훅/tm-context 관리 경로)",
                      file=sys.stderr)
                return 2
        del_allowed = False
        for af in _KNOWLEDGE_ALLOWED_FOLDERS:
            if norm_folder == af or norm_folder.startswith(af + "/"):
                del_allowed = True
                break
        if not del_allowed:
            print(f"[error] knowledge delete: folder '{folder_part}' 는 허용되지 않습니다. "
                  f"허용: {', '.join(_KNOWLEDGE_ALLOWED_FOLDERS)}",
                  file=sys.stderr)
            return 2

        # containment 가드
        try:
            candidate.relative_to(memory_dir)
        except ValueError:
            print(f"[error] knowledge delete: 경로가 memory/ 를 벗어납니다: {rel_path!r}",
                  file=sys.stderr)
            return 2

        target_path = candidate

        if not target_path.is_file():
            print(f"teammode knowledge delete — 파일 없음(멱등): {rel_path}")
            return 0

        # ── 파일 I/O (OSError/PermissionError → exit 2 + 친화 메시지) ─────────
        folder_path = target_path.parent
        index_path = folder_path / "INDEX.md"
        try:
            _index_remove_row(index_path, rel_for_index)
        except (OSError, PermissionError) as exc:
            print(f"[error] knowledge delete: INDEX 갱신 실패 — {exc}", file=sys.stderr)
            return 2

        try:
            target_path.unlink()
        except (OSError, PermissionError) as exc:
            print(f"[error] knowledge delete: 파일 삭제 실패 — {exc}", file=sys.stderr)
            return 2

        # do_commit(paths 한정, push=False) — P1-2: CommitResult 확인 + 실패 알림
        changed_paths = [str(target_path), str(index_path)]
        commit_result = _git_ops.do_commit(
            str(team_root),
            message=f"docs(memory): delete {rel_path}",
            push=False,
            paths=changed_paths,
        )
        # 정상 케이스(ok=False 여도 경고 불필요):
        #   "nothing to commit"   — 멱등
        #   "no paths to stage"   — 빈 경로 목록
        #   "not a git work tree" — git 없는 환경(파일 삭제는 완료)
        # 그 외 ok=False → 실제 git 실패(add 실패·commit 실패·timeout 등) → 경고 + non-zero
        _COMMIT_SILENT_DETAILS = ("nothing to commit", "no paths to stage",
                                  "not a git work tree")
        if not commit_result.ok and commit_result.detail not in _COMMIT_SILENT_DETAILS:
            print(f"[warning] knowledge delete: 커밋 실패 — {commit_result.detail}",
                  file=sys.stderr)
            print(f"teammode knowledge delete — {rel_path} 삭제됨(커밋 안 됨)")
            return 1

        print(f"teammode knowledge delete — {rel_path} 삭제됨")
        return 0

    print(f"[error] knowledge: 알 수 없는 action: {action!r}. write/delete 중 하나.",
          file=sys.stderr)
    return 2


def cmd_pull(team_root: Path) -> int:
    """팀 레포를 `git pull --ff-only`로 최신화 — git_ops 공통 안전장치 재사용(V.3).

    auto_pull 과 같은 do_pull(손자 killpg·ff-only·타임아웃·자격증명 차단)을 쓴다. 실패는
    비치명(우아한 축소): git 아님·오프라인·ff불가·타임아웃 → exit 1 + 안내, 크래시 없음.
    엔진은 절대 워킹트리를 오염시키지 않는다(ff-only).
    """
    result = _git_ops.do_pull(str(team_root))
    if result.ok:
        print(f"teammode pull — 최신화됨: {result.detail or 'up-to-date'}")
        return 0
    # 비치명: 작업을 막지 않되, 무엇이 안 됐는지 알린다(스킬/사람이 판단).
    print(f"teammode pull — 건너뜀(비치명): {result.detail}", file=sys.stderr)
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
        print(f"teammode commit — 커밋됨{suffix}: {result.detail}")
        return 0
    # 변경 없음/git 아님 등 — 비치명. 작업을 막지 않되 사유를 알린다.
    print(f"teammode commit — 건너뜀(비치명): {result.detail}", file=sys.stderr)
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
    active = (team_root / ".tgates-active").exists()
    state = "on (active)" if active else "off"

    if as_json:
        print(json.dumps({
            "state": "on" if active else "off",
            "index": index_text,
            "members": members,
        }, ensure_ascii=False))
        return 0

    lines = ["=== teammode context ===", f"state: {state}", "", "--- INDEX ---"]
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
                "util", "knowledge")


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
                        settings_path=util_settings)

    if verb == "knowledge":
        positionals = opts.get("positionals") or []
        action = positionals[0] if positionals else None
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
                      skills_dir=opts.get("skills-dir"))
    return cmd_off(team_root, resolved_settings, member=opts.get("member"),
                   skills_dir=opts.get("skills-dir"))


if __name__ == "__main__":
    raise SystemExit(main())
