# Install · Bootstrap

tm-mode SPEC v0.4 — install · bootstrap

## §4. Install · Bootstrap (install.py)

> **Entry contract (0.3 update — clone-and-go · 2026-07-06 URL entry added)**: There are three entry paths. ⓪ **One product URL line**: the user gives the agent the product README URL and says "세팅해줘" — the agent follows the README's **"For AI agents: setup instructions"** procedure (new team = create from gh template / join = clone team URL → present `install.py --dry-run --yes` plan for **chat approval** → `--yes`). The gate is the same contract as ①. ① **clone-and-go**: a person clones the team repo and tells the agent "셋업해줘" — the AGENTS.md "first contact" bootstrap procedure presents the `install.py --root . --dry-run` plan → **chat approval** → runs the real install with `--yes` (product decision 2026-07-04: the essence of --yes is the person's explicit intent — chat approval can substitute. No real host contact before approval; dry-run is the consent gate). ② **CLI**: `tm-mode init` (new team: create repo → immediately join) or `tm-mode join <url>` (join: clone+setup) — the wizard asks for member name, org, team name, role, agent, and Obsidian through dialogue, then delegates to `infra/install.py` as a subprocess. After any path, run `tm-onboard` (verification + briefing) in the agent.
>
> **Role of install.py**: It is invoked by the CLI and runs inside an already cloned team repo. It sets up scaffolding, agent wiring, env, and hooks in one pass, then verifies at the end with `context --json` that **L1 data can be read**. The normal install.py bootstrap path is a **deterministic fixed script** and does not make LLM judgments (service selection). The only exception is the new `--register-obsidian` registration path, which may generate `ts`/`vault_id` with `time.time()`/`os.urandom()`.
>
> ground truth: `infra/install.py`, `infra/install_lib.py` in the working tree as of 2026-06-16; the entry contract is in `src/teammode/cli.py` (`cmd_init`, `cmd_join`, `_wizard_join`, `_done`). The current working tree has uncommitted changes related to `install-skills`/adapters/tests, and this section reflects the **current file contents** regardless of commit state.

### 4.0 Design Principles

| Principle | Meaning |
|---|---|
| **Core ≠ skin** | install.py is the install core. Entry skins ("셋업해줘", pipx, npx, plugin) are eventually thin layers that call install.py. |
| **Deterministic** | The normal bootstrap path aims for the same input and same file state → same result. Name, identity, role, settings, and root are read from explicit arguments or git/local files; if ambiguous, stop. Exception: new registration for `--register-obsidian` can have different `ts`/`vault_id` defaults on each run (§4.9). |
| **Do not trust env** | The team root is determined only by `--root` or a cwd with team markers. Ambient `TEAMMODE_HOME`/`LEGACY_TOOL_HOME` is not trusted during install. The verify subprocess also receives only an env allowlist. |
| **Reach L1 independently** | Even without service connections (L2), proceed through the memory structure, members, hook wiring, active marker, and context collection. Empty `services` slots are normal. |
| **Cross-agent** | The wire step detects agents by the presence of home `.claude`/`.codex` directories and delegates to each adapter CLI. However, bootstrap verify only calls `context` (`on` is not used), so it is independent of adapter loading (§4.4, §4.7). |
| **Host-write gate** | Real agent settings and real env are processed only when there is `--yes` or an isolated `--settings` intent. If `--settings` is present, env is not written to the real host. |
| **Delegate judgment** | "Which Notion DB / which calendar" is outside install.py's scope. Provider connection is filled by a separate connect/onboard layer, and install allows empty slots. |

**Out of scope**: service OAuth/token issuance/resource selection, team repo creation (`gh repo create --template`), and clone — those belong to `cli.py` (`cmd_init`, `cmd_join`). Hosting, dashboards, and digests are also out of scope. The current `install.py` assumes it is run inside an already cloned team repo (the CLI clones, then delegates via subprocess).

### 4.1 CLI Contract

```
python3 infra/install.py [--root PATH] [--agent VALUE] [--member-name NAME]
                        [--role ROLE] [--settings DIR] [--yes]
                        [--update] [--dry-run]
                        [--register-obsidian] [--obsidian-config PATH]

python3 infra/install.py --uninstall --root PATH [--settings PATH] [--yes]
                        [--profile PATH] [--obsidian-config PATH]

python3 infra/install.py --<agent> <adapter-args...>
```

| Flag | Meaning | Default |
|---|---|---|
| `--root PATH` | Explicit team root. Normalized with `Path(PATH).resolve()`. | If omitted, cwd is used only when it has a team marker (`.git`/`team.config.json`/`memory`); otherwise exit 2 |
| `--agent VALUE` | Stored in `Options.agent`. | `auto` |
| `--member-name NAME` | Session log author/member name. `opts.member_name` takes priority; otherwise use a suggestion from `git config user.name` after keeping only Unicode alphanumerics and lowercasing. Final validation uses `_validate_author` rules (Unicode `isalnum()` + `-`/`_`, first character alphanumeric). | None if suggestion fails → exit 3 before scaffold |
| `--role ROLE` | Optional role string to place in the user's own entry in `team.config.json.members`. This is not the role classification value from members.md. | If `None`, omit/remove the config member role key |
| `--settings DIR/PATH` | In bootstrap, interpreted as an isolated directory for agent settings, MCP, and skills instead of the real host (verify only calls `context` — it does not create a settings file). In `--uninstall`, the same value is not interpreted as a directory and is used verbatim as a Claude `settings.json` file path string. | Omitted |
| `--yes` | Consent to proceed through real-host wiring and env injection without `--settings`. | off |
| `--update` | Only parsed as `Options.update=True`. | **Not used by current bootstrap** |
| `--dry-run` | Print only the plan without changes. No contact with settings, memory, or env. | off |
| `--register-obsidian` | Standalone Obsidian vault registration action independent of bootstrap. If this flag is present, bootstrap is not run. | off |
| `--obsidian-config PATH` | Override obsidian.json path. If omitted, use platform default. | Platform default |
| `--uninstall` | Host rollback action captured before bootstrap/dispatch. | off |
| `--profile PATH` | POSIX profile target for env removal in `--uninstall`. | If omitted, POSIX `~/.bashrc`; no file on Windows |

Implementation notes:

- `parse_args()` is hand-parsed. Tokens other than the flags listed above are silently ignored.
- `--agent` is currently not used as a wiring filter in bootstrap. Actual wiring targets are the full agent list detected by `_detect()`. Therefore passing `--agent codex` is not reflected in the current code's `wire_agents()` call.
- If `--register-obsidian` is present, bootstrap-related flags such as `--dry-run`, `--yes`, and `--member-name` have no meaning.

**Exit codes**: `0` success or nonfatal skip / `2` precondition or argument error / `3` name resolution failure, name conflict, partial wire failure, or verify failure. For `--register-obsidian`, only root errors exit 2; registration failures such as Obsidian missing/broken/duplicate are nonfatal `0`.

### 4.2 Entry Branching and Dispatcher

The branch order in `main(argv)` is fixed.

1. Call `ensure_utf8_io()` first to adjust stdout/stderr to UTF-8. stdin is not adjusted.
2. If argv contains `--uninstall`, parse with `_parse_uninstall()` and go to `cmd_uninstall()`. No other bootstrap/dispatch branch is considered.
3. `_split_agent(argv)` takes the first discovered `--<agent>` whose `infra/agents/<agent>/` directory exists as the agent. It removes only that flag and passes the rest of argv to the adapter.
4. If there is an agent, run `_dispatch(agent, rest)`:
   - If `agents/<agent>/adapter.py` is not a file, exit 2.
   - The gate is **agent-aware**: the isolated-intent flag is that agent's own settings flag (`install_lib._AGENT_WIRE[agent]["flag"]` — claude=`--settings`, codex=`--config`), and `--install` is always treated as real-install intent. If neither exists, exit 2 — do not write to real host settings without explicit intent. Therefore `python3 infra/install.py --codex --config <path> sync` reaches the adapter normally.
   - Missing values for the isolation flag (no next token, or the next token is a `--option`/verb) are a clear exit 2 (prevents accidents such as `--config sync` being consumed as a value).
   - For claude dispatch, `--config` is the team config flag and is not accepted as isolation intent. Agents not registered in `_AGENT_WIRE` conservatively fall back to accepting only `--settings`/`--install`.
   - `--install` is a dispatcher-only flag and is removed from adapter argv. `--root <value>` is **translated** to adapter `--team-root` (no silent removal). If `--root` and `--team-root` are both present with different values, exit 2 for ambiguity; if the values are the same, pass through harmlessly.
   - Set `sys.argv = [adapter_path] + rest`, load adapter.py with `runpy.run_path()`, and call `main(rest)`. Return its rc as-is.
5. If there is no agent but argv contains a `sync` or `uninstall` token, print the "specify an agent: --<agent>" error plus a list of available agent directories, and exit 2.
6. Otherwise parse bootstrap arguments.
7. If the parse result has `register_obsidian=True`, run the standalone `register_obsidian()` action and exit.
8. Otherwise run `bootstrap()`.

`_parse_uninstall()` for `--uninstall` consumes values only for `--root`, `--settings`, `--profile`, and `--obsidian-config`, and recognizes only `--yes` as a bool. Unknown boolean flags are ignored. If a value flag has no value after it, `None` may be inserted.

### 4.3 Preconditions and Detect

The preflight function is `install_lib.preflight(team_root, python_version, git_present, remote_authed)`, and callers inject the input values.

| Precondition | Implementation | Failure |
|---|---|---|
| Python | `python_version >= MIN_PYTHON`, currently `MIN_PYTHON = (3, 9)` | `PreflightResult(ok=False, exit_code=2, message=...)` → bootstrap exit 2 |
| git binary | In bootstrap, `shutil.which("git") is not None` | exit 2 |
| Team root marker | `has_team_marker(team_root)`: one of `.git`, `team.config.json`, `memory` exists | exit 2 |
| Remote auth | Current bootstrap passes `remote_authed=True` as the preflight argument. Actual auth check is a separate warning after detect | Do not exit only because auth failed |

Root resolution:

- If `--root` is present, use `Path(opts.root).resolve()`. Existence is not checked separately; it may fail during preflight's marker check.
- If `--root` is absent, use cwd only when it has a team marker.
- If neither applies, bootstrap/register-obsidian prints `[error] --root <팀루트> 가 필요합니다... 환경변수(TEAMMODE_HOME)는 읽지 않습니다.` and exits 2.

The detect function `_detect(team_root, home)` is read-only.

| Key | Value |
|---|---|
| `remote_url` | `git remote get-url origin`; `None` on failure/timeout/missing git |
| `team_name_default` | Last remote URL segment with `.git` removed. `None` if no remote |
| `git_user_name` | `git config user.name`; `None` on failure |
| `git_user_email` | `git config user.email`; `None` on failure; used in members.md identity comments |
| `member_name_suggestion` | git user.name lowercased and stripped with `[^a-z0-9]`. `None` if empty |
| `agents` | Checks for `home/.claude` and `home/.codex` directories and returns a sorted subset of `["claude", "codex"]` |
| `remote_authed` | true if a remote exists and `git ls-remote --exit-code origin HEAD` succeeds within 5 seconds |
| `role` | Result of `detect_role(team_root)` |

`_git()` runs with `GIT_TERMINAL_PROMPT=0`, timeout 5 seconds, and capture_output. Failure, timeout, missing git, or nonzero rc returns `None`. Remote auth failure only prints `[warn] git 원격 인증 미확인...` in bootstrap and continues.

### 4.4 Procedure (Idempotent, In Order)

```
① preflight   Python 버전·git 바이너리·팀 표식 검사. 실패 즉시 exit 2.
② detect      git remote→org/repo, git user.name→이름 제안, user.email→identity,
              설치 에이전트(~/.claude·~/.codex), 원격 인증, role(③)
③ role        team.config.json 유효(spec_version + team.name 비-placeholder) → 팀원(§4.5)
              부재/미초기화 → 도입자(§4.5). ※ services 채움 여부로 가르지 않음(빈 슬롯 정상)
④ plan        team_root, role, agents, member_name 출력. --dry-run이면 여기서 exit 0.
⑤ scaffold    memory/ 구조·INDEX·members.md 등재·banner 선기록, 도입자는 최소 config.
              첫 세션로그는 쓰지 않는다(디렉토리만).
⑥ wire        감지 에이전트마다 install-mcp → sync --on → install-skills.
              실호스트 배선은 --yes 또는 --settings 필요.
⑦ env         런타임 훅용 TEAMMODE_HOME 영구 주입 — POSIX:셸 프로파일 1줄 / Windows:setx (§4.8)
⑧ verify      context --json 으로 설치 확인(데이터 읽힘) — 팀모드 on 은 호출하지 않는다
              (설치 ≠ 활성화 — on 의 auto_update 부작용 회피). 활성화는 사용자가 `tm on` 할 때만.
              ※ 실제 *맥락 주입*은 여기가 아니라 다음 세션의 SessionStart 훅(§1.6·§2.4)
```

Detailed invariants:

- `--dry-run` exits 0 immediately after detect and plan output. It is before scaffold, so it does not touch memory/settings/env/verify.
- Member name is `opts.member_name or det["member_name_suggestion"]`. If both are missing, exit 3 before scaffold.
- `team_name_default` is the remote repo name when available, otherwise `team_root.name`.
- The default `shell="__env__"` is used only to detect bash/zsh/fish from `os.environ["SHELL"]`. Env is not used to determine the team root.
- If `platform=None`, use `sys.platform` to branch Windows/POSIX env handling.
- verify only calls `_engine_capture(["context", "--root", team_root, "--json"])` (**does not use `on`** — avoids the side effect where `cmd_on`'s `auto_update_on_start` leaves automatic commits in the team repo). A nonzero `context` rc or stdout JSON parse failure exits 3. It does not create settings or an active marker and does not turn team mode on (install ≠ activation).
- `_engine_capture()` limits subprocess env to `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TZ`, `PYTHONPATH`, `TERM`, `XDG_STATE_HOME`. Ambient `TEAMMODE_HOME`/`LEGACY_TOOL_HOME` is not passed through.

### 4.5 Introducer/Member Role Branching

**Role classification (closed — `config_is_valid`)**: If `team.config.json` is a dict and has `spec_version` (truthy) + `team.name` (str, not a placeholder), classify as **member**; otherwise **introducer**. placeholder = `{"", "changeme", "todo", "your-team-name", "team-name", "tbd", "placeholder"}`. Do not classify based on whether services are filled.

`load_config()` treats a missing `team.config.json`, JSON parse failure, and read failure all as `None`. The `providers_dir` argument is accepted for `config_is_valid()` signature compatibility but is not used for role classification. Missing provider packs, services schema violations, and members schema violations do not flip the introducer/member classification.

Scaffold targets:

- Common directories: `memory/team`, `memory/team/decisions/archive`, `memory/team/meeting/summary`, `memory/team/meeting/raw`, `memory/team/sessions/<member_name>`.
- Common files: `memory/INDEX.md` and `memory/team/decisions/current.md` are written only when absent.
- `memory/team/members.md` creates a header if absent, then appends the member line.
- `memory/banner.txt` is written only when absent as `=== <team_name> ===\n`.
- The first session log file is not created.

Introducer path:

- Call `write_introducer_config()` only when `role == "introducer"`.
- If `config_is_valid(load_config(team_root))` is already true, do not overwrite config.
- The new config is written in the following shape.
  ```json
  {
    "spec_version": "0.4",
    "team": {
      "name": "<team_name>",
      "timezone": "Asia/Seoul",
      "locale": "ko_KR",
      "greeting": "<team_name> 팀모드 ON",
      "farewell": "수고하셨습니다 — <team_name>"
    },
    "admin_contact": "<member_name>",
    "members_file": "memory/team/members.md",
    "banner_file": "memory/banner.txt",
    "services": {}
  }
  ```
- If `timezone`/`locale` arguments are truthy, those values are used, but current `_detect()` does not create these keys, so the bootstrap path uses the defaults `Asia/Seoul`, `ko_KR`.

Member path:

- If `role == "member"`, do not write config team/services/admin fields.
- However, the current implementation calls `upsert_member_role()` for both introducer and member, so it can add/update **only the user's own name entry** in `team.config.json.members`. This is broader than the older "members only read config" contract.
- If `members` is absent, treat it as `[]` and append the user's entry. If `members` is not a list or config is missing/broken, no-op.
- If an entry with the same name exists, preserve existing extra keys and update only `name`/`role`. If `--role` is absent or an empty string, remove the `role` key from the existing user entry.
- Entries for other people are not touched in order or content.

members/services schema validation:

- `services_are_valid()` is not used for role classification. Canonical role keys are `issues`, `chat`, `docs`, `calendar`; each slot must be an object, provider is required, provider pack must exist, scope if present must be `team|personal`, and every `resource_fields` entry in the provider pack must be a non-empty string. Extra keys are allowed. If a provider file exists but has invalid JSON/schema, `ProviderValidationError` may be propagated.
- `members_are_valid()` is also not used for role classification. After scaffold, bootstrap only prints a warning if config is a dict and members is invalid. A members entry is `{name required, role optional, extra keys allowed}`; name uses the engine `_validate_author`, and role is a non-empty string that forbids newlines/control characters.

**Name conflict policy (closed — M4 · deterministic):**
- Same English name + same (or unknown) identity → do not add; treat as the user's own entry (idempotent). Reinstall by the same person/on another machine = normal.
- Same name + **different identity** (both identifiers exist) → **exit 3** (a human resolves it; do not guess "me or someone else"). reference: `register_member` raises `ConflictError`.
- Unknown identity (legacy entry or not injected) is not treated as a conflict (idempotent).
- To use a different name, override with `--member-name`. Invalid names (traversal, leading dash, etc.) raise `InvalidNameError` → exit 3. Name validation reuses the engine `_validate_author` as the single source.
- identity is detected from `git config user.email`. If absent, no id comment is attached to the members.md line.
- members.md line format is `- <name>  <!-- id: <identity> -->` or `- <name>`. The parser looks only at lines starting with `- ` and treats the first token before the id comment as the name.

### 4.6 Agent Settings Write Boundary

- Real-host writes during wire (`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.claude.json`, skills dirs, etc.) are a normal install operation. verify only calls `context`, so it does not write settings or markers. It still never writes without explicit intent.
- After completing scaffold in bootstrap, if `opts.settings is None and not opts.yes`, print `[wire] 건너뜀...` and exit 0. In that case env injection and verify are also not run.
- Passing `--settings <DIR>` → isolated mode. Agent settings/config, Claude MCP file, and skills dir go under DIR. Real env is not touched.
- Omitting `--settings` + passing `--yes` → real-host mode. Wire to default home-based paths and inject env.
- If `--settings` and `--yes` are passed together, isolated mode wins for env purposes. settings go under the isolated directory and real env is skipped.
- Dispatch mode (`--<agent> sync`) uses the same gate spirit (agent-aware): if that agent's settings flag (claude=`--settings`, codex=`--config`) or `--install` is absent, exit 2. In codex dispatch, `--settings` is not accepted as isolation intent (not an option in the codex adapter CLI — the gate points to `--config`). `--install` is dispatcher-only (not forwarded to the adapter).
- `--dry-run` does not touch settings, memory, or env, and prints only the plan.
- install/on/off do not read ambient `TEAMMODE_HOME` even if it points at the real host (§1.2 P1).
- **Trust boundary — root injection by skins**: If an agent calls install.py from "셋업해줘" with the wrong root injected, an accident can be reproduced → skin root resolution logic is a test target (required). Prompt-injection caution: do not recommend a habit of blindly following an **arbitrary repo** README (non-normative). However, the **"For AI agents" section in the product's official README** (§4 entry ⓪) is a normative entry point with deterministic procedure + dry-run approval gate, and is not the target of this warning (2026-07-06 entry contract update).

### 4.7 Agent Wiring (wire)

`wire_agents(agents, home, settings_override, run_adapter, team_root)` calls adapters for each detected agent. `run_adapter` is a required injected callable; if absent, raise `ValueError`. This section describes only the wire step. Bootstrap verify calls only `context` regardless of this detected list (`on` is not used).

> **install-mcp is a registrar verb (Option A, §internals 2.8).** It only **registers the connected provider's vendor MCP alias in the agent settings**; it does not wrap actions or relay through `role_server` — the AI directly calls the registered MCP tools. install allows empty slots (§4.0 "Delegate judgment"); the connect layer (`tm-connect`, §skills 5.4) fills which provider goes into which slot, official MCP preparation, and config push. install only wires the alias declared in that config.

Supported agents and paths:

| agent | sync settings flag/path | team config flag | MCP path | skills path |
|---|---|---|---|---|
| `claude` | `--settings`; real host `home/.claude/settings.json`; isolated `DIR/claude/settings.json` | `--config <team_root>/team.config.json` | isolated `--mcp-config DIR/claude/.claude.json`; adapter default `~/.claude.json` on real host | `--skills-dir`; real host `home/.claude/skills`; isolated `DIR/claude/skills` |
| `codex` | `--config`; real host `home/.codex/config.toml`; isolated `DIR/codex/config.toml` | `--team-config <team_root>/team.config.json` | No separate flag. Uses blocks inside config.toml | `--skills-dir`; real host `home/.codex/skills`; isolated `DIR/codex/skills` |

Call order and failure branches:

1. If the agent name is unsupported, add `(agent, "지원하지 않는 에이전트")` to failed and continue.
2. `install-mcp`: `run_adapter(agent, "install-mcp", settings_flag, settings_path, cfg_extra + mcp_extra)`.
   - If rc is nonzero, aggregate that agent failure as `install-mcp rc=<rc>` and skip sync/install-skills.
   - On success, print `[wire] <agent> MCP 등록 동기화 완료`.
3. `sync --on`: pass the same settings path, team config extra, and claude isolated MCP extra again.
   - `_make_run_adapter()` builds argv as `[global_flags..., "sync", "--on"]` when the verb is `sync`.
   - If rc is nonzero, aggregate that agent failure as `sync rc=<rc>` and skip install-skills.
   - On success, print `[wire] <agent> 훅 동기화 완료 → <path>`.
4. `install-skills`: pass `cfg_extra + [skills_flag, skills_path]`.
   - If rc is 0, add the agent to wired and print `[wire] <agent> 스킬 심링크 완료 → <skills_path>`.
   - If rc is nonzero, aggregate that agent failure as `install-skills rc=<rc>`.
5. Even if an adapter call throws an exception, aggregate only that agent as failed and continue with other agents.
6. If anything failed, `WireResult(ok=False, exit_code=3)`. Successful parts are not rolled back.

`_make_run_adapter()` loads adapter.py with `runpy.run_path()` each time and calls `main(argv)`. It pre-creates the parent directory of the settings path. extra_args are placed as global flags before the subcommand. `install-mcp`/`install-skills` are `[global_flags..., verb]`; `sync` is `[global_flags..., "sync", "--on"]`.

### 4.8 Environment Variable Injection (§9)

- The variable name is `TEAMMODE_HOME`. Intentional calls such as install/on/off do not trust this env; it is used only by runtime hooks to find the team root.
- In isolated mode (`--settings`), always skip env injection and print manual setup guidance only.
- On Windows (`sys.platform` starts with `win` or `cygwin`), do not write shell profiles; run `setx TEAMMODE_HOME <abs team_root>`. On success, the profile display is `HKCU\Environment\TEAMMODE_HOME`. setx exception or nonzero rc is nonfatal `injected=False`.
- POSIX writes a marker line to a shell-specific profile.
  - bash: `<home>/.bashrc`, `export TEAMMODE_HOME="<team_root>"  # teammode (env injection, §9)`
  - zsh: `<home>/.zshrc`, same export format
  - fish: `<home>/.config/fish/config.fish`, `set -gx TEAMMODE_HOME "<team_root>"  # teammode (env injection, §9)`
- Shell detection checks whether the basename of the `$SHELL` path contains `bash`/`zsh`/`fish`. If `shell` is already a kind string, as in test injection, use it as-is.
- If the profile is absent, create the parent directory and write a new file.
- If there is exactly one existing marker line and its content is identical, do not change it.
- If existing marker lines are present but the value differs, or there are multiple markers, remove all existing marker lines and append exactly one new line.
- Unsupported/undetected shells only print nonfatal guidance.

env removal (`remove_injected_env`) is used only in uninstall. Windows runs `reg delete HKCU\Environment /v TEAMMODE_HOME /f`; POSIX deletes only lines containing the `# teammode (env injection` prefix. Missing file, missing marker, write failure, and reg failure all return `False` and do not raise.

### 4.9 Obsidian Vault Registration (`--register-obsidian`, opt-in)

The "Obsidian view" from 05 draft has been implemented in reference as a **standalone opt-in action** (separate from bootstrap and runnable anytime after onboarding). Except for root resolution errors, it is **nonfatal — registration failure also exits 0**.

- Turn memory/ into a vault (create `.obsidian/` if absent — core: graph/backlink/global-search, community: dataview; even if write fails, an empty `.obsidian/` is nonfatal).
- obsidian.json path: `--obsidian-config` takes priority; if omitted, use platform defaults (linux `~/.config/obsidian/obsidian.json`, mac `~/Library/Application Support/obsidian/obsidian.json`, win `<home>/AppData/Roaming/obsidian/obsidian.json`; the pure helper can inject `appdata`). path/id/ts are injectable.
- If `register_obsidian()` is called without injected `now_ms`/`vault_id`, generate them with `time.time()` and `os.urandom(8).hex()`. Therefore even with the same input and same file state, a new registration entry can have different `ts`/id on each run. If the same path is already registered, it is skipped and the result is idempotent.
- **Merge registration (clobber 0)**: preserve all existing vaults and add only the new entry. Entry = `{"<16hex id>": {"path": <memory absolute path>, "ts": <ms>, "open": false}}`. Atomic write (temp+os.replace; if symlink, replace the real target and preserve the link).
- **Safe skip (nonfatal)**: no settings directory (Obsidian not installed), obsidian.json parse failure, top-level not an object, vaults not a dict, same path already registered (idempotent), vault_id collision → do not register and return `registered=False`. No error raises.
- `register_obsidian()` itself must resolve root, so if there is no root and cwd has no team marker, exit 2. Other registration failures only print a message and exit 0.

### 4.10 Host Rollback (`--uninstall`)

`cmd_uninstall()` is a separate branch that rolls back, in reverse order, the host traces install added: off, Claude hook, env, and Obsidian registration. It never deletes memory/ team data. MCP registration and skills install traces are not reclaimed in the current implementation.

Required gates:

- If `--root` is absent, exit 2.
- If neither `--settings` nor `--yes` is present, exit 2. Do not roll back real `~/.claude` without explicit intent.
- settings path is used verbatim as a file path string when `--settings` is present; otherwise it is `~/.claude/settings.json`. This differs from bootstrap's `--settings <isolated directory>` meaning, so passing a directory to uninstall treats that directory itself like the settings file.

Steps and failure policy:

1. Load `teammode.py` with `runpy.run_path()` and call `cmd_off(team_root, settings_path)`. If `.teammode-active` existed and is gone after the call, add active marker to the removed list. Exceptions warn and continue.
2. Load the Claude adapter and call `Adapter(...).uninstall()`. If there is a `[remove]` message, record settings hook removal. Exceptions warn and continue.
3. env removal:
   - If `--settings` exists and `--profile` is absent, skip real env removal by the isolation symmetry principle.
   - Otherwise POSIX removes only marker lines from `--profile` or default `~/.bashrc`.
   - Windows attempts `reg delete` regardless of profile arguments.
   - Exceptions warn and continue.
4. Obsidian deregistration: from `--obsidian-config` or platform default obsidian.json, remove only vault entries whose path matches `team_root/memory`. Exceptions warn and continue.
5. If any items were removed, print the list; otherwise print "되돌릴 호스트 변경 없음". Always print the memory preservation message.

In the current implementation, uninstall calls `Adapter(...).uninstall()` and `uninstall_skills()` for **both** claude and codex (trace-0 symmetry, #4). claude `uninstall()` removes only the `settings.json` hook; codex `uninstall()` removes both hook blocks (`teammode-hooks-*`) and MCP blocks (`teammode-mcp-*`) from `config.toml` (codex stores hooks and MCP in one file). The codex path is derived as `agent_settings_path` from the grandparent of the claude `settings.json` path (isolated root). However, the reverse operation for Claude `.claude.json` MCP registration (`install-mcp`) is still not called (Claude MCP/statusLine cleanup is follow-up work).

### 4.11 Acceptance Criteria (Golden — §6 Scenario Candidates)

| # | Scenario | Acceptance |
|---|---|---|
| I1 | `--yes` or `--settings` install in an empty/engine-only repo (no config) | Completes introducer path → memory/·minimal config (`spec_version: "0.4"`, empty services)·sessions/·members·banner·wire·env (real install only)·verify. First log not created |
| I1b | install in an empty/engine-only repo without `--yes`/`--settings` | Completes scaffold, skips wire/env/verify, and exits 0 |
| I2 | install in a repo with valid config | member path → preserve team/services/admin fields, register in members.md, upsert only own config.members entry, wire. context reads existing logs |
| I2b | New session immediately after I1/I2 | SessionStart hook **actually injects** context (here, not during install) |
| I3 | Rerun immediately after I1 | Idempotent — zero duplicates, no changes |
| I4 | Run with ambient `TEAMMODE_HOME`=real host set | No real host contact (env isolation) |
| I4b | Bootstrap with `--settings <격리디렉토리>` | Real `~/.claude/settings.json` **and real host env (POSIX shell profiles / Windows registry) untouched**, only paths under isolated directory. In bootstrap, `--settings` is the authority for env isolation (isolation wins even with `--yes`). Real env injection happens only with `--yes` and no `--settings` |
| I-win | Round trip with nt mocking (`platform=win32` + injected setx/reg runner) | install→on→context→uninstall: env uses setx/reg delete (no shell profile contact), active marker and memory normal, real setx/reg not run. (Native environment recommended for real Windows verification) |
| I-dry | `--dry-run` | settings·memory·env untouched + plan only |
| I5 | Expect single-agent wiring from only `--agent codex` | Current implementation does not use `--agent` as a filter, so it wires all detected agents. Single-agent filter remains a gap |
| I6 / I6b | Python below minimum/git binary absent / only remote auth absent | I6: exit 2 unchanged / I6b: warn and proceed with local L1 |
| I7 / I8 | Reinstall with same name / `--member-name` collides with another person's registered name | I7: idempotent own entry / I8: exit 3 + members.md unchanged |
| I9 | Obsidian missing/broken obsidian.json with `--register-obsidian` | If root is valid, nonfatal skip + exit 0 |
| I10 | `--uninstall --root <r> --settings <settings-file-path>` | Try off, Claude hook removal, and matching Obsidian vault removal; skip real env removal if no `--profile`; preserve memory. MCP/skills traces are currently not removed on this path |

---
