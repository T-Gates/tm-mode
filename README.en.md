[한국어](README.md) | **English**

# tm-mode

> Turn your team mode on. — A **cross-agent team collaboration toolkit** for AI coding agents (Claude Code · Codex).

**Everyone on the team works with their own AI, and nobody has to write up or ask "so what did you do today?"**
Each session, the agent automatically *reads* the team context and *records* what it did. The only thing a human does is `git push`.

## Getting started — two paths

**① clone-and-go (if a team repo already exists — no CLI install needed):**

```bash
git clone <team-repo-clone-url> && cd <team-repo>
# Open Claude Code / Codex and say: "set up this repo"
```

→ The agent shows an install plan (dry-run — everything it would write to your machine), waits for your approval in chat, then sets up. The engine ships inside the repo, so cloning it means you're ready to install.

**② CLI wizard (starting from team creation, or if you want it to clone for you):**

```bash
pip install "git+https://github.com/T-Gates/tm-mode"
tm-mode init                      # new team (introducer) — creates the repo, then sets up
tm-mode join <team-repo-clone-url> # join an existing team (member)
```

→ The CLI wizard asks for org, team name, your name, agents, and install location, then handles **repo creation/clone, hooks, skills, and env in one go.** (Same via curl — `... | sh -s -- init|join`. Once published to PyPI, `uv tool install tm-mode` / `pipx install tm-mode` will be the recommended route.)

Either way, once installed, run `tm-onboard` in your agent — verification and a value briefing are automatic.

**Requirements**: `python3` (3.9+) · `git` — that's it. (`gh` is optional, used only for automatic repo creation in `tm-mode init`.)

> Status: **v0.1 — L1 (team memory, automatic context injection, session logs, Obsidian view) works and is validated in daily use.** For L2 (service connections), some providers work today (linear, notion — those with MCP launch info); others (slack, google) are placeholders while the provider pack grows.

---

## Why tm-mode?

> **In one line:** the *writer and reader* of team memory shifts from humans to agents.
> With Slack, Notion, or a wiki, *humans write and humans read*. With tm-mode, **agents do both** → zero extra human labor.

That's the core, and it shows up as two pillars:

### Pillar ① Work flow — automatic recording & injection

> **Before** — daily "what are you working on?" standups, scrolling Slack, writing end-of-day recaps.
> **After** — open a session and the team state is already there; the day's work and decisions get recorded by the agent on their own.

At session start, a hook injects each member's recent session logs into the agent, and every session the agent records what it did into `memory/`. **Nobody tells a human to write things down** — the agent follows the reminders it receives.

### Pillar ② Team & product memory — pulled directly from memory

> **Before** — dig through Notion for product specs, domain rules, and past decisions, then copy-paste them into the agent.
> **After** — the agent **pulls team and product memory directly** from memory. No human ferrying context around.

Pile up product specs, team rules, decisions, and domain knowledge as markdown in `memory/`, and the agent searches and retrieves them when needed. **A single source of internal team memory that agents consume directly** — replacing the internal wiki or Notion.

### Why not Slack · Notion · meetings?

| | Slack · Notion · wiki | tm-mode |
|---|---|---|
| Who **writes** | humans (end-of-day write-ups) | **agents, automatically** |
| Who **reads** | humans (search & copy-paste) | **agents, automatically at session start** |
| Extra human labor | yes | **zero** |

### Supporting strengths

| Strength | One line |
|---|---|
| 📈 **Compounding · zero-day onboarding** | The more logs accumulate, the thicker the context; a new member starts day one with the full history — zero handover meetings. |
| 🤖 **Cross-agent · zero lock-in** | Team members can use different agents (Claude Code, Codex) and share the same memory. No forced tool standardization; switch agents and keep your context. |
| 🌿 **Git-native** | Markdown + git. Zero servers/infra, 100% data ownership; history, diffs, and backups come free. |

<details>
<summary>More strengths</summary>

| Strength | One line |
|---|---|
| 📝 **Personal asset** | Reasons behind decisions, blockers, and daily work remain on record — material for retrospectives, résumés, and blog posts. |
| 🔒 **Safety first** | Tokens stay in a local vault, real config writes are gated behind `--yes`, and pushing is a human decision. |
| 🧩 **No per-agent redefinition** | Put a skill once in `infra/skills/base/` and it deploys to both Claude and Codex. |
| 🎚️ **Skill management** | Define and share the team's skills in one place; install only the ones you want. |
| 🔏 **Log privacy** | Session logs are guided to record team work only, and the recording point is explicit every session. |

</details>

## What you get (L1)

| Feature | Description |
|---|---|
| **Team memory** | Session logs, decisions, and an INDEX as markdown in `memory/`, shared via git. |
| **Automatic context injection** | At session start, a hook (`session-start.py`) injects each member's recent session logs into the agent. |
| **Mechanical session logging** | `teammode.py log` handles dates, frontmatter, and the 6 AM cutoff automatically (agents can't get filenames wrong). |
| **Obsidian view** *(opt-in, zero keys)* | Open `memory/` as an Obsidian vault and see team memory as a graph. Auto-registration supported. |

## Team lifecycle

```
team setup (introducer, once)  →  personal setup (each member)  →  service connections (L2)
```

## Install

**If a team repo exists, clone-and-go (clone → "set up this repo"); to start from team creation, a single `tm-mode init`** (see [Getting started](#getting-started--two-paths) above). For requirements, **activation** (`tm on`), flags, and engine verbs, see **→ [INSTALL.md](INSTALL.md)** (Korean).

## Layout

```
infra/
├── teammode.py        # engine (verbs)
├── install.py         # bootstrap (setup)
├── install_lib.py     # bootstrap pure core
├── git_ops.py         # shared git ops
├── agents/<name>/     # per-agent adapters (claude · codex)
├── hooks/             # shared hooks (session-start · session-log-remind · auto_pull)
└── skills/            # skills (tm-onboard …)
memory/                # team memory (created at setup)
conformance/           # compatibility checks + golden scenarios
```

Spec: [docs/spec/](docs/spec/README.md) — the single authoritative SPEC v0.3 (Korean; docs are Korean-first). Contributor map: [ARCHITECTURE.md](ARCHITECTURE.md) (Korean).

## License

tm-mode is distributed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
