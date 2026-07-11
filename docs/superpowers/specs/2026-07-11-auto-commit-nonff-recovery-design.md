# Auto-Commit Non-Fast-Forward Recovery Design

## Problem

tm-mode originally recovered an auto-commit push rejected as non-fast-forward in
`do_commit(push=True)` by fetching, rebasing with `--autostash`, and retrying the
push. The async-push change later rewired the auto-commit hook to
`do_commit(push=False)` and delegated delivery to a plain-push-only worker. The
recovery implementation remained in `git_ops.py`, but the auto-commit path no
longer invoked it.

When another team member advances the shared branch after a session starts, the
worker preserves the local commit and pending ledger but cannot publish it.
Further session-log edits can accumulate locally until a later session-start
reconcile or manual pull succeeds.

## Goals

- Publish auto-committed session logs even when the remote branch advances first.
- Restore the existing fetch, `rebase --autostash`, and one-time re-push contract.
- Preserve unrelated tracked working-tree edits during recovery.
- Preserve every local commit when authentication, networking, timeout, or merge
  conflicts prevent publication.
- Keep the pending ledger and detached worker as a fallback for unsuccessful
  foreground publication.
- Keep the worker plain-push-only so it never rewrites history concurrently with a
  later file edit.
- Surface failures without terminating the agent session.

## Non-Goals

- Force-pushing or overwriting remote history.
- Automatically resolving content conflicts.
- Changing the manual `tm-mode pull` or session-start reconciliation algorithms.
- Adding runtime dependencies.
- Redesigning the team repository around per-member branches.

## Chosen Approach

Use a foreground-first hybrid:

1. The PostToolUse auto-commit hook calls `do_commit(..., push=True, paths=...)`.
2. The existing `do_commit` path performs the normal push. On a narrowly detected
   non-fast-forward rejection, it fetches, rebases with `--autostash`, and retries
   the push once.
3. If publication succeeds, the hook clears only the pending state it observed
   before starting, using the content-snapshot compare-and-delete helper. It clears
   the sync warning only after the repository reports no commits ahead and no
   pending ledger remains.
4. If the local commit succeeds but publication does not, the hook immediately
   records the localized foreground failure detail in the sync-warning marker,
   emits a localized stderr warning, atomically records a fresh pending ledger,
   and starts the existing detached worker. The worker handles later plain-push
   retries and remains forbidden from rebasing.
5. If pending-ledger persistence fails, the hook replaces the marker with a more
   severe localized warning that still includes the original push failure detail.
   The committed data remains local.

This restores correctness on the path where the agent is already blocked by the
PostToolUse hook. It avoids moving history mutation into a detached process that
can overlap the next file edit.

## State Transitions

| Commit result | Push result | Pending action | Warning action |
|---|---|---|---|
| no commit | n/a | leave existing state unchanged | leave unchanged |
| committed | pushed | compare-and-delete the pre-existing pending snapshot | clear only when ahead is zero and no pending remains |
| committed | not pushed | atomically write new pending state and kick worker | immediately preserve localized detailed failure; worker clears it after confirmed recovery; strengthen it if ledger write fails |
| commit failed | n/a | leave existing state unchanged | hook remains non-fatal |

## Concurrency Safety

- The foreground hook is the only path allowed to fetch and rebase during an
  auto-commit operation.
- `push-worker.py` continues to call `push_plain()` and must leave `HEAD` unchanged
  on non-fast-forward rejection.
- Pending cleanup must use the existing content snapshot and nonce. Unconditional
  deletion could erase a pending record written by another hook invocation.
- Pending reads, writes, and compare-and-delete cleanup share a short team-scoped
  OS advisory lock distinct from the worker's network-duration lock. This closes
  the read/remove TOCTOU without making file-edit hooks wait for network I/O.
- A successful push can include older pending commits as ancestors. Cleanup is
  valid only after `ahead == 0` and the observed ledger has not changed.
- `--autostash` remains required because the hook stages only the files named by
  the normalized event while unrelated tracked edits may remain dirty.

## Timeout Contract

Foreground publication reactivates `PUSH_TOTAL_BUDGET` in `do_commit`. The
auto-commit manifest timeout must therefore return from the async-only value to a
value that covers the 35-second push budget, a possible first local attempt before
an index-lock result, the one-second retry delay, cross-platform rebase rollback,
pending identity validation, and durable fallback state. The implementation uses
70 seconds so the Windows taskkill/drain worst case still leaves lock/fsync and
runner headroom; the old 20-40 second values do not safely cover that retry tail.

## Tests

Add or update tests that prove:

1. The auto-commit hook calls `do_commit(push=True)`.
2. A real two-clone non-fast-forward scenario publishes the session-log commit and
   leaves no pending ledger.
3. An unrelated dirty tracked file survives the rebase unchanged.
4. A rebase conflict preserves the local commit and writes pending state without
   leaving rebase or stash residue.
5. A transient push failure records pending state and kicks the worker.
6. A successful foreground push clears only an unchanged older pending snapshot.
7. A concurrently replaced pending snapshot is not deleted.
8. The plain worker still refuses to rebase and leaves `HEAD` unchanged.
9. Korean and English warning content remains locale-correct.
10. The manifest timeout matches the foreground push budget.
11. A forced writer between pending comparison and deletion cannot lose the new
    nonce, and session-start stale cleanup also uses snapshot-based deletion.

Run focused tests first, then the full pytest suite, static conformance lint, and
all five dynamic conformance scenarios. Verify Python 3.9 compatibility in CI.

## Release

- Ship the fix through a pull request against `main`; never force-push.
- Require CI success and adversarial code review before merge.
- Release as a patch version because this restores documented behavior without
  changing the public CLI.
- Publish this release to PyPI only. Gate the npm publish job behind an explicit
  repository variable that defaults to disabled, so a tag does not attempt or
  fail an npm publication before package ownership and Trusted Publishing are
  configured.
- Immediately before creating the release tag, verify that the live npm opt-in
  repository variable is absent or not `true`; abort tagging otherwise. Also
  verify the workflow at the exact tag candidate SHA still contains that guard.
- Verify the PyPI Trusted Publisher from live settings or recent successful
  publishing evidence before creating the release tag and require the npm job to
  report skipped rather than failed.
- After publishing, update a disposable team instance and reproduce a real
  two-clone non-fast-forward edit-to-origin flow.
- Separately reconcile any team instance that accumulated divergence before the
  fix; installing the new engine does not rewrite existing local history.
