# Team Mode Operating Guidelines

This session is in team mode. Work by actively using the team's memory (context).

## Before starting work — find the context first
- Before starting any task, look up the relevant context — team memory (`tm-memory`) + session logs (who did what before, and why).
- Do not proceed without knowing the product, decisions, rules, and work history the team has built up.

## If memory is empty — ask for it to be filled
- If team memory lacks context, ask the user to fill it in:
  > "To do this properly I need team context — could you add X to memory?"
- Do not proceed by guessing from thin context.
- **If existing documents exist, actively recommend moving them into memory** — if something is already written in Notion, Google Docs, Confluence, etc., importing it is the fastest path. Ask first: "Do you already have this written up somewhere? If so, I'll move it right in."
- **If `product/tech/` is empty**: request the GitHub repo link, and once received, analyze the repo with a background subagent to auto-fill `stack.md` and `features.md`.

## Core context (what the team should have)
- **Product & brand**: what we build, key features, core customers, brand philosophy (differentiation & values), roadmap
- **Ground rules**: how the team works, code conventions, principles to uphold
- **Decisions**: why things were decided that way (prevents repeated debates)
- **Architecture & tech stack**: system structure, core technologies (DB, framework, language), constraints

## Memory is written through verbs
- Do not Edit/Write `memory/` directly (that skips the INDEX and commit procedures).
- Look up with `tm-memory` / add & update with `tm-manage-memory`.
- When the user states a decision or memory to leave for the team, record it with `tm-manage-memory`. Accumulated memory is auto-injected into the next session.

## If you spot a problem in teammode itself — suggest reporting it upstream
- If you find a bug, friction, or improvement in the tm-mode product (`infra/`), don't bury it — suggest reporting it to the user:
  > "Shall I report this to the teammode upstream repo?"
- Report via the `tm-contribute` skill — first run the **upstream-diff diagnosis** to determine "is this really an upstream bug, or a local problem in our instance?" (prevents junk issues). If it's an instance problem, recommend `tm on` (sync); if it's an upstream bug, file a GitHub issue.
- Problems we hit help other teams too — the product grows together.
