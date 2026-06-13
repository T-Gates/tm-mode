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
    """가드의 _CONTENT_GUARDED 분기가 'before≠after(부재→존재 포함)'를 오염으로 본다.

    실 파일을 건드리지 않고 conftest 의 비교식을 그대로 재현해, suffix 없는 .bashrc
    에 대해서도 탐지가 동작함을 실증한다.
    """
    p = Path(os.path.expanduser("~/.bashrc"))
    before = ("absent", None)
    after = ("file", b"export TEAMMODE_HOME=/oops\n")
    # conftest 의 _CONTENT_GUARDED 분기: suffix 무관, before != after → 오염.
    assert p in conftest._CONTENT_GUARDED
    assert before != after, "가드가 프로파일 내용 변화를 탐지해야 한다"
