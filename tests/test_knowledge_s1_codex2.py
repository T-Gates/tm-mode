"""S1 핫픽스 — codex 2차 재검수 2건 반영 테스트 (fault injection 기반).

항목:
  1. [major] INDEX 갱신 중 예외 시 INDEX 가 원상 유지(신규면 미생성), 본문도 롤백.
  2. [major] temp 파일 write/replace 실패 시 임시파일 잔류 0.

mock 전략: subprocess 는 patch 전달 불가 → _run_main(runpy.run_path 기반) 사용.
  - os.replace 패치: `mock.patch("os.replace")` — os 모듈은 공유되므로 동작.
  - tempfile.NamedTemporaryFile 패치: teammode.py 가 함수 내 `import tempfile` 후
    `tempfile.NamedTemporaryFile` 를 쓰므로 `mock.patch("tempfile.NamedTemporaryFile")` 동작.

모든 테스트는 tmp_path 격리 — 실 호스트·실 ~/.claude·실 memory 무접촉.
"""
import io
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"

sys.path.insert(0, str(REPO / "infra"))


# ── 공통 헬퍼 ──────────────────────────────────────────────────────────

def _run_main(root: Path, *argv) -> tuple[int, str, str]:
    """teammode.py main() 을 같은 프로세스 내에서 실행 (mock patch 유효).

    반환: (returncode, stdout_text, stderr_text)
    """
    mod = runpy.run_path(str(ENGINE), run_name="__s1_codex2_test__")
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
# 항목 1: INDEX atomic 갱신 — 예외 시 INDEX 원상 유지 + 본문 롤백
# ══════════════════════════════════════════════════════════════════════

def test_index_atomic_new_file_index_not_created_on_failure(tmp_path):
    """write: 신규 파일 + INDEX 없음 상태에서 INDEX os.replace 실패 → INDEX 미생성.

    fault injection: os.replace 를 patch 해 INDEX.md 로의 replace 만 OSError.
    본문 파일의 replace 는 성공 → 본문 write 완료 후 INDEX replace 실패.
    호출자가 INDEX 실패를 감지해 본문을 롤백(신규 파일 삭제) 해야 한다.
    INDEX.md 는 생성되지 않아야 한다 (partial/broken INDEX 없음).

    2차 codex 지적: INDEX 가 BROKEN-PARTIAL-INDEX 상태로 남지 않는지 확인.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    index_path = folder_dir / "INDEX.md"
    target_path = folder_dir / "atomic-new.md"

    assert not index_path.exists(), "사전 조건: INDEX.md 가 없어야 한다"
    assert not target_path.exists(), "사전 조건: 본문 파일이 없어야 한다"

    original_replace = os.replace

    def _patched_replace(src, dst):
        dst_path = Path(dst)
        # INDEX.md 로의 replace 만 실패
        if dst_path.name == "INDEX.md":
            raise OSError("fault injection: INDEX os.replace 실패")
        return original_replace(src, dst)

    with mock.patch("os.replace", _patched_replace):
        rc, out, err = _run_main(
            tmp_path,
            "memory", "write",
            "--folder", "team",
            "--filename", "atomic-new.md",
            "--content", "INDEX atomic 테스트.",
            "--author", "jane-doe",
            "--weight", "📎",
        )

    assert rc == 2, f"INDEX replace 실패 시 exit 2 가 아님: rc={rc}, err={err!r}"

    # INDEX.md 는 생성되지 않아야 한다
    assert not index_path.exists(), (
        f"INDEX.md 가 생성됐다 (atomic 실패해도 INDEX 가 남음): {index_path}"
    )

    # 본문 파일도 롤백(신규 → 삭제)
    assert not target_path.exists(), (
        f"본문 파일이 롤백되지 않았다 (신규 파일이 남아 있음): {target_path}"
    )

    # .idx.tmp 임시파일 잔류 없음 확인
    tmp_leftovers = list(folder_dir.glob("*.idx.tmp"))
    assert not tmp_leftovers, (
        f"INDEX 임시파일이 잔류했다: {tmp_leftovers}"
    )


def test_index_atomic_existing_index_preserved_on_failure(tmp_path):
    """write: 기존 INDEX.md 있는 상태에서 INDEX os.replace 실패 → INDEX 원본 보존.

    fault injection: os.replace 를 patch 해 INDEX.md 로의 replace 만 OSError.
    기존 INDEX.md 내용이 그대로 유지되어야 한다 (partial write 로 깨지면 안 됨).
    본문 파일도 롤백(신규 → 삭제) 되어야 한다.

    2차 codex 지적: INDEX 가 BROKEN-PARTIAL-INDEX 상태로 남지 않는지 확인.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    index_path = folder_dir / "INDEX.md"
    original_index = (
        "| 가중치 | 경로 | 내용 | 편집일 |\n"
        "|--------|------|------|--------|\n"
        "| 📎 | `memory/team/existing-entry.md` | 기존 항목. | 2026-01-01 |\n"
    )
    index_path.write_text(original_index, encoding="utf-8")

    target_path = folder_dir / "new-entry.md"
    assert not target_path.exists()

    original_replace = os.replace

    def _patched_replace(src, dst):
        dst_path = Path(dst)
        if dst_path.name == "INDEX.md":
            raise OSError("fault injection: INDEX os.replace 실패")
        return original_replace(src, dst)

    with mock.patch("os.replace", _patched_replace):
        rc, out, err = _run_main(
            tmp_path,
            "memory", "write",
            "--folder", "team",
            "--filename", "new-entry.md",
            "--content", "신규 항목 내용.",
            "--author", "jane-doe",
            "--weight", "📎",
        )

    assert rc == 2, f"INDEX replace 실패 시 exit 2 가 아님: rc={rc}, err={err!r}"

    # INDEX.md 는 원본 그대로여야 한다
    index_after = index_path.read_text(encoding="utf-8")
    assert index_after == original_index, (
        f"INDEX.md 내용이 변경됐다 (atomic 보장 실패):\n"
        f"원본:\n{original_index!r}\n현재:\n{index_after!r}"
    )

    # 본문 파일도 롤백(신규 → 삭제)
    assert not target_path.exists(), (
        f"본문 파일이 롤백되지 않았다: {target_path}"
    )

    # .idx.tmp 임시파일 잔류 없음
    tmp_leftovers = list(folder_dir.glob("*.idx.tmp"))
    assert not tmp_leftovers, (
        f"INDEX 임시파일이 잔류했다: {tmp_leftovers}"
    )


# ══════════════════════════════════════════════════════════════════════
# 항목 2: temp 파일 write/replace 실패 시 임시파일 잔류 0
# ══════════════════════════════════════════════════════════════════════

def test_temp_file_no_leak_on_write_failure(tmp_path):
    """write: NamedTemporaryFile.write() 가 실패해도 임시파일 잔류 0.

    fault injection: context manager 내 write() 를 OSError 로 patch.
    2차 codex 지적: _tmp_path 가 write() 전에 저장되지 않으면
    except/finally 에서 cleanup 을 못 찾아 임시파일이 잔류한다.
    수정 후: _tmp_path = Path(_tf.name) 이 write() 전에 실행되므로
    finally 에서 항상 unlink 가능.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    original_ntf = tempfile.NamedTemporaryFile

    class _FailingNTF:
        """파일은 생성되지만 write() 호출 시 OSError 를 던지는 mock."""
        def __init__(self, **kwargs):
            self._real = original_ntf(**kwargs)
            self.name = self._real.name

        def write(self, data):
            # 파일은 디스크에 생성됐지만 write 실패 → 누수 시나리오
            raise OSError("fault injection: temp write 실패")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            # 실제 파일 핸들 닫기(NamedTemporaryFile delete=False 이므로 unlink 는 안 함)
            try:
                self._real.__exit__(*args)
            except Exception:
                pass
            return False

    with mock.patch("tempfile.NamedTemporaryFile", _FailingNTF):
        rc, out, err = _run_main(
            tmp_path,
            "memory", "write",
            "--folder", "team",
            "--filename", "temp-leak-test.md",
            "--content", "temp 누수 테스트.",
            "--author", "jane-doe",
            "--weight", "📎",
        )

    # write 실패이므로 exit 2
    assert rc == 2, f"temp write 실패 시 exit 2 가 아님: rc={rc}, err={err!r}"

    # folder 내 .tmp 파일 잔류 없음 확인
    tmp_leftovers = list(folder_dir.glob("*.tmp"))
    assert not tmp_leftovers, (
        f"temp 파일이 잔류했다 (누수 발생): {tmp_leftovers}"
    )


def test_temp_file_no_leak_on_replace_failure(tmp_path):
    """write: os.replace() 가 실패해도 임시파일(.tmp) 잔류 0.

    fault injection: 본문 파일로의 os.replace 를 OSError 로 patch.
    _tmp_path 가 설정된 상태에서 replace 실패 → finally 에서 unlink 해야 한다.
    """
    _init_git(tmp_path)
    folder_dir = tmp_path / "memory" / "team"
    folder_dir.mkdir(parents=True, exist_ok=True)

    original_replace = os.replace

    def _patched_replace(src, dst):
        dst_path = Path(dst)
        # 본문 파일로의 replace 만 실패
        if dst_path.name == "replace-fail-test.md":
            raise OSError("fault injection: 본문 os.replace 실패")
        return original_replace(src, dst)

    with mock.patch("os.replace", _patched_replace):
        rc, out, err = _run_main(
            tmp_path,
            "memory", "write",
            "--folder", "team",
            "--filename", "replace-fail-test.md",
            "--content", "replace 실패 테스트.",
            "--author", "jane-doe",
            "--weight", "📎",
        )

    assert rc == 2, f"replace 실패 시 exit 2 가 아님: rc={rc}, err={err!r}"

    # folder 내 .tmp 파일 잔류 없음
    tmp_leftovers = list(folder_dir.glob("*.tmp"))
    assert not tmp_leftovers, (
        f"temp 파일이 잔류했다 (replace 실패 후 누수): {tmp_leftovers}"
    )
