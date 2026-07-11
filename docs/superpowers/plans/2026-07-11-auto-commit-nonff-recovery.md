# Auto-Commit Non-Fast-Forward Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task-by-task with specification and code-quality review gates.

**Goal:** Restore reliable session-log publication when an auto-commit push is rejected as non-fast-forward, while preserving the async worker as a non-mutating fallback and releasing the fix to PyPI without publishing npm.

**Architecture:** The foreground PostToolUse hook owns commit plus the existing bounded `do_commit(push=True)` recovery transaction. It snapshots any existing pending ledger before committing, conditionally clears only that snapshot after confirmed publication, and creates a fresh pending ledger plus detached plain-push worker only when foreground publication fails. The worker remains plain-push-only. The tag workflow keeps the npm job but requires an explicit repository-variable opt-in, which is disabled by default.

**Tech Stack:** Python 3.9+ standard library, pytest, Git, GitHub Actions, PyPI Trusted Publishing.

---

## Safety constraints

- Work only in the isolated tm-mode feature worktree.
- Do not edit any unrelated repository, especially the user's backend workspace.
- Do not force-push, auto-resolve rebase conflicts, or make `push-worker.py` fetch/rebase.
- Do not publish npm. A tag must make `publish-npm` report `skipped` unless `vars.NPM_PUBLISH_ENABLED == 'true'` was explicitly configured.
- Preserve unrelated tracked edits with `rebase --autostash`; preserve the local auto-commit on every push/rebase failure.
- Run each test in RED before changing its corresponding production behavior.

## Task 1: Pin the foreground/fallback state machine with RED tests

**Files:**

- Modify: `tests/test_auto_commit_sync_warning.py`
- Modify: `tests/test_hooks_l2g.py`
- Modify: `tests/test_async_push.py`
- Test: `tests/test_auto_commit_sync_warning.py`
- Test: `tests/test_hooks_l2g.py`
- Test: `tests/test_async_push.py`

### Step 1: Expand the fake Git operations surface

Make `_FakeGitOps` accept an initial pending snapshot and expose calls for:

```python
def read_push_pending(self, root): ...
def clear_push_pending_if_unchanged(self, root, snapshot): ...
def _ahead_behind_raw(self, root, timeout): ...
def write_push_pending(self, root): ...
```

Record every `do_commit(..., push=...)`, compare-and-delete, pending write, warning write, warning clear, and worker kick. Do not emulate behavior that production code does not own.

### Step 2: Replace the obsolete async-only assertions

Add or revise tests for these exact transitions:

```python
def test_foreground_push_success_clears_unchanged_pending_and_warning(...):
    # pre-existing pending snapshot, committed=True, pushed=True, ahead=0
    # assert push=True
    # assert snapshot compare-and-delete was attempted
    # assert no new pending write or worker kick
    # assert warning clears only after no pending remains

def test_foreground_push_success_preserves_replaced_pending(...):
    # compare-and-delete reports False/current content differs
    # assert warning is not cleared

def test_foreground_push_failure_writes_pending_and_kicks_worker(...):
    # committed=True, pushed=False
    # assert push=True, fresh pending write, worker kick, no warning clear
    # assert result.detail is preserved in sync-warning and localized stderr

def test_pending_write_failure_writes_fallback_warning(...):
    # committed=True, pushed=False, write_push_pending=False
    # assert localized fallback warning preserves result.detail and no worker kick

def test_nothing_to_commit_leaves_pending_and_warning_unchanged(...):
    # committed=False
```

Add Korean and English locale cases for the foreground-failure marker and stderr.
With an English fake `result.detail`, the English product-owned output must contain
no Hangul.

Update `test_auto_commit_pushes_nonblocking` so its spy asserts `push is True`. Because its fake repository has no remote, also assert the local commit remains and a pending ledger is written while the hook exits zero.

Add an index-lock retry test whose first result is `committed=False` with `detail` containing `index.lock`, second result is committed, and both captured calls have `push=True`.

### Step 3: Add the real Git regression tests before implementation

Using the existing bare-origin/two-clone helpers in `tests/test_async_push.py`, add these tests now:

- `test_auto_commit_pushes_origin_without_pending`
- `test_auto_commit_recovers_non_ff_and_preserves_dirty_file`
- `test_auto_commit_conflict_preserves_local_commit_and_pending`

The exact arrangements and assertions are specified in Task 3. Keep the existing worker non-fast-forward test to prove the detached worker never mutates history.

### Step 4: Run the focused tests and confirm RED

Run:

```bash
python -m pytest -q \
  tests/test_auto_commit_sync_warning.py \
  tests/test_hooks_l2g.py::test_auto_commit_pushes_nonblocking \
  tests/test_hooks_l2g.py::test_auto_commit_index_lock_retry_keeps_foreground_push \
  tests/test_async_push.py::test_auto_commit_pushes_origin_without_pending \
  tests/test_async_push.py::test_auto_commit_recovers_non_ff_and_preserves_dirty_file \
  tests/test_async_push.py::test_auto_commit_conflict_preserves_local_commit_and_pending
```

Expected RED: current hook calls `do_commit(push=False)`, always writes pending after a commit, never conditionally clears old pending state, never clears a recovered warning, and leaves the real clone ahead instead of recovering non-fast-forward publication.

Do not edit production code until those failures are observed and recorded.

## Task 2: Implement the foreground-first hybrid in the hook

**Files:**

- Modify: `infra/hooks/auto-commit.py`
- Modify: `infra/i18n.py`
- Test: `tests/test_auto_commit_sync_warning.py`
- Test: `tests/test_hooks_l2g.py`

### Step 1: Snapshot pending before the commit transaction

Replace the boolean-only read with a stable content snapshot:

```python
pending_snapshot = _git_ops.read_push_pending(root)
if pending_snapshot:
    print(...prior pending warning..., file=sys.stderr)
```

The snapshot is the only value allowed in the success-path compare-and-delete.

### Step 2: Restore bounded foreground publication

Call the existing recovery path on both the normal and index-lock retry attempts:

```python
result = _git_ops.do_commit(root, message=message, push=True, paths=paths)
...
result = _git_ops.do_commit(root, message=message, push=True, paths=paths)
```

Do not duplicate fetch/rebase/push logic in the hook; `infra/git_ops.py::do_commit` remains its single implementation.

### Step 3: Handle a successful publication without ledger races

For `committed and pushed`:

1. If `pending_snapshot` exists, call `clear_push_pending_if_unchanged(root, pending_snapshot)`.
2. Re-read pending state.
3. Read `(ahead, _behind, has_upstream)` using `_ahead_behind_raw(root, DEFAULT_TIMEOUT)`.
4. Clear the sync warning only when `has_upstream`, `ahead == 0`, and no pending content remains.
5. Never write a fresh pending ledger or kick the worker on this path.

An absent pre-existing snapshot is valid; do not call compare-and-delete with an empty string.

### Step 4: Preserve the failed-publication fallback

For `committed and not pushed`:

```python
detail = getattr(result, "detail", "") or "unknown push failure"
_git_ops.write_sync_warning(root, _t(
    "hook_ac_push_failed_marker", lang,
    "auto-commit push 실패(커밋 보존): {detail}", detail=detail))
print(_t("hook_ac_push_failed_print", lang, ...detail..., detail=detail),
      file=sys.stderr)
if _git_ops.write_push_pending(root):
    _kick_push_worker(root)
else:
    # Stronger marker still embeds the original push failure detail.
    _git_ops.write_sync_warning(root, localized_pending_failure_with_detail)
    print(localized_warning, file=sys.stderr)
```

Add the two foreground-failure keys to both i18n catalogs. Extend the existing
pending-write-failed marker so it accepts and preserves `{detail}` in both
languages. Keep the hook non-fatal. Leave existing pending state untouched when
no commit was created. A successful worker may clear the warning only after its
existing `ahead == 0` and unchanged-pending checks pass.

### Step 5: Update module comments to match behavior

Remove the stale “local commit only / worker owns every push” text. Document:

- foreground bounded push and non-fast-forward recovery;
- pending worker only as fallback;
- worker stays plain-push-only;
- compare-and-delete protects a newer pending nonce.
- foreground failure detail is visible immediately and survives ledger-write failure.

### Step 6: Run focused GREEN tests

Run the Task 1 command again. Expected: all pass.

### Step 7: Commit the state-machine slice

```bash
git add infra/hooks/auto-commit.py infra/i18n.py tests/test_auto_commit_sync_warning.py tests/test_hooks_l2g.py
git commit -m "fix(hooks): restore foreground auto-commit recovery"
```

## Task 3: Verify real Git recovery and failure preservation

**Files:**

- Modify: `tests/test_async_push.py`
- Reference only: `infra/git_ops.py`
- Reference only: `infra/hooks/push-worker.py`

### Step 1: Verify the rewritten auto-commit success integration contract

The Task 1 replacement for `test_auto_commit_writes_pending_and_commits_sync` uses the real bare origin and clone helper, disables the detached worker, edits a named file, and asserts:

```python
assert result.returncode == 0
assert ahead == 0
assert git_ops.read_push_pending(str(work)) == ""
assert git_ops.read_sync_warning(str(work)) == ""
```

Verify the named file exists at the bare origin tip.

### Step 2: Verify the two-clone non-fast-forward recovery test

Arrange:

1. Clone A is the active team clone.
2. Clone B commits and pushes a different file first.
3. A has an unrelated dirty tracked file not named in the hook payload.
4. A edits the session-log file named in the hook payload.
5. Run the real hook with the worker disabled.

Assert:

- origin contains both B's commit/content and A's auto-commit/content;
- A reports `ahead == 0` and `behind == 0`;
- A's unrelated dirty tracked file is unchanged and remains dirty;
- no pending ledger or sync warning remains;
- neither `.git/rebase-merge` nor `.git/rebase-apply` exists;
- `git stash list` has no hook-created residue.

### Step 3: Verify the real rebase-conflict fallback test

Arrange both clones to change the same tracked line, let B push first, then invoke the hook for A's edit.

Assert:

- hook exits zero;
- A's local auto-commit still exists and is ahead of its upstream;
- origin remains at B's safe commit (no force push);
- the worktree is not left in an active rebase;
- pending ledger exists and the foreground failure detail is immediately observable in the sync-warning marker;
- A's intended content is still recoverable from its local commit;
- no autostash residue remains.

### Step 4: Preserve the worker non-mutation regression

Keep `test_worker_non_ff_keeps_pending_writes_marker` and strengthen it, if needed, to snapshot `HEAD` before the worker and assert it is unchanged after rejection. Do not change worker production code.

### Step 5: Run the new integration tests and confirm GREEN

```bash
python -m pytest -q \
  tests/test_async_push.py::test_auto_commit_pushes_origin_without_pending \
  tests/test_async_push.py::test_auto_commit_recovers_non_ff_and_preserves_dirty_file \
  tests/test_async_push.py::test_auto_commit_conflict_preserves_local_commit_and_pending \
  tests/test_async_push.py::test_worker_non_ff_keeps_pending_writes_marker
```

The first three were observed failing in Task 1. They must now pass without modifying `git_ops.py` unless a test exposes a separately proven defect in its existing recovery implementation.

### Step 6: Run the full async/recovery regression set

```bash
python -m pytest -q tests/test_async_push.py tests/test_git_ops.py
```

### Step 7: Commit the integration tests

```bash
git add tests/test_async_push.py
git commit -m "test(hooks): cover auto-commit non-ff recovery"
```

## Task 4: Restore the hook timeout contract with RED/GREEN coverage

**Files:**

- Modify: `tests/test_hooks_l2g.py`
- Modify: `infra/hooks/manifest.json`
- Modify comments only: `infra/git_ops.py`

### Step 1: Add a timeout contract test

Find the `auto-commit.py` manifest entry and assert:

```python
assert entry["timeout"] >= 35
assert "foreground" in entry["_timeout_note"].lower()
assert "PUSH_TOTAL_BUDGET" in entry["_timeout_note"]
```

### Step 2: Run it and confirm RED

```bash
python -m pytest -q tests/test_hooks_l2g.py::test_auto_commit_manifest_covers_foreground_push_budget
```

Expected RED: timeout is 20 seconds and the note declares async-only behavior.

### Step 3: Update the manifest

Set the auto-commit timeout to 35 seconds. Explain that the retry path can consume
a first local attempt, one second of backoff, and then a fresh 22-second
push/recovery transaction plus cleanup. Update the stale `git_ops.py` comment that
names the old 30-second manifest cap; do not change the 22-second runtime budget.

### Step 4: Run GREEN and commit

```bash
python -m pytest -q tests/test_hooks_l2g.py::test_auto_commit_manifest_covers_foreground_push_budget
git add infra/hooks/manifest.json infra/git_ops.py tests/test_hooks_l2g.py
git commit -m "fix(hooks): cover foreground push timeout budget"
```

## Task 5: Make npm publication explicitly opt-in

**Files:**

- Modify: `tests/test_npm_wrapper.py`
- Modify: `.github/workflows/publish.yml`

### Step 1: Add the release-scope contract test

Add a test that isolates the actual `publish-npm` job block, extracts its `if`
expression, and asserts that expression contains the exact opt-in guard:

```python
assert "vars.NPM_PUBLISH_ENABLED == 'true'" in npm_job_if
```

The test must fail if the text appears only in a comment or another job. Keep the
existing assertions that the OIDC job exists and uses no npm token. The package
remains releasable later; only automatic execution is disabled.

### Step 2: Run it and confirm RED

```bash
python -m pytest -q tests/test_npm_wrapper.py::test_npm_publish_requires_explicit_repository_opt_in
```

Expected RED: the current job runs on every upstream `v*` tag.

### Step 3: Gate the npm job

Change only the job condition and explanatory comments:

```yaml
publish-npm:
  if: >-
    github.repository == 'T-Gates/tm-mode' &&
    vars.NPM_PUBLISH_ENABLED == 'true'
```

Do not remove the job, change package contents, run `npm publish`, or configure the repository variable.

### Step 4: Run GREEN and packaging contracts

```bash
python -m pytest -q tests/test_npm_wrapper.py tests/test_release_packaging.py
npm pack --dry-run --prefix npm
```

`npm pack --dry-run` is local packaging validation only; it must not publish.

### Step 5: Commit the workflow guard

```bash
git add .github/workflows/publish.yml tests/test_npm_wrapper.py
git commit -m "ci(release): require opt-in for npm publish"
```

## Task 6: Document the fix and run branch verification

**Files:**

- Modify: `CHANGELOG.md`
- Verify: entire repository

### Step 1: Add an Unreleased fix entry

State that auto-committed session logs again recover non-fast-forward pushes in the bounded foreground path and retain the pending worker on failure. State that npm publication is opt-in and disabled by default.

### Step 2: Run focused and full verification

```bash
python -m pytest -q \
  tests/test_auto_commit_sync_warning.py \
  tests/test_async_push.py \
  tests/test_git_ops.py \
  tests/test_hooks_l2g.py \
  tests/test_npm_wrapper.py \
  tests/test_release_packaging.py
python -m pytest -q
python conformance/check.py lint --root .
python conformance/check.py verify --root . --engine "python infra/teammode.py"
git diff --check
```

Record exact pass counts and any skips/warnings. Inspect `git status --short` and the full `origin/main...HEAD` diff for secrets, personal paths, generated files, and unrelated changes.

### Step 3: Adversarial review gate

Request independent reviewers in this order:

1. specification reviewer: state-machine and release-scope compliance;
2. code-quality reviewer: race safety, error handling, Python 3.9 compatibility, test validity;
3. final pre-landing review: entire diff against `origin/main`.

Resolve every correctness issue and rerun affected tests plus full verification.

### Step 4: Commit changelog/cleanup

```bash
git add CHANGELOG.md
git commit -m "docs: note auto-commit sync recovery"
```

## Task 7: Land the feature PR safely

**Files:** no new product edits unless review/CI proves they are required.

### Step 1: Refresh and prove mergeability

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git status --short --branch
```

If `origin/main` advanced, rebase the isolated feature branch only after confirming there are no local uncommitted changes, then rerun full verification. Never force-push an already reviewed public branch; push a corrected branch or use `--force-with-lease` only with separate explicit user approval.

### Step 2: Push and create the feature PR

Use the repository PR template. Include the root cause (#19 recovery bypassed by #45 wiring), the chosen foreground/fallback split, RED/GREEN evidence, and the npm-default-skip release scope.

### Step 3: Wait for every required check

Inspect failed logs rather than retrying blindly. Merge only when checks are green and the PR head SHA still matches the reviewed SHA. Use squash merge with the platform's head-SHA guard.

### Step 4: Verify `main`

Fetch `origin/main`, verify the merge commit contains the intended files, and wait for main-branch CI.

## Task 8: Release patch 0.1.4 to PyPI only

**Files:**

- Modify in a fresh `release/0.1.4` branch after Task 7 lands: `src/teammode/__init__.py`
- Modify: `src/teammode/cli.py`
- Modify: `install.sh`
- Modify: `npm/package.json`
- Modify: `npm/bin/tm-mode.js`
- Modify: `CHANGELOG.md`

### Step 1: Prepare a separate version-only release PR

Update every version/tag pin to `0.1.4`. Updating npm package metadata keeps cross-package pins coherent but does not authorize npm publication. Move the Unreleased entries into a dated `0.1.4` section, including main changes since `0.1.3`.

### Step 2: Verify release pins and package artifacts

```bash
python -m pytest -q tests/test_release_packaging.py tests/test_release_pin.py tests/test_npm_wrapper.py
python -m pytest -q
python conformance/check.py lint --root .
python conformance/check.py verify --root . --engine "python infra/teammode.py"
npm pack --dry-run --prefix npm
git diff --check
```

Again, `npm pack --dry-run` is validation only.

### Step 3: Review, land, and tag

Open and land the release PR under the same CI/head-SHA gates. After `origin/main`
and main CI are green, record the exact candidate SHA and complete all of these
pre-tag gates:

```bash
gh variable get NPM_PUBLISH_ENABLED --repo T-Gates/tm-mode
git show "${RELEASE_SHA}:.github/workflows/publish.yml"
gh run list --repo T-Gates/tm-mode --workflow publish.yml --limit 5
```

- Abort if `NPM_PUBLISH_ENABLED` resolves to `true`. An absent variable is the
  expected disabled state.
- Inspect the workflow from `RELEASE_SHA`, not the working tree, and confirm the
  npm job's actual `if` expression contains the opt-in guard.
- Verify PyPI Trusted Publishing through live repository/environment settings or
  a recent successful upstream PyPI publish run. If neither is available, stop
  before tagging and report the blocker.

Only after those gates pass, create an annotated `v0.1.4` tag at `RELEASE_SHA`
and push only that tag.

### Step 4: Verify publication behavior

On the tag workflow:

- `build` must succeed;
- PyPI `publish` must succeed;
- `publish-npm` must be `skipped`, not failed or run.

Verify PyPI independently:

```bash
python -m pip index versions tm-mode
```

Install `tm-mode==0.1.4` into a disposable environment and run `tm-mode --help`. Verify raw `install.sh` points to `v0.1.4`. Do not run `npm publish`.

## Task 9: Post-release product smoke test and legacy-team handoff

**Files:** no changes to the user's existing team clone until separately authorized.

### Step 1: Disposable two-clone smoke test

Install the released PyPI artifact into an isolated disposable team. Reproduce: remote clone pushes first, active clone edits a session log, auto-commit hook runs, origin ends with both commits, active clone is 0 ahead/0 behind, and pending/warning state is empty.

### Step 2: Report the existing team clone separately

Read-only inspect the user's existing team clone and report its exact ahead/behind, dirty state, pending ledger, and sync warning. Installing the fixed engine does not reconcile the already-diverged history.

Before modifying or rebasing that existing clone, explain the exact commit-preserving reconciliation and ask for separate approval because it changes shared team history. Never touch the unrelated backend workspace.
