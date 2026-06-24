"""L2-F `issue` 동사 — issues 슬롯 확인 + 정규 입력 스키마 echo (B-4 altitude).

엔진은 issues 슬롯 provider 를 확인하고 **정규 입력 스키마를 stdout JSON 으로 echo
까지만** 한다. action_map 해석·페이로드 변환·실 MCP 호출은 하지 않는다(어댑터/스킬
몫 — SPEC §3 "엔진은 판단 안 함"). 빈 슬롯이면 [info] + exit 0(비치명).

P0-1: 하니스가 `issue --root <root> create --title …` 로 조립한다 — verb=issue,
서브액션 create 가 --root 뒤 positional 로 와도 정상 파싱.
V.4: 사용자 텍스트는 json.dumps 로만 직렬화 — 셸/JSON 인젝션 면역.
모든 쓰기는 tmp_path 격리(실 호스트 무접촉). --root 명시(env 폴백 없음).
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run_harness_style(root: Path, argv):
    """하니스(SubprocessEngine.run)와 동일하게 argv 를 조립해 엔진을 호출한다.

    full = [engine, argv[0], "--root", root] + argv[1:]  (check.py:~463 과 동일).
    이렇게 해야 `issue create --title` → `issue --root <root> create --title` 로
    --root 가 verb 와 서브액션 사이에 끼워지는 실제 경로를 검증한다(P0-1).
    """
    full = [sys.executable, str(ENGINE), argv[0], "--root", str(root)] + argv[1:]
    return subprocess.run(full, capture_output=True, text=True)


def _connect_issues(root: Path, provider: str = "linear"):
    cfg = {"spec_version": "0.1", "team": {"name": "t"},
           "services": {"issues": {"provider": provider, "scope": "personal"}}}
    (root / "team.config.json").write_text(json.dumps(cfg), encoding="utf-8")


# ── P0-1: verb=issue, --root 사이삽입에도 서브액션 정상 파싱 ──

def test_issue_root_inserted_between_verb_and_subaction(tmp_path):
    _connect_issues(tmp_path)
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", "테스트 이슈"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["verb"] == "issue"
    assert out["action"] == "create"          # --root 가 끼워져도 create 가 서브액션
    assert out["input"]["title"] == "테스트 이슈"


# ── 빈 슬롯: [info] + exit 0(비치명) ──

def test_issue_empty_slot_no_config_is_info_exit0(tmp_path):
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", "x"])
    assert r.returncode == 0, r.stderr
    assert "[info]" in r.stdout
    assert "x" not in r.stdout or "테스트" not in r.stdout  # echo 안 함(연결 안 됨)


def test_issue_empty_slot_unknown_provider_is_info(tmp_path):
    # providers/ 에 없는 provider → 연결로 인정 안 함(추측 금지) → [info]
    _connect_issues(tmp_path, provider="nonexistent-tracker")
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", "x"])
    assert r.returncode == 0
    assert "[info]" in r.stdout


def test_issue_no_issues_slot_in_services_is_info(tmp_path):
    cfg = {"spec_version": "0.1", "team": {"name": "t"}, "services": {}}
    (tmp_path / "team.config.json").write_text(json.dumps(cfg), encoding="utf-8")
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", "x"])
    assert r.returncode == 0
    assert "[info]" in r.stdout


# ── 연결 슬롯: 정규 입력 스키마 echo ──

def test_issue_connected_echoes_canonical_schema(tmp_path):
    _connect_issues(tmp_path)
    r = _run_harness_style(tmp_path,
                           ["issue", "create", "--title", "버그", "--body", "재현됨"])
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {
        "verb": "issue", "action": "create", "service": "issues",
        "provider": "linear",
        "input": {"title": "버그", "body": "재현됨"},
    }


def test_issue_omits_unset_fields(tmp_path):
    _connect_issues(tmp_path)
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", "only-title"])
    out = json.loads(r.stdout)
    assert out["input"] == {"title": "only-title"}  # body/assignee 등 미설정은 생략


# ── B-4: action_map 해석 안 함(echo 까지만) ──

def test_issue_does_not_interpret_action_map(tmp_path):
    # 엔진은 페이로드 변환을 하지 않는다 — echo 출력에 action_map 키가 없어야 한다.
    # (action_map 은 L2 에서 폐기됐고, 엔진은 애초에 그런 변환을 하지 않았다.)
    _connect_issues(tmp_path)
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", "t"])
    out = json.loads(r.stdout)
    assert "action_map" not in out
    assert set(out.keys()) == {"verb", "action", "service", "provider", "input"}
    # provider 는 이름만(팩 전체나 mcp/action_map 페이로드 변환 결과가 아님)
    assert out["provider"] == "linear"


# ── V.4: 인젝션 면역(셸/JSON 인젝션 안 됨) ──

def test_issue_injection_immune_json(tmp_path):
    _connect_issues(tmp_path)
    payload = '"; rm -rf / #{evil} \\n {"x":1}'
    r = _run_harness_style(tmp_path, ["issue", "create", "--title", payload])
    assert r.returncode == 0, r.stderr
    # 출력은 단일 유효 JSON 1줄 — 페이로드가 구조를 깨지 않는다(json.dumps 직렬화).
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert out["input"]["title"] == payload  # 원문 그대로 보존(해석/변환 없음)


# ── 서브액션 없이도 크래시 안 함(action=None) ──

def test_issue_no_subaction_does_not_crash(tmp_path):
    _connect_issues(tmp_path)
    r = _run_harness_style(tmp_path, ["issue"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["action"] is None
