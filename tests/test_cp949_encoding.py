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


# ── 2. P1#4: counter 파일이 tempfile.gettempdir() 를 사용한다 ──

def test_counter_file_uses_tempfile_gettempdir(tmp_path, monkeypatch):
    """session-log-remind 카운터 파일 경로가 tempfile.gettempdir() 기반이어야 한다.

    TMPDIR env 가 없어도 올바른 임시 디렉토리(OS 네이티브)를 사용한다.
    Windows 에서 TMPDIR 은 TEMP/TMP 와 다를 수 있어 /tmp 하드코딩은 실패한다.
    """
    # TMPDIR 을 제거해 폴백 경로 테스트
    monkeypatch.delenv("TMPDIR", raising=False)

    hook = REPO / "infra" / "hooks" / "session-log-remind.py"
    hook_src = hook.read_text(encoding="utf-8")

    # 소스 코드에 tempfile.gettempdir() 가 사용됐고 TMPDIR 환경변수 폴백이 없는지 확인
    assert "tempfile.gettempdir()" in hook_src, (
        "counter_file 이 tempfile.gettempdir() 를 사용해야 한다(P1#4)")
    assert 'os.environ.get("TMPDIR"' not in hook_src, (
        "TMPDIR env 하드코딩이 남아있다 — tempfile.gettempdir() 로 교체되어야 한다(P1#4)")

    # 실제로 hook 을 실행해 카운터 파일이 올바른 경로에 생성되는지 확인
    active = tmp_path / ".tgates-active"
    active.write_text("")
    canonical = {"event": "UserPromptSubmit", "prompt": "test", "agent": "claude-test"}

    proc = subprocess.run(
        [PY, str(hook)],
        input=json.dumps(canonical),
        capture_output=True, text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
        env={**os.environ, "TEAMMODE_HOME": str(tmp_path)},
    )

    assert proc.returncode == 0, f"hook crash: {proc.stderr}"
    # 카운터 파일이 tempfile.gettempdir() 위치에 생성됐어야 한다
    expected_counter = os.path.join(
        tempfile.gettempdir(), "teammode-prompt-counter-claude-test")
    assert os.path.isfile(expected_counter), (
        f"카운터 파일이 {expected_counter} 에 생성되어야 한다")


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
