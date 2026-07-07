# Skill System

tm-mode SPEC v0.3 — skill layers (base/core) + legacy migration roadmap + onboarding skill spec (§5)

## Skill Layers (base / core)

| Layer | Install/activation timing | Skills |
|---|---|---|
| **base** | Always during setup (install) | `tm-onboard` · `tm` (on/off toggle) |
| **core** | Active when team mode is `on` | `tm-connect` · `tm-context` · `tm-customize` · `tm-memory` · `tm-manage-memory` |

- **base** = the minimal skills for turning team mode on/off and setting it up. Always installed.
- **core** = active only when team mode is on (context and memory operations). Inactive when `off`.
- The **util** layer (optional install) is currently empty. Items such as `dev-cycle` are deferred — they are general development meta-work and do not align with the tm-mode core identity (context sharing and service connection).

## Migration Roadmap (Legacy Toolkit → tm-mode)

Migrate proven legacy toolkit skills into general-purpose tm-mode. Legacy-specific dependencies (dedicated HOME env, hardcoded channel/DB IDs) are translated into tm-mode generality (explicit `--root`, `team.config.json` services slots).

### This Migration — 4 L1 Skills

| Skill | Source (legacy) | L1 core (works immediately) | L2 graceful (added when connected) | Prerequisite |
|---|---|---|---|---|
| **tm** (on/off) | `legacy` | Wrapper around engine `on`/`off` verbs + context injection + session log (`log`/`commit`) | — | Lightweight sync |
| **tm-memory** | `load-knowledge` | Load `memory/` INDEX hierarchy (read-only) | — | INDEX structure policy |
| **tm-context** | `get-context` | Session log and decisions summary | Linear In Progress · Calendar | decisions manifest |
| **tm-manage-memory** | `manage-knowledge` | File CRUD, INDEX update, `commit` | Slack notification | INDEX auto-update (edit date) |

- **Works immediately as L1 core**, and L2 services (Linear/Calendar/Slack) are gracefully added only when connected (quietly skipped if not connected).
- Migration order: `tm-memory` (easiest) → `tm` → `tm-context` → `tm-manage-memory`.

### L2 Follow-ups (After Provider Connection — Reserved Slots)

| Skill | Source | Dependency |
|---|---|---|
| `tm-meeting` | `create-meeting` | **Notion(docs) storage is essential → not L1** |
| `tm-tasks` · `tm-task` | get/set/create-tasks · start/end-task | Linear(issues) provider |
| `tm-schedule` | `schedule` | Calendar provider |

### Prerequisite Infrastructure (Needed Before Migration)

1. **Lightweight sync** — update only hooks when `tm` is turned on (currently only the whole `install.py`, no partial execution mode).
2. **session-start hook** — automatic context injection (automated version of `tm-context`).
3. **decisions manifest** — `memory/team/decisions/current.md` (used by `tm-context` and `tm-meeting`, currently undecided).
4. **member emoji** — `team.config.json` or `members.md` (optional).

### Excluded (Not Migrated)

| Skill | Reason |
|---|---|
| `legacy-onboard` · `credentials` | tm-mode already has `tm-onboard` and vault (duplicate) |
| `3d-modeling` · `acme-browse` | Specific to one team (examples) |
| `check-health` · `lint` · `cheer` | Product/toolkit-specific · low priority |
| `dev-cycle` | General development meta-work — does not fit the core identity (deferred) |

---

tm-mode SPEC v0.3 — tm-onboard · tm-connect (§5)

## §5. Onboarding Skill (tm-onboard)

> The ground truth for this section is the current working tree's `infra/skills/base/tm-onboard/SKILL.md`, `infra/skills/core/tm-connect/SKILL.md`, and `src/teammode/cli.py`. As of 2026-06-16, the current working tree has uncommitted changes related to `install-skills` (`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py`, etc.), and this section reflects the **currently implemented skill bodies** regardless of commit state.
>
> **Core contract change (2026-06)**: Installation is completed by the CLI (`tm-mode init` / `tm-mode join`). After install, the skill only does ① verification (delegated to a subagent) and ② value delivery (value.md). Having the skill directly call `install.py` or ask for member name, org, team name, or role is the **old contract and has been discarded.**

### 5.1 Identity and Triggers

```yaml
name: tm-onboard
description: Use right after a tm-mode install (`tm-mode init` / `tm-mode join`) — when
  entering Claude Code/Codex in a freshly set-up team repo. Dispatches a verification
  subagent to confirm the install landed, and meanwhile conveys what tm-mode does for you.
triggers:
  - "tm-onboard"
  - "팀모드 온보딩"
  - "팀모드 시작"
  - "설치 잘 됐나"
  - "팀모드 셋업 확인"
  - when the CLI tells the user to open an agent and run tm-onboard
```

`tm-onboard` is the skill to run **immediately after `tm-mode init` / `tm-mode join` installation**, when first entering the agent. Installation, repo creation, and clone have already been completed by the CLI wizard. The skill does **exactly two things**: ① install verification (delegated to a verification subagent; the main agent does not wait), and ② team-mode value delivery (read `value.md` and relay it to the person).

Related skill covered in the same section:

```yaml
name: tm-connect
description: Connect a service slot (issues / chat / docs / calendar) to a tm-mode team.
triggers:
  - "서비스 연결"
  - "이슈 트래커 연결"
  - "채팅 연결"
  - "문서 연결"
  - "캘린더 연결"
  - "팀모드 서비스 붙여줘"
  - "tm-mode connect"
  - "connect service"
  - after tm-onboard offers L2
```

`tm-connect` performs the L2 connection that `tm-onboard` suggests right after first value delivery. Token guidance, local vault storage, config slot recording, and rewiring are the responsibility of `tm-connect`.

### 5.2 CLI ↔ tm-onboard ↔ install.py Division of Labor

| Step | Owner |
|---|---|
| Repo creation (`gh repo create --template`) | `cli.py` `cmd_init` |
| Team repo clone | `cli.py` `cmd_join` (runs after wizard step 2) |
| Member name, org, team name, role, agent, Obsidian dialogue | `cli.py` `_wizard_join` (TTY) / argument path (non-TTY) |
| preflight, detect, role classification, scaffold, wire, env, verify | `install.py`. The CLI delegates by subprocess. |
| Install completion guidance ("open an agent and enter tm-onboard") | `cli.py` `_done()` |
| Install verification (subagent delegation) | `tm-onboard`. Main agent does not wait and delivers value in parallel. |
| Team-mode value delivery (read `value.md` and relay to person) | `tm-onboard`. Main agent proceeds while verification subagent runs. |
| personality customization opt-in | `tm-customize` skill (outside tm-onboard scope — progressive). |
| Obsidian registration opt-in | Already asked in CLI wizard step 5, or direct `install.py --register-obsidian`. |
| L2 service connection suggestion | `tm-onboard` does not handle this — each skill (`tm-connect`) appears when needed. |
| L2 service connection execution | `tm-connect`. Provider data guidance, credentials storage, config slot recording, rewiring. |
| Host install rollback | Run `install.py --uninstall` directly. Destructive, so confirm with the person first. |

**What the skill does not do (discarded old contract):**
- Directly call `install.py` — the CLI has completed it. If reinstall is needed, guide the user to rerun `tm-mode join <url>` (idempotent).
- Ask for member name, org, team name, or role — the CLI wizard already collected them.
- Explain introducer/member classification — the CLI wizard handled it.
- Start installation for someone who is not installed → the skill does not install. If inside a repo, route to the AGENTS.md first-contact bootstrap (0.3 clone-and-go — installation belongs to that procedure); if outside a repo, provide `tm-mode init` / `tm-mode join <url>` CLI guidance and stop.

### 5.3 Flow (First Entry After Install — Parallel)

> **Precondition**: `tm-mode init` or `tm-mode join <url>` has already completed. The agent runs from the cloned team repo root.

```
"tm-onboard" (또는 "팀모드 시작" / "설치 잘 됐나")
 1. 검증 서브에이전트를 즉시 디스패치한다 — 읽기 전용·수정 금지. 메인은 기다리지 않는다.
 2. (서브가 도는 동안) infra/skills/base/tm-onboard/value.md 를 읽고 가치를 사람에게 전달한다.
 3. 검증 결과 도착 → 종합:
    - 전부 ✅ → "설치도 정상 확인됐어요" 한 줄 매듭.
    - ❌ 항목 있음 → 무엇이 안 됐는지 짚고 → `tm-mode join <팀레포 URL>` 재실행 안내(멱등).
 4. 마무리: "작업 시작할 땐 `tm on` 하세요." 한 걸음 안내로 끝낸다.
```

Verification subagent checklist (based on SKILL.md §①):
1. `python3 infra/teammode.py context --root <팀루트> --json` — outputs state without error (`state=off` is normal — install ≠ activation)
2. `memory/team/members.md` member registration, `memory/INDEX.md` exists
3. `team.config.json` exists + records `agents`
4. Skill symlinks (claude=`~/.claude/skills`, codex relevant path)
5. Hook wiring (`~/.claude/settings.json`, etc.)

What install.py does internally (reference — the skill does not reproduce it):

- preflight, detect, automatic role classification
- scaffold: `memory/INDEX.md`, `memory/team/members.md`, `memory/team/sessions/<이름>/`, and empty services config for introducer
- hook sync and real settings write (when `--yes`)
- env injection
- verify: confirm install with `context` (`on` not used — active marker/settings not created, install ≠ activation)

### 5.4 Service Connection Skill (tm-connect)

#### 5.4.1 tm-connect — Register Vendor MCP in Role Slot

> **Option A (confirmed 2026-06-25).** `tm-connect` **connects (registers) the team's chosen official vendor MCP** to a role slot. tm-mode only connects it; **actions** such as issue creation or calendar event creation are performed by the AI directly calling the registered `mcp__<alias>__<벤더도구>`. tm-connect does not wrap actions or create handlers. Handler abstraction, `role_server`, and the "reuse > absorb > handcraft" priority decision were discarded (`docs/archive/2026-06-25-L2-redesign.md`).

`tm-connect` connects one of the role slots `issues`, `chat`, `docs`, `calendar`. It does not hardcode product names and speaks in **role vocabulary**. The actual product is determined by `services.<역할>.provider` in `team.config.json` and `providers/<provider>.json`.

provider selection (registrar flow):

- The user decides which role to connect.
- **If `team.config.json` already has `services.<역할>.provider`** (follow-up member), read that provider. Do not reselect. Follow the provider pack and registration target the introducer already chose, committed, and pushed in config.
- **If the slot is empty** (first registrar), ask the person which provider to use and, if needed, search and present candidates. The person chooses — **identifying the official provider is a security gate**, so do not proceed by unattended guessing.
  - Prepare that provider's **official vendor MCP**: if an official MCP repo exists, bring it into this repo (`infra/mcp/<provider>/`, etc.) and commit it (team-shared storage); if no official one exists, the AI writes one itself (do not push this burden to the user).
    - When handcrafting: use the provider's official API spec to write a Python MCP SDK server exposing **only the tools needed for that slot**, and commit code+execution metadata under `infra/mcp/<provider>/` (next members reuse it). A handcrafted MCP is still only that vendor-specific MCP and does not create role-unified verbs (preserve Option A). Registration is handled by install-mcp the same way as official MCPs. See "MCP preparation" in `docs/archive/2026-06-25-L2-redesign.md` and `internals.md` §2.8 for the seven steps and detailed principles.
  - Record the chosen provider and instance values in `team.config.json` and **push to GitHub** as the team-shared declaration.
- If the `providers/<provider>.json` pack does not actually exist, it is unsupported. Do not proceed by guessing.

Fields read from the provider pack:

| Field | Purpose |
|---|---|
| `token_guide.url` | Deep link for the token issuance page. Present as-is. |
| `token_guide.steps` | List of issuance steps. Guide in order. |
| `auth` | Connection method. Choose wording according to `api_key`, `oauth`, or `bot_token`. |
| `default_scope` | `team` or `personal`. Default for credentials namespace and guidance. |
| `resource_fields` | List of instance field names to fill in the `team.config.json` slot after connection. |
| `mcp.register_hint` | Reference for install-mcp rewiring guidance. |

Token issuance guidance:

- Read `token_guide.url` and `token_guide.steps` as data and guide from them. Do not hardcode links, buttons, or steps in the skill body.
- Do not vaguely say "go find the key."
- Token issuance, OAuth consent, bot installation, and sharing toggles are done directly by the person. This is a security boundary and is not handled unattended.
- The skill fixes expectations as "your part is only N tokens" to reduce token bottlenecks.

`auth` branching:

| `auth` | Guidance |
|---|---|
| `api_key` | Create a personal/integration key → copy → paste. Each member issues their own so attribution stays with them. |
| `bot_token` | Issue an app/bot token, install it in the workspace, then copy and paste the bot token. |
| `oauth` | localhost OAuth (PKCE). When the person allows the consent screen, receive the token through callback. Pasting may not be needed. |

credentials storage:

- In 0.2, there is no automatic team token sharing.
- Each member enters their own token directly. Even when `default_scope` is `team`, it is not completed by the introducer once.
- Storage location is local `$XDG_DATA_HOME/teammode/credentials/default.json` (single vault — multi-team unsupported, 2026-06-21. Not tied to team name, so safe across renames).
- File permission is 0600.
- It is not tracked by git.
- Storage is performed by `infra/credentials.py`. The skill does not print or record plaintext tokens to stdout, logs, session logs, or config.

Current storage call presented by the skill:

```bash
python3 -c "import sys; sys.path.insert(0,'infra'); import credentials; \
  credentials.store('<team>', '<scope>', '<역할>', input())"
```

- Tokens enter only through stdin.
- Do not put tokens in command-line arguments.
- Do not write tokens to session logs.
- The 0.2 vault is plaintext JSON. Always warn not to put it in synced folders such as Syncthing, Dropbox, or iCloud. 0600 permissions, no git tracking, and avoiding synced folders are the 0.2 defenses. OS keychain is future work.

config slot recording:

- After storing the token, choose the actual resource to use, for example document DB, chat channel, or calendar.
- `resource_fields` declares the instance field names to fill in config. If empty, no instance value is needed.
- Record `{ provider, scope, <resource_fields each field = chosen value> }` in the `services.<역할>` slot of `team.config.json`.
- Do not put tokens in config. Tokens belong in the credentials vault; non-secret instance values belong to config.
- When the introducer commits provider/instance values for a team-scope slot to config, members read that declaration. However, each member still enters their own token in 0.2.

Rewiring (MCP alias registration):

```bash
python3 infra/install.py --root . --yes
```

- After connection, rerun install so the adapter registers the new slot's vendor MCP alias in agent settings through `install-mcp` (§internals 2.8). Once an empty slot is filled, sync can activate the corresponding matcher.
- `mcp.register_hint` is used as a reference for this registration guidance.

First value (Option A — AI directly calls vendor MCP tools):

- tm-connect does not wrap actions. Once the connection is complete, **the AI directly calls the registered `mcp__<alias>__<벤더도구>`** to show first value (for example, if the issues slot is connected, directly call that provider MCP's issue creation tool).
- tm-connect does not create a new action command such as `tm-issues create`. That would revive the discarded abstraction (Option B).

What `tm-connect` does not do:

- It does not perform token issuance or consent clicks on behalf of the user.
- It does not leave plaintext tokens in stdout, logs, session logs, or config.
- It does not do doctor-level verification or self-repair. Scope is limited to a valid ping immediately after connection.
- It does not create issue bodies, execute actions (create issues, add calendar events, etc.), or automatically call other skills. Actions are performed by the AI directly calling registered vendor MCP tools.
- However, on the first-registrar path, **provider declaration (config) and commit/push of the fetched official MCP** are normal registrar-flow steps (team-shared declaration). It does not make arbitrary commits or PRs to the user's code repo.

#### 5.4.2 Host Rollback (Direct `install.py --uninstall`)

The tm-reset skill has been removed. Host install rollback is performed by running `install.py --uninstall` directly. Because it is destructive, always get human confirmation first, describe the rollback scope, then execute. For detailed behavior, see `docs/spec/internals.md §4.10(cmd_uninstall)`.

```bash
python3 infra/install.py --uninstall --root . --yes
```

- `install.py` handles off, Claude adapter hook uninstall, env line removal, and Obsidian deregistration. MCP registration removal and skills removal are not handled on this path.
- `--yes` is write intent for removal from real settings. Isolated test cleanup uses `--settings <settings-file-path>`.
- `memory/` is not deleted. Team data remains intact.
- The repo folder itself is not deleted. Full cleanup is a human-run `rm -rf <repo>`.
- Idempotent and nonfatal: no-op if already absent.

### 5.5 Boundaries / Single Responsibility

- `tm-onboard` directly handles only onboarding and the first L1 value. It only suggests L2 and passes execution to `tm-connect`.
- `tm-connect` performs only provider-data-based connection. It does not guess products, fields, or issuance procedures absent from provider packs.
- Neither skill manually reproduces what install.py/engine/credentials do.
- If failure occurs (exit != 0), report the reason and stop. Do not repair by guessing.
- Empty service slots are first-class citizens. L1 use before connection is normal.
- Pushes and PRs are decided by humans.

Common mistakes:

| Mistake | Correct approach |
|---|---|
| **tm-onboard directly calls install.py** | Installation was completed by the CLI. The skill only verifies and delivers value. |
| **tm-onboard asks again for member name, org, team name, or role** | The CLI wizard already collected them. Do not ask. |
| **The skill starts installation for "셋업해줘"** | The skill does not install — if inside a repo, route to AGENTS.md "first contact" bootstrap (dry-run→chat approval→--yes); otherwise provide `tm-mode init`/`join` terminal guidance and stop. |
| Main agent blocks synchronously on verification | Dispatch verification subagent + main agent delivers value meanwhile (parallel). |
| Skip verification and assume "it must be installed" | Have the subagent check real files/commands — especially hooks and skill symlinks. |
| Manually reproduce `install.py` steps | If it failed, guide the user to rerun `tm-mode join <url>` (idempotent). |
| List L2, Obsidian, personality as a menu | Do not handle them here. Each skill appears when needed (progressive). |
| Treat an empty team (0 session logs) as failure | Normal — narrate it as "starts accumulating from now." |
| Think `--member-name` distinguishes introducer/member | install.py automatically classifies role by config validity. (Internal reference for install.py) |
| Present `--yes` as simple consent only | `--yes` is intent to write/remove real-host settings. Isolation uses `--settings`. |
| tm-connect: service connection is executed directly by tm-onboard | tm-onboard does not handle it. The tm-connect skill appears when needed. |
| Hardcode issuance links or steps | Read `token_guide` and `auth` from `providers/<provider>.json`. |
| Tell users that team scope is completed once by the introducer | In 0.2, each member enters their own token. Team scope still requires each member to store their own token. |
| Record tokens in config or session logs | Tokens belong only in the local credentials vault. Config stores only instance values. |
| Say plaintext vaults are OK in synced folders | The 0.2 vault is plaintext JSON. Synced folders are forbidden. |
| Treat empty slots as errors | Empty slots are normal. The engine emits a nonfatal `[info]` guidance message. |
| Say uninstall deletes memory too | uninstall only rolls back host traces and preserves `memory/`. |

### 5.6 Memory Import Skill (tm-import-memory)

**Identity**: A core skill that scrapes pages from the document service connected to the docs slot, organizes them by topic, and stores them in team `memory/`. Role vocabulary principle — the actual service name is answered by `services.docs.provider`, and guidance/source labels read and use that name (do not hardcode product names; internals.md §7.3 role vocabulary principle). Supports both cold-start seed and rerun additions ("이 노션 페이지 추가해"). The skill makes judgments; engine `memory write` (§3.6) handles storage.

**Boundary**: External-document-origin / bulk / tree = `tm-import-memory` / conversation-origin single-item CRUD = `tm-manage-memory` / slot connection = `tm-connect` (if unconnected, guide and stop). `tm-onboard` only makes a one-line suggestion for empty teams.

**Flow summary**: ① check docs slot → ② inspect only page list/tree (not body; default cap 20 pages and depth 2) → ③ **preview confirmation gate** (source→storage location, weight proposal, rationale table, plus one-line route description (desc) for new top-level folders, and explicit "up to N files, one commit/push per file" — this single confirmation also bulk-approves weight and route desc) → ④ per-page fan-out subagents (only subagents read body; no direct file writes) → ⑤ main agent merges by topic (within ~10 files, no page:file 1:1) and calls `memory write` → ⑥ completion report (distinguish created/updated, INDEX, push state).

**Rules**:
- Default weight 📎; propose 📌 only for clear decisions/rules; **never auto-propose 🔥**. The weight convention ("do not guess") is satisfied by approval of the preview table.
- A bottom `## 출처` section is required in the body (marks external origin and includes provider name). File names are determined by the **approved storage location (topic)** — reruns for the same topic converge on the same file (idempotent).
- A new top-level folder must first be registered with `memory route upsert --path <폴더>/ --desc <승인된 설명> --author <멤버명>` (§3.6 dynamic allow rule — `--desc` must not be guessed and is approved in preview).
- Partial rerun: if the target topic file exists, read the existing file and save the **full body** with only the rerun source section updated (engine write is replace — passing content without merging loses other source content).
- Do not directly Edit/Write `memory/` — INDEX, frontmatter, backlinks, and commits are the engine's job.
- Source documents are read-only. Continuous automatic sync is out of scope.

---
