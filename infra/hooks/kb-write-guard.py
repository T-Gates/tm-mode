#!/usr/bin/env python3
"""kb-write-guard — PreToolUse KB 쓰기 거버넌스 훅 (공통 스크립트, 정규 스키마 전용).

스펙 §2.10: 정규 입력 스키마만 인지하며 에이전트를 모른다. normalize 심이 원어를
정규형으로 바꿔 stdin 으로 넘긴다. manifest 가 이 훅을 PreToolUse / file_edit 매처로
등록한다(strict, fallback runtime, enforcement block).

── 역할 ────────────────────────────────────────────────────────────────────────
"팀 메모리는 동사로만 쓴다(teammode 차별점)"를 **Write/Edit 직접 편집 도구**에 대해 강제한다.
에이전트가 `Edit`/`Write` 도구로 `memory/` 하위를 **직접 편집**하려 하면 차단.
→ 반드시 `python infra/teammode.py knowledge write …` 동사를 경유해야 한다.
  (엔진 동사는 별도 프로세스 open()이라 PreToolUse 대상이 아님 → 자연 통과.)

⚠️  Bash 등 다른 경로를 통한 우회는 현 범위 밖(별도 정책 필요).
    Write/Edit 직접 편집 가드만 이 훅의 보장 범위다.

── unlock 플래그 ──────────────────────────────────────────────────────────────
tm-manage-knowledge 스킬이 절차 시작 시 플래그를 touch, 완료(커밋 후) 시 rm 한다.
플래그 위치: $XDG_STATE_HOME/teammode/kb-unlock-<root_hash>-<session_id>
  없으면 $TMPDIR/teammode-kb-unlock-<USER>-<root_hash>-<session_id> 로 폴백.
  (root_hash: 팀루트 절대경로의 SHA-1 앞 8자리. 레포별·세션별 격리.)

★TTL 가드(필수): mtime이 KB_UNLOCK_TTL_SECONDS 초를 넘으면 만료 → 차단.
  (스킬 비정상 종료 시 영구 unlock 방지.)

세션 ID 매칭(필수): CLAUDE_SESSION_ID 가 없으면 → deny(fail-closed).
  플래그 파일명에 session_id 가 포함되므로 내용 검사는 하지 않는다.

── fail-closed 정책 ────────────────────────────────────────────────────────────
file_edit 액션이고 경로 판별 실패(files 없음 + raw 없음) → deny.
stdin 파싱 실패 → deny(보수적 차단).

── 크로스에이전트 ─────────────────────────────────────────────────────────────
claude : PreToolUse block → Write/Edit 직접 편집 가드 강제.
codex  : events.json 에서 PreToolUse=null → 이 훅이 **애초 등록되지 않는다**. 어댑터
         sync 가 enforcement:block 의 "차단 강제 상실"을 [warn] 으로 표면화(무음 누락 0).
         codex 는 이번 릴리스 폴백(경고만) — 커버리지 안정화 후 별도 백로그.

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


# unlock 플래그 TTL(초). 5분 = 스킬 한 사이클에 충분하고 잔류 허용은 최소.
KB_UNLOCK_TTL_SECONDS = 300


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


def unlock_flag_path(team_root: str | None = None) -> str:
    """unlock 플래그 파일 절대경로.

    플래그 파일명에 root_hash(레포별)와 session_id(세션별)를 포함해 격리한다.
    같은 사용자의 다른 레포·다른 세션 플래그는 별도 파일이 된다.

    1순위: $XDG_STATE_HOME/teammode/kb-unlock-<root_hash>-<session_id>
    폴백:  $TMPDIR/teammode-kb-unlock-<USER>-<root_hash>-<session_id>
    팀 루트 밖에 두어 git 무추적(머신 상태).
    """
    root = team_root if team_root is not None else _team_root()
    rh = _root_hash(root)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "nosession")
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


def _is_unlock_valid(team_root: str) -> bool:
    """unlock 플래그가 유효(존재 + TTL 미만)한지 검사.

    플래그 파일명 자체에 root_hash + session_id 가 포함되어 있으므로
    레포별·세션별 격리는 경로 계산 단계에서 완료된다.

    CLAUDE_SESSION_ID 가 없으면 fail-closed — 세션 없는 환경에서는
    unlock 플래그를 신뢰할 수 없으므로 deny.

    반환:
      True  → unlock 허용 (memory/ 직접 편집 통과)
      False → 차단 (세션ID 없음 / 플래그 없음 / TTL 만료)
    """
    # 세션ID 없으면 fail-closed(P0-2)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    if not session_id:
        return False

    flag = unlock_flag_path(team_root)
    try:
        stat = os.stat(flag)
    except OSError:
        return False  # 플래그 없음

    age = time.time() - stat.st_mtime
    if age < 0 or age >= KB_UNLOCK_TTL_SECONDS:
        return False  # TTL 만료

    return True


def _is_memory_path(file_path: str, team_root: str) -> bool | None:
    """file_path 가 팀 루트의 memory/ 하위인지 확인.

    Path.resolve() 기반 containment — symlink 우회(alias→memory)를 차단한다.
    엔진 knowledge 의 memory.resolve() 가드와 동형.

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


def _deny(reason: str) -> None:
    """Claude PreToolUse 차단 결정 JSON 을 stdout 으로 출력(+ 호출부가 exit 2)."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.stderr.write(f"[teammode] KB 쓰기 차단: {reason}\n")


def main() -> int:
    _ensure_utf8_io()

    # ── 0. 입력 파싱 ── (fail-closed: 파싱 불가 → deny)
    raw_stdin = sys.stdin.read()
    try:
        data = json.loads(raw_stdin or "{}")
    except (json.JSONDecodeError, ValueError):
        # stdin 파싱 실패 = 알 수 없는 입력 → 보수적으로 차단(fail-closed, P1-2).
        _deny("입력 파싱 실패 — 보수적 차단(fail-closed).")
        return 2

    # top-level 이 JSON object(dict) 가 아니면 malformed → fail-closed.
    # (유효 JSON 이어도 [], "x", 123, null 등은 data.get() 에서 터지므로 먼저 차단.)
    if not isinstance(data, dict):
        _deny("입력이 JSON object(dict) 가 아님 — 보수적 차단(fail-closed).")
        return 2

    if data.get("event") != "PreToolUse":
        return 0

    root = _team_root()

    # ── 1. .teammode-active 가드: 마커 없으면 차단도 안 함(빌드 안전) ──
    if not os.path.isfile(os.path.join(root, ".teammode-active")):
        return 0

    # ── 2. file_edit 액션인지 확인 ──
    # 정규 스키마(normalize.py): action 필드가 "file_edit" 로 설정됨.
    # tool.kind == "builtin", tool.name == "Write"|"Edit".
    action = data.get("action") or ""
    if action != "file_edit":
        return 0  # 파일 편집 아님 → 통과

    # ── 3. 대상 경로가 memory/ 하위인지 확인 ──
    # 정규 스키마: data["files"] = ["/abs/path/to/file"] (normalize.py L113-116)
    files_raw = data.get("files")
    # files 타입 검증: None 이면 빈 리스트로 간주, 리스트가 아니면 malformed → fail-closed.
    if files_raw is None:
        files = []
    elif not isinstance(files_raw, list):
        _deny(
            "malformed 입력 — files 필드가 리스트가 아님(fail-closed). "
            "정규 스키마로 재시도하거나 tm-manage-knowledge 스킬을 사용하세요."
        )
        return 2
    else:
        files = files_raw

    # 정규 스키마는 단일 경로(normalize: file_path 하나 → files=[fp]).
    # 다중 요소는 malformed → fail-closed. files[0] 만 보면 뒤 요소의 memory/ 경로를
    # 놓쳐 우회된다(예: files=[밖, memory/...] → allow 오판).
    if len(files) > 1:
        _deny(
            "malformed 입력 — files 가 단일 경로가 아님(정규 스키마는 단일, fail-closed). "
            "정규 스키마로 재시도하거나 tm-manage-knowledge 스킬을 사용하세요."
        )
        return 2

    # files[0] 이 문자열인지 확인
    file_path = ""
    if files:
        first = files[0]
        if not isinstance(first, str):
            _deny(
                "malformed 입력 — files[0] 이 문자열이 아님(fail-closed). "
                "정규 스키마로 재시도하거나 tm-manage-knowledge 스킬을 사용하세요."
            )
            return 2
        file_path = first

    # 정규 스키마에 없으면 raw 에서 보조 조회
    if not file_path:
        raw = data.get("raw")
        # raw 타입 검증: dict 여야 함
        if raw is not None and not isinstance(raw, dict):
            _deny(
                "malformed 입력 — raw 필드가 dict 가 아님(fail-closed). "
                "정규 스키마로 재시도하거나 tm-manage-knowledge 스킬을 사용하세요."
            )
            return 2
        raw = raw or {}
        tool_input = raw.get("tool_input")
        # tool_input 타입 검증: dict 여야 함 (문자열이면 malformed → fail-closed)
        if tool_input is not None and not isinstance(tool_input, dict):
            _deny(
                "malformed 입력 — raw.tool_input 이 dict 가 아님(fail-closed). "
                "정규 스키마로 재시도하거나 tm-manage-knowledge 스킬을 사용하세요."
            )
            return 2
        tool_input = tool_input or {}
        file_path = tool_input.get("file_path", "") or ""

    # file_edit 인데 경로 판별 불가 → fail-closed(P1-2)
    if not file_path:
        _deny(
            "memory/ 경로 판별 실패 — 보수적 차단(fail-closed). "
            "파일 경로가 포함된 정규 스키마로 재시도하거나 "
            "tm-manage-knowledge 스킬을 사용하세요."
        )
        return 2

    in_memory = _is_memory_path(file_path, root)
    if in_memory is None:
        # resolve() 실패(심링크·권한 등) → fail-closed(P1-2)
        _deny(
            "memory/ 경로 판별 중 오류 — 보수적 차단(fail-closed). "
            "tm-manage-knowledge 스킬을 사용하세요."
        )
        return 2
    if not in_memory:
        return 0  # memory/ 밖 → 무영향(통과)

    # ── 4. unlock 플래그 확인 ──
    if _is_unlock_valid(root):
        return 0  # 스킬이 플래그를 세운 구간 → 통과

    _deny(
        "memory/ 하위 직접 편집은 금지돼 있습니다. "
        "KB(지식 베이스)는 '동사 경유 원칙' — Edit/Write 직접 편집 대신 엔진 동사를 써야 "
        "충돌 없이 팀 공유 메모리에 기록됩니다. "
        "지식은 tm-manage-knowledge 스킬을 통해서만 추가·수정·삭제하세요 "
        "(엔진: python infra/teammode.py knowledge write …)."
    )
    return 2  # PreToolUse 차단


if __name__ == "__main__":
    raise SystemExit(main())
