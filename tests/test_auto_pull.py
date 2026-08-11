"""SessionStart main sync and product-upstream throttle predicate tests.

현재 계약:
  - SessionStart는 origin/main을 매번 즉시 동기화하며 origin throttle 상태를 만들지 않는다.
  - ``should_pull``은 별도 제품 upstream fetch의 24시간 throttle에만 쓰인다.
  - UserPromptSubmit 훅은 네트워크 작업 없이 세션로그 리마인더만 수행한다.
  - 실패는 작업을 막지 않는다. 네트워크는 tmp local bare remote로 모사한다.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra" / "hooks"))

import auto_pull as ap  # noqa: E402


# ── 로컬 git 헬퍼 (네트워크 0, /tmp 격리) ──

def _git(cwd, *args, check=True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, env=env, check=check)


@pytest.fixture
def cloned_repo(tmp_path):
    """upstream(bare) + clone. upstream 에 새 커밋 → clone 은 1 behind 상태로 만든다."""
    upstream = tmp_path / "upstream.git"
    work = tmp_path / "work"        # upstream 에 푸시하기 위한 작업본
    clone = tmp_path / "clone"      # 우리가 pull 할 대상(팀 루트 흉내)

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "clone", str(upstream), str(work))
    (work / "a.txt").write_text("v1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c1")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "checkout", "main")

    # upstream 에 새 커밋 (clone 은 이제 1 behind)
    (work / "b.txt").write_text("v2\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "c2")
    _git(work, "push", "origin", "main")

    class C:
        pass
    c = C()
    c.upstream, c.work, c.clone = upstream, work, clone
    return c


# ── should_pull: 제품 upstream fetch 스로틀 판정 ──

def test_should_pull_true_when_no_state_file(tmp_path):
    state = tmp_path / "last-pull"
    assert ap.should_pull(str(state), now=1000.0, throttle_seconds=300) is True


def test_should_pull_false_within_throttle(tmp_path):
    state = tmp_path / "last-pull"
    state.write_text("1000.0")
    # 마지막 pull 1000, now 1200 → 200s 경과 < 300 스로틀 → skip
    assert ap.should_pull(str(state), now=1200.0, throttle_seconds=300) is False


def test_should_pull_true_after_throttle_elapsed(tmp_path):
    state = tmp_path / "last-pull"
    state.write_text("1000.0")
    # 1000 → 1400 = 400s ≥ 300 → pull
    assert ap.should_pull(str(state), now=1400.0, throttle_seconds=300) is True


def test_should_pull_true_on_corrupt_state(tmp_path):
    state = tmp_path / "last-pull"
    state.write_text("garbage-not-a-float")
    # 깨진 상태 파일 → 보수적으로 pull 허용(스로틀 모름 = 막지 않음)
    assert ap.should_pull(str(state), now=1400.0, throttle_seconds=300) is True


# ── 훅 통합 (2026-06-17 P0 hook hang 수정 후) ──────────────────────────────
#
# 의도 변경: 레포 최신화는 "상시(매 프롬프트, UserPromptSubmit)"에서 "세션 시작 1회
# (SessionStart)"로 옮겼다. UserPromptSubmit 동기 블로킹 훅의 매 프롬프트 git pull 이
# hang 시 작업을 막던 트리거였기 때문. 따라서:
#   - session-log-remind.py(UserPromptSubmit): pull 안 함, 리마인더만.
#   - session-start.py(SessionStart): 세션당 1회 pull(auto_pull 모듈 재사용·throttle).

REMIND_HOOK = REPO / "infra" / "hooks" / "session-log-remind.py"
START_HOOK = REPO / "infra" / "hooks" / "session-start.py"


def _run_remind(team_root, state_dir, prompt="hi"):
    """session-log-remind(UserPromptSubmit)를 정규 JSON stdin 으로 호출(격리 env)."""
    import json
    env = {
        **os.environ,
        "TEAMMODE_HOME": str(team_root),
        "XDG_STATE_HOME": str(state_dir),
        "TMPDIR": str(state_dir),
        "GIT_TERMINAL_PROMPT": "0",
    }
    canonical = {"event": "UserPromptSubmit", "prompt": prompt, "agent": "claude"}
    return subprocess.run(
        [sys.executable, str(REMIND_HOOK)], input=json.dumps(canonical),
        capture_output=True, text=True, env=env, cwd=str(team_root))


def _run_start(team_root, state_dir):
    """session-start(SessionStart)를 정규 JSON stdin 으로 호출(격리 env)."""
    import json
    env = {
        **os.environ,
        "TEAMMODE_HOME": str(team_root),
        "XDG_STATE_HOME": str(state_dir),
        "TMPDIR": str(state_dir),
        "GIT_TERMINAL_PROMPT": "0",
    }
    canonical = {"event": "SessionStart", "agent": "claude"}
    return subprocess.run(
        [sys.executable, str(START_HOOK)], input=json.dumps(canonical),
        capture_output=True, text=True, env=env, cwd=str(team_root))


# ── session-log-remind: pull 분리(매 프롬프트 pull 금지), 리마인더는 유지 ──

def test_remind_does_NOT_pull_when_team_active(cloned_repo, tmp_path):
    """핵심 회귀: UserPromptSubmit 훅은 더 이상 pull 하지 않는다(매 프롬프트 hang 트리거 제거)."""
    (cloned_repo.clone / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = _run_remind(cloned_repo.clone, state_dir)
    assert proc.returncode == 0
    # 1 behind 인데도 pull 안 함 → b.txt 미반영(상시 최신화 제거 확인)
    assert not (cloned_repo.clone / "b.txt").exists()
    # pull 상태 파일도 안 만든다(pull 시도 자체가 없음)
    assert not (state_dir / "teammode" / "last-pull").exists()


def test_remind_still_reminds_when_team_active(tmp_path):
    """pull 을 떼도 리마인더 로직은 그대로 동작한다(세션로그 전무 → 발화).

    출력은 normalize가 재전달하는 JSON이며 locale과 무관하게 두 안내 채널이 채워진다.
    """
    import json
    team = tmp_path / "team"
    (team / "memory" / "team" / "sessions").mkdir(parents=True)
    (team / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # team.config.json 없어 폴백 경로(degraded). issue #26: 폴백도 멤버 경로와 대칭으로
    # check_reset 한다 — 첫 호출은 date=""→오늘로 바뀌어 warm-up 리셋(미발화)되므로,
    # 상태파일을 date=오늘로 선시드해 warm-up 을 건너뛰고 age≥1800 발화를 검증한다.
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _kst = _dt.now(_tz(_td(hours=9)))
    _today = (_kst - _td(days=1)).strftime("%Y-%m-%d") if _kst.hour < 6 \
        else _kst.strftime("%Y-%m-%d")
    (state_dir / "teammode-remind-state-claude.json").write_text(json.dumps({
        "count": 0, "last_mtime": 0.0, "date": _today, "last_strong_remind": 0.0,
    }))
    proc = _run_remind(team, state_dir)
    assert proc.returncode == 0  # 작업 절대 차단 금지
    # 세션로그 전무 → age ≥ 1800 → 발화(JSON 출력)
    assert proc.stdout.strip() != "", "세션로그 전무인데 리마인드 미발화"
    payload = json.loads(proc.stdout)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert hook_output["additionalContext"].strip()
    assert payload["systemMessage"].strip()


def test_remind_inactive_no_remind(cloned_repo, tmp_path):
    """.teammode-active 없으면 무동작(exit 0, 출력 없음)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = _run_remind(cloned_repo.clone, state_dir)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ── session-start: origin/main 즉시 동기화, throttle 없음 ──

def test_start_pulls_when_team_active(cloned_repo, tmp_path):
    """팀 모드 활성 + 1 behind → SessionStart가 main을 즉시 동기화한다."""
    (cloned_repo.clone / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = _run_start(cloned_repo.clone, state_dir)
    assert proc.returncode == 0
    assert (cloned_repo.clone / "b.txt").exists()  # 최신화됨
    assert not (state_dir / "teammode" / "last-pull").exists()


def test_start_does_not_pull_when_team_inactive(cloned_repo, tmp_path):
    """.teammode-active 없으면 pull 도 주입도 안 함(exit 0)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = _run_start(cloned_repo.clone, state_dir)
    assert proc.returncode == 0
    assert not (cloned_repo.clone / "b.txt").exists()


def test_start_never_blocks_on_pull_failure(tmp_path):
    """팀 루트가 git 레포 아니어도(=pull 실패) SessionStart 훅은 exit 0 + 맥락 주입."""
    import json
    team = tmp_path / "team"
    (team / "memory" / "team" / "sessions").mkdir(parents=True)
    (team / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = _run_start(team, state_dir)
    assert proc.returncode == 0  # 세션 절대 차단 금지
    out = json.loads(proc.stdout)
    assert "additionalContext" in out["hookSpecificOutput"]


def test_start_does_not_throttle_rapid_restart(cloned_repo, tmp_path):
    """연속 SessionStart도 새 origin/main 변경을 즉시 반영한다."""
    (cloned_repo.clone / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    p1 = _run_start(cloned_repo.clone, state_dir)
    assert p1.returncode == 0
    assert not (state_dir / "teammode" / "last-pull").exists()
    # upstream 에 또 새 커밋
    (cloned_repo.work / "c.txt").write_text("v3\n")
    _git(cloned_repo.work, "add", ".")
    _git(cloned_repo.work, "commit", "-m", "c3")
    _git(cloned_repo.work, "push", "origin", "main")
    p2 = _run_start(cloned_repo.clone, state_dir)
    assert p2.returncode == 0
    assert (cloned_repo.clone / "c.txt").exists()
    assert not (state_dir / "teammode" / "last-pull").exists()
