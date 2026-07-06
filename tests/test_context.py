"""V.2 `context` 동사 — 팀 메모리 긁어 구조화 출력 테스트 (스펙 01 §4).

엔진은 기계적 수집만: INDEX.md·멤버별 최근 작업일 세션로그 파일·그 summary 라인·
.teammode-active 상태를 긁어 구조화(텍스트/JSON) 출력. **요약은 안 함**(스킬 몫).

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
    _write_log(tmp_path, "bob", "2026-06-13", "오늘요약내용")
    r = _run(tmp_path, "context")
    assert "bob" in r.stdout
    assert "오늘요약내용" in r.stdout


def test_context_picks_most_recent_workday_file(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "bob", "2026-06-10", "오래된요약")
    _write_log(tmp_path, "bob", "2026-06-13", "최신요약")
    r = _run(tmp_path, "context")
    assert "최신요약" in r.stdout
    # 기본 단위 = 최근 1파일 (스펙 §4.1): 오래된 summary 는 안 나온다
    assert "오래된요약" not in r.stdout


def test_context_multiple_members(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "bob", "2026-06-13", "작업메모A")
    _write_log(tmp_path, "jonathon", "2026-06-13", "협업작업")
    r = _run(tmp_path, "context")
    assert "bob" in r.stdout and "jonathon" in r.stdout
    assert "작업메모A" in r.stdout and "협업작업" in r.stdout


# ── 상태(.teammode-active) 반영 ──

def test_context_reports_active_state(tmp_path):
    _write_index(tmp_path)
    (tmp_path / ".teammode-active").write_text("", encoding="utf-8")
    r = _run(tmp_path, "context")
    # 활성 상태가 출력에 드러난다 (on/active 류 토큰)
    assert "active" in r.stdout.lower() or "on" in r.stdout.lower()


# ── JSON 모드(스킬이 파싱) — 구조화 출력 ──

def test_context_json_mode_parses(tmp_path):
    _write_index(tmp_path)
    _write_log(tmp_path, "bob", "2026-06-13", "제이슨요약")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "members" in data
    names = {m["author"] for m in data["members"]}
    assert "bob" in names
    bob = next(m for m in data["members"] if m["author"] == "bob")
    assert bob["summary"] == "제이슨요약"
    assert bob["date"] == "2026-06-13"


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
    d = tmp_path / "memory" / "team" / "sessions" / "bob"
    d.mkdir(parents=True, exist_ok=True)
    # YYYY-MM-DD 가 아닌 보조 파일 (스펙 §2.1: 주입 대상 아님)
    (d / "notes.md").write_text("보조파일내용", encoding="utf-8")
    _write_log(tmp_path, "bob", "2026-06-13", "진짜요약")
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
    d = tmp_path / "memory" / "team" / "sessions" / "bob"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-06-13.md").write_text(
        "---\nroot:x:0:0:SECRETLEAK:/root:/bin/bash\nsummary: innocuous\n"
        "date: 2026-06-13\n---\nbody\n", encoding="utf-8")
    r = _run(tmp_path, "context")
    assert "SECRETLEAK" not in r.stdout
    rj = _run(tmp_path, "context", "--json")
    assert "SECRETLEAK" not in rj.stdout
    data = json.loads(rj.stdout)
    bob = next(m for m in data["members"] if m["author"] == "bob")
    assert bob["summary"] == "innocuous"


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


# ── personality_customized 결정적 판정 (2026-06-20 도그푸딩 결함 수정) ──

def _write_config(root: Path, name: str, greeting: str = None, farewell: str = None):
    """team.config.json 작성 (기본값: 기본 공식 그대로)."""
    cfg = {
        "spec_version": "0.2",
        "team": {
            "name": name,
            "greeting": greeting if greeting is not None else f"{name} 팀모드 ON",
            "farewell": farewell if farewell is not None else f"수고하셨습니다 — {name}",
        },
        "services": {},
    }
    (root / "team.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def test_context_personality_customized_default_is_false(tmp_path):
    """기본 greeting/farewell 공식 그대로 + banner.txt 없음 → personality_customized=false."""
    _write_config(tmp_path, "MyTeam")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "personality_customized" in data
    assert data["personality_customized"] is False


def test_context_personality_customized_custom_greeting_is_true(tmp_path):
    """greeting 이 기본 공식과 다르면 personality_customized=true."""
    _write_config(tmp_path, "MyTeam", greeting="안녕하세요 팀모드!")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["personality_customized"] is True


def test_context_personality_customized_custom_farewell_is_true(tmp_path):
    """farewell 이 기본 공식과 다르면 personality_customized=true."""
    _write_config(tmp_path, "MyTeam", farewell="잘 가요!")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["personality_customized"] is True


def test_context_personality_customized_banner_txt_is_true(tmp_path):
    """banner.txt 내용이 기본 배너와 다르면(커스텀) personality_customized=true."""
    _write_config(tmp_path, "MyTeam")
    banner_dir = tmp_path / "memory"
    banner_dir.mkdir(parents=True, exist_ok=True)
    (banner_dir / "banner.txt").write_text("=== CUSTOM BANNER ===\n", encoding="utf-8")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["personality_customized"] is True


def test_context_personality_customized_default_banner_is_false(tmp_path):
    """기본 배너 그대로(install 이 fresh 팀에 깐 것)면 미커스텀=false (#2 핵심).

    '존재'가 아니라 '내용'으로 판정 — fresh 팀도 banner.txt 가 깔리므로 존재만으로
    true 면 모든 새 팀이 커스텀으로 오판된다.
    """
    import shutil
    _write_config(tmp_path, "MyTeam")
    banners = tmp_path / "infra" / "banners"
    banners.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "banners" / "ansi_shadow.txt",
                banners / "ansi_shadow.txt")
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    # default_banner_content 와 동일하게 기록 (write_banner 가 fresh 에 깐 것 재현)
    art = (banners / "ansi_shadow.txt").read_text(encoding="utf-8").rstrip("\n")
    (mem / "banner.txt").write_text(
        art + "\n💡 팀색 입히기: tm-customize\n", encoding="utf-8")
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["personality_customized"] is False


def test_context_personality_customized_no_config_is_false(tmp_path):
    """team.config.json 없으면 personality_customized=false (비치명)."""
    r = _run(tmp_path, "context", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["personality_customized"] is False
