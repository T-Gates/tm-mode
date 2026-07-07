# AGENTS.md — tm-mode

This repo is **tm-mode**, a cross-agent team collaboration toolkit. Agents (Claude Code and Codex) read this file to guide setup and operation.

## 첫 접촉 (First contact): "셋업해줘" / "set this up" / "온보딩" / "onboarding"

When the user asks for tm-mode setup or onboarding, **classify the input form first**.

### Input-form classification — did it arrive as a URL, or are you already inside a repo?

- If the request comes with the **tm-mode product URL** (`github.com/T-Gates/tm-mode`), such as "세팅해줘" / "set it up", and the current folder is not a team repo → follow the product README's **"For AI agents: setup instructions"** procedure (ask once: new team vs joining → secure the repo with clone / gh template → show the `python3 infra/install.py --root . --dry-run --yes` plan → after approval, run the same install with only `--dry-run` removed). ⚠️ The `curl | sh` one-liner is **for humans in a terminal** — agents must not use it (non-interactive execution installs without the plan and approval gate; the README carries the same warning).
- If the request comes with a **team repo URL** (a tm-mode-derived team) → run `git clone <url> ~/teammode/<repo>` (if it already exists, confirm with the user), then in that folder perform the "installation-state detection → bootstrap" flow below exactly. If clone fails (permission/URL), report it as-is and stop — the bootstrap dry-run approval gate is the only write gate.
- If there is no URL and the request is made **inside a team repo** (already cloned) → proceed directly to the detection below.

Then **detect the installation state**.

### Installation-State Detection (for routing — conservative)

Minimum signal set for treating installation as complete: 1. `team.config.json` exists; 2. `memory/team/members.md` exists; 3. `team.config.json` parses as **JSON** and top-level `agents` is a **non-empty list of strings** (parse failure or type mismatch = ambiguous). If all three are present → route to the **`tm-onboard` skill** (`infra/skills/base/tm-onboard/SKILL.md`) — it only verifies and explains value.

- `.teammode-active` is only an **activation** marker and is not used for installation-state detection (install does not turn `on`).
- Detection is for routing only — completeness verification belongs to the tm-onboard verification subagent. Misclassification is safe: if you wrongly think it is installed, tm-onboard verification reveals missing pieces and re-routes to bootstrap; if you wrongly think it is not installed, `install.py` is idempotent, so rerunning is harmless.
- If any signal is missing or ambiguous → use **bootstrap** below.

### Pre-Install Bootstrap (clone-and-go — cloned repo = immediately usable)

Because the engine (`infra/`) already exists inside the repo, setup can finish here without the CLI. **Consent to write host settings is obtained through conversational approval** — the point of `--yes` is "the person's explicit intent" (product decision, 2026-07-04).

1. **Print the plan only**: `python3 infra/install.py --root . --dry-run --yes`
   — include `--yes` **together with** dry-run: dry-run takes precedence, so nothing is written, while the plan renders on a **real-install basis** (including env injection and autopush). A plan printed without `--yes` is a "non-install (no injection)" plan and is not the same thing the user is approving.
2. If the output contains a `member_name=(unset)` blocker, ask for the member name **exactly once**, then rerun step 1 with `--member-name <name>`. (The no-repeat-question rule applies when the wizard already asked; bootstrap has no wizard.)
3. **Show the entire dry-run output to the user and obtain explicit approval.** The plan includes repo writes, real host file paths, hooks to wire, env, scaffold auto commit/push attempts, and Codex Trust. Before approval, **must not write** real host settings, skill directories, shell env, or Obsidian.
4. When the user approves, run the real install by removing **only `--dry-run` from the arguments in step 1**: `python3 infra/install.py --root . --yes [--member-name <name>]`
   — the approved plan and execution have the same arguments and the same contract. This includes repo scaffold creation/update, detected Claude/Codex wiring, and scaffold auto commit/push attempts.
5. If Codex was wired, tell the user they may need to **open the TUI once and press Trust** (do not inject trusted hashes directly — this is the person's decision).
6. On success, continue to **`tm-onboard`** (verification + value). On failure (exit != 0), relay the exit code and message to the person and stop — no speculative repair.

> **Keep the CLI path in parallel**: for creating a new team repo from scratch, use `tm-mode init`; to let the CLI handle cloning too, use `tm-mode join <clone-url>` — the wizard handles the dialogue, and when it finishes the agent runs `tm-onboard`.

**The `tm-onboard` skill does exactly two things:**

1. **Install verification** — delegate to a verification-only subagent (the main agent does not wait).
2. **tm-mode value delivery** — read `infra/skills/base/tm-onboard/value.md` and deliver it to the person.

The skill does not call `install.py` directly and does not ask for member name, org, team name, role, agent, or Obsidian (the first and only member-name question belongs to the bootstrap **procedure** above, not to the skill).

## Service Connection: "연결해줘" / "connect it" / "서비스 붙여줘" / "attach a service"
To attach a service to a role slot (issues / chat / docs / calendar), follow the **`tm-connect` skill** (`infra/skills/core/tm-connect/SKILL.md`). tm-onboard does not handle L2 service connection — when the moment requires it, the `tm-connect` skill appears through its trigger (progressive). The actual connection (token guidance, vault storage, config slot recording, rewiring) is handled by tm-connect.

- Read issuance links, steps, and connection methods **as data** from `providers/<provider>.json` fields `token_guide`, `auth`, `default_scope`, and `resource_fields`; do not hardcode them.
- **Individual entry (v0.1)**: each member enters their own token directly → local vault (`infra/credentials.py`, 0600). No automatic team sharing.
- ⚠️ The vault is plaintext, so **sync folders (Syncthing/Dropbox/etc.) are forbidden.**

## Safety (Required)
- The team root must be provided **only through explicit `--root`**. Do not trust environment variables such as `TEAMMODE_HOME`.
- Do not touch real agent settings (`~/.claude/settings.json`), shell profiles, or `obsidian.json` without **`--yes` (real install) or `--settings` (isolated) / `--register-obsidian` consent**.
- If blocked (exit != 0), relay the reason to the person and stop. No speculative repair.
- **Public hygiene**: all committed content in this repo (fixtures, docs, comments) must not contain real names, real paths, or real team identifiers — the allowed vocabulary (alice/bob/acme, etc.) is sourced from the "Public hygiene" section of CONTRIBUTING.md. The CI guard (`tests/test_no_identity_leaks.py`) blocks violations.
- **Windows PowerShell**: git diagnostic messages (clone progress, remote info, etc.) are emitted on stderr and appear red, but they are non-fatal — ignore them unless they are actual errors.

## Operation (in a Set-Up Team Repo)
- Team work context is recorded as **session logs**. Do not create files directly; let the engine (`teammode.py log`) / hooks record them (date, frontmatter, and 06:00 cutoff are automatic).
- At session start, the `session-start.py` hook automatically injects recent per-member session logs — use that to understand team status.
- Pushes and PRs are the person's decision. Agents do not push arbitrarily.

## Key Files
- **Entry CLI**: `src/teammode/cli.py` (`tm-mode init` / `tm-mode join` — the wizard owns installation)
- Engine: `infra/teammode.py` (verbs: on/off/log/context/pull/commit/update)
- Setup: `infra/install.py` (+ `install_lib.py`) — called by the CLI through subprocess delegation
- Hooks: `infra/hooks/` · adapters: `infra/agents/<name>/` · skills: `infra/skills/`
- If behavior differs from expectations, check the design specs ([installation and bootstrap](docs/spec/onboarding.md) · [onboarding skill](docs/spec/skills.md)).
