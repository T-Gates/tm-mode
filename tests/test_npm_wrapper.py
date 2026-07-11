"""npm/npx 래퍼 계약 (RELEASE-v1 준비리스트 — 설치 용이성).

npm 패키지 = Node stdlib 만 쓰는 얇은 스킨: 태그 핀 raw cli.py 다운로드 → python3
실행(install.sh 의 JS 포팅). pipx/uvx 위임 없음(부재 머신에서 죽는 안 기각 — codex R1).
버전·핀은 __version__ 과 교차 고정(릴리스 루틴에서 함께 bump).
"""
import json
import re
import shutil
import subprocess
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _version():
    init = (REPO / "src" / "teammode" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M).group(1)


def test_package_json_contract():
    pkg = json.loads((REPO / "npm" / "package.json").read_text(encoding="utf-8"))
    assert pkg["name"] == "tm-mode"
    assert pkg["version"] == _version(), "npm 버전 ≠ __version__ — 릴리스 루틴 위반"
    assert pkg["bin"]["tm-mode"] == "bin/tm-mode.js"
    assert pkg.get("dependencies", {}) == {}, "Node stdlib 만 — 의존성 0 계약"
    assert "node" in pkg.get("engines", {})


def test_shim_pin_matches_version():
    js = (REPO / "npm" / "bin" / "tm-mode.js").read_text(encoding="utf-8")
    m = re.search(r'PIN_REF\s*=\s*["\']refs/tags/v([^"\']+)["\']', js)
    assert m, "shim 에 PIN_REF 없음"
    assert m.group(1) == _version(), f"shim 핀({m.group(1)}) ≠ __version__({_version()})"
    assert "/main/" not in js, "main 추적 금지 — 재현 가능한 설치(2b)"


def test_shim_no_pipx_delegation():
    js = (REPO / "npm" / "bin" / "tm-mode.js").read_text(encoding="utf-8")
    assert "pipx" not in js and "uvx" not in js, \
        "v1 은 직행 전용 — 위임은 부재 머신에서 죽음(codex R1 판정)"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node 바이너리 오라클 — CI 러너엔 없을 수 있음")
def test_shim_runs_cli_help_offline(tmp_path):
    """[실기] TEAMMODE_CLI_URL=file:// 로 네트워크 없이 — cli.py 위임·exit 전파."""
    env = {**os.environ,
           "TEAMMODE_CLI_URL": (REPO / "src" / "teammode" / "cli.py").as_uri(),
           "XDG_CACHE_HOME": str(tmp_path / "cache")}
    r = subprocess.run(["node", str(REPO / "npm" / "bin" / "tm-mode.js"), "--help"],
                       capture_output=True, text=True, env=env, timeout=60,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "init" in r.stdout and "join" in r.stdout  # cli.py usage 도달


@pytest.mark.skipif(shutil.which("node") is None, reason="node 오라클")
def test_shim_empty_download_exits_2(tmp_path):
    """빈 파일 방어(install.sh 계약 포팅) — 무동작 성공 위장 금지."""
    empty = tmp_path / "empty.py"; empty.write_text("")
    env = {**os.environ, "TEAMMODE_CLI_URL": empty.as_uri(),
           "XDG_CACHE_HOME": str(tmp_path / "cache")}
    r = subprocess.run(["node", str(REPO / "npm" / "bin" / "tm-mode.js"), "--help"],
                       capture_output=True, text=True, env=env, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 2
    assert "비어" in (r.stderr + r.stdout) or "empty" in (r.stderr + r.stdout).lower()


def test_publish_workflow_has_npm_oidc_job():
    t = (REPO / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "registry.npmjs.org" in t, "npm 발행 잡 없음"
    assert "NPM_TOKEN" not in t and "NODE_AUTH_TOKEN" not in t, \
        "시크릿 토큰 금지 — npm OIDC Trusted Publishing 계약"


def test_npm_publish_requires_explicit_repository_opt_in():
    """npm job 의 실제 if 식에 opt-in 이 있어야 v* 태그 기본 실행을 막는다."""
    text = (REPO / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8")
    match = re.search(
        r"(?ms)^  publish-npm:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        text)
    assert match, "publish-npm job block 없음"
    job = match.group("body")
    condition = re.search(
        r"(?m)^    if:\s*(?P<inline>[^\n]*)(?P<continuation>(?:\n      [^\n]*)*)",
        job)
    assert condition, "publish-npm.if 조건 없음"
    actual_if = condition.group(0)
    assert "vars.NPM_PUBLISH_ENABLED == 'true'" in actual_if
