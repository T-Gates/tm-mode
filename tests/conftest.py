"""테스트 안전 가드 — 실 에이전트 설정 파일 오염 방지.

자율 빌드 규약: 실 환경(~/.claude/settings.json, ~/.codex/config.toml 등)은
절대 건드리지 않는다. 테스트는 tmp_path 픽스처만 써야 한다.

이 conftest 는 매 테스트 전후로 실 설정 경로의 존재/내용을 스냅샷해, 테스트가
실수로 실 파일을 생성·변경하면 즉시 실패시킨다. (과거 누수 재발 방지)
"""
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

_GUARDED = [
    Path(os.path.expanduser("~/.claude/settings.json")),
    Path(os.path.expanduser("~/.codex/config.toml")),
    Path(os.path.expanduser("~/.codex")),
    Path(os.path.expanduser("~/.claude")),
    *_SHELL_PROFILES,
    _real_state_dir() / "last-pull",
    _real_state_dir(),
]

# 내용 변화(부재→존재 포함)를 suffix 무관하게 오염으로 보는 경로.
# 셸 프로파일은 dotfile 이라 suffix 검사로는 못 잡으므로 여기에 명시한다.
_CONTENT_GUARDED = set(_SHELL_PROFILES) | {
    Path(os.path.expanduser("~/.claude/settings.json")),
    Path(os.path.expanduser("~/.codex/config.toml")),
}


@pytest.fixture(autouse=True)
def _isolate_pull_state(tmp_path_factory, monkeypatch):
    """모든 테스트에 격리 XDG_STATE_HOME 주입 — auto-pull 상태가 실 경로에 새지 않게.

    session-log-remind 가 subprocess 로 띄워져도 상속받도록 os.environ 에 박는다.
    (개별 테스트가 _run_hook 등으로 다시 덮어쓰는 것도 허용 — 그쪽도 격리 경로다.)
    """
    state_home = tmp_path_factory.mktemp("xdg-state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))


def _snapshot():
    snap = {}
    for p in _GUARDED:
        if p.is_file():
            snap[p] = ("file", p.read_bytes())
        elif p.is_dir():
            snap[p] = ("dir", None)
        else:
            snap[p] = ("absent", None)
    return snap


@pytest.fixture(autouse=True)
def _no_real_config_pollution():
    before = _snapshot()
    yield
    after = _snapshot()
    state_paths = {_real_state_dir() / "last-pull", _real_state_dir()}
    for p in _GUARDED:
        b, a = before[p], after[p]
        # auto-pull 상태 경로는 suffix 가 없어도(예: last-pull) 부재→존재 전이를 오염으로
        # 본다 — 다른 도구가 만들 일이 없는 teammode 전용 경로이기 때문.
        if p in state_paths:
            if b[0] == "absent" and a[0] != "absent":
                pytest.fail(
                    f"실 auto-pull 상태 오염 감지: {p} (before=absent, after={a[0]}). "
                    f"테스트는 XDG_STATE_HOME 격리를 써야 한다.")
            continue
        # 셸 프로파일·핵심 설정파일: suffix 무관(dotfile 포함) 상태/내용 변화 = 오염.
        # (`.bashrc` 는 .suffix=="" 라 suffix 검사로는 못 잡힌다 — L1-0 핵심.)
        if p in _CONTENT_GUARDED:
            if b != a:
                pytest.fail(
                    f"실 셸 프로파일/설정 오염 감지: {p} (before={b[0]}, after={a[0]}). "
                    f"테스트는 monkeypatch HOME=tmp + fake 프로파일만 써야 한다.")
            continue
        # 디렉토리(~/.claude 등)는 다른 도구가 만들 수 있으니 파일 내용 변화만 엄격 검사
        if p.suffix and b != a:
            pytest.fail(
                f"실 설정 파일 오염 감지: {p} (before={b[0]}, after={a[0]}). "
                f"테스트는 tmp_path 만 사용해야 한다.")
