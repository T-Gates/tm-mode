"""Obsidian 볼트 자동등록 — 순수 함수 + install.py --register-obsidian (spec/05).

스펙 05: Obsidian 뷰는 키0(메모리 그대로 열기), 자동등록은 opt-in·merge·host-write.

호스트 안전 철칙(L1과 동일):
- 전부 fake HOME(monkeypatch.setenv("HOME", tmp)) + --obsidian-config <tmp경로>.
  실 ~/.config/obsidian/obsidian.json 절대 무접촉(conftest 가드가 강제).
- merge 안전: 기존 볼트 보존(clobber 0). 멱등: 같은 등록 2회 → 항목 1개.
- 미설치(obsidian.json 부모 부재) → skip·무raise.
- 어떤 경우도 raise 로 install 흐름 안 막음(비치명).
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))
import install_lib as il  # noqa: E402


# ─────────────────────────── 경로 해석(플랫폼별·주입) ───────────────────────────

def test_default_config_path_linux(tmp_path):
    home = tmp_path / "home"
    p = il.obsidian_config_path("linux", home=home)
    assert p == home / ".config" / "obsidian" / "obsidian.json"


def test_default_config_path_mac(tmp_path):
    home = tmp_path / "home"
    p = il.obsidian_config_path("darwin", home=home)
    assert p == home / "Library" / "Application Support" / "obsidian" / "obsidian.json"


def test_default_config_path_windows(tmp_path):
    home = tmp_path / "home"
    appdata = tmp_path / "AppData" / "Roaming"
    p = il.obsidian_config_path("win32", home=home, appdata=appdata)
    assert p == appdata / "obsidian" / "obsidian.json"


# ─────────────────────────── 볼트화(.obsidian/) ───────────────────────────

def test_ensure_vault_creates_obsidian_dir(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    created = il.ensure_obsidian_vault(memory)
    assert created is True
    assert (memory / ".obsidian").is_dir()


def test_ensure_vault_idempotent(tmp_path):
    memory = tmp_path / "memory"
    (memory / ".obsidian").mkdir(parents=True)
    created = il.ensure_obsidian_vault(memory)
    assert created is False  # 이미 있음 — 무변경
    assert (memory / ".obsidian").is_dir()


# ─────────────────────────── 등록: merge·멱등·미설치 ───────────────────────────

def _write_config(path: Path, vaults: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")


def test_register_merges_preserving_existing(tmp_path):
    """기존 볼트 2개 있는 obsidian.json 에 등록 → 기존 2개 그대로 + 신규 1개(clobber 0)."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {
        "aaaaaaaaaaaaaaaa": {"path": "/some/vault/one", "ts": 111, "open": True},
        "bbbbbbbbbbbbbbbb": {"path": "/some/vault/two", "ts": 222, "open": False},
    })
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="cccccccccccccccc", ts=333)

    assert res["registered"] is True
    data = json.loads(cfg.read_text())
    vaults = data["vaults"]
    # 기존 2개 보존(내용 그대로)
    assert vaults["aaaaaaaaaaaaaaaa"] == {"path": "/some/vault/one", "ts": 111, "open": True}
    assert vaults["bbbbbbbbbbbbbbbb"] == {"path": "/some/vault/two", "ts": 222, "open": False}
    # 신규 1개 추가
    assert vaults["cccccccccccccccc"]["path"] == str(memory.resolve())
    assert vaults["cccccccccccccccc"]["ts"] == 333
    assert vaults["cccccccccccccccc"]["open"] is False
    assert len(vaults) == 3


def test_register_idempotent_same_path(tmp_path):
    """같은 path 이미 등록 → skip(멱등). 같은 등록 2회 → 항목 1개만(중복 0)."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {})
    memory = tmp_path / "memory"
    memory.mkdir()

    r1 = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="1111111111111111", ts=1)
    r2 = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="2222222222222222", ts=2)

    assert r1["registered"] is True
    assert r2["registered"] is False  # 같은 path 이미 등록 — skip
    vaults = json.loads(cfg.read_text())["vaults"]
    # 같은 path 항목 1개만(중복 0) — 첫 등록 id 보존
    paths = [v["path"] for v in vaults.values()]
    assert paths.count(str(memory.resolve())) == 1
    assert "1111111111111111" in vaults
    assert "2222222222222222" not in vaults


def test_register_creates_config_when_dir_exists_but_file_absent(tmp_path):
    """obsidian.json 부모 디렉토리는 있는데 파일이 없으면 → 새로 생성하고 등록."""
    cfg_dir = tmp_path / ".config" / "obsidian"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "obsidian.json"
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="abcabcabcabcabca", ts=9)

    assert res["registered"] is True
    assert cfg.is_file()
    vaults = json.loads(cfg.read_text())["vaults"]
    assert len(vaults) == 1


def test_register_skips_when_obsidian_not_installed(tmp_path):
    """미설치: obsidian.json 부모 디렉토리 부재 → skip(생성 안 함, 무raise)."""
    cfg = tmp_path / "nope" / "obsidian" / "obsidian.json"  # 부모 없음
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="ffffffffffffffff", ts=7)

    assert res["registered"] is False
    assert "skip" in res["reason"].lower() or "설치" in res["reason"]
    assert not cfg.exists()  # 생성 안 함
    assert not cfg.parent.exists()


def test_register_does_not_raise_on_broken_config(tmp_path):
    """깨진 obsidian.json → raise 안 함(비치명). install 흐름 안 막음."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("NOT JSON {{", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()

    # raise 하지 않아야 한다
    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="dddddddddddddddd", ts=5)
    assert res["registered"] is False


def test_register_does_not_clobber_on_id_collision(tmp_path):
    """clobber 방어: 주입 vault_id 가 기존 다른 path 의 항목과 충돌 → 덮어쓰지 않고 skip."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {
        "collide000000000": {"path": "/other/vault", "ts": 9, "open": True},
    })
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="collide000000000", ts=100)

    assert res["registered"] is False  # clobber 방지
    vaults = json.loads(cfg.read_text())["vaults"]
    # 기존 항목 그대로(덮어쓰지 않음)
    assert vaults["collide000000000"] == {"path": "/other/vault", "ts": 9, "open": True}
    assert len(vaults) == 1


def test_register_preserves_top_level_keys(tmp_path):
    """obsidian.json 의 vaults 외 최상위 키(예: 설정)도 보존."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({
        "vaults": {},
        "frame": "hidden",  # vaults 외 사용자 설정
    }), encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()

    il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="0123456789abcdef", ts=1)

    data = json.loads(cfg.read_text())
    assert data["frame"] == "hidden"  # 보존
    assert len(data["vaults"]) == 1


def test_register_creates_vault_dir(tmp_path):
    """등록 시 memory/ 가 볼트화(.obsidian/ 생성)된다."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {})
    memory = tmp_path / "memory"
    memory.mkdir()

    il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="eeeeeeeeeeeeeeee", ts=4)

    assert (memory / ".obsidian").is_dir()


# ─────────────────────────── 비-dict JSON 안전 skip (data-loss 방지) ───────────────────────────

def test_register_skips_top_level_array(tmp_path):
    """최상위가 배열(유효 JSON 이나 object 아님) → broken-config 처럼 skip(원본 무손상)."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    cfg.parent.mkdir(parents=True)
    raw = json.dumps(["a", "b"])
    cfg.write_text(raw, encoding="utf-8")
    original_bytes = cfg.read_bytes()
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="aaaaaaaaaaaaaaaa", ts=1)

    assert res["registered"] is False  # 폐기·덮어쓰기 금지
    assert cfg.read_bytes() == original_bytes  # 원본 바이트 그대로 보존


def test_register_skips_vaults_is_list(tmp_path):
    """vaults 가 list(유효 JSON·object 이나 vaults 비-dict) → skip(원본 무손상)."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    cfg.parent.mkdir(parents=True)
    raw = json.dumps({"vaults": ["x", "y"], "frame": "hidden"})
    cfg.write_text(raw, encoding="utf-8")
    original_bytes = cfg.read_bytes()
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="bbbbbbbbbbbbbbbb", ts=2)

    assert res["registered"] is False  # clobber 금지
    assert cfg.read_bytes() == original_bytes  # 원본 바이트 그대로 보존


# ─────────────────────────── 원자적 쓰기 (truncate 손상 방지) ───────────────────────────

def test_register_write_is_correct(tmp_path):
    """원자적 쓰기 후 내용이 정확(merge 결과 그대로)."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {"keep000000000000": {"path": "/k", "ts": 1, "open": False}})
    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="newnewnewnewnew0", ts=42)

    assert res["registered"] is True
    data = json.loads(cfg.read_text())
    assert data["vaults"]["keep000000000000"]["path"] == "/k"
    assert data["vaults"]["newnewnewnewnew0"]["ts"] == 42


def test_register_no_tmp_leftover(tmp_path):
    """원자 교체 후 임시 파일이 남지 않는다(디렉토리에 obsidian.json 만)."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {})
    memory = tmp_path / "memory"
    memory.mkdir()

    il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="cccccccccccccccc", ts=3)

    leftovers = [p.name for p in cfg.parent.iterdir() if p.name != "obsidian.json"]
    assert leftovers == []


def test_register_atomic_no_truncation_on_write_failure(tmp_path, monkeypatch):
    """쓰기 도중 실패해도 원본이 잘리지 않는다(원자성) — tmp 미커밋, 원본 무손상."""
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {"orig000000000000": {"path": "/o", "ts": 1, "open": False}})
    original_bytes = cfg.read_bytes()
    memory = tmp_path / "memory"
    memory.mkdir()

    # os.replace 가 실패하도록 패치(tmp→target 커밋 직전 실패 시뮬레이션).
    import os as _os
    real_replace = _os.replace

    def boom(src, dst, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(_os, "replace", boom)

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="ddddddddddddddd0", ts=9)

    assert res["registered"] is False  # 비치명
    assert cfg.read_bytes() == original_bytes  # 원본 무손상(truncate 0)
    # tmp 잔여 없음
    monkeypatch.setattr(_os, "replace", real_replace)
    leftovers = [p.name for p in cfg.parent.iterdir() if p.name != "obsidian.json"]
    assert leftovers == []


# ─────────────────────────── 심링크/read-only 회귀 (Case E·F) ───────────────────────────

def test_register_symlink_follows_to_real_target(tmp_path):
    """Case E: obsidian.json 이 심링크면 실타깃에 merge, 링크 유지."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_target = real_dir / "obsidian-real.json"
    _write_config(real_target, {"keep000000000000": {"path": "/k", "ts": 1, "open": True}})

    cfg_dir = tmp_path / ".config" / "obsidian"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "obsidian.json"
    cfg.symlink_to(real_target)

    memory = tmp_path / "memory"
    memory.mkdir()

    res = il.register_obsidian_vault(
        memory, config_path=cfg, vault_id="symsymsymsymsym0", ts=7)

    assert res["registered"] is True
    # 링크 유지(심링크 그대로)
    assert cfg.is_symlink()
    # 실타깃에 merge(기존 보존 + 신규)
    data = json.loads(real_target.read_text())
    assert data["vaults"]["keep000000000000"]["path"] == "/k"
    assert "symsymsymsymsym0" in data["vaults"]


def test_register_read_only_is_non_fatal(tmp_path):
    """Case F: 설정 파일이 read-only(쓰기 불가) → 비치명 skip, 원본 무손상."""
    import os
    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {"orig000000000000": {"path": "/o", "ts": 1, "open": False}})
    original_bytes = cfg.read_bytes()
    memory = tmp_path / "memory"
    memory.mkdir()

    # 디렉토리를 read-only 로 만들어 tmp 생성/replace 를 막는다.
    os.chmod(cfg.parent, 0o500)
    try:
        res = il.register_obsidian_vault(
            memory, config_path=cfg, vault_id="ro00000000000000", ts=8)
    finally:
        os.chmod(cfg.parent, 0o700)

    assert res["registered"] is False  # 비치명
    assert cfg.read_bytes() == original_bytes  # 원본 무손상


# ─────────────────────────── install.py --register-obsidian (CLI) ───────────────────────────

def _run_install(argv):
    import runpy
    saved = sys.argv[:]
    try:
        mod = runpy.run_path(str(REPO / "infra" / "install.py"),
                             run_name="__register_obsidian_test__")
        return mod["main"](argv)
    finally:
        sys.argv = saved


def _team_root(tmp_path):
    root = tmp_path / "team"
    (root / "memory").mkdir(parents=True)
    (root / "team.config.json").write_text(
        json.dumps({"spec_version": "0.1", "team": {"name": "acme"}}),
        encoding="utf-8")
    return root


def test_cli_register_obsidian_merges(tmp_path, monkeypatch, capsys):
    """--register-obsidian + --obsidian-config <tmp> → 기존 볼트 보존 merge."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cfg = tmp_path / ".config" / "obsidian" / "obsidian.json"
    _write_config(cfg, {"keepme0000000000": {"path": "/x/y", "ts": 1, "open": False}})

    root = _team_root(tmp_path)
    rc = _run_install([
        "--root", str(root), "--register-obsidian",
        "--obsidian-config", str(cfg)])

    assert rc == 0
    vaults = json.loads(cfg.read_text())["vaults"]
    assert "keepme0000000000" in vaults  # 기존 보존
    assert len(vaults) == 2  # 기존 + 신규
    assert (root / "memory" / ".obsidian").is_dir()


def test_cli_register_obsidian_skips_when_not_installed(tmp_path, monkeypatch):
    """미설치(부모 부재) → exit 0(비치명), 실 호스트 무접촉."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cfg = tmp_path / "nowhere" / "obsidian" / "obsidian.json"
    root = _team_root(tmp_path)
    rc = _run_install([
        "--root", str(root), "--register-obsidian",
        "--obsidian-config", str(cfg)])

    assert rc == 0  # 비치명
    assert not cfg.exists()
