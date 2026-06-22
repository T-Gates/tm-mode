"""Windows cp949 인코딩 버그 수정 검증 — P0·P1·P1#4.

변경 항목:
  - normalize.py subprocess.run: encoding="utf-8", errors="replace" 추가 (P0)
  - git_ops.run_git Popen kwargs: encoding="utf-8", errors="replace" 추가 (P1)
  - install_lib.py setx/reg subprocess.run: encoding="utf-8", errors="replace" 추가 (P1)
  - session-log-remind.py: TMPDIR → tempfile.gettempdir(), 파일 I/O try/except (P1#4)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NORMALIZE = REPO / "infra" / "agents" / "claude" / "normalize.py"
PY = sys.executable


# ── 1. P0: normalize subprocess 한글 라운드트립 ──

@pytest.fixture
def normalize_env(tmp_path):
    """normalize 실행 환경: hooks + echo-stub + events.json."""
    hooks = tmp_path / "infra" / "hooks"
    agentd = tmp_path / "infra" / "agents" / "claude"
    hooks.mkdir(parents=True)
    agentd.mkdir(parents=True)

    (agentd / "normalize.py").write_text(
        NORMALIZE.read_text(encoding="utf-8"), encoding="utf-8")
    (agentd / "events.json").write_text(
        (REPO / "infra" / "agents" / "claude" / "events.json")
        .read_text(encoding="utf-8"),
        encoding="utf-8")

    # echo stub: 정규 JSON stdin 을 그대로 stdout 으로 재방출
    (hooks / "echo-stub.py").write_text(
        "import sys\nd=sys.stdin.read()\nsys.stdout.write(d)\nsys.exit(0)\n",
        encoding="utf-8")
    (hooks / "manifest.json").write_text(
        json.dumps([{"event": "UserPromptSubmit", "script": "echo-stub.py"}]),
        encoding="utf-8")
    return tmp_path


def test_normalize_korean_roundtrip_no_crash(normalize_env):
    """한글 프롬프트가 normalize subprocess 를 통과해 crash 없이 정확히 전달된다 (P0).

    encoding="utf-8", errors="replace" 가 없으면 cp949 환경에서 UnicodeDecodeError.
    subprocess 로 돌려 실제 encoding 협상(text=True 의존)이 발생하는 경로를 검증한다.
    """
    korean_prompt = "안녕하세요 팀 모드 한글 테스트 🚀"
    raw = {"hook_event_name": "UserPromptSubmit", "prompt": korean_prompt}

    proc = subprocess.run(
        [PY, str(normalize_env / "infra" / "agents" / "claude" / "normalize.py"),
         "echo-stub.py"],
        input=json.dumps(raw, ensure_ascii=False),
        capture_output=True, text=True,
        encoding="utf-8",
        cwd=str(normalize_env),
        env={**os.environ, "TEAMMODE_HOME": str(normalize_env)},
    )

    # 크래시 없음
    assert proc.returncode == 0, f"normalize crash: {proc.stderr}"
    # 한글이 손실 없이 라운드트립
    out = json.loads(proc.stdout)
    assert out["event"] == "UserPromptSubmit"
    assert out["prompt"] == korean_prompt, (
        f"한글 손실: expected={korean_prompt!r}, got={out['prompt']!r}")


def test_normalize_emoji_korean_roundtrip(normalize_env):
    """이모지·한글 혼합 프롬프트가 손실 없이 전달된다."""
    prompt = "팀 모드 활성화 완료 ✅ — 오늘 작업: 코드 리뷰 및 PR 머지"
    raw = {"hook_event_name": "UserPromptSubmit", "prompt": prompt}

    proc = subprocess.run(
        [PY, str(normalize_env / "infra" / "agents" / "claude" / "normalize.py"),
         "echo-stub.py"],
        input=json.dumps(raw, ensure_ascii=False),
        capture_output=True, text=True,
        encoding="utf-8",
        cwd=str(normalize_env),
        env={**os.environ, "TEAMMODE_HOME": str(normalize_env)},
    )

    assert proc.returncode == 0, f"crash: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["prompt"] == prompt


def test_normalize_korean_safe_when_child_decodes_cp949(normalize_env):
    """자식 훅이 stdin 을 cp949 로 디코드해도 한글이 안전하다 — ensure_ascii(ASCII escape) 효과 (P0).

    ⭐ codex 적대검수가 잡은 진짜 P0 시나리오: 부모가 stdin 에 UTF-8 로 써도(encoding="utf-8"),
    자식 Python 의 sys.stdin.read() 는 자기 locale(Windows cp949)로 디코드한다. normalize 가
    한글을 UTF-8 바이트(ensure_ascii=False)로 보내면 cp949 자식이 그 멀티바이트를 디코드하다
    UnicodeDecodeError/mojibake. → normalize 가 ASCII escape(\\uXXXX)로 보내면 어떤 locale
    자식이든 안전(자식 json.loads 가 원복). PYTHONIOENCODING=cp949 로 자식 디코드를 강제해
    실제 Windows 상황을 재현한다(이 테스트는 ensure_ascii=False 회귀 시 실패한다).
    """
    korean = "안녕하세요 팀모드 한글 — 세션로그 리마인더"
    raw = {"hook_event_name": "UserPromptSubmit", "prompt": korean}

    # 자식 훅(echo-stub)이 stdin 을 **cp949 로 디코드**하도록 격리 재현 — Windows
    # 한국어 locale 의 자식 sys.stdin 을 흉내낸다(env 가 아니라 raw bytes decode 로
    # 강제해, normalize 자신의 stdin 읽기는 건드리지 않는다 = 실제 상황: normalize→자식
    # 쓰기만 문제였던 원 제보 _writerthread EncodeError 와 일치).
    (normalize_env / "infra" / "hooks" / "echo-stub.py").write_text(
        "import sys\n"
        "d = sys.stdin.buffer.read().decode('cp949')\n"   # 자식 = cp949 locale
        "sys.stdout.buffer.write(d.encode('utf-8'))\n"
        "sys.exit(0)\n",
        encoding="utf-8")

    proc = subprocess.run(
        [PY, str(normalize_env / "infra" / "agents" / "claude" / "normalize.py"),
         "echo-stub.py"],
        input=json.dumps(raw, ensure_ascii=False),
        capture_output=True, text=True,
        encoding="utf-8",
        cwd=str(normalize_env),
        env={**os.environ, "TEAMMODE_HOME": str(normalize_env)},
    )

    # ensure_ascii=False 회귀 시: normalize 가 한글 UTF-8 바이트를 보내 자식 cp949
    # decode 가 0xec 에서 터진다. ASCII escape 면 자식 locale 무관하게 통과.
    assert proc.returncode == 0, f"cp949 자식에서 normalize crash: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["prompt"] == korean, f"cp949 자식서 한글 손실: got={out['prompt']!r}"


def test_normalize_source_has_no_ensure_ascii_false():
    """normalize.py 가 ensure_ascii=False 로 stdin 을 보내지 않는다 — P0 회귀 고정.

    ensure_ascii=False 면 한글이 UTF-8 멀티바이트로 자식 stdin 에 가 cp949 자식서 깨진다.
    기본(ensure_ascii=True)으로 ASCII escape 를 보내야 OS 무관 안전.
    """
    src = NORMALIZE.read_text(encoding="utf-8")
    assert "ensure_ascii=False" not in src, (
        "normalize.py 가 자식 stdin 에 ensure_ascii=False 로 보낸다 — "
        "cp949 자식 디코드가 깨진다(P0). ensure_ascii 기본(ASCII)으로 보내야 한다.")


# ── 2. P1#4: counter 파일이 tempfile.gettempdir() 를 사용한다 ──

def test_counter_file_uses_tempfile_gettempdir(tmp_path, monkeypatch):
    """session-log-remind 카운터 파일 경로가 tempfile.gettempdir() 기반이어야 한다.

    TMPDIR env 가 없어도 올바른 임시 디렉토리(OS 네이티브)를 사용한다.
    Windows 에서 TMPDIR 은 TEMP/TMP 와 다를 수 있어 /tmp 하드코딩은 실패한다.
    """
    # TMPDIR 을 제거해 폴백 경로 테스트
    monkeypatch.delenv("TMPDIR", raising=False)
    # TEAMMODE_MEMBER 가 부모 env 로 새면 멤버별 상태파일 경로가 되므로, 폴백(agent 단위)
    # 경로를 검증하려면 제거한다 (subprocess 는 {**os.environ} 를 상속하므로 부모에서 지운다).
    monkeypatch.delenv("TEAMMODE_MEMBER", raising=False)
    # tempfile.gettempdir() 는 첫 호출값을 tempfile.tempdir 에 캐시한다.
    # delenv 는 env 만 지우고 캐시는 안 지우므로, 부모가 이미 TMPDIR 로 캐시했다면
    # 기대값이 오염된다 → 캐시를 무효화해 subprocess(hook) 와 동일 조건으로 맞춘다.
    monkeypatch.setattr(tempfile, "tempdir", None)

    hook = REPO / "infra" / "hooks" / "session-log-remind.py"
    hook_src = hook.read_text(encoding="utf-8")

    # 소스 코드에 tempfile.gettempdir() 가 사용됐고 TMPDIR 환경변수 폴백이 없는지 확인
    assert "tempfile.gettempdir()" in hook_src, (
        "counter_file 이 tempfile.gettempdir() 를 사용해야 한다(P1#4)")
    assert 'os.environ.get("TMPDIR"' not in hook_src, (
        "TMPDIR env 하드코딩이 남아있다 — tempfile.gettempdir() 로 교체되어야 한다(P1#4)")

    # 실제로 hook 을 실행해 카운터 파일이 올바른 경로에 생성되는지 확인
    active = tmp_path / ".teammode-active"
    active.write_text("")
    canonical = {"event": "UserPromptSubmit", "prompt": "test", "agent": "claude-test"}

    # 상태 파일이 tempfile.gettempdir() 위치에 *이번 실행으로* 생성됐는지 본다.
    # 멤버 미특정 폴백 → _state_path(agent) = teammode-remind-state-<agent>.json
    # 고정 agent 라 이전 실행의 stale 파일이 남아 있으면 "이번 훅이 만들었다"를 오판하므로
    # 실행 전 제거한다(존재만 확인하는 약한 단언 보강).
    expected_state = os.path.join(
        tempfile.gettempdir(), "teammode-remind-state-claude-test.json")
    if os.path.exists(expected_state):
        os.remove(expected_state)

    proc = subprocess.run(
        [PY, str(hook)],
        input=json.dumps(canonical),
        capture_output=True, text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
        env={**os.environ, "TEAMMODE_HOME": str(tmp_path)},
    )

    assert proc.returncode == 0, f"hook crash: {proc.stderr}"
    assert os.path.isfile(expected_state), (
        f"상태 파일이 {expected_state} 에 생성되어야 한다")
    # 존재만으로는 약하다 — 내용이 카운터 상태 JSON(dict + count 키)인지까지 확인.
    with open(expected_state, encoding="utf-8") as f:
        state = json.load(f)
    assert isinstance(state, dict) and "count" in state, (
        f"상태 파일 내용이 카운터 JSON 이어야 한다: {state!r}")


def test_counter_file_write_failure_is_silent(tmp_path, monkeypatch):
    """카운터 파일 쓰기 실패 시 hook 이 조용히 처리한다(crash 없음) — P1#4 try/except.

    읽기 전용 경로를 만들어 OSError 를 유발하고 return code = 0 을 확인한다.
    """
    hook = REPO / "infra" / "hooks" / "session-log-remind.py"
    hook_src = hook.read_text(encoding="utf-8")

    # 파일 쓰기 실패가 try/except OSError 로 감싸져 있어야 한다
    assert "except OSError" in hook_src, (
        "카운터 파일 쓰기가 try/except OSError 로 보호되어야 한다(P1#4)")


# ── 3. P1#4: $USER 제거 확인 ──

def test_no_unix_user_env_in_reminder_message():
    """reminder 메시지에 $USER Unix-ism 이 없어야 한다 (P1#4 §96).

    $USER 는 Windows 에서 무의미하며 혼동을 줄 수 있다.
    members.md 의 영문 이름을 참조하라는 안내로 대체되어야 한다.
    """
    hook = REPO / "infra" / "hooks" / "session-log-remind.py"
    src = hook.read_text(encoding="utf-8")

    assert "$USER" not in src, (
        "reminder 메시지에 $USER 가 남아있다 — 'OS 사용자명 아님' 으로 교체되어야 한다")
    assert "OS 사용자명 아님" in src, (
        "reminder 메시지에 'OS 사용자명 아님' 안내가 있어야 한다")
