---
name: tm-context
description: Use when the user asks about team status, current situation, or needs context loaded. Triggers on "팀 현황", "지금 팀 상황", "맥락 알려줘", "context", "팀원 뭐해", "team status", "current team situation", "load team context", "what is everyone working on".
---

# tm-context — Team Status Lookup

Look up the current team status and summarize it in plain language. This is strictly read-only: do not touch files or state.

> Procedure: fill L1(session logs) **first, always, and completely**, then append connected L2 services(issues/calendar) as supporting context.
> tm-context works fully without L2(graceful).

## L1 Core (Always Works)

### 1. Collect Members and Index

```bash
python3 infra/teammode.py context --root . --json
```

Returned JSON schema:

```json
{
  "state": "on" | "off",
  "index": "<full INDEX.md text or empty string>",
  "members": [
    {
      "author": "<English name>",
      "date": "<work date YYYY-MM-DD>",
      "summary": "<one-line session-log frontmatter summary>",
      "file": "<session-log relative path>",
      "role": "<team.config.json role or null>"
    }
  ]
}
```

> ℹ️ `members` contains only members with at least one session log(`_collect_members` skips directories with zero logs). Team members without logs do not appear in the JSON. If you need the full roster, read `members_file` from `team.config.json`(default: `memory/team/members.md`) and supplement from there.

### 2. Deep-Read Session Logs (Core: Do Not Stop at Summary)

`members[].summary` is only a one-line frontmatter summary and does not capture even half of the real status. **Actually open each member's `members[].file`** and semantically extract the following three items from the body.

| Item | Extract |
|------|-----------|
| **Current work** | Summary of recent work(2-3 lines) |
| **Next work** | Next steps, TODOs, or "next" style statements |
| **Blocked / needs decision** | Blockers, unresolved items, "blocked" statements, or items awaiting decisions |

> ⚠️ Session logs are **free-form**. A fixed template heading is not guaranteed; interpret the team's work-log heading by meaning(for example, `## 작업 내역` / `## Work log`) instead of treating any one heading string as a parse key. Read the body and **extract by meaning**. If the same member directory(`memory/team/sessions/<author>/`) has multiple recent logs, read the most recent 1-3 days together to understand the flow.

### Check `state`

- `state == "on"`: normal.
- `state == "off"`: team mode is off. Explain that recent committed session logs are visible, but the current session is not reflected.

### Fresh Setup Guidance(Zero Session Logs)

If the `members` array is empty(no one has records yet), respond in the user's language with this meaning:
> "There are no team records yet. Session logs will accumulate from the next work session(automatically saved on `tm off`)."

## L2 Supporting Information(Connected Slots Only; Silently Skip If Absent)

Read the `services` slots in `team.config.json` and append data **only when a service is connected**.
If not connected, silently skip. L2 lookup failures(network/auth) are non-fatal too. Show L1 first and only say, in the user's language, that some service data could not be fetched.

### 3. decisions

If `memory/team/decisions/current.md` exists, additionally output a few recent decisions. If it does not exist, silently skip.

### 4. Issues(`services.issues` Slot)

**Read the file directly** to check whether `services.issues.provider` in `team.config.json` is populated.

If connected, that tracker is **registered as an MCP server by the `tm-<provider>` convention**(prefix the provider value with `tm-`). Therefore, do not hard-code the provider:

1. Read the `services.issues.provider` value.
2. Choose and call an **issue list/search** tool exposed by that `tm-<provider>` MCP server(discover the tool name at runtime; providers differ, so do not bake it into the skill).
3. For each member, match only **In Progress** issues by assignee and show only the identifier plus title as supporting information.

> ⚠️ Do not show Backlog/Todo(those are plans, not status, and belong to a separate skill). The `teammode.py issue` verb does not perform MCP lookup; it only echoes provider/schema, so perform the real lookup **directly** through the MCP tool as described above.
> If the `tm-<provider>` server is not registered or has no issue tool, silently skip.

### 5. Calendar(`services.calendar` Slot)

If `services.calendar.provider` is populated, query the MCP server that follows the same `tm-<provider>` convention for **team events in the next 7 days** and append them. Read the calendar identifier from the slot's resource field(`calendar_id`, etc. from `resource_fields` in `providers/<provider>.json`).

> Which calendar the team designates for team events is **instance configuration territory**. Do not hard-code a specific color/category filter into the skill. If not connected, silently skip.

## Output Format

Respond in the user's language. If **member-specific emoji are defined in `members_file`(members.md)**, put the emoji before the name; otherwise use only the name(`members.md` is the single source).

```
<emoji?> <name> (<role?>)
  Current work: (deep extraction from session logs, 2-3 lines)
  Next work: (next steps from session logs, if present)
  Blocked: (show blockers/unresolved items if present; otherwise omit)
  📌 Issues: (In Progress identifier+title when L2 is connected; otherwise omit the line)

📋 Recent Decisions
  ...

🗓 Upcoming Team Events(when L2 calendar is connected)
  ...
```

When supplementing and showing team members from the roster because they have no logs and therefore do not appear in JSON, write the equivalent of "No session logs; recent activity unknown" in the user's language.

## Design Principles Summary

| Item | tm-context behavior |
|------|----------------|
| Repo path | Explicitly use `--root .`(no env fallback) |
| Session logs | Deep-extract semantically from the `file` body, not from summary(free-form) |
| Member emoji | Render if defined in members.md; otherwise use only the name |
| issues slot | Query directly through `tm-<provider>` MCP tools(In Progress only); skip when unconnected |
| calendar slot | Query directly through `tm-<provider>` MCP tools; color filters, etc. are instance territory; skip when unconnected |
| git pull | Not included(the `tm on` procedure handles it) |

## Non-Goals

- State changes, issue creation, file edits: read-only.
- Risk judgment -> `tm-check-health`(separate skill, roadmap).
- Listing Backlog/Todo/plans(status only; plans belong to a separate skill).
- Do not show an error or pressure the user to connect L2 just because L2 is unconnected.

## Engine Verb Call Summary

| Item | Command |
|------|------|
| Collect team context | `teammode.py context --root . --json` |

---

> If behavior differs from expectation, check the `cmd_context` function in `infra/teammode.py`.
> For the `tm-<provider>` MCP registration convention, see `infra/install.py`(provider value -> `tm-<provider>` alias).
