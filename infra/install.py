#!/usr/bin/env python3
"""teammode install.py — 결정적 부트스트랩 + 어댑터 디스패처 (spec/04).

두 모드를 흡수한다(L1-A 결정: 디스패처 인터페이스 보존):

  1. 부트스트랩 (기본):  install.py [--root PATH] [--agent ...] [--member-name ...]
                          [--settings PATH] [--yes] [--update] [--dry-run]
     → preflight·detect·role·scaffold·wire·env·verify (§4). LLM 판단 0(§1.2).

  2. 어댑터 디스패치 (보존):  install.py --<agent> sync [--on|--off] / uninstall
     → agents/<name>/adapter.py 위임(스펙 02 §2 불변식 3). 엔진·테스트 호환 유지.

판정: 첫 인자에 agents/<name>/ 와 일치하는 --<agent> 플래그가 있으면 디스패치, 아니면
부트스트랩. env 불신뢰(§10, P1): 팀 루트는 --root 명시로만, ambient TEAMMODE_HOME 무시.
"""
from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent
AGENTS = INFRA / "agents"

sys.path.insert(0, str(INFRA))
import install_lib as il  # noqa: E402


# ─────────────────────────── 어댑터 디스패치 (보존) ───────────────────────────

def _split_agent(argv):
    """argv 앞쪽 --<agent> 플래그 1개를 떼어내 (agent_name, 나머지 argv)."""
    agent = None
    rest = []
    for arg in argv:
        if agent is None and arg.startswith("--") and (AGENTS / arg[2:]).is_dir():
            agent = arg[2:]
        else:
            rest.append(arg)
    return agent, rest


def _dispatch(agent, rest) -> int:
    """--<agent> → agents/<name>/adapter.py 위임 (분기 로직 0)."""
    adapter_path = AGENTS / agent / "adapter.py"
    if not adapter_path.is_file():
        print(f"[error] {agent} 어댑터 없음: {adapter_path}", file=sys.stderr)
        return 2

    # L1-0 P2 가드(엔진 _resolve_settings 계승): 어댑터의 --settings 기본값이 실
    # ~/.claude/settings.json 이므로, 디스패처가 명시(--settings 격리)/실설치 의사
    # (--install) 없이 위임하면 실 호스트 오염. 둘 다 없으면 거부(exit 2).
    if "--settings" not in rest and "--install" not in rest:
        print("[error] --settings <경로> (격리) 또는 --install (실설치) 중 하나가 "
              "필요합니다. 명시 없이 실 호스트 설정에 쓰지 않습니다.", file=sys.stderr)
        return 2
    # --install 은 디스패처 전용 플래그 — 어댑터로 넘기지 않는다(어댑터는 --settings 만 안다).
    if "--install" in rest:
        rest = [a for a in rest if a != "--install"]

    sys.argv = [str(adapter_path)] + rest
    mod = runpy.run_path(str(adapter_path), run_name="__teammode_adapter__")
    return mod["main"](rest)


# ─────────────────────────── env 수집 (부트스트랩) ───────────────────────────

def _git(args, cwd) -> str | None:
    """git 명령을 조용히 실행해 stdout(strip) 반환. 실패/부재 → None.

    부트스트랩 detect 전용 — 읽기만(원격·user.name). 자격증명 hang 차단.
    """
    try:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        out = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=5, env=env)
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _resolve_root(opts_root) -> Path | None:
    """팀 루트 결정 (§10): --root 명시 우선. 미지정 시 cwd 가 팀 표식 가지면 cwd.

    env(TEAMMODE_HOME) 는 절대 읽지 않는다(P1). 추측 금지 — 표식 없으면 None 반환.
    """
    if opts_root:
        return Path(opts_root).resolve()
    cwd = Path.cwd()
    if il.has_team_marker(cwd):
        return cwd
    return None


def _detect(team_root: Path, home: Path) -> dict:
    """detect (§4 ②): git remote·user.name·설치 에이전트·config 존재. 읽기만."""
    remote = _git(["remote", "get-url", "origin"], cwd=team_root)
    user_name = _git(["config", "user.name"], cwd=team_root)
    # 원격 인증: ls-remote 가 빨리 되면 인증 OK. 실패해도 비치명(경고).
    remote_authed = bool(remote) and _git(
        ["ls-remote", "--exit-code", "origin", "HEAD"], cwd=team_root) is not None
    return {
        "remote_url": remote,
        "team_name_default": il.repo_name_from_remote(remote),
        "git_user_name": user_name,
        "member_name_suggestion": il.suggest_member_name(user_name),
        "agents": il.detect_agents(home),
        "remote_authed": remote_authed,
        "role": il.detect_role(team_root),
    }


def bootstrap(opts: il.Options, *, home: Path, python_version,
              out=print, err=None) -> int:
    """부트스트랩 오케스트레이터 (§4). L1-A: ①preflight ②detect ③role + 계획 출력.

    ④scaffold·⑤wire·⑥env·⑦verify 는 후속 슬라이스(L1-B..F)에서 채운다.
    값 주입(home·python_version)으로 테스트가 호스트를 건드리지 않게 한다.
    """
    if err is None:
        def err(*a, **k):
            print(*a, file=sys.stderr, **k)

    # 팀 루트 (§10, P1 — env 불신뢰·추측 금지)
    team_root = _resolve_root(opts.root)
    if team_root is None:
        err("[error] --root <팀루트> 가 필요합니다(또는 팀 표식 있는 폴더에서 실행). "
            "환경변수(TEAMMODE_HOME)는 읽지 않습니다.")
        return 2

    # ① preflight — 값 주입(호스트 직접 읽기 최소화)
    git_present = shutil.which("git") is not None
    remote_authed = True  # detect 전 잠정. detect 후 실제 값으로 갱신·경고.
    pre = il.preflight(team_root, python_version, git_present, remote_authed)
    if not pre.ok:
        err(f"[error] preflight 실패: {pre.message}")
        return pre.exit_code

    # ② detect ③ role
    det = _detect(team_root, home)
    for w in pre.warnings:
        err(f"[warn] {w}")
    if not det["remote_authed"]:
        err("[warn] git 원격 인증 미확인 — 로컬 L1 은 진행(협업 시 push/pull 막힘).")

    # 계획 출력 (L1-A 단계: 무변경. dry-run 여부 무관하게 아직 부작용 0).
    role = det["role"]
    out(f"[plan] team_root={team_root}")
    out(f"[plan] role={role} "
        f"(team.name 기본='{det['team_name_default']}')")
    out(f"[plan] agents={det['agents'] or '(없음)'}")
    out(f"[plan] member_name="
        f"{opts.member_name or det['member_name_suggestion'] or '(미정 — --member-name 필요)'}")
    if opts.dry_run:
        out("[dry-run] 변경 없음 — 계획만 출력했습니다(settings·memory·env 무접촉).")
    # L1-B 부터 scaffold/wire/env/verify 가 여기 이어진다(현재는 계획까지).
    return 0


# ─────────────────────────── 엔트리 ───────────────────────────

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 디스패치 모드 판정: 첫 토큰들 중 --<agent>(agents/<name>/ 존재) 가 있으면 위임.
    agent, rest = _split_agent(argv)
    if agent is not None:
        return _dispatch(agent, rest)

    # 부트스트랩 모드. --foo 형 미지의 에이전트 플래그가 (디스패치 의도로) 왔는지 구분:
    # sync/uninstall 동사가 있으면 디스패치 의도였으나 에이전트 미지정 → 안내.
    if any(a in ("sync", "uninstall") for a in argv):
        avail = sorted(p.name for p in AGENTS.iterdir() if p.is_dir())
        print(f"[error] 에이전트를 지정하세요: --<agent>. 사용 가능: {avail}",
              file=sys.stderr)
        return 2

    opts = il.parse_args(argv)
    return bootstrap(
        opts,
        home=Path(os.path.expanduser("~")),
        python_version=sys.version_info[:2],
    )


if __name__ == "__main__":
    raise SystemExit(main())
