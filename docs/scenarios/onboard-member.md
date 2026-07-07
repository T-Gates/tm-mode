# Member Onboarding Scenario

This is the complete user journey where a member proceeds from clone → `tm-join` L1 setup → `tm-connect` L2 service connection, including the points where it diverges from the introducer path.

---

## Scenario 2 — Member

**I am joining a team repo that an introducer has already created.** The repo already has a **valid `team.config.json`**(`spec_version` exists + `team.name` is not a placeholder). When I run setup, `install.py` sees `detect_role()` → `config_is_valid()=True`, determines **`role=member`**, and **only reads the config**. I do not overwrite the team identity(name, greeting, service declarations) created by the introducer.

> Observer view — **three divergence points from the introducer path:**
> 1. **Config is read-only.** Because of the `if role == "introducer"` guard in `scaffold_memory()`, `write_introducer_config()` is **not called**(`install_lib.py:592`). A member is registered in `members.md` and **only their own entry is upserted** in `config.members`(`upsert_member_role`). `spec_version`, `team`, and other members' entries are **never touched**.
> 2. **Name collision exits 3.** If the same name is already registered in `members.md` with a **different git email(identity)**, `register_member()` raises `ConflictError` → `[error] 이름 충돌(사람이 해소 필요): …` + exit **3**. The introducer is the first person, so this collision is effectively absent there.
> 3. **Existing team session logs appear in context.** The introducer has a newly created team with 0 logs, but when a member joins, session logs from the introducer and earlier members may already exist, so `context` shows per-member summaries.

### Phase ① Repo Clone

**(a) Input** — Clone the team repo that the introducer pushed.
```bash
git clone git@github.com:our-team/our-repo.git
cd our-repo
```
**(b) Agent** — Waits.
**(c) Screen** — `team.config.json` **already exists**(`team.name="our-repo"`, `members:[{name:"alice"}]`, and some services filled). `memory/team/members.md` contains `- alice …`, and `memory/team/sessions/alice/` contains existing logs. However, the `.teammode-active` marker **does not exist on this host**(the marker is host-local and not committed → this host has not been set up yet).
**(d) Next** — Ask the agent to set it up. → Phase ②.

### Phase ② `tm-join` L1 Setup

**(a) Input** — Natural language:
> "팀모드 합류할게, 셋업해줘"

**(b) Agent** — The **command shape is the same** as the introducer path(the role is determined automatically). It recommends specifying `--member-name` to avoid name collisions.
```bash
python infra/install.py --root . --member-name bob --yes
```
> The agent explains in advance: "도입자/팀원은 자동 판정됩니다. 이미 유효한 `team.config.json` 이 있으니 **팀원**으로 잡힐 거예요. config 는 읽기만 하고 당신 엔트리만 추가합니다".

**(c) Screen** — The flow proceeds with `role=member`. The **differences from the introducer path are `[plan] role`** and the unchanged config:
```
[plan] team_root=/abs/path/our-repo
[plan] role=member (team.name 기본='our-repo')
[plan] agents=['claude']
[plan] member_name=bob
[scaffold] memory/ 구조·members.md 등재 완료 (role=member).
[wire] claude MCP 등록 동기화 완료
[wire] claude 훅 동기화 완료 → /home/me/.claude/settings.json
[wire] claude 스킬 심링크 완료 → /home/me/.claude/skills
[env] /home/me/.bashrc 에 TEAMMODE_HOME 주입 (신규 주입).
[verify] 설치 검증 OK — members=2 (팀모드는 꺼둠).
[done] 설치 완료. 팀모드를 켜려면 `tm on`(또는 /tm) 하세요.
```
- exit **0**. `members=2` — existing alice + newly registered bob.
- Side effects: append `- bob  <!-- id: <bob git email> -->` to `members.md`, and upsert `{name:"bob"}` into `config.members`(only the member's own entry; alice's entry untouched). **`spec_version`, `team`, `services`, and `admin_contact` in `team.config.json` remain unchanged** — members do not touch them. The default `team.name` is calculated, but it is meaningless because config is not written(read-only).

> Blocking points / branches that diverge from the introducer path:
> - **Name collision(exit 3)** — If I run with `--member-name alice`, but alice in `members.md` is registered with a **different git email**:
>   ```
>   [error] 이름 충돌(사람이 해소 필요): members.md 의 'alice' 는 다른 식별자(alice@x)로 등재돼 있습니다. 당신(bob@y)과 충돌 — --member-name 으로 다른 이름을 쓰거나 사람이 해소하세요.
>   ```
>   exit **3**. **The agent does not correct it arbitrarily**(no guessing); it asks the person to rerun with `--member-name <다른 영문이름>`.
>   Conversely, same name + same/unknown identity is idempotent. It is treated as reinstall/another machine, does not add to `members.md`, and is not a collision.
> - **Invalid name**(`..`/slash/leading dash and other traversal/footgun cases) → `[error] 멤버 이름 거부: …` + exit **3**.
> - The remaining gates(`--yes`/`--settings`/`--dry-run`/preflight/remote authentication) are the same as the introducer path.

**(d) Next** — The agent translates it: "팀원으로 합류했습니다. config 는 읽기만 했고 당신(bob) 엔트리만 추가했어요. 팀에 이미 기록이 있으니 보여드릴게요." → first value.

**First value** — The agent runs:
```bash
python infra/teammode.py context --root . --json
```
**Screen** — Unlike the introducer path, **existing team session logs are visible**. `state=off`(installation does not turn on team mode; it remains off until `tm on`), and `members` contains alice(existing log summary) and bob(no logs yet):
```json
{"state": "off", "index": "…", "members": [
  {"author": "alice", "date": "2026-06-10", "summary": "결제 모듈 리팩터링 …", "role": "pm"},
  {"author": "bob", "date": "…", "summary": "…", "role": null}
]}
```
The agent summarizes it as "지금 팀 상황: alice 가 결제 모듈 작업 중, 당신은 방금 합류. 다음 세션부터 이 맥락이 자동 주입됩니다". In text mode, it shows one line per member in a form like `- alice(pm) [2026-06-10] summary: …`.

> personality customization: **members do not change greeting/farewell**(team scope = introducer responsibility; read-only). Obsidian registration is a host-local opt-in, so each member can also run `--register-obsidian`.

### Phase ③ `tm-connect` L2 Service Connection

**(a) Input** — Either respond to the L2 suggestion from `tm-join`, or later say:
> "캘린더 연결해줘"

**(b) Agent** — Runs `tm-connect`. If the introducer **already declared** the calendar slot in config(`services.calendar.provider="google"`), it **reads** and uses that provider(asks the person only when the slot is empty). Guidance is based on data from `providers/google.json`.

**(c) Screen / human part** — Based on `providers/google.json`(`auth: "oauth"`, `scope: "personal"`, `resource_fields: ["calendar_id"]`):
- Issuance: `https://console.cloud.google.com/apis/credentials` → create OAuth client ID → **click "Allow" on the consent screen through localhost redirect(PKCE)**(once per person; permission granted by a person) → auto-discover calendar ID → choose the calendar to use.
- **This is the human part(security boundary):** clicking **"Allow"** on the OAuth consent screen cannot be unattended. The skill takes the user right before the consent gate, and a person clicks it.
- **Each person enters their own value:** scope=personal, but in v0.1 each member enters their own token once anyway(no automatic team sharing). Even if the introducer committed the config slot's provider and `calendar_id` declaration, **I receive the token myself**.
- Instance value: write `calendar_id`(for example, `primary`) to the `services.calendar` slot(token in vault, only instance value in config).
- Rewire: `python infra/install.py --root . --yes` → register google MCP("Google Calendar MCP 서버를 정규 서버명 'google' 로 등록(localhost OAuth/PKCE)").

**(d) Next** — Once connection is complete, both L1 and L2 are working. Additional roles are attached with the same pattern. If a slot remains empty, `issue`/related verbs show only nonfatal `[info]` guidance, which is normal(first-class citizen).
