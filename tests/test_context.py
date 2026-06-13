"""V.2 `context` 동사 — 팀 메모리 긁어 구조화 출력 테스트 (스펙 01 §4).

엔진은 기계적 수집만: INDEX.md·멤버별 최근 작업일 세션로그 파일·그 summary 라인·
.tgates-active 상태를 긁어 구조화(텍스트/JSON) 출력. **요약은 안 함**(스킬 몫).

골든 02-context-injection: stdout 에 "INDEX" 와 "summary" 포함.
P1: --root 명시. /tmp 격리.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run(root: Path, *argv):
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(root / ".teammode-settings.json"), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_log(root: Path, author: str, date: str, summary: str, body: str = "본문"):
    d = root / "memory" / "team" / "sessions" / author
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date}.md").write_text(
        f"---\nauthor: {author}\ndate: {date}\nsummary: {summary}\n---\n\n{body}\n",
        encoding="utf-8")


def _write_index(root: Path, text: str = "팀 메모리 인덱스"):
    m = root / "memory"
    m.mkdir(parents=True, exist_ok=True)
    (m / "INDEX.md").write_text(f"# INDEX\n\n{text}\n", encoding="utf-8")


# ── 기본: 골든 02 핵심 (INDEX + summary 토큰) ──

def test_context_exit_zero_empty_memory(tmp_path):
    # memory 가 비어도 크래시 안 함 (빈 memory 경계)
    r = _run(tmp_path, "context")
    assert r.returncode == 0, r.stderr


def test_context_stdout_has_index_token(tmp_path):
    _write_index(tmp_path)
    r = _run(tmp_path, "context")
    assert r.returncode == 0
    assert "INDEX" in r.stdout


def test_context_stdout_has_summary_token_even_empty(tmp_path):
    # 골든 02 step2: summary 토큰은 구조적 라벨로 항상 존재 (멤버 없어도)
    r = _run(tmp_path, "context")
    assert "summary" in r.stdout


def test_context_includes_index_content(tmp_path):
    _write_index(tmp_path, "독특한인덱스내용XYZ")
    r = _run(tmp_path, "context")
    assert "독특한인덱스내용XYZ" in r.stdout


# ── 멤버별 최근 작업일 1파일 + summary 수집 ──

def test_context_collects_member_summary(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "eunsu", "2026-06-13", "오늘요약내용")
    r = _run(tmp_path, "context")
    assert "eunsu" in r.stdout
    assert "오늘요약내용" in r.stdout


def test_context_picks_most_recent_workday_file(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "eunsu", "2026-06-10", "오래된요약")
    _write_log(tmp_path, "eunsu", "2026-06-13", "최신요약")
    r = _run(tmp_path, "context")
    assert "최신요약" in r.stdout
    # 기본 단위 = 최근 1파일 (스펙 §4.1): 오래된 summary 는 안 나온다
    assert "오래된요약" not in r.stdout


def test_context_multiple_members(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "eunsu", "2026-06-13", "은수작업")
    _write_log(tmp_path, "junhyung", "2026-06-13", "준형작업")
    r = _run(tmp_path, "context")
    assert "eunsu" in r.stdout and "junhyung" in r.stdout
    assert "은수작업" in r.stdout and "준형작업" in r.stdout


# ── 상태(.tgates-active) 반영 ──

def test_context_reports_active_state(tmp_path):
    _write_index(tmp_path)
    (tmp_path / ".tgates-active").write_text("", encoding="utf-8")
    r = _run(tmp_path, "context")
    # 활성 상태가 출력에 드러난다 (on/active 류 토큰)
    assert "active" in r.stdout.lower() or "on" in r.stdout.lower()


# ── JSON 모드(스킬이 파싱) — 구조화 출력 ──

def test_context_json_mode_parses(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "eunsu", "2026-06-13", "제이슨요약")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "members" in data
    names = {m["author"] for m in data["members"]}
    assert "eunsu" in names
    eunsu = next(m for m in data["members"] if m["author"] == "eunsu")
    assert eunsu["summary"] == "제이슨요약"
    assert eunsu["date"] == "2026-06-13"


# ── 적대: summary 없는 구 로그 (마이그레이션 단서) ──

def test_context_old_log_without_summary_no_crash(tmp_path):
    _write_index(tmp_path)
    d = tmp_path / "memory" / "team" / "sessions" / "old"
    d.mkdir(parents=True, exist_ok=True)
    # frontmatter 에 summary 없음 (v0.1 이전 로그)
    (d / "2026-06-13.md").write_text(
        "---\nauthor: old\ndate: 2026-06-13\n---\n\n옛날본문\n", encoding="utf-8")
    r = _run(tmp_path, "context")
    assert r.returncode == 0
    # summary 주입 생략 — 전문 폴백 금지(스펙 §4.1). 멤버는 보이되 summary 는 빈/생략
    assert "old" in r.stdout


def test_context_json_old_log_summary_empty(tmp_path):
    _write_index(tmp_path)
    d = tmp_path / "memory" / "team" / "sessions" / "old"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-06-13.md").write_text(
        "---\nauthor: old\ndate: 2026-06-13\n---\n\n본문\n", encoding="utf-8")
    r = _run(tmp_path, "context", "--json")
    data = json.loads(r.stdout)
    old = next(m for m in data["members"] if m["author"] == "old")
    assert old["summary"] in ("", None)


# ── 적대: 보조 파일·비로그 .md 무시 ──

def test_context_ignores_non_log_md_files(tmp_path):
    _write_index(tmp_path)
    d = tmp_path / "memory" / "team" / "sessions" / "eunsu"
    d.mkdir(parents=True, exist_ok=True)
    # YYYY-MM-DD 가 아닌 보조 파일 (스펙 §2.1: 주입 대상 아님)
    (d / "notes.md").write_text("보조파일내용", encoding="utf-8")
    _write_log(tmp_path, "eunsu", "2026-06-13", "진짜요약")
    r = _run(tmp_path, "context")
    assert "진짜요약" in r.stdout
    assert "보조파일내용" not in r.stdout


# ── 필수 인자 ──

def test_context_requires_root(tmp_path):
    r = subprocess.run([sys.executable, str(ENGINE), "context"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0


# ── 적대: frontmatter 의 임의 키는 출력에 새지 않는다 (검수 지적 락) ──

def test_context_does_not_leak_arbitrary_frontmatter_keys(tmp_path):
    # 세션로그가 (심링크 등으로) passwd 류 콜론 라인을 담아도, 엔진은 알려진 3필드
    # (author/date/summary)만 방출한다 — 임의 키 내용 누수 0.
    _write_index(tmp_path)
    d = tmp_path / "memory" / "team" / "sessions" / "eunsu"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-06-13.md").write_text(
        "---\nroot:x:0:0:SECRETLEAK:/root:/bin/bash\nsummary: innocuous\n"
        "date: 2026-06-13\n---\nbody\n", encoding="utf-8")
    r = _run(tmp_path, "context")
    assert "SECRETLEAK" not in r.stdout
    rj = _run(tmp_path, "context", "--json")
    assert "SECRETLEAK" not in rj.stdout
    data = json.loads(rj.stdout)
    eunsu = next(m for m in data["members"] if m["author"] == "eunsu")
    assert eunsu["summary"] == "innocuous"


def test_context_file_in_sessions_not_treated_as_member(tmp_path):
    # sessions/ 바로 아래 파일(디렉토리 아님)은 멤버로 오인되지 않는다.
    _write_index(tmp_path)
    sess = tmp_path / "memory" / "team" / "sessions"
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "stray.md").write_text("x", encoding="utf-8")
    r = _run(tmp_path, "context")
    assert r.returncode == 0


def test_context_summary_with_colon_preserved(tmp_path):
    # summary 값에 콜론이 있어도 첫 콜론만 분리 — 값 전체 보존.
    _write_index(tmp_path)
    d = tmp_path / "memory" / "team" / "sessions" / "bob"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-06-13.md").write_text(
        "---\nauthor: bob\ndate: 2026-06-13\nsummary: ratio 3:1 and more\n---\n",
        encoding="utf-8")
    r = _run(tmp_path, "context", "--json")
    bob = next(m for m in json.loads(r.stdout)["members"] if m["author"] == "bob")
    assert bob["summary"] == "ratio 3:1 and more"


# ── 멤버 디렉토리에 로그 파일이 0개 ──

def test_context_member_dir_with_no_log(tmp_path):
    _write_index(tmp_path)
    (tmp_path / "memory" / "team" / "sessions" / "ghost").mkdir(parents=True)
    r = _run(tmp_path, "context")
    assert r.returncode == 0  # 빈 멤버 디렉토리도 크래시 안 함
