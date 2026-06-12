#!/usr/bin/env python3
"""session-log-remind — 세션로그 갱신 리마인더 (공통 스크립트, 정규 스키마 전용).

스펙 02 §6: 이 스크립트는 **정규 입력 스키마(§6.1)만 인지**하며 특정 에이전트를
알지 못한다. normalize 심이 원어를 정규형으로 바꿔 stdin 으로 넘겨준다.

정규 입력(stdin):
  { "event": "UserPromptSubmit", "prompt": "...", "agent": "claude", "raw": {...} }

출력: Claude 의 additionalContext 형식 JSON(stdout) — 시각 + 세션로그 갱신 안내.
30분 이상 미갱신 또는 5프롬프트 주기마다 리마인드(스펙 01 §3.4 권장).
에이전트 무지를 유지하기 위해 출력은 시맨틱 안내문이며 mcp__·툴명 직표기 없음(§8.2).
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# auto_pull 은 같은 hooks/ 디렉토리의 형제 모듈 — 상시 레포 최신화(슬라이스 U).
try:
    import auto_pull as _auto_pull  # type: ignore
except ImportError:  # 모듈 부재여도 리마인드는 동작해야 한다(실패 무해)
    _auto_pull = None


def _team_root() -> str:
    """런타임 훅의 팀 루트 = 환경변수 TEAMMODE_HOME (없으면 cwd).

    ⚠️ 엔진(teammode.py)과 달리 이 스크립트는 env 를 읽는다 — 이유: 런타임 훅은
    에이전트 하니스가 발동하므로 `--root` CLI 인자를 받을 통로가 없다. 스펙 01 §1.2가
    "구현은 팀 루트를 가리키는 환경변수를 제공해야 한다(필수)"라고 명시하며, 이 변수가
    바로 그것이다. 엔진이 env 를 안 읽는 것(P1)은 의도적 호출(on/off)이 폴더를 추측하지
    않게 하기 위함이고, read-only 인 런타임 훅은 그 사고 표면이 아니다.
    """
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


def _pull_state_path() -> str:
    """마지막 auto-pull 시각 상태 파일 — **팀 루트 밖** 사용자 상태 디렉토리에 둔다.

    팀 루트(memory/ 등)를 오염시키지 않기 위해 $XDG_STATE_HOME 또는 ~/.teammode 사용.
    환경변수 미주입 시 합리적 기본값으로 폴백한다(런타임 훅은 인자 통로가 없으므로 env
    참조가 정당 — 엔진과 달리 read-only/상태격리 목적이라 P1 사고 표면 아님).
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "teammode", "last-pull")


def _maybe_auto_pull(team_root: str) -> None:
    """리마인드 판정 **이전에** 팀 레포를 최신화한다(최신 상태로 리마인드 판단).

    실패는 절대 작업을 막지 않는다(철칙) — auto_pull 은 예외를 전파하지 않으며, 여기서도
    어떤 예외도 삼킨다. 느린 네트워크는 auto_pull 내부 타임아웃으로 가드된다.
    """
    if _auto_pull is None:
        return
    try:
        throttle = int(os.environ.get("TEAMMODE_PULL_THROTTLE",
                                      _auto_pull.DEFAULT_THROTTLE_SECONDS))
        result = _auto_pull.auto_pull(
            team_root, _pull_state_path(), now=time.time(),
            throttle_seconds=throttle)
        for w in getattr(result, "warnings", []):
            sys.stderr.write(w + "\n")
    except Exception:  # noqa: BLE001 — 철칙: 무슨 일이 있어도 리마인드·작업을 막지 않는다
        pass


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # 공통 스크립트는 입력 오류로 세션을 막지 않는다

    if data.get("event") != "UserPromptSubmit":
        return 0

    root = _team_root()
    # 팀 모드 활성 시에만 동작
    if not os.path.isfile(os.path.join(root, ".acme-active")):
        return 0

    # 상시 레포 최신화 — 리마인드 판정 전에 최신화(실패 무해, 슬라이스 U).
    _maybe_auto_pull(root)

    sessions = glob.glob(
        os.path.join(root, "memory", "team", "sessions", "**", "*.md"),
        recursive=True)
    age = 9999
    if sessions:
        age = int(time.time() - max(os.path.getmtime(f) for f in sessions))

    # 프롬프트 카운터 (에이전트별 임시 파일)
    agent = data.get("agent", "unknown")
    counter_file = os.path.join(
        os.environ.get("TMPDIR", "/tmp"), f"teammode-prompt-counter-{agent}")
    try:
        count = int(open(counter_file).read().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1

    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    weekday = "월화수목금토일"[now.weekday()]
    time_line = (f"[teammode] 현재 시각: {now.strftime('%Y-%m-%d')}"
                 f"({weekday}) {now.strftime('%H:%M')} KST")

    base_guide = (
        " 세션 로그를 팀 루트의 memory/team/sessions/<이름>/ 에 기록하세요. "
        "<이름>은 members.md의 영문 이름($USER 아님). "
        "파일은 하루 하나(YYYY-MM-DD.md, -late 등 분리 금지), "
        "frontmatter(author/date/summary) 필수. "
        "날짜는 06시 컷 — 위 시각이 00~06시면 전날 파일, 06시 이후면 오늘 파일. "
        "현재 작업 레포의 ./memory/ 에는 쓰지 마세요. "
        "한 일뿐 아니라 근거·접은 대안·막힌 점·다음 단계까지 한 흐름으로. "
        "개인 내용 제외, 팀 작업만.")

    context = None
    if age >= 1800:
        context = time_line + "\n⛔ 세션 로그 30분 이상 미갱신. 첫 행동으로" + base_guide
        count = 0
    elif count >= 5:
        context = time_line + "\n" + base_guide.lstrip()
        count = 0

    with open(counter_file, "w") as f:
        f.write(str(count))

    if context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
