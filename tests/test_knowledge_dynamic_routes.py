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
