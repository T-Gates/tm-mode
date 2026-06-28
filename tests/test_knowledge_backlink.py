"""L2 — 양방향 백링크 테스트 (memory 문서 ↔ 세션로그) + chat 통지 재료.

검증 목록:
  - write(신규): 세션로그에 `[[<rel>]]` 줄 append + 문서 frontmatter 에 session 필드
  - write(수정): 세션로그·문서 양방향 멱등 (같은 줄 중복 X, session 중복 X)
  - delete: 세션로그에 삭제 백링크 줄 append
  - 세션로그 없을 때 자동 생성 (frontmatter + 메모리 변경 섹션)
  - 비차단: 백링크가 본작업(파일/INDEX)을 롤백시키지 않음 — 본작업 성공 유지
  - chat 통지 재료: stdout 에 `[chat-notify]` 한 줄 (add/update/delete)

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
세션로그(team/sessions/<author>/<date>.md) 는 blocked 폴더지만 백링크는 엔진 내부
특별 경로로 직접 쓴다(memory.write API 우회).
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run(root: Path, *argv):
    cmd = [sys.executable, str(ENGINE), *argv, "--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _session_path(root: Path, author: str, date: str) -> Path:
    return root / "memory" / "team" / "sessions" / author / f"{date}.md"


def _doc_path(root: Path, folder: str, filename: str) -> Path:
    return root / "memory" / folder / filename


DATE = "2026-06-25"


# ── 세션로그 → 문서 백링크 ──────────────────────────────────────────

def test_write_appends_session_backlink(tmp_path):
    """write(신규): 세션로그에 [[<rel>]] 백링크 줄이 추가된다."""
    r = _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", "그라운드룰.", "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    assert r.returncode == 0, r.stderr
    sp = _session_path(tmp_path, "eunsu", DATE)
    assert sp.is_file(), "세션로그가 생성되지 않았다"
    text = sp.read_text(encoding="utf-8")
    # 위키링크는 vault 루트(memory/) 기준 상대경로여야 클릭이 작동한다 (#21).
    # memory/ 접두사가 박히면 Obsidian 이 memory/memory/... 로 해석해 링크가 깨진다.
    assert "[[team/rule.md]]" in text
    assert "[[memory/team/rule.md]]" not in text
    assert "📝 생성" in text


def test_write_session_log_created_with_frontmatter(tmp_path):
    """세션로그가 없으면 frontmatter 포함해 생성된다."""
    _run(tmp_path, "memory", "write",
         "--folder", "soma", "--filename", "x.md",
         "--content", "내용.", "--author", "jun", "--weight", "📎",
         "--date", DATE)
    sp = _session_path(tmp_path, "jun", DATE)
    text = sp.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "author: jun" in text
    assert f"date: {DATE}" in text


# ── 문서 → 세션로그 백링크 ──────────────────────────────────────────

def test_write_adds_session_field_to_doc(tmp_path):
    """write: 문서 frontmatter 에 session 필드가 추가된다."""
    _run(tmp_path, "memory", "write",
         "--folder", "team", "--filename", "rule.md",
         "--content", "그라운드룰.", "--author", "eunsu",
         "--weight", "📌", "--date", DATE)
    doc = _doc_path(tmp_path, "team", "rule.md").read_text(encoding="utf-8")
    assert f"session: team/sessions/eunsu/{DATE}.md" in doc
    # 4 known 필드도 보존
    assert "author: eunsu" in doc
    assert "weight: 📌" in doc


# ── 멱등 ────────────────────────────────────────────────────────────

def test_session_backlink_idempotent(tmp_path):
    """같은 문서 재수정 시 세션로그 백링크 줄이 중복되지 않는다."""
    for content in ("v1.", "v2.", "v3."):
        _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", content, "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    sp = _session_path(tmp_path, "eunsu", DATE)
    text = sp.read_text(encoding="utf-8")
    # 같은 (수정, 경로) 줄은 1개만 (신규 1줄, 이후 수정은 동일 줄이라 중복 방지)
    n_links = text.count("[[team/rule.md]]")
    assert n_links <= 2, f"백링크 줄이 과도하게 누적됨: {n_links}"


def test_doc_session_field_idempotent(tmp_path):
    """재수정 시 문서 frontmatter 에 session 필드가 중복 추가되지 않는다."""
    for content in ("v1.", "v2."):
        _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", content, "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    doc = _doc_path(tmp_path, "team", "rule.md").read_text(encoding="utf-8")
    assert doc.count("session:") == 1, "session 필드가 중복됨"


# ── delete 백링크 ────────────────────────────────────────────────────

def test_delete_appends_session_backlink(tmp_path):
    """delete: 세션로그에 삭제 백링크 줄이 추가된다."""
    _run(tmp_path, "memory", "write",
         "--folder", "team", "--filename", "gone.md",
         "--content", "삭제 대상.", "--author", "eunsu",
         "--weight", "📎", "--date", DATE)
    r = _run(tmp_path, "memory", "delete",
             "--path", "memory/team/gone.md", "--author", "eunsu")
    assert r.returncode == 0, r.stderr
    # delete 는 now_kst 기준 workday 라 DATE 와 다를 수 있다 — author 디렉토리에서 탐색
    sdir = tmp_path / "memory" / "team" / "sessions" / "eunsu"
    texts = "".join(p.read_text(encoding="utf-8") for p in sdir.glob("*.md"))
    assert "🗑️ 삭제" in texts
    # delete 백링크도 vault 루트 기준이어야 한다 (#21).
    assert "[[team/gone.md]]" in texts
    assert "[[memory/team/gone.md]]" not in texts


# ── 비차단: 본작업 유지 ─────────────────────────────────────────────

def test_backlink_nonblocking_core_write_succeeds(tmp_path):
    """백링크는 advisory — 본작업(파일+INDEX)은 정상 완료된다."""
    r = _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", "내용.", "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    assert r.returncode == 0
    assert _doc_path(tmp_path, "team", "rule.md").is_file()
    assert (tmp_path / "memory" / "team" / "INDEX.md").is_file()


# ── chat 통지 재료 (stdout) ─────────────────────────────────────────

def test_chat_notify_summary_on_write(tmp_path):
    """write: stdout 에 chat 통지 재료 한 줄(추가/경로/weight/author)이 나온다."""
    r = _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", "첫줄 요약.", "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    assert "[chat-notify]" in r.stdout
    assert "추가" in r.stdout
    assert "memory/team/rule.md" in r.stdout
    assert "📌" in r.stdout
    assert "eunsu" in r.stdout


def test_chat_notify_summary_on_update(tmp_path):
    """수정 시 통지 재료가 '수정' 동작으로 나온다."""
    _run(tmp_path, "memory", "write",
         "--folder", "team", "--filename", "rule.md",
         "--content", "v1.", "--author", "eunsu",
         "--weight", "📌", "--date", DATE)
    r = _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", "v2.", "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    assert "[chat-notify]" in r.stdout
    assert "수정" in r.stdout


def test_chat_notify_summary_on_delete(tmp_path):
    """delete 시 통지 재료가 '삭제' 동작으로 나온다."""
    _run(tmp_path, "memory", "write",
         "--folder", "team", "--filename", "gone.md",
         "--content", "x.", "--author", "eunsu",
         "--weight", "📎", "--date", DATE)
    r = _run(tmp_path, "memory", "delete",
             "--path", "memory/team/gone.md", "--author", "eunsu")
    assert "[chat-notify]" in r.stdout
    assert "삭제" in r.stdout
    assert "memory/team/gone.md" in r.stdout


# ── 백링크가 본 커밋에 포함된다 (검수 BLOCK 회귀) ─────────────────────
# 실 git temp repo(원격 없음)에서 memory write/delete 시 세션로그·문서 백링크가
# HEAD 커밋에 같이 들어가고 미커밋 잔류가 0인지 검증한다.

def _init_git(root: Path) -> None:
    """tmp 경로에 최소 git repo 초기화 (원격 없음 — push 비차단 검증용)."""
    subprocess.run(["git", "init", str(root)], capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@test.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"],
                   capture_output=True)
    readme = root / "README.md"
    readme.write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], capture_output=True)


def _git_out(root: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True).stdout


def test_write_backlinks_included_in_head_commit(tmp_path):
    """write: 세션로그·문서 백링크가 같은 HEAD 커밋에 포함되고 미커밋 잔류 0.

    종전 BLOCK: 백링크가 do_commit 뒤라 영영 미커밋됐다 → 이 단언으로 회귀 방지.
    """
    _init_git(tmp_path)
    r = _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", "그라운드룰.", "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    assert r.returncode == 0, r.stderr

    # HEAD 커밋에 문서·INDEX·세션로그가 모두 포함돼야 한다
    show = _git_out(tmp_path, "show", "--stat", "--name-only", "HEAD")
    assert "memory/team/rule.md" in show, f"문서가 커밋에 없음:\n{show}"
    assert "memory/team/INDEX.md" in show, f"INDEX 가 커밋에 없음:\n{show}"
    assert f"memory/team/sessions/eunsu/{DATE}.md" in show, \
        f"세션로그가 커밋에 없음:\n{show}"

    # 문서 frontmatter 의 session 필드도 커밋된 내용에 들어가 있어야 한다
    doc = _doc_path(tmp_path, "team", "rule.md").read_text(encoding="utf-8")
    assert f"session: team/sessions/eunsu/{DATE}.md" in doc

    # 미커밋 잔류 0 (status clean)
    status = _git_out(tmp_path, "status", "--short")
    assert status.strip() == "", f"미커밋 잔류가 있다:\n{status!r}"


def test_delete_backlink_included_in_head_commit(tmp_path):
    """delete: 세션로그 삭제 백링크가 같은 HEAD 커밋에 포함되고 미커밋 잔류 0."""
    _init_git(tmp_path)
    _run(tmp_path, "memory", "write",
         "--folder", "team", "--filename", "gone.md",
         "--content", "삭제 대상.", "--author", "eunsu",
         "--weight", "📎", "--date", DATE)
    r = _run(tmp_path, "memory", "delete",
             "--path", "memory/team/gone.md", "--author", "eunsu")
    assert r.returncode == 0, r.stderr

    # 삭제 커밋(HEAD)에 세션로그가 포함돼야 한다(삭제 백링크 줄 추가분)
    show = _git_out(tmp_path, "show", "--stat", "--name-only", "HEAD")
    sdir = tmp_path / "memory" / "team" / "sessions" / "eunsu"
    sess_names = [p.name for p in sdir.glob("*.md")]
    assert any(f"sessions/eunsu/{n}" in show for n in sess_names), \
        f"세션로그가 삭제 커밋에 없음:\n{show}"

    # 미커밋 잔류 0
    status = _git_out(tmp_path, "status", "--short")
    assert status.strip() == "", f"미커밋 잔류가 있다:\n{status!r}"


def test_write_push_failure_nonblocking(tmp_path):
    """push=True 경로 비차단: 원격이 없어 push 가 실패해도 RC=0·커밋 보존·경고만.

    원격 없는 repo 에서 do_commit(push=True) 는 push 가 실패(no upstream)하지만
    do_commit 이 커밋을 보존하므로 cmd_knowledge 는 RC=0 으로 끝나야 한다.
    """
    _init_git(tmp_path)
    r = _run(tmp_path, "memory", "write",
             "--folder", "team", "--filename", "rule.md",
             "--content", "내용.", "--author", "eunsu",
             "--weight", "📌", "--date", DATE)
    # push 실패에도 RC=0 (비차단)
    assert r.returncode == 0, r.stderr
    # 커밋은 보존됨 — 미커밋 잔류 0
    status = _git_out(tmp_path, "status", "--short")
    assert status.strip() == "", f"미커밋 잔류가 있다:\n{status!r}"
    # push 실패 경고는 stderr 로만 (RC 영향 없음)
    assert "push 실패" in r.stderr or "push" in r.stderr.lower()
