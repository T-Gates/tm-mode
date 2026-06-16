# teammode L2 온보딩 — Slack 연결 가이드

> 목적: 온보딩 에이전트가 **이 문서만 보고** 유저를 `봇 토큰 발급 → MCP 등록`까지 정확히 이끌도록.
> 조사 기준일: 2026-06-15. 추측 없음 — 아래는 각 서버의 현행 공식 문서/README 기준.

---

## 0. scope: 팀(team), 도입자 1회

| 항목 | 값 |
|------|-----|
| 연결 단위 | **워크스페이스당 봇 1개** (= 팀 공유) |
| 누가 | **도입자(팀 어드민) 1명이 1회** 발급·설치 → 토큰을 팀 크리덴셜로 공유 |
| 재실행 | 채널이 늘거나 scope를 더 줄 때만 (앱 재설치 필요) |
| 개인 단위 | 없음. Slack 슬롯은 personal이 아니라 **team** 슬롯이다. |

> 토큰은 팀 시크릿(`acme-credentials` / 노션 Secret 페이지)에 1번만 저장하면 전 팀원이 공유.

---

## 1. 어떤 MCP 서버를 쓰나

두 후보. **둘 다 stdio·npx·xoxb 봇 토큰을 지원**한다. teammode는 아래 1순위 권장.

| | **korotovsky/slack-mcp-server** (권장) | @modelcontextprotocol/server-slack (대체) |
|---|---|---|
| npm 패키지 | `slack-mcp-server` | `@modelcontextprotocol/server-slack` |
| repo | github.com/korotovsky/slack-mcp-server | github.com/modelcontextprotocol/servers-archived (src/slack) |
| 상태 | **활발히 유지보수** (1.4k+ stars) | **아카이브됨** (유지보수 종료, Zencoder가 일부 승계) |
| 봇 토큰 env | `SLACK_MCP_XOXB_TOKEN` | `SLACK_BOT_TOKEN` |
| 추가 필수 env | (없음 — 토큰만) | `SLACK_TEAM_ID`(T...), `SLACK_CHANNEL_IDS`(C...,C...) |
| 쓰기(메시지 전송) | **기본 OFF**, 명시적으로 켜야 함 (안전 기본값) | 기본 ON |
| 비고 | xoxp(유저)·xoxc/xoxd(브라우저세션)도 지원 | 단순함이 장점이나 아카이브 |

권장: **korotovsky + 봇 토큰(xoxb-)**. 유지보수되고, 쓰기가 기본 OFF라 사고가 적다.

---

## 2. 봇 토큰(xoxb-) 발급 — 클릭별 경로

> 빠른 길: **앱 manifest 붙여넣기**(2-B)가 scope를 한 번에 박아서 제일 빠르다. 손으로 하려면 2-A.

### 2-A. 수동 경로
1. https://api.slack.com/apps 접속 (워크스페이스에 로그인된 상태)
2. **Create New App → From scratch** → 앱 이름 입력 + 워크스페이스 선택 → Create
3. 좌측 메뉴 **OAuth & Permissions** 클릭
4. **Scopes → Bot Token Scopes**에서 `Add an OAuth Scope`로 아래 scope 추가 (§2-C)
5. 같은 페이지 상단 **Install to Workspace** (또는 OAuth Tokens 섹션의 Install) → 권한 승인
6. 설치 후 같은 페이지 **OAuth Tokens** 섹션의 **Bot User OAuth Token** 복사 → `xoxb-`로 시작
7. (메시지를 읽을 채널마다) Slack 앱에서 그 채널에 들어가 `/invite @봇이름` — **봇은 초대된 채널만 읽는다**

### 2-B. manifest 붙여넣기 경로 (권장, 빠름)
1. https://api.slack.com/apps → **Create New App → From a manifest** → 워크스페이스 선택
2. 형식을 **JSON**으로 바꾸고 아래를 붙여넣기 → Next → Create
3. 생성 후 **Install to Workspace** → 승인 → **Bot User OAuth Token(xoxb-)** 복사
4. 읽을 채널마다 `/invite @봇이름`

```json
{
  "display_information": { "name": "teammode-bot" },
  "oauth_config": {
    "scopes": {
      "bot": [
        "channels:history",
        "channels:read",
        "groups:history",
        "groups:read",
        "users:read",
        "chat:write",
        "chat:write.public",
        "reactions:write",
        "search:read.public"
      ]
    }
  },
  "settings": {
    "org_deploy_enabled": false,
    "socket_mode_enabled": false,
    "token_rotation_enabled": false
  }
}
```

### 2-C. 필요한 OAuth scopes (Bot Token Scopes)

| scope | 용도 | 필수도 |
|-------|------|--------|
| `channels:history` | 공개 채널 메시지 읽기 | 필수(읽기) |
| `channels:read` | 공개 채널 기본정보·목록 | 필수 |
| `groups:history` | 비공개 채널 메시지 읽기 | 비공개 쓸 때 |
| `groups:read` | 비공개 채널 기본정보 | 비공개 쓸 때 |
| `users:read` | 워크스페이스 멤버 조회 | 권장 |
| `chat:write` | 봇이 들어간 채널에 메시지 전송 | 쓰기 |
| `chat:write.public` | **초대 없이** 공개 채널에 전송 | 쓰기(편의) |
| `reactions:write` | 이모지 리액션 추가 | 선택 |
| `search:read.public` | 메시지 검색(공개) | 선택 |

> 메모: scope를 추가/변경할 때마다 **앱을 워크스페이스에 재설치**해야 적용된다.
> 봇 토큰 한계: DM 검색 불가, 초대 안 된 채널은 읽기 불가. (DM/검색까지 필요하면 §6의 xoxp 유저 토큰 고려)

---

## 3. 필요한 리소스 ID (채널 ID 등)

- **채널 ID(C...)**: korotovsky 서버는 토큰만 있으면 `channels_list` 툴로 **자동 조회**된다 → 온보딩 중 별도로 안 받아도 됨.
- 수동으로 필요할 때(예: 아카이브 서버의 `SLACK_CHANNEL_IDS`):
  - **UI**: Slack에서 채널명 클릭 → `채널 세부정보(View channel details)` → 맨 아래 **Channel ID**(C로 시작) 복사.
  - **API**: `conversations.list` (scope `channels:read`/`groups:read`) 로 목록·ID 일괄 조회.
- **Team ID(T...)**: 아카이브 서버에만 필요. Slack URL/About this workspace에서 확인하거나 `auth.test` API.

---

## 4. MCP 등록 형식 예시

### 4-A. Claude Code — `~/.claude.json`(전역) 또는 프로젝트 `.mcp.json`

korotovsky + 봇 토큰(읽기 전용 기본):
```json
{
  "mcpServers": {
    "slack": {
      "command": "npx",
      "args": ["-y", "slack-mcp-server@latest", "--transport", "stdio"],
      "env": {
        "SLACK_MCP_XOXB_TOKEN": "xoxb-여기에-토큰"
      }
    }
  }
}
```

메시지 전송까지 켜려면 (특정 채널만 허용):
```json
"env": {
  "SLACK_MCP_XOXB_TOKEN": "xoxb-...",
  "SLACK_MCP_ADD_MESSAGE_TOOL": "C0123456789,C0987654321"
}
```
- `SLACK_MCP_ADD_MESSAGE_TOOL`: `true`=전체 허용 / `C123,C456`=해당 채널만 / `!C123`=그 채널만 제외.
- 쓰기 툴(`conversations_add_message`)은 이 변수가 없으면 **등록조차 안 됨**(안전 기본값).

대체 서버(아카이브) 형식:
```json
{
  "mcpServers": {
    "slack": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-slack"],
      "env": {
        "SLACK_BOT_TOKEN": "xoxb-...",
        "SLACK_TEAM_ID": "T01234567",
        "SLACK_CHANNEL_IDS": "C01234567,C76543210"
      }
    }
  }
}
```

### 4-B. Codex — `~/.codex/config.toml` (프로젝트 `.codex/config.toml`도 가능)

```toml
[mcp_servers.slack]
command = "npx"
args = ["-y", "slack-mcp-server@latest", "--transport", "stdio"]
env = { SLACK_MCP_XOXB_TOKEN = "xoxb-여기에-토큰" }
```

쓰기 허용 시 `env`에 `SLACK_MCP_ADD_MESSAGE_TOOL = "C0123..."` 추가.
CLI 단축: `codex mcp add slack --env SLACK_MCP_XOXB_TOKEN=xoxb-... -- npx -y slack-mcp-server@latest --transport stdio`

> 셸 환경변수로 비밀을 관리하면 `env` 대신 `env_vars = ["SLACK_MCP_XOXB_TOKEN"]`로 포워딩 가능.

---

## 5. 온보딩 멘트 (유저에게 그대로 읽어줄 한 줄 + 탐색 팁)

> **유저용 한 줄**:
> "https://api.slack.com/apps 에서 **Create New App → From a manifest** 누르고, 제가 드릴 JSON을 붙여넣은 다음 **Install to Workspace** 해주세요. 그러고 나서 화면의 **Bot User OAuth Token**(xoxb-로 시작) 만 복사해서 저한테 주시면 됩니다. (메시지 읽을 채널엔 `/invite @봇이름` 한 번씩요)"

탐색 팁(유저가 화면에서 헤맬 때):
- 토큰이 안 보이면 → 그 페이지에서 **Ctrl-F(맥 Cmd-F) / F3** 로 **"token"** 검색 → "Bot User OAuth Token"으로 점프.
- scope 화면 못 찾으면 → 좌측 메뉴 **OAuth & Permissions**, 거기서 **"scope"** 로 검색.
- 봇이 채널을 못 읽으면 → 거의 항상 **초대(`/invite`) 누락**이거나 scope 추가 후 **재설치 안 함**.

---

## 6. 부록 — 봇 토큰으로 부족할 때 (참고)

- **DM/그룹DM 읽기·메시지 검색**이 꼭 필요하면 korotovsky의 **유저 OAuth 토큰(`xoxp-`)** 사용:
  - env: `SLACK_MCP_XOXP_TOKEN`. scope는 User Token Scopes에 `im:history,im:read,mpim:history,search:read` 등 추가.
  - 단, 이건 "유저 본인 권한"으로 동작 → 팀 봇이 아니라 개인 토큰 성격. teammode 팀 슬롯엔 봇 토큰을 기본으로 한다.
- **브라우저 세션 토큰(`xoxc-`+쿠키 `xoxd-`)** 방식도 있으나(앱 생성 없이 스텔스), 만료·약관 리스크가 있어 온보딩 표준으로는 비권장.

---

## 출처
- korotovsky 인증: https://github.com/korotovsky/slack-mcp-server/blob/master/docs/01-authentication-setup.md
- korotovsky 설정/env/툴: https://github.com/korotovsky/slack-mcp-server/blob/master/docs/03-configuration-and-usage.md
- 아카이브 공식 서버: https://github.com/modelcontextprotocol/servers-archived/tree/main/src/slack
- Slack 토큰 문서: https://docs.slack.dev/authentication/tokens/
- conversations.list: https://api.slack.com/methods/conversations.list
- Codex MCP(config.toml): https://developers.openai.com/codex/mcp
