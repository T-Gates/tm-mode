# Issue 114 Maintainer Test Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep public SPEC/document version consistency checked in the tm-mode product repository without distributing that release-only check into team-instance validation updates.

**Architecture:** Treat `tests/` and `conformance/` as the instance-distributed validation layer, while placing product release metadata checks in a top-level `maintainer_tests/` suite that `tm-mode update` does not sync. CI runs both suites explicitly. A validation-sync regression test proves that upgrading an older instance safely removes the former `tests/test_release_pin.py` path without copying its maintainer-only replacement.

**Tech Stack:** Python 3.9+, pytest, GitHub Actions, tm-mode validation sync (`infra/git_ops.py`).

> **Lifecycle/status:** This plan was authored before implementation and
> intentionally kept uncommitted while the task commits were built. It was
> committed after verification as the final execution record. A checked box
> (`[x]`) means the corresponding evidence was observed in this issue-114
> implementation session; an unchecked box means that evidence has not yet
> been observed.

## Observed verification evidence

- Environment follow-up: `python3 --version` printed `Python 3.14.6`.
- Baseline: `python3 -m pytest -q` reached `[100%]` and exited 0.
- Task 1 RED: `python3 -m pytest -q maintainer_tests/test_validation_distribution_boundary.py`
  reported 2 failed and exited 1 while the old `tests/test_release_pin.py` path
  was present and the CI maintainer command was absent.
- Task 1 GREEN: `python3 -m pytest -q maintainer_tests` reported 9 passed and
  exited 0.
- Task 2 regression: `python3 -m pytest -q tests/test_validation_sync.py::test_release_check_move_outside_validation_prunes_old_instance_copy`
  exited 0. The combined `python3 -m pytest -q tests/test_validation_sync.py maintainer_tests`
  run also exited 0; no pass count was recorded for either run.
- Task 3 focused verification: `python3 -m pytest -q tests/test_no_identity_leaks.py maintainer_tests/test_validation_distribution_boundary.py`
  reported 3 passed.
- Final controller product suite after all code/test follow-ups, at HEAD
  `b845c2a`: `python3 -m pytest -q tests maintainer_tests` reached `[100%]` and
  exited 0 under Python 3.14.6. Its pass count was suppressed by double-quiet
  output.
- Final diff hygiene: `git diff --check origin/main..HEAD` exited 0 with no
  output. `git status --short` exited 0 with no output.
- Commit `59c26f4` adversarial RED: the focused default-collection check failed
  when an ambient `PYTEST_PLUGINS` override reached the child pytest process;
  the non-TTY subprocess check also failed its `sitecustomize.py` sentinel when
  ambient Python startup code reached the child.
- Commit `59c26f4` GREEN: `python3 -m pytest -q maintainer_tests` reported
  9 passed after pytest/Python subprocess isolation was tightened.
- Commit `0fe67c3` adversarial RED: adding a no-shared-history requirement to
  the legacy-instance regression failed at the `merge-base` assertion while
  the fixture still cloned shared upstream history.
- Commit `0fe67c3` GREEN: the unrelated-history regression passed for both
  `prior-manual-revert` and `current-pre-move` blob parameters. The combined
  `python3 -m pytest -q tests/test_validation_sync.py maintainer_tests` run
  exited 0; no pass count is inferred.
- Final adversarial re-review at HEAD `0fe67c3`: **Ready: YES**. The review
  passed with no remaining findings.

The whole-product-suite result above was observed at `b845c2a` after commits
`59c26f4` and `0fe67c3` and all other code/test follow-ups. Any subsequent
commit in this execution is evidence-documentation-only and changes this plan,
not implementation or tests.

`pyproject.toml` already sets pytest `addopts = "-q"`; the explicit `-q` in
these commands therefore produced double-quiet output. In particular, the
final product-suite pass count was suppressed and is intentionally not
inferred here.

## As built

- Follow-up hardening in
  `maintainer_tests/test_validation_distribution_boundary.py` adds an actual
  default `pytest --collect-only` subprocess and proves that default collection
  excludes `maintainer_tests/`. Its stronger CI assertion parses the same
  `jobs.pytest` matrix job block and requires both the default and maintainer
  commands there. The child environment removes ambient `PYTEST_ADDOPTS` and
  `PYTEST_PLUGINS` overrides and disables third-party plugin autoload.
- `tests/test_release_pin.py` was moved to
  `maintainer_tests/test_release_pin.py` and modified, not merely renamed. The
  final file retains the public installer-oneliner and changelog contracts that
  already existed on `origin/main`. Its actual follow-up addition is stronger
  non-TTY subprocess host isolation: it explicitly isolates POSIX/Windows home
  state (`HOME`, `USERPROFILE`), XDG and GitHub config directories, `APPDATA`/
  `LOCALAPPDATA`, clears GitHub tokens and inherited member/drive-path values,
  sanitizes Python startup overrides, uses an empty `PATH`, and invokes the
  child with Python `-I -S`; a sentinel proves ambient `sitecustomize.py` is not
  imported.
- `tests/test_validation_sync.py` models a legacy instance with history
  unrelated to upstream and covers two blob states: a prior manually reverted
  release check and the current pre-move release check. Both must be pruned as
  upstream-deleted without receiving the upstream-only replacement.
- `CONTRIBUTING.md`, `CONTRIBUTING.ko.md`, and
  `.github/PULL_REQUEST_TEMPLATE.md` carry equivalent two-suite guidance and
  evidence requirements.
- Final branch inventory is exactly these eight diff entries:
  1. Modify `.github/PULL_REQUEST_TEMPLATE.md`.
  2. Modify `.github/workflows/test.yml`.
  3. Modify `CONTRIBUTING.ko.md`.
  4. Modify `CONTRIBUTING.md`.
  5. Add `docs/superpowers/plans/2026-07-16-issue-114-maintainer-test-boundary.md`.
  6. Move and modify `tests/test_release_pin.py` to
     `maintainer_tests/test_release_pin.py`.
  7. Add `maintainer_tests/test_validation_distribution_boundary.py`.
  8. Modify `tests/test_validation_sync.py`.

---

### Task 1: Lock the product-versus-instance test boundary

**Files:**
- Create: `maintainer_tests/test_validation_distribution_boundary.py`
- Move and modify: `tests/test_release_pin.py` to `maintainer_tests/test_release_pin.py`
- Modify: `.github/workflows/test.yml`

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

Move `tests/test_release_pin.py` to `maintainer_tests/test_release_pin.py`. The
initial move retains its release assertions, including the pre-existing public
installer-oneliner and changelog contracts. Follow-up hardening adds stronger
default-collection and CI-job boundary assertions plus the host-isolated
subprocess environment recorded in **As built** above. In
`.github/workflows/test.yml`, keep the existing instance validation command and
add a separate step:

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

- [x] **Step 3: Request an adversarial code review**

Ask a fresh reviewer to verify that default instance pytest no longer collects the release-only test, upstream CI still runs it, and legacy instance update safely deletes the old path.

Observed final verdict at HEAD `0fe67c3`: **Ready: YES**; the adversarial
re-review passed with no remaining findings.
