"""공개 레포 위생 가드 — 실환경 식별자 유입 차단 (2026-07-07 전수 익명화 재발 방지).

배경: 공개 전 감사에서 테스트 fixture·문서에 실제 멤버명·홈경로·팀 인스턴스
레포 URL 이 다수 발견돼 전수 익명화했다. 이 가드는 그 재발을 CI 에서 기계적으로
막는다. ⚠️ 실명 자체를 denylist 로 두면 가드 파일이 곧 재누출이므로, 여기서는
**일반 패턴**(절대 홈경로·이메일·비제품 org 레포)만 잡는다. 이름 규칙은
CONTRIBUTING.md "픽스처 어휘" 절이 단일 소스다: 사람=alice/bob/jane-doe/
jonathan/jonathon(편집거리 쌍), 팀/org=acme/Acme/ACME, 레포=acme-team.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SELF = "tests/test_no_identity_leaks.py"

# 제품이 소유한 org 레포(유일하게 허용되는 T-Gates 참조)
PRODUCT_REPO = "T-Gates/tm-mode"

_PATTERNS = [
    # 하드코딩 macOS/리눅스 홈경로 — fixture 는 tmp_path·~ 표기·/Users/alice(가드 예외 아님,
    # 아래 allowlist 로 한정)만. 실사용자 홈이 박히는 사고의 공통 시그니처.
    ("absolute-home-path",
     re.compile(r"/(?:Users|home)/(?!alice\b|bob\b|me\b|user\b)[a-z][a-z0-9._-]+/")),
    # 이메일 — 플레이스홀더 도메인(example.*/test.com/acme.com/한글자.com)과
    # SSH 계정 표기(git@…), 시나리오용 evil.com 만 허용
    ("email", re.compile(
        r"\b(?!git@|ssh@|me@gmail\.com)[a-zA-Z0-9._%+-]+@"
        r"(?!example\.[a-z]+|test\.com|acme\.com|evil\.com|[a-z]\.com"
        r"|(?:[a-zA-Z0-9-]+\.)?users\.noreply\.github\.com)"
        r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}\b")),
    # 제품 org 의 비제품 레포(팀 인스턴스 등) 참조
    ("non-product-org-repo", re.compile(r"T-Gates/(?!tm-mode\b)[A-Za-z0-9._-]+")),
]


def _tracked_text_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    keep = []
    for f in out.splitlines():
        if f == SELF:
            continue  # 가드 자신(패턴 정의) 제외
        if f.endswith((".png", ".jpg", ".gif", ".ico", ".pdf", ".woff", ".woff2")):
            continue
        keep.append(f)
    return keep


def test_no_real_identities_in_tracked_files():
    hits = []
    for f in _tracked_text_files():
        try:
            text = (REPO / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for name, pat in _PATTERNS:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{f}:{line}: [{name}] {m.group(0)[:60]}")
    assert not hits, (
        "실환경 식별자 의심 패턴 발견 — fixture 는 중립값만(alice/bob/acme, tmp_path, "
        "user@example.com). CONTRIBUTING.md '공개 위생' 절 참조:\n" + "\n".join(hits))
