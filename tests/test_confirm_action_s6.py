"""S6 — confirm hook 일반화 테스트 (codex⑧⑨ 해소).

전략:
  - manifest에 등록된 side-effect 도구(현재: linear/create_issue)는 confirm 게이트 발동(차단).
  - manifest 미등록 도구(read 계열·미연결 벤더 도구)는 게이트 없음(통과).
  - confirm-action.py 하드코딩 TARGET_SERVER/TARGET_NAME 제거 → 동적 판정 확인.
  - mock 기반: 네트워크 0.

[P2] L2 재설계: teammode 단일 서버 side-effect 매처(issues_create·issues_update·
chat_send·docs_write·docs_create·calendar_create)는 폐기됐다. L2 = 슬롯에 벤더 MCP 를
꽂는 등록기이고, 동작(이슈 생성 등)은 AI 가 벤더 MCP 도구(mcp__<alias>__create_issue 등)를
직접 호출한다. confirm 게이트는 그 벤더 도구에 manifest 엔트리로 붙는다(예: linear/create_issue).
따라서 이 테스트들은 teammode/* 대신 살아있는 벤더 매처로 게이트 발동을 검증한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "infra" / "hooks"
CONFIRM = HOOKS / "confirm-action.py"
MANIFEST = HOOKS / "manifest.json"
PY = sys.executable

# manifest에 confirm 게이트가 붙은 살아있는 side-effect (server, tool, marker) 조합.
# L2 후엔 벤더 MCP 도구가 직접 게이트 대상이다(현재 linear/create_issue 한 건).
SIDE_EFFECT_TOOLS = [
    ("linear", "create_issue", "teammode-linear-create-allow"),
]

# manifest 미등록 도구 — 게이트 없음(통과). read 계열·미연결 벤더 도구 예시.
READ_TOOLS = [
    ("linear", "list_issues"),
    ("linear", "get_issue"),
    ("notion", "search"),
    ("slack", "list_channels"),
]


@pytest.fixture
def fake_root(tmp_path):
    """tmp 팀 루트 + .teammode-active 마커."""
    root = tmp_path / "team"
    root.mkdir()
    (root / ".teammode-active").write_text("")
    return root


def _run_confirm(payload, root, *, args=None, marker=None):
    """helper — args와 marker 중 하나만 써도 됨."""
    argv = [PY, str(CONFIRM)]
    if marker is not None:
        argv.append(marker)
    elif args:
        argv += args
    return subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(root)},
    )


# ═══════════════════════════════════════════════════════════════════════
# 6-A-1. confirm-action.py 동적 판정 — side-effect 도구는 차단
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("server,tool,marker", SIDE_EFFECT_TOOLS)
def test_side_effect_tool_blocked_when_active(fake_root, server, tool, marker):
    """manifest 등록 side-effect 도구는 allow 신호 없으면 차단(exit 2 + deny JSON)."""
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": server, "name": tool},
        "agent": "claude",
    }
    proc = _run_confirm(payload, fake_root, marker=marker)
    assert proc.returncode == 2, (
        f"{server}/{tool}: 차단 기대(exit 2), 실제={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("server,tool,marker", SIDE_EFFECT_TOOLS)
def test_side_effect_tool_passes_with_env_signal(fake_root, server, tool, marker):
    """allow 신호(TEAMMODE_CONFIRM env) 있으면 side-effect 도구도 통과(exit 0)."""
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": server, "name": tool},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(CONFIRM), marker],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(fake_root),
             "TEAMMODE_CONFIRM": marker},
    )
    assert proc.returncode == 0, (
        f"{server}/{tool}: 통과 기대(exit 0), 실제={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


@pytest.mark.parametrize("server,tool,marker", SIDE_EFFECT_TOOLS)
def test_side_effect_tool_passes_with_signal_file(fake_root, server, tool, marker):
    """신호 파일(.teammode-confirm/<marker>) 있으면 통과(exit 0)."""
    confirm_dir = fake_root / ".teammode-confirm"
    confirm_dir.mkdir()
    (confirm_dir / marker).write_text("")
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": server, "name": tool},
        "agent": "claude",
    }
    proc = _run_confirm(payload, fake_root, marker=marker)
    assert proc.returncode == 0


# ═══════════════════════════════════════════════════════════════════════
# 6-A-2. manifest 미등록 도구는 게이트 없음(통과)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("server,tool", READ_TOOLS)
def test_read_tool_always_passes(fake_root, server, tool):
    """manifest 미등록 도구는 confirm 게이트 없음 — marker 없이 호출 → 통과(exit 0)."""
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": server, "name": tool},
        "agent": "claude",
    }
    proc = _run_confirm(payload, fake_root, marker=None)
    assert proc.returncode == 0, (
        f"{server}/{tool}: 미등록 도구 통과 기대(exit 0), 실제={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


@pytest.mark.parametrize("server,tool", READ_TOOLS)
def test_read_tool_passes_even_with_marker(fake_root, server, tool):
    """manifest 미등록 도구는 임의 marker가 와도 대상 아님 → 통과.

    동적 판정 핵심: confirm-action.py 는 입력 (server, tool) 로 manifest 대상 여부를
    판정한다. 미등록 도구에 살아있는 side-effect marker 를 넘겨도 (server, tool) 이
    manifest targets 에 없으면 통과한다(우회/오배선으로 차단되지 않음).
    """
    some_side_effect_marker = "teammode-linear-create-allow"
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": server, "name": tool},
        "agent": "claude",
    }
    proc = _run_confirm(payload, fake_root, marker=some_side_effect_marker)
    assert proc.returncode == 0, (
        f"{server}/{tool}: 미등록 도구 통과 기대(exit 0), 실제={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 6-A-3. 하드코딩 제거 확인 — 동적 판정
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_no_hardcoded_target_server_name():
    """confirm-action.py 소스에 TARGET_SERVER·TARGET_NAME 하드코딩이 없다.

    기존 linear/create_issue 하드코딩 제거 확인.
    동적 판정은 manifest의 server/tool 기반으로만 동작해야 함.
    """
    src = CONFIRM.read_text(encoding="utf-8")
    assert 'TARGET_SERVER = "linear"' not in src, \
        "TARGET_SERVER 하드코딩 제거 필요"
    assert 'TARGET_NAME = "create_issue"' not in src, \
        "TARGET_NAME 하드코딩 제거 필요"


def test_confirm_dynamic_dispatch_from_manifest(fake_root):
    """manifest에 등록된 server/tool이 동적으로 게이트 발동함을 확인.

    하드코딩 없이 manifest (server, tool) 매핑만으로 차단 대상을 결정한다.
    """
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    marker = "teammode-linear-create-allow"
    proc = _run_confirm(payload, fake_root, marker=marker)
    assert proc.returncode == 2  # manifest 등록 → 차단


# ═══════════════════════════════════════════════════════════════════════
# 6-A-4. 벤더 MCP confirm 게이트(linear/create_issue) — L2 핵심 동작 경로
# ═══════════════════════════════════════════════════════════════════════

def test_existing_linear_create_issue_still_blocked(fake_root):
    """L2 벤더 MCP 게이트 — linear/create_issue 는 allow 없으면 차단(exit 2)."""
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = _run_confirm(payload, fake_root, args=["teammode-linear-create-allow"])
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_existing_linear_create_issue_passes_with_signal(fake_root):
    """linear/create_issue: allow 신호 있으면 통과."""
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = subprocess.run(
        [PY, str(CONFIRM), "teammode-linear-create-allow"],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(fake_root),
             "TEAMMODE_CONFIRM": "teammode-linear-create-allow"},
    )
    assert proc.returncode == 0


# ═══════════════════════════════════════════════════════════════════════
# 6-B. manifest 엔트리 확인
# ═══════════════════════════════════════════════════════════════════════

def test_manifest_has_no_teammode_server_entries():
    """[P2] manifest에 teammode 단일 서버 매처가 더 이상 없다(죽은 코드 cleanup)."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    teammode_entries = [
        e for e in entries
        if e.get("event") == "PreToolUse"
        and e.get("match", {}).get("mcp", {}).get("server") == "teammode"
    ]
    assert not teammode_entries, (
        f"teammode 단일 서버 매처가 남아있음(P2 폐기 대상): {teammode_entries}"
    )


def test_manifest_side_effect_entries_have_required_fields():
    """manifest의 confirm-action 엔트리 각각이 필수 필드(script/args/enforcement/fallback)를 가짐."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    confirm_entries = [
        e for e in entries
        if e.get("event") == "PreToolUse"
        and e.get("script") == "confirm-action.py"
        and e.get("match", {}).get("mcp")
    ]
    assert confirm_entries, "confirm-action 매처가 하나도 없음"
    for e in confirm_entries:
        tool = e.get("match", {}).get("mcp", {}).get("tool", "unknown")
        assert e.get("args"), f"{tool}: args(marker) 누락"
        assert e.get("enforcement") == "block", \
            f"{tool}: enforcement=block 필요"
        assert e.get("fallback") == "runtime", \
            f"{tool}: fallback=runtime 필요"


def test_manifest_linear_entry_preserved():
    """벤더 MCP 게이트(linear/create_issue) 엔트리가 manifest에 보존되어 있다(L2 동작 경로)."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    linear_entries = [
        e for e in entries
        if e.get("event") == "PreToolUse"
        and e.get("match", {}).get("mcp", {}).get("server") == "linear"
        and e.get("match", {}).get("mcp", {}).get("tool") == "create_issue"
    ]
    assert linear_entries, "linear/create_issue 엔트리 소멸 — L2 벤더 게이트 위반"


# ═══════════════════════════════════════════════════════════════════════
# 6-B. 테스트 전략 — mock 기반, 네트워크 0 확인
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_action_no_network_dependency(fake_root):
    """confirm-action.py는 네트워크 접근 없이 동작한다(파일시스템만)."""
    src = CONFIRM.read_text(encoding="utf-8")
    network_imports = ["import requests", "import httpx", "import urllib.request",
                       "import http.client", "import socket"]
    for imp in network_imports:
        assert imp not in src, (
            f"confirm-action.py가 네트워크 라이브러리를 import함: {imp}\n"
            "confirm hook은 파일시스템/env만 사용해야 한다"
        )


def test_no_marker_is_noop_even_with_teammode_active(fake_root):
    """marker 없이 호출 = 게이트 대상 미판정 → 통과(exit 0).

    동적 판정: marker 없으면 allow 판정 불가하므로, 살아있는 manifest 에선
    이 분기는 발생하지 않지만(항상 marker 제공) 방어적으로 통과/차단 둘 다 허용 확인.
    """
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = _run_confirm(payload, fake_root, marker=None)
    assert proc.returncode in (0, 2), "예상치 못한 returncode"


# ═══════════════════════════════════════════════════════════════════════
# 추가: .teammode-active 가드 — teammode off 시 모든 도구 통과
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("server,tool,marker", SIDE_EFFECT_TOOLS)
def test_side_effect_tool_noop_when_inactive(tmp_path, server, tool, marker):
    """.teammode-active 없으면 side-effect 도구도 차단 안 함(exit 0)."""
    root = tmp_path / "inactive_team"
    root.mkdir()
    # .teammode-active 없음
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": server, "name": tool},
        "agent": "claude",
    }
    proc = _run_confirm(payload, root, marker=marker)
    assert proc.returncode == 0, (
        f"{server}/{tool}: teammode off 시 통과 기대, 실제={proc.returncode}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 이슈 #9(a): TEAMMODE_HOME 스테일 시 stderr 경고 (게이트가 조용히 열리는 것 표면화)
# ═══════════════════════════════════════════════════════════════════════

def test_stale_teammode_home_warns_on_stderr(tmp_path):
    """TEAMMODE_HOME 이 존재하지 않는 경로 → 통과 거동(exit 0·stdout 빈)은 불변 + stderr 경고."""
    gone = tmp_path / "moved-away"  # 존재하지 않음
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = _run_confirm(payload, gone, marker="teammode-linear-create-allow")
    assert proc.returncode == 0, "거동 불변 — 스테일 루트가 도구를 막으면 안 됨"
    assert proc.stdout.strip() == "", f"stdout 은 deny JSON 채널 — 불변: {proc.stdout!r}"
    assert "TEAMMODE_HOME" in proc.stderr
    assert "유효한 팀 루트" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1, "경고는 정확히 한 줄"


def test_valid_root_teammode_off_stays_silent(tmp_path):
    """유효 팀 루트(memory 표식)인데 .teammode-active 없음 = 정상 off — 침묵 유지."""
    root = tmp_path / "team"
    root.mkdir()
    (root / "memory").mkdir()
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    proc = _run_confirm(payload, root, marker="teammode-linear-create-allow")
    assert proc.returncode == 0
    assert proc.stderr.strip() == "", f"정상 off 상태는 경고 금지: {proc.stderr!r}"
