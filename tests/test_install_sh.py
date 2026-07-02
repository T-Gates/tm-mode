"""install.sh (curl 진입 스킨) 스모크 — pip 와 등가 경로 검증.

install.sh 는 python3·git 확인 후 cli.py(stdlib 단일파일)를 raw 로 받아 실행하는
얇은 진입점이다. 네트워크 없이 테스트하려고 TEAMMODE_CLI_URL=file://<로컬 cli.py> 로
소스를 override 한다(install.sh 가 file:// 면 curl 대신 cp 사용).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "install.sh"
CLI = REPO / "src" / "teammode" / "cli.py"

# sh·python3 가 없는 환경(희귀)에서는 스킵.
pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or shutil.which("python3") is None,
    reason="sh/python3 필요",
)


def _run(args):
    env = dict(os.environ)
    env["TEAMMODE_CLI_URL"] = f"file://{CLI}"
    return subprocess.run(
        ["sh", str(INSTALL_SH), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_install_sh_exists_and_executable():
    assert INSTALL_SH.is_file()
    assert os.access(INSTALL_SH, os.X_OK), "install.sh 실행권한 필요(chmod +x)"


def test_install_sh_help_delegates_to_cli():
    """--help → cli.py argparse usage 가 떠야 위임 파이프라인이 동작한 것."""
    r = _run(["--help"])
    assert r.returncode == 0, r.stderr
    assert "tm-mode" in r.stdout
    assert "{init,join}" in r.stdout


def test_install_sh_passes_args_to_cli():
    """인자가 cli.py 까지 전달 — 잘못된 액션은 argparse 가 거부(비정상 exit)."""
    r = _run(["bogus"])
    assert r.returncode != 0
    # 'bogus'(우리가 준 인자)가 에러에 등장 = 인자가 cli.py 까지 전달됨
    # (argparse 내부 문구 'invalid choice' 에 의존하지 않음 — 버전 견고).
    assert "bogus" in (r.stdout + r.stderr)


def test_install_sh_repeat_no_mktemp_collision():
    """연속 호출에 mktemp 충돌 없음(BSD/macOS trailing-X 회귀 가드)."""
    for _ in range(3):
        r = _run(["--help"])
        assert r.returncode == 0, r.stderr


def test_install_sh_no_temp_leak():
    """실행 후 teammode-cli.* 임시파일이 남지 않는다(trap 정리)."""
    tmpdir = Path(os.environ.get("TMPDIR", "/tmp"))
    before = set(tmpdir.glob("teammode-cli.*"))
    _run(["--help"])   # 성공 경로(exit 0)
    _run(["bogus"])    # 실패 경로(비정상 exit) — trap 은 양쪽 다 정리해야 한다
    after = set(tmpdir.glob("teammode-cli.*"))
    assert after <= before, f"임시파일 누수: {after - before}"


def test_install_sh_reconnects_dev_tty_for_wizard():
    """파이프 실행(curl … | sh)에서도 제어 tty 가 있으면 stdin 을 /dev/tty 로
    재연결해 cli.py join wizard 가 뜬다 — pip 와 등가(#6).

    실제 제어 tty 동작은 CI 에서 재현 불가 → 스크립트 내용 단언으로 잠근다.
    -e /dev/tty 는 불충분(CI 는 노드만 있고 제어 tty 없음) — 읽기 probe 여야 한다.
    """
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert "( : < /dev/tty ) 2>/dev/null" in src, \
        "/dev/tty 읽기 가능 probe 필요 (-e /dev/tty 로는 CI 오탐)"
    assert '"$TMP" "$@" < /dev/tty' in src, \
        "wizard 용 stdin 재연결 실행줄(< /dev/tty) 필요"


def test_install_sh_empty_download_rejected(tmp_path):
    """0바이트 cli.py 를 받으면 조용히 성공하지 않고 비정상 종료(#1 — 빈 응답 방어)."""
    empty = tmp_path / "empty-cli.py"
    empty.write_text("")
    env = dict(os.environ)
    env["TEAMMODE_CLI_URL"] = f"file://{empty}"
    r = subprocess.run(
        ["sh", str(INSTALL_SH), "--help"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert r.returncode == 2, f"빈 다운로드는 exit 2 여야: rc={r.returncode}"
    assert "받지 못했" in r.stderr or "빈 응답" in r.stderr
