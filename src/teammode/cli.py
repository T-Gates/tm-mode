#!/usr/bin/env python3
"""teammode — 팀모드 부트스트랩 런처 (pip 진입 스킨; 스펙 "코어 ≠ 스킨").

  teammode init [OWNER/REPO]   새 팀: GitHub template 으로 레포 생성/clone → 셋업
  teammode join <clone-url>    합류: 팀 레포 clone → 셋업

설계(얇은 런처): 스킬·훅·엔진을 패키지에 번들하지 않는다. clone 된 팀 레포의
`infra/install.py` 를 **subprocess 로 실행**(import 아님) → 모든 `__file__` 기반
리소스 참조가 팀 레포의 실파일을 가리킨다. 패키지 의존성 0(stdlib + git[+gh]).
설치는 결정적, 활성화·검증·브리핑은 들어가서 `tm-onboard`(에이전트)가 맡는다.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE_REPO = "T-Gates/teammode"


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


def _slugify(name: str) -> str:
    """영문 소문자·숫자·하이픈만 — 멤버명 제안용(한글 user.name 대비)."""
    s = "".join(c if c.isalnum() else "-" for c in name.lower())
    return "-".join(filter(None, s.split("-")))


def _prompt(label: str, default: str | None = None) -> str:
    """TTY 일 때만 input(). 비-TTY(에이전트/CI/pipe)는 절대 input 호출 안 함(행업 방지)."""
    if not sys.stdin.isatty():
        return default or ""
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or (default or "")


def _resolve_member(opt_member: str | None) -> str | None:
    if opt_member:
        return opt_member
    guess = _git_user_name()
    slug = _slugify(guess) if guess else None
    if sys.stdin.isatty():
        return _prompt("멤버 이름(영문)", slug) or None
    return slug  # 비대화: git 추론값(없으면 install.py 가 재판단/에러 안내)


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
    print("어디에 만들까요?")
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


def _clone_dir_from_url(url: str) -> Path:
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return Path(name).resolve()


def _done(repo_dir: Path, *, joined: bool) -> None:
    print()
    print(f"✅ 팀 {'합류' if joined else '생성'} 완료 — {repo_dir}")
    print("   설치는 됐지만 팀모드는 아직 꺼져 있습니다(설치 ≠ 활성화).")
    print(f"   다음: {repo_dir} 에서 Claude Code(또는 Codex)를 열고")
    print('        "tm-onboard" 또는 "팀모드 켜" 라고 하면 검증·브리핑·활성화를 마칩니다.')
    if not joined:
        try:
            url = subprocess.run(["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            url = ""
        if url:
            print()
            print("   팀원에게 이 한 줄을 공유하세요:")
            print(f"     pipx run teammode join {url}")


def cmd_init(args) -> int:
    if not _have("git"):
        _err("git 이 필요합니다.")
        return 2
    if not _have("gh"):
        _err("새 팀 레포 생성에는 GitHub CLI(gh)가 필요합니다 — https://cli.github.com "
             "(이미 만든 레포면 `teammode join <url>`).")
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
            _err("org/계정을 정할 수 없습니다 — `teammode init OWNER/REPO` 형태로 지정하세요.")
            return 2
        repo = target or _prompt("팀 레포 이름", "team")
    full = f"{owner}/{repo}"
    dest = Path(args.dir).resolve() if args.dir else Path(repo).resolve()

    vis = "--public" if args.public else "--private"
    print(f"[init] {full} 생성(template={TEMPLATE_REPO}) + clone → {dest}")
    rc = subprocess.run(["gh", "repo", "create", full,
                        "--template", TEMPLATE_REPO, vis, "--clone", str(dest)]).returncode
    if rc != 0:
        _err("레포 생성/clone 실패 — 권한·이름 중복을 확인하세요.")
        return rc
    if not (dest / "infra").is_dir():
        alt = Path(repo).resolve()  # gh 가 dest 를 다르게 해석한 경우 fallback
        if (alt / "infra").is_dir():
            dest = alt

    member = _resolve_member(args.member_name)
    extra: list[str] = []
    if args.team_name:
        extra += ["--team-name", args.team_name]
    if args.obsidian:
        extra += ["--register-obsidian"]
    rc = _delegate_install(dest, member, extra)
    if rc != 0:
        return rc
    _done(dest, joined=False)
    return 0


def cmd_join(args) -> int:
    if not _have("git"):
        _err("git 이 필요합니다.")
        return 2
    dest = Path(args.dir).resolve() if args.dir else None
    cmd = ["git", "clone", args.url] + ([str(dest)] if dest else [])
    print(f"[join] clone {args.url}")
    if subprocess.run(cmd).returncode != 0:
        _err("clone 실패 — 레포 접근 권한(SSH 키 / `gh auth login`)을 확인하세요.")
        return 1
    if dest is None:
        dest = _clone_dir_from_url(args.url)
    if not (dest / "infra").is_dir():
        _err(f"clone 된 레포에 infra/ 가 없습니다: {dest}")
        return 3

    member = _resolve_member(args.member_name)
    rc = _delegate_install(dest, member, [])
    if rc != 0:
        return rc
    _done(dest, joined=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="teammode", description="팀모드 부트스트랩 런처")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="새 팀 레포 생성(template) + 셋업")
    pi.add_argument("repo", nargs="?", help="OWNER/REPO 또는 REPO (생략 시 대화형)")
    pi.add_argument("--member-name")
    pi.add_argument("--team-name")
    pi.add_argument("--dir", help="clone 위치(기본: repo 명)")
    pi.add_argument("--obsidian", action="store_true", help="Obsidian 볼트 등록")
    pi.add_argument("--public", action="store_true", help="공개 레포(기본 private)")
    pi.set_defaults(func=cmd_init)

    pj = sub.add_parser("join", help="기존 팀 레포 clone + 셋업")
    pj.add_argument("url", help="팀 레포 clone URL")
    pj.add_argument("--member-name")
    pj.add_argument("--dir", help="clone 위치")
    pj.set_defaults(func=cmd_join)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
