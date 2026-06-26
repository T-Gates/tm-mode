"""P2 회귀 — memory delete: INDEX.md 없는 폴더에서도 커밋이 성공해야 한다.

버그: delete 가 커밋 paths 에 folder INDEX.md 를 **무조건** 포함 → 그 폴더에 INDEX.md 가
없으면(엔진 외부에서 채워진 폴더, 예: product/design/) `git add <없는 INDEX.md>` 가
pathspec 매칭 실패로 커밋 전체를 abort. 파일은 이미 unlink 된 뒤라 "삭제됐지만 커밋
안 됨" 부분 실패가 남는다.
픽스: index_path.is_file() 일 때만 스테이징.

tmp_path 격리 — 실 호스트 무접촉.
"""
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


def test_delete_in_folder_without_index_commits(tmp_path: Path):
    root = tmp_path
    _init_git(root)
    # INDEX.md 가 없는 폴더에 파일 하나 (엔진 외부에서 채워진 상황 모사)
    d = root / "memory" / "product" / "design"
    d.mkdir(parents=True)
    f = d / "guide.md"
    f.write_text("# 템플릿\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "seed"],
                   capture_output=True)
    assert not (d / "INDEX.md").exists()  # 전제: 폴더에 INDEX.md 없음

    r = _run(root, "memory", "delete",
             "--path", "memory/product/design/guide.md", "--author", "tester")

    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
    assert not f.exists(), "파일이 삭제돼야 한다"
    assert "삭제됨(커밋 안 됨)" not in r.stdout, f"부분 실패: {r.stdout!r}"
    # 삭제가 커밋됐는지: 워킹트리에 미커밋 삭제가 남으면 안 된다
    st = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                        capture_output=True, text=True)
    assert "guide.md" not in st.stdout, f"삭제가 커밋 안 됨: {st.stdout!r}"
