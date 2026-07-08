"""GitHub template workflow cleanup.

팀 인스턴스는 product repo 의 `.github/workflows` 를 보유하면 안 된다. cleanup core 는
install.py chokepoint 와 init-created join 경로가 공유한다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import importlib.util
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
INSTALL = REPO / "infra" / "install.py"

sys.path.insert(0, str(REPO / "infra"))
import git_ops as go  # noqa: E402


def _load_cli():
    orig_pkg = sys.modules.get("teammode")
    orig_cli = sys.modules.get("teammode.cli")
    if "teammode" not in sys.modules or not hasattr(sys.modules["teammode"], "__path__"):
        pkg = types.ModuleType("teammode")
        pkg.__path__ = []  # type: ignore[attr-defined]
        pkg.__package__ = "teammode"
        sys.modules["teammode"] = pkg
    spec = importlib.util.spec_from_file_location(
        "teammode.cli", REPO / "src" / "teammode" / "cli.py")
    cli = importlib.util.module_from_spec(spec)
    cli.__package__ = "teammode"
    sys.modules["teammode.cli"] = cli
    spec.loader.exec_module(cli)  # type: ignore[union-attr]
    sys.modules["teammode"].cli = cli  # type: ignore[attr-defined]
    return cli, orig_pkg, orig_cli


def _restore_cli(orig_pkg, orig_cli):
    if orig_pkg is None:
        sys.modules.pop("teammode", None)
    else:
        sys.modules["teammode"] = orig_pkg
    if orig_cli is None:
        sys.modules.pop("teammode.cli", None)
    else:
        sys.modules["teammode.cli"] = orig_cli


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check, capture_output=True, text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _init_bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True,
                   capture_output=True, text=True)
    return path


def _seed_template_repo(tmp_path: Path, *, origin_name: str = "team.git",
                        install_wrapper: bool = False,
                        git_ops_text: str | None = None) -> tuple[Path, Path]:
    origin = _init_bare(tmp_path / origin_name)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True,
                   capture_output=True, text=True)
    _git(seed, "config", "user.name", "Template")
    _git(seed, "config", "user.email", "template@example.com")
    (seed / "infra").mkdir()
    if install_wrapper:
        (seed / "infra" / "install.py").write_text(
            "import runpy, sys\n"
            f"mod = runpy.run_path({str(INSTALL)!r}, run_name='__tm_install__')\n"
            "raise SystemExit(mod['main'](sys.argv[1:]))\n",
            encoding="utf-8")
    else:
        (seed / "infra" / "install.py").write_text("# fake\n", encoding="utf-8")
    if git_ops_text is None:
        shutil.copy2(REPO / "infra" / "git_ops.py", seed / "infra" / "git_ops.py")
    else:
        (seed / "infra" / "git_ops.py").write_text(git_ops_text, encoding="utf-8")
    (seed / ".github" / "workflows").mkdir(parents=True)
    (seed / ".github" / "workflows" / "test.yml").write_text("name: test\n",
                                                               encoding="utf-8")
    (seed / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
    (seed / ".github" / "ISSUE_TEMPLATE" / "bug.md").write_text("bug\n",
                                                                 encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "template")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")
    subprocess.run(["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True, capture_output=True, text=True)
    return origin, seed


def _clone(origin: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", str(origin), str(dest)], check=True,
                   capture_output=True, text=True)
    return dest


def _origin_has_path(origin: Path, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(origin), "cat-file", "-e", f"refs/heads/main:{path}"],
        capture_output=True, text=True)
    return proc.returncode == 0


def test_strip_template_workflows_pushes_removal_and_preserves_issue_template(tmp_path):
    origin, _ = _seed_template_repo(tmp_path)
    team = _clone(origin, tmp_path / "team")

    res = go.strip_template_workflows(str(team))

    assert res.ok is True, res.detail
    assert res.pushed is True
    assert not (team / ".github" / "workflows").exists()
    assert (team / ".github" / "ISSUE_TEMPLATE" / "bug.md").is_file()
    assert not _origin_has_path(origin, ".github/workflows/test.yml")
    assert _origin_has_path(origin, ".github/ISSUE_TEMPLATE/bug.md")


@pytest.mark.parametrize("shape", ["file", "symlink", "broken-symlink"])
def test_strip_template_workflows_handles_file_and_symlink_shapes(tmp_path, shape):
    origin = _init_bare(tmp_path / f"{shape}.git")
    team = tmp_path / "team"
    subprocess.run(["git", "clone", str(origin), str(team)], check=True,
                   capture_output=True, text=True)
    _git(team, "config", "user.name", "Template")
    _git(team, "config", "user.email", "template@example.com")
    (team / ".github").mkdir()
    workflows = team / ".github" / "workflows"
    if shape == "file":
        workflows.write_text("not a dir\n", encoding="utf-8")
    elif shape == "symlink":
        target = team / "workflow-target"
        target.mkdir()
        try:
            workflows.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
    else:
        try:
            workflows.symlink_to(team / "missing-target", target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
    _git(team, "add", ".github/workflows")
    _git(team, "commit", "-m", f"template {shape}")
    _git(team, "branch", "-M", "main")
    _git(team, "push", "-u", "origin", "main")
    subprocess.run(["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True, capture_output=True, text=True)

    res = go.strip_template_workflows(str(team))

    assert res.ok is True, res.detail
    assert not os.path.lexists(team / ".github" / "workflows")
    assert not _origin_has_path(origin, ".github/workflows")


def test_strip_template_workflows_push_failure_is_honest(tmp_path):
    origin, _ = _seed_template_repo(tmp_path)
    team = _clone(origin, tmp_path / "team")
    _git(team, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    res = go.strip_template_workflows(str(team))

    assert res.ok is False
    assert res.committed is True
    assert "remote repository still contains .github/workflows" in res.detail
    assert "delete .github/workflows" in res.detail
    assert _origin_has_path(origin, ".github/workflows/test.yml")
    assert not (team / ".github" / "workflows").exists()


def test_install_sanitizes_workflow_bearing_clone(tmp_path, monkeypatch):
    origin, _ = _seed_template_repo(tmp_path)
    team = _clone(origin, tmp_path / "team")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    proc = subprocess.run(
        [PY, str(INSTALL), "--root", str(team), "--member-name", "alice",
         "--settings", str(tmp_path / "iso")],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": str(home), "LC_ALL": "ko_KR.UTF-8"})

    assert proc.returncode == 0, proc.stderr
    assert not (team / ".github" / "workflows").exists()
    assert not _origin_has_path(origin, ".github/workflows/test.yml")
    assert _origin_has_path(origin, ".github/ISSUE_TEMPLATE/bug.md")


@pytest.mark.parametrize("origin_url", [
    "git@github.com:T-Gates/tm-mode.git",
    "ssh://git@github.com/T-Gates/tm-mode.git",
    "https://github.com/T-Gates/tm-mode.git/",
    "https://GitHub.com/T-Gates/tm-mode.git",
    "git@GitHub.com:T-Gates/tm-mode.git",
    "git@github.com:t-gates/TM-MODE.GIT",
    "ssh://GIT@www.github.com/T-Gates/tm-mode.git",
])
def test_product_origin_variants_preserve_workflows(tmp_path, origin_url):
    product = tmp_path / "product"
    product.mkdir()
    subprocess.run(["git", "init", str(product)], check=True,
                   capture_output=True, text=True)
    _git(product, "remote", "add", "origin", origin_url)
    (product / ".github" / "workflows").mkdir(parents=True)
    (product / ".github" / "workflows" / "test.yml").write_text("name: test\n",
                                                               encoding="utf-8")

    assert (product / ".github" / "workflows" / "test.yml").is_file()
    res = go.strip_template_workflows(str(product))
    assert res.ok is True
    assert res.skipped_product is True
    assert (product / ".github" / "workflows" / "test.yml").is_file()


def test_installed_team_with_product_upstream_is_stripped_on_install_rerun(tmp_path, monkeypatch):
    origin, _ = _seed_template_repo(tmp_path)
    team = _clone(origin, tmp_path / "team")
    _git(team, "remote", "add", "upstream", "https://github.com/T-Gates/tm-mode.git")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    proc = subprocess.run(
        [PY, str(INSTALL), "--root", str(team), "--member-name", "alice",
         "--settings", str(tmp_path / "iso")],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": str(home), "LC_ALL": "ko_KR.UTF-8"})

    assert proc.returncode == 0, proc.stderr
    assert not (team / ".github" / "workflows").exists()
    assert not _origin_has_path(origin, ".github/workflows/test.yml")


def test_local_team_repo_named_tm_mode_is_stripped(tmp_path):
    origin, _ = _seed_template_repo(tmp_path, origin_name="tm-mode.git")
    team = _clone(origin, tmp_path / "team-named-tm-mode")

    res = go.strip_template_workflows(str(team))

    assert res.ok is True, res.detail
    assert res.pushed is True
    assert not (team / ".github" / "workflows").exists()
    assert not _origin_has_path(origin, ".github/workflows/test.yml")


def test_product_fork_remote_is_guarded_without_touching_workflows(tmp_path):
    product = tmp_path / "product-fork"
    product.mkdir()
    subprocess.run(["git", "init", str(product)], check=True,
                   capture_output=True, text=True)
    _git(product, "remote", "add", "origin", "git@github.com:alice/tm-mode.git")
    (product / ".github" / "workflows").mkdir(parents=True)
    (product / ".github" / "workflows" / "test.yml").write_text("name: test\n",
                                                               encoding="utf-8")

    res = go.strip_template_workflows(str(product))

    assert res.ok is True
    assert res.skipped_product is True
    assert (product / ".github" / "workflows" / "test.yml").is_file()


def test_github_team_named_tm_mode_with_product_upstream_is_preserved(tmp_path):
    team = tmp_path / "team-named-tm-mode"
    team.mkdir()
    subprocess.run(["git", "init", str(team)], check=True,
                   capture_output=True, text=True)
    _git(team, "remote", "add", "origin", "git@github.com:acme/tm-mode.git")
    _git(team, "remote", "add", "upstream", "https://github.com/T-Gates/tm-mode.git")
    (team / ".github" / "workflows").mkdir(parents=True)
    (team / ".github" / "workflows" / "test.yml").write_text("name: test\n",
                                                             encoding="utf-8")

    res = go.strip_template_workflows(str(team))

    assert res.ok is True
    assert res.skipped_product is True
    assert (team / ".github" / "workflows" / "test.yml").is_file()


def test_cli_fallback_cleans_when_repo_git_ops_lacks_strip_function(tmp_path):
    origin, _ = _seed_template_repo(
        tmp_path,
        git_ops_text="# old git_ops without strip_template_workflows\n")
    team = _clone(origin, tmp_path / "team")
    cli, orig_pkg, orig_cli = _load_cli()
    try:
        res = cli._strip_template_workflows(team)
    finally:
        _restore_cli(orig_pkg, orig_cli)

    assert res.ok is True, res.detail
    assert res.pushed is True
    assert not (team / ".github" / "workflows").exists()
    assert not _origin_has_path(origin, ".github/workflows/test.yml")


def test_cli_fallback_preserves_product_origin_when_git_ops_is_broken(tmp_path):
    product = tmp_path / "product"
    product.mkdir()
    subprocess.run(["git", "init", str(product)], check=True,
                   capture_output=True, text=True)
    _git(product, "remote", "add", "origin",
         "git@github.com:T-Gates/tm-mode.git/")
    (product / "infra").mkdir()
    (product / "infra" / "git_ops.py").write_text("raise RuntimeError('old')\n",
                                                  encoding="utf-8")
    (product / ".github" / "workflows").mkdir(parents=True)
    (product / ".github" / "workflows" / "test.yml").write_text("name: test\n",
                                                               encoding="utf-8")
    cli, orig_pkg, orig_cli = _load_cli()
    try:
        res = cli._strip_template_workflows(product)
    finally:
        _restore_cli(orig_pkg, orig_cli)

    assert res.ok is True
    assert res.skipped_product is True
    assert (product / ".github" / "workflows" / "test.yml").is_file()


def test_cli_fallback_retries_push_when_strip_commit_already_exists(tmp_path):
    origin, _ = _seed_template_repo(
        tmp_path,
        git_ops_text="# old git_ops without strip_template_workflows\n")
    team = _clone(origin, tmp_path / "team")
    _git(team, "config", "user.name", "Template")
    _git(team, "config", "user.email", "template@example.com")
    _git(team, "rm", "-r", ".github/workflows")
    _git(team, "commit", "-m",
         "chore(teammode): remove product workflows from team instance")
    cli, orig_pkg, orig_cli = _load_cli()
    try:
        res = cli._strip_template_workflows(team)
    finally:
        _restore_cli(orig_pkg, orig_cli)

    assert res.ok is True, res.detail
    assert res.pushed is True
    assert not _origin_has_path(origin, ".github/workflows/test.yml")


def test_cli_fallback_no_origin_reports_honest_push_failure(tmp_path):
    origin, _ = _seed_template_repo(
        tmp_path,
        git_ops_text="# old git_ops without strip_template_workflows\n")
    team = _clone(origin, tmp_path / "team")
    _git(team, "remote", "remove", "origin")
    cli, orig_pkg, orig_cli = _load_cli()
    try:
        res = cli._strip_template_workflows(team)
    finally:
        _restore_cli(orig_pkg, orig_cli)

    assert res.ok is False
    assert res.committed is True
    assert "remote repository still contains .github/workflows" in res.detail
    assert "push failed" in res.detail


def test_join_created_false_sanitizes_via_install_chokepoint(tmp_path, monkeypatch):
    origin, _ = _seed_template_repo(tmp_path, install_wrapper=True)
    dest = tmp_path / "joined"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LC_ALL", "ko_KR.UTF-8")
    cli, orig_pkg, orig_cli = _load_cli()
    try:
        args = SimpleNamespace(
            url=str(origin), dir=str(dest), member_name="alice",
            agent=None, role=None, obsidian=False)
        rc = cli.cmd_join(args, created=False)
    finally:
        _restore_cli(orig_pkg, orig_cli)

    assert rc == 0
    assert not (dest / ".github" / "workflows").exists()
    assert not _origin_has_path(origin, ".github/workflows/test.yml")
