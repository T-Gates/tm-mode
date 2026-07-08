"""session-start.py — 엔진 업데이트(upstream NOTICE.md) 알림 테스트.

배경: `tm on`을 계속 켜둔 인스턴스는 auto_update_on_start(cmd_on 전용 경로)를
다시 타지 않아 엔진이 upstream 보다 뒤처져도 아무도 알려주지 않는 갭이 있었다.
session-start 훅이 세션 시작마다 로컬 NOTICE.md 와 upstream/main 의 NOTICE.md 를
비교해(둘 다 read-only — 후자는 `git show`로 로컬 git 오브젝트만 조회, fetch 아님)
다르면 `tm-mode update` 안내를 한 줄 추가한다.

네트워크는 /tmp 로컬 fake remote 로 모사 — 실 toolkit·실 ~/.claude 무접촉.
(tests/test_notice.py 의 upstream_with_notice 픽스처와 동형 패턴)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
HOOK = REPO / "infra" / "hooks" / "session-start.py"


def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


def _run_hook(team_root: Path):
    env = {"TEAMMODE_HOME": str(team_root), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if "XDG_STATE_HOME" in os.environ:
        env["XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    payload = {"event": "SessionStart", "agent": "claude"}
    return subprocess.run(
        [PY, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, env=env)


def _seed_active(team_root: Path, notice_text: str | None):
    """팀 모드 활성 최소 구조 + (옵션) 로컬 NOTICE.md."""
    (team_root / "memory" / "team" / "sessions").mkdir(parents=True, exist_ok=True)
    (team_root / ".teammode-active").write_text("")
    (team_root / "team.config.json").write_text(
        json.dumps({"team": {"name": "t", "locale": "ko_KR"}}), encoding="utf-8")
    if notice_text is not None:
        (team_root / "NOTICE.md").write_text(notice_text, encoding="utf-8")


@pytest.fixture()
def team_with_stale_notice(tmp_path):
    """upstream(bare, 새 NOTICE) + team(로컬, 옛 NOTICE, upstream fetch 완료).

    fetch 는 픽스처 셋업 단계에서 1회만 수행 — 훅 실행 시점엔 이미 로컬에 캐시된
    upstream/main 오브젝트만 있다(훅이 새로 fetch 하면 이 테스트의 전제와 무관하게
    통과해버릴 수 있어, 별도 테스트(test_hook_makes_no_new_fetch)에서 upstream 을
    fetch 후 접근 불가로 만들어 "새 fetch 없음"을 직접 검증한다).
    """
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "NOTICE.md").write_text(
        "# teammode\n\n## 2026-07-08\n- 새 upstream 업데이트\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "new notice")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main", check=False)
    _seed_active(team, "# teammode\n\n## 2026-06-17\n- 옛 로컬 상태\n")
    (team / "README.md").write_text("team\n")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    _git(team, "remote", "add", "upstream", str(upstream))
    _git(team, "fetch", "upstream")  # 훅 실행 "전에" 1회 — 훅은 이 캐시만 읽는다

    return team


def test_notice_differs_shows_update_available(team_with_stale_notice):
    """로컬과 upstream 의 NOTICE.md 가 다르면 안내가 additionalContext 에 나타난다."""
    proc = _run_hook(team_with_stale_notice)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "tm-mode update" in ctx, f"엔진 업데이트 안내 없음: {ctx[:300]}"


def test_notice_same_shows_nothing(tmp_path):
    """로컬과 upstream 의 NOTICE.md 가 같으면 안내가 없다(도배 방지)."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"
    same_text = "# teammode\n\n## 2026-06-17\n- 동일 상태\n"

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "NOTICE.md").write_text(same_text, encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "notice")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main", check=False)
    _seed_active(team, same_text)
    (team / "README.md").write_text("team\n")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    _git(team, "remote", "add", "upstream", str(upstream))
    _git(team, "fetch", "upstream")

    proc = _run_hook(team)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "tm-mode update" not in ctx, f"동일한데 안내가 나타남: {ctx[:300]}"


def test_no_upstream_remote_does_not_crash(tmp_path):
    """upstream remote 자체가 없어도(등록 전 신규 클론 등) 크래시 없이 조용히 생략."""
    team = tmp_path / "team"
    team.mkdir()
    _git(team, "init")
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "checkout", "-b", "main", check=False)
    _seed_active(team, "# teammode\n\n## 2026-06-17\n- 로컬\n")
    (team / "README.md").write_text("team\n")
    _git(team, "add", ".")
    _git(team, "commit", "-m", "team init")
    # upstream remote 등록 안 함

    proc = _run_hook(team)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "tm-mode update" not in ctx


def test_hook_makes_no_new_fetch(team_with_stale_notice):
    """훅 실행 시점에 upstream 원격이 이미 접근 불가여도(fetch 를 안 하므로) 정상 동작.

    픽스처가 이미 fetch 를 마쳤으므로, 그 뒤 origin(bare repo) 경로를 없애 훅이
    "새로 fetch"를 시도한다면 실패/크래시하거나 안내가 사라질 것이다. 훅이 로컬
    캐시(git show)만 쓴다면 결과가 fetch 전과 동일해야 한다 — 이게 "새 네트워크
    호출 없음"의 실행 가능한 증거다.
    """
    # upstream remote url 을 존재하지 않는 경로로 바꿔 "네트워크 불가"를 모사.
    _git(team_with_stale_notice, "remote", "set-url", "upstream",
         "/nonexistent/path/does-not-exist.git")

    proc = _run_hook(team_with_stale_notice)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "tm-mode update" in ctx, (
        "upstream 원격이 접근 불가한데도 안내가 사라짐 — 훅이 새로 fetch 를 "
        f"시도했다가 실패한 것으로 보임(로컬 캐시만 써야 함): {ctx[:300]}")
