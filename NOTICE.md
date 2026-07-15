<!-- NOTICE (Apache-2.0): tm-mode, Copyright 2026 T-Gates -->
**English** | [한국어](NOTICE.ko.md)

# tm-mode Update Notices

This file carries the maintainers' latest update announcements to teams running tm-mode.
When `tm on` detects that upstream's `NOTICE.md` differs from your local copy, the latest entry is surfaced automatically.

---

## 2026-07-15

- **Safer automatic sync**: session-log auto-commit now reconciles concurrent teammate pushes before publishing, retries within a bounded window, and records recoverable pending state instead of blocking hooks.
- **Single SessionStart side-effect pass**: repeated Codex `resume` reconstruction and queued `compact` events for the same root turn no longer replay pull, relay, or context injection; Claude sessions and genuinely new turns continue to run normally.
- **Concurrent-start race fix**: SessionStart claim locking now admits one winner under real simultaneous starts, preventing duplicate hook output without disabling fail-open recovery.

## 2026-06-18

- **Three-tier skill layers (base/core/util)** + `tm-manage-utils`: choose and manage util skills per member. On `tm on`, core skills auto-install and registered util skills are symlinked in; on `off`, they're removed (base skills stay).
- **Auto-update on `tm` ON**: turning team mode on now automatically syncs and commits the upstream engine (`infra/` and `NOTICE.md` only; push stays manual). Skipped if the working tree is dirty.
- **`tm-memory` skill**: loads the team memory INDEX hierarchy (read-only, discovered dynamically).
- **`memory` engine verb + `tm-manage-memory` skill**: add/update/delete memory entries (frontmatter, INDEX, and edit dates handled automatically; folder allowlist and path-traversal guard included).
- **KB write governance**: direct edits (Write/Edit) to `memory/` are now blocked — memory changes must go through the engine verbs (Claude PreToolUse deny + an unlock flag/TTL). ⚠️ This guards Write/Edit only — it does not block a Bash-based workaround.

## 2026-06-17

- Added the `tm` skill: toggles team mode on/off (`infra/skills/base/tm/`).
- Added the `tm-context` skill: quick team-status lookup (`infra/skills/base/tm-context/`).
- Fixed a P0 cp949 Korean-encoding bug: prevented a crash on Korean console output on Windows (`io_encoding.py`).
- Added a banner picker: 6 random banner styles (`infra/banners/`).
- `tm-mode update` file sync: now handles the template's unrelated-histories case (checkout-based sync instead of merge).
- Fixed a hook hang: grandchild processes (e.g. `git-remote-https`) are now terminated in bulk via `killpg`.
