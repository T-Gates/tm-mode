"""배너 출력 패턴 검증.

toolkit 패턴 채택 이후 cmd_on 은 배너를 stdout 에 찍지 않는다.
배너는 에이전트가 memory/banner.txt 를 Read 해 코드펜스로 감싸 웰컴 메시지에 포함시킨다.

ON 테스트: cmd_on stdout 에 펜스 배너가 *없어야* 한다.
OFF 테스트: cmd_off 는 변경 없이 기존대로 stdout 에 펜스 배너를 출력한다.
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


def _find_any_fence_indices(lines: list[str]):
    """stdout 줄 목록에서 백틱 3개 이상으로만 이루어진 단독 줄의 인덱스를 반환."""
    import re as _re
    return [i for i, line in enumerate(lines)
            if _re.fullmatch(r"`{3,}", line.rstrip("\n"))]


# ── ON: 배너를 stdout 에 찍지 않음 (toolkit 패턴) ──

def test_on_banner_not_in_stdout(tmp_path):
    """cmd_on stdout 에 배너 펜스가 없어야 한다.

    toolkit 패턴: 배너는 에이전트가 Read 해서 웰컴 메시지에 넣는다.
    엔진은 stdout 에 배너를 찍지 않으므로 펜스(```)가 0개여야 한다.
    """
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    fence_indices = _find_any_fence_indices(r.stdout.splitlines())
    assert len(fence_indices) == 0, (
        f"cmd_on stdout 에 배너 펜스가 있으면 안 됨(toolkit 패턴 위반):\n"
        f"found fences at lines {fence_indices}\nstdout:\n{r.stdout}"
    )


def test_on_greeting_still_in_stdout(tmp_path):
    """greeting 은 배너와 무관하게 cmd_on stdout 에 나와야 한다."""
    _write_config(tmp_path, greeting="GREETING_TOKEN")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    assert "GREETING_TOKEN" in r.stdout, (
        f"greeting 이 stdout 에 없음:\n{r.stdout}"
    )


def test_on_no_banner_even_with_custom_banner_txt(tmp_path):
    """커스텀 banner.txt 가 있어도 cmd_on 은 그것을 stdout 에 찍지 않는다.

    에이전트가 Read 하는 역할이므로 엔진은 이 파일을 무시해야 한다.
    """
    banner_dir = tmp_path / "memory"
    banner_dir.mkdir(parents=True, exist_ok=True)
    (banner_dir / "banner.txt").write_text(
        "CUSTOM_BANNER_LINE_ONE\nCUSTOM_BANNER_LINE_TWO\n", encoding="utf-8")
    r = _run(tmp_path, "on")
    assert r.returncode == 0, r.stderr
    fence_indices = _find_any_fence_indices(r.stdout.splitlines())
    assert len(fence_indices) == 0, (
        f"커스텀 banner.txt 있어도 cmd_on stdout 에 펜스 없어야 함:\n"
        f"fences at {fence_indices}\nstdout:\n{r.stdout}"
    )
    # 배너 내용 자체도 stdout 에 없어야 한다
    assert "CUSTOM_BANNER_LINE_ONE" not in r.stdout, (
        f"배너 내용이 stdout 에 노출됨(엔진이 찍은 것):\n{r.stdout}"
    )


def test_cmd_off_no_on_in_fresh_env(tmp_path):
    """cmd_off 는 banner.txt 없는 fresh 환경에서 출력에 'ON'이 없어야 한다(회귀 방지).

    OFF인데 배너에 'ON'이 뜨는 모순을 잡는다.
    fallback 배너가 `=== <팀> ===`(중립)로 고정됐으므로 'ON' 문자열이 없어야 한다.
    """
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    assert "ON" not in r.stdout, (
        f"OFF 시 출력에 'ON'이 포함돼 있음(모순):\n{r.stdout}"
    )


# ── OFF: 배너를 stdout 에 찍지 않음 (toolkit 패턴, ON 과 동일) ──

def test_off_banner_not_in_stdout(tmp_path):
    """cmd_off stdout 에 배너 펜스가 없어야 한다(toolkit 패턴).

    배너는 에이전트가 banner.txt 를 Read 해 farewell 앞에 출력한다.
    """
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    fence_indices = _find_any_fence_indices(r.stdout.splitlines())
    assert len(fence_indices) == 0, (
        f"cmd_off stdout 에 배너 펜스가 있으면 안 됨(toolkit 패턴 위반):\n"
        f"found fences at lines {fence_indices}\nstdout:\n{r.stdout}"
    )


def test_off_farewell_in_stdout_without_banner(tmp_path):
    """farewell 은 cmd_off stdout 에 나오되 배너 펜스는 없어야 한다."""
    _write_config(tmp_path, farewell="FAREWELL_TOKEN")
    r = _run(tmp_path, "off")
    assert r.returncode == 0, r.stderr
    assert "FAREWELL_TOKEN" in r.stdout, f"farewell 토큰이 stdout 에 없음:\n{r.stdout}"
    fence_indices = _find_any_fence_indices(r.stdout.splitlines())
    assert len(fence_indices) == 0, (
        f"OFF — 배너 펜스가 stdout 에 있으면 안 됨:\nfences at {fence_indices}\n{r.stdout}"
    )
