"""P1 핫픽스 — knowledge 동사 입력 견고성 테스트 (RED→GREEN TDD).

항목:
  1. 파일 I/O 미처리 예외 → exit 2 + 친화 메시지 (OSError/PermissionError)
  2. author/filename isascii() 강제 — 한글 author·topic 거부
  3. content 제어문자 거부(개행·탭 제외)

모든 테스트는 tmp_path 격리 — 실 호스트 무접촉.
"""
import io
import os
import runpy
import stat
import subprocess
import sys
from pathlib import Path

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
    mod = runpy.run_path(str(ENGINE), run_name="__p1_hotfix_test__")
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


# ══════════════════════════════════════════════════════════════════════
# 항목 2: author/filename isascii() 강제 — 한글 거부
# ══════════════════════════════════════════════════════════════════════

def test_knowledge_write_rejects_korean_author(tmp_path):
    """한글 author(예: '햄버거') → 거부(exit 2). isascii() 강제."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "x.md",
             "--content", "테스트.",
             "--author", "햄버거",
             "--weight", "📎")
    assert r.returncode == 2, (
        f"한글 author '햄버거' 가 거부되지 않았다: rc={r.returncode}, stdout={r.stdout!r}"
    )


def test_knowledge_write_rejects_unicode_author(tmp_path):
    """비ASCII author(예: 'über') → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "x.md",
             "--content", "테스트.",
             "--author", "über",
             "--weight", "📎")
    assert r.returncode == 2, (
        f"비ASCII author 'über' 가 거부되지 않았다: rc={r.returncode}"
    )


def test_knowledge_write_rejects_korean_filename(tmp_path):
    """한글 filename(topic, 예: '한글주제.md') → 거부(exit 2). isascii() 강제."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "한글주제.md",
             "--content", "테스트.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, (
        f"한글 filename '한글주제.md' 가 거부되지 않았다: rc={r.returncode}, stdout={r.stdout!r}"
    )


def test_log_rejects_korean_author(tmp_path):
    """log 동사도 한글 author → 거부(exit 2). _validate_author 공유."""
    r = _run(tmp_path, "log",
             "--author", "햄버거",
             "--text", "테스트",
             "--now", "2026-06-18T10:00:00+09:00")
    assert r.returncode == 2, (
        f"log 한글 author '햄버거' 가 거부되지 않았다: rc={r.returncode}"
    )


def test_ascii_author_still_accepted(tmp_path):
    """정상 ASCII author('jane-doe', 'alice-dev', 'bob123') 는 여전히 통과."""
    for author in ("jane-doe", "alice-dev", "bob123"):
        r = _run(tmp_path, "knowledge", "write",
                 "--folder", "team",
                 "--filename", f"{author}-test.md",
                 "--content", "정상 author 테스트.",
                 "--author", author,
                 "--weight", "📎")
        assert r.returncode == 0, (
            f"정상 ASCII author '{author}' 가 거부됐다: rc={r.returncode}, "
            f"stderr={r.stderr!r}"
        )


# ══════════════════════════════════════════════════════════════════════
# 항목 1: 파일 I/O 예외 → exit 2 + 친화 메시지 (트레이스백 아님)
# ══════════════════════════════════════════════════════════════════════

def test_knowledge_write_long_filename_exit2(tmp_path):
    """긴 파일명(255자↑) → OSError → exit 2 + 친화 메시지 (트레이스백 아님)."""
    # 255바이트 초과 파일명 (Linux ext4 등의 한계: 255바이트)
    # 260 + ".md" = 264자 → OSError 발생
    long_name = "a" * 260 + ".md"
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", long_name,
             "--content", "테스트.",
             "--author", "jane-doe",
             "--weight", "📎")
    # exit 2: I/O 예외는 입력검증 실패와 같은 코드(친화 메시지 규약)
    assert r.returncode == 2, (
        f"긴 파일명이 exit 2 를 내지 않았다: rc={r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    # 트레이스백이 없어야 한다
    assert "Traceback" not in r.stderr, f"트레이스백이 노출됐다: {r.stderr!r}"
    assert "Traceback" not in r.stdout, f"stdout 에 트레이스백이 있다: {r.stdout!r}"


def test_knowledge_write_permission_error_exit2(tmp_path):
    """INDEX 갱신 실패(os.replace OSError) → exit 2 + 친화 메시지(Traceback 없음).

    원래는 INDEX.md chmod(444) 로 write_text PermissionError 를 유발했으나,
    atomic write(temp+os.replace) 도입으로 Linux 에서 부모 디렉토리 쓰기 권한만
    있으면 read-only 파일도 덮어쓸 수 있어 chmod(444) 방식이 동작하지 않음.
    → fault injection: os.replace 를 patch 해 INDEX replace 만 OSError 로 대체.
    보장: INDEX 갱신 실패 시 exit 2 + Traceback 미노출.
    """
    import io as _io
    import runpy as _runpy
    from unittest import mock as _mock

    root = tmp_path / "root"
    root.mkdir()
    _init_git(root)

    # 정상 write 로 INDEX.md 생성
    _run(root, "knowledge", "write",
         "--folder", "team",
         "--filename", "seed.md",
         "--content", "시드.",
         "--author", "jane-doe",
         "--weight", "📎")

    index_path = root / "memory" / "team" / "INDEX.md"
    assert index_path.is_file(), "사전 조건: INDEX.md 가 생성됐어야 한다"

    # os.replace 를 patch 해 INDEX.md 로의 replace 만 OSError
    original_replace = os.replace

    def _patched_replace(src, dst):
        dst_path = Path(dst)
        if dst_path.name == "INDEX.md":
            raise OSError("fault injection: INDEX os.replace 권한 오류")
        return original_replace(src, dst)

    mod = _runpy.run_path(str(ENGINE), run_name="__p1_perm_test__")
    args = ["knowledge", "write",
            "--folder", "team",
            "--filename", "another.md",
            "--content", "또 다른 내용.",
            "--author", "jane-doe",
            "--weight", "📌",
            "--root", str(root)]
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = _io.StringIO()
    sys.stderr = _io.StringIO()
    try:
        with _mock.patch("os.replace", _patched_replace):
            try:
                rc = mod["main"](args)
            except SystemExit as e:
                rc = int(e.code) if e.code is not None else 0
    finally:
        out = sys.stdout.getvalue()
        err = sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_stdout, old_stderr

    assert rc == 2, (
        f"PermissionError 가 exit 2 를 내지 않았다: rc={rc}\n"
        f"stdout={out!r}\nstderr={err!r}"
    )
    assert "Traceback" not in err, f"트레이스백이 노출됐다: {err!r}"
    assert "Traceback" not in out, f"stdout 에 트레이스백이 있다: {out!r}"


@pytest.mark.skipif(os.getuid() == 0, reason="root 는 chmod 무시 → 테스트 불가")
def test_knowledge_delete_permission_error_exit2(tmp_path):
    """지식 파일 권한 제거(PermissionError) → delete 시 exit 2 + 친화 메시지."""
    root = tmp_path / "root"
    root.mkdir()
    _init_git(root)

    # 파일 생성
    _run(root, "knowledge", "write",
         "--folder", "team",
         "--filename", "perm-test.md",
         "--content", "삭제 권한 테스트.",
         "--author", "jane-doe",
         "--weight", "📎")

    target = root / "memory" / "team" / "perm-test.md"
    assert target.is_file()

    # 파일이 있는 디렉토리 쓰기 권한 제거 → unlink PermissionError 유도
    parent_dir = root / "memory" / "team"
    parent_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x: 쓰기 불가

    try:
        r = _run(root, "knowledge", "delete",
                 "--path", "team/perm-test.md",
                 "--author", "jane-doe")
    finally:
        parent_dir.chmod(
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
            stat.S_IRGRP | stat.S_IXGRP |
            stat.S_IROTH | stat.S_IXOTH
        )

    assert r.returncode == 2, (
        f"delete PermissionError 가 exit 2 를 내지 않았다: rc={r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "Traceback" not in r.stderr, f"트레이스백이 노출됐다: {r.stderr!r}"


# ══════════════════════════════════════════════════════════════════════
# 항목 3: content 제어문자 거부 (개행·탭 제외)
# ══════════════════════════════════════════════════════════════════════

def test_knowledge_write_rejects_null_byte_in_content(tmp_path):
    """content 에 NUL 바이트(\x00) → 거부(exit 2).

    NUL 바이트는 subprocess argv 통과가 OS 레벨에서 불가하므로 main() 직접 호출.
    """
    rc, out, err = _run_main(
        tmp_path,
        "knowledge", "write",
        "--folder", "team",
        "--filename", "ctrl-test.md",
        "--content", "정상 텍스트\x00악성 삽입",
        "--author", "jane-doe",
        "--weight", "📎",
    )
    assert rc == 2, (
        f"NUL 바이트 content 가 거부되지 않았다: rc={rc}, out={out!r}, err={err!r}"
    )


def test_knowledge_write_rejects_escape_in_content(tmp_path):
    """content 에 ESC(\x1b) → 거부(exit 2). 터미널 제어 시퀀스 삽입 차단."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "esc-test.md",
             "--content", "정상 텍스트\x1b[31mred",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, (
        f"ESC 제어문자 content 가 거부되지 않았다: rc={r.returncode}"
    )


def test_knowledge_write_rejects_bell_in_content(tmp_path):
    """content 에 BEL(\x07) → 거부(exit 2)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "bell-test.md",
             "--content", "정상\x07벨소리",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 2, (
        f"BEL 제어문자 content 가 거부되지 않았다: rc={r.returncode}"
    )


def test_knowledge_write_allows_newline_and_tab_in_content(tmp_path):
    """content 에 개행(\n)·탭(\t)은 허용 — 문서 포맷에 필수."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "newline-tab-test.md",
             "--content", "첫 줄\n둘째 줄\n\t들여쓰기",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 0, (
        f"개행·탭이 거부됐다(허용돼야 함): rc={r.returncode}, stderr={r.stderr!r}"
    )


def test_knowledge_write_allows_korean_in_content(tmp_path):
    """content 에 한글·유니코드는 허용 (제어문자만 거부)."""
    r = _run(tmp_path, "knowledge", "write",
             "--folder", "team",
             "--filename", "korean-content.md",
             "--content", "한글 내용이 있는 지식. 🔥 이모지도 포함.",
             "--author", "jane-doe",
             "--weight", "📎")
    assert r.returncode == 0, (
        f"한글·이모지 content 가 거부됐다(허용돼야 함): rc={r.returncode}, "
        f"stderr={r.stderr!r}"
    )
