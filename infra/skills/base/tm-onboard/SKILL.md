---
name: tm-onboard
description: Use right after a tm-mode install (`tm-mode init` / `tm-mode join`) — when entering Claude Code/Codex in a freshly set-up team repo. Dispatches a verification subagent to confirm the install landed, and meanwhile conveys what tm-mode does for you. Triggers on "tm-onboard", "teammode onboarding", "start teammode", "is the install ok", "check teammode setup", "팀모드 온보딩", "팀모드 시작", "설치 잘 됐나", "팀모드 셋업 확인", or when the CLI tells the user to open an agent and run tm-onboard.
---

# tm-onboard — Install Verification + tm-mode Value Briefing

The install was **already completed by the CLI (`tm-mode init` / `tm-mode join`)**. When a person enters an agent right after setup, this skill does **exactly two things**:

1. **Check whether the install landed correctly** — delegate this to a **verification subagent** (the main agent does not wait).
2. **Explain what tm-mode does for the person (value)** — the main agent delivers this **while** verification is running.

> ⛔ **Do not install or ask setup questions.** Asking for member name, org, team name, role, agent, or obsidian; calling `install.py` directly; creating or cloning a repo — **all of that belongs to the CLI wizard and has already happened.** Do not reproduce it. (If the user says "set this up" while tm-mode is not installed yet → **if the repo contains `infra/install.py` and AGENTS.md, route to the bootstrap procedure in the "첫 접촉" (First contact) section of AGENTS.md** (clone-and-go — dry-run → conversational approval → that procedure handles installation). Otherwise, tell them to run "`tm-mode init` (new team) / `tm-mode join <url>` (join)" in their terminal and stop. This skill itself never runs installation in any case.)

## Entry Flow (Parallel — In This Order)
1. Immediately dispatch a **verification subagent** (§①). Read-only; report results only. **The main agent does not wait for it to finish.**
2. While the subagent runs, **the main agent delivers the §② value briefing** to the person — do not leave them staring at an empty screen.
3. When the verification result arrives, **summarize it in the user's language**:
   - Everything ✅ → close with one line like "The install was also verified successfully."
   - Anything missing (❌) → explain *what* failed in plain language → tell the person to run `tm-mode join <team-repo URL>` again from the same location (install is idempotent — it safely fills in missing pieces). **Do not manually reproduce the install.py steps.**

> Entry context: after `tm-mode init/join`, the CLI tells the user that *opening Claude/Codex and entering 'tm-onboard' will automatically run verification and briefing* (cli.py `_done()`). This skill is that first entry point. Assume it is running from the **team root (the cloned repo)**.

---

## ① Install Verification — Delegate to a Verification Subagent

As soon as this skill starts, dispatch **one dedicated verification subagent** with the prompt below (read-only, **absolutely no modification or installation** — verification only). In dogfooding, the core bug was "hooks and skill symlinks were not registered", so **do not assume success; have the subagent verify with real files and commands**.

> **[Verification Subagent Prompt Template]** — fill in `<team-root>`, `<member-name>`, and `<agent>`, then dispatch:
>
> From team root `<absolute team-root path>`, **only verify** whether tm-mode was installed correctly. **Absolutely no modification, installation, or git writes (read-only).** Check each item below with real commands/files and report a table with ✅/❌ plus a one-line reason for anything that failed:
> 1. **Core engine**: Does `python3 infra/teammode.py context --root <team-root> --json` print state **without errors**? (`state=off` immediately after install is normal — installed does not mean active.) Also include the team name, member count, and session count from the output (used in the value briefing).
> 2. **Scaffold**: Is `<member-name>` listed in `memory/team/members.md`, and does `memory/INDEX.md` exist?
> 3. **Team config**: Does `team.config.json` exist, and are `agents` recorded?
> 4. **Skill symlinks**: In the agent skill directory (claude=`~/.claude/skills`, codex=the relevant path), are tm-mode skills such as `tm`, `tm-onboard`, and `tm-memory` symlinked/installed?
> 5. **Hook wiring**: Does the agent configuration (claude=`~/.claude/settings.json`) include tm-mode hooks such as session-start?
>
> On the last line, give the **overall verdict** (all normal / list of missing items) in one line.

- The main agent receives only this subagent's final result and summarizes it through Entry Flow step 3. If the result is important enough to distrust self-reporting, the main agent directly re-checks only the missing items.
- **One-line note for Codex users**: after the first install or a hook change, they may need to open codex (TUI) once and press **Trust** on the hook trust prompt — otherwise hooks are silently skipped in headless mode (`codex exec`) (`tm on` detects this and reports it with `[warn]`).

---

## ② tm-mode Value Briefing (While Verification Runs)

Immediately after dispatching the verification subagent, **read `infra/skills/base/tm-onboard/value.md`** and explain the value there in human language, adapted to the person and context (founding a new team / joining an existing team, role). Do not recite it verbatim — follow the tone guide in value.md and give the key points briefly in your own words. **Respond in the user's language.**

> 💡 The **single source of truth for the value content is `value.md`**. Do not duplicate value copy in this body — if the team/founder edits only value.md, the delivered message should change.

Then add the current team status using **the status returned by the verification subagent** (team name, member count, session count), in the user's language.
- **A brand-new team is empty** (0 session logs, 0 KB items) → normal. In the value.md tone, say that the structure is in place and it starts accumulating **from now** — do not describe the empty state as a failure.
- **Close with the next single step**: say that when they start work, they should run `tm on` — it refreshes and opens the agent with team context. This is the only instruction needed to prevent "what now?" right after setup.
- If the team is empty (0 session logs / 0 KB items) and it looks like team documents already exist in a document service, you may add exactly one sentence: if they have existing docs, they can say "memory upload" to bring them into team memory. Mention it only; execution belongs to the `tm-import-memory` skill. Stop there.

---

## Non-Goals / Boundaries
- **No installation, setup questions, repo creation, or install.py calls** — the CLI (`tm-mode init/join`) already handled all of it.
- **Do not hold the main flow synchronously for verification** — delegate to a subagent, and let the main agent deliver the value briefing in parallel (do not make the person wait).
- **Do not list menus** — service connection (L2), Obsidian registration, banner/personality customization, and memory upload are *not handled here*. When they become necessary, each skill (`tm-connect`, `tm-customize`, `tm-import-memory`) will naturally appear as the trigger (progressive).
- Activation (`tm on`) is **only recommended** here — actually turning it on and showing the welcome/banner belongs to the `tm` skill.
- Do not write code, create issues, or push.

## Common Mistakes
| Mistake | Correct Method |
|------|------------|
| Asking again for member name, org, team name, or role | Do not ask — the CLI wizard already collected it |
| Making the person wait while the main agent performs verification synchronously | **Dispatch a verification subagent + deliver value while it runs** (parallel) |
| Calling `install.py` directly to reproduce installation | Verify only. If something is missing, guide the user to rerun `tm-mode join <url>` (idempotent) |
| Skipping verification and assuming "it must be installed" | Have the subagent verify with real files/commands — especially hooks and skill symlinks |
| Listing L2, Obsidian, or personality items as a menu | Do not handle them. Each skill appears when needed |
| Treating an empty team (0 session logs) as a failure | Normal — narrate that it starts accumulating from now |
| Starting installation for someone who is not installed yet | The skill does not install — inside a repo, route to the "첫 접촉" (First contact) bootstrap in AGENTS.md; outside a repo, guide them to "`tm-mode init`/`join` in the terminal" and stop |

---
> For behavior specs, check `docs/spec/` (install.py and onboard skill); for the entry contract, check `_done()` in `src/teammode/cli.py` (it points to this skill).
