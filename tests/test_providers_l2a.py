"""L2-A — provider 팩 기반 테스트 (SPEC §7 · 부록 B-1/B-2 · L2-PLAN A.4).

- 스키마 검증: 정상/누락/오타/항등 불변식 위반
- 4종 provider 팩 로드 + (provider,역할,scope,auth,resource_fields) 단언
- config services 유효성: 빈 슬롯·부분채움 valid / 인스턴스필드 누락 invalid
- 토큰키 린트 발화

호스트 무접촉: 전부 tmp_path. 실 providers/ 는 읽기만(데이터 검증).
"""
import json
import sys
from pathlib import Path

import pytest

_INFRA = Path(__file__).resolve().parent.parent / "infra"
_CONF = Path(__file__).resolve().parent.parent / "conformance"
for p in (str(_INFRA), str(_CONF)):
    if p not in sys.path:
        sys.path.insert(0, p)

import providers as P  # noqa: E402
import install_lib as IL  # noqa: E402
import check as CHECK  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_PROVIDERS = _REPO_ROOT / "providers"

# ─────────── 픽스처: 정상 팩 + tmp providers 디렉토리 ───────────

_GOOD_PACK = {
    "provider": "notion",
    "token_guide": {"url": "https://example.test/x", "steps": ["a", "b"]},
    "default_scope": "team",
    "auth": "api_key",
    "services": ["docs"],
    "resource_fields": ["database_id"],
    "mcp": {"register_hint": "register notion"},
    "action_map": {},
}


def _write_pack(d: Path, name: str, data: dict) -> Path:
    f = d / f"{name}.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


@pytest.fixture
def tmp_providers(tmp_path):
    d = tmp_path / "providers"
    d.mkdir()
    return d


# ─────────── 스키마 검증: 정상 ───────────

def test_validate_good_pack():
    pack = P.validate_pack(dict(_GOOD_PACK), expected_name="notion")
    assert pack.provider == "notion"
    assert pack.canonical_server == "notion"  # 항등
    assert pack.resource_fields == ["database_id"]


def test_load_good_pack_from_file(tmp_providers):
    f = _write_pack(tmp_providers, "notion", _GOOD_PACK)
    pack = P.load_pack(f)
    assert pack.auth == "api_key"


# ─────────── 스키마 검증: 누락 ───────────

@pytest.mark.parametrize("missing", sorted(P._REQUIRED_KEYS))
def test_validate_missing_required_key_rejected(missing):
    data = dict(_GOOD_PACK)
    del data[missing]
    with pytest.raises(P.ProviderValidationError):
        P.validate_pack(data, expected_name="notion")


def test_validate_missing_token_guide_url():
    data = dict(_GOOD_PACK)
    data["token_guide"] = {"steps": []}  # url 누락
    with pytest.raises(P.ProviderValidationError):
        P.validate_pack(data, expected_name="notion")


# ─────────── 스키마 검증: 오타(미지 키) ───────────

def test_validate_unknown_key_rejected():
    data = dict(_GOOD_PACK)
    data["resorce_fields"] = []  # 오타
    with pytest.raises(P.ProviderValidationError):
        P.validate_pack(data, expected_name="notion")


def test_validate_bad_auth_rejected():
    data = dict(_GOOD_PACK)
    data["auth"] = "magic"
    with pytest.raises(P.ProviderValidationError):
        P.validate_pack(data, expected_name="notion")


def test_validate_bad_scope_rejected():
    data = dict(_GOOD_PACK)
    data["default_scope"] = "global"
    with pytest.raises(P.ProviderValidationError):
        P.validate_pack(data, expected_name="notion")


# ─────────── 스키마 검증: 항등 불변식 위반 ───────────

def test_identity_invariant_violation_rejected():
    data = dict(_GOOD_PACK)  # provider == 'notion'
    with pytest.raises(P.ProviderValidationError, match="항등 불변식"):
        P.validate_pack(data, expected_name="slack")  # 파일명 != provider


def test_identity_invariant_via_filename(tmp_providers):
    # 파일명 slack.json 인데 provider 필드가 notion → load 시 reject.
    f = _write_pack(tmp_providers, "slack", _GOOD_PACK)
    with pytest.raises(P.ProviderValidationError, match="항등 불변식"):
        P.load_pack(f)


# ─────────── action_map 예약 필드 (shape 만) ───────────

def test_action_map_reserved_shape_only():
    data = dict(_GOOD_PACK)
    data["action_map"] = {"create": "issue.create"}  # 임의 shape
    pack = P.validate_pack(data, expected_name="notion")
    assert pack.action_map == {"create": "issue.create"}  # 보존만, 소비 없음


def test_action_map_non_object_rejected():
    data = dict(_GOOD_PACK)
    data["action_map"] = ["not", "object"]
    with pytest.raises(P.ProviderValidationError):
        P.validate_pack(data, expected_name="notion")


# ─────────── 실 4종 provider 팩 로드 + 요약 단언 ───────────

def test_real_four_providers_load():
    packs = P.load_all(_REAL_PROVIDERS)
    assert set(packs) == {"linear", "slack", "notion", "google"}


@pytest.mark.parametrize("name,role,scope,auth,fields", [
    ("linear", "issues", "personal", "api_key", []),
    ("slack", "chat", "team", "bot_token", ["channel_id"]),
    ("notion", "docs", "team", "api_key", ["database_id"]),
    ("google", "calendar", "personal", "oauth", ["calendar_id"]),
])
def test_real_provider_summary(name, role, scope, auth, fields):
    pack = P.lookup(name, providers_dir=_REAL_PROVIDERS)
    assert pack is not None
    assert pack.provider == name
    assert pack.services == [role]
    assert pack.default_scope == scope
    assert pack.auth == auth
    assert pack.resource_fields == fields


def test_lookup_missing_returns_none():
    assert P.lookup("doesnotexist", providers_dir=_REAL_PROVIDERS) is None


# ─────────── services 스키마 유효성 (B-2) ───────────
#
# 적대검수 P1-1: services 스키마 위반은 services_are_valid 의 책임(설치/검증 시점
# [warn] 발화용)이지, config_is_valid(=role 판정, 파괴적 분기)의 책임이 아니다.
# 채운 슬롯 검증은 services_are_valid 로 직접 단언한다.

def _base_cfg(services):
    return {"spec_version": "0.1", "team": {"name": "acme"},
            "services": services}


def test_empty_slot_valid_none():
    assert IL.services_are_valid(None, providers_dir=_REAL_PROVIDERS)


def test_empty_slot_valid_empty_dict():
    assert IL.services_are_valid({}, providers_dir=_REAL_PROVIDERS)


def test_partial_fill_valid():
    # linear(resource_fields 없음) 만 채움 — 나머지 빈 슬롯.
    services = {"issues": {"provider": "linear", "scope": "personal"}}
    assert IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


def test_filled_slot_with_instance_field_valid():
    services = {"docs": {"provider": "notion", "scope": "team",
                         "database_id": "abc123"}}
    assert IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


def test_filled_slot_missing_instance_field_invalid():
    # notion 인데 database_id 누락 → services 스키마 invalid([warn] 발화).
    services = {"docs": {"provider": "notion", "scope": "team"}}
    assert not IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


def test_filled_slot_empty_instance_field_invalid():
    services = {"chat": {"provider": "slack", "scope": "team",
                         "channel_id": "   "}}
    assert not IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


def test_unknown_role_invalid():
    services = {"tickets": {"provider": "linear"}}
    assert not IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


def test_unknown_provider_invalid():
    services = {"issues": {"provider": "jira"}}
    assert not IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


def test_bad_scope_invalid():
    services = {"issues": {"provider": "linear", "scope": "world"}}
    assert not IL.services_are_valid(services, providers_dir=_REAL_PROVIDERS)


# ─────────── role 판정은 services 스키마/provider팩에 의존하지 않는다 (P1-1) ───────────

def test_config_is_valid_ignores_bad_services():
    # 채운 슬롯이 인스턴스 필드를 누락해도(서비스 스키마 invalid) role 은 member.
    # provider팩이 멀쩡한 경우조차 config_is_valid 는 services 를 보지 않는다.
    cfg = _base_cfg({"docs": {"provider": "notion", "scope": "team"}})
    assert IL.config_is_valid(cfg, providers_dir=_REAL_PROVIDERS)


def test_role_not_demoted_when_provider_pack_missing(tmp_path):
    """채운 notion 슬롯 멤버 config + providers/ 누락 → 여전히 member, 덮어쓰기 X.

    적대검수 P1-1 재현: provider 팩이 삭제·미동기화돼도 valid 멤버 config 가
    introducer 로 강등돼 빈 도입자 config 로 덮어써지는 데이터손실이 없어야 한다.
    """
    member_cfg = {
        "spec_version": "0.1",
        "team": {"name": "acme", "greeting": "hi", "farewell": "bye"},
        "admin_contact": "alice",
        "services": {"docs": {"provider": "notion", "scope": "team",
                              "database_id": "realdb"}},
    }
    cfg_path = tmp_path / "team.config.json"
    cfg_path.write_text(json.dumps(member_cfg), encoding="utf-8")
    # providers/ 디렉토리 자체가 없는 상황 (팩 미동기화).
    assert not (tmp_path / "providers").exists()

    # role 판정: provider팩 없어도 member (introducer 강등 X).
    assert IL.detect_role(tmp_path) == "member"

    # 멱등 가드: 도입자 경로(write_introducer_config)가 멤버 config 를 덮어쓰지 않음.
    IL.write_introducer_config(tmp_path, team_name="other",
                               admin_contact="bob")
    after = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert after == member_cfg  # services·greeting·farewell 소실 없음


def test_example_config_is_introducer_via_real_code_path():
    # team.config.example.json 을 그대로(=$-strip 없이) 실코드 경로로 판정.
    # placeholder team.name 이므로 도입자여야 한다(config_is_valid=False).
    ex = json.loads((_REPO_ROOT / "team.config.example.json").read_text(
        encoding="utf-8"))
    assert not IL.config_is_valid(ex, providers_dir=_REAL_PROVIDERS)
    # services 블록은 $comment 가 들어있지 않으므로 스키마도 유효해야 한다.
    assert IL.services_are_valid(ex["services"], providers_dir=_REAL_PROVIDERS)


def test_example_config_copied_team_judged_member(tmp_path):
    """example 을 실제 복사해 team.name 만 채운 팀이 member 로 올바로 판정.

    P0-1 회귀 방어: services 블록 안 $comment 가 멤버를 introducer 로 오판해
    config 를 덮어쓰던 데이터손실 경로가 막혔는지 실코드로 실증.
    """
    ex = json.loads((_REPO_ROOT / "team.config.example.json").read_text(
        encoding="utf-8"))
    ex["team"]["name"] = "acme"  # placeholder → 실 이름
    cfg_path = tmp_path / "team.config.json"
    cfg_path.write_text(json.dumps(ex), encoding="utf-8")

    assert IL.detect_role(tmp_path) == "member"
    # 멱등 가드가 채워진 config 를 덮어쓰지 않음.
    IL.write_introducer_config(tmp_path, team_name="x", admin_contact="y")
    after = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert after == ex


# ─────────── 토큰키 린트 발화 ───────────

def test_secret_lint_fires_on_plaintext_token(tmp_path):
    f = tmp_path / "team.config.local.json"
    f.write_text('{"api_token": "xoxb-REAL-SECRET-abc123"}', encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert "team.config.local.json" in detail


def test_secret_lint_fires_on_secret_key(tmp_path):
    f = tmp_path / "team-credentials.json"
    f.write_text('{"client_secret": "deadbeeflivevalue"}', encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert "team-credentials.json" in detail


def test_secret_lint_passes_clean_repo():
    name, ok, detail = CHECK.lint_no_tracked_secrets(_REPO_ROOT)
    assert ok, f"실 레포에 토큰키 진입: {detail}"


def test_secret_lint_ignores_resource_id_fields(tmp_path):
    # database_id·calendar_id·channel_id 는 비밀 아님 → 발화 안 함.
    f = tmp_path / "team.config.example.json"
    f.write_text(json.dumps({
        "services": {
            "docs": {"provider": "notion", "database_id": "deadbeef"},
            "calendar": {"provider": "google", "calendar_id": "primary"},
            "chat": {"provider": "slack", "channel_id": "C123"},
        }
    }), encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert ok, detail


def test_secret_lint_allows_placeholder_value(tmp_path):
    f = tmp_path / "team.config.example.json"
    f.write_text('{"api_token": "your-token-here"}', encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert ok, detail


def test_secret_lint_fires_on_dotenv(tmp_path):
    # .env / .env.* 도 추적 거부 대상 (P2).
    f = tmp_path / ".env"
    f.write_text("API_TOKEN=xoxb-REAL-SECRET-abc123\n", encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert ".env" in detail


def test_secret_lint_no_false_positive_on_keylike_words(tmp_path):
    # monkey/donkey/turkey 등 'key' 로 끝나는 단어는 비밀 아님 → 발화 안 함 (P2).
    f = tmp_path / "team.config.example.json"
    f.write_text(json.dumps({
        "monkey": "banana", "donkey": "kong", "turkey": "dinner",
        "spec_version": "0.1",
    }), encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert ok, detail


def test_secret_lint_still_fires_on_real_key_variants(tmp_path):
    # api_key / access-key / apikey / passphrase 는 여전히 발화 (P2 회귀 방어).
    for content in (
        '{"api_key": "live-abc123"}',
        '{"access-key": "live-abc123"}',
        '{"apikey": "live-abc123"}',
        '{"passphrase": "hunter2longvalue"}',
        '{"key": "live-abc123"}',
    ):
        f = tmp_path / "team.config.local.json"
        f.write_text(content, encoding="utf-8")
        name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
        assert not ok, content


def test_run_lint_includes_secret_check():
    report = CHECK.run_lint(_REPO_ROOT)
    names = [c[0] for c in report.checks]
    assert "토큰키 추적 거부" in names


# ─────────── N4: lint git-scan 분기 명시 회귀테스트 ───────────
#
# lint_no_tracked_secrets 의 files=None 경로는 다섯 분기를 가진다. 기존엔
# test_secret_lint_passes_clean_repo(실 레포 GREEN)로만 암묵 커버 → 각 분기를
# 명시 잠금한다. tmp git repo 격리(실 레포·실 HOME 무접촉).
#
# 각 테스트는 해당 분기를 보정 전(제거)으로 돌리면 RED 되게 설계(mutation 의미):
#   ① git tracked 스캔 제거 → ① 테스트 RED
#   ② others --exclude-standard 스캔 제거 → ② 테스트 RED
#   ③ --exclude-standard 제거(gitignored 포함) → ③ 테스트 RED
#   ④ rglob fallback 제거 → ④ 테스트 RED
#   ⑤ tracked 스캔 제거(force-add 는 tracked 분기로 잡힘) → ⑤ 테스트 RED
import subprocess as _sp


def _git(root, *args):
    _sp.run(["git", "-C", str(root)] + list(args),
            check=True, capture_output=True, text=True)


def _git_init(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.test")
    _git(root, "config", "user.name", "tester")


_PLAINTEXT_SECRET = '{"api_token": "xoxb-REAL-SECRET-abc123"}'


def test_secret_lint_branch1_git_tracked(tmp_path):
    """① git tracked secret 파일 → 발화.

    mutation: ls-files(tracked) 스캔 분기 제거 시 이 파일이 후보에서 빠져 RED.
    ⚠️ rglob fallback(분기 ④)이 가리지 않게, untracked-not-ignored 무해 파일을
    하나 둬서 scan 이 비지 않게 한다(others 스캔이 README.md 를 잡음 → `if not scan`
    거짓 → fallback 미진입). 그러면 secret 발화의 유일 경로는 tracked 스캔뿐 →
    tracked 분기 제거가 곧 RED 가 된다.
    """
    _git_init(tmp_path)
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")  # untracked, 무해
    f = tmp_path / "team.config.local.json"
    f.write_text(_PLAINTEXT_SECRET, encoding="utf-8")
    _git(tmp_path, "add", "team.config.local.json")  # tracked(staged)
    # tracked 스캔에만 잡힘을 명시: others 에는 secret 이 안 보인다.
    others = _sp.run(["git", "-C", str(tmp_path), "ls-files",
                      "--others", "--exclude-standard"],
                     capture_output=True, text=True).stdout
    assert "team.config.local.json" not in others
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert "team.config.local.json" in detail


def test_secret_lint_branch2_untracked_not_ignored(tmp_path):
    """② untracked-not-ignored secret → 발화.

    mutation: ls-files --others --exclude-standard 스캔 분기 제거 시,
    아직 add 안 된(=tracked 아닌) 이 파일이 후보에서 빠져 RED.
    """
    _git_init(tmp_path)
    # tracked 무해 파일 하나 — scan 이 비지 않게 해 rglob fallback(분기 ④)이
    # 이 케이스를 가리지 않게 한다. 그러면 secret 발화의 유일 경로는 others 스캔뿐.
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    # add 하지 않음(untracked) + .gitignore 에도 없음(not-ignored).
    f = tmp_path / "team-credentials.json"
    f.write_text(_PLAINTEXT_SECRET, encoding="utf-8")
    # tracked 스캔에는 안 잡힘을 명시 — others 분기에서만 잡혀야 한다.
    tracked = _sp.run(["git", "-C", str(tmp_path), "ls-files"],
                      capture_output=True, text=True).stdout
    assert "team-credentials.json" not in tracked
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert "team-credentials.json" in detail


def test_secret_lint_branch3_gitignored_excluded(tmp_path):
    """③ gitignored secret → 무발화(제외 확인).

    mutation: --exclude-standard 제거(gitignored 포함)하면 이 파일이 후보에
    들어와 RED(=잘못 발화). 즉 gitignore 된 외부 캐시(.codex-ref 등) 제외 보장.
    """
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("*credentials*\n", encoding="utf-8")
    f = tmp_path / "my-credentials.json"
    f.write_text(_PLAINTEXT_SECRET, encoding="utf-8")  # gitignored, not force-added
    # tracked·others 둘 다에서 빠짐을 명시.
    tracked = _sp.run(["git", "-C", str(tmp_path), "ls-files"],
                      capture_output=True, text=True).stdout
    others = _sp.run(["git", "-C", str(tmp_path), "ls-files",
                      "--others", "--exclude-standard"],
                     capture_output=True, text=True).stdout
    assert "my-credentials.json" not in tracked
    assert "my-credentials.json" not in others
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert ok, f"gitignored secret 이 잘못 발화: {detail}"


def test_secret_lint_branch4_non_git_rglob_fallback(tmp_path):
    """④ 비-git tmp dir + secret → rglob fallback 발화.

    mutation: rglob fallback 제거 시 비-git 디렉토리에서 후보가 비어 RED.
    """
    # .git 없음 — ls-files 가 비-0 종료 → scan 빈 채로 fallback.
    assert not (tmp_path / ".git").exists()
    f = tmp_path / "nested" / "team-credentials.json"
    f.parent.mkdir(parents=True)
    f.write_text(_PLAINTEXT_SECRET, encoding="utf-8")
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert "team-credentials.json" in detail


def test_secret_lint_branch5_force_added_ignored(tmp_path):
    """⑤ gitignore 됐지만 force-add(git add -f)된 secret → 발화.

    tracked 는 ignore 무관 — ls-files 가 force-add 된 파일을 나열하므로 잡힌다.
    mutation: tracked(ls-files) 스캔 분기 제거 시, 이 파일은 others(ignored 라
    제외)에도 안 잡혀 후보에서 완전히 빠져 RED.
    """
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("*secret*.json\n", encoding="utf-8")
    f = tmp_path / "team-secret.json"
    f.write_text(_PLAINTEXT_SECRET, encoding="utf-8")
    _git(tmp_path, "add", "-f", "team-secret.json")  # ignore 무시 강제 추적
    # tracked 에는 있고 others 에는 없음(ignored)을 명시.
    tracked = _sp.run(["git", "-C", str(tmp_path), "ls-files"],
                      capture_output=True, text=True).stdout
    others = _sp.run(["git", "-C", str(tmp_path), "ls-files",
                      "--others", "--exclude-standard"],
                     capture_output=True, text=True).stdout
    assert "team-secret.json" in tracked
    assert "team-secret.json" not in others
    name, ok, detail = CHECK.lint_no_tracked_secrets(tmp_path)
    assert not ok
    assert "team-secret.json" in detail
