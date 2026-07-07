# CLAUDE.md — tm-mode

For agent operation and setup in this repo, **[AGENTS.md](AGENTS.md)** is the single source of truth (shared by Claude Code and Codex).

## Quick Start
When the user says **"이 레포 셋업해줘" / "set up this repo"** (or "팀모드 셋업" / "team-mode setup", "온보딩" / "onboarding"), follow the **"첫 접촉" (First contact)** procedure in AGENTS.md and use the `tm-onboard` skill (`infra/skills/base/tm-onboard/SKILL.md`).

- **Two installation paths**: 1. clone-and-go — if the team repo is already cloned, finish setup here through the AGENTS.md "첫 접촉" bootstrap (dry-run plan → conversational approval → `install.py --yes`); 2. CLI — new team: `tm-mode init`, joining: `tm-mode join <url>` (wizard).
- **The `tm-onboard` skill only verifies and briefs after installation.** It does not call `install.py` directly or ask for member name or team name (installation and the one-time member-name question belong to the AGENTS.md bootstrap procedure).
- Safety: the team root is explicit `--root` only (do not trust env). Real settings writes are gated by `--yes` (after conversational approval) / `--settings` (isolated). Pushes are the person's decision.

## Service Connection (L2)
To attach a service to a role slot (issues / chat / docs / calendar), follow the **`tm-connect` skill** (`infra/skills/core/tm-connect/SKILL.md`). tm-onboard only *suggests* this right after the first value briefing; tm-connect performs it. Issuance guidance must be read from `providers/<provider>.json` data, and tokens are **entered individually** → local vault (`infra/credentials.py`, plaintext 0600 — sync folders forbidden).

For details, see [AGENTS.md](AGENTS.md). For behavior specs, see [docs/spec/](docs/spec/README.md); for backlog and unimplemented designs, see [docs/BACKLOG.md](docs/BACKLOG.md).
