"""Phase 2 게이트1 — 설치 아티팩트 태그 핀 계약 (RELEASE-v1 2b·2c).

install.sh 가 받는 cli.py, 문서·도움말의 curl 원라이너가 전부 릴리스 태그에
핀돼야 재현 가능한 설치가 된다(main 추적 = 잘못 머지 순간 신규 설치 전멸).
핀 버전은 __version__ 과 일치해야 릴리스 루틴에서 어긋나지 않는다.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _version():
    init = (REPO / "src" / "teammode" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M).group(1)


def _spec_version():
    install_lib = (REPO / "infra" / "install_lib.py").read_text(encoding="utf-8")
    return re.search(
        r'^SPEC_VERSION\s*=\s*"([^"]+)"', install_lib, re.M
    ).group(1)


def test_install_sh_cli_url_pinned_to_version_tag():
    t = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r'CLI_URL="\$\{TEAMMODE_CLI_URL:-([^}]+)\}"', t)
    assert m, "CLI_URL 기본값 파싱 불가"
    url = m.group(1)
    assert "/main/" not in url, f"CLI_URL 이 main 추적: {url} (2b)"
    assert f"/refs/tags/v{_version()}/" in url, \
        f"CLI_URL 핀({url})이 __version__({_version()})과 불일치"


def test_user_doc_oneliners_pinned_to_current_version():
    """Every public curl installer example must pin the current package version."""
    expected = f"/refs/tags/v{_version()}/"
    for rel in ("README.md", "INSTALL.md", "install.sh", "src/teammode/cli.py"):
        t = (REPO / rel).read_text(encoding="utf-8")
        for line in t.splitlines():
            if "raw.githubusercontent.com" in line and "install.sh" in line:
                assert "/main/" not in line, f"{rel}: main 추적 원라이너 잔존 — {line.strip()}"
                assert expected in line, (
                    f"{rel}: installer example is not pinned to v{_version()} — "
                    f"{line.strip()}"
                )


def test_changelog_unreleased_section_precedes_latest_release():
    """Keep pending notes separate from already published release entries."""
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.index("## [Unreleased]")
    latest_release = changelog.index(f"## {_version()}")

    assert unreleased < latest_release, (
        "CHANGELOG.md: [Unreleased] must appear before the latest release section"
    )


def test_public_spec_version_references_match_install_source():
    """Keep the generated config and authoritative spec headers on one version."""
    expected = _spec_version()
    example = json.loads(
        (REPO / "team.config.example.json").read_text(encoding="utf-8")
    )
    assert example["spec_version"] == expected

    for rel in (
        "docs/spec/README.md",
        "docs/spec/00-overview.md",
        "docs/spec/internals.md",
        "docs/spec/onboarding.md",
        "docs/spec/skills.md",
    ):
        header = "\n".join(
            (REPO / rel).read_text(encoding="utf-8").splitlines()[:10]
        )
        assert f"SPEC v{expected}" in header, (
            f"{rel}: header does not match SPEC_VERSION {expected}"
        )


def test_template_repo_configurable():
    """TEMPLATE_REPO: --template 플래그 + env(가시 게이트 동반) — 포크 배포(2b)."""
    t = (REPO / "src" / "teammode" / "cli.py").read_text(encoding="utf-8")
    assert re.search(r'TEMPLATE_REPO\s*=\s*os\.environ\.get\(\s*"TM_TEMPLATE_REPO"', t), \
        "TEMPLATE_REPO 하드코딩 — env 설정가능화 필요(2b)"
    assert '"--template"' in t, "init --template 플래그 없음(P3)"


def test_nontty_env_template_rejected(tmp_path, monkeypatch):
    """[codex P1] 비-TTY 에서 env 만의 비기본 template 은 생성 전에 중단(악성 주입 차단)."""
    import subprocess, sys
    cli = REPO / "src" / "teammode" / "cli.py"
    r = subprocess.run(
        [sys.executable, str(cli), "init", "evil-owner/evil-repo"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env={**__import__("os").environ,
             "TM_TEMPLATE_REPO": "attacker/malicious-template"},
        cwd=str(tmp_path), timeout=30)
    assert r.returncode != 0
    assert "TM_TEMPLATE_REPO" in (r.stderr + r.stdout)
    assert "--template" in (r.stderr + r.stdout)


def test_cli_pin_ref_matches_version():
    """cli.py 는 단독 실행 파일(패키지 import 불가) — 자체 PIN_REF 가 __version__ 과 일치."""
    t = (REPO / "src" / "teammode" / "cli.py").read_text(encoding="utf-8")
    m = re.search(r'^PIN_REF\s*=\s*"refs/tags/v([^"]+)"', t, re.M)
    assert m, "cli.py PIN_REF 없음"
    assert m.group(1) == _version(), f"cli.py 핀({m.group(1)}) ≠ __version__({_version()})"
