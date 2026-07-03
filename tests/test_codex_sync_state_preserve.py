"""C2 — codex plain sync 의 statusMessage 보존 (self-heal).

실회귀: 직접 디스패치로 `sync`(--on/--off 없음)를 돌리면 mode=None →
base 엔트리만 + statusMessage 미삽입으로 기존 ON 상태 블록을 silent
downgrade 재작성했다. codex trust 해시가 statusMessage 를 포함하므로
불필요한 재작성은 재-trust 까지 유발한다.

계약(codex 문답 2026-07-03 수렴):
  - plain sync 는 기존 managed 블록이 있으면 현재 렌더 상태(ON=블록 안
    statusMessage 존재)를 보존해 그 상태로 렌더한다(self-heal).
  - manifest 미변경이면 바이트 동일 → 무변경(_write_block False) → 해시 보존.
  - 블록이 없으면 기존 스펙대로 base/off 간주(최초 off).
  - 명시 --off 는 여전히 downgrade 한다(의도된 off).

모든 테스트 tmp_path — 실 ~/.codex 무접촉.
"""
from __future__ import annotations

import json
import runpy
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

_CLAUDE = runpy.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                         run_name="__claude_c2__")
_CODEX = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                        run_name="__codex_c2__")
ClaudeAdapter = _CLAUDE["Adapter"]
CodexAdapter = _CODEX["Adapter"]


def _scaffold(tmp_path):
    """tmp 팀 루트 — 실 manifest·events 복사 + 팀명 있는 config."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "hooks" / "manifest.json",
                root / "infra" / "hooks" / "manifest.json")
    shutil.copy(REPO / "infra" / "agents" / "codex" / "events.json",
                root / "infra" / "agents" / "codex" / "events.json")
    (root / "infra" / "agents" / "codex" / "normalize.py").write_text("# stub\n")
    (root / "team.config.json").write_text(json.dumps(
        {"spec_version": "0.2", "team": {"name": "t-preserve"}}))
    return root


def _codex(root, tmp_path):
    return CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python="python3", team_root=str(root),
    )


def _text(ad) -> str:
    return Path(ad.settings_path).read_text(encoding="utf-8")


def test_plain_sync_preserves_on_state_byte_identical(tmp_path):
    """ON 상태에서 plain sync → statusMessage 보존 + 파일 바이트 동일(해시 보존)."""
    root = _scaffold(tmp_path)
    ad = _codex(root, tmp_path)
    ad.sync(mode="on")
    on_text = _text(ad)
    assert "statusMessage" in on_text  # 전제: on 렌더에 statusMessage 존재

    ad.sync(mode=None)  # plain sync (직접 디스패치 시나리오)
    healed = _text(ad)
    assert "statusMessage" in healed, "plain sync 가 statusMessage 를 드랍하면 안 됨"
    assert healed == on_text, (
        "manifest 미변경 plain sync 는 바이트 동일(무재작성=trust 해시 보존)이어야 함")


def test_plain_sync_without_block_renders_base_off(tmp_path):
    """블록 없는 최초 plain sync → 기존 스펙 유지: base 만(off 간주), statusMessage 없음."""
    root = _scaffold(tmp_path)
    ad = _codex(root, tmp_path)
    ad.sync(mode=None)
    text = _text(ad)
    assert "teammode-hooks-start" in text
    assert "statusMessage" not in text


def test_plain_sync_after_off_stays_off(tmp_path):
    """OFF 상태에서 plain sync → off 보존(ON 으로 승격 금지) + 바이트 동일."""
    root = _scaffold(tmp_path)
    ad = _codex(root, tmp_path)
    ad.sync(mode="on")
    ad.sync(mode="off")
    off_text = _text(ad)
    assert "statusMessage" not in off_text

    ad.sync(mode=None)
    assert _text(ad) == off_text


def test_plain_sync_heals_status_message_when_on_scripts_present(tmp_path):
    """statusMessage 가 (과거 버전 등으로) 빠졌어도 mode=on 스크립트가 블록에 있으면
    ON 으로 추론해 statusMessage 를 self-heal 로 되살린다(codex 문답 R2-2)."""
    root = _scaffold(tmp_path)
    ad = _codex(root, tmp_path)
    ad.sync(mode="on")
    text = _text(ad)
    # statusMessage 줄만 제거한 '과거 ON 블록' 시뮬레이션
    degraded = "\n".join(ln for ln in text.split("\n")
                         if not ln.startswith("statusMessage"))
    Path(ad.settings_path).write_text(degraded, encoding="utf-8")
    assert "session-start.py" in degraded  # 전제: on 스크립트는 남아있음

    ad.sync(mode=None)
    healed = _text(ad)
    assert "statusMessage" in healed, (
        "on 스크립트 존재 = ON 추론 → statusMessage self-heal 이어야 함")


# ── claude 대칭 self-heal (codex 문답 R2-2: 공유 계약으로 양쪽 어댑터 일치) ──

def _claude(root, tmp_path):
    (root / "infra" / "agents" / "claude" / "events.json").write_bytes(
        (REPO / "infra" / "agents" / "claude" / "events.json").read_bytes())
    (root / "infra" / "agents" / "claude" / "normalize.py").write_text("# stub\n")
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3", team_root=str(root),
    )


def test_claude_plain_sync_preserves_on_state_byte_identical(tmp_path):
    """claude 도 동일 회귀: plain sync 가 ON 엔트리(session-start 등)·statusLine 을
    드랍하면 안 된다 — ON 후 plain sync 는 바이트 동일."""
    root = _scaffold(tmp_path)
    ad = _claude(root, tmp_path)
    ad.sync(mode="on")
    on_text = Path(ad.settings_path).read_text(encoding="utf-8")
    assert "session-start.py" in on_text

    ad.sync(mode=None)
    healed = Path(ad.settings_path).read_text(encoding="utf-8")
    assert "session-start.py" in healed, "plain sync 가 on 엔트리를 드랍하면 안 됨"
    assert healed == on_text, "manifest 미변경 plain sync 는 무변경이어야 함"


def test_claude_plain_sync_without_prior_state_renders_base(tmp_path):
    """claude: 최초 plain sync → 기존 스펙 유지(base 만, on 엔트리 없음)."""
    root = _scaffold(tmp_path)
    ad = _claude(root, tmp_path)
    ad.sync(mode=None)
    text = Path(ad.settings_path).read_text(encoding="utf-8")
    assert "confirm-action.py" in text  # base 엔트리
    assert "session-start.py" not in text  # on 엔트리 없음(off 간주)


def test_explicit_off_still_downgrades(tmp_path):
    """명시 --off 는 여전히 statusMessage 를 걷어낸다(의도된 downgrade 보존)."""
    root = _scaffold(tmp_path)
    ad = _codex(root, tmp_path)
    ad.sync(mode="on")
    assert "statusMessage" in _text(ad)
    ad.sync(mode="off")
    assert "statusMessage" not in _text(ad)
