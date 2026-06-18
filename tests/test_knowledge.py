"""작업 C — knowledge 동사 테스트 (C-1·C-3).

검증 목록:
  - knowledge write: 파일 생성·frontmatter 스탬프·INDEX 행 등재
  - knowledge write: 기존 파일 수정(updated_at 갱신, created_at 보존)
  - knowledge write: 멱등(같은 내용 재호출 → 변경 없음)
  - knowledge write: frontmatter 없는 기존 파일 수정 시 frontmatter 자동 추가
  - INDEX upsert: 표 없으면 새로 생성, 있으면 행 추가/갱신
  - 편집일: 메타 커밋 제외 계산 (git repo 필요)
  - traversal 차단: folder에 .., filename에 ../, 절대경로 등
  - weight 필수: --weight 없으면 거부(exit 2)
  - knowledge delete: 파일 삭제·INDEX 행 제거
  - knowledge delete: 멱등(없는 파일 삭제 → exit 0)
  - conformance: knowledge write → INDEX 존재 → tm-knowledge 가 INDEX 로 봄

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
git 작업이 필요한 테스트는 tmp_path 안에 독립 git repo 생성.
"""
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"

sys.path.insert(0, str(REPO / "infra"))


# ── 공통 헬퍼 ──────────────────────────────────────────────────────

def _run(root: Path, *argv):
    """teammode.py 를 subprocess 로 직접 호출.

    knowledge 동사는 settings 불필요(메모리 동사).
    """
    cmd = [sys.executable, str(ENGINE), *argv, "--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_main(args: list) -> int:
    """teammode.py main() 직접 호출 — exit code 반환."""
    mod = runpy.run_path(str(ENGINE), run_name="__knowledge_test__")
    return mod["main"](args)


def _init_git(root: Path) -> None:
    """tmp 경로에 최소 git repo 초기화."""
    subprocess.run(["git", "init", str(root)], capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@test.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"],
                   capture_output=True)
    # 초기 커밋(빈 레포에서 commit 이 실패하지 않도록)
    readme = root / "README.md"
    readme.write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], capture_output=True)


def _knowledge_path(root: Path, folder: str, filename: str) -> Path:
    return root / "memory" / folder / filename


def _index_path(root: Path, folder: str) -> Path:
    return root / "memory" / folder / "INDEX.md"


# ── C-1: 파일 생성 ──────────────────────────────────────────────────

def test_knowledge_write_creates_file(tmp_path):
    """knowledge write: 파일이 생성된다."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "code-conventions.md",
             "--content", "커밋 메시지는 Conventional Commits.",
             "--author", "jane-doe",
             "--weight", "📌")
    assert r.returncode == 0, r.stderr
    p = _knowledge_path(tmp_path, "team", "code-conventions.md")
    assert p.is_file(), "파일이 생성되지 않았다"
    content = p.read_text(encoding="utf-8")
    assert "커밋 메시지는 Conventional Commits." in content


def test_knowledge_write_frontmatter_stamped(tmp_path):
    """knowledge write: frontmatter 4필드가 자동으로 추가된다."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "groundrule.md",
             "--content", "미팅은 월·수·금.",
             "--author", "jane-doe",
             "--weight", "🔥")
    assert r.returncode == 0, r.stderr
    p = _knowledge_path(tmp_path, "team", "groundrule.md")
    text = p.read_text(encoding="utf-8")
    assert "created_at:" in text
    assert "updated_at:" in text
    assert "author: jane-doe" in text
    assert "weight: 🔥" in text
    assert text.startswith("---\n")


def test_knowledge_write_index_row_added(tmp_path):
    """knowledge write: INDEX.md 에 행이 추가된다."""
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "groundrule.md",
         "--content", "미팅은 월·수·금.",
         "--author", "jane-doe",
         "--weight", "📌")
    idx = _index_path(tmp_path, "team")
    assert idx.is_file(), "INDEX.md 가 생성되지 않았다"
    content = idx.read_text(encoding="utf-8")
    assert "groundrule.md" in content
    assert "📌" in content


def test_knowledge_write_index_table_format(tmp_path):
    """knowledge write: INDEX.md 가 파이프 표 형식을 갖는다."""
    _run(tmp_path, "knowledge", "write",
         "--folder", "extras",
         "--filename", "schedule.md",
         "--content", "팀 일정 정리.",
         "--author", "jane-doe",
         "--weight", "📎")
    idx = _index_path(tmp_path, "extras")
    text = idx.read_text(encoding="utf-8")
    assert "| 가중치 | 경로 | 내용 | 편집일 |" in text
    assert "|--------|------|------|--------|" in text
    assert "📎" in text


# ── C-1: 수정 ────────────────────────────────────────────────────────

def test_knowledge_write_update_preserves_created_at(tmp_path):
    """knowledge write: 기존 파일 수정 시 created_at 보존, updated_at 갱신."""
    # 최초 생성
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "conventions.md",
         "--content", "초기 내용.",
         "--author", "jane-doe",
         "--weight", "📌",
         "--date", "2026-01-01")
    p = _knowledge_path(tmp_path, "team", "conventions.md")
    text1 = p.read_text(encoding="utf-8")
    assert "created_at: 2026-01-01" in text1

    # 수정
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "conventions.md",
         "--content", "수정된 내용.",
         "--author", "jane-doe",
         "--weight", "📌",
         "--date", "2026-06-18")
    text2 = p.read_text(encoding="utf-8")
    assert "created_at: 2026-01-01" in text2, "created_at 이 바뀌었다"
    assert "updated_at: 2026-06-18" in text2, "updated_at 이 갱신되지 않았다"
    assert "수정된 내용." in text2


def test_knowledge_write_existing_no_frontmatter_gets_frontmatter(tmp_path):
    """knowledge write: frontmatter 없는 기존 파일 수정 시 frontmatter 자동 추가."""
    p = _knowledge_path(tmp_path, "product", "spec.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("기존 콘텐츠 (frontmatter 없음)\n", encoding="utf-8")

    _run(tmp_path, "knowledge", "write",
         "--folder", "product",
         "--filename", "spec.md",
         "--content", "새 콘텐츠.",
         "--author", "jane-doe",
         "--weight", "🔥")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "frontmatter 가 추가되지 않았다"
    assert "author: jane-doe" in text
    assert "weight: 🔥" in text


# ── C-1: 멱등 ────────────────────────────────────────────────────────

def test_knowledge_write_idempotent(tmp_path):
    """knowledge write: 같은 내용 재호출 → 변경 없음 출력, 파일 동일."""
    args = [
        "knowledge", "write",
        "--folder", "team",
        "--filename", "idempotent.md",
        "--content", "반복 내용.",
        "--author", "jane-doe",
        "--weight", "📎",
        "--date", "2026-06-18",
    ]
    _run(tmp_path, *args)
    p = _knowledge_path(tmp_path, "team", "idempotent.md")
    mtime1 = p.stat().st_mtime

    r2 = _run(tmp_path, *args)
    assert r2.returncode == 0, r2.stderr
    # 멱등: "변경 없음" 출력
    assert "변경 없음" in r2.stdout or "멱등" in r2.stdout, \
        f"멱등 표시 없음: {r2.stdout!r}"


def test_knowledge_delete_idempotent(tmp_path):
    """knowledge delete: 이미 없는 파일 삭제 → exit 0."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/nonexistent.md",
             "--author", "jane-doe")
    assert r.returncode == 0, r.stderr


# ── C-3: INDEX 다중 행 ────────────────────────────────────────────────

def test_knowledge_write_index_multiple_entries(tmp_path):
    """여러 파일 write → INDEX 에 각 행이 등재된다."""
    for name in ("a.md", "b.md", "c.md"):
        _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", name,
             "--content", f"내용 {name}.",
             "--author", "jane-doe",
             "--weight", "📎")
    idx = _index_path(tmp_path, "team")
    text = idx.read_text(encoding="utf-8")
    assert "a.md" in text
    assert "b.md" in text
    assert "c.md" in text


def test_knowledge_write_index_row_updated_on_rewrite(tmp_path):
    """기존 INDEX 행이 있으면 갱신(중복 삽입 안 됨)."""
    for i in range(2):
        _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "single.md",
             "--content", f"내용 버전 {i}.",
             "--author", "jane-doe",
             "--weight", "📌",
             "--date", f"2026-06-{i + 1:02d}")
    idx = _index_path(tmp_path, "team")
    text = idx.read_text(encoding="utf-8")
    # 같은 경로가 두 번 나오면 안 됨
    assert text.count("single.md") == 1, "INDEX 에 중복 행이 있다"


# ── C-3: delete ────────────────────────────────────────────────────

def test_knowledge_delete_removes_file_and_index_row(tmp_path):
    """knowledge delete: 파일 삭제 + INDEX 행 제거."""
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "to-delete.md",
         "--content", "삭제할 파일.",
         "--author", "jane-doe",
         "--weight", "📎")
    p = _knowledge_path(tmp_path, "team", "to-delete.md")
    assert p.is_file()

    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/to-delete.md",
             "--author", "jane-doe")
    assert r.returncode == 0, r.stderr
    assert not p.is_file(), "파일이 삭제되지 않았다"

    idx = _index_path(tmp_path, "team")
    if idx.is_file():
        text = idx.read_text(encoding="utf-8")
        assert "to-delete.md" not in text, "INDEX 행이 제거되지 않았다"


# ── C-3: traversal 차단 ───────────────────────────────────────────────

def test_knowledge_write_rejects_dotdot_folder(tmp_path):
    """knowledge write: --folder 에 '..' 세그먼트 → exit 2."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "../evil",
             "--filename", "x.md",
             "--content", "evil.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, "traversal folder 가 거부되지 않았다"
    # 실제로 파일이 생성되지 않았는지
    evil = tmp_path.parent / "evil"
    assert not evil.exists(), f"traversal 탈출: {evil}"


def test_knowledge_write_rejects_absolute_folder(tmp_path):
    """knowledge write: --folder 가 절대경로이면 거부."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "/tmp/evil",
             "--filename", "x.md",
             "--content", "evil.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2


def test_knowledge_write_rejects_slash_filename(tmp_path):
    """knowledge write: --filename 에 슬래시 포함 → 거부."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "../../etc/passwd",
             "--content", "evil.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2


def test_knowledge_delete_rejects_dotdot_path(tmp_path):
    """knowledge delete: --path 에 '..' 포함 → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "../etc/passwd",
             "--author", "jane-doe")
    assert r.returncode == 2, "traversal path 가 거부되지 않았다"


# ── C-3: 필수 인자 검증 ───────────────────────────────────────────────

def test_knowledge_write_requires_weight(tmp_path):
    """knowledge write: --weight 없으면 exit 2 (추측 금지)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "noweight.md",
             "--content", "내용.",
             "--author", "jane-doe")
    assert r.returncode == 2, "--weight 없이 성공해선 안 된다"


def test_knowledge_write_requires_folder(tmp_path):
    """knowledge write: --folder 없으면 exit 2."""
    r = _run(tmp_path, "knowledge", "write",
             "--filename", "x.md",
             "--content", "내용.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2


def test_knowledge_write_requires_author(tmp_path):
    """knowledge write: --author 없으면 exit 2."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "x.md",
             "--content", "내용.",
             "--weight", "📎")
    assert r.returncode == 2


def test_knowledge_write_requires_content(tmp_path):
    """knowledge write: --content 없으면 exit 2."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "x.md",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2


def test_knowledge_delete_requires_author(tmp_path):
    """knowledge delete: --author 없으면 exit 2."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/x.md")
    assert r.returncode == 2


def test_knowledge_delete_requires_path(tmp_path):
    """knowledge delete: --path 없으면 exit 2."""
    r = _run(tmp_path, "knowledge", "delete",
             "--author", "jane-doe")
    assert r.returncode == 2


def test_knowledge_unknown_action(tmp_path):
    """knowledge: 알 수 없는 action → exit 2."""
    r = _run(tmp_path, "knowledge", "badaction",
             "--folder", "team",
             "--filename", "x.md",
             "--content", "내용.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2


# ── C-3: 편집일 계산 (git repo 필요) ──────────────────────────────────

def test_knowledge_edit_date_excludes_meta_commit(tmp_path):
    """편집일 계산(P1-3): 본문 미변경 재write 시 INDEX 기존 편집일이 보존된다.

    시뮬레이션:
      1. 지식 파일 최초 생성 (편집일 = 과거 날짜로 고정)
      2. weight 만 변경하는 재write (본문 동일)
      3. INDEX 편집일이 과거 날짜로 보존되는지 검증

    Note: _git_last_content_commit_date 함수는 subject-substring 의존으로 제거됨(P1-3).
    대신 INDEX 행의 편집일을 직접 보존하는 방식으로 대체됨.
    """
    root = tmp_path / "gitroot"
    root.mkdir()
    _init_git(root)

    # 1) 지식 파일 최초 write — 과거 날짜로 편집일 고정
    _run(root, "knowledge", "write",
         "--folder", "team",
         "--filename", "edit-date-test.md",
         "--content", "콘텐츠 v1.",
         "--author", "jane-doe",
         "--weight", "📎",
         "--date", "2023-05-10")

    p = _knowledge_path(root, "team", "edit-date-test.md")
    assert p.is_file()

    # INDEX 에 2023-05-10 편집일이 있어야 함
    idx = _index_path(root, "team")
    text1 = idx.read_text(encoding="utf-8")
    assert "2023-05-10" in text1, f"초기 편집일(2023-05-10)이 INDEX 에 없다: {text1!r}"

    # 2) 동일 본문으로 weight 만 변경하는 재write
    _run(root, "knowledge", "write",
         "--folder", "team",
         "--filename", "edit-date-test.md",
         "--content", "콘텐츠 v1.",   # 본문 동일
         "--author", "jane-doe",
         "--weight", "🔥")            # weight 변경

    # 3) 편집일이 보존되는지 확인 (_index_get_edit_date 로직 검증)
    mod = runpy.run_path(str(ENGINE), run_name="__edit_date_test__")
    fn = mod["_index_get_edit_date"]
    result = fn(idx, "memory/team/edit-date-test.md")
    assert result is not None, "INDEX 에 편집일이 없다"
    assert result == "2023-05-10", \
        f"본문 미변경 재write 후 편집일이 바뀌었다: {result!r} (기대: '2023-05-10')"


def test_knowledge_write_commits_to_git(tmp_path):
    """knowledge write: git repo 에서 파일 + INDEX 가 커밋된다."""
    root = tmp_path / "gitroot"
    root.mkdir()
    _init_git(root)

    _run(root, "knowledge", "write",
         "--folder", "extras",
         "--filename", "extras-info.md",
         "--content", "팀 17기 정보.",
         "--author", "jane-doe",
         "--weight", "📌")

    # git log 로 커밋 생성 확인
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"],
        capture_output=True, text=True)
    assert "extras-info.md" in result.stdout or "memory" in result.stdout, \
        f"커밋이 생성되지 않았거나 대상이 없음: {result.stdout!r}"


# ── C-3: conformance — knowledge write → INDEX 존재 → tm-knowledge 참조 ──

def test_conformance_knowledge_visible_via_index(tmp_path):
    """conformance: knowledge write → 파일 존재 + INDEX 에 등재 (tm-knowledge 가 발견 가능).

    tm-knowledge 스킬은 'find memory -name INDEX.md' 로 동적 발견한다.
    knowledge write 후 해당 폴더 INDEX.md 가 존재해야 한다.
    """
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "conformance-test.md",
         "--content", "conformance 검증용 지식.",
         "--author", "jane-doe",
         "--weight", "📌")

    # 파일 존재
    p = _knowledge_path(tmp_path, "team", "conformance-test.md")
    assert p.is_file(), "지식 파일이 없다"

    # INDEX.md 존재
    idx = _index_path(tmp_path, "team")
    assert idx.is_file(), "INDEX.md 가 없다 — tm-knowledge 가 발견 불가"

    # INDEX 에 파일 경로 등재
    idx_content = idx.read_text(encoding="utf-8")
    assert "conformance-test.md" in idx_content, "INDEX 에 파일이 등재되지 않았다"

    # INDEX 가 가중치 표 형식을 가짐 (tm-knowledge 가 이 형식으로 읽음)
    assert "| 가중치 |" in idx_content, "INDEX 에 가중치 표 형식 없음"


# ── 스킬 파일 존재 검증 ───────────────────────────────────────────────

def test_tm_manage_knowledge_skill_md_exists():
    """infra/skills/core/tm-manage-knowledge/SKILL.md 가 존재한다."""
    skill_md = REPO / "infra" / "skills" / "core" / "tm-manage-knowledge" / "SKILL.md"
    assert skill_md.is_file(), "tm-manage-knowledge SKILL.md 가 없다"


def test_tm_manage_knowledge_frontmatter():
    """tm-manage-knowledge SKILL.md frontmatter: name 필드가 정확하다."""
    skill_md = REPO / "infra" / "skills" / "core" / "tm-manage-knowledge" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "name: tm-manage-knowledge" in text, "SKILL.md name 필드 없음"


def test_tm_manage_knowledge_in_core_sources(tmp_path):
    """_skill_sources(layer='core') 에 tm-manage-knowledge 가 있다."""
    import shutil
    import runpy as _runpy

    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "adapter.py",
        root / "infra" / "agents" / "claude" / "adapter.py",
    )
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "events.json",
        root / "infra" / "agents" / "claude" / "events.json",
    )
    shutil.copytree(REPO / "infra" / "skills" / "base",
                    root / "infra" / "skills" / "base")
    shutil.copytree(REPO / "infra" / "skills" / "core",
                    root / "infra" / "skills" / "core")
    (root / "infra" / "skills" / "util").mkdir(parents=True, exist_ok=True)

    _CLAUDE = _runpy.run_path(
        str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
        run_name="__km_adapter__",
    )
    ClaudeAdapter = _CLAUDE["Adapter"]
    a = ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        team_root=str(root),
        skills_dir=str(tmp_path / "skills"),
    )
    core_names = {s.name for s in a._skill_sources(layer="core")}
    assert "tm-manage-knowledge" in core_names, \
        f"tm-manage-knowledge 가 core sources 에 없다: {core_names}"


# ── knowledge 동사가 _KNOWN_VERBS 에 포함됐는지 ──

def test_knowledge_in_known_verbs():
    """'knowledge' 가 엔진 _KNOWN_VERBS 에 있다."""
    mod = runpy.run_path(str(ENGINE), run_name="__known_verbs_test__")
    assert "knowledge" in mod["_KNOWN_VERBS"]


# ── author traversal 가드 ──────────────────────────────────────────────

def test_knowledge_write_rejects_traversal_author(tmp_path):
    """knowledge write: --author 에 traversal 문자 → exit 2."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "x.md",
             "--content", "x",
             "--author", "../../evil",
             "--weight", "📎")
    assert r.returncode == 2


def test_knowledge_delete_rejects_traversal_author(tmp_path):
    """knowledge delete: --author 에 traversal 문자 → exit 2."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/x.md",
             "--author", "../evil")
    assert r.returncode == 2


# ── INDEX legend 존재 ──────────────────────────────────────────────────

def test_knowledge_write_index_has_legend(tmp_path):
    """INDEX.md 에 범례 줄 (> 가중치: ...) 이 있다."""
    _run(tmp_path, "knowledge", "write",
         "--folder", "product",
         "--filename", "legend-test.md",
         "--content", "범례 검증용.",
         "--author", "jane-doe",
         "--weight", "🔥")
    idx = _index_path(tmp_path, "product")
    text = idx.read_text(encoding="utf-8")
    assert "🔥 핵심" in text, "INDEX 범례에 '🔥 핵심' 없음"
    assert "📌 중요" in text, "INDEX 범례에 '📌 중요' 없음"
    assert "📎 참고" in text, "INDEX 범례에 '📎 참고' 없음"


# ══════════════════════════════════════════════════════════════════════
# 강화 테스트 (codex 적대검수 지적 반영)
# ══════════════════════════════════════════════════════════════════════

# ── P0: symlink 탈출 거부 ─────────────────────────────────────────────

def test_knowledge_write_rejects_symlink_memory_escape(tmp_path):
    """P0: memory/ 가 team_root 밖을 가리키는 symlink 면 write 거부."""
    # team_root 와 외부 디렉토리를 분리 (같은 tmp_path 안이면 P0 가드가 안 걸림)
    team_root = tmp_path / "team_root"
    team_root.mkdir()
    outside_dir = tmp_path / "outside_memory"  # team_root 밖(형제)
    outside_dir.mkdir()

    # memory/ → team_root 밖 outside_dir 로 심링크
    symlink_memory = team_root / "memory"
    symlink_memory.symlink_to(outside_dir)

    r = _run(team_root, "knowledge", "write",
             "--folder", "team",
             "--filename", "evil.md",
             "--content", "탈출 시도.",
             "--author", "jane-doe",
             "--weight", "📎")
    # team_root 밖 symlink 이므로 거부(exit 2)
    assert r.returncode == 2, f"symlink 탈출이 거부되지 않았다: {r.stderr}"
    # 실제로 파일이 생성되지 않았는지
    evil_file = outside_dir / "team" / "evil.md"
    assert not evil_file.exists(), "symlink 탈출 후 파일이 생성됐다"


def test_knowledge_delete_rejects_symlink_memory_escape(tmp_path):
    """P0: memory/ 가 team_root 밖 symlink 면 delete 거부."""
    team_root = tmp_path / "team_root"
    team_root.mkdir()
    outside_dir = tmp_path / "outside_memory"  # team_root 형제
    outside_dir.mkdir()

    # 실제 파일 생성 (심링크 전)
    (outside_dir / "team").mkdir(parents=True, exist_ok=True)
    target = outside_dir / "team" / "victim.md"
    target.write_text("삭제될 파일", encoding="utf-8")

    symlink_memory = team_root / "memory"
    symlink_memory.symlink_to(outside_dir)

    r = _run(team_root, "knowledge", "delete",
             "--path", "team/victim.md",
             "--author", "jane-doe")
    assert r.returncode == 2, f"symlink 탈출 delete 가 거부되지 않았다: {r.stderr}"
    # 파일이 삭제되지 않았는지
    assert target.exists(), "symlink 탈출 후 파일이 삭제됐다"


# ── P1-1: 허용 폴더 거부 ──────────────────────────────────────────────

def test_knowledge_write_rejects_disallowed_folder_sessions(tmp_path):
    """P1-1: team/sessions 는 허용 폴더 아님 → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team/sessions",
             "--filename", "log.md",
             "--content", "세션 로그.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, "team/sessions 가 거부되지 않았다"
    assert not (tmp_path / "memory" / "team" / "sessions" / "log.md").exists()


def test_knowledge_write_rejects_disallowed_folder_meeting(tmp_path):
    """P1-1: team/meeting 는 허용 폴더 아님 → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team/meeting",
             "--filename", "minutes.md",
             "--content", "회의록.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, "team/meeting 가 거부되지 않았다"


def test_knowledge_write_rejects_arbitrary_folder(tmp_path):
    """P1-1: 허용 목록에 없는 임의 폴더 → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "custom-folder",
             "--filename", "x.md",
             "--content", "임의 폴더 시도.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, "임의 폴더가 거부되지 않았다"


def test_knowledge_delete_rejects_disallowed_folder(tmp_path):
    """P1-1: team/sessions 경로 delete → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/sessions/log.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "team/sessions delete 가 거부되지 않았다"


def test_knowledge_delete_rejects_index_md(tmp_path):
    """P1-1: INDEX.md 직접 삭제 → 거부(exit 2)."""
    # INDEX.md 를 먼저 만들기 위해 write 한 번 호출
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "seed.md",
         "--content", "시드.",
         "--author", "jane-doe",
         "--weight", "📎")

    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/INDEX.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "INDEX.md 삭제가 거부되지 않았다"
    # INDEX.md 는 그대로여야 함
    idx = _index_path(tmp_path, "team")
    assert idx.is_file(), "INDEX.md 가 삭제됐다"


# ── P1-2: 편집일 = today (본문 변경 시) ──────────────────────────────

def test_knowledge_write_edit_date_is_today_on_new_file(tmp_path):
    """P1-2: 신규 파일 write 후 INDEX 편집일이 오늘 날짜다."""
    import datetime as _dt
    today = _dt.date.today().strftime("%Y-%m-%d")

    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "edit-date-new.md",
         "--content", "오늘 날짜 검증.",
         "--author", "jane-doe",
         "--weight", "📎")

    idx = _index_path(tmp_path, "team")
    text = idx.read_text(encoding="utf-8")
    assert today in text, f"INDEX 에 오늘 날짜({today})가 없다: {text!r}"


def test_knowledge_write_edit_date_is_today_on_content_change(tmp_path):
    """P1-2: 본문이 바뀌면 INDEX 편집일이 today 로 갱신된다."""
    import datetime as _dt
    today = _dt.date.today().strftime("%Y-%m-%d")

    # 초기 write (과거 날짜로)
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "edit-date-update.md",
         "--content", "초기 내용.",
         "--author", "jane-doe",
         "--weight", "📎",
         "--date", "2020-01-01")

    # 본문 변경 write
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "edit-date-update.md",
         "--content", "변경된 내용.",
         "--author", "jane-doe",
         "--weight", "📎")

    idx = _index_path(tmp_path, "team")
    text = idx.read_text(encoding="utf-8")
    # 편집일이 오늘이어야 함
    assert today in text, f"본문 변경 후 INDEX 편집일이 오늘({today})이 아님: {text!r}"


# ── 멱등 mtime 실제 비교 ───────────────────────────────────────────────

def test_knowledge_write_idempotent_mtime_unchanged(tmp_path):
    """멱등: 동일 내용 재호출 시 파일 mtime 이 변경되지 않는다."""
    args = [
        "knowledge", "write",
        "--folder", "team",
        "--filename", "mtime-test.md",
        "--content", "멱등 mtime 검증.",
        "--author", "jane-doe",
        "--weight", "📎",
        "--date", "2026-06-18",
    ]
    _run(tmp_path, *args)
    p = _knowledge_path(tmp_path, "team", "mtime-test.md")
    mtime1 = p.stat().st_mtime_ns  # 나노초 정밀도

    r2 = _run(tmp_path, *args)
    assert r2.returncode == 0
    mtime2 = p.stat().st_mtime_ns
    assert mtime1 == mtime2, f"멱등인데 파일이 수정됐다: {mtime1} → {mtime2}"


# ── paths 한정: 무관 staged 변경 미포함 ───────────────────────────────

def test_knowledge_write_does_not_sweep_unrelated_staged(tmp_path):
    """knowledge write 커밋이 --paths 한정으로 무관한 staged 파일을 포함하지 않는다."""
    root = tmp_path / "gitroot"
    root.mkdir()
    _init_git(root)

    # 무관한 파일 staged
    unrelated = root / "unrelated.txt"
    unrelated.write_text("무관 파일\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "unrelated.txt"], capture_output=True)

    # knowledge write
    _run(root, "knowledge", "write",
         "--folder", "team",
         "--filename", "scoped.md",
         "--content", "범위 한정 커밋 검증.",
         "--author", "jane-doe",
         "--weight", "📎")

    # 커밋 파일 목록 확인 — 가장 최근 커밋에 unrelated.txt 가 없어야 함
    result = subprocess.run(
        ["git", "-C", str(root), "diff-tree", "--no-commit-id", "-r",
         "--name-only", "HEAD"],
        capture_output=True, text=True)
    committed_files = result.stdout.strip().splitlines()
    assert "unrelated.txt" not in committed_files, \
        f"무관 파일이 커밋에 포함됐다: {committed_files}"

    # unrelated.txt 는 여전히 staged 상태여야 함
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--short"],
        capture_output=True, text=True)
    assert "unrelated.txt" in status.stdout, "무관 파일이 커밋에 포함됐거나 사라졌다"


# ── P2: weight 3-enum 검증 ────────────────────────────────────────────

def test_knowledge_write_rejects_invalid_weight(tmp_path):
    """P2: 허용되지 않는 weight → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "bad-weight.md",
             "--content", "잘못된 weight.",
             "--author", "jane-doe",
             "--weight", "❌")
    assert r.returncode == 2, "잘못된 weight 가 거부되지 않았다"


# ── P2: INDEX 경로 포맷 표준 (memory/ 포함) ──────────────────────────

def test_knowledge_index_path_format_memory_prefix(tmp_path):
    """INDEX 에 기록된 경로가 'memory/team/...' 형식이다."""
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "path-format.md",
         "--content", "경로 포맷 검증.",
         "--author", "jane-doe",
         "--weight", "📎")
    idx = _index_path(tmp_path, "team")
    text = idx.read_text(encoding="utf-8")
    assert "memory/team/path-format.md" in text, \
        f"INDEX 경로가 memory/ 접두사를 포함하지 않는다: {text!r}"


def test_knowledge_delete_matches_memory_prefix_path(tmp_path):
    """delete 가 memory/ 접두사 포함 경로로 INDEX 행을 제대로 제거한다."""
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "prefix-delete.md",
         "--content", "삭제 테스트.",
         "--author", "jane-doe",
         "--weight", "📎")

    # memory/ 접두사 없이 delete → 정상 처리돼야 함
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/prefix-delete.md",
             "--author", "jane-doe")
    assert r.returncode == 0, r.stderr

    idx = _index_path(tmp_path, "team")
    if idx.is_file():
        text = idx.read_text(encoding="utf-8")
        assert "prefix-delete.md" not in text, "INDEX 행이 제거되지 않았다"


# ── P2: 추가 frontmatter 필드 보존 ───────────────────────────────────

def test_knowledge_write_preserves_extra_frontmatter_fields(tmp_path):
    """P2: 기존 파일에 알 수 없는 frontmatter 필드가 있으면 재작성 후에도 보존된다."""
    p = _knowledge_path(tmp_path, "team", "extra-fields.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    # source 필드가 있는 기존 파일
    p.write_text(
        "---\n"
        "created_at: 2020-01-01\n"
        "updated_at: 2020-01-01\n"
        "author: jane-doe\n"
        "weight: 📎\n"
        "source: https://example.com\n"
        "---\n"
        "기존 내용.\n",
        encoding="utf-8"
    )

    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "extra-fields.md",
         "--content", "수정된 내용.\n",
         "--author", "jane-doe",
         "--weight", "📎")

    text = p.read_text(encoding="utf-8")
    assert "source: https://example.com" in text, \
        "추가 frontmatter 필드(source)가 재작성 후 사라졌다"


# ══════════════════════════════════════════════════════════════════════
# P1 잔여 테스트 (codex 2차 지적 반영)
# ══════════════════════════════════════════════════════════════════════

# ── P1-1: delete 폴더 검증 (write 와 동일한 blocked/allowed 규칙) ─────

def test_knowledge_delete_rejects_sessions_folder(tmp_path):
    """P1-1: team/sessions 경로 delete → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/sessions/jane-doe/2026-06-18.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "team/sessions delete 가 거부되지 않았다"


def test_knowledge_delete_rejects_meeting_folder(tmp_path):
    """P1-1: team/meeting 경로 delete → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "team/meeting/2026-06-18-standup.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "team/meeting delete 가 거부되지 않았다"


def test_knowledge_delete_rejects_arbitrary_folder(tmp_path):
    """P1-1: 허용 목록에 없는 임의 폴더 delete → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "arbitrary-folder/file.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "임의 폴더 delete 가 거부되지 않았다"


def test_knowledge_delete_rejects_root_memory_file(tmp_path):
    """P1-1: memory/ 바로 아래(허용 폴더 없는) 파일 delete → 거부(exit 2)."""
    # memory/evil.md — 폴더 부분 없음, 허용 목록에 해당 없음
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "memory/evil.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "root memory 파일 delete 가 거부되지 않았다"


def test_knowledge_delete_rejects_root_index(tmp_path):
    """P1-1: memory/INDEX.md(root-level) delete → 거부(exit 2)."""
    # 직접 memory/INDEX.md 를 delete 경로로 지정
    r = _run(tmp_path, "knowledge", "delete",
             "--path", "memory/INDEX.md",
             "--author", "jane-doe")
    assert r.returncode == 2, "root INDEX.md delete 가 거부되지 않았다"


# ── P1-2: commit 실패 → 경고 + non-zero ──────────────────────────────

def test_knowledge_write_commit_failure_returns_nonzero(tmp_path):
    """P1-2: git repo 에서 index.lock 으로 커밋 실패 유도 → 파일 생성됨 + exit 1 + [warning]."""
    root = tmp_path / "gitroot"
    root.mkdir()
    _init_git(root)

    # git index.lock 을 미리 만들어 git add 가 실패하게 한다
    lock_file = root / ".git" / "index.lock"
    lock_file.write_text("lock", encoding="utf-8")

    r = _run(root, "knowledge", "write",
             "--folder", "team",
             "--filename", "commit-fail.md",
             "--content", "커밋 실패 검증.",
             "--author", "jane-doe",
             "--weight", "📎")

    # index.lock 제거 (잔존 방지)
    try:
        lock_file.unlink()
    except OSError:
        pass

    # 파일은 써졌어야 함
    p = _knowledge_path(root, "team", "commit-fail.md")
    assert p.is_file(), "커밋 실패여도 파일은 생성돼야 한다"
    # exit code 1 (커밋 실패 알림)
    assert r.returncode == 1, \
        f"커밋 실패 write 가 exit 0 을 냈다(경고 묵살): rc={r.returncode}, stderr={r.stderr!r}"
    # [warning] 출력
    assert "[warning]" in r.stderr, \
        f"커밋 실패 [warning] 가 stderr 에 없다: {r.stderr!r}"


def test_knowledge_delete_commit_failure_returns_nonzero(tmp_path):
    """P1-2: git repo 에서 index.lock 으로 커밋 실패 유도 → 파일 삭제됨 + exit 1 + [warning]."""
    root = tmp_path / "gitroot"
    root.mkdir()
    _init_git(root)

    # 먼저 파일 생성(정상 git 환경에서)
    _run(root, "knowledge", "write",
         "--folder", "team",
         "--filename", "del-commit-fail.md",
         "--content", "삭제될 파일\n",
         "--author", "jane-doe",
         "--weight", "📎")
    p = _knowledge_path(root, "team", "del-commit-fail.md")
    assert p.is_file(), "사전 조건: 파일이 생성됐어야 한다"

    # git index.lock 으로 commit 실패 유도
    lock_file = root / ".git" / "index.lock"
    lock_file.write_text("lock", encoding="utf-8")

    r = _run(root, "knowledge", "delete",
             "--path", "team/del-commit-fail.md",
             "--author", "jane-doe")

    try:
        lock_file.unlink()
    except OSError:
        pass

    # 파일은 삭제됐어야 함
    assert not p.is_file(), "커밋 실패여도 파일은 삭제돼야 한다"
    # exit code 1
    assert r.returncode == 1, \
        f"커밋 실패 delete 가 exit 0 을 냈다(경고 묵살): rc={r.returncode}, stderr={r.stderr!r}"
    # [warning] 출력
    assert "[warning]" in r.stderr, \
        f"커밋 실패 [warning] 가 stderr 에 없다: {r.stderr!r}"


# ── P1-3: 편집일 보존 (본문 미변경 재write 시 기존 INDEX 행 날짜 유지) ──

def test_knowledge_write_edit_date_preserved_on_no_content_change(tmp_path):
    """P1-3: 본문 미변경(weight/author 만 변경) 재write 시 기존 편집일이 보존된다."""
    # 초기 write — 편집일을 과거 날짜로 고정
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "preserve-date.md",
         "--content", "변경 없는 내용.",
         "--author", "jane-doe",
         "--weight", "📎",
         "--date", "2020-03-15")

    # 첫 번째 write 후 INDEX 편집일 확인
    idx = _index_path(tmp_path, "team")
    text1 = idx.read_text(encoding="utf-8")
    assert "2020-03-15" in text1, f"초기 편집일(2020-03-15)이 INDEX 에 없다: {text1!r}"

    # 동일 본문으로 weight 만 변경하는 재write (content 동일 → 본문 미변경)
    _run(tmp_path, "knowledge", "write",
         "--folder", "team",
         "--filename", "preserve-date.md",
         "--content", "변경 없는 내용.",
         "--author", "jane-doe",
         "--weight", "🔥")  # weight 변경

    idx2 = idx.read_text(encoding="utf-8")
    # 편집일이 오늘 날짜(today)로 바뀌지 않고 2020-03-15 가 보존돼야 함
    assert "2020-03-15" in idx2, \
        f"본문 미변경 재write 후 편집일이 바뀌었다(subject-substring 의존 제거 확인): {idx2!r}"
