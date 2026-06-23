#!/usr/bin/env python3
"""글리프 타일 생성기 (dev-only, 빌드타임 1회) — figlet 폰트의 글자별 ASCII 아트를
JSON으로 추출해 런타임(의존성 0)에서 stitch 렌더할 수 있게 한다.

왜: pip 런처/엔진은 stdlib-only(pyfiglet 런타임 의존 금지). 그러나 창립자 init때
임의 팀명을 ASCII 배너로 렌더하려면 figlet이 필요 → 빌드타임에 글자별 글리프만 뽑아
JSON으로 박아두면, 런타임은 글자 블록을 가로로 이어붙이기만 하면 된다(banner_render.py).

ansi_shadow 는 smushing 이 없어 per-char 렌더 후 stitch = native figlet 출력과 동일함을
실측 확인(2026-06-23). 다른 폰트 추가 시 stitch 동일성 먼저 검증할 것.

실행 (pyfiglet 런타임 의존 회피 — uv 로 일시 설치):
    uv run --with pyfiglet python3 infra/banners/glyphs/_generate_glyphs.py
재생성 대상 폰트는 FONTS 리스트로 관리. 출력: 같은 폴더의 <font>.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pyfiglet  # dev-only — uv run --with pyfiglet

# 팀명에 쓰일 수 있는 인쇄가능 ASCII 전체(0x20~0x7E). 비-ASCII(한글 등)는 런타임 폴백.
CHARSET = [chr(c) for c in range(0x20, 0x7F)]
FONTS = ["ansi_shadow"]


def build_font(font: str) -> dict:
    fig = pyfiglet.Figlet(font=font)
    raw_glyphs: dict[str, list[str]] = {}
    for ch in CHARSET:
        lines = fig.renderText(ch).rstrip("\n").split("\n")
        # 공백뿐인 꼬리줄 제거(글자별 높이 편차 정규화 전 단계)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:  # 공백문자 등 — 최소 1줄 보장(폭 유지)
            lines = [fig.renderText(ch).split("\n")[0]]
        raw_glyphs[ch] = lines

    height = max(len(g) for g in raw_glyphs.values())
    glyphs: dict[str, list[str]] = {}
    for ch, lines in raw_glyphs.items():
        # 높이를 height 로 통일(아래쪽 패딩), 각 글자 내부는 같은 폭으로 우측 패딩
        lines = lines + [""] * (height - len(lines))
        width = max((len(r) for r in lines), default=0)
        glyphs[ch] = [r.ljust(width) for r in lines]
    return {"font": font, "height": height, "glyphs": glyphs}


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    for font in FONTS:
        data = build_font(font)
        out_path = out_dir / f"{font}.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        print(f"wrote {out_path}  (height={data['height']}, chars={len(data['glyphs'])})")


if __name__ == "__main__":
    main()
