"""L1-0 — conftest 안전 가드 실증 테스트.

install.py ⑥(env 주입, §9)이 실 호스트 셸 프로파일(~/.bashrc 등)에 1줄 쓰는 사고를
방지하려면, conftest 의 `_no_real_config_pollution` 가드가 그 경로들을 **실제로**
감시하고 있어야 한다. 이 파일은 가드가:
  1. 셸 프로파일 5종을 _GUARDED 에 포함하고,
  2. 보호 대상 파일의 내용 변화를 탐지하는 비교 로직(`p.suffix and b != a`)을 갖는지
를 실증한다. (가드가 비어 있으면 이후 슬라이스가 실 프로파일을 건드려도 못 잡는다.)
"""
import os
from pathlib import Path

import conftest


SHELL_PROFILES = [
    "~/.bashrc",
    "~/.zshrc",
    "~/.profile",
    "~/.bash_profile",
    "~/.config/fish/config.fish",
]


def test_shell_profiles_are_guarded():
    """셸 프로파일 5종이 전부 _GUARDED 목록에 있다(env 주입 사고 방지)."""
    guarded = {p for p in conftest._GUARDED}
    for prof in SHELL_PROFILES:
        p = Path(os.path.expanduser(prof))
        assert p in guarded, f"셸 프로파일이 가드되지 않음: {prof}"


def test_profiles_are_content_guarded_not_suffix_dependent():
    """프로파일은 _CONTENT_GUARDED 에 들어 suffix 무관 검사를 받는다.

    ⚠️ 핵심 함정(L1-0 실측): `.bashrc` 등 dotfile 은 pathlib 상 .suffix == "" 이다.
    과거 가드가 의존하던 `p.suffix and b != a` 분기로는 절대 안 잡힌다 → 반드시
    _CONTENT_GUARDED 멤버십(suffix 무관)으로 보호돼야 한다. 이 테스트가 그 함정을 박는다.
    """
    for prof in SHELL_PROFILES:
        p = Path(os.path.expanduser(prof))
        # dotfile 4종은 정말로 suffix 가 없음 — 그래서 suffix 의존 검사로는 못 잡는다.
        if p.name.startswith(".") and "." not in p.name[1:]:
            assert p.suffix == "", f"{prof} 가정 깨짐: dotfile 인데 suffix 있음"
        assert p in conftest._CONTENT_GUARDED, (
            f"{prof} 가 _CONTENT_GUARDED 에 없음 → suffix 검사로 누락됨(가드 무력)")


def test_guard_detection_logic_flags_profile_content_change():
    """가드가 suffix 없는 .bashrc 의 'before≠after(부재→존재)'를 실제로 발화한다.

    실 파일을 건드리지 않고 conftest 의 **라이브 판정 함수** _pollution_reason 을 직접
    호출해, dotfile(.suffix=="") 에 대해서도 탐지가 동작함을 실증한다.
    """
    p = Path(os.path.expanduser("~/.bashrc"))
    assert p in conftest._CONTENT_GUARDED
    reason = conftest._pollution_reason(
        p, ("absent", None), ("file", b"export TEAMMODE_HOME=/oops\n"))
    assert reason is not None, "가드가 프로파일 내용 변화를 탐지해야 한다"


# ── L2-B0 새 쓰기 표면 가드 실증 ──────────────────────────────────────────────
# install-mcp(MCP 등록 실경로)·credentials 금고가 만들 새 표면을 conftest 가드가
# "실제로 덮고 발화하는지" 박는다. 라이브 _pollution_reason 호출로 "가드 없으면 못 잡고,
# 있으면 잡는다"를 실증(blind spot 차단). 스킬 디렉토리 가드는 v0.2 이월로 제거됨(L2-PLAN.md:67).

# (a) claude MCP 등록 실경로 = ~/.claude.json (근거: spec/05-onboard-skill.md:90).

def test_claude_mcp_config_is_guarded():
    """~/.claude.json 이 _GUARDED 에 들어 MCP 등록 변경을 잡는다."""
    p = Path(os.path.expanduser("~/.claude.json"))
    assert p in conftest._GUARDED, "claude MCP 등록 실경로가 _GUARDED 에 없음"
    assert p == conftest._CLAUDE_MCP_CONFIG, "MCP 등록 실경로 상수와 불일치"


def _claude_snap(raw: bytes):
    """_snapshot 이 ~/.claude.json 에 대해 만드는 ('claude-json', footprint) 튜플을 재현."""
    return ("claude-json", conftest._claude_json_footprint(raw))


def test_claude_mcp_config_teammode_marker_injection_detected():
    """teammode 가 실 ~/.claude.json 에 _teammode_managed 마커를 주입하면 가드가 발화한다.

    실 파일 무접촉 — conftest 의 라이브 판정 함수 _pollution_reason 을 footprint 스냅샷으로
    직접 호출. install-mcp 의 _build_mcp_entry 가 남기는 소유 마커(_teammode_managed:true)가
    실 파일에 새로 등장하면 = teammode 발 오염 → 반드시 잡아야 한다(빌드안전 핵심).
    """
    p = Path(os.path.expanduser("~/.claude.json"))
    before = _claude_snap(b'{"mcpServers": {"existing": {}}, "projects": {"/a": {}}}')
    after = _claude_snap(
        b'{"mcpServers": {"existing": {}, "linear": {"_teammode_managed": true}}, '
        b'"projects": {"/a": {}}}')
    reason = conftest._pollution_reason(p, before, after)
    assert reason is not None, "teammode _teammode_managed 마커 주입을 가드가 잡아야 한다"
    assert "MCP" in reason or "오염 감지" in reason, (
        f"MCP 등록 파일 오염 메시지가 MCP/설정 분류가 아님: {reason}")


def test_claude_mcp_config_normalize_hook_injection_detected():
    """teammode normalize.py 경로 훅이 실 ~/.claude.json 본문에 새로 등장하면 발화한다.

    방어적 가드 — 어댑터는 보통 settings 에 훅을 쓰지만, 만에 하나 ~/.claude.json 본문에
    normalize.py 경로가 새로 들어오면 teammode 발 오염으로 본다(footprint has_hook 전이).
    """
    p = Path(os.path.expanduser("~/.claude.json"))
    before = _claude_snap(b'{"mcpServers": {}}')
    after = _claude_snap(
        b'{"hooks": {"PreToolUse": [{"command": "infra/agents/claude/normalize.py x"}]}}')
    reason = conftest._pollution_reason(p, before, after)
    assert reason is not None, "normalize.py 훅 주입(footprint has_hook 전이)을 가드가 잡아야 한다"


def test_claude_mcp_config_live_session_unrelated_change_silent():
    """라이브 Claude 세션의 무관한 ~/.claude.json 갱신은 가드가 무시한다(플레이크 제거).

    이 머신에서 라이브 세션 MCP 가 ~/.claude.json 의 projects·타 mcpServers 항목을 주기적으로
    갱신한다. teammode footprint(_teammode_managed 항목·normalize.py 훅)는 그대로면, 전체
    byte 가 달라져도 발화하면 안 된다 — 과거 전체 byte 비교가 만든 teardown ERROR 의 정체.
    """
    p = Path(os.path.expanduser("~/.claude.json"))
    before = _claude_snap(
        b'{"mcpServers": {"linear": {"_teammode_managed": true}, "user_srv": {"cmd": "x"}}, '
        b'"projects": {"/old": {}}}')
    # 라이브 세션이 무관 항목만 변경: projects 갱신 + 사용자 MCP 서버 추가/변경.
    after = _claude_snap(
        b'{"mcpServers": {"linear": {"_teammode_managed": true}, "user_srv": {"cmd": "y"}, '
        b'"another_user_srv": {}}, "projects": {"/old": {}, "/new": {"sessions": 5}}}')
    reason = conftest._pollution_reason(p, before, after)
    assert reason is None, (
        "라이브 세션의 무관 갱신(projects·타 mcpServers)에 false-positive 발생 "
        "— teammode footprint 가 동일하면 무발화해야 한다")


# (b) credentials 실경로 = $XDG_DATA_HOME/teammode/credentials
#     (기본 ~/.local/share/teammode/credentials).

def test_credentials_dir_is_guarded():
    """credentials 금고 실경로가 _GUARDED + _ENTRY_TRACKED_DIRS 에 들어 침투를 잡는다."""
    p = Path(os.path.expanduser("~/.local/share/teammode/credentials"))
    assert p == conftest._real_credentials_dir(), "credentials 실경로 헬퍼와 불일치"
    assert p in conftest._GUARDED, "credentials 실경로가 _GUARDED 에 없음"
    assert p in conftest._ENTRY_TRACKED_DIRS, (
        "credentials 실경로가 _ENTRY_TRACKED_DIRS 에 없음 → 내부 파일 추가가 안 잡힘")


def test_credentials_dir_creation_detected():
    """가드가 credentials 금고 '부재→존재'를 실제로 발화한다(라이브 호출)."""
    p = conftest._real_credentials_dir()
    reason = conftest._pollution_reason(p, ("absent", None), ("dir", ()))
    assert reason is not None, "가드가 credentials 금고 신규 생성을 오염으로 봐야 한다"


def test_credentials_entry_added_to_existing_dir_detected():
    """이미 존재하는 credentials 디렉토리에 토큰 파일이 새로 생기면 발화한다.

    과거 _CREATION_GUARDED 는 디렉토리를 ("dir",None)으로만 봐서 dir→dir 로 보고 침묵했다
    (dotfile blind spot 동형). 엔트리 집합 추적으로 강화 후엔, 같은 dir 이라도 내부 엔트리
    집합이 달라지면(before=빈 → after=team.json) 오염으로 잡혀야 한다.
    """
    p = conftest._real_credentials_dir()
    before = ("dir", ())                  # 디렉토리는 이미 존재(엔트리 없음)
    after = ("dir", ("team.json",))       # 내부에 토큰 파일이 새로 생김
    reason = conftest._pollution_reason(p, before, after)
    assert reason is not None, (
        "이미 존재하는 credentials 디렉토리 내부 파일 추가를 가드가 잡아야 한다")


def test_credentials_dir_entries_snapshotted():
    """_snapshot 이 credentials 디렉토리를 ('dir', sorted 엔트리 튜플)로 잡는다(실 디렉토리).

    실 호스트에 디렉토리가 없으면(보통의 경우) skip — 실 파일 무접촉 원칙. 있으면 엔트리
    집합 형태로 스냅샷됨을 확인한다(부재→존재 외에 dir→dir 내부 변화도 추적 가능함을 실증).
    """
    p = conftest._real_credentials_dir()
    snap = conftest._snapshot()
    kind, payload = snap[p]
    if kind == "dir":
        assert isinstance(payload, tuple), (
            "엔트리 추적 디렉토리는 ('dir', 엔트리 튜플)로 스냅샷돼야 한다")
        assert payload == tuple(sorted(payload)), "엔트리는 정렬돼 있어야 한다"
    else:
        assert payload is None  # absent/file


def test_xdg_data_home_isolated():
    """autouse 픽스처가 XDG_DATA_HOME 을 격리 tmp 로 덮어 credentials 가 실 경로로 안 샌다."""
    val = os.environ.get("XDG_DATA_HOME")
    assert val, "XDG_DATA_HOME 가 격리되지 않음(credentials 가 실 ~/.local/share 로 샐 위험)"
    assert "xdg-data" in val, f"XDG_DATA_HOME 가 tmp 격리 경로가 아님: {val}"


# ── 라이브 판정 로직 실증 (blind spot 결정적 차단) ──────────────────────────
# conftest._pollution_reason 은 autouse 가드가 실제로 호출하는 **그** 판정 함수다.
# 아래 테스트는 이 함수로 "가드가 박힌 경로는 발화하고, 가드 없으면 침묵한다"를
# 직접 증명한다 — 멤버십만 있고 안 도는 blind spot 을 결정적으로 차단.

ABSENT = ("absent", None)


def test_live_logic_fires_on_mcp_config_teammode_write():
    """가드가 ~/.claude.json 에 teammode 소유 항목이 새로 생기는 것을 실제로 발화한다.

    부재(footprint 없음) → teammode _teammode_managed 항목 등장(footprint 변화)이면 오염.
    (빈 mcpServers 신규 생성처럼 teammode 흔적이 없는 변화는 footprint 동일이라 무발화 —
    그건 라이브 세션도 일으키는 무관 변화이므로 정상.)
    """
    p = Path(os.path.expanduser("~/.claude.json"))
    before = ("claude-json", conftest._claude_json_footprint(None))  # 부재
    after = ("claude-json", conftest._claude_json_footprint(
        b'{"mcpServers": {"linear": {"_teammode_managed": true}}}'))
    reason = conftest._pollution_reason(p, before, after)
    assert reason is not None, "teammode 소유 MCP 항목 신규 등록을 가드가 못 잡음"
    assert "오염 감지" in reason


def test_live_logic_fires_on_credentials_creation():
    """가드가 credentials 금고 디렉토리 신규 생성을 실제로 발화한다."""
    p = conftest._real_credentials_dir()
    reason = conftest._pollution_reason(p, ABSENT, ("dir", ()))
    assert reason is not None, "credentials 금고 신규 생성을 가드가 못 잡음"
    assert "오염 감지" in reason


def test_live_logic_fires_on_credentials_entry_addition():
    """가드가 기존 credentials 디렉토리에 토큰 파일 추가(dir→dir 내부 변화)를 발화한다."""
    p = conftest._real_credentials_dir()
    reason = conftest._pollution_reason(p, ("dir", ()), ("dir", ("team.json",)))
    assert reason is not None, "credentials 디렉토리 내부 파일 추가를 가드가 못 잡음"
    assert "오염 감지" in reason


def test_live_logic_silent_when_not_guarded():
    """가드 대상이 아닌(=_ENTRY_TRACKED_DIRS/_CONTENT_GUARDED 밖) 디렉토리 생성은 침묵한다.

    "가드 없으면 못 잡는다"는 대조군 — 발화가 멤버십 때문임을 증명(blind spot 의 반대 증거).
    같은 dir 생성 변화라도 가드 집합 밖이면 _pollution_reason 이 None 을 반환한다.
    """
    unguarded = Path(os.path.expanduser("~/some-random-unguarded-dir-xyz"))
    assert unguarded not in conftest._ENTRY_TRACKED_DIRS
    assert unguarded not in conftest._CONTENT_GUARDED
    reason = conftest._pollution_reason(unguarded, ABSENT, ("dir", None))
    assert reason is None, "가드 밖 디렉토리 생성은 발화하면 안 됨(대조군)"


def test_live_logic_no_false_positive_on_unchanged_credentials_dir():
    """이미 존재하던 credentials 디렉토리가 무변화면(존재→존재, 엔트리 동일) 발화 안 함.

    엔트리 추적은 before != after 일 때만 발화 — 실 호스트에 금고 디렉토리가 이미 있고
    내부 엔트리도 그대로면 테스트가 안 건드리는 한 통과해야 한다(과방어 방지 대조군).
    """
    p = conftest._real_credentials_dir()
    reason = conftest._pollution_reason(p, ("dir", ("team.json",)), ("dir", ("team.json",)))
    assert reason is None, "변화 없는 credentials 디렉토리에 false-positive 발생"
