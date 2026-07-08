"""session-start.py — 엔진 업데이트(upstream NOTICE.md) 알림 + 캐시 최신화 테스트.

배경: `tm on`을 계속 켜둔 인스턴스는 auto_update_on_start(cmd_on 전용 경로)를
다시 타지 않아 엔진이 upstream 보다 뒤처져도 아무도 알려주지 않는 갭이 있었다.
session-start 훅이 세션 시작마다 로컬 NOTICE.md 와 upstream/main 의 NOTICE.md 를
비교해 다르면 `tm-mode update` 안내를 한 줄 추가한다.

⚠️ 적대검수 발견 — 치명 설계 결함(최초 버전): 그 비교 자체는 로컬 git 오브젝트만
읽고(fetch 안 함) 그 자체는 의도된 설계였지만, **그 캐시를 최신화하는 유일한 경로
(sync_from_upstream)가 cmd_on/cmd_update 안에만 있어서**, 계속 켜둔 인스턴스는
upstream/main 캐시가 최초 fetch 시점에 영원히 멈추고, 알림이 그 이후의 새 upstream
변화를 영원히 못 봤다 — 정확히 이 알림이 존재하는 이유인 시나리오에서 알림이
못 울렸다. 수정: session-start 가 세션 시작마다(스로틀 적용, 기본 24h) upstream 을
`git_ops.fetch_upstream` 로 짧게 fetch 만(merge·checkout 없음) 새로 고친다.

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
    # XDG_STATE_HOME 은 conftest._isolate_pull_state(autouse, 테스트마다 새 tmp)가
    # 이미 os.environ 에 격리 경로를 심어둔다 — 여기서는 그 값을 subprocess 로
    # 상속만 시킨다(같은 테스트 안에서 훅을 두 번 부르면 두 호출이 같은 state 파일을
    # 공유해야 스로틀을 검증할 수 있다 — 그래서 새로 만들지 않고 상속만 한다).
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


def _make_upstream(tmp_path: Path, notice_text: str) -> Path:
    """bare upstream 레포 하나 생성 + NOTICE.md 커밋·push. 반환: bare repo 경로."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    (seed / "NOTICE.md").write_text(notice_text, encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "notice")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")
    return upstream


def _push_new_notice(tmp_path: Path, upstream: Path, notice_text: str) -> None:
    """이미 만들어진 bare upstream 에 NOTICE.md 를 새 내용으로 갱신·push(추가 커밋)."""
    seed2 = tmp_path / "seed2"
    _git(tmp_path, "clone", str(upstream), str(seed2))
    _git(seed2, "config", "user.name", "t")
    _git(seed2, "config", "user.email", "t@t")
    (seed2 / "NOTICE.md").write_text(notice_text, encoding="utf-8")
    _git(seed2, "add", ".")
    _git(seed2, "commit", "-m", "notice v2")
    _git(seed2, "push", "origin", "main")


@pytest.fixture()
def team_with_upstream_not_yet_fetched(tmp_path):
    """upstream(bare, 새 NOTICE) + team(로컬, 옛 NOTICE) — **아직 fetch 안 함**.

    이전 버전은 픽스처가 미리 fetch 를 해뒀지만, 그러면 "훅 스스로 첫 세션에 fetch
    해서 캐시를 채우는지"를 증명하지 못한다(치명 결함 수정의 핵심 주장). 여기서는
    upstream remote 만 등록하고 fetch 는 훅(첫 실행, 스로틀 state 없음)에 맡긴다.
    """
    upstream = _make_upstream(tmp_path, "# teammode\n\n## 2026-07-08\n- 새 upstream 업데이트\n")
    team = tmp_path / "team"
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
    # fetch 안 함 — 훅이 첫 실행에서 스스로 fetch 해야 알림이 나온다.
    return team, upstream


def test_first_session_fetches_upstream_and_shows_notice(team_with_upstream_not_yet_fetched):
    """upstream/main 이 로컬에 전혀 없어도(첫 세션), 훅 스스로 fetch 해서 알림이 나온다.

    이게 이번 수정의 핵심 증거 — 이전 버전은 이 시나리오에서 영원히 안내가 안 나왔다
    (캐시가 없거나 옛날 그대로라서). 이제는 세션 시작마다(스로틀 미도달 시) fetch 를
    스스로 하므로, fetch 를 한 번도 안 한 팀도 첫 세션부터 정상 동작한다.
    """
    team, _upstream = team_with_upstream_not_yet_fetched
    proc = _run_hook(team)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "엔진 업데이트가 upstream" in ctx, (
        f"첫 세션에 훅이 upstream 을 fetch 하지 못한 것으로 보임: {ctx[:300]}")


def test_notice_same_shows_nothing(tmp_path):
    """로컬과 upstream 의 NOTICE.md 가 같으면 안내가 없다(도배 방지). 첫 세션 fetch 포함."""
    same_text = "# teammode\n\n## 2026-06-17\n- 동일 상태\n"
    upstream = _make_upstream(tmp_path, same_text)
    team = tmp_path / "team"
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

    proc = _run_hook(team)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "엔진 업데이트가 upstream" not in ctx, f"동일한데 안내가 나타남: {ctx[:300]}"


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
    assert "엔진 업데이트가 upstream" not in ctx


def test_fetch_failure_does_not_block_session(tmp_path):
    """upstream 원격이 존재하지만 접근 불가(bad URL)여도 세션은 절대 안 막힌다.

    첫 세션(스로틀 state 없음) → 훅이 fetch 를 시도하지만 원격이 없는 경로라 실패 →
    fetch_upstream 이 무raise 로 흡수하고, 알림 비교는 캐시가 없으니 조용히 생략.
    """
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
    _git(team, "remote", "add", "upstream", "/nonexistent/path/does-not-exist.git")

    proc = _run_hook(team)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "엔진 업데이트가 upstream" not in ctx


def test_second_call_within_throttle_does_not_refetch(team_with_upstream_not_yet_fetched):
    """스로틀 창 안의 두 번째 호출은 다시 fetch 하지 않는다 — v2 가 안 보여야 한다.

    1회차: 훅이 처음 fetch → upstream/main 이 v1 로 캐시되고, 스로틀 state 파일이
    "방금 시도함"으로 기록된다. 그 직후 upstream 에 v2 를 push 한다. 2회차(같은
    스로틀 창 안, 기본 24h)는 다시 fetch 하면 안 되므로, 로컬 upstream/main 캐시는
    여전히 v1 이어야 한다 — `git show upstream/main:NOTICE.md` 로 직접 확인해
    "안내가 없다"보다 더 강한 증거(캐시 내용 자체)를 남긴다.
    """
    team, upstream = team_with_upstream_not_yet_fetched

    proc1 = _run_hook(team)
    assert proc1.returncode == 0, proc1.stderr

    v1 = _git(team, "show", "upstream/main:NOTICE.md").stdout

    # upstream 에 v2 를 push — 팀은 아직 이걸 모른다(스로틀 안이라 재fetch 안 함).
    _push_new_notice(team.parent, upstream,
                     "# teammode\n\n## 2026-07-09\n- v2 업데이트(2회차엔 안 보여야 함)\n")

    proc2 = _run_hook(team)
    assert proc2.returncode == 0, proc2.stderr

    v1_after = _git(team, "show", "upstream/main:NOTICE.md").stdout
    assert v1_after == v1, (
        "2회차 호출이 스로틀 창 안인데도 upstream/main 캐시가 바뀜 — 다시 fetch 한 "
        "것으로 보임(스로틀 미작동)")
    assert "v2 업데이트" not in v1_after
