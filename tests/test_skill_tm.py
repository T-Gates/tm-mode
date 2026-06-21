"""tm 스킬 conformance 테스트.

검증 목록:
  - infra/skills/base/tm/SKILL.md 존재
  - frontmatter(name · description) 파싱 유효
  - ON/OFF 절차 키워드 존재(엔진 동사 인터페이스 계약)
  - install_skills 가 tm 을 심링크 대상에 포함
  - uninstall 이 tm 심링크를 제거(소유 판정)
  - OFF 커밋 명령에 --paths 경로 한정이 명시됨(P1 안전 회귀 방지)
  - commit --paths 가 실제로 해당 경로만 stage 함(git_ops 단위)
  - SKILL.md 동사·플래그가 엔진 _KNOWN_VERBS/_VALUE_FLAGS 와 정합

모든 테스트는 tmp_path 격리 — 실 ~/.claude/skills 무접촉.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_MD = REPO / "infra" / "skills" / "base" / "tm" / "SKILL.md"

sys.path.insert(0, str(REPO / "infra"))

import runpy
_CLAUDE = runpy.run_path(
    str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
    run_name="__tm_conformance__",
)
ClaudeAdapter = _CLAUDE["Adapter"]


# ── SKILL.md 파일 자체 ──

def test_skill_md_exists():
    assert SKILL_MD.is_file(), "infra/skills/base/tm/SKILL.md 가 없다"


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
    assert fm.get("name") == "tm", f"name 필드가 'tm' 이어야 한다. 실제: {fm.get('name')!r}"


def test_frontmatter_description_nonempty():
    fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    desc = fm.get("description", "")
    assert desc, "description 이 비어 있다"
    assert len(desc) > 10, "description 이 너무 짧다"


# ── ON/OFF 엔진 동사 계약 키워드 ──

@pytest.mark.parametrize("keyword", [
    "teammode.py on",
    "teammode.py off",
    "teammode.py pull",
    # "teammode.py log" — deprecated: 세션로그는 Read(끝 offset)+Edit 로 직접 기록(SKILL.md 안내 교체).
    "teammode.py commit",
    "teammode.py context",
    "--root",
    "--install",
])
def test_skill_md_contains_keyword(keyword):
    text = SKILL_MD.read_text(encoding="utf-8")
    assert keyword in text, (
        f"SKILL.md 에 '{keyword}' 가 없다 — "
        f"ON/OFF 절차에서 해당 엔진 동사/플래그를 명시해야 한다"
    )


def test_skill_md_no_push():
    """tm 은 push 금지 — --push 플래그를 절차 명령으로 쓰지 않는다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    # commit 명령에 --push 플래그가 들어가면 안 됨
    import re
    # "teammode.py commit ... --push" 형태 탐지
    assert not re.search(r"teammode\.py commit[^\n]*--push", text), (
        "SKILL.md 의 commit 명령에 --push 가 포함되어 있다 — tm 은 push 금지"
    )


def test_skill_md_has_off_confirmation():
    """OFF 절차에 사용자 확인 단계가 있어야 한다."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "확인" in text, "OFF 절차에 사용자 확인 요구가 없다"


# ── install_skills 포함 검증 ──

def _scaffold(tmp_path):
    """tmp 팀 루트 — 실 infra/skills/base 전체 복사."""
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


def test_tm_in_source_skills(tmp_path):
    """adapter._skill_sources() 가 tm 을 목록에 포함한다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    names = {s.name for s in a._skill_sources()}
    assert "tm" in names, f"_skill_sources 에 tm 이 없다. 실제 목록: {names}"


def test_install_skills_creates_tm_link(tmp_path):
    """install_skills 실행 후 tm 심링크가 생성된다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills()
    link = tmp_path / "claude-skills" / "tm"
    assert link.exists() or link.is_symlink(), "tm 심링크/복사본이 생성되지 않았다"
    assert (link / "SKILL.md").is_file(), "tm/SKILL.md 가 없다"


def test_install_skills_tm_points_to_source(tmp_path):
    """tm 심링크가 실제 소스 디렉토리를 가리킨다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills()
    link = tmp_path / "claude-skills" / "tm"
    if link.is_symlink():
        assert link.resolve() == (root / "infra" / "skills" / "base" / "tm").resolve()


def test_uninstall_removes_tm(tmp_path):
    """uninstall_skills 가 tm 심링크를 제거한다."""
    root = _scaffold(tmp_path)
    a = _claude_adapter(root, tmp_path)
    a.install_skills()
    a.uninstall_skills()
    assert not (tmp_path / "claude-skills" / "tm").exists(), (
        "uninstall 후 tm 이 남아 있다 — 소유 판정 오류"
    )


# ── P1 안전 회귀 방지: OFF commit 에 --paths 경로 한정 ──

def test_skill_md_off_commit_has_paths_flag():
    """OFF 절차 commit 명령에 --paths 가 포함되어 있어야 한다(전체 add -A 방지)."""
    import re
    text = SKILL_MD.read_text(encoding="utf-8")
    # "teammode.py commit ... --paths ..." 형태
    assert re.search(r"teammode\.py commit[^\n]*--paths", text), (
        "SKILL.md 의 OFF commit 명령에 --paths 가 없다 — "
        "경로 한정 없이 전체 워킹트리(git add -A)가 stage될 수 있다(P1 회귀)."
    )


def test_skill_md_off_commit_paths_includes_memory():
    """OFF 절차 commit 명령의 --paths 값이 memory/ 를 포함한다(세션로그 디렉터리)."""
    import re
    text = SKILL_MD.read_text(encoding="utf-8")
    m = re.search(r"teammode\.py commit[^\n]*--paths\s+[\"']?(\S+)[\"']?", text)
    assert m, "SKILL.md 의 commit --paths 값을 파싱하지 못했다"
    paths_val = m.group(1).strip("\"'")
    assert "memory" in paths_val, (
        f"commit --paths 값이 memory/ 를 포함하지 않는다: {paths_val!r}"
    )


# ── commit --paths 엔진·git_ops 단위 테스트 ──

def _init_git_repo(path: Path) -> None:
    """tmp 디렉터리를 최소 git 레포로 초기화한다(커밋 가능 상태)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"],
                   check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"],
                   check=True, capture_output=True, env=env)
    # 초기 커밋 없으면 HEAD 없어서 staged diff 가 안 됨 — dummy 파일로 초기 커밋 생성
    dummy = path / ".gitkeep"
    dummy.write_text("init")
    subprocess.run(["git", "-C", str(path), "add", ".gitkeep"],
                   check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"],
                   check=True, capture_output=True, env=env)


def test_commit_paths_stages_only_specified_dir(tmp_path):
    """commit --paths memory/ 가 memory/ 내 파일만 stage 하고 코드 파일은 제외한다."""
    import git_ops

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    # memory/ 에 세션로그, infra/ 에 코드 파일 생성
    (repo / "memory").mkdir()
    (repo / "memory" / "2026-06-17.md").write_text("세션로그")
    (repo / "infra").mkdir()
    (repo / "infra" / "code.py").write_text("# 코드 — 커밋되면 안 됨")

    result = git_ops.do_commit(str(repo), message="session: test", paths=["memory/"])
    assert result.ok, f"commit 실패: {result.detail}"

    # 코드 파일이 stage/commit 에 포함되지 않았는지 확인
    r = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "HEAD"],
        capture_output=True, text=True)
    out = r.stdout
    assert r.returncode == 0
    assert "infra/code.py" not in out, (
        "infra/code.py 가 커밋에 포함됐다 — --paths memory/ 한정이 작동하지 않음"
    )
    assert "memory/2026-06-17.md" in out, (
        "memory/2026-06-17.md 가 커밋에 없다 — 세션로그가 stage 되지 않음"
    )


def test_commit_no_paths_stages_all(tmp_path):
    """paths=None(기본) 이면 git add -A 로 모든 파일이 stage 된다(범용 commit 동작 보존)."""
    import git_ops

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "memory").mkdir()
    (repo / "memory" / "log.md").write_text("로그")
    (repo / "infra").mkdir()
    (repo / "infra" / "code.py").write_text("# 코드")

    result = git_ops.do_commit(str(repo), message="full: all", paths=None)
    assert result.ok, f"commit 실패: {result.detail}"

    r = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "HEAD"],
        capture_output=True, text=True)
    out = r.stdout
    assert r.returncode == 0
    assert "infra/code.py" in out, (
        "paths=None 인데도 infra/code.py 가 커밋에 없다 — add -A 동작 회귀"
    )
    assert "memory/log.md" in out, (
        "paths=None 인데도 memory/log.md 가 커밋에 없다"
    )


def test_commit_paths_excludes_pre_staged_outside(tmp_path):
    """tm off: 사용자가 미리 staged 한 외부 경로(코드)는 commit --paths 에서 제외된다 (P1).

    codex 적대검수가 잡은 엣지 — add 만 한정하면 이미 index 에 있던 outside 가 함께
    커밋된다. commit 에도 pathspec(`commit -- memory/`)을 줘서 pre-staged 를 제외한다.
    """
    import git_ops

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "memory").mkdir()
    (repo / "memory" / "log.md").write_text("세션로그")
    (repo / "infra").mkdir()
    (repo / "infra" / "code.py").write_text("# 미완성 코드 — 커밋되면 안 됨")

    # 사용자가 tm off 전에 코드를 미리 stage (수동 git add)
    subprocess.run(["git", "-C", str(repo), "add", "infra/code.py"],
                   check=True, capture_output=True)

    # tm off → memory/ 만 커밋
    result = git_ops.do_commit(str(repo), message="session: test", paths=["memory/"])
    assert result.ok, f"commit 실패: {result.detail}"

    r = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "HEAD"],
        capture_output=True, text=True)
    assert r.returncode == 0
    assert "infra/code.py" not in r.stdout, (
        "pre-staged 코드가 세션로그 커밋에 휩쓸렸다 — commit pathspec 미작동 (P1)"
    )
    assert "memory/log.md" in r.stdout, "세션로그가 커밋되지 않음"

    # pre-staged 코드는 여전히 index 에 보존(커밋 안 됨, 사용자 것 그대로)
    r2 = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True)
    assert "infra/code.py" in r2.stdout, (
        "pre-staged 코드가 사라졌다 — 커밋만 제외하고 staged 상태는 보존해야 함"
    )


# ── 엔진 _KNOWN_VERBS · _VALUE_FLAGS 정합 ──

def _load_teammode():
    """infra/teammode.py 모듈을 runpy 로 로드한다(import 사이드이펙트 없이)."""
    import runpy
    return runpy.run_path(
        str(REPO / "infra" / "teammode.py"),
        run_name="__tm_conformance__",
    )


def test_engine_known_verbs_cover_skill_md_verbs():
    """SKILL.md 에 등장하는 동사(on/off/log/context/pull/commit)가 _KNOWN_VERBS 에 있다."""
    mod = _load_teammode()
    known = set(mod["_KNOWN_VERBS"])
    required = {"on", "off", "log", "context", "pull", "commit"}
    missing = required - known
    assert not missing, (
        f"_KNOWN_VERBS 에서 누락된 동사: {missing}. SKILL.md 가 참조하는 동사가 엔진에 없다."
    )


def test_engine_value_flags_include_paths():
    """_VALUE_FLAGS 에 --paths 가 포함되어 있어야 한다(경로 한정 플래그 파싱 가능)."""
    mod = _load_teammode()
    value_flags = set(mod["_VALUE_FLAGS"])
    assert "--paths" in value_flags, (
        "--paths 가 _VALUE_FLAGS 에 없다 — _parse_args 가 다음 토큰을 값으로 소비하지 않음"
    )
