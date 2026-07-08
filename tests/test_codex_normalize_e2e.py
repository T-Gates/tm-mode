"""B1 — Codex 런타임 계약 E2E: 실제 codex normalize 를 서브프로세스로 관통.

기존 테스트는 codex normalize 를 임포트해 코어 함수만 검증했다 — 이 파일은
infra/agents/codex/normalize.py 를 **실행**해 CODEX 원어 wire(stdin) → 정규화 →
공통 훅 → exit/stdout 전파까지 실 경로 전체를 계약으로 못박는다.

케이스:
  1. kb-write-guard deny 회귀 — apply_patch wire 가 memory/team/x.md 를 노리면
     exit 2 + permissionDecision=deny JSON + stderr 사유(라이브 검증 codify).
     (+ 컨트롤: 같은 wire 를 guard 에 직접 넣으면 무동작 — normalize 가 하중을 진다.)
  2. session-log-remind — UserPromptSubmit wire + TEAMMODE_MEMBER env, 스테일 로그
     상태 → additionalContext ≤3줄(compact 계약) + exit 0.
  3. session-start — SessionStart wire + 활성 팀루트 → 유효 JSON,
     additionalContext 존재, exit 0 (+ A2 세션 relay 파일 생성).
  4. 미지/변형 wire — exit 0, stdout 무배출(비-guard 훅의 fail-open-quietly 계약).

격리 규약(test_codex_integration_faults / test_session_unlock_codex 준수):
  - infra/ 트리를 tmp 팀루트로 통째 복사해 실행 — __file__ 기준 팀루트 훅
    (kb-write-guard)이 tmp 루트를 보게 한다(실호스트 무접촉).
  - env 스크럽: CLAUDE_*_SESSION_ID / TEAMMODE_HOME / TEAMMODE_MEMBER 제거.
  - XDG_STATE_HOME·TMPDIR 을 tmp 로 고정(unlock 플래그·remind 상태 격리).
  - .teammode-active 마커는 tmp 팀루트에만 생성.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
PY = sys.executable

# codex wire 는 events.json·normalize.py docstring 기준으로 조립한다:
#   이벤트명은 Claude 와 동일(hook_event_name), file_edit 액션의 도구명은 apply_patch,
#   patch 본문은 tool_input.command (2026-06-21 실 hook stdin 캡처).
CODEX_EVENTS = json.loads((INFRA / "agents" / "codex" / "events.json").read_text(encoding="utf-8"))
APPLY_PATCH_TOOL = CODEX_EVENTS["actions"]["file_edit"]  # "apply_patch"


def _load_workday():
    """엔진 workday 단일 소스로 06시 컷 날짜를 계산(리마인더 상태 시딩용)."""
    spec = importlib.util.spec_from_file_location("_workday_e2e", INFRA / "workday.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_team_root(tmp_path: Path) -> Path:
    """tmp 팀루트: infra/ 실트리 복사 + .teammode-active + memory/ 표식.

    복사본의 codex normalize 를 실행하면 HOOKS_DIR·manifest·events 가 전부 tmp
    루트를 가리키고, kb-write-guard 의 __file__ 기준 _team_root() 도 tmp 루트가
    된다 — 설치본과 동형(레포 워크트리 무접촉).
    """
    root = tmp_path / "team"
    shutil.copytree(
        INFRA, root / "infra",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (root / ".teammode-active").write_text("")
    (root / "memory").mkdir()
    return root


def _scrubbed_env(tmp_path: Path, extra: dict | None = None) -> dict:
    """세션·팀 env 스크럽 + XDG/TMPDIR tmp 격리."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
                        "TEAMMODE_HOME", "TEAMMODE_MEMBER")}
    state_home = tmp_path / "state"
    state_home.mkdir(exist_ok=True)
    env["XDG_STATE_HOME"] = str(state_home)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmpdir)
    if extra:
        env.update(extra)
    return env


def _run_codex_normalize(root: Path, script: str, wire_text: str,
                         env: dict) -> subprocess.CompletedProcess:
    """복사된 실제 codex normalize 를 서브프로세스로 실행 — 어댑터 배선과 동형."""
    normalize = root / "infra" / "agents" / "codex" / "normalize.py"
    return subprocess.run(
        [PY, str(normalize), script],
        input=wire_text, capture_output=True, text=True, env=env,
    )


def _apply_patch_wire(target: str, session_id: str = "codex-e2e-sess") -> dict:
    """CODEX 원어 PreToolUse wire — 실 캡처 형태(tool_input.command 에 patch)."""
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {target}\n"
        "@@\n"
        "+e2e\n"
        "*** End Patch\n"
    )
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,  # A2 — top-level 세션 id 승격 대상
        "tool_name": APPLY_PATCH_TOOL,
        "tool_input": {"command": patch},
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. kb-write-guard deny 회귀 (라이브 검증 codify)
# ═══════════════════════════════════════════════════════════════════════

def test_kb_guard_denies_apply_patch_into_memory_via_codex_normalize(tmp_path):
    """apply_patch wire → memory/team/x.md 타겟 → deny(exit 2 + JSON + stderr 사유)."""
    root = _make_team_root(tmp_path)
    env = _scrubbed_env(tmp_path)
    wire = _apply_patch_wire("memory/team/x.md")

    proc = _run_codex_normalize(root, "kb-write-guard.py", json.dumps(wire), env)

    assert proc.returncode == 2, (
        f"memory/ 직접 편집은 deny(exit 2) 여야 한다: exit={proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    out = json.loads(proc.stdout)  # stdout 은 유효 JSON 하나
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    reason = hso.get("permissionDecisionReason", "")
    assert isinstance(reason, str) and reason.strip(), "deny 사유가 비어 있으면 안 된다"
    assert proc.stderr.strip(), "차단 시 stderr 진단 한 줄이 있어야 한다"


def test_kb_guard_allows_apply_patch_outside_memory_via_codex_normalize(tmp_path):
    """대칭 통과: memory/ 밖 파일 apply_patch 는 exit 0 (정상 편집 무간섭)."""
    root = _make_team_root(tmp_path)
    env = _scrubbed_env(tmp_path)
    wire = _apply_patch_wire("src/app.py")

    proc = _run_codex_normalize(root, "kb-write-guard.py", json.dumps(wire), env)

    assert proc.returncode == 0, (
        f"memory/ 밖 편집은 통과여야 한다: exit={proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")


def test_raw_codex_wire_without_normalize_is_inert(tmp_path):
    """컨트롤: 같은 CODEX 원어 wire 를 guard 에 **직접** 넣으면 무동작(exit 0).

    guard 는 정규 스키마 전용(event 필드) — 원어 hook_event_name 은 인지 못 한다.
    즉 케이스 1 의 deny 는 normalize 변환이 실제로 하중을 진다는 증명이다.
    """
    root = _make_team_root(tmp_path)
    env = _scrubbed_env(tmp_path)
    guard = root / "infra" / "hooks" / "kb-write-guard.py"
    wire = _apply_patch_wire("memory/team/x.md")

    proc = subprocess.run(
        [PY, str(guard)], input=json.dumps(wire),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0
    assert not proc.stdout.strip()


# ═══════════════════════════════════════════════════════════════════════
# 2. session-log-remind — compact 계약 (UserPromptSubmit, 멤버 env)
# ═══════════════════════════════════════════════════════════════════════

def test_session_log_remind_compact_via_codex_normalize(tmp_path):
    """UserPromptSubmit wire + TEAMMODE_MEMBER + 스테일 로그 → ≤3줄 컨텍스트, exit 0."""
    root = _make_team_root(tmp_path)
    member = "alice"
    env = _scrubbed_env(tmp_path, {
        "TEAMMODE_HOME": str(root),
        "TEAMMODE_MEMBER": member,  # env prefix 시뮬레이션 — 멤버 단일 소스
    })

    # 스테일 상태 시딩: 세션로그 파일 없음(mtime 0.0)·오늘 날짜·직전 count 4 →
    # 이번 프롬프트가 5번째가 되어 리마인더가 발화한다(check_reset 회피).
    workday = _load_workday()
    date_str = workday.workday_str(workday.now_kst())
    tag = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    state_file = (Path(env["TMPDIR"])
                  / f"teammode-remind-state-codex-{member}-{tag}.json")
    state_file.write_text(json.dumps({
        "count": 4, "last_mtime": 0.0, "date": date_str,
        "last_strong_remind": 0.0,
    }), encoding="utf-8")

    wire = {"hook_event_name": "UserPromptSubmit",
            "prompt": "작업 계속",
            "session_id": "codex-e2e-remind"}
    proc = _run_codex_normalize(root, "session-log-remind.py", json.dumps(wire), env)

    assert proc.returncode == 0, (
        f"advisory 훅은 exit 0: exit={proc.returncode}\nstderr={proc.stderr!r}")
    out = json.loads(proc.stdout)  # 정규 envelope JSON
    context = out["hookSpecificOutput"]["additionalContext"]
    assert context.strip(), "리마인더 additionalContext 가 있어야 한다"
    n_lines = len(context.splitlines())
    assert n_lines <= 3, (
        f"compact 계약: additionalContext ≤3줄이어야 한다(Codex 화면 도배 방지), "
        f"실제 {n_lines}줄:\n{context}")


# ═══════════════════════════════════════════════════════════════════════
# 3. session-start — 활성 팀루트 맥락 주입
# ═══════════════════════════════════════════════════════════════════════

def test_session_start_injects_context_via_codex_normalize(tmp_path):
    """SessionStart wire → 유효 JSON + additionalContext + exit 0 (+ A2 relay)."""
    root = _make_team_root(tmp_path)
    env = _scrubbed_env(tmp_path, {"TEAMMODE_HOME": str(root)})

    # 세션당 1회 정합(auto-pull)을 스로틀로 스킵 — 방금 정합한 것으로 시딩
    # (tmp 루트는 git 레포가 아니고 네트워크도 안 된다: 결정적으로 우회).
    last_pull = Path(env["XDG_STATE_HOME"]) / "teammode" / "last-pull"
    last_pull.parent.mkdir(parents=True, exist_ok=True)
    last_pull.write_text(repr(time.time()), encoding="utf-8")

    sid = "codex-e2e-start"
    wire = {"hook_event_name": "SessionStart", "session_id": sid}
    proc = _run_codex_normalize(root, "session-start.py", json.dumps(wire), env)

    assert proc.returncode == 0, (
        f"SessionStart 훅은 exit 0: exit={proc.returncode}\nstderr={proc.stderr!r}")
    out = json.loads(proc.stdout)  # stdout 은 유효 JSON
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    context = hso["additionalContext"]
    assert isinstance(context, str) and context.strip(), "세션 맥락이 주입돼야 한다"
    assert "[teammode]" in context

    # A2 — 정규 stdin session_id 가 relay 파일로 영속됐는지(Codex unlock 가용성 장치).
    rh = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    relay = Path(env["XDG_STATE_HOME"]) / "teammode" / "sessions" / rh / sid
    assert relay.is_file(), f"세션 relay 파일이 있어야 한다: {relay}"


# ═══════════════════════════════════════════════════════════════════════
# 4. 미지/변형 wire — fail-open-quietly (비-guard 훅)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("script", ["session-log-remind.py", "session-start.py"])
@pytest.mark.parametrize("wire_text", [
    "",                                              # 빈 stdin
    "not json {{{",                                  # JSON 아님
    "[1, 2, 3]",                                     # top-level 이 object 아님(list)
    '"just-a-string"',                               # top-level 이 object 아님(str)
    json.dumps({"hook_event_name": "TotallyUnknownEvent"}),  # 미지 이벤트
    json.dumps({"tool_name": APPLY_PATCH_TOOL}),     # 이벤트 필드 자체가 없음
], ids=["empty", "not-json", "json-list", "json-string", "unknown-event", "no-event"])
def test_malformed_wire_fails_open_quietly(tmp_path, script, wire_text):
    """미지/변형 wire → exit 0 + stdout 무배출(비-guard 훅은 세션을 절대 안 막는다)."""
    root = _make_team_root(tmp_path)
    env = _scrubbed_env(tmp_path, {"TEAMMODE_HOME": str(root)})

    proc = _run_codex_normalize(root, script, wire_text, env)

    assert proc.returncode == 0, (
        f"{script} + {wire_text!r}: fail-open(exit 0) 이어야 한다 — "
        f"exit={proc.returncode}\nstderr={proc.stderr!r}")
    assert not proc.stdout.strip(), (
        f"{script} + {wire_text!r}: stdout 에 아무것도 내면 안 된다 — "
        f"stdout={proc.stdout!r}")
    assert "Traceback" not in proc.stderr, (
        f"변형 wire 가 traceback 을 내면 안 된다:\n{proc.stderr}")


# ═══════════════════════════════════════════════════════════════════════
# 5. session-start 엔진 업데이트 알림 — Codex 경로에도 동일하게 흐르는지
# ═══════════════════════════════════════════════════════════════════════
#
# session-start.py 의 _build_context() 는 에이전트를 모른다 — 정규 스키마만 보고
# 같은 additionalContext 문자열을 만든다. normalize.py 는 그 stdout 을 그대로
# 재전파(passthrough, 위 main() 참고)하므로, Claude 직접 호출로 검증한
# tests/test_session_start_engine_update_notice.py 의 결과가 Codex wire 경로에도
# 동일하게 나오는지를 여기서 별도로 증명한다(스코프 추가 2 — "Codex 도 되는지 확인").

def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


def test_session_start_engine_update_notice_via_codex_normalize(tmp_path):
    """로컬과 upstream 의 NOTICE.md 가 다르면, Codex wire 경로로도 안내가 나온다.

    _make_team_root 는 git 레포가 아니므로 여기서 별도로 git init 하고, 진짜
    upstream(bare) 을 만들어 fetch 까지 마친 뒤(훅 실행 "전" 1회) codex normalize 를
    통과시킨다 — read_upstream_notice 가 로컬 git 오브젝트만 읽는다는 계약은
    tests/test_session_start_engine_update_notice.py 에서 이미 별도 검증했으므로,
    여기서는 "Codex 경로로도 같은 결과가 나온다"만 좁게 확인한다.
    """
    root = _make_team_root(tmp_path)
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "NOTICE.md").write_text(
        "# teammode\n\n## 2026-07-08\n- 새 upstream 업데이트\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "new notice")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    _git(root, "init")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@t")
    _git(root, "checkout", "-b", "main", check=False)
    (root / "NOTICE.md").write_text(
        "# teammode\n\n## 2026-06-17\n- 옛 로컬 상태\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "team init")
    _git(root, "remote", "add", "upstream", str(upstream))
    _git(root, "fetch", "upstream")  # codex normalize 실행 "전" 1회 — 새 fetch 없음

    env = _scrubbed_env(tmp_path, {"TEAMMODE_HOME": str(root)})
    last_pull = Path(env["XDG_STATE_HOME"]) / "teammode" / "last-pull"
    last_pull.parent.mkdir(parents=True, exist_ok=True)
    last_pull.write_text(repr(time.time()), encoding="utf-8")

    wire = {"hook_event_name": "SessionStart", "session_id": "codex-e2e-notice"}
    proc = _run_codex_normalize(root, "session-start.py", json.dumps(wire), env)

    assert proc.returncode == 0, (
        f"SessionStart 훅은 exit 0: exit={proc.returncode}\nstderr={proc.stderr!r}")
    out = json.loads(proc.stdout)
    context = out["hookSpecificOutput"]["additionalContext"]
    # team.config.json 이 없는 이 픽스처는 en 폴백(PR-i1) — i18n 카탈로그의 영어
    # 문구가 나온다. ko 팀이면 ko 리터럴이 나오므로 양쪽 다 인정한다.
    assert ("엔진 업데이트가 upstream" in context
            or "engine update is available upstream" in context), (
        f"Codex wire 경로로도 엔진 업데이트 안내가 나와야 한다: {context[:300]}")
