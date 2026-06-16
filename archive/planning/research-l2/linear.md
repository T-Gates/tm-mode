# Linear 연결 가이드 (L2 온보딩)

> 목적: teammode 온보딩 시 **에이전트가 이 문서만 보고** 팀원을 "토큰 발급 → MCP 등록 → 리소스 ID 확보"까지 끝낼 수 있게 하는 작업 지시서.
> 기준일: 2026-06-15. 출처는 모두 현행 공식 문서(Linear Docs / Linear Developers). 추측 아님.
> **탐색 팁(에이전트 → 유저):** 유저가 화면에서 못 찾으면 **Ctrl-F(맥: Cmd-F) / 브라우저 페이지 내 검색**을 적극 쓰게 하라. Linear 앱 안에서 ID 찾을 땐 **Cmd/Ctrl+K** 커맨드 메뉴 → "Copy model UUID".

---

## 0. 온보딩 한 줄 안내 (에이전트가 유저에게 먼저 보낼 문장)

> "Linear를 연결할게요. 두 가지 길이 있어요 — (A) **공식 원격 MCP**(가장 쉬움, OAuth 로그인 1번) 또는 (B) **개인 API 키 + 로컬 MCP**(키를 직접 관리). teammode는 **액션이 '본인'으로 찍혀야**(attribution) 하므로 둘 다 **각자 1회**씩 본인 계정으로 해야 합니다. 어느 쪽으로 갈까요? 잘 모르겠으면 A를 추천해요."

---

## 1. Linear MCP 서버

### 1-A. (권장) 공식 원격 MCP — OAuth, 키 관리 불필요

Linear가 직접 호스팅하는 원격 MCP. Cloudflare/Anthropic과 공동 구축. **Streamable HTTP** 전송, **OAuth 2.1 + dynamic client registration**. 키를 따로 발급/보관할 필요 없음(로그인만).

- **엔드포인트:** `https://mcp.linear.app/mcp`
- 이슈/프로젝트/코멘트 등 찾기·생성·수정 도구 제공.

**Claude Code 등록:**
```bash
claude mcp add --transport http linear-server https://mcp.linear.app/mcp
```
등록 후 Claude Code 세션에서 `/mcp` 실행 → 브라우저 OAuth 로그인 플로우 진행 → 본인 Linear 계정 승인.

**Codex 등록 (CLI):**
```bash
codex mcp add linear --url https://mcp.linear.app/mcp
```
그 후 `codex mcp login linear` 로 인증 플로우 진행.
> 첫 MCP 사용이면 `~/.codex/config.toml` 에 `rmcp` 기능을 켜야 함:
> ```toml
> [features]
> experimental_use_rmcp_client = true
> ```
> 환경변수 방식으로 직접 적을 수도 있음:
> ```toml
> [features]
> experimental_use_rmcp_client = true
>
> [mcp_servers.linear]
> url = "https://mcp.linear.app/mcp"
> ```

**원격 MCP를 지원 안 하는 클라이언트(구형) — `mcp-remote` 브리지:**
```json
{
  "mcpServers": {
    "linear": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
    }
  }
}
```
(VS Code / Windsurf 동일. Zed는 `context_servers` 키 사용.)

**Claude.ai(데스크톱/웹) 커넥터:** Settings → Connectors 에서 Linear 추가, 또는 `https://claude.ai/customize/connectors`.

> **OAuth 토큰/API 키 직접 주입도 가능:** 공식 MCP는 interactive 플로우 대신 `Authorization: Bearer <yourtoken>` 헤더로 OAuth 토큰 또는 API 키를 직접 받을 수 있음. read-only 제한 키나 기존 OAuth 앱 연동에 유용.

문제 해결(공식 FAQ):
- 연결 시 internal server error → `rm -rf ~/.mcp-auth` 후 재시도, 필요시 node 업데이트.
- WSL(Windows) 오류 → `command: "wsl"`, args에 `npx -y mcp-remote https://mcp.linear.app/sse --transport sse-only`.

### 1-B. (대안) 개인 API 키 + 로컬 MCP 패키지

키를 직접 관리하고 싶거나 권한을 좁히고 싶을 때. 커뮤니티 npm 패키지를 `LINEAR_API_KEY` env로 구동. (공식 패키지가 아니므로 패키지 신뢰도/최신성은 설치 시점 확인 필요.)

대표 패키지(현행 확인된 것):
- `@tacticlaunch/mcp-linear` (구 `@tacticlaunch/linear-mcp`) — env: `LINEAR_API_TOKEN`
- `@ibraheem4/linear-mcp` — env: `LINEAR_API_KEY`

**Claude Code 등록 예시 (env 주입):**
```bash
claude mcp add linear \
  --env LINEAR_API_KEY=lin_api_xxx \
  -- npx -y @ibraheem4/linear-mcp
```
또는 설정 파일 직접 작성:
```json
{
  "mcpServers": {
    "linear": {
      "command": "npx",
      "args": ["-y", "@ibraheem4/linear-mcp"],
      "env": { "LINEAR_API_KEY": "lin_api_xxx" }
    }
  }
}
```
> ⚠️ env 이름은 패키지마다 다름(`LINEAR_API_KEY` vs `LINEAR_API_TOKEN`). 고른 패키지 README 확인.
> ⚠️ teammode 기본은 **1-A 공식 원격 MCP**. 1-B는 키 직접 관리/권한 제한이 꼭 필요할 때만.

---

## 2. 토큰 발급 단계 (개인 API 키, `lin_api_…`)

> 공식 MCP(1-A)는 OAuth라 이 단계가 **불필요**. 1-B 로컬 MCP나 GraphQL 직접 호출, 또는 Bearer 헤더 주입 시에만 필요.

**클릭 경로:**
1. Linear 웹앱 → 좌상단 **프로필 아이콘 → Settings**
2. 사이드바 **Account → Security & Access**
   - 직접 링크: `https://linear.app/settings/account/security`
3. **Personal API keys** 섹션 → **New API key** (= Create key)
4. 키 이름 입력 → **Create**
5. **키는 한 번만 표시됨.** 즉시 복사해 안전한 곳에 저장(분실 시 재발급만 가능).

**키 형식 / 사용 헤더:**
- 키 접두사: `lin_api_…`
- GraphQL 직접 호출 시 헤더: `Authorization: <API_KEY>` ← **Bearer 없이** 키 그대로.
  - (OAuth 액세스 토큰일 때만 `Authorization: Bearer <ACCESS_TOKEN>`)

**권한(scope) 선택 — 키 생성 시 지정 가능:**
- Full access(유저가 접근 가능한 모든 데이터) 또는 제한: **Read / Write / Admin / Create issues / Create comments**
- 특정 **팀(team)** 으로 접근 범위 제한 가능.
- teammode 이슈 생성/상태변경을 하려면 최소 Write(+ Create issues) 필요.

**OAuth 앱 옵션:** Linear는 personal API key 외에 **OAuth2** 인증도 지원(GraphQL API 공식). 자체 앱을 등록해 access token을 발급받아 `Authorization: Bearer <ACCESS_TOKEN>` 로 사용. teammode 온보딩 기본 경로로는 과함 — 다수 멤버 attribution은 1-A 공식 MCP의 per-user OAuth로 이미 해결됨.

---

## 3. 필요한 리소스 ID (토큰만으론 안 끝나는 부분)

> **중요:** 토큰/MCP 연결이 끝나도, teammode가 "이슈를 어느 팀에 만들지", "상태를 backlog/in_progress/done 중 무엇으로 바꿀지" 정하려면 **team ID·project ID·workflow state UUID**가 필요하다. 이건 토큰 발급으로 자동으로 생기지 않는다. **단, 토큰만 있으면 GraphQL/MCP로 자동 조회 가능** — 유저가 손으로 UUID를 찾아 넣을 필요는 없고, 에이전트가 아래 쿼리로 긁어오면 된다.

- **GraphQL 엔드포인트:** `https://api.linear.app/graphql` (introspection 지원)
- 앱 안에서 수동으로 찾기: **Cmd/Ctrl+K → "Copy model UUID"** (현재 보고 있는 페이지 기준 결과).

**Team ID 조회:**
```graphql
query Teams { teams { nodes { id name } } }
```

**Project ID 조회 (팀/워크스페이스 단위):**
```graphql
query { projects { nodes { id name } } }
```

**Workflow state UUID 조회 (backlog / in_progress / done 매핑):**
```graphql
query {
  workflowStates {
    nodes { id name type }
  }
}
```
- `id` = 상태 UUID, `name` = 표시명(예: "In Progress"), `type` = **분류 enum**.
- `type` enum 값(case-sensitive): **`backlog`, `unstarted`, `started`, `completed`, `canceled`** (+ 팀이 Triage 켜면 `triage`).
- teammode 매핑 권장:
  - backlog → `type == "backlog"`
  - in_progress → `type == "started"`
  - done → `type == "completed"`
- ⚠️ **상태는 팀마다 다름.** 각 팀이 자체 워크플로 상태를 가지므로 같은 "Done"이라도 UUID가 팀별로 다르다. 팀 단위로 매핑할 것.
> 참고: 공식 Getting started 예제의 `workflowStates` 쿼리는 `id`/`name`만 보여주지만, 분류 자동화를 위해 **`type` 필드를 반드시 함께 조회**하라(스키마에 존재).

**이슈 생성 mutation (teamId 필수):**
```graphql
mutation IssueCreate {
  issueCreate(input: {
    title: "New issue"
    description: "markdown..."
    teamId: "9cfb482a-81e3-4154-b5b9-2c805e70a02d"
  }) { success issue { id title } }
}
```
**이슈 상태 변경:** `issueUpdate(input: { stateId: "<workflow state UUID>" })`. `id`는 UUID 또는 `BLA-123` 같은 단축 식별자 모두 허용.

> 자동화 권장 플로우(에이전트): 토큰/MCP 연결 직후 → `teams` + `workflowStates(type 포함)` + `projects` 한 번 긁어 → teammode 설정(team.config 등)에 team ID / state UUID 매핑 캐싱.

---

## 4. Scope: 개인 단위 (attribution)

- **Linear 연결은 개인(per-user)이다.** 공식 MCP는 OAuth로 본인 계정에 로그인, API 키도 개인 계정에 종속.
- 따라서 **에이전트가 만든 이슈·코멘트·상태변경은 그 사람 명의로 찍힌다(attribution).** 누가 무엇을 했는지 Linear 활동 로그에 본인으로 남음.
- ⇒ **팀원 각자 1회씩** 본인 계정으로 연결해야 한다. 한 명이 대표로 연결해 공유하면 모든 액션이 그 한 사람으로 찍혀 attribution이 깨진다.
- 공유/봇 명의가 필요하면 별도 OAuth `app` user 경로가 있으나, teammode 기본은 **개인 attribution 유지**.

---

## 5. 빠른 체크리스트 (에이전트용)

- [ ] 경로 선택: 1-A(공식 OAuth, 권장) vs 1-B(개인 키)
- [ ] (1-A) `claude mcp add --transport http linear-server https://mcp.linear.app/mcp` → `/mcp` 로 OAuth
- [ ] (1-B) Settings → Account → Security & Access → Personal API keys → New API key → 즉시 복사 → MCP env 주입
- [ ] scope: 최소 Write + Create issues (필요시 팀 제한)
- [ ] `teams` / `projects` / `workflowStates(type 포함)` 조회해 ID 캐싱
- [ ] state 매핑: backlog→backlog, in_progress→started, done→completed (팀별 UUID)
- [ ] attribution 확인: 본인 계정으로 연결됐는지(각자 1회)

---

## 출처
- Linear Docs — MCP server: https://linear.app/docs/mcp
- Linear Docs — Security & Access: https://linear.app/docs/security-and-access (`https://linear.app/settings/account/security`)
- Linear Docs — Issue status / workflows: https://linear.app/docs/configuring-workflows
- Linear Developers — Getting started (GraphQL): https://linear.app/developers/graphql (endpoint `https://api.linear.app/graphql`)
- Claude — Remote MCP in Claude Code: https://claude.com/blog/claude-code-remote-mcp
- npm — `@ibraheem4/linear-mcp`, `@tacticlaunch/mcp-linear` (1-B 로컬 패키지 옵션)
