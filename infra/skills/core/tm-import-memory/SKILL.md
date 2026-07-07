---
name: tm-import-memory
description: Use when the user wants to bulk-import external docs (docs 슬롯에 연결된 문서 서비스, docs slot connected document service) into team memory — "메모리 업로드", "문서 메모리로 옮겨", "메모리로 가져와/시드", "이 페이지(들) 메모리에 추가해" (including when the user refers to the connected document service by name, for example "노션 메모리 가져와"); English triggers include "upload docs to memory", "move docs into memory", "import/seed memory", "add this page/these pages to memory", and "import docs memory". Single-item CRUD from conversation belongs to tm-manage-memory.
---

# tm-import-memory — External Docs -> Team Memory Upload (Import)

Scrape pages from the document service connected to the **docs slot**, organize them by topic, and save them into team `memory/`.
This skill handles both cold-start seeding and later incremental reruns. **This skill makes the judgment; the engine `memory write` performs the save.**

> **Role vocabulary principle**: This skill speaks only in terms of the "docs slot". The actual service is determined by `services.docs.provider` in `team.config.json`; read that provider name and use it in user-facing guidance and source attribution (do not hardcode a specific product name).

## Boundary (With tm-manage-memory)

| Origin | Skill |
|---|---|
| External docs (docs slot), bulk, tree-shaped import | **tm-import-memory** (this skill) |
| Single-item knowledge/decisions from conversation | tm-manage-memory |

Reruns such as "add this page" also use this skill. Because filenames are chosen from the **approved destination topic**, the same topic converges into the same file and stays idempotent (see the partial rerun rule in section 4).

## Procedure

### 0. Check the docs slot
Check `services.docs` in `team.config.json` and the corresponding MCP connected to the agent.
**If it is not connected**: respond in the user's language and say that the document service is not connected yet, and that `tm-connect` can connect it; then **stop**. Connecting the service belongs to tm-connect.

### 1. Scope discovery — do not read page bodies
From the link the user provided (or from workspace search), identify **only the page list/tree**. A hub-page fetch returns the child-page link list, so it can be enumerated without reading bodies.
Default cap: **20 pages and depth 2**. For anything beyond that, show only the list and proceed only with the pages the user chooses. "All of it" is an unbounded run; do not proceed without list confirmation.

### 2. Preview confirmation gate (required — this one confirmation also approves the weight and route explanation in bulk)
Respond in the user's language. Before fan-out, show a plan table and get **one** confirmation:

| Source page | -> Destination (folder/filename) | Proposed weight | Rationale |
|---|---|---|---|

- Default weight is **📎(reference)**. Propose 📌 only for clear decisions/rules/operating principles. **Never auto-propose 🔥** (the team can promote core items later). The point of the weight convention is "no silent confirmation"; once the user approves this table, confirmation has been received, and you do not ask separately per file.
- Prefer existing structure for destinations (`product/<product>/...`, `team/decisions`, etc.). If a **new top-level folder** is needed (for example `fundraise/`), include the **path plus a one-line routing map description (`desc`)** in the table and approve it together. Engine `route upsert` must not guess `--desc` (required argument), so it must be settled here.
- State together: "Up to N files will be created/updated, with one commit/push attempt per file."
- **Merge by topic**. Do not create one file per page; aim for about 10 generated files or fewer.

### 3. Per-page fan-out (subagents)
Dispatch subagents in parallel, one per page. Each subagent:
- Reads **only one page body**, its own page. This is the reason for fan-out: large external docs should not pollute the main context.
- Returns only a **structured result** containing summary, topic tags, and source (page title and URL).
- **Does not write files directly.**

### 4. Aggregate and save (main)
If a new top-level folder was approved, **register it first** (all arguments are required; a bare call exits 2):

```
python3 infra/teammode.py memory route upsert --root <team-root> \
  --path <folder>/ --desc "<one-line description approved in preview>" --author <member-name>
```

After merging and deduplicating subagent results by topic, save each file through the engine verb:

```
python3 infra/teammode.py memory write --root <team-root> \
  --folder <folder> --filename <kebab-name>.md \
  --content "<body>

## Sources
- [Page title](URL) (<docs provider name>, collected YYYY-MM-DD)" \
  --author <member-name> --weight <approved weight>
```

- The body must include a `## Sources` section at the bottom to mark external provenance. Use the value from `services.docs.provider` as the provider name.
- **Partial rerun merge rule**: if the target topic file already exists, **read the existing file first, update only the rerun source/page portion**, and build the full body while preserving content from other sources and the `## Sources` list. Engine write is a full replace, so if you do not pass the entire merged result, content from other sources is lost. Do not create a new file from only the rerun page either (that violates the no 1:1 rule).
- ⛔ Do **not** directly Edit/Write `memory/`; INDEX registration, frontmatter, backlinks, and commits are all handled by the engine.

### 5. Completion report
Respond in the user's language. Report the created/updated file list, INDEX reflection, and push success/failure separately (engine push failure is non-blocking; the local commit is preserved).
No need to collect re-onboarding/team members; they receive it through `git pull`.

## What This Does Not Do
- Token issuance/connection (-> tm-connect) or single conversation-memory CRUD (-> tm-manage-memory)
- Source-document edits (read-only) or continuous automatic sync (v0.2+)
- Ignoring caps to collect "everything"; do not proceed without list confirmation

## Common Mistakes
| Mistake | Correct approach |
|---|---|
| Main agent reads all page bodies directly | Only subagents read bodies; main agent handles list and aggregation |
| Asking about weight per file | The single preview table confirmation approves them in bulk |
| Page:file 1:1 dump | Merge by topic, about 10 files or fewer |
| Directly editing `memory/` | Always go through `memory write` |
| Writing directly to a new top-level folder | Register first with `route upsert` (all of `--path`, `--desc`, and `--author`) |
| Replacing a whole merged file during a partial rerun | Read the existing file and save the full body with only that source updated |
| Hardcoding guidance for a specific product name | Read `services.docs.provider` and speak using that name |
