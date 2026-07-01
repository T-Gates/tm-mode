"""이슈 #23 — install 의 push-safe 이메일 사전 점검(GH007 예방).

git user.email 이 GitHub noreply 형식이 아니면 경고 + 정확한 수정 명령을 안내한다.
**자동 변경은 하지 않는다**(noreply 숫자ID 오프라인 유도 불가 — 안내만이 안전).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra"))

import install  # noqa: E402


def _collect_err():
    msgs = []

    def err(*a, **k):
        msgs.append(" ".join(str(x) for x in a))
    return msgs, err


def test_push_safe_email_predicate():
    assert install._email_is_push_safe("123+me@users.noreply.github.com") is True
    assert install._email_is_push_safe("me@users.noreply.github.com") is True
    assert install._email_is_push_safe("me@gmail.com") is False
    assert install._email_is_push_safe("") is False
    assert install._email_is_push_safe(None) is False


def test_warns_for_private_email_on_github_remote(tmp_path):
    msgs, err = _collect_err()
    det = {"git_user_email": "me@gmail.com",
           "remote_url": "https://github.com/T-Gates/tgates-team.git"}
    install._warn_if_email_not_push_safe(det, tmp_path, err)
    joined = "\n".join(msgs)
    assert "GH007" in joined
    assert "config user.email" in joined          # 정확한 수정 명령 안내
    assert "users.noreply.github.com" in joined


def test_no_warn_for_noreply_email(tmp_path):
    msgs, err = _collect_err()
    det = {"git_user_email": "123+me@users.noreply.github.com",
           "remote_url": "https://github.com/T-Gates/tgates-team.git"}
    install._warn_if_email_not_push_safe(det, tmp_path, err)
    assert msgs == []


def test_no_warn_for_non_github_remote(tmp_path):
    # GH007 은 GitHub 한정 — 비-github 원격이면 노이즈 내지 않는다.
    msgs, err = _collect_err()
    det = {"git_user_email": "me@gmail.com",
           "remote_url": "https://gitlab.com/x/y.git"}
    install._warn_if_email_not_push_safe(det, tmp_path, err)
    assert msgs == []


def test_warns_when_email_unset_on_github(tmp_path):
    msgs, err = _collect_err()
    det = {"git_user_email": None,
           "remote_url": "git@github.com:T-Gates/tgates-team.git"}
    install._warn_if_email_not_push_safe(det, tmp_path, err)
    assert any("GH007" in m for m in msgs)
