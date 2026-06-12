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


def _team_root() -> str:
    return os.environ.get("TGATES_HOME", os.getcwd())


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # 공통 스크립트는 입력 오류로 세션을 막지 않는다

    if data.get("event") != "UserPromptSubmit":
        return 0

    root = _team_root()
    # 팀 모드 활성 시에만 동작
    if not os.path.isfile(os.path.join(root, ".tgates-active")):
        return 0

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
