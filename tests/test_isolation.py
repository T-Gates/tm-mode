"""슬라이스 0 + P1 — 환경 격리 회귀 테스트.

P0 사고 재발 방지: ambient 환경에 TEAMMODE_HOME(또는 구 LEGACY_TOOL_HOME)이 set돼 있어도
verify/conform 러너는 그 경로를 절대 건드리지 않는다. SubprocessEngine 은 ambient
env 를 차단하고 run root 만 명시 주입해야 한다(`env -i` 정신).

P1 사고 근본: 엔진(teammode.py)이 ambient `TEAMMODE_HOME`을 무조건 신뢰하면,
SubprocessEngine 격리를 우회한 직접 CLI 호출 시 동일 사고가 재현된다. 엔진은
팀 루트를 **명시 인자 `--root`로만** 받아야 하며 env 를 절대 읽지 않는다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "conformance"))
import check  # noqa: E402

ENGINE = [sys.executable, str(REPO / "infra" / "teammode.py")]
SCENARIO_DIR = REPO / "conformance" / "scenarios"


def test_isolated_env_excludes_ambient_team_home(tmp_path, monkeypatch):
    # ambient 에 실 호스트를 가리키는 변수를 심는다
    fake_host = tmp_path / "REAL_HOST"
    fake_host.mkdir()
    monkeypatch.setenv("TEAMMODE_HOME", str(fake_host))
    monkeypatch.setenv("LEGACY_TOOL_HOME", str(fake_host))

    run_root = tmp_path / "runroot"
    run_root.mkdir()
    eng = check.SubprocessEngine(ENGINE, run_root)
    env = eng._isolated_env()
    # P1: 엔진은 팀 루트를 env 가 아니라 `--root` 로 받는다 → env 에 팀 루트 지시
    # 변수가 아예 없어야 한다(ambient 누수 0). run root 는 argv 의 --root 로 전달됨.
    assert "TEAMMODE_HOME" not in env
    assert "LEGACY_TOOL_HOME" not in env
    # 화이트리스트 변수는 그대로 통과 (PATH 등)
    assert "PATH" in env or "PATH" not in os.environ
    # run root 는 run() 이 argv 에 --root 로 끼워 넣는다
    full = eng.engine_cmd + ["off", "--root", str(run_root)]
    assert "--root" in full and str(run_root) in full


def test_verify_does_not_touch_ambient_host(tmp_path, monkeypatch):
    """ambient TEAMMODE_HOME=실호스트 set 상태로 verify 돌려도 그 경로 무접촉."""
    fake_host = tmp_path / "REAL_HOST"
    fake_host.mkdir()
    # 실호스트에 ON 마커를 미리 둔다 — off 시나리오가 이걸 지우면 격리 실패
    (fake_host / ".acme-active").write_text("")
    (fake_host / "memory").mkdir()
    sentinel = fake_host / "memory" / "banner.txt"
    sentinel.write_text("ORIGINAL HOST BANNER")

    monkeypatch.setenv("TEAMMODE_HOME", str(fake_host))
    monkeypatch.setenv("LEGACY_TOOL_HOME", str(fake_host))

    run_root = tmp_path / "runroot"
    run_root.mkdir()
    # 엔진이 settings 를 격리 cwd 에 쓰도록 --settings 지정
    eng = check.SubprocessEngine(
        ENGINE + ["--settings", str(run_root / "settings.json")], run_root)
    report = check.run_mode("verify", eng, run_root, scenario_dir=SCENARIO_DIR)

    # 핵심 단언: 실호스트의 마커·배너가 그대로 살아있다 (오염 0)
    assert (fake_host / ".acme-active").exists(), "실호스트 ON 마커가 삭제됨 — 격리 실패!"
    assert sentinel.read_text() == "ORIGINAL HOST BANNER", "실호스트 배너가 덮어써짐 — 격리 실패!"
    # 작업은 격리 run root 에서 일어났다
    assert (run_root / "memory" / "banner.txt").exists()
    # on/off 시나리오는 정상 동작 (격리해도 기능은 그대로)
    by_id = {r.id: r for r in report.results}
    assert by_id["05-off-persist"].passed is True
    assert by_id["01-on-banner"].passed is True


def test_conform_also_isolated(tmp_path, monkeypatch):
    fake_host = tmp_path / "REAL_HOST"
    fake_host.mkdir()
    (fake_host / ".acme-active").write_text("")
    monkeypatch.setenv("TEAMMODE_HOME", str(fake_host))

    run_root = tmp_path / "runroot"
    run_root.mkdir()
    eng = check.SubprocessEngine(
        ENGINE + ["--settings", str(run_root / "settings.json")], run_root)
    check.run_mode("conform", eng, run_root, scenario_dir=SCENARIO_DIR)
    assert (fake_host / ".acme-active").exists(), "conform 이 실호스트 마커 삭제 — 격리 실패!"


# ──────────────────────────────────────────────────────────────────
# P1-b — 엔진 직접 CLI 호출 시에도 ambient env 무신뢰 (SubprocessEngine 우회)
# ──────────────────────────────────────────────────────────────────

def _run_engine(args, cwd, env):
    """teammode.py 를 SubprocessEngine 안 거치고 직접 호출 (피해자 env 그대로)."""
    return subprocess.run(
        ENGINE + list(args), cwd=str(cwd), capture_output=True, text=True, env=env)


def test_direct_off_ignores_ambient_team_home(tmp_path):
    """ambient TEAMMODE_HOME=피해자 set 상태로 `off --root <격리>` 직접 호출 →
    피해자 경로의 .acme-active 는 생존한다 (엔진이 env 를 안 읽음)."""
    victim = tmp_path / "VICTIM"
    victim.mkdir()
    (victim / ".acme-active").write_text("")

    run_root = tmp_path / "runroot"
    run_root.mkdir()

    env = dict(os.environ)
    env["TEAMMODE_HOME"] = str(victim)   # 피해자를 가리키도록 심는다
    env["LEGACY_TOOL_HOME"] = str(victim)

    proc = _run_engine(
        ["off", "--root", str(run_root),
         "--settings", str(run_root / "settings.json")],
        cwd=run_root, env=env)

    assert proc.returncode == 0, proc.stderr
    # 엔진이 env 를 읽었다면 피해자 마커가 삭제됐을 것 — 생존해야 통과
    assert (victim / ".acme-active").exists(), (
        "엔진이 ambient TEAMMODE_HOME 을 읽어 피해자 마커 삭제 — 격리 실패!")


def test_direct_off_without_root_errors(tmp_path):
    """`--root` 미지정 시 정책 (A): 에러로 즉시 종료 (어느 폴더도 안 건드림)."""
    victim = tmp_path / "VICTIM"
    victim.mkdir()
    (victim / ".acme-active").write_text("")

    env = dict(os.environ)
    env["TEAMMODE_HOME"] = str(victim)

    proc = _run_engine(["off"], cwd=victim, env=env)

    assert proc.returncode != 0, "--root 없으면 에러 종료해야 함 (정책 A)"
    assert "--root" in proc.stderr
    # cwd(피해자) 로도 폴백하지 않는다 — 마커 생존
    assert (victim / ".acme-active").exists(), (
        "--root 없는데 cwd 를 건드림 — 정책 A 위반!")


def test_direct_on_without_root_errors(tmp_path):
    env = dict(os.environ)
    env["TEAMMODE_HOME"] = str(tmp_path / "VICTIM")
    proc = _run_engine(["on"], cwd=tmp_path, env=env)
    assert proc.returncode != 0
    assert "--root" in proc.stderr


def test_direct_on_with_root_writes_only_to_root(tmp_path):
    """`on --root <격리>` 는 격리 루트에만 배너·마커를 만든다."""
    run_root = tmp_path / "runroot"
    run_root.mkdir()
    env = dict(os.environ)
    env["TEAMMODE_HOME"] = str(tmp_path / "VICTIM")  # 무시돼야 함

    proc = _run_engine(
        ["on", "--root", str(run_root),
         "--settings", str(run_root / "settings.json")],
        cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert (run_root / ".acme-active").exists()
    assert (run_root / "memory" / "banner.txt").is_file()


# ──────────────────────────────────────────────────────────────────
# P2 — --settings 생략 시 실 ~/.claude 오염 방지 가드
# ──────────────────────────────────────────────────────────────────

def test_settings_omitted_without_install_refuses(tmp_path):
    """--settings 도 --install 도 없으면 ~/.claude 쓰기 거부 (에러 종료)."""
    run_root = tmp_path / "runroot"
    run_root.mkdir()
    env = dict(os.environ)
    proc = _run_engine(["on", "--root", str(run_root)], cwd=tmp_path, env=env)
    assert proc.returncode != 0, "격리 모드(--settings)도 --install 도 없으면 거부해야 함"
    assert "settings" in proc.stderr.lower() or "install" in proc.stderr.lower()
    # 거부됐으므로 마커도 안 생긴다 (settings 단계 이전에 차단)
    assert not (run_root / ".acme-active").exists()
