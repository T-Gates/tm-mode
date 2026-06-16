# L2 온보딩 — Google Calendar 연결 가이드

> 대상: teammode L2 멤버 (개발자 아님 포함).
> 목적: 에이전트가 이 문서만 보고 유저를 **Google 인증 → MCP 등록 → 동작 확인**까지 끌고 가게.
> 현행 기준: 2026-06 (nspady/google-calendar-mcp = `@cocal/google-calendar-mcp`).
> 출처: 본 문서 맨 아래 "출처" 섹션. 추측 없음, 공식 repo README/authentication.md 기반.

---

## 0. TL;DR (에이전트용 실행 순서)

1. **GCP에서 OAuth 자격증명 발급** → `gcp-oauth.keys.json` 파일 1개 다운로드 (1회만).
2. **MCP 등록** (`claude mcp add` 또는 Codex `config.toml`), env로 그 파일 경로 지정.
3. **에이전트에게 "구글 캘린더 인증해줘"라고 말함** → 브라우저가 뜸 → **"허용(Allow)" 클릭**.
4. 끝. `list-calendars` 호출되면 성공.

핵심 메시지(유저에게 그대로):
> **"잠시 후 브라우저 창이 뜨면 본인 구글 계정으로 로그인하고 '허용'을 눌러주세요. 그게 전부예요."**

---

## 1. 대표 MCP 서버

| 항목 | 값 |
|------|-----|
| repo | `nspady/google-calendar-mcp` (오픈소스 사실상 표준, GitHub stars 최다) |
| npm 패키지 | `@cocal/google-calendar-mcp` |
| 실행 | `npx @cocal/google-calendar-mcp` (별도 설치 불필요) |
| 트랜스포트 | stdio (기본). HTTP/Docker 모드도 있으나 온보딩엔 stdio가 단순 |
| 라이선스 | MIT |
| 멀티계정 | 지원 (work/personal/family 닉네임으로 동시 연결) |

> 후보군: `rsc1102/Google_Calendar_MCP`, `deciduus/calendar-mcp`(Python), `guinacio/mcp-google-calendar`, `domdomegg/google-cal-mcp` 등이 있으나 **nspady가 사실상 표준**이라 이걸로 통일한다.

### 제공 툴 (참고)
`list-calendars`, `list-events`, `get-event`, `search-events`, `create-event`, `update-event`, `delete-event`, `respond-to-event`, `get-freebusy`, `get-current-time`, `list-colors`, `manage-accounts`

> 컨텍스트/보안 절약: `ENABLED_TOOLS` env 또는 `--enable-tools`로 노출 툴 제한 가능
> (예: `ENABLED_TOOLS="list-events,create-event,get-current-time,update-event"`).
> `manage-accounts`는 필터와 무관하게 항상 노출됨(인증 관리용).

---

## 2. 인증 단계 — 두 갈래 중 하나

인증은 결국 **GCP에서 OAuth client(데스크톱 앱)를 한 번 만들고**, 그 JSON을 MCP에 물려준 뒤
**MCP가 띄우는 OAuth 흐름에서 "허용"만 누르면** 끝난다. 두 작업은 별개가 아니라 순차다.

### 2-A. GCP에서 OAuth client(Desktop app) 만들기 — 1회 (필수)

> 클릭 경로. 유저에게 한 단계씩 읽어주며 진행. **반드시 "Desktop app" 타입**이어야 함(중요).

1. https://console.cloud.google.com 접속.
2. 상단 프로젝트 선택 → **"New Project"** → 이름(예: `Calendar MCP`) → **Create**.
   - 이후 단계 전, **상단바에서 방금 만든 프로젝트가 선택돼 있는지 확인**.
3. **Calendar API 활성화**: "APIs & Services" → **Library** → "Google Calendar API" 검색 → **Enable**.
   - 바로가기: https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
4. **OAuth 동의 화면(consent screen)** 구성 (처음이면 자격증명 만들 때 먼저 뜸):
   - User type: **External**
   - App name / User support email / Developer contact: 본인 이메일
   - **Scopes 추가**: "Add or Remove Scopes" →
     - `https://www.googleapis.com/auth/calendar.events` (이벤트 읽기/쓰기)
     - 또는 더 넓게 `https://www.googleapis.com/auth/calendar` (캘린더 전체)
     - (읽기 전용만 원하면 `https://www.googleapis.com/auth/calendar.readonly`)
   - **Test users 추가**: 본인 구글 이메일 등록.
     - ⚠️ **추가 후 2~3분 전파 대기**. 안 기다리면 인증 화면에서 막힘("Access blocked").
     - Audience 화면: https://console.cloud.google.com/auth/audience
5. **OAuth client 생성**: "APIs & Services" → **Credentials** → **Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app** ← 가장 중요. 다른 타입이면 인증 실패.
   - Name: `Calendar MCP Client` → **Create**.
6. **JSON 다운로드**: 새 client 우측 다운로드(⬇️) → **`gcp-oauth.keys.json`** 으로 저장.
   - 안전한 경로에 보관 (예: `~/.config/google-calendar-mcp/gcp-oauth.keys.json`).
   - 권한 잠금: `chmod 600 <경로>`. **절대 git에 커밋 금지**(.gitignore 추가).

#### `gcp-oauth.keys.json` 형식 (참고 — Desktop app은 `installed` 키)
```json
{
  "installed": {
    "project_id": "YOUR_PROJECT_ID",
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "client_secret": "YOUR_CLIENT_SECRET",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"]
  }
}
```
> `project_id` 반드시 포함 → 없으면 "User Rate Limit Exceeded" 에러.
> `redirect_uris`의 `http://localhost`(루프백)가 MCP 인증 흐름의 핵심.

### 2-B. MCP가 제공하는 OAuth 흐름 (실제 "허용" 단계)

> 2-A에서 만든 JSON을 MCP에 물려놓으면(§3), **GCP 콘솔 추가 작업 없이** 아래로 끝난다.

1. 유저가 에이전트에게 **"구글 캘린더 인증해줘"** (Claude Code/Desktop 동일).
   - ⚠️ **인증 전에는 어떤 캘린더 툴도 실패**(`-32600` 에러). 반드시 먼저 인증.
2. 브라우저 창 자동으로 뜸 → 본인 구글 계정 로그인.
3. **"허용(Allow)"** 클릭 (loopback `http://localhost`로 토큰 받음 → 로컬 저장).
4. "성공" 화면 뜨면 에이전트로 복귀. 끝.

> **온보딩 난이도**: 2-A(GCP client 발급)는 1회성이고 클릭이 많지만 불가피.
> 일단 발급되면 2-B는 "브라우저 뜨면 허용 클릭" 1번이라 매우 쉽다.
> → **2-A를 에이전트가 단계별로 손잡고 끌어주는 게 온보딩 성패를 가름.**

### 토큰 저장 위치 (자동)
- macOS/Linux: `~/.config/google-calendar-mcp/tokens.json`
- Windows: `%APPDATA%\google-calendar-mcp\tokens.json`
- 커스텀: `GOOGLE_CALENDAR_MCP_TOKEN_PATH` env.

### 재인증 (test 모드 = 토큰 7일 만료)
```bash
export GOOGLE_OAUTH_CREDENTIALS="/path/to/gcp-oauth.keys.json"
npx @cocal/google-calendar-mcp auth
```
> **7일 만료 피하기**: GCP 콘솔 OAuth consent screen → **"PUBLISH APP"** → production.
> 토큰 무기한. 단 "미검증 앱" 경고 화면이 뜸(유저가 우회 클릭하면 됨).

---

## 3. MCP 등록 형식

### Claude Code (CLI) — 권장
```bash
claude mcp add google-calendar \
  --env GOOGLE_OAUTH_CREDENTIALS=/path/to/gcp-oauth.keys.json \
  -- npx -y @cocal/google-calendar-mcp
```
- `--env KEY=value` (단축 `-e`). 여러 개면 플래그 반복.
- `--` 뒤부터는 서버 실행 명령(그대로 전달). 옵션과 명령 구분자라 **필수**.
- 스코프 지정 옵션: `--scope user`(전역) / `--scope project`(프로젝트). 생략 시 기본 local.
- (선택) 툴 제한: `--env ENABLED_TOOLS=list-events,create-event,get-current-time` 추가.

### Claude Desktop (JSON config)
`~/Library/Application Support/Claude/claude_desktop_config.json` (mac) /
`%APPDATA%\Claude\claude_desktop_config.json` (win):
```json
{
  "mcpServers": {
    "google-calendar": {
      "command": "npx",
      "args": ["@cocal/google-calendar-mcp"],
      "env": {
        "GOOGLE_OAUTH_CREDENTIALS": "/path/to/gcp-oauth.keys.json"
      }
    }
  }
}
```

### Codex CLI — `~/.codex/config.toml`
```toml
[mcp_servers.google-calendar]
command = "npx"
args = ["-y", "@cocal/google-calendar-mcp"]

[mcp_servers.google-calendar.env]
GOOGLE_OAUTH_CREDENTIALS = "/path/to/gcp-oauth.keys.json"
```
> `[mcp_servers.<name>.env]`는 반드시 같은 `<name>` 아래 중첩. 이름 오타 나면 블록이 끊겨 무시됨.
> 기존 셸 변수를 넘기려면 `env_vars = ["GOOGLE_OAUTH_CREDENTIALS"]` 형태도 가능.

---

## 4. 필요한 리소스 ID

### Calendar ID — **자동 조회**, 손으로 안 찾아도 됨
- 인증 후 **`list-calendars` 툴**이 연결된 모든 캘린더와 ID를 반환(내부적으로 Google calendarList API).
- 형태:
  - 기본(주) 캘린더: 보통 본인 이메일 그대로 (`bob@gmail.com`).
  - 보조/공유 캘린더: `...@group.calendar.google.com`.
- 에이전트 흐름: 인증 → `list-calendars` 1회 → 결과에서 대상 캘린더 ID 골라 이후 `create-event` 등에 사용.
- (수동 확인이 굳이 필요하면: Google Calendar 웹 → 캘린더 설정 → "캘린더 통합" → "캘린더 ID".)

### colorId
- 이벤트 색상은 `colorId`(문자열 번호)로 지정.
- **`list-colors` 툴**이 사용 가능한 이벤트/캘린더 색상과 그 ID를 반환 → 거기서 골라 `create-event`에 전달.

---

## 5. Scope (개인/혼합)

### OAuth scope (권한 범위)
| scope | 의미 | 추천 |
|-------|------|------|
| `calendar.readonly` | 읽기 전용 | 조회만 필요한 멤버 |
| `calendar.events` | 이벤트 읽기/쓰기 | **기본 권장** (등록·수정까지) |
| `calendar` | 캘린더 전체(생성/삭제/설정) | 풀권한 필요할 때만 |

### 캘린더 가시성 = 개인/팀 혼합
- **개인 캘린더**: 각자 본인 구글 계정으로 인증 → 본인만 접근(프라이빗).
- **팀/공유 캘린더**: Google Calendar에서 해당 캘린더를 멤버 계정과 **공유**해두면,
  그 멤버가 자기 인증으로 `list-calendars` 했을 때 공유 캘린더도 함께 잡힘 → 팀 일정 공동 관리.
- 즉 **혼합 운영 가능**: 인증은 개인 계정 1개로 하되, 공유 설정에 따라 개인+팀 캘린더가 한 번에 노출.
- teammode 권장: 개인 캘린더는 개인 인증으로, 팀 일정은 **팀 공유 캘린더 1개를 멤버들에게 공유**해서
  각자 자기 MCP로 같은 팀 캘린더에 쓰게 함.

---

## 6. 온보딩 한 줄 안내 + 탐색 팁

### 유저에게 줄 한 줄 (그대로 복붙)
> **"브라우저 창이 뜨면 본인 구글 계정으로 로그인 → '허용(Allow)' 버튼만 누르면 연결 끝!"**

재인증(7일 후)일 때:
> **"인증이 만료됐어요. 다시 브라우저 뜨면 '허용'만 눌러주세요."**

### GCP 콘솔에서 길 잃을 때 — Ctrl-F / F3 탐색 팁
- 콘솔/페이지 내 검색: **Ctrl+F**(Win/Linux) / **Cmd+F**(mac). 다음 결과로 이동은 **F3** 또는 **Enter**.
- 찾을 키워드 예시:
  - 자격증명 만들기 → `Create Credentials` / `OAuth client ID`
  - 앱 타입 고를 때 → `Desktop`
  - 스코프 추가 → `Add or Remove Scopes`
  - 테스트 유저 → `Test users` / `Audience`
  - 토큰 만료 풀기 → `Publish App`
- 콘솔 상단 **검색바**에 "OAuth consent screen", "Credentials", "Calendar API" 직접 입력하면 바로 점프.

---

## 7. 자주 막히는 곳 (에이전트 체크리스트)

| 증상 | 원인 / 해결 |
|------|------------|
| `-32600` 에러 | 인증을 안 함 → 먼저 "구글 캘린더 인증해줘" |
| "Access blocked" | test user 미등록/미전파 → 본인 이메일 추가 후 2~3분 대기 |
| "Invalid credentials" | client 타입이 Desktop app이 아님 → 재발급 |
| "User Rate Limit Exceeded" | JSON에 `project_id` 누락 → 재다운로드 |
| 인증 브라우저 "Something went wrong" | Chromium 계열 브라우저 사용 / `npx ... auth` 수동 실행 |
| 토큰 7일마다 만료 | test 모드 정상 동작. 주기 재인증 or Publish App |
| npx인데 자격증명 못 찾음 | `GOOGLE_OAUTH_CREDENTIALS`를 **절대경로**로 지정했는지 |
| 포트 충돌 | 3500~3505 포트 막혔는지 확인 |

---

## 출처
- [nspady/google-calendar-mcp — README](https://github.com/nspady/google-calendar-mcp) (`@cocal/google-calendar-mcp`)
- [nspady/google-calendar-mcp — docs/authentication.md](https://github.com/nspady/google-calendar-mcp/blob/main/docs/authentication.md)
- [Connect Claude Code to tools via MCP — Claude Code Docs](https://code.claude.com/docs/en/mcp)
- [Model Context Protocol — Codex | OpenAI Developers](https://developers.openai.com/codex/mcp)
- [Best Google Calendar MCP Servers in 2026 — CalendarMCP](https://calendarmcp.ai/blog/best-calendar-mcp-servers-2026)
