"""#45 — async push: pending ledger + plain push + worker drain 테스트.

확정 스펙(이슈 #45 + plain-push-only 정정):
  - auto-commit 은 do_commit(push=False) 까지만 동기 → 커밋 성공 시 XDG pending 원자 기록
    + push-worker detach kick.
  - push-worker: per-team lock 단일 실행, drain loop(최대 3), **plain push only** —
    로컬 히스토리 무접촉(rebase 복구 없음 — index.lock 경합으로 편집 커밋 유실 방지).
    non-ff 는 복구 없이 sync-warning 마커만(정합은 session-start reconcile 에 위임).
    no-upstream 만 `push -u origin HEAD` 1회.
  - pending clear 는 push 성공 + ahead==0 확인 후에만(push 중 새 커밋 유실 방지).
  - 성공 = pending·sync-warning clear / 실패 = sync-warning detail.

모든 테스트는 tmp_path + XDG_STATE_HOME 격리 — 실 호스트 무접촉.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import git_ops  # noqa: E402


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    """XDG_STATE_HOME 격리 — 실 ~/.local/state 무접촉."""
    state = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def _init_repo(path: Path, *, bare: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    args = ["git", "init", "-q"] + (["--bare"] if bare else []) + [str(path)]
    subprocess.run(args, check=True, capture_output=True)
    if not bare:
        subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                       capture_output=True)
    return path


def _clone_pair(tmp_path) -> tuple:
    """bare origin + 작업 클론 (upstream tracking 설정 완료)."""
    origin = _init_repo(tmp_path / "origin.git", bare=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "T"],
                   capture_output=True)
    (work / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-qu", "origin", "HEAD"],
                   capture_output=True)
    return origin, work


def _commit_file(repo: Path, name: str, content: str = "x") -> None:
    (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"add {name}"],
                   capture_output=True)


# ── pending ledger ──────────────────────────────────────────────────

def test_pending_ledger_roundtrip(xdg, tmp_path):
    """write → read(truthy) → clear(멱등) — 팀별 파일, XDG 하위."""
    root = str(tmp_path / "team")
    assert git_ops.read_push_pending(root) == ""
    git_ops.write_push_pending(root)
    assert git_ops.read_push_pending(root) != ""
    p = Path(git_ops.push_pending_path(root))
    assert p.is_file() and str(xdg) in str(p)
    git_ops.clear_push_pending(root)
    assert git_ops.read_push_pending(root) == ""
    git_ops.clear_push_pending(root)  # 멱등 — 예외 없음


def test_pending_ledger_is_per_team(xdg, tmp_path):
    """팀 A 의 clear 가 팀 B 마커를 건드리지 않는다(sync-warning 과 동일 규약)."""
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    git_ops.write_push_pending(a)
    git_ops.write_push_pending(b)
    git_ops.clear_push_pending(a)
    assert git_ops.read_push_pending(a) == ""
    assert git_ops.read_push_pending(b) != ""


def test_pending_age_seconds(xdg, tmp_path):
    """age: 없으면 None, 있으면 0 이상 float — UserPromptSubmit 경량검사용."""
    root = str(tmp_path / "team")
    assert git_ops.push_pending_age_seconds(root) is None
    git_ops.write_push_pending(root)
    age = git_ops.push_pending_age_seconds(root)
    assert isinstance(age, float) and age >= 0.0


# ── push_plain (plain-push-only) ────────────────────────────────────

def test_push_plain_success(xdg, tmp_path):
    """로컬 ahead 1 → plain push 성공."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail
    ahead, behind = git_ops.ahead_behind(str(work))
    assert ahead == 0


def test_push_plain_non_ff_no_recovery(xdg, tmp_path):
    """non-ff: 복구(rebase/fetch) 없이 pushed=False + 'non-fast-forward' 분류.

    로컬 히스토리 무접촉이 계약 — worker 가 rebase 를 하면 index.lock 경합으로
    사용자 편집 커밋이 조용히 유실될 수 있다(#45 정정의 근거).
    """
    origin, work = _clone_pair(tmp_path)
    # 다른 클론이 먼저 push → work 는 non-ff
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "O"],
                   capture_output=True)
    _commit_file(other, "theirs.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"], capture_output=True)

    _commit_file(work, "mine.md")
    head_before = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is False
    assert "non-fast-forward" in detail
    # 로컬 히스토리 무접촉(HEAD 불변 — rebase 안 함)
    head_after = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    assert head_after == head_before


def test_push_plain_no_upstream_sets_u_once(xdg, tmp_path):
    """upstream 미설정 브랜치: `push -u origin HEAD` 1회로 성공."""
    origin, work = _clone_pair(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "-qb", "feat/x"],
                   capture_output=True)
    _commit_file(work, "b.md")
    pushed, detail = git_ops.push_plain(str(work))
    assert pushed is True, detail


# ── push-worker (drain loop · plain-push-only) ──────────────────────

WORKER = REPO / "infra" / "hooks" / "push-worker.py"


def _run_worker(root: Path, env_extra: dict | None = None):
    env = os.environ.copy()
    env_extra = env_extra or {}
    env.update(env_extra)
    return subprocess.run([sys.executable, str(WORKER), "--root", str(root)],
                          capture_output=True, text=True, env=env, timeout=60)


def test_worker_pushes_and_clears_pending(xdg, tmp_path, monkeypatch):
    """pending 존재 + ahead 1 → push 성공 → pending·sync-warning clear."""
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    git_ops.write_push_pending(str(work))
    git_ops.write_sync_warning(str(work), "이전 실패 잔재")
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0, r.stderr
    assert git_ops.read_push_pending(str(work)) == ""
    assert git_ops.read_sync_warning(str(work)) == ""
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 0


def test_worker_no_pending_is_noop(xdg, tmp_path):
    """pending 없으면 아무것도 안 하고 조용히 종료(push 시도 없음)."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")  # ahead 1 이지만 pending 없음
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 1  # push 하지 않았다 — pending 이 유일한 트리거


def test_worker_non_ff_keeps_pending_writes_marker(xdg, tmp_path):
    """non-ff: 복구 없이 sync-warning 기록, pending 유지(정합은 세션 시작에 위임)."""
    origin, work = _clone_pair(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "O"],
                   capture_output=True)
    _commit_file(other, "theirs.md")
    subprocess.run(["git", "-C", str(other), "push", "-q"], capture_output=True)

    _commit_file(work, "mine.md")
    git_ops.write_push_pending(str(work))
    head_before = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0  # 실패도 비치명 종료
    assert git_ops.read_push_pending(str(work)) != "", "non-ff 인데 pending 을 지웠다"
    assert "non-fast-forward" in git_ops.read_sync_warning(str(work))
    head_after = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    assert head_after == head_before, "worker 가 로컬 히스토리를 건드렸다(계약 위반)"


def test_worker_drains_new_pending_written_during_push(xdg, tmp_path):
    """drain: push 성공 후 ahead 가 남아 있으면(새 커밋) 이어서 push — 잔여 0 까지."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    _commit_file(work, "b.md")  # ahead 2 — plain push 한 번에 다 나가긴 하지만
    git_ops.write_push_pending(str(work))
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 0
    assert git_ops.read_push_pending(str(work)) == ""


def test_worker_lock_single_instance(xdg, tmp_path):
    """lock 파일이 살아 있으면 두 번째 worker 는 즉시 조용히 종료(중복 push 방지)."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    git_ops.write_push_pending(str(work))
    # lock 선점 재현 — worker 와 같은 경로 규약으로 직접 생성
    lock = Path(git_ops.push_pending_path(str(work)) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("live", encoding="utf-8")
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg)})
    assert r.returncode == 0
    # lock 이 살아 있어 push 하지 않았고 pending 유지
    assert git_ops.read_push_pending(str(work)) != ""
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 1


# ── auto-commit 재배선 (동기=커밋까지, push=worker 위임) ─────────────

AUTO_COMMIT = REPO / "infra" / "hooks" / "auto-commit.py"


def _run_auto_commit(root: Path, files: list, xdg: Path, extra_env: dict | None = None):
    import json as _json
    env = os.environ.copy()
    env["TEAMMODE_HOME"] = str(root)
    env["XDG_STATE_HOME"] = str(xdg)
    env.update(extra_env or {})
    payload = _json.dumps({"event": "PostToolUse", "action": "file_edit",
                           "files": [str(f) for f in files]})
    return subprocess.run([sys.executable, str(AUTO_COMMIT)],
                          input=payload, capture_output=True, text=True,
                          env=env, timeout=30)


def _activate(root: Path) -> None:
    (root / ".teammode-active").write_text("on", encoding="utf-8")


def test_auto_commit_writes_pending_and_commits_sync(xdg, tmp_path):
    """커밋은 동기 완료 + pending 기록. push 는 훅 안에서 기다리지 않는다.

    (worker kick 은 detach 라 race — 이 테스트는 '커밋 즉시성+ledger'만 고정하고
    worker 무력화(TEAMMODE_DISABLE_PUSH_WORKER=1)로 push 를 관찰가능하게 남긴다.)
    """
    _, work = _clone_pair(tmp_path)
    _activate(work)
    f = work / "memory-note.md"
    f.write_text("메모", encoding="utf-8")
    r = _run_auto_commit(work, [f], xdg,
                         {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0, r.stderr
    # 커밋은 동기 완료
    log = subprocess.run(["git", "-C", str(work), "log", "--oneline", "-1"],
                         capture_output=True, text=True).stdout
    assert "auto-commit" in log
    # push 는 훅이 직접 하지 않음(worker 몫) → ahead 1 + pending 기록
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 1
    assert git_ops.read_push_pending(str(work)) != ""


def test_auto_commit_leftover_pending_warns_stderr(xdg, tmp_path):
    """시작 시 잔존 pending 이 있으면 stderr 1줄('한 편집 늦은' 즉시 가시화)."""
    _, work = _clone_pair(tmp_path)
    _activate(work)
    git_ops.write_push_pending(str(work))
    f = work / "note2.md"
    f.write_text("x", encoding="utf-8")
    r = _run_auto_commit(work, [f], xdg,
                         {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0
    assert "push" in r.stderr and "pending" in r.stderr.lower() or "미완" in r.stderr


def test_auto_commit_kicks_worker_end_to_end(xdg, tmp_path):
    """실제 detach kick: 훅 종료 후 worker 가 push 를 완료(폴링 최대 15s)."""
    import time as _t
    _, work = _clone_pair(tmp_path)
    _activate(work)
    f = work / "note3.md"
    f.write_text("x", encoding="utf-8")
    r = _run_auto_commit(work, [f], xdg)
    assert r.returncode == 0, r.stderr
    deadline = _t.time() + 15
    while _t.time() < deadline:
        ahead, _ = git_ops.ahead_behind(str(work))
        if ahead == 0 and git_ops.read_push_pending(str(work)) == "":
            break
        _t.sleep(0.3)
    ahead, _ = git_ops.ahead_behind(str(work))
    assert ahead == 0, "worker 가 push 를 완료하지 못했다"
    assert git_ops.read_push_pending(str(work)) == ""


# ── session-start pending recovery (#45 가시화 3중의 ①) ─────────────

SESSION_START = REPO / "infra" / "hooks" / "session-start.py"


class _FakeGo:
    """recovery 판정 경로만 검증하는 fake git_ops."""

    def __init__(self, pending: str, ahead: int, has_upstream: bool):
        self._pending = pending
        self._ahead = ahead
        self._has = has_upstream
        self.kicked = 0
        self.cleared = 0
        self.DEFAULT_TIMEOUT = 2

    def read_push_pending(self, root):
        return self._pending

    def _ahead_behind_raw(self, root, timeout):
        return (self._ahead, 0, self._has)

    def kick_push_worker(self, root, worker):
        self.kicked += 1
        return True

    def clear_push_pending(self, root):
        self.cleared += 1


def _load_session_start():
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_start_mod", SESSION_START)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recover_ahead_rekicks_worker_no_direct_push(tmp_path, capsys, monkeypatch):
    """pending + ahead>0 → 경고 + worker 재kick 만(직접 push 금지·clear 금지)."""
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=2, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    err = capsys.readouterr().err
    assert "push 미완" in err and "ahead=2" in err
    assert fake.kicked == 1
    assert fake.cleared == 0


def test_recover_ahead_english_for_en_locale(tmp_path, capsys, monkeypatch):
    """i18n(적대검수 — long tail): lang="en" 을 명시로 넘기면 한글 없이 영어로만
    경고가 나온다(_recover_push_pending 은 _maybe_auto_pull 이 한 번 해석해 넘긴다)."""
    import re
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=2, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path), "en")
    err = capsys.readouterr().err
    assert "push" in err and "ahead=2" in err
    assert not re.search(r"[가-힣]", err), f"en 팀 출력에 한글 섞임: {err!r}"


def test_recover_stale_pending_auto_cleared(tmp_path, capsys, monkeypatch):
    """pending + ahead==0 → stale 자동 clear(이미 push 됨), 경고·kick 없음."""
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=0, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    assert fake.cleared == 1
    assert fake.kicked == 0
    assert capsys.readouterr().err == ""


def test_recover_no_pending_silent(tmp_path, capsys, monkeypatch):
    """pending 없으면 완전 침묵(판정·kick 비용 없음)."""
    mod = _load_session_start()
    fake = _FakeGo("", ahead=5, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    assert capsys.readouterr().err == ""
    assert fake.cleared == 0 and fake.kicked == 0


# ── UserPromptSubmit 초경량 pending-age 검사 (#45 가시화 3중의 ②) ───

REMIND = REPO / "infra" / "hooks" / "session-log-remind.py"


def _load_remind():
    import importlib.util
    spec = importlib.util.spec_from_file_location("remind_mod", REMIND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_remind_warns_on_old_pending(xdg, tmp_path, capsys):
    """age > 임계 → stderr 1줄 + warned 마커(30분 스로틀) — 2회째는 침묵."""
    mod = _load_remind()
    root = str(tmp_path / "team")
    git_ops.write_push_pending(root)
    # mtime 을 과거로 조작해 age > 600s 재현
    old = Path(git_ops.push_pending_path(root))
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 700))
    mod._warn_push_pending_age(root)
    err1 = capsys.readouterr().err
    # i18n 갱신(적대검수 — long tail): root 에 team.config.json 이 없어 en 기본
    # (team_lang 계약) — "push" 는 en/ko 두 문구 모두에 그대로 나오는 언어중립 토큰.
    assert "push" in err1
    mod._warn_push_pending_age(root)  # 스로틀 — 재경고 없음
    assert capsys.readouterr().err == ""


def test_remind_silent_on_fresh_or_no_pending(xdg, tmp_path, capsys):
    """pending 없음/신선(age<임계) → 완전 침묵."""
    mod = _load_remind()
    root = str(tmp_path / "team")
    mod._warn_push_pending_age(root)          # 없음
    git_ops.write_push_pending(root)
    mod._warn_push_pending_age(root)          # 신선
    assert capsys.readouterr().err == ""


# ── codex 적대검수 반영 (P1×3·P2×3·P3) ─────────────────────────────

def test_clear_pending_if_unchanged_guard(xdg, tmp_path):
    """[P1] clear race 가드: 스냅샷 이후 pending 이 재기록됐으면 clear 하지 않는다.

    판별자는 파일 내용(고유 nonce) — coarse mtime FS(1s 해상도)에서도 같은 초 내
    재기록을 정확히 구분한다(codex 재검수). 재기록 사이에 sleep 을 두지 않는 것이
    바로 그 검증이다.
    """
    root = str(tmp_path / "team")
    git_ops.write_push_pending(root)
    snap = git_ops.read_push_pending(root)
    # 변경 없음 → clear 성공
    assert git_ops.clear_push_pending_if_unchanged(root, snap) is True
    assert git_ops.read_push_pending(root) == ""
    # 재기록(새 커밋의 pending) — 같은 초 내 연속 기록이어도 nonce 로 구분된다.
    git_ops.write_push_pending(root)
    snap_old = git_ops.read_push_pending(root)
    git_ops.write_push_pending(root)  # 경합: push 도중 새 pending(즉시 재기록)
    assert git_ops.clear_push_pending_if_unchanged(root, snap_old) is False
    assert git_ops.read_push_pending(root) != "", "경합 pending 이 유실됐다"
    # 빈 스냅샷은 항상 거부(보수)
    assert git_ops.clear_push_pending_if_unchanged(root, "") is False


def test_write_push_pending_returns_bool(xdg, tmp_path, monkeypatch):
    """[P1] ledger 기록 성공 여부를 호출부가 알 수 있다(bool 반환)."""
    root = str(tmp_path / "team")
    assert git_ops.write_push_pending(root) is True
    # 기록 불가 환경(state dir 를 파일로 막음) → False
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "blocked"))
    blocked = tmp_path / "blocked" / "teammode"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("not a dir", encoding="utf-8")
    assert git_ops.write_push_pending(root) is False


def test_auto_commit_ledger_failure_leaves_sync_warning(xdg, tmp_path):
    """[P1] 커밋 성공 + ledger 기록 실패 → sync-warning fallback + stderr(무음 유실 차단)."""
    _, work = _clone_pair(tmp_path)
    _activate(work)
    f = work / "note-lf.md"
    f.write_text("x", encoding="utf-8")
    # write 는 막고 sync-warning 은 살리기: pending 파일 자리를 디렉토리로 선점
    Path(git_ops.push_pending_path(str(work))).parent.mkdir(parents=True, exist_ok=True)
    Path(git_ops.push_pending_path(str(work))).mkdir()
    r = _run_auto_commit(work, [f], xdg, {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0
    # i18n 갱신(적대검수 — long tail): 이 픽스처는 en 기본(team_lang 계약) — 언어중립
    # 마커("push-pending" 은 ko/en 문구 모두에 그대로 나옴)로 확인.
    assert "push-pending" in r.stderr
    assert "pending" in git_ops.read_sync_warning(str(work))


def test_auto_commit_ledger_failure_english_for_en_locale_team(xdg, tmp_path):
    """i18n(적대검수 — long tail, auto-commit 신설 스캐폴딩): en 팀은 ledger 기록
    실패 stderr 도, sync-warning 마커 내용도 전부 영어이고 한글이 섞이지 않는다.

    sync-warning 마커는 나중에 session-start 의 hook_ss_sync_warn(en-locale)의
    {warn} 자리에 그대로 삽입되므로, 마커 자체가 en 이어야 en 팀 출력이 끝까지
    영어로 유지된다(addendum 2 에서 발견한 것과 동일한 클래스의 함정).
    """
    import json as _json
    import re
    _, work = _clone_pair(tmp_path)
    _activate(work)
    (work / "team.config.json").write_text(
        _json.dumps({"team": {"name": "acme", "locale": "en_US"}}), encoding="utf-8")
    f = work / "note-lf-en.md"
    f.write_text("x", encoding="utf-8")
    Path(git_ops.push_pending_path(str(work))).parent.mkdir(parents=True, exist_ok=True)
    Path(git_ops.push_pending_path(str(work))).mkdir()
    r = _run_auto_commit(work, [f], xdg, {"TEAMMODE_DISABLE_PUSH_WORKER": "1"})
    assert r.returncode == 0
    assert "push-pending" in r.stderr
    marker = git_ops.read_sync_warning(str(work))
    assert "pending" in marker
    assert not re.search(r"[가-힣]", r.stderr), f"en 팀 stderr 에 한글 섞임: {r.stderr!r}"
    assert not re.search(r"[가-힣]", marker), f"en 팀 마커에 한글 섞임: {marker!r}"


def test_recover_unknown_upstream_still_kicks_worker(tmp_path, capsys, monkeypatch):
    """[P2] 판정불가(무 upstream 또는 git 오류) → 보수 경고 + worker kick(영구 경고 루프 차단).

    worker 의 push_plain 이 no-upstream 을 `push -u` 로 처리하므로 kick 이 안전하다.
    """
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=0, has_upstream=False)
    monkeypatch.setattr(mod, "_git_ops", fake)
    mod._recover_push_pending(str(tmp_path))
    err = capsys.readouterr().err
    assert "판정 불가" in err
    assert fake.kicked == 1, "판정불가에서 worker 를 재기동하지 않으면 신규 브랜치 pending 이 영구 잔존"
    assert fake.cleared == 0


def test_recovery_runs_even_when_pull_throttled(tmp_path, monkeypatch, capsys):
    """[P2] pending recovery 는 auto-pull 스로틀과 독립 — throttle 로 조기 return 해도 실행."""
    mod = _load_session_start()
    fake = _FakeGo("pending", ahead=1, has_upstream=True)
    monkeypatch.setattr(mod, "_git_ops", fake)

    class _FakePull:
        DEFAULT_THROTTLE_SECONDS = 600
        @staticmethod
        def should_pull(state, now, throttle):
            return False  # 스로틀에 막힌 상황 재현
    monkeypatch.setattr(mod, "_auto_pull", _FakePull)
    mod._maybe_auto_pull(str(tmp_path))
    assert fake.kicked == 1, "스로틀에 막혀 recovery 가 실행되지 않았다"


def test_worker_drain_exhaustion_writes_marker(xdg, tmp_path):
    """[P3] drain 한도 소진 시(pending 잔존) sync-warning 즉시 표면화."""
    _, work = _clone_pair(tmp_path)
    _commit_file(work, "a.md")
    git_ops.write_push_pending(str(work))
    r = _run_worker(work, {"XDG_STATE_HOME": str(xdg),
                           "TEAMMODE_WORKER_MAX_LOOPS": "0"})
    assert r.returncode == 0
    assert "drain" in git_ops.read_sync_warning(str(work))
    assert git_ops.read_push_pending(str(work)) != ""
