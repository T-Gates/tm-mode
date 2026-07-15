"""슬라이스 U — 세션 시작 레포 최신화 (throttled auto-pull) 테스트.

현재 스펙/설계:
  - SessionStart 훅이 세션당 1회 팀 레포를 정합하며 스로틀로 과부하를 막는다.
  - UserPromptSubmit 훅은 네트워크 작업 없이 세션로그 리마인더만 수행한다.
  - **실패는 절대 작업을 막지 않는다(철칙)**: 네트워크 없음·ff 불가·충돌·타임아웃·
    git 아님 → 조용히 통과, 예외 전파 0.

순수 함수(테스트 가능):
  should_pull(state_path, now, throttle_seconds) -> bool   스로틀 판정
  do_pull(team_root, ...) -> PullResult                    git pull --ff-only 실행
  auto_pull(team_root, state_path, now, throttle_seconds)   조립 (절대 raise 안 함)

모든 시각·경로·스로틀초는 인자 주입(P1 교훈: env 무조건 신뢰 금지).
네트워크는 /tmp 로컬 fake git remote 로 모사 — 실 toolkit·실 ~/.claude 무접촉.
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


# ── should_pull: 스로틀 판정 (시각 주입) ──

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


# ── do_pull: 실제 ff-only pull ──

def test_do_pull_fast_forwards(cloned_repo):
    before = _git(cloned_repo.clone, "rev-parse", "HEAD").stdout.strip()
    res = ap.do_pull(str(cloned_repo.clone))
    after = _git(cloned_repo.clone, "rev-parse", "HEAD").stdout.strip()
    assert res.ok is True
    assert before != after  # fast-forward 됨
    assert (cloned_repo.clone / "b.txt").exists()


def test_do_pull_uses_ff_only(cloned_repo):
    """로컬에 분기 커밋 생성 → ff 불가 → merge 안 하고 실패(워킹트리 무오염)."""
    (cloned_repo.clone / "local.txt").write_text("local\n")
    _git(cloned_repo.clone, "add", ".")
    _git(cloned_repo.clone, "commit", "-m", "local-divergent")
    local_head = _git(cloned_repo.clone, "rev-parse", "HEAD").stdout.strip()

    res = ap.do_pull(str(cloned_repo.clone))
    assert res.ok is False  # ff 불가 → 실패
    # ff-only 라서 merge 커밋이 생기지 않음 — HEAD 그대로
    assert _git(cloned_repo.clone, "rev-parse", "HEAD").stdout.strip() == local_head
    # b.txt(upstream 변경)는 머지되지 않음
    assert not (cloned_repo.clone / "b.txt").exists()


def test_do_pull_on_non_git_dir_is_not_ok(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    res = ap.do_pull(str(plain))
    assert res.ok is False  # git 레포 아님 → 실패, 예외 없음


def test_do_pull_blocks_credential_prompt(tmp_path):
    """없는 원격 → 자격증명 프롬프트 없이 즉시 실패(hang 금지)."""
    repo = tmp_path / "r"
    _git(tmp_path, "init", str(repo))
    (repo / "x").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c")
    # 인증을 요구하는 가짜 https 원격
    _git(repo, "remote", "add", "origin",
         "https://invalid.invalid/nope.git")
    res = ap.do_pull(str(repo), timeout=10)
    assert res.ok is False  # hang 없이 실패


# ── auto_pull: 조립 + 실패 무해 철칙 ──

def test_auto_pull_pulls_and_records_time(cloned_repo, tmp_path):
    state = tmp_path / "last-pull"
    res = ap.auto_pull(str(cloned_repo.clone), str(state),
                       now=5000.0, throttle_seconds=300)
    assert res.attempted is True
    assert (cloned_repo.clone / "b.txt").exists()
    # 성공 시 상태 파일에 now 기록
    assert abs(float(state.read_text().strip()) - 5000.0) < 1e-6


def test_auto_pull_skips_within_throttle(cloned_repo, tmp_path):
    state = tmp_path / "last-pull"
    state.write_text("5000.0")
    res = ap.auto_pull(str(cloned_repo.clone), str(state),
                       now=5100.0, throttle_seconds=300)  # 100s < 300
    assert res.attempted is False
    # pull 안 함 → upstream 변경 미반영
    assert not (cloned_repo.clone / "b.txt").exists()


def test_auto_pull_never_raises_on_non_git(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    state = tmp_path / "last-pull"
    # 예외 0 — 작업 절대 차단 금지
    res = ap.auto_pull(str(plain), str(state), now=1.0, throttle_seconds=300)
    assert res.ok is False
    assert res.attempted is True


def test_auto_pull_never_raises_on_missing_dir(tmp_path):
    state = tmp_path / "last-pull"
    res = ap.auto_pull(str(tmp_path / "does-not-exist"), str(state),
                       now=1.0, throttle_seconds=300)
    assert res.ok is False  # 예외 전파 없이 조용히 실패


def test_auto_pull_records_time_on_failure_to_throttle_retries(cloned_repo, tmp_path):
    """pull 실패해도 **시도** 시각을 기록한다 — 원격 장애 시 다음 세션 시작이
    곧바로 재시도하지 않게(throttle 창당 1회만). '실패 무해' 철칙의 핵심 보강.
    """
    # ff 불가 상태 만들기
    (cloned_repo.clone / "local.txt").write_text("local\n")
    _git(cloned_repo.clone, "add", ".")
    _git(cloned_repo.clone, "commit", "-m", "divergent")
    state = tmp_path / "last-pull"
    res = ap.auto_pull(str(cloned_repo.clone), str(state),
                       now=5000.0, throttle_seconds=300)
    assert res.ok is False
    assert state.exists()  # 시도 기록됨
    # 다음 SessionStart(throttle 안)는 재시도하지 않는다(작업 세금 방지)
    assert ap.should_pull(str(state), now=5100.0, throttle_seconds=300) is False


def test_auto_pull_corrupt_state_does_not_raise(cloned_repo, tmp_path):
    state = tmp_path / "last-pull"
    state.write_text("not-a-number")
    res = ap.auto_pull(str(cloned_repo.clone), str(state),
                       now=5000.0, throttle_seconds=300)
    # 깨진 상태여도 예외 없이 동작(보수적으로 pull 시도)
    assert res.attempted is True


def test_auto_pull_unwritable_state_dir_does_not_raise(cloned_repo, tmp_path):
    """상태 파일 기록 실패(권한 등)해도 작업을 막지 않는다."""
    # 존재하지 않는 깊은 경로의 부모를 파일로 막아 mkdir 실패 유도
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    state = blocker / "sub" / "last-pull"  # blocker 가 파일이라 mkdir 불가
    res = ap.auto_pull(str(cloned_repo.clone), str(state),
                       now=5000.0, throttle_seconds=300)
    # pull 자체는 성공하되, 상태 기록 실패가 예외로 새지 않음
    assert res.attempted is True
    assert (cloned_repo.clone / "b.txt").exists()


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


# ── session-start: 세션당 1회 pull(auto_pull 재사용·throttle) ──

def test_start_pulls_when_team_active(cloned_repo, tmp_path):
    """팀 모드 활성 + 1 behind → SessionStart 훅이 ff-pull 을 수행한다."""
    (cloned_repo.clone / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = _run_start(cloned_repo.clone, state_dir)
    assert proc.returncode == 0
    assert (cloned_repo.clone / "b.txt").exists()  # 최신화됨
    assert (state_dir / "teammode" / "last-pull").exists()  # 시각 기록됨


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


def test_start_throttles_rapid_restart(cloned_repo, tmp_path):
    """첫 SessionStart pull 성공 → 상태 기록 → throttle 창 안 둘째 호출은 pull 스킵."""
    (cloned_repo.clone / ".teammode-active").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    p1 = _run_start(cloned_repo.clone, state_dir)
    assert p1.returncode == 0
    assert (state_dir / "teammode" / "last-pull").exists()
    # upstream 에 또 새 커밋
    (cloned_repo.work / "c.txt").write_text("v3\n")
    _git(cloned_repo.work, "add", ".")
    _git(cloned_repo.work, "commit", "-m", "c3")
    _git(cloned_repo.work, "push", "origin", "main")
    p2 = _run_start(cloned_repo.clone, state_dir)
    assert p2.returncode == 0
    # throttle(기본 300s) 안이라 둘째 pull 안 함 → c.txt 미반영(재시작 폭주 가드)
    assert not (cloned_repo.clone / "c.txt").exists()
