"""Phase 2 게이트4 — 패키징 계약 (RELEASE-v1 2a·2e·2g).

배포 채널의 기계 검증 가능한 부분만 오프라인으로 고정한다:
- 2a 버전 단일소스: pyproject 는 dynamic version, 소스는 __init__.__version__ 하나.
- 2e wheel LICENSE: license-files 지정(hatchling 이 dist 에 동봉).
- 2g 발행 워크플로: v* 태그 트리거 + Trusted Publishing(OIDC) 계약.
실빌드·실발행은 CI/사람 몫 — 여기선 설정 파일 계약만 잠근다.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _pyproject():
    return (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_version_single_source_dynamic():
    t = _pyproject()
    assert re.search(r'^dynamic\s*=\s*\[\s*"version"\s*\]', t, re.M), \
        "pyproject [project] 에 dynamic version 없음(2a)"
    assert not re.search(r'^version\s*=', t, re.M), \
        "pyproject 에 수기 version 잔존 — 단일소스 위반(2a)"
    assert re.search(
        r'\[tool\.hatch\.version\]\s*\npath\s*=\s*"src/teammode/__init__\.py"', t), \
        "hatch version source 미지정(2a)"


def test_version_source_parsable():
    init = (REPO / "src" / "teammode" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', init, re.M)
    assert m, "__version__ 파싱 불가 — hatch regex 계약(2a)"


def test_wheel_includes_license():
    t = _pyproject()
    assert re.search(r'^license-files\s*=\s*\[\s*"LICENSE"\s*\]', t, re.M), \
        "license-files 미지정 — wheel LICENSE 누락(2e)"


def test_publish_workflow_contract():
    wf = REPO / ".github" / "workflows" / "publish.yml"
    assert wf.is_file(), "publish.yml 없음(2g)"
    t = wf.read_text(encoding="utf-8")
    assert re.search(r"tags:\s*\n\s*- +['\"]?v\*", t), "v* 태그 트리거 아님(2g)"
    assert "pypa/gh-action-pypi-publish" in t, "Trusted Publishing 액션 아님(2g)"
    assert "id-token: write" in t, "OIDC 권한(id-token) 누락(2g)"
    assert "password" not in t and "PYPI_TOKEN" not in t, \
        "시크릿 토큰 방식 금지 — Trusted Publishing 계약(2g)"
