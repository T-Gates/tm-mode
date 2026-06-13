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

import os
import sys
from datetime import datetime
from pathlib import Path

INFRA = Path(__file__).resolve().parent       # 설치 위치 (manifest·adapter 소재)

# workday(06시컷) 순수 함수 — 같은 디렉토리 형제 모듈. drift 방지로 컷 계산을 단일 소스화.
sys.path.insert(0, str(INFRA))
import workday as _workday  # noqa: E402


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
_VALUE_FLAGS = ("--root", "--settings", "--author", "--text", "--now")


def _parse_args(argv):
    """argv → (verb, opts dict). 알 수 없는 플래그는 무시(후속 슬라이스 확장 여지).

    의도적으로 argparse 대신 손파싱한다 — `--root`/`--settings` 부재를 동사별 정책
    메시지로 명확히 다루기 위함(특히 정책 A 에러 문구 일관성). 동사별 추가 플래그
    (--author/--text/--now 등)도 같은 통로로 모은다.
    """
    verb = None
    opts: dict = {"install": False}
    it = iter(argv)
    for a in it:
        if a in _VALUE_FLAGS:
            opts[a.lstrip("-")] = next(it, None)
        elif a == "--install":
            opts["install"] = True
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
_KNOWN_VERBS = ("on", "off", "log")


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
