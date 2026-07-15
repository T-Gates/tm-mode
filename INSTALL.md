# Installation — tm-mode

This is the **single source of truth** for installing tm-mode. There are three entry paths: **agent URL one-liner** (give the product README to an agent and say "세팅해줘" / "set it up" — same approval-gated procedure as README's "For AI agents: setup instructions") · **clone-and-go** (clone the team repo → say "셋업해줘" / "set this up" in the agent) · **`tm-mode` CLI** (the wizard handles repo creation/clone, scaffold, hook wiring, skill deployment, and env injection).

## 0. clone-and-go — if a team repo already exists (no CLI install required)

```bash
git clone <team-repo clone-url> && cd <team-repo>
# Open Claude Code / Codex and say: "셋업해줘" / "set this up"
```

The agent follows the AGENTS.md "첫 접촉 (First contact)" procedure: show the `python3 infra/install.py --root . --dry-run --yes` plan (every file, hook, env change, and scaffold action that would be written on a real-install basis) → after **chat approval** → run the same command with only `--dry-run` removed → one-time Codex TUI Trust guidance → `tm-onboard` verification and briefing. Before approval, it writes nothing.

## Requirements
- **Python 3.9+**, **git**
- Creating a new team (`init`) also requires **GitHub CLI (`gh`)** in an authenticated state (`gh auth login`)

## 1. Install the launcher (pip or curl — choose one)
```bash
# pipx (recommended) or pip — live PyPI
pipx install tm-mode        # or: pip install tm-mode

# Or curl (without pip) — append one of the step-2 commands exactly as shown:
#   curl -fsSL https://raw.githubusercontent.com/T-Gates/tm-mode/refs/tags/v0.1.6/install.sh | sh -s -- <command>
```

## 2. Create or join a team

### New team — introducer
```bash
tm-mode init
```
The wizard asks for org/account, team name, and repo name → creates the repo from the template → immediately installs on your machine (clone + setup) in one flow. (For non-interactive use, pass `tm-mode init OWNER/REPO`.)

### Join an existing team — member
```bash
tm-mode join <team-repo clone-url>
```
The wizard asks for install location, agent (claude/codex), name, role, and Obsidian, then runs clone + setup.

> The curl entry path is the same — `... | sh -s -- init` / `... | sh -s -- join <url>`.

After installation finishes, the CLI instructs you to: **open Claude Code or Codex and enter `tm-onboard`** → install verification and the tm-mode value briefing run automatically. (Installation is complete, but team mode is still off — **installed ≠ activated.**)

> **Codex users**: after first install or hook changes, open codex (TUI) once and press **Trust** in the hook trust prompt — otherwise hooks are silently skipped in headless (`codex exec`) runs (`tm on` detects this and shows a [warn]).

## 3. Turn on team mode (activation)
Installation does not automatically turn on team mode. Turn it on when you start work:
```bash
# Tell the agent "팀모드 켜" / "tm on"   (or run directly:)
python3 infra/teammode.py on --root . --install
```
Once enabled, the `session-start` hook automatically injects team context from the next session onward. To turn it off, run `... off --root . --install`.

## 4. (Optional) Obsidian vault
The `join` wizard asks about this. To attach it later, rerun `tm-mode join <url>` from the same location (idempotent). This lets you view `memory/` as an Obsidian graph. If Obsidian is not installed, setup skips it gracefully (creates nothing).

---

## Appendix — `install.py` / flags (advanced/internal)
In normal entry paths, you should not call this directly — `tm-mode init/join` delegates to the cloned repo's `infra/install.py` by subprocess (`--root . --yes`). Use this only for debugging, isolation, or removal:

| Flag | Role |
|---|---|
| `--yes` | Allow wiring real agent settings (for example `~/.claude/settings.json`). **Without this, nothing is written** (safety gate). The CLI always calls install with `--yes` |
| `--settings <directory>` | Isolated run — no real host contact (tests/CI). The path is a directory (agent-specific settings are created under it) |
| `--dry-run` | Print the plan only, without making changes |
| `--register-obsidian` | Register the Obsidian vault (opt-in) |
| `--uninstall` | Remove what install added to the host (hooks, skills, env, markers) in reverse order |

### Engine verbs (`teammode.py`)
| Verb | Role |
|---|---|
| `on` / `off` | Turn team mode on/off (banner, hooks, active marker) |
| `log` | Record a session log (date and frontmatter are automatic) |
| `context` | Collect everyone's recent session logs and status as JSON (summaries are a skill's responsibility) |
| `pull` / `commit` / `update` | Git sync, commit, and upstream update |

Every verb receives the team root explicitly through `--root` (environment variables are not trusted — safety).

For `update` specifically, the installed launcher also has a thin shortcut so you don't need to spell out the full engine invocation: `tm-mode update [path] [--dry-run] [--force]` (path defaults to the current directory; it must be a team repo root — no parent-directory search). It's exactly equivalent to `python3 infra/teammode.py update --root <path> [--dry-run] [--force]`.

### Removal
The `tm-mode` CLI has no uninstall command. To remove host-side setup, run this inside the team repo:
```bash
python3 infra/install.py --root . --uninstall
```

---

For **what tm-mode is and why to use it**, see [README.md](README.md). For behavior specs, see [docs/spec/](docs/spec/README.md).
