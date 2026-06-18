"""S1 핫픽스 — codex 적대검수 3건 반영 테스트 (fault injection 기반).

항목:
  1. content 제어문자 범위: C1(U+0085, U+009B), Cf(U+200D ZWJ), surrogate 거부.
  2. delete --path filename 문자 검증: 제어문자·전각문자 filename 거부.
  3. write/delete 부분 실패 정합성: fault injection 으로 INDEX/파일 정합성 확인.
  4. (선택) folder segment isascii: 전각문자 folder 거부.

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
"""
import io
import os
import runpy
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"

sys.path.insert(0, str(REPO / "infra"))


# ── 공통 헬퍼 ──────────────────────────────────────────────────────

def _run(root: Path, *argv):
    """teammode.py 를 subprocess 로 직접 호출."""
    cmd = [sys.executable, str(ENGINE), *argv, "--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_main(root: Path, *argv) -> tuple[int, str, str]:
    """teammode.py main() 을 직접 호출 (subprocess 불가 케이스용).

    반환: (returncode, stdout_text, stderr_text)
    """
    mod = runpy.run_path(str(ENGINE), run_name="__s1_hotfix_test__")
    args = list(argv) + ["--root", str(root)]
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = mod["main"](args)
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    finally:
        out = sys.stdout.getvalue()
        err = sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return rc, out, err


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


def _write_ok(root: Path, filename: str = "test-file.md",
              folder: str = "team", content: str = "테스트 내용.") -> subprocess.CompletedProcess:
    """정상 write 호출 헬퍼."""
    return _run(root, "knowledge", "write",
                "--folder", folder,
                "--filename", filename,
                "--content", content,
                "--author", "jane-doe",
                "--weight", "📎")


# ══════════════════════════════════════════════════════════════════════
# 항목 1: content 제어문자 범위 확장 — C1·Cf·surrogate 거부
# ══════════════════════════════════════════════════════════════════════

def test_content_rejects_c1_nel(tmp_path):
    """C1 제어: U+0085 NEL (NEXT LINE) → 거부(exit 2).

    기존 코드는 0x00–0x1F+0x7F 만 거부 → U+0085 통과. 수정 후 Cc 카테고리로 거부.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "write",
        "--folder", "team",
        "--filename", "c1-nel.md",
        "--content", "정상 텍스트\x85C1 제어",  # U+0085 NEL
        "--author", "jane-doe",
        "--weight", "📎",
    )
    assert rc == 2, (
        f"U+0085 NEL(C1 제어)이 거부되지 않았다: rc={rc}, err={err!r}"
    )


def test_content_rejects_c1_csi(tmp_path):
    """C1 제어: U+009B CSI (CONTROL SEQUENCE INTRODUCER) → 거부(exit 2).

    터미널 주입 벡터. 기존 코드 통과 → 수정 후 Cc 카테고리로 거부.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "write",
        "--folder", "team",
        "--filename", "c1-csi.md",
        "--content", "정상 텍스트\x9bCSI 삽입",  # U+009B CSI
        "--author", "jane-doe",
        "--weight", "📎",
    )
    assert rc == 2, (
        f"U+009B CSI(C1 제어)이 거부되지 않았다: rc={rc}, err={err!r}"
    )


def test_content_rejects_cf_zwj(tmp_path):
    """Cf 포맷: U+200D ZWJ (ZERO WIDTH JOINER) → 거부(exit 2).

    기존 코드는 Cf 카테고리 무시 → ZWJ 통과. 수정 후 Cf 카테고리로 거부.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "write",
        "--folder", "team",
        "--filename", "cf-zwj.md",
        "--content", "정상 텍스트‍ZWJ 삽입",  # U+200D ZWJ
        "--author", "jane-doe",
        "--weight", "📎",
    )
    assert rc == 2, (
        f"U+200D ZWJ(Cf 포맷)이 거부되지 않았다: rc={rc}, err={err!r}"
    )


def test_content_rejects_surrogate(tmp_path):
    """고립 surrogate U+D800 → 거부(exit 2).

    기존 코드는 surrogate 를 만나면 UnicodeEncodeError uncaught. 수정 후 명시 거부.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "write",
        "--folder", "team",
        "--filename", "surrogate.md",
        "--content", "정상 텍스트" + "\ud800" + "surrogate",  # 고립 surrogate
        "--author", "jane-doe",
        "--weight", "📎",
    )
    assert rc == 2, (
        f"고립 surrogate(U+D800)가 거부되지 않았다: rc={rc}, err={err!r}"
    )


def test_content_allows_emoji(tmp_path):
    """이모지(이모지는 Cs/Cf 아님, 일반 유니코드) → 허용 유지.

    수정으로 이모지까지 막히면 안 된다.
    """
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "emoji-ok.md",
             "--content", "이모지 포함 내용 🔥✅🎉",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 0, (
        f"이모지 content 가 거부됐다(허용돼야 함): rc={r.returncode}, stderr={r.stderr!r}"
    )


def test_content_allows_newline_tab_cr(tmp_path):
    """개행(\n)·탭(\t)·CR(\r)은 여전히 허용 — 문서 포맷에 필수."""
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "write",
        "--folder", "team",
        "--filename", "allowed-ctrl.md",
        "--content", "첫 줄\n둘째 줄\r\n\t들여쓰기",
        "--author", "jane-doe",
        "--weight", "📎",
    )
    assert rc == 0, (
        f"개행·탭·CR이 거부됐다(허용돼야 함): rc={rc}, err={err!r}"
    )


# ══════════════════════════════════════════════════════════════════════
# 항목 2: delete --path filename 문자 검증
# ══════════════════════════════════════════════════════════════════════

def test_delete_rejects_control_char_in_filename(tmp_path):
    """delete --path 에 제어문자 포함 filename → 거부(exit 2).

    기존 코드: filename 검증 없음 → 제어문자 filename 통과(파일 없으면 멱등 반환).
    수정 후: write 와 동일 정책으로 filename 검증.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "delete",
        "--path", "team/bad\x01file.md",  # 제어문자(SOH)
        "--author", "jane-doe",
    )
    assert rc == 2, (
        f"delete 에서 제어문자 filename 이 거부되지 않았다: rc={rc}, err={err!r}"
    )


def test_delete_rejects_unicode_filename(tmp_path):
    """delete --path 에 전각문자·비ASCII filename → 거부(exit 2).

    기존 코드: ASCII 검증 없음 → 전각문자 filename 통과.
    수정 후: isascii() 강제.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "delete",
        "--path", "team/Ａ-file.md",  # 전각 A (U+FF21)
        "--author", "jane-doe",
    )
    assert rc == 2, (
        f"delete 에서 전각문자 filename 이 거부되지 않았다: rc={rc}, err={err!r}"
    )


def test_delete_rejects_nul_in_filename(tmp_path):
    """delete --path 에 NUL 포함 filename → 거부(exit 2), ValueError uncaught 아님.

    기존 코드: NUL → Path 생성 시 ValueError uncaught (exit 1).
    수정 후: filename 검증에서 exit 2 친화 메시지.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "delete",
        "--path", "team/bad\x00file.md",  # NUL
        "--author", "jane-doe",
    )
    assert rc == 2, (
        f"delete 에서 NUL filename 이 exit 2 를 내지 않았다: rc={rc}, err={err!r}"
    )
    # exit 1(uncaught exception) 이 아닌 exit 2(검증 거부) 여야 한다


def test_delete_valid_filename_allowed(tmp_path):
    """delete 정상 filename(kebab-case ASCII) → 파일 없으면 멱등(exit 0)으로 통과.

    filename 검증이 올바른 파일명을 막아선 안 된다.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "delete",
        "--path", "team/valid-file.md",
        "--author", "jane-doe",
    )
    # 파일 없으면 멱등(exit 0), 있어도 정상 삭제 후 exit 0
    assert rc == 0, (
        f"정상 filename 이 delete 에서 거부됐다: rc={rc}, err={err!r}"
    )


# ══════════════════════════════════════════════════════════════════════
# 항목 3: write/delete 부분 실패 정합성 (fault injection)
# ══════════════════════════════════════════════════════════════════════

def test_write_atomicity_file_rollback_on_index_failure(tmp_path):
    """write: 파일 write 성공 후 INDEX 갱신 실패 → 파일 롤백(신규 파일은 삭제).

    fault injection: folder 디렉토리를 read-only 로 만들어 INDEX.md write 를 실패시킨다.
    파일 자체는 먼저 write 가 성공하고(tmp_path 아래 tempfile → os.replace),
    그 직후 INDEX write 가 권한 에러로 실패 → 파일 롤백(삭제) 확인.
    기대: exit 2 + 신규 파일이 남아 있지 않음.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    target = folder_dir / "rollback-new.md"
    assert not target.exists(), "사전 조건: 파일이 없어야 한다"

    # 파일 write 후 INDEX write 실패를 유도:
    # INDEX.md 를 먼저 생성하고 read-only 로 만들 경우 _index_upsert 내부의
    # index_path.write_text 가 PermissionError 를 던진다.
    index_path = folder_dir / "INDEX.md"
    index_path.write_text("# INDEX\n", encoding="utf-8")
    # folder 디렉토리를 read-only → index_path.write_text 가 PermissionError
    os.chmod(str(folder_dir), stat.S_IRUSR | stat.S_IXUSR)

    try:
        r = _run(tmp_path, "knowledge", "write",
                 "--folder", "team",
                 "--filename", "rollback-new.md",
                 "--content", "정합성 테스트.",
                 "--author", "jane-doe",
                 "--weight", "📎")
        rc = r.returncode
    finally:
        # 권한 복원 (cleanup)
        os.chmod(str(folder_dir), stat.S_IRWXU)

    assert rc == 2, f"INDEX 실패 시 exit 2 가 아님: rc={rc}"
    assert not target.exists(), (
        f"INDEX 실패 후 파일이 남아 있다(롤백 안 됨): {target}"
    )


def test_write_atomicity_existing_file_restored_on_index_failure(tmp_path):
    """write: 기존 파일 덮어쓰기 성공 후 INDEX 실패 → 기존 파일 복원.

    fault injection: INDEX.md 를 read-only 로 만들어 INDEX write 를 실패시킨다.
    기존 파일은 원본 내용으로 복원되어야 한다.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    target = folder_dir / "existing-file.md"
    original_content = (
        "---\nauthor: jane-doe\nweight: 📎\n"
        "created_at: 2026-01-01\nupdated_at: 2026-01-01\n---\n원본 내용."
    )
    target.write_text(original_content, encoding="utf-8")

    # INDEX.md 생성 후 folder 를 read-only → INDEX write 실패
    index_path = folder_dir / "INDEX.md"
    index_path.write_text("# INDEX\n", encoding="utf-8")
    os.chmod(str(folder_dir), stat.S_IRUSR | stat.S_IXUSR)

    try:
        r = _run(tmp_path, "knowledge", "write",
                 "--folder", "team",
                 "--filename", "existing-file.md",
                 "--content", "수정된 내용.",
                 "--author", "jane-doe",
                 "--weight", "📎")
        rc = r.returncode
    finally:
        os.chmod(str(folder_dir), stat.S_IRWXU)

    assert rc == 2, f"INDEX 실패 시 exit 2 가 아님: rc={rc}"
    restored = target.read_text(encoding="utf-8")
    assert restored == original_content, (
        f"기존 파일이 복원되지 않았다:\n원본: {original_content!r}\n현재: {restored!r}"
    )


def test_delete_atomicity_index_restored_on_unlink_failure(tmp_path):
    """delete: INDEX 행 제거 성공 후 unlink 실패 → INDEX 롤백(행 복원).

    fault injection: Path.unlink 를 OSError 로 패치.
    기대: exit 2 + INDEX 에 행이 다시 있음.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    # 파일 생성
    target = folder_dir / "delete-test.md"
    target.write_text(
        "---\nauthor: jane-doe\nweight: 📎\ncreated_at: 2026-01-01\nupdated_at: 2026-01-01\n---\n삭제 테스트.",
        encoding="utf-8"
    )

    # INDEX 에 행 추가
    index_path = folder_dir / "INDEX.md"
    index_path.write_text(
        "| 가중치 | 경로 | 내용 | 편집일 |\n"
        "|--------|------|------|--------|\n"
        "| 📎 | `memory/team/delete-test.md` | 삭제 테스트. | 2026-01-01 |\n",
        encoding="utf-8"
    )
    index_original = index_path.read_text(encoding="utf-8")

    mod = runpy.run_path(str(ENGINE), run_name="__delete_rollback_test__")

    args = [
        "knowledge", "delete",
        "--path", "team/delete-test.md",
        "--author", "jane-doe",
        "--root", str(tmp_path),
    ]

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        original_unlink = Path.unlink
        def _failing_unlink(self, missing_ok=False):
            if self.name == "delete-test.md":
                raise OSError("fault injection: unlink 실패")
            return original_unlink(self, missing_ok=missing_ok)
        with mock.patch.object(Path, "unlink", _failing_unlink):
            try:
                rc = mod["main"](args)
            except SystemExit as e:
                rc = int(e.code) if e.code is not None else 0
    finally:
        out = sys.stdout.getvalue()
        err = sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_stdout, old_stderr

    assert rc == 2, f"unlink 실패 시 exit 2 가 아님: rc={rc}, err={err!r}"
    index_after = index_path.read_text(encoding="utf-8")
    assert "delete-test.md" in index_after, (
        f"INDEX 롤백 실패: 행이 복원되지 않았다.\n원본:\n{index_original}\n현재:\n{index_after}"
    )
    assert target.exists(), (
        f"파일은 삭제 실패했어야 하는데 없다(unlink 패치가 작동 안 함)"
    )


# ══════════════════════════════════════════════════════════════════════
# 항목 4: folder segment isascii 강제
# ══════════════════════════════════════════════════════════════════════

def test_folder_segment_rejects_fullwidth_unicode(tmp_path):
    """--folder 에 전각문자 세그먼트(Ａ, U+FF21) → 거부(exit 2).

    기존 코드: isalnum() 이 유니코드라 전각문자 통과.
    수정 후: isascii() 강제로 거부.
    """
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team/Ａsubdir",  # 전각 A (U+FF21)
             "--filename", "test.md",
             "--content", "테스트.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, (
        f"전각문자 folder 세그먼트가 거부되지 않았다: rc={r.returncode}, stderr={r.stderr!r}"
    )


def test_folder_segment_allows_normal_ascii(tmp_path):
    """정상 ASCII folder 세그먼트 → 여전히 허용.

    수정으로 정상 folder 가 막히면 안 된다.
    """
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "ascii-ok.md",
             "--content", "정상 content.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 0, (
        f"정상 ASCII folder 가 거부됐다: rc={r.returncode}, stderr={r.stderr!r}"
    )
