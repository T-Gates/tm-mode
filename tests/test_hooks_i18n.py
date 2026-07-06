"""PR-i1 — 훅 주입물 i18n 테스트.

계약(codex 문답 확정):
  - locale 소스 = team.config.json 의 team.locale, 정규화 ko* → ko / 그 외 → en.
  - 폴백: config 읽힘 + team.locale 없음 → ko(구팀 무변화) /
          config 없음·파싱 실패·루트 invalid → en(제품 기본).
  - 엔진 소유 주입 문자열만 현지화 — 팀 작성물(memory/ 문서·INDEX 본문·summary 내용)은
    라벨만 바꾸고 본문은 절대 번역하지 않는다.

검증 표면:
  1) i18n.team_lang 폴백 4케이스
  2) session-start 주입 — en 팀 = 영어 라벨 + guidelines.en.md, ko 팀 = 기존 한국어
  3) session-log-remind — en 팀 리마인더가 영어
  4) kb-write-guard — en 팀 deny 가 영어 (__file__ 루트 계약: 훅+i18n 을 tmp 루트에 복사)
  5) confirm-action — en 팀 deny 가 영어

안전 철칙: 실 호스트 무접촉 — 전부 tmp_path 격리.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import i18n as _i18n  # noqa: E402

PY = sys.executable
SESSION_START = REPO / "infra" / "hooks" / "session-start.py"
REMIND = REPO / "infra" / "hooks" / "session-log-remind.py"
KB_GUARD = REPO / "infra" / "hooks" / "kb-write-guard.py"
CONFIRM = REPO / "infra" / "hooks" / "confirm-action.py"


def _write_config(root: Path, *, locale: str | None, extra: dict | None = None) -> None:
    """team.config.json 생성. locale=None 이면 team 블록에 locale 키 자체를 생략."""
    team: dict = {"name": "t"}
    if locale is not None:
        team["locale"] = locale
    cfg: dict = {"team": team}
    if extra:
        cfg.update(extra)
    (root / "team.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


# ═══ 1) team_lang 폴백 계약 ═══════════════════════════════════════════════════

def test_team_lang_en_locale(tmp_path):
    """team.locale=en_US → en."""
    _write_config(tmp_path, locale="en_US")
    assert _i18n.team_lang(str(tmp_path)) == "en"


def test_team_lang_ko_locale(tmp_path):
    """team.locale=ko_KR → ko."""
    _write_config(tmp_path, locale="ko_KR")
    assert _i18n.team_lang(str(tmp_path)) == "ko"


def test_team_lang_missing_locale_field_is_ko(tmp_path):
    """config 는 읽히는데 team.locale 없음 → ko (구팀 무변화 계약)."""
    _write_config(tmp_path, locale=None)
    assert _i18n.team_lang(str(tmp_path)) == "ko"


def test_team_lang_no_config_is_en(tmp_path):
    """config 파일 없음 → en (제품 기본)."""
    assert _i18n.team_lang(str(tmp_path)) == "en"


def test_team_lang_broken_config_is_en(tmp_path):
    """파싱 실패·루트 invalid → en."""
    (tmp_path / "team.config.json").write_text("{not json", encoding="utf-8")
    assert _i18n.team_lang(str(tmp_path)) == "en"
    (tmp_path / "team.config.json").write_text("[1,2]", encoding="utf-8")
    assert _i18n.team_lang(str(tmp_path)) == "en"


def test_team_lang_normalization_ko_star(tmp_path):
    """정규화: ko* → ko, 그 외(미지 locale 포함) → en."""
    _write_config(tmp_path, locale="ko")
    assert _i18n.team_lang(str(tmp_path)) == "ko"
    _write_config(tmp_path, locale="ko_KR.UTF-8")
    assert _i18n.team_lang(str(tmp_path)) == "ko"
    _write_config(tmp_path, locale="fr_FR")
    assert _i18n.team_lang(str(tmp_path)) == "en"


# ═══ 2) session-start 주입 ════════════════════════════════════════════════════

def _run_session_start(root: Path) -> subprocess.CompletedProcess:
    env = {"TEAMMODE_HOME": str(root), "PATH": "/usr/bin:/bin"}
    if "XDG_STATE_HOME" in os.environ:
        env["XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    return subprocess.run(
        [PY, str(SESSION_START)],
        input=json.dumps({"event": "SessionStart", "agent": "claude"}),
        capture_output=True, text=True, env=env)


def _seed_team(root: Path, *, locale: str | None) -> None:
    (root / "memory" / "team" / "sessions").mkdir(parents=True, exist_ok=True)
    (root / ".teammode-active").write_text("")
    _write_config(root, locale=locale)


def _ctx(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_session_start_en_team_injects_english(tmp_path):
    """locale=en_US 팀 → 영어 라벨 + guidelines.en.md 내용."""
    _seed_team(tmp_path, locale="en_US")
    ctx = _ctx(_run_session_start(tmp_path))
    assert "[teammode] Team mode active — session start context:" in ctx
    assert "--- Recent work by member (summary) ---" in ctx
    # 빈 팀 안내도 영어
    assert "No session logs yet" in ctx
    # guidelines.en.md(엔진 영어판) 주입 — 한국어 지침이 아니어야 한다
    assert "Team Mode Operating Guidelines" in ctx
    assert "팀모드 운영 지침" not in ctx
    # 세션로그 규칙 블록도 영어
    assert "--- Session log rules" in ctx
    # 한국어 라벨이 남아 있으면 안 된다
    assert "팀 모드 활성" not in ctx
    assert "멤버별 최근 작업" not in ctx


def test_session_start_ko_team_keeps_korean(tmp_path):
    """locale=ko_KR 팀 → 기존 한국어 주입 계약 보존."""
    _seed_team(tmp_path, locale="ko_KR")
    ctx = _ctx(_run_session_start(tmp_path))
    assert "[teammode] 팀 모드 활성 — 세션 시작 맥락:" in ctx
    assert "--- 멤버별 최근 작업 (summary) ---" in ctx
    assert "아직 세션로그 없음" in ctx
    assert "팀모드 운영 지침" in ctx        # 엔진 ko guidelines
    assert "--- 세션로그 규칙" in ctx
    assert "Team mode active" not in ctx


def test_session_start_en_index_label_only_content_untranslated(tmp_path):
    """INDEX 라벨은 영어, 본문(팀 작성물)은 무번역 그대로."""
    _seed_team(tmp_path, locale="en_US")
    (tmp_path / "memory" / "INDEX.md").write_text(
        "# INDEX\n한국어 본문 그대로 KEEPME\n", encoding="utf-8")
    ctx = _ctx(_run_session_start(tmp_path))
    assert "--- Team memory INDEX ---" in ctx
    assert "한국어 본문 그대로 KEEPME" in ctx
    assert "--- 팀 메모리 INDEX ---" not in ctx


def test_session_start_en_team_custom_guidelines_untranslated(tmp_path):
    """팀 커스텀 memory/team/guidelines.md 는 en 팀이어도 그대로 주입(무번역)."""
    _seed_team(tmp_path, locale="en_US")
    gd = tmp_path / "memory" / "team"
    (gd / "guidelines.md").write_text("팀커스텀지침토큰QQZZ\n", encoding="utf-8")
    ctx = _ctx(_run_session_start(tmp_path))
    assert "팀커스텀지침토큰QQZZ" in ctx


def test_session_start_en_rules_match_slog_module(tmp_path):
    """en 규칙 블록 == _slog_rules.session_log_rules('en') — 단일 소스 유지."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_slog_rules_i18n_probe", REPO / "infra" / "hooks" / "_slog_rules.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.session_log_rules("ko") == mod.SESSION_LOG_RULES  # 하위호환
    en_rules = mod.session_log_rules("en")
    assert len(en_rules.splitlines()) <= 6
    _seed_team(tmp_path, locale="en_US")
    ctx = _ctx(_run_session_start(tmp_path))
    assert en_rules in ctx


# ═══ 3) session-log-remind ═══════════════════════════════════════════════════

def _today_date_str() -> str:
    kst = datetime.now(timezone(timedelta(hours=9)))
    if kst.hour < 6:
        return (kst - timedelta(days=1)).strftime("%Y-%m-%d")
    return kst.strftime("%Y-%m-%d")


def _fire_remind(tmp_path: Path, *, locale: str | None) -> subprocess.CompletedProcess:
    """멤버 경로 강발화(파일 없음 age=9999) — locale 만 달리해 발화 언어를 검증."""
    root = tmp_path / "team"
    root.mkdir(exist_ok=True)
    (root / ".teammode-active").write_text("")
    team: dict = {"name": "t"}
    if locale is not None:
        team["locale"] = locale
    (root / "team.config.json").write_text(
        json.dumps({"team": team, "members": [{"name": "eunsu"}]}),
        encoding="utf-8")
    agent = f"claude-i18n-{locale}"
    state = tmp_path / (f"teammode-remind-state-{agent}-eunsu-"
                        + __import__("hashlib").sha256(
                            str(root).encode()).hexdigest()[:8] + ".json")
    state.write_text(json.dumps({
        "count": 4, "last_mtime": 0.0, "date": _today_date_str(),
        "last_strong_remind": 0.0}))
    env = {**os.environ, "TEAMMODE_HOME": str(root), "TMPDIR": str(tmp_path)}
    env.pop("TEAMMODE_MEMBER", None)
    return subprocess.run(
        [PY, str(REMIND)],
        input=json.dumps({"event": "UserPromptSubmit", "prompt": "hi",
                          "agent": agent}),
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(root))


def test_remind_en_team_is_english(tmp_path):
    proc = _fire_remind(tmp_path, locale="en_US")
    assert proc.returncode == 0
    assert proc.stdout.strip(), f"발화 실패: {proc.stderr}"
    obj = json.loads(proc.stdout)
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "Session log" in ctx, f"영어 리마인더 아님: {ctx!r}"
    assert "세션로그" not in ctx and "세션 로그" not in ctx
    assert "(rules: see the session-start injection)" in ctx
    assert "Session log" in obj["systemMessage"]


def test_remind_ko_team_stays_korean(tmp_path):
    proc = _fire_remind(tmp_path, locale="ko_KR")
    assert proc.returncode == 0
    assert proc.stdout.strip(), f"발화 실패: {proc.stderr}"
    obj = json.loads(proc.stdout)
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "세션 로그 30분 이상 미갱신" in ctx
    assert "(규칙: 세션 시작 주입 참조)" in ctx
    assert "세션로그 미작성" in obj["systemMessage"]


# ═══ 4) kb-write-guard ═══════════════════════════════════════════════════════
# 이 훅은 TEAMMODE_HOME 무신뢰 — __file__ 기준 팀 루트. 픽스처는 훅(+i18n)을
# tmp 루트에 복사해 __file__ 이 tmp 루트를 가리키게 한다(test_kb_write_guard 패턴).

def _install_guard(root: Path, *, with_i18n: bool) -> Path:
    hooks_dir = root / "infra" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / "kb-write-guard.py"
    shutil.copy2(str(KB_GUARD), str(dst))
    if with_i18n:
        shutil.copy2(str(REPO / "infra" / "i18n.py"), str(root / "infra" / "i18n.py"))
    return dst


def _run_guard(root: Path, *, with_i18n: bool) -> subprocess.CompletedProcess:
    guard = _install_guard(root, with_i18n=with_i18n)
    payload = {
        "event": "PreToolUse", "action": "file_edit",
        "files": [str(root / "memory" / "x.md")],
        "tool": {"kind": "builtin", "name": "Write"},
        "agent": "claude", "raw": {},
    }
    env = {k: v for k, v in os.environ.items()
           if k not in ("TEAMMODE_HOME", "CLAUDE_SESSION_ID",
                        "CLAUDE_CODE_SESSION_ID")}
    return subprocess.run([PY, str(guard)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def test_kb_guard_en_team_denies_in_english(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    _write_config(root, locale="en_US")
    (root / ".teammode-active").write_text("")
    proc = _run_guard(root, with_i18n=True)
    assert proc.returncode == 2
    reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Direct edits under memory/" in reason, f"영어 deny 아님: {reason!r}"
    assert "tm-manage-memory" in reason
    assert "직접 편집" not in reason


def test_kb_guard_ko_team_denies_in_korean(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    _write_config(root, locale="ko_KR")
    (root / ".teammode-active").write_text("")
    proc = _run_guard(root, with_i18n=True)
    assert proc.returncode == 2
    reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "직접 편집은 금지" in reason
    assert "tm-manage-memory" in reason


def test_kb_guard_without_i18n_module_falls_back_korean(tmp_path):
    """부분 배포(i18n.py 부재) → 종전 한국어 deny 유지(무해 강등)."""
    root = tmp_path / "team"
    root.mkdir()
    _write_config(root, locale="en_US")  # locale 이 en 이어도 i18n 부재면 ko 폴백
    (root / ".teammode-active").write_text("")
    proc = _run_guard(root, with_i18n=False)
    assert proc.returncode == 2
    reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "직접 편집은 금지" in reason


# ═══ 5) confirm-action ═══════════════════════════════════════════════════════

def _run_confirm(root: Path) -> subprocess.CompletedProcess:
    payload = {
        "event": "PreToolUse",
        "tool": {"kind": "mcp", "server": "linear", "name": "create_issue"},
        "agent": "claude",
    }
    return subprocess.run(
        [PY, str(CONFIRM), "teammode-linear-create-allow"],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "TEAMMODE_HOME": str(root)})


def test_confirm_action_en_team_denies_in_english(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    _write_config(root, locale="en_US")
    (root / ".teammode-active").write_text("")
    proc = _run_confirm(root)
    assert proc.returncode == 2
    reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "requires human confirmation" in reason, f"영어 deny 아님: {reason!r}"
    assert "사람 확인" not in reason


def test_confirm_action_ko_team_denies_in_korean(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    _write_config(root, locale="ko_KR")
    (root / ".teammode-active").write_text("")
    proc = _run_confirm(root)
    assert proc.returncode == 2
    reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "사람 확인이 필요합니다" in reason


# ── codex 적대검수 반영(2026-07-07): 키 드리프트·엣지 회귀락 ──

def test_all_hook_t_keys_exist_in_catalog():
    """[P2] 모든 훅의 t()/i18n.t() hook_* 키가 en_US 카탈로그에 존재 — 오타/누락 시
    조용히 키-원문 폴백되는 드리프트를 메타로 차단."""
    import re as _re, sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "infra"))
    import i18n as _i18n
    hooks = (_P(__file__).resolve().parents[1] / "infra" / "hooks")
    pat = _re.compile(r'(?:_i18n\.t|_t|i18n\.t|\bt)\(\s*["\'](hook_[a-z0-9_]+)["\']')
    keys = set()
    for f in hooks.glob("*.py"):
        keys |= set(pat.findall(f.read_text(encoding="utf-8")))
    assert keys, "훅에서 hook_* 키를 하나도 못 찾음(정규식 점검)"
    cat = _i18n.MESSAGES["en_US"]
    missing = sorted(k for k in keys if k not in cat)
    assert not missing, f"en_US 카탈로그 누락 키: {missing}"


import pytest as _pytest


@_pytest.mark.parametrize("cfg,expected", [
    ({"team": {"locale": None}}, "ko"),       # 명시 null
    ({"team": {"locale": ""}}, "ko"),          # 빈 문자열
    ({"team": {"locale": "   "}}, "ko"),       # 공백
    ({"team": []}, "ko"),                       # team 이 비-dict → locale 없음 취급
    ({"team": {"locale": "EN_us"}}, "en"),     # 대소문자 무관
    ({"team": {"locale": "ko"}}, "ko"),        # 2글자
    (None, "en"),                               # config 파싱 실패 전달
    ("not a dict", "en"),                       # 루트 비-dict
])
def test_team_lang_from_config_edges(cfg, expected):
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "infra"))
    import i18n as _i18n
    assert _i18n.team_lang_from_config(cfg) == expected
