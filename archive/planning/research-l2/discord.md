# Discord 연결 가이드 (teammode L2 온보딩)

> **에이전트용 진행 스크립트.** 이 문서 하나로 유저를 **봇 토큰 발급 → 서버 초대 → MCP 등록**까지 끝낸다.
> 한 번에 한 단계씩 안내하고, 유저가 "했어" 할 때마다 다음으로 넘어간다. 추측하지 말고 여기 적힌 클릭 경로 그대로 읽어준다.
>
> **온보딩 한 줄 안내 (유저에게 먼저 보낼 문장):**
> "Discord 봇을 만들어 팀 서버에 붙이고, 그 봇 토큰을 MCP에 등록하면 끝이에요. 5단계, 10분이면 됩니다. 한 단계씩 같이 갈게요."
>
> **탐색 팁:** 원하는 부분은 `Ctrl-F`(Mac은 `Cmd-F`), 브라우저 다음 검색은 `F3`로 점프하세요. 섹션 앵커: `[토큰]` `[초대]` `[ID]` `[MCP등록]` `[체크리스트]`

---

## 0. scope & 사전 정리 (에이전트가 먼저 판단)

- **범위 = 팀 1개 = 봇 1개 = 서버 1개.** 봇은 팀당 한 번만 만든다(**도입자 1인이 1회 수행**). 나머지 팀원은 이미 만들어진 MCP 설정만 받으면 되고, 토큰을 다시 발급할 필요 없다.
- **토큰은 비밀.** 봇 토큰은 비밀번호와 동급이다. 채팅/노션/깃에 평문으로 흘리지 말고, 팀 크리덴셜 보관소(예: acme 노션 Secret)에 넣는다.
- 이 가이드의 **대표 MCP = `SaseQ/discord-mcp`** (Java/JDA 기반, 서버 정보·채널·메시지 읽기/쓰기 풀세트, HTTP transport, Claude Code + Codex 등록 명령 공식 문서화). 가벼운 npx 대안은 맨 아래 "대안 MCP" 참조.

---

## 1. Discord 애플리케이션 + 봇 만들기  <a id="토큰"></a> `[토큰]`

> 유저에게: "먼저 봇 본체를 만들어요."

1. https://discord.com/developers/applications 접속 (Discord 계정 로그인 필요).
2. 우상단 **New Application** 클릭 → 이름 입력(예: `acme-bot`) → 약관 체크 → **Create**.
3. 왼쪽 사이드바 **Bot** 클릭.
4. (필요 시) 봇 **Username** 지정.

### 1-1. Message Content Intent 켜기 (메시지 읽으려면 필수)

> 유저에게: "봇이 메시지 **내용**을 읽으려면 이 토글이 꼭 필요해요. 안 켜면 메시지가 빈 내용으로 들어옵니다."

- 같은 **Bot** 페이지에서 아래로 스크롤 → **Privileged Gateway Intents** 섹션.
- **MESSAGE CONTENT INTENT** 토글 **ON**.
- (메시지 읽기만 할 거면 이거 하나면 충분. SERVER MEMBERS / PRESENCE INTENT는 멤버 목록·상태까지 필요할 때만.)
- 페이지 하단 **Save Changes**.

---

## 2. 봇 토큰 발급 (Reset → Copy)  `[토큰]`

> 유저에게: "이제 토큰을 뽑아요. **딱 한 번만** 보이니 바로 복사해서 안전한 곳에 붙여두세요."

1. 같은 **Bot** 페이지 위쪽 **Token** 영역.
2. **Reset Token** 클릭 → (2FA 켜져 있으면 확인) → **Yes, do it!**.
3. 나타난 토큰을 **Copy**. **이 화면을 닫으면 다시 못 본다** → 즉시 크리덴셜 보관소에 저장.

> 토큰을 잃어버리면? 다시 **Reset Token** 누르면 새 토큰이 나온다(기존 토큰은 즉시 무효화 → MCP 설정도 갱신해야 함).

클릭 경로 요약: `discord.com/developers/applications → New Application → Bot → (Message Content Intent ON) → Reset Token → Copy`

---

## 3. 봇을 팀 서버에 초대 (OAuth2 URL Generator)  <a id="초대"></a> `[초대]`

> 유저에게: "봇은 서버에 초대돼야 그 서버를 읽고 쓸 수 있어요. 초대 링크를 만들게요."

1. 왼쪽 사이드바 **OAuth2** → **URL Generator**.
2. **SCOPES** 에서 **`bot`** 체크.
   - (슬래시 명령까지 쓸 거면 `applications.commands`도 같이 체크 — 메시지 읽기/쓰기에는 `bot` 만으로 충분.)
3. 아래에 **BOT PERMISSIONS** 가 나타남. 다음을 체크:
   - **읽기:** `View Channels`, `Read Message History`
   - **쓰기:** `Send Messages` (스레드도 쓸 거면 `Send Messages in Threads`)
   - (선택, 풍부한 상호작용용: `Add Reactions`, `Attach Files`)
4. **INTEGRATION TYPE: `Guild Install`** 선택 (서버에 설치하는 모드).
5. 맨 아래 **GENERATED URL** 을 **Copy** → 새 탭에서 열기.
6. 드롭다운에서 **팀 서버 선택** → **Authorize** → 캡차 통과. 봇이 서버 멤버 목록에 뜨면 성공.

> **권한 비트 참고(자동 계산됨):** `View Channels`=0x400(1024), `Send Messages`=0x800(2048), `Read Message History`=0x10000(65536). 세 개만 합치면 `permissions=67584`. 체크박스가 URL의 `permissions=` 값을 자동으로 만들어주므로 직접 계산할 필요는 없다.
>
> **DM 전용이면** 권한 0개여도 동작하지만(봇과 DM하려면 서버를 공유만 하면 됨), 나중에 채널을 읽으려면 위 권한이 필요하니 지금 켜두는 걸 권장.

---

## 4. 필요한 리소스 ID 얻기 (개발자 모드 → Copy ID)  <a id="ID"></a> `[ID]`

> 유저에게: "특정 채널/서버를 지목하려면 숫자 ID가 필요해요. 개발자 모드를 켜면 우클릭으로 복사됩니다."

1. Discord 앱에서 좌하단 **톱니바퀴(User Settings)** 클릭.
2. **Advanced(고급)** → **Developer Mode** 토글 **ON** (파란색).
3. 이제 우클릭 메뉴에 **Copy ID** 가 생김:
   - **길드(서버) ID:** 서버 아이콘/이름 **우클릭 → Copy Server ID**
   - **채널 ID:** 좌측 채널 목록의 채널 **우클릭 → Copy Channel ID**
   - (참고) 유저 ID: 유저 우클릭 → Copy User ID / 메시지 ID: 메시지 우클릭 → Copy Message ID

> ID는 전부 **snowflake**(긴 숫자 문자열, 예: `123456789012345678`).
> `DISCORD_GUILD_ID` 를 MCP env에 넣어두면, 매 호출마다 `guildId` 를 안 적어도 되는 **기본 서버**가 된다(선택이지만 팀 1서버 운영이면 강력 추천).

---

## 5. MCP 등록  <a id="MCP등록"></a> `[MCP등록]`

대표 MCP: **`SaseQ/discord-mcp`** (https://github.com/SaseQ/discord-mcp). Docker로 한 컨테이너를 띄우고 HTTP로 붙는 방식이 권장(여러 클라이언트가 한 봇을 공유).

### 5-1. 서버 컨테이너 띄우기 (도입자 1회)

```bash
export DISCORD_TOKEN="여기에_봇_토큰"
export DISCORD_GUILD_ID="여기에_길드_ID"   # 선택
export SPRING_PROFILES_ACTIVE=http

docker run -d -i \
  --name discord-mcp \
  --restart unless-stopped \
  -p 8085:8085 \
  -e SPRING_PROFILES_ACTIVE \
  -e DISCORD_TOKEN \
  -e DISCORD_GUILD_ID \
  saseq/discord-mcp:latest
```

- 기본 MCP 엔드포인트: `http://localhost:8085/mcp`
- 헬스체크: `curl -fsS http://localhost:8085/actuator/health`

### 5-2. 클라이언트에 등록

**Claude Code (권장, HTTP):**
```bash
claude mcp add discord-mcp --transport http http://localhost:8085/mcp
```

**Codex CLI:**
```bash
codex mcp add discord-mcp --url http://localhost:8085/mcp
codex mcp list
```

**config.json 직접 작성(HTTP):**
```json
{
  "mcpServers": {
    "discord-mcp": {
      "url": "http://localhost:8085/mcp"
    }
  }
}
```

### 5-3. (대안) Docker 컨테이너 직접 띄우지 않고 stdio로 — 클라이언트가 매번 컨테이너 실행

> 별도 상주 서버 없이 가볍게 쓰고 싶을 때. 토큰이 설정 파일에 박힌다는 점 주의.

**Claude Code (stdio):**
```bash
claude mcp add discord-mcp -- docker run --rm -i \
  -e DISCORD_TOKEN=<봇_토큰> \
  -e DISCORD_GUILD_ID=<길드_ID> \
  saseq/discord-mcp:latest
```

**config.json (stdio):**
```json
{
  "mcpServers": {
    "discord-mcp": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "DISCORD_TOKEN=<봇_토큰>",
        "-e", "DISCORD_GUILD_ID=<길드_ID>",
        "saseq/discord-mcp:latest"
      ]
    }
  }
}
```

### 5-4. 등록 후 확인

- 클라이언트(Claude Code) 재시작 → 도구 목록에 `discord-mcp` 의 툴들이 보이는지 확인.
- 핵심 툴: `list_channels`(채널 목록), `read_messages`(채널 메시지 읽기), `send_message`(메시지 보내기), `get_server_info`(서버 정보). (그 외 채널/역할/모더레이션/이벤트 등 풀세트 제공.)
- 빠른 검증: 에이전트에게 "팀 서버 채널 목록 보여줘" → `list_channels` 호출되면 연결 성공.

---

## 체크리스트 (에이전트가 한 줄씩 확인)  <a id="체크리스트"></a> `[체크리스트]`

- [ ] Discord Developer Portal에서 New Application 생성
- [ ] **Bot 페이지에서 Message Content Intent ON** (+ Save)
- [ ] **Reset Token → Copy** 후 토큰을 크리덴셜 보관소에 저장
- [ ] OAuth2 URL Generator: `bot` scope + (View Channels / Read Message History / Send Messages) + **Guild Install** → 링크 열어 팀 서버에 **Authorize**
- [ ] Discord 앱 Developer Mode ON → **Copy Server ID** (그리고 필요 채널 **Copy Channel ID**)
- [ ] `DISCORD_TOKEN`(+선택 `DISCORD_GUILD_ID`)으로 컨테이너 기동, 헬스체크 OK
- [ ] `claude mcp add discord-mcp ...` 로 등록, 재시작 후 `list_channels` 동작 확인

---

## 부록: 대안 MCP

상황에 따라 다음도 현역(2026-06 기준). 팀 표준은 위 `SaseQ/discord-mcp`.

| MCP | 형태 | env 변수 | 특징 |
|-----|------|----------|------|
| **`anthropics/claude-plugins-official` › discord** | Claude Code 플러그인(Bun) | `DISCORD_BOT_TOKEN` (`~/.claude/channels/discord/.env`) | **공식**. DM/채널 채팅 봇(페어링 흐름). `reply`/`react`/`fetch_messages` 등 메시징 중심. 서버 관리 툴은 없음. `claude --channels plugin:discord@claude-plugins-official` 로 기동. |
| **`mcp-discord` (npm)** | `npx -y mcp-discord` (stdio) | `DISCORD_TOKEN` | 가벼운 노드 기반. Claude Code 호환. 서버 관리 일부. |
| **`discord-mcp-server` (npm)** | `npx -y discord-mcp-server` (stdio) | `DISCORD_BOT_TOKEN` | 노드 기반 경량 대안. |
| **`HardHeadHackerHead/discord-mcp`** | 셋업 위저드 포함 | (위저드가 안내) | 어드민 툴 134개/20카테고리. 풀 관리형. |

> **언제 공식 플러그인?** 팀이 원하는 게 "Discord에서 Claude와 대화"(채팅 인터페이스)면 공식 플러그인이 맞다. "팀 서버의 채널/메시지를 읽고 자동화"가 목적이면 `SaseQ/discord-mcp`(이 가이드 본문) 사용.

---

### 출처 (모두 현행, 2026-06 확인)
- 공식 Discord 플러그인 README — https://github.com/anthropics/claude-plugins-official/blob/main/external_plugins/discord/README.md
- SaseQ/discord-mcp README (설치·env·Claude Code/Codex 등록·툴 목록) — https://github.com/SaseQ/discord-mcp
- Discord Developer Portal — https://discord.com/developers/applications
- Discord OAuth2 문서(Guild Install / bot scope / permissions) — https://docs.discord.com/developers/topics/oauth2
- Discord Permissions 문서(비트 값) — https://docs.discord.com/developers/topics/permissions
- 개발자 모드 / Copy ID 안내 — https://support.discord.com/hc/en-us/articles/206346498
