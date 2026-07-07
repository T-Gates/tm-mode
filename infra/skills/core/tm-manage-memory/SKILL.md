---
name: tm-manage-memory
description: Use when adding, updating, or deleting memory in the team memory base. Triggers on "메모리 추가해", "메모리 수정", "메모리 삭제", "KB 업데이트", "메모리에 저장", "add memory", "update memory", "delete memory", "update the KB", "save this to memory".
---

# tm-manage-memory — Team Memory Base CRUD

## Overview

Handles adding, updating, and deleting memory files under `memory/`.
**This skill makes the judgment; the engine `memory` verb handles the mechanics**(implementation depth B).
Bulk or external document(docs slot) imports belong to `tm-import-memory` — this skill is for one-off CRUD derived from conversation.
The skill must not write files directly or edit INDEX. Always go through the engine verb.

## When to Use

- "메모리 추가해", "KB에 저장", "메모리에 저장", "add memory", "save to the KB", "save this to memory"
- "~ 수정해", "~ 업데이트해", "update ~", "edit ~" (when the target is a memory file)
- "~ 삭제해", "~ 지워", "delete ~", "remove ~" (when the target is a memory file)
- When content derived from the conversation should be reflected in the memory base

## Target Scope

| Folder | Target | Notes |
|------|------|------|
| `product/` | O | Product-related memory |
| `team/` | O | Conventions, ground rules, members |
| `team/decisions/` | O | Team decisions |
| Top-level folders registered in the root INDEX | O | Team-specific domains(for example, `fundraise/`) — after registration with `memory route upsert` |
| `team/sessions/` | X | Accumulated automatically(hooks) |
| `team/meeting/` | X | -> tm-context |

## Metadata Contract

Every memory file has YAML frontmatter(the engine stamps it automatically):

```yaml
---
created_at: 2026-06-18
updated_at: 2026-06-18
author: bob
weight: 🔥          # 🔥 core / 📌 important / 📎 reference
---
```

**weight contract(core)**:
- `🔥 핵심`: Stable memory the team references often
- `📌 중요`: Information that affects work
- `📎 참고`: Background memory / history
- **The agent must not guess and insert this arbitrarily** — always confirm with the user.
  Only when the user says "알아서 해" / "decide for me" may the agent infer it from context.

## Procedure

### 1. Determine Intent

Identify the **action** and **target** from the user's message.

| Pattern | Action |
|------|------|
| "~에 ~추가해", "KB에 저장", content provided without a filename | **Add** |
| "~ 업데이트해", "~ 수정해", existing filename mentioned + change details | **Update** |
| "~ 삭제해", "~ 지워" | **Delete** |

If the target is ambiguous, read `memory/INDEX.md`, present the folder list, and ask the user to choose.

### 2. Folder Routing(when adding)

Compare against the folder descriptions in INDEX.md and automatically recommend an appropriate folder.
Respond in the user's language.

```
This looks appropriate for team/decisions/.
If that is correct, say "yes"; if another folder is better, tell me which one.
```

The target folder must be one of:
- `product/`, `team/`, `team/decisions/`, or a team-specific top-level folder registered in the root INDEX

### 3. Decide Filename + Weight(when adding)

Suggest a kebab-case filename that summarizes the content, then confirm it.
The weight must **always be asked and confirmed with the user** — do not guess.
Respond in the user's language.

```
Filename: api-auth-flow.md
Weight: 📌 중요 (🔥 핵심 / 📌 중요 / 📎 참고)
If this is correct, say "yes"; if anything should change, tell me.
```

### 4. Call the Engine Verb

After confirmation is complete, call the engine verb.

> **Honest explanation of the unlock flag**: The engine `memory` verb uses a separate process `open()`,
> so it is not subject to the PreToolUse hook; going through the engine does not itself require unlock.
> Unlock is only necessary if the skill directly touches `memory/` with the `Write`/`Edit` tools — this skill
> never does that, so the principle is to avoid opening an unlock window(no direct-edit window needed).
> Steps 4-0/4-2 below are reference implementations for exceptional cases that require manual editing.
> **In the normal flow(engine path), do not run 4-0/4-2.**

#### 4-0. unlock begin(start)

```bash
python3 infra/teammode.py memory unlock begin --root .
```

The engine handles the flag path contract(root_hash + session_id, XDG/TMPDIR fallback) together with
kb-write-guard as a single source; the skill must not manually recompute the path.

Session id resolution order: ① env `CLAUDE_SESSION_ID`/`CLAUDE_CODE_SESSION_ID`(Claude)
② latest relay file left by the SessionStart hook(Codex — also works for sessions without env)
③ explicit error if neither exists — unlock cannot be opened outside an agent session(fail-closed).

#### 4-1. Call the engine verb

**Add/update:**
```bash
python3 infra/teammode.py memory write \
  --root . \
  --folder <folder> \
  --filename <filename.md> \
  --content "<content>" \
  --author <current-user> \
  --weight "<weight>"
```

**Delete(user reconfirmation required before deletion):**
```bash
python3 infra/teammode.py memory delete \
  --root . \
  --path <memory/relative-path> \
  --author <current-user>
```

What the engine handles(the skill must not do these directly):
- frontmatter stamping(created_at/updated_at/author/weight)
- file write/delete
- INDEX.md row upsert/removal
- edit date calculation(based on the body commit, excluding metadata commits)
- do_commit(paths only, push=False)

#### 4-2. unlock end(after commit completes)

Immediately after the engine verb completes successfully(including commit), close the edit window(idempotent — ignore if already absent).

```bash
python3 infra/teammode.py memory unlock end --root .
```

> If a flag remains after abnormal termination(error/interruption), it expires automatically after the TTL(5 minutes).

### 5. Bidirectional Backlinks(engine automatic — verify only)

Immediately after the memory change, the engine **mechanically** creates bidirectional links(the skill does nothing):

- **Session log -> document**: append one line to today's session log for the current author: `📝 생성/✏️ 수정/🗑️ 삭제: [[<경로>]]`.
- **Document -> session log**: add `session: team/sessions/<author>/<work-date>.md` to the written document's frontmatter.

Idempotent(no duplicate lines/fields on repeated edits) and non-blocking(memory change remains even if backlinking fails). The skill only checks the result.

### 6. Chat Notification(when the chat slot is connected)

After the memory change **completes successfully**, if `services.chat` in `team.config.json` is connected,
**the AI directly calls the vendor MCP tool for the chat slot** and notifies the team(option A — the engine does not call MCP).
When reporting the notification outcome to the user, respond in the user's language.

- The engine outputs a one-line notification summary to stdout in this form: `[chat-notify] memory 추가/수정/삭제: <경로> · weight=… · author=… · 요약=…`.
  The AI uses that line to compose the notification message(action, file path, weight, author, first-line summary).
- Notify for every change(add/update/delete); there is no filter.
- If the chat slot is not connected(no `services.chat`), skip notification.
- **Non-blocking(advisory)**: if the notification call fails, keep the memory change as-is — report only the error and do not stop.

> The actual vendor MCP tool name and channel selection for the chat slot follow the provider recorded in config when `tm-connect` connected it.

### 7. Completion Report

Respond in the user's language.

```
Added team/decisions/api-auth-decision.md
INDEX updated · commit completed(push is separate)
Session-log backlink · chat notification completed
```

## Registering a New Top-Level Folder — Root Routing Map(route)

The root `memory/INDEX.md`(2-column routing map) is the single entry point injected into every session —
**when a new top-level folder is created, registering it here is required**. Registration/removal also goes through engine verbs:

```bash
python3 infra/teammode.py memory route upsert \
  --root . --path fundraise/ --desc "투자유치 리서치" --author <current-user>
# Remove: memory route remove --root . --path fundraise/ --author <current-user>
```

- If `memory write` detects an unregistered folder, it prints one line like `[hint] '...'가 루트 INDEX에 미등재 — 등록: ...`
  Follow that command as-is(confirm only the one-line `--desc` with the user).
- Do not guess `--desc` — routing-map quality determines the quality of every session injection, so confirm with the user.

## Non-Goals(L1)

- Registering a verification-sources manifest(optional, follow-up)
- Session log management(-> automatic hooks)
- Meeting note management(-> tm-context)
- Reading/loading memory(-> tm-memory)
- Calling issue tracker / document tool / calendar management APIs
- Direct file editing(always go through the memory verb)

## INDEX Format(reference)

Format maintained automatically by the engine:

```
> 가중치: 🔥 핵심 · 📌 중요 · 📎 참고

| 가중치 | 경로 | 내용 | 편집일 |
|--------|------|------|--------|
| 📌 | `memory/team/decisions/api-auth-decision.md` | API 인증 방식 결정 | 2026-06-18 |
```

- **Edit date**: last commit date on which the body actually changed(excluding meta/INDEX/weight commits)
- The tm-memory skill uses this INDEX to understand "what exists where"
