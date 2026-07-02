"""memory write → 루트 라우팅 맵 미등재 힌트 테스트 (#7 잔여 갭).

`memory route {upsert|remove}` 동사(#12/#16)는 존재하지만 write 흐름이
이를 안내하지 않아 발견 불가였다. write 성공 후 대상 최상위 폴더가
루트 `memory/INDEX.md` 2열 표에 커버되지 않으면 `[hint]` 한 줄을
stdout 으로 출력한다(자동 등재 아님 — 설명 한 줄은 사람/AI 가 정한다).

커버리지:
  - 미등재 최상위 폴더(soma/) write → stdout 에 [hint] + `memory route upsert`
  - 등재된 폴더(team/ — 템플릿은 파일행 `team/members.md` 만 등재) write → 힌트 없음
  - 하위 폴더 write(team/decisions/)도 최상위(team/) 커버로 판단 → 힌트 없음
  - 힌트가 제안한 명령을 그대로 실행 → 루트 INDEX 에 행 등재 + 재write 시 힌트 소멸

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"

sys.path.insert(0, str(REPO / "infra"))


# ── 공통 헬퍼 (test_route_index.py 규약 준수) ─────────────────────────

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


# 설치 템플릿(install_lib._INDEX_HEADER)과 같은 모양 — 최상위 "폴더행" 없이
# 파일행만 등재된 stock 상태를 재현한다(team/·product/ 는 파일행으로 커버).
_SEED_INDEX = (
    "# 팀 메모리 인덱스 (INDEX.md)\n"
    "\n"
    "세션 시작 시 주입되는 단일 진입점. 새 폴더를 만들면 여기 등재한다(필수).\n"
    "\n"
    "| 경로 | 여기에 넣는 것 |\n"
    "|---|---|\n"
    "| `team/members.md` | 멤버 명부 |\n"
    "| `team/decisions/current.md` | 활성 결정사항 |\n"
    "| `product/brand/philosophy.md` | 브랜드 철학 |\n"
)


def _seed_root_index(root: Path, content: str = _SEED_INDEX) -> Path:
    index_path = root / "memory" / "INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content, encoding="utf-8")
    return index_path


def _write(root: Path, folder: str, filename: str = "note.md"):
    return _run(root, "memory", "write",
                "--folder", folder,
                "--filename", filename,
                "--content", "테스트 내용 한 줄.",
                "--author", "eunsu",
                "--weight", "📎")


# ── 미등재 최상위 폴더 → 힌트 ───────────────────────────────────────

def test_write_unregistered_top_folder_emits_hint(tmp_path):
    """루트 INDEX 에 soma/ 커버 행이 없으면 write 후 [hint] 한 줄이 나온다."""
    _seed_root_index(tmp_path)
    r = _write(tmp_path, "soma")
    assert r.returncode == 0, r.stderr
    assert "[hint]" in r.stdout, f"[hint] 없음:\n{r.stdout}"
    assert "memory route upsert" in r.stdout
    assert "soma/" in r.stdout
    # 실 CLI 플래그명 일치 (cmd_route: --root/--path/--desc/--author 필수)
    assert "--path soma/" in r.stdout
    assert "--desc" in r.stdout
    assert "--author" in r.stdout


def test_hint_is_advisory_write_still_succeeds(tmp_path):
    """힌트가 나와도 write 자체(파일·folder INDEX)는 정상 완료된다."""
    _seed_root_index(tmp_path)
    r = _write(tmp_path, "soma")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "memory" / "soma" / "note.md").is_file()
    assert "teammode memory write — soma/note.md 완료" in r.stdout


# ── 등재된 폴더 → 힌트 없음 ─────────────────────────────────────────

def test_write_registered_folder_no_hint(tmp_path):
    """team/ 은 파일행(team/members.md)으로 커버 — 힌트가 나오면 안 된다."""
    _seed_root_index(tmp_path)
    r = _write(tmp_path, "team")
    assert r.returncode == 0, r.stderr
    assert "[hint]" not in r.stdout, f"등재된 폴더에 힌트:\n{r.stdout}"


def test_write_subfolder_of_registered_top_no_hint(tmp_path):
    """team/decisions/ write 도 최상위 team/ 커버로 판단 — 힌트 없음."""
    _seed_root_index(tmp_path)
    r = _write(tmp_path, "team/decisions")
    assert r.returncode == 0, r.stderr
    assert "[hint]" not in r.stdout, f"하위 폴더 write 에 힌트:\n{r.stdout}"


def test_write_top_folder_row_counts_as_registered(tmp_path):
    """폴더행 `soma/` 자체가 등재돼 있으면 힌트 없음 (route upsert 결과 모양)."""
    _seed_root_index(tmp_path, _SEED_INDEX.replace(
        "| `product/brand/philosophy.md` | 브랜드 철학 |\n",
        "| `product/brand/philosophy.md` | 브랜드 철학 |\n"
        "| `soma/` | 소마 과정 관련 정보 |\n"))
    r = _write(tmp_path, "soma")
    assert r.returncode == 0, r.stderr
    assert "[hint]" not in r.stdout


# ── 힌트가 제안한 명령의 라운드트립 ─────────────────────────────────

def test_hint_suggested_command_registers_and_silences(tmp_path):
    """힌트의 제안 명령(route upsert)을 그대로 실행 → 행 등재 + 재write 시 힌트 소멸."""
    _init_git(tmp_path)
    index_path = _seed_root_index(tmp_path)
    r1 = _write(tmp_path, "soma", "a.md")
    assert "[hint]" in r1.stdout

    # 힌트가 제안한 그대로: memory route upsert --root <루트> --path soma/ --desc ... --author ...
    r2 = _run(tmp_path, "memory", "route", "upsert",
              "--path", "soma/",
              "--desc", "소마 과정 관련 정보",
              "--author", "eunsu")
    assert r2.returncode == 0, r2.stderr
    content = index_path.read_text(encoding="utf-8")
    assert "| `soma/` | 소마 과정 관련 정보 |" in content

    # 등재 후 같은 폴더 재write → 힌트 없음
    r3 = _write(tmp_path, "soma", "b.md")
    assert r3.returncode == 0, r3.stderr
    assert "[hint]" not in r3.stdout
