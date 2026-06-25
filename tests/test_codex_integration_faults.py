"""codex 통합 fault injection 6건 검증 (blocker1·major4·minor1).

슬라이스 경계 통합 결함 — 도그푸딩 직전 필수 게이트.

#1 [blocker] confirm 게이트가 실제 경로(normalize 경유)서 안 걸림
#2 [major]   get_token_for_role 죽은 API → 계약 명확화 + 헬퍼 테스트
#3 [major]   async 핸들러 노출되나 await 안 함
#4/#5 (SKILL.md 내용 변경 — 런타임 검증 불필요)
#6 [minor]   manifest 파싱 실패 시 confirm fail-open → fail-closed
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
CONFIRM = REPO / "infra" / "hooks" / "confirm-action.py"
MANIFEST = REPO / "infra" / "hooks" / "manifest.json"
PY = sys.executable

# [P1 삭제] role_server 폐기 — _load_role_server 헬퍼 및 #2/#3 role_server 의존
# 테스트(get_token_for_role·call_tool·async/sync 핸들러)는 제거됐다.
# #1(confirm 게이트 normalize 경유)·#6(fail-closed)·normalize _lookup_entry 테스트는
# role_server 모듈을 import 하지 않고 confirm-action 체인만 검증하므로 보존한다.


# ═══════════════════════════════════════════════════════════════════════
# 헬퍼
# ═══════════════════════════════════════════════════════════════════════

def _make_teammode_payload(server: str, tool: str) -> dict:
    """normalize 경유 테스트용 Claude 원어 PreToolUse 페이로드."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": f"mcp__{server}__{tool}",
        "tool_input": {"title": "test"},
    }


def _run_normalize_then_confirm(tmp_path: Path, server: str, tool: str, marker: str):
    """normalize.py → confirm-action.py 실제 체인 실행.

    normalize 가 Claude 원어를 정규형으로 변환 후 confirm-action.py 를 호출하는
    실제 경로를 subprocess 로 재현.  tmp 팀 루트에 .teammode-active 마커 생성.
    """
    # tmp 팀 루트 + .teammode-active
    team_root = tmp_path / "team"
    team_root.mkdir(exist_ok=True)
    (team_root / ".teammode-active").write_text("")

    payload = _make_teammode_payload(server, tool)
    env = {**os.environ, "TEAMMODE_HOME": str(team_root)}

    proc = subprocess.run(
        [PY, str(NORMALIZE), "confirm-action.py", marker],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env=env,
    )
    return proc


# ═══════════════════════════════════════════════════════════════════════
# #1 [blocker] confirm 게이트가 normalize 경유 실제 경로에서 정상 동작
# ═══════════════════════════════════════════════════════════════════════

# manifest 에 등록된 모든 confirm-action.py 엔트리를 순회
_MANIFEST_ENTRIES = [
    e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))
    if e.get("script") == "confirm-action.py"
    and e.get("event") == "PreToolUse"
    and e.get("match", {}).get("mcp")
]


@pytest.mark.parametrize("entry", _MANIFEST_ENTRIES,
                         ids=["{server}/{tool}".format(**e["match"]["mcp"])
                              for e in _MANIFEST_ENTRIES])
def test_confirm_gate_fires_via_normalize(tmp_path, entry):
    """#1 blocker — normalize 경유 실경로에서 모든 confirm-action 엔트리가 차단(exit≠0).

    수정 전: _lookup_entry 가 첫 번째 엔트리(linear)만 반환 → teammode 엔트리에
    대해 self-filter 가 False → exit 0 통과(버그).
    수정 후: canonical+args 기반 정확한 엔트리 선택 → teammode 엔트리도 차단.
    """
    server = entry["match"]["mcp"]["server"]
    tool = entry["match"]["mcp"]["tool"]
    args = entry.get("args", "")
    marker = args if isinstance(args, str) else (args[0] if args else "")

    proc = _run_normalize_then_confirm(tmp_path, server, tool, marker)
    assert proc.returncode != 0, (
        f"#1 blocker: normalize 경유 {server}/{tool} 차단 기대, "
        f"실제 exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    # confirm-action.py 가 deny JSON 을 stdout 에 출력해야 함
    if proc.stdout.strip():
        out = json.loads(proc.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", (
            f"deny JSON 없음: {proc.stdout}"
        )


def test_confirm_gate_fires_linear_create_issue_via_normalize(tmp_path):
    """#1 blocker 핵심 케이스 — 살아있는 벤더 매처(linear/create_issue)가 normalize 경유로 차단.

    [P2] teammode 단일 서버 매처 폐기 → 입력을 살아있는 벤더 MCP confirm 게이트로 교체.
    런타임 실 도구명은 등록 별칭 `mcp__tm-linear__create_issue` 다(resolve_server_alias).
    normalize 가 정규 서버명 linear 로 환원해 manifest 매처와 일치 → 차단되어야 한다.
    테스트 의도(normalize→confirm 게이트 차단)는 그대로 보존한다.
    """
    proc = _run_normalize_then_confirm(
        tmp_path, "tm-linear", "create_issue",
        "teammode-linear-create-allow",
    )
    assert proc.returncode != 0, (
        f"#1 blocker: tm-linear/create_issue normalize 경유 차단 실패\n"
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_confirm_gate_passes_with_allow_signal_via_normalize(tmp_path):
    """#1 — allow 신호 있으면 normalize 경유도 통과(exit 0)."""
    marker = "teammode-linear-create-allow"
    team_root = tmp_path / "team2"
    team_root.mkdir(exist_ok=True)
    (team_root / ".teammode-active").write_text("")
    confirm_dir = team_root / ".teammode-confirm"
    confirm_dir.mkdir()
    (confirm_dir / marker).write_text("")

    # 런타임 실 도구명은 등록 별칭 tm-linear (normalize 가 linear 로 환원).
    payload = _make_teammode_payload("tm-linear", "create_issue")
    proc = subprocess.run(
        [PY, str(NORMALIZE), "confirm-action.py", marker],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode == 0, (
        f"allow 신호 있을 때 통과 기대, exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


# ═══════════════════════════════════════════════════════════════════════
# [P1 삭제] #2/#3 (role_server get_token_for_role·call_tool·async/sync 핸들러)
# 테스트 제거 — role_server 폐기. confirm 게이트(#1)·fail-closed(#6) 는 아래 유지.
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# #6 [minor] manifest 파싱 실패 시 fail-closed
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_fail_closed_when_manifest_missing_with_marker(tmp_path):
    """#6 — manifest 없는 환경에서 marker 인자와 함께 호출 → 차단(exit≠0).

    marker 를 받았는데 manifest 를 못 읽으면 게이트 대상 여부 미판정 → 보수적 차단.
    """
    team_root = tmp_path / "team_no_manifest"
    team_root.mkdir()
    (team_root / ".teammode-active").write_text("")

    # confirm-action.py 를 tmp 에 복사하되, 존재하지 않는 manifest 경로를 가리키도록
    # 환경 조작은 어려우므로, 대신 broken manifest 를 주입한다
    broken_hooks = tmp_path / "hooks"
    broken_hooks.mkdir()
    (broken_hooks / "manifest.json").write_text("not valid json", encoding="utf-8")
    broken_confirm = broken_hooks / "confirm-action.py"
    broken_confirm.write_text(CONFIRM.read_text(encoding="utf-8"), encoding="utf-8")

    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "teammode", "name": "issues_create"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(broken_confirm), "teammode-issues-create-allow"],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode != 0, (
        f"#6: manifest 파싱 실패 + marker 있음 → 차단 기대, "
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_confirm_argv_marker_mismatch_is_denied(tmp_path):
    """전체검수 재검수 — argv_marker 가 manifest_marker 와 다르면 fail-closed deny.

    실제 입력 (server,name) 으로 찾은 manifest target 의 marker 만 신뢰한다.
    오배선/스테일로 다른 도구의 marker 가 argv 로 와도 승인 우회 안 됨.
    codex 실증: linear/create_issue payload + teammode marker → 우회 통과(버그)였던 것.
    """
    team_root = tmp_path / "team_mismatch"
    team_root.mkdir()
    (team_root / ".teammode-active").write_text("")

    # 실제 manifest 를 쓰는 원본 confirm-action.py 직접 호출
    # linear/create_issue 입력인데 argv 로 teammode marker 를 줌 (불일치)
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(CONFIRM), "teammode-issues-create-allow"],  # 불일치 marker
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode == 2, (
        f"argv_marker != manifest_marker → fail-closed deny(exit2) 기대, "
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_confirm_fail_open_when_manifest_missing_without_marker(tmp_path):
    """#6 — manifest 없는 환경에서 marker 없이 호출 → 통과(exit 0).

    marker 없음 = 이 훅이 해당 도구의 게이트로 지정되지 않은 상황 → 통과.
    """
    team_root = tmp_path / "team_no_manifest2"
    team_root.mkdir()
    (team_root / ".teammode-active").write_text("")

    broken_hooks = tmp_path / "hooks2"
    broken_hooks.mkdir()
    (broken_hooks / "manifest.json").write_text("not valid json", encoding="utf-8")
    broken_confirm = broken_hooks / "confirm-action.py"
    broken_confirm.write_text(CONFIRM.read_text(encoding="utf-8"), encoding="utf-8")

    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "teammode", "name": "issues_create"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(broken_confirm)],  # marker 없음
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(team_root)},
    )
    assert proc.returncode == 0, (
        f"#6: marker 없음 + manifest 파싱 실패 → 통과 기대, "
        f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


# ═══════════════════════════════════════════════════════════════════════
# normalize _lookup_entry 단위 테스트 — #1 핵심 로직
# ═══════════════════════════════════════════════════════════════════════

def _load_normalize():
    """normalize 모듈을 동적 로드."""
    norm_path = REPO / "infra" / "agents" / "claude" / "normalize.py"
    spec = importlib.util.spec_from_file_location("_norm_fault_test", norm_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lookup_entry_selects_correct_entry_by_canonical():
    """#1 — _lookup_entry 가 canonical 기반으로 정확한 엔트리를 선택."""
    norm = _load_normalize()

    manifest = [
        # linear 엔트리 (첫 번째)
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-linear-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
        },
        # teammode 엔트리 (두 번째)
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-issues-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "teammode", "tool": "issues_create"}},
        },
    ]

    # teammode canonical
    canonical_teammode = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "teammode", "name": "issues_create"},
    }

    entry = norm._lookup_entry(
        manifest, "confirm-action.py", "PreToolUse",
        canonical=canonical_teammode,
        extra_args=["teammode-issues-create-allow"],
    )
    assert entry is not None, "_lookup_entry 가 None 반환"
    assert entry["args"] == "teammode-issues-create-allow", (
        f"teammode 엔트리 기대, 실제={entry['args']}"
    )


def test_lookup_entry_selects_linear_entry_for_linear_canonical():
    """#1 — linear canonical 입력 시 linear 엔트리 선택."""
    norm = _load_normalize()

    manifest = [
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-linear-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "linear", "tool": "create_issue"}},
        },
        {
            "event": "PreToolUse",
            "script": "confirm-action.py",
            "args": "teammode-issues-create-allow",
            "fallback": "runtime",
            "match": {"mcp": {"server": "teammode", "tool": "issues_create"}},
        },
    ]

    canonical_linear = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
    }

    entry = norm._lookup_entry(
        manifest, "confirm-action.py", "PreToolUse",
        canonical=canonical_linear,
        extra_args=["teammode-linear-create-allow"],
    )
    assert entry is not None
    assert entry["args"] == "teammode-linear-create-allow", (
        f"linear 엔트리 기대, 실제={entry['args']}"
    )


def test_lookup_entry_single_entry_no_regression():
    """#1 — 엔트리가 1개일 때 기존 동작 유지(regression 없음)."""
    norm = _load_normalize()

    manifest = [
        {
            "event": "PostToolUse",
            "script": "auto-commit.py",
            "fallback": "runtime",
            "match": {"action": "file_edit"},
        },
    ]

    canonical = {"event": "PostToolUse", "action": "file_edit"}
    entry = norm._lookup_entry(manifest, "auto-commit.py", "PostToolUse",
                               canonical=canonical)
    assert entry is not None
    assert entry["script"] == "auto-commit.py"
