#!/usr/bin/env python3
"""Claude Code statusLine 렌더 스크립트 — 팀모드 상태 표시 (크로스플랫폼).

Claude Code의 statusLine.command 로 등록되어, Claude가 상태줄을 렌더링할 때
이 스크립트를 실행한다. stdin으로 세션 JSON을 받을 수 있으나 이 구현에서는 사용하지 않는다.

동작:
  standalone 모드 (TEAMMODE_WRAPPED_CMD 없음, --wrapped 없음):
    - <팀루트>/.teammode-active 있으면: ANSI cyan [<팀명>] 출력
    - 없으면: 무출력(exit 0)

  wrapper 모드 (TEAMMODE_WRAPPED_CMD 환경변수 또는 --wrapped <cmd> 인수):
    1. stdin 데이터를 읽어 원본 명령에 그대로 전달
    2. 원본 명령(shell=True)을 subprocess 실행, stdout 캡처
    3. 팀모드 활성이면 "\\033[1;36m[<팀명>]\\033[0m " 배지를 원본 출력 **맨 앞에** prepend
    4. 팀모드 비활성이면 원본 출력만
    5. 오류 비치명적 — 원본 출력 있으면 사용, 없고 활성이면 [팀명]만

크로스OS: sys.executable + io_encoding.ensure_utf8_io() 패턴 사용 (기존 훅 전체와 일관).
Windows에서 Claude statusLine은 Git Bash / PowerShell로 실행되므로
이 스크립트는 subprocess 재실행 없이 직접 ANSI 출력만 한다.

팀루트 계산:
  infra/agents/claude/teammode_statusline.py
  → parent(claude) → parent(agents) → parent(infra) → parent(팀루트)
  = __file__.resolve().parent × 4
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# stdout UTF-8 보장 — Windows cp949 콘솔 크래시 방지 (기존 훅 전체와 동일 패턴).
# infra/ 가 sys.path 에 없을 수 있으므로 방어적으로 추가.
_HERE = Path(__file__).resolve().parent
_INFRA_DIR = _HERE.parent.parent  # agents/claude → agents → infra
if str(_INFRA_DIR) not in sys.path:
    sys.path.insert(0, str(_INFRA_DIR))
try:
    from io_encoding import ensure_utf8_io  # type: ignore
except ImportError:
    def ensure_utf8_io() -> None:  # 모듈 부재여도 동작(보정만 스킵)
        return


def _team_root() -> Path:
    """런타임 팀루트 — __file__ 기준 정적 계산(env 무신뢰).

    infra/agents/claude/teammode_statusline.py 이므로:
    parent × 1 = infra/agents/claude
    parent × 2 = infra/agents
    parent × 3 = infra
    parent × 4 = 팀루트
    """
    return Path(__file__).resolve().parent.parent.parent.parent


def _read_team_name(team_root: Path) -> str:
    """team.config.json 의 team.name 을 읽는다. 파싱 실패 시 'team' 폴백."""
    config_path = team_root / "team.config.json"
    if not config_path.is_file():
        return "team"
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        name = cfg.get("team", {}).get("name") if isinstance(cfg, dict) else None
        if name and isinstance(name, str):
            return name
    except (ValueError, OSError, AttributeError):
        pass
    return "team"


def _parse_args(argv: list) -> tuple:
    """argv 파싱 — --wrapped <cmd> 인수 추출.

    반환: (wrapped_cmd_or_None, remaining_argv)
    """
    wrapped_cmd = None
    remaining = []
    i = 0
    while i < len(argv):
        if argv[i] == "--wrapped" and i + 1 < len(argv):
            wrapped_cmd = argv[i + 1]
            i += 2
        else:
            remaining.append(argv[i])
            i += 1
    return wrapped_cmd, remaining


def _run_wrapped(wrapped_cmd: str, stdin_data: bytes, root: Path) -> int:
    """wrapper 모드 실행.

    원본 명령을 실행하고 팀모드 활성 여부에 따라 출력을 조합한다.
    항상 exit 0 — statusLine은 비치명적이어야 한다.
    """
    active_file = root / ".teammode-active"
    is_active = active_file.is_file()
    team_name = _read_team_name(root) if is_active else ""

    original_stdout = ""
    try:
        import shutil as _shutil
        _bash = _shutil.which("bash")
        if _bash:
            # bash 명시 실행: shell=False → Windows GitBash/cmd.exe 셸 불일치 방지.
            # bash가 없으면 shell=True 폴백 (하단).
            # BACKLOG: PowerShell-only 윈도우(bash 미설치) — shell=True + cmd.exe로 .sh 실행 불가.
            result = subprocess.run(
                [_bash, "-c", wrapped_cmd],
                shell=False,
                input=stdin_data,
                capture_output=True,
            )
        else:
            # bash 미설치 환경 폴백 — Linux/Mac에서는 발생하지 않음.
            # BACKLOG: PowerShell-only 윈도우는 cmd.exe로 .sh 실행 불가, 추가 대응 필요.
            result = subprocess.run(
                wrapped_cmd,
                shell=True,
                input=stdin_data,
                capture_output=True,
            )
        original_stdout = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
    except Exception:
        pass  # 비치명적

    if is_active:
        badge = f"\033[1;36m[{team_name}]\033[0m"
        if original_stdout:
            print(f"{badge} {original_stdout}")  # 배지를 원본 statusLine 맨 앞에 prepend
        else:
            # 원본 출력 없어도 팀모드 활성이면 팀명 단독 출력
            print(badge)
    else:
        if original_stdout:
            print(original_stdout)
        # 비활성 + 원본 없음 → 무출력

    return 0


def main(argv=None) -> int:
    ensure_utf8_io()

    if argv is None:
        argv = sys.argv[1:]

    # --wrapped 인수 파싱
    wrapped_cmd, _remaining = _parse_args(list(argv))

    # 환경변수 폴백
    if wrapped_cmd is None:
        wrapped_cmd = os.environ.get("TEAMMODE_WRAPPED_CMD")

    # stdin 읽기 — wrapper 모드에서 원본 명령에 전달 (Claude 세션 JSON 등)
    stdin_data = b""
    if not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.buffer.read()
        except Exception:
            pass

    root = _team_root()

    if wrapped_cmd:
        return _run_wrapped(wrapped_cmd, stdin_data, root)

    # standalone 모드 (기존 동작 유지)
    active_file = root / ".teammode-active"

    if not active_file.is_file():
        # 팀모드 비활성 — 무출력
        return 0

    team_name = _read_team_name(root)
    # ANSI cyan bold: \033[1;36m...\033[0m
    print(f"\033[1;36m[{team_name}]\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
