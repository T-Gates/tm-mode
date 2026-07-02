#!/usr/bin/env python3
"""tm-mode — 팀모드 부트스트랩 런처 (pip·curl 진입 스킨; 스펙 "코어 ≠ 스킨").

진입 2종(등가 — 둘 다 이 cli.py 로 위임):
  pip:  pip install "git+https://github.com/T-Gates/tm-mode" && tm-mode join <url>
  curl: curl -fsSL https://raw.githubusercontent.com/T-Gates/tm-mode/main/install.sh | sh -s -- join <url>

  tm-mode init [OWNER/REPO]   새 팀: 레포 생성(template) → 곧바로 join(clone+셋업)
  tm-mode join <clone-url>    합류: 팀 레포 clone → 셋업

설계(얇은 런처): 스킬·훅·엔진을 패키지에 번들하지 않는다. clone 된 팀 레포의
`infra/install.py` 를 **subprocess 로 실행**(import 아님) → 모든 `__file__` 기반
리소스 참조가 팀 레포의 실파일을 가리킨다. 패키지 의존성 0(stdlib + git[+gh]).
설치는 결정적, 활성화·검증·브리핑은 들어가서 `tm-onboard`(에이전트)가 맡는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── POSIX raw-key 위젯 백엔드 (선택적) ────────────────────────────────────────
# termios/tty 는 POSIX 전용 — Windows 에선 ImportError 라, 감싸지 않으면 curl 단일파일
# 실행이 import 단계에서 통째로 죽는다(§7.6/D2). 실패 시 termios=None → 모든 위젯이
# 자동으로 번호 fallback 으로 분기한다.
try:  # pragma: no cover - 플랫폼 의존
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows 등
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

TEMPLATE_REPO = "T-Gates/tm-mode"


def _err(msg: str) -> None:
    print(f"[error] {msg}", file=sys.stderr)


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _git_user_name() -> str | None:
    try:
        r = subprocess.run(["git", "config", "user.name"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # noqa: BLE001 — 추론 실패는 비치명
        return None


def _git_user_email_local_part() -> str | None:
    """git config user.email → @ 앞 부분(비-TTY 빈슬러그 fallback용)."""
    try:
        r = subprocess.run(["git", "config", "user.email"],
                           capture_output=True, text=True, timeout=5)
        email = r.stdout.strip()
        if email and "@" in email:
            local = email.split("@")[0]
            # ASCII 소문자·숫자·하이픈만 남기기
            cleaned = "".join(c if (c.isascii() and (c.isalnum() or c == "-")) else "-"
                              for c in local.lower())
            cleaned = "-".join(filter(None, cleaned.split("-")))
            return cleaned or None
        return None
    except Exception:  # noqa: BLE001
        return None


def _slugify(name: str) -> str:
    """영문 소문자·숫자·하이픈만 — 멤버명 제안용(한글 user.name 대비)."""
    s = "".join(c if (c.isascii() and c.isalnum()) else "-" for c in name.lower())
    return "-".join(filter(None, s.split("-")))


def _prompt(label: str, default: str | None = None) -> str:
    """TTY 일 때만 input(). 비-TTY(에이전트/CI/pipe)는 절대 input 호출 안 함(행업 방지)."""
    if not sys.stdin.isatty():
        return default or ""
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or (default or "")


# ═══════════════════════════════════════════════════════════════════════════
#  색 팔레트 (의미별) — D5. NO_COLOR·TERM=dumb·non-tty 에선 자동 off.
# ═══════════════════════════════════════════════════════════════════════════

_ANSI = {
    "reset": "\x1b[0m",
    "ok": "\x1b[32m",      # 초록 — 성공·완료
    "warn": "\x1b[33m",    # 노랑 — 주의
    "hi": "\x1b[36m",      # 시안 — 강조·명령
    "dim": "\x1b[2m",      # 흐림 — 부가·비활성
}


def _use_color() -> bool:
    """색 사용 여부 게이트. 색만으로 정보전달 금지(텍스트 라벨 병행)이 전제."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    term = os.environ.get("TERM", "")
    if term == "" or term == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return True


def _paint(text: str, kind: str) -> str:
    if not _use_color():
        return text
    return f"{_ANSI[kind]}{text}{_ANSI['reset']}"


def _ok(text: str) -> str:
    return _paint(text, "ok")


def _warn(text: str) -> str:
    return _paint(text, "warn")


def _hi(text: str) -> str:
    return _paint(text, "hi")


def _dim(text: str) -> str:
    return _paint(text, "dim")


# ═══════════════════════════════════════════════════════════════════════════
#  raw-key 위젯 백엔드 (POSIX termios) — A1.
#  키 읽는 순간만 raw, 복원은 try/finally 로 무조건(§7.6 R2).
# ═══════════════════════════════════════════════════════════════════════════


def _raw_capable() -> bool:
    """화살표 위젯을 켜도 되는지 게이트(§7.1·D3).

    조건(전부 AND): termios import 성공, stdin·stdout 둘 다 isatty,
    termios.tcgetattr(stdin) 성공, TERM ∉ {"", dumb}, TM_NO_TUI 미설정.

    pytest 는 stdin 을 가짜로 두고 isatty 만 True 로 강제 패치하므로
    tcgetattr 가 실패(예외) → 반드시 False → 번호 fallback 으로 분기(테스트 보존).
    """
    if termios is None:
        return False
    if os.environ.get("TM_NO_TUI"):
        return False
    term = os.environ.get("TERM", "")
    if term == "" or term == "dumb":
        return False
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:  # noqa: BLE001 — isatty 자체가 깨지면 raw 불가
        return False
    try:
        termios.tcgetattr(sys.stdin.fileno())
    except Exception:  # noqa: BLE001 — 가짜 stdin(pytest)·파이프 등
        return False
    return True


def _read_key() -> str:
    """raw 모드로 키 1개 읽어 의미 토큰 반환.

    반환: "up"/"down"/"enter"/"space"/"abort"(Ctrl-C)/"eof"(Ctrl-D, 빈 read)/그 외 원문자.
    raw 진입은 termios.tcgetattr→tty.setraw, 복원은 try/finally tcsetattr(TCSADRAIN).
    finally 내부도 try 로 감싸 tcsetattr 재실패가 원 예외를 가리지 않게 한다(§7.6).
    """
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)    # 버퍼링 없는 raw 바이트(sys.stdin.read 는 버퍼링 → select 와 불일치)
        if ch == b"":          # EOF(Ctrl-D) — 빈 read. 무한루프 방어.
            return "eof"
        if ch == b"\x03":      # Ctrl-C
            return "abort"
        if ch == b"\x04":      # Ctrl-D
            return "eof"
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b" ":
            return "space"
        if ch == b"\x1b":      # ESC 시퀀스(화살표). os fd 라 select 가드가 정합(ESC 단독 블로킹 방지).
            import select
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                return "esc"
            seq = os.read(fd, 2)
            if seq == b"[A":
                return "up"
            if seq == b"[B":
                return "down"
            if seq == b"[C":
                return "right"
            if seq == b"[D":
                return "left"
            return "esc"
        try:
            return ch.decode("utf-8", "ignore")   # j/k/q 등 일반 키
        except Exception:  # noqa: BLE001
            return ""
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:  # noqa: BLE001 — 복원 재실패가 원 예외를 가리지 않게
            pass


def _render_menu(title: str, hint: str, lines: list[str], cursor: int,
                 *, first: bool) -> None:
    """메뉴를 in-place 로 다시 그린다. first=False 면 직전 출력 줄 수만큼 위로 올려 덮어쓴다.

    lines: 항목 텍스트(커서/마크 제외). cursor: 현재 커서 인덱스.
    """
    # title 이 빈 문자열이면 title 줄을 출력하지 않는다(헤더 중복 방지).
    # 그 경우 redraw 줄 수(total)도 hint + 항목만큼만 잡아야 raw 가 어긋나지 않는다.
    show_title = title != ""
    total = len(lines) + (2 if show_title else 1)  # (title +) hint + 항목
    if not first:
        # 커서를 위로 total 줄 올리고 각 줄을 지운다.
        sys.stdout.write(f"\x1b[{total}A")
    if show_title:
        sys.stdout.write("\x1b[2K" + _hi(title) + "\n")
    sys.stdout.write("\x1b[2K" + _dim(hint) + "\n")
    for i, ln in enumerate(lines):
        prefix = _hi("❯ ") if i == cursor else "  "
        sys.stdout.write(f"\x1b[2K{prefix}{ln}\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════════════
#  통일 위젯 API (A2) — 각자 3-state(§7.1):
#    ① 비-TTY            → input 호출 없이 default 반환
#    ② TTY + raw 불가    → 기존 번호 fallback (input)
#    ③ TTY + raw 가능    → 화살표 위젯
# ═══════════════════════════════════════════════════════════════════════════


def _pick_one(title: str, hint: str, choices: list[str],
              *, default_index: int = 0,
              fallback: "callable | None" = None) -> int:
    """단일 선택(라디오). 선택된 인덱스 반환.

    fallback: raw 불가 시 호출할 번호입력 콜백 `() -> int`. None 이면 내장 번호입력
      (각 항목 출력 후 `_prompt("번호", str(default_index+1))`).
    """
    if not sys.stdin.isatty():
        return default_index
    if not _raw_capable():
        if fallback is not None:
            return fallback()
        for i, c in enumerate(choices, 1):
            print(f"  {i}) {c}")
        sel = _prompt("  번호 선택  ›", str(default_index + 1))
        try:
            idx = int(sel) - 1
        except ValueError:
            return default_index
        return idx if 0 <= idx < len(choices) else default_index
    # raw 메뉴
    cursor = default_index
    first = True
    try:
        sys.stdout.write("\x1b[?25l")  # 커서 hide(try 안 — 예외에도 finally 가 show 복원)
        while True:
            lines = [("◉ " if i == cursor else "◯ ") + c
                     for i, c in enumerate(choices)]
            _render_menu(title, hint, lines, cursor, first=first)
            first = False
            key = _read_key()
            if key in ("up", "k"):
                cursor = (cursor - 1) % len(choices)
            elif key in ("down", "j"):
                cursor = (cursor + 1) % len(choices)
            elif key == "enter":
                return cursor
            elif key in ("abort", "eof"):
                raise KeyboardInterrupt  # Ctrl-C/D = 취소 → main 이 130 종료(승인으로 둔갑 방지)
    finally:
        try:
            sys.stdout.write("\x1b[?25h")  # 커서 show 복원(별도 finally)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def _pick_many(title: str, hint: str, choices: list[str],
               *, selected: list[int] | None = None,
               disabled: set[int] | None = None,
               min_select: int = 0,
               fallback: "callable | None" = None) -> list[int]:
    """복수 선택(체크박스 ◉/◯ 토글). 선택된 인덱스 리스트 반환.

    disabled: 비활성(미설치) 인덱스 — raw 커서는 skip, toggle 무시.
    fallback: raw 불가 시 번호입력 콜백 `() -> list[int]`. None 이면 안전 기본.
    """
    sel = set(selected or [])
    disabled = disabled or set()
    if not sys.stdin.isatty():
        return sorted(sel)
    if not _raw_capable():
        if fallback is not None:
            return fallback()
        return sorted(sel)
    # raw 메뉴 — 첫 커서는 활성 항목으로.
    enabled = [i for i in range(len(choices)) if i not in disabled]
    if not enabled:
        return sorted(sel)
    cursor = enabled[0]
    first = True
    try:
        sys.stdout.write("\x1b[?25l")  # 커서 hide(try 안 — 예외에도 finally 가 show 복원)
        while True:
            lines = []
            for i, c in enumerate(choices):
                mark = "◉ " if i in sel else "◯ "
                label = c if i not in disabled else _dim(c + "  (미설치)")
                lines.append(mark + label)
            _render_menu(title, hint, lines, cursor, first=first)
            first = False
            key = _read_key()
            if key in ("up", "k"):
                # 활성 항목으로만 이동
                pos = enabled.index(cursor) if cursor in enabled else 0
                cursor = enabled[(pos - 1) % len(enabled)]
            elif key in ("down", "j"):
                pos = enabled.index(cursor) if cursor in enabled else 0
                cursor = enabled[(pos + 1) % len(enabled)]
            elif key == "space":
                if cursor not in disabled:  # disabled toggle 무시
                    if cursor in sel:
                        sel.remove(cursor)
                    else:
                        sel.add(cursor)
            elif key == "enter":
                if len(sel) >= min_select:
                    return sorted(sel)
                # min_select 미달 → Enter 무시(최소 선택 강제). 메뉴 유지.
            elif key in ("abort", "eof"):
                raise KeyboardInterrupt  # Ctrl-C/D = 취소 → main 이 130 종료
    finally:
        try:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def _ask_text(label: str, default: str | None = None) -> str:
    """텍스트 입력. raw 가능하면 readline prefill(편집 가능한 기본값),
    import/raw 실패 시 기존 `_prompt` 로 폴백(§7.7).

    게이트는 stdin 만 본다(readline 은 stdout 리다이렉트와 무관).
    """
    if not sys.stdin.isatty():
        return default or ""
    # prefill(편집 가능 기본값)은 GNU readline 전용. macOS libedit(editline)은
    # insert_text/pre_input_hook 가 안 먹어 → _prompt 로 폴백해 기본값([default])을 보여준다.
    if default:
        try:
            import readline
        except ImportError:
            readline = None
        if readline is not None and getattr(readline, "backend", "") == "readline":
            def _hook():
                readline.insert_text(default)
                readline.redisplay()

            readline.set_pre_input_hook(_hook)
            try:
                val = input(f"{label}: ").strip()
            except EOFError:
                val = ""
            finally:
                readline.set_pre_input_hook(None)
            return val or default
    return _prompt(label, default)


def _confirm(title: str, *, default: bool = True,
             fallback: "callable | None" = None) -> bool:
    """예/아니오 확인. §7.3 의미론: default=True, 부정은 n/no 만, 빈입력=default.

    fallback: raw 불가 시 콜백 `() -> bool`. None 이면 내장 Y/n 입력
      (default 그대로, "n"/"no" 만 부정).
    """
    if not sys.stdin.isatty():
        return default
    if not _raw_capable():
        if fallback is not None:
            return fallback()
        suffix = "[Y/n]" if default else "[y/N]"
        raw = _prompt(f"{title} {suffix}", "Y" if default else "N")
        if default:
            return raw.strip().lower() not in ("n", "no")
        return raw.strip().lower() in ("y", "yes")
    # raw 메뉴 — 예/아니오 두 항목 라디오
    yes_idx = 0 if default else 1
    idx = _pick_one(title, "(↑↓ 이동 · Enter 확정)",
                    ["예", "아니오"], default_index=yes_idx)
    return idx == 0


def _resolve_member(opt_member: str | None) -> str | None:
    if opt_member:
        return opt_member
    guess = _git_user_name()
    slug = _slugify(guess) if guess else None
    if sys.stdin.isatty():
        # TTY: 빈 슬러그면 반복 입력 강제
        if not slug:
            while True:
                val = _prompt("멤버 이름(영문, 필수)").strip()
                if val:
                    return val
        return _prompt("멤버 이름(영문)", slug) or None
    # 비-TTY: 빈 슬러그면 git email local-part fallback
    if not slug:
        slug = _git_user_email_local_part()
    return slug  # None 이면 install.py 가 재판단/에러 안내


def _pick_owner() -> str | None:
    """gh 로 개인 계정 + 속한 org 목록을 띄워 번호 선택. **자동 선택 금지**(잘못된 곳에 레포 생성 방지)."""
    if not sys.stdin.isatty():
        return None  # 비대화면 인자(OWNER/REPO)로 받았어야 함
    try:
        me = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        orgs = subprocess.run(["gh", "api", "user/orgs", "--jq", ".[].login"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    choices = [c for c in ([me] + orgs.splitlines()) if c]
    if not choices:
        return None
    print("레포를 어디에 만들까요?  (계정 또는 org를 선택하세요)")
    for i, c in enumerate(choices, 1):
        print(f"  {i}) {c}{' (개인 계정)' if i == 1 else ' (org)'}")
    sel = _prompt("선택", "1")
    try:
        idx = int(sel) - 1
    except ValueError:
        idx = 0
    return choices[idx] if 0 <= idx < len(choices) else choices[0]


def _delegate_install(repo_dir: Path, member: str | None, extra: list[str]) -> int:
    """clone 된 레포의 infra/install.py 를 **그 레포 안에서** 실행(위임).

    이것이 얇은 런처의 핵심 — install.py 의 __file__ 기반 리소스(엔진·어댑터·scaffolds)가
    팀 레포 실파일을 가리키게 되어 패키지 번들이 불필요해진다.
    """
    install_py = repo_dir / "infra" / "install.py"
    if not install_py.is_file():
        _err(f"팀 레포에 infra/install.py 가 없습니다: {install_py}")
        return 3
    argv = [sys.executable, str(install_py), "--root", str(repo_dir), "--yes"]
    if member:
        argv += ["--member-name", member]
    argv += extra
    return subprocess.run(argv).returncode


def _default_dest_from_url(url: str) -> Path:
    """--dir 미지정 시 기본 설치 위치 — TTY wizard 1단계 기본값과 동일(#6).

    과거엔 cwd/<repo> 였다(git clone 기본) — 비-TTY(curl 파이프·CI)에서 현재 폴더에
    레포가 조용히 떨어지는 사고를 막기 위해 ~/teammode/<repo> 로 통일한다.
    """
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return Path.home() / "teammode" / name


def _wait_template_ready(full: str, *, attempts: int = 30, interval: float = 1.0) -> bool:
    """gh template 로 만든 레포의 infra/ 가 채워질 때까지 폴링(비동기 복사 대비).

    `gh repo create --template` 는 레포만 즉시 만들고 내용 복사는 GitHub 백그라운드라,
    생성 직후 clone 하면 빈 레포가 잡힌다(E2E 로 확인). infra/ 가 보일 때까지 대기.
    True=준비됨, False=시간 초과(호출부가 안내·보류).
    """
    print("레포 내용 복사를 기다리는 중입니다...")
    for _ in range(attempts):
        if subprocess.run(["gh", "api", f"repos/{full}/contents/infra"],
                          capture_output=True).returncode == 0:
            return True
        time.sleep(interval)
    return False


def _invite_lines(url: str) -> list[str]:
    """팀원 초대 명령(pip·curl) 두 줄. url = 팀 레포 clone URL.

    터미널에선 둘 다 wizard(#6: curl 은 /dev/tty 재연결), 파이프/CI 는
    기본값 설치 후 tm-onboard 로 마무리.
    """
    return [
        f'  pip:  pip install "git+https://github.com/{TEMPLATE_REPO}" && tm-mode join {url}',
        f'  curl: curl -fsSL https://raw.githubusercontent.com/{TEMPLATE_REPO}'
        f'/main/install.sh | sh -s -- join {url}',
    ]


def _done(repo_dir: Path, *, created: bool = False, url: str | None = None) -> None:
    """셋업 완료 안내(D6 통일 끝 블록).

    created=True 면 '생성'(init 경유), 아니면 '합류'(join).
    url 이 주어지면(init·join 공통) **①팀원 초대 명령(url 포함) ②tm-onboard 실행**을
    둘 다 _hi 강조 + 박스로 보여준다 — 순수 join 합류자도 '다른 팀원 부를 url' 을 보게 됨.
    """
    team_name = _read_team_name(repo_dir) or repo_dir.name
    print()
    print()
    print(_ok(f"🎉 {team_name} 팀 {'생성' if created else '합류'} 완료") + f"  {repo_dir}")
    print()
    print(f"당신의 에이전트에 {_hi('팀 모드')}가 생겼어요 — 켜는 동안 팀원 전체가")
    print("맥락을 공유하고, 팀 메모리를 함께 관리해요.")

    bar = "─" * 58
    # ① 팀 모드 켜기 — 지금 할 행동이라 맨 위.
    print()
    print(bar)
    print("  " + _hi("① 팀 모드 켜기"))
    print("     " + _ok("설치가 끝났습니다!") + "  아직 팀 모드는 꺼져 있어요.")
    print("     에이전트를 열고 " + _hi("/tm-onboard") + " 를 실행하세요.")
    print("     설치 검증과 팀 모드 활성화가 자동으로 진행됩니다.")
    print(bar)

    # ② 팀원 초대 (url 있을 때만) — 명령이 길어 시선 분산되니 맨 아래.
    if url:
        print()
        print(bar)
        print("  ② 팀원 초대 — 아래 명령 중 하나를 공유하세요")
        print("     (둘 다 터미널에선 설치 위저드, 파이프/CI 는 기본값 설치 + tm-onboard 마무리):")
        print()
        for line in _invite_lines(url):
            print("  " + line)
        print(bar)


def cmd_init(args) -> int:
    if not _have("git"):
        _err("git 이 필요합니다.")
        return 2
    if not _have("gh"):
        _err("새 팀 레포 생성에는 GitHub CLI(gh)가 필요합니다 — https://cli.github.com "
             "(이미 만든 레포면 `tm-mode join <url>`).")
        return 2
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        _err("GitHub 인증이 필요합니다 — `gh auth login` 후 다시 실행하세요.")
        return 2

    # OWNER/REPO 결정 (org 자동선택 금지)
    target = args.repo
    if target and "/" in target:
        owner, _, repo = target.partition("/")
    else:
        owner = _pick_owner()
        if not owner:
            _err("org/계정을 정할 수 없습니다 — `tm-mode init OWNER/REPO` 형태로 지정하세요.")
            return 2
        # 팀명을 레포명보다 **먼저** 묻는다 — 배너·상태줄 배지·인사말·레포명기본의 단일 소스.
        # team.config.json 은 팀 레포에 커밋(공유)되므로 창립자가 여기서 한 번 정하면
        # 모든 팀원에게 같은 정체성이 퍼진다.
        if not getattr(args, "team_name", None):
            args.team_name = _prompt(
                "팀명이 뭐예요?  (팀 배너·상태줄 배지·인사말에 쓰여요)", owner) or owner
        repo = target or _prompt("레포 이름 (팀 레포)", f"{_slugify(args.team_name)}-team")
    full = f"{owner}/{repo}"
    vis = "--public" if args.public else "--private"
    # 팀명 미정(OWNER/REPO 직접지정 등 비대화 경로) 폴백 = owner.
    if not getattr(args, "team_name", None):
        args.team_name = owner

    # ① 레포 "생성"만 (--clone 없음) — clone·셋업은 join 이 담당(생성 ↔ 참여 분리).
    # 생성 직전 owner/repo 최종 확인 — 소유자 오선택 방어(엉뚱한 계정에 조용히 생성 방지).
    # 비-TTY(인자로 OWNER/REPO 받은 경우 등)는 자동 진행.
    if sys.stdin.isatty():
        if _prompt(f"📦 새 팀 레포를 '{full}' 에 만듭니다. 맞나요? [Y/n]", "Y").strip().lower() == "n":
            _err(f"취소됨 — 위치를 직접 지정하려면 `tm-mode init <OWNER>/{repo}` 로 다시 실행하세요.")
            return 1
    print(f"{full} 레포를 생성합니다 (template: {TEMPLATE_REPO})")
    rc = subprocess.run(["gh", "repo", "create", full,
                        "--template", TEMPLATE_REPO, vis]).returncode
    if rc != 0:
        _err("레포 생성 실패 — 권한·이름 중복을 확인하세요.")
        return rc

    url = f"https://github.com/{full}.git"

    # ②-pre: template 내용 복사는 GitHub 백그라운드(비동기)다 — 생성 직후엔 빈 레포라
    # 곧바로 clone 하면 infra/ 가 없어 join 이 실패한다(E2E 로 확인). infra/ 가 채워질
    # 때까지 폴링 대기한 뒤 join 으로 넘어간다.
    if not _wait_template_ready(full):
        _err(f"template 반영이 지연됩니다(레포는 생성됨). 잠시 후 "
             f"`tm-mode join {url}` 로 셋업을 마치세요.")
        return 1

    # ② 곧바로 join 으로 — 방금 만든 레포를 본인 머신에 clone+셋업(생성 → 참여, 단일 경로).
    # 팀원 초대 안내는 끝 블록(_done)에서 join 합류자와 통일해 한 번만 출력한다(D6).
    print()
    print("레포가 준비됐습니다. 이어서 본인 머신에 설치(join)합니다.")
    args.url = url
    # init 파서엔 없는, cmd_join(특히 비-TTY 경로)이 참조하는 속성 보강.
    # 셋업 정보는 TTY 면 wizard 가 묻고, 비-TTY 면 이 기본값으로 진행.
    for _attr, _default in (("member_name", None), ("dir", None),
                            ("obsidian", False), ("agent", None), ("role", None)):
        if not hasattr(args, _attr):
            setattr(args, _attr, _default)
    return cmd_join(args, created=True)


ROLES = [
    "developer", "pm", "designer", "researcher",
    "marketer", "ops", "lead",
]


def _detect_agents_from_install_lib(home: Path) -> list[str]:
    """팀 레포 clone 전에도 호출 가능: install_lib.detect_agents를 동적 import 없이 재현.

    install_lib 는 팀 레포 안에 있으므로 join wizard 는 직접 복제 로직을 사용.
    """
    agent_dirs = {"claude": ".claude", "codex": ".codex"}
    found = [name for name, d in agent_dirs.items() if (home / d).is_dir()]
    return sorted(found)


def _parse_members_md(members_file: Path) -> list[str]:
    """memory/team/members.md → 영문 이름 목록 (간단 파싱)."""
    if not members_file.is_file():
        return []
    names = []
    for line in members_file.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("- "):
            body = s[2:].strip()
            # <!-- id: ... --> 주석 제거 후 첫 토큰이 이름
            body = re.sub(r"<!--.*?-->", "", body).strip()
            name = body.split()[0] if body.split() else ""
            if name:
                names.append(name)
    return names


def _read_team_name(dest: Path) -> str | None:
    """clone 된 dest/team.config.json 에서 team.name 을 읽는다.

    파일 없음·JSON 깨짐·필드 없음 등 어떤 실패에도 **raise 하지 않고** None 반환.
    """
    try:
        cfg = json.loads((dest / "team.config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 읽기 실패는 조용히 폴백
        return None
    team = cfg.get("team") if isinstance(cfg, dict) else None
    name = team.get("name") if isinstance(team, dict) else None
    return name if isinstance(name, str) and name.strip() else None


def _wizard_join(url: str, args, clone_fn=None) -> tuple[Path, str | None, list[str], bool]:
    """TTY 대화형 join wizard 5단계 + 최종 확인. (dest, member, extra_args, clone_skip) 반환.

    clone_fn: 선택적 콜백 `(url, dest) -> bool`. 단계1·2 확정 후, 단계3(members.md 읽기)
      전에 호출한다. True 반환 시 clone 성공, False 시 오류 처리. None 이면 clone 건너뜀
      (테스트가 members.md를 미리 세팅한 경우·기존 폴더 재사용 등).
    extra 에는 --agent, --role, --register-obsidian 이 들어간다.
    clone_skip=True 이면 git clone 을 건너뛴다(기존 폴더 재사용).
    """
    home = Path.home()

    print("팀 레포를 설정합니다 (5단계 + 확인)\n")

    while True:  # 7단계에서 n → 전체 재시작
        # ── 1단계: 설치 위치 ──────────────────────────────────────────────
        repo_name = url.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        default_dest = home / "teammode" / repo_name

        print(_hi("[1/5] 설치 위치") + "  (팀 레포를 받을 폴더)")
        while True:
            # 경로 = _ask_text(prefill). raw 불가(테스트 포함)면 _prompt 로 폴백 → 동일 1입력.
            raw = _ask_text("  ›", str(default_dest))
            dest = Path(raw).expanduser().resolve()
            if dest.exists() and any(dest.iterdir()):
                print(f"  {_warn(str(dest))} 가 이미 있고 비어있지 않습니다.")
                # 다른위치/재설치 = _pick_one. fallback 은 기존 번호 1입력 그대로.
                def _dir_fallback() -> int:
                    c = _prompt("  1) 다른 위치 입력   2) 기존 폴더에 재설치 [1/2]", "1")
                    return 1 if c.strip() == "2" else 0
                pick = _pick_one(
                    "", "(↑↓ 이동 · Enter 확정)",
                    ["다른 위치 입력", "기존 폴더에 재설치 (clone 건너뜀)"],
                    default_index=0, fallback=_dir_fallback)
                if pick == 1:
                    clone_skip = True
                    break
                # 0(다른 위치) → 다시 위치 입력
            else:
                clone_skip = False
                break

        # ── 2단계: 에이전트 선택 ──────────────────────────────────────────
        installed = _detect_agents_from_install_lib(home)
        all_agents = ["claude", "codex"]
        if not installed:
            print("  설치된 에이전트를 감지할 수 없습니다. 계속 진행합니다.")
        selected_agents: list[str] = list(installed) if installed else []

        print("\n" + _hi("[2/5] 에이전트 선택") + "  (스페이스=토글, Enter=확정)")

        def _agents_fallback() -> list[int]:
            """raw 불가 시(테스트 포함) 기존 번호 토글 루프 그대로(§7.4 H3, cli:337-360 1:1).

            (a) 빈입력(Enter)까지 무한 토글, (b) 매 토글 input 1회,
            (c) 미설치 토큰 거부, (d) 콤마/공백 다중토큰.
            selected_agents(클로저)를 갱신하고 인덱스 리스트를 반환한다.
            """
            while True:
                for i, ag in enumerate(all_agents, 1):
                    mark = "x" if ag in selected_agents else " "
                    note = "" if ag in installed else "  (미설치)"
                    print(f"  [{mark}] {i}) {ag}{note}")
                raw = _prompt("  ›", "")
                if not raw.strip():
                    break  # Enter → 현재 선택 그대로 확정
                for token in raw.replace(",", " ").split():
                    try:
                        idx = int(token) - 1
                    except ValueError:
                        continue
                    if 0 <= idx < len(all_agents):
                        ag = all_agents[idx]
                        if ag not in installed:
                            print(f"  '{ag}'은(는) 미설치라 선택할 수 없습니다.")
                            continue
                        if ag in selected_agents:
                            selected_agents.remove(ag)
                        else:
                            selected_agents.append(ag)
                print()  # 토글 후 갱신된 목록을 루프 상단에서 다시 출력
            return [i for i, ag in enumerate(all_agents) if ag in selected_agents]

        disabled_idx = {i for i, ag in enumerate(all_agents) if ag not in installed}
        sel_idx = _pick_many(
            "",
            "(↑↓ 이동 · 스페이스 토글 · Enter 확정 · 미설치=선택 불가"
            + (" · 최소 1개)" if installed else ")"),
            all_agents,
            selected=[i for i, ag in enumerate(all_agents) if ag in selected_agents],
            disabled=disabled_idx, min_select=(1 if installed else 0),
            fallback=_agents_fallback)
        selected_agents = [all_agents[i] for i in sel_idx]

        # ── clone (단계 2.5): members.md 읽기 전에 실행 → 기존멤버 목록 정확하게 읽힘 ──
        # clone_skip 이거나 clone_fn 이 없으면 건너뜀(테스트·재사용 경로).
        if not clone_skip and clone_fn is not None:
            print(f"clone 중...  {url} → {dest}")
            ok = clone_fn(url, dest)
            if not ok:
                _err("clone 실패 — 레포 접근 권한(SSH 키 / `gh auth login`)을 확인하세요.")
                raise SystemExit(1)

        # ── 3단계: 새/기존 멤버 ───────────────────────────────────────────
        # clone 완료 후 읽으므로 신규 합류 시 기존멤버 목록이 정확히 채워진다.
        members_file = dest / "memory" / "team" / "members.md"
        existing_members = _parse_members_md(members_file)

        print("\n" + _hi("[3/5] 멤버") + "  (처음 합류하시나요?)")

        def _member_kind_fallback() -> int:
            c = _prompt("  1) 새로 합류   2) 기존 팀원  ›", "1")
            return 1 if c.strip() == "2" else 0
        kind = _pick_one(
            "", "(↑↓ 이동 · Enter 확정)",
            ["새로 합류", "기존 팀원"], default_index=0,
            fallback=_member_kind_fallback)
        is_new = kind != 1

        # ── 4단계: 이름 (3단계 안에서 처리) ─────────────────────────────
        if is_new:
            guess = _git_user_name()
            slug = _slugify(guess) if guess else ""
            if not slug:
                # 빈 슬러그: 반복 강제 (default 없음 → _ask_text 가 _prompt 로 폴백, 1입력 유지)
                while True:
                    val = _ask_text("  이름(영문, 필수)  ›").strip()
                    if val:
                        member = val
                        break
            else:
                member = _ask_text("  이름(영문)  ›", slug) or slug
        else:
            if existing_members:
                # 기존팀원 = _pick_one(번호 1입력). "직접입력" 항목 금지(§7.5 H4).
                def _existing_fallback() -> int:
                    print("  기존 팀원 목록:")
                    for i, n in enumerate(existing_members, 1):
                        print(f"    {i}) {n}")
                    sel = _prompt("  번호 선택  ›", "1")
                    try:
                        idx = int(sel) - 1
                    except ValueError:
                        return 0
                    return idx if 0 <= idx < len(existing_members) else 0
                pick = _pick_one(
                    "", "(↑↓ 이동 · Enter 확정)",
                    existing_members, default_index=0, fallback=_existing_fallback)
                member = existing_members[pick] if 0 <= pick < len(existing_members) \
                    else existing_members[0]
            else:
                print("  (members.md 없음 — 이름을 직접 입력하세요)")
                guess = _git_user_name()
                slug = _slugify(guess) if guess else ""
                member = _ask_text("  이름(영문)  ›", slug or None) or None

        # ── 5단계(구 5): 역할 — 자유텍스트 1입력 유지(§7.2 H1, 위젯화 금지) ──
        # ROLES 는 권장목록 표시용 데이터로만 쓴다(_pick_one 금지).
        print("\n" + _hi("[4/5] 역할") + "  (권장: " + _dim(" / ".join(ROLES)) + ")")
        role = _ask_text("  › (Enter=생략)", "")  # default 없음 → _prompt 폴백, 1입력

        # ── 6단계(구 6): Obsidian — _confirm(§7.3: default=True, n/no 만 부정) ──
        print("\n" + _hi("[5/5] Obsidian") + "  (볼트에 팀 레포를 연결할까요?)")

        def _obsidian_fallback() -> bool:
            obsidian_raw = _prompt("  › [Y/n]", "Y")
            return obsidian_raw.strip().lower() != "n"
        register_obsidian = _confirm(
            "Obsidian 볼트에 연결", default=True, fallback=_obsidian_fallback)

        # ── 요약 확인 ─────────────────────────────────────────────────────
        print()
        print("── 설치 요약 ─────────────────────────────────────")
        print(f"  팀        : {url}")
        print(f"  위치      : {dest}")
        print(f"  에이전트  : {', '.join(selected_agents) if selected_agents else '(없음)'}")
        print(f"  이름      : {member or '(미지정)'}")
        print(f"  역할      : {role or '(생략)'}")
        print(f"  Obsidian  : {'등록' if register_obsidian else '건너뜀'}")
        print(f"  clone     : {'skip — 기존 폴더 재사용' if clone_skip else '새로 clone'}")
        print("──────────────────────────────────────────────────")

        def _confirm_fallback() -> bool:
            confirm = _prompt("이대로 진행할까요? [Y/n]", "Y")
            return confirm.strip().lower() != "n"
        proceed = _confirm("이대로 진행할까요?", default=True,
                           fallback=_confirm_fallback)
        if not proceed:
            print("  처음부터 다시 시작합니다.\n")
            continue  # while True 재시작
        break  # 확인 완료

    # ── 8단계: extra 인자 조립 ────────────────────────────────────────────
    extra: list[str] = []
    for ag in selected_agents:
        extra += ["--agent", ag]
    if role:
        extra += ["--role", role]
    if register_obsidian:
        extra += ["--register-obsidian"]

    return dest, member or None, extra, clone_skip


def cmd_join(args, *, created: bool = False) -> int:
    if not _have("git"):
        _err("git 이 필요합니다.")
        return 2

    is_tty = sys.stdin.isatty()
    # 명시 플래그(--dir/--member-name/--agent/--role/--obsidian)가 하나라도 있으면
    # TTY 여도 wizard 를 건너뛴다(플래그 = 비대화 의도, CLI 관례). /dev/tty 재연결(#6)로
    # 터미널의 curl 사용자가 플래그를 줬는데 wizard 가 무시하는 회귀 방지(codex P2).
    # wizard 는 이 플래그들을 소비하지 않으므로, 플래그 존중 = 인자 경로가 유일하다.
    explicit_flags = bool(
        getattr(args, "dir", None) or getattr(args, "member_name", None)
        or getattr(args, "agent", None) or getattr(args, "role", None)
        or getattr(args, "obsidian", False))

    if is_tty and not explicit_flags:
        # ── 대화형: wizard 8단계 ──────────────────────────────────────────
        # clone_fn: 단계2 후·단계3(members.md 읽기) 전에 실행해 기존멤버 목록을 정확히 읽음.
        def _clone_fn(clone_url: str, clone_dest: Path) -> bool:
            return subprocess.run(["git", "clone", clone_url, str(clone_dest)]).returncode == 0

        try:
            dest, member, extra, clone_skip = _wizard_join(args.url, args, clone_fn=_clone_fn)
        except SystemExit as _se:
            return int(_se.code) if _se.code is not None else 1

        if clone_skip:
            print(f"기존 폴더를 재사용합니다: {dest}")
    else:
        # ── 비-TTY: 인자 경로 (input 절대 호출 안 함) ────────────────────
        # --dir 없으면 cwd 가 아니라 wizard 와 같은 ~/teammode/<repo> 가 기본(#6).
        dest = Path(args.dir).resolve() if args.dir else _default_dest_from_url(args.url)
        print(_warn(f"비대화 모드 — 기본값으로 설치합니다 (위치: {dest}). "
                    "세부 설정(역할·Obsidian)은 에이전트에서 tm-onboard 로 마무리하세요."))
        cmd = ["git", "clone", args.url, str(dest)]
        print(f"clone 중...  {args.url}")
        if subprocess.run(cmd).returncode != 0:
            _err("clone 실패 — 레포 접근 권한(SSH 키 / `gh auth login`)을 확인하세요.")
            return 1

        member = _resolve_member(args.member_name)
        extra = []
        if args.agent:
            for ag in args.agent:
                extra += ["--agent", ag]
        if args.role:
            extra += ["--role", args.role]
        if args.obsidian:
            extra += ["--register-obsidian"]

    if not (dest / "infra").is_dir():
        _err(f"clone 된 레포에 infra/ 가 없습니다: {dest}")
        return 3

    # init → join 경유 시 팀명 전달(join 자체 파서엔 --team-name 없음; init 만 줌).
    if getattr(args, "team_name", None):
        extra += ["--team-name", args.team_name]
    # 역할은 동사로 확정(1f): init(created=True)=도입자, join=멤버. 파일추론 대체.
    extra += ["--role-intent", "introducer" if created else "member"]
    rc = _delegate_install(dest, member, extra)
    if rc != 0:
        return rc
    _done(dest, created=created, url=getattr(args, "url", None))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tm-mode", description="팀모드 부트스트랩 런처")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="새 팀 레포 생성(template) → 곧바로 join(clone+셋업)")
    pi.add_argument("repo", nargs="?", help="OWNER/REPO 또는 REPO (생략 시 대화형)")
    pi.add_argument("--team-name", help="팀 이름(미지정 시 레포명 기본)")
    pi.add_argument("--public", action="store_true", help="공개 레포(기본 private)")
    # 셋업 정보(설치위치·에이전트·멤버·역할·obsidian)는 join wizard 가 대화로 묻는다.
    pi.set_defaults(func=cmd_init)

    pj = sub.add_parser("join", help="기존 팀 레포 clone + 셋업")
    pj.add_argument("url", help="팀 레포 clone URL")
    pj.add_argument("--member-name")
    pj.add_argument("--dir", help="clone 위치")
    pj.add_argument("--agent", action="append", metavar="AGENT",
                    help="에이전트 (claude/codex). 비-TTY용; 반복 가능")
    pj.add_argument("--role", help="역할 (비-TTY용)")
    pj.add_argument("--obsidian", action="store_true", help="Obsidian 볼트 등록 (비-TTY용)")
    pj.set_defaults(func=cmd_join)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print()
        print(_warn("취소됐습니다."))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
