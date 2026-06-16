"""L2-C — install-skills 어댑터 동사 (SPEC §2.7, L2-PLAN L2-C).

install-skills 는 infra/skills/base/<name>/ (tm-onboard·tm-connect·tm-reset) 을 에이전트의
스킬 디렉토리(claude=~/.claude/skills, codex=~/.codex/skills)에 심링크한다(폴백: 복사).

검증 묶음:
  - 심링크 생성 (claude·codex 크로스에이전트, 각자 다른 스킬 경로)
  - 멱등(재실행 무변경)
  - is_owned 소유 판정(teammode 심링크/복사만 관리, 사용자 동명 스킬 무접촉)
  - uninstall(역 제거) — 사용자 스킬 보존
  - 복사 폴백(os.symlink OSError 모킹 → copytree + 마커)
  - 격리(--skills-dir) 무접촉: 실 ~/.claude/skills·~/.codex/skills 안 건드림
  - 고아 정리(소스에서 사라진 teammode 소유 스킬 제거)

모든 테스트는 tmp_path + tmp skills_dir 만 쓴다 — 실 스킬 디렉토리 무접촉
(conftest 스킬 footprint 가드가 이 경로를 지킨다).
"""
import runpy
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402

_CLAUDE = runpy.run_path(str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
                         run_name="__claude_l2c__")
_CODEX = runpy.run_path(str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
                        run_name="__codex_l2c__")
ClaudeAdapter = _CLAUDE["Adapter"]
CodexAdapter = _CODEX["Adapter"]

SKILL_NAMES = {"tm-onboard", "tm-connect", "tm-reset"}


def _scaffold(tmp_path):
    """tmp 팀 루트 — 실 infra/skills/base 스킬 트리 복사."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "agents" / "claude" / "adapter.py",
                root / "infra" / "agents" / "claude" / "adapter.py")
    shutil.copy(REPO / "infra" / "agents" / "claude" / "events.json",
                root / "infra" / "agents" / "claude" / "events.json")
    shutil.copy(REPO / "infra" / "agents" / "codex" / "events.json",
                root / "infra" / "agents" / "codex" / "events.json")
    shutil.copytree(REPO / "infra" / "skills" / "base",
                    root / "infra" / "skills" / "base")
    return root


def _claude(root, tmp_path, skills_name="claude-skills"):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3", team_root=str(root),
        skills_dir=str(tmp_path / skills_name),
    )


def _codex(root, tmp_path, skills_name="codex-skills"):
    return CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python="python3", team_root=str(root),
        skills_dir=str(tmp_path / skills_name),
    )


# ── 심링크 생성 + 크로스에이전트 ──

def test_claude_install_skills_symlinks(tmp_path):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)
    changes = a.install_skills()
    sk = tmp_path / "claude-skills"
    for name in SKILL_NAMES:
        link = sk / name
        assert link.is_symlink(), f"{name} 심링크 미생성"
        assert (link / "SKILL.md").is_file()
        # 링크가 우리 소스를 가리키는지
        assert Path(link.resolve()) == (root / "infra" / "skills" / "base" / name).resolve()
    assert any("심링크" in c for c in changes)


def test_codex_install_skills_own_path(tmp_path):
    """codex 는 자기 스킬 경로(여기선 격리 codex-skills)에 심링크 — claude 와 독립."""
    root = _scaffold(tmp_path)
    a = _codex(root, tmp_path)
    a.install_skills()
    sk = tmp_path / "codex-skills"
    for name in SKILL_NAMES:
        assert (sk / name).is_symlink()
    # claude 경로는 안 건드림(크로스에이전트 격리)
    assert not (tmp_path / "claude-skills").exists()


def test_codex_default_skills_dir_is_codex():
    """codex DEFAULT_SKILLS_DIR 은 ~/.codex/skills, claude 는 ~/.claude/skills (경로 분기)."""
    assert ClaudeAdapter.DEFAULT_SKILLS_DIR == "~/.claude/skills"
    assert CodexAdapter.DEFAULT_SKILLS_DIR == "~/.codex/skills"


# ── 멱등 ──

def test_install_skills_idempotent(tmp_path):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)
    a.install_skills()
    again = a.install_skills()
    assert again == ["[ok] 변경 없음"]


# ── is_owned 소유 판정 + 사용자 무접촉 ──

def test_user_skill_untouched(tmp_path):
    """사용자가 직접 둔 동명 스킬(심링크 아님·마커 없음)은 무접촉(소유권)."""
    root = _scaffold(tmp_path)
    sk = tmp_path / "claude-skills"
    user_skill = sk / "tm-onboard"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("user's own skill\n")
    a = _claude(root, tmp_path)
    changes = a.install_skills()
    # 사용자 디렉토리 그대로(심링크로 안 바뀜), 내용 보존
    assert not user_skill.is_symlink()
    assert (user_skill / "SKILL.md").read_text() == "user's own skill\n"
    assert any("무접촉" in c and "tm-onboard" in c for c in changes)
    # 나머지는 정상 심링크
    assert (sk / "tm-connect").is_symlink()


def test_is_owned_skill_discriminates(tmp_path):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)
    src = root / "infra" / "skills" / "base" / "tm-onboard"
    sk = tmp_path / "claude-skills"
    sk.mkdir()
    # 우리 심링크 → owned
    owned = sk / "tm-onboard"
    import os
    os.symlink(str(src), str(owned), target_is_directory=True)
    assert a.is_owned_skill(owned, src) is True
    # 사용자 디렉토리 → not owned
    user = sk / "tm-connect"
    user.mkdir()
    assert a.is_owned_skill(user, root / "infra" / "skills" / "base" / "tm-connect") is False


# ── uninstall (역 제거) ──

def test_uninstall_skills_removes_owned_only(tmp_path):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)
    a.install_skills()
    sk = tmp_path / "claude-skills"
    # 사용자 스킬 추가
    user = sk / "my-skill"
    user.mkdir()
    (user / "SKILL.md").write_text("mine\n")
    a.uninstall_skills()
    for name in SKILL_NAMES:
        assert not (sk / name).exists(), f"{name} 제거 안 됨"
    assert user.is_dir(), "사용자 스킬이 삭제됨(무접촉 위반)"


# ── 복사 폴백 (윈도우 심링크 권한 실패 모킹) ──

def test_copy_fallback_on_symlink_oserror(tmp_path, monkeypatch):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)

    def boom(*args, **kwargs):
        raise OSError("symlink privilege not held (Windows)")
    monkeypatch.setattr("os.symlink", boom)
    changes = a.install_skills()
    sk = tmp_path / "claude-skills"
    for name in SKILL_NAMES:
        d = sk / name
        assert d.is_dir() and not d.is_symlink(), f"{name} 복사 폴백 실패"
        assert (d / "SKILL.md").is_file()
        assert (d / "_teammode_skill").is_file(), "복사본 소유 마커 없음"
    assert any("복사" in c for c in changes)
    # 복사본도 owned 로 판정 → uninstall 로 제거됨
    a.uninstall_skills()
    for name in SKILL_NAMES:
        assert not (sk / name).exists()


def test_copy_fallback_idempotent(tmp_path, monkeypatch):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)
    monkeypatch.setattr("os.symlink",
                        lambda *x, **k: (_ for _ in ()).throw(OSError("win")))
    a.install_skills()
    again = a.install_skills()
    assert again == ["[ok] 변경 없음"]


# ── 고아 정리 (소스에서 사라진 teammode 소유 스킬) ──

def test_orphan_owned_skill_removed(tmp_path):
    root = _scaffold(tmp_path)
    a = _claude(root, tmp_path)
    a.install_skills()
    sk = tmp_path / "claude-skills"
    # 소스에서 tm-reset 제거 → 재install 시 고아 심링크 청소
    shutil.rmtree(root / "infra" / "skills" / "base" / "tm-reset")
    changes = a.install_skills()
    assert not (sk / "tm-reset").exists()
    assert any("remove-skill" in c and "tm-reset" in c for c in changes)


# ── 격리/무접촉 (실 HOME monkeypatch — 기본 경로가 격리 HOME 하위인지) ──

def test_default_skills_dir_respects_home(tmp_path, monkeypatch):
    """skills_dir 미지정 → DEFAULT_SKILLS_DIR(~/.claude/skills) 를 HOME 기준 해석.

    monkeypatch HOME=tmp 면 실 ~/.claude/skills 가 아닌 tmp 하위로 향한다(무접촉 실증).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home, raising=False)
    root = _scaffold(tmp_path)
    a = ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3", team_root=str(root),
        skills_dir=None,  # 기본 경로 해석
    )
    assert str(a.skills_dir).startswith(str(fake_home))
    a.install_skills()
    assert (fake_home / ".claude" / "skills" / "tm-onboard").is_symlink()


def test_codex_default_skills_dir_respects_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home, raising=False)
    root = _scaffold(tmp_path)
    a = CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "config.toml"),
        python="python3", team_root=str(root),
        skills_dir=None,
    )
    assert str(a.skills_dir).startswith(str(fake_home))
    assert ".codex" in str(a.skills_dir)


# ── wire: install.py 는 install-skills 를 어댑터 동사로 호출만 한다(직접 심링크 금지) ──

def test_wire_calls_install_skills_after_sync(tmp_path):
    """D.1: 에이전트마다 install-mcp → sync → install-skills 순. wire 는 호출만."""
    calls = []

    def run_adapter(agent, verb, flag, path, extra_args=None):
        calls.append((agent, verb))
        return 0

    res = il.wire_agents(["claude", "codex"], home=tmp_path,
                         settings_override=tmp_path / "iso",
                         run_adapter=run_adapter)
    assert res.ok and res.exit_code == 0
    for agent in ("claude", "codex"):
        ss = calls.index((agent, "sync"))
        sk = calls.index((agent, "install-skills"))
        assert ss < sk, f"{agent}: install-skills 가 sync 보다 먼저"
    assert set(res.wired) == {"claude", "codex"}


def test_wire_install_skills_gets_isolated_skills_dir(tmp_path):
    """격리 게이트: install-skills 는 --skills-dir <iso>/<agent>/skills 를 받는다."""
    iso = tmp_path / "iso"
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude", "codex"], home=tmp_path,
                   settings_override=iso, run_adapter=run_adapter)
    for agent in ("claude", "codex"):
        extra = seen[(agent, "install-skills")]
        assert "--skills-dir" in extra
        sk_path = extra[extra.index("--skills-dir") + 1]
        assert sk_path == str(iso / agent / "skills")


def test_wire_real_host_skills_dir_under_home(tmp_path):
    """실호스트 모드: install-skills 는 home 기준 .claude/skills·.codex/skills 를 받는다."""
    seen = {}

    def run_adapter(agent, verb, flag, path, extra_args=None):
        seen[(agent, verb)] = list(extra_args or [])
        return 0

    il.wire_agents(["claude", "codex"], home=tmp_path,
                   run_adapter=run_adapter)
    c = seen[("claude", "install-skills")]
    assert c[c.index("--skills-dir") + 1] == str(tmp_path / ".claude" / "skills")
    x = seen[("codex", "install-skills")]
    assert x[x.index("--skills-dir") + 1] == str(tmp_path / ".codex" / "skills")


def test_wire_install_skills_failure_exit3(tmp_path):
    """install-skills 실패 → 그 에이전트 실패 집계, exit 3, 다른 에이전트 계속."""
    def run_adapter(agent, verb, flag, path, extra_args=None):
        if agent == "codex" and verb == "install-skills":
            return 2
        return 0

    res = il.wire_agents(["claude", "codex"], home=tmp_path,
                         settings_override=tmp_path / "iso",
                         run_adapter=run_adapter)
    assert not res.ok and res.exit_code == 3
    assert "claude" in res.wired
    assert any(a == "codex" for a, _ in res.failed)
