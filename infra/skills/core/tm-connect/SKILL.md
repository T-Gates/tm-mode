---
name: tm-connect
description: Use to connect a service slot (issues / chat / docs / calendar) to a tm-mode team — guiding token issuance, storing it in the local credentials vault, and recording the resource in team config. Triggers on "서비스 연결", "이슈 트래커 연결", "채팅 연결", "문서 연결", "캘린더 연결", "팀모드 서비스 붙여줘", "tm-mode connect", "connect service", or after tm-onboard offers L2.
---

# tm-connect — 서비스 슬롯 연결 (L2 등록기)

tm-mode 의 **역할 슬롯**(issues / chat / docs / calendar)에 팀이 고른 **공식 벤더 MCP를 꽂아주는 등록기**. tm-onboard 가 첫 가치(L1) 직후 "연결할래요?" 하고 제안하면, **실행은 이 스킬**이 한다.

```
tm-onboard (제안+트리거)  →  tm-connect (등록기 실행)
   "연결할래요?"              슬롯 → provider → MCP 마련 → 토큰 안내 → 금고 저장 → config 기록 → MCP alias 등록
```

> **A안 (2026-06-25 확정).** tm-mode 는 **연결(등록)만** 한다. 이슈 생성·일정 추가 같은 **동작은 AI가 등록된 벤더 MCP 도구를 직접 호출**한다 — 이 스킬이 동작을 래핑하지 않는다. 핸들러 생성·`role_server` 프록시·역할 추상화 동사·"재사용>흡수>수제" 우선순위 판정은 모두 폐기됐다(`docs/archive/2026-06-25-L2-redesign.md`). 권위 스펙은 `docs/spec/skills.md §5.4.1`·`internals.md §2.8`.

## 원칙

- **될 일은 데이터·엔진이, 판단·동의 게이트는 이 스킬이.** 발급 링크·단계·연결방식은 **`providers/<provider>.json` 에서 읽어** 안내한다 — 스킬 본문에 링크·단계를 하드코딩하지 않는다.
- **역할 어휘만.** 이 스킬은 issues / chat / docs / calendar 라는 **역할**로만 말한다. 실제 어느 서비스인지는 `team.config.json` 의 `services.<역할>.provider` 와 `providers/<provider>.json` 이 답한다 — 런타임에 데이터를 보고 옮긴다.
- **각자 입력(v0.2).** 각 멤버가 **자기 토큰을 직접 입력**한다. 팀 토큰 자동공유는 v0.2 에 없다 — 팀 scope 슬롯도 각 멤버가 "각자 1회" 입력한다.
- **정직한 경계(사람 몫).** 토큰 발급·동의(OAuth "허용", 개인키 "Create+붙여넣기", 봇 설치)와 **공식 제공처 식별**은 **사람이 권한을 부여·확정**하는 보안 경계라 무인 불가. 스킬은 동의 게이트 직전까지 데려가고 클릭·붙여넣기·선택만 사람이.
- **연결만, 동작은 AI 직접.** 등록이 끝나면 동작은 AI가 등록된 벤더 MCP 도구를 직접 호출한다. 이 스킬은 동작 명령(`tm-issues create` 같은 CLI)을 만들지 않는다 — 폐기한 추상화(B안)의 부활이다.

---

## 1. 슬롯 선택 — 어느 역할을 연결하나

사용자가 붙이려는 역할(issues / chat / docs / calendar)을 정한다. 이후 모든 단계는 그 슬롯 하나에 대해서만 진행한다.

---

## 2. provider 결정 — config 가 진실, 첫 등록자만 고른다

그 슬롯의 provider 를 `team.config.json` 의 `services.<역할>.provider` 에서 읽는다 (**추측 금지 — 데이터가 진실**).

### 2-A. provider 가 이미 있으면 (후속 멤버)

`services.<역할>.provider` 가 채워져 있으면 **재선택하지 않는다.** 도입자가 골라 config 에 커밋·push 해 둔 provider 를 그대로 따른다. 바로 §3 의 MCP 마련(이미 본 레포에 와 있을 것)·§4 토큰 입력으로 진행한다.

> "이미 팀이 이 슬롯에 `<provider>` 를 선언해 뒀습니다. 재선택하지 않고 그 provider 로 진행합니다.
> provider 를 바꾸려면 `team.config.json` 의 `services.<역할>` 을 직접 수정 후 PR 로 팀 합의를 거쳐야 합니다."

### 2-B. 슬롯이 비어 있으면 (첫 등록자)

어떤 provider 를 쓸지 사람에게 묻는다. 필요하면 후보를 검색해 N개 제시한다.

1. **사람이 고른다** — **공식 제공처 식별이 보안 게이트**다. 무인 추측으로 진행하지 않는다(공식 아닌 가짜 MCP/엔드포인트를 잡으면 토큰 유출).
2. `providers/<provider>.json` 팩이 실재하는지 확인한다. 없으면 미지원 — 추측해서 진행하지 않는다.
3. **공식 벤더 MCP를 마련한다** (§3).
4. 고른 provider·인스턴스 값을 `team.config.json` 의 `services.<역할>` 에 기록하고 **GitHub 에 push** 한다 = 팀 공유 선언 (§5).

provider 가 정해지면 그 팩에서 **다음 필드를 데이터로 읽어** 안내를 구성한다:

| 팩 필드 | 용도 (스킬이 읽어서 안내) |
|---|---|
| `token_guide.url` | 토큰 발급 페이지로 가는 **딥링크** — 그대로 사람에게 제시 |
| `token_guide.steps` | 발급까지의 **단계 목록** — 순서대로 안내 |
| `auth` | 연결방식(`api_key` / `oauth` / `bot_token`) — 멘트를 이 값에 맞춰 고른다(§4) |
| `default_scope` | `team` / `personal` — credentials namespace·기본값(§4) |
| `resource_fields` | 연결 후 config 에 채울 **인스턴스 필드** 이름 목록(§5) |
| `mcp.register_hint` | MCP alias 등록 안내에 참고(§3·§6) |

---

## 3. 공식 벤더 MCP 마련 — 공식 우선, 없으면 자작

첫 등록자 경로에서 그 provider 의 **공식 벤더 MCP**를 본 레포에 둔다. 실제 alias 등록 동작은 §6 의 install-mcp(install.py 재실행)가 하고, 이 단계는 그 등록이 가리킬 **MCP 코드·실행 메타를 마련**하는 일이다.

```
공식 MCP 레포 있음 → git 으로 가져와 본 레포 infra/mcp/<provider>/ 에 둠 + 커밋 (팀 공유 보관소)
공식 없음          → AI 자작 (사용자에게 안 미룬다 — "자작 X" 원칙은 폐기)
```

**공식 우선.** 자작은 공식 MCP 레포가 없을 때만의 대안이다. 다음 멤버는 본 레포에 든 것을 재사용한다(재마련 X).

### 자작 (공식 없을 때만)

1. provider 공식 API 스펙(REST/GraphQL 문서)을 출처로 **그 벤더 전용 MCP** 를 Python MCP SDK 로 작성한다.
2. **그 슬롯 역할에 필요한 도구만** 노출한다 (예: calendar 면 list_events / create_event 수준 — 슬롯 전부를 덮는 만능 X).
3. `infra/mcp/<provider>/` 에 서버 코드 + 실행 command(기동 메타)를 두고 본 레포에 커밋한다. 공식 MCP를 가져왔을 때와 같은 위치·구조다.
4. 토큰은 공식 MCP와 동일 경로(§4 로컬 금고 0600)다. 자작이라고 별도 토큰 경로를 만들지 않는다.
5. 첫 자작 직후 **적대검수(서브에이전트)** 로 노출 도구의 실동작을 검증한다.

> ⚠️ **자작 MCP는 역할 추상화가 아니다.** provider API 를 감싼 도구를 *그대로* 노출할 뿐, 슬롯 통일 동사(역할별 추상 동사)를 만드는 게 **아니다**. 그건 폐기한 `role_server`/역할 추상화(B안)의 부활이다. tm-mode 는 자작 MCP 에 대해서도 **연결(등록)만** 하고, 동작은 AI가 직접 호출한다(A안).

상세 7단계·원칙은 `docs/archive/2026-06-25-L2-redesign.md` "MCP 마련" 과 `internals.md §2.8` 참조.

---

## 4. 토큰 안내 → 각자 입력 → credentials 금고 저장

### 4-A. 토큰 안내 (사람 몫 — 동의 게이트까지)

팩의 `token_guide.url` 로 데려가고 `token_guide.steps` 를 순서대로 옮긴다. 막연한 "키 찾아와" 금지 — **정확한 링크·버튼**으로.

- 사람이 발급 페이지에서 토큰을 만든다(이 단계는 사람이 권한을 부여하는 보안 경계 — 무인 불가).
- "당신 몫은 토큰 N개뿐"으로 기대치를 고정한다(토큰 병목 완화).

팩의 `auth` 값을 읽어 안내를 고른다(스킬이 서비스를 모름 — 값으로만 분기):

| `auth` | 안내 |
|---|---|
| `api_key` | 발급 페이지에서 **개인/통합 키를 Create → 복사 → 붙여넣기**. attribution 이 본인으로 남도록 각자 발급. |
| `bot_token` | 앱/봇 토큰을 발급해 워크스페이스에 설치 후 **봇 토큰 복사 → 붙여넣기**. |
| `oauth` | **localhost OAuth(PKCE)** — 동의 화면에서 사람이 "허용". 콜백으로 토큰 수령(붙여넣기 없을 수 있음). |

OAuth 크리덴셜 키 계약:

| auth 타입 | 저장 key |
|-----------|---------|
| `api_key` / `bot_token` | `<역할>` |
| `oauth` | `<역할>_access_token` + `<역할>_refresh_token` |

### 4-B. 각자 입력 → credentials 금고에 저장

**팀 자동공유 없음(v0.2).** 각 멤버가 자기 토큰을 직접 입력하고, **로컬 금고**(`infra/credentials.py`)에 저장한다.

- 저장 위치: 멤버 로컬 `$XDG_DATA_HOME/teammode/credentials/default.json`(단일 금고, 파일 권한 0600). git 추적 안 됨.
- 팩의 `default_scope` 로 네임스페이스를 고른다 — `team` 이든 `personal` 이든 **v0.2 는 각자 1회 입력**이다(팀 scope 라고 도입자 1회로 끝나지 않는다 — 자동공유 미구현).
- 저장은 엔진/모듈이 한다(스킬이 평문 토큰을 stdout·로그·세션로그에 절대 출력하지 않는다):
  ```bash
  python -c "import sys; sys.path.insert(0,'infra'); import credentials; \
    credentials.store('<team>', '<scope>', '<역할>', input())"
  ```
  토큰은 표준입력으로만 흘리고, 명령행 인자·로그·세션로그 어디에도 평문으로 남기지 않는다.

> ⚠️ **평문 금고 경고(필수 안내).** v0.2 금고는 **평문 JSON** 이다. Syncthing·Dropbox·iCloud 등 **동기화 폴더에 절대 두지 말 것** — 평문 토큰이 동기화되어 다른 기기·서비스로 새면 곧 유출이다. 0600 권한 + git 미추적 + 동기화 폴더 금지가 v0.2 의 방어선이다(OS 키체인은 후속).

---

## 5. config 슬롯 기록 — 인스턴스 값(resource_fields)

토큰을 받으면 그 서비스에서 **실제 어느 리소스**(문서 DB·채팅 채널·캘린더 등)를 쓸지 정한다. 팩의 `resource_fields` 가 config 에 채울 **인스턴스 필드 이름**을 선언한다 — **이 목록이 인스턴스 값 필요 여부를 결정한다**. 빈 리스트면 인스턴스 값 불필요(예: 채팅 슬롯은 채널을 다 써서 dbid 류가 없다), 비어 있지 않으면 그 슬롯만 해당 값(예: 문서/캘린더 슬롯의 dbid)을 사람에게 묻는다.

- `team.config.json` 의 `services.<역할>` 슬롯에 `{ provider, scope, <resource_fields 각 필드 = 고른 값> }` 를 기록한다.
- **인스턴스 값(리소스 ID·채널·캘린더 등)은 config 소관**이다 — 토큰(비밀)은 금고에, 인스턴스 값(비밀 아님)은 config 에. 토큰을 config 에 적지 않는다(토큰키 추적 거부 린트가 막는다).
- 첫 등록자 경로에선 provider·인스턴스 값을 **도입자가 config 에 커밋·push** 한다(팀 공유 선언, §2-B). 팀원은 그 선언을 읽기만 한다(단, 토큰은 v0.2 에서 각자 입력 — §4).

---

## 6. MCP alias 등록 (install-mcp 재실행) → 첫 가치

### 6-A. MCP alias 등록 (재배선)

연결을 마치면 adapter 가 그 provider 의 **벤더 MCP alias 를 멤버 에이전트(claude/codex) 설정에 등록**하도록 **install-mcp 를 재실행**한다. install-mcp 는 §3 에서 마련한 공식/자작 MCP 산출물을 **동일하게** 다뤄 정규 서버명 alias 로 등록한다(`internals.md §2.8`). 팩의 `mcp.register_hint` 를 참고로 옮긴다.

```bash
python infra/install.py --root . --yes        # 재배선(adapter 에 벤더 MCP alias 등록 + sync)
```

재배선 후 확인:
- 그 provider 의 벤더 MCP alias 가 에이전트 설정에 teammode 관리 항목으로 등록됐는지 확인.
- 해당 벤더 MCP 도구가 에이전트에 노출됐는지 확인.

### 6-B. 첫 가치 (A안 — AI가 벤더 MCP 도구 직접 호출)

동작은 tm-connect 가 래핑하지 않는다. 연결이 끝나면 **AI가 등록된 벤더 MCP 도구를 직접 호출**해 첫 가치를 보여준다.

- 예: issues 슬롯이면 그 provider MCP 의 이슈 생성 도구를, calendar 슬롯이면 일정 추가 도구를 AI가 직접 호출한다.
- 도구 호출이 보이면 그 자리에서 **첫 도그푸딩**(실제 서비스에 이슈/일정이 생김)이 가능하다.
- 동작 명령(`tm-issues create` 같은 CLI)을 새로 만들지 않는다 — 그것은 폐기한 추상화(B안)의 부활이다.

---

## 안 하는 것 / 경계

- 토큰 발급·동의 클릭을 대신하지 않는다 — 보안 경계(사람 몫).
- 공식 제공처 식별을 무인 추측으로 확정하지 않는다 — 사람이 고른다(보안 게이트).
- 평문 토큰을 stdout·로그·세션로그·config 어디에도 출력하지 않는다.
- 동작(이슈 만들기·일정 추가 등)을 직접 실행하거나 래핑하지 않는다 — 동작은 AI가 등록된 벤더 MCP 도구를 직접 호출한다.
- 동작 CLI/래퍼(`tm-issues create` 류)를 만들지 않는다 — B안(역할 추상화)의 부활이라 폐기.
- 후속 멤버 경로에서 provider 를 재선택하지 않는다 — config 선언을 읽는다(교체는 직접 수정 + 팀 PR 합의).
- 검증·자가수리는 `doctor`(로드맵) 몫. 연결 직후 유효 ping 정도까지만.
- 첫 등록자 경로의 **provider 선언(config)·가져온 공식 MCP의 커밋/푸시**는 등록기 흐름의 정상 단계다(팀 공유 선언). 사용자 코드 레포에 대한 임의 커밋·PR 은 하지 않는다.

## Common Mistakes

| 실수 | 올바른 방법 |
|------|------------|
| 발급 링크·단계를 스킬 본문에 하드코딩 | `providers/<provider>.json` 의 `token_guide` 를 읽어 안내 |
| 후속 멤버인데 provider 재선택 | config 의 `services.<역할>.provider` 를 읽고 그대로 진행 |
| 공식 제공처를 추측해서 무인 진행 | 사람이 고른다 — 공식 식별은 보안 게이트 |
| 공식 MCP 있는데 자작부터 | 공식 우선 — 자작은 공식 레포 없을 때만의 대안 |
| 자작 MCP에 역할 통일 동사 노출 | 벤더 API 도구를 그대로 노출 — 역할 추상화는 폐기(B안) |
| 동작 CLI(`tm-issues create`)를 만듦 | 동작은 AI가 벤더 MCP 도구 직접 호출 — 래퍼 금지(A안) |
| 팀 scope 면 도입자 1회로 끝난다고 안내 | v0.2 는 각자 입력 — 팀 scope 도 각 멤버가 자기 토큰 입력 |
| 토큰을 config·세션로그에 기록 | 토큰은 금고(0600)에, 인스턴스 값만 config 에 |
| 금고를 동기화 폴더에 둬도 된다고 안내 | 평문이라 동기화 폴더 금지 — 0600 + git 미추적이 방어선 |
| 빈 슬롯을 에러로 취급 | 빈 슬롯 = 1급 시민. 첫 등록자가 provider 골라 채우면 재배선으로 활성 |
| 도구명·서비스명 직표기 | 역할 어휘(issues/chat/docs/calendar)로만 말한다 |

---
> 발견: 이 스킬은 AGENTS.md / CLAUDE.md 의 포인터로 찾는다(tm-onboard 와 동일 방식). 동작·데이터 근거는 `providers/<name>.json`(연결 데이터)·`infra/credentials.py`(금고)·`docs/spec/skills.md §5.4.1`(등록기 흐름)·`internals.md §2.8`(install-mcp 의무)·`docs/archive/2026-06-25-L2-redesign.md`(A안 기준) 확인.
</content>
</invoke>
