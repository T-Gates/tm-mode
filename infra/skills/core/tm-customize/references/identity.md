# Identity Customization (Team Name, Greeting, Farewell)

## What Identity Means

The team's three display strings. All of them live in the `team` block of `team.config.json` at the team root.

| Field | Where it is used | Default formula |
|---|---|---|
| `name` | Statusline team name · greeting/farewell default formula · banner default | (git repository name at install time) |
| `greeting` | Output immediately after the `tm on` banner | `{name} 팀모드 ON` |
| `farewell` | Output when `tm off` exits | `수고하셨습니다 — {name}` |

## You Can Change It Freely at Any Time (Zero Impact)

The team name and greeting are purely for display. **Changing them at any time does not break anything** because the credential vault is a single file (`default.json`) and is not bound to the team name (single-vault transition on 2026-06-21). L2 tokens, session logs, and members are all unaffected.

> (This simplicity holds because multi-team support is not currently available. If multi-team support becomes necessary, revisit the vault key strategy then.)

## Method: Edit team.config.json Directly

Critical difference from the banner: `team.config.json` is at the **team root**, so it is **not** covered by `kb-write-guard` (which is only for `memory/`) -> **you may edit it with Edit/Write tools**. Since it is JSON, Edit is safer than sed. There is no engine verb for identity changes.

1. Open `team.config.json` and change `team.name` / `team.greeting` / `team.farewell`. **Keep the JSON valid** (commas and quotation marks).
   ```jsonc
   "team": {
     "name": "acme",
     "greeting": "acme 팀모드 켜짐 🐳",
     "farewell": "오늘도 고생했어요 — acme"
   }
   ```
2. If greeting/farewell differ from the default formula, `personality_customized` becomes `true` (the engine decides at runtime by comparing them against the default formula, using the same flag as the banner).
3. **Check**: confirm the change with `python3 infra/teammode.py context --root . --json`, or verify that the new greeting appears on the next `tm on`. When reporting the result to the user, respond in the user's language.

## Notes When Changing Only the Team Name

- If you change `name` but leave greeting/farewell as the default formula, the new name is reflected automatically because the formula uses `{name}`. If greeting/farewell are already fixed custom strings, also update any old team name inside those strings **manually**.
- If the banner is ASCII with the team name embedded in `memory/banner.txt`, changing the team name is not reflected in the banner automatically -> update the banner separately via `references/banner.md`.

## Common Mistakes

| Mistake | Correct Method |
|---|---|
| Mistakenly assuming identity must also be changed only through Bash | `team.config.json` is at the root, so Edit/Write is OK (only the banner is Bash-only) |
| Breaking JSON (missing commas, etc.) | After editing, check validity by confirming `tm context` can read it |
| Old name remains after customizing greeting and then changing the team name | Manually update the team name inside custom strings (only the official `{name}` formula updates automatically) |
| Worrying that "changing the team name breaks tokens" | It does not break them because there is a single vault; change it freely |
