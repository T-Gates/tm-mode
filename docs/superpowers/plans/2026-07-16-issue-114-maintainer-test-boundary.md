# Issue 114 Maintainer Test Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep public SPEC/document version consistency checked in the tm-mode product repository without distributing that release-only check into team-instance validation updates.

**Architecture:** Treat `tests/` and `conformance/` as the instance-distributed validation layer, while placing product release metadata checks in a top-level `maintainer_tests/` suite that `tm-mode update` does not sync. CI runs both suites explicitly. A validation-sync regression test proves that upgrading an older instance safely removes the former `tests/test_release_pin.py` path without copying its maintainer-only replacement.

**Tech Stack:** Python 3.9+, pytest, GitHub Actions, tm-mode validation sync (`infra/git_ops.py`).

---

### Task 1: Lock the product-versus-instance test boundary

**Files:**
- Create: `maintainer_tests/test_validation_distribution_boundary.py`
- Move later: `tests/test_release_pin.py` to `maintainer_tests/test_release_pin.py`
- Modify later: `.github/workflows/test.yml`

- [x] **Step 1: Write the failing boundary tests**

```python
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_release_contract_checks_live_outside_instance_validation_tree():
    assert not (REPO / "tests" / "test_release_pin.py").exists()
    assert (REPO / "maintainer_tests" / "test_release_pin.py").is_file()


def test_ci_runs_maintainer_tests_separately_from_instance_validation():
    workflow = (REPO / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m pytest -q maintainer_tests" in workflow
```

- [x] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest -q maintainer_tests/test_validation_distribution_boundary.py`

Expected: FAIL because `tests/test_release_pin.py` still exists, its maintainer replacement does not, and CI does not invoke the maintainer suite.

- [x] **Step 3: Move the release contract test and add the CI step**

Move `tests/test_release_pin.py` to `maintainer_tests/test_release_pin.py` without changing its assertions. In `.github/workflows/test.yml`, keep the existing instance validation command and add a separate step:

```yaml
- name: Run maintainer-only product checks
  run: python -m pytest -q maintainer_tests
```

- [x] **Step 4: Run the boundary and release tests and verify GREEN**

Run: `python3 -m pytest -q maintainer_tests`

Expected: all maintainer tests pass, including the public SPEC/document version contract.

### Task 2: Prove old instances migrate cleanly

**Files:**
- Modify: `tests/test_validation_sync.py`

- [x] **Step 1: Add an integration regression for moving an upstream-only test out of `tests/`**

```python
def test_release_check_move_outside_validation_prunes_old_instance_copy(
        tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    team = tmp_path / "team"

    _git(tmp_path, "init", "--bare", str(upstream))
    _git(upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(upstream), str(seed))
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@t")
    _write(seed, "tests/test_release_pin.py", "def test_old(): assert True\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "v1 release check in validation")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", str(upstream), str(team))
    _git(team, "config", "user.name", "t")
    _git(team, "config", "user.email", "t@t")
    _git(team, "remote", "add", "upstream", str(upstream))

    (seed / "maintainer_tests").mkdir()
    _git(seed, "mv", "tests/test_release_pin.py",
         "maintainer_tests/test_release_pin.py")
    _git(seed, "commit", "-m", "v2 move release check out of validation")
    _git(seed, "push")
    _git(team, "fetch", "upstream")

    plan = go.plan_validation_sync(str(team), "upstream/main")
    assert "tests/test_release_pin.py" in {d.path for d in plan.safe_deletes}
    planned = set(plan.safe_paths) | set(plan.up_to_date)
    assert not any(path.startswith("maintainer_tests/") for path in planned)

    result = go.apply_validation_sync(str(team), "upstream/main", plan)
    assert result.ok, result.detail
    assert "tests/test_release_pin.py" in result.deleted
    assert not (team / "tests" / "test_release_pin.py").exists()
    assert not (team / "maintainer_tests" / "test_release_pin.py").exists()
```

- [x] **Step 2: Run the targeted sync regression**

Run: `python3 -m pytest -q tests/test_validation_sync.py maintainer_tests`

Expected: all validation-sync and maintainer tests pass.

### Task 3: Document the two test contracts

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `CONTRIBUTING.ko.md`
- Modify: `.github/PULL_REQUEST_TEMPLATE.md`

- [x] **Step 1: Distinguish instance validation from maintainer release checks in both contributor guides and the PR template**

Document these commands with matching English/Korean guidance, and require both
results in the pull request template:

```bash
python -m pytest -q                  # instance-distributed runtime validation
python -m pytest -q maintainer_tests # upstream-only release/docs/package contracts
```

- [x] **Step 2: Verify public hygiene and English/Korean/template parity**

Run: `python3 -m pytest -q tests/test_no_identity_leaks.py maintainer_tests/test_validation_distribution_boundary.py`

Expected: all checks pass, and inspection of `CONTRIBUTING.md`,
`CONTRIBUTING.ko.md`, and `.github/PULL_REQUEST_TEMPLATE.md` confirms that all
three surfaces describe or request both test commands consistently.

### Task 4: Full verification and review

**Files:**
- Verify all modified files above.

- [x] **Step 1: Run the complete product suite**

Run: `python3 -m pytest -q tests maintainer_tests`

Expected: exit 0 with no failures.

- [x] **Step 2: Inspect the diff and staged migration shape**

Run: `git diff --check origin/main..HEAD`, `git status --short`, and
`git diff --stat origin/main..HEAD`, then inspect the full branch diff and this
plan document.

Expected: one test move, one new maintainer boundary test, one validation-sync
regression, one CI step, matching English/Korean/PR-template contributor
guidance, and this implementation-plan record only; no SPEC content or version
changes.

- [ ] **Step 3: Request an adversarial code review**

Ask a fresh reviewer to verify that default instance pytest no longer collects the release-only test, upstream CI still runs it, and legacy instance update safely deletes the old path.
