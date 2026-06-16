# L2 온보딩 — Notion 연결 가이드

> **이 문서의 목적**: 에이전트가 이 문서만 보고 유저를 **토큰 발급 → 공유(Connections) → MCP 등록**까지 끝까지 이끌 수 있게.
> **scope**: Notion 연결은 **팀 단위 1회 도입**. 도입자 1명이 integration 1개를 만들고, 팀이 쓸 DB/페이지를 거기에 공유하면 끝. 팀원 각자가 토큰을 새로 만들 필요 없음 (토큰 1개를 공유하거나, 각자 OAuth 방식 사용).
> 현행 기준: `@notionhq/notion-mcp-server` v2.x (2026-06 확인). 추측 없이 공식 README + 팀 실제 설정 기준.

---

## 0. 온보딩 한 줄 안내 (유저에게 그대로 던질 문장)

> "Notion을 연결할게요. (1) https://www.notion.so/profile/integrations 에서 **New integration → Internal**으로 만들고 **Internal Integration Secret(`ntn_…`)** 복사 → (2) **팀이 쓸 DB/페이지를 그 integration에 공유(⋯ → Connections → Connect to)** → (3) 토큰을 알려주시면 MCP에 등록합니다. (2)번 공유 단계를 빼먹으면 API가 전부 막히니 꼭 하세요."

**탐색 팁**: 이 문서/Notion 화면에서 **Ctrl-F**(브라우저·대부분 앱) 또는 **F3**으로 키워드 점프. Notion 설정 화면에선 `Connections`, `Capabilities`, `Secret`, `Access` 단어를 Ctrl-F로 찾으면 빠름.

---

## 1. Notion MCP 서버 (공식 + 등록 형식)

### 1-A. 셀프호스트 (팀 현행 방식 — 권장 기본)

**패키지**: 공식 [`@notionhq/notion-mcp-server`](https://github.com/makenotion/notion-mcp-server) (npm, `makenotion` 공식 레포). 팀은 현재 `^2.2.1` 핀.

**설치**:
```bash
npm i @notionhq/notion-mcp-server     # 또는 npx -y @notionhq/notion-mcp-server 로 무설치 실행
```

**env (둘 중 하나)**:

| 변수 | 값 | 비고 |
|------|----|----|
| `NOTION_TOKEN` | `ntn_****` | **권장**. 토큰만 넣으면 됨 |
| `OPENAPI_MCP_HEADERS` | `{"Authorization":"Bearer ntn_****","Notion-Version":"2022-06-28"}` | 고급용. 헤더 직접 지정 (Notion-Version 명시 가능) |
| `NOTION_VERSION` | `2022-06-28` | 팀 start.sh가 쓰는 값. 미지정 시 패키지 기본값 사용 |

> ⚠️ 패키지 v2.0.0부터 내부적으로 Notion API `2025-09-03`(data source 추상화)로 이동. 팀 start.sh는 `OPENAPI_MCP_HEADERS`로 `Notion-Version: 2022-06-28`을 강제해 기존 동작 유지 중. 그대로 따르면 됨.

**MCP 등록 형식 (Claude Code / Cursor `.mcp.json` · `claude_desktop_config.json` · `~/.claude.json`)**:

가장 단순 (NOTION_TOKEN 직접):
```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@notionhq/notion-mcp-server"],
      "env": { "NOTION_TOKEN": "ntn_****" }
    }
  }
}
```

**팀 실제 등록 형식 (참고 — T-gates `notion-acme`)**: 토큰을 JSON에 박지 않고 `.env` + start.sh 래퍼로 분리.
- 등록: `command` = `.../infra/mcp/notion-acme/start.sh`, `args` = `[]`, `env` = `{}`
- `start.sh`가 `.env`의 `NOTION_TOKEN`/`NOTION_VERSION`을 읽어 `OPENAPI_MCP_HEADERS`를 조립한 뒤 `node node_modules/@notionhq/notion-mcp-server/bin/cli.mjs` 실행
- `.env`는 `.gitignore` 처리 (토큰 커밋 방지)
- `.env.example`:
  ```
  NOTION_TOKEN=ntn_your_token_here
  NOTION_VERSION=2022-06-28
  ```

> 신규 팀이라면 이 래퍼 패턴을 복사해서 쓰는 게 가장 안전 (토큰을 글로벌 config에 평문으로 안 남김).

### 1-B. 원격(호스티드) MCP — OAuth (대안)

Notion이 직접 호스팅하는 원격 MCP. **토큰 발급·JSON 작업 불필요, OAuth 한 번으로 끝**.
- URL: `https://mcp.notion.com/mcp` (Streamable HTTP, OAuth 인증, bearer token 미지원)
- Claude Code 등록: `claude mcp add --transport http notion https://mcp.notion.com/mcp` → 브라우저 OAuth 승인
- 권한: 유저가 Notion 앱에서 가진 권한만큼 부여됨 (별도 공유 토글 불필요 — 워크스페이스 설치 시 권한 위임)
- 플랜 제약: Claude의 원격 커넥터는 Pro/Max/Team/Enterprise에서 사용
- 공식 안내: https://developers.notion.com/docs/mcp , https://developers.notion.com/guides/mcp/get-started-with-mcp

> 트레이드오프: 셀프호스트(1-A)는 토큰·버전·DB 공유를 팀이 통제 / 원격(1-B)은 설치가 쉽지만 유저별 OAuth라 "팀 1토큰 공유" 모델과 안 맞음. **팀 공유 자동화 파이프라인엔 1-A 권장.**

### 1-C. 커뮤니티 대안 (참고만)

공식이 있으므로 기본 사용 안 함. 존재 정도만:
- `awkoy/notion-mcp-server`, `suekou/mcp-notion-server`, `ccabanillas/notion-mcp`

---

## 2. 토큰 발급 단계 (핵심 함정 = 공유 토글)

### Step 1 — Integration 만들고 Secret 복사
1. 브라우저에서 **https://www.notion.so/profile/integrations** 접속 (구 경로 `notion.so/my-integrations`도 동일하게 리다이렉트)
2. **`New integration`** 클릭
3. 이름 입력 (예: `T-gates MCP`), **연결할 workspace 선택**, **Type = Internal** 확인 → 생성
4. **Capabilities** 설정 (Configuration 탭): 자동화엔 보통 **Read content + Update content + Insert content** 체크.
   - 읽기 전용으로 안전하게 가려면 **Read content만** 체크 (보안 권장 옵션)
5. **Configuration** 탭에서 **Internal Integration Secret** = 토큰 복사
   - 형식: **`ntn_…`** (신규). 구버전 워크스페이스/오래된 토큰은 **`secret_…`** 형식 — 둘 다 유효, 그대로 쓰면 됨

### Step 2 — DB/페이지를 integration에 공유 ⚠️ **(이거 빼먹으면 전부 404/권한오류 — 최대 함정)**

> Notion integration은 **기본적으로 어떤 콘텐츠에도 접근 권한이 없음.** 토큰이 유효해도, 공유 안 한 페이지는 API가 못 본다.

**방법 A — Integration 설정의 Access 탭에서 일괄 (권장)**:
- integration 설정 → **`Access`** 탭 → Edit access → 팀이 쓸 페이지/DB 선택

**방법 B — 페이지에서 개별 공유**:
1. 대상 DB/페이지 열기
2. 우상단 **`⋯`(점 3개)** 클릭
3. **`Connections`**(= Connect to) 선택
4. 검색창에 integration 이름 입력 → 선택 → 연결

> 부모 페이지를 공유하면 하위 페이지/DB도 상속됨. **팀 루트 페이지 하나를 공유**해 두면 그 아래 전부 접근 가능 → 도입자 1회 작업으로 끝.

---

## 3. 필요한 리소스 ID (DB ID · Page ID)

### ID 형식
- 32자 hex, 하이픈 패턴 **8-4-4-4-12** (예: `34230fbe-4508-8027-a987-c06488620f62`)
- URL엔 보통 하이픈 없이 32자 연속으로 나옴 → API엔 둘 다 통함 (하이픈 유/무 무관)

### URL에서 추출
- **페이지 URL**: `https://www.notion.so/워크스페이스/페이지제목-<32자ID>?...` → 제목 뒤 마지막 32자 토막이 page ID
- **DB(인라인/풀페이지)**: URL 경로의 32자 토막. 공개 링크는 `...notion.site/이름?v=<view_id>` 형태로 `v=` 뒤는 **view ID**(DB ID 아님)니 주의 — DB ID는 경로 쪽 32자

### 자동 조회 (에이전트가 직접 가능 — 토큰만 있으면)
- **Search API**로 공유된 페이지/DB를 이름으로 찾아 ID 획득:
  - MCP 툴: `API-post-search` / `notion-search` (검색어 → 결과의 `id` 사용)
  - 검색 필터 값 주의: v2(2025-09-03 API)에선 `["page","data_source"]` (구 `["page","database"]` 아님)
- **DB → data source**: v2에선 DB 작업이 `data_source_id` 기반. `retrieve-a-database`로 DB 메타 조회 시 그 안에 data source ID 목록이 들어 있음 → `query-data-source` 등에 사용
- 즉 **유저가 ID를 몰라도, 토큰 발급 + 공유만 끝나면 에이전트가 search로 자동 탐색 가능.** ID 수동 추출은 폴백.

---

## 4. scope (팀 단위 도입)

- **integration 1개** = 팀 공용. 도입자 1명이 만든다.
- 팀이 쓸 **DB/페이지를 그 integration에 공유** (루트 페이지 1개 공유로 하위 상속 권장).
- 토큰 1개를 팀 MCP 설정(`.env`)에 넣어 공유 → 팀원 각자 토큰 발급 불필요.
- **도입자 1회 작업**으로 완료. 이후 새 DB가 생기면 그 DB만 같은 integration에 추가 공유.
- (원격 OAuth 1-B를 쓰면 모델이 "유저별 권한"이라 팀 1토큰 공유와 안 맞음 → 팀 자동화엔 셀프호스트 1-A 토큰 공유 권장.)

---

## 5. 빠른 점검 (등록 후 동작 확인)

1. MCP 재시작/리로드 후 `notion` 서버가 떴는지 (툴 목록에 notion 툴 노출)
2. search 한 번 → 공유한 페이지가 결과에 나오면 OK
3. 결과가 비거나 권한오류면 → **Step 2 공유(Connections) 누락** 1순위 의심, 그다음 토큰 오타/Capabilities 확인

---

### Sources
- [makenotion/notion-mcp-server (공식 GitHub)](https://github.com/makenotion/notion-mcp-server)
- [@notionhq/notion-mcp-server (npm)](https://www.npmjs.com/package/@notionhq/notion-mcp-server)
- [Notion MCP docs (remote/OAuth)](https://developers.notion.com/docs/mcp) · [Get started with MCP](https://developers.notion.com/guides/mcp/get-started-with-mcp)
- [Notion Authorization guide](https://developers.notion.com/guides/get-started/authorization)
- [Notion API connections (Help Center)](https://www.notion.com/help/create-integrations-with-the-notion-api)
- 팀 실제 설정: `/home/jane-doe/work/extras/acme/acme-toolkit/infra/mcp/notion-acme/` (start.sh, .env.example, package.json)
