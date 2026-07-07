---
name: tm-connect
description: Use to connect a service slot (issues / chat / docs / calendar) to a tm-mode team — guiding token issuance, storing it in the local credentials vault, and recording the resource in team config. Triggers on "서비스 연결", "이슈 트래커 연결", "채팅 연결", "문서 연결", "캘린더 연결", "팀모드 서비스 붙여줘", "tm-mode connect", "connect service", "connect issues tracker", "connect chat", "connect docs", "connect calendar", "connect team service", or after tm-onboard offers L2.
---

# tm-connect — Service Slot Connection (L2 Registrar)

This skill is the registrar that attaches the team's chosen **official vendor MCP** to a tm-mode **role slot** (issues / chat / docs / calendar). When tm-onboard offers "연결할래요?" after the first value moment (L1), **this skill** performs the work.

```
tm-onboard (offer+trigger)  →  tm-connect (run registrar)
   "연결할래요?"              slot → provider → prepare MCP → token guidance → vault storage → config write → MCP alias registration
```

> **A안 / Option A (decided 2026-06-25).** tm-mode only handles **connection (registration)**. For actions such as creating issues or adding calendar events, **the AI directly calls the registered vendor MCP tools**. This skill does not wrap those actions. Handler generation, the `role_server` proxy, role-abstraction verbs, and the "reuse > absorb > hand-build" priority rule were all discarded. The authoritative specs are `docs/spec/skills.md §5.4.1` and `internals.md §2.8`.

## Principles

- **Data and the engine handle what can be automated; this skill handles judgment and consent gates.** Token issuance links, steps, and connection methods are read from **`providers/<provider>.json`** when guiding the user. Do not hardcode links or steps in the skill body.
- **Use role vocabulary only.** This skill speaks only in the roles issues / chat / docs / calendar. The actual service is answered by `team.config.json` at `services.<역할>.provider` and by `providers/<provider>.json`; read the runtime data and relay it.
- **Each person enters their own token (v0.2).** Each member **directly enters their own token**. Automatic team token sharing does not exist in v0.2. Even team-scope slots require each member to enter a token "once per person."
- **Honest boundary (human-owned).** Token issuance, consent (OAuth "Allow", private key "Create+paste", bot installation), and **identifying the official provider** are security boundaries where a human grants or confirms authority. This cannot be unattended. The skill takes the user up to the consent gate; the human clicks, pastes, and chooses.
- **Connection only; actions are done directly by the AI.** After registration, actions are performed by the AI directly calling the registered vendor MCP tools. This skill does not create action commands such as `tm-issues create`; that would revive the discarded abstraction (Option B).

---

## 0. Tell the User First — Plain-Language Procedure

When starting a connection, **do not jump straight into the steps. First explain the whole procedure once in plain language**. The user does not know tm-mode internal terms such as slot, provider, MCP, or alias. Avoid those words and explain what they mean.

All user-facing responses in this flow must respond in the user's language. The examples below are English illustrations; localize them naturally instead of hardcoding English wording.

**Opening guidance example** (for a calendar connection):

> "I'll connect the team's **calendar** now. The order is:
> 1. Choose which calendar service the team will use (the provider comes from the provider pack or the team's config).
> 2. Create and paste one service token, which works like a key.
> 3. That's it. After that, I can add and look up calendar events directly for you.
>
> The only thing you need to do yourself is **create and paste the token once**. I'll handle the rest."

**Plain-language term mapping** (do not use the left-hand terms with the user; use the right-hand phrasing):

| Internal term | Say to the user |
|---|---|
| slot / role | "issues / chat / docs / calendar" (as-is) |
| provider | "which service" (the provider chosen from config or the provider pack) |
| MCP / alias registration | "connect it so the AI can use that service" |
| credentials vault | "store the token safely" |
| instance value / resource_fields | "which calendar, document, or channel" |

When moving to each step, also add **one simple line saying what is happening and why it is needed**. For example, instead of "Go to the token issuance page...", say something like "The AI needs one key to work with your calendar on your behalf. Please create it once at this link." For a follow-up member whose team already chose the service, keep it shorter: "The team already chose the calendar service. You only need to enter your token once."

---

## 1. Select the Slot — Which Role to Connect

Determine the role the user wants to attach (issues / chat / docs / calendar). Every later step proceeds for that one slot only.

---

## 2. Decide the Provider — Config Is Truth, Only the First Registrar Chooses

Read the slot provider from `team.config.json` at `services.<역할>.provider` (**do not guess; data is truth**).

### 2-A. Provider Already Exists (Follow-Up Member)

If `services.<역할>.provider` is populated, **do not reselect it**. Follow the provider that the introducer already chose, committed, and pushed in config. Proceed directly to §3 MCP preparation (it should already be in this repository) and §4 token entry.

> "The team has already declared `<provider>` for this slot. I will not reselect it and will continue with that provider.
> To change the provider, edit `team.config.json` at `services.<역할>` directly and get team agreement through a PR."

### 2-B. Slot Is Empty (First Registrar)

Ask the human which provider to use. If needed, search and present N candidates.

1. **The human chooses**. **Identifying the official provider is a security gate**. Do not proceed from unattended guesswork; a fake non-official MCP or endpoint can leak tokens.
2. Check that a `providers/<provider>.json` pack exists. If it does not, it is unsupported; do not continue by guessing.
3. **Prepare the official vendor MCP** (§3).
4. Record the chosen provider and instance values in `team.config.json` at `services.<역할>`, then **push to GitHub**. This is the team-shared declaration (§5).

After the provider is chosen, read **the following fields as data** from that pack to build the guidance:

| Pack field | Purpose (read by the skill for guidance) |
|---|---|
| `token_guide.url` | **Deep link** to the token issuance page; present it to the human as-is |
| `token_guide.steps` | **Ordered step list** for issuance; guide the user in order |
| `auth` | Connection method (`api_key` / `oauth` / `bot_token`); choose wording according to this value (§4) |
| `default_scope` | `team` / `personal`; credentials namespace and default (§4) |
| `resource_fields` | Names of **instance fields** to fill in config after connection (§5) |
| `mcp.register_hint` | Reference for MCP alias registration guidance (§3, §6) |

---

## 3. Prepare the Official Vendor MCP — Official First, Custom If None Exists

On the first-registrar path, place that provider's **official vendor MCP** in this repository. The actual alias registration is done later by §6 install-mcp (rerunning install.py); this step prepares the **MCP code and execution metadata** that registration will point to.

```
Official MCP repo exists → fetch it with git and place it in infra/mcp/<provider>/ in this repo + commit (team-shared storage)
No official MCP         → AI builds a custom one (do not push this onto the user; the "no custom MCP" rule was discarded)
```

**Official first.** Custom MCP is only the fallback when no official MCP repository exists. Later members reuse what is already in this repository (do not prepare it again).

### Custom MCP (Only When No Official One Exists)

1. Use the provider's official API spec (REST/GraphQL docs) as the source and write **a vendor-specific MCP** with the Python MCP SDK.
2. Expose **only the tools needed for that slot role**. For example, a calendar slot may expose list_events / create_event level tools; do not build an all-purpose server covering every slot.
3. Put server code plus the execution command (startup metadata) under `infra/mcp/<provider>/` and commit it to this repository. Use the same location and structure as when importing an official MCP.
4. Tokens use the same path as official MCPs (§4 local vault 0600). Do not create a separate token path just because it is custom.
5. Immediately after the first custom build, run an **adversarial review (subagent)** to verify the exposed tools actually work.

> ⚠️ **A custom MCP is not role abstraction.** It only exposes tools wrapping the provider API *as-is*. It does **not** create unified slot verbs (role-specific abstraction verbs). That would revive the discarded `role_server` / role abstraction (Option B). Even for custom MCPs, tm-mode only handles **connection (registration)**, and actions are called directly by the AI (Option A).

See `docs/spec/internals.md §2.8` for the detailed seven steps and principles.

---

## 4. Token Guidance → Individual Entry → Credentials Vault Storage

### 4-A. Token Guidance (Human-Owned, Up to the Consent Gate)

Take the user to the pack's `token_guide.url` and relay `token_guide.steps` in order. Do not vaguely say "find the key"; use **exact links and buttons**.

- The human creates the token on the issuance page. This is a security boundary where the human grants authority; it cannot be unattended.
- Set expectations with "your part is only N tokens" to reduce the token bottleneck.

Read the pack's `auth` value and choose the guidance accordingly. The skill does not know the service; it branches only on the value:

| `auth` | Guidance |
|---|---|
| `api_key` | On the issuance page, **Create → copy → paste** a personal/integration key. Each member issues their own so attribution remains theirs. |
| `bot_token` | Issue an app/bot token, install it into the workspace, then **copy → paste the bot token**. |
| `oauth` | **localhost OAuth(PKCE)**; the human clicks "Allow" on the consent screen. Tokens are received through the callback, so pasting may not be needed. |

OAuth credential key contract:

| auth type | storage key |
|-----------|---------|
| `api_key` / `bot_token` | `<역할>` |
| `oauth` | `<역할>_access_token` + `<역할>_refresh_token` |

### 4-B. Individual Entry → Store in the Credentials Vault

**No automatic team sharing (v0.2).** Each member directly enters their own token, and it is stored in the **local vault** (`infra/credentials.py`).

- Storage location: member-local `$XDG_DATA_HOME/teammode/credentials/default.json` (single vault, file mode 0600). It is not tracked by git.
- Choose the namespace from the pack's `default_scope`. Whether it is `team` or `personal`, **v0.2 requires one entry per person**. A team scope does not mean the introducer enters it once for everyone; automatic sharing is not implemented.
- Storage is done by the engine/module. The skill must never print plaintext tokens to stdout, logs, or session logs:
  ```bash
  python3 -c "import sys; sys.path.insert(0,'infra'); import credentials; \
    credentials.store('<team>', '<scope>', '<역할>', input())"
  ```
  Tokens flow only through standard input and must never remain in command-line arguments, logs, or session logs.

> ⚠️ **Plaintext vault warning (required guidance).** The v0.2 vault is **plaintext JSON**. Never place it in a synchronized folder such as Syncthing, Dropbox, or iCloud. If plaintext tokens sync to other devices or services, they are effectively leaked. File mode 0600 + not tracked by git + no sync folder is the v0.2 defense line. OS keychain support comes later.

---

## 5. Record the Config Slot — Instance Values (resource_fields)

After receiving the token, decide **which actual resource** in that service to use (document DB, chat channel, calendar, etc.). The pack's `resource_fields` declares the **instance field names** to fill in config, and **this list determines whether instance values are needed**. If it is an empty list, no instance value is needed (for example, a chat slot may use all channels and have no dbid-like value). If it is non-empty, ask the human only for the values relevant to that slot (for example, the dbid for docs/calendar slots).

- Record `{ provider, scope, <resource_fields each field = chosen value> }` in the `services.<역할>` slot of `team.config.json`.
- **Instance values (resource IDs, channels, calendars, etc.) belong in config**. Tokens (secrets) go in the vault; instance values (not secrets) go in config. Do not write tokens into config; the token-key anti-tracking lint will block it.
- On the first-registrar path, the introducer **commits and pushes** the provider and instance values in config (team-shared declaration, §2-B). Team members only read that declaration. Tokens are still entered by each person in v0.2 (§4).

---

## 6. MCP Alias Registration (Rerun install-mcp) → First Value

### 6-A. MCP Alias Registration (Rewiring)

After finishing the connection, **rerun install-mcp** so the adapter registers that provider's **vendor MCP alias in the member agent (claude/codex) settings**. install-mcp handles the official/custom MCP artifact prepared in §3 **the same way**, registering it under the canonical server-name alias (`internals.md §2.8`). Relay the pack's `mcp.register_hint` as reference.

```bash
python3 infra/install.py --root . --yes        # rewire (register vendor MCP alias in adapter + sync)
```

After rewiring, verify:
- The provider's vendor MCP alias is registered in the agent settings as a teammode-managed entry.
- That vendor MCP's tools are exposed to the agent.

### 6-B. First Value (A안 / Option A — AI Directly Calls Vendor MCP Tools)

tm-connect does not wrap actions. After connection is complete, **the AI directly calls the registered vendor MCP tools** to demonstrate first value.

- For example, for an issues slot, the AI calls that provider MCP's issue creation tool; for a calendar slot, it calls the event creation tool.
- If the tool call is visible, **first dogfooding** can happen immediately: an actual issue/event appears in the service.
- Do not create new action commands such as `tm-issues create`; that would revive the discarded abstraction (Option B).

---

## Non-Goals / Boundaries

- Do not perform token issuance or consent clicks on the user's behalf; that is a security boundary owned by the human.
- Do not confirm the official provider through unattended guesswork; the human chooses it (security gate).
- Do not print plaintext tokens anywhere: stdout, logs, session logs, or config.
- Do not directly execute or wrap actions such as creating issues or adding events; actions are performed by the AI directly calling the registered vendor MCP tools.
- Do not create action CLIs/wrappers such as `tm-issues create`; that would revive Option B (role abstraction), which was discarded.
- Do not reselect the provider on the follow-up-member path; read the config declaration. Replacement requires direct editing plus team agreement through a PR.
- Validation and self-repair belong to `doctor` (roadmap). Immediately after connection, do no more than a basic validity ping.
- On the first-registrar path, **declaring the provider in config and committing/pushing the imported official MCP** are normal registrar-flow steps (team-shared declaration). Do not make arbitrary commits or PRs to the user's code repository.

## Common Mistakes

| Mistake | Correct approach |
|------|------------|
| Hardcoding issuance links or steps in the skill body | Read `token_guide` from `providers/<provider>.json` and guide from that data |
| Reselecting the provider as a follow-up member | Read config at `services.<역할>.provider` and continue with it |
| Guessing the official provider and continuing unattended | The human chooses; official identification is a security gate |
| Building custom first when an official MCP exists | Official first; custom is only the fallback when no official repo exists |
| Exposing unified role verbs in a custom MCP | Expose vendor API tools as-is; role abstraction was discarded (Option B) |
| Creating an action CLI such as `tm-issues create` | Actions are direct AI calls to vendor MCP tools; wrappers are forbidden (Option A) |
| Saying team scope means the introducer enters the token once | v0.2 requires individual entry; even team scope means each member enters their own token |
| Recording tokens in config or session logs | Tokens go in the vault (0600); only instance values go in config |
| Saying the vault may be placed in a sync folder | It is plaintext, so sync folders are forbidden; 0600 + not tracked by git is the defense line |
| Treating an empty slot as an error | Empty slots are first-class. The first registrar chooses a provider, fills it, and rewiring activates it |
| Naming concrete tools or services directly | Speak only in role vocabulary (issues/chat/docs/calendar) |

---
> Discovery: This skill is found through pointers in AGENTS.md / CLAUDE.md (same as tm-onboard). For action and data grounding, check `providers/<name>.json` (connection data), `infra/credentials.py` (vault), `docs/spec/skills.md §5.4.1` (registrar flow and Option A baseline), and `internals.md §2.8` (install-mcp requirement).
</content>
</invoke>
