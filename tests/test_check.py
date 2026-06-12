"""슬라이스 1 — conformance/check.py 러너 테스트.

검사 대상:
  1. 시나리오 파싱 (load_scenarios)
  2. 통과/실패 판정 (run_scenario + 각 assertion kind)
  3. Tier 산출 (compute_tier — advisory 순응률 기반, §11.11)
  4. verify/conform 모드가 골든 시나리오를 소비
모든 테스트는 tmp_path 픽스처를 쓴다 — 실환경 무접촉.
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "conformance"))

import check  # noqa: E402


SCENARIO_DIR = REPO / "conformance" / "scenarios"


# ──────────────────────────────────────────────────────────────────
# 1. 시나리오 파싱
# ──────────────────────────────────────────────────────────────────

def test_load_scenarios_finds_five():
    scenarios = check.load_scenarios(SCENARIO_DIR)
    assert len(scenarios) == 5
    ids = {s.id for s in scenarios}
    assert ids == {
        "01-on-banner",
        "02-context-injection",
        "03-issue-create",
        "04-log-accumulate",
        "05-off-persist",
    }


def test_scenario_has_steps_and_tier_signal():
    scenarios = {s.id: s for s in check.load_scenarios(SCENARIO_DIR)}
    on = scenarios["01-on-banner"]
    assert on.tier_signal == "deterministic"
    assert len(on.steps) >= 1
    assert on.steps[0].action["kind"] == "command"
    assert scenarios["04-log-accumulate"].tier_signal == "advisory"


def test_load_scenarios_skips_non_json(tmp_path):
    (tmp_path / "README.md").write_text("not a scenario")
    (tmp_path / "x.json").write_text(json.dumps(
        {"id": "x", "title": "t", "tier_signal": "deterministic", "steps": []}
    ))
    scenarios = check.load_scenarios(tmp_path)
    assert [s.id for s in scenarios] == ["x"]


# ──────────────────────────────────────────────────────────────────
# 2. 통과/실패 판정 — 가짜 엔진으로 assertion kind들을 구동
# ──────────────────────────────────────────────────────────────────

class FakeEngine:
    """argv → 미리 정해둔 (exit, stdout, stderr) + 파일 부작용을 흉내내는 엔진."""

    def __init__(self, root, responses=None, side_effects=None):
        self.root = Path(root)
        self.responses = responses or {}
        self.side_effects = side_effects or {}
        self.calls = []

    def run(self, argv):
        self.calls.append(list(argv))
        key = argv[0] if argv else ""
        for fn in self.side_effects.get(key, []):
            fn(self.root)
        exit_code, stdout, stderr = self.responses.get(key, (127, "", "no-op engine"))
        return check.Result(exit_code=exit_code, stdout=stdout, stderr=stderr)


def _scenario(steps, sid="t", tier="deterministic"):
    return check.Scenario.from_dict(
        {"id": sid, "title": sid, "tier_signal": tier, "steps": steps}
    )


def test_exit_code_assertion_pass_and_fail(tmp_path):
    sc = _scenario([
        {"name": "s", "action": {"kind": "command", "argv": ["on"]},
         "expect": [{"kind": "exit_code", "value": 0}]},
    ])
    eng_ok = FakeEngine(tmp_path, responses={"on": (0, "", "")})
    res_ok = check.run_scenario(sc, eng_ok, tmp_path)
    assert res_ok.passed is True

    eng_bad = FakeEngine(tmp_path, responses={"on": (1, "", "boom")})
    res_bad = check.run_scenario(sc, eng_bad, tmp_path)
    assert res_bad.passed is False


def test_stdout_contains_assertion(tmp_path):
    sc = _scenario([
        {"name": "s", "action": {"kind": "command", "argv": ["on"]},
         "expect": [{"kind": "stdout_contains", "value": "banner!"}]},
    ])
    eng = FakeEngine(tmp_path, responses={"on": (0, "the banner! here", "")})
    assert check.run_scenario(sc, eng, tmp_path).passed is True

    eng2 = FakeEngine(tmp_path, responses={"on": (0, "nothing", "")})
    assert check.run_scenario(sc, eng2, tmp_path).passed is False


def test_noop_step_inherits_last_command_output(tmp_path):
    sc = _scenario([
        {"name": "cmd", "action": {"kind": "command", "argv": ["context"]},
         "expect": [{"kind": "exit_code", "value": 0}]},
        {"name": "check", "action": {"kind": "noop"},
         "expect": [{"kind": "stdout_contains", "value": "INDEX"}]},
    ])
    eng = FakeEngine(tmp_path, responses={"context": (0, "INDEX here", "")})
    assert check.run_scenario(sc, eng, tmp_path).passed is True


def test_file_exists_and_file_contains(tmp_path):
    def make_banner(root):
        (root / "memory").mkdir(exist_ok=True)
        (root / "memory" / "banner.txt").write_text("BIG TEAM")

    sc = _scenario([
        {"name": "s", "action": {"kind": "command", "argv": ["on"]},
         "expect": [
             {"kind": "file_exists", "path": "memory/banner.txt"},
             {"kind": "file_contains", "path": "memory/banner.txt", "value": "BIG"},
         ]},
    ])
    eng = FakeEngine(tmp_path, responses={"on": (0, "", "")},
                     side_effects={"on": [make_banner]})
    assert check.run_scenario(sc, eng, tmp_path).passed is True

    # 파일이 안 생기면 실패 (깨끗한 루트에서 — 위 side_effect 잔재 격리)
    clean = tmp_path / "clean"
    clean.mkdir()
    eng2 = FakeEngine(clean, responses={"on": (0, "", "")})
    assert check.run_scenario(sc, eng2, clean).passed is False


def test_fs_write_action(tmp_path):
    sc = _scenario([
        {"name": "seed", "action": {"kind": "fs_write", "path": "memory/INDEX.md",
                                    "content": "hi"},
         "expect": [{"kind": "file_exists", "path": "memory/INDEX.md"}]},
    ])
    eng = FakeEngine(tmp_path)
    assert check.run_scenario(sc, eng, tmp_path).passed is True
    assert (tmp_path / "memory" / "INDEX.md").read_text() == "hi"


def test_session_log_single_file_and_contains(tmp_path):
    def write_log(root):
        d = root / "memory" / "team" / "sessions" / "eunsu"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "2026-06-13.md"
        prev = f.read_text() if f.exists() else "---\nauthor: eunsu\n---\n"
        f.write_text(prev + "\nentry")

    sc = _scenario([
        {"name": "log1", "action": {"kind": "command", "argv": ["log"]},
         "expect": [{"kind": "exit_code", "value": 0}]},
        {"name": "log2", "action": {"kind": "command", "argv": ["log"]},
         "expect": [
             {"kind": "session_log_single_file", "author": "eunsu"},
         ]},
    ])
    eng = FakeEngine(tmp_path, responses={"log": (0, "", "")},
                     side_effects={"log": [write_log]})
    assert check.run_scenario(sc, eng, tmp_path).passed is True

    # 분할 파일이 생기면 single_file 위반
    def write_split(root):
        d = root / "memory" / "team" / "sessions" / "bob"
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-06-13.md").write_text("a")
        (d / "2026-06-13-late.md").write_text("b")

    sc2 = _scenario([
        {"name": "log", "action": {"kind": "command", "argv": ["log"]},
         "expect": [{"kind": "session_log_single_file", "author": "bob"}]},
    ])
    eng2 = FakeEngine(tmp_path, responses={"log": (0, "", "")},
                      side_effects={"log": [write_split]})
    assert check.run_scenario(sc2, eng2, tmp_path).passed is False


def test_state_off_assertion(tmp_path):
    def go_off(root):
        active = root / ".tgates-active"
        if active.exists():
            active.unlink()

    sc = _scenario([
        {"name": "off", "action": {"kind": "command", "argv": ["off"]},
         "expect": [{"kind": "exit_code", "value": 0}]},
        {"name": "persisted", "action": {"kind": "noop"},
         "expect": [{"kind": "state_off"}]},
    ])
    (tmp_path / ".tgates-active").write_text("")
    eng = FakeEngine(tmp_path, responses={"off": (0, "", "")},
                     side_effects={"off": [go_off]})
    assert check.run_scenario(sc, eng, tmp_path).passed is True


def test_unknown_assertion_kind_is_failure_not_crash(tmp_path):
    sc = _scenario([
        {"name": "s", "action": {"kind": "command", "argv": ["on"]},
         "expect": [{"kind": "totally_made_up"}]},
    ])
    eng = FakeEngine(tmp_path, responses={"on": (0, "", "")})
    res = check.run_scenario(sc, eng, tmp_path)
    assert res.passed is False


# ──────────────────────────────────────────────────────────────────
# 3. Tier 산출 (§11.11 — advisory 순응률)
# ──────────────────────────────────────────────────────────────────

def test_compute_tier_all_pass_is_tier1():
    # deterministic 전부 통과 + advisory 전부 통과 → Tier 1
    results = [
        check.ScenarioResult("01-on-banner", "deterministic", True, []),
        check.ScenarioResult("04-log-accumulate", "advisory", True, []),
    ]
    tier = check.compute_tier(results)
    assert tier.tier == 1
    assert tier.advisory_compliance == 1.0


def test_compute_tier_advisory_miss_downgrades():
    # deterministic 통과하지만 advisory 실패 → Tier 강등 (1 미만)
    results = [
        check.ScenarioResult("01-on-banner", "deterministic", True, []),
        check.ScenarioResult("04-log-accumulate", "advisory", False, []),
    ]
    tier = check.compute_tier(results)
    assert tier.advisory_compliance == 0.0
    assert tier.tier >= 2


def test_compute_tier_deterministic_fail_is_noncompliant():
    # 결정적 시나리오 실패 = 비호환 (Tier 산정 이전에 탈락)
    results = [
        check.ScenarioResult("01-on-banner", "deterministic", False, []),
        check.ScenarioResult("04-log-accumulate", "advisory", True, []),
    ]
    tier = check.compute_tier(results)
    assert tier.compliant is False


# ──────────────────────────────────────────────────────────────────
# 4. 모드 디스패치 — verify/conform이 골든 시나리오를 소비
# ──────────────────────────────────────────────────────────────────

def test_run_verify_returns_results_for_all_scenarios(tmp_path):
    # no-op 엔진 → 전부 RED (인수 테스트로 박힘)
    eng = FakeEngine(tmp_path)
    report = check.run_mode("verify", eng, tmp_path, scenario_dir=SCENARIO_DIR)
    assert len(report.results) == 5
    assert all(r.passed is False for r in report.results)
    assert report.green is False


def test_run_conform_computes_tier(tmp_path):
    eng = FakeEngine(tmp_path)
    report = check.run_mode("conform", eng, tmp_path, scenario_dir=SCENARIO_DIR)
    assert report.tier is not None
    assert report.tier.compliant is False  # no-op 엔진은 비호환


def test_lint_mode_runs_without_engine(tmp_path):
    # lint = 정적 검사. 최소한 manifest 정규형 검사 1개라도 동작.
    # manifest 없는 빈 레포에서는 결과만 반환(크래시 금지).
    report = check.run_lint(tmp_path)
    assert hasattr(report, "checks")
    assert isinstance(report.checks, list)
