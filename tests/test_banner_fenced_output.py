"""배너 펜스 출력 검증 — cmd_on stdout 이 코드블록(```)으로 감싸여 나오는지 확인.

요구사항:
1. cmd_on stdout에서 배너 내용은 ``` 펜스 안에(단독 줄) 들어 있어야 한다.
2. 오프닝 펜스(```)는 단독 줄이어야 한다 — 배너 마지막 줄에 붙으면 안 됨.
3. 클로징 펜스(```)도 단독 줄이어야 한다.
4. greeting 은 펜스 밖 (펜스 이후)에 나와야 한다.
5. 펜스 안에 배너 내용이 비어 있으면 안 된다.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "infra" / "teammode.py"


def _run(root: Path, verb: str, *argv):
    cmd = [sys.executable, str(ENGINE), verb, "--root", str(root),
           "--settings", str(root / ".teammode-settings.json"), *argv]
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_config(root: Path, **team_extra):
    team = {"name": "acme", "timezone": "Asia/Seoul", "locale": "ko_KR"}
    team.update(team_extra)
    cfg = {"spec_version": "0.1", "team": team, "services": {}}
    (root / "team.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def _find_fence_indices(lines: list[str]):
    """stdout 줄 목록에서 ``` 단독 줄의 인덱스를 순서대로 반환."""
    return [i for i, line in enumerate(lines) if line.rstrip("\n") == "```"]


# ── 기본 배너(banner.txt 없음) ──

def test_banner_output_is_fenced(tmp_path):
    """cmd_on stdout에 ``` 펜스가 두 개(오프닝+클로징) 존재해야 한다."""
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines(keepends=True)
    fence_indices = _find_fence_indices(lines)
    assert len(fence_indices) >= 2, (
        f"펜스(```)가 2개 미만: found={fence_indices}\nstdout:\n{r.stdout}"
    )


def test_banner_fence_lines_are_standalone(tmp_path):
    """오프닝·클로징 ``` 은 다른 문자와 같은 줄에 없어야 한다(단독 줄)."""
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    fence_indices = _find_fence_indices(r.stdout.splitlines(keepends=True))
    assert len(fence_indices) >= 2, f"펜스 부족:\n{r.stdout}"
    open_idx, close_idx = fence_indices[0], fence_indices[1]
    # 오프닝 줄: 정확히 ``` 만
    assert lines[open_idx] == "```", f"오프닝 펜스 줄에 불순물: {lines[open_idx]!r}"
    # 클로징 줄: 정확히 ``` 만
    assert lines[close_idx] == "```", f"클로징 펜스 줄에 불순물: {lines[close_idx]!r}"


def test_banner_content_inside_fence_is_nonempty(tmp_path):
    """펜스 안(오프닝~클로징 사이)에 배너 내용이 있어야 한다."""
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    fence_indices = _find_fence_indices(r.stdout.splitlines(keepends=True))
    assert len(fence_indices) >= 2, f"펜스 부족:\n{r.stdout}"
    open_idx, close_idx = fence_indices[0], fence_indices[1]
    inner = lines[open_idx + 1:close_idx]
    assert inner, f"펜스 안 내용이 비어 있음:\n{r.stdout}"
    assert any(line.strip() for line in inner), (
        f"펜스 안 내용이 공백뿐:\n{r.stdout}"
    )


def test_banner_contains_team_mode_inside_fence(tmp_path):
    """배너에 'team mode ON' 텍스트가 펜스 안에 있어야 한다(기본 fallback 배너)."""
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    fence_indices = _find_fence_indices(r.stdout.splitlines(keepends=True))
    assert len(fence_indices) >= 2
    open_idx, close_idx = fence_indices[0], fence_indices[1]
    inner_text = "\n".join(lines[open_idx + 1:close_idx])
    assert "team mode ON" in inner_text, (
        f"'team mode ON'이 펜스 안에 없음.\n안쪽 내용:\n{inner_text}\n전체:\n{r.stdout}"
    )


def test_greeting_is_outside_fence(tmp_path):
    """greeting 은 클로징 펜스 이후에 나와야 한다 — 펜스 안에 있으면 안 된다."""
    _write_config(tmp_path, greeting="GREETING_TOKEN")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    fence_indices = _find_fence_indices(r.stdout.splitlines(keepends=True))
    assert len(fence_indices) >= 2
    close_idx = fence_indices[1]
    # greeting 이 stdout 에 있어야 함
    assert "GREETING_TOKEN" in r.stdout
    # greeting 이 클로징 펜스 이후에 있어야 함
    greeting_line_idx = next(
        i for i, line in enumerate(lines) if "GREETING_TOKEN" in line
    )
    assert greeting_line_idx > close_idx, (
        f"greeting(line {greeting_line_idx})이 클로징 펜스(line {close_idx}) 안에 있음.\n"
        f"stdout:\n{r.stdout}"
    )


def test_custom_banner_also_fenced(tmp_path):
    """커스텀 banner.txt 도 펜스로 감싸져야 한다."""
    banner_dir = tmp_path / "memory"
    banner_dir.mkdir(parents=True, exist_ok=True)
    custom_banner = "CUSTOM_BANNER_LINE_ONE\nCUSTOM_BANNER_LINE_TWO\n"
    (banner_dir / "banner.txt").write_text(custom_banner, encoding="utf-8")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    fence_indices = _find_fence_indices(r.stdout.splitlines(keepends=True))
    assert len(fence_indices) >= 2, f"펜스 부족:\n{r.stdout}"
    open_idx, close_idx = fence_indices[0], fence_indices[1]
    inner_text = "\n".join(lines[open_idx + 1:close_idx])
    assert "CUSTOM_BANNER_LINE_ONE" in inner_text, (
        f"커스텀 배너가 펜스 안에 없음:\n{r.stdout}"
    )
    assert "CUSTOM_BANNER_LINE_TWO" in inner_text


def test_no_double_fence(tmp_path):
    """펜스가 4개 이상(중첩)이면 안 된다 — 엔진이 한 번만 감싸야 함."""
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    fence_indices = _find_fence_indices(r.stdout.splitlines(keepends=True))
    assert len(fence_indices) == 2, (
        f"펜스가 2개가 아님(중첩 의심): found={len(fence_indices)}\nstdout:\n{r.stdout}"
    )


# ── 엣지 케이스: embedded fence ──

def _find_any_fence_indices(lines: list[str]):
    """stdout 줄 목록에서 백틱 3개 이상으로만 이루어진 단독 줄의 인덱스를 반환."""
    import re as _re
    return [i for i, line in enumerate(lines)
            if _re.fullmatch(r"`{3,}", line.rstrip("\n"))]


def test_embedded_fence_banner_not_broken(tmp_path):
    """배너 내에 ``` 줄이 있어도 동적 펜스로 전체 배너가 한 코드블록 안에 담겨야 한다.

    고정 ``` 펜스라면 내부 ``` 에서 블록이 조기 종료돼
    그 이후 내용(AFTER_FENCE)이 펜스 밖으로 샌다.
    동적 펜스(길이 ≥ 4)라면 배너 내 ``` 과 구분되므로 AFTER_FENCE 도 펜스 안에 있어야 한다.
    """
    banner_dir = tmp_path / "memory"
    banner_dir.mkdir(parents=True, exist_ok=True)
    # 배너에 백틱 3개(```) 줄 삽입
    custom_banner = "BEFORE_FENCE\n```\nAFTER_FENCE\n"
    (banner_dir / "banner.txt").write_text(custom_banner, encoding="utf-8")

    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr

    lines = r.stdout.splitlines()
    fence_indices = _find_any_fence_indices(lines)
    assert len(fence_indices) >= 2, (
        f"펜스가 2개 미만:\nstdout:\n{r.stdout}"
    )
    open_idx, close_idx = fence_indices[0], fence_indices[-1]

    # 오프닝·클로징 펜스가 배너 내 ``` 보다 길어야 한다(동적 펜스 보장)
    open_fence = lines[open_idx]
    assert len(open_fence) > 3, (
        f"오프닝 펜스가 3자리(고정)임 — 동적 펜스 미적용: {open_fence!r}"
    )

    # BEFORE_FENCE, AFTER_FENCE 모두 펜스 안에 있어야 한다
    inner_text = "\n".join(lines[open_idx + 1:close_idx])
    assert "BEFORE_FENCE" in inner_text, (
        f"BEFORE_FENCE 가 펜스 밖:\ninner:\n{inner_text}\nstdout:\n{r.stdout}"
    )
    assert "AFTER_FENCE" in inner_text, (
        f"AFTER_FENCE 가 펜스 밖(조기 종료 버그):\ninner:\n{inner_text}\nstdout:\n{r.stdout}"
    )


def test_empty_banner_fence_structure(tmp_path):
    """banner.txt 가 빈 파일이어도 펜스 구조가 깨지지 않아야 한다.

    내용이 비어 있으면 fallback 배너로 치환되므로(엔진 §11.5),
    최소한 오프닝+클로징 펜스 2개가 정상 출력되어야 한다.
    """
    banner_dir = tmp_path / "memory"
    banner_dir.mkdir(parents=True, exist_ok=True)
    # 공백만 있는 파일 (rstrip 후 빈 문자열)
    (banner_dir / "banner.txt").write_text("   \n\n", encoding="utf-8")

    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr

    fence_indices = _find_any_fence_indices(r.stdout.splitlines())
    # 엔진이 rstrip("\n") 후 내용이 공백이어도 펜스는 있어야 한다
    assert len(fence_indices) >= 2, (
        f"빈/공백 배너일 때 펜스가 2개 미만:\nstdout:\n{r.stdout}"
    )
