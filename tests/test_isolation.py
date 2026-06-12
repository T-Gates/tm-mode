"""슬라이스 0 — 환경 격리 회귀 테스트 (P0).

사고 재발 방지: ambient 환경에 TEAMMODE_HOME(또는 구 LEGACY_TOOL_HOME)이 set돼 있어도
verify/conform 러너는 그 경로를 절대 건드리지 않는다. SubprocessEngine 은 ambient
env 를 차단하고 run root 만 명시 주입해야 한다(`env -i` 정신).
"""
import os
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
    # run root 가 유일한 TEAMMODE_HOME, 구 변수는 통과 안 됨
    assert env["TEAMMODE_HOME"] == str(run_root)
    assert "LEGACY_TOOL_HOME" not in env
    assert env["TEAMMODE_HOME"] != str(fake_host)


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
