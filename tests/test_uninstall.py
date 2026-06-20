"""신규 기능 — install.py --uninstall (호스트 되돌리기).

install 이 호스트에 더한 것을 역순·안전·멱등하게 제거한다. 스펙 바깥 신규 기능이며
현 코드베이스의 실제 설치 산물(.teammode-active 마커 + settings.json teammode 훅)에
더해, 미래의 env 주입/obsidian 등록까지 **우리 표식만** 골라 되돌리는 역함수를 둔다.

호스트 철칙: 모든 테스트는 fake HOME + 격리 경로(--settings/--obsidian-config tmp).
실 ~/.bashrc·~/.claude·~/.config/obsidian 무접촉(conftest 가드가 별도로 검사).
"""
import json
import runpy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
sys.path.insert(0, str(INFRA))

import install_lib  # noqa: E402


def _run_install(argv):
    saved = sys.argv[:]
    try:
        mod = runpy.run_path(str(INFRA / "install.py"),
                             run_name="__uninstall_test__")
        return mod["main"](argv)
    finally:
        sys.argv = saved


# ── env 주입 역함수: remove_injected_env ────────────────────────────────

ENV_MARKER = "# teammode (env injection"


def _profile_with_ours(tmp_path):
    p = tmp_path / ".bashrc"
    p.write_text(
        "export PATH=/usr/local/bin:$PATH  # 남의 줄, 보존돼야 함\n"
        "alias ll='ls -al'  # 또 다른 남의 줄\n"
        f'export TEAMMODE_TEAM_NAME=tgates  {ENV_MARKER} v0.1)\n'
        "export EDITOR=vim  # 남의 줄\n",
        encoding="utf-8",
    )
    return p


def test_remove_injected_env_only_our_line(tmp_path):
    p = _profile_with_ours(tmp_path)
    changed = install_lib.remove_injected_env(p)
    assert changed is True
    text = p.read_text()
    # 우리 줄만 사라짐
    assert ENV_MARKER not in text
    assert "TEAMMODE_TEAM_NAME" not in text
    # 남의 줄은 전부 보존
    assert "export PATH=/usr/local/bin:$PATH" in text
    assert "alias ll='ls -al'" in text
    assert "export EDITOR=vim" in text


def test_remove_injected_env_idempotent(tmp_path):
    p = _profile_with_ours(tmp_path)
    install_lib.remove_injected_env(p)
    after_first = p.read_text()
    # 2회차는 무동작(무변경)
    changed = install_lib.remove_injected_env(p)
    assert changed is False
    assert p.read_text() == after_first


def test_remove_injected_env_no_marker_noop(tmp_path):
    p = tmp_path / ".bashrc"
    body = "export PATH=/x:$PATH\nalias g=git\n"
    p.write_text(body, encoding="utf-8")
    changed = install_lib.remove_injected_env(p)
    assert changed is False
    assert p.read_text() == body  # 남의 줄 무접촉


def test_remove_injected_env_missing_file_noop(tmp_path):
    p = tmp_path / "nope" / ".bashrc"
    changed = install_lib.remove_injected_env(p)  # raise 금지
    assert changed is False
    assert not p.exists()


# ── obsidian 등록 역함수: unregister_obsidian_vault ─────────────────────

def _obsidian_config(tmp_path, our_path, others=True):
    cfg = tmp_path / "obsidian.json"
    data = {"vaults": {}}
    if others:
        data["vaults"]["aaaaaaaa"] = {"path": "/home/x/other-vault", "ts": 1}
    data["vaults"]["bbbbbbbb"] = {"path": str(our_path), "ts": 2, "open": True}
    if others:
        data["vaults"]["cccccccc"] = {"path": "/home/x/third", "ts": 3}
    data["foo_top_level"] = {"keep": "me"}  # 최상위 키 보존 검증
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg


def test_unregister_obsidian_only_our_vault(tmp_path):
    our = tmp_path / "teamroot" / "memory"
    cfg = _obsidian_config(tmp_path, our, others=True)
    changed = install_lib.unregister_obsidian_vault(cfg, str(our))
    assert changed is True
    data = json.loads(cfg.read_text())
    paths = {v["path"] for v in data["vaults"].values()}
    assert str(our) not in paths           # 우리 볼트만 제거
    assert "/home/x/other-vault" in paths  # 다른 볼트 보존
    assert "/home/x/third" in paths
    assert data["foo_top_level"] == {"keep": "me"}  # 최상위 키 보존


def test_unregister_obsidian_idempotent(tmp_path):
    our = tmp_path / "teamroot" / "memory"
    cfg = _obsidian_config(tmp_path, our, others=True)
    install_lib.unregister_obsidian_vault(cfg, str(our))
    after_first = cfg.read_text()
    changed = install_lib.unregister_obsidian_vault(cfg, str(our))
    assert changed is False
    assert cfg.read_text() == after_first


def test_unregister_obsidian_absent_vault_noop(tmp_path):
    other_only = tmp_path / "config_other.json"
    other_only.write_text(json.dumps(
        {"vaults": {"z": {"path": "/somewhere/else"}}}), encoding="utf-8")
    changed = install_lib.unregister_obsidian_vault(
        other_only, str(tmp_path / "teamroot" / "memory"))
    assert changed is False
    assert json.loads(other_only.read_text())["vaults"]["z"]["path"] == "/somewhere/else"


def test_unregister_obsidian_missing_config_skip(tmp_path):
    cfg = tmp_path / "noexist" / "obsidian.json"
    changed = install_lib.unregister_obsidian_vault(cfg, str(tmp_path / "m"))
    assert changed is False
    assert not cfg.exists()


# ── install.py --uninstall (전체 플로우) ────────────────────────────────

def _on(team_root, settings):
    """teammode on 으로 실제 설치 산물(마커 + 훅)을 만든다."""
    saved = sys.argv[:]
    try:
        mod = runpy.run_path(str(INFRA / "teammode.py"),
                             run_name="__uninstall_on__")
        return mod["main"](["on", "--root", str(team_root),
                            "--settings", str(settings)])
    finally:
        sys.argv = saved


def test_uninstall_removes_marker_and_hooks(tmp_path):
    team_root = tmp_path / "teamroot"
    team_root.mkdir()
    settings = tmp_path / "settings.json"
    rc = _on(team_root, settings)
    assert rc == 0
    marker = team_root / ".teammode-active"
    assert marker.exists()
    blob = json.loads(settings.read_text())
    assert "normalize.py" in json.dumps(blob)  # teammode 훅 등록됨

    rc = _run_install(["--uninstall", "--root", str(team_root),
                       "--settings", str(settings)])
    assert rc == 0
    # off: 마커 삭제
    assert not marker.exists()
    # 어댑터 uninstall: teammode 훅 제거
    blob = json.loads(settings.read_text())
    assert "normalize.py" not in json.dumps(blob)


def test_uninstall_preserves_user_hooks(tmp_path):
    team_root = tmp_path / "teamroot"
    team_root.mkdir()
    settings = tmp_path / "settings.json"
    _on(team_root, settings)
    # 사용자 자기 훅 추가
    data = json.loads(settings.read_text())
    data.setdefault("hooks", {}).setdefault("PostToolUse", []).append(
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "my-own-script.sh"}]})
    settings.write_text(json.dumps(data))

    _run_install(["--uninstall", "--root", str(team_root),
                  "--settings", str(settings)])
    blob = json.dumps(json.loads(settings.read_text()))
    assert "my-own-script.sh" in blob       # 남의 훅 보존
    assert "normalize.py" not in blob       # 우리 훅만 제거


def test_uninstall_idempotent(tmp_path):
    team_root = tmp_path / "teamroot"
    team_root.mkdir()
    settings = tmp_path / "settings.json"
    _on(team_root, settings)
    _run_install(["--uninstall", "--root", str(team_root),
                  "--settings", str(settings)])
    first = settings.read_text()
    # 2회차: 이미 없는 것 제거 — raise 금지, 무오류
    rc = _run_install(["--uninstall", "--root", str(team_root),
                       "--settings", str(settings)])
    assert rc == 0
    assert settings.read_text() == first
    assert not (team_root / ".teammode-active").exists()


def test_uninstall_does_not_delete_memory(tmp_path):
    team_root = tmp_path / "teamroot"
    (team_root / "memory" / "team" / "sessions" / "euns").mkdir(parents=True)
    payload = team_root / "memory" / "team" / "sessions" / "euns" / "2026-06-15.md"
    payload.write_text("팀 데이터 — 절대 삭제 금지\n", encoding="utf-8")
    settings = tmp_path / "settings.json"
    _on(team_root, settings)

    _run_install(["--uninstall", "--root", str(team_root),
                  "--settings", str(settings)])
    assert payload.exists()
    assert "절대 삭제 금지" in payload.read_text()


def test_uninstall_removes_env_and_obsidian(tmp_path):
    """--uninstall 이 env 줄·obsidian 등록까지 역함수로 처리(격리 경로)."""
    team_root = tmp_path / "teamroot"
    team_root.mkdir()
    settings = tmp_path / "settings.json"
    _on(team_root, settings)

    profile = _profile_with_ours(tmp_path)
    our_vault = team_root / "memory"
    obs_cfg = _obsidian_config(tmp_path, our_vault, others=True)

    rc = _run_install(["--uninstall", "--root", str(team_root),
                       "--settings", str(settings),
                       "--profile", str(profile),
                       "--obsidian-config", str(obs_cfg)])
    assert rc == 0
    assert ENV_MARKER not in profile.read_text()
    assert "export PATH=/usr/local/bin:$PATH" in profile.read_text()  # 남의 줄 보존
    obs = json.loads(obs_cfg.read_text())
    paths = {v["path"] for v in obs["vaults"].values()}
    assert str(our_vault) not in paths
    assert "/home/x/other-vault" in paths  # 다른 볼트 보존


def test_uninstall_requires_root(capsys):
    rc = _run_install(["--uninstall", "--settings", "/tmp/x.json"])
    assert rc == 2
    assert "--root" in capsys.readouterr().err


def test_uninstall_real_host_gate(tmp_path, capsys):
    """게이트: --settings/--yes 없이 실호스트(~/.claude) 쓰기 거부."""
    team_root = tmp_path / "teamroot"
    team_root.mkdir()
    rc = _run_install(["--uninstall", "--root", str(team_root)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--settings" in err or "--yes" in err


# ── #5: _dispatch 부트스트랩 인자 필터 (--root/--install 가 어댑터로 새지 않음) ──

def _install_mod():
    return runpy.run_path(str(INFRA / "install.py"), run_name="__dispatch_test__")


def test_strip_dispatch_only_args_removes_root_value_flag():
    """#5: --root <값> 은 value-flag 라 플래그+값 둘 다 제거 — 어댑터로 안 샌다."""
    strip = _install_mod()["_strip_dispatch_only_args"]
    assert strip(["uninstall", "--root", ".", "--settings", "x"]) == \
        ["uninstall", "--settings", "x"]
    # codex: --config 는 보존, --root/--install 만 제거
    assert strip(["--config", "y", "--install", "uninstall", "--root", "/t"]) == \
        ["--config", "y", "uninstall"]


def test_strip_dispatch_only_args_keeps_adapter_args():
    """어댑터가 아는 인자(--settings/--config/서브커맨드)는 보존."""
    strip = _install_mod()["_strip_dispatch_only_args"]
    assert strip(["--settings", "s", "sync", "--on"]) == \
        ["--settings", "s", "sync", "--on"]
