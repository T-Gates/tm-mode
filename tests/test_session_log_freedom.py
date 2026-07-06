"""세션로그 자유편집 기능 회귀 테스트 (test_session_log_freedom.py).

feat/session-log-freedom 에서 추가된 신규 로직 3종을 커버한다:

  대상 1 — kb-write-guard.py::_is_own_session_log(file_path, team_root)
  대상 2 — install_lib.py::find_similar_names(name, existing)
  대상 3 — install_lib.py::inject_member_env_settings(settings_path, member_name)

안전 철칙: tmp_path 격리 — 실호스트 무접촉.
모듈 로드: 기존 test_kb_write_guard.py 의 importlib 방식 그대로(KB_GUARD 절대경로).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "infra" / "hooks"
KB_GUARD = HOOKS / "kb-write-guard.py"
sys.path.insert(0, str(REPO / "infra"))

import install_lib as il  # noqa: E402


# ─────────────────────────── 헬퍼 ───────────────────────────────────────────

def _load_guard_mod(name: str = "kbg_freedom"):
    """kb-write-guard.py 를 독립 모듈로 로드 (importlib).

    test_kb_write_guard.py 의 _load_guard_mod 와 동일 방식.
    """
    spec = importlib.util.spec_from_file_location(name, KB_GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_sessions_dir(tmp_path: Path, member: str) -> Path:
    """tmp_path 안에 memory/team/sessions/<member>/ 구조를 만들고 sessions 루트를 반환."""
    sessions = tmp_path / "memory" / "team" / "sessions"
    (sessions / member).mkdir(parents=True, exist_ok=True)
    return sessions


# ═══════════════════════════════════════════════════════════════════════════
# 대상 1 — _is_own_session_log
# ═══════════════════════════════════════════════════════════════════════════

class TestIsOwnSessionLog:
    """_is_own_session_log(file_path, team_root) 단위 테스트."""

    def _fn(self, monkeypatch, member: str, tmp_path: Path):
        """모듈을 로드하고, TEAMMODE_MEMBER=member 를 세팅 후 함수를 반환."""
        mod = _load_guard_mod(f"kbg_{member}_test")
        monkeypatch.setenv("TEAMMODE_MEMBER", member)
        return mod._is_own_session_log

    # ── True 케이스 ─────────────────────────────────────────────────────────

    def test_own_file_returns_true(self, monkeypatch, tmp_path):
        """TEAMMODE_MEMBER=bob + sessions/bob/x.md → True."""
        _make_sessions_dir(tmp_path, "bob")
        fn = self._fn(monkeypatch, "bob", tmp_path)
        fp = str(tmp_path / "memory" / "team" / "sessions" / "bob" / "x.md")
        assert fn(fp, str(tmp_path)) is True

    # ── False 케이스 ────────────────────────────────────────────────────────

    def test_other_member_returns_false(self, monkeypatch, tmp_path):
        """남의 세션로그(sessions/jonathon/x.md) → False."""
        _make_sessions_dir(tmp_path, "bob")
        _make_sessions_dir(tmp_path, "jonathon")
        fn = self._fn(monkeypatch, "bob", tmp_path)
        fp = str(tmp_path / "memory" / "team" / "sessions" / "jonathon" / "x.md")
        assert fn(fp, str(tmp_path)) is False

    def test_knowledge_path_returns_false(self, monkeypatch, tmp_path):
        """메모리 경로(team/decisions/x.md) → False."""
        _make_sessions_dir(tmp_path, "bob")
        fn = self._fn(monkeypatch, "bob", tmp_path)
        fp = str(tmp_path / "memory" / "team" / "decisions" / "x.md")
        assert fn(fp, str(tmp_path)) is False

    def test_index_md_returns_false(self, monkeypatch, tmp_path):
        """memory/INDEX.md → False (세션로그 디렉토리 아님)."""
        _make_sessions_dir(tmp_path, "bob")
        fn = self._fn(monkeypatch, "bob", tmp_path)
        fp = str(tmp_path / "memory" / "INDEX.md")
        assert fn(fp, str(tmp_path)) is False

    def test_no_env_member_returns_false(self, monkeypatch, tmp_path):
        """TEAMMODE_MEMBER 미설정 → False (fail-closed)."""
        _make_sessions_dir(tmp_path, "bob")
        mod = _load_guard_mod("kbg_no_member")
        monkeypatch.delenv("TEAMMODE_MEMBER", raising=False)
        fp = str(tmp_path / "memory" / "team" / "sessions" / "bob" / "x.md")
        assert mod._is_own_session_log(fp, str(tmp_path)) is False

    def test_symlink_sessions_dir_returns_false(self, monkeypatch, tmp_path):
        """sessions/<member> 디렉토리 자체가 symlink(→ decisions) → False (우회 차단)."""
        member = "bob"
        decisions = tmp_path / "memory" / "team" / "decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        sessions = tmp_path / "memory" / "team" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        # sessions/bob 를 decisions 디렉토리로 symlink
        own_dir = sessions / member
        own_dir.symlink_to(decisions)
        fn = self._fn(monkeypatch, member, tmp_path)
        fp = str(own_dir / "x.md")
        assert fn(fp, str(tmp_path)) is False

    def test_unicode_member_name_returns_false(self, monkeypatch, tmp_path):
        """멤버명 유니코드("하늘") → False (슬러그 화이트리스트 위반)."""
        member = "하늘"
        _make_sessions_dir(tmp_path, member)
        fn = self._fn(monkeypatch, member, tmp_path)
        fp = str(tmp_path / "memory" / "team" / "sessions" / member / "x.md")
        assert fn(fp, str(tmp_path)) is False

    def test_member_name_with_space_returns_false(self, monkeypatch, tmp_path):
        """멤버명에 공백("a b") → False (슬러그 화이트리스트 위반)."""
        member = "a b"
        # 실제 디렉토리를 만들 수 없으므로 env 세팅만으로 충분 — 정규식이 먼저 차단
        mod = _load_guard_mod("kbg_space_member")
        monkeypatch.setenv("TEAMMODE_MEMBER", member)
        fp = str(tmp_path / "memory" / "team" / "sessions" / "a b" / "x.md")
        assert mod._is_own_session_log(fp, str(tmp_path)) is False

    def test_member_name_with_slash_returns_false(self, monkeypatch, tmp_path):
        """멤버명에 슬래시("a/b") → False (슬러그 화이트리스트 위반 — 경로 트래버설)."""
        member = "a/b"
        mod = _load_guard_mod("kbg_slash_member")
        monkeypatch.setenv("TEAMMODE_MEMBER", member)
        fp = str(tmp_path / "memory" / "team" / "sessions" / "a" / "b" / "x.md")
        assert mod._is_own_session_log(fp, str(tmp_path)) is False


# ═══════════════════════════════════════════════════════════════════════════
# 대상 2 — find_similar_names
# ═══════════════════════════════════════════════════════════════════════════

class TestFindSimilarNames:
    """install_lib.find_similar_names(name, existing) 단위 테스트."""

    def test_close_edit_distance_detected(self):
        """jonathan vs jonathon (편집거리 1) → 유사 목록에 포함."""
        result = il.find_similar_names("jonathan", ["jonathon", "bob"])
        assert result == ["jonathon"]

    def test_dissimilar_name_not_detected(self):
        """kim vs [jonathan, alexandra] (편집거리 큼) → 빈 리스트."""
        result = il.find_similar_names("kim", ["jonathan", "alexandra"])
        assert result == []

    def test_identical_name_excluded(self):
        """동일 이름(bob vs [bob]) → 제외(UNIQUE 처리는 register_member 몫)."""
        result = il.find_similar_names("bob", ["bob"])
        assert result == []

    def test_prefix_similarity_detected(self):
        """공통 프리픽스 유사("jon" vs ["jonathon"]) → 유사로 검출.

        "jon" 의 80% = 2.4 → ceil 2. "jonathon" 과의 공통 프리픽스 "jon" = 3 >= 2.
        편집거리 = 5(> max_distance=2)이지만 프리픽스 조건으로 잡힌다.
        """
        result = il.find_similar_names("jon", ["jonathon"])
        assert "jonathon" in result

    def test_empty_existing_returns_empty(self):
        """기존 이름 목록이 비어있으면 항상 빈 리스트."""
        result = il.find_similar_names("bob", [])
        assert result == []

    def test_exact_match_among_many_excluded(self):
        """여러 이름 중 동일 이름은 제외, 유사 이름만 반환."""
        result = il.find_similar_names("alice", ["alice", "alize"])
        assert "alice" not in result
        assert "alize" in result


# ═══════════════════════════════════════════════════════════════════════════
# 대상 3 — inject_member_env_settings
# ═══════════════════════════════════════════════════════════════════════════

class TestInjectMemberEnvSettings:
    """install_lib.inject_member_env_settings(settings_path, member_name) 단위 테스트."""

    def test_first_injection_returns_true_and_sets_member(self, tmp_path):
        """첫 주입 → True, settings.json["env"]["TEAMMODE_MEMBER"] == member_name."""
        settings = tmp_path / "settings.json"
        result = il.inject_member_env_settings(settings, "bob")
        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["TEAMMODE_MEMBER"] == "bob"

    def test_existing_env_keys_preserved(self, tmp_path):
        """기존 env 키(OTHER_VAR)가 보존된다."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "env": {"OTHER_VAR": "hello"},
            "hooks": {}
        }, indent=2), encoding="utf-8")
        il.inject_member_env_settings(settings, "bob")
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["OTHER_VAR"] == "hello"
        assert data["env"]["TEAMMODE_MEMBER"] == "bob"

    def test_other_top_level_keys_preserved(self, tmp_path):
        """hooks 등 다른 최상위 키가 보존된다."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "hooks": {"SessionStart": []},
        }, indent=2), encoding="utf-8")
        il.inject_member_env_settings(settings, "bob")
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "hooks" in data
        assert data["hooks"]["SessionStart"] == []

    def test_same_value_reinject_returns_false(self, tmp_path):
        """같은 값으로 재호출 → False (멱등)."""
        settings = tmp_path / "settings.json"
        il.inject_member_env_settings(settings, "bob")
        result = il.inject_member_env_settings(settings, "bob")
        assert result is False

    def test_different_value_reinject_returns_true(self, tmp_path):
        """다른 멤버명으로 재호출 → True (값 갱신)."""
        settings = tmp_path / "settings.json"
        il.inject_member_env_settings(settings, "bob")
        result = il.inject_member_env_settings(settings, "jonathon")
        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["env"]["TEAMMODE_MEMBER"] == "jonathon"

    def test_nonexistent_settings_created(self, tmp_path):
        """settings.json 이 없어도 새로 생성된다."""
        settings = tmp_path / "subdir" / "settings.json"
        assert not settings.exists()
        result = il.inject_member_env_settings(settings, "bob")
        assert result is True
        assert settings.is_file()
