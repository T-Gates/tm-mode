#!/usr/bin/env python3
"""credentials — teammode 의 평문 자격증명 금고 (L2-E, v0.1).

B-3 결정(Jane): **각 멤버가 자기 토큰을 직접 입력한다.** 팀 토큰 자동공유 메커니즘은
v0.1 미구현(v0.2 이월). 팀 scope 토큰도 v0.1 에서는 "각자 1회" 로컬 저장이며, 이 모듈은
**전송 채널을 제공하지 않는다** — store/load/delete 모두 멤버 로컬 디스크에만 작용한다.
팀(team) scope 와 개인(personal) scope 는 같은 파일 안에서 네임스페이스로만 구분된다.

저장 위치(B-3): `$XDG_DATA_HOME/teammode/credentials/<team>.json`
  (기본 `~/.local/share/teammode/credentials`). 파일 권한 **0600**.
  근거 정정: last-pull 은 `XDG_STATE_HOME` 선례이나, credentials 는 비밀이므로
  `XDG_DATA_HOME`(사용자 데이터) 채택. git 추적 금지(.gitignore `*credentials*` 데이터 패턴).

철칙(P0/P1 — 마스킹):
  - **토큰 평문이 stdout/로그/예외 메시지에 절대 새지 않는다.** git_ops 의 stderr `detail`
    노출 동형 사고를 막기 위해, 이 모듈의 모든 예외·repr·로그는 토큰값을 담지 않는다
    (키 이름만 노출). 누출 0 은 마스킹 테스트로 강제된다(tests/test_credentials_l2e.py).
  - v0.1 은 **평문 JSON** 이다(OS 키체인은 v0.2 이월). 평문이므로 0600 + git 미추적 +
    동기화 폴더 금지 경고(tm-connect 스킬)가 방어선이다.

철칙(P1 — 실패 무해): 외부 노출 함수는 자격증명값을 담은 예외를 전파하지 않는다.
  자격증명 부재는 None(load) / False(delete) 로 표현한다. 키 이름·경로 오류 같은
  프로그래밍 오류만 토큰값 없는 ValueError 로 전파한다.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Optional

# 파일 권한 — 소유자 읽기/쓰기만(0600). umask 에 의존하지 않고 저장 직후 명시적으로 chmod.
_FILE_MODE = 0o600
_DIR_MODE = 0o700

# scope 네임스페이스 — 팀 토큰과 개인 토큰을 같은 파일 안에서 분리. (전송 채널 아님.)
SCOPE_TEAM = "team"
SCOPE_PERSONAL = "personal"
_SCOPES = (SCOPE_TEAM, SCOPE_PERSONAL)

# team / key 식별자 화이트리스트 — 경로 traversal·인젝션 차단(`..`, `/`, NUL 등 거부).
# 파일명·JSON 키로 안전한 문자만 허용한다. 위반은 토큰값 없는 ValueError.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def credentials_dir() -> Path:
    """credentials 금고 디렉토리 — `$XDG_DATA_HOME/teammode/credentials`.

    env 미주입 시 `~/.local/share/teammode/credentials` 로 폴백. 런타임 read/write 격리
    목적이라 env 참조가 정당하다(session-log-remind `_pull_state_path` 동형). 테스트는
    conftest autouse 가 XDG_DATA_HOME 를 tmp 로 격리하므로 실 호스트 경로를 건드리지 않는다.
    """
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return Path(base) / "teammode" / "credentials"


def _vault_path(team: str) -> Path:
    return credentials_dir() / f"{_check_ident(team, 'team')}.json"


# SEC-4: 평문 금고는 동기화 폴더에 두면 안 된다(skills.md §, internals.md §).
# 흔한 동기화 폴더 경로 패턴 — Syncthing 은 임의 경로라 완전 감지 불가하므로 휴리스틱.
_SYNC_FOLDER_HINTS = (
    "dropbox", "onedrive", "google drive", "googledrive", "gdrive",
    "mobile documents", "cloudstorage", "icloud", "/sync/", "/syncthing/",
    "yandex.disk", "pcloud", "mega",
)


def _warn_if_sync_folder(path: Path) -> None:
    """저장 경로가 흔한 동기화 폴더 패턴이면 경고한다(SEC-4) — 거부는 하지 않는다.

    동기화 폴더의 완전 감지는 불가하고(Syncthing 은 임의 경로) 오탐 차단은 작업을
    막으므로, 차단 대신 경고로 방어선을 둔다. 평문 토큰이 Syncthing/Dropbox/iCloud
    등으로 여러 기기·클라우드에 퍼지는 사고를 환기한다. 0600·git 미추적과 함께 0.2 방어선.
    """
    low = str(path).lower()
    if any(hint in low for hint in _SYNC_FOLDER_HINTS):
        print(
            "[warn] credentials 금고가 동기화 폴더로 보이는 경로에 있습니다 — "
            "평문 토큰이 Syncthing/Dropbox/iCloud 등으로 퍼질 수 있습니다. "
            "$XDG_DATA_HOME 를 로컬 전용 경로로 두세요(SEC-4).",
            file=sys.stderr)


def _check_ident(value: str, what: str) -> str:
    """식별자(team/key) 검증 — traversal·인젝션 차단. 위반 시 **값 없는** ValueError."""
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        # ⚠️ value(토큰일 수 있는 key 가 아니라 식별자지만) 자체를 메시지에 담지 않는다 —
        #    마스킹 철칙 일관성: 사용자 입력 echo 0.
        raise ValueError(f"invalid {what} identifier (allowed: [A-Za-z0-9_.-])")
    # `.`·`..` 같은 순수 dot 식별자는 화이트리스트 정규식을 통과하나 traversal/현재디렉토리
    # 의미를 가지므로 명시적으로 거부(파일명/경로 안전).
    if set(value) == {"."}:
        raise ValueError(f"invalid {what} identifier (allowed: [A-Za-z0-9_.-])")
    return value


def _check_scope(scope: str) -> str:
    if scope not in _SCOPES:
        raise ValueError(f"invalid scope (allowed: {', '.join(_SCOPES)})")
    return scope


def _secure_dir(d: Path) -> None:
    """디렉토리를 0700 으로 생성/보정 — 비밀 금고 부모는 소유자 전용."""
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(_DIR_MODE)
    except OSError:
        # 권한 보정 실패는 비차단(작업을 막지 않는다). 파일 자체 0600 이 1차 방어선.
        pass


def _read_vault(team: str) -> dict:
    """금고 파일 로드 — 부재/파손 시 빈 dict. **예외에 토큰값을 담지 않는다.**

    O_NOFOLLOW 로 연다 — 금고 경로가 심링크면 ELOOP 로 거부(금고 밖 파일을 추종해
    읽는 것을 차단). 같은 UID 공격면(0700)이라 위협모델은 좁지만 비밀 파일 표준 하드닝.
    심링크/부재/IO 오류는 모두 빈 금고로 취급 — 예외·메시지에 경로/토큰을 담지 않는다.
    """
    path = _vault_path(team)
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    except OSError:
        # 심링크면 ELOOP(O_NOFOLLOW), 그 외 IO 오류 — 모두 빈 금고로 무해 처리.
        return {}
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    finally:
        os.close(fd)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # 파손 파일 — 내용(토큰 평문 가능)을 예외에 절대 담지 않는다. 빈 금고로 취급.
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_vault(team: str, data: dict) -> Path:
    """금고 파일을 0600 으로 저장. 디렉토리 0700 보정 후 원자적 기록."""
    path = _vault_path(team)
    _secure_dir(path.parent)
    # 빈 파일 핸들을 0600 으로 먼저 만든 뒤 기록(umask 무관). 기존 파일도 모드 재단언.
    # O_NOFOLLOW: 금고 경로가 심링크면 ELOOP 로 거부 — 심링크를 추종해 금고 밖에
    # 토큰 평문을 기록하는 것을 차단(비밀 파일 표준 하드닝). 메시지에 경로/토큰 없음.
    try:
        fd = os.open(str(path),
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                     _FILE_MODE)
    except OSError:
        # 심링크(ELOOP) 등으로 안전 오픈 실패 — 금고 밖 기록을 막고 토큰 누출 없는
        # 에러로 전파(경로/토큰값 비노출). 호출자는 저장 실패를 인지한다.
        raise OSError("vault path is not a regular file (refusing to write)")
    try:
        os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    # O_CREAT 는 기존 파일 모드를 바꾸지 않으므로 명시적으로 0600 재단언(umask 의존 금지).
    os.chmod(path, _FILE_MODE)
    return path


def store(team: str, scope: str, key: str, token: str) -> Path:
    """자격증명 저장(각자 입력). 같은 (scope,key) 는 덮어쓴다. 반환 = 금고 파일 경로.

    token 값은 **인자로만** 흐르고 예외/로그/반환에 절대 노출되지 않는다(마스킹).
    """
    _check_scope(scope)
    _check_ident(key, "key")
    if not isinstance(token, str):
        raise ValueError("token must be a string")
    _warn_if_sync_folder(_vault_path(team))  # SEC-4: 동기화 폴더면 경고(거부 X)
    data = _read_vault(team)
    data.setdefault(scope, {})
    if not isinstance(data[scope], dict):
        data[scope] = {}
    data[scope][key] = token
    return _write_vault(team, data)


def load(team: str, scope: str, key: str) -> Optional[str]:
    """자격증명 조회. 부재 시 None(예외 아님 — 실패 무해)."""
    _check_scope(scope)
    _check_ident(key, "key")
    data = _read_vault(team)
    section = data.get(scope)
    if not isinstance(section, dict):
        return None
    value = section.get(key)
    return value if isinstance(value, str) else None


def delete(team: str, scope: str, key: str) -> bool:
    """자격증명 삭제. 실제로 지워졌으면 True, 원래 없으면 False."""
    _check_scope(scope)
    _check_ident(key, "key")
    data = _read_vault(team)
    section = data.get(scope)
    if not isinstance(section, dict) or key not in section:
        return False
    del section[key]
    if not section:
        data.pop(scope, None)
    _write_vault(team, data)
    return True


def list_keys(team: str, scope: str) -> list:
    """scope 안의 키 이름 목록(정렬). **값은 반환하지 않는다**(누출 방지)."""
    _check_scope(scope)
    data = _read_vault(team)
    section = data.get(scope)
    if not isinstance(section, dict):
        return []
    return sorted(section.keys())


def file_mode(team: str) -> Optional[int]:
    """금고 파일의 권한 비트(stat.S_IMODE). 부재 시 None. (0600 실측 테스트용.)"""
    path = _vault_path(team)
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
