---
name: tm
description: Use when the user wants to enable or disable team mode. Triggers on "팀 모드 켜", "tm on", "tm off", "팀 모드 꺼", "tm-mode on", "tm-mode off", "turn team mode on", "enable team mode", "turn team mode off", "disable team mode".
---

# tm — Team Mode Toggle

## Overview

An L1 core skill that turns team mode on or off. ON updates the repo, wires the engine, and injects team context. OFF saves the session log and commits it; push remains a human gate.

## When to Use

- "팀 모드 켜", "tm on", "tm-mode on", "turn team mode on", "enable team mode"
- "팀 모드 꺼", "tm off", "tm-mode off", "turn team mode off", "disable team mode"

## What This Does Not Do

- Write code, create issues, connect services, or automatically call other skills — this skill only toggles team mode.
- Query issue trackers, calendars, or chat (L2 services) — those are outside the L1 core scope. Once L2 services are connected, other skills handle them.
- Push — commit only. A human pushes directly.

## Environment

- Specify the team repo location with the `--root` argument. Do not read environment variables such as `TEAMMODE_HOME`.
- Show the current `git config user.name` as a *suggested value*, but use it only after explicit user confirmation.
- Use the `--install` flag to wire hooks into the real host (`~/.claude/settings.json`).

## ON Procedure

1. **Update the repo**: `python3 infra/teammode.py pull --root .`
   - Failure is non-fatal — continue if offline or already up to date.

2. **Turn team mode on**: `python3 infra/teammode.py on --root . --install`
   - The engine prints the greeting if present in `team.config.json`, performs adapter sync with `mode=on`, creates the `.teammode-active` marker, and runs upstream fetch plus NOTICE comparison.
   - NOTICE alert: if upstream `NOTICE.md` differs from local, it prints `[공지] tm-mode 최신 업데이트: …
     — 받으려면 \`tm-mode update\``. If they match, it stays quiet to avoid repeated noise.
   - ⚠️ **The banner is not in engine stdout.** In step 5 below, the agent directly reads `memory/banner.txt` and outputs it in a code fence.

3. **Inject context**: `python3 infra/teammode.py context --root . --json`
   - Parse the JSON result and output it in the welcome format below.
   - `state=on`: normal. `state=off`: wiring problem — tell the user to check hook wiring with `tm-onboard`.

4. **Read the guidelines (required)**: Read `infra/guidelines.md` and `memory/team/guidelines.md` if it exists, and treat them as this session's team-mode behavior guidelines.
   - These are the **same source** as the SessionStart hook. At session start the hook injects them automatically, but when team mode is turned on during a session with `tm on`, the hook does not run. This step fills that gap (team decision: inject guidelines on `tm on` too).
   - Read them with the Read tool and place them **only in context** — do not print the full text to the user (zero noise).

5. **Welcome message** — output in the user's language, in this order:

   **5-a. Banner output (required first item)**: Read `memory/banner.txt` with the **Read tool**, wrap its content in a code fence (\`\`\`), and output it as the **first element** of the welcome message.
   - If the file is missing or blank, omit this section entirely and continue instead of using `_render_banner(team_root)` fallback text.
   - ⚠️ To keep banner lines intact, it **must** be placed inside a \`\`\` code fence. Do not wrap it in an extra \`\`\` if it is already fenced (no duplicate fences). Never abbreviate, summarize, or reconstruct it — output the file content exactly.

   **5-b. Team context** — parse the context JSON and continue with this format. Localize labels naturally and respond in the user's language:

   ```
   Welcome! Welcome to <team name>.

   📊 Current team member status
   <for each member>
   👤 <name> [<date>]
     🔧 Working on: <summary>
     ⏭ Next: <the session log's next-work section, if present>
     🚧 Blocked: <the session log's blockers/attempts section, if present>

   📋 Previous session outcomes (3-5 lines)
   <summary of the most recent session log's work-log section>

   [📅 Upcoming schedule (4 days including today)]
   <if the calendar slot is connected, query today through +3 days; if not connected, omit this section>

   [📌 Issues in progress]
   <if the issues slot is connected, query In Progress; if not connected, omit this section>
   ```

   - **Member emoji**: use the `members[].emoji` field from `memory/team/members.md` or `team.config.json` if present. Otherwise use 👤 as the default emoji.
   - **⏭ Next / 🚧 Blocked**: read and summarize the team's session-log sections for next work and blockers/attempts in the team's language, for example `## 다음 할 일` / `## 막힌 점` or `## Next` / `## Blockers`. Omit if absent (do not output empty slots).
   - **📅 Schedule / 📌 Issues**: show these only when the L2 service slots for calendar and issues are connected. If not connected, omit the entire section (no blank lines or "not connected" text).
   - **Zero session logs**: tell the user, in their language, that the structure is in place and automatic logging/injection will start from the next task. Use this instead of the 📊 section.
   - Member display order follows the order in `members.md`; use the `members` array in the context JSON.

## OFF Procedure

0. **⚠️ Confirm**: Ask the user once more in the user's language (for example, "Turn team mode off?") and proceed only after confirmation. Do not turn it off without confirmation.

1. **Record the session log**: Ask what was done in this session, or use what the user already said.
   - File: `memory/team/sessions/<name>/<today with 06:00 cutoff>.md` (if the time is 00:00-05:59, use the previous day's file).
   - **Write directly with Read(end offset)+Edit** (do not use the `log` verb — deprecated, context-saving, and less faithful):
     - If the file exists, read only the last 20 lines with `Read("<path>", offset=max(1, line_count-20), limit=25)`, then append with `Edit`. If the frontmatter `summary` needs updating, also run `Read("<path>", offset=1, limit=6)`.
     - If the file does not exist, create it with `Write` using frontmatter (`author`/`date`/`summary`) plus the first entry.
     - The reminder hook places the exact offset command into context when the user speaks — follow it as given.
   - `<name>`: the English name confirmed with the user. If it has not been confirmed, ask first, using `git config user.name` only as a suggested value.
   - Content: summarize the session work (see "Session Log Format" below).

2. **Commit**: Commit only the session log (`memory/` directory) to the team repo.
   ```bash
   python3 infra/teammode.py commit --root . --paths "memory/" --message "session: <이름> <날짜>"
   ```
   - Limit the staging scope to the session-log directory with `--paths memory/` — do **not** sweep in the whole working tree such as `infra/` code.
   - **Do not push** — commit only. A human decides whether to push.

3. **Turn team mode off**: `python3 infra/teammode.py off --root . --install`
   - The engine performs adapter sync with `mode=off`, deletes the `.teammode-active` marker, and prints the farewell. **The banner is not in engine stdout** (toolkit pattern, same as ON).
   - ⚠️ **Immediately before the farewell, the agent reads `memory/banner.txt` and prints it in a code fence (\`\`\`)** using the same method as ON 5-a. If the file is missing or blank, omit the banner. Relay the farewell (engine stdout) exactly without changing a single character. Do not wrap it in an extra \`\`\` (no duplicate fences); do not abbreviate or reconstruct it.

4. **Show session summary**: Read the session-log file recorded in step 1 (`memory/team/sessions/<name>/<today>.md`) with the Read tool and summarize the team's work-log section in 3-5 lines for the user, in the user's language.
   - This step is performed by **the agent/skill as an LLM summary** — not by the engine.
   - Output format (localize the label naturally):
     ```
     📋 This session's outcomes
     - <3-5 line summary>
     ```
   - Treat the work-log heading as the team's language convention (for example, `## 작업 내역` or `## Work log`), not as a fixed parse key. If the session-log file does not exist or the work-log section is empty, omit this step.

## Session Log Format (content to put in the body)

Use the team's session-log headings in its language. For example, a Korean team may use `## 작업 내역`, while an English team may use `## Work log`; the meaning is what matters, not a fixed heading string.

```
## Work log
- Do not stop at "what was done." Weave in why it was done (rationale), alternatives
  that were considered and dropped, who decided what, and the key details so the
  context remains alive for later readers. (Exclude personal content; team work only.)

## Blockers / attempts
- Problems encountered, attempts made, and whether they were resolved (omit if none)

## Next
- Follow-up work and undecided items
```

## Engine Verb Call Summary

| Procedure | Command | Notes |
|------|------|------|
| ON — update | `teammode.py pull --root .` | failure=non-fatal |
| ON — wiring/banner | `teammode.py on --root . --install` | banner, greeting, sync, marker |
| ON — context | `teammode.py context --root . --json` | skill parses and summarizes |
| OFF — session log | (skill writes directly with Read(end offset)+Edit) | `log` verb deprecated — not the engine |
| OFF — commit | `teammode.py commit --root . --paths "memory/" --message <message>` | stage only `memory/`; never push |
| OFF — remove hooks | `teammode.py off --root . --install` | sync=off, marker delete, farewell (agent reads `banner.txt` for banner) |
| OFF — session summary | (skill reads session log and produces an LLM summary) | not an engine verb — agent step |

## Common Mistakes

| Mistake | Correct Method |
|------|------------|
| Ending OFF without a session log | Always record the session log first |
| Using a `--push` flag | Commit only — a human pushes directly |
| Inferring and fixing the name from git/account/email | `git user.name` is only a *suggested value* — confirm with the user |
| Skipping pull on ON | Always update first, and continue even if it fails |
| Guessing the repo path from the `TEAMMODE_HOME` environment variable | Explicit `--root .` is required (engine policy A) |
| Querying L2 services such as issue trackers, calendars, or chat | `tm` is only an L1 toggle. After L2 is connected, other skills handle it |
| Printing member display decorations | Member display decoration (emoji, etc.) is outside the tm scope — names in `members.md` and `team.config.json` `members.role` are the member basis, and visual decoration is handled by later infrastructure (SessionStart hook) |
| Dumping the context result verbatim | The skill parses it and summarizes it in human language as "current team situation: ..." |

---

> If behavior is unexpected, check `docs/spec/` or comments in `infra/teammode.py`.
