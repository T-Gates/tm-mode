"""tm-context 스킬 conformance 테스트.

검증 목록:
  - infra/skills/base/tm-context/SKILL.md 존재
  - frontmatter(name · description) 파싱 유효
  - 트리거 키워드 존재 ("팀 현황", "팀원 뭐해", "context")
  - 핵심 엔진 동사·플래그 명시 확인 (context --root --json)
  - install_skills 가 tm-context 를 심링크 대상에 포함
  - uninstall 이 tm-context 심링크를 제거(소유 판정)
  - context 동사가 실제 엔진 _KNOWN_VERBS 에 있는지 확인
  - L1/graceful 경계 문구 포함 확인

모든 테스트는 tmp_path 격리 — 실 ~/.claude/skills 무접촉.
"""
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_MD = REPO / "infra" / "skills" / "core" / "tm-context" / "SKILL.md"

sys.path.insert(0, str(REPO / "infra"))

import runpy

_CLAUDE = runpy.run_path(
    str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
    run_name="__tm_context_conformance__",
)
ClaudeAdapter = _CLAUDE["Adapter"]


# ── SKILL.md 파일 자체 ──

def test_skill_md_exists():
    assert SKILL_MD.is_file(), "infra/skills/core/tm-context/SKILL.md 가 없다"


def _parse_frontmatter(text: str) -> dict:
    """--- ... --- 블록에서 key: value 단순 파싱."""
    fm: dict = {}
    if not text.startswith("---"):
        return fm
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def test_frontmatter_name():
    fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    assert fm.get("name") == "tm-context", (
        f"name 필드가 'tm-context' 이어야 한다. 실제: {fm.get('name')!r}"
    )


def test_frontmatter_description_nonempty():
    fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    desc = fm.get("description", "")
    assert desc, "description 이 비어 있다"
    assert len(desc) > 10, "description 이 너무 짧다"


# ── 트리거 키워드 ──

@pytest.mark.parametrize("keyword", [
    "팀 현황",
    "팀원 뭐해",
    "context",
    "맥락 알려줘",
])
def test_trigger_keywords_in_description(keyword):
    fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    desc = fm.get("description", "")
    assert keyword in desc, (
        f"description 에 트리거 키워드 '{keyword}' 가 없다"
    )


# ── 엔진 동사·플래그 계약 키워드 ──

@pytest.mark.parametrize("keyword", [
    "teammode.py context",
    "--root",
    "--json",
])
def test_skill_md_contains_engine_keyword(keyword):
    text = SKILL_MD.read_text(encoding="utf-8")
    assert keyword in text, (
        f"SKILL.md 에 '{keyword}' 가 없다 — L1 코어 호출 명세가 빠져 있다"
    )


# ── 읽기 전용 선언 ──

def test_skill_md_declares_readonly():
    """tm-context 는 읽기 전용 — 파일/상태 변경 금지 선언이 있어야 한다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "읽기 전용" in text or "read" in text.lower(), (
        "SKILL.md 에 읽기 전용 선언이 없다"
    )


# ── graceful skip 선언 ──

@pytest.mark.parametrize("phrase", [
    "skip",
    "graceful",
])
def test_skill_md_has_graceful_skip(phrase):
    """L2 미연결·decisions 미구현 시 graceful skip 명세가 있어야 한다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert phrase in text.lower(), (
        f"SKILL.md 에 graceful skip 관련 문구('{phrase}')가 없다"
    )


# ── 세션로그 없음 안내 ──

def test_skill_md_has_empty_session_log_guidance():
    """갓 셋업(세션로그 0개) 시 안내 문구가 있어야 한다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "세션로그" in text and ("없" in text or "기록" in text), (
        "SKILL.md 에 세션로그 0개 시 안내가 없다"
    )


# ── install_skills 포함 검증 ──

def _scaffold(tmp_path):
    """tmp 팀 루트 — 실 infra/skills/base·core 전체 복사."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "adapter.py",
        root / "infra" / "agents" / "claude" / "adapter.py",
    )
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "events.json",
        root / "infra" / "agents" / "claude" / "events.json",
    )
    shutil.copytree(
        REPO / "infra" / "skills" / "base",
        root / "infra" / "skills" / "base",
    )
    shutil.copytree(
        REPO / "infra" / "skills" / "core",
        root / "infra" / "skills" / "core",
    )
    return root


def _claude_adapter(root, tmp_path):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),
        skills_dir=str(tmp_path / "claude-skills"),
    )


def test_tm_context_in_source_skills(tmp_path):
    """adapter._skill_sources(layer='core') 가 tm-context 를 목록에 포함한다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    names = {s.name for s in a._skill_sources(layer="core")}
    assert "tm-context" in names, (
        f"_skill_sources(layer='core') 에 tm-context 가 없다. 실제 목록: {names}"
    )
    # base 레이어에는 없어야 한다
    base_names = {s.name for s in a._skill_sources(layer="base")}
    assert "tm-context" not in base_names, (
        f"tm-context 가 base 레이어에 있다 — core 로 이동되어야 한다. base 목록: {base_names}"
    )


def test_install_skills_creates_tm_context_link(tmp_path):
    """install_skills(layer='core') 실행 후 tm-context 심링크가 생성된다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills(layer="core")
    link = tmp_path / "claude-skills" / "tm-context"
    assert link.exists() or link.is_symlink(), (
        "tm-context 심링크/복사본이 생성되지 않았다"
    )
    assert (link / "SKILL.md").is_file(), "tm-context/SKILL.md 가 없다"


def test_install_skills_tm_context_points_to_source(tmp_path):
    """tm-context 심링크가 실제 core 소스 디렉토리를 가리킨다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills(layer="core")
    link = tmp_path / "claude-skills" / "tm-context"
    if link.is_symlink():
        assert link.resolve() == (
            root / "infra" / "skills" / "core" / "tm-context"
        ).resolve()


def test_uninstall_removes_tm_context(tmp_path):
    """uninstall_skills 가 tm-context 심링크를 제거한다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills(layer="core")
    a.uninstall_skills()
    assert not (tmp_path / "claude-skills" / "tm-context").exists(), (
        "uninstall 후 tm-context 가 남아 있다 — 소유 판정 오류"
    )


# ── 엔진 _KNOWN_VERBS 정합 ──

def _load_teammode():
    return runpy.run_path(
        str(REPO / "infra" / "teammode.py"),
        run_name="__tm_context_conformance__",
    )


def test_engine_context_verb_in_known_verbs():
    """context 동사가 엔진 _KNOWN_VERBS 에 있다 — SKILL.md 가 참조하는 동사가 실재한다."""
    mod = _load_teammode()
    known = set(mod["_KNOWN_VERBS"])
    assert "context" in known, (
        f"'context' 가 엔진 _KNOWN_VERBS 에 없다. 실제: {known}"
    )


def test_engine_json_flag_in_value_flags():
    """--json 플래그가 엔진 _VALUE_FLAGS 에 없어도 bool 플래그로 파싱된다(회귀 방지).

    --json 은 값 없는 스위치이므로 _VALUE_FLAGS 에 들어가지 않는 것이 정상이다.
    이 테스트는 엔진 파싱이 --json 을 실제로 처리한다는 것을 opts['json'] 키로 확인.
    """
    mod = _load_teammode()
    _, opts = mod["_parse_args"](["context", "--root", ".", "--json"])
    assert opts.get("json") is True, (
        "--json 플래그가 opts['json']=True 로 파싱되지 않는다"
    )


# ── cmd_context 실제 JSON 계약 검증 ──

def _make_team_root(tmp_path) -> Path:
    """테스트용 최소 팀 루트 — sessions 디렉토리만 생성."""
    root = tmp_path / "teamroot"
    root.mkdir()
    return root


def _write_session(team_root: Path, author: str, date_str: str, summary: str,
                   body: str = "") -> Path:
    """멤버 세션로그 파일을 작성하고 경로를 반환한다."""
    sessions_dir = team_root / "memory" / "team" / "sessions" / author
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_path = sessions_dir / f"{date_str}.md"
    log_path.write_text(
        f"---\nauthor: {author}\ndate: {date_str}\nsummary: {summary}\n---\n{body}",
        encoding="utf-8",
    )
    return log_path


def _run_context_json(team_root: Path) -> dict:
    """cmd_context(as_json=True)를 직접 호출해 stdout을 JSON으로 파싱한다."""
    import io
    import contextlib
    import json as _json

    mod = _load_teammode()
    cmd_context = mod["cmd_context"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_context(team_root, as_json=True)
    assert rc == 0, f"cmd_context 가 exit {rc} 를 반환했다"
    return _json.loads(buf.getvalue())


def test_cmd_context_json_top_level_keys(tmp_path):
    """cmd_context --json 출력의 최상위 키가 state/index/members/personality_customized 여야 한다."""
    root = _make_team_root(tmp_path)
    data = _run_context_json(root)
    assert set(data.keys()) == {"state", "index", "members", "personality_customized"}, (
        f"최상위 키 불일치. 실제: {set(data.keys())}"
    )


def test_cmd_context_json_member_fields(tmp_path):
    """members 배열 원소가 author/date/summary/file/role 필드를 가져야 한다."""
    root = _make_team_root(tmp_path)
    _write_session(root, "alice", "2026-06-17", "MQTT 연동 테스트 중")
    data = _run_context_json(root)
    assert len(data["members"]) == 1, (
        f"세션로그 1개 있는 멤버가 members 에 없다. members={data['members']}"
    )
    m = data["members"][0]
    expected_keys = {"author", "date", "summary", "file", "role"}
    assert expected_keys.issubset(m.keys()), (
        f"멤버 필드 누락. 실제 키: {set(m.keys())}"
    )
    assert m["author"] == "alice"
    assert m["summary"] == "MQTT 연동 테스트 중"
    assert "alice" in m["file"]


def test_cmd_context_json_no_sessions_returns_empty_members(tmp_path):
    """세션로그 0개 팀 루트에서 members 가 빈 배열이고 exit 0 이어야 한다.

    L1 요약 경로가 세션로그 없어도 정상 종료되는지 확인 (SKILL.md 계약).
    """
    root = _make_team_root(tmp_path)
    data = _run_context_json(root)
    assert data["members"] == [], (
        f"세션로그 0개인데 members 가 비어있지 않다: {data['members']}"
    )
    assert data["state"] in ("on", "off")


def test_cmd_context_json_no_services_does_not_crash(tmp_path):
    """team.config.json 에 services 슬롯이 없어도 cmd_context 가 exit 0 이어야 한다.

    L2 미연결 시 L1 경로가 막히지 않는지 확인 (graceful skip 계약).
    """
    root = _make_team_root(tmp_path)
    # services 없는 최소 config
    (root / "team.config.json").write_text(
        '{"team": {"name": "test-team"}}', encoding="utf-8"
    )
    _write_session(root, "bob", "2026-06-17", "테스트 작업 중")
    data = _run_context_json(root)
    # L1 은 정상 동작해야 한다
    assert len(data["members"]) == 1
    assert data["members"][0]["author"] == "bob"


def test_cmd_context_json_member_role_from_config(tmp_path):
    """team.config.json 의 members[].role 이 JSON 멤버 필드에 반영된다."""
    root = _make_team_root(tmp_path)
    (root / "team.config.json").write_text(
        '{"members": [{"name": "carol", "role": "Backend"}]}', encoding="utf-8"
    )
    _write_session(root, "carol", "2026-06-17", "API 개발")
    data = _run_context_json(root)
    assert len(data["members"]) == 1
    assert data["members"][0]["role"] == "Backend", (
        f"role 필드가 config 에서 읽어지지 않았다: {data['members'][0]}"
    )


def test_skill_md_no_issue_verb_call():
    """SKILL.md 에 `issue --root . --json` 호출 지시가 없어야 한다.

    cmd_issue 는 MCP 조회를 하지 않으므로 이슈 조회 용도로 쓰면 안 된다(P1-2).
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    # 단순 `issue --root . --json` 형태의 bash 호출이 없어야 함
    assert "issue --root . --json" not in text, (
        "SKILL.md 에 `issue --root . --json` 호출 지시가 남아 있다 — P1-2 미반영"
    )


def test_skill_md_json_schema_has_required_fields():
    """SKILL.md 가 JSON 스키마에 author/date/summary/file/role 필드를 명시해야 한다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for field in ("author", "date", "summary", "file", "role"):
        assert field in text, (
            f"SKILL.md JSON 스키마에 '{field}' 필드 명시가 없다"
        )
