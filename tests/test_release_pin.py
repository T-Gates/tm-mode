"""Phase 2 게이트1 — 설치 아티팩트 태그 핀 계약 (RELEASE-v1 2b·2c).

install.sh 가 받는 cli.py, 문서·도움말의 curl 원라이너가 전부 릴리스 태그에
핀돼야 재현 가능한 설치가 된다(main 추적 = 잘못 머지 순간 신규 설치 전멸).
핀 버전은 __version__ 과 일치해야 릴리스 루틴에서 어긋나지 않는다.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _version():
    init = (REPO / "src" / "teammode" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M).group(1)


def test_install_sh_cli_url_pinned_to_version_tag():
    t = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r'CLI_URL="\$\{TEAMMODE_CLI_URL:-([^}]+)\}"', t)
    assert m, "CLI_URL 기본값 파싱 불가"
    url = m.group(1)
    assert "/main/" not in url, f"CLI_URL 이 main 추적: {url} (2b)"
    assert f"/refs/tags/v{_version()}/" in url, \
        f"CLI_URL 핀({url})이 __version__({_version()})과 불일치"


def test_no_main_tracking_oneliners_in_user_docs():
    """사용자용 설치 원라이너에 raw main 잔존 금지(README·INSTALL·cli 도움말·install.sh 헤더)."""
    for rel in ("README.md", "INSTALL.md", "install.sh", "src/teammode/cli.py"):
        t = (REPO / rel).read_text(encoding="utf-8")
        for line in t.splitlines():
            if "raw.githubusercontent.com" in line and "install.sh" in line:
                assert "/main/" not in line, f"{rel}: main 추적 원라이너 잔존 — {line.strip()}"


def test_template_repo_configurable():
    """TEMPLATE_REPO: env TM_TEMPLATE_REPO 로 override 가능(포크 배포)."""
    t = (REPO / "src" / "teammode" / "cli.py").read_text(encoding="utf-8")
    assert re.search(r'TEMPLATE_REPO\s*=\s*os\.environ\.get\(\s*"TM_TEMPLATE_REPO"', t), \
        "TEMPLATE_REPO 하드코딩 — env 설정가능화 필요(2b)"


def test_cli_pin_ref_matches_version():
    """cli.py 는 단독 실행 파일(패키지 import 불가) — 자체 PIN_REF 가 __version__ 과 일치."""
    t = (REPO / "src" / "teammode" / "cli.py").read_text(encoding="utf-8")
    m = re.search(r'^PIN_REF\s*=\s*"refs/tags/v([^"]+)"', t, re.M)
    assert m, "cli.py PIN_REF 없음"
    assert m.group(1) == _version(), f"cli.py 핀({m.group(1)}) ≠ __version__({_version()})"
