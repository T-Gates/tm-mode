---
name: tm-memory
description: Use when the user wants to load team memory into context. Triggers on "메모리 불러와", "팀 메모리", "메모리 로드", "memory", "메모리 로드", "load memory", "team memory", "load team memory".
---

# tm-memory — Load Team Memory

## Overview

Load the **INDEX summaries under `memory/` into context**. The purpose of loading memory is to understand "what exists where," and the INDEX files are enough for that. Read full individual files only when the work actually requires that depth.

## When to Use

- "메모리 불러와", "팀 메모리", "메모리 로드", "memory", "메모리 로드", "load memory", "team memory", "load team memory"
- Requests that **point to a specific document**, such as "DB 스펙 좀 봐봐" or "코드 컨벤션 읽어" -> skip the suggestion step and load only that file immediately.

## Procedure

### Mode A — Load INDEX Files (Default)
1. **Update the repo**: from the team root, run `python3 infra/teammode.py pull --root .` (engine verb) to pick up memory pushed by other teammates.
2. **Discover and load the INDEX hierarchy**: first read `memory/INDEX.md` as the folder map. Then use `find memory -name INDEX.md` to discover lower-level INDEX files and read **all** of them. tm-mode is independent of product structure, so discover paths dynamically instead of hard-coding them. INDEX files contain one-line summaries for each document; that is enough to understand "what exists where."
3. **Present the summary**: based on the loaded INDEX files, group and show "what memory exists now," and tell the user, in the user's language, **"tell me if you want to go deep on a specific topic."**
4. **Load full files only on request**: if the user points to a specific file, Read only that file.

### Mode B — FTS (Keyword Search)
Use this for requests that **search for something by keyword**, such as "X 관련 맥락 찾아줘" or "Y 결정 언제 했어?"

1. Use `grep -r --include="*.md" -l "키워드" memory/` to collect the list of hit files.
   - Include session logs (`memory/team/sessions/`) too, so the search also captures "who worked on this before."
2. For each hit file, excerpt the context around the keyword (±2 lines) and show it.
3. If related files exist, say, in the user's language, "tell me if you need the full text."

## Rules

- **Default to INDEX files only.** Load full text for entire folders or "everything" only when the user explicitly asks. Do not pour full files into context without being asked.
- **Never Read binary files (pdf, jpg, png, etc.).** Report only paths and counts.
- **Large reference folders** (`prior-art`, etc. with their own INDEX/README): read only README/INDEX. Read individual files only on explicit request. If the user asks for a large scope like **"다" / "전부"**, do not dump individual full texts into context; show the README's listing table, then narrow scope by asking, in the user's language, **"which item should I open?"**

## Non-Goals

- Editing files (read-only)
- Bulk-loading full text without being asked
- Creating team-status or session summaries (-> tm-context)
- Calling external APIs (external services such as issues, docs, or calendar)
