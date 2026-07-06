"""V.1 `log` 동사 — 세션로그 파일 생성/append 테스트 (스펙 01 §3).

엔진은 기계적 재료손질만: 날짜(06시컷)·frontmatter(author/date/summary)·하루1파일
append 를 자동화한다. 내용 요약은 안 한다(--text 그대로).

P1 정신: --root 명시 인자만(env 폴백 없음). 모든 시각은 --now 주입으로 결정적 검증.
모든 쓰기는 tmp_path 격리 — 실 호스트 무접촉.
"""
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run(root: Path, *argv, settings=None):
    """엔진 CLI 를 subprocess 로 직접 호출. settings 미지정 시 격리 경로 자동 주입."""
    if settings is None:
        settings = root / ".teammode-settings.json"
    cmd = [sys.executable, str(ENGINE), argv[0], "--root", str(root),
           "--settings", str(settings), *argv[1:]]
    return subprocess.run(cmd, capture_output=True, text=True)


def _sessions(root: Path, author: str) -> Path:
    return root / "memory" / "team" / "sessions" / author


def _log_files(root: Path, author: str):
    d = _sessions(root, author)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md")
                  if len(p.stem) >= 10 and p.stem[:4].isdigit() and p.stem[4] == "-")


# ── 기본 동작 ──

def test_log_creates_file_with_frontmatter(tmp_path):
    r = _run(tmp_path, "log", "--author", "bob", "--text", "첫 항목",
             "--now", "2026-06-13T14:00:00+09:00")
    assert r.returncode == 0, r.stderr
    files = _log_files(tmp_path, "bob")
    assert len(files) == 1
    assert files[0].name == "2026-06-13.md"
    content = files[0].read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "author: bob" in content
    assert "date: 2026-06-13" in content
    assert "summary:" in content
    assert "첫 항목" in content


def test_log_appends_same_day_single_file(tmp_path):
    _run(tmp_path, "log", "--author", "bob", "--text", "첫 항목",
         "--now", "2026-06-13T10:00:00+09:00")
    r = _run(tmp_path, "log", "--author", "bob", "--text", "둘째 항목",
             "--now", "2026-06-13T15:00:00+09:00")
    assert r.returncode == 0, r.stderr
    files = _log_files(tmp_path, "bob")
    assert len(files) == 1, f"하루 1파일 위반: {[f.name for f in files]}"
    content = files[0].read_text(encoding="utf-8")
    assert "첫 항목" in content and "둘째 항목" in content
    # frontmatter 는 한 번만
    assert content.count("\n---\n") == 1 or content.startswith("---\n")
    assert content.count("author: bob") == 1


def test_log_different_day_new_file(tmp_path):
    _run(tmp_path, "log", "--author", "bob", "--text", "어제",
         "--now", "2026-06-12T10:00:00+09:00")
    _run(tmp_path, "log", "--author", "bob", "--text", "오늘",
         "--now", "2026-06-13T10:00:00+09:00")
    files = _log_files(tmp_path, "bob")
    assert {f.name for f in files} == {"2026-06-12.md", "2026-06-13.md"}


# ── 06시 컷 경계 ──

def test_log_before_six_writes_previous_day_file(tmp_path):
    # 06-13 05:59 시작 → 06-12 파일
    r = _run(tmp_path, "log", "--author", "bob", "--text", "새벽작업",
             "--now", "2026-06-13T05:59:00+09:00")
    assert r.returncode == 0
    files = _log_files(tmp_path, "bob")
    assert files[0].name == "2026-06-12.md"
    assert "date: 2026-06-12" in files[0].read_text(encoding="utf-8")


def test_log_six_oclock_writes_same_day(tmp_path):
    r = _run(tmp_path, "log", "--author", "bob", "--text", "아침작업",
             "--now", "2026-06-13T06:00:00+09:00")
    files = _log_files(tmp_path, "bob")
    assert files[0].name == "2026-06-13.md"


def test_log_dawn_appends_to_previous_day(tmp_path):
    # 23:00 작성 후 다음날 02:00 작성 → 같은(전날) 파일에 append, 안 찢음
    _run(tmp_path, "log", "--author", "bob", "--text", "밤작업",
         "--now", "2026-06-12T23:00:00+09:00")
    _run(tmp_path, "log", "--author", "bob", "--text", "새벽이어서",
         "--now", "2026-06-13T02:00:00+09:00")
    files = _log_files(tmp_path, "bob")
    assert len(files) == 1
    assert files[0].name == "2026-06-12.md"


# ── 적대: 경로 traversal / 이상 author ──

def test_log_rejects_path_traversal_author(tmp_path):
    r = _run(tmp_path, "log", "--author", "../../etc", "--text", "x",
             "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0
    # 팀 루트 밖에 아무것도 안 쓴다
    assert not (tmp_path.parent / "etc").exists()


def test_log_rejects_slash_author(tmp_path):
    r = _run(tmp_path, "log", "--author", "a/b", "--text", "x",
             "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0


def test_log_rejects_absolute_author(tmp_path):
    victim = tmp_path / "victim"
    r = _run(tmp_path, "log", "--author", str(victim), "--text", "x",
             "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0
    assert not (victim / "memory").exists()


def test_log_rejects_empty_author(tmp_path):
    r = _run(tmp_path, "log", "--author", "", "--text", "x",
             "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0


def test_log_rejects_dotdot_segment(tmp_path):
    r = _run(tmp_path, "log", "--author", "..", "--text", "x",
             "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0


def test_log_rejects_leading_dash_author(tmp_path):
    # 적대 검수 지적: '-rf'·'--root' 류는 다운스트림 git/rm/glob 에서 플래그로 오인.
    for bad in ("-rf", "--root", "-"):
        r = _run(tmp_path, "log", "--author", bad, "--text", "x",
                 "--now", "2026-06-13T10:00:00+09:00")
        assert r.returncode != 0, f"{bad!r} 가 거부되지 않음"
    # 선두 dash 디렉토리가 생기지 않았다
    sess = tmp_path / "memory" / "team" / "sessions"
    if sess.is_dir():
        assert not any(p.name.startswith("-") for p in sess.iterdir())


def test_log_rejects_null_byte_author():
    # 널바이트는 subprocess argv 통과 자체가 OS 레벨에서 차단되지만(이중 방어), 엔진
    # 검증기도 직접 거부하는지 단언한다(다른 진입경로 대비).
    tm = runpy.run_path(str(ENGINE), run_name="__test_validate__")
    assert tm["_validate_author"]("a\x00b") is not None


def test_log_text_with_newlines_summary_is_first_line_only(tmp_path):
    # summary 는 한 줄(첫 줄)만 — 여러 줄 본문이 frontmatter 로 새지 않는다(스펙 §3.3).
    _run(tmp_path, "log", "--author", "bob",
         "--text", "첫줄요약\n둘째줄상세\n셋째줄", "--now", "2026-06-13T10:00:00+09:00")
    content = _log_files(tmp_path, "bob")[0].read_text(encoding="utf-8")
    fm = content.split("---\n")[1]   # frontmatter 블록
    assert "summary: 첫줄요약" in fm
    assert "둘째줄상세" not in fm     # 본문은 frontmatter 밖
    # 본문에는 전체 보존
    assert "둘째줄상세" in content and "셋째줄" in content


def test_log_summary_skips_markdown_header(tmp_path):
    # #3: text 첫 줄이 마크다운 헤더(`## 작업 내역`)면 summary 로 박지 않고
    # 첫 의미있는 본문 줄을 쓴다 — 헤더가 summary 로 새면 웰컴·맥락주입 품질 저하.
    _run(tmp_path, "log", "--author", "bob",
         "--text", "## 작업 내역\n- 실제 한 일", "--now", "2026-06-13T10:00:00+09:00")
    content = _log_files(tmp_path, "bob")[0].read_text(encoding="utf-8")
    fm = content.split("---\n")[1]   # frontmatter 블록
    assert "summary: - 실제 한 일" in fm
    assert "## 작업 내역" not in fm   # 헤더는 frontmatter(summary)에 안 들어감
    # 본문에는 헤더 포함 전체 보존
    assert "## 작업 내역" in content


# ── 필수 인자 검증 ──

def test_log_requires_root(tmp_path):
    # --root 없이 직접 호출 → 에러 종료(P1 정책 A), cwd 무접촉
    r = subprocess.run(
        [sys.executable, str(ENGINE), "log", "--author", "bob", "--text", "x"],
        capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0
    assert not (tmp_path / "memory").exists()


def test_log_requires_author(tmp_path):
    r = _run(tmp_path, "log", "--text", "x", "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0


def test_log_requires_text(tmp_path):
    r = _run(tmp_path, "log", "--author", "bob", "--now", "2026-06-13T10:00:00+09:00")
    assert r.returncode != 0


# ── append 손상 방지: 기존 내용 보존 ──

def test_log_append_preserves_existing_content(tmp_path):
    _run(tmp_path, "log", "--author", "bob", "--text", "MARKER_ONE",
         "--now", "2026-06-13T10:00:00+09:00")
    before = _log_files(tmp_path, "bob")[0].read_text(encoding="utf-8")
    _run(tmp_path, "log", "--author", "bob", "--text", "MARKER_TWO",
         "--now", "2026-06-13T11:00:00+09:00")
    after = _log_files(tmp_path, "bob")[0].read_text(encoding="utf-8")
    # 기존 본문이 그대로 남아있다 (덮어쓰기 아님)
    assert "MARKER_ONE" in after
    assert after.index("MARKER_ONE") < after.index("MARKER_TWO")
    assert len(after) > len(before)


# ── default now (실시각) 도 동작은 한다 ──

def test_log_without_now_uses_real_time(tmp_path):
    r = _run(tmp_path, "log", "--author", "bob", "--text", "지금")
    assert r.returncode == 0
    assert len(_log_files(tmp_path, "bob")) == 1


# ── 멀티 author 격리 ──

def test_log_separate_authors_separate_dirs(tmp_path):
    _run(tmp_path, "log", "--author", "bob", "--text", "A",
         "--now", "2026-06-13T10:00:00+09:00")
    _run(tmp_path, "log", "--author", "jonathon", "--text", "B",
         "--now", "2026-06-13T10:00:00+09:00")
    assert len(_log_files(tmp_path, "bob")) == 1
    assert len(_log_files(tmp_path, "jonathon")) == 1
    assert "A" in _log_files(tmp_path, "bob")[0].read_text(encoding="utf-8")
    assert "B" in _log_files(tmp_path, "jonathon")[0].read_text(encoding="utf-8")
