"""루트 라우팅 맵(`memory/INDEX.md` 2열 표) CRUD 동사 테스트 — `memory route`.

대상 동사: `memory route {upsert|remove}` — 루트 2열 표
`| 경로 | 여기에 넣는 것 |` 만 편집(기존 4열 folder-INDEX 무회귀).

커버리지:
  - upsert 신규 삽입 / 기존 갱신 / 멱등(2차 커밋 없음)
  - remove 존재 행 제거 / 부재 시 무변경(멱등 exit 0)
  - 표 위 산문 보존
  - 폴더행(`product/brand/`) vs 파일행(`product/brand/philosophy.md`) 오매칭 없음
  - traversal 거부(exit 2)
  - upsert --desc 누락(exit 2)

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"

sys.path.insert(0, str(REPO / "infra"))


# ── 공통 헬퍼 (test_knowledge_p1_hotfix.py 규약 준수) ─────────────────

def _run(root: Path, *argv):
    """teammode.py 를 subprocess 로 직접 호출."""
    cmd = [sys.executable, str(ENGINE), *argv, "--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _init_git(root: Path) -> None:
    """tmp 경로에 최소 git repo 초기화."""
    subprocess.run(["git", "init", str(root)], capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@test.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"],
                   capture_output=True)
    readme = root / "README.md"
    readme.write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], capture_output=True)


def _commit_count(root: Path) -> int:
    r = subprocess.run(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True)
    return int(r.stdout.strip() or "0")


# 표 위 산문(주입 안내·"새 폴더 등재 필수"·팀 루트 안내) + 2열 표 시드.
_PROSE_LINE_1 = "세션 시작 시 주입되는 단일 진입점. 새 폴더를 만들면 여기 등재한다(필수)."
_PROSE_LINE_2 = "팀 루트: `$TEAMMODE_HOME` (또는 `teammode.py --root <경로>` 명시)."

_SEED_INDEX = (
    "# 팀 메모리 인덱스 (INDEX.md)\n"
    "\n"
    f"{_PROSE_LINE_1}\n"
    "\n"
    f"{_PROSE_LINE_2}\n"
    "\n"
    "| 경로 | 여기에 넣는 것 |\n"
    "|---|---|\n"
    "| `team/members.md` | 멤버 명부 |\n"
    "| `product/brand/` | 브랜드 철학·디자인 가이드 |\n"
    "| `product/brand/philosophy.md` | 브랜드 철학 단건 |\n"
)


def _seed_root_index(root: Path, content: str = _SEED_INDEX) -> Path:
    index_path = root / "memory" / "INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "memory/INDEX.md"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "seed root index"],
                   capture_output=True)
    return index_path


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# upsert — 신규 삽입
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_inserts_new_row(tmp_path):
    root = tmp_path
    _init_git(root)
    index_path = _seed_root_index(root)

    r = _run(root, "memory", "route", "upsert",
             "--path", "product/marketing/",
             "--desc", "마케팅·GTM·광고",
             "--author", "bob")
    assert r.returncode == 0, f"upsert 실패: rc={r.returncode}, err={r.stderr!r}"

    text = _read(index_path)
    assert "`product/marketing/`" in text
    assert "마케팅·GTM·광고" in text
    # 표 안(파이프 행)으로 들어갔는지
    assert "| `product/marketing/` | 마케팅·GTM·광고 |" in text


# ══════════════════════════════════════════════════════════════════════
# upsert — 기존 행 갱신
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_updates_existing_row(tmp_path):
    root = tmp_path
    _init_git(root)
    index_path = _seed_root_index(root)

    r = _run(root, "memory", "route", "upsert",
             "--path", "team/members.md",
             "--desc", "멤버 명부 — 갱신된 설명",
             "--author", "bob")
    assert r.returncode == 0, f"upsert 갱신 실패: {r.stderr!r}"

    text = _read(index_path)
    assert "멤버 명부 — 갱신된 설명" in text
    assert "| 멤버 명부 |" not in text  # 옛 설명 사라짐
    # 행이 중복 추가되지 않았는지 (`team/members.md` 한 번만)
    assert text.count("`team/members.md`") == 1


# ══════════════════════════════════════════════════════════════════════
# upsert — 멱등 (2차 커밋 없음)
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_idempotent_no_second_commit(tmp_path):
    root = tmp_path
    _init_git(root)
    _seed_root_index(root)

    r1 = _run(root, "memory", "route", "upsert",
              "--path", "product/marketing/",
              "--desc", "마케팅",
              "--author", "bob")
    assert r1.returncode == 0
    count_after_first = _commit_count(root)

    # 동일 호출 재실행 → 변경 없음 → 새 커밋 없음
    r2 = _run(root, "memory", "route", "upsert",
              "--path", "product/marketing/",
              "--desc", "마케팅",
              "--author", "bob")
    assert r2.returncode == 0, f"멱등 호출이 0 이 아님: {r2.stderr!r}"
    count_after_second = _commit_count(root)
    assert count_after_second == count_after_first, (
        f"멱등 호출이 새 커밋을 만들었다: {count_after_first} → {count_after_second}"
    )


# ══════════════════════════════════════════════════════════════════════
# remove — 존재 행 제거
# ══════════════════════════════════════════════════════════════════════

def test_route_remove_present_row(tmp_path):
    root = tmp_path
    _init_git(root)
    index_path = _seed_root_index(root)

    r = _run(root, "memory", "route", "remove",
             "--path", "team/members.md",
             "--author", "bob")
    assert r.returncode == 0, f"remove 실패: {r.stderr!r}"

    text = _read(index_path)
    assert "`team/members.md`" not in text
    # 다른 행은 유지
    assert "`product/brand/`" in text


# ══════════════════════════════════════════════════════════════════════
# remove — 부재 시 무변경 (멱등 exit 0)
# ══════════════════════════════════════════════════════════════════════

def test_route_remove_absent_is_idempotent(tmp_path):
    root = tmp_path
    _init_git(root)
    _seed_root_index(root)
    count_before = _commit_count(root)

    r = _run(root, "memory", "route", "remove",
             "--path", "product/does-not-exist/",
             "--author", "bob")
    assert r.returncode == 0, f"부재 remove 가 0 이 아님: rc={r.returncode}, {r.stderr!r}"
    count_after = _commit_count(root)
    assert count_after == count_before, "부재 remove 가 새 커밋을 만들었다"


# ══════════════════════════════════════════════════════════════════════
# 표 위 산문 보존
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_preserves_prose_above_table(tmp_path):
    root = tmp_path
    _init_git(root)
    index_path = _seed_root_index(root)

    r = _run(root, "memory", "route", "upsert",
             "--path", "product/marketing/",
             "--desc", "마케팅",
             "--author", "bob")
    assert r.returncode == 0

    text = _read(index_path)
    assert _PROSE_LINE_1 in text, "표 위 산문 1 이 사라졌다"
    assert _PROSE_LINE_2 in text, "표 위 산문 2 가 사라졌다"
    assert "# 팀 메모리 인덱스 (INDEX.md)" in text


# ══════════════════════════════════════════════════════════════════════
# 폴더행 vs 파일행 오매칭 없음
# ══════════════════════════════════════════════════════════════════════

def test_route_folder_row_and_file_row_no_cross_match_remove(tmp_path):
    root = tmp_path
    _init_git(root)
    index_path = _seed_root_index(root)

    # 파일행만 제거 → 폴더행은 유지돼야 한다
    r = _run(root, "memory", "route", "remove",
             "--path", "product/brand/philosophy.md",
             "--author", "bob")
    assert r.returncode == 0, f"파일행 remove 실패: {r.stderr!r}"

    text = _read(index_path)
    assert "`product/brand/philosophy.md`" not in text, "파일행이 제거돼야 한다"
    assert "| `product/brand/` |" in text, "폴더행은 유지돼야 한다(오매칭 없음)"


def test_route_folder_row_update_does_not_touch_file_row(tmp_path):
    root = tmp_path
    _init_git(root)
    index_path = _seed_root_index(root)

    # 폴더행 갱신 → 파일행 설명 불변
    r = _run(root, "memory", "route", "upsert",
             "--path", "product/brand/",
             "--desc", "폴더행 갱신",
             "--author", "bob")
    assert r.returncode == 0, f"폴더행 upsert 실패: {r.stderr!r}"

    text = _read(index_path)
    assert "| `product/brand/` | 폴더행 갱신 |" in text
    assert "| `product/brand/philosophy.md` | 브랜드 철학 단건 |" in text, (
        "파일행이 폴더행 갱신에 휩쓸렸다(오매칭)"
    )


# ══════════════════════════════════════════════════════════════════════
# traversal 거부
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_rejects_traversal(tmp_path):
    root = tmp_path
    _init_git(root)
    _seed_root_index(root)

    r = _run(root, "memory", "route", "upsert",
             "--path", "../escape.md",
             "--desc", "탈출 시도",
             "--author", "bob")
    assert r.returncode == 2, (
        f"traversal '../escape.md' 가 거부되지 않았다: rc={r.returncode}, {r.stdout!r}"
    )


def test_route_upsert_rejects_absolute_path(tmp_path):
    root = tmp_path
    _init_git(root)
    _seed_root_index(root)

    r = _run(root, "memory", "route", "upsert",
             "--path", "/etc/passwd",
             "--desc", "절대경로 시도",
             "--author", "bob")
    assert r.returncode == 2, (
        f"절대경로 '/etc/passwd' 가 거부되지 않았다: rc={r.returncode}"
    )


# ══════════════════════════════════════════════════════════════════════
# upsert --desc 누락 → exit 2
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_missing_desc_exit2(tmp_path):
    root = tmp_path
    _init_git(root)
    _seed_root_index(root)

    r = _run(root, "memory", "route", "upsert",
             "--path", "product/marketing/",
             "--author", "bob")
    assert r.returncode == 2, (
        f"--desc 누락이 exit 2 를 내지 않았다: rc={r.returncode}, {r.stdout!r}"
    )


def test_route_unknown_sub_action_exit2(tmp_path):
    root = tmp_path
    _init_git(root)
    _seed_root_index(root)

    r = _run(root, "memory", "route", "frobnicate",
             "--path", "product/marketing/",
             "--author", "bob")
    assert r.returncode == 2, (
        f"알 수 없는 서브액션이 exit 2 를 내지 않았다: rc={r.returncode}"
    )


# ══════════════════════════════════════════════════════════════════════
# 표 부재 시 새 표 생성
# ══════════════════════════════════════════════════════════════════════

def test_route_upsert_creates_table_when_absent(tmp_path):
    root = tmp_path
    _init_git(root)
    # 표 없는 산문만 있는 INDEX
    _seed_root_index(root, content="# 팀 메모리 인덱스\n\n산문만 있고 표는 없음.\n")

    index_path = root / "memory" / "INDEX.md"
    r = _run(root, "memory", "route", "upsert",
             "--path", "product/marketing/",
             "--desc", "마케팅",
             "--author", "bob")
    assert r.returncode == 0, f"표 생성 upsert 실패: {r.stderr!r}"

    text = _read(index_path)
    assert "| 경로 | 여기에 넣는 것 |" in text
    assert "| `product/marketing/` | 마케팅 |" in text
    assert "산문만 있고 표는 없음." in text  # 기존 산문 보존
