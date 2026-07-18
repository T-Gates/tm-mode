# Changelog

이 파일은 tm-mode의 주요 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따릅니다.

## [Unreleased]

## 0.1.7 — 2026-07-18

- Recovered pending session-log publication after an external `pull --rebase` rewrites the pending commit: exact patch-equivalent coverage across publication, pending CAS advance, SessionStart reconciliation, and pending rewrite now advances the ledger to current history so ordinary push can clear it.
- Kept rewritten-history recovery fail-closed: checkout, remote, destination, fingerprint, incomplete or empty proof, Git errors, timeouts, replace refs, and grafts are rejected; workers still never rewrite history or force-push.
- Separated maintainer-only release-pin checks from syncable instance validation, keeping upstream release gates in CI without distributing them into team instances and resolving #114.

## 0.1.6 — 2026-07-15

- Made automatic session-log publication safe under concurrent teammate pushes: auto-commit now performs a bounded reconcile and exact push, preserves unrelated work, and records recoverable pending state instead of blocking agent hooks.
- Prevented repeated Codex `SessionStart(resume)` reconstruction and queued `compact` events for the same root turn from replaying session relay, pull, and context-injection side effects; Claude sessions and genuine new turns continue normally.
- Closed a lock-timing race that could admit two simultaneous SessionStart winners, and replaced sequential process tests with real concurrent claim contention coverage.
- Advanced the hook and adapter specification to 0.4 for lifecycle events and exact session, tool-use, turn, and agent correlation fields.

## 0.1.5 — 2026-07-11

- Corrected every public curl installer example to pin the current immutable release tag, and added a regression gate that prevents documentation, launcher, and package pins from drifting apart again.
- Added end-to-end regression coverage proving that raw Claude Code and Codex file-edit hook payloads normalize, auto-commit, and publish a canonical session log to the configured Git remote.
- Cleaned the changelog boundary so already published work no longer remains under `[Unreleased]`; npm publication stays explicitly disabled unless the repository opt-in is enabled.

## 0.1.4 — 2026-07-11

- Restored reliable session-log auto publication: non-fast-forward pushes now use bounded foreground fetch/rebase/re-push recovery, with exact-autostash rollback and durable branch-bound retry state on failure.
- Kept failed publication non-fatal and recoverable through a redacted warning plus a branch-bound, multi-entry pending ledger with locked compare-and-delete and atomic private state writes.
- Hardened session startup and shutdown publication gates: shared hook deadlines preserve context output, and `tm off` waits for both `ahead=0` and an empty readable pending ledger.
- Completed locale-aware output across `tm on`/`update`/`off`, engine commands, validators, runtime hooks, default identity text, and adapter warnings while preserving team-authored text; the remaining adapter sync informational summaries stay explicitly tracked as i18n backlog.
- Made npm publication explicitly opt-in. This release publishes to PyPI while the npm job remains skipped unless `NPM_PUBLISH_ENABLED=true` is deliberately configured.

## 0.1.3 — 2026-07-08

- `tm-mode update [path]` launcher subcommand: existing team repos can now run the PyPI/pipx launcher to sync the repo engine from upstream (`--dry-run` and `--force` pass through to `infra/teammode.py update`).
- Session-start engine update notice: active teams now get an actionable notice when local `NOTICE.md` differs from upstream, with a throttled fetch so long-running `on` teams are not silently left behind.
- Prevented product GitHub Actions workflows from leaking into team instances through repository guards, install-time stripping, and sync-path denylisting.
- Contributor CI gate docs: PR template and CONTRIBUTING now spell out Python 3.9 compatibility, local environment isolation, fake git remote branch setup, identity hygiene, and post-main-merge full-suite reruns.

## 0.1.2 — 2026-07-07

- **Install wizard, redesigned**: clack-style rail UI with arrow-key widgets, vivid palette (stdlib ANSI, zero deps), context lines and key hints on every step, `◇ answer` echoes, URL Step 0 (`tm-mode join` without arguments now asks), and `init` fully matching the same style.
- **English by default, Korean preserved**: engine output, docs, skills, and agent entry docs (AGENTS/CLAUDE/INSTALL) are now English; skill/entry triggers are bilingual (Korean phrases kept). Hook injections pick ko/en automatically from `team.locale` (existing teams unchanged).
- **Public hygiene**: fixtures and history fully anonymized; a CI guard (`tests/test_no_identity_leaks.py`) now blocks real-environment identifiers. Repo history was rewritten accordingly (fresh clones recommended).
- Codex `hooks.json` coexistence notice + spec contract; backlog moved to GitHub Issues (label `design`).

## [0.1.1] - 2026-07-06

### Added

- Agent one-liner entry point: paste the repo URL into Claude Code/Codex — README "For AI agents" gives agents a deterministic, approval-gated setup procedure (entry contract is now three-way: URL one-liner / clone-and-go / CLI).
- npm shim `npx tm-mode` (tag-pinned cli.py runner, zero deps) + npm OIDC publish job.

### Changed

- Setup wizard copy rewritten in English (calm, consistent tone); README is English-first with the Korean edition inline (home-anchor toggle).
- Codex placeholder MCP entries are now comments — a command-less real table bricked codex config loading (fatal "invalid transport"); existing brick tables self-heal on next sync.

### Fixed

- validation sync v2: safe deletion of upstream-removed files (blob-history + terminal-removal judgement, raw-copy backups).
- `[Y/n]` prompts now treat "no" as no.

## 0.1.0 — 2026-07-05

- Shipped the first validated L1 workflow: cross-agent Claude Code/Codex adapters, team memory, automatic context injection, daily session logs, guarded auto-commit/push, and optional Obsidian registration.
- Added the initial L2 collaboration surface: provider slots, member roles, issue/context/customization/contribution/import skills, dynamic knowledge-base routing, and per-member session-log editing (#13, #14, #16, #17, #25, #31, #51).
- Added the original asynchronous publication fallback and durable pending marker (#45), later superseded as the primary success path by the bounded foreground recovery in 0.1.4.
- Established memory governance and safety boundaries: route-gated custom folders, protected index files, atomic writes, path containment, credential guards, and memory-only staging for team shutdown.
- Hardened install, sync, MCP, Codex wiring, memory deletion/backlinks, non-fast-forward recovery, and statusline idempotency (#3, #10, #15, #19, #22, #28, #29, #30), and adopted Apache-2.0 licensing (#27).
