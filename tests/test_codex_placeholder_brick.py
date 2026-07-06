"""P1 회귀락 — codex placeholder 는 실 TOML 블록 금지 (config 벽돌화 방지).

실기 확정 버그(2026-07-06): launch 정보 없는 provider 를 `[mcp_servers.tm-*]`
실블록(command/url 없이 소유마커만)으로 쓰면 codex CLI 가 config 로드 전체를
fatal 거부("invalid transport") → 세션 기동 불가·훅 전멸. 6/27 에도 동일 유형
재발 흔적(만성 "codex 세션 push 안 됨"의 주범).

계약: no-launch provider 는 마커 블록 안 **주석**(`# [tm-placeholder] ...`)으로만
기록한다. command/url 있는 provider 는 기존 실블록 유지.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))


def _adapter(tmp_path):
    import json
    import runpy
    mod = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                         run_name="__codex_test__")
    import shutil as _sh
    agent_dir = tmp_path / "agent"; agent_dir.mkdir(exist_ok=True)
    _sh.copy(REPO / "infra" / "agents" / "codex" / "events.json",
             agent_dir / "events.json")
    hooks_dir = tmp_path / "hooks"; hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "manifest.json").write_text(json.dumps([]))
    return mod["Adapter"](agent_dir=str(agent_dir),
                          manifest_path=str(hooks_dir / "manifest.json"),
                          settings_path=str(agent_dir / "config.toml"),
                          python="python3", team_root=str(tmp_path), member=None)


class _Pack:
    def __init__(self, mcp):
        self.mcp = mcp


def test_no_launch_provider_renders_comment_not_table(tmp_path):
    """launch 정보 없음 → 실 [mcp_servers.*] 테이블 금지, 주석 placeholder 만."""
    a = _adapter(tmp_path)
    pack = _Pack({"register_hint": "Google Calendar 공식 MCP — install-mcp(P4) 확정"})
    block = a._render_mcp_block([("tm-google", "google", pack)])
    assert "[mcp_servers.tm-google]" not in block, \
        "no-launch placeholder 가 실 TOML 테이블로 렌더됨 — codex 벽돌화(P1)"
    assert re.search(r'^# \[tm-placeholder\] google\b', block, re.M), \
        "주석 placeholder 부재 — 재렌더 관리·사용자 안내 소실"
    assert "install-mcp(P4)" in block  # hint 보존


def test_launch_provider_still_renders_real_table(tmp_path):
    """command 있는 provider 는 기존 실블록 계약 유지."""
    a = _adapter(tmp_path)
    pack = _Pack({"command": "npx", "args": ["-y", "some-mcp"]})
    block = a._render_mcp_block([("tm-linear", "linear", pack)])
    assert "[mcp_servers.tm-linear]" in block
    assert "command = 'npx'" in block


def test_mixed_render_only_placeholder_commented(tmp_path):
    a = _adapter(tmp_path)
    real = _Pack({"command": "npx", "args": ["-y", "x"]})
    ph = _Pack({"register_hint": "hint"})
    block = a._render_mcp_block([("tm-linear", "linear", real),
                                 ("tm-google", "google", ph)])
    assert "[mcp_servers.tm-linear]" in block
    assert "[mcp_servers.tm-google]" not in block
    assert "# [tm-placeholder] google" in block


@pytest.mark.skipif(shutil.which("codex") is None,
                    reason="실 codex 바이너리 오라클 — CI 러너엔 없음")
def test_live_codex_rejects_table_placeholder_accepts_comment(tmp_path):
    """[실기 오라클] 구방식 실블록 → config 로드 fatal / 주석형 → config 통과.

    auth 불요 판정: 'Error loading config.toml' 은 인증 전에 발생 — 주석형은
    그 에러가 없어야 한다(이후 다른 이유로 실패해도 config 는 통과한 것).
    """
    broken = tmp_path / "broken"; broken.mkdir()
    (broken / "config.toml").write_text(
        "[mcp_servers.tm-x]\n_teammode_managed = true\n", encoding="utf-8")
    r = subprocess.run(["codex", "exec", "ok"],
                       env={"CODEX_HOME": str(broken), "PATH": __import__("os").environ["PATH"],
                            "HOME": __import__("os").environ.get("HOME", "")},
                       capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL)
    assert "Error loading config.toml" in (r.stderr + r.stdout), \
        "오라클 전제 붕괴: codex 가 command 없는 실블록을 더는 거부하지 않음?"

    fixed = tmp_path / "fixed"; fixed.mkdir()
    (fixed / "config.toml").write_text(
        "# teammode-mcp-start\n# [tm-placeholder] x — hint\n# teammode-mcp-end\n",
        encoding="utf-8")
    r = subprocess.run(["codex", "exec", "ok"],
                       env={"CODEX_HOME": str(fixed), "PATH": __import__("os").environ["PATH"],
                            "HOME": __import__("os").environ.get("HOME", "")},
                       capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    assert "Error loading config.toml" not in (r.stderr + r.stdout), \
        "주석형 placeholder 가 config 로드를 깨뜨림"
