"""테스트 안전 가드 — 실 에이전트 설정 파일 오염 방지.

자율 빌드 규약: 실 환경(~/.claude/settings.json, ~/.codex/config.toml 등)은
절대 건드리지 않는다. 테스트는 tmp_path 픽스처만 써야 한다.

이 conftest 는 매 테스트 전후로 실 설정 경로의 존재/내용을 스냅샷해, 테스트가
실수로 실 파일을 생성·변경하면 즉시 실패시킨다. (과거 누수 재발 방지)
"""
import json
import os
from pathlib import Path

import pytest

def _real_state_dir() -> Path:
    """auto-pull 상태(last-pull)의 실 기본 경로 — 테스트가 절대 건드리면 안 된다.

    session-log-remind 의 _pull_state_path() 와 동일 규칙($XDG_STATE_HOME/teammode 또는
    ~/.local/state/teammode). 여기서는 ambient XDG 를 무시한 **실 HOME 기준** 경로를 가드
    대상으로 잡는다(테스트가 XDG_STATE_HOME 을 격리로 덮어도, 실 경로 자체의 변화를 검사).
    """
    return Path(os.path.expanduser("~/.local/state/teammode"))


def _real_credentials_dir() -> Path:
    """credentials 금고(L2-E)의 실 기본 경로 — 테스트가 절대 건드리면 안 된다.

    B-3 결정: 저장 = `$XDG_DATA_HOME/teammode/credentials`(기본 `~/.local/share/teammode/credentials`).
    last-pull 와 동일 철학으로, ambient XDG 를 무시한 **실 HOME 기준** 경로를 가드 대상으로
    잡는다(테스트가 XDG_DATA_HOME 격리를 덮어도, 실 경로 자체의 부재→존재 전이를 검사).
    teammode 전용 비밀 디렉토리라 다른 도구가 만들 일이 없으므로 디렉토리 자체도 엄격 가드.
    """
    return Path(os.path.expanduser("~/.local/share/teammode/credentials"))


# 셸 프로파일 — install.py ⑥ env 주입(§9)이 실 호스트 프로파일에 1줄 쓰는 사고를
# 방지(L1-0). 테스트는 monkeypatch HOME=tmp + fake 프로파일로만 env 주입을 검증한다.
# ⚠️ 주의: `.bashrc` 등 dotfile 은 pathlib 상 .suffix == "" 이다(선두 dot 은 stem).
# 따라서 과거의 `p.suffix and b != a` 분기로는 절대 안 잡힌다 → 아래 _CONTENT_GUARDED
# 집합으로 **suffix 무관 내용 변화**를 강제 검사한다(L1-0 실측 버그 수정).
_SHELL_PROFILES = [
    Path(os.path.expanduser("~/.bashrc")),
    Path(os.path.expanduser("~/.zshrc")),
    Path(os.path.expanduser("~/.profile")),
    Path(os.path.expanduser("~/.bash_profile")),
    Path(os.path.expanduser("~/.config/fish/config.fish")),
]

# Obsidian 중앙 설정 — install.py --register-obsidian 이 host-write 하므로, 테스트가
# 실 obsidian.json 을 건드리면 사용자의 볼트 목록을 오염시킨다(P1 정신). 플랫폼별
# 실 경로(linux/mac/win 상당)를 전부 가드 — 테스트는 fake HOME + --obsidian-config <tmp> 만.
_OBSIDIAN_CONFIGS = [
    Path(os.path.expanduser("~/.config/obsidian/obsidian.json")),       # linux
    Path(os.path.expanduser(
        "~/Library/Application Support/obsidian/obsidian.json")),       # mac
    Path(os.path.expanduser("~/AppData/Roaming/obsidian/obsidian.json")),  # win
]

# L2 새 쓰기 표면 (B0 — install-mcp/credentials 가 만들 표면).
# 무인 빌드 호스트 무오염 전제: L2 어댑터가 실 MCP 등록 파일·실 credentials 금고를
# 건드리면 즉시 발화하도록 미리 가드를 박는다(과거 dotfile blind spot 동형).
#
# ⚠️ 스킬 디렉토리 가드 제거(2026-06-16): install-skills(L2-C)가 v0.2 로 이월돼(L2-PLAN.md:67)
# v0.1 무인 빌드는 스킬 디렉토리(~/.claude/skills, ~/.codex/skills, ~/.codex/prompts)에
# 쓰지 않는다. 게다가 codex 스킬 경로는 spec 에 명문화되지 않은 추정 경로라 과(過) 가드
# (false-positive 위험)였다. → 스킬 경로 가드 전면 제거. claude `~/.claude.json` MCP 가드와
# credentials 가드는 v0.1 실표면이므로 유지.

# (a) claude MCP 등록 실경로 — install-mcp(§2.8)가 MCP 서버를 등록하는 실제 파일.
# 근거: spec/05-onboard-skill.md:90 "MCP 등록 방식 차이(`~/.claude.json` vs
# `~/.codex/config.toml`)". codex 쪽 MCP 섹션은 이미 _GUARDED/_CONTENT_GUARDED 의
# `~/.codex/config.toml`(파일 전체 내용 비교)로 잡히므로 별도 추가 불요.
_CLAUDE_MCP_CONFIG = Path(os.path.expanduser("~/.claude.json"))

_GUARDED = [
    Path(os.path.expanduser("~/.claude/settings.json")),
    Path(os.path.expanduser("~/.codex/config.toml")),
    Path(os.path.expanduser("~/.codex")),
    Path(os.path.expanduser("~/.claude")),
    _CLAUDE_MCP_CONFIG,
    *_SHELL_PROFILES,
    *_OBSIDIAN_CONFIGS,
    _real_state_dir() / "last-pull",
    _real_state_dir(),
    _real_credentials_dir(),
]

# 내용 변화(부재→존재 포함)를 suffix 무관하게 오염으로 보는 경로.
# 셸 프로파일은 dotfile 이라 suffix 검사로는 못 잡으므로 여기에 명시한다.
#
# ⚠️ `~/.claude.json` 은 _CONTENT_GUARDED 에서 **제외**(2026-06-16): 이 파일은 라이브
# Claude Code 세션이 MCP·projects 등으로 끊임없이 갱신하는 살아있는 파일이다. 전체 byte
# 비교로 가드하면, 테스트 실행 중 라이브 세션이 이 파일을 건드릴 때마다 그 순간 돌던
# (무관한) 테스트에 teardown ERROR 가 무작위로 붙는다(환경 아티팩트 = false-positive).
# → `~/.claude.json` 은 **teammode 가 등록한 흔적**(_teammode_managed 마커 mcpServers 항목,
#    또는 normalize.py 경로 훅 주입)만 추출해 before≠after 를 보는 전용 footprint 가드로
#    좁힌다(_claude_json_footprint / _CLAUDE_MCP_CONFIG 분기). teammode 코드의 실 오염은
#    여전히 잡고(빌드안전), 라이브 세션의 무관 갱신은 무시(플레이크 제거).
_CONTENT_GUARDED = set(_SHELL_PROFILES) | set(_OBSIDIAN_CONFIGS) | {
    Path(os.path.expanduser("~/.claude/settings.json")),
    Path(os.path.expanduser("~/.codex/config.toml")),
}

# teammode 가 실 ~/.claude.json 에 남기는 소유 흔적 식별자.
#   - mcpServers 항목의 `_teammode_managed: True` 마커(install-mcp, adapter._build_mcp_entry).
#   - normalize.py 경로 훅 주입 흔적(어댑터가 settings 에 쓰지만, 방어적으로 ~/.claude.json
#     본문에 이 문자열이 새로 등장하는 것도 teammode 발 오염으로 본다).
_TEAMMODE_MANAGED_KEY = "_teammode_managed"
_TEAMMODE_HOOK_NEEDLE = "normalize.py"

# credentials 금고는 **디렉토리 내부 엔트리 집합**을 추적해 침투를 잡는다(아래 _snapshot 참조).
# 과거 _CREATION_GUARDED 는 디렉토리를 ("dir",None) 으로만 봐서 "이미 존재하는 디렉토리 내부
# 파일 추가"를 dir→dir 로 보고 침묵했다(과거 dotfile blind spot 동형). credentials 는 teammode
# 전용 비밀 디렉토리라 부재→존재뿐 아니라 **내부에 토큰 파일이 새로 생기는 것**도 오염이다.
# → 엔트리 집합(sorted 이름) 스냅샷으로 before != after 면 발화한다.
_ENTRY_TRACKED_DIRS = {_real_credentials_dir()}


@pytest.fixture(autouse=True)
def _isolate_pull_state(tmp_path_factory, monkeypatch):
    """모든 테스트에 격리 XDG_STATE_HOME 주입 — auto-pull 상태가 실 경로에 새지 않게.

    session-log-remind 가 subprocess 로 띄워져도 상속받도록 os.environ 에 박는다.
    (개별 테스트가 _run_hook 등으로 다시 덮어쓰는 것도 허용 — 그쪽도 격리 경로다.)
    """
    state_home = tmp_path_factory.mktemp("xdg-state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    # credentials 금고(L2-E)는 XDG_DATA_HOME 기반($XDG_DATA_HOME/teammode/credentials, B-3).
    # 실 `~/.local/share/teammode/credentials` 로 새지 않게 격리 경로를 주입(subprocess 상속).
    data_home = tmp_path_factory.mktemp("xdg-data")
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))


def _claude_json_footprint(raw: bytes):
    """`~/.claude.json` 내용에서 **teammode 소유 흔적만** 추출한다(라이브 무관 갱신 무시).

    반환은 비교 가능한 정렬 튜플 — 부재면 빈 footprint(()). teammode 가 이 파일을
    오염시키면(_teammode_managed mcpServers 항목 추가, 또는 normalize.py 경로 훅 주입)
    footprint 가 달라져 before≠after 로 발화한다. 라이브 세션의 무관한 mcpServers·projects
    갱신은 footprint 에 영향 없어 무발화(false-positive 제거).

    파싱 실패해도 normalize.py needle 의 존재 여부는 raw 텍스트로 본다(방어적).
    """
    if raw is None:
        return ()
    managed_servers = ()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = None
    if isinstance(data, dict):
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            managed_servers = tuple(sorted(
                name for name, entry in servers.items()
                if isinstance(entry, dict)
                and entry.get(_TEAMMODE_MANAGED_KEY) is True))
    has_hook = _TEAMMODE_HOOK_NEEDLE.encode("utf-8") in raw
    return (managed_servers, has_hook)


def _snapshot():
    snap = {}
    for p in _GUARDED:
        if p == _CLAUDE_MCP_CONFIG:
            # ~/.claude.json: 전체 byte 가 아니라 teammode 소유 footprint 만 스냅샷.
            # 라이브 Claude Code 세션이 무관한 항목(다른 mcpServers·projects)을 갱신해도
            # footprint 가 동일하면 before==after → 무발화(플레이크 제거). teammode 가
            # _teammode_managed 항목·normalize.py 훅을 주입하면 footprint 가 달라져 발화.
            if p.is_file():
                snap[p] = ("claude-json", _claude_json_footprint(p.read_bytes()))
            else:
                snap[p] = ("claude-json", _claude_json_footprint(None))
        elif p in _ENTRY_TRACKED_DIRS:
            # 엔트리 추적 디렉토리: 부재면 ("absent",None), 존재하면 내부 엔트리 이름 집합을
            # 정렬 튜플로 스냅샷. dir→dir 라도 내부에 토큰 파일이 새로 생기면 before!=after.
            if p.is_dir():
                snap[p] = ("dir", tuple(sorted(e.name for e in p.iterdir())))
            elif p.exists():
                snap[p] = ("file", p.read_bytes())
            else:
                snap[p] = ("absent", None)
        elif p.is_file():
            snap[p] = ("file", p.read_bytes())
        elif p.is_dir():
            snap[p] = ("dir", None)
        else:
            snap[p] = ("absent", None)
    return snap


def _pollution_reason(p, b, a):
    """가드의 순수 판정 함수 — 경로 p 의 before/after 가 오염이면 사유 문자열, 아니면 None.

    fixture 본문에서 분리해 **라이브 판정 로직을 직접 테스트**할 수 있게 한다(test_guard.py).
    가드가 _GUARDED 에 경로만 넣고 정작 발화 안 하는 blind spot 을 박기 위함.
    """
    state_paths = {_real_state_dir() / "last-pull", _real_state_dir()}
    # auto-pull 상태 경로: suffix 없어도(예: last-pull) 부재→존재 전이를 오염으로 본다.
    if p in state_paths:
        if b[0] == "absent" and a[0] != "absent":
            return (f"실 auto-pull 상태 오염 감지: {p} (before=absent, after={a[0]}). "
                    f"테스트는 XDG_STATE_HOME 격리를 써야 한다.")
        return None
    # credentials 금고: teammode 전용 비밀 디렉토리. 부재→존재뿐 아니라 **내부 엔트리 집합
    # 변화**(이미 존재하는 디렉토리에 토큰 파일 추가)도 오염으로 본다. _snapshot 이 dir 를
    # ("dir", sorted 엔트리 튜플)로 잡으므로 dir→dir 라도 내부에 파일이 생기면 b != a.
    if p in _ENTRY_TRACKED_DIRS:
        if b != a:
            return (f"실 credentials 금고 오염 감지: {p} (before={b[0]}, after={a[0]}). "
                    f"테스트는 monkeypatch HOME=tmp + XDG_DATA_HOME 격리만 써야 한다.")
        return None
    # MCP 등록 파일(`~/.claude.json`): 전체 byte 가 아니라 **teammode 소유 footprint**만
    # 비교한다. b/a 는 ("claude-json", footprint) 형태(_snapshot). footprint 가 달라졌을
    # 때만 = teammode 가 _teammode_managed 항목·normalize.py 훅을 주입했을 때만 발화.
    # 라이브 세션의 무관 갱신(다른 mcpServers·projects)은 footprint 동일 → 무발화(플레이크 제거).
    if p == _CLAUDE_MCP_CONFIG:
        if b[1] != a[1]:
            return (f"실 MCP 등록/설정 파일 오염 감지: {p} "
                    f"(teammode footprint before={b[1]}, after={a[1]}). "
                    f"teammode 가 실 ~/.claude.json 에 _teammode_managed 항목/normalize.py 훅을 "
                    f"주입했다 — 테스트는 monkeypatch HOME=tmp + --settings 격리만 써야 한다.")
        return None
    # 셸 프로파일·핵심 설정파일: suffix 무관(dotfile/신규생성 포함) 변화 = 오염.
    # (`.bashrc` 는 .suffix=="" 라 suffix 검사로는 못 잡힌다 — L1-0 핵심.)
    if p in _CONTENT_GUARDED:
        if b != a:
            return (f"실 셸 프로파일/설정 오염 감지: {p} (before={b[0]}, after={a[0]}). "
                    f"테스트는 monkeypatch HOME=tmp + fake 프로파일만 써야 한다.")
        return None
    # 디렉토리(~/.claude 등)는 다른 도구가 만들 수 있으니 파일 내용 변화만 엄격 검사
    if p.suffix and b != a:
        return (f"실 설정 파일 오염 감지: {p} (before={b[0]}, after={a[0]}). "
                f"테스트는 tmp_path 만 사용해야 한다.")
    return None


@pytest.fixture(autouse=True)
def _no_real_config_pollution():
    before = _snapshot()
    yield
    after = _snapshot()
    for p in _GUARDED:
        reason = _pollution_reason(p, before[p], after[p])
        if reason:
            pytest.fail(reason)
