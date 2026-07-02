#!/usr/bin/env python3
"""session-log-remind — 세션로그 갱신 리마인더 (공통 스크립트, 정규 스키마 전용).

스펙 02 §6: 이 스크립트는 **정규 입력 스키마(§6.1)만 인지**하며 특정 에이전트를
알지 못한다. normalize 심이 원어를 정규형으로 바꿔 stdin 으로 넘겨준다.

정규 입력(stdin):
  { "event": "UserPromptSubmit", "prompt": "...", "agent": "claude", "raw": {...} }

출력: JSON stdout — additionalContext(상세 안내, 모델 컨텍스트용) + systemMessage(짧은 한 줄, 사용자 화면 표시용).
systemMessage 방출은 옵트아웃 가능 — team.config.json 의 ux.session_log_remind.system_message
(기본 true) 가 false 면 내지 않고 additionalContext(모델 컨텍스트)만 낸다(화면 noise 절감).
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


# 팀 레포 표식 — install_lib.has_team_marker(_TEAM_MARKERS)와 동일 규약(드리프트 주의).
_TEAM_MARKERS = (".git", "team.config.json", "memory")


def _warn_if_stale_home(root: str) -> None:
    """TEAMMODE_HOME 이 설정됐는데 유효한 팀 루트가 아니면 stderr 한 줄 경고 (이슈 #9a).

    레포 이동/이름변경 후 env 가 옛 경로를 가리키면 훅이 조용히 죽어(.teammode-active
    부재 exit 0) 원인 진단이 불가했다. stdout 은 훅 출력(JSON)으로 소비되므로 경고는
    stderr 로만, 한 줄로 내고 거동(exit 0)은 바꾸지 않는다. 팀 표식이 있는데
    .teammode-active 만 없는 정상 off 상태는 종전대로 침묵한다.
    """
    if not os.environ.get("TEAMMODE_HOME"):
        return
    if any(os.path.exists(os.path.join(root, m)) for m in _TEAM_MARKERS):
        return
    try:
        print(f"[teammode] TEAMMODE_HOME이 유효한 팀 루트가 아닙니다: {root} — "
              "레포 이동/이름변경 시 셸 프로파일의 TEAMMODE_HOME을 갱신하세요",
              file=sys.stderr)
    except (OSError, UnicodeError):
        pass  # 경고 실패가 훅을 막지 않는다(advisory)


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


def _system_message_enabled(root: str) -> bool:
    """화면용 systemMessage 방출 여부 — team.config.json ux.session_log_remind.system_message (기본 True).

    False 면 모델 컨텍스트(additionalContext)는 유지한 채 사용자 화면 한 줄(systemMessage)만
    생략해 화면 noise 를 줄인다. team.config.json 은 엔진 sync(SYNC_PATHS=infra/) 대상이
    아니므로 이 옵션은 `tm-mode update` 에도 보존된다. 누락·타입 불일치 시 True(현행 동작 보존).
    """
    ux = _load_team_config(root).get("ux")
    if not isinstance(ux, dict):
        return True
    slr = ux.get("session_log_remind")
    if not isinstance(slr, dict):
        return True
    val = slr.get("system_message", True)
    return val if isinstance(val, bool) else True


def _valid_member_name(name: str) -> bool:
    """멤버명이 경로·지시문에 안전한 식별자인지 — teammode._validate_author·kb-write-guard 와 동일 규칙.

    멤버명은 _my_log_path 로 경로에 join 되고 _log_kit 로 Read(...) 지시문에
    그대로 박힌다. team.config.json(레포 공유) 또는 TEAMMODE_MEMBER(env)에서 오므로
    신뢰 경계 밖이다 — '/'·'\\'·'.'·'..'·절대경로·개행·따옴표·')' 등을 차단해
    경로 traversal·컨텍스트 주입을 막는다(실패 시 폴백). ASCII 영숫자+'-_'만 허용 —
    isalnum() 은 유니코드라 한글이 통과하지만, kb-write-guard([A-Za-z0-9_-])·_validate_author
    (isascii 강제)와 어긋나면 한글 멤버는 리마인더는 멤버로 굳고 편집 가드는 fail-closed 가 된다.
    """
    if not name or name in (".", ".."):
        return False
    if not name.isascii():
        return False
    if "/" in name or "\\" in name or os.path.isabs(name):
        return False
    if not name[0].isalnum():
        return False
    return all(c.isalnum() or c in "-_" for c in name)


def _resolve_member(root: str) -> str | None:
    """멤버 이름 결정.

    1. env TEAMMODE_MEMBER (단일 소스 — install 이 settings.json 에 박음)
    2. fallback: team.config.json members 가 1명 → members[0]["name"]
       (단일멤버·env 미설정 전환기 호환)
    3. 둘 다 불가(또는 이름이 안전 식별자가 아님) → None(폴백 신호)
    """
    # 1. env 단일 소스 (멀티멤버에서 "나"를 가르는 기준)
    env_name = os.environ.get("TEAMMODE_MEMBER", "").strip()
    if env_name and _valid_member_name(env_name):
        return env_name
    # 2. fallback: config members 1명
    config = _load_team_config(root)
    members = config.get("members", [])
    if not isinstance(members, list):
        return None
    valid = [m for m in members if isinstance(m, dict)]
    if len(valid) == 1:
        name = valid[0].get("name", "")
        name = name.strip() if isinstance(name, str) else ""
        if name and _valid_member_name(name):
            return name
    return None


def _my_log_path(root: str, member: str, date_str: str) -> str:
    """내 세션로그 파일 경로: memory/team/sessions/<멤버>/<날짜>.md."""
    return os.path.join(root, "memory", "team", "sessions", member, f"{date_str}.md")


def _count_lines(path: str) -> int:
    """파일 줄 수. 없거나 못 읽으면 0(새 파일로 간주).

    advisory 훅은 매 프롬프트 경로라 예외 전파 금지 — 깨진 UTF-8/바이너리
    세션로그도 errors='replace' 로 줄 수만 세고 크래시하지 않는다.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _log_kit(log_path: str) -> str:
    """세션로그를 Read(끝 offset)+Edit 로 이어쓰는 구체 명령을 만든다.

    파일이 있으면 끝 ~20줄만 읽는 offset 명령을, 없으면 새로 Write 안내를 준다.
    훅이 정확한 offset 을 코드로 깔아줘 모델이 log 동사로 도망가지 못하게 한다.
    """
    n = _count_lines(log_path)
    # log_path 는 TEAMMODE_HOME 루트를 포함한다(멤버명만 검증됐을 뿐 루트는 신뢰 밖).
    # 경로에 따옴표·개행이 있으면 Read("...") 지시문 줄이 갈라져 컨텍스트 주입이 되므로
    # 문자열 리터럴로 이스케이프해 박는다. (ensure_ascii=False: 한글 폴더 경로 보존)
    p = json.dumps(log_path, ensure_ascii=False)
    if n == 0:
        return (f' 세션로그 파일이 아직 없습니다 — Read 없이 '
                f'frontmatter(author/date/summary)+첫 항목을 Write({p}, ...) 로 새로 만드세요.')
    off = max(1, n - 20)
    return (f' 이어쓰기: Read({p}, offset={off}, limit=25) 로 끝부분만 읽고 Edit 로 추가. '
            f'summary(frontmatter) 갱신이 필요하면 Read({p}, offset=1, limit=6) 도. '
            f'log 동사·전체 Read 금지 — 끝 20줄만.')


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


def _global_sessions_mtime(root: str) -> float:
    """전역 sessions 폴더 내 최신 파일 mtime. 없으면 0.0. 폴백 check_reset 용.

    멤버 경로의 `_current_mtime`(내 파일 mtime)과 대칭 — 멤버 식별 실패(degraded)
    상황에서 '누군가' 세션로그를 갱신하면 이 값이 바뀌어 폴백 count 를 리셋한다.
    """
    sessions = glob.glob(
        os.path.join(root, "memory", "team", "sessions", "**", "*.md"),
        recursive=True)
    if not sessions:
        return 0.0
    return max(os.path.getmtime(f) for f in sessions)


def main() -> int:
    _ensure_utf8_io()  # 한글 출력이 Windows cp949 stdout 에서 크래시 방지
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # 공통 스크립트는 입력 오류로 세션을 막지 않는다

    if data.get("event") != "UserPromptSubmit":
        return 0

    root = _team_root()
    _warn_if_stale_home(root)  # 스테일 TEAMMODE_HOME 표면화(이슈 #9a) — 거동 불변
    # 팀 모드 활성 시에만 동작
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        return 0

    # (레포 최신화는 여기서 하지 않는다 — SessionStart 훅이 세션당 1회 담당.)

    now = _kst_now()
    weekday = "월화수목금토일"[now.weekday()]
    time_line = (f"[teammode] 현재 시각: {now.strftime('%Y-%m-%d')}"
                 f"({weekday}) {now.strftime('%H:%M')} KST")

    base_guide = (
        " 세션 로그를 팀 루트의 memory/team/sessions/<이름>/ 에 Read(끝부분 offset)+Edit 로 "
        "직접 관리하세요(log 동사 쓰지 말 것 — 컨텍스트 절약·충실도). "
        "본인 세션로그는 가드 예외라 append뿐 아니라 직접 수정·재구성·요약 갱신이 됩니다. "
        "<이름>은 members.md의 영문 이름(OS 사용자명 아님). "
        "파일은 하루 하나(YYYY-MM-DD.md, -late 등 분리 금지), "
        "frontmatter(author/date/summary) 필수. "
        "날짜는 06시 컷 — 위 시각이 00:00~05:59면 전날 파일, 06:00 이후면 오늘 파일. "
        "현재 작업 레포의 ./memory/ 에는 쓰지 마세요. "
        "한 일뿐 아니라 근거·접은 대안·막힌 점·다음 단계까지 한 흐름으로. "
        "일상 추가는 끝 20줄만 Read, 큰 재구성·요약 갱신만 전체 Read. "
        "개인 내용 제외, 팀 작업만.")

    # 멤버 식별
    member = _resolve_member(root)
    agent = data.get("agent", "unknown")

    if member is not None:
        # ── 멤버 특정 경로 ──
        state_file = _state_path(agent, member=member, root=root)
        date_str = _log_date(now)
        log_path = _my_log_path(root, member, date_str)
        log_kit = _log_kit(log_path)  # offset 명령(끝 20줄 Read+Edit)을 코드로 깔아준다
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
        # 멤버를 못 정해 경로를 특정할 수 없으므로 offset 키트는 비운다(base_guide 일반 안내만).
        log_kit = ""
        state_file = _state_path(agent)
        age = _global_sessions_age(root)
        g_mtime = _global_sessions_mtime(root)
        date_str = _log_date(now)

        # 폴백도 상태파일 기반으로 count·last_strong_remind 를 관리 (B 해결: 0.0 고정 제거)
        state = _read_state(state_file)

        # check_reset(멤버 경로와 대칭): 전역 sessions mtime 변화 OR 날짜(06시 컷) 바뀜 →
        # count=0 + return(안 보챔). 멤버 식별 실패 degraded 경로라 '내 파일'을 특정 못 해
        # '누구든' 세션로그를 갱신하면 리셋되는 약한 신호지만, 멤버 env 누락 시 리마인더가
        # 5프롬프트마다 무한 반복(issue #26)되는 것을 막는다.
        if g_mtime != state["last_mtime"] or date_str != state["date"]:
            _write_state(state_file, {
                "count": 0,
                "last_mtime": g_mtime,
                "date": date_str,
                "last_strong_remind": state["last_strong_remind"],
            })
            return 0

        count = state["count"] + 1
        _write_state(state_file, {
            "count": count,
            "last_mtime": g_mtime,
            "date": date_str,
            "last_strong_remind": state["last_strong_remind"],
        })

    # ── 발사 조건 판정 — 미리 계산 후 분기 ──
    # A 해결: strong_ok/weak_ok 를 독립 계산, strong throttle 중에도 elif(약발화)로 떨어진다.
    # B 해결: 멤버·폴백 모두 상태파일의 last_strong_remind 를 읽는다.
    context = None
    system_msg = None
    now_ts = time.time()
    last_strong = state["last_strong_remind"]

    strong_ok = (age >= 1800) and (now_ts - last_strong >= 1800)
    weak_ok = (count >= 5) and (count % 5 == 0)

    if strong_ok:
        context = (
            f"{time_line}\n"
            f"⛔ 세션 로그 30분 이상 미갱신 ({count}번째 프롬프트째 세션로그 미작성). "
            f"첫 행동으로{base_guide}{log_kit}"
        )
        system_msg = f"⛔ 세션로그 미작성 — {count}번째 프롬프트째. 첫 행동으로 기록하세요"
        # 강발화 시각 기록 (멤버·폴백 둘 다 상태파일에 저장)
        if member is not None:
            _write_state(state_file, {
                "count": count,
                "last_mtime": mtime,
                "date": date_str,
                "last_strong_remind": now_ts,
            })
        else:
            # 폴백 강발화도 갱신된 g_mtime/date_str 를 유지 — 다음 런 check_reset 이 stale
            # 값으로 오인 리셋하지 않게(멤버 경로 mtime/date_str 보존과 대칭).
            _write_state(state_file, {
                "count": count,
                "last_mtime": g_mtime,
                "date": date_str,
                "last_strong_remind": now_ts,
            })
    elif weak_ok:
        context = (
            f"{time_line}\n"
            f"{count}번째 프롬프트째 세션로그 미작성.{base_guide}{log_kit}"
        )
        system_msg = f"📝 세션로그 미작성 — {count}번째 프롬프트째"

    if context:
        out = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            },
        }
        # 화면용 systemMessage 는 옵트아웃 가능(기본 on). config 가 false 면 모델
        # 컨텍스트(additionalContext)만 내고 화면 한 줄은 생략해 noise 를 줄인다.
        if _system_message_enabled(root):
            out["systemMessage"] = system_msg
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
