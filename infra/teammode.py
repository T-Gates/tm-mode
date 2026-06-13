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


def _active_marker(team_root: Path) -> Path:
    return team_root / ".acme-active"


def _banner_file(team_root: Path) -> Path:
    return team_root / "memory" / "banner.txt"


def _adapter(settings_path=None):
    import runpy
    mod = runpy.run_path(str(INFRA / "agents" / "claude" / "adapter.py"),
                         run_name="__teammode_engine__")
    Adapter = mod["Adapter"]
    return Adapter(
        agent_dir=str(INFRA / "agents" / "claude"),
        manifest_path=str(INFRA / "hooks" / "manifest.json"),
        settings_path=settings_path or os.path.expanduser("~/.claude/settings.json"),
        # 어댑터의 team_root = 설치 위치(normalize.py 소유 마커 기준). 메모리 쓰기의
        # 팀 루트(_team_root, cwd)와는 별개 축이다.
        team_root=str(INFRA.parent),
    )


def _render_banner(team_root: Path) -> str:
    """배너 캐시를 읽거나, 없으면 팀 이름 기반 최소 배너를 생성·캐시한다(§11.5)."""
    banner_file = _banner_file(team_root)
    if banner_file.is_file():
        return banner_file.read_text(encoding="utf-8")
    team_name = os.environ.get("ACME_TEAM_NAME", "acme")
    banner = f"=== {team_name} team mode ON ===\n"
    banner_file.parent.mkdir(parents=True, exist_ok=True)
    banner_file.write_text(banner, encoding="utf-8")
    return banner


def cmd_on(team_root: Path, settings_path: str) -> int:
    print(_render_banner(team_root), end="")
    _adapter(settings_path).sync(mode="on")
    _active_marker(team_root).write_text("", encoding="utf-8")
    return 0


def cmd_off(team_root: Path, settings_path: str) -> int:
    _adapter(settings_path).sync(mode="off")
    marker = _active_marker(team_root)
    if marker.exists():
        marker.unlink()
    print("teammode off — 상태 저장됨")
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


# 값을 받는 옵션 플래그 화이트리스트. 여기 없는 `--flag` 는 부울/무시로 다룬다 —
# 알 수 없는 플래그의 다음 토큰을 값으로 삼키지 않게 해 verb 손실을 막는다.
_VALUE_FLAGS = ("--root", "--settings", "--author", "--text", "--now", "--message")


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


def cmd_commit(team_root: Path, message: str, push: bool) -> int:
    """git add/commit/(push) 묶음 — git_ops 공통 안전장치 재사용(V.4).

    실패 무해(우아한 축소): 변경 없음·git 아님·push 실패 모두 비치명. push 실패는
    로컬 커밋을 되돌리지 않는다(커밋 보존). exit code 로 결과를 구분하되 크래시 0.
    """
    result = _git_ops.do_commit(str(team_root), message=message, push=push)
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
    active = (team_root / ".acme-active").exists()
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
            lines.append(f"- {m['author']} [{m['date']}] summary: {summ}")
            lines.append(f"    file: {m['file']}")
    else:
        lines.append("(세션로그 없음 — summary 수집 대상 0)")
    print("\n".join(lines))
    return 0


def _parse_args(argv):
    """argv → (verb, opts dict). 알 수 없는 플래그는 무시(후속 슬라이스 확장 여지).

    의도적으로 argparse 대신 손파싱한다 — `--root`/`--settings` 부재를 동사별 정책
    메시지로 명확히 다루기 위함(특히 정책 A 에러 문구 일관성). 동사별 추가 플래그
    (--author/--text/--now 등)도 같은 통로로 모은다.
    """
    verb = None
    opts: dict = {"install": False, "json": False, "push": False}
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
        elif verb is None and not a.startswith("-"):
            verb = a
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
_KNOWN_VERBS = ("on", "off", "log", "context", "pull", "commit")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verb, opts = _parse_args(argv)

    if verb not in _KNOWN_VERBS:
        if verb is None:
            print("usage: teammode.py {on|off|log} --root <팀루트> ...",
                  file=sys.stderr)
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
        return cmd_commit(team_root, message, opts["push"])

    # on/off: P2 settings 경로도 명시로만. 둘 다 없으면 실 ~/.claude 추측 오염 거부.
    resolved_settings = _resolve_settings(opts.get("settings"), opts["install"])
    if resolved_settings is None:
        print("[error] --settings <경로> (격리 모드) 또는 --install (실설치) 중 "
              "하나가 필요합니다. 명시 없이 실 ~/.claude/settings.json 에 쓰지 않습니다.",
              file=sys.stderr)
        return 2

    if verb == "on":
        return cmd_on(team_root, resolved_settings)
    return cmd_off(team_root, resolved_settings)


if __name__ == "__main__":
    raise SystemExit(main())
