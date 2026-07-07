# Internal Rules

tm-mode SPEC v0.3 — Engine and Standards Rules

## §1. Team Memory Standard (Team Memory)

Data standard for team memory: directory structure, session log format, and context injection rules. Any agent that reads and writes according to this standard shares the same team memory, making it the foundation for cross-agent compatibility.

### 1.1 Directory Structure

Team memory lives under `memory/` at the team root. This location is **required**.

```
memory/
├── INDEX.md                      # 메모리 인덱스 — 세션 시작 시 주입되는 단일 진입점
├── banner.txt                    # 팀 배너 캐시 (권장 표준 위치 — 엔진·install이 실사용)
└── team/
    ├── members.md                # 멤버 명부 — 이름/identity 등재. 역할은 config.members가 단일 소스
    ├── sessions/<이름>/          # 멤버별 세션로그(§1.3). <이름>=members.md의 영문 이름
    ├── decisions/                # 확정된 결정사항
    │   ├── current.md            #   활성 결정
    │   └── archive/              #   과거 결정
    └── meeting/
        ├── summary/              # 회의록 요약본
        └── raw/                  # 회의 원본 (STT·텍스트)
```

- **INDEX.md (required)** — Maintain a table that explains what belongs in each folder. When a new folder is created, INDEX.md must be updated. Folders that are not listed are omitted from injection and discovery. (For the default INDEX.md table scaffolded by install.py, see `install_lib._INDEX_MD`.)
- **members.md (required)** — Member names are the names listed in this file, not system account names such as `$USER`. Code, hooks, and skills must reference this file instead of hard-coding names (required). The single source of truth for reference validation is `teammode._validate_author`, and the actual allowed rules are: no empty string, no `/` or `\`, no `.` or `..`, no absolute paths, the first character must be Unicode `isalnum()`, and every character must be Unicode `isalnum()` or `-` or `_`. In other words, uppercase letters, Unicode alphanumerics, and underscores are allowed by the implementation; names are not restricted to ASCII lowercase. Member entry line format (reference implementation, aligned with §4.4): `- <name>  <!-- id: <identity> -->`. The `id` comment is used by install.py to deterministically distinguish the same person from another person (§4.4 conflict policy), and compatibility is preserved even without it. Member **roles** belong in the `members` array of `team.config.json`, not in members.md (L2-A2, team decision 2026-06-16): `members: [{name, role?}]` — during install, each member only upserts their own `name` entry (self upsert) and leaves other members' entries untouched. `role` may be recommended vocabulary (developer/pm/designer/...) or a free-form string, and may be omitted. An empty array or missing `members` key is also valid (no regression for existing config). ⚠️ The `members` block is completely separate from role **classification** (`config_is_valid`) — schema violations only emit `[warn]` and do not change adopter/team-member classification. The current scaffold text in `install_lib._INDEX_MD`/`_MEMBERS_HEADER` still contains older wording that members.md is the single source for roles and contact details and that names are lowercase, but the single source of truth for code behavior is the rule above. The detailed format for contact fields in members.md remains reserved in 0.2.
- **sessions/<name>/** — Auxiliary files may be placed here in addition to session logs (`YYYY-MM-DD.md`) (not subject to injection or checks). However, **`.md` filenames starting with `YYYY-MM-DD` are reserved for the session log namespace** and cannot be used for auxiliary files (split files such as `-late` are subject to §1.3 violation checks). Namespace determination: stem length ≥10, `stem[:4]` numeric, `stem[4]=='-'`, `stem[7]=='-'` (reference: `teammode._is_session_log_name`).
- **decisions/** — Only "finalized" decisions. Matters still under discussion stay in session logs or meeting notes.

**Recommended and reserved items:**

| Path | Classification | Content |
|---|---|---|
| `team/reviews/` | Recommended | External evaluations and feedback. Filename `YYYY-MM-DD-출처-단계.md` |
| `team/ground-rules.md` | Recommended | Team operating ground rules (the standard location referenced by the engine/digest) |
| `banner.txt` | Recommended | Standard location for the team banner cache. Pointed to by `banner_file` in `team.config.json` (reference: `memory/banner.txt`) |

**Team extensions (free-form)**: Free-form folders may be added under `memory/` (for example, `product/`, `fundraise/`). There are two rules — ① do not create a new folder if an existing folder is sufficient (recommended, prevents proliferation), ② list any new folder in INDEX.md (required). Listing/removal is handled by the reference verb `teammode.py memory route {upsert|remove}` (`--root --path --desc --author`), and when `memory write` detects an unlisted top-level folder, it prints one `[hint]` line to stdout pointing to this verb (not automatic listing — a human confirms the one-line description).

### 1.2 Write Location, Team Root, and env Rules (Required)

- **Write location**: Team memory writes always go to **`memory/` at the team root**. Writing to an incidental `./memory/` inside the code repo currently being worked on is prohibited. Implementations are encouraged to prevent confusion with session reminders and similar mechanisms (reference: `session-log-remind.py`).
- **Team root for runtime hooks = environment variable**: Runtime hooks are triggered by the agent harness, so there is no argument path such as `--root`. Therefore, implementations **must provide an environment variable for runtime hooks to reference (required)**. Reference variable name: **`TEAMMODE_HOME`** (fallback to cwd if absent). The hooks that read env directly in the reference are `session-start.py`, `session-log-remind.py`, `auto-commit.py`, and `confirm-action.py`. `auto_pull.py` does not read env; it is a helper that receives `team_root` from the caller.
- **Team root for engine/adapter/install = explicit arguments only (required)**: Intentionally invoked verbs such as on/off, log, context, pull, commit, update, and install receive the team root **only through an explicit argument (`--root`)**. They must not fall back to environment variables or infer cwd; if `--root` is absent (for install, if there is no marker after cwd marker validation), they **must not run and must exit with an error** (exit 2). Rationale: real P0/P1 incidents occurred when ambient env (for example, `LEGACY_TOOL_HOME` pointing at the host toolkit) leaked in, and direct calls outside the isolated harness touched host state markers (such as `.teammode-active`) and `memory/banner.txt`. The fundamental fix is to keep the engine from guessing "which folder to touch."
- **Agent settings paths are also explicit only (required)**: Real user settings such as `~/.claude/settings.json` are not touched without an **explicit path (`--settings`) or explicit install flag (`--install`/`--yes`)** (§3.1, §4.5).

### 1.3 Session Logs — Location, Unit, and Detail Level (Required)

- Path: `memory/team/sessions/<이름>/YYYY-MM-DD.md`. `<이름>` is the English name from members.md.
- **One file per day.** Logs for the same workday are appended to the existing file. Creating split files such as `-late` or `-2` is prohibited.
- `YYYY-MM-DD` is the workday (§1.4) and must match the frontmatter `date`.
- **Detail-level standard (required)**: A session log is not a "list of things done." The single standard is to **fully reconstruct the context later, even when read after the fact**. Each entry should flow through: ① what was done ② why that decision was made ③ alternatives that were dropped ④ blockers ⑤ next steps.
- **Team work only (required).** Exclude personal schedules and private content.
- Multiple sessions on the same day are appended with time dividers (for example, `## 14:30`) (recommended). The reference engine `log` automatically inserts a `## HH:MM` (KST) divider on append.
- Implementations are encouraged to provide in-session reminders when logs have not been updated for a certain amount of time (reference: 30 minutes) — the core design is for hooks, not human habit, to enforce the discipline (reference: `session-log-remind.py`, age≥1800 seconds or every 5 prompts).

### 1.4 06:00 Cutoff (Required)

The workday boundary is **06:00 KST**, not midnight. The single source of truth in the reference implementation is `infra/workday.py`, and the current code does not read the timezone from `team.config.json`. `KST = timezone(timedelta(hours=9))` and `CUT_HOUR = 6` are fixed constants.

- `workday(now: datetime) -> datetime`: Normalizes input `now` to workday midnight `datetime(..., tzinfo=KST)`.
  - If `now.tzinfo is None`, treats the input time as a **KST naive time** and applies `now.replace(tzinfo=KST)`.
  - If it is an aware datetime, first converts it with `now.astimezone(KST)`.
  - After conversion to KST, if `now.hour < 6`, applies `now - timedelta(days=1)`.
  - The return value is a `00:00:00 KST` datetime for the adjusted date. Minutes, seconds, and microseconds are not preserved in the return value.
- `workday_str(now: datetime) -> str`: Performs only `workday(now).strftime("%Y-%m-%d")`. This is the reference value for the session log filename and frontmatter `date`.
- `now_kst() -> datetime`: The current time used for CLI defaults. It returns `datetime.now(KST)`. For tests and reproduction, inject `teammode.py log --now <ISO8601>` instead of using wall-clock time.
- Boundary conditions:
  - Logs started from KST `00:00:00` through `05:59:59.999999` belong to the **previous day** workday.
  - From KST `06:00:00`, logs belong to the **current day** workday. In other words, the only cutoff is between `05:59` and `06:00`.
  - Month and year boundaries follow the same rule. Example: `2026-07-01T05:59:00+09:00` → `2026-06-30`, `2026-07-01T06:00:00+09:00` → `2026-07-01`.
  - Aware input is converted to KST first and then evaluated. Example: `2026-06-15T20:59:00+00:00` is KST `2026-06-16 05:59`, so it becomes `2026-06-15`; `2026-06-15T21:00:00+00:00` is KST `2026-06-16 06:00`, so it becomes `2026-06-16`.
- **Decision time = the time log writing starts**. In the reference CLI, the single `now` passed into `cmd_log(..., now)` is used to calculate the filename, frontmatter `date`, and entry time labels. Even if the same `teammode.py log` call crosses 06:00 while running, it is not re-evaluated mid-call.
- `teammode.py log --now` is parsed with `datetime.fromisoformat()`. Parse failure or omission is not an error; it quietly falls back to `now_kst()` (§3.2). The workday calculation itself follows the `workday_str(now)` rule above.
- Implementation note: `workday()` treats naive datetimes as KST, but the entry label (`## HH:MM`) in `teammode.py log` is currently built directly with `now.astimezone(KST)`. Python's naive `astimezone()` uses the executing host's local timezone interpretation. If the reference execution environment is KST, the workday and label match; on a non-KST host, passing a naive `--now` can make the workday decision (treated as KST) differ from the label display (interpreted as host-local and then converted to KST). For reproducible tests and operational input, ISO8601 with an offset is recommended.

### 1.5 frontmatter (Required)

```markdown
---
author: bob
date: 2026-06-11
summary: 훅 어댑터 레이어 설계 확정 — events.json 번역표, 폴백 정책, 서비스 추상화까지
---
```

| Field | Required | Definition |
|---|---|---|
| `author` | Required | English name from members.md. System account names such as `$USER` are prohibited |
| `date` | Required | Workday (06:00 cutoff applied). Must match the filename |
| `summary` | Required | **One-line** summary of that day's work (recommended within 100 characters) |

- The three fields above are the required minimum set. Teams and implementations may add additional fields.
- **`summary`** — Read by scaled injection (§1.6), context collection, and dashboards (roadmap) as a shared field. If the content changes during the day, **replace** it with the representative content (do not append). The reference engine `log` **initializes** summary from the first line (100 characters) of `--text` on the first record, but does not decide whether to update (replace) it — updates are the responsibility of skills/people (engine philosophy: mechanical material preparation, no summarization/judgment).
- **Migration**: Older logs without `summary` are not treated as non-compliant. It is required starting with newly written logs.

### 1.6 Injection Rules (Scale)

At session start, implementations inject team memory into context. Injecting every member's full text causes context to grow in proportion to headcount, so injection scales by team size.

| Team Size | Session Log Injection Method |
|---|---|
| **~4 people** | Inject every member's latest log **in full** |
| **5+ people** | One `summary` line for everyone + **own** log in full + teammate details via **lazy load** |

- **Team size** = number of members listed in members.md.
- **Default injection unit** = **the single most recent workday file per member** (whether full text or summary). Implementations may provide a wider range (recent N days), but the default is 1 file.
- If the target file has no `summary` (old log), **omit summary injection** for that member — full-text fallback injection is prohibited (the purpose is to prevent context blowup).
- reference: `teammode._collect_members` collects `{author, date, summary, file}` from each member's latest 1 file (no summarization). The `session-start.py` hook injects INDEX + per-member summary lines as `additionalContext`. ⚠️ **The current reference does not implement the team-size branch (~4 people full text / 5+ summary) and always uses summary-line-based injection** — see Appendix A. In 0.2, the scaling rule is not subject to automated conformance checks (outside the §6.4 K-list), and the injection method is checked through the golden scenario "context lookup."
- The `groups` key in `team.config.json` is a **0.2 reserved word** (for squad-level injection scope). Implementations must ignore it and must not assign meaning to values other than `null`.
- **Scope of applicability**: The target team size for 0.2 is **2-5 people**. This specification does not guarantee behavior for 7 or more people.

---

## §2. Hook · Adapter Standard (Hook & Adapter)

> Keep only **one copy of the content** for hooks, skills, and MCP, and let the `agents/<name>/` adapter **translate notation, registration methods, and input schemas that differ by agent**. The design goal is to attach a new agent by adding only an adapter, but the current reference has detection and wiring paths hardcoded in `install_lib._AGENT_HOME_DIRS` and `_AGENT_WIRE`, so adding a new agent also requires modifying the install_lib maps in addition to the `agents/<name>/` files.
>
> The ground truth for this section is `infra/agents/{claude,codex}/{adapter.py,normalize.py,events.json}`, `infra/hooks/{manifest.json,session-start.py,session-log-remind.py,auto_pull.py,auto-commit.py,confirm-action.py}`, and `infra/io_encoding.py` in the working tree as of 2026-06-16. The current working tree has uncommitted changes related to `install-skills` (`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py`, etc.), and this section reflects **the current implementation regardless of commit status**.

### 2.1 Directory Structure

```
infra/
├── hooks/                       # 공통 — 1벌
│   ├── manifest.json            #   정규형 선언 (§2.3)
│   ├── session-start.py          #   SessionStart additionalContext 주입 + 세션당 1회 auto_pull
│   ├── session-log-remind.py     #   UserPromptSubmit 리마인더 (pull 안 함 — 2026-06-17 분리)
│   ├── auto_pull.py              #   manifest 엔트리 아님. session-start helper(세션당 1회)
│   ├── auto-commit.py            #   PostToolUse/file_edit 자동 커밋(동기=커밋까지, #45)
│   ├── push-worker.py            #   detach push worker — per-team lock·drain·plain-push-only(#45)
│   └── confirm-action.py         #   PreToolUse/linear.create_issue 확인 차단
├── skills/
│   └── base/<skill>/SKILL.md     # 공통 스킬 원본. 현 구현은 base만 설치(오버라이드 없음)
├── agents/
│   └── <name>/                  # 에이전트별 어댑터 — 파일 3개
│       ├── adapter.py           #   설치 시점 번역기 (§2.7)
│       ├── events.json          #   번역표 (§2.6)
│       └── normalize.py         #   런타임 통역사 (§2.10)
└── install.py                   # 디스패처 겸 부트스트랩: --<agent> 플래그 → agents/<name>/ 위임
```

**Structural principles and current reference exceptions:**
1. The design goal is to keep agent-specific notation out of common scripts and skills. However, some common hooks in the current reference exceptionally know the Claude output schema (`hookSpecificOutput`/`permissionDecision`) directly (`session-start.py`, `session-log-remind.py`; Appendix A.3). In the 2026-06-22 redesign, `session-log-remind.py` switched to **JSON stdout** with `hookSpecificOutput.additionalContext`+`systemMessage` (same shape as session-start) — normalize re-emits it unchanged so Claude receives it. (The PreToolUse exit-2 block in `confirm-action.py` is common to Claude and Codex, so it is not agent-specific notation.)
2. Agent-specific settings rendering, manifest translation, and normalize input-conversion logic live under `agents/<name>/`. Because of the exception above, the strong claim that "all agent-specific logic lives only under `agents/<name>/`" is not true for the current reference.
3. Install-time wiring is delegated to the adapter CLI. `install.py`/`install_lib.py` know only the agent-specific settings paths, isolation paths, and verb invocation order; the adapter handles manifest translation, hook string generation, and MCP/skill installation details.
4. Common runtime scripts read only the canonical schema. Agent-native JSON must be converted by `normalize.py` before being passed to common script stdin.

### 2.2 Manifest Entry Format

`infra/hooks/manifest.json` is an array of hook entries and is declared **only in an agent-independent canonical form**.

```jsonc
{
  "event": "PostToolUse",                 // 필수. 정규 이벤트 (§2.4)
  "match": { "action": "file_edit" },     // 선택. 정규 매처 (§2.5). 생략 = 전체 매칭
  "script": "auto-commit.py",             // 필수. hooks/ 하위 공통 스크립트
  "args": "",                             // 선택. 기본 ""
  "timeout": 3,                            // 선택. 초(seconds). 기본값은 구현 정의
  "mode": "on",                           // 선택. 생략 = base(상시) / "on" = 팀 모드 켜진 동안만
  "fallback": "runtime",                  // 선택. "runtime" | "drop". 기본 "drop" (§2.9)
  "strict": false,                        // 선택. 기본 false. normalize 변환 실패 정책 (§2.10)
  "enforcement": "advisory"               // 선택. "advisory" | "block". 폴백 경고 강화용 (아래)
}
```

- `event` and `script` are required. The adapter accesses them without a separate value validator. If a key is missing, the current implementation can fail with `KeyError`.
- An omitted or falsy `match` means match everything. In the current implementation, the only supported keys are `action` and `mcp`. Unknown match keys are treated as inexpressible during adapter translation, while the normalize runtime filter passes them as `True` (unknown matches are not blocked at runtime). Current lint/conformance does not check whether `match` has exactly one key.
- `args` is appended to the end of the command as a raw string. It is not parsed as a list and shell escaping is not reinterpreted. Example: `"args": "teammode-linear-create-allow"` in the manifest becomes the first positional argument to `confirm-action.py`.
- `timeout` is declared in **seconds**. Both Claude settings.json and Codex config.toml use second-based hook timeouts, so adapters write it unchanged (preventing conversion drift at the source). If omitted, neither side writes a timeout field.
- An omitted `mode` is a base entry. `"on"` is registered together with base during `sync --on`. `sync --off` registers only base. Plain `sync` **preserves the existing managed state** (self-heal): it infers the current state from teammode-owned objects in the existing settings (ON signals — Claude: an owned hook command points to a `mode:"on"` script or a managed statusLine exists / Codex: a `statusMessage` exists inside the teammode-hooks block or a `mode:"on"` script is referenced), renders with that state, and if no owned objects exist, treats it as initial off and registers only base. There is no separate state file ("remember last on/off") — the inference source is the settings file itself. Before this contract existed, plain `sync` silently downgraded the ON state to base-only, and for Codex, dropping statusMessage even caused a trust-hash mismatch (requiring re-trust).
- The default `fallback` is `"drop"`. `"runtime"` is a mode where, when the event is supported but the matcher cannot be expressed, the hook is registered without a matcher and left to normalize self-filtering. If the event itself is `null`, it cannot be registered even with runtime and is dropped.
- `strict` determines the exit code on normalize conversion failure. If any manifest entry with the same `script` has `strict: true`, normalize conversion failure invoked for that script exits 1. Otherwise it exits 0.
- The default `enforcement` is `"advisory"`. In the current code, this field only strengthens the warning text on the Codex `sync()` path where `event is None`. That is, if an event is declared unsupported (`null`) in events.json and has `enforcement: "block"`, it prints `[warn] ... (block 강제 상실) → 비활성`. Codex currently supports all four event types, so the PreToolUse blocking hook in the reference manifest does not take this path. Claude `sync()` does not read `enforcement`.
- **Forbidden (required)**: writing agent-specific notation directly in the manifest — `mcp__*`-style tool names, matcher strings such as `Write|Edit`, `apply_patch`, or specific agent settings file paths. Everything must be canonical only. (Subject to lint/conformance checks — reference: `check._lint_manifest_canonical` greps for `mcp__`/`Write|Edit`/`apply_patch`.)

### 2.3 reference manifest (current build)

The reference build declares four entries in `infra/hooks/manifest.json`. All four declared scripts exist under `infra/hooks/`. Including `auto_pull.py`, there are five hook-related Python files, but `auto_pull.py` is not a manifest entry; it is a helper imported and called by `session-log-remind.py`.

| event | match | script | mode | fallback | enforcement | strict | script exists |
|---|---|---|---|---|---|---|---|
| `SessionStart` | (none) | `session-start.py` | on | (drop) | advisory | — | ✅ |
| `UserPromptSubmit` | (none) | `session-log-remind.py` | on | (drop) | advisory | — | ✅ |
| `PostToolUse` | `action: file_edit` | `auto-commit.py` | (base) | runtime | block | — | ✅ |
| `PreToolUse` | `mcp: {server: linear, tool: create_issue}` | `confirm-action.py` | (base) | runtime | block | true | ✅ |

Summary of the five hook-related files in the current build:

| file | manifest registration | input | main branches | output and exit |
|---|---:|---|---|---|
| `session-start.py` | ✅ `SessionStart` | canonical JSON stdin | no-op if event mismatch, JSON parse failure, `.teammode-active` missing, or engine import/collection failure | when active, **auto-pull once per session** (throttled and failure-safe), then Claude additionalContext JSON stdout; intended to always exit 0 |
| `session-log-remind.py` | ✅ `UserPromptSubmit` | canonical JSON stdin | no-op if event mismatch, JSON parse failure, or `.teammode-active` missing. When active: member identification (TEAMMODE_MEMBER env first → single config fallback → fallback if absent) + age/counter decision based on my file mtime. check_reset: my file mtime changed or date changed (06:00 cutoff) → count=0 + return (does not nag). (**no pull** — per-prompt pull was split into one SessionStart pull by the 2026-06-17 P0 hook hang fix) | when needed, **`hookSpecificOutput.additionalContext`+`systemMessage` JSON stdout** (propagated by normalize re-emission). Emits strong(age≥1800 & 30-minute throttle) OR weak(count%5==0). Body defaults to **compact** (1-3 lines of dynamic state: Nth prompt, file path, offset + rule reference — the rule body is injected once by session-start via `_slog_rules.SESSION_LOG_RULES`) — opt back into the legacy long body (count/offset append kit) with `ux.session_log_remind.context_style:"full"`; also degrades to the long body if the rules module is missing (fail-to-verbose). Normal exit 0. State-file write failures are caught as OSError (safe). |
| `auto_pull.py` | ❌ helper | function call | absorbs state-file throttle, git pull failure, and exceptions into a result object | no CLI main. Returns `AutoPullResult`; intended not to propagate exceptions. **Caller: session-start.py (once per session)** — moved from the previous session-log-remind (per prompt) |
| `auto-commit.py` | ✅ `PostToolUse` | canonical JSON stdin | no-op on event/action mismatch, `.teammode-active` missing, `git_ops` missing, no files, or exception | commits only canonical `files` synchronously with `do_commit(push=False)` → on commit success, records push-pending ledger + detaches `push-worker.py` kick (#45 — push is the worker's job, plain-push-only). Retries index.lock once after 1s. Always exits 0 |
| `confirm-action.py` | ✅ `PreToolUse` | canonical JSON stdin + first argv marker | passes on event mismatch, `.teammode-active` missing, target MCP mismatch, or human allow signal | without allow, deny JSON stdout + stderr, exit 2 |

### 2.4 Canonical Events (0.2)

| canonical name | meaning | meaning-preservation requirement |
|---|---|---|
| `SessionStart` | session start | fire once per session before the user's first input |
| `UserPromptSubmit` | immediately after user prompt submission | fire before the agent begins generating a response |
| `PreToolUse` | immediately before tool execution | **must be blockable** — a hook failure (nonzero exit) must be able to prevent tool execution |
| `PostToolUse` | immediately after tool execution | fire after the tool result is finalized |

- Canonical names are based on **Claude Code vocabulary** (Tier 1 Reference).
- Adding a canonical event is allowed only with a minor bump.
- If an agent cannot express a canonical event, it must be **explicitly** represented as `null` in the adapter translation table (events.json); silent omission is forbidden.

### 2.5 Canonical Matchers

The canonical shape of the `match` object is one of the following two forms. The intended form has exactly one key, but the current reference does not validate this separately.

```jsonc
{ "action": "file_edit" }                                  // (a) 빌트인 행위 클래스
{ "mcp": { "server": "linear", "tool": "create_issue" } }  // (b) MCP 툴 — 정규 서버명
```

- **Canonical action class 0.2**: only `file_edit` (file creation/modification). `shell_exec`, `file_read`, and others are added with a minor bump only when the need is demonstrated.
- **Canonical server name (required)**: MCP server registration aliases differ by environment (`slack-myteam`, `claude_ai_Google_Calendar`, etc.). The manifest references only the **canonical server names** (provider identifiers: `linear`, `slack`, `notion`, `google`, etc.) declared in `services` (§7). Guaranteeing the mapping from canonical server name to actual registration alias is the adapter's registration-time responsibility (§2.8).

### 2.6 events.json — Adapter Translation Table

Each adapter declares the translation table from canonical vocabulary to that agent's notation **as data** (so translation rules are not hidden in code branches).

```jsonc
// agents/claude/events.json (reference)
{
  "agent": "claude",
  "config_file": "~/.claude/settings.json",
  "events": { "SessionStart": "SessionStart", "UserPromptSubmit": "UserPromptSubmit",
              "PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse" },
  "actions": { "file_edit": "Write|Edit" },
  "mcp_tool_format": "mcp__{server}__{tool}"
}
// agents/codex/events.json (reference) — PreToolUse: "PreToolUse"(지원), file_edit: "apply_patch",
//   mcp_tool_format: "mcp__{server}__{tool}"
```

Rules (required):
1. `events` must contain **every canonical event key** from §2.4. If unsupported, use `null` — missing keys are forbidden. However, current `conformance/check.py lint` does not check events completeness.
2. `actions` must contain every canonical action class key in 0.2. If unsupported, use `null`. However, current lint does not check actions completeness.
3. Do not hardcode agent-specific special handling (event skip, matcher transformation) in install code — put all of it in this file.
4. The replacement variables for `mcp_tool_format` are `{server}` and `{tool}`. `{server}` receives the **actual registration alias** resolved by the adapter.
5. Current reference values:
   - Claude: all four `events` use the same names, `actions.file_edit = "Write|Edit"`, `mcp_tool_format = "mcp__{server}__{tool}"`, default settings file `~/.claude/settings.json`.
   - Codex: all four `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `PostToolUse` events are supported, `actions.file_edit = "apply_patch"`, `mcp_tool_format = "mcp__{server}__{tool}"`, default config file `~/.codex/config.toml`.

### 2.7 adapter.py — Install-Time Contract

The adapter must implement the following CLI (required). The dispatcher calls it.

```
adapter.py [global-options] sync [--on|--off]   # manifest → 에이전트 설정 동기화
adapter.py [global-options] uninstall           # tm-mode 훅 제거 + tm-mode 스킬 제거
adapter.py [global-options] install-mcp         # services 연결 provider MCP 별칭 등록
adapter.py [global-options] install-skills      # infra/skills/base/* 설치
```

Implemented global options:

| adapter | settings/config option | team config option | MCP option | provider option | skills option | python option |
|---|---|---|---|---|---|---|
| Claude | `--settings`, default `~/.claude/settings.json` | `--config`, default `<team_root>/team.config.json` | `--mcp-config`, default `~/.claude.json` | `--providers-dir` | `--skills-dir`, default `~/.claude/skills` | `--python`, default `sys.executable` |
| Codex | `--config`, default `~/.codex/config.toml` | `--team-config`, default `<team_root>/team.config.json` | none. Uses the `# teammode-mcp-*` block inside the `--config` file | `--providers-dir` | `--skills-dir`, default `~/.codex/skills` | `--python`, default `sys.executable` |

Note: the table above is the CLI contract of adapter `adapter.py` itself. The `install.py --<agent> ...` dispatcher gate separately recognizes only `--settings` or `--install` as safe intent; the Codex adapter's `--config` does not pass this gate (§4.2).

Common CLI exit codes:

- `main()` on the normal path always returns `0`. Each verb's change messages are printed to stdout.
- argparse usage errors (missing required subcommand, unknown option, etc.) follow Python argparse's default behavior: print to stderr and exit 2.
- Internal adapter exceptions are not broadly caught at top level. For example, an invalid manifest shape causing a `KeyError` can terminate abnormally. The `install.py` wire aggregates that agent as failed if the adapter return rc is not 0.
- The `uninstall` CLI also calls `uninstall_skills()` after removing hooks. Codex `uninstall()` removes the hook block and MCP block together, then skills removal is executed.

Implementation contract for `sync`:

1. Select target entries:
   - `mode is None` (plain `sync`): first infer the current state from teammode-owned objects in the existing settings using `_infer_existing_mode()` (self-heal). If inferred as ON, treat it the same as on; otherwise (no owned objects / cannot decide), target only base entries (treat as initial off).
   - `mode == "off"`: target only base entries whose manifest has no `mode`.
   - `mode == "on"`: target base entries + entries with `mode: "on"`.
   - There is no separate state storage. Plain `sync` means "preserve current state + re-render"; if the manifest is unchanged, the file is unchanged (preserving the Codex trust hash).
2. Event translation:
   - Read `events.json.events[canonical_event]`.
   - If the key is absent, treat it as unsupported, like `None`.
   - If the value is `null`, omit registration + `[warn] <script>: <agent> 미지원(이벤트 <event>) → 비활성`.
   - In Codex, if `enforcement == "block"`, `(block 강제 상실)` is added to the warning above.
3. MCP matcher preprocessing:
   - If `team.config.json` is missing, JSON parsing fails, the top-level value is not an object, or `services` is not an object, `_load_services()` returns `None`. In that case, the empty-slot rule and install-mcp preflight check are not applied (preserves L1 behavior).
   - If `services` is a dict and match is `{"mcp": ...}`, check provider connectivity first. It is connected if any role in the provider pack's `services` role list has `team.config.json.services[role].provider == canonical_server`. If the provider pack cannot be found, fall back to checking whether any services value has the same provider.
   - If not connected, omit the entry regardless of fallback + `[info] <script>: '<provider>' 역할 슬롯 미연결 → MCP 매처 생략(빈 슬롯, 슬롯 연결 후 sync 재실행)`.
   - If connected but no alias with `_teammode_managed: true` exists in the MCP registration file/block, treat install-mcp as not run first and omit that entry + `[warn] ... MCP 별칭 미보장(install-mcp 선행 필요) → 이 매처만 생략`.
4. Matcher translation:
   - No `match`: `(matcher=None, expressible=True)`.
   - `{"action": "file_edit"}`: use the `events.json.actions.file_edit` string. Claude uses `Write|Edit`, Codex uses `apply_patch`.
   - `{"mcp": {"server": S, "tool": T}}`: substitute `server=resolve_server_alias(S)`, `tool=T` into `events.json.mcp_tool_format`. `resolve_server_alias` prefixes the canonical server name with `tm-` (`linear`→`tm-linear`) — this is the alias namespace registered by teammode, so it coexists without colliding with a user MCP of the same name. The matcher string matches the actual runtime tool name (`mcp__tm-linear__create_issue`), and normalize reverse-resolves that alias back to the canonical server name (§6.1).
   - If inexpressible and `fallback == "runtime"`, register without a matcher. If inexpressible and fallback is drop, warn with `[warn] ... 매처 표현 불가 → 비활성` and omit it.
5. Command generation:
   - The format must be `<python> <agents/<name>/normalize.py> <script> [args]`. Do not register common scripts directly.
   - The default `<python>` is the absolute path of install-time `sys.executable`. If `--python` is provided, use that string as-is.
   - `_to_slash(s)` replaces every backslash with `/`, then `_quote_arg(s)` wraps tokens containing spaces, tabs, or double quotes in double quotes. Tokens already wrapped in the same quotes are left unchanged. The empty string becomes `""`. Simple tokens are not quoted.
   - `args` is appended raw after the command string, without separate quoting.
6. Ownership:
   - Hook ownership is determined by whether the command string contains this adapter's absolute `normalize.py` path or the trailing path `agents/<agent>/normalize.py`.
   - Apply `_to_slash` before comparison so old backslash registrations are also recognized as owned.
   - Do not treat a hook as owned merely because it contains `agents/`.
   - Do not delete or modify user hooks.
7. Idempotency:
   - Claude reads JSON settings, upserts/deletes the `hooks` object, and writes only if `json.dumps(indent=2, ensure_ascii=False) + "\n"` differs from the original. Broken JSON is treated as `{}`.
   - If Claude has an owned hook for the same event/matcher, it upserts the first hook command and timeout. If the command or timeout differs, it updates and returns `[update]`. If the manifest has no timeout, it removes the existing timeout key (prevents stale 5000).
   - Claude removes owned hooks not in the manifest target command set. If an event array becomes empty, it also deletes the event key.
   - Codex does not use a TOML parser; it renders and replaces the entire managed block from `# teammode-hooks-start` through `# teammode-hooks-end`. If no block exists, it appends one to the end of the file.
   - The Codex hook block writes `[[hooks.<event>]]`, optional `matcher = "..."`, `[[hooks.<event>.hooks]]`, `type = "command"`, `command = ...`, and `timeout = <seconds>` for each event. The command string prefers single-quoted literals; if the command contains a single quote, it escapes as a double-quoted TOML string.
   - In Codex, even when there are zero registration targets, an empty teammode hook block is still a render target. It is written if it differs from the existing file.
8. Output:
   - warnings and infos are printed directly inside `sync()`.
   - If there are no file changes and no warnings/infos, put `[ok] 변경 없음` in the returned list.
   - If there are file changes, Claude returns `[add]`, `[update]`, and `[remove]` messages, while Codex returns `[sync] Codex 훅 <n>개 등록`.

Relationship with the `install.py` wire:

- Current `install_lib.wire_agents()` calls the adapter for each detected agent in this order: `install-mcp → sync --on → install-skills`.
- If `install-mcp` fails for one agent, that agent's `sync` and `install-skills` are skipped, while wiring continues for other agents. The aggregated wire exit code for failures is 3.
- If `sync` fails, that agent's `install-skills` is also skipped.
- `install-skills` failure is aggregated as a failure for that agent, but wiring continues for other agents.
- In isolation mode (`--settings`), adapter-specific settings/config, Claude MCP file, and skills dir are all passed explicitly under the isolation subpaths. In real-host mode, the home-based default paths are used after passing the `--yes` gate.

### 2.8 Duties of install-mcp

`install-mcp` is the wiring verb for the L2 registrar — it **prepares the official (or custom, if none exists) vendor MCP selected by the team and registers that canonical server-name alias in agent settings**. tm-mode ends there, and actions (issue creation, calendar additions) are performed by the AI directly calling the registered `mcp__<alias>__<벤더도구>` — install-mcp does not wrap actions or relay through `role_server`.

The current `install-mcp` implementation does not build MCP servers itself (0.2 limitation). The contract is to **guarantee the alias for providers that have real launch data (url/command) as a teammode-managed entry in agent settings**. Providers without launch data do not get an alias guarantee — Codex records a comment placeholder (§2.8-3), while Claude records a nonfunctional marker entry and honestly reports that it is 'not connected'. Executable MCP server definitions are guided by the provider pack's `mcp.register_hint`, and that supplement (bringing an official MCP into this repo or creating a custom one) is filled by the L2 registrar/connect layer (§5.4).

**Official/custom branch.** install-mcp is responsible only for registration, while **preparation** (import official / create custom) happens in the connect layer (§skills 5.4) — but install-mcp handles the outputs of both paths **identically**. Whether official MCP or custom MCP, the code + execution metadata lives in this repo under `infra/mcp/<provider>/`, and it is registered with the same alias under the canonical server name (`resolve_server_alias(provider)`). install-mcp has no branch that treats "custom" differently.

**Custom path details** (only when no official MCP repo exists; official first):

1. Use the provider's official API spec (REST/GraphQL docs) as the source and write the server with the Python MCP SDK — expose **only the tools needed for that slot role** (for calendar, roughly list_events / create_event).
2. Put the server code and launch command in `infra/mcp/<provider>/` and commit them to this repo = the team-shared repository. The next member reuses it instead of creating it again.
3. Tokens use the same path as official MCP (env / local vault 0600, §5.4 and §7.5). Do not create a separate token path just because it is custom.
4. Immediately after the first custom implementation, verify the real behavior of the exposed tools with adversarial review (subagent).

A custom MCP is **that vendor's dedicated MCP** — it simply exposes tools wrapping the provider API and does not create role-unified verbs such as `issues_create`. That would revive the abandoned `role_server`/role abstraction (Option B). tm-mode only connects (registers) custom MCPs as well (Option A). For the detailed seven steps, see "MCP preparation" in `docs/archive/2026-06-25-L2-redesign.md`.

Common rules:

1. The input source is `team.config.json.services`. Connected providers are calculated only when `_load_services()` returns a dict. Missing file, broken JSON, missing `services`, or non-object `services` are equivalent to zero connected providers.
2. Connected providers are collected from `services` values that are objects and whose `provider` is a nonempty string. If the same provider appears in multiple roles, it is registered only once.
3. Read provider packs with `providers.lookup(provider, providers_dir=...)`. If the pack is missing or lookup raises an exception, do not guess; emit `[info] <provider>: provider 팩 없음 → MCP 등록 생략`.
4. The alias is the result of `resolve_server_alias(provider)`. The current implementation prefixes the canonical server name with `tm-` (`linear`→`tm-linear`, idempotent — input already prefixed is left unchanged). It is a teammode-owned namespace, so it coexists without key collision with an MCP of the same name registered directly by the user (`linear`). The registration entry's `_canonical_server` stores the canonical server name, not the alias.
5. The MCP matcher guarantee in sync checks whether this alias has a teammode management marker. Claude parses `mcpServers[alias]._teammode_managed is True`; Codex parses the presence of `[mcp_servers.<alias>]` inside the teammode MCP block as `_teammode_managed: True`.

Claude implementation (`~/.claude.json` shape):

1. The default MCP file is `~/.claude.json`, overrideable with CLI `--mcp-config`. To prevent a Codex-inheritance footgun, there is a `_SEALED` sentinel; calling the parent `install_mcp()` while sealed raises `NotImplementedError`.
2. `_read_mcp_config()` treats missing files, broken JSON, and non-object top-level values as `{}`. If it is a normal object, it preserves the whole object and modifies only `mcpServers`.
3. The registration entry is the following placeholder.
   ```jsonc
   {
     "_teammode_managed": true,
     "_canonical_server": "<provider>",
     "_register_hint": "<provider pack mcp.register_hint or empty>"
   }
   ```
4. If the existing alias has `_teammode_managed: true` and the entry is the same, there is no change.
5. If the existing alias is not owned by teammode, warn with `[warn] <alias>: 사용자 등록 MCP 서버 존재 → 무접촉` and exclude that alias from desired. The user entry and other top-level data (`projects`, etc.) are preserved.
6. For missing aliases or teammode-owned aliases requiring registration/update, set `servers[alias] = entry` and return `[mcp] <alias> 등록`.
7. Removal deletes teammode-owned entries in `mcpServers` that are not in the current desired aliases and returns `[remove-mcp] <alias>`.
8. Empty-slot safety:
   - If the original has no `mcpServers` key and there are no servers to register, do not create or touch the file.
   - If the original has an `mcpServers` key or servers remain, serialize to canonical JSON and write only if it differs from the original.
9. If there are no return messages, distinguish by desired alias count. If desired aliases exist, return `[ok] 변경 없음 (<n>개 provider 등록됨)`; otherwise return `[info] 연결된 MCP provider 없음 (빈 슬롯)`.
10. **Existing (user) MCP detection (#3)**: for placeholder-target providers (packs with neither hosted URL nor launch command — slack/google, etc.), before registration, search the settings file's user servers (non-owned `mcpServers` entries / for Codex, `[mcp_servers.*]` sections outside the teammode block) for a server that appears to be the same provider. Detection rules: ① server-key token match (non-alphanumeric separators, case-insensitive, excluding the `tm-` namespace), ② url substring, ③ command+args substring. If detected, do not register a placeholder; only print `[info] <provider>: 기존 MCP 서버 '<name>' 발견 … tm-<provider> 별칭으로 직접 연결` guidance (no automatic adoption — ownership cannot be guaranteed without an ownership marker). Because it is excluded from desired, old stale placeholders disappear through the existing removal path (`[remove-mcp]`). If not detected, keep the previous behavior (placeholder + manual guidance). Providers with hosted/launch commands (linear/notion, etc.) keep real registration regardless of detection.

Codex implementation (`~/.codex/config.toml` shape):

1. MCP registration is managed only through the `# teammode-mcp-start` / `# teammode-mcp-end` block inside the `--config` file. There is no separate `--mcp-config`.
2. `_read_mcp_servers()` reads only `[mcp_servers.<name>]` headers inside this block by regex and returns values like `{name: {"_teammode_managed": True}}`.
3. Only providers with real launch data (hosted `url` or `command`/`args`) are registered as real `[mcp_servers.tm-<provider>]` tables (section key=alias, `_canonical_server`=canonical server name). **Providers without launch data (placeholders) must not become real tables — they remain only as one comment line inside the marker block**:
   ```toml
   # [tm-placeholder] <provider> — <provider pack mcp.register_hint>
   ```
   Rationale (P1, confirmed in practice on 2026-07-06): a real table without command/url makes Codex CLI fatally reject the entire config load ("invalid transport") → session cannot start and all hooks disappear. The comment form preserves the re-render management and stale-removal (#3 path) contract, but it is not captured by alias guarantee (`_read_mcp_servers`) — sync honestly omits it with `[warn] MCP 별칭 미보장` (it does not disguise a nonworking placeholder as guaranteed).
4. If there is at least one provider, render the whole teammode MCP block and replace the existing block or append it to the end of the file. If a write occurs, return `[mcp] <alias> 등록` (real registration) / `[mcp] <alias> placeholder 기록(주석 …)` for each alias; if bytes are identical, return `[ok] 변경 없음 (실등록 n개[, placeholder n개 — 연결되지 않음])`.
5. If there are no providers, remove only the existing teammode MCP block. If no block exists, do not touch the file and return `[info] 연결된 MCP provider 없음 (빈 슬롯)`.
6. (Replaces the old limitation clause) Since placeholders exist only in the comment form described in 3, Codex runtime launch errors are impossible. Alias-slot guarantees hold only for real registered providers.
7. Limitation: the Codex implementation does not parse the full TOML, so it does not check for duplicate collisions with user `[mcp_servers.<same>]` sections outside the teammode block. teammode manages only the marker block. However, for **existing server detection (#3, same contract as Claude implementation 10)**, it line-scans section headers and one-line `url`/`command`/`args` values outside the block for detection only (limited to placeholder-target providers; it does not register or modify them).

### 2.8.1 hooks.json Coexistence Contract (2026-07-06 decision)

Codex **loads and merges** `hooks.json` and inline `[hooks]` in `config.toml`, and emits a harmless warning if both exist in the same layer (official docs: "Prefer one representation per layer" — no canonical designation and no deprecation). teammode's contract:

- teammode **owns only the `# teammode-hooks-*`/`# teammode-mcp-*` marker blocks in `config.toml`**. It does not write `hooks.json` (that file may be generated/regenerated by other tools such as cmux: preservation of external entries is not guaranteed, creating hook-disappearance risk + trust-state keys are based on the config.toml path, so migration would force everyone to re-approve).
- If sync detects combined use of `hooks.json`, it explains the cause and harmlessness of the warning in one `[info]` line.
- **Re-evaluation gate**: redesign the migration if Codex officially makes hooks.json canonical/deprecated, or if cmux documents a merge that preserves external entries.

### 2.9 Fallback Policy

The `fallback` of a manifest entry — behavior when an adapter cannot express that entry for its agent.

| value | trigger condition | behavior |
|---|---|---|
| `"drop"` (default) | event or matcher cannot be expressed | omit registration + `[warn] <script>: <agent> 미지원 → 비활성` (required; no silent omission) |
| `"runtime"` | only matcher cannot be expressed (event is supported) | register without a matcher + preserve meaning through normalize self-filtering (§2.10-2) |

- If it is `"runtime"` but **the event itself is unsupported** (events.json `null`), there is no way to express it, so behavior is the same as `"drop"` + `[warn]` (required). Reference adapters: if `event is None`, drop even with runtime.
- **Empty-slot-first rule (required)**: if the provider role slot referenced by an `mcp` matcher is not connected (§7.2), omit registration regardless of `fallback` + `[info]` — this is not an error (empty slot = first-class citizen). It is activated by reconnecting the slot and rerunning `sync`.
- **Missing config file and empty services differ**: missing `team.config.json`, parse failure, or missing services means services information is unknown (`None`), so the empty-slot rule is not applied. In that case, MCP match translation is still attempted for L1 compatibility. By contrast, if the file exists and has `services: {}`, it is treated as an explicit empty slot and omitted with `[info]`.
- **install-mcp not run first is a warn**: if services says the provider is connected but there is no alias guarantee, it is not an empty slot, so this is `[warn]`, not `[info]`, and only that MCP match entry is omitted.
- Selection guide: convenience that may be absent → `drop` / safety checks that must not be absent (confirmation/blocking types) → `runtime` (+ `strict` if needed).

### 2.10 normalize — Runtime Contract

**Input**: agent-native JSON (stdin) → **Output**: pass canonical JSON (stdin) to the common script.

**Canonical input schema 0.2:**

```jsonc
{
  "event": "PostToolUse",            // 필수. 정규 이벤트 (§2.4)
  "action": "file_edit",             // 해당 시. 정규 행위 클래스
  "tool": { "kind": "mcp",           //   해당 시(Pre/PostToolUse). "mcp" | "builtin"
            "server": "linear",      //   kind=mcp일 때. 정규 서버명
            "name": "create_issue" },
  "files": ["path"],                 // file_edit일 때. normalize가 받은 file_path 문자열 배열
  "prompt": "사용자 입력 …",          // UserPromptSubmit일 때
  "agent": "codex",                  // 필수. 출처 에이전트명
  "raw": { }                         // 선택. 원어 전문(탈출구). 생략 시 {}
}
```

Current normalize always outputs `event`, `agent`, and `raw`. If stdin is empty, it treats the native input as `{}`, so `event` may become an empty string. `raw` is the native dict as-is. The other fields are attached only when applicable. Common scripts trust only this schema, and for security must not use model-controlled payloads such as `raw.tool_input` as allow signals (`confirm-action.py` follows this).

**normalize duties (required):**
1. Invocation form: `normalize.py <script> [args...]`. The current implementation does not validate `<script>` and executes it by joining `HOOKS_DIR / script`. If argv is absent, it writes `[normalize] script 인자 필요` to stderr and exits 0.
2. Input loading: read all of stdin. If it is only whitespace, treat it as `{}`. If `JSONDecodeError`, `ValueError`, `KeyError`, or `TypeError` occurs during JSON parsing or conversion, go to the conversion-failure policy.
3. Event conversion:
   - Native event is `raw["hook_event_name"]` first, otherwise `raw["event"]`, otherwise `""`.
   - Find the canonical event by reverse-mapping `events.json.events`. If no mapping exists, use the native event string as-is.
   - `agent` is `events.json.agent`, and the default is `"claude"` for Claude normalize. Codex normalize rebinds the Claude normalize module's path constants to Codex so it reads the Codex events.json.
4. `UserPromptSubmit`: `prompt = raw.get("prompt", "")`.
5. `PreToolUse`/`PostToolUse`:
   - `tool_name = raw.get("tool_name", "")`, `tool_input = raw.get("tool_input", {}) or {}`.
   - If `tool_name` is empty, do not attach tool/action/files.
   - If `tool_name` matches `mcp_tool_format`, set `tool = {"kind": "mcp", "server": <server>, "name": <tool>}`.
   - Otherwise set `tool = {"kind": "builtin", "name": tool_name}`.
   - If the builtin tool name is equivalent to an OR string in `events.json.actions`, attach `action`. Claude `Write|Edit` is split on `|` and compared equivalently with `Write` or `Edit`. Codex is compared equivalently with `apply_patch`.
   - If action is detected and `tool_input.file_path` is truthy, set `files = [file_path]` without absolute-path conversion or path-escape validation; otherwise set `files = []`.
6. MCP reverse parsing:
   - Convert the `mcp_tool_format` template to a regex and capture `{server}` and `{tool}`.
   - The captured `{server}` may be the registration alias in the actual runtime tool name (`tm-linear`), so remove the `tm-` prefix via `_canonical_server` and output the canonical server name (`linear`) (inverse of `resolve_server_alias`). This is required so self-filtering (§6.2) and `confirm-action.py` match the manifest's canonical server name (§2.5). A user server of the same name without the prefix (`linear`) is preserved as-is.
7. Runtime self-filter:
   - The manifest is read from `infra/hooks/manifest.json` on every call. If the file is missing or broken JSON, it is an empty list.
   - The lookup key is `(script, canonical_event)`, and the first matching entry is used. Current lint does not check duplicate `(event, script)` pairs.
   - Filtering occurs only when an entry exists and `fallback == "runtime"`.
   - An action match is `canonical.action == match.action`.
   - An mcp match requires `canonical.tool.kind == "mcp"` and server/name to equal the match's server/tool.
   - If there is no match, pass. Unknown match keys also currently return `True` in `_matches_filter()`.
   - If it does not match, do not execute the common script and exit 0.
8. Common script execution:
   - Execute with `subprocess.run([sys.executable, str(HOOKS_DIR / script)] + extra_args, input=json.dumps(canonical), capture_output=True, text=True, encoding="utf-8", errors="replace")`. `ensure_ascii` is **intentionally left at the default (True)** — stdin is sent as pure ASCII (`\uXXXX` escapes), so it is safe even if the child decodes stdin with any locale (Windows cp949, etc.; child `json.loads` restores it). The current implementation does not separately validate whether `script` is a filename, whether it contains `..`, or whether it escapes outside `infra/hooks/`.
   - Re-emit the common script's stdout/stderr unchanged to normalize's own stdout/stderr.
   - The normalize exit code is the common script returncode. Therefore, the exit 2 block from `confirm-action.py` is preserved.
   - If the script file does not exist, the Python subprocess usually emits exit 2 and stderr, and normalize returns that returncode unchanged.
9. Conversion failure:
   - If any manifest entry with the same `script` has truthy `strict`, write `[normalize] 변환 실패: <exc>` to stderr and exit 1.
   - If there is no strict, write the same warning to stderr and exit 0.
10. UTF-8:
   - Claude normalize `main()` calls `_ensure_utf8_io()` at startup.
   - Codex normalize reuses Claude normalize `main` as-is, so it follows the same correction path.

### 2.11 Cross-Agent (Claude ↔ Codex)

- **Shared translation core (reference)**: the Codex adapter inherits Claude `Adapter` to reuse the translation core (events.json-based), and redefines only Codex-specific **config format (TOML block) + fallback handling**. Codex normalize imports functions from Claude normalize and rebinds only the path constants (events.json and manifest) into the Codex context.
- **Codex PreToolUse support**: Codex supports all four event types in events.json with `PreToolUse: "PreToolUse"`. Blocking hooks with `enforcement: block`, such as `confirm-action.py` and `kb-write-guard.py`, are also registered as `[[hooks.PreToolUse]]` in `config.toml`, so exit-2 blocking takes effect. Actual Codex hook input has the shape `tool_name`/`tool_input` (or top-level `name`/`input`), and apply_patch carries the patch string in `tool_input.command` (captured 2026-06-21) — normalize converts file headers into canonical `files[]`.
- **Codex PreToolUse blocking semantics (verified in practice, 2026-07-03, codex-cli 0.142.5)**: Codex implements the Claude-compatible PreToolUse wire — `PreToolUsePermissionDecisionWire` parses `hookEventName`/`permissionDecision`/`permissionDecisionReason` from hook stdout JSON. **exit 2 = blocked**, and stderr is the channel for the block reason. `permissionDecision` supports only `"deny"` (`allow`/`ask` unsupported). Passing is exit 0 + no output. The output of the reference blocking hook (deny JSON stdout + nonempty stderr + exit 2) satisfies this contract as-is, so it is common to Claude and Codex.
- Independent implementations do not need to use the `agents/` directory structure or Python as-is. What must be preserved is **the declaration format (manifest.json and events.json) and meaning**, not the implementation language or file layout (§6 C2).
- Codex MCP registration limitations are surfaced honestly. `install-mcp` writes only comment placeholders (§2.8-3 — real blocks forbidden) to `config.toml`, and the user may need to supplement real launchable MCP server definitions.
- Codex `sync()` supports `PreToolUse`, so it registers `confirm-action.py` as `[[hooks.PreToolUse]]`. MCP tool calls registered by `install-mcp` can also be blocked through the normalize → confirm gate path.

### 2.12 Skill Resolution — Single Source + Overrides

In the current implementation, skill bodies use only `infra/skills/base/<name>/SKILL.md` as the source. The override search for `agents/<name>/skills/<skill>/SKILL.md` from the earlier draft is **not currently implemented**. Override resolution, requires gates, and traversal guards are still unimplemented in 0.2.

The base skill in the current working tree is `tm-onboard`. `tm-connect` is in `infra/skills/core/tm-connect/`.

`install-skills` implementation contract:

1. source discovery targets only children under `self.skills_src_dir = <team_root>/infra/skills/base` that are directories and contain a `SKILL.md` file. It uses sorted `iterdir()` order.
2. target is `<skills_dir>/<name>`. The default `skills_dir` is Claude `~/.claude/skills` and Codex `~/.codex/skills`. It can be isolated/explicit with CLI `--skills-dir`.
3. If target does not exist, create the parent and first try `os.symlink(src, target, target_is_directory=True)`. The success message is `[skill] <name> 심링크`.
4. If symlink fails with `OSError` (mostly Windows — symlinks require Developer Mode/admin privileges): **Windows (`os.name=='nt'`) tries a junction first** — run `cmd /c mklink /J <target> <src>` via `subprocess` (py3.9, so not `_winapi.CreateJunction` (3.12+)). A junction needs no privileges and is a link, so source updates from pull are reflected (not stale like a copy). The success message is `[skill] <name> 정션`. ⚠️ Because `cmd /c mklink` is reparsed by `cmd.exe` after `/c`, paths containing cmd metacharacters (`& | < > ^ " %`) create command-injection risk — such paths skip junctions and go straight to the copy fallback. If junction also fails or the platform is non-Windows, use `shutil.copytree(src, target)` + `_teammode_skill` marker (`[skill] <name> 복사(폴백)`) — heavy and non-updating, the last resort.
5. Ownership detection:
   - If symlink, it is owned when the realpath of absolute `os.readlink(target)` equals the src realpath.
   - If directory: Windows junctions have `is_symlink`=False, so they are owned when `os.path.realpath(target)` resolves to the src realpath. Otherwise, it is owned if the `_teammode_skill` marker file exists.
   - Anything else, or OSError, is not owned.
6. If target already exists and is owned, this is idempotent no-change. Symlinks and junctions are links, so source changes are reflected automatically. However, for copy fallbacks, even if the source changes, the current 0.2 implementation only checks for marker existence and does not recopy (stale — junction introduction reduced the impact because copy is the last resort).
7. If target already exists and is not owned, return `[skip] <name>: 사용자 스킬 존재 → 무접촉` and do not overwrite it.
8. orphan cleanup: if `skills_dir` exists, inspect children whose names are not in the current source name set. If `is_owned_skill(child, skills_src_dir / child.name)` is True, unlink symlinks and remove directories — **Windows tries `os.rmdir` first** (if junction, detach only the link and leave the source untouched; if real copied directory, it is nonempty and fails → `shutil.rmtree`), while non-Windows uses `shutil.rmtree`. Then return `[remove-skill] <name>`.
9. If there are no change messages, return `[ok] 변경 없음`.
10. `uninstall_skills()` returns `[ok] 제거할 스킬 없음` if `skills_dir` does not exist. If it exists, remove only owned skills among its children and return `[remove-skill] <name>`. If there is nothing to remove, return `[ok] 제거할 스킬 없음`.
11. The `adapter.py uninstall` CLI executes both hook removal and skill removal. Claude hook uninstall may return an empty list when there are no owned hook removal messages, and skills uninstall messages are printed separately. Codex hook uninstall returns `[remove] tm-mode 훅 블록` or `[ok] 제거할 블록 없음` depending on block removal, then runs skills uninstall.

`io_encoding.ensure_utf8_io()` implementation contract:

1. The purpose is to prevent Korean JSON/warning output from dying with `UnicodeEncodeError` on non-UTF-8 stdout/stderr, such as Windows native cp949.
2. `_is_utf8(enc)` is True when `codecs.lookup(enc).name == "utf-8"`. It is False if `enc` is falsy or lookup fails.
3. `_reconfigure_stream(stream)` is a no-op if stream is None or has no `reconfigure` attribute. It is also a no-op if already UTF-8. When needed, it calls `stream.reconfigure(encoding="utf-8")` and silently ignores `ValueError`, `OSError`, and `AttributeError`. It does not specify the `errors` policy, preserving the existing policy.
4. `ensure_utf8_io()` corrects only stdout and stderr. It does not correct stdin.
5. Call sites are adapter `main()`, normalize `main()`, `session-start.py`, `session-log-remind.py`, `confirm-action.py`, and other engine entry points. `auto-commit.py` and `auto_pull.py` do not call this function in the current code.

---

## §3. Engine Verbs (teammode.py) — Newly Codified

The engine `infra/teammode.py` currently recognizes only 10 verbs as known verbs.

```
python3 infra/teammode.py <verb> --root <팀루트> [동사별 플래그]
verbs: on | off | log | context | pull | commit | update | issue | memory | util
```

The ground truth for this section is the current working tree's `infra/teammode.py`, `infra/workday.py`, and `infra/git_ops.py`. As of 2026-06-16, this repository has uncommitted changes related to `install-skills` (`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py`, etc.), but the 10 verbs above are the complete set of `teammode.py` engine verbs themselves (0.3 — `memory` and `util` incorporated).

Common invariants:

- On entry, it calls `ensure_utf8_io()` to try to guarantee UTF-8 for stdout/stderr. stdin is not adjusted. This is a defense to reduce cases where Korean error messages or JSON output are garbled or crash because of native encoding.
- The team root uses **only the explicit `--root` value**. It does not infer the target root for engine verbs from `TEAMMODE_HOME`, cwd, or the install location.
- If `--root` is absent, or only the value-taking flag is present without a value, it is treated as `root is None`. For every known verb, before any other verb-specific required option, it writes `[error] --root <팀루트> ...` to stderr and exits 2.
- It only performs `team_root = Path(root).resolve()`. The common phase does not verify existence, whether it is a directory, or whether `team.config.json` exists. Those are handled naturally later by verb-specific file IO or git checks.
- If the verb is not a known verb, there are two branches. If the verb itself is missing, it writes usage to stderr and exits 2. If there is a first non-flag token but it is not a known verb, it writes `[unimplemented] <verb>` to stderr and exits 127.
- The engine does not summarize or judge. `log` preserves the received text, `context` transfers frontmatter values, and `issue` only echoes the canonical schema.
- Some exceptions are not caught at the Python level. For example, adapter execution failures in `on/off`, banner file read/write failures, log file write failures, and similar cases are not converted into separate exit codes and may terminate as ordinary Python exceptions (usually traceback + exit 1). Only explicitly handled input errors are exit 2, nonfatal git failures are exit 1, and unimplemented verbs are exit 127.

The argv parser (`_parse_args`) is a hand-written parser, not `argparse`.

- Value-taking flag whitelist: `--root`, `--settings`, `--author`, `--text`, `--now`, `--message`, `--title`, `--body`, `--assignee`, `--label`, `--priority`, `--paths`, `--member`, `--skills-dir`, `--skill`, `--folder`, `--filename`, `--content`, `--weight`, `--path`, `--date`, `--desc`.
- When it sees one of the flags above, it consumes the next token as the value. It consumes the next token as the value even if that token looks like `--다른플래그`. If there is no next token, the value is `None`. If the same flag is repeated, the last value remains.
- Boolean flags: `--install`, `--json`, `--push`, `--dry-run`. The default values are all `False`.
- A `--flag` outside the whitelist is ignored. In that case, the next token is not consumed as a value. Therefore a non-flag token after an unknown Boolean flag may become the verb or a positional.
- The first non-flag token becomes `verb`, and subsequent non-flag tokens are accumulated in `positionals` in order. Even if a value-taking flag appears between the verb and subaction, as in `issue --root <root> create`, `create` remains a positional.
- Verbs that use extra positionals: `issue` (first positional=action), `memory` (first positional=action write/delete/route/unlock; for `route` and `unlock`, second positional=subaction), and `util` (first positional=action add/remove/list). Other known verbs do not use positionals.

### 3.1 on / off (via settings — `--root` + (`--settings` or `--install`) required)

Only `on/off` calls the Claude adapter `sync()`, so it has a settings path gate. `log/context/pull/commit/update/issue` do not require settings, and ignore `--settings` even if provided.

- settings resolution:
  - If `--settings <경로>` is present, that string is used as-is as the adapter settings path.
  - If `--settings` is absent and `--install` is present, it uses `os.path.expanduser("~/.claude/settings.json")`.
  - If both are present, `--settings` takes precedence. There is no conflict error with `--install`.
  - If neither is present, it writes `[error] --settings <경로> ... 또는 --install ...` to stderr and exits 2.
- adapter creation:
  - It loads `infra/agents/claude/adapter.py` with `runpy.run_path(..., run_name="__teammode_engine__")`.
  - It creates `Adapter(agent_dir=<infra>/agents/claude, manifest_path=<infra>/hooks/manifest.json, settings_path=<resolved>, team_root=<INFRA.parent>)`.
  - Here, the adapter's `team_root` is the engine install location (`infra/..`), not the memory write target received through `--root`. In current repository execution these are usually the same, but in code they are separate axes.
- Banner and personality config:
  - `_render_banner(team_root)` reads `<team_root>/memory/banner.txt` as UTF-8 as-is and outputs it to stdout if it is a file.
  - If the banner file does not exist, it builds `=== <team_name> ===\n` from the `TEAMMODE_TEAM_NAME` environment variable value or the default `"teammode"`, creates the `memory/` directory, and caches it in `memory/banner.txt`. This env var is not used to determine the team root.
  - `_read_team_field(team_root, field)` returns the value only when `team.<field>` in `<team_root>/team.config.json` is a non-empty string. Missing config, JSON parse failure, type mismatch, and exceptions are all absorbed as `None`. They do not block `on/off`.
- `on` execution order:
  - It prints the banner to stdout **wrapped in a dynamically sized fence (fenced code block)**. Specifically: it applies `rstrip("\n")` to the `_render_banner` return value, then uses as the fence a backtick string whose length is at least 1 longer than the longest consecutive backtick run appearing in the content (and at least 3). This keeps the entire banner inside a single code block without early termination even if the banner contains a `` ``` `` line. Output format: `<fence>\n<banner_content>\n<fence>`.
  - If `team.greeting` exists, it prints it on the next line.
  - It calls adapter `sync(mode="on")`.
  - It writes an empty string as UTF-8 to `<team_root>/.teammode-active`. It does not create the parent directory. Any existing file is overwritten.
  - It calls `auto_update_on_start(team_root)`. This automatically syncs the upstream engine (infra/) and creates an automatic commit (push is absolutely forbidden). Dirty guards, fetch failures, and similar cases are silently skipped — they do not block on.
  - On normal completion, it exits 0.
- Upstream automatic update for `on` (task D):
  - `auto_update_on_start` calls `git_ops.sync_from_upstream`. Fetch failure, missing remote, or offline state → silently skipped without blocking on.
  - If there are uncommitted changes in the target paths (infra/, NOTICE.md), it skips with blocked=True and only prints a notice for the human.
  - If there are changes, it creates a paths-limited automatic commit with `do_commit(paths=res.paths, push=False)`. Automatic push is absolutely forbidden.
  - On successful commit, it prints the first NOTICE bullet in the format "Engine updated: <content>".
  - This function swallows all exceptions. It does not affect the exit code of `on`.
- `off` execution order:
  - It calls adapter `sync(mode="off")`.
  - If `<team_root>/.teammode-active` exists, it deletes it. If it does not exist, execution continues unchanged.
  - If `team.farewell` exists, it prints that string; otherwise it prints `tm-mode off — 상태 저장됨`.
  - On normal completion, it exits 0.
- Idempotency:
  - Re-running `on` rewrites the same empty `.teammode-active` file and performs adapter sync again. If the banner file already exists, it does not regenerate it. Upstream may attempt fetch every time.
  - `off` proceeds through the success path even if the marker is absent. Adapter sync is performed every time.

### 3.2 log (session-log recording — `--root --author --text` required)

CLI:

```
python3 infra/teammode.py log --root <팀루트> --author <이름> --text <내용> [--now <ISO8601>]
```

- Required options:
  - If `--author` is absent or has no value, it writes `[error] log: --author <이름> 가 필요합니다.` to stderr and exits 2.
  - If `--text` is absent or has no value, it writes `[error] log: --text <내용> 가 필요합니다.` to stderr and exits 2.
  - An empty string value is allowed if the shell can pass it, because it is not `text is None`. `author=""` is rejected by the author validation below.
- `--now`:
  - If present, it is parsed with `datetime.fromisoformat(now_str)`.
  - On `ValueError`, this is not treated as an error; it falls back to `workday.now_kst()`.
  - If absent, it uses `workday.now_kst()`.
  - The actual rules for the 06:00 cut and naive/aware handling follow §1.4.
- author validation (`_validate_author`):
  - Reject empty string: `author 가 비어 있습니다.`
  - Reject inclusion of `/` or `\`: `author 에 경로 구분자가 포함될 수 없습니다: ...`
  - Reject `"."`, `".."`: `author 로 '.' 는 허용되지 않습니다.`, etc.
  - Reject absolute paths: `author 는 절대 경로일 수 없습니다: ...`
  - Reject if the first character is `-` or `_`: `author 는 영숫자로 시작해야 합니다: ...`
  - Reject if any character is not one of `str.isalnum()`, `-`, or `_`: `author 에 허용되지 않는 문자가 있습니다: ...`
  - On failure, it writes `[error] <메시지>` to stderr and exits 2.
  - The code comment refers to the members.md English-name convention, but the actual validation allows Unicode `isalnum()`. It is not limited to ASCII lowercase.
- Path and filename:
  - `date_str = workday.workday_str(now)`.
  - `sessions_dir = <team_root>/memory/team/sessions/<author>`.
  - `log_path = <sessions_dir>/<date_str>.md`.
  - If the `log_path.resolve()` string does not start with the `sessions_dir.resolve()` string, it writes `[error] 로그 경로가 세션 디렉토리를 벗어납니다.` to stderr and exits 2. This is a second defense after author validation.
  - For a valid path, it runs `sessions_dir.mkdir(parents=True, exist_ok=True)`.
- Entry label and body:
  - `time_label = now.astimezone(KST).strftime("%H:%M")`.
  - The entry is exactly in the format `\n## <HH:MM>\n\n<text>\n`. It does not summarize, escape, or trim the text.
  - As noted in the implementation caveat in §1.4, label conversion for naive `now` follows Python local timezone interpretation.
- New file creation:
  - If the file does not exist, it writes frontmatter first.
  - The frontmatter is exactly `---\nauthor: <author>\ndate: <date_str>\nsummary: <summary>\n---\n`.
  - `summary` is the first 100 characters of the first line of `text.strip().splitlines()[0]` if it exists, and an empty string if `text.strip()` is empty.
  - It then writes the entry.
- Existing file append:
  - If the file exists, it does not read, validate, or fix the frontmatter.
  - It appends only the entry in UTF-8 append mode.
  - The one-file-per-workday invariant for the same workday is applied only through the filename. The engine does not modify already-invalid frontmatter.
- Success output: writes `tm-mode log — <author>/<date_str>.md 기록됨` to stdout and exits 0.

### 3.3 context (context collection — `--root` required, `--json` optional)

CLI:

```
python3 infra/teammode.py context --root <팀루트> [--json]
```

`context` does not create or modify files. It reads and structures INDEX, session-log frontmatter, the active marker, and config roles. It does not generate summaries.

- INDEX:
  - The read path is `<team_root>/memory/INDEX.md`.
  - If it is a file, it is read as UTF-8. `OSError` is absorbed as an empty string.
  - If it is not a file, it is an empty string.
- active state:
  - It only checks whether `<team_root>/.teammode-active` exists.
  - In JSON, `"state": "on"` or `"off"`.
  - In text, `state: on (active)` or `state: off`.
- Session-log collection (`_collect_members`):
  - The root is `<team_root>/memory/team/sessions`.
  - If this directory does not exist, the member list is an empty array.
  - Among direct children, it iterates only directories, sorted by `member_dir.name`.
  - In each member directory, among `*.md`, only files for which `_is_session_log_name(p.stem)` is true are considered candidates.
  - The actual `_is_session_log_name` predicate is loose: true when the stem length is ≥ 10, the first 4 characters are digits, the 5th character (`stem[4]`) is `-`, and the 8th character (`stem[7]`) is `-`. It does not check whether month/day positions are digits or whether extra characters follow the stem.
  - If there are no candidates, that member directory is skipped from the output.
  - If there are candidates, it chooses one file with the lexicographically largest stem using `max(logs, key=lambda p: p.stem)`. It does not parse dates.
  - It reads the file as UTF-8, and treats it as an empty string on `OSError`.
- frontmatter parsing (`_parse_frontmatter`):
  - If the text does not start with `"---"`, it returns an empty dict.
  - If the first line is not exactly `---`, it returns an empty dict.
  - It then iterates lines until the closing `---`. If there is no closing line, it iterates to the end of the file.
  - It stores only lines containing `:`, split on the first colon as `key.strip()` / `value.strip()`. This is not a YAML parser. It does not interpret quoting, multiline values, or lists.
  - The collected object is `{author: <member_dir.name>, date: fm["date"] or latest.stem, summary: fm["summary"] or "", file: <team_root 상대경로>}`.
  - If summary is absent, as with old logs, it is an empty string. It does not fall back to full text.
- role augmentation (`_member_roles`):
  - It reads `<team_root>/team.config.json`.
  - It only looks at `members` when it is a list in the JSON object.
  - If each entry is a dict and `name` is a string and `role` is a non-empty string, then `roles[name] = role`.
  - Missing config, parse failure, type mismatch, and exceptions are all absorbed as an empty dict.
  - It adds `role = roles.get(author)` to each collected member dict. If absent, this is Python `None`; in JSON, `null`.
- Text output:
  - The first line is `=== tm-mode context ===`.
  - The INDEX section places `index_text.rstrip()` after `--- INDEX ---`. If it is an empty string, it prints `(INDEX.md 없음)`.
  - The member section title is `--- members (멤버별 최근 작업일 1파일 summary) ---`.
  - If there are no members, it prints `(세션로그 없음 — summary 수집 대상 0)`.
  - If members exist and summary is non-empty, it prints `- <who> [<date>] summary: <summary>`, then `    file: <file>` on the next line.
  - If summary is an empty string, it prints `(summary 없음 — 구로그)`.
  - If role exists, it is `<author>(<role>)`; otherwise it is `<author>`. Only the role in text output passes through `_sanitize_line()`, which replaces DEL or U+0000~U+001F control characters with spaces. The role in JSON output is escaped by `json.dumps` from its original value.
- JSON output:
  - With `--json`, it writes `json.dumps({"state": ..., "index": ..., "members": ...}, ensure_ascii=False)` as one line to stdout.
  - In the current implementation, member objects have the keys `author`, `date`, `summary`, `file`, and `role`.
- Success exits 0. Read failures are reduced to empty values where possible and do not change the exit code.

### 3.4 pull / commit / update (git synchronization — `--root` required)

The three verbs use `infra/git_ops.py` as a common safety layer.

Common git safety mechanisms:

- The default timeout is `DEFAULT_TIMEOUT = 2` seconds (pull/fetch taking more than 2 seconds is a nonfatal failure; local commit/checkout is also enough).
- `git_env()` copies the current environment, forces `GIT_TERMINAL_PROMPT=0`, sets `GIT_SSH_COMMAND` to `ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new -oConnectTimeout=5` if it is absent, and sets `GIT_ASKPASS` to `true` if it is absent. The purpose is to avoid hanging on HTTPS/SSH credential prompts.
- Network-related git calls get `-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=<timeout>`. This applies to pull, fetch, and push. It does not apply to merge.
- `run_git(args, timeout)` runs `git <args>` with stdout/stderr pipes, stdin DEVNULL, and text mode. On POSIX it creates a new process group with `start_new_session=True`, and when `subprocess.TimeoutExpired` occurs, `kill_group()` sends SIGKILL to the entire process group. Kill exceptions are absorbed even on failure.
- `is_git_worktree(team_root)` returns true only when `git -C <team_root> rev-parse --is-inside-work-tree` has rc 0 and stdout is `true`. Exceptions return false.
- External functions aim not to propagate exceptions. Failures are represented as `ok=False` and a `detail` string in dataclass results.

#### 3.4.1 pull

CLI:

```
python3 infra/teammode.py pull --root <팀루트>
```

- Before execution, if it is not a git worktree, the result is `PullResult(ok=False, detail="not a git work tree")`.
- The actual command is `git -C <team_root> -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 pull --ff-only --no-rebase --no-edit`.
- On timeout, detail is `timeout`.
- On execution exception, detail is `exec error: <exc>`.
- If rc is 0, `ok=True`, and detail is the first 200 characters of stripped stdout. If stdout is empty, engine output substitutes `up-to-date`.
- If rc is non-zero, `ok=False`, and detail is the first 200 characters of stripped stderr or stdout.
- Engine success output: `tm-mode pull — 최신화됨: <detail-or-up-to-date>` to stdout, exit 0.
- Engine failure output: `tm-mode pull — 건너뜀(비치명): <detail>` to stderr, exit 1.
- Because of `--ff-only`, it does not automatically create non-ff merges or conflicts.

#### 3.4.2 commit

CLI:

```
python3 infra/teammode.py commit --root <팀루트> --message <메시지> [--push]
```

- If `--message` is absent or its value is falsy, it writes `[error] commit: --message <메시지> 가 필요합니다.` to stderr and exits 2. Empty-string messages are rejected.
- The engine CLI does not expose an option to limit paths. Therefore `git_ops.do_commit(..., paths=None)` is called, and the staging scope is the entire working tree via `git add -A`.
- If it is not a git worktree, the result is `CommitResult(ok=False, detail="not a git work tree")`.
- stage:
  - The command is `git -C <team_root> add -A`.
  - Timeout detail is `add timeout`.
  - Execution exception detail is `add exec error: <exc>`.
  - rc non-zero detail is `add failed: <stderr 앞 200자>`.
- Change check:
  - It runs `git -C <team_root> diff --cached --quiet`.
  - If rc != 0, it treats this as staged changes present.
  - If rc == 0 or there is an exception, it treats this as no changes.
  - If there are no changes, the result is `CommitResult(ok=False, committed=False, detail="nothing to commit")`; it does not create empty commits.
- commit:
  - The command is `git -C <team_root> commit -m <message>`.
  - Timeout detail is `commit timeout`.
  - Execution exception detail is `commit exec error: <exc>`.
  - rc non-zero detail is `commit failed: <stderr-or-stdout 앞 200자>`.
  - If successful and `--push` is absent, the result is `ok=True, committed=True, pushed=False`, and detail is the first 200 characters of commit stdout.
- push:
  - Only when `--push` is present, it runs `git -C <team_root> -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 push`.
  - If push succeeds, the result is `ok=True, committed=True, pushed=True, detail="committed and pushed"`.
  - Push timeout, execution exception, or rc non-zero **does not revert the local commit**. The result is `ok=True, committed=True, pushed=False`, and detail is `committed; push timeout` / `committed; push exec error: ...` / `committed; push failed: ...`.
- Engine success output:
  - Push success: `tm-mode commit — 커밋됨 (pushed): committed and pushed`
  - Push requested but push failed: `tm-mode commit — 커밋됨 (push 실패·커밋은 보존): <detail>`
  - Push not requested: `tm-mode commit — 커밋됨: <detail>`
  - All exit 0. Push failure is not exit 1.
- Engine failure output: no changes, not git, add/commit failure, etc. produce `tm-mode commit — 건너뜀(비치명): <detail>` to stderr, exit 1.

#### 3.4.3 update

CLI:

```
python3 infra/teammode.py update --root <팀루트>
```

`update` is the verb for applying the template upstream. It operates as **file synchronization, not merge**. The constant is `UPSTREAM_REMOTE = "upstream"`, and the synchronization target is the module constant `git_ops.SYNC_PATHS = ["infra"]` (engine path). The CLI cannot change remote/branch/paths. It accepts only one flag, `--dry-run` (preview).

**Why not merge**: The adopting repository is created from a GitHub *template*, so it has **unrelated histories** with upstream (`T-Gates/tm-mode`) and zero common ancestors. Therefore `git merge`/`pull --ff-only` is permanently blocked by `fatal: refusing to merge unrelated histories`. So merge was discarded, and this is implemented as file synchronization that overwrites **only the engine path** from upstream into the working tree with `git checkout`. It works regardless of the history relationship (common ancestor).

**Synchronization targets and protected targets**: Only `SYNC_PATHS` (`infra/`) is overwritten. ⚠️ `memory/`, `team.config.json`, `.git`, and other team-owned files are **never** touched (the checkout pathspec is limited to `infra/`). When adding a new engine directory, extend only `SYNC_PATHS`.

Engine `cmd_update(team_root, dry_run)` delegates in one call to `git_ops.sync_from_upstream(team_root, remote="upstream", dry_run=...)`. The stages of `sync_from_upstream`:

- Stage 1 fetch:
  - Reuses `git_ops.fetch_upstream(team_root, remote="upstream")` (sharing credential blocking, killpg, and HTTP timeout safety mechanisms).
  - If it is not a git worktree, `ok=False` with `not a git work tree`.
  - If the `git remote` list does not include `upstream`, `ok=False` with `no 'upstream' remote`.
  - The actual fetch command is `git -C <team_root> -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 fetch --quiet upstream`.
  - On fetch failure, `SyncResult(ok=False, detail="fetch 실패: <detail>")`. The engine writes `tm-mode update — 건너뜀(비치명): ...` to stderr plus guidance to manually run `git remote add upstream <UPSTREAM_URL>`, then exits 1.
- Stage 2 default branch detection:
  - `detect_default_branch(team_root, remote="upstream")` — uses only local refs (no network or hangs). It does not use `git remote show`.
  - Order: ① final segment of `git symbolic-ref refs/remotes/upstream/HEAD` → ② `main` if `refs/remotes/upstream/main` exists → ③ fallback `main`.
  - `ref = "upstream/<branch>"`. If the ref does not exist, `ok=False, detail="upstream 브랜치를 찾을 수 없습니다: <ref>"`.
- Stage 3 changedness check (idempotent):
  - If `git diff --name-status <ref> -- infra` is empty, `ok=True, changed=False, detail="이미 최신"`. The engine writes `tm-mode update — 이미 최신입니다.` to stdout and exits 0.
- Stage 4 dirty guard (required):
  - If `git status --porcelain -- infra` is not empty (uncommitted staged/unstaged/untracked changes in the target path), it **aborts** with `ok=False, blocked=True`. This is because overwriting would lose them.
  - The engine writes to stderr: "중단: ... 먼저 변경을 커밋하거나 되돌린 뒤 다시 실행하세요(사람 판단 필요)" plus the diff, then exits 1. tm-mode principle: when blocked, do not guess-repair.
  - If status lookup fails or throws an exception, it conservatively treats the path as dirty and aborts.
- Stage 5 dry-run:
  - With `--dry-run`, it stops after stage 4 and fills `SyncResult.diff` with the result of `git diff --name-status <ref> -- infra`, returning `ok=True, changed=False`. **Actual changes: 0.** The engine prints `tm-mode update [dry-run] — 동기화하면 바뀔 파일(infra):` plus the diff and exits 0.
- Stage 6 apply (checkout):
  - The actual command is `git -C <team_root> checkout <ref> -- infra`. It overwrites the working tree and the changes become **staged**.
  - Timeout detail is `checkout timeout`; execution exception is `checkout exec error: ...`; rc non-zero is `checkout 실패: <앞 200자>`.
  - On success, `ok=True, changed=True, detail="동기화 완료(staged)"`. The engine writes `tm-mode update — 엔진 파일 동기화 완료(infra, staged). 바뀐 파일:` plus the diff and guidance that a human should commit directly to stdout, then exits 0.
- **Never perform automatic commit or push**: checkout only leaves changes staged. The engine also does not commit or push — a human reviews what changed and commits directly. Because update does not resolve conflicts, rebase, or merge, it is independent of unrelated histories.

### 3.5 issue (service-slot verb — `--root` required, first positional = subaction)

> **L1 prototype — scheduled for removal.** This verb is an echo prototype from the L1 era. It **only echoes** the canonical input schema as stdout JSON and does not create an actual issue. In the L2 redesign (Option A, 2026-06-25), actions such as issue creation were finalized as **the AI directly calling the registered vendor MCP tool (`mcp__<alias>__create_issue`, etc.)**, so this engine verb is not an execution path. The current conformance scenario 03 (`03-issue-create`) checks this echo contract, so it remains in 0.2 and is scheduled to be removed when the scenario is cleaned up. **Do not grow this verb into an operational CLI** (adding commands that actually create or send via `--title`, etc. would revive an abstraction that was discarded).

CLI:

```
python3 infra/teammode.py issue --root <팀루트> [<action>] [--title <t>] [--body <b>] [--assignee <a>] [--label <l>] [--priority <p>]
```

The altitude of `issue` is the same as `context`. The engine checks whether the issues slot is connected, and if it is connected, echoes the canonical input schema as stdout JSON. It does not interpret the `action_map` in `providers/<provider>.json`, transform provider-specific payloads, call MCP, or create actual issues.

- positional:
  - Only the first positional is `action`. Example: `create`.
  - If there is no positional, `action` is `null`.
  - Second and later positionals are currently ignored.
- Input fields:
  - The canonical field flags are `--title`, `--body`, `--assignee`, `--label`, and `--priority`.
  - Only fields whose value is not `None` enter the `input` object. If the value is an empty string, it can still enter because it is not `None`.
  - If the same field is repeated, only the last value remains.
  - User text is not interpreted and is only serialized with `json.dumps(..., ensure_ascii=False)`. It is not interpolated into shell commands or other JSON strings.
- provider resolution (`_resolve_issue_provider`):
  - If `<team_root>/team.config.json` is not a file, it is unconnected.
  - If the JSON root is not an object, it is unconnected.
  - If `services` is not an object, it is unconnected.
  - If `services.issues` is not an object, it is unconnected.
  - If `services.issues.provider` is not a non-empty string, it is unconnected.
  - `_providers.lookup(provider)` must not be `None` to be considered connected. By default, lookup searches for the repository-root file `providers/<provider>.json` and must pass provider pack validation. Missing provider file, validation failure, and exceptions are all absorbed as unconnected.
  - The engine does not guess provider names or choose a fallback provider.
- Empty slot:
  - Missing config, missing slot, unknown provider, invalid provider pack, and similar cases are all an empty slot.
  - It prints `[info] issues 슬롯이 연결돼 있지 않습니다. team.config.json 의 services.issues 를 연결하세요(tm-connect).` to stdout and exits 0.
  - In an empty slot, it does not echo schema JSON.
- Connected slot:
  - It prints one line of JSON to stdout and exits 0.
  - The exact shape is `{"verb":"issue","action":<action|null>,"service":"issues","provider":<provider>,"input":{...}}`.
  - `input` contains only canonical fields for which a value was given.
  - `provider` is the provider string from config and is a name that passed providers lookup.

### 3.6 memory (memory-file CRUD — `--root` required, first positional = action)

A machine-owned verb for memory files under `memory/`. **Judgment (content, classification, and final weight) belongs to the skill; the machine work (validation, files, INDEX, and commit) belongs to this verb**. Actions are `write` | `delete` | `route` | `unlock`.

**write** — all of `--folder --filename --content --author --weight` are required:

- Automatically stamps 4 frontmatter fields (`created_at`/`updated_at`/`author`/`weight`). Extra fields in existing files (`session`, etc.) are preserved. On a new write, the body is **fully replaced (replace)** — there is no append mode. Recalling with the same content is idempotent (no changes, no commit created).
- `--weight` is the 3-enum `🔥`/`📌`/`📎`. The engine does not guess — it passes the value the skill confirmed with the user.
- Automatically upserts a row in the folder 4-column INDEX (`memory/<folder>/INDEX.md`) + bidirectional backlinks (session-log append + document frontmatter `session:` — advisory and nonblocking, performed *before* do_commit so it is included in the same commit).
- `do_commit(paths, push=True)` — **push failure is nonblocking** (local commit preserved, [warning] only, RC 0).
- Validation: blocks author/filename/folder traversal, symlink escape, and control characters. **The `INDEX.md` filename is rejected for write** (engine-managed file — symmetric with delete).
- **Allowed folders** = static list (`product`, `team`, `team/decisions`, and descendants — general scaffold folders) ∪ **exact top-level folder rows in the root `memory/INDEX.md` routing map** (`` `X/` `` — wrapped in backticks, single segment, trailing slash) and their descendants. File rows (`X/foo.md`), nested folder rows (`X/Y/`), and prose mentions are not recognized (prefix judgment is forbidden). **The block list (`team/sessions`, `team/meeting`) takes precedence over dynamic allow rules and is rejected.** If the root INDEX is missing or unreadable, only the static list is used (conservative fallback). Because `memory route upsert` is already an "intentional registration action", dynamic allow preserves its guard meaning (#51).

**delete** — `--path --author` required: delete file + remove folder INDEX row + session-log backlink + commit (push nonblocking). Missing files are idempotent exit 0 (but OS exceptions such as EACCES from stat are exit 2 — no false success). Rejects deleting `INDEX.md`. Allowed/blocked folder rules are the same as write.

**route** — subaction `upsert` (`--path --desc --author` required) | `remove` (`--path --author`): CRUD rows in the root `memory/INDEX.md` **2-column routing map** (`| 경로 | 여기에 넣는 것 |`). Preserves prose around the table, exact-matches backtick tokens (distinguishes folder rows/file rows), atomic write, idempotent, commit (push nonblocking). `--desc` must not be guessed (required argument). Lightweight traversal guard (blocking `memory/` escape and table-breaking characters).

**unlock** — subaction `begin` | `end`: create/remove the KB direct-edit unlock flag for kb-write-guard (mode 0600). Session id prefers env (`CLAUDE_SESSION_ID`), and falls back to hook relay if absent. TTL (300s) is enforced by the guard — lingering begin flags also expire. The single source of truth for the flag path convention is `infra/hooks/kb-write-guard.py`.

### 3.7 util (instance utility skill management — `--root` required, first positional = action)

Registration management for utility skills in `infra/skills/util/` (the instance-owned layer). Actions are `add` | `remove` | `list`. Updates `util-skills.json` + reflects agent skill symlinks, idempotently. Defends against `--member`/`--skill` traversal. Adding a nonexistent skill is rejected.

---

## §6. Conformance Declaration (Conformance)

tm-mode allows anyone to build an **independent implementation** that follows the same standard, separate from the reference implementation. This section defines the conditions and procedure for an independent implementation to declare itself **"tm-mode compatible"**.

Conformance promise: **compatible implementations can share the same team repo.** Even if member A on a team uses the reference implementation and member B uses an independent implementation, team memory will not break.

### 6.1 Conformance Requirements (all three required)

- **C1 — Compliance with the team memory standard (§1)**: Session log format (location, one file per day, 06:00 cutoff, frontmatter author/date/summary), core directories, INDEX updates, and injection scale (verified with the 0.2 self-check + golden scenario "context lookup"). Bidirectional: produced files comply with the format + the implementation reads a standard team repo and operates correctly.
- **C2 — Preservation of hook and adapter standard semantics (§2)**: Semantics of the four canonical events (especially PreToolUse blocking), invocation of common scripts with the canonical input schema, fallback behavior (no silent skip), and canonical-form rules (no agent-specific notation; reference MCP semantics). **Note: an independent implementation does not need to use the `agents/` structure or Python as-is** — the preservation target is the declaration format (manifest.json and events.json) and semantics, not language or layout.
- **C3 — Pass the conformance kit (§6.4)**: Pass the required checks + attach the result log to the listing application.

**Scope limit**: A conformance declaration is tied to a specific spec_version ("tm-mode compatible (spec 0.2)"). A declaration without a version is invalid. Partial implementations can be listed with a "partial" mark (for example, memory-only covers K1~K2 and K8 + the context lookup and session log writing golden scenarios). Subset appropriateness is subject to maintainer review approval.

### 6.2 Reference Validation Tool — check.py Three Modes

The reference provides three modes through the single tool `conformance/check.py` (the concrete implementation of the conformance kit design from 03):

| Mode | Nature | Content |
|---|---|---|
| `lint` | Static (no engine execution) | The current reference runs only three static checks: `_lint_manifest_canonical` (part of K4: verifies the manifest does not contain `mcp__`/`Write\|Edit`/`apply_patch`), `lint_no_tracked_secrets` (blocks token keys in config/credentials data files), and `lint_skill_canonical` (K7: blocks `mcp__` and direct provider product names in skill bodies). It does not check events/actions completeness, duplicate `(event, script)`, or single-key match rules. |
| `verify` | Dynamic | Runs golden scenarios against **our toolkit** (dogfooding). Requires `--engine` |
| `conform` | Dynamic + Tier | Runs the same scenarios against **any implementation** + calculates a Tier from advisory compliance rate |

- `verify`/`conform` share the same golden scenario definitions (`conformance/scenarios/*.json`) — **scenario = executable spec**. Running them against an empty engine (no-op) makes all scenarios RED = the engine's acceptance tests.
- **Harness interface (the spirit of C2, without mandating language or layout)**: The engine only needs to satisfy `engine.run(argv) → Result(exit_code, stdout, stderr)` and produce file side effects under root. The reference `SubprocessEngine` accepts any `--engine` prefix (for example, `python3 infra/teammode.py`), so it can check implementations in any language. The team root is explicitly injected with `--root` after the verb, and ambient team-root variable leakage is blocked with an env whitelist (P1 double defense). Because the reference engine requires explicit settings (P2), the CLI injects isolated settings under root (`--settings`; other implementations that do not know this flag ignore it as an unknown flag).

### 6.3 Tier Calculation

- **All deterministic scenarios must pass to be compatible.** Tier grade is based on advisory compliance rate: Tier 1 = advisory 100% / Tier 2 = partial advisory / Tier 3 = advisory 0. If even one deterministic scenario fails, `compliant=False` (Tier is not calculated).
- Reference scenario `tier_signal`: deterministic (01, 02, 03, 05) / advisory (04).

### 6.4 Check Items (K1~K8, reference status)

| # | Check | Mapping | Reference status |
|---|---|---|---|
| K1 | Accumulation of produced session logs (one file per day + includes content) | §1.3·§1.5 | Scenario 04 + assertion (`session_log_single_file/contains`). The current check.py does not check for the presence of the three frontmatter fields or whether the filename matches the date. |
| K2 | 06:00 cutoff boundary values (05:59→previous day / 06:00→current day) | §1.4 | `test_workday` (unit) — kit automated-check roadmap |
| K3 | events.json completeness (all canonical event and action keys present) | §2.6 | Not implemented. Current lint does not check this. |
| K4 | Canonical manifest form (grep for agent-specific notation) | §2.2 | Implemented in `lint` |
| K5 | normalize golden test (source terms → canonical schema match) | §2.10 | `test_normalize` (unit) — kit roadmap |
| K6 | Fallback behavior (`[warn]` on unsupported events, no silent skip) | §2.9 | `test_adapter_codex` (unit) — kit roadmap |
| K7 | Canonical skill body form (no `mcp__` or direct product names) | §2.12·§7.3 | Implemented as lint via `lint_skill_canonical` |
| K8 | Core directory structure + new folders listed in INDEX | §1.1 | lint roadmap |

In addition, it runs **five golden scenarios** (turn on → context lookup → issue creation → session log writing → turn off). The reference scenarios are `01-on-banner`, `02-context-injection`, `03-issue-create`, `04-log-accumulate`, and `05-off-persist`. `03-issue-create` is GREEN via the `issue` verb (§3.5) — the scenario sets up (`fs_write`) and cleans up (`fs_delete`) the connected issues fixture itself, so it does not contaminate 04/05 in the shared root. The fixture content in the current `03-issue-create.json` still contains `"spec_version":"0.1"`, but the implementation's `config_is_valid()` only checks truthiness, and the reference implementation version is `install_lib.SPEC_VERSION == "0.3"`.

### 6.5 Listing Procedure and Badge

1. **Application** — Open an issue in the upstream repo (`implementation` label): implementation name, repo, license, target agent/platform, spec_version, and kit result log (before kit publication: C1/C2 self-checklist + records for the five golden scenarios).
2. **Review** — The maintainer checks the kit result + performs a bidirectional interoperability spot check (reference reads a session log produced by the independent implementation, and vice versa). Given the side-project cadence, this may take several weeks.
3. **Listing** — Add it to the README Implementations table (implementation, agent/platform, spec_version, status, verification date).
4. **Status transitions** — On a minor bump, listed implementations are notified. `compatible → stale`: if re-verification is not submitted by the time the next minor after the notified minor is released, the status becomes stale (the maintainer may also state an absolute deadline; partial implementations follow the same rule). `stale/partial → compatible`: submit results against the current spec_version in the existing issue → update the table (no new application required). Withdrawal is available at any time by owner request.

Badge: `![tm-mode compatible](…/tm-mode-compatible%20(spec%200.2)-blue)`. **spec_version must be included** (a declaration without a version is invalid). `partial`/`stale` cannot use the compatible badge (status badges are allowed). Honor-based — false claims may be removed and announced.

### 6.6 Versioning Linkage

Additions or changes to kit check items (K1~K8) are a minor bump. A 0.x conformance declaration applies to that minor; after the 1.0 freeze, this is relaxed to the entire 1.x line. Listing two or more independent implementations triggers the 1.0 freeze + RFC-lite adoption.

---

## §7. Service Slots · Provider Packs

The second axis, orthogonal to the agent axis (§2): the same role (issue tracker, chat, ...) can use different products for each team.

> **L2 = MCP registrar (Option A, finalized 2026-06-25).** Slots **connect (register) the official vendor MCP** selected by the team. tm-mode only performs the connection; for actions such as creating issues or adding calendar events, **the AI directly calls `mcp__<alias>__<벤더도구>`**. tm-mode does not wrap roles in a tool-neutral function contract (`issues_create`, etc.) or relay them through a `role_server` proxy. A provider pack is **registration metadata** that describes which MCP to register in a slot and how (`mcp.register_hint`), plus token issuance guidance (`token_guide`) — it is not an action translation table. Source of truth: `docs/archive/2026-06-25-L2-redesign.md`.

The current implementation ground truth is `providers/*.json`, `infra/providers.py`, `infra/install_lib.py::services_are_valid`, and `infra/credentials.py`. This area has no standalone CLI. Failures are represented not by CLI exit codes, but by `ProviderValidationError`/`ValueError`/`OSError` exceptions or boolean/`None` returns.

### 7.1 Role Slot Declaration + Scope (Required)

`services` in `team.config.json` is a declaration of **role slot -> provider to register (official vendor MCP)**. Role names are fixed to product-neutral vocabulary: `issues`·`chat`·`docs`·`calendar` (only the slot *name* is neutral; the slot *contents* are that provider's actual vendor MCP — tm-mode does not abstract it into tool-neutral functions). In the implementation, if `services` is `None` or `{}`, all slots are valid empty slots. Partially populated slots are also valid.

The shape of a populated slot is the object `{ "provider": <정규 provider>, "scope": <team|personal optional>, <resource_fields...> }`. If `scope` is present in the slot, it must be either `team` or `personal`. Currently, `services_are_valid()` does not make a missing `scope` invalid. It is intentionally left this way so connection skills/consumers can use the provider pack's `default_scope` as the default.

```jsonc
"services": {
  "issues":   { "provider": "linear", "scope": "personal" },
  "chat":     { "provider": "slack",  "scope": "team", "channel_id": "C0123..." },
  "docs":     { "provider": "notion", "scope": "team", "database_id": "..." },
  "calendar": { "provider": "google", "scope": "personal", "calendar_id": "primary" }
}
```

The validation function is `infra/install_lib.py::services_are_valid(services, *, providers_dir=None) -> bool`. However, the "bool return" contract applies when the provider file is missing or the general shape validation fails. If the provider file exists but JSON parsing or provider pack schema validation fails, `ProviderValidationError` from `providers.lookup()` propagates and is not absorbed as `False`.

- `services is None` -> `True`.
- `services == {}` -> `True`.
- If `services` is not a dict, `False`.
- Each role key must be one of `issues`, `chat`, `docs`, `calendar`. Typos such as `tickets` are `False`.
- Each slot must be a dict.
- `slot.provider` must be a non-empty string.
- If `providers.lookup(provider, providers_dir=providers_dir)` is `None`, `False`. The implementation does not guess provider names or choose a fallback provider. JSON/schema errors for provider files that exist propagate as `ProviderValidationError`.
- If `slot.scope` exists, it must be one of `team|personal`. If absent, this function allows it.
- Every field listed in the provider pack's `resource_fields` must be present in the slot as a non-empty string. `None`, missing values, and whitespace-only strings are `False`.
- Extra keys not required by the provider pack are allowed. Unknown slot keys are not rejected so future extensions are not blocked.
- This validation is not used in destructive branches of role detection. `config_is_valid()`/`detect_role()` do not downgrade an existing member config to an adopter config based on the services schema or provider pack presence.

The current provider pack resource fields are `linear=[]`, `slack=["channel_id"]`, `notion=["database_id"]`, and `google=["calendar_id"]`.

### 7.2 Empty Slots = First-Class Citizens (Required)

An unconnected role slot is **not an error; it is a declared state**. If `services` is absent, if `services: {}`, or if a specific role key is missing, that role is an empty slot. The adopter path in install.py starts with `services: {}`.

- The adapter applies the empty-slot-first rule only when it reads `team.config.json` and confirms that `services` is a dict. Missing config files, broken JSON, missing `services`, or non-object `services` are treated as `None` by the adapter and follow the L1 behavior-preservation path (§2.8/§2.9).
- An MCP hook matcher that references an empty-slot provider skips registration regardless of `fallback` and emits `[info]`. If the provider pack cannot be found, the adapter does not guess; as a limited fallback, it only checks whether any slot in the `services` values has the same provider.
- `install-mcp` registers only connected providers under their canonical server-name aliases. If there are no connected providers, it removes the existing teammode MCP block or no-ops, then returns `[info] 연결된 MCP provider 없음 (빈 슬롯)`.
- The `issue` engine verb treats the slot as connected only when `services.issues.provider` is a non-empty string and passes `providers.lookup()`. Missing config, missing slots, unknown providers, and invalid provider packs are all absorbed as empty slots.
- The implementation must not fail installation or session startup because of empty slots.

### 7.3 Service References in Skills and Hooks

- **Skill body**: Speak in terms of role vocabulary (`issues`, `chat`, `docs`, `calendar`). Read the actual product name, issuance link, and issuance steps from provider pack data.
- **Hook matcher**: The 0.2 implementation does not compile-consume `providers/<name>.json.action_map`. The MCP matcher (manifest) uses the canonical server-name scheme from §2.5, and the canonical server name is identical to the provider name. However, the **registration alias** is not the canonical server name; it is `resolve_server_alias(provider)`=`tm-<provider>` (§2.8). Runtime tool names are captured with this alias, and normalization converts them back to the canonical server name so they match the manifest matcher (§6.1).
- `action_map` is currently a reserved field. If present in a provider pack, only its object shape is validated and it is preserved in `ProviderPack.action_map`. If absent, it is `None`. Non-object values such as lists or strings fail provider validation.

### 7.4 Provider Pack Schema and Loader Contract

Provider pack files live by default in `providers/<name>.json` at the repository root. `DEFAULT_PROVIDERS_DIR` in `infra/providers.py` is `providers/` under the repository root, which is the parent of `infra/`. `<name>` is the canonical server name and must exactly match the `provider` field. 0.2 has no separate `canonical_server` field.

Required keys:

| Key | Implemented validation |
| --- | --- |
| `provider` | non-empty string. Must exactly match the file stem when `load_pack()` is called. |
| `token_guide` | object. |
| `token_guide.url` | non-empty string. |
| `token_guide.steps` | list. Element types are not currently validated. |
| `default_scope` | `team` or `personal`. |
| `auth` | One of `api_key`, `oauth`, `bot_token`. |
| `services` | non-empty list, and every element is a non-empty string. |
| `resource_fields` | list, and every element is a non-empty string. Empty lists are allowed. |
| `mcp` | object. |
| `mcp.register_hint` | non-empty string. |

Optional keys:

| Key | Implemented validation |
| --- | --- |
| `action_map` | If present, it must be an object. In 0.2 it is only preserved as a reserved field. |

All unknown top-level keys are rejected. For example, a typo such as `resorce_fields` raises `ProviderValidationError`.

The `ProviderPack` dataclass fields are `provider`, `token_guide`, `default_scope`, `auth`, `services`, `resource_fields`, `mcp`, `action_map=None`, and `raw=None`. The `canonical_server` property always returns `provider`. `services` and `resource_fields` are copied into new lists before storage, while `token_guide`, `mcp`, and `raw` preserve the loaded dict objects.

Function contracts:

- `validate_pack(data, *, expected_name=None) -> ProviderPack`
  - If `data` is not a dict, `ProviderValidationError("provider 팩은 object 여야 합니다.")`.
  - Missing required keys raise `ProviderValidationError("필수 키 누락: ...")` with a sorted list.
  - Unknown keys raise `ProviderValidationError("알 수 없는 키(오타 의심): ...")` with a sorted list.
  - If `expected_name` is not `None`, enforce `data["provider"] == expected_name`. The violation message includes "항등 불변식 위반".
  - On success, returns a `ProviderPack`. It does not read files or disk.
- `load_pack(path) -> ProviderPack`
  - If `path` is not a file, `ProviderValidationError("provider 팩 파일이 없습니다: <path>")`.
  - Reads it as UTF-8 text and parses JSON.
  - JSON parse failures raise `ProviderValidationError("provider 팩 JSON 파싱 실패(<path>): <parser error>")`.
  - Because the file stem is passed as `expected_name` when calling `validate_pack()`, `provider: "notion"` inside `providers/slack.json` is rejected.
- `load_all(providers_dir=None) -> dict`
  - If `providers_dir` is `None`, use `DEFAULT_PROVIDERS_DIR`.
  - If the directory does not exist, returns `{}`. Missing provider packs are a normal state compatible with empty slots.
  - Loads `*.json` files with `load_pack()` in sorted glob order.
  - Validation exceptions are not caught; they propagate to the caller.
  - Returns a `{pack.provider: pack}` dict.
- `lookup(provider: str, providers_dir=None) -> ProviderPack | None`
  - If `<providers_dir>/<provider>.json` does not exist, returns `None`.
  - If the file exists, returns the result of `load_pack()`.
  - It does not whitelist-validate the provider string itself. JSON/schema errors in files that exist propagate as `ProviderValidationError`.

This module has no CLI, so it has no separate exit codes.

### 7.5 Current Four Provider Packs

The current repository only contains `providers/google.json`, `providers/linear.json`, `providers/notion.json`, and `providers/slack.json`. The provider set according to `providers.load_all()` is `{ "google", "linear", "notion", "slack" }`.

| provider | Role (`services`) | `default_scope` | `auth` | `resource_fields` | `mcp.register_hint` |
| --- | --- | --- | --- | --- | --- |
| `linear` | `["issues"]` | `personal` | `api_key` | `[]` | Register the official Linear MCP server under canonical server name `linear` (using a personal API key). |
| `slack` | `["chat"]` | `team` | `bot_token` | `["channel_id"]` | Register the Slack MCP server under canonical server name `slack` (using a bot token, once by the adopter). |
| `notion` | `["docs"]` | `team` | `api_key` | `["database_id"]` | Register the Notion MCP server under canonical server name `notion` (using an integration token, once by the adopter). |
| `google` | `["calendar"]` | `personal` | `oauth` | `["calendar_id"]` | Register the Google Calendar MCP server under canonical server name `google` (localhost OAuth/PKCE). |

All issuance guidance data lives in the provider pack's `token_guide`. Connection skills must read and present this value as-is instead of hardcoding it.

| provider | `token_guide.url` | `token_guide.steps` |
| --- | --- | --- |
| `linear` | `https://linear.app/settings/api` | Go to Linear Settings -> Security & access -> Personal API keys; click `Create key` and enter a label; copy the generated key and have each person paste it once. |
| `slack` | `https://api.slack.com/apps` | Create New App -> From scratch; grant bot scopes in OAuth & Permissions, then install it to the workspace; copy the Bot User OAuth Token (`xoxb-…`) and specify the channel in team config. |
| `notion` | `https://www.notion.so/my-integrations` | Create a New integration; copy the Internal Integration Token (`secret_…`); share the integration in the target page/DB's Connections, then specify the DB in team config. |
| `google` | `https://console.cloud.google.com/apis/credentials` | Create an OAuth client ID; approve the consent screen through a localhost redirect (PKCE); after connection, auto-discover calendar IDs and specify the calendar to use in config. |

Each provider pack's `action_map` is currently `{}`. The fact that it is `{}` only means the reserved field is empty; it does not mean issue/chat/docs/calendar action translation is implemented.

### 7.6 Token Vault Location, Scope, and Security Invariants

`infra/credentials.py` is a 0.2 plaintext JSON vault. There is no OS keychain, encryption, automatic team sharing, remote sync, or publish/fetch/share verbs. In 0.2, even team-scope tokens are entered directly by each member into their own local vault. `team` and `personal` are namespaces in the same file, not transmission policies.

Storage location:

- `credentials_dir() -> Path` returns `$XDG_DATA_HOME/teammode/credentials`.
- If `XDG_DATA_HOME` is absent, it uses `~/.local/share/teammode/credentials`.
- The function itself does not create the directory. `_secure_dir()` creates it on write.
- The vault file is a single `<credentials_dir>/default.json` (multi-team unsupported, 2026-06-21). `_vault_path(team=None)` returns this file regardless of the `team` argument — it is not bound to the team name, so changing `team.name` does not change vault keys. `migrate_legacy_vault(old_team)` migrates the pre-single-file team-name-keyed vault (`<old_team>.json`) to `default.json` once; if `default.json` already exists, it no-ops (idempotent). If multi-team support becomes necessary, revive per-`team` filenames then.
- When creating or repairing the directory, it attempts `0700`. `chmod` failures are non-blocking.
- After saving, the file reasserts `0600`. Even if an existing file mode has become broader, the next `store()` returns it to `0600`.
- Because this is plaintext JSON, it must not be placed in a synced folder (Syncthing/Dropbox, etc.). `store()` heuristically detects common synced-folder path patterns (`dropbox`·`onedrive`·`mobile documents`·`icloud`·`/sync/`, etc.) and **warns** (SEC-4). It does not reject — Syncthing can use arbitrary paths, so complete detection is impossible, and blocking false positives would stop work; the defense line is a warning. Git tracking is not checked, and `.gitignore` is a separate defense line.

Identifiers and scope:

- The public scope constants are `SCOPE_TEAM = "team"` and `SCOPE_PERSONAL = "personal"`.
- The only allowed scopes are exactly `team` and `personal`. Any other value raises `ValueError("invalid scope (allowed: team, personal)")`.
- The `key` identifier must pass the regex `^[A-Za-z0-9_.\-]+$`. `team` is no longer a path component (single vault), so it is not validated or rejected in normal CRUD paths — only the `old_team` argument of `migrate_legacy_vault(old_team)` is validated with the same rule (legacy filename safety).
- `/`, spaces, empty strings, NUL, `;`, and other characters are rejected.
- Identifiers made entirely of dots, such as `"."`, `".."`, and `"..."`, are rejected separately even if they pass the regex.
- Identifier error messages do not echo the input value itself. Messages have the form `invalid <team|key> identifier (allowed: [A-Za-z0-9_.-])`.

Masking invariants:

- Plaintext tokens are not printed to stdout, stderr, logs, exception messages, or return values.
- The return value of `store()` is the vault file `Path`, not the token.
- `list_keys()` returns only key names, not values.
- When broken JSON, symlink reads, or IO errors are encountered on the read path, file contents or tokens are not exposed through exceptions; they are treated as an empty vault.

Symlink/file handling:

- Reads use `os.open(path, O_RDONLY | O_NOFOLLOW)`. Missing files, symlinks, OS errors, UTF-8 decode failures, JSON parse failures, and top-level non-objects are all treated as `{}`.
- Writes prepare the parent and then use `os.open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0o600)`. If safe open fails because of a symlink or similar case, it raises `OSError("vault path is not a regular file (refusing to write)")`.
- Saved JSON is the result of `json.dumps(data, ensure_ascii=False, indent=2)`.
- Current writes are not atomic replacement via temp-file rename; they directly truncate/write the target file. Therefore a "0600 plaintext vault" is implemented, but crash-safe atomic writes are not.

### 7.7 Credentials Function Contracts

This module also has no CLI, so it has no exit codes. The public function contracts are as follows.

- `credentials_dir() -> Path`
  - Reads the `XDG_DATA_HOME` environment variable to calculate the vault directory path.
  - No side effects. Does not create the directory.
- `store(team: str, scope: str, key: str, token: str) -> Path`
  - Validates `scope` and `key` first. `team` is validated when calculating the vault path.
  - If `token` is not a string, `ValueError("token must be a string")`.
  - Reads the existing vault. Missing, broken, or unreadable vaults are treated as an empty dict.
  - If `data[scope]` does not exist, creates `{}`. If the existing `data[scope]` is not a dict, replaces it with `{}`.
  - If the same `(scope, key)` already exists, overwrites it. Idempotency means "the last stored value is the loaded value".
  - On successful save, returns the vault file path.
  - Write rejections such as symlinks propagate as token-free `OSError`.
- `load(team: str, scope: str, key: str) -> Optional[str]`
  - Validates `scope` and `key`.
  - Missing vaults, read errors, broken JSON, missing sections, non-object sections, and missing keys all return `None`.
  - Returns the stored value only when it is a string. If it is not a string, returns `None`.
  - Does not modify the file.
- `delete(team: str, scope: str, key: str) -> bool`
  - Validates `scope` and `key`.
  - If the section does not exist or the key does not exist, returns `False`.
  - If the key exists, deletes it, writes the file again, and returns `True`.
  - If the section becomes empty after deletion, removes the scope key itself from the top-level JSON.
  - Deleting a missing value is not an exception; it is a no-op false.
- `list_keys(team: str, scope: str) -> list`
  - Validates `scope`. There is no key argument.
  - If the section does not exist or is not a dict, returns `[]`.
  - Returns sorted section key names. Token values are not returned.
- `file_mode(team: str) -> Optional[int]`
  - Returns `stat.S_IMODE(os.stat(path).st_mode)` for the vault file.
  - Missing files or stat errors return `None`.
  - This is a test/validation helper and does not modify the file.

Remaining implementation gaps:

- Automatic distribution/sharing/syncing of team-scope tokens is not implemented. Team-scope provider and instance values may be committed to config, but each person must enter tokens in their local vault.
- The credentials vault is plaintext JSON. OS keychain/encryption is not implemented.
- Provider `action_map` interpretation, provider-specific payload translation, and actual MCP calls are outside the implementation scope of this section.
- Because provider packs reject top-level unknown keys, adding a new top-level field requires extending `_KNOWN_KEYS` in `infra/providers.py` first.

---

## Appendix A. Design ↔ build reconciliation (code is the source of truth — closed open issues + remaining gaps)

### A.1 Open issues closed by implementation (draft → closed)

| Original open issue (spec) | Closed decision (code-based) | Supporting code |
|---|---|---|
| 04 §12-1 Python lower bound (3.9? 3.10?) | **3.9** (`MIN_PYTHON`) | `install_lib.MIN_PYTHON` |
| 04 §12-2 Whether install writes the first session log | **It does not** (directory only). The first log is written by the first work-session hook | `scaffold_memory` (no log creation) |
| 04 §12-3 If `--yes` cannot determine the name | **exit 3** (do not infer identity) | `bootstrap` (member_name None → 3) |
| 04 §3 Role classification criteria | spec_version + team.name non-placeholder = team member; otherwise, introducer | `config_is_valid`·`detect_role` |
| 04 §5/§6 Name collision | Deterministically classify same person / different person by identity (git email comment) → different person exits 3 | `register_member` (ConflictError) |
| 04 §9/M6 env variable name (LEGACY_TOOL_HOME vs TEAMMODE_HOME) | Standardized on **`TEAMMODE_HOME`** (matches runtime hook code). Discard the erroneous `LEGACY_TOOL_HOME` in 01 Appendix A | `install_lib.ENV_VAR`·3 hook types |
| 04 §10 Real-host write gate | Skip wiring unless `--yes` (real install) or `--settings` (isolated) is present | `bootstrap` (wire gate)·`_dispatch` |
| 05 Entire document (design draft) | tm-onboard SKILL.md was **actually written** | `infra/skills/base/tm-onboard/SKILL.md` |
| 05 Obsidian registration mechanism | Standalone opt-in action via `--register-obsidian`·merge·non-fatal·later registration | `register_obsidian`·`register_obsidian_vault` |
| 02 §5 `sync` with no flag | Preserve existing managed state (self-heal `_infer_existing_mode`); if there is no owned content, install only base (treat as initially off) | adapter `_infer_existing_mode`·`_wanted_entries` |
| L2 Two safety hooks | `auto-commit.py`·`confirm-action.py` files exist + manifest registration | `infra/hooks/manifest.json`·`infra/hooks/auto-commit.py`·`infra/hooks/confirm-action.py` |
| L2 MCP wiring | Claude/Codex adapter `install-mcp` + install wire pre-call implemented | `infra/agents/claude/adapter.py`·`infra/agents/codex/adapter.py`·`infra/install_lib.py` |
| L2 Skill installation | Claude/Codex adapter `install-skills` + install wire post-call implemented | `infra/agents/claude/adapter.py`·`infra/agents/codex/adapter.py`·`infra/install_lib.py` |
| L2 Provider packs | Four provider JSON files exist for Linear/Slack/Notion/Google | `providers/{linear,slack,notion,google}.json` |
| L2 `team.config.json services` schema | Role slots (`issues/chat/docs/calendar`) → `{provider, scope?, <resource_fields...>}` object. Empty slots allowed; validated based on provider packs | `install_lib.services_are_valid`·`infra/providers.py` |
| L2 Provider pack schema | Finalized as `provider`, `token_guide`, `default_scope`, `auth`, `services`, `resource_fields`, `mcp`, optional `action_map` | `infra/providers.py::validate_pack`·`providers/*.json` |
| L2 Credentials vault | Implemented a local plaintext JSON vault with store/load/delete/list/file_mode | `infra/credentials.py` |
| L2 Connection skill separation | tm-onboard only suggests L2; tm-connect performs the actual connection | `infra/skills/base/tm-onboard/SKILL.md`·`infra/skills/core/tm-connect/SKILL.md` |
| Member role field | Finalized as `team.config.json` `members: [{name, role?}]`; install upserts only its own entry | `install_lib.upsert_member_role`·`scaffold_memory` |
| Default team personality output | Implemented introducer config defaults for `greeting`/`farewell` and engine on/off output | `install_lib.write_introducer_config`·`teammode.cmd_on/off` |

### A.2 Behaviors present only in code (not specified in the spec → made explicit in the body)

- **Full engine verb set (§3)**: 02/01 had no verb contract — newly specified on/off/log/context/pull/commit/update in §3. `--root` required·do not trust env·explicit settings for on/off (P2)·06:00 cutoff log·context state/json·git verbs reduced to non-fatal behavior.
- **`enforcement` manifest field (§2.2)**: Not mentioned in the 02 draft; the reference adapter uses it in practice (strengthened warning on block fallback) → finalized in the body.
- **Automatic upstream fetch notification on on (§3.1)**: No merge·fetch only. Consolidated the partial 02/04 mentions into §3.1.
- **install dispatch mode (§4.1·§2.1)**: Compatibility interface for `install.py --<agent> sync/uninstall`.
- **`spec_version` string in the conformance 03 fixture**: The fixture content in `03-issue-create.json` still contains `"spec_version":"0.1"`. This is not a supported-version declaration; it is a valid config fixture for the scenario, and the single source of truth for the reference-supported version is `install_lib.SPEC_VERSION == "0.3"`.

### A.3 Remaining gaps (code still falls short of the spec goals — non-normative)

- **Agent memory leakage in common hooks**: The design goal is to isolate agent-specific memory under `agents/<name>/`, but the current `session-start.py`/`confirm-action.py` directly know the Claude output schema and Codex limitations. `session-log-remind.py` resolved this gap in the 2026-06-21 redesign by switching to plaintext stdout output.
- ~~**Claude fixed in bootstrap verify**: wire calls the adapter for each detected agent, but verify runs `teammode.py on` regardless of the detection result, and engine `on` always loads only the Claude adapter.~~ → **Closed**: verify removed the `on` call and now uses only `context` (`auto_update_on_start` avoids the automatic commit side effect; heterogeneous adversarial review B1), so adapter loading itself disappeared.
- ~~**Codex dispatch gate mismatch**: The Codex adapter settings option is `--config`, but the `install.py --<agent>` dispatcher gate recognizes only `--settings`/`--install`. `--codex --config <path> sync` exits 2 before reaching the adapter.~~ → **Closed**: the gate became agent-aware and recognizes `install_lib._AGENT_WIRE[agent]["flag"]` (claude=`--settings`, codex=`--config`) as isolated intent. Missing values (where the next token is a verb/option, as in `--config sync`) are a clear exit 2. Agents not registered in `_AGENT_WIRE` accept only the conservative fallback (`--settings`/`--install`).
- **Injection scale branching not implemented (§1.6)**: The reference `session-start.py` always injects based on summary lines; there is no full-text for ~4 people / 5+ people branch. It is not a 0.2 automated-check target, so it is not non-compliant, but it remains a gap against the spec goal.
- ~~**conformance scenario 03 (`issue create`)**: Engine verb not implemented → exit 127 (intended RED, service slot L2).~~ → **Closed (0.2)**: Implemented the `issue` verb (§3.5), 03 GREEN.
- **lint check coverage**: The reference `check.py lint` runs part of K4 (manifest normal-form grep), token-key data-file lint, and K7 (skill body normal form). K3 events/actions completeness, duplicate `(event, script)`, match single key, and K8 INDEX listing are not implemented.
- **`--update` flag unused**: It is parsed in the install.py CLI (`Options.update`), but bootstrap does not use it (idempotent reruns effectively serve as update). Specified in §4.1.
- **`--json` role output not implemented**: The structured role output (`--json`) for install required by 05/04 is not implemented → tm-onboard works around this by directly checking `config_is_valid` (§5.2).
- ~~**Codex real hook input schema unverified**: normalize assumes a Claude-like shape (§2.11) — finalize after capturing the real Codex environment.~~ → **Closed**: Input and blocking semantics in both directions were finalized by the 2026-06-21 real-environment capture (`tool_name`/`tool_input`, apply_patch=`tool_input.command`) + 2026-07-03 blocking semantic live verification (codex-cli 0.142.5, §2.11).
- **install.py uninstall does not reclaim MCP/skills**: install wire calls `install-mcp`·`install-skills`, but `cmd_uninstall()` calls only Claude `Adapter.uninstall()`, so it does not remove MCP registrations or skill installation traces.
- **`--settings` meaning mismatch**: bootstrap interprets `--settings` as an isolation directory, but uninstall uses the same value directly as the settings file path.
- **No normalize path validation**: `tool_input.file_path` is copied into `files` without absolutization/path escape validation, and `<script>` is executed as `HOOKS_DIR / script` without filename/path escape validation.
- **Team personality custom banner input**: Default `greeting`/`farewell` generation and on/off output are implemented, but the onboarding UX that accepts custom banner content and replaces `memory/banner.txt` remains a possible extension.
- **Fixed workday timezone**: The reference uses the KST constant (`workday.KST`), and `team.config.json team.timezone` injection is not connected (possible extension).
- **Windows native support (env setx·hook interpreter·POSIX audit)**: Implementation complete (§4.8·§2 — `is_windows` branch, setx/reg env, `sys.executable` hook command, POSIX assumption removed). **However, because the reference build was written on Linux, the Windows branch has only been verified through setx/reg subprocess mocking (runner injection) + platform injection** — live measurement in a native Windows environment is recommended for registry persistence, new-session env reflection, and path interpretation (this is not a code gap, but a verification-environment limitation).

---

## Appendix B. Open Items (open)

- [ ] Scope for automating adopter commit/push guidance (install.py boundary).
- [x] Confirmed the real schema for Codex hook input JSON (2026-06-21 real-environment capture: apply_patch=`tool_input.command`) + Codex expression of PreToolUse blocking semantics — finalized through real verification on 2026-07-03 (codex-cli 0.142.5): Claude-compatible wire (`PreToolUsePermissionDecisionWire` — parsing `hookEventName`/`permissionDecision`/`permissionDecisionReason`), exit 2 = blocking + stderr reason channel, only `deny` is supported (`allow`/`ask` unsupported). The reference blocking hook (deny JSON + exit 2 + stderr) satisfies the contract (§2.11). (The possibility of Hermes expression is carried over separately.)
- [ ] (D1) Codex hook trust gating (`trusted_hash`) not yet investigated — real-environment confirmation is needed for whether hook registration requires trust confirmation and whether there is a re-approval UX when config changes.
- [ ] Real investigation of Hermes event mapping (pre_llm_call≈UserPromptSubmit, on_session_start≈SessionStart — recheck).
- [ ] Manifest lookup cost for normalize (reading the file on every invocation — need for caching).
- [ ] Canonical server-name representation for multiple instances of the same provider and providers duplicated across roles (remaining limitation of deciding canonical server name = provider identifier).
- [ ] Output schema for install role `--json` (§5.2 workaround resolution).

---

## Appendix C. Version History · Changes Compared to 01~05

- **2026-06-16** — Implementation realignment — reflected the code ground truth in detail in §2~§5,§7 (codex dev-cycle)
- **0.3** — **engine verb contract change (minor bump, §0.4)**: Formalized adding `memory` (write/delete/route/unlock) and `util` (add/remove/list) to the known verbs (new §3.6·§3.7 — making the verbs that landed in code first true in the spec). Added a **dynamic allow rule for top-level folders listed in the root INDEX routing map** to the memory allowed folders (exact folder rows only · blocked takes precedence · conservative fallback, #51). Reject writes to filenames named `INDEX.md` (symmetric with delete). Updated the value flag allowlist and boolean flags (`--dry-run`) against the actual code (§3). **Removed a team-specific folder from the static allowlist (breaking)** — it was a hardcoded product from a specific team domain. Team-specific folders are unified behind route registration first, and unregistered write/delete operations are rejected (+ registration command hint).
- **0.2** — **engine verb contract change (minor bump, §0.4)**: Added the eighth engine verb, `issue` (§3.5). After checking the `issues` slot provider, only echo the canonical input schema as stdout JSON (no action_map interpretation or payload conversion — that belongs to adapters/skills). Added `--title --body --assignee --label --priority` to the value allowlist, and formalized positional subaction parsing (§3). Closed conformance 03 (RED→GREEN). Added the harness `fs_delete` action (scenario self-teardown).
  - **L2 (service connection) build**: provider packs (`providers/{linear,slack,notion,google}.json` — token_guide · auth · default_scope · resource_fields, §7) + config `services` extended object schema (§7.1) + adapter `install-mcp` (§2.8, empty-slot sync correction §2.9) + adapter `install-skills` (§2.12) + `install.py` wire-level multi-verb integration (§4) + credentials vault (`infra/credentials.py`, per-user input, 0600, §5.4·§7.5) + two safety hooks (auto-commit · confirm-action) + `tm-connect` skill (§5.4) + K7 skill lint (§2.12).
  - **config.members member role (§1.1, L2-A2)**: `members:[{name, role?}]`, each user upserts their own entry (no touching others). Added the `role` field to `context` verb output (§3.3 — additive; null when unregistered/omitted; backward compatible). The members block is separated from role validation (`config_is_valid`). Reject newlines/control characters in role (free vocabulary).
- **0.1 (previous single edition)** — Consolidated the scattered specs 01 (official), 02 (official), 03 (official), 04 (0.1-draft), and 05 (0.1-draft) into a single authoritative document. Unified spec_version, terminology, and notation conventions. Promoted the 04/05 drafts to build-baseline closed status (Appendix A.1). Formalized engine verbs (§3), the `enforcement` field, and install dispatch mode that previously existed only in code. Listed remaining gaps in Appendix A.3.

**Major changes compared with 01~05 → SPEC:**
- After resolving the `0.1-draft` status of 04/05, the current document is a single cross-area `0.2` that includes the issue verb and the L2 service connection implementation.
- Resolved the self-contradiction in 04 §9 between `LEGACY_TOOL_HOME` and `TEAMMODE_HOME` → unified on `TEAMMODE_HOME` (discarding the typo in 01 Appendix A).
- Added the engine verbs chapter (§3) — the on/off/log/context/pull/commit/update contracts were not present anywhere in 01~05.
- Finalized the `enforcement` field from 02 in the body (not mentioned in the draft, but used in the actual code).
- Finalized the Obsidian view design from 05 as the `--register-obsidian` implementation contract (§4.7).
- Promoted `scope: team|personal` into §7 (05 §5 premise → official) and integrated the v1 provider matrix (Linear · Slack · Notion · GCal).
- Compared the "conformance kit concept" from 03 against the real three-mode reference `check.py` (§6.2).

---

## Appendix D. This SPEC replaces 01~05 — reference list requiring repointing on removal

> This SPEC replaces `spec/01`~`spec/05`. **Do not remove them yet** — the references below point to old paths/sections, so this list must be repointed to the new section numbers in `docs/spec/` before removing the spec/ files. (List only — not modified in this work.)

**Documents and entry points:**
- `README.md` — "스펙: 설계 폴더 `spec/` — 01 팀메모리 · 02 … 05 onboard." (entire list → `docs/spec/README.md`)
- `AGENTS.md` — "설계 스펙(`spec/04-install.md`·`spec/05-onboard-skill.md`) 확인."
- `infra/skills/base/tm-onboard/SKILL.md` footer — "`spec/04-install.md`·`spec/05-onboard-skill.md`를 확인."
- `conformance/scenarios/README.md` — spec 02/03 references in the top-level description, mode descriptions, scenario table, and harness interface description.

**Code docstrings and comments (no behavior change, reference text only):**
- `infra/install.py` top-level docstring — references spec/04 and spec 02 invariants.
- `infra/install.py` `register_obsidian()` docstring — references spec/05 opt-in.
- `infra/install_lib.py` top-level docstring and major section comments — reference spec/04 and specs 01/02/05.
- `infra/install_lib.py` `_INDEX_MD`·`_MEMBERS_HEADER` scaffold strings — spec 01 §2.1 and old members.md role/lowercase wording.
- `infra/install_lib.py` env/Obsidian-related comments and docstrings — reference specs 01/05.
- `infra/teammode.py` `_render_banner()` docstring — old §11.5 banner reference.
- `infra/teammode.py` `_validate_author()`, `_frontmatter()`, `_is_session_log_name()`, `_parse_frontmatter()`, `_collect_members()`, `_read_index()` docstrings — reference spec 01 and old sections.
- `infra/workday.py` top-level docstring and timezone comment — references spec 01 §3.2.
- `infra/agents/claude/adapter.py` top-level docstring, design invariants, and translation/wiring/MCP/skill comments — reference spec 02 and old sections.
- `infra/agents/claude/normalize.py` top-level docstring and normalize/filter/lookup docstrings — reference spec 02 §6 and the old normalize section.
- `infra/agents/codex/adapter.py` top-level docstring, Codex traits, and fallback/MCP comments — reference spec 02 and old §11.11/§7.
- `infra/agents/codex/normalize.py` top-level docstring — references spec 02 §6 and "Appendix B / draft §12 unresolved".
- `infra/hooks/session-start.py` top-level docstring and `_team_root()` docstring — reference specs 02/04/01.
- `infra/hooks/session-log-remind.py` top-level docstring and `_team_root()` docstring — reference specs 02/01.
- `conformance/check.py` top-level docstring, `_session_log_files()`, Tier output comment, `_lint_manifest_canonical()`, and `SubprocessEngine.run()` comment — reference specs 01/02/03 and old §11.x.

**Test docstrings (reference text only — behavior-independent):**
- `tests/*.py` many files — `test_workday`(spec 01 §3.2)·`test_context`(spec 01 §4)·`test_log`(spec 01 §3)·`test_normalize`(spec 02 §6)·`test_adapter_claude/codex`(spec 02 §4·§5·§7)·`test_install_l1a~l1e`/`test_install_golden`/`test_install_l1b`(each section of spec/04)·`test_register_obsidian`(spec/05).

**Note**: `BUILD-LOG.md`·`CHECKLIST.md` also contain many spec references, but they are history documents, so their repoint priority is lower (follow-up if desired).

> ⚠️ Some section numbers in 02 draft (§11.x: §11.5 banner·§11.11 Tier·§11.12 check 3 modes) do not exist in the body of `spec/02-hook-manifest.md` (remaining references pointing to draft §11.x). SPEC mapping: §11.5→§3.1(banner), §11.11→§6.3(Tier), §11.12→§6.2(check 3 modes). Correct them together when repointing.
