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

import json
import os
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

INFRA = Path(__file__).resolve().parent
AGENTS = INFRA / "agents"

sys.path.insert(0, str(INFRA))
import install_lib as il  # noqa: E402
import git_ops as _git_ops  # noqa: E402  — scaffold 자동 커밋+push(do_commit) 재사용
import i18n as _i18n  # noqa: E402
# stdout/stderr UTF-8 보장 — Windows native 인코딩(cp949 등)에서 한글 print 깨짐·크래시 방지.
from io_encoding import ensure_utf8_io  # noqa: E402


# ─────────────────────────── 어댑터 디스패치 (보존) ───────────────────────────

# ─────────────────────────── 호스트 되돌리기 (--uninstall, 신규) ───────────────────────────
#
# install 이 호스트에 더한 것을 역순·안전·멱등하게 되돌린다. 재사용 우선(신규 중복 금지):
# off(teammode.cmd_off) + 어댑터 uninstall + env/obsidian 역함수(install_lib).
# memory/(팀 데이터)는 절대 삭제하지 않는다. 실호스트 게이트는 install 과 동일 시맨틱.

# 값을 받는 옵션 플래그 — 다음 토큰을 값으로 소비한다.
_UNINSTALL_VALUE_FLAGS = ("--root", "--settings", "--profile", "--obsidian-config")


def _parse_uninstall(argv):
    """--uninstall argv → opts dict. 알 수 없는 부울 플래그는 무시."""
    opts = {"root": None, "settings": None, "profile": None,
            "obsidian-config": None, "yes": False}
    it = iter(argv)
    for a in it:
        if a == "--uninstall":
            continue
        if a in _UNINSTALL_VALUE_FLAGS:
            opts[a.lstrip("-")] = next(it, None)
        elif a == "--yes":
            opts["yes"] = True
    return opts


# [삭제] --check-mcp query CLI (S2) — 스킬·스펙 어디서도 호출하지 않는 사문이었고
# (테스트만 참조), teammode 가 등록한 alias 만 인식해 "사용자 기존 MCP 감지"(#3)
# 목적에도 못 미쳤다. 감지는 어댑터 install-mcp 내부 후보 안내로 흡수(codex 문답
# 2026-07-03 R2-5 수렴 — 재활용보다 제거+어댑터 helper 신설이 깨끗).


def _default_profile(platform=None):
    """env 주입 셸 프로파일 기본 경로(미지정 시). 실호스트 게이트가 별도로 보호.

    Windows 는 env 가 셸 프로파일이 아니라 레지스트리(setx)에 살아서 대상 파일 없음 → None.
    platform 미지정 시 sys.platform (W-C: POSIX 가정 제거).
    """
    if il.is_windows(platform):
        return None
    return Path(os.path.expanduser("~/.bashrc"))


def _default_obsidian_config(platform=None) -> Path:
    """obsidian.json 기본 경로(미지정 시) — 플랫폼별 (W-C).

    - linux: $XDG_CONFIG_HOME 또는 ~/.config 하위
    - mac/win: il.obsidian_config_path 가 플랫폼별 해석(Library / AppData\\Roaming)
    platform 미지정 시 sys.platform.
    """
    plat = platform if platform is not None else sys.platform
    if il.is_windows(plat) or plat.startswith("darwin"):
        return il.obsidian_config_path(plat, home=Path(os.path.expanduser("~")))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "obsidian" / "obsidian.json"


def cmd_uninstall(opts, *, platform=None) -> int:
    """install 이 호스트에 더한 것을 역순·안전·멱등 제거 (신규 기능).

    platform 미지정 시 sys.platform (W-D: 윈도우는 reg delete, posix 는 셸 프로파일).

    재사용 우선(신규 중복 금지):
      1. off  — teammode.cmd_off 로 .teammode-active 마커 삭제 + 어댑터 sync(off)
      2. 어댑터 uninstall — settings.json 의 teammode 훅 제거
      3. env 줄 제거 — install_lib.remove_injected_env (우리 표식 줄만)
      4. obsidian 등록 해제 — install_lib.unregister_obsidian_vault (해당 볼트만)

    전부 비치명(이미 없는 것 제거 OK, raise 금지). memory/(팀 데이터)는 무삭제.
    실호스트 쓰기/삭제는 --yes 또는 --settings 게이트(install 과 동일 시맨틱).
    """
    root = opts.get("root")
    if root is None:
        print("[error] --uninstall: --root <team-root> is required. The engine does "
              "not guess the working folder.", file=sys.stderr)
        return 2
    team_root = Path(root).resolve()
    if platform is None:
        platform = sys.platform

    settings = opts.get("settings")
    yes = opts.get("yes")
    # 실호스트 게이트(install 과 동일): --settings(격리) 또는 --yes 없으면 거부.
    if settings is None and not yes:
        print("[error] --uninstall: either --settings <path> (isolated mode) or --yes "
              "(confirm real-install rollback) is required. Real ~/.claude is not "
              "touched without an explicit flag.", file=sys.stderr)
        return 2
    settings_path = settings or os.path.expanduser("~/.claude/settings.json")

    removed = []

    # 1. off (마커 삭제 + sync off) — teammode.cmd_off 재사용
    tm = runpy.run_path(str(ENGINE), run_name="__uninstall_off__")
    marker = team_root / ".teammode-active"
    had_marker = marker.exists()
    try:
        tm["cmd_off"](team_root, settings_path)
    except Exception as e:  # noqa: BLE001 — 되돌리기는 비치명. 다음 단계 계속.
        print(f"[warn] skipped off step (non-fatal): {e}", file=sys.stderr)
    if had_marker and not marker.exists():
        removed.append(".teammode-active marker")

    # 2. 어댑터 uninstall — claude·codex 양쪽 teammode 훅·스킬 제거 (#4: 흔적 0 대칭).
    #    install 은 claude+codex 둘 다 배선하므로 uninstall 도 양쪽을 지워야 한다.
    #    경로: claude 는 받은 settings_path(= 격리면 <iso>/claude/settings.json, 실설치면
    #    ~/.claude/settings.json). codex 는 거기서 파생 — 격리면 <iso>(조부모)에서
    #    agent_settings_path 로 <iso>/codex/config.toml, 실설치면 ~/.codex/config.toml.
    for _agent in ("claude", "codex"):
        try:
            if _agent == "claude":
                _agent_settings = settings_path
                _agent_skills = opts.get("skills_dir") or str(
                    Path(settings_path).parent / "skills")
            else:  # codex
                if settings is not None:
                    _iso_root = Path(settings_path).parent.parent  # <iso>/claude/settings.json → <iso>
                    _agent_settings = str(il.agent_settings_path(
                        "codex", home=Path.home(), settings_override=_iso_root))
                else:
                    _agent_settings = str(il.agent_settings_path(
                        "codex", home=Path.home(), settings_override=None))
                _agent_skills = str(Path(_agent_settings).parent / "skills")
            ad = runpy.run_path(str(AGENTS / _agent / "adapter.py"),
                                run_name=f"__uninstall_{_agent}__")
            Adapter = ad["Adapter"]
            adapter = Adapter(
                agent_dir=str(AGENTS / _agent),
                manifest_path=str(INFRA / "hooks" / "manifest.json"),
                settings_path=_agent_settings,
                team_root=str(INFRA.parent),
                skills_dir=_agent_skills,
            )
            changes = adapter.uninstall()
            if any(c.startswith("[remove]") for c in changes):
                removed.append(f"{_agent} settings teammode hooks")
            # 스킬(정션/심링크/복사) 제거 — install 의 install_skills 역(대칭). 안 하면 정션이
            # 고아로 남아 dangling(윈도우 도그푸딩서 uninstall 후 tm-* 정션 잔존 실측).
            try:
                skill_changes = adapter.uninstall_skills()
                if any(c.startswith("[remove-skill]") for c in skill_changes):
                    removed.append(f"{_agent} skill symlinks/junctions")
            except Exception as e:  # noqa: BLE001 — 비치명, 다음 단계 계속.
                print(f"[warn] skipped {_agent} skill removal (non-fatal): {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] skipped {_agent} adapter uninstall (non-fatal): {e}", file=sys.stderr)

    # 2.5 settings.json env 우리 키 제거 — inject_env_settings 의 역함수(대칭, issue #9b).
    #     install 이 박은 TEAMMODE_MEMBER·TEAMMODE_HOME 만 제거, 남의 env 키 무접촉.
    #     codex 의 home/member 핀은 hook command 블록 소속이라 2단계 블록 제거로 함께 사라짐.
    try:
        if il.remove_env_settings(Path(settings_path),
                                  ("TEAMMODE_MEMBER", "TEAMMODE_HOME")):
            removed.append("settings.json env (TEAMMODE_MEMBER·TEAMMODE_HOME)")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] skipped settings.json env removal (non-fatal): {e}", file=sys.stderr)

    # 3. env 제거 — install_lib.remove_injected_env (우리 표식만)
    #    Windows: reg delete HKCU\Environment(레지스트리). POSIX: 셸 프로파일 우리 줄.
    #    ⚠️ 격리(--settings)면 install 도 실 env 를 안 건드렸으므로(bootstrap ⑥ 스킵)
    #       uninstall 도 실 호스트 env(셸 프로파일/레지스트리)를 건드리지 않는다(대칭·I4b).
    #       단 --profile 명시는 격리에서도 그 경로(테스트용)만 정리 — 실 호스트 무관.
    if settings is not None and not opts.get("profile"):
        print("[env] skipped — isolated mode (--settings): real host env untouched.")
    else:
        profile = (Path(opts["profile"]) if opts.get("profile")
                   else _default_profile(platform=platform))
        try:
            if il.remove_injected_env(profile, platform=platform):
                if il.is_windows(platform):
                    removed.append(f"persistent user env ({il.ENV_VAR}, HKCU\\Environment)")
                else:
                    removed.append(f"env injection line ({profile})")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] skipped env removal (non-fatal): {e}", file=sys.stderr)

    # 4. obsidian 등록 해제 — install_lib.unregister_obsidian_vault (해당 볼트만)
    obs_cfg = (Path(opts["obsidian-config"]) if opts.get("obsidian-config")
               else _default_obsidian_config())
    vault_path = team_root / "memory"
    try:
        if il.unregister_obsidian_vault(obs_cfg, str(vault_path)):
            removed.append(f"obsidian vault registration ({vault_path})")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] skipped obsidian unregister (non-fatal): {e}", file=sys.stderr)

    if removed:
        print("teammode uninstall — removed:")
        for r in removed:
            print(f"  - {r}")
    else:
        print("teammode uninstall — no host changes to revert (already clean).")
    print("  (memory/ team data is preserved.)")
    return 0


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


# 어댑터 서브커맨드(동사) 집합 — 게이트의 value-flag 값 검증에 사용(값이 동사를
# 삼키는 UX 사고 방지: `--config sync` 처럼 값 결손이면 명확히 exit 2).
_ADAPTER_VERBS = ("sync", "uninstall", "install-mcp", "install-skills")


def _flag_value_state(rest, flag):
    """value-flag 의 (존재, 값유효) 판정. 값 유효 = 다음 토큰이 있고 --옵션/동사가 아님."""
    for i, a in enumerate(rest):
        if a == flag:
            nxt = rest[i + 1] if i + 1 < len(rest) else None
            ok = (nxt is not None and not nxt.startswith("--")
                  and nxt not in _ADAPTER_VERBS)
            return True, ok
    return False, False


def _translate_dispatch_args(rest):
    """어댑터로 넘기기 전 디스패처 전용 인자를 번역·제거 (#5·C3). (argv, err) 반환.

    어댑터는 --settings(claude)/--config(codex)/--team-root 등 자기 플래그만 안다.
    부트스트랩 전용 인자를 그대로 넘기면 어댑터 argparse 가 'unrecognized arguments'
    로 깨진다(`--codex uninstall --root .` 사고).
      --install  : 디스패처 전용(실설치 의사) — 게이트로만 쓰고 어댑터엔 안 넘김.
      --root <값> : 어댑터 --team-root 로 **번역**(C3 — 과거의 무언 제거는 사용자
                    의도를 조용히 버리는 footgun). --team-root 와 값이 다르면
                    모호성 거부(err 반환 → exit 2), 같으면 중복 무해.
    """
    out = []
    root_val = None
    root_seen = False
    team_root_val = None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--install":
            i += 1
            continue
        if a == "--root":
            root_seen = True
            if i + 1 < len(rest):
                root_val = rest[i + 1]
                i += 2
            else:
                i += 1
            continue
        if a == "--team-root" and i + 1 < len(rest):
            team_root_val = rest[i + 1]
            out.extend((a, rest[i + 1]))
            i += 2
            continue
        out.append(a)
        i += 1
    if root_seen and root_val is None:
        return None, "[error] --root requires a team-root path value."
    if root_val is not None:
        if team_root_val is not None:
            if Path(root_val).resolve() != Path(team_root_val).resolve():
                return None, ("[error] --root and --team-root values differ "
                              f"(--root={root_val}, --team-root={team_root_val}). "
                              "In dispatch mode use only --team-root.")
            # 같은 값 — --team-root 가 이미 전달되므로 번역 불필요(중복 무해).
        else:
            out = ["--team-root", root_val] + out
    return out, None


def _dispatch(agent, rest) -> int:
    """--<agent> → agents/<name>/adapter.py 위임 (분기 로직 0)."""
    adapter_path = AGENTS / agent / "adapter.py"
    if not adapter_path.is_file():
        print(f"[error] {agent} adapter not found: {adapter_path}", file=sys.stderr)
        return 2

    # L1-0 P2 가드(엔진 _resolve_settings 계승): 어댑터의 설정 기본값이 실 호스트
    # (~/.claude/settings.json·~/.codex/config.toml)이므로, 디스패처가 격리 명시/
    # 실설치 의사(--install) 없이 위임하면 실 호스트 오염 → 거부(exit 2).
    # C1(agent-aware): 격리 의도 플래그는 에이전트 **자신의** 설정 플래그다 —
    # _AGENT_WIRE[agent]["flag"] (claude=--settings, codex=--config). 과거엔
    # --settings 만 인정해 `--codex --config <path> sync` 가 어댑터 도달 전
    # exit 2 였다(internals.md 부록 A.3 known gap — 이 커밋으로 닫음).
    # _AGENT_WIRE 미등록 에이전트는 보수 폴백(--settings)만 인정.
    spec = il._AGENT_WIRE.get(agent)
    iso_flag = spec["flag"] if spec else "--settings"
    present, value_ok = _flag_value_state(rest, iso_flag)
    if not present and "--install" not in rest:
        print(f"[error] either {iso_flag} <path> (isolated) or --install (real install) "
              "is required. Real host settings are not written without an explicit flag.",
              file=sys.stderr)
        return 2
    if present and not value_ok:
        print(f"[error] {iso_flag} requires a path value. "
              f"e.g. install.py --{agent} {iso_flag} <path> sync", file=sys.stderr)
        return 2
    rest, _err = _translate_dispatch_args(rest)
    if _err:
        print(_err, file=sys.stderr)
        return 2

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
            encoding="utf-8", errors="replace", timeout=5, env=env)
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


ENGINE = INFRA / "teammode.py"

# 템플릿(upstream) 추적 — tm-mode 원본 레포. `tm-mode update`(UPSTREAM_REMOTE="upstream")
# 가 이 remote 를 fetch 한 뒤 엔진 경로(infra/)만 파일 동기화(git checkout)로 덮어쓴다.
# merge 가 아니다 — template 로 만든 레포는 teammode 와 unrelated histories(공통 조상 0)라
# merge 가 막히기 때문. origin=자기 레포(출처는 GitHub API 에만)이므로 셋업 때 upstream 을 박아준다.
# A안(상수 하드코딩) — 초기 단계라 config 필드 대신 고정(팀 결정 2026-06-17).
UPSTREAM_URL = "https://github.com/T-Gates/tm-mode.git"
UPSTREAM_REMOTE = "upstream"


def _ensure_upstream(team_root: Path) -> bool:
    """템플릿 추적 remote('upstream') 등록 — 없을 때만(멱등). 실패는 비치명(반환 False)."""
    remotes = (_git(["remote"], team_root) or "").split()
    if UPSTREAM_REMOTE in remotes:
        return False
    _git(["remote", "add", UPSTREAM_REMOTE, UPSTREAM_URL], team_root)
    # 등록 확인(_git 부작용만으론 불확실 — git=ground truth).
    return UPSTREAM_REMOTE in (_git(["remote"], team_root) or "").split()


def _engine_capture(argv):
    """teammode.py 를 subprocess 로 호출해 CompletedProcess 반환(verify 용).

    ⚠️ env 화이트리스트로 ambient TEAMMODE_HOME 누수 차단(check.py 와 동일
    정신, P1 이중 방어). 팀 루트·settings 는 argv 의 명시 인자로만 전달된다.
    """
    passthrough = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
                   "TZ", "PYTHONPATH", "TERM", "XDG_STATE_HOME",
                   # Windows 필수 — 없으면 python subprocess 기동·동작 실패.
                   # (TEAMMODE_HOME 만 차단하면 누수 방어 목적은 유지)
                   "SYSTEMROOT", "SYSTEMDRIVE", "PATHEXT", "COMSPEC",
                   "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USERPROFILE",
                   "HOMEDRIVE", "HOMEPATH")
    env = {k: os.environ[k] for k in passthrough if k in os.environ}
    # encoding 명시: 자식(teammode.py)은 UTF-8 출력인데 부모가 Windows locale(cp949)로
    # decode 하면 한글 배너/greeting 에서 UnicodeDecodeError → _readerthread 크래시.
    return subprocess.run(
        [sys.executable, str(ENGINE)] + list(argv),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=30)


def _engine_call(argv) -> int:
    proc = _engine_capture(argv)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


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
    user_email = _git(["config", "user.email"], cwd=team_root)
    # 원격 인증: ls-remote 가 빨리 되면 인증 OK. 실패해도 비치명(경고).
    remote_authed = bool(remote) and _git(
        ["ls-remote", "--exit-code", "origin", "HEAD"], cwd=team_root) is not None
    return {
        "remote_url": remote,
        "team_name_default": il.repo_name_from_remote(remote),
        "git_user_name": user_name,
        "git_user_email": user_email,
        "member_name_suggestion": il.suggest_member_name(user_name),
        "agents": il.detect_agents(home),
        "remote_authed": remote_authed,
        "role": il.detect_role(team_root),
        "locale": il.detect_host_locale(),
        "timezone": il.detect_host_timezone(),
    }


def _done_message(det: dict) -> str:
    """설치 완료 메시지(현지화). det['locale'] 언어로, 미지원 시 en_US."""
    return _i18n.t("done_installed", _i18n.resolve_lang(det.get("locale")))


def _autocommit_scaffold(team_root: Path, member_name: str, out) -> None:
    """scaffold·members·config 자동 커밋+push. push 실패해도 커밋 보존 +
    sync-warning 마커로 가시화(런타임 auto-commit 와 동일 패턴, 이슈 #5).
    """
    _cr = _git_ops.do_commit(
        str(team_root),
        message=f"team setup: register {member_name} + memory scaffold [auto]",
        push=True, paths=["memory", "team.config.json"])
    if getattr(_cr, "pushed", False):
        _git_ops.clear_sync_warning(str(team_root))
        out("[push] pushed memory/members to the team repo.")
    elif getattr(_cr, "ok", False) or getattr(_cr, "committed", False):
        _detail = getattr(_cr, "detail", "") or "push failed"
        _git_ops.write_sync_warning(str(team_root), _detail)
        out(f"[push] committed — push failed ({_detail}). Run `git push` after checking.")


def _email_is_push_safe(email) -> bool:
    """GitHub push-safe 이메일인지 — noreply 도메인이면 private-email 보호(GH007)와 무관."""
    return bool(email) and str(email).strip().lower().endswith(
        "@users.noreply.github.com")


def _warn_if_email_not_push_safe(det: dict, team_root: Path, err) -> None:
    """git user.email 이 GitHub noreply 가 아니면 GH007(private email push 거부) 위험을
    경고 + 정확한 수정 명령 안내(이슈 #23). **자동 변경은 하지 않는다.**

    왜 자동 설정이 아니라 안내인가: push-safe noreply 주소는
    `<숫자ID>+<username>@users.noreply.github.com` 형식인데, 숫자 ID 는 GitHub API 인증
    조회 없이는 알 수 없고, origin remote 의 소유자(조직/레포)는 커밋 작성자와 다를 수
    있어 오프라인으로 정확한 주소를 유도할 수 없다. 잘못 추정해 repo-local user.email 을
    박으면 더 나쁘다(틀린 주소 → 여전히 거부되거나 신원 오염). global 무단 변경은 절대
    금지. 따라서 안전한 선택은 "위험 경고 + 사용자가 본인 noreply 로 직접 설정하도록
    정확한 명령 안내". (감지는 repo-local→global 을 git 이 알아서 합성한 effective 값.)
    """
    email = det.get("git_user_email")
    remote = (det.get("remote_url") or "").lower()
    # GH007 은 GitHub 한정 — github 원격일 때만 경고(불필요한 노이즈 방지).
    if "github.com" not in remote:
        return
    if _email_is_push_safe(email):
        return
    shown = email or "(not set)"
    err(f"[warn] git user.email='{shown}' is not a GitHub noreply address. "
        f"If 'Keep my email addresses private' is enabled on GitHub, "
        f"push may be rejected with GH007 (private email) and auto-commits pile up locally.")
    err(f"       fix: on GitHub → Settings → Emails, find your noreply address"
        f" (<ID>+<username>@users.noreply.github.com), then set it on this repo:")
    err(f"         git -C {team_root} config user.email "
        f"'<ID>+<username>@users.noreply.github.com'")


def _make_run_adapter():
    """wire 용 run_adapter(agent, verb, flag, path, extra_args) → 어댑터 main 호출 rc.

    어댑터를 격리 import(runpy)해 main(argv) 실행. 동사별로 argv 구성:
      - install-mcp:    [flag, path, *extra_args, "install-mcp"]
      - install-skills: [flag, path, *extra_args, "install-skills"]
      - sync:           [flag, path, "sync", "--on"]
    extra_args 는 wire_agents 가 동사별 게이트로 해석한 추가 글로벌 플래그(예: claude
    install-mcp 의 --mcp-config <격리경로>). 경로는 wire_agents 가 이미 해석함 — 여기선
    그대로 전달하고 argparse 글로벌 플래그(서브커맨드 앞)로 배치한다.
    """
    def run_adapter(agent, verb, flag, path, extra_args=None) -> int:
        adapter_path = AGENTS / agent / "adapter.py"
        if not adapter_path.is_file():
            raise FileNotFoundError(f"{agent} adapter not found: {adapter_path}")
        extra_args = list(extra_args or [])
        # 어댑터의 settings 부모 디렉토리 보장(어댑터는 부모 mkdir 하지만 방어적으로).
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # install-mcp 격리 경로(--mcp-config 등)의 부모도 보장.
        for i in range(0, len(extra_args) - 1, 2):
            if extra_args[i].startswith("--") and "/" in str(extra_args[i + 1]):
                Path(extra_args[i + 1]).parent.mkdir(parents=True, exist_ok=True)
        # 글로벌 플래그(서브커맨드 앞) + 동사 + 동사별 플래그.
        global_flags = [flag, path] + extra_args
        if verb == "sync":
            argv = global_flags + ["sync", "--on"]
        else:
            argv = global_flags + [verb]
        saved = sys.argv[:]
        try:
            sys.argv = [str(adapter_path)] + argv
            mod = runpy.run_path(str(adapter_path), run_name="__teammode_wire__")
            return mod["main"](argv)
        finally:
            sys.argv = saved
    return run_adapter


def register_obsidian(opts: il.Options, *, home: Path, platform: str,
                      now_ms=None, vault_id=None, out=print, err=None) -> int:
    """--register-obsidian 액션 (spec/05, opt-in). 비치명 — 항상 exit 0.

    Obsidian 중앙 obsidian.json 에 memory/ 볼트를 merge 등록한다. 미설치·깨짐 등은
    우아하게 skip(install 흐름 안 막음). 경로는 --obsidian-config 우선, 미지정 시
    플랫폼 기본(주입된 home·platform 으로 해석 — ambient 무신뢰, P1).

    id/ts 는 호스트의 비결정 소스(os.urandom/time)를 *여기서* 만들어 순수 함수에 주입.
    테스트는 install_lib 순수 함수를 직접 결정적으로 검증한다.
    """
    if err is None:
        def err(*a, **k):
            print(*a, file=sys.stderr, **k)

    team_root = _resolve_root(opts.root)
    if team_root is None:
        err("[error] --root <team-root> is required (or run inside a folder with a team "
            "marker). Environment variables (TEAMMODE_HOME) are not read.")
        return 2

    memory_dir = team_root / "memory"

    # 설정 경로: 명시(--obsidian-config) 우선, 미지정 시 플랫폼 기본(주입 home).
    if opts.obsidian_config:
        config_path = Path(opts.obsidian_config)
    else:
        config_path = il.obsidian_config_path(platform, home=home)

    # 비결정 소스를 여기서 생성해 순수 함수에 주입(Date.now/random 직접 호출 금지 — 주입).
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if vault_id is None:
        vault_id = os.urandom(8).hex()  # 16 hex

    res = il.register_obsidian_vault(
        memory_dir, config_path=config_path, vault_id=vault_id, ts=now_ms)

    if res["registered"]:
        out(f"[obsidian] vault registered → {config_path} ({res['reason']}).")
        out(f"[obsidian] open memory/ in Obsidian to browse team memory as-is.")
    else:
        out(f"[obsidian] registration skipped — {res['reason']} (non-fatal, install continues).")
    return 0  # 비치명 — 항상 0


def bootstrap(opts: il.Options, *, home: Path, python_version,
              shell="__env__", platform=None, out=print, err=None) -> int:
    """부트스트랩 오케스트레이터 (§4). ①preflight ②detect ③role ④scaffold ⑤wire ⑥env.

    ⑦verify 는 후속 슬라이스(L1-F)에서 채운다.
    값 주입(home·python_version·shell·platform)으로 테스트가 호스트를 건드리지 않게 한다.
    shell 기본값 "__env__" → os.environ["SHELL"] 에서 셸 종류 감지(테스트는 monkeypatch).
    platform 기본값 None → sys.platform (W-A: 윈도우는 setx, posix 는 셸 프로파일).
    """
    if err is None:
        def err(*a, **k):
            print(*a, file=sys.stderr, **k)
    if platform is None:
        platform = sys.platform

    # 셸 종류 해석 (§9). shell="__env__" → $SHELL 에서(런타임 훅용 env 주입 대상 결정).
    # ⚠️ 이건 *env 주입 대상 셸* 판단일 뿐 — 팀 루트를 env 에서 읽는 것과 무관(§10).
    if shell == "__env__":
        shell = il.detect_shell(os.environ.get("SHELL"))
    elif shell:
        # 경로(슬래시/백슬래시 포함)면 detect_shell 로 종류 추출, 이미 종류면 그대로.
        is_path = "/" in str(shell) or "\\" in str(shell)
        shell = il.detect_shell(shell) if is_path else shell

    # 팀 루트 (§10, P1 — env 불신뢰·추측 금지)
    team_root = _resolve_root(opts.root)
    if team_root is None:
        err("[error] --root <team-root> is required (or run inside a folder with a team "
            "marker). Environment variables (TEAMMODE_HOME) are not read.")
        return 2

    # ① preflight — 값 주입(호스트 직접 읽기 최소화)
    git_present = shutil.which("git") is not None
    remote_authed = True  # detect 전 잠정. detect 후 실제 값으로 갱신·경고.
    pre = il.preflight(team_root, python_version, git_present, remote_authed)
    if not pre.ok:
        err(f"[error] preflight failed: {pre.message}")
        return pre.exit_code

    # GitHub template 로 만든 팀 인스턴스에는 product CI/release workflow 가 복사된다.
    # dry-run 은 아래 계획 출력에서 무접촉으로 끝나고, 실 bootstrap 은 모든 entry path 의
    # chokepoint 이므로 여기서 한 번만 제거한다. product repo/fork 는 git_ops 가 보호한다.
    if not opts.dry_run and _git_ops.is_git_worktree(str(team_root)):
        strip_res = _git_ops.strip_template_workflows(str(team_root))
        if not strip_res.ok:
            err(f"[error] workflow cleanup failed: {strip_res.detail}")
            return 1
        if getattr(strip_res, "pushed", False):
            out("[github] removed product workflows from the team repository.")

    # ② detect ③ role
    det = _detect(team_root, home)
    for w in pre.warnings:
        err(f"[warn] {w}")
    if not det["remote_authed"]:
        err("[warn] git remote auth unverified — local L1 proceeds (push/pull will be blocked for collaboration).")
    # push-safe 이메일 점검(이슈 #23) — GH007 사전 경고(자동 변경 없음, 안내만).
    _warn_if_email_not_push_safe(det, team_root, err)

    # 멤버 이름 결정 (§3·m1): --member-name 우선 → git user.name 제안.
    # 추측 금지: 이름을 못 정하면 exit 3(신원 추측 금지, §12-3).
    role = il.detect_role(team_root, forced=opts.role_intent)
    member_name = opts.member_name or det["member_name_suggestion"]
    # 팀명 우선순위: init 위저드 --team-name(opts.team_name) → 레포명 감지 → 폴더명.
    team_name_default = opts.team_name or det["team_name_default"] or team_root.name

    # 에이전트 집합 결정: --agent 선택 있으면 감지 결과와 교집합, 없으면(auto) 감지 전부.
    _detected = det["agents"]
    if opts.agents:
        # 사용자가 명시한 에이전트 중 실제 설치된 것만 wire.
        _wire_agents = [a for a in opts.agents if a in _detected]
        _missing = [a for a in opts.agents if a not in _detected]
        for _ag in _missing:
            err(f"[warn] --agent {_ag} not detected in the home directory — excluding it.")
    else:
        _wire_agents = _detected  # auto: 감지 전부

    # 계획 출력
    out(f"[plan] team_root={team_root}")
    out(f"[plan] role={role} (team.name default='{team_name_default}')")
    out(f"[plan] agents={_wire_agents or '(none)'}")
    out(f"[plan] member_name={member_name or '(unset)'}")

    if opts.dry_run:
        # clone-and-go: dry-run 을 **동의 게이트**로 — 실호스트에 무엇을 쓸지
        # (settings·MCP·skills·env·훅·autopush·Trust) 실 wire 와 단일 소스로 계산해
        # 전부 보여준다. 에이전트가 이 출력을 사용자에게 제시하고 대화 승인을 받은
        # 뒤에야 --yes 실행이 정당하다(AGENTS.md 첫 접촉 절차).
        try:
            _shell = il.detect_shell(os.environ.get("SHELL"))
        except Exception:  # noqa: BLE001 — 셸 감지 실패는 계획 표시에 비치명
            _shell = None
        _plan = il.plan_install(
            team_root=team_root, agents=_wire_agents,
            member_name=member_name, role=role,
            team_name_default=team_name_default, home=home,
            settings_override=opts.settings, shell=_shell,
            platform=sys.platform,
            # 정직성(codex P2): 같은 인자의 non-dry 실행과 계약 일치 — --yes 없이는
            # wire/env/autopush 전부 건너뛰므로 계획도 그렇게 렌더한다. AGENTS.md 절차가
            # "--yes 를 붙이면 이 계획대로 실행"임을 사용자에게 설명한다.
            real_host_install=(bool(opts.yes) and opts.settings is None))
        for _line in il.render_install_plan(_plan, home=home):
            out(_line)
        out("[dry-run] no changes — plan only (settings/memory/env untouched).")
        return 0

    if not member_name:
        err("[error] cannot determine a member name. Set it with --member-name <name> "
            "(git user.name is also absent, so no guessing).")
        return 3

    # 유사성 가드(시안 사태 방지): 기존 이름과 혼동될 만큼 비슷하면 거부.
    # jonathan↔jonathon 처럼 UNIQUE 는 통과하지만 AI 가 헷갈리는 케이스를 막는다.
    _members_file = team_root / "memory" / "team" / "members.md"
    _similar = il.find_similar_names(member_name, il._member_names(_members_file))
    if _similar:
        err(f"[error] member name '{member_name}' is too similar to existing {_similar} "
            f"(AI confusion risk — jonathan↔jonathon case). Use a more distinct slug "
            f"(e.g. jonh→john). If truly intended, add it to members.md manually.")
        return 3

    # ④ scaffold (§4④·§5·§6, M1·M2·M4) — 멱등. 첫 세션로그 안 씀(M2).
    try:
        il.scaffold_memory(team_root, member_name=member_name, role=role,
                           team_name=team_name_default,
                           timezone=det.get("timezone"),
                           locale=det.get("locale"),
                           identity=det.get("git_user_email"),
                           member_role=opts.role)
    except il.InvalidNameError as e:
        err(f"[error] member name rejected: {e}")
        return 3
    except il.ConflictError as e:
        err(f"[error] name conflict (needs human resolution): {e}")
        return 3
    out(f"[scaffold] memory/ structure and members.md registration done (role={role}).")
    # 템플릿 추적: upstream remote 등록(없을 때만). tm-mode update 가 이걸 fetch 후 infra/ 동기화.
    # 레포 내 git 변경(scaffold 류)이라 --yes 게이트 무관 — dry-run 은 위에서 이미 return.
    if _ensure_upstream(team_root):
        out(f"[upstream] template tracking registered — {UPSTREAM_REMOTE} → {UPSTREAM_URL}")
    # config.members 스키마 비치명 점검 (A2.1) — 위반은 [warn] 만, role 판정 무영향.
    _cfg_after = il.load_config(team_root)
    if isinstance(_cfg_after, dict) and not il.members_are_valid(
            _cfg_after.get("members")):
        err("[warn] the members block in team.config.json does not match the spec "
            "(each entry is a {name, role?} object). Install proceeds — no effect on role detection.")

    # ⑤ wire (§4⑤·§8, M5) — 감지된 에이전트마다 어댑터 install-mcp→sync→install-skills.
    # settings_override: --settings 지정 시 격리 경로. 미지정+실설치 의도면 실호스트.
    settings_override = opts.settings
    if settings_override is None and not opts.yes:
        # 실호스트 쓰기는 명시 의도(--settings 격리 또는 --yes 실설치)에서만(§10, P2 정신).
        # 무인 안전: --yes 없이 실 ~/.claude 에 쓰지 않는다.
        out("[wire] skipped — real-host wiring needs --yes (real install) or --settings "
            "(isolated). Scaffold is done (memory is ready).")
        return 0
    wire = il.wire_agents(
        _wire_agents, home=home, settings_override=settings_override,
        run_adapter=_make_run_adapter(), team_root=team_root,
        member_name=member_name)
    for m in wire.messages:
        out(m)
    if not wire.ok:
        for agent, why in wire.failed:
            err(f"[error] wire failed: {agent} — {why}")
        return wire.exit_code  # 부분 실패 exit 3, 성공분은 롤백 안 함(M5)

    # wire 성공한 에이전트 집합을 team.config.json 에 기록 → on/off 가 재감지 없이 읽는다.
    if wire.wired:
        try:
            if il.write_agents_to_config(team_root, wire.wired):
                out(f"[config] agents={wire.wired} → recorded in team.config.json")
        except Exception as _e:
            err(f"[warn] failed to record agents config (non-fatal): {_e}")

    # settings.json env 에 TEAMMODE_MEMBER·TEAMMODE_HOME 주입 — 가드훅(kb-write-guard)
    # 의 본인 판정 단일 소스 + 훅 팀루트(issue #9b). 셸 프로파일과 달리 settings.json
    # env 라야 셸 종류·프로파일 스냅샷 스테일과 무관하게 훅·도구 환경에 닿는다.
    # settings_override 면 격리 경로에 박힌다.
    # ⚠️ claude 가 배선된 경우만 — codex-only 호스트에 stray ~/.claude/settings.json
    #    을 만들지 않는다(Opus 적대검수 blocker).
    if "claude" in (wire.wired or []):
        try:
            _claude_settings = il.agent_settings_path(
                "claude", home=home, settings_override=settings_override)
            if _claude_settings and il.inject_env_settings(
                    _claude_settings,
                    {"TEAMMODE_MEMBER": member_name,
                     "TEAMMODE_HOME": str(team_root)}):
                out(f"[env] settings.json: TEAMMODE_MEMBER={member_name}, "
                    f"TEAMMODE_HOME={team_root}")
        except Exception as e:
            err(f"[warn] failed to inject settings.json env "
                f"(TEAMMODE_MEMBER/TEAMMODE_HOME) (non-fatal): {e}")

    # ⑥ env 주입 (§9, m2) — 런타임 훅용 TEAMMODE_HOME 을 셸 프로파일에 멱등 1줄.
    # 셸은 $SHELL 에서(주입). 미지원/미감지 셸은 경고만(비치명 — L1 핵심은 메모리+훅).
    # ⚠️ 격리(--settings)면 env 주입도 격리 — 실 호스트 셸 프로파일 무접촉(§10 I4b).
    #   --settings 가 env 격리의 권위: --yes 와 같이 와도 격리 우선(실 프로파일 미접촉).
    #   실 env 주입은 --settings 없는 실설치(--yes)에서만(훅이 TEAMMODE_HOME 찾으려면 필요).
    if settings_override is not None:
        out("[env] skipped — isolated mode (--settings): real host env (shell profile/registry) "
            f"untouched. Set manually if needed: {il.ENV_VAR}={team_root}")
    elif il.is_windows(platform):
        # Windows: 셸 프로파일이 아니라 setx 로 영구 user env(HKCU\Environment).
        env_res = il.inject_env(shell, home, team_root, platform=platform)
        if env_res["injected"]:
            out(f"[env] {il.ENV_VAR} injected into persistent user env (setx, {env_res['profile']}). "
                "Takes effect from new terminals/sessions.")
        else:
            out(f"[env] skipped — {env_res['reason']}. Runtime hooks may not find the team "
                f"root, so setting it manually is recommended: setx {il.ENV_VAR} \"{team_root}\"")
    elif shell:
        env_res = il.inject_env(shell, home, team_root, platform=platform)
        if env_res["injected"]:
            out(f"[env] injected {il.ENV_VAR} into {env_res['profile']} "
                f"({env_res['reason']}).")
        elif env_res["profile"]:
            out(f"[env] {il.ENV_VAR} already set ({env_res['reason']}).")
        else:
            out(f"[env] skipped — {env_res['reason']}. Runtime hooks may not find the team "
                f"root, so setting it manually is recommended: {il.ENV_VAR}={team_root}")
    else:
        out(f"[env] no shell detected — set manually if needed: {il.ENV_VAR}={team_root}")

    # ⑦ verify (§4⑦·B1) — 설치가 정상인지 context --json 으로 확인한다. **팀모드는 켜지
    # 않는다**(on 미호출, 설치 ≠ 활성화). 활성화는 사용자 몫이고, verify 의 on 은
    # cmd_on→auto_update_on_start 로 팀 레포에 upstream 자동 커밋을 남기는 부작용이
    # 있어(이종 적대검수 B1) on/off 왕복을 제거했다 — context 는 마커 없이도 동작한다
    # (state=off 가 정상). ※ 실제 맥락 *주입*은 다음 세션 SessionStart 훅이 한다(여기 아님).
    res_ctx = _engine_capture(["context", "--root", str(team_root), "--json"])
    if res_ctx.returncode != 0:
        err(f"[error] verify: tm-mode context failed (rc={res_ctx.returncode}).")
        return 3
    try:
        ctx = json.loads(res_ctx.stdout)
    except (ValueError, json.JSONDecodeError):
        err("[error] verify: context --json output is not JSON.")
        return 3
    out(_i18n.t("verify_ok", _i18n.resolve_lang(det.get("locale")),
                n=len(ctx.get("members", []))))

    # scaffold·members·config 자동 커밋+push — onboarding 은 "자기 등재가 바로 팀 레포에"가 맞다
    # (팀 결정 2026-06-23, "푸시는 사람" 철학 폐기). 실설치(--yes, 격리 아님)에서만 수행.
    # do_commit 은 push 실패(원격/오프라인/권한)해도 커밋을 보존한다(비치명, 예외 전파 안 함).
    if opts.yes and not opts.settings:
        _autocommit_scaffold(team_root, member_name, out)

    out(_done_message(det))
    return 0


# ─────────────────────────── 엔트리 ───────────────────────────

_HELP_TEXT = """\
usage: install.py [--root PATH] [--member-name NAME] [--role TEXT]
                  [--settings PATH] [--yes] [--update] [--dry-run]
                  [--register-obsidian] [--obsidian-config PATH]
                  [--uninstall]
                  [--<agent> sync|uninstall [--settings PATH] [--install]]

teammode deterministic bootstrap + adapter dispatcher.

Main flags:
  --root PATH          team-root path (required; env not trusted)
  --member-name NAME   session-log author name
  --team-name NAME     init wizard team name (source of team.name/banner/badge)
  --role-intent ROLE   introducer/member decision (introducer|member; set by init/join)
  --role TEXT          role/title (e.g. lead/dev)
  --yes                allow wiring into real ~/.claude/settings.json (real install)
  --settings PATH      isolated settings path (test/CI)
  --dry-run            print the plan only, no changes
  --update             sync infra/ files from upstream (tm-mode)
  --register-obsidian  register memory/ as an Obsidian vault (opt-in)
  --uninstall          reverse what install added to the host

Agent dispatch (e.g. --claude sync):
  --<agent> sync [--on|--off]   turn agent hooks on/off
  --<agent> uninstall           remove agent hooks
  Requires either --settings <isolated-path> or --install (real install) as a flag.

Details: see docs/spec/.
"""


def main(argv=None) -> int:
    # 한글 메시지·verify 가 호출하는 context json 이 비-UTF8 stdout(Windows)에서 깨지거나
    # 크래시하지 않도록 진입 즉시 UTF-8 보장(io_encoding 참조 — 크로스플랫폼 안전).
    ensure_utf8_io()
    argv = list(sys.argv[1:] if argv is None else argv)

    # --help / -h: --root 없어도 usage 출력(exit 0). argparse 와 달리 손파싱이므로
    # 명시 처리 필요 — 없으면 bootstrap이 --root 미지정으로 exit 2 를 냄.
    if "--help" in argv or "-h" in argv:
        print(_HELP_TEXT, end="")
        return 0

    # --uninstall: 호스트 되돌리기 액션(신규). 부트스트랩·디스패치와 별개 분기.
    if "--uninstall" in argv:
        return cmd_uninstall(_parse_uninstall(argv))

    # 디스패치 모드 판정: 첫 토큰들 중 --<agent>(agents/<name>/ 존재) 가 있으면 위임.
    agent, rest = _split_agent(argv)
    if agent is not None:
        return _dispatch(agent, rest)

    # 부트스트랩 모드. --foo 형 미지의 에이전트 플래그가 (디스패치 의도로) 왔는지 구분:
    # sync/uninstall 동사가 있으면 디스패치 의도였으나 에이전트 미지정 → 안내.
    if any(a in ("sync", "uninstall") for a in argv):
        avail = sorted(p.name for p in AGENTS.iterdir() if p.is_dir())
        print(f"[error] specify an agent: --<agent>. Available: {avail}",
              file=sys.stderr)
        return 2

    opts = il.parse_args(argv)
    home = Path(os.path.expanduser("~"))

    # --register-obsidian 단독 액션은 설치 의도(--yes/--settings)가 없을 때만 obsidian 만 등록.
    # ⚠️ onboarding 은 --yes(또는 --settings) + --register-obsidian 으로 온다. 예전엔 여기서
    #    obsidian 만 하고 return 해버려 scaffold·훅·스킬이 통째로 누락됐다(설치 가로채기 버그).
    #    설치 의도가 있으면 bootstrap 을 끝까지 돌리고, 성공 후 obsidian 도 등록한다.
    if opts.register_obsidian and not opts.yes and not opts.settings:
        return register_obsidian(opts, home=home, platform=sys.platform)

    rc = bootstrap(opts, home=home, python_version=sys.version_info[:2])
    if rc == 0 and opts.register_obsidian:
        # onboarding 중 obsidian 옵션 — 설치 성공 후 볼트 등록(비치명, 항상 exit 0).
        register_obsidian(opts, home=home, platform=sys.platform)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
