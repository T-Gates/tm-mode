# Jira 연결 가이드 (L2 온보딩)

> 목적: teammode 온보딩 시 **에이전트가 이 문서만 보고** 팀원을 "토큰 발급 → MCP 등록 → 리소스 ID 확보"까지 끝낼 수 있게 하는 작업 지시서.
> 기준일: 2026-06-15. 출처는 모두 현행 공식 문서(Atlassian Support / Atlassian Developer) + 대표 커뮤니티 패키지 README. 추측 아님.
> **탐색 팁(에이전트 → 유저):** 유저가 화면에서 못 찾으면 **Ctrl-F(맥: Cmd-F) / F3** 페이지 내 검색을 적극 쓰게 하라. 토큰 페이지에서 토큰이 안 보이면 "Create"·"API token"으로 검색.

---

## 0. 온보딩 한 줄 안내 (에이전트가 유저에게 먼저 보낼 문장)

> "Jira를 연결할게요. 두 가지 길이 있어요 — (A) **공식 원격 MCP(Atlassian Rovo MCP)**: OAuth 로그인 1번이면 끝, 키 관리 불필요(권장). 또는 (B) **개인 API 토큰 + 로컬 MCP**(`sooperset/mcp-atlassian`): 키를 직접 관리. teammode는 **액션이 '본인'으로 찍혀야**(attribution) 하므로 둘 다 **각자 1회**씩 본인 Atlassian 계정으로 해야 합니다. 어느 쪽으로 갈까요? 잘 모르겠으면 A를 추천해요. (Jira Cloud 기준입니다. Server/Data Center면 알려주세요)"

---

## 1. Jira MCP 서버

### 1-A. (권장) 공식 원격 MCP — Atlassian Rovo MCP Server (OAuth, 키 관리 불필요)

Atlassian이 직접 호스팅하는 원격 MCP. Jira·Confluence·Compass를 LLM/IDE/에이전트에 연결. **2026년 2월 GA**(Claude가 첫 공식 파트너). 로컬 설치 불필요 — 기존 권한을 그대로 존중하는 프록시.

- **권장 엔드포인트(authv2):** `https://mcp.atlassian.com/v1/mcp/authv2`
- **(API 토큰 헤더 방식 엔드포인트):** `https://mcp.atlassian.com/v1/mcp`
- ⚠️ **`https://mcp.atlassian.com/v1/sse` 는 2026-06-30 이후 지원 종료.** SSE 쓰던 구설정은 `/mcp` 계열로 갱신.
- 검색·요약·생성·수정·벌크관리(Jira 이슈, Confluence 페이지, Compass 컴포넌트) 도구 제공.

**Claude Code 등록 (원격 HTTP, 네이티브 — 브리지 불필요):**
```bash
claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2
```
등록 후 Claude Code 세션에서 `/mcp` 실행 → 브라우저 OAuth 2.1 로그인 → 본인 Atlassian 사이트 승인.
- 기본은 **로컬(per-project, 본인) config**. 팀 공유하려면 `--scope project`(커밋되는 `.mcp.json`), 전 프로젝트에 쓰려면 `--scope user`.

**Codex 등록 (CLI):**
```bash
codex mcp add atlassian --url https://mcp.atlassian.com/v1/mcp/authv2
```
그 후 `codex mcp login atlassian` 로 OAuth. 첫 원격 MCP면 `~/.codex/config.toml` 에 rmcp 켜기:
```toml
[features]
experimental_use_rmcp_client = true

[mcp_servers.atlassian]
url = "https://mcp.atlassian.com/v1/mcp/authv2"
```

**원격 MCP를 네이티브로 못 쓰는 구형 클라이언트(VS Code/Cursor 등) — `mcp-remote` 브리지** (Node.js v18+ 필요):
```json
{
  "mcpServers": {
    "atlassian": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.atlassian.com/v1/mcp/authv2"]
    }
  }
}
```
첫 연결 시 브라우저가 열려 OAuth 진행. (Claude Code/Codex는 네이티브 HTTP라 브리지 불필요)

**Claude.ai(데스크톱/웹) 커넥터:** Settings → Connectors 에서 Atlassian 추가.

**(옵션) OAuth 대신 API 토큰 헤더 직접 주입** — 조직 어드민이 허용한 경우, interactive OAuth 없이 헤더로 인증(봇/CI/헤드리스). 엔드포인트는 `https://mcp.atlassian.com/v1/mcp`:
```json
{
  "mcpServers": {
    "atlassian-rovo-mcp": {
      "url": "https://mcp.atlassian.com/v1/mcp",
      "headers": {
        "Authorization": "Basic BASE64_ENCODED_EMAIL_AND_TOKEN"
      }
    }
  }
}
```
- **개인 API 토큰** → `Authorization: Basic <base64(email:api_token)>` (§2-B).
- **서비스 계정 API 키**(어드민 발급) → `Authorization: Bearer <api_key>`.
- ⚠️ 토큰 헤더 방식은 OAuth보다 **사용 가능 도구가 적을 수 있음**(일부 Compass 등 비활성), 또 토큰이 특정 `cloudId`에 묶이지 않으므로 **요청마다 cloudId를 명시**해야 함.

### 1-B. (대안) 개인 API 토큰 + 로컬 MCP 패키지 — `sooperset/mcp-atlassian`

키를 직접 관리하거나 권한을 좁히고 싶을 때, 또는 **Server/Data Center**일 때(공식 Rovo MCP는 Cloud 전용). 가장 널리 쓰이는 커뮤니티 패키지. Cloud + Server/DC 모두 지원, Jira + Confluence 모두.

- **패키지:** `mcp-atlassian` (PyPI, `uvx`로 실행) / Docker 이미지 `ghcr.io/sooperset/mcp-atlassian:latest`
- repo: `github.com/sooperset/mcp-atlassian`
- **Cloud 필수 env:** `JIRA_URL`(예: `https://your-company.atlassian.net`), `JIRA_USERNAME`(본인 이메일), `JIRA_API_TOKEN`(§2-A 토큰)
- **Server/Data Center:** `JIRA_USERNAME`+`JIRA_API_TOKEN` 대신 `JIRA_PERSONAL_TOKEN` 사용 (PAT)
- Confluence도 쓰려면 `CONFLUENCE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_API_TOKEN` 추가. Jira만 쓰려면 Jira env만 줘도 됨.

**Claude Code 등록 예시 (uvx, env 주입):**
```bash
claude mcp add atlassian \
  --env JIRA_URL=https://your-company.atlassian.net \
  --env JIRA_USERNAME=you@company.com \
  --env JIRA_API_TOKEN=ATATT_xxx \
  -- uvx mcp-atlassian
```
또는 설정 파일 직접 작성:
```json
{
  "mcpServers": {
    "atlassian": {
      "command": "uvx",
      "args": ["mcp-atlassian"],
      "env": {
        "JIRA_URL": "https://your-company.atlassian.net",
        "JIRA_USERNAME": "you@company.com",
        "JIRA_API_TOKEN": "ATATT_xxx"
      }
    }
  }
}
```

**Docker 방식:**
```bash
docker run --rm -i \
  -e JIRA_URL=https://your-company.atlassian.net \
  -e JIRA_USERNAME=you@company.com \
  -e JIRA_API_TOKEN=ATATT_xxx \
  ghcr.io/sooperset/mcp-atlassian:latest
```

**Codex 등록 (`~/.codex/config.toml`):**
```toml
[mcp_servers.atlassian]
command = "uvx"
args = ["mcp-atlassian"]
env = { JIRA_URL = "https://your-company.atlassian.net", JIRA_USERNAME = "you@company.com", JIRA_API_TOKEN = "ATATT_xxx" }
```

> ⚠️ teammode 기본은 **1-A 공식 원격 MCP**. 1-B는 키 직접 관리/권한 제한이 꼭 필요하거나 **Server/DC**일 때만.

---

## 2. 토큰 발급 단계 (개인 API 토큰)

> 공식 MCP(1-A) OAuth 경로는 이 단계 **불필요**(로그인만). 1-B 로컬 MCP, REST 직접 호출, 또는 1-A 토큰 헤더 주입 시에만 필요.
> 필요한 3종 세트: **Cloud 인스턴스 URL**(`https://<your-site>.atlassian.net`) + **계정 이메일** + **API 토큰**.

### 2-A. API 토큰 만들기 — 클릭 경로

1. https://id.atlassian.com/manage-profile/security/api-tokens 접속 (본인 Atlassian 계정 로그인 상태)
   - 경로로 찾으려면: Atlassian 계정 → **Account settings → Security → API tokens** → **Create API token**
2. **Create API token** (클래식) 또는 **Create API token with scopes** (스코프 지정) 클릭
3. 라벨(이름) + 만료일 입력 → **Create**
4. **토큰은 한 번만 표시됨.** 즉시 복사해 안전한 곳에 저장(분실 시 재발급만 가능).
5. 토큰 소유 계정의 **이메일 주소**를 함께 기록(헤더/`JIRA_USERNAME`에 필요).

> **Rovo MCP용 빠른 링크(스코프 자동 채움):** 1-A 토큰 헤더 방식을 쓸 거면 공식이 제공하는 프리필 URL을 쓰면 편함:
> `https://id.atlassian.com/manage-profile/security/api-tokens?autofillToken&expiryDays=max&appId=mcp&selectedScopes=all`
> 스코프를 직접 고르려면 그 화면에서 **Back** 눌러 수동 선택.

### 2-B. Base64 인코딩 (1-A 토큰 헤더 방식일 때만)

`email:api_token` 형식을 base64로 인코딩해 `Authorization: Basic <...>` 에 넣는다:
```bash
# 형식: email:api_token
echo -n "you@company.com:ATATT_xxx" | base64
```
출력값을 §1-A 토큰 헤더 JSON의 `BASE64_ENCODED_EMAIL_AND_TOKEN` 자리에 붙임.
> 1-B 로컬 MCP는 인코딩 불필요 — `JIRA_USERNAME`/`JIRA_API_TOKEN` env에 그대로.

### 2-C. 토큰 형식 / 클래식 vs 스코프드

- 토큰 접두사: `ATATT...` 형태.
- **클래식 토큰:** 유저가 가진 **모든 권한** 부여. 엔드포인트는 `https://<site>.atlassian.net/rest/api/3/...`.
- **스코프드(granular) 토큰:** 선택한 scope만 부여(최소 권한, 더 안전). 단 엔드포인트가 다름 → `https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...` (Cloud ID 필요).
- teammode 자동화엔 클래식이 단순. 보안상 스코프드를 쓰면 cloudId 라우팅 필요(§3).
- ⚠️ 클래식 토큰은 2026 중반까지는 동작하지만 점진 폐기 흐름. 현재 신규는 스코프드 권장 추세.

### 2-D. OAuth(3LO) 옵션

Jira는 personal API token 외 **OAuth 2.0 (3LO)** 도 지원 — 자체 앱 등록 후 `Authorization: Bearer <access_token>` 으로 호출. 단 teammode 온보딩 기본 경로로는 과함: 다수 멤버 attribution은 **1-A 공식 Rovo MCP의 per-user OAuth로 이미 해결**된다. 자체 OAuth 앱은 봇/통합 서비스를 따로 만들 때만.

---

## 3. 필요한 리소스 ID (토큰만으론 안 끝나는 부분)

> **중요:** 토큰/MCP 연결이 끝나도, teammode가 "이슈를 어느 프로젝트에 만들지", "상태를 To Do/In Progress/Done 중 무엇으로 바꿀지" 정하려면 **project key·status·transition ID**가 필요하다. **단, 토큰만 있으면 REST/MCP로 자동 조회 가능** — 유저가 손으로 ID를 찾아 넣을 필요 없고, 에이전트가 아래로 긁어오면 된다.

- **REST 베이스(클래식 토큰):** `https://<site>.atlassian.net/rest/api/3/...`
- **REST 베이스(스코프드 토큰):** `https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...`
- **Cloud ID 조회:** `GET https://<site>.atlassian.net/_edge/tenant_info` → `cloudId` 반환. (또는 `https://api.atlassian.com/oauth/token/accessible-resources` 로 접근 가능한 사이트·cloudId 목록)

**프로젝트 목록 / project key:**
```
GET /rest/api/3/project/search
```
→ 각 프로젝트의 `key`(예: `TG`), `id`, `name`. 이슈 생성 시 **project key 또는 id 필요**.

**보드(board) 목록 (Jira Software):**
```
GET /rest/agile/1.0/board
```
→ 보드 `id`/`name`/연결 `location`(프로젝트). 스프린트·백로그 다룰 때.

**상태(status) 조회 — 프로젝트/이슈타입별:**
```
GET /rest/api/3/project/{projectIdOrKey}/statuses
```
→ 이슈타입마다 사용 가능한 status 목록(`name`, `id`, `statusCategory`).
- 인스턴스 전체 status는 `GET /rest/api/3/status`.
- `statusCategory.key` enum: **`new`(To Do), `indeterminate`(In Progress), `done`(Done)**. teammode 매핑은 이 카테고리 기준이 안전(표시명은 프로젝트마다 다름).

**상태 변경 = transition (status를 직접 set 불가, transition을 거쳐야 함):**
```
GET  /rest/api/3/issue/{issueIdOrKey}/transitions      # 현재 가능한 transition 목록+id
POST /rest/api/3/issue/{issueIdOrKey}/transitions
     body: { "transition": { "id": "31" } }
```
- ⚠️ Jira는 **status로 바로 못 바꾼다.** 먼저 GET으로 가능한 transition을 조회 → 원하는 status로 가는 transition `id`를 골라 POST.
- ⚠️ transition은 **이슈의 현재 상태·워크플로에 따라 달라짐.** 매번 GET으로 현행 목록을 받아야 함(하드코딩 금지).
- 동시 transition 미지원(중복 요청 시 409/400).

**이슈 생성 (project key + issuetype 필수):**
```
POST /rest/api/3/issue
body: { "fields": {
  "project": { "key": "TG" },
  "issuetype": { "name": "Task" },
  "summary": "New issue",
  "description": { ... ADF ... }
}}
```
> 자동화 권장 플로우(에이전트): 토큰/MCP 연결 직후 → `project/search` + (필요시) `project/{key}/statuses` 한 번 긁어 → teammode 설정(team.config 등)에 project key / status 카테고리 매핑 캐싱. transition id는 캐싱하지 말고 **이슈별로 실시간 GET**.

---

## 4. Scope: 개인 단위 (attribution)

- **Jira 연결은 개인(per-user)이다.** 공식 Rovo MCP는 OAuth로 본인 계정 로그인, 개인 API 토큰도 본인 계정·이메일에 종속.
- 따라서 **에이전트가 만든 이슈·코멘트·상태변경은 그 사람 명의로 찍힌다(attribution).** 누가 무엇을 했는지 Jira 히스토리에 본인으로 남음. Rovo MCP는 본인의 기존 Jira 권한을 그대로 따른다.
- ⇒ **팀원 각자 1회씩** 본인 계정으로 연결해야 한다. 한 명이 대표로 연결해 공유하면 모든 액션이 그 한 사람으로 찍혀 attribution이 깨진다.
- 공유/봇 명의가 필요하면 어드민이 **서비스 계정 API 키(Bearer)** 또는 자체 OAuth 앱을 쓰는 경로가 있으나, teammode 기본은 **개인 attribution 유지**.

---

## 5. 빠른 체크리스트 (에이전트용)

- [ ] Cloud vs Server/DC 확인 (Server/DC면 1-A 불가 → 1-B + `JIRA_PERSONAL_TOKEN`)
- [ ] 경로 선택: 1-A(공식 Rovo OAuth, 권장) vs 1-B(개인 토큰 + sooperset)
- [ ] (1-A) `claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2` → `/mcp` 로 OAuth
- [ ] (1-B) https://id.atlassian.com/manage-profile/security/api-tokens → Create → 즉시 복사 → `JIRA_URL`/`JIRA_USERNAME`/`JIRA_API_TOKEN` env 주입
- [ ] 3종 세트 확보: Cloud URL + 이메일 + 토큰 (토큰 헤더 방식이면 base64(email:token))
- [ ] `project/search` 로 project key 조회·캐싱
- [ ] status 매핑: statusCategory `new`→To Do, `indeterminate`→In Progress, `done`→Done
- [ ] 상태변경은 transition: 이슈별 `GET .../transitions` → `POST .../transitions {id}` (실시간, 하드코딩 금지)
- [ ] 스코프드 토큰이면 cloudId 확보 후 `api.atlassian.com/ex/jira/{cloudId}/...` 라우팅
- [ ] attribution 확인: 본인 계정으로 연결됐는지(각자 1회)

---

## 출처
- Atlassian — Remote MCP server 소개: https://www.atlassian.com/blog/announcements/remote-mcp-server
- Atlassian Support — Getting started (Rovo MCP): https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/
- Atlassian Support — Configuring authentication via API token (엔드포인트·Basic/Bearer·base64·한계): https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token/
- Atlassian Support — Configuring OAuth 2.1: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-oauth-2-1/
- Atlassian Support — Setting up IDEs (mcp-remote 브리지): https://support.atlassian.com/atlassian-rovo-mcp-server/docs/setting-up-ides/
- GitHub — atlassian/atlassian-mcp-server: https://github.com/atlassian/atlassian-mcp-server
- GitHub — sooperset/mcp-atlassian (env·Docker·Server/DC PAT): https://github.com/sooperset/mcp-atlassian
- PyPI — mcp-atlassian: https://pypi.org/project/mcp-atlassian/
- Atlassian — API tokens 관리: https://id.atlassian.com/manage-profile/security/api-tokens
- Atlassian Dev — Jira Cloud REST v3 (projects/issues/transitions): https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- Atlassian Dev — Jira Software REST (boards): https://developer.atlassian.com/cloud/jira/software/rest/api-group-board/
- Codex MCP(config.toml): https://developers.openai.com/codex/mcp
