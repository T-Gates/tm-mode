"""W-E — Windows native stdout 인코딩 크래시 수정 (실 도그푸딩 발견 버그).

[버그] 실 Windows 에서 teammode 엔진 출력이 기본 stdout 인코딩(cp949 등 비-UTF8)에
한글을 쓰다 UnicodeEncodeError 로 크래시한다. 실측: `teammode.py context --json` 의
`print(json.dumps({...}, ensure_ascii=False))`(한글 INDEX 포함)가 teammode.py 에서
크래시(rc=1) → install verify 단계 연쇄 실패. `PYTHONIOENCODING=utf-8` 주면 정상 →
인코딩이 근본 원인.

[수정] 진입점 main 진입부에서 io_encoding.ensure_utf8_io() 로 stdout/stderr 를 UTF-8
보장(비-UTF8 TextIOWrapper 만, reconfigure 가능할 때만 — capsys/StringIO 무파손).

[이 테스트] stdout 을 비-UTF8(ascii/cp949) TextIOWrapper 로 **치환**(실 Windows 모킹)한
상태에서:
  - 보정 전(raw write): UnicodeEncodeError 발생을 증명(mutation = 버그 재현).
  - 보정 후(ensure_utf8_io): 크래시 없이 **올바른 한글**을 UTF-8 bytes 로 출력.
  - teammode context --json·install 한글 출력이 같은 모킹에서 크래시 안 함.
  - capsys(StringIO) 는 reconfigure 부재라 건드리지 않음 → 테스트 캡처 무파손.

호스트 무접촉: tmp_path + monkeypatch + BytesIO 모킹만. 실 setx/reg/stdout 불사용.
"""
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

from io_encoding import ensure_utf8_io, _is_utf8  # noqa: E402

ENGINE = REPO / "infra" / "teammode.py"


def _make_legacy_stdout(encoding: str):
    """실 Windows 기본 stdout 모킹 — BytesIO 위 비-UTF8 TextIOWrapper.

    reconfigure 가능(실 콘솔과 동형)하지만 인코딩이 비-UTF8 이라, 한글을 strict 로 쓰면
    UnicodeEncodeError(ascii) — 보정 전 크래시를 정확히 재현한다.
    """
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding=encoding, newline="")
    return raw, wrapper


# ── 1. 핵심 mutation: 보정 전 크래시 / 보정 후 정상 ──

def test_legacy_stdout_crashes_without_fix():
    """보정 전: 비-UTF8(ascii) stdout 에 한글 쓰면 UnicodeEncodeError (버그 재현)."""
    _raw, wrapper = _make_legacy_stdout("ascii")
    with pytest.raises(UnicodeEncodeError):
        wrapper.write("팀 모드 한글 출력")
        wrapper.flush()


def test_ensure_utf8_io_fixes_legacy_stdout(monkeypatch):
    """보정 후: ensure_utf8_io() 가 비-UTF8 stdout 을 UTF-8 로 reconfigure → 한글 정상."""
    raw, wrapper = _make_legacy_stdout("ascii")
    monkeypatch.setattr(sys, "stdout", wrapper)
    monkeypatch.setattr(sys, "stderr", wrapper)

    ensure_utf8_io()

    assert _is_utf8(sys.stdout.encoding)  # ascii → utf-8 로 전환됨
    sys.stdout.write("팀 모드 한글 출력")  # 보정 전이면 여기서 크래시
    sys.stdout.flush()
    assert raw.getvalue() == "팀 모드 한글 출력".encode("utf-8")


def test_ensure_utf8_io_handles_cp949(monkeypatch):
    """Windows 한국어 로캘(cp949)도 UTF-8 로 통일 — cp949 에 없는 문자(이모지)도 안전."""
    raw, wrapper = _make_legacy_stdout("cp949")
    monkeypatch.setattr(sys, "stdout", wrapper)
    ensure_utf8_io()
    assert _is_utf8(sys.stdout.encoding)
    # cp949 에 없는 문자(✅)도 UTF-8 전환 후엔 유실·크래시 없이 출력된다.
    sys.stdout.write("완료 ✅")
    sys.stdout.flush()
    assert raw.getvalue() == "완료 ✅".encode("utf-8")


# ── 2. 크로스플랫폼 무회귀: 이미 UTF-8 이면 무동작 ──

def test_ensure_utf8_io_noop_on_utf8(monkeypatch):
    """이미 UTF-8(Linux/macOS 기본)이면 reconfigure 호출 안 함 — 무회귀."""
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="utf-8")
    called = {"n": 0}
    orig_reconfigure = wrapper.reconfigure

    def _spy(*a, **k):
        called["n"] += 1
        return orig_reconfigure(*a, **k)

    monkeypatch.setattr(wrapper, "reconfigure", _spy)
    monkeypatch.setattr(sys, "stdout", wrapper)
    ensure_utf8_io()
    assert called["n"] == 0  # 이미 UTF-8 — 건드리지 않음


# ── 3. capsys/StringIO(테스트 캡처) 무파손 ──

def test_ensure_utf8_io_safe_on_stringio_capsys(monkeypatch):
    """pytest capsys 의 StringIO 류(= reconfigure 속성 부재)는 건드리지 않는다."""
    buf = io.StringIO()  # capsys 캡처 버퍼와 동형: reconfigure 없음
    assert not hasattr(buf, "reconfigure")
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "stderr", buf)
    ensure_utf8_io()  # 예외 없이 통과해야 한다(건드리지 않음)
    # 여전히 정상 쓰기 가능 — 캡처 무파손
    sys.stdout.write("한글 캡처")
    assert buf.getvalue() == "한글 캡처"


def test_capsys_capture_intact_after_ensure(capsys):
    """실 capsys 캡처가 ensure_utf8_io() 호출 후에도 한글을 정상 캡처(파손 0)."""
    ensure_utf8_io()
    print("팀 모드 캡처 검증 ✅")
    captured = capsys.readouterr()
    assert "팀 모드 캡처 검증 ✅" in captured.out


# ── 4. e2e: teammode context --json 이 모킹된 비-UTF8 stdout 에서 크래시 안 함 ──

def _write_index(root: Path, text: str):
    m = root / "memory"
    m.mkdir(parents=True, exist_ok=True)
    (m / "INDEX.md").write_text(f"# INDEX\n\n{text}\n", encoding="utf-8")


def test_context_json_survives_legacy_stdout(tmp_path, monkeypatch):
    """teammode context --json (한글 INDEX 포함) 이 비-UTF8 stdout 모킹에서 크래시 안 함.

    실 Windows 도그푸딩 크래시(teammode.py:cmd_context print)를 in-process 로 재현·검증.
    보정 없이는 UnicodeEncodeError(rc=1), main 의 ensure_utf8_io() 덕에 통과·한글 보존.
    """
    _write_index(tmp_path, "팀 메모리 인덱스 한글 본문")

    raw, wrapper = _make_legacy_stdout("ascii")
    monkeypatch.setattr(sys, "stdout", wrapper)

    sys.path.insert(0, str(REPO / "infra"))
    import teammode as tm  # noqa: E402

    rc = tm.main(["context", "--root", str(tmp_path),
                  "--settings", str(tmp_path / ".teammode-settings.json"), "--json"])
    sys.stdout.flush()

    assert rc == 0
    out = raw.getvalue().decode("utf-8")  # UTF-8 로 보정됐으므로 디코드 가능
    payload = json.loads(out)
    assert "팀 메모리 인덱스 한글 본문" in payload["index"]


# ── 5. normalize.py 재방출 인코딩 (P1) — 런타임 훅 최종 출구 ──
#
# normalize.py 는 훅 커맨드의 외곽 래퍼다(build_command 가 `python normalize.py <hook>.py`).
# 내부 훅을 subprocess(capture_output, text=True)로 돌린 뒤 sys.stdout.write(proc.stdout)
# 로 **자기 stdout 에 재방출**한다. session-start 의 additionalContext(이모지 흔함)가 이
# 래퍼를 거쳐 cp949 콘솔에 재방출되면 보정 없이는 UnicodeEncodeError 로 크래시한다.
# main 진입부의 ensure_utf8_io() 가 이 마지막 출구를 막는다.

def _build_normalize_env(tmp_path, hook_emit: str):
    """team_root/infra/{hooks,agents/claude} + 이모지·한글 재방출 stub 훅 구성.

    normalize 모듈을 in-process import 해 HOOKS_DIR/EVENTS/MANIFEST 를 이 tmp 로 가리킨다.
    stub 훅은 stdin 을 무시하고 hook_emit(이모지+한글) 을 stdout 에 print 한다.
    """
    root = tmp_path
    hooks = root / "infra" / "hooks"
    agentd = root / "infra" / "agents" / "claude"
    hooks.mkdir(parents=True)
    agentd.mkdir(parents=True)

    src_agentd = REPO / "infra" / "agents" / "claude"
    (agentd / "events.json").write_text(
        (src_agentd / "events.json").read_text(encoding="utf-8"), encoding="utf-8")

    # stub 훅: 이모지+한글을 stdout 으로 재방출(session-start additionalContext 모사)
    (hooks / "emit-stub.py").write_text(
        "import sys\nsys.stdin.read()\n"
        f"sys.stdout.write({hook_emit!r})\nsys.exit(0)\n",
        encoding="utf-8")
    # 무매처 일반 엔트리(자가 필터 통과)
    (hooks / "manifest.json").write_text(
        json.dumps([{"script": "emit-stub.py", "event": "SessionStart"}]),
        encoding="utf-8")

    sys.path.insert(0, str(src_agentd))
    import importlib
    import normalize as nm  # noqa: E402
    importlib.reload(nm)  # 다른 테스트의 경로 상수 오염 방지
    nm.HOOKS_DIR = hooks
    nm.MANIFEST = hooks / "manifest.json"
    nm.EVENTS = agentd / "events.json"
    return nm


# 이모지+한글: cp949·ascii 어느 쪽에도 strict 로 못 쓰는 페이로드(크래시 트리거)
_REEMIT = '{"hookSpecificOutput": {"additionalContext": "INDEX 완료 ✅ 🚀 한글"}}'


def test_normalize_reemit_survives_legacy_stdout(tmp_path, monkeypatch):
    """보정 후(GREEN): normalize main 이 cp949 stdout 에 이모지·한글 재방출해도 크래시 안 함."""
    nm = _build_normalize_env(tmp_path, _REEMIT)

    raw, wrapper = _make_legacy_stdout("cp949")
    monkeypatch.setattr(sys, "stdout", wrapper)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"hook_event_name": "SessionStart"})))

    rc = nm.main(["emit-stub.py"])
    sys.stdout.flush()

    assert rc == 0
    out = raw.getvalue().decode("utf-8")  # UTF-8 로 보정됐으므로 디코드 가능
    assert "완료 ✅ 🚀 한글" in out


def test_normalize_reemit_crashes_without_fix(tmp_path, monkeypatch):
    """mutation 가드(RED): ensure_utf8_io 무력화 시 같은 재방출이 UnicodeEncodeError 크래시.

    normalize.main 의 _ensure_utf8_io() 호출이 load-bearing 임을 증명(no-op 패치 → 재현).
    """
    nm = _build_normalize_env(tmp_path, _REEMIT)
    monkeypatch.setattr(nm, "_ensure_utf8_io", lambda: None)  # 보정 무력화

    raw, wrapper = _make_legacy_stdout("cp949")
    monkeypatch.setattr(sys, "stdout", wrapper)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"hook_event_name": "SessionStart"})))

    with pytest.raises(UnicodeEncodeError):
        nm.main(["emit-stub.py"])
        sys.stdout.flush()


def test_context_json_crashes_without_fix(tmp_path, monkeypatch):
    """mutation 가드: ensure_utf8_io 를 무력화하면 같은 경로가 UnicodeEncodeError 로 크래시.

    main 의 보정 호출이 실제로 크래시를 막는 load-bearing 코드임을 증명한다(보정 제거 →
    예외 재현). teammode.ensure_utf8_io 를 no-op 으로 패치해 '보정 전' 상태를 만든다.
    """
    _write_index(tmp_path, "팀 메모리 인덱스 한글")

    raw, wrapper = _make_legacy_stdout("ascii")
    monkeypatch.setattr(sys, "stdout", wrapper)

    sys.path.insert(0, str(REPO / "infra"))
    import teammode as tm  # noqa: E402
    monkeypatch.setattr(tm, "ensure_utf8_io", lambda: None)  # 보정 무력화

    with pytest.raises(UnicodeEncodeError):
        tm.main(["context", "--root", str(tmp_path),
                 "--settings", str(tmp_path / ".teammode-settings.json"), "--json"])
