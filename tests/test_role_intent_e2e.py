import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_install(team_root: Path, iso: Path, role_intent: str):
    env = dict(os.environ)
    # 완전 격리 — 실 호스트 무접촉
    for k in ("HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        env[k] = str(iso / k.lower())
        os.makedirs(env[k], exist_ok=True)
    subprocess.run(
        [sys.executable, str(REPO / "infra" / "install.py"),
         "--root", str(team_root), "--member-name", "bob",
         "--role-intent", role_intent, "--settings", str(iso / "settings")],
        env=env, cwd=str(team_root), capture_output=True, text=True, timeout=120)
    # verify 단계가 실패해 non-zero 여도 scaffold(config)는 그 전에 끝난다 → rc 무시.


def test_init_verb_writes_introducer_config(tmp_path):
    team = tmp_path / "team"; team.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(team), check=True)
    _run_install(team, tmp_path / "iso", "introducer")
    assert (team / "team.config.json").is_file()   # 도입자 → config 작성


def test_join_verb_skips_introducer_config(tmp_path):
    team = tmp_path / "team"; team.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(team), check=True)
    _run_install(team, tmp_path / "iso", "member")
    assert not (team / "team.config.json").is_file()  # 멤버 → config 무작성
