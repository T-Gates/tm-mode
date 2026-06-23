#!/usr/bin/env python3
"""teammode — 팀모드 부트스트랩 런처 (pip·curl 진입 스킨; 스펙 "코어 ≠ 스킨").

진입 2종(등가 — 둘 다 이 cli.py 로 위임):
  pip:  pip install "git+https://github.com/T-Gates/teammode" && teammode join <url>
  curl: curl -fsSL https://raw.githubusercontent.com/T-Gates/teammode/main/install.sh | sh -s -- join <url>

  teammode init [OWNER/REPO]   새 팀: 레포 생성(template) → 곧바로 join(clone+셋업)
  teammode join <clone-url>    합류: 팀 레포 clone → 셋업

설계(얇은 런처): 스킬·훅·엔진을 패키지에 번들하지 않는다. clone 된 팀 레포의
`infra/install.py` 를 **subprocess 로 실행**(import 아님) → 모든 `__file__` 기반
리소스 참조가 팀 레포의 실파일을 가리킨다. 패키지 의존성 0(stdlib + git[+gh]).
설치는 결정적, 활성화·검증·브리핑은 들어가서 `tm-onboard`(에이전트)가 맡는다.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
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


def _clone_dir_from_url(url: str) -> Path:
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return Path(name).resolve()


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


def _print_invite(url: str) -> None:
    """팀원 초대 한 줄(pip·curl — 둘 다 동일) 출력. url = 팀 레포 clone URL."""
    print()
    print("팀원에게 아래 명령 중 하나를 공유하세요 (pip·curl 어느 쪽이든 동일합니다):")
    print()
    print(f'  pip:  pip install "git+https://github.com/{TEMPLATE_REPO}" && teammode join {url}')
    print(f'  curl: curl -fsSL https://raw.githubusercontent.com/{TEMPLATE_REPO}'
          f'/main/install.sh | sh -s -- join {url}')


def _done(repo_dir: Path, *, created: bool = False) -> None:
    """셋업 완료 안내. created=True 면 '생성'(init 경유), 아니면 '합류'(join).

    초대 안내는 여기서 안 한다 — init 이 레포 생성 직후 _print_invite 로 출력한다.
    """
    print()
    print(f"팀 {'생성' if created else '합류'} 완료  {repo_dir}")
    print()
    print("설치는 끝났지만 팀모드는 아직 꺼져 있습니다.")
    print(f"다음 단계: {repo_dir} 에서 Claude Code 또는 Codex를 열고")
    print('           "tm-onboard" 또는 "팀모드 켜" 라고 입력하면')
    print("           검증 · 브리핑 · 활성화가 자동으로 진행됩니다.")


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
             f"`teammode join {url}` 로 셋업을 마치세요.")
        return 1

    # ② 팀원 초대 안내 (도입자 = 생성자).
    _print_invite(url)

    # ③ 곧바로 join 으로 — 방금 만든 레포를 본인 머신에 clone+셋업(생성 → 참여, 단일 경로).
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


_ROLES_SUGGESTED = [
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


def _wizard_join(url: str, args, clone_fn=None) -> tuple[Path, str | None, list[str], bool]:
    """TTY 대화형 join wizard 8단계. (dest, member, extra_args, clone_skip) 반환.

    clone_fn: 선택적 콜백 `(url, dest) -> bool`. 단계1·2 확정 후, 단계3(members.md 읽기)
      전에 호출한다. True 반환 시 clone 성공, False 시 오류 처리. None 이면 clone 건너뜀
      (테스트가 members.md를 미리 세팅한 경우·기존 폴더 재사용 등).
    extra 에는 --agent, --role, --register-obsidian 이 들어간다.
    clone_skip=True 이면 git clone 을 건너뛴다(기존 폴더 재사용).
    """
    home = Path.home()

    print("팀에 합류합니다 — 5단계예요.\n")

    while True:  # 7단계에서 n → 전체 재시작
        # ── 1단계: 설치 위치 ──────────────────────────────────────────────
        repo_name = url.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        default_dest = home / "teammode" / repo_name

        print("[1/5] 설치 위치  (팀 레포를 받을 폴더)")
        while True:
            raw = _prompt(f"  ›", str(default_dest))
            dest = Path(raw).expanduser().resolve()
            if dest.exists() and any(dest.iterdir()):
                print(f"  '{dest}' 가 이미 있고 비어있지 않습니다.")
                choice = _prompt("  1) 다른 위치 입력   2) 기존 폴더에 재설치 [1/2]", "1")
                if choice.strip() == "2":
                    # clone skip 플래그를 반환값으로 표시 — sentinel Path 활용
                    dest = dest  # 그대로 사용
                    clone_skip = True
                    break
                # 1 또는 기타 → 다시 위치 입력
            else:
                clone_skip = False
                break

        # ── 2단계: 에이전트 선택 ──────────────────────────────────────────
        installed = _detect_agents_from_install_lib(home)
        all_agents = ["claude", "codex"]
        if not installed:
            print("  설치된 에이전트를 감지할 수 없습니다. 계속 진행합니다.")
        selected_agents: list[str] = list(installed) if installed else []

        print("\n[2/5] 에이전트 선택  (번호=켜고끄기, Enter=확정)")
        while True:
            for i, ag in enumerate(all_agents, 1):
                mark = "x" if ag in selected_agents else " "
                note = "" if ag in installed else "  (미설치)"
                print(f"  [{mark}] {i}) {ag}{note}")
            raw = _prompt("  ›", "")
            if not raw.strip():
                break  # Enter → 현재 선택 그대로 확정 (비-TTY 도 여기서 즉시 확정)
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

        print("\n[3/5] 멤버  (처음 합류하시나요?)")
        choice3 = _prompt("  1) 새로 합류   2) 기존 팀원  ›", "1")
        is_new = choice3.strip() != "2"

        # ── 4단계: 이름 (3단계 안에서 처리) ─────────────────────────────
        if is_new:
            guess = _git_user_name()
            slug = _slugify(guess) if guess else ""
            if not slug:
                # 빈 슬러그: 반복 강제
                while True:
                    val = _prompt("  이름(영문, 필수)  ›").strip()
                    if val:
                        member = val
                        break
            else:
                member = _prompt("  이름(영문)  ›", slug) or slug
        else:
            if existing_members:
                print("  기존 팀원 목록:")
                for i, n in enumerate(existing_members, 1):
                    print(f"    {i}) {n}")
                sel = _prompt("  번호 선택  ›", "1")
                try:
                    idx = int(sel) - 1
                    member = existing_members[idx] if 0 <= idx < len(existing_members) \
                        else existing_members[0]
                except (ValueError, IndexError):
                    member = existing_members[0]
            else:
                print("  (members.md 없음 — 이름을 직접 입력하세요)")
                guess = _git_user_name()
                slug = _slugify(guess) if guess else ""
                member = _prompt("  이름(영문)  ›", slug or None) or None

        # ── 5단계(구 5): 역할 ────────────────────────────────────────────
        print("\n[4/5] 역할  (권장: " + " / ".join(_ROLES_SUGGESTED) + ")")
        role = _prompt("  › (Enter=생략)", "")

        # ── 6단계(구 6): Obsidian ─────────────────────────────────────────
        print("\n[5/5] Obsidian  (볼트에 팀 레포를 연결할까요?)")
        obsidian_raw = _prompt("  › [y/N]", "N")
        register_obsidian = obsidian_raw.strip().lower() == "y"

        # ── 요약 확인 ─────────────────────────────────────────────────────
        print()
        print("── 설치 요약 ─────────────────────────────────────")
        print(f"  위치      : {dest}")
        print(f"  에이전트  : {', '.join(selected_agents) if selected_agents else '(없음)'}")
        print(f"  이름      : {member or '(미지정)'}")
        print(f"  역할      : {role or '(생략)'}")
        print(f"  Obsidian  : {'등록' if register_obsidian else '건너뜀'}")
        print(f"  clone     : {'skip — 기존 폴더 재사용' if clone_skip else '새로 clone'}")
        print("──────────────────────────────────────────────────")
        confirm = _prompt("이대로 진행할까요? [Y/n]", "Y")
        if confirm.strip().lower() == "n":
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

    if is_tty:
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
        dest = Path(args.dir).resolve() if args.dir else None
        cmd = ["git", "clone", args.url] + ([str(dest)] if dest else [])
        print(f"clone 중...  {args.url}")
        if subprocess.run(cmd).returncode != 0:
            _err("clone 실패 — 레포 접근 권한(SSH 키 / `gh auth login`)을 확인하세요.")
            return 1
        if dest is None:
            dest = _clone_dir_from_url(args.url)

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
    rc = _delegate_install(dest, member, extra)
    if rc != 0:
        return rc
    _done(dest, created=created)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="teammode", description="팀모드 부트스트랩 런처")
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
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
