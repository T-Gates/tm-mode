# Introducer Onboarding Scenario

This is the complete user journey where an introducer proceeds from clone → `tm-join` L1 setup → `tm-connect` L2 service connection.

---

## Scenario 1 — Introducer

**I am the first person on the team to turn on tm-mode.** The empty repo(or a repo that only has existing code) has no `team.config.json`. When I run setup, `install.py` sees the missing config via `detect_role()`, determines **`role=introducer`**, and **writes a new config**(team name, default greeting/farewell, and empty services). Team members who join later read this config.

> Observer view: the branch point for the introducer path is `install_lib.detect_role()` → `config_is_valid()`. If there is no `team.config.json`, no `spec_version`, or `team.name` is a placeholder(`""`/`changeme`/`todo`/`your-team-name`/`team-name`/`tbd`/`placeholder`), the role is **introducer**. Therefore only the introducer path calls `write_introducer_config()` inside `scaffold_memory()`, creating the config(`install_lib.py:592`).

### Phase ① Repo Clone

**(a) Input** — I clone an empty team repo(or the tm-mode template) and launch the agent inside it.
```bash
git clone git@github.com:our-team/our-repo.git
cd our-repo
```
**(b) Agent** — Does nothing yet. It waits until I talk to it.
**(c) Screen** — Only the shell prompt. No tm-mode traces(no `team.config.json`, `memory/`, or `.teammode-active`).
**(d) Next** — Usually I ask for setup right away, but because this is a new repo, I may first ask what the repo is for. → Phase ①.5(or straight to Phase ②).

### Phase ①.5 First Curiosity — "이거 뭐하는 레포야?"

**(a) Input** — Before setup, I ask out of curiosity:
> "이거 뭐하는 레포야?"

**(b) Agent** — Reads `README.md` and `AGENTS.md`, then answers **in human language**. It does not make the user memorize anything, and naturally includes:
- **Identity**: a cross-agent team collaboration toolkit for AI coding agents like Claude Code and Codex.
- **One-line value**: "소통하지 않아도 팀이 뭘 하는지 안다" — it gathers team context into one git repo and automatically injects it at session start.
- **Easy adoption**: one sentence, "이 레포 셋업해줘". No need to read settings or docs first(zero entry barrier).
- **Compounding value**: as session logs accumulate, the automatically injected context gets richer; newcomers start on day one with the full history. It gets stronger the more it is used.
- **The record also stays useful to individuals**: reasons for decisions, blocked points, and what happened that day remain available for retrospectives, resumes, cover letters, and blog material.
- **Honest status**: L1(team memory, automatic context injection, Obsidian view) works; L2(service integration) is WIP.
- ⭐ **AI behavior guideline**: do not list the values above abstractly. **Infer the user's situation(role/interests) and give one tailored example.** Pick one scene that will resonate most with that person.
  - Example) Developer → "3개월 뒤 '이 설계 왜 이렇게 했지' 싶을 때, PR 뒤질 필요 없이 그날 세션로그에 이유가 적혀 있어요."
  - Example) Job seeker → "자소서 쓸 때 이번 분기에 뭘 했는지 막막하잖아요. 로그가 '언제 뭘 왜 했는지'를 다 말해줘요."
  - Example) Blog operator → "막혔다 푼 과정이 그대로 남으니, 그게 곧 글감이 돼요."

**(c) Screen** — The agent's natural-language explanation(not a terminal command). Order: identity → one-line value → (easy adoption, compounding value, record usage) → status → one tailored example → setup suggestion.

**(d) Next** — If interested, the user says "그래 셋업해줘" → Phase ②. If not interested, they can just close it; there is no pressure.

### Phase ② `tm-join` L1 Setup

**(a) Input** — In **natural language**, not a slash command:
> "이 레포 셋업해줘"

**(b) Agent** — Follows the `tm-join`(currently `tm-onboard`) skill. It first explains that "`install.py` automatically determines introducer/member from `team.config.json` validity," and delegates to one command instead of reproducing the steps manually. `--member-name` is not a branch switch; it is the argument that sets the author name for both paths.
```bash
python infra/install.py --root . --member-name alice --yes
```
- `--root .` : the team root is **explicit only**(do not trust env `TEAMMODE_HOME`; use `_resolve_root`). No guessing.
- `--yes` : the gate that **wires(writes) hooks into the real `~/.claude/settings.json`**. Without it, wire is skipped.

**(c) Screen** — The `install.py` bootstrap flows in this order: `①preflight ②detect ③role ④scaffold ⑤wire ⑥env ⑦verify`. Since there is no config, `role=introducer`. Actual output(tags and strings are exactly as in the code):
```
[plan] team_root=/abs/path/our-repo
[plan] role=introducer (team.name 기본='our-repo')
[plan] agents=['claude']
[plan] member_name=alice
[scaffold] memory/ 구조·members.md 등재 완료 (role=introducer).
[wire] claude MCP 등록 동기화 완료
[wire] claude 훅 동기화 완료 → /home/me/.claude/settings.json
[wire] claude 스킬 심링크 완료 → /home/me/.claude/skills
[env] /home/me/.bashrc 에 TEAMMODE_HOME 주입 (신규 주입).
[verify] 설치 검증 OK — members=1 (팀모드는 꺼둠).
[done] 설치 완료. 팀모드를 켜려면 `tm on`(또는 /tm) 하세요.
```
- exit code **0**.
- The default `team.name` is the repo name from the git remote(`repo_name_from_remote`), or the folder name if there is no remote. In the example above, remote → `our-repo`.
- Side effects(introducer only): creates a new `team.config.json` — `spec_version="0.2"`, `team.name`, `team.greeting`(`"our-repo 팀모드 ON"`), `team.farewell`(`"수고하셨습니다 — our-repo"`), `admin_contact="alice"`, `services: {}`(all slots empty), and `members: [{name:"alice"}]`. It also creates `memory/INDEX.md`, `memory/team/members.md`(`- alice  <!-- id: <git email> -->`), `memory/team/decisions/current.md`, `memory/banner.txt`, and the empty session directory `memory/team/sessions/alice/`. **It does not write the first session log**(M2 — directory only).

> Blocking points / points that require a person / branches:
> - **If neither `--yes` nor `--settings` is present**, wire, env, and verify are skipped; setup ends after scaffold(exit 0). The screen shows `[wire] 건너뜀 — 실호스트 배선은 --yes(실설치) 또는 --settings(격리) 필요. 스캐폴드는 완료(메모리는 준비됨).` → state remains `off`.
> - With **`--dry-run`**, it prints `[plan]`, then only `[dry-run] 변경 없음 — 계획만 출력했습니다(settings·memory·env 무접촉).`, and exits 0. No files are created.
> - With **`--settings <격리경로>`**, the real host is untouched: `[env] 건너뜀 — 격리 모드(--settings): 실 호스트 env … 무접촉.`; verify calls only `context`(installation check — settings do not matter because team mode on is not used). For isolated tests and CI.
> - **If git remote authentication is missing**, stderr shows `[warn] git 원격 인증 미확인 — 로컬 L1 은 진행(협업 시 push/pull 막힘).` (nonfatal; continues).
> - **If the name cannot be determined**(no `--member-name` and no git user.name) → stderr `[error] 멤버 이름을 정할 수 없습니다. --member-name <영문이름> 으로 지정하세요…` + exit **3**. A person supplies the name and reruns.
> - **preflight failure**(Python below 3.9 / missing git binary / missing team marker) → stderr `[error] preflight 실패: …` + exit **2**. No changes.

**(d) Next** — The agent translates the result into human language: "팀을 새로 만들었습니다(도입자). 메모리 구조가 섰고 훅이 실 settings 에 배선됐어요. **갓 만든 팀이라 세션로그는 0** — 다음 작업부터 자동 기록·주입됩니다." Then it shows the first value(L1).

**First value(after setup)** — The agent runs:
```bash
python infra/teammode.py context --root . --json
```
**Screen** — `state=off`(installation does not turn on team mode; it remains off until `tm on`, with no marker), one alice entry in `members`, and no summary(log count 0):
```json
{"state": "off", "index": "# 팀 메모리 인덱스 (INDEX.md)…", "members": [{"author": "alice", "date": "…", "summary": "…", "role": null}]}
```
The agent summarizes it as "지금 팀 상황: 멤버 1명(alice), 아직 작업 로그 없음, 구조는 준비됨".

> personality customization(opt-in, key 0): the agent **asks**, "배너·시작멘트·끝맺음말 커스텀할래요?" Yes → replace `team.greeting`/`team.farewell` in `team.config.json`(engine `on` prints greeting right after the banner; `off` prints farewell), or replace `memory/banner.txt` directly. **Only the introducer** writes this team-scoped value(shared with members once committed). No → keep defaults(no impact).
>
> Obsidian view(opt-in, key 0): yes → `python infra/install.py --root . --register-obsidian`. Creates `.obsidian/`(graph/dataview) + **merge registers** into `obsidian.json`(preserves existing vaults; idempotent). **If Obsidian is not installed, gracefully skip** → `[obsidian] 등록 건너뜀 — Obsidian 미설치(설정 디렉토리 부재) — skip (비치명, install 계속).`, always exit **0**. It can be run independently later with "Obsidian 등록해줘".

### Phase ③ `tm-connect` L2 Service Connection

**(a) Input** — Right after the first value, `tm-join` **suggests without pressure**, "서비스(이슈·채팅·문서·캘린더) 연결할래요? 나중에 해도 돼요". I say:
> "이슈 트래커 연결해줘"

**(b) Agent** — From here, the **`tm-connect` skill** runs(`tm-join` only suggests and triggers). It speaks only in role vocabulary(issues); data decides which product is used. Because the issues slot is empty, it asks which provider to use(for example, Linear), confirms that `providers/linear.json` exists, then **reads** that pack's `token_guide`, `auth`, `default_scope`, and `resource_fields` to construct the guidance(no hardcoded links in the skill body).

**(c) Screen / what a person must do directly** — Based on `providers/linear.json`:
- Issuance guide(`token_guide.url` and `steps`): `https://linear.app/settings/api` → Security & access → Personal API keys → "Create key" → enter label → copy key.
- `auth: "api_key"` → message: "개인 키를 Create → 복사 → 붙여넣기. attribution 이 본인으로 남도록 **각자 발급**."
- `default_scope: "personal"` → each member **enters their own token directly**(v0.1 has no automatic team sharing).
- **This is the human part(security boundary; cannot be unattended):** a person issues the token(clicks Create) and copies it. The token is stored in the local vault only via standard input:
  ```bash
  python -c "import sys; sys.path.insert(0,'infra'); import credentials; \
    credentials.store('our-repo', 'personal', 'issues', input())"
  ```
  → `$XDG_DATA_HOME/teammode/credentials/our-repo.json`(0600, not tracked by git). Because this is a plaintext vault, **do not put it in a sync folder**(never Syncthing/Dropbox/iCloud).
- Config slot record: `resource_fields: []`(Linear needs no instance fields) → `services.issues = {provider:"linear", scope:"personal"}` in `team.config.json`. **The token is not written to config**(vault only).
- Rewire: `python infra/install.py --root . --yes` → the adapter registers the issues MCP(`mcp.register_hint`: "Linear 공식 MCP 서버를 정규 서버명 'linear' 로 등록").

**First value(issues verb)** — The agent runs:
```bash
python infra/teammode.py issue create --root . --title "온보딩 테스트"
```
If connected, the canonical input schema is echoed as JSON(exit 0):
```json
{"verb": "issue", "action": "create", "service": "issues", "provider": "linear", "input": {"title": "온보딩 테스트"}}
```
> Honest marker for unimplemented/stopped behavior: the engine's `issue` verb **only echoes the schema**. It **does not** interpret `action_map`, transform payloads, or make real MCP calls(adapter/skill responsibility). In other words, this command does not actually create an issue in Linear. If the slot is empty and unconnected, it shows `[info] issues 슬롯이 연결돼 있지 않습니다. team.config.json 의 services.issues 를 연결하세요(tm-connect).` + exit **0**(empty slot = first-class citizen, not an error).

**(d) Next** — When the introducer connects additional team-scope slots(for example, chat=slack, docs=notion), they **commit** the provider and instance values to config so members can read them. However, **tokens are entered individually in v0.1**, so the introducer cannot finish everything with one action.

> Differences in the human part by provider(branching on `auth` value):
> | provider | role slot | auth | scope | resource_fields | human part |
> |---|---|---|---|---|---|
> | linear | issues | api_key | personal | (none) | Create key → copy → paste |
> | slack | chat | bot_token | team | channel_id | Create app → bot scopes → install → copy bot token |
> | notion | docs | api_key | team | database_id | Create integration → copy token → share Connections with target DB |
> | google | calendar | oauth | personal | calendar_id | OAuth client → consent screen **"Allow"**(localhost PKCE) |
