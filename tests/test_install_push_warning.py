import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install as _install  # noqa: E402


class _FakeCR:
    def __init__(self, pushed, committed, detail):
        self.pushed = pushed
        self.committed = committed
        self.ok = committed
        self.detail = detail


def test_push_failure_writes_sync_warning(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(_install._git_ops, "do_commit",
                        lambda *a, **k: _FakeCR(False, True, "committed; push timeout"))
    monkeypatch.setattr(_install._git_ops, "write_sync_warning",
                        lambda root, detail: calls.__setitem__("warn", (root, detail)))
    # 실 함수 시그니처(team_root 필수)와 동일 arity 로 스텁 — 0-인자 lambda 는
    # install.py 의 인자 누락 호출(TypeError)을 통과시켜 실크래시를 은폐했다.
    monkeypatch.setattr(_install._git_ops, "clear_sync_warning",
                        lambda team_root: calls.__setitem__("clear", team_root))
    msgs = []
    _install._autocommit_scaffold(tmp_path, "bob", msgs.append)
    assert calls["warn"] == (str(tmp_path), "committed; push timeout")
    assert "clear" not in calls
    assert any("committed; push timeout" in m for m in msgs)  # detail 표면화


def test_push_success_clears_warning(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(_install._git_ops, "do_commit",
                        lambda *a, **k: _FakeCR(True, True, ""))
    monkeypatch.setattr(_install._git_ops, "write_sync_warning",
                        lambda root, detail: calls.__setitem__("warn", True))
    monkeypatch.setattr(_install._git_ops, "clear_sync_warning",
                        lambda team_root: calls.__setitem__("clear", team_root))
    _install._autocommit_scaffold(tmp_path, "bob", lambda m: None)
    # push 성공 시 자기 팀 루트의 마커만 지운다(멀티팀 오지움 방지 — git_ops 계약)
    assert calls.get("clear") == str(tmp_path)
    assert "warn" not in calls
