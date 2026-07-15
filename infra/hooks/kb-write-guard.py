#!/usr/bin/env python3
"""kb-write-guard — PreToolUse KB 쓰기 거버넌스 훅 (공통 스크립트, 정규 스키마 전용).

스펙 §2.10: 정규 입력 스키마만 인지하며 에이전트를 모른다. normalize 심이 원어를
정규형으로 바꿔 stdin 으로 넘긴다. manifest 가 이 훅을 PreToolUse / file_edit 매처로
등록한다(strict, fallback runtime, enforcement block).

── 역할 ────────────────────────────────────────────────────────────────────────
"팀 메모리는 동사로만 쓴다(teammode 차별점)"를 **Write/Edit 직접 편집 도구**에 대해 강제한다.
에이전트가 `Edit`/`Write` 도구로 `memory/` 하위를 **직접 편집**하려 하면 차단.
→ 반드시 `python infra/teammode.py memory write …` 동사를 경유해야 한다.
  (엔진 동사는 별도 프로세스 open()이라 PreToolUse 대상이 아님 → 자연 통과.)

⚠️  Bash 등 다른 경로를 통한 우회는 현 범위 밖(별도 정책 필요).
    Write/Edit 직접 편집 가드만 이 훅의 보장 범위다.

── unlock 플래그 ──────────────────────────────────────────────────────────────
tm-manage-memory 스킬이 절차 시작 시 플래그를 touch, 완료(커밋 후) 시 rm 한다.
플래그 위치: $XDG_STATE_HOME/teammode/kb-unlock-<root_hash>-<session_id>
  없으면 $TMPDIR/teammode-kb-unlock-<USER>-<root_hash>-<session_id> 로 폴백.
  (root_hash: 팀루트 절대경로의 SHA-1 앞 8자리. 레포별·세션별 격리.)

★TTL 가드(필수): mtime이 KB_UNLOCK_TTL_SECONDS 초를 넘으면 만료 → 차단.
  (스킬 비정상 종료 시 영구 unlock 방지.)

세션 ID 매칭(필수, A2): 후보는 최대 2개 —
  ① 정규 stdin 의 session_id(normalize 가 raw session_id/sessionId 에서 승격; Codex 경로)
  ② env CLAUDE_SESSION_ID / CLAUDE_CODE_SESSION_ID (Claude 경로, 구 플래그 호환)
  각 후보는 ^[A-Za-z0-9._-]{1,128}$ 검증(플래그 파일명에 박히므로 traversal 문자 거부)
  — malformed 는 드롭(비치명), 중복 제거. **어느 후보든** 정확한 플래그 경로가
  존재+TTL 유효하면 unlock(glob 금지). 후보가 하나도 없으면 deny(fail-closed).
  보안 계약은 경로+mtime 기반 — 플래그 **내용**은 진단용(빈 파일도 유효).

── 다중 파일(codex apply_patch) ────────────────────────────────────────────────
files[] 의 각 경로를 개별 판정한다 — memory/ 파일이 하나라도 섞이면 차단, 전부 memory/
밖이면 통과(정상 다중파일 편집 허용). 전수 검사라 [밖, memory/...] 혼합 우회도 막힌다.

── fail-closed 정책 ────────────────────────────────────────────────────────────
file_edit 액션이고 경로 판별 실패(files 없음 + raw 없음) → deny.
files 원소 비문자열·경로 resolve 실패 → deny(보수적 차단).
stdin 파싱 실패 → deny(보수적 차단).

── 크로스에이전트 ─────────────────────────────────────────────────────────────
claude : PreToolUse block → Write/Edit 직접 편집 가드 강제.
codex  : PreToolUse hooks 지원 → apply_patch/file_edit 직접 편집 가드 강제.
         normalize 가 apply_patch tool_input.command/tool_input.patch/tool_input.input
         또는 top-level input 문자열의 파일 헤더를 정규 files[] 로 변환한다.

── .teammode-active 가드 ────────────────────────────────────────────────────────
teammode 가 꺼진 일상 작업 중에는 차단하지 않는다(빌드 안전).

정규 입력(stdin):
  { "event": "PreToolUse",
    "tool":  { "kind": "file", "action": "file_edit", "path": "/abs/path/to/file" },
    "agent": "claude",
    "raw":   {...} }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# stdout UTF-8 보장 — 한글 차단 사유 json 이 Windows cp949 stdout 에서 크래시 방지.
try:
    _infra = str(Path(__file__).resolve().parent.parent)
    if _infra not in sys.path:
        sys.path.insert(0, _infra)
    from io_encoding import ensure_utf8_io as _ensure_utf8_io  # type: ignore
except ImportError:
    def _ensure_utf8_io() -> None:  # 모듈 부재여도 훅은 동작(보정만 스킵)
        return

# i18n(PR-i1) — deny/안내 문구 ko/en 분기. io_encoding 과 동일한 infra/ sys.path
# 재사용 패턴. 부재(부분 배포·훅 단독 복사) 시 ko 강등(종전 거동 보존, fail-safe).
try:
    import i18n as _i18n  # type: ignore
except ImportError:
    _i18n = None
try:
    import git_ops as _git_ops  # type: ignore
except ImportError:
    _git_ops = None


def _hook_lang(team_root: str) -> str:
    """팀 locale → deny 문구 언어("ko"|"en").

    ⚠️ 이 훅은 TEAMMODE_HOME 을 신뢰하지 않는다(P0-3) — locale 도 반드시
    __file__ 기준 팀 루트(team_root 인자)의 config 에서 읽는다(env 기반 헬퍼 금지).
    i18n 부재/실패 시 ko(종전 거동).
    """
    if _i18n is None:
        return "ko"
    try:
        return _i18n.team_lang(team_root)
    except Exception:  # noqa: BLE001 — locale 해석 실패가 가드 판정을 막지 않는다
        return "ko"


def _t(key: str, lang: str, ko: str, **fmt) -> str:
    """deny 문자열 선택 — ko 원문은 호출부 리터럴이 단일 소스(구팀 무변화 계약),
    en 은 i18n 카탈로그(hook_* 키). i18n 부재 시 ko 폴백."""
    if lang == "en" and _i18n is not None:
        return _i18n.t(key, "en", **fmt)
    return ko.format(**fmt) if fmt else ko


# unlock 플래그 TTL(초). 5분 = 스킬 한 사이클에 충분하고 잔류 허용은 최소.
KB_UNLOCK_TTL_SECONDS = 300

# 세션 id relay 파일 TTL(초, A2). session-start 가 기록하고 새 기록 때 스테일을
# 기회적으로 프루닝한다. 세션 수명보다 넉넉하게 24h — relay 오선택은 guard 가
# 어차피 fail-closed(스퓨리어스 deny)로 막으므로 보안 상수가 아니라 위생 상수다.
SESSION_RELAY_TTL_SECONDS = 24 * 60 * 60

# 세션 id 후보 검증(A2) — 플래그/relay **파일명**에 그대로 박히므로 경로 구분자·
# traversal 을 원천 거부한다. malformed 후보는 드롭(비치명), 조작 불가.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _team_root() -> str:
    """런타임 훅의 팀 루트.

    __file__ 기준 정적 계산을 유일 기준으로 사용한다.
    이 파일은 infra/hooks/ 에 있으므로 parent.parent.parent == 팀 루트.

    TEAMMODE_HOME env 는 신뢰하지 않는다 — env 가 다른/inactive repo 를 가리키면
    .teammode-active 체크가 no-op 되거나 containment root 가 틀어져 guard 전체가
    무력화된다(P0-3). 팀루트는 명시/정적 계산만(env 무신뢰).
    """
    # infra/hooks/kb-write-guard.py → parent=hooks, parent.parent=infra, parent^3=팀루트
    return str(Path(__file__).resolve().parent.parent.parent)


def _root_hash(team_root: str) -> str:
    """팀 루트 절대경로의 SHA-1 앞 8자리 — 플래그 파일명 레포별 격리용."""
    return hashlib.sha1(team_root.encode()).hexdigest()[:8]


def _session_id() -> str:
    """Claude Code 세션 ID.

    신·구 환경변수명을 모두 지원한다. 과거엔 `CLAUDE_SESSION_ID` 였으나
    현재 Claude Code 는 `CLAUDE_CODE_SESSION_ID` 로 export 한다 — 둘 중 먼저
    잡히는 값을 쓴다(없으면 빈 문자열).
    """
    return (os.environ.get("CLAUDE_SESSION_ID")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or "").strip()


def _valid_session_id(value) -> str:
    """세션 id 후보 검증(A2) — 유효하면 정제된 문자열, malformed 면 빈 문자열.

    파일명에 박히는 값이므로 _SESSION_ID_RE 로 traversal 문자를 거부한다.
    malformed 는 드롭(빈 문자열 반환)이지 치명 에러가 아니다 — 후보 집합이
    비었을 때만 호출부가 fail-closed 한다.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not _SESSION_ID_RE.fullmatch(value):
        return ""
    return value


def _session_candidates(data: dict) -> list:
    """unlock 세션 id 후보 — **배타 우선순위**로 최대 1개(union 금지, codex A2-스푸핑).

    ① env CLAUDE_SESSION_ID / CLAUDE_CODE_SESSION_ID — Claude 경로(구 플래그 호환).
       하네스가 심는 프로세스 환경이라 페이로드로 조작할 수 없다 → 있으면 **권위**.
    ② data["session_id"] — normalize 가 훅 stdin 에서 승격한 정규 필드(Codex 경로).
       env 가 없거나 malformed 일 때만 폴백으로 쓴다.

    union(둘 다 후보)이면 안 되는 이유: stdin session_id 는 페이로드 쪽에서 조작
    가능한 입력이다. env 세션 B 가 stdin 에 session_id="A" 를 실어 보내면 A 의
    유효 unlock 플래그로 B 의 편집이 통과한다 — env 바인딩 다운그레이드. env 가
    있으면 env 후보만 검사해 이 스푸핑을 차단한다(엔진 `memory unlock begin` 의
    env 우선 → relay 폴백 해석 순서와도 일치).
    """
    env_cand = _valid_session_id(_session_id())
    if env_cand:
        return [env_cand]
    stdin_cand = _valid_session_id(data.get("session_id"))
    if stdin_cand:
        return [stdin_cand]
    return []


def unlock_flag_path(team_root: str | None = None,
                     session_id: str | None = None) -> str:
    """unlock 플래그 파일 절대경로.

    플래그 파일명에 root_hash(레포별)와 session_id(세션별)를 포함해 격리한다.
    같은 사용자의 다른 레포·다른 세션 플래그는 별도 파일이 된다.

    session_id 미지정 시 env(구 규약) 폴백, 그것도 없으면 "nosession" 강등 —
    경로 계산은 항상 가능해야 진단이 쉽다(_is_unlock_valid 는 후보 없으면 deny).

    1순위: $XDG_STATE_HOME/teammode/kb-unlock-<root_hash>-<session_id>
    폴백:  $TMPDIR/teammode-kb-unlock-<USER>-<root_hash>-<session_id>
    팀 루트 밖에 두어 git 무추적(머신 상태).
    """
    root = team_root if team_root is not None else _team_root()
    rh = _root_hash(root)
    if session_id is None:
        session_id = _session_id() or "nosession"
    suffix = f"{rh}-{session_id}"

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "teammode", f"kb-unlock-{suffix}")
    # TMPDIR/TMP/TEMP → /tmp 순 폴백
    tmpdir = (os.environ.get("TMPDIR")
              or os.environ.get("TMP")
              or os.environ.get("TEMP")
              or "/tmp")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    return os.path.join(tmpdir, f"teammode-kb-unlock-{user}-{suffix}")


def session_relay_dir(team_root: str | None = None) -> str:
    """세션 id relay 디렉토리 절대경로(A2) — 플래그와 같은 상태 루트 폴백 규약.

    session-start 훅이 정규 stdin 세션 id 를 `<relay_dir>/<session_id>` 파일로
    남기고(세션별 1파일), 엔진 `memory unlock begin|end` 가 env 부재 시 최신
    파일(mtime)로 세션 id 를 알아낸다. 규약의 단일 소스는 이 함수다(드리프트 방지
    — session-start·teammode.py 가 importlib 로 이 모듈을 로드해 재사용).

    1순위: $XDG_STATE_HOME/teammode/sessions/<root_hash>/
    폴백:  $TMPDIR/teammode-sessions-<USER>-<root_hash>/
    """
    root = team_root if team_root is not None else _team_root()
    rh = _root_hash(root)
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "teammode", "sessions", rh)
    tmpdir = (os.environ.get("TMPDIR")
              or os.environ.get("TMP")
              or os.environ.get("TEMP")
              or "/tmp")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    return os.path.join(tmpdir, f"teammode-sessions-{user}-{rh}")


def _is_unlock_valid(team_root: str, data: dict) -> bool:
    """unlock 플래그가 유효(존재 + TTL 미만)한지 검사(A2 — 배타 우선순위 후보).

    _session_candidates 가 고른 세션 id(최대 1: env 권위, 없으면 stdin 폴백)에
    대해 **정확한 플래그 경로**(glob 금지)를 검사하고, 존재+TTL 유효하면 unlock.
    파일명 자체에 root_hash + session_id 가 포함되므로 레포별·세션별 격리는
    경로 계산 단계에서 완료된다. 내용은 진단용 — 검사 안 함
    (구 빈 플래그도 그대로 유효).

    후보가 하나도 없으면(env 도 stdin 도 없거나 전부 malformed) fail-closed.

    반환:
      True  → unlock 허용 (memory/ 직접 편집 통과)
      False → 차단 (후보 없음 / 플래그 없음 / TTL 만료)
    """
    candidates = _session_candidates(data)
    if not candidates:
        return False  # 세션 id 후보 없음 → fail-closed(P0-2)

    now = time.time()
    for session_id in candidates:
        flag = unlock_flag_path(team_root, session_id=session_id)
        try:
            stat = os.stat(flag)
        except OSError:
            continue  # 이 후보의 플래그 없음 → 다음 후보
        age = now - stat.st_mtime
        if 0 <= age < KB_UNLOCK_TTL_SECONDS:
            return True

    return False  # 어느 후보도 유효 플래그 없음 / TTL 만료


def _my_member() -> str:
    """현재 멤버 식별자 (TEAMMODE_MEMBER env). 없으면 빈 문자열.

    install 이 settings.json env 에 박는다 — 멀티멤버에서 "나"를 가르는 단일 소스.
    """
    return os.environ.get("TEAMMODE_MEMBER", "").strip()


def _is_own_session_log(file_path: str, team_root: str) -> bool:
    """file_path 가 본인 세션로그 디렉토리(memory/team/sessions/<TEAMMODE_MEMBER>/)
    하위인지. 본인 세션로그는 가드 예외(자유 편집)다.

    방어(codex 적대검수 반영):
    - env 미설정이면 False(fail-closed — 가드 유지).
    - 멤버명은 슬러그(영숫자·_-)만 허용 — env 오염·유니코드/공백/경로구분자 위장 차단
      (install/엔진의 이름 검증을 훅에서도 강제, env 를 신뢰하지 않는다).
    - sessions/<member> 디렉토리 자체가 symlink 면 거부 — 메모리·타인 폴더로의
      resolve 우회를 막는다.
    - target.resolve() 가 own_dir 하위여야 통과 — .. ·중간 symlink 우회 차단.
    """
    member = _my_member()
    if not member:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+", member):
        return False
    try:
        sessions_root = (Path(team_root) / "memory" / "team" / "sessions").resolve()
        own_dir = sessions_root / member
        # member 디렉토리 자체가 symlink → resolve 가 다른 곳을 가리킴 → 우회. 거부.
        if own_dir.is_symlink():
            return False
        target = Path(file_path).resolve()
        target.relative_to(own_dir.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_memory_path(file_path: str, team_root: str) -> bool | None:
    """file_path 가 팀 루트의 memory/ 하위인지 확인.

    Path.resolve() 기반 containment — symlink 우회(alias→memory)를 차단한다.
    엔진 memory 의 memory.resolve() 가드와 동형.

    ── 상대경로 fail-closed (S2-1) ────────────────────────────────────────────
    file_path 가 상대경로이면 CWD 가 무엇이냐에 따라 containment 판정이 달라진다.
    훅 입력은 보통 절대경로지만 방어적으로:
      - 절대경로가 아니면 팀루트 기준으로 join 한 뒤 resolve 시도.
      - 팀루트 기준 join 후에도 resolve 실패 시 None → 호출부가 fail-closed.

    ── memory 내부 symlink 경계 (S2-2) ────────────────────────────────────────
    memory/ 내부 경로(raw, 비-resolve)가 memory/ 하위이지만 symlink 타겟이
    memory/ 밖을 가리키는 경우, resolve 결과만 보면 False(통과)가 된다.
    이를 차단하기 위해 두 가지 경로 모두 검사:
      (A) raw_abs: 상위 디렉터리가 비-resolve memory_root 하위 → True(차단)
      (B) resolved_abs: resolve 결과가 resolved memory_root 하위 → True(차단)
    어느 쪽이든 True면 차단(union 방식).

    반환:
      True  → memory/ 하위(차단 대상)
      False → memory/ 밖(통과)
      None  → 판별 실패(경로 없음·예외) — 호출부가 fail-closed 처리해야 함
    """
    if not file_path:
        return None

    try:
        p = Path(file_path)

        # ── S2-1: 상대경로 → 팀루트 기준 절대경로로 변환 ──
        if not p.is_absolute():
            # CWD 의존 resolve 금지 — 팀루트 기준으로 join.
            p = Path(team_root) / p

        # resolve() 로 symlink 추적 + 절대경로 정규화
        resolved_abs = p.resolve()

        team_root_resolved = Path(team_root).resolve()
        memory_root_resolved = (team_root_resolved / "memory").resolve()
        # 비-resolve memory_root (symlink 미추적, 명시 경로 기준)
        memory_root_raw = (Path(team_root) / "memory")

        # ── S2-2 (A): 비-resolve 경로 기준 containment ──
        # p 가 memory_root_raw 하위인지 확인 (symlink 경로 자체 기준).
        # os.path.normpath 로 `.`/`..` 를 먼저 접어 lexical 정규화한 뒤 판정.
        # (symlink 는 resolve 하지 않고 .. 만 해소 — S2-2 (B) 의 symlink 차단과 역할 분리.)
        p_normed = Path(os.path.normpath(p))
        try:
            p_normed.relative_to(memory_root_raw)
            return True  # raw(normpath) 경로상 memory/ 하위 → 차단
        except ValueError:
            pass  # memory/ 밖 → (B) 확인으로 진행

        # ── S2-2 (B): resolve 결과 기준 containment ──
        # resolve 결과가 memory/ 하위이면 차단 (alias→memory 우회 차단)
        try:
            resolved_abs.relative_to(memory_root_resolved)
            return True
        except ValueError:
            return False  # 어느 쪽도 memory/ 하위 아님 → 통과

    except (OSError, RuntimeError, TypeError):
        # resolve() 실패(권한 등) 또는 타입 오류 → 판별 불가
        return None


def _deny(reason: str, lang: str = "ko") -> None:
    """Claude PreToolUse 차단 결정 JSON 을 stdout 으로 출력(+ 호출부가 exit 2)."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.stderr.write(_t("hook_kb_stderr_blocked", lang,
                        "[teammode] KB 쓰기 차단: {reason}", reason=reason) + "\n")


def _deny_edit_lease(reason: str, lang: str = "ko") -> None:
    """Deny a file tool that cannot join the shared-worktree edit barrier."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.stderr.write(_t(
        "hook_edit_lease_stderr_blocked", lang,
        "[teammode] 파일 편집 보류: {reason}", reason=reason) + "\n")


def _begin_edit_lease(root: str, data: dict, lang: str) -> bool:
    """Reserve this exact session/tool call after every governance check passes."""
    if _git_ops is None:
        if not os.path.exists(os.path.join(root, ".git")):
            return True  # non-repository fixtures cannot run Git reconciliation
        reason = _t(
            "hook_edit_lease_deny_unavailable", lang,
            "편집 동기화 모듈을 불러오지 못해 보수적으로 차단했습니다. 다시 시도하세요.")
        _deny_edit_lease(reason, lang)
        return False
    owner = _git_ops.hook_edit_lease_owner(data)
    metadata = _git_ops.hook_edit_lease_metadata(data) if owner else None
    if not owner or metadata is None:
        if not _git_ops.is_git_worktree(root):
            return True
        reason = _t(
            "hook_edit_lease_deny_identity", lang,
            "세션/도구 식별자가 없어 안전한 자동 정합을 보장할 수 없습니다. "
            "에이전트 훅을 다시 동기화한 뒤 재시도하세요.")
        _deny_edit_lease(reason, lang)
        return False
    ok, detail = _git_ops.begin_hook_edit_lease(
        root, owner, metadata=metadata)
    if ok:
        return True
    safe_detail = _git_ops.sanitize_git_detail(detail or "edit lease unavailable")
    reason = _t(
        "hook_edit_lease_deny_busy", lang,
        "다른 세션의 정합 작업과 겹쳐 파일 편집을 시작하지 않았습니다. "
        "잠시 후 다시 시도하세요: {detail}", detail=safe_detail)
    _deny_edit_lease(reason, lang)
    return False


def main() -> int:
    _ensure_utf8_io()

    # 팀 루트는 __file__ 기준 정적 계산(TEAMMODE_HOME 무신뢰) — locale 도 같은
    # 루트의 config 에서 읽는다. stdin 과 무관하므로 파싱 전에 결정해도 안전.
    root = _team_root()
    lang = _hook_lang(root)

    # ── 0. 입력 파싱 ── (fail-closed: 파싱 불가 → deny)
    raw_stdin = sys.stdin.read()
    try:
        data = json.loads(raw_stdin or "{}")
    except (json.JSONDecodeError, ValueError):
        # stdin 파싱 실패 = 알 수 없는 입력 → 보수적으로 차단(fail-closed, P1-2).
        _deny(_t("hook_kb_deny_parse", lang,
                 "입력 파싱 실패 — 보수적 차단(fail-closed)."), lang)
        return 2

    # top-level 이 JSON object(dict) 가 아니면 malformed → fail-closed.
    # (유효 JSON 이어도 [], "x", 123, null 등은 data.get() 에서 터지므로 먼저 차단.)
    if not isinstance(data, dict):
        _deny(_t("hook_kb_deny_not_dict", lang,
                 "입력이 JSON object(dict) 가 아님 — 보수적 차단(fail-closed)."), lang)
        return 2

    if data.get("event") != "PreToolUse":
        return 0

    # ── 1. .teammode-active 가드: 마커 없으면 차단도 안 함(빌드 안전) ──
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        return 0

    # ── 2. file_edit 액션인지 확인 ──
    # 정규 스키마(normalize.py): action 필드가 "file_edit" 로 설정됨.
    # tool.kind == "builtin", tool.name == "Write"|"Edit".
    action = data.get("action") or ""
    if action != "file_edit":
        return 0  # 파일 편집 아님 → 통과

    # ── 3. 대상 경로 수집 — 정규 스키마 files[] ──
    # claude Write/Edit 은 단일 경로지만, codex apply_patch 는 한 호출에 여러 파일을
    # 편집할 수 있어 normalize 가 files[] 에 여러 경로를 넣는다. 따라서 "다중=malformed"
    # 로 일괄 차단하지 않고 **각 파일을 개별 판정**한다 — memory/ 파일이 하나라도 섞이면
    # 차단, 전부 memory/ 밖이면 통과(정상 다중파일 편집 허용). 전수 검사라 [밖, memory/...]
    # 같은 혼합 우회도 막힌다(과거 단건 검사 우회 우려를 더 정확히 차단).
    files_raw = data.get("files")
    # files 타입 검증: None 이면 빈 리스트로 간주, 리스트가 아니면 malformed → fail-closed.
    if files_raw is None:
        files = []
    elif not isinstance(files_raw, list):
        _deny(_t(
            "hook_kb_deny_files_not_list", lang,
            "malformed 입력 — files 필드가 리스트가 아님(fail-closed). "
            "정규 스키마로 재시도하거나 tm-manage-memory 스킬을 사용하세요."), lang)
        return 2
    else:
        files = files_raw

    # 각 원소가 문자열인지 검증 (정수·None 등 섞이면 malformed → fail-closed)
    for item in files:
        if not isinstance(item, str):
            _deny(_t(
                "hook_kb_deny_files_item", lang,
                "malformed 입력 — files 원소가 문자열이 아님(fail-closed). "
                "정규 스키마로 재시도하거나 tm-manage-memory 스킬을 사용하세요."), lang)
            return 2

    # 정규 스키마에 경로가 없으면 raw.tool_input.file_path 로 보조 조회(단일 경로 폴백)
    if not files:
        raw = data.get("raw")
        # raw 타입 검증: dict 여야 함
        if raw is not None and not isinstance(raw, dict):
            _deny(_t(
                "hook_kb_deny_raw_not_dict", lang,
                "malformed 입력 — raw 필드가 dict 가 아님(fail-closed). "
                "정규 스키마로 재시도하거나 tm-manage-memory 스킬을 사용하세요."), lang)
            return 2
        raw = raw or {}
        tool_input = raw.get("tool_input")
        # tool_input 타입 검증: dict 여야 함 (문자열이면 malformed → fail-closed)
        if tool_input is not None and not isinstance(tool_input, dict):
            _deny(_t(
                "hook_kb_deny_tool_input_not_dict", lang,
                "malformed 입력 — raw.tool_input 이 dict 가 아님(fail-closed). "
                "정규 스키마로 재시도하거나 tm-manage-memory 스킬을 사용하세요."), lang)
            return 2
        tool_input = tool_input or {}
        fp = tool_input.get("file_path", "") or ""
        if fp:
            files = [fp]

    # file_edit 인데 경로를 하나도 못 구함 → fail-closed(P1-2)
    if not files:
        _deny(_t(
            "hook_kb_deny_no_path", lang,
            "memory/ 경로 판별 실패 — 보수적 차단(fail-closed). "
            "파일 경로가 포함된 정규 스키마로 재시도하거나 "
            "tm-manage-memory 스킬을 사용하세요."), lang)
        return 2

    # ── 4. 각 파일 개별 판정 ──
    #   판별 실패(None)      → 즉시 fail-closed deny(memory/ 가능성 배제 불가)
    #   memory/ 밖           → 무해(통과 후보)
    #   memory/ + 본인 세션로그 → 예외 허용
    #   memory/ + 그 외      → unlock 필요(needs_unlock)
    needs_unlock = False
    for file_path in files:
        in_memory = _is_memory_path(file_path, root)
        if in_memory is None:
            # resolve() 실패(심링크·권한 등) → fail-closed(P1-2)
            _deny(_t(
                "hook_kb_deny_resolve_error", lang,
                "memory/ 경로 판별 중 오류 — 보수적 차단(fail-closed). "
                "tm-manage-memory 스킬을 사용하세요."), lang)
            return 2
        if not in_memory:
            continue  # memory/ 밖 → 무영향
        # 본인 세션로그(sessions/<TEAMMODE_MEMBER>/)는 자유 편집 허용
        # (toolkit 식 "살아있는 문서": append뿐 아니라 수정·재구성·summary 갱신).
        if _is_own_session_log(file_path, root):
            continue
        needs_unlock = True  # 거버넌스 대상 memory/ 파일

    # memory/ 파일을 하나도 안 건드리면 통과 (정상 다중파일 편집 포함)
    if not needs_unlock:
        return 0 if _begin_edit_lease(root, data, lang) else 2

    # ── 5. memory/ 파일 포함 → unlock 플래그 확인 ──
    if _is_unlock_valid(root, data):
        # 스킬이 플래그를 세운 구간 → governance 통과 후 edit lease 등록.
        return 0 if _begin_edit_lease(root, data, lang) else 2

    _deny(_t(
        "hook_kb_deny_direct_edit", lang,
        "memory/ 하위 직접 편집은 금지돼 있습니다. "
        "KB(메모리 베이스)는 '동사 경유 원칙' — Edit/Write 직접 편집 대신 엔진 동사를 써야 "
        "충돌 없이 팀 공유 메모리에 기록됩니다. "
        "메모리는 tm-manage-memory 스킬을 통해서만 추가·수정·삭제하세요 "
        "(엔진: python infra/teammode.py memory write …)."), lang)
    return 2  # PreToolUse 차단


if __name__ == "__main__":
    raise SystemExit(main())
