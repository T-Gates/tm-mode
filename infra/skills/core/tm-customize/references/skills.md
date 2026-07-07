# Customize Utility Skills

## What util skills are

Auxiliary skills that team members use for their own work (research, debugging, review, etc.). Unlike core skills (tm, tm-context, ...), they are selected and installed **per team member**, because planners and developers use different skills.

- **Recommendation pool (catalog)** = the `infra/skills/util/<name>/` directories. Only skills present here can be installed.
- **Member install list** = `memory/team/sessions/<member>/util-skills.json`.
- **When changes apply**: if team mode is `on` and `add`/`remove` is run with `--install` (or `--settings <경로>`), symlinks are applied immediately. If not provided, that is, if only `--root .` is provided, immediate application is skipped even in the active state (to protect the real host), and only the json is updated -> applied on the next `on`. If team mode is `off`, only the json is always updated.
- **Principle**: do not edit files or symlinks directly. Go only through the engine's `util` verb.

## Three verbs

```bash
# Query — available (catalog) + installed (member install status) as JSON
python3 infra/teammode.py util list --root . [--member <이름>]

# Add (if on, apply immediately with --install)
python3 infra/teammode.py util add --root . --member <이름> --skill <스킬명> --install

# Remove (same behavior)
python3 infra/teammode.py util remove --root . --member <이름> --skill <스킬명> --install
```

- If you try to add a missing skill (outside the catalog), the engine **rejects** it. Do not guess or force it.
- `add`/`remove` are idempotent, so rerunning them is safe.

## Session-log-based Recommendations

Recommend "what would be good to install for this person" by **deriving it from session logs**. This is the tm-mode way: the agent directly reads memory and makes a judgment.

**Procedure:**

1. **Query catalog and install status**
   ```bash
   python3 infra/teammode.py util list --root . --member <이름>
   ```
   → `{"available": [{"name":"...","description":"..."}], "installed": ["skill-name", ...]}`
   Warning: the shapes differ. `available` is an **array of objects** (name+description), while `installed` is an **array of strings**. When matching, compare `available[].name` against the strings in `installed[]`.

2. **Empty catalog guard (important)** — if `available` is empty, **there is nothing to recommend.** Do not spin. Respond in the user's language, explain this, and stop. Example in English:
   > "The util skill pool is empty, so there is nothing to recommend yet. Add skills under `infra/skills/util/` first, then recommendations can work." (See below for how to fill it.)

3. **Identify work patterns from session logs** — read that member's logs:
   ```bash
   ls memory/team/sessions/<이름>/        # YYYY-MM-DD.md files
   ```
   Read several recent logs and look for **repeated work types** (for example: frequent research / frequent debugging / many PR reviews).

4. **Match recommendations** — among uninstalled skills (items in `available[].name` that are not in `installed`), choose **only those that actually fit** the identified pattern. Every recommendation **must include one line of log evidence**. Respond in the user's language. Example in English:
   > "Your logs show frequent debugging sessions (6/18 and 6/19), so I recommend `<debug-skill>`."

5. **Add only after consent** — if the user accepts, install with `util add`. Do not force it.

**Safety guards:**

- No speculative recommendations without log evidence. Do not rely on generic claims like "because they are a developer"; base recommendations only on **work that actually appears in that person's logs**.
- Do not recommend skills outside the `available` pool (the engine will reject them anyway).
- If there are no session logs or they are empty (new member), hold off on recommendations and, in the user's language, say that you can recommend once logs accumulate.
- If logs exist but no pattern matches the catalog, do not force a recommendation. Be direct, in the user's language, and hold off: for example, "I could not find enough evidence in the current logs for a suitable recommendation."

## How to Fill the Catalog

If the recommendation pool is empty, add `infra/skills/util/<name>/SKILL.md` to register a skill (same SKILL.md structure as other skills). Put auxiliary skills shared by the team here; afterward, they can be selected, installed, and recommended per member.

## Common Mistakes

| Mistake | Correct method |
|---|---|
| Editing symlinks/json directly | Go only through the engine's `util` verb |
| Trying to recommend from an empty catalog | If `available` is empty, tell the user to fill it and stop |
| Recommending from generic assumptions without reading logs | One line of session-log evidence is required |
| Recommending a skill outside the catalog | Only recommend within `available` (the engine rejects anything else) |
