"""L1-F — install.py 골든 시나리오 (spec/04 §11: I1·I2·I2b·I3·I4·I4b·I-dry).

스펙 04 §11 합격 기준을 실행 가능한 인수 테스트로. install.py 를 subprocess 로
끝까지 돌려(부트스트랩 전 경로) 외부 계약을 검증한다. 호스트 무접촉:
HOME=tmp + --settings 격리 + TEAMMODE_HOME ambient 주입해도 무시 확인.
"""
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
INSTALL = REPO / "infra" / "install.py"
ENGINE = REPO / "infra" / "hooks" / "session-start.py"  # I2b 용

# P1-1 실호스트 무접촉 단위 락 — 어댑터를 직접 들고 monkeypatch HOME 으로 "실 ~/.claude.json
# 등가 경로"를 만들어, 빈 슬롯 install-mcp 가 그 파일을 부재→부재/바이트동일로 유지함을 잠근다.
# (격리 --settings 가 아니라 기본 실경로 해석 path 를 타게 한다.)
sys.path.insert(0, str(REPO / "infra"))  # providers 모듈 해석용
_CLAUDE_ADAPTER_MOD = runpy.run_path(
    str(REPO / "infra" / "agents" / "claude" / "adapter.py"),
    run_name="__golden_claude__")
_CodexAdapterMod = runpy.run_path(
    str(REPO / "infra" / "agents" / "codex" / "adapter.py"),
    run_name="__golden_codex__")
_ClaudeAdapter = _CLAUDE_ADAPTER_MOD["Adapter"]
_CodexAdapter = _CodexAdapterMod["Adapter"]


def _env(home: Path, extra=None):
    # i18n(1b): install 출력은 호스트 로캘 구동(detect_host_locale). 골든 단정은
    # 한국어이므로 로캘을 핀 고정해 CI 호스트 로캘과 무관하게 결정적이게 한다.
    e = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "ko_KR.UTF-8"}
    if "XDG_STATE_HOME" in os.environ:
        e["XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    if extra:
        e.update(extra)
    return e


def _git_init(path: Path, name="Heidi", email="h@h.com"):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(path)})
    for k, v in (("user.name", name), ("user.email", email)):
        subprocess.run(["git", "config", k, v], cwd=str(path), check=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(path)})


def _run_install(team: Path, home: Path, args, extra_env=None):
    return subprocess.run(
        [PY, str(INSTALL), "--root", str(team)] + args,
        capture_output=True, text=True, env=_env(home, extra_env), timeout=60)


# ─────────────────────────── I1 — 빈/엔진만 레포 (도입자) ───────────────────────────

def test_I1_introducer_full_run(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0, proc.stderr
    # memory/·config(빈 슬롯)·sessions/<이름>/·배너 생성
    assert (team / "memory" / "INDEX.md").is_file()
    assert (team / "team.config.json").is_file()
    cfg = json.loads((team / "team.config.json").read_text())
    assert cfg["services"] == {}
    assert (team / "memory" / "team" / "sessions" / "heidi").is_dir()
    assert (team / "memory" / "banner.txt").is_file()
    # 첫 세션로그 미생성(M2)
    assert list((team / "memory" / "team" / "sessions" / "heidi").iterdir()) == []
    # verify: 설치 검증은 돌되, 활성화는 opt-in — 기본 install 은 팀모드를 켜지 않는다.
    assert "[verify] 설치 검증 OK" in proc.stdout
    assert "tm on" in proc.stdout          # 켜는 법 안내
    assert not (team / ".teammode-active").exists()  # 마커 없음 = off (설치 ≠ 활성화)


# ─────────────────────────── I2 — 유효 config 레포 (팀원) ───────────────────────────

def test_I2_member_only_upserts_own_config_entry(tmp_path):
    """L2-A2 완화(은수 결정): 팀원 install 은 config **코어 키 무수정** + **자기
    members 엔트리만** upsert. spec_version/team/admin_contact/services 불변,
    타인 members 엔트리 무접촉."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team, name="Ivan", email="ivan@x.com")
    cfg = {"spec_version": "0.1", "team": {"name": "preset-team"},
           "admin_contact": "founder", "services": {},
           "members": [{"name": "founder", "role": "pm"}]}
    (team / "team.config.json").write_text(json.dumps(cfg, indent=2))
    # 기존 팀원 1명 등재된 상태
    md = team / "memory" / "team"
    md.mkdir(parents=True)
    (md / "members.md").write_text("# members\n- founder\n")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso), "--role", "developer"])
    assert proc.returncode == 0, proc.stderr
    after = json.loads((team / "team.config.json").read_text())
    # 코어 키 무수정.
    assert after["spec_version"] == "0.1"
    assert after["team"] == {"name": "preset-team"}
    assert after["admin_contact"] == "founder"
    assert after["services"] == {}
    # 타인(founder) 엔트리 무접촉 + 자기(ivan) 엔트리만 추가.
    assert {"name": "founder", "role": "pm"} in after["members"]
    assert {"name": "ivan", "role": "developer"} in after["members"]
    # members.md 본인 이름 등재
    assert "ivan" in (md / "members.md").read_text()
    assert "[verify] 설치 검증 OK" in proc.stdout  # 기본 install = 활성화 안 함


# ─────────────────────────── I2b — 다음 세션 SessionStart 주입 ───────────────────────────

def test_I2b_next_session_injects_context(tmp_path):
    """I1/I2 직후 새 세션 → SessionStart 훅이 맥락 실제 주입(install 아님)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    _run_install(team, home, ["--settings", str(iso)])
    # 다음 세션 주입은 활성화(on) 전제 → 설치 후 명시적으로 켠다(마커 생성). 설치는 자동 on 안 함.
    on_proc = subprocess.run(
        [PY, str(REPO / "infra" / "teammode.py"), "on",
         "--root", str(team), "--settings", str(iso / "on.json")],
        capture_output=True, text=True, env=_env(home), timeout=60)
    assert on_proc.returncode == 0, on_proc.stderr
    # 멤버 세션로그 하나 적재(첫 작업 시뮬레이션) — 그래야 주입 내용이 생김
    sess = team / "memory" / "team" / "sessions" / "heidi"
    (sess / "2026-06-14.md").write_text(
        "---\nauthor: heidi\ndate: 2026-06-14\nsummary: 부트스트랩 검증\n---\n")
    # 새 세션 = SessionStart 훅 호출
    proc = subprocess.run(
        [PY, str(REPO / "infra" / "hooks" / "session-start.py")],
        input=json.dumps({"event": "SessionStart", "agent": "claude"}),
        capture_output=True, text=True,
        env=_env(home, {"TEAMMODE_HOME": str(team)}))
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "부트스트랩 검증" in out["hookSpecificOutput"]["additionalContext"]


# ─────────────────────────── I3 — 멱등 재실행 ───────────────────────────

def test_I3_idempotent_rerun(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    _run_install(team, home, ["--settings", str(iso)])
    cfg1 = (team / "team.config.json").read_text()
    members1 = (team / "memory" / "team" / "members.md").read_text()
    proc2 = _run_install(team, home, ["--settings", str(iso)])
    assert proc2.returncode == 0
    assert (team / "team.config.json").read_text() == cfg1
    assert (team / "memory" / "team" / "members.md").read_text() == members1
    assert members1.count("heidi") == 1


# ─────────────────────────── I4 — ambient TEAMMODE_HOME 무시 ───────────────────────────

def test_I4_ambient_teammode_home_ignored(tmp_path):
    """실호스트 가리키는 ambient TEAMMODE_HOME set 상태 → 그 경로 무접촉(P1 회귀)."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    victim = tmp_path / "victim"
    victim.mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)],
                        extra_env={"TEAMMODE_HOME": str(victim),
                                   "TGATES_HOME": str(victim)})
    assert proc.returncode == 0
    # victim 무접촉: memory·marker 미생성
    assert not (victim / "memory").exists()
    assert not (victim / ".teammode-active").exists()
    # 작업은 명시 --root(team)에만
    assert (team / "memory").is_dir()


# ─────────────────────────── I4b — --settings 격리 ───────────────────────────

def test_I4b_settings_isolation(tmp_path):
    """--settings 격리 지정 → 실 ~/.claude/settings.json 무접촉, 격리에만 씀."""
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0
    # 실호스트(fake home) ~/.claude/settings.json 무생성
    assert not (home / ".claude" / "settings.json").exists()
    # 격리 경로에만 배선
    assert (iso / "claude" / "settings.json").is_file()
    # 격리는 env 까지 격리 — 실 셸 프로파일에 TEAMMODE_HOME 안 샘(도그푸딩 회귀).
    for name in (".bashrc", ".zshrc", ".profile", ".bash_profile"):
        p = home / name
        if p.is_file():
            assert "TEAMMODE_HOME" not in p.read_text(), \
                f"격리(--settings)인데 {name} 에 env 가 샜다"


# ─────────────────────── D.3 — wire 다동사(install-mcp→sync) + MCP 실경로 무접촉 ───────────────────────

def test_D3_isolated_install_does_not_touch_real_mcp_config(tmp_path):
    """D.3/N3: --settings 격리 install 시 실 MCP 등록 경로(~/.claude.json) 0바이트 무접촉.

    install-mcp 가 격리 모드에서 fake HOME 의 ~/.claude.json 을 부재→부재로 유지하고
    (sync 의 settings.json 경로를 암묵 재활용하지 않음), MCP 등록은 격리 경로에만 쓴다.
    """
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)  # claude 감지됨
    # 실 MCP 등록 경로(fake home ~/.claude.json) — install 전 부재.
    real_mcp = home / ".claude.json"
    assert not real_mcp.exists()
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0, proc.stderr
    # 실 MCP 등록 경로 부재→부재(0바이트 무접촉) — install-mcp 가 격리로만 동작.
    assert not real_mcp.exists(), "격리 install 인데 실 ~/.claude.json 생성됨(N3 footgun)"
    # 빈 슬롯이면 MCP 쓰기는 전부 격리 경로로 향한다(실경로 무접촉이 핵심). 격리 파일이
    # 생기더라도 등록된 teammode provider 는 0이어야 한다(빈 슬롯 = 등록할 것 없음).
    iso_mcp = iso / "claude" / ".claude.json"
    if iso_mcp.exists():
        servers = json.loads(iso_mcp.read_text()).get("mcpServers", {})
        assert all(not v.get("_teammode_managed") for v in servers.values()), \
            f"빈 슬롯인데 teammode MCP 서버 등록됨: {servers}"
    # 훅 sync 는 격리 settings 에 정상 배선.
    assert (iso / "claude" / "settings.json").is_file()


def test_D3_connected_slot_registers_mcp_in_isolation(tmp_path):
    """D.3: 슬롯 연결(linear→issues) → install-mcp 가 격리 MCP 파일에 등록, 실경로 무접촉.

    빈 슬롯(부재) → L1(훅) → 슬롯연결(MCP 등록) 경로를 격리에서 실증. 팀원 경로는 config 를
    수정하지 않으므로(I2) linear 연결 config 를 미리 심어두고 install 한다.
    """
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team, name="Mona", email="m@x.com")
    # linear 가 issues 역할에 연결된 config 를 미리 심음(도입자가 채운 슬롯).
    cfg = {"spec_version": "0.1", "team": {"name": "preset"},
           "admin_contact": "founder",
           "services": {"issues": {"provider": "linear", "scope": "personal"}}}
    (team / "team.config.json").write_text(json.dumps(cfg, indent=2))
    md = team / "memory" / "team"
    md.mkdir(parents=True)
    (md / "members.md").write_text("# members\n- founder\n")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)   # codex 도 감지 → N6: placeholder 등록도 wire 성공
    real_mcp = home / ".claude.json"
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso)])
    assert proc.returncode == 0, proc.stderr   # N6: codex 커맨드없는 placeholder 도 비실패
    # 실 MCP 등록 경로 무접촉(부재).
    assert not real_mcp.exists()
    # 격리 MCP 파일에 tm-linear 등록(teammode 소유 마커, tm-<provider> 별칭).
    iso_mcp = iso / "claude" / ".claude.json"
    assert iso_mcp.is_file(), "연결 슬롯인데 격리 MCP 등록 파일 미생성"
    data = json.loads(iso_mcp.read_text())
    assert "tm-linear" in data.get("mcpServers", {})
    assert data["mcpServers"]["tm-linear"].get("_teammode_managed") is True
    # N3: codex 는 ~/.claude.json 을 절대 안 건드림(여전히 부재). codex MCP 는 config.toml 블록.
    assert not real_mcp.exists()
    codex_cfg = iso / "codex" / "config.toml"
    assert codex_cfg.is_file()
    assert "teammode-mcp-start" in codex_cfg.read_text()  # placeholder 블록 등록됨(N6)
    # 멱등: 재실행해도 격리 MCP 파일 동일, 실경로 여전히 부재.
    before = iso_mcp.read_text()
    proc2 = _run_install(team, home, ["--settings", str(iso)])
    assert proc2.returncode == 0
    assert iso_mcp.read_text() == before
    assert not real_mcp.exists()


# ─────────────────────────── I-dry — dry-run 무접촉 ───────────────────────────

def test_Idry_no_side_effects(tmp_path):
    team = tmp_path / "team"
    team.mkdir()
    _git_init(team)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    iso = tmp_path / "iso"
    proc = _run_install(team, home, ["--settings", str(iso), "--dry-run"])
    assert proc.returncode == 0
    assert "dry-run" in proc.stdout.lower()
    # 무접촉: memory·config·marker·iso settings 전부 미생성
    assert not (team / "memory").exists()
    assert not (team / "team.config.json").exists()
    assert not (team / ".teammode-active").exists()
    assert not iso.exists()
    assert not (home / ".bashrc").exists()


# ───────────── P1-1 — 빈 슬롯 install-mcp 실호스트 무접촉 (안전 게이트) ─────────────
#
# D 검수 P1: 빈 슬롯 install_mcp 가 실 ~/.claude.json 을 생성·재작성하던 안전위반의 락.
# 격리 --settings 가 아니라 **기본 실경로 해석**(monkeypatch HOME → ~/.claude.json) 을 타게
# 해서, 빈 슬롯이면 부재→부재 유지 + 사용자 파일이면 바이트 동일임을 직접 단언한다.

def _scaffold_team(tmp_path, services):
    """tmp 팀 루트 — 실 manifest·events·providers 로 어댑터 구동 + services 로 config."""
    import shutil
    root = tmp_path / "teamroot"
    for sub in ("infra/agents/claude", "infra/agents/codex", "infra/hooks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "infra" / "hooks" / "manifest.json",
                root / "infra" / "hooks" / "manifest.json")
    shutil.copy(REPO / "infra" / "agents" / "claude" / "events.json",
                root / "infra" / "agents" / "claude" / "events.json")
    shutil.copy(REPO / "infra" / "agents" / "codex" / "events.json",
                root / "infra" / "agents" / "codex" / "events.json")
    shutil.copy(REPO / "infra" / "agents" / "claude" / "adapter.py",
                root / "infra" / "agents" / "claude" / "adapter.py")
    (root / "infra" / "agents" / "claude" / "normalize.py").write_text("# stub\n")
    (root / "infra" / "agents" / "codex" / "normalize.py").write_text("# stub\n")
    cfg = {"spec_version": "0.1", "team": {"name": "t"}}
    if services is not None:
        cfg["services"] = services
        (root / "team.config.json").write_text(json.dumps(cfg))
    return root


def _claude_realpath(root, home, monkeypatch):
    """mcp_config_path 미지정 → 어댑터가 기본 실경로(~/.claude.json) 해석.
    monkeypatch HOME 으로 그 실경로를 tmp HOME 안으로 보낸다(격리 아님 — 실경로 path)."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", str(home), 1) if p.startswith("~") else p)
    return _ClaudeAdapter(
        agent_dir=str(root / "infra" / "agents" / "claude"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(home / ".claude" / "settings.json"),
        python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"),
        # mcp_config_path 미지정 → __init__ 이 os.path.expanduser("~/.claude.json") 사용.
    )


def test_P1_empty_slot_install_mcp_absent_stays_absent(tmp_path, monkeypatch):
    """빈 슬롯 install-mcp: 부재 ~/.claude.json 을 생성하지 않는다(부재→부재)."""
    root = _scaffold_team(tmp_path, {})  # 빈 슬롯
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    ad = _claude_realpath(root, home, monkeypatch)
    real_mcp = home / ".claude.json"
    assert not real_mcp.exists()
    out = ad.install_mcp()
    assert not real_mcp.exists(), "빈 슬롯인데 실 ~/.claude.json 신규 생성됨(P1 위반)"
    assert any("빈 슬롯" in c for c in out)


def test_P1_empty_slot_install_mcp_user_file_byte_identical(tmp_path, monkeypatch):
    """빈 슬롯 install-mcp: 사용자 데이터가 든(mcpServers 無) ~/.claude.json 을
    바이트 동일하게 유지한다 — 빈 mcpServers 주입·리인덴트로 touch 하지 않는다."""
    root = _scaffold_team(tmp_path, {})  # 빈 슬롯
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    real_mcp = home / ".claude.json"
    # 사용자 데이터 有, mcpServers 키 無 — 일부러 비정규 들여쓰기(리인덴트 touch 탐지용).
    user_blob = '{"projects":{"x":1},  "numStartups": 7}'
    real_mcp.write_text(user_blob, encoding="utf-8")
    before = real_mcp.read_bytes()
    ad = _claude_realpath(root, home, monkeypatch)
    out = ad.install_mcp()
    assert real_mcp.read_bytes() == before, \
        "빈 슬롯인데 사용자 ~/.claude.json 이 변경됨(빈 mcpServers 주입/리인덴트 touch)"
    assert any("빈 슬롯" in c for c in out)


def test_P1_codex_empty_slot_install_mcp_user_file_byte_identical(tmp_path, monkeypatch):
    """codex 빈 슬롯 install-mcp: 사용자 config.toml 을 바이트 동일하게 유지(부재→부재 포함)."""
    root = _scaffold_team(tmp_path, {})  # 빈 슬롯
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    cfg_path = home / ".codex" / "config.toml"
    # 부재→부재 락
    ad = _CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(cfg_path),
        python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"))
    ad.install_mcp()
    assert not cfg_path.exists(), "codex 빈 슬롯인데 config.toml 신규 생성됨(P1 위반)"
    # 사용자 데이터 有, teammode-mcp 블록 無 → 바이트 동일 락
    user_toml = '[some_user_section]\nkey = "value"\n'
    cfg_path.write_text(user_toml, encoding="utf-8")
    before = cfg_path.read_bytes()
    ad2 = _CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(cfg_path),
        python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"))
    ad2.install_mcp()
    assert cfg_path.read_bytes() == before, \
        "codex 빈 슬롯인데 사용자 config.toml 변경됨(P1 위반)"


# ───────────── N1 — claude install-mcp 멱등 재실행 거짓 빈슬롯 메시지 제거 ─────────────

def test_N1_idempotent_rerun_reports_ok_not_empty_slot(tmp_path, monkeypatch):
    """연결 슬롯 install-mcp 멱등 재실행: '[info] 빈 슬롯' 거짓 메시지 대신 '[ok] 변경없음'."""
    services = {"issues": {"provider": "linear", "scope": "personal"}}
    root = _scaffold_team(tmp_path, services)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    ad1 = _claude_realpath(root, home, monkeypatch)
    out1 = ad1.install_mcp()  # 최초 등록
    assert any("[mcp]" in c for c in out1)
    ad2 = _claude_realpath(root, home, monkeypatch)
    out2 = ad2.install_mcp()  # 멱등 재실행
    assert not any("빈 슬롯" in c for c in out2), \
        f"연결됐는데 거짓 '빈 슬롯' 메시지: {out2}"
    assert any("변경 없음" in c for c in out2)


# ───────────── N2 — codex install-mcp 무변경에 거짓 [mcp] 등록 보고 제거 ─────────────

def test_N2_codex_idempotent_rerun_no_false_register_report(tmp_path, monkeypatch):
    """codex 연결 슬롯 멱등 재실행: 무변경인데 '[mcp] 등록' 보고 대신 '[ok] 변경없음'."""
    services = {"issues": {"provider": "linear", "scope": "personal"}}
    root = _scaffold_team(tmp_path, services)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    cfg_path = home / ".codex" / "config.toml"
    common = dict(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(cfg_path), python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"))
    out1 = _CodexAdapter(**common).install_mcp()
    assert any("[mcp]" in c for c in out1)
    out2 = _CodexAdapter(**common).install_mcp()  # 멱등 재실행 = 무변경
    assert not any("[mcp]" in c for c in out2), \
        f"codex 무변경인데 거짓 '[mcp] 등록' 보고: {out2}"
    assert any("변경 없음" in c for c in out2)


# ───────────── P2-1 — codex MCP 봉인 유닛 (N3 정적 락) ─────────────

def test_P2_codex_mcp_config_sealed_and_parent_install_mcp_refuses(tmp_path):
    """codex 인스턴스의 _read_mcp_config()=={}(봉인) + 부모 install_mcp 직접 호출 시
    NotImplementedError — 상속된 ~/.claude.json 실경로가 footgun 으로 새지 않음(N3)."""
    root = _scaffold_team(tmp_path, {"issues": {"provider": "linear"}})
    cfg_path = tmp_path / "codex.config.toml"
    ad = _CodexAdapter(
        agent_dir=str(root / "infra" / "agents" / "codex"),
        manifest_path=str(root / "infra" / "hooks" / "manifest.json"),
        settings_path=str(cfg_path), python="python3", team_root=str(root),
        providers_dir=str(REPO / "providers"))
    # 봉인: 부모 _read_mcp_config 가 실경로 접근 없이 {} 반환.
    assert ad._read_mcp_config() == {}
    # mcp_config_path 가 봉인 센티넬(codex 모듈이 상속받는 그 _SEALED)인지 확인.
    assert ad.mcp_config_path is _CodexAdapterMod["_SEALED"]
    # 부모(codex 가 상속한 정확한 BaseAdapter) install_mcp 를 직접 호출 → 봉인 가드 거부.
    # (codex 의 base = 같은 runpy 네임스페이스의 Adapter 이므로 _SEALED identity 일치.)
    Base = _CodexAdapter.__mro__[1]
    with pytest.raises(NotImplementedError):
        Base.install_mcp(ad)
