#!/usr/bin/env python3
"""teammode check — 단일 검수 도구 3-in-1 (스펙 02 §11.12).

  lint    — 정적: manifest 정규형·events.json 완전성 등 (엔진 실행 없음)
  verify  — 동적: 골든 시나리오를 우리 툴킷에 실행 (독푸딩 검수)
  conform — 동적+Tier: 같은 골든 시나리오를 임의 구현에 실행 + advisory 순응률로 Tier 산출
            (스펙 03 §3 conformance kit의 실물)

verify와 conform은 같은 골든 시나리오 정의(conformance/scenarios/)를 공유한다 —
시나리오 = 실행 가능한 스펙. 빈 엔진(no-op)에 돌리면 전부 RED = 엔진의 인수 테스트.

엔진은 argv를 받아 Result(exit_code, stdout, stderr)를 돌려주고 root 아래에
파일 부작용을 내는 하니스 인터페이스만 만족하면 된다 (스펙 03 §2 C2 주의:
파일 배치·언어 비강제).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ──────────────────────────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────────────────────────

@dataclass
class Result:
    """엔진 1회 실행 결과."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class Step:
    name: str
    action: dict
    expect: list

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(
            name=d.get("name", ""),
            action=d.get("action", {"kind": "noop"}),
            expect=d.get("expect", []),
        )


@dataclass
class Scenario:
    id: str
    title: str
    tier_signal: str  # "deterministic" | "advisory"
    steps: list
    spec_refs: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(
            id=d["id"],
            title=d.get("title", d["id"]),
            tier_signal=d.get("tier_signal", "deterministic"),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            spec_refs=d.get("spec_refs", []),
        )


@dataclass
class AssertionResult:
    kind: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    id: str
    tier_signal: str
    passed: bool
    assertions: list = field(default_factory=list)


@dataclass
class TierResult:
    compliant: bool
    tier: Optional[int]
    advisory_compliance: float
    deterministic_pass: bool


@dataclass
class Report:
    mode: str
    results: list = field(default_factory=list)
    tier: Optional[TierResult] = None

    @property
    def green(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)


@dataclass
class LintReport:
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c[1] for c in self.checks)


# ──────────────────────────────────────────────────────────────────
# 시나리오 파싱
# ──────────────────────────────────────────────────────────────────

def load_scenarios(scenario_dir) -> list:
    scenario_dir = Path(scenario_dir)
    scenarios = []
    for path in sorted(scenario_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            scenarios.append(Scenario.from_dict(json.load(f)))
    return scenarios


# ──────────────────────────────────────────────────────────────────
# Assertion 평가
# ──────────────────────────────────────────────────────────────────

def _sessions_dir(root: Path, author: str) -> Path:
    return Path(root) / "memory" / "team" / "sessions" / author


def _session_log_files(root: Path, author: str) -> list:
    d = _sessions_dir(root, author)
    if not d.is_dir():
        return []
    # 세션로그 네임스페이스: YYYY-MM-DD 로 시작하는 .md (스펙 01 §2.1)
    out = []
    for p in d.glob("*.md"):
        stem = p.stem
        if len(stem) >= 10 and stem[:4].isdigit() and stem[4] == "-":
            out.append(p)
    return out


def _eval_assertion(a: dict, root: Path, last: Optional[Result]) -> AssertionResult:
    kind = a.get("kind")
    root = Path(root)

    if kind == "exit_code":
        got = last.exit_code if last else None
        ok = got == a.get("value")
        return AssertionResult(kind, ok, f"exit_code={got} want={a.get('value')}")

    if kind == "stdout_contains":
        text = last.stdout if last else ""
        ok = a.get("value", "") in text
        return AssertionResult(kind, ok, f"stdout missing {a.get('value')!r}" if not ok else "")

    if kind == "stderr_contains":
        text = last.stderr if last else ""
        ok = a.get("value", "") in text
        return AssertionResult(kind, ok, "")

    if kind == "file_exists":
        ok = (root / a["path"]).is_file()
        return AssertionResult(kind, ok, f"missing {a['path']}" if not ok else "")

    if kind == "file_contains":
        p = root / a["path"]
        ok = p.is_file() and a.get("value", "") in p.read_text(encoding="utf-8")
        return AssertionResult(kind, ok, "")

    if kind == "session_log_single_file":
        files = _session_log_files(root, a["author"])
        ok = len(files) == 1
        return AssertionResult(kind, ok, f"{len(files)} session-log files" if not ok else "")

    if kind == "session_log_contains":
        files = _session_log_files(root, a["author"])
        blob = "".join(p.read_text(encoding="utf-8") for p in files)
        ok = a.get("value", "") in blob
        return AssertionResult(kind, ok, "")

    if kind == "state_off":
        # off 상태 영속화: .acme-active 마커 부재
        ok = not (root / ".acme-active").exists()
        return AssertionResult(kind, ok, "active marker still present" if not ok else "")

    if kind == "state_on":
        ok = (root / ".acme-active").exists()
        return AssertionResult(kind, ok, "")

    return AssertionResult(kind or "?", False, f"unknown assertion kind: {kind!r}")


# ──────────────────────────────────────────────────────────────────
# 시나리오 실행
# ──────────────────────────────────────────────────────────────────

def _apply_action(action: dict, engine, root: Path, last: Optional[Result]):
    """동작을 수행하고 (새 last Result) 반환. noop은 이전 Result 유지."""
    kind = action.get("kind", "noop")
    if kind == "command":
        return engine.run(action.get("argv", []))
    if kind == "fs_write":
        p = root / action["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(action.get("content", ""), encoding="utf-8")
        return last
    if kind == "noop":
        return last
    # 알 수 없는 action — 실패 신호를 위해 비정상 Result
    return Result(exit_code=127, stderr=f"unknown action kind: {kind!r}")


def run_scenario(scenario: Scenario, engine, root) -> ScenarioResult:
    root = Path(root)
    last: Optional[Result] = None
    all_assertions = []
    passed = True
    for step in scenario.steps:
        last = _apply_action(step.action, engine, root, last)
        for a in step.expect:
            ar = _eval_assertion(a, root, last)
            all_assertions.append(ar)
            if not ar.passed:
                passed = False
    return ScenarioResult(scenario.id, scenario.tier_signal, passed, all_assertions)


# ──────────────────────────────────────────────────────────────────
# Tier 산출 (§11.11)
# ──────────────────────────────────────────────────────────────────

def compute_tier(results: list) -> TierResult:
    """결정적 시나리오가 전부 통과해야 호환. advisory 순응률로 Tier 등급.

    Tier 1 = advisory 100% / Tier 2 = advisory 부분 / Tier 3 = advisory 0.
    결정적 실패가 하나라도 있으면 compliant=False (Tier 미산정).
    """
    det = [r for r in results if r.tier_signal == "deterministic"]
    adv = [r for r in results if r.tier_signal == "advisory"]

    deterministic_pass = all(r.passed for r in det) if det else True

    if adv:
        advisory_compliance = sum(1 for r in adv if r.passed) / len(adv)
    else:
        advisory_compliance = 1.0

    if not deterministic_pass:
        return TierResult(False, None, advisory_compliance, deterministic_pass)

    if advisory_compliance >= 1.0:
        tier = 1
    elif advisory_compliance > 0.0:
        tier = 2
    else:
        tier = 3
    return TierResult(True, tier, advisory_compliance, deterministic_pass)


# ──────────────────────────────────────────────────────────────────
# 모드 디스패치
# ──────────────────────────────────────────────────────────────────

def run_mode(mode: str, engine, root, scenario_dir=None) -> Report:
    if scenario_dir is None:
        scenario_dir = Path(__file__).resolve().parent / "scenarios"
    scenarios = load_scenarios(scenario_dir)
    results = [run_scenario(s, engine, root) for s in scenarios]
    report = Report(mode=mode, results=results)
    if mode == "conform":
        report.tier = compute_tier(results)
    return report


# ── lint (정적) ──

def _lint_manifest_canonical(root: Path) -> tuple:
    """manifest.json에 에이전트 고유 표기(mcp__, Write|Edit 등)가 없는지 (스펙 02 §3, K4)."""
    manifest_path = Path(root) / "infra" / "hooks" / "manifest.json"
    if not manifest_path.is_file():
        return ("manifest 정규형", True, "manifest 없음 — 건너뜀")
    text = manifest_path.read_text(encoding="utf-8")
    forbidden = ["mcp__", "Write|Edit", "apply_patch"]
    hits = [tok for tok in forbidden if tok in text]
    return ("manifest 정규형", not hits,
            f"에이전트 고유 표기 발견: {hits}" if hits else "")


def run_lint(root) -> LintReport:
    root = Path(root)
    checks = []
    checks.append(_lint_manifest_canonical(root))
    return LintReport(checks=checks)


# ──────────────────────────────────────────────────────────────────
# 실제 엔진 어댑터 (CLI에서 verify/conform 시 사용)
# ──────────────────────────────────────────────────────────────────

class SubprocessEngine:
    """엔진 CLI를 subprocess로 호출하는 실엔진 하니스.

    engine_cmd: argv 앞에 붙는 실행 prefix (예: ["python3", "infra/teammode.py"]).
    cwd: 엔진 작업 디렉토리(= 팀 루트). 파일 부작용도 여기 기준.
    """

    def __init__(self, engine_cmd: list, cwd):
        self.engine_cmd = list(engine_cmd)
        self.root = Path(cwd)

    # 격리에 필요한 최소 ambient 변수만 통과시킨다. 그 외(특히 TEAMMODE_HOME·
    # LEGACY_TOOL_HOME 같은 팀 루트 지시 변수)는 절대 상속하지 않는다 — `env -i` 정신.
    # ※ 엔진은 더 이상 env 로 팀 루트를 받지 않는다(P1) — 팀 루트는 `--root` 로 명시
    #   전달한다. env 화이트리스트는 2차 방어선(혹 다른 구현이 env 를 읽어도 누수 0).
    _PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
                    "SYSTEMROOT", "PATHEXT", "TZ", "PYTHONPATH", "TERM")

    def _isolated_env(self) -> dict:
        """ambient를 차단하고 필수 변수만 담은 env. 팀 루트 지시 변수는 통과 안 됨."""
        return {k: os.environ[k] for k in self._PASSTHROUGH if k in os.environ}

    def run(self, argv) -> Result:
        # 엔진을 run root(=검사 대상 팀 루트)에 고정한다. 팀 루트는 `--root` 명시 인자로
        # 전달하고(P1: env 비신뢰), env 화이트리스트로 ambient TEAMMODE_HOME/LEGACY_TOOL_HOME
        # 누수도 차단한다(이중 방어, 스펙 01 §2.4). 첫 토큰(동사) 뒤에 --root 를 끼운다.
        argv = list(argv)
        if argv:
            full = self.engine_cmd + [argv[0], "--root", str(self.root)] + argv[1:]
        else:
            full = self.engine_cmd + ["--root", str(self.root)]
        proc = subprocess.run(
            full,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self._isolated_env(),
        )
        return Result(proc.returncode, proc.stdout, proc.stderr)


def _print_report(report: Report) -> int:
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.id} ({r.tier_signal})")
        if not r.passed:
            for a in r.assertions:
                if not a.passed:
                    print(f"        ✗ {a.kind}: {a.detail}")
    if report.tier is not None:
        t = report.tier
        if t.compliant:
            print(f"\nTier {t.tier} — advisory 순응률 {t.advisory_compliance:.0%}")
        else:
            print("\n비호환: 결정적 시나리오 실패")
    print(f"\n{'GREEN' if report.green else 'RED'}: "
          f"{sum(1 for r in report.results if r.passed)}/{len(report.results)} 통과")
    return 0 if report.green else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="teammode check", description=__doc__)
    parser.add_argument("mode", choices=["lint", "verify", "conform"])
    parser.add_argument("--root", default=".", help="팀 루트 (검사 대상 레포)")
    parser.add_argument("--engine", default=None,
                        help="엔진 실행 prefix (예: 'python3 infra/teammode.py'). "
                             "verify/conform에 필요")
    parser.add_argument("--scenario-dir", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()

    if args.mode == "lint":
        report = run_lint(root)
        for name, ok, detail in report.checks:
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
        return 0 if report.ok else 1

    if args.engine is None:
        print("[error] verify/conform에는 --engine 이 필요합니다.", file=sys.stderr)
        return 2
    engine_cmd = args.engine.split()
    # 레퍼런스 엔진(teammode.py)은 settings 경로를 명시로만 받는다(P2). 검수는 실
    # ~/.claude 를 절대 건드리면 안 되므로, run root 하위 격리 settings 를 주입한다.
    # --settings 를 모르는 타 구현은 미지 플래그로 무시한다(§2 C2: 플래그 비강제).
    # 사용자가 이미 --settings 를 넣었으면 덮어쓰지 않는다.
    if "--settings" not in engine_cmd:
        engine_cmd = engine_cmd + ["--settings", str(root / ".teammode-settings.json")]
    engine = SubprocessEngine(engine_cmd, root)
    sdir = Path(args.scenario_dir) if args.scenario_dir else None
    report = run_mode(args.mode, engine, root, scenario_dir=sdir)
    return _print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
