"""#51 PR-A — 루트 INDEX 라우팅 맵 기반 동적 allowlist 테스트.

계약(이슈 #51 설계 확정):
  - 루트 INDEX 에 정확한 최상위 폴더행(`fundraise/`)이 있으면 write/delete 허용
  - 파일행(`fundraise/foo.md`)만 있으면 불허 (prefix 판정 금지)
  - 미등재 폴더(`legal/`)는 계속 거부
  - blocked(team/sessions·team/meeting)는 route 등재해도 거부 (blocked 우선)
  - INDEX.md 파일명 write 거부 (delete 와 대칭)

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run(root: Path, *argv):
    cmd = [sys.executable, str(ENGINE), *argv, "--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", str(root)], capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"],
                   capture_output=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"],
                   capture_output=True)


def _root_index_with(root: Path, *rows: str) -> None:
    """루트 memory/INDEX.md 를 2열 표 + 주어진 행들로 생성."""
    idx = root / "memory" / "INDEX.md"
    idx.parent.mkdir(parents=True, exist_ok=True)
    body = "| 경로 | 여기에 넣는 것 |\n|---|---|\n" + "".join(r + "\n" for r in rows)
    idx.write_text(body, encoding="utf-8")


# ── write: 동적 허용 ────────────────────────────────────────────────

def test_route_registered_top_folder_write_allowed(tmp_path):
    """루트 INDEX 에 `fundraise/` 폴더행 → write 허용 (현재 red)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `fundraise/` | 투자유치 리서치 |")
    r = _run(root, "memory", "write", "--folder", "fundraise",
             "--filename", "vc-notes.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 0, r.stderr
    assert (root / "memory" / "fundraise" / "vc-notes.md").is_file()


def test_unregistered_folder_still_rejected(tmp_path):
    """미등재 `legal/` 는 계속 거부."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `fundraise/` | 투자유치 |")
    r = _run(root, "memory", "write", "--folder", "legal",
             "--filename", "x.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 2
    assert "허용되지 않습니다" in r.stderr


def test_file_row_only_does_not_allow_folder(tmp_path):
    """파일행 `fundraise/foo.md` 만으로는 `fundraise/` 불허 (exact 폴더행만)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `fundraise/foo.md` | 단건 파일 |")
    r = _run(root, "memory", "write", "--folder", "fundraise",
             "--filename", "bar.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 2


def test_nested_folder_row_does_not_allow_top(tmp_path):
    """중첩 폴더행 `fundraise/vc/` 만으로는 최상위 `fundraise/` 불허."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `fundraise/vc/` | 중첩 폴더 |")
    r = _run(root, "memory", "write", "--folder", "fundraise",
             "--filename", "bar.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 2


def test_blocked_folder_wins_over_route(tmp_path):
    """`team/sessions` 하위는 route 등재와 무관하게 write 거부 (blocked 우선)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `team/sessions/` | 세션로그 |")
    r = _run(root, "memory", "write", "--folder", "team/sessions/alice",
             "--filename", "x.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 2
    assert "차단" in r.stderr or "저장 대상이 아닙니다" in r.stderr


def test_dynamic_top_subfolder_allowed(tmp_path):
    """등재된 최상위의 하위 폴더(`fundraise/vc/`)도 허용 (정적 목록과 동일 규칙)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `fundraise/` | 투자유치 |")
    r = _run(root, "memory", "write", "--folder", "fundraise/vc",
             "--filename", "notes.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 0, r.stderr


def test_no_root_index_falls_back_to_static(tmp_path):
    """루트 INDEX 부재 → 정적 목록만 (product 허용, fundraise 거부)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    ok = _run(root, "memory", "write", "--folder", "product",
              "--filename", "a.md", "--content", "x",
              "--author", "test", "--weight", "📎")
    assert ok.returncode == 0, ok.stderr
    no = _run(root, "memory", "write", "--folder", "fundraise",
              "--filename", "b.md", "--content", "x",
              "--author", "test", "--weight", "📎")
    assert no.returncode == 2


# ── delete: 동적 허용 대칭 ──────────────────────────────────────────

def test_route_registered_folder_delete_allowed(tmp_path):
    """등재 폴더의 파일 delete 도 허용 (write 대칭)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `fundraise/` | 투자유치 |")
    w = _run(root, "memory", "write", "--folder", "fundraise",
             "--filename", "vc-notes.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert w.returncode == 0, w.stderr
    d = _run(root, "memory", "delete", "--path", "fundraise/vc-notes.md",
             "--author", "test")
    assert d.returncode == 0, d.stderr
    assert not (root / "memory" / "fundraise" / "vc-notes.md").exists()


def test_unregistered_folder_delete_rejected(tmp_path):
    """미등재 폴더 delete 는 계속 거부 (엔진 외부에서 생긴 파일이라도)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    target = root / "memory" / "legal" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    r = _run(root, "memory", "delete", "--path", "legal/x.md", "--author", "test")
    assert r.returncode == 2


# ── INDEX.md write 거부 (delete 와 대칭) ────────────────────────────

def test_index_md_filename_write_rejected(tmp_path):
    """--filename INDEX.md 거부 — 엔진 관리 폴더 INDEX 덮어쓰기 차단 (delete 와 대칭)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    r = _run(root, "memory", "write", "--folder", "product",
             "--filename", "INDEX.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 2
    assert "INDEX.md" in r.stderr


def test_dynamic_top_nonascii_delete_rejected(tmp_path):
    """동적 등재가 비ASCII/공백 세그먼트여도 delete 는 세그먼트 검증으로 거부.

    (write 는 _validate_knowledge_path 의 세그먼트 검증이 이미 막음 — delete 대칭, codex P2)
    """
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `한글/` | 비ASCII 폴더 |", "| `bad top/` | 공백 폴더 |")
    for folder in ("한글", "bad top"):
        target = root / "memory" / folder / "x.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        r = _run(root, "memory", "delete", "--path", f"{folder}/x.md",
                 "--author", "test")
        assert r.returncode == 2, f"{folder!r} delete 가 통과함: {r.stdout}"
        assert (root / "memory" / folder / "x.md").exists()


def test_static_subfolder_segment_delete_rejected(tmp_path):
    """정적 루트 하위라도 비정상 세그먼트(product/한글)는 delete 거부 (선재 갭 동시 봉쇄)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    target = root / "memory" / "product" / "한글" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    r = _run(root, "memory", "delete", "--path", "product/한글/x.md",
             "--author", "test")
    assert r.returncode == 2


# ── soma 하드코딩 제거: 팀 전용 폴더는 정적 목록이 아니라 동적 허용으로 (#51) ──

def test_soma_not_statically_allowed(tmp_path):
    """soma 는 그린고래 팀 전용 — 제품 정적 목록에서 제거됨.

    루트 INDEX 미등재면 write 거부(범용 product/team 과 달리 스캐폴드에도 없음).
    """
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    r = _run(root, "memory", "write", "--folder", "soma",
             "--filename", "x.md", "--content", "x",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 2, "soma 가 아직 정적 허용됨 (하드코딩 잔존)"
    assert "허용되지 않습니다" in r.stderr


def test_soma_allowed_when_registered(tmp_path):
    """팀이 soma/ 를 루트 INDEX 에 등재하면 동적 허용으로 write 가능 (우리 인스턴스 경로)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    _root_index_with(root, "| `soma/` | 소마 과정 관련 정보 |")
    r = _run(root, "memory", "write", "--folder", "soma",
             "--filename", "schedule.md", "--content", "소마 일정",
             "--author", "test", "--weight", "📎")
    assert r.returncode == 0, r.stderr
    assert (root / "memory" / "soma" / "schedule.md").is_file()


def test_rejection_includes_route_upsert_hint(tmp_path):
    """미등재 팀 전용 폴더 거부 시 stderr 에 실행 가능한 route upsert 힌트(공유 포맷)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    r = _run(root, "memory", "write", "--folder", "soma",
             "--filename", "x.md", "--content", "x",
             "--author", "eunsu", "--weight", "📎")
    assert r.returncode == 2
    assert "[hint]" in r.stderr, f"거부에 힌트 없음:\n{r.stderr}"
    assert "memory route upsert" in r.stderr
    assert "--path soma/" in r.stderr
    assert "--desc" in r.stderr
    assert "--author eunsu" in r.stderr


def test_delete_rejection_includes_route_upsert_hint(tmp_path):
    """미등재 팀 전용 폴더 delete 거부에도 동일 포맷 힌트 (write 와 대칭, codex 재검수)."""
    root = tmp_path / "team"
    root.mkdir()
    _init_git(root)
    target = root / "memory" / "legal" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    r = _run(root, "memory", "delete", "--path", "legal/x.md", "--author", "eunsu")
    assert r.returncode == 2
    assert "[hint]" in r.stderr, f"delete 거부에 힌트 없음:\n{r.stderr}"
    assert "memory route upsert" in r.stderr
    assert "--path legal/" in r.stderr
    assert "--author eunsu" in r.stderr
