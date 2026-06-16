# teammode 스펙 06 — 서비스 연결 (L2) [설계 명세]

| | |
|---|---|
| spec_version | **0.1-draft** |
| 상태 | 설계 명세 (2026-06-15). 6개 솔루션 조사(`research-l2/`) distill. **미검수** — dev-cycle 문서모드 적대 검수 예정. |
| 범위 | **L2 = 서비스 연결 단계.** L1(팀 메모리) 다음. tm-onboard 팀셋업 직후 이어붙는다. 온보딩 에이전트가 이 문서 + `research-l2/<provider>.md`만으로 유저를 토큰 확보 → MCP 생성 → 유효성 확인까지 끌고 가는 실행 절차 + provider 템플릿. |
| 범위 외 | provider 팩 JSON 스키마 확정·`install-mcp` 코드·credentials 금고 = **L2 빌드 대상**(§6). 이 문서는 명세이지 구현이 아니다. |
| 관련 | [SPEC.md](../../teammode-repo/SPEC.md) §7(서비스 슬롯·provider 팩)·§2(어댑터)·§2.8(install-mcp 의무)·§5.4(tm-onboard L2 로드맵), [05 tm-onboard](../../teammode-repo/spec/05-onboard-skill.md), [04 install.py](../../teammode-repo/spec/04-install.md) |
| 입력 | `research-l2/{slack,linear,notion,google-calendar,discord,jira}.md` — 6솔루션 MCP설치·토큰발급·scope·리소스ID 조사 (이 문서의 단일 출처, 추측 없음) |

---

## 0. 한 문장

> L1(기억)이 켜진 직후, **"이미 쓰는 솔루션 있어요? 어떤 거 연결할래요?"** 로 시작해 — 카테고리(issues/chat/docs/calendar)별로 유저가 고른 provider마다, 우리가 미리 가진 **provider 템플릿**으로 토큰을 **순차적으로 함께 확보**하고, 받은 토큰을 **그대로 에이전트 config에 MCP로 등록**(install-mcp 계약)한 뒤, **가벼운 ping + 리소스 ID 자동조회**로 마무리한다. 사람 몫은 토큰/허용 클릭뿐, 나머지는 에이전트가 한다.

---

## 1. 개요 · 위치

### 1.1 L1 → L2 흐름상 위치

```
tm-onboard (05) 팀셋업
 ① install.py → L1 기계 부트스트랩 (세션로그·훅·env·맥락수집)
 ② role 내레이션 + context 첫 가치
 ③ L1 동작 확인 (서비스 0이어도 정상)
        │
        ▼  ← 바로 여기서 L2로 이어짐 (팀셋업 직후)
L2 서비스 연결 (이 문서)
 ① "이미 쓰는 솔루션 있어요? 어떤 거 연결할래요?" (카테고리별 질의)
 ② 고른 provider마다 provider 템플릿으로 안내
 ③ 토큰을 에이전트가 유저와 순차 확보 (정확한 클릭 경로 + Ctrl-F 탐색 안내)
 ④ 받은 토큰 그대로 MCP 생성 (install-mcp: ~/.claude.json·.mcp.json / ~/.codex/config.toml)
 ⑤ 연결 직후 가벼운 ping + 리소스 ID 자동조회 → config 슬롯 기록
 (순차: 한 솔루션 끝 → 다음)
```

- **강요 X**: L1만으로도 충분. L2는 유저가 당길 때(05 §5.3). 단 **팀셋업이 끝나면 자연스럽게 L2 제안으로 이어진다** — "이제 쓰던 도구 연결할래요?"
- L2는 install.py(결정적 기계)가 아니라 **tm-onboard 스킬(LLM 판단·대화)** 의 본체(05 §5.2·§5.4). 어느 provider·어느 리소스 선택은 LLM이, MCP 등록의 기계적 번역은 install-mcp(어댑터)가.

### 1.2 두 축: 카테고리 × scope

L2는 SPEC §7 서비스 슬롯 위에서 동작한다. 두 가지로 정리:

- **카테고리(역할 슬롯, 도구 중립)**: `issues` · `chat` · `docs` · `calendar` (SPEC §7.1, 고정 어휘 — kanban/slack 같은 제품명 아님).
- **scope(연결 단위)**: `team`(도입자 1회 → 팀원 공유) | `personal`(멤버 각자 1회). 슬롯마다 `scope` 필드(SPEC §7.1).

| 카테고리 | Tier 1 provider | 확장 provider(조사됨) | 기본 scope |
|---|---|---|---|
| `issues` | Linear | Jira | personal (attribution) |
| `chat` | Slack | Discord | team |
| `docs` | Notion | — | team |
| `calendar` | Google Calendar | — | personal/혼합 |

> Tier 1 = 독푸딩 조합(SPEC §7.4). Discord·Jira는 같은 카테고리의 대체 provider로 조사·템플릿 보유 → 유저가 그걸 쓰면 그 템플릿으로 안내.

---

## 2. L2 온보딩 절차 (핵심)

> 에이전트 진행 규칙: **한 번에 한 솔루션, 한 단계씩.** 유저가 "했어" 할 때마다 다음. 추측 금지 — 클릭 경로는 §3 표 + `research-l2/<provider>.md` 그대로 읽어준다.

### ① 질의 — "어떤 거 연결할래요?"

카테고리별로 묻는다. 강요하지 말고 "이미 쓰는 것"을 기준으로:

> **에이전트 멘트(요지)**: "이미 팀에서 쓰는 솔루션 있어요? 카테고리별로 연결할 수 있어요 —
> **이슈/태스크**(Linear·Jira), **채팅**(Slack·Discord), **문서**(Notion), **캘린더**(Google Calendar).
> 안 써도 돼요. 쓰는 것만, 원하는 만큼만 골라요. 토큰 한두 개면 끝납니다."

- 유저가 고른 것만 진행. 안 고른 카테고리는 **빈 슬롯**으로 둔다(SPEC §7.2, 1급 시민 — 에러 아님).
- 기대치 고정(SPEC §7.5-4): "당신 몫은 토큰 N개뿐" — 고른 provider 수만큼 사람 작업이 발생함을 미리 알린다.

### ② provider 템플릿으로 안내

- 고른 provider마다 §3의 **provider 템플릿** 행을 펼쳐 진행. 상세 클릭 경로는 해당 행이 링크하는 `research-l2/<provider>.md`.
- 템플릿이 알려주는 것: scope, MCP 서버(패키지/remote), 연결방식(토큰붙여넣기 vs OAuth허용), 필요 리소스 ID(자동조회 여부), 토큰발급 한줄경로.

### ③ 토큰을 에이전트가 유저와 순차 확보

각 provider 조사 파일의 **정확한 클릭 경로 그대로** 안내. scope·연결방식에 따라 두 갈래:

**(a) 토큰 붙여넣기형** (Slack 봇토큰 / Notion integration / Discord 봇토큰 / Linear·Jira 개인키 옵션):
1. 조사 파일의 클릭 경로를 한 단계씩 읽어준다.
2. 토큰 페이지에 도달하면 토큰을 복사해 에이전트에게 전달하도록 안내.
3. 토큰은 보통 **한 번만 표시** → "지금 바로 복사" 강조 (Slack·Discord·Linear·Jira 공통).

**(b) OAuth 허용형** (Linear·Jira 공식 remote MCP / Google Calendar):
1. MCP를 먼저 등록(④)한 뒤 인증 플로우 발동.
2. 브라우저가 뜨면 **"허용(Allow)" 클릭** — 토큰 심부름 없음. (Google은 사전에 GCP OAuth client JSON 1회 발급 필요 — §3 주석.)

> **디폴트 선택 규칙**: remote OAuth를 지원하는 issues provider(Linear·Jira)는 **기본 (b) OAuth**로 안내한다 — 유저가 키를 직접 관리하거나 Server/DC라서 (b)가 안 될 때만 (a) 개인키. (research: "모르겠으면 OAuth 추천".)
> **Notion 예외**: Notion도 remote OAuth(`mcp.notion.com`)가 있으나 **팀 1토큰 공유 모델과 안 맞아** 팀 자동화엔 **셀프호스트(토큰) 권장**(research notion.md §1-B) → (a)로 진행. §3 표의 Notion 연결방식도 토큰 붙여넣기로 고정.

> **[필수 멘트] 유저가 토큰 페이지에서 길을 잃으면 — Ctrl-F / F3 적극 검색 안내.**
> 모든 조사 파일이 공통으로 강조하는 마찰 해소 장치. 에이전트는 유저가 화면에서 헤매는 즉시 다음을 안내해야 한다:
> > "화면에서 안 보이면 **Ctrl-F**(맥은 **Cmd-F**), 다음 결과는 **F3**으로 키워드를 찾으세요. 예: `token`, `scope`, `secret`, `Create`, `connections`, `Desktop`."
> provider별 추천 검색어는 §3 표 + 조사 파일에 명시. (예: Slack `token`/`scope`, Notion `connections`/`secret`, GCal `Desktop`/`Create Credentials`, Jira `API token`.)

순차 원칙: **한 솔루션의 ③~⑤가 끝나야 다음 솔루션으로.** 병렬로 여러 토큰을 한꺼번에 요구하지 않는다(유저 인지부하↓).

### ④ 받은 토큰 그대로 MCP 생성 (install-mcp 계약)

토큰/허용을 받으면 **즉시** 에이전트별 config에 MCP를 등록한다. 이것이 `install-mcp` 계약(SPEC §2.8). 에이전트별 등록 형식은 §4.

- 정규 서버명(provider 식별자: `slack`·`linear`·`notion`·`google`·`discord`·`atlassian`) → 어댑터가 자기 표기로 번역(SPEC §2.8-2, 기본 규칙: 별칭=정규명).
- 토큰은 평문 노출 금지 — 팀 scope는 팀 금고, 개인 scope는 로컬(§5, SPEC §5.4·§7.5-3). credentials 금고는 아직 미구현 → 당장은 config/env에 두되 `.gitignore` + 래퍼 패턴 권장(§4.4).

### ⑤ 연결 직후 유효성 확인 + 리소스 ID 자동조회

MCP 등록 직후 가벼운 검증:

1. **API ping**: 연결 성공을 즉시 피드백할 정도의 1회 호출(05 §5.5 — 검증은 doctor가 아니라 연결 성공 확인 수준).
   - Slack: `channels_list` / Linear: `teams` / Notion: search 1회 / GCal: `list-calendars` / Discord: `list_channels` / Jira: `project/search`.
2. **리소스 ID 자동조회**: 토큰만 있으면 에이전트가 직접 긁어온다 — 유저가 손으로 ID를 찾을 필요 없음(모든 조사 파일 공통). 결과를 후보로 제시 → 유저 1클릭 선택 → **config 슬롯에 기록**.
   - Slack: 채널 ID(`channels_list`) / Linear: team·project·workflow state UUID(`teams`·`workflowStates(type)`) / Notion: DB·page ID(search → data_source) / GCal: 캘린더 ID(`list-calendars`)·colorId / Discord: 채널·길드 ID / Jira: project key·status category(transition은 캐싱 금지, 실시간 GET).

> ping 실패 시 1순위 의심: Notion=페이지 공유(connections) 누락 / Slack=채널 invite 누락 또는 scope 추가 후 재설치 안 함 / GCal=인증 안 함(`-32600`) / Discord=Message Content Intent 또는 서버 초대 누락. (조사 파일의 "자주 막히는 곳" 참조.)

---

## 3. provider 템플릿 표 (6개)

> 우리가 미리 보유하는 템플릿. 각 행 = 온보딩 에이전트가 그 provider를 고른 유저에게 펼칠 안내 카드. **상세 클릭 경로·env·troubleshooting은 반드시 링크한 조사 파일에서** distill(이 표는 요약).

| 카테고리 | provider | scope | MCP 서버 (패키지/remote) | 연결방식 | 필요 리소스 ID (자동조회?) | 토큰발급 한줄경로 | Ctrl-F 검색어 | 상세 |
|---|---|---|---|---|---|---|---|---|
| `chat` | **Slack** | team (도입자 1회) | `slack-mcp-server` (npx, stdio) ※korotovsky 권장, 쓰기 기본 OFF | **토큰 붙여넣기** (봇 `xoxb-`) | 채널 ID `C…` (✅ `channels_list` 자동) | api.slack.com/apps → Create New App **From a manifest**(권장) → Install to Workspace → **Bot User OAuth Token(xoxb-)** 복사. 읽을 채널엔 `/invite @봇` | `token`, `scope`, `OAuth & Permissions` | [slack.md](../research-l2/slack.md) |
| `chat` | **Discord** | team (도입자 1회) | `SaseQ/discord-mcp` (Docker, HTTP `:8085/mcp`) | **토큰 붙여넣기** (봇 토큰) | 채널·길드 ID snowflake (개발자 모드 → Copy ID; `DISCORD_GUILD_ID` env 권장) | discord.com/developers/applications → New Application → **Bot** → **Message Content Intent ON** → **Reset Token → Copy**. OAuth2 URL Generator(`bot`+권한, Guild Install)로 서버 초대 | `Token`, `Intent`, `OAuth2` | [discord.md](../research-l2/discord.md) |
| `docs` | **Notion** | team (도입자 1회) | `@notionhq/notion-mcp-server` (npx, stdio) ※셀프호스트 권장 / remote OAuth는 대안 | **토큰 붙여넣기** (`ntn_…`) + **공유 토글** ⚠️ | DB·page ID (✅ search → data_source 자동) | notion.so/profile/integrations → **New integration**(Internal) → **Internal Integration Secret(`ntn_…`)** 복사 → **대상 DB/페이지를 integration에 공유**(⋯ → Connections) ⚠️ 빼먹으면 전부 404 | `connections`, `secret`, `capabilities`, `access` | [notion.md](../research-l2/notion.md) |
| `calendar` | **Google Calendar** | personal/혼합 | `@cocal/google-calendar-mcp` (npx, stdio) | **OAuth 허용** (사전: GCP OAuth client JSON 1회) | 캘린더 ID·colorId (✅ `list-calendars`·`list-colors` 자동) | console.cloud.google.com → 프로젝트 생성 → Calendar API Enable → OAuth consent(External, test user) → **Credentials → Create → OAuth client ID → Desktop app** → JSON 다운(`gcp-oauth.keys.json`). 그 후 "인증해줘" → 브라우저 **허용** | `Desktop`, `Create Credentials`, `Add or Remove Scopes`, `Test users` | [google-calendar.md](../research-l2/google-calendar.md) |
| `issues` | **Linear** | personal (각자 1회, attribution) | **공식 remote MCP** `https://mcp.linear.app/mcp` (권장) / 로컬 키 패키지는 대안 | **OAuth 허용**(권장) / 개인키 `lin_api_…` 붙여넣기(대안) | team·project·workflow state UUID (✅ `teams`·`workflowStates(type)` 자동) | (A) `claude mcp add --transport http linear https://mcp.linear.app/mcp` → `/mcp` OAuth. (정규명 `linear`로 등록 — research 예시의 `linear-server`는 비정규 별칭, §4.1) (B) linear.app/settings/account/security → **Personal API keys → New API key** → 즉시 복사 | `API key`, `Security`, (앱 내 Cmd/Ctrl+K → "Copy model UUID") | [linear.md](../research-l2/linear.md) |
| `issues` | **Jira** | personal (각자 1회, attribution) | **공식 Rovo remote MCP** `https://mcp.atlassian.com/v1/mcp/authv2` (권장, Cloud) / `sooperset/mcp-atlassian` 로컬은 대안(Server/DC 필수) | **OAuth 허용**(권장) / 개인 API 토큰 `ATATT…`+이메일+사이트URL 붙여넣기(대안) | project key·status category·transition ID (✅ `project/search` 자동 / transition은 실시간 GET, 캐싱 금지) | (A) `claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2` → `/mcp` OAuth. (B) id.atlassian.com/manage-profile/security/api-tokens → **Create API token** → 즉시 복사 | `API token`, `Create`, `Security` | [jira.md](../research-l2/jira.md) |

**표 읽는 법 (에이전트):**
- **연결방식 = OAuth 허용** → 토큰 심부름 없음. ④에서 MCP 먼저 등록 → 인증 플로우 → "허용" 클릭(③-b). Google만 사전 GCP JSON 1회 발급이 선행.
- **연결방식 = 토큰 붙여넣기** → ③-a. 토큰 한 번만 표시되므로 즉시 복사 강조.
- **자동조회 ✅** → 유저에게 ID를 묻지 말 것. 토큰 연결 후 에이전트가 긁어 후보 제시(⑤).
- **scope = team** → 도입자만 진행, 팀원은 config 읽기(0회). **scope = personal** → 팀원 각자 1회(§5).
- Tier 1 권장 조합은 **Linear / Slack / Notion / Google Calendar**. Discord·Jira는 유저가 그 도구를 쓸 때만.

---

## 4. MCP 생성 계약 (install-mcp)

> 토큰을 받아 **에이전트별 config에 MCP를 등록하는 기계적 절차.** 어느 provider·어느 리소스 선택은 스킬(LLM)이, 등록 번역은 어댑터의 `install-mcp`가(SPEC §2.7·§2.8, 05 §5.5·§8). reference 미구현 → L2 빌드 대상(§6).

### 4.1 정규명 → 어댑터 번역

1. 스킬이 정규 서버명(`slack`·`linear`·`notion`·`google`·`discord`·`atlassian`)과 토큰/URL을 install-mcp에 넘긴다.
2. install-mcp가 `services` 선언(SPEC §7)을 읽어 자기 에이전트 방식으로 등록(SPEC §2.8-1).
3. 정규 서버명 → 실제 등록 별칭 매핑을 이 시점 확정. **기본: 별칭=정규명**(SPEC §2.8-2). manifest의 MCP 매처(예: `{linear, create_issue}`)가 이 별칭을 참조하므로, install-mcp 선행 후에야 해당 매처 훅이 `sync`로 활성화된다(SPEC §2.7).
4. **⚠️ 비정규 별칭 금지(별칭 보장)**: 일부 provider의 조사/공식 예시 명령은 비정규 별칭을 쓴다 — Linear `linear-server`, Discord `discord-mcp`, Google `google-calendar`. install-mcp는 이를 **무시하고 반드시 정규명(`linear`·`discord`·`google`)으로 등록**한다. 비정규 별칭으로 등록하면 매처(`mcp__linear__…` 등)가 그 별칭과 불일치해 **훅이 영영 미발동**한다(SPEC §2.8 별칭 보장 위반). §3 표·§4.2 예시의 별칭은 참고일 뿐, 등록 식별자는 항상 정규명.

### 4.2 에이전트별 등록 형식 차이

조사 파일에서 distill한 형식. provider별 정확한 값은 §3 링크 참조.

**Claude Code** — `~/.claude.json`(전역) 또는 프로젝트 `.mcp.json`:

- stdio 패키지형 (Slack·Notion·GCal): `mcpServers.<name>` = `{ command: "npx", args: [...], env: { <TOKEN_VAR>: "..." } }`
- remote OAuth형 (Linear·Jira): `claude mcp add --transport http <name> <url>` → 세션에서 `/mcp` 로 OAuth.
- HTTP 상주형 (Discord): `claude mcp add <name> --transport http http://localhost:8085/mcp` (컨테이너 선기동).

```jsonc
// 예: Slack stdio (Claude)
{ "mcpServers": { "slack": {
  "command": "npx", "args": ["-y", "slack-mcp-server@latest", "--transport", "stdio"],
  "env": { "SLACK_MCP_XOXB_TOKEN": "xoxb-…" } } } }
```

**Codex** — `~/.codex/config.toml`(또는 프로젝트 `.codex/config.toml`):

- stdio 패키지형: `[mcp_servers.<name>]` + `command`/`args` + `env = { … }` (env는 같은 `<name>` 블록 아래 중첩 — 이름 오타 시 블록 끊김).
- remote OAuth형: `codex mcp add <name> --url <url>` → `codex mcp login <name>`. 첫 원격 MCP면 `[features] experimental_use_rmcp_client = true` 선행.

```toml
# 예: Slack stdio (Codex)
[mcp_servers.slack]
command = "npx"
args = ["-y", "slack-mcp-server@latest", "--transport", "stdio"]
env = { SLACK_MCP_XOXB_TOKEN = "xoxb-…" }
```

### 4.3 provider별 env 변수명 (distill — 패키지마다 다름, 주의)

| provider | 등록 키(정규명) | 토큰/인증 env 또는 방식 | 추가 필수 |
|---|---|---|---|
| Slack | `slack` | `SLACK_MCP_XOXB_TOKEN`(korotovsky) ※쓰기 켜려면 `SLACK_MCP_ADD_MESSAGE_TOOL` | (없음) |
| Discord | `discord` | `DISCORD_TOKEN` | `DISCORD_GUILD_ID`(권장), `SPRING_PROFILES_ACTIVE=http` |
| Notion | `notion` | `NOTION_TOKEN`(`ntn_…`) ※또는 `OPENAPI_MCP_HEADERS` | `NOTION_VERSION`(팀 start.sh는 `2022-06-28` 강제) |
| Google Calendar | `google` | `GOOGLE_OAUTH_CREDENTIALS`=절대경로(`gcp-oauth.keys.json`) | (선택) `ENABLED_TOOLS` |
| Linear | `linear` | (A) remote OAuth, env 없음 / (B) `LINEAR_API_KEY` 또는 `LINEAR_API_TOKEN`(패키지마다 다름) | (B) 패키지 README 확인 |
| Jira | `atlassian` | (A) remote OAuth, env 없음 / (B) `JIRA_URL`+`JIRA_USERNAME`+`JIRA_API_TOKEN`(Cloud) / `JIRA_PERSONAL_TOKEN`(Server/DC) | (B) Cloud URL·이메일 3종 |

> ⚠️ Linear/Jira 로컬 패키지의 env 이름은 패키지마다 갈린다(`LINEAR_API_KEY` vs `LINEAR_API_TOKEN`) — 고른 패키지 README 확인(조사 파일 명시). 기본은 둘 다 **공식 remote OAuth**(env 불요).

### 4.4 토큰 보관 (평문 노출 금지)

- config JSON/TOML에 토큰 직박기보다 **`.env` + 래퍼 시작스크립트** 패턴 권장(Notion 팀 `start.sh` 선례: `.env`의 토큰을 읽어 헤더 조립 → `.env`는 `.gitignore`). 토큰을 전역 config에 평문으로 남기지 않음.
- 팀 scope 토큰은 팀 금고(credentials)에 1회 저장 → 팀원 공유. 개인 scope 토큰은 로컬. (credentials 금고 미구현 — §6.)

---

## 5. scope team/personal 규칙

> SPEC §7.1·§5.4. **연결 단위가 두 종류** — 누가 몇 번 연결하느냐가 갈린다.

### 5.1 team scope — 도입자 1회 → 팀원 공유 (Slack · Notion · Discord)

- **도입자(팀 어드민) 1명이 1회** 발급·설치 → 토큰을 팀 금고에 저장 + 리소스 ID(채널/DB)를 **config의 team scope 슬롯에 커밋**.
- **팀원은 0회 연결** — 레포의 config를 읽기만 한다(install.py 팀원 경로, 04 §6 "도입자 1회 고생 → 팀원 0"). 토큰은 금고에서 받음.
- 재실행: 채널/DB가 늘거나 scope를 더 줄 때만(앱 재설치 필요). Slack은 scope 추가 시 **워크스페이스 재설치** 필수(조사 파일).

### 5.2 personal scope — 멤버 각자 1회 (Linear · Jira · Google Calendar)

- **attribution 때문에 공유 불가**: Linear/Jira는 에이전트가 만든 이슈·코멘트·상태변경이 **연결한 사람 명의로 찍힌다**. 한 명이 대표 연결해 공유하면 모든 액션이 그 사람으로 찍혀 attribution이 깨짐 → **팀원 각자 본인 계정으로 1회**.
- Google Calendar는 개인 인증(본인 구글 계정 OAuth)이되 **혼합 운영 가능**: 팀 공유 캘린더 1개를 멤버들에게 공유해두면 각자 자기 인증으로 `list-calendars` 시 그 팀 캘린더도 잡힘 → 개인+팀 동시.
- 저장: 개인 토큰/인증은 **로컬**(팀 금고 아님). config에는 개인이 고른 리소스(예: 캘린더 ID)만 남고 토큰은 로컬.

### 5.3 정직한 경계 (무인 불가, 사람 몫)

스킬은 동의 게이트 **직전까지** 몰고 가고, 권한 부여 클릭은 사람이(05 §5.4·§5.5, 보안 경계):
- OAuth "허용" / 개인키 "Create + 붙여넣기" / Notion "공유 토글" / Discord "서버 초대 Authorize" = 사람이 권한 부여.
- 에이전트는 정확한 위치·버튼을 짚고(③), 결과(토큰/허용)를 받아 처리(④⑤)한다.

---

## 6. 미구현 / 다음 (L2 빌드 대상)

이 문서는 **명세**다. 아래는 빌드해야 할 L2 코드 — reference 현재 미구현(SPEC 부록 A, 05 §5.4 "L2 미구현 — 준비 중", 04 §8 갭):

| 항목 | 내용 | 의존 |
|---|---|---|
| **provider 팩 JSON 스키마** | `providers/<name>.json` — §3 표를 기계가 읽는 데이터로. `token_guide`(발급 딥링크+단계), MCP 등록 템플릿(에이전트별), 리소스 자동조회 쿼리, env 변수명, scope, ping 호출. provider별 상수 스키마 = v0.2 확정(SPEC §7.1·§7.3 "예약"). | — |
| **install-mcp 코드** | 어댑터 `adapter.py install-mcp`(SPEC §2.7 예약·§2.8 의무) — 정규명→별칭 등록, services 읽어 MCP 배선. Claude/Codex 각각. | provider 팩 |
| **credentials 금고** | 팀 토큰 1회 저장 → 팀원 공유(SPEC §5.4·§7.5-3). teammode에 아직 없음 — 의존 슬라이스. 당장은 `.env`+`.gitignore` 래퍼로 대체. | — |
| **빈 슬롯 ↔ 매처 활성화** | install-mcp 선행 후 `sync` 재실행으로 MCP 매처 훅·`requires` 스킬 활성화(SPEC §2.7·§7.2). 슬롯 연결 후 재배선 흐름 코드. | install-mcp |
| **`infra/mcp/_template/`** | 공식 MCP 없는 사내 도구용 최소 서버 + 시작 스크립트 + 가이드(SPEC §7.4) — 온보딩 중 즉석 제작 제안용. | — |

**닫힌 결정(이 명세가 확정):**
- L2 진입 = tm-onboard 팀셋업 직후(§1.1). 강요 X, 유저가 당길 때.
- 카테고리 4종 × scope 2종 위에서 동작(§1.2, SPEC §7.1과 정합).
- 6 provider 템플릿(§3) — Tier 1(Linear/Slack/Notion/GCal) + 확장(Discord/Jira).
- 사람 몫 = 토큰 붙여넣기 또는 OAuth 허용뿐, 나머지(MCP 등록·리소스 조회)는 에이전트(§2).
- 토큰 페이지에서 길 잃으면 **Ctrl-F/F3 검색 안내가 필수 멘트**(§2-③).

**열린 미결(v0.2 또는 검수에서):**
1. provider별 상수 스키마 상세(SPEC §7.1 예약) — `states`/`channels`/`docs_db` 등 config 슬롯 키 확정.
2. 훅 매처용 `providers/<name>.json` 정규 액션 `{service, action}` 매핑표 스키마(SPEC §7.3 예약).
3. Slack 쓰기 기본 OFF·Discord Message Content Intent 같은 provider별 안전 기본값을 팩에 어떻게 표현할지.
4. Jira 클래식 토큰 점진 폐기·GCal test모드 7일 만료 등 **provider 측 변화 추적** 책임(팩 갱신 주기).
