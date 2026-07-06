"""팀명 → ASCII 배너 글리프 렌더 + --team-name 배선 검증.

slice: init 위저드 팀명 → team.name·배너 단일소스. render_name_banner 는 빌드타임에
뽑아둔 infra/banners/glyphs/<font>.json 을 런타임(의존성 0)에 가로 stitch 한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


def _load_engine():
    """infra/teammode.py 를 고유 이름으로 파일 직접 로드(teammode 스텁 오염 회피).

    test_cli_join_wizard.py 가 sys.modules['teammode'] 를 pip 런처 스텁으로 덮으므로
    `import teammode` 는 collection 순서에 따라 엔진이 아닐 수 있다.
    """
    spec = importlib.util.spec_from_file_location(
        "teammode_engine_nb", str(REPO / "infra" / "teammode.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tm = _load_engine()


# ── render_name_banner ──

def test_render_name_banner_ascii_produces_rectangular_art():
    art = tm.render_name_banner(REPO, "ACME")
    assert art is not None
    lines = art.split("\n")
    assert len(lines) == 6  # ansi_shadow height
    assert "█" in art
    # stitch 결과는 직사각형(모든 줄 길이 동일)이어야 한다
    assert len({len(line) for line in lines}) == 1


def test_render_name_banner_non_ascii_returns_none():
    assert tm.render_name_banner(REPO, "티게이츠") is None


def test_render_name_banner_missing_glyphs_returns_none(tmp_path):
    # 글리프 JSON 없는 루트 → None(폴백)
    assert tm.render_name_banner(tmp_path, "ACME") is None


def test_render_name_banner_empty_returns_none():
    assert tm.render_name_banner(REPO, "   ") is None


# ── default_banner_content (단일소스) ──

def test_default_banner_content_uses_name_art():
    art = tm.render_name_banner(REPO, "ACME")
    content = tm.default_banner_content(REPO, "ACME")
    assert content.startswith(art)
    assert "팀색 입히기" in content


def test_default_banner_content_non_ascii_falls_back_plain(tmp_path):
    # 글리프·고정아트 둘 다 없는 루트 + 비-ASCII → === name === 폴백
    assert tm.default_banner_content(tmp_path, "팀") == "=== 팀 ===\n"


# ── --team-name 배선 (parse_args) ──

def test_parse_args_reads_team_name():
    opts = il.parse_args(["--root", "/x", "--team-name", "ACME"])
    assert opts.team_name == "ACME"


def test_parse_args_team_name_absent_is_none():
    assert il.parse_args(["--root", "/x"]).team_name is None
