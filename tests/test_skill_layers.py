"""스킬 계층(base/core/util) 설치·제거 테스트 (A-1/A-2).

검증 목록:
  - tm-context 가 core 레이어에 있고 base 에는 없다
  - install_skills(layer='core') → core 심링크 생성
  - install_skills(layer='base') → base 심링크만 (core 무접촉)
  - uninstall_skills → 모든 레이어 제거
  - _is_layer_skill: 계층 한정 소유 판정
  - util add/remove/list 동사 (JSON 갱신 + 심링크 반영 + 멱등)
  - util: 없는 스킬 추가 거부
  - util: --member/--skill traversal 방어
  - on → core 심링크 설치
  - off → core 제거, base 보존

모든 테스트는 tmp_path 격리 — 실 ~/.claude/skills 무접촉.
"""
import json
import os
import runpy
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

_CLAUDE = runpy.run_path(
    str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
    run_name="__skill_layers__",
)
ClaudeAdapter = _CLAUDE["Adapter"]


# ── scaffold helpers ──

def _scaffold(tmp_path, include_util_skill: bool = False):
    """tmp 팀 루트 — 실 infra/skills/base·core·util 트리 복사."""
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "adapter.py",
        root / "infra" / "agents" / "claude" / "adapter.py",
    )
    shutil.copy(
        REPO / "infra" / "agents" / "claude" / "events.json",
        root / "infra" / "agents" / "claude" / "events.json",
    )
    shutil.copytree(REPO / "infra" / "skills" / "base",
                    root / "infra" / "skills" / "base")
    shutil.copytree(REPO / "infra" / "skills" / "core",
                    root / "infra" / "skills" / "core")
    # util 디렉토리 생성 (보통 비어 있음)
    (root / "infra" / "skills" / "util").mkdir(parents=True, exist_ok=True)
    if include_util_skill:
        _add_util_skill(root, "test-util")
    return root


def _add_util_skill(root: Path, skill_name: str) -> Path:
    """util 레이어에 더미 스킬 추가."""
    sk = root / "infra" / "skills" / "util" / skill_name
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: Test util skill {skill_name}.\n---\n",
        encoding="utf-8",
    )
    return sk


def _adapter(root, tmp_path, skills_name="claude-skills"):
    return ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(tmp_path / "settings.json"),
        python="python3",
        team_root=str(root),
        skills_dir=str(tmp_path / skills_name),
    )


# ── A-1: tm-context 이동 검증 ──

def test_tm_context_in_core_not_base():
    """tm-context 가 core 레이어에 있고 base 에는 없다."""
    core_skill = REPO / "infra" / "skills" / "core" / "tm-context" / "SKILL.md"
    base_skill = REPO / "infra" / "skills" / "base" / "tm-context"
    assert core_skill.is_file(), "core/tm-context/SKILL.md 가 없다"
    assert not base_skill.exists(), "base/tm-context 가 아직 존재한다 (이동 미완)"


def test_skill_md_no_temp_comment():
    """core/tm-context/SKILL.md 에 임시 배치 주석이 없어야 한다."""
    text = (REPO / "infra" / "skills" / "core" / "tm-context" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "잠정 base" not in text, "임시 배치 주석이 제거되지 않았다"
    assert "infra/skills/core/로 이동" not in text, "이동 예고 주석이 제거되지 않았다"


# ── A-2: adapter 계층별 설치 ──

def test_skill_sources_base_no_tm_context(tmp_path):
    """_skill_sources(layer='base') 에 tm-context 가 없다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    base_names = {s.name for s in a._skill_sources(layer="base")}
    assert "tm-context" not in base_names


def test_skill_sources_core_has_tm_context(tmp_path):
    """_skill_sources(layer='core') 에 tm-context 가 있다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    core_names = {s.name for s in a._skill_sources(layer="core")}
    assert "tm-context" in core_names


def test_install_skills_base_no_tm_context(tmp_path):
    """install_skills(layer='base') 는 tm-context 를 설치하지 않는다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="base")
    sk = tmp_path / "claude-skills"
    assert not (sk / "tm-context").exists(), "base install 이 tm-context 를 설치했다"


def test_install_skills_core_creates_tm_context(tmp_path):
    """install_skills(layer='core') 는 tm-context 심링크를 생성한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="core")
    sk = tmp_path / "claude-skills"
    link = sk / "tm-context"
    assert link.exists() or link.is_symlink(), "core install 이 tm-context 를 생성하지 않았다"
    assert (link / "SKILL.md").is_file()


def test_install_skills_core_idempotent(tmp_path):
    """install_skills(layer='core') 는 멱등이다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="core")
    again = a.install_skills(layer="core")
    assert again == ["[ok] 변경 없음"]


def test_is_layer_skill_core(tmp_path):
    """_is_layer_skill(target, 'core') 는 core 레이어 심링크에 True 를 반환한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="core")
    sk = tmp_path / "claude-skills"
    link = sk / "tm-context"
    assert a._is_layer_skill(link, "core") is True
    assert a._is_layer_skill(link, "base") is False


def test_is_layer_skill_base(tmp_path):
    """_is_layer_skill(target, 'base') 는 base 레이어 심링크에 True 를 반환한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="base")
    sk = tmp_path / "claude-skills"
    link = sk / "tm-onboard"
    assert a._is_layer_skill(link, "base") is True
    assert a._is_layer_skill(link, "core") is False


# ── uninstall_skills: 모든 레이어 제거 ──

def test_uninstall_skills_removes_all_layers(tmp_path):
    """uninstall_skills() 는 base+core 모두 제거한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="base")
    a.install_skills(layer="core")
    sk = tmp_path / "claude-skills"
    assert (sk / "tm-onboard").is_symlink()
    assert (sk / "tm-context").is_symlink() or (sk / "tm-context").exists()
    a.uninstall_skills()
    assert not (sk / "tm-onboard").exists()
    assert not (sk / "tm-context").exists()


def test_off_removes_core_preserves_base(tmp_path):
    """_uninstall_layer('core') 는 core 만 제거하고 base 는 보존한다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    a.install_skills(layer="base")
    a.install_skills(layer="core")
    sk = tmp_path / "claude-skills"
    # core 만 제거
    import importlib, types
    tm_path = str(REPO / "infra" / "teammode.py")
    mod = runpy.run_path(tm_path, run_name="__skill_layers_off__")
    mod["_uninstall_layer"](a, "core")
    # tm-context(core) 는 사라짐
    assert not (sk / "tm-context").exists(), "core 스킬이 제거되지 않았다"
    # tm-onboard(base) 는 보존
    assert (sk / "tm-onboard").is_symlink(), "base 스킬이 잘못 제거됐다"


# ── util 동사 ──

def _run_util(args, tmp_path, root):
    """teammode.py util ... 를 직접 실행."""
    tm_path = str(REPO / "infra" / "teammode.py")
    mod = runpy.run_path(tm_path, run_name="__skill_layers_util__")
    main = mod["main"]
    return main(args)


def test_util_list_empty(tmp_path):
    """util list: util 스킬 없으면 available=[], installed=[] JSON."""
    root = _scaffold(tmp_path)
    import io, contextlib
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_list__")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod["main"](["util", "list", "--root", str(root)])
    assert rc == 0
    data = json.loads(buf.getvalue())
    assert data["available"] == []
    assert data["installed"] == []


def test_util_list_with_skill(tmp_path):
    """util list: util 스킬 있으면 available 에 반영."""
    root = _scaffold(tmp_path, include_util_skill=True)
    import io, contextlib
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_list2__")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod["main"](["util", "list", "--root", str(root)])
    assert rc == 0
    data = json.loads(buf.getvalue())
    names = [s["name"] for s in data["available"]]
    assert "test-util" in names


def test_util_add_updates_json(tmp_path):
    """util add: util-skills.json 에 스킬 등록."""
    root = _scaffold(tmp_path, include_util_skill=True)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_add__")
    rc = mod["main"](["util", "add", "--root", str(root),
                       "--member", "alice", "--skill", "test-util"])
    assert rc == 0
    path = root / "memory" / "team" / "sessions" / "alice" / "util-skills.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "test-util" in data["installed"]


def test_util_add_idempotent(tmp_path):
    """util add: 이미 등록된 스킬 재추가는 멱등."""
    root = _scaffold(tmp_path, include_util_skill=True)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_add_idem__")
    args = ["util", "add", "--root", str(root),
             "--member", "alice", "--skill", "test-util"]
    mod["main"](args)
    rc = mod["main"](args)
    assert rc == 0
    path = root / "memory" / "team" / "sessions" / "alice" / "util-skills.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["installed"].count("test-util") == 1, "중복 등록됨"


def test_util_remove_updates_json(tmp_path):
    """util remove: util-skills.json 에서 스킬 제거."""
    root = _scaffold(tmp_path, include_util_skill=True)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_remove__")
    # 먼저 add
    mod["main"](["util", "add", "--root", str(root),
                  "--member", "alice", "--skill", "test-util"])
    rc = mod["main"](["util", "remove", "--root", str(root),
                       "--member", "alice", "--skill", "test-util"])
    assert rc == 0
    path = root / "memory" / "team" / "sessions" / "alice" / "util-skills.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "test-util" not in data["installed"]


def test_util_remove_idempotent(tmp_path):
    """util remove: 미등록 스킬 제거는 멱등(에러 없음)."""
    root = _scaffold(tmp_path)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_remove_idem__")
    rc = mod["main"](["util", "remove", "--root", str(root),
                       "--member", "alice", "--skill", "nonexistent"])
    assert rc == 0


def test_util_add_nonexistent_skill_rejected(tmp_path):
    """util add: 존재하지 않는 util 스킬 추가는 거부(exit 2)."""
    root = _scaffold(tmp_path)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_add_reject__")
    rc = mod["main"](["util", "add", "--root", str(root),
                       "--member", "alice", "--skill", "nonexistent"])
    assert rc == 2


def test_util_add_traversal_member_rejected(tmp_path):
    """util add: --member 에 경로 traversal 문자 포함 → 거부(exit 2)."""
    root = _scaffold(tmp_path, include_util_skill=True)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_traversal_member__")
    rc = mod["main"](["util", "add", "--root", str(root),
                       "--member", "../evil", "--skill", "test-util"])
    assert rc == 2


def test_util_add_traversal_skill_rejected(tmp_path):
    """util add: --skill 에 경로 traversal 문자 포함 → 거부(exit 2)."""
    root = _scaffold(tmp_path, include_util_skill=True)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_traversal_skill__")
    rc = mod["main"](["util", "add", "--root", str(root),
                       "--member", "alice", "--skill", "../evil"])
    assert rc == 2


def test_util_list_with_member_shows_installed(tmp_path):
    """util list --member: installed 에 등록된 스킬 표시."""
    root = _scaffold(tmp_path, include_util_skill=True)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_list_member__")
    mod["main"](["util", "add", "--root", str(root),
                  "--member", "alice", "--skill", "test-util"])
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod["main"](["util", "list", "--root", str(root), "--member", "alice"])
    assert rc == 0
    data = json.loads(buf.getvalue())
    assert "test-util" in data["installed"]


def test_util_unknown_action_rejected(tmp_path):
    """util: 알 수 없는 action → exit 2."""
    root = _scaffold(tmp_path)
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_unknown__")
    rc = mod["main"](["util", "badaction", "--root", str(root)])
    assert rc == 2


# ── _KNOWN_VERBS 에 util 포함 ──

def test_util_in_known_verbs():
    """'util' 이 엔진 _KNOWN_VERBS 에 있다."""
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_known_verbs__")
    assert "util" in mod["_KNOWN_VERBS"]


# ── tm-manage-utils SKILL.md 존재 검증 ──

def test_tm_manage_utils_skill_md_exists():
    """infra/skills/base/tm-manage-utils/SKILL.md 가 존재한다."""
    skill_md = REPO / "infra" / "skills" / "base" / "tm-manage-utils" / "SKILL.md"
    assert skill_md.is_file()


def test_tm_manage_utils_in_base_sources(tmp_path):
    """_skill_sources(layer='base') 에 tm-manage-utils 가 있다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    base_names = {s.name for s in a._skill_sources(layer="base")}
    assert "tm-manage-utils" in base_names


# ── tm-customize SKILL.md 및 references 존재 검증 ──

def test_tm_customize_skill_md_exists():
    """infra/skills/base/tm-customize/SKILL.md 가 존재한다."""
    skill_md = REPO / "infra" / "skills" / "base" / "tm-customize" / "SKILL.md"
    assert skill_md.is_file()


def test_tm_customize_references_exist():
    """tm-customize/references/ 하위 3개 문서가 존재한다."""
    refs = REPO / "infra" / "skills" / "base" / "tm-customize" / "references"
    for name in ("banner.md", "persona.md", "skills.md"):
        assert (refs / name).is_file(), f"references/{name} 없음"


def test_tm_customize_in_base_sources(tmp_path):
    """_skill_sources(layer='base') 에 tm-customize 가 있다."""
    root = _scaffold(tmp_path)
    a = _adapter(root, tmp_path)
    base_names = {s.name for s in a._skill_sources(layer="base")}
    assert "tm-customize" in base_names


# ── tm-onboard 흡수 후 personality 절차 제거 검증 ──

def test_tm_onboard_personality_section_redirects_to_tm_customize():
    """tm-onboard가 personality 커스텀 절차를 직접 보유하지 않고 tm-customize로 안내한다."""
    onboard = REPO / "infra" / "skills" / "base" / "tm-onboard" / "SKILL.md"
    text = onboard.read_text(encoding="utf-8")
    # 배너 picker 절차(cat infra/banners/<폰트명>.txt 단계)가 제거됐는지
    assert "cat infra/banners" not in text, "배너 picker 절차가 아직 남아 있음 — tm-customize로 옮겨야 한다"
    # tm-customize 안내가 있는지
    assert "tm-customize" in text, "tm-onboard에 tm-customize 안내가 없음"


# ── P0 핵심 경로 직접 검증 (codex 지적 대응) ──

def _run_teammode(args):
    """teammode.py main() 를 직접 호출 — 반환값(exit code)만."""
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__p0_test__")
    return mod["main"](args)


def _settings(tmp_path) -> str:
    """tmp 안의 settings.json 경로."""
    return str(tmp_path / "settings.json")


def test_cmd_on_creates_core_symlink(tmp_path):
    """cmd_on → core 심링크(tm-context) 생성, base 심링크(tm-onboard)도 생성."""
    root = _scaffold(tmp_path)
    settings = _settings(tmp_path)
    rc = _run_teammode(["on", "--root", str(root), "--settings", settings])
    assert rc == 0
    # on 은 base install(install.py 가 전담)하지 않지만 core 는 설치한다.
    # teammode.py cmd_on 은 adapter.install_skills(layer="core") 만 호출.
    skills_dir = tmp_path / "skills"
    assert (skills_dir / "tm-context").exists() or (skills_dir / "tm-context").is_symlink(), \
        "on 후 tm-context(core) 심링크가 없다"


def test_cmd_off_removes_core_keeps_base(tmp_path):
    """cmd_off → core 제거, base 심링크는 보존 (on 이 base 를 설치하지 않으므로 off 후도 부재)."""
    root = _scaffold(tmp_path)
    settings = _settings(tmp_path)
    # on 으로 core 설치
    _run_teammode(["on", "--root", str(root), "--settings", settings])
    skills_dir = tmp_path / "skills"
    assert (skills_dir / "tm-context").exists() or (skills_dir / "tm-context").is_symlink()
    # base 스킬 수동으로 설치 (on 은 base 를 안 건드리므로 직접 설치)
    a = ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=settings,
        team_root=str(root),
        skills_dir=str(skills_dir),
    )
    a.install_skills(layer="base")
    assert (skills_dir / "tm-onboard").is_symlink()
    # off
    rc = _run_teammode(["off", "--root", str(root), "--settings", settings])
    assert rc == 0
    # core 제거됨
    assert not (skills_dir / "tm-context").exists(), "off 후 tm-context(core) 가 남아 있다"
    # base 보존
    assert (skills_dir / "tm-onboard").is_symlink(), "off 가 base 스킬을 잘못 제거했다"


def test_skills_dir_derived_from_settings(tmp_path):
    """P0-1: --settings <tmp> 만 주면 심링크가 tmp 아래 생기고, 파생 규칙이 바르게 적용됨.

    파생 규칙: skills_dir = Path(settings_path).parent / 'skills'.
    실호스트 무접촉은 conftest._no_real_config_pollution autouse fixture 가 담당한다
    (before/after footprint 비교). 이 테스트는 '파생된 경로에 심링크 생성' 규칙만 확인.
    """
    root = _scaffold(tmp_path)
    settings = str(tmp_path / "settings.json")
    rc = _run_teammode(["on", "--root", str(root), "--settings", settings])
    assert rc == 0
    # 파생 규칙: settings_path.parent / 'skills' = tmp_path / 'skills'
    expected_skills_dir = tmp_path / "skills"
    assert expected_skills_dir.is_dir(), f"파생 skills_dir {expected_skills_dir} 없음"
    assert (expected_skills_dir / "tm-context").exists() or \
           (expected_skills_dir / "tm-context").is_symlink(), \
        "파생 skills_dir 에 tm-context 없음 — 파생 규칙이 동작하지 않는다"
    # 심링크가 tmp_path 안을 가리키는지 확인 (실호스트 경로가 아닌지)
    link = expected_skills_dir / "tm-context"
    if link.is_symlink():
        target_real = str(link.resolve())
        assert str(tmp_path) in target_real or str(REPO) in target_real, \
            f"심링크 타깃이 예상 밖: {target_real}"


def test_traversal_in_util_skills_json_rejected_on_on(tmp_path):
    """P0-2: util-skills.json 에 "../foo" 넣으면 on 이 skip+경고, skills dir 밖 생성 안 함."""
    import io, contextlib
    root = _scaffold(tmp_path)
    # alice 의 util-skills.json 에 traversal 항목 삽입
    alice_dir = root / "memory" / "team" / "sessions" / "alice"
    alice_dir.mkdir(parents=True, exist_ok=True)
    bad_json = json.dumps({"installed": ["../evil", "nonexistent"]})
    (alice_dir / "util-skills.json").write_text(bad_json, encoding="utf-8")
    settings = _settings(tmp_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run_teammode(["on", "--root", str(root),
                             "--settings", settings, "--member", "alice"])
    assert rc == 0, "traversal 가드가 on 전체를 실패시켜선 안 된다"
    out = buf.getvalue()
    # warn 메시지 출력 확인
    assert "warn" in out.lower() or "skip" in out.lower(), \
        "traversal 스킬에 대한 경고 없음"
    # skills_dir 밖에 파일이 생기지 않았다
    skills_dir = tmp_path / "skills"
    escaped = tmp_path / "evil"  # ../evil 로 탈출했을 경우의 경로
    assert not escaped.exists(), f"traversal 탈출: {escaped} 생성됨"
    if skills_dir.is_dir():
        names = [p.name for p in skills_dir.iterdir()]
        assert "evil" not in names, "skills_dir 안에 'evil' 생성됨"


def test_orphan_core_preserved_after_base_reinstall(tmp_path):
    """고아 자살삭제 방지: on 으로 core 설치 후 install_skills(layer='base') 재실행해도 core 보존."""
    root = _scaffold(tmp_path)
    settings = _settings(tmp_path)
    skills_dir = tmp_path / "skills"
    a = ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=settings,
        team_root=str(root),
        skills_dir=str(skills_dir),
    )
    # core 설치
    a.install_skills(layer="core")
    assert (skills_dir / "tm-context").is_symlink() or (skills_dir / "tm-context").exists()
    # base 재실행
    a.install_skills(layer="base")
    # core 심링크 보존 (base 고아청소가 core 스킬을 지우면 안 됨)
    assert (skills_dir / "tm-context").is_symlink() or (skills_dir / "tm-context").exists(), \
        "base 재실행이 core/tm-context 를 제거했다"


# ── P0-2: util 즉시반영 실호스트 무접촉 + containment 가드 (codex 2R 결함 대응) ──

def test_util_add_active_no_settings_skips_symlink(tmp_path):
    """P0-2: util add — active 상태에서 --settings/--install 없으면 심링크 즉시반영 skip.

    실호스트 ~/.claude/skills 무접촉 검증: tmp 격리 전용 skills_dir 가 인자로 주어지지
    않은 채로 active marker 가 있어도, settings_path=None → 즉시반영 경로 진입 안 함.
    json 갱신은 유지(다음 on 에서 반영)되어야 한다.
    """
    import io, contextlib
    real_skills = Path.home() / ".claude" / "skills"
    # real_skills 사전 footprint
    real_before = set(real_skills.iterdir()) if real_skills.is_dir() else set()

    root = _scaffold(tmp_path, include_util_skill=True)
    # active marker 생성 (on 상태 시뮬레이션)
    (root / ".teammode-active").touch()

    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_add_no_settings__")
    buf_err = io.StringIO()
    # --settings / --install 없이 util add
    with contextlib.redirect_stderr(buf_err):
        rc = mod["main"](["util", "add", "--root", str(root),
                           "--member", "alice", "--skill", "test-util"])
    assert rc == 0, "settings 없어도 json 갱신은 성공해야 한다"

    # json 갱신 확인
    path = root / "memory" / "team" / "sessions" / "alice" / "util-skills.json"
    assert path.is_file(), "util-skills.json 갱신되지 않음"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "test-util" in data["installed"], "json에 스킬이 등록되지 않음"

    # skip 안내 출력 확인
    err_out = buf_err.getvalue()
    assert "skip" in err_out.lower(), "즉시반영 skip 안내가 없다"

    # 실호스트 무접촉 확인
    real_after = set(real_skills.iterdir()) if real_skills.is_dir() else set()
    added = real_after - real_before
    assert not added, f"실호스트 ~/.claude/skills 가 오염됨: {added}"


def test_util_add_active_with_settings_links_to_isolated_dir(tmp_path):
    """P0-2: util add — active + --settings <tmp> → 심링크가 tmp skills_dir 에 생성됨.

    settings_path 가 주어지면 즉시반영이 동작하되, 파생된 격리 skills_dir 에만 생성.
    실호스트 무접촉.
    """
    real_skills = Path.home() / ".claude" / "skills"
    real_before = set(real_skills.iterdir()) if real_skills.is_dir() else set()

    root = _scaffold(tmp_path, include_util_skill=True)
    # active marker
    (root / ".teammode-active").touch()

    settings = _settings(tmp_path)  # tmp_path/settings.json
    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_add_with_settings__")
    rc = mod["main"](["util", "add", "--root", str(root),
                       "--member", "alice", "--skill", "test-util",
                       "--settings", settings])
    assert rc == 0

    # 파생 skills_dir = tmp_path/skills
    isolated_skills = tmp_path / "skills"
    link = isolated_skills / "test-util"
    assert link.exists() or link.is_symlink(), \
        f"격리 skills_dir({isolated_skills})에 심링크 없음"

    # 실호스트 무접촉
    real_after = set(real_skills.iterdir()) if real_skills.is_dir() else set()
    added = real_after - real_before
    assert not added, f"실호스트 ~/.claude/skills 오염: {added}"


def test_util_add_containment_guard_rejects_outside_src(tmp_path):
    """P0-2: util add — 소스가 util 디렉터리 밖을 가리키면 거부(exit 2).

    외부 경로를 가리키는 심링크를 util 디렉터리에 만들고 add 하면 containment 가드가 막는다.
    """
    root = _scaffold(tmp_path)
    util_dir = root / "infra" / "skills" / "util"
    # util_dir 안에 '나쁜' 스킬 심링크: tmp_path/outside 를 가리킨다
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: outside\ndescription: evil outside skill.\n---\n",
        encoding="utf-8",
    )
    fake_skill = util_dir / "outside"
    fake_skill.symlink_to(outside)  # outside 를 가리키는 심링크 — util_dir 안이지만 resolved는 밖

    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_containment__")
    import io, contextlib
    buf_err = io.StringIO()
    with contextlib.redirect_stderr(buf_err):
        rc = mod["main"](["util", "add", "--root", str(root),
                           "--member", "alice", "--skill", "outside"])
    assert rc == 2, "containment 가드가 거부하지 않음"
    err_out = buf_err.getvalue()
    assert "containment" in err_out.lower() or "밖" in err_out, \
        f"containment 거부 메시지 없음: {err_out!r}"


def test_util_remove_active_no_settings_skips_symlink(tmp_path):
    """P0-2: util remove — active 상태에서 --settings 없으면 심링크 제거 skip.

    json 갱신은 유지, 실호스트 무접촉.
    """
    import io, contextlib
    real_skills = Path.home() / ".claude" / "skills"
    real_before = set(real_skills.iterdir()) if real_skills.is_dir() else set()

    root = _scaffold(tmp_path, include_util_skill=True)
    # json에 미리 등록 (util add 로 json 갱신)
    _run_util(["util", "add", "--root", str(root),
               "--member", "alice", "--skill", "test-util"], tmp_path, root)
    # active marker
    (root / ".teammode-active").touch()

    mod = runpy.run_path(str(REPO / "infra" / "teammode.py"),
                         run_name="__util_remove_no_settings__")
    buf_err = io.StringIO()
    with contextlib.redirect_stderr(buf_err):
        rc = mod["main"](["util", "remove", "--root", str(root),
                           "--member", "alice", "--skill", "test-util"])
    assert rc == 0

    # json에서 제거됨
    path = root / "memory" / "team" / "sessions" / "alice" / "util-skills.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "test-util" not in data["installed"]

    # skip 안내
    err_out = buf_err.getvalue()
    assert "skip" in err_out.lower()

    # 실호스트 무접촉
    real_after = set(real_skills.iterdir()) if real_skills.is_dir() else set()
    assert real_after - real_before == set(), "실호스트 오염"
