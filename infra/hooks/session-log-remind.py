#!/usr/bin/env python3
"""session-log-remind — 세션로그 갱신 리마인더 (공통 스크립트, 정규 스키마 전용).

스펙 02 §6: 이 스크립트는 **정규 입력 스키마(§6.1)만 인지**하며 특정 에이전트를
알지 못한다. normalize 심이 원어를 정규형으로 바꿔 stdin 으로 넘겨준다.

정규 입력(stdin):
  { "event": "UserPromptSubmit", "prompt": "...", "agent": "claude", "raw": {...} }

출력: 평문 stdout print — 시각(KST) + count + 세션로그 갱신 안내.
30분 이상 미갱신 또는 5프롬프트마다 리마인드(스펙 01 §3.4 권장).

멤버 식별(B-순위):
  1. team.config.json members 가 1명 → members[0]["name"]
  2. len > 1 → env TEAMMODE_MEMBER
  3. 둘 다 불가 → 전역 sessions 폴백(기존 동작 유지, degraded)

상태파일: {count, last_mtime, date, last_strong_remind} JSON — 내 세션로그 mtime 추적.
check_reset: 내 파일 mtime 변화 또는 날짜(06시 컷 기준) 바뀜 → count=0 + return(안 보챔).
강발화 throttle: last_strong_remind 기준 1800초 미만이면 age≥1800 조건이라도 강발화 스킵.

에이전트 무지를 유지하기 위해 출력은 시맨틱 안내문이며 mcp__·툴명 직표기 없음(§8.2).

⚠️ 레포 최신화는 더 이상 여기서(매 프롬프트) 하지 않는다 — 의도가 "상시 최신화"에서
"세션 시작 1회"로 바뀌었다(2026-06-17, P0 hook hang). UserPromptSubmit 은 동기 블로킹
훅이라 매 프롬프트 git pull 이 hang 시 작업을 막는 트리거였다. 세션당 1회 pull 은
session-start.py(SessionStart)가 담당하고, 세션 중 최신화는 `teammode pull` 수동이다.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

try:
    from pathlib import Path as _Path
    _infra = str(_Path(__file__).resolve().parent.parent)
    if _infra not in sys.path:
        sys.path.insert(0, _infra)
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 훅은 동작(보정만 스킵)
        return

# workday 단일소스 재사용 (06시 컷 계산)
try:
    from workday import workday_str as _workday_str  # type: ignore
    _HAS_WORKDAY = True
except ImportError:
    _HAS_WORKDAY = False


def _team_root() -> str:
    """런타임 훅의 팀 루트 = 환경변수 TEAMMODE_HOME (없으면 cwd).

    ⚠️ 엔진(teammode.py)과 달리 이 스크립트는 env 를 읽는다 — 이유: 런타임 훅은
    에이전트 하니스가 발동하므로 `--root` CLI 인자를 받을 통로가 없다. 스펙 01 §1.2가
    "구현은 팀 루트를 가리키는 환경변수를 제공해야 한다(필수)"라고 명시하며, 이 변수가
    바로 그것이다. 엔진이 env 를 안 읽는 것(P1)은 의도적 호출(on/off)이 폴더를 추측하지
    않게 하기 위함이고, read-only 인 런타임 훅은 그 사고 표면이 아니다.
    """
    return os.environ.get("TEAMMODE_HOME", os.getcwd())


def _kst_now() -> datetime:
    KST = timezone(timedelta(hours=9))
    return datetime.now(KST)


def _log_date(now: datetime) -> str:
    """06시 컷 기준 작업 날짜 문자열 반환 (workday.py 단일소스 위임).

    00:00~05:59(KST) → 전날 날짜, 06:00 이후 → 오늘 날짜.
    """
    if _HAS_WORKDAY:
        return _workday_str(now)
    # workday.py 임포트 실패 시 인라인 폴백 (동일 로직)
    KST = timezone(timedelta(hours=9))
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    if now.hour < 6:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def _load_team_config(root: str) -> dict:
    """팀 루트의 team.config.json 로드. 없거나 파싱 실패 시 빈 dict."""
    try:
        path = os.path.join(root, "team.config.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_member(root: str) -> str | None:
    """멤버 이름 결정 (B-순위).

    1. team.config.json members 가 1명 → members[0]["name"]
    2. len > 1 → env TEAMMODE_MEMBER
    3. 둘 다 불가 → None(폴백 신호)
    """
    config = _load_team_config(root)
    members = config.get("members", [])
    if not isinstance(members, list):
        return None
    # 원소가 dict인 것만 유효하게 처리
    valid = [m for m in members if isinstance(m, dict)]
    if len(valid) == 1:
        name = valid[0].get("name", "")
        if isinstance(name, str):
            name = name.strip()
        else:
            name = ""
        if name:
            return name
    if len(valid) > 1:
        env_name = os.environ.get("TEAMMODE_MEMBER", "").strip()
        if env_name:
            return env_name
    return None


def _my_log_path(root: str, member: str, date_str: str) -> str:
    """내 세션로그 파일 경로: memory/team/sessions/<멤버>/<날짜>.md."""
    return os.path.join(root, "memory", "team", "sessions", member, f"{date_str}.md")


def _root_tag(root: str) -> str:
    """루트 경로의 짧은 식별 태그 — 상태파일 키 분리용. 8자리 hex."""
    return hashlib.sha256(root.encode()).hexdigest()[:8]


def _state_path(agent: str, member: str | None = None, root: str | None = None) -> str:
    """상태파일 경로 — tempfile.gettempdir() 기반.

    멤버(+루트)가 주어지면 키에 포함해 멤버별 격리. 없으면 agent 단위(폴백).
    """
    if member and root:
        tag = _root_tag(root)
        return os.path.join(
            tempfile.gettempdir(),
            f"teammode-remind-state-{agent}-{member}-{tag}.json"
        )
    return os.path.join(tempfile.gettempdir(), f"teammode-remind-state-{agent}.json")


def _safe_int(val: object, default: int) -> int:
    """int 변환 실패 시 default."""
    try:
        return int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_float(val: object, default: float) -> float:
    """float 변환 실패 시 default."""
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_str(val: object, default: str) -> str:
    """str 아니면 default."""
    return val if isinstance(val, str) else default


def _read_state(path: str) -> dict:
    """상태파일 읽기. 없거나 파싱 실패 / 타입 이상 시 기본값."""
    defaults: dict = {
        "count": 0,
        "last_mtime": 0.0,
        "date": "",
        "last_strong_remind": 0.0,
    }
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return defaults
        return {
            "count": _safe_int(data.get("count"), defaults["count"]),
            "last_mtime": _safe_float(data.get("last_mtime"), defaults["last_mtime"]),
            "date": _safe_str(data.get("date"), defaults["date"]),
            "last_strong_remind": _safe_float(
                data.get("last_strong_remind"), defaults["last_strong_remind"]
            ),
        }
    except (OSError, json.JSONDecodeError, ValueError):
        return defaults


def _write_state(path: str, state: dict) -> None:
    """상태파일 쓰기. 실패는 무해."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass  # 카운터 파일 쓰기 실패는 무해 — 리마인드 로직을 막지 않는다


def _current_mtime(log_path: str) -> float:
    """내 세션로그 파일의 현재 mtime. 없으면 0.0."""
    try:
        return os.path.getmtime(log_path)
    except OSError:
        return 0.0


def _global_sessions_age(root: str) -> int:
    """전역 sessions 폴더 내 최신 파일 기준 age(초). 폴백(degraded) 경로에서만 사용."""
    sessions = glob.glob(
        os.path.join(root, "memory", "team", "sessions", "**", "*.md"),
        recursive=True)
    if not sessions:
        return 9999
    return int(time.time() - max(os.path.getmtime(f) for f in sessions))


def main() -> int:
    _ensure_utf8_io()  # 한글 출력이 Windows cp949 stdout 에서 크래시 방지
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # 공통 스크립트는 입력 오류로 세션을 막지 않는다

    if data.get("event") != "UserPromptSubmit":
        return 0

    root = _team_root()
    # 팀 모드 활성 시에만 동작
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        return 0

    # (레포 최신화는 여기서 하지 않는다 — SessionStart 훅이 세션당 1회 담당.)

    now = _kst_now()
    weekday = "월화수목금토일"[now.weekday()]
    time_line = (f"[teammode] 현재 시각: {now.strftime('%Y-%m-%d')}"
                 f"({weekday}) {now.strftime('%H:%M')} KST")

    base_guide = (
        " 세션 로그를 팀 루트의 memory/team/sessions/<이름>/ 에 기록하세요. "
        "<이름>은 members.md의 영문 이름(OS 사용자명 아님). "
        "파일은 하루 하나(YYYY-MM-DD.md, -late 등 분리 금지), "
        "frontmatter(author/date/summary) 필수. "
        "날짜는 06시 컷 — 위 시각이 00:00~05:59면 전날 파일, 06:00 이후면 오늘 파일. "
        "현재 작업 레포의 ./memory/ 에는 쓰지 마세요. "
        "한 일뿐 아니라 근거·접은 대안·막힌 점·다음 단계까지 한 흐름으로. "
        "개인 내용 제외, 팀 작업만.")

    # 멤버 식별
    member = _resolve_member(root)
    agent = data.get("agent", "unknown")

    if member is not None:
        # ── 멤버 특정 경로 ──
        state_file = _state_path(agent, member=member, root=root)
        date_str = _log_date(now)
        log_path = _my_log_path(root, member, date_str)
        mtime = _current_mtime(log_path)
        age = int(time.time() - mtime) if mtime > 0 else 9999

        state = _read_state(state_file)

        # check_reset: 내 파일 mtime 변화 OR 날짜 바뀜 → count=0, 상태 갱신, 안 보챔
        if mtime != state["last_mtime"] or date_str != state["date"]:
            _write_state(state_file, {
                "count": 0,
                "last_mtime": mtime,
                "date": date_str,
                "last_strong_remind": state["last_strong_remind"],
            })
            return 0

        # 카운터 증가 (리셋 없이 누적)
        count = state["count"] + 1
        _write_state(state_file, {
            "count": count,
            "last_mtime": mtime,
            "date": date_str,
            "last_strong_remind": state["last_strong_remind"],
        })

    else:
        # ── 폴백(degraded): 전역 mtime 기준 ──
        state_file = _state_path(agent)
        age = _global_sessions_age(root)

        # 폴백도 상태파일 기반으로 count·last_strong_remind 를 관리 (B 해결: 0.0 고정 제거)
        state = _read_state(state_file)
        count = state["count"] + 1
        _write_state(state_file, {
            "count": count,
            "last_mtime": state["last_mtime"],
            "date": state["date"],
            "last_strong_remind": state["last_strong_remind"],
        })

    # ── 발사 조건 판정 — 미리 계산 후 분기 ──
    # A 해결: strong_ok/weak_ok 를 독립 계산, strong throttle 중에도 elif(약발화)로 떨어진다.
    # B 해결: 멤버·폴백 모두 상태파일의 last_strong_remind 를 읽는다.
    context = None
    now_ts = time.time()
    last_strong = state["last_strong_remind"]

    strong_ok = (age >= 1800) and (now_ts - last_strong >= 1800)
    weak_ok = (count >= 5) and (count % 5 == 0)

    if strong_ok:
        context = (
            f"{time_line}\n"
            f"⛔ 세션 로그 30분 이상 미갱신 ({count}번째 프롬프트째 세션로그 미작성). "
            f"첫 행동으로{base_guide}"
        )
        # 강발화 시각 기록 (멤버·폴백 둘 다 상태파일에 저장)
        if member is not None:
            _write_state(state_file, {
                "count": count,
                "last_mtime": mtime,
                "date": date_str,
                "last_strong_remind": now_ts,
            })
        else:
            _write_state(state_file, {
                "count": count,
                "last_mtime": state["last_mtime"],
                "date": state["date"],
                "last_strong_remind": now_ts,
            })
    elif weak_ok:
        context = (
            f"{time_line}\n"
            f"{count}번째 프롬프트째 세션로그 미작성.{base_guide}"
        )

    if context:
        print(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
