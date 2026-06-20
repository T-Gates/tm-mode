"""L2-E — credentials 금고 (각자입력, B-3) 테스트.

검증축:
  1. 저장/조회/삭제 라운드트립.
  2. **0600 권한 stat 실측**(umask 의존 금지 — 저장 직후 os.stat 모드비트 단언).
  3. **토큰 누출 0 실증**: 유니크 센티넬 토큰을 저장 후, 모든 예외 메시지·stdout/stderr·
     로그 출력에 그 센티넬이 **부분문자열로도** 안 나타남을 적대적으로 grep. (git_ops 의
     stderr `detail` 누출 동형 사고 방지.)
  4. 팀 vs 개인 scope 격리.
  5. 실 credentials 경로 무오염(B0 conftest 가드 + XDG 격리 실증).

격리: conftest autouse `_isolate_pull_state` 가 XDG_DATA_HOME 를 tmp 로 monkeypatch 하므로
실 `~/.local/share/teammode/credentials` 무접촉. B0 가드(`_ENTRY_TRACKED_DIRS`)가 실경로
침투 시 즉시 발화한다.
"""
import io
import json
import logging
import os
import stat
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import credentials as cred  # noqa: E402

TEAM = "acme"


def _sentinel() -> str:
    """유니크 센티넬 토큰 — 우연 충돌 없이 출력 grep 가능."""
    return "SENTINEL_TOKEN_" + uuid.uuid4().hex


# ─────────────────────────── 1. 라운드트립 ───────────────────────────

def test_store_load_delete_roundtrip():
    tok = _sentinel()
    cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", tok)
    assert cred.load(TEAM, cred.SCOPE_PERSONAL, "linear") == tok

    assert cred.delete(TEAM, cred.SCOPE_PERSONAL, "linear") is True
    assert cred.load(TEAM, cred.SCOPE_PERSONAL, "linear") is None
    # 이미 없는 키 삭제 = False(실패 무해, 예외 아님).
    assert cred.delete(TEAM, cred.SCOPE_PERSONAL, "linear") is False


def test_load_missing_returns_none():
    assert cred.load(TEAM, cred.SCOPE_TEAM, "nope") is None


def test_store_overwrites():
    cred.store(TEAM, cred.SCOPE_PERSONAL, "k", "old")
    cred.store(TEAM, cred.SCOPE_PERSONAL, "k", "new")
    assert cred.load(TEAM, cred.SCOPE_PERSONAL, "k") == "new"


def test_list_keys_returns_names_not_values():
    tok = _sentinel()
    cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", tok)
    cred.store(TEAM, cred.SCOPE_PERSONAL, "gcal", tok)
    keys = cred.list_keys(TEAM, cred.SCOPE_PERSONAL)
    assert keys == ["gcal", "linear"]
    # 값은 절대 반환 목록에 없다.
    assert tok not in keys


# ─────────────────────────── 2. 0600 권한 실측 ───────────────────────────

def test_file_mode_is_0600_after_store():
    cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", _sentinel())
    path = cred.credentials_dir() / f"{TEAM}.json"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    # umask 에 의존하지 않는 절대 단언 — group/other 비트 0, owner rw.
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    assert cred.file_mode(TEAM) == 0o600


def test_dir_mode_is_owner_only():
    cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", _sentinel())
    d = cred.credentials_dir()
    mode = stat.S_IMODE(os.stat(d).st_mode)
    # group/other 권한 비트가 없어야 한다(0700 보정).
    assert mode & 0o077 == 0, f"dir leaks perms: {oct(mode)}"


def test_mode_reasserted_on_overwrite():
    """기존 파일을 0666 으로 망가뜨려도 store 가 0600 으로 재단언하는지."""
    cred.store(TEAM, cred.SCOPE_PERSONAL, "k", "v")
    path = cred.credentials_dir() / f"{TEAM}.json"
    os.chmod(path, 0o666)
    cred.store(TEAM, cred.SCOPE_PERSONAL, "k", "v2")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ─────────────────────── 2b. 심링크 추종 거부(O_NOFOLLOW, P2-1) ───────────────────────

def test_store_refuses_symlinked_vault_no_outside_write(tmp_path):
    """금고 경로에 심링크를 심으면 store 가 O_NOFOLLOW 로 거부 — 금고 밖 토큰 평문 무기록."""
    tok = _sentinel()
    vault = cred.credentials_dir() / f"{TEAM}.json"
    vault.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "OUTSIDE_TARGET.txt"  # 금고 밖 타깃(존재하지 않음).
    os.symlink(str(outside), str(vault))
    assert os.path.islink(str(vault))

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        with pytest.raises(OSError) as ei:
            cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", tok)
    # 금고 밖 파일이 절대 생기지 않는다(심링크 추종 차단).
    assert not outside.exists(), "심링크 추종으로 금고 밖에 파일이 생성됨"
    # 에러/출력 어디에도 토큰 평문이 새지 않는다.
    msg = str(ei.value) + repr(ei.value) + out.getvalue() + err.getvalue()
    _assert_no_sentinel(msg, tok, "심링크 거부 경로")


def test_read_vault_ignores_symlinked_vault(tmp_path):
    """_read_vault(load 경유)도 심링크를 추종해 금고 밖 내용을 읽지 않는다 → None."""
    tok = _sentinel()
    target = tmp_path / "outside.json"
    # 금고 밖에 '진짜 토큰처럼 보이는' 내용을 둔다.
    target.write_text(json.dumps({cred.SCOPE_PERSONAL: {"linear": tok}}),
                      encoding="utf-8")
    vault = cred.credentials_dir() / f"{TEAM}.json"
    vault.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(target), str(vault))

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        # 심링크 추종 거부 → 금고 밖 토큰을 읽지 않고 None(빈 금고 취급).
        assert cred.load(TEAM, cred.SCOPE_PERSONAL, "linear") is None
    _assert_no_sentinel(out.getvalue() + err.getvalue(), tok, "심링크 read 거부")


# ─────────────────────────── 3. 토큰 누출 0 실증 ───────────────────────────

def _assert_no_sentinel(text: str, tok: str, where: str):
    assert tok not in text, f"토큰 센티넬이 {where} 에 누출됨: {text!r}"


def test_token_never_in_stdout_stderr_on_success():
    tok = _sentinel()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", tok)
        cred.load(TEAM, cred.SCOPE_PERSONAL, "linear")
        cred.list_keys(TEAM, cred.SCOPE_PERSONAL)
        cred.delete(TEAM, cred.SCOPE_PERSONAL, "linear")
    _assert_no_sentinel(out.getvalue(), tok, "stdout")
    _assert_no_sentinel(err.getvalue(), tok, "stderr")


def test_token_never_in_exception_messages():
    """식별자 인젝션·잘못된 scope 등 모든 예외 경로에서 토큰값이 새지 않음(부분문자열도)."""
    tok = _sentinel()
    # 잘못된 key 식별자(traversal 시도) — 토큰을 들고 store 호출 → ValueError.
    bad_keys = ["../etc", "a/b", "key with space", "", "..", "x;rm -rf"]
    for bad in bad_keys:
        with pytest.raises(ValueError) as ei:
            cred.store(TEAM, cred.SCOPE_PERSONAL, bad, tok)
        msg = str(ei.value) + repr(ei.value)
        _assert_no_sentinel(msg, tok, f"예외 메시지(key={bad!r})")
        # bad key(사용자 입력) 자체도 echo 안 함(인젝션 면역 일관성).
        if bad:
            assert bad not in str(ei.value)

    # 잘못된 scope.
    with pytest.raises(ValueError) as ei:
        cred.store(TEAM, "nonscope", "k", tok)
    _assert_no_sentinel(str(ei.value) + repr(ei.value), tok, "예외(scope)")

    # 잘못된 team 식별자.
    with pytest.raises(ValueError) as ei:
        cred.store("../evil", cred.SCOPE_TEAM, "k", tok)
    _assert_no_sentinel(str(ei.value) + repr(ei.value), tok, "예외(team)")


def test_token_never_in_corrupt_file_exception(monkeypatch):
    """금고 파일이 파손돼도(평문 토큰 잔존 가능) 예외/출력에 내용이 안 샌다 — 빈 금고 취급."""
    tok = _sentinel()
    cred.store(TEAM, cred.SCOPE_PERSONAL, "k", tok)
    path = cred.credentials_dir() / f"{TEAM}.json"
    # 파일을 깨뜨리되 토큰 평문이 남아있게(파손 JSON).
    path.write_text('{ broken json with ' + tok + ' inside', encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        # 파손 파일은 빈 금고로 취급 → None, 예외 없음.
        assert cred.load(TEAM, cred.SCOPE_PERSONAL, "k") is None
    _assert_no_sentinel(out.getvalue() + err.getvalue(), tok, "파손파일 처리 출력")


def test_token_never_in_logging(caplog):
    tok = _sentinel()
    with caplog.at_level(logging.DEBUG):
        cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", tok)
        cred.load(TEAM, cred.SCOPE_PERSONAL, "linear")
        cred.delete(TEAM, cred.SCOPE_PERSONAL, "linear")
    _assert_no_sentinel(caplog.text, tok, "로그")


def test_module_source_has_no_token_print():
    """적대적 정적 검사: 모듈 소스에 토큰값을 print/log/예외에 직접 넣는 패턴이 없는지.

    동적 grep(stdout/예외)이 못 잡는 미래 회귀 표면을 소스 레벨에서 한 번 더 박는다.
    """
    src = (REPO / "infra" / "credentials.py").read_text(encoding="utf-8")
    # 토큰 변수를 직접 포매팅에 넣는 명백한 누출 패턴 거부.
    forbidden = ["{token}", "print(token", "(token)", "f\"{tok", "{tok}"]
    for pat in forbidden:
        assert pat not in src, f"소스에 잠재적 토큰 누출 패턴: {pat!r}"


# ─────────────────────────── 4. scope 격리 ───────────────────────────

def test_team_vs_personal_scope_isolation():
    """팀 scope 와 개인 scope 가 같은 key 라도 격리(각자입력 — 둘 다 로컬 저장)."""
    team_tok = _sentinel()
    personal_tok = _sentinel()
    cred.store(TEAM, cred.SCOPE_TEAM, "notion", team_tok)
    cred.store(TEAM, cred.SCOPE_PERSONAL, "notion", personal_tok)

    assert cred.load(TEAM, cred.SCOPE_TEAM, "notion") == team_tok
    assert cred.load(TEAM, cred.SCOPE_PERSONAL, "notion") == personal_tok
    assert team_tok != personal_tok

    # 한 scope 삭제가 다른 scope 를 건드리지 않는다.
    cred.delete(TEAM, cred.SCOPE_TEAM, "notion")
    assert cred.load(TEAM, cred.SCOPE_TEAM, "notion") is None
    assert cred.load(TEAM, cred.SCOPE_PERSONAL, "notion") == personal_tok


def test_scopes_coexist_in_same_file():
    cred.store(TEAM, cred.SCOPE_TEAM, "slack", "t1")
    cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", "p1")
    path = cred.credentials_dir() / f"{TEAM}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {cred.SCOPE_TEAM, cred.SCOPE_PERSONAL}
    assert data[cred.SCOPE_TEAM] == {"slack": "t1"}
    assert data[cred.SCOPE_PERSONAL] == {"linear": "p1"}


# ─────────────────────────── 5. 실경로 무오염 + 각자입력(전송채널 없음) ───────────

def test_writes_under_isolated_xdg_not_real_host():
    """저장 파일이 conftest 가 격리한 XDG_DATA_HOME 아래에만 생기는지 실증."""
    cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", _sentinel())
    path = cred.credentials_dir() / f"{TEAM}.json"
    assert path.exists()
    xdg = Path(os.environ["XDG_DATA_HOME"])
    # 격리 경로 하위여야 하고, 실 HOME credentials 경로가 아니어야 한다.
    assert str(path).startswith(str(xdg))
    real = Path(os.path.expanduser("~/.local/share/teammode/credentials"))
    assert not str(path).startswith(str(real))
    # B0 conftest 가드(_ENTRY_TRACKED_DIRS)가 실경로 침투 시 이 테스트 종료 후 발화한다.


def test_no_team_transmission_channel():
    """B-3 각자입력: store/load/delete 외 '팀 전송/공유' 공개 동사가 없음(v0.1 미구현)."""
    public = {n for n in dir(cred) if not n.startswith("_")}
    forbidden = {"share", "push", "broadcast", "sync", "fetch_team", "publish", "upload"}
    leaked = public & forbidden
    assert not leaked, f"v0.1 에 없어야 할 팀 전송 동사 노출: {leaked}"


# ─────────────────────────── SEC-4: 동기화 폴더 경고 ───────────────────────────

def test_store_warns_on_sync_folder(monkeypatch, tmp_path):
    """SEC-4: 금고 경로가 동기화 폴더 패턴이면 store 가 경고한다(거부는 안 함)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "Dropbox" / "data"))
    err = io.StringIO()
    with redirect_stderr(err):
        path = cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", "tok")
    assert "SEC-4" in err.getvalue(), "동기화 폴더인데 경고가 없다"
    assert path.is_file()  # 거부하지 않음 — 저장은 정상 수행


def test_store_no_warn_on_local_folder(monkeypatch, tmp_path):
    """정상 로컬 경로는 SEC-4 경고가 없다(오탐 없음)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "localdata"))
    err = io.StringIO()
    with redirect_stderr(err):
        cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", "tok")
    assert "SEC-4" not in err.getvalue()


def test_sync_folder_warning_does_not_leak_token(monkeypatch, tmp_path):
    """SEC-4 경고도 토큰 누출 0 — 경고 메시지에 토큰값이 없다."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "Library" / "Mobile Documents"))
    tok = _sentinel()
    err = io.StringIO()
    with redirect_stderr(err):
        cred.store(TEAM, cred.SCOPE_PERSONAL, "linear", tok)
    assert tok not in err.getvalue()
