"""배너 picker 정합성 테스트 — infra/banners/ 6종 존재·비어있지 않음 검증.

빌드타임에 pyfiglet 로 렌더된 정적 .txt 파일이 레포에 커밋됐는지,
그리고 picker 운영에 필요한 6종이 모두 있는지 확인한다.
런타임 외부 의존성 0 원칙 유지: 이 테스트는 pyfiglet 을 import 하지 않는다.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BANNERS_DIR = REPO / "infra" / "banners"

EXPECTED_FONTS = {"ansi_shadow", "slant", "chunky", "cyberlarge", "larry3d", "speed"}


def test_banners_dir_exists():
    assert BANNERS_DIR.is_dir(), (
        f"infra/banners/ 디렉토리가 없습니다. "
        f"배너 picker 기능이 정상 동작하려면 이 디렉토리가 있어야 합니다."
    )


def test_all_six_banner_files_exist():
    missing = []
    for font in sorted(EXPECTED_FONTS):
        p = BANNERS_DIR / f"{font}.txt"
        if not p.is_file():
            missing.append(font)
    assert not missing, (
        f"다음 폰트의 배너 파일이 없습니다: {missing}. "
        f"빌드타임 pyfiglet 렌더 산출물을 infra/banners/<font>.txt 로 커밋하세요."
    )


def test_all_banner_files_non_empty():
    empty = []
    for font in sorted(EXPECTED_FONTS):
        p = BANNERS_DIR / f"{font}.txt"
        if p.is_file() and p.stat().st_size == 0:
            empty.append(font)
    assert not empty, (
        f"다음 배너 파일이 비어 있습니다: {empty}. "
        f"렌더 산출물이 올바르게 생성됐는지 확인하세요."
    )


def test_banner_files_contain_team_mode_text():
    """배너 파일에 TEAM 또는 MODE 관련 문자(ASCII 아트 특성상 원본 텍스트를 직접 포함하지
    않을 수 있으므로, 파일이 비어있지 않고 유의미한 내용을 가지는지만 확인한다)."""
    for font in sorted(EXPECTED_FONTS):
        p = BANNERS_DIR / f"{font}.txt"
        if not p.is_file():
            continue
        content = p.read_text(encoding="utf-8")
        # ASCII 아트는 원래 글자를 직접 포함하지 않으므로 충분한 길이가 있는지만 본다.
        assert len(content.strip()) > 10, (
            f"infra/banners/{font}.txt 내용이 너무 짧습니다 — 렌더가 제대로 됐는지 확인하세요."
        )


def test_no_extra_unexpected_files():
    """infra/banners/ 에 예상치 않은 .txt 파일이 없는지 확인(관리 범위 명확화)."""
    if not BANNERS_DIR.is_dir():
        return
    actual = {p.stem for p in BANNERS_DIR.glob("*.txt")}
    extra = actual - EXPECTED_FONTS
    assert not extra, (
        f"infra/banners/ 에 정의되지 않은 배너 파일이 있습니다: {extra}. "
        f"EXPECTED_FONTS 목록에 추가하거나 파일을 제거하세요."
    )
