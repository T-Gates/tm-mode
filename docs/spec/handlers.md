# handlers/ Convention (Deprecated)

> **This document is deprecated.** In the L2 redesign (2026-06-25, Option A confirmed), the `role_server` proxy and
> hand-written `handlers/<role>.py` handler abstraction were discarded and replaced with an **MCP registrar**.
> Truth source: `docs/archive/2026-06-25-L2-redesign.md`.

## What Changed

L2 no longer abstracts roles (issues/chat/docs/calendar) into a tool-neutral function contract.

- **Deprecated**: `handlers/<role>.py` files, required function signature contracts such as `issues_create()`, `handlers_are_valid()` validation, the `infra/mcp/role_server.py` proxy, and "reuse > absorb > hand-write" priority decisions.
- **Replacement**: tm-mode only **connects (registers)** the team's chosen **official vendor MCP to the role slot**. **Behavior such as creating issues or adding calendar events is performed by the AI directly calling `mcp__<alias>__<벤더도구>`**. tm-mode does not wrap behavior in another layer.

## Where to Look

| Old content | Current location |
|---|---|
| Flow for connecting a provider/MCP to a slot | `docs/spec/skills.md §5.4` (tm-connect - registrar flow) |
| MCP alias registration (install-mcp) | `docs/spec/internals.md §2.8` |
| Role slot declarations and provider pack schema | `docs/spec/internals.md §7` |
| Token vault | `docs/spec/internals.md §7.6`, `infra/credentials.py` |

> Do **not** create behavior CLIs/commands such as `tm-issues create` that wrap behavior - that would revive
> the deprecated abstraction (Option B). Behavior is performed by the AI directly calling vendor MCP tools.
