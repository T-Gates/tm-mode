# Overview

tm-mode SPEC v0.4 - Overview, terminology, notation, and versioning

| | |
|---|---|
| spec_version | **0.4** |
| Status | Official single edition (reconciled with build, 2026-07-15; 0.4 - seven-event lifecycle, correlation IDs, exact publication, and SessionStart dedupe made explicit) |
| Scope | Team memory standard · hook/adapter standard · engine verbs · install/bootstrap · onboarding skills · conformance declarations · service slots |
| Supersedes | This document **integrates and replaces** the scattered `spec/01` through `spec/05` documents (see the repoint list in Appendix D). |
| Notation | **Required / Recommended / Reserved** (§0.3) |

> **Reconcile principle**: when the design (`spec/`) and build (`infra/` and `conformance/`) differ, **the build (code) is the truth**. The body describes issues closed by code as closed, and records differences in Appendix A. Items left open by the 04/05 draft but answered by implementation are stated as final in the body. Design items that are ahead of the code (not implemented) are explicitly marked "reserved/roadmap" in the body.

---

## §0. Overview, Terminology, and Notation

### 0.1 One Sentence

> A cross-agent team collaboration toolkit that gathers a team's work context (session logs, decisions, status) **as Markdown in one git repository**, lets any AI coding agent (Claude Code, Codex, ...) read and write the same team memory, and has agents **automatically read that context at session start**.

tm-mode is designed along two orthogonal axes: the **agent axis** (§2 - one copy of the same hook/skill content, translated into each agent's notation by adapters) and the **service axis** (§7 - the same role may be backed by different products for each team). The data standard (§1) is the foundation for both axes.

### 0.2 Terminology

| Term | Definition |
|---|---|
| **Team repo** | A git repository containing team memory and engine settings. One private repo per team. |
| **Team root** | The local clone path of the team repo. |
| **Session log** | A journal of team work a member performed during an agent session (§1.3). |
| **Workday** | The date after applying the 06:00 cutoff (§1.4). |
| **Injection** | Automatically loading part of team memory into agent context at session start (§1.6). |
| **Canonical** | The standard vocabulary tm-mode defines independently of any agent: canonical events (§2.4), action classes (§2.5), and canonical input schema (§2.10). |
| **Adapter** | A folder under `infra/agents/<name>/`. At install time, it translates and registers canonical declarations into the corresponding agent's settings (§2.7). |
| **Normalize shim** | A thin runtime translation layer that converts an agent's hook input JSON into the canonical schema (§2.10). |
| **Action class** | An agent-independent abstraction of built-in tools. Example: `file_edit` = Claude `Write\|Edit` = Codex `apply_patch`. |
| **Common script** | `infra/hooks/*.py`. It knows only the canonical schema and does not know any specific agent. |
| **Service slot** | A name for a service role such as issues/chat/docs/calendar. The team registers its chosen **official vendor MCP** in the slot (§7). tm-mode only connects it; the AI calls that MCP tool directly for behavior (there is no extra wrapper layer with a tool-neutral function contract). |
| **Reference implementation** | The implementation in this repo. Tier 1 = Claude Code baseline. Code: `infra/` and `conformance/`. |
| **Independent implementation** | An implementation written separately from this repo's code, targeting compliance with this spec (§6). |
| **L1 / L2 / L3** | Reach levels: L1 = session log + hooks + context collection (reachable by install.py alone), L2 = **registrar that plugs official vendor MCPs into service slots** (connection only; the AI calls MCP tools directly for behavior), L3 = digests, etc. |

### 0.3 Notation

- **Required** - noncompliance if not followed. Subject to conformance checks.
- **Recommended** - not noncompliant if omitted, but there should be a justified reason.
- **Reserved** - an item whose place is defined in the current spec_version (0.4), but whose meaning is not finalized. Do not use arbitrarily.

### 0.4 Versioning (common to all areas)

- Every area of this SPEC (§1-§7) **shares a single `spec_version`**, currently **0.4**. If any area has a normative change, the version rises together. A team repo's `team.config.json` top-level `spec_version` field declares the version followed by that team data.
- **Minor bump targets (required + CHANGELOG entry)**: additions or semantic changes to session-log format fields, folder-structure changes, canonical events (§2.4), action classes (§2.5), canonical input schema (§2.10), adapter contract (§2.7), engine verb contract (§3), and conformance check items (§6.4). Meaning-preserving fixes such as typos or explanation improvements do not change the version.
- **During the 0.x period, backward compatibility may break.** Once two or more independent implementations are listed, freeze 1.0 and introduce an RFC-lite change process (proposal issue -> implementation impact review -> merge after agreement).
- The single source for supported versions in the reference implementation is `infra/install_lib.py::SPEC_VERSION`. Current reference adapter `events.json` files do not have a separate `spec_version` field. Independent implementations must state their supported `spec_version` in listing applications, badges, and result logs (§6).
- The CHANGELOG is managed in the main repo that distributes the spec (not in team repos).

---
