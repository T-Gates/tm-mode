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


def test_fs_delete_action_removes_fixture(tmp_path):
    # 03 teardown 경로: fs_write 로 세운 fixture 를 fs_delete 가 원복(공유 root 오염 방지).
    (tmp_path / "team.config.json").write_text("{}", encoding="utf-8")
    sc = _scenario([
        {"name": "teardown", "action": {"kind": "fs_delete", "path": "team.config.json"},
         "expect": []},
    ])
    eng = FakeEngine(tmp_path)
    assert check.run_scenario(sc, eng, tmp_path).passed is True
    assert not (tmp_path / "team.config.json").exists()


def test_fs_delete_action_ignores_traversal(tmp_path):
    # root 밖(상위 traversal)은 무시 — 시나리오 정리가 호스트를 건드리지 못하게.
    # base/root 안에서 동작하도록 root 를 한 단계 안으로 둔다.
    root = tmp_path / "root"
    root.mkdir()
    parent_outside = tmp_path / "do-not-delete.txt"
    parent_outside.write_text("keep", encoding="utf-8")
    # 형제-prefix 우회: `/base/root` 가 `/base/root-evil` 의 문자열 prefix 이지만
    # root-evil 은 root 밖이다. 순수 prefix 가드는 이 케이스를 못 막는다(고전 버그).
    sibling_evil = tmp_path / "root-evil"
    sibling_evil.mkdir()
    sibling_secret = sibling_evil / "secret.txt"
    sibling_secret.write_text("keep", encoding="utf-8")
    sc = _scenario([
        {"name": "evil-parent",
         "action": {"kind": "fs_delete", "path": "../do-not-delete.txt"},
         "expect": []},
        {"name": "evil-sibling-prefix",
         "action": {"kind": "fs_delete", "path": "../root-evil/secret.txt"},
         "expect": []},
    ])
    eng = FakeEngine(root)
    check.run_scenario(sc, eng, root)
    assert parent_outside.exists()   # 부모탈출 — 삭제 안 됨
    assert sibling_secret.exists()   # 형제-prefix — 삭제 안 됨


def test_fs_delete_missing_file_is_noop(tmp_path):
    sc = _scenario([
        {"name": "del", "action": {"kind": "fs_delete", "path": "nope.json"}, "expect": []},
    ])
    eng = FakeEngine(tmp_path)
    # 없는 파일 삭제는 크래시 없이 통과(빈 expect)
    assert check.run_scenario(sc, eng, tmp_path).passed is True


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
        active = root / ".teammode-active"
        if active.exists():
            active.unlink()

    sc = _scenario([
        {"name": "off", "action": {"kind": "command", "argv": ["off"]},
         "expect": [{"kind": "exit_code", "value": 0}]},
        {"name": "persisted", "action": {"kind": "noop"},
         "expect": [{"kind": "state_off"}]},
    ])
    (tmp_path / ".teammode-active").write_text("")
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


# ──────────────────────────────────────────────────────────────────
# 5. 스킬 본문 정규형 린트 (K7 — SPEC §2.12·§7.3, L2-H H.3)
# ──────────────────────────────────────────────────────────────────

def _write_skill(root, name, body):
    d = root / "infra" / "skills" / "base" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d / "SKILL.md"


def test_skill_lint_passes_on_role_vocabulary(tmp_path):
    _write_skill(tmp_path, "good",
                 "# good\n이슈 트래커 MCP 에서 조회하고 채팅에 알린다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok is True, detail


def test_skill_lint_rejects_mcp_direct_notation(tmp_path):
    _write_skill(tmp_path, "bad_mcp",
                 "# bad\nmcp__linear__list_issues 로 조회한다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok is False
    assert "mcp__" in detail


def test_skill_lint_rejects_product_name_from_providers(tmp_path):
    # providers/ 에 있는 provider 이름(= 제품 식별자)이 스킬 본문에 박히면 위반.
    pdir = tmp_path / "providers"
    pdir.mkdir()
    (pdir / "linear.json").write_text('{"provider": "linear"}', encoding="utf-8")
    _write_skill(tmp_path, "bad_prod",
                 "# bad\nLinear 의 이슈를 만든다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok is False
    assert "linear" in detail.lower()


def test_skill_lint_word_boundary_no_false_positive(tmp_path):
    # 'googler' 같은 부분일치는 거짓양성으로 잡지 않는다.
    _write_skill(tmp_path, "boundary",
                 "# ok\ngoogler 라는 단어는 제품명이 아니다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok is True, detail


def test_skill_lint_clean_on_real_repo():
    # 실 레포의 스킬 본문(tm-onboard·tm-connect)은 정규형을 지킨다.
    report = check.lint_skill_canonical(REPO)
    assert report[1] is True, report[2]
    # tm-connect 가 실제로 검사 대상에 들어왔는지(빈 통과가 아님) 확인.
    connect = REPO / "infra" / "skills" / "core" / "tm-connect" / "SKILL.md"
    assert connect.is_file()


def _write_skill_tier(root, tier, name, body):
    d = root / "infra" / "skills" / tier / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d / "SKILL.md"


def test_skill_lint_exempts_instance_owned_util_tier(tmp_path):
    # util/ 은 인스턴스 소유 커스터마이즈 계층 — 팀이 자기 연결 서비스를 문서화하며
    # 제품명·mcp__ 를 쓰는 것이 정당하다. 기본 스캔(files=None)에서 제외돼야 한다.
    pdir = tmp_path / "providers"
    pdir.mkdir()
    (pdir / "google.json").write_text('{"provider": "google"}', encoding="utf-8")
    _write_skill_tier(tmp_path, "util", "myteam-sched",
                      "# sched\nGoogle 캘린더를 mcp__google_calendar__list 로 조회한다.\n")
    _write_skill_tier(tmp_path, "core", "x",
                      "# x\n이슈 트래커 MCP 에서 조회한다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok is True, detail


def test_skill_lint_core_tier_still_guarded(tmp_path):
    # util 면제가 core(제품 스킬)까지 새지 않는다 — core 의 제품명은 여전히 위반.
    pdir = tmp_path / "providers"
    pdir.mkdir()
    (pdir / "google.json").write_text('{"provider": "google"}', encoding="utf-8")
    _write_skill_tier(tmp_path, "util", "myteam-sched",
                      "# sched\nGoogle 캘린더를 mcp__google_calendar__list 로 조회한다.\n")
    _write_skill_tier(tmp_path, "core", "x",
                      "# x\nGoogle 캘린더를 조회한다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path)
    assert ok is False
    assert "google" in detail.lower()
    # util 파일은 위반 목록에 나타나지 않는다.
    assert "myteam-sched" not in detail


def test_skill_lint_explicit_files_bypass_util_exemption(tmp_path):
    # files 명시 주입 시(테스트 격리) 필터링하지 않는다 — 검사 대상은 호출자가 정한다.
    f = _write_skill_tier(tmp_path, "util", "myteam-sched",
                          "# sched\nmcp__google_calendar__list 로 조회한다.\n")
    name, ok, detail = check.lint_skill_canonical(tmp_path, files=[f])
    assert ok is False
    assert "mcp__" in detail


def test_lint_report_includes_skill_check():
    report = check.run_lint(REPO)
    names = [c[0] for c in report.checks]
    assert "스킬 본문 정규형" in names
    assert report.ok is True


# ──────────────────────────────────────────────────────────────────
# 6. tm-connect frontmatter — tm-onboard 포맷 일관 (L2-H H.3)
# ──────────────────────────────────────────────────────────────────

def _frontmatter(skill_path):
    text = skill_path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill_path} 는 YAML frontmatter 로 시작해야 함"
    end = text.index("\n---", 4)
    block = text[4:end]
    fm = {}
    for raw in block.splitlines():
        if ":" in raw and not raw.startswith(" "):
            k, _, v = raw.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def test_tm_connect_frontmatter_matches_onboard_format():
    onboard = REPO / "infra" / "skills" / "base" / "tm-onboard" / "SKILL.md"
    connect = REPO / "infra" / "skills" / "core" / "tm-connect" / "SKILL.md"
    fm_on = _frontmatter(onboard)
    fm_co = _frontmatter(connect)
    # 같은 필드 집합(name·description) + 트리거 문구 포함 — tm-onboard 포맷 일관.
    assert set(fm_co) == set(fm_on) == {"name", "description"}
    assert fm_co["name"] == "tm-connect"
    assert "Triggers" in fm_co["description"] or "Trigger" in fm_co["description"]
