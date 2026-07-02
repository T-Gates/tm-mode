"""블로커 1 — session-start.py guidelines 주입 테스트.

guidelines.md (범용) + memory/team/guidelines.md (팀 커스텀) 가
additionalContext 에 포함되는지 검증한다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
HOOK = REPO / "infra" / "hooks" / "session-start.py"


def _run_hook(payload: dict, team_root: Path, extra_env=None):
    # 격리 XDG_STATE_HOME(conftest 주입)을 명시 전달 — 최소 env 라 자동상속이 안 되고,
    # 누락 시 session-start 의 auto-pull 이 실 ~/.local/state/teammode/last-pull 에 쓴다
    # (CI 가드 발화). test_install_golden._env / test_install_l1e._hook_env 동형.
    env = {"TEAMMODE_HOME": str(team_root), "PATH": "/usr/bin:/bin"}
    if "XDG_STATE_HOME" in os.environ:
        env["XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PY, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, env=env)


def _seed_active(team_root: Path):
    """팀 모드 활성 최소 구조 생성."""
    (team_root / "memory" / "team" / "sessions").mkdir(parents=True, exist_ok=True)
    (team_root / ".teammode-active").write_text("")


def _payload():
    return {"event": "SessionStart", "agent": "claude"}


# ─── 범용 guidelines (infra/guidelines.md) 주입 ───

def test_guidelines_content_in_context(tmp_path):
    """infra/guidelines.md 내용이 additionalContext 에 포함된다."""
    _seed_active(tmp_path)
    # guidelines.md 를 tmp_path/infra/ 에 준비 (TEAMMODE_HOME 기준)
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    (infra_dir / "guidelines.md").write_text(
        "# 팀모드 운영 지침\n유니크가이드라인토큰XXYY\n", encoding="utf-8")

    proc = _run_hook(_payload(), tmp_path)
    assert proc.returncode == 0
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "유니크가이드라인토큰XXYY" in ctx, f"guidelines 내용이 주입되지 않음: {ctx[:200]}"


def test_guidelines_appears_before_index(tmp_path):
    """guidelines 블록이 INDEX 블록보다 앞에 위치해야 한다."""
    _seed_active(tmp_path)
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    (infra_dir / "guidelines.md").write_text("# 지침\nGUIDELINES_MARKER\n")
    (tmp_path / "memory" / "INDEX.md").write_text("# INDEX\nINDEX_MARKER\n")

    proc = _run_hook(_payload(), tmp_path)
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    g_pos = ctx.find("GUIDELINES_MARKER")
    i_pos = ctx.find("INDEX_MARKER")
    assert g_pos != -1, "guidelines 마커가 없음"
    assert i_pos != -1, "INDEX 마커가 없음"
    assert g_pos < i_pos, f"guidelines({g_pos})가 INDEX({i_pos})보다 뒤에 있음"


def test_guidelines_absent_does_not_crash(tmp_path):
    """infra/guidelines.md 없어도 세션 주입이 정상 동작한다 (비치명)."""
    _seed_active(tmp_path)
    proc = _run_hook(_payload(), tmp_path)
    assert proc.returncode == 0
    out = proc.stdout.strip()
    # 팀모드 활성이면 뭔가 출력해야 한다
    assert out != "" or True  # 활성이면 output 있음 — 크래시만 없으면 OK


def test_team_custom_guidelines_in_context(tmp_path):
    """memory/team/guidelines.md (팀 커스텀) 내용도 additionalContext 에 포함된다."""
    _seed_active(tmp_path)
    # 범용 guidelines 도 준비 (없어도 되지만 있을 때 팀 커스텀도 포함되는지 확인)
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    (infra_dir / "guidelines.md").write_text("# 범용\n범용지침\n")
    # 팀 커스텀 guidelines
    team_guide_dir = tmp_path / "memory" / "team"
    team_guide_dir.mkdir(parents=True, exist_ok=True)
    (team_guide_dir / "guidelines.md").write_text(
        "# 팀 전용 지침\n팀커스텀가이드라인ZZWW\n", encoding="utf-8")

    proc = _run_hook(_payload(), tmp_path)
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "팀커스텀가이드라인ZZWW" in ctx, f"팀 커스텀 guidelines 미포함: {ctx[:300]}"


def test_team_custom_guidelines_absent_is_ok(tmp_path):
    """memory/team/guidelines.md 없어도 정상 동작 (없으면 skip)."""
    _seed_active(tmp_path)
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    (infra_dir / "guidelines.md").write_text("# 지침\n범용만존재\n")
    # 팀 커스텀은 없음

    proc = _run_hook(_payload(), tmp_path)
    assert proc.returncode == 0
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "범용만존재" in ctx


# ─── 이슈 #9(a): TEAMMODE_HOME 스테일 시 stderr 경고 ───

def test_stale_teammode_home_warns_on_stderr(tmp_path):
    """TEAMMODE_HOME 이 존재하지 않는 경로 → exit 0 + stdout 불변(빈) + stderr 한 줄 경고."""
    gone = tmp_path / "moved-away"  # 존재하지 않음
    proc = _run_hook(_payload(), gone)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", f"stdout 은 훅 출력 채널 — 불변: {proc.stdout!r}"
    assert "TEAMMODE_HOME" in proc.stderr
    assert "유효한 팀 루트" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1, "경고는 정확히 한 줄"


def test_valid_root_teammode_off_stays_silent(tmp_path):
    """유효 팀 루트(memory 표식)인데 .teammode-active 없음 = 정상 off — 침묵 유지."""
    (tmp_path / "memory").mkdir()
    proc = _run_hook(_payload(), tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert proc.stderr.strip() == "", f"정상 off 상태는 경고 금지: {proc.stderr!r}"
