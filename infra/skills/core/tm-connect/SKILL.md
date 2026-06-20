---
name: tm-connect
description: Use to connect a service slot (issues / chat / docs / calendar) to a teammode team — guiding token issuance, storing it in the local credentials vault, and recording the resource in team config. Triggers on "서비스 연결", "이슈 트래커 연결", "채팅 연결", "문서 연결", "캘린더 연결", "팀모드 서비스 붙여줘", "teammode connect", "connect service", or after tm-onboard offers L2.
---

# tm-connect — 서비스 슬롯 연결 (L2)

teammode 의 **역할 슬롯**(issues / chat / docs / calendar)에 서비스를 붙이는 스킬. tm-onboard 가 첫 가치(L1) 직후 "연결할래요?" 하고 제안하면, **실행은 이 스킬**이 한다.

```
tm-onboard (제안+트리거)  →  tm-connect (실행)
   "연결할래요?"              판정 → 핸들러 → 토큰 안내 → 금고 저장 → config 기록 → 재배선
```

## 원칙

- **될 일은 데이터·엔진이, 판단·동의 게이트는 이 스킬이.** 발급 링크·단계·연결방식은 **`providers/<역할의 provider>.json` 에서 읽어** 안내한다 — 스킬 본문에 링크·단계를 하드코딩하지 않는다.
- **역할 어휘만.** 이 스킬은 issues / chat / docs / calendar 라는 **역할**로만 말한다. 실제 어느 서비스인지는 `team.config.json` 의 `services.<역할>.provider` 와 `providers/<provider>.json` 이 답한다 — 런타임에 데이터를 보고 옮긴다.
- **각자 입력(v0.1).** 각 멤버가 **자기 토큰을 직접 입력**한다. 팀 토큰 자동공유는 v0.1 에 없다 — 팀 scope 슬롯도 각 멤버가 "각자 1회" 입력한다.
- **정직한 경계(사람 몫).** 토큰 발급·동의(OAuth "허용", 개인키 "Create+붙여넣기", 공유 토글)는 **사람이 권한을 부여**하는 보안 경계라 무인 불가. 스킬은 동의 게이트 직전까지 데려가고 클릭·붙여넣기만 사람이.
- **핸들러는 팀 공유 코드.** 핸들러 생성 시 반드시 사람 확인 게이트를 거쳐 팀 레포에 커밋한다 — 에이전트가 임의로 생성·교체하지 않는다.

---

## 0. 어느 역할을 연결하나 — provider 데이터를 먼저 읽는다

사용자가 붙이려는 역할(issues / chat / docs / calendar)을 정한다. 그 역할의 provider 와 연결 데이터는 두 곳에서 읽는다 (**추측 금지 — 데이터가 진실**):

1. **이미 config 에 선언된 provider**: `team.config.json` 의 `services.<역할>.provider` 를 읽는다.
2. **아직 빈 슬롯이면**: 어떤 provider 를 쓸지 사람에게 묻고, `providers/` 에 그 이름의 팩이 실재하는지 확인한다(`providers/<provider>.json`). 없으면 미지원 — 추측해서 진행하지 않는다.

provider 가 정해지면 그 팩에서 **다음 필드를 데이터로 읽어** 안내를 구성한다:

| 팩 필드 | 용도 (스킬이 읽어서 안내) |
|---|---|
| `token_guide.url` | 토큰 발급 페이지로 가는 **딥링크** — 그대로 사람에게 제시 |
| `token_guide.steps` | 발급까지의 **단계 목록** — 순서대로 안내 |
| `auth` | 연결방식(`api_key` / `oauth` / `bot_token`) — 멘트를 이 값에 맞춰 고른다(§2) |
| `default_scope` | `team` / `personal` — 누가 입력하나의 기본값(§3) |
| `resource_fields` | 연결 후 config 에 채울 **인스턴스 필드** 이름 목록(§4) |
| `mcp.register_hint` | 재배선 안내에 참고 |

---

## 1. 우선순위 판정 — 재사용 > 흡수 > 수제

역할이 정해지면 아래 순서대로 판정한다. 상위 조건을 만족하면 하위 경로는 실행하지 않는다.

### ① 재사용 (팀 표준 핸들러 존재)

`handlers/<역할>.py` 가 팀 레포 루트에 **이미 있으면** 재사용 경로를 따른다.

> "이미 팀 표준 핸들러가 있습니다. 새로 생성하지 않고 이 핸들러를 재사용합니다.
> 핸들러를 교체하려면 `handlers/<역할>.py` 를 직접 수정 후 PR 로 팀 합의를 거쳐야 합니다."

재사용 확인 절차:

1. `infra/install_lib.handlers_are_valid(Path("handlers"))` 로 기존 핸들러 유효성 확인
2. 유효하면 §3(토큰 안내) → §4(config 기록) → §5(재배선) 순으로 진행

### ② 흡수 (팀이 이미 쓰는 서비스 연결 탐지)

재사용 경로가 아닐 때, 팀이 해당 역할의 서비스를 **이미 다른 경로로 연결**하고 있는지 확인한다:

```bash
python infra/install.py --check-mcp <provider> --root . --agent claude
python infra/install.py --check-mcp <provider> --root . --agent codex
```

둘 중 하나라도 `{"connected": true, ...}` 를 반환하면 흡수 경로를 따른다.

흡수 경로:
1. 기존 연결의 엔드포인트 정보를 파악한다
2. `handlers/<역할>.py` 를 생성한다 (§2 핸들러 생성 절차 따름)
3. 기존 토큰 금고 키를 재사용한다 (새 토큰 발급 불필요할 수 있음 — 사람에게 확인)

### ③ 수제 (신규 서비스 연결)

위 두 경로 모두 해당 없으면 수제 경로를 따른다.

1. 역할에 연결할 서비스 API 를 파악한다:
   - provider 팩(`providers/<provider>.json`)의 `token_guide`, `auth`, `resource_fields` 를 읽는다
   - API 문서 URL 이 팩에 있으면 참고한다
   - 사람이 API 엔드포인트·인증 방식 정보를 제공하면 그것을 우선한다
2. `handlers/<역할>.py` 를 생성한다 (§2 핸들러 생성 절차 따름)

---

## 2. 핸들러 생성 / 검증

재사용 이외(흡수·수제) 경우, 핸들러 코드를 생성한다.

### 2-A. 생성 규칙

생성하는 `handlers/<역할>.py` 는 다음 규칙을 반드시 따른다:

1. **역할별 필수 함수** 전부 정의 (계약: `docs/spec/handlers.md §2`)
2. **토큰 리터럴 금지** — 토큰·API 키를 코드에 직접 embed 하지 않는다. 반드시 `credentials.load()` 경유:
   ```python
   import sys
   sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'infra'))
   import credentials
   TEAM = "<team-name>"
   def issues_create(title, body="", ...):
       token = credentials.load(TEAM, "personal", "issues")
       # ... 서비스 API 호출 ...
   ```
3. **`handlers_are_valid()` 통과** — 문법 오류·필수 함수 누락·토큰 리터럴이 없어야 한다
4. **atomic write** — `infra/install_lib._atomic_write_text()` 를 사용해 partial 파일 방지

### 2-B. 사람 확인 게이트 (필수)

핸들러 검증 통과 후, **반드시 아래 게이트를 거쳐야 한다**:

1. "팀 레포에 다음 핸들러를 커밋하겠습니다" 안내 + 생성된 코드 **전체** 출력
2. 사람의 명시적 확인(수락) 대기
3. 확인 후: **커밋 직전에 conformance lint 실행** (토큰 누출 사전 차단):
   ```bash
   python conformance/check.py lint --root .
   ```
   lint 가 통과(0건 FAIL)해야만 커밋한다. FAIL 있으면 핸들러를 수정하고 재검증.
4. lint 통과 확인 후: `git commit handlers/<역할>.py`

> ⚠️ 사람이 확인하기 전에는 커밋하지 않는다. 검증 실패 또는 lint FAIL 시에도 커밋하지 않는다.
> ⚠️ lint 는 `handlers_are_valid()` 가 잡지 못하는 분할(`'xox'+'b-...'`) · base64 우회 토큰도 탐지한다.

### 2-C. hard 강제

역할당 핸들러는 1개만 허용한다. 이미 `handlers/<역할>.py` 가 있으면 재사용 경로(§1-①)가 강제되고, 이 절차는 실행되지 않는다.

---

## 3. 토큰 안내 (사람 몫 — 동의 게이트까지)

팩의 `token_guide.url` 로 데려가고 `token_guide.steps` 를 순서대로 옮긴다. 막연한 "키 찾아와" 금지 — **정확한 링크·버튼**으로.

- 사람이 발급 페이지에서 토큰을 만든다(이 단계는 사람이 권한을 부여하는 보안 경계 — 무인 불가).
- "당신 몫은 토큰 N개뿐"으로 기대치를 고정한다(토큰 병목 완화).

팩의 `auth` 값을 읽어 안내를 고른다(스킬이 서비스를 모름 — 값으로만 분기):

| `auth` | 안내 |
|---|---|
| `api_key` | 발급 페이지에서 **개인/통합 키를 Create → 복사 → 붙여넣기**. attribution 이 본인으로 남도록 각자 발급. |
| `bot_token` | 앱/봇 토큰을 발급해 워크스페이스에 설치 후 **봇 토큰 복사 → 붙여넣기**. |
| `oauth` | **localhost OAuth(PKCE)** — 동의 화면에서 사람이 "허용". 콜백으로 토큰 수령. |

OAuth 크리덴셜 키 계약:

| auth 타입 | 저장 key |
|-----------|---------|
| `api_key` / `bot_token` | `<역할>` |
| `oauth` | `<역할>_access_token` + `<역할>_refresh_token` |

---

## 4. 각자 입력 → credentials 금고에 저장

**팀 자동공유 없음(v0.1).** 각 멤버가 자기 토큰을 직접 입력하고, **로컬 금고**(`infra/credentials.py`)에 저장한다.

- 저장 위치: 멤버 로컬 `$XDG_DATA_HOME/teammode/credentials/default.json`(단일 금고, 파일 권한 0600). git 추적 안 됨.
- 팩의 `default_scope` 로 네임스페이스를 고른다 — `team` 이든 `personal` 이든 **v0.1 은 각자 1회 입력**이다(팀 scope 라고 도입자 1회로 끝나지 않는다 — 자동공유 미구현).
- 저장은 엔진/모듈이 한다(스킬이 평문 토큰을 stdout·로그·세션로그에 절대 출력하지 않는다):
  ```bash
  python -c "import sys; sys.path.insert(0,'infra'); import credentials; \
    credentials.store('<team>', '<scope>', '<역할>', input())"
  ```
  토큰은 표준입력으로만 흘리고, 명령행 인자·로그·세션로그 어디에도 평문으로 남기지 않는다.

> ⚠️ **평문 금고 경고(필수 안내).** v0.1 금고는 **평문 JSON** 이다. Syncthing·Dropbox·iCloud 등 **동기화 폴더에 절대 두지 말 것** — 평문 토큰이 동기화되어 다른 기기·서비스로 새면 곧 유출이다. 0600 권한 + git 미추적 + 동기화 폴더 금지가 v0.1 의 방어선이다(OS 키체인은 v0.2).

---

## 5. config 슬롯 기록 — 인스턴스 값(resource_fields)

토큰을 받으면 그 서비스에서 **실제 어느 리소스**(문서 DB·채팅 채널·캘린더 등)를 쓸지 정한다. 팩의 `resource_fields` 가 config 에 채울 **인스턴스 필드 이름**을 선언한다(빈 리스트면 인스턴스 값 불필요).

- `team.config.json` 의 `services.<역할>` 슬롯에 `{ provider, scope, <resource_fields 각 필드 = 고른 값> }` 를 기록한다.
- **인스턴스 값(리소스 ID·채널·캘린더 등)은 config 소관**이다 — 토큰(비밀)은 금고에, 인스턴스 값(비밀 아님)은 config 에. 토큰을 config 에 적지 않는다(토큰키 추적 거부 린트가 막는다).
- 팀 scope 슬롯의 provider·인스턴스 값을 **도입자가 config 에 커밋**하면 팀원은 그 선언을 읽기만 한다(단, 토큰은 v0.1 에서 각자 입력 — §4).

---

## 6. 재배선 (install-mcp 재실행) → 첫 가치 검증

### 6-A. 재배선

연결을 마치면 어댑터가 새 슬롯의 역할 도구 서버를 등록하도록 **install-mcp 를 재실행**한다(빈 슬롯이 채워졌으니 핸들러 기반 teammode 서버가 활성화됨). 팩의 `mcp.register_hint` 를 참고로 옮긴다.

```bash
python infra/install.py --root . --yes        # 재배선(어댑터에 teammode 서버 등록 + sync)
```

재배선 후 확인:
- teammode 서버가 에이전트 설정에 등록됐는지 확인
- 해당 역할의 도구가 에이전트에 노출됐는지 확인 (에이전트에서 `<역할>_create` 등 도구 호출 가능)

### 6-B. 첫 가치 검증 (도그푸딩 체크포인트)

재배선 완료 후, **실제 MCP 도구 호출 체인 전체**를 검증한다.
`python infra/teammode.py issue create` 는 JSON echo 만 하고 role_server·confirm 게이트·서비스 API 를 타지 않으므로 도그푸딩으로 인정하지 않는다.

**검증 절차 (issues 역할 기준)**:

1. **raw JSON-RPC harness 로 role_server 호출 확인**:
   ```bash
   echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
     | python -m infra.mcp.role_server --team <팀> --handlers-dir $(pwd)/handlers
   ```
   응답에 `issues_create` 가 포함돼 있으면 핸들러 로드 성공.

2. **에이전트에서 issues 역할의 create 도구 직접 호출** — 에이전트가 teammode 서버의 `issues_create` 도구를 호출한다.
   - 이 호출이 normalize → confirm-action.py 게이트를 경유하는지 confirm hook 로그로 확인.
   - confirm hook 가 **차단(exit 2)** 을 반환하면 게이트 정상 동작.

3. **사람의 allow 신호 제공** (사이드이펙트 허용 결정):
   ```bash
   mkdir -p .teammode-confirm
   touch .teammode-confirm/teammode-issues-create-allow
   ```
   또는 `export TEAMMODE_CONFIRM=teammode-issues-create-allow`

4. **재호출 → 서비스 API 실응답 확인**: 에이전트가 `issues_create` 도구 재호출 → role_server → 핸들러 → 실 서비스 API 응답.

> 이 시점이 **첫 도그푸딩 가능 지점**이다:
> 에이전트 도구 호출 → normalize 심 → confirm 게이트 → role_server → 핸들러 → 실 API 응답.
> 체인 전체를 타야 도그푸딩으로 인정한다.

---

## 안 하는 것 / 경계

- 토큰 발급·동의 클릭을 대신하지 않는다 — 보안 경계(사람 몫).
- 평문 토큰을 stdout·로그·세션로그·config 어디에도 출력하지 않는다.
- 핸들러를 사람 확인 없이 커밋하지 않는다 — 생성 후 반드시 코드 전체 출력 + 사람 수락.
- 기존 `handlers/<역할>.py` 를 임의로 교체하지 않는다 — 재사용 경로가 hard 강제.
- 검증·자가수리는 `doctor`(로드맵) 몫. 연결 직후 유효 ping 정도까지만.
- 코드 작성·이슈 본문 생성·다른 스킬 자동 호출 안 함. 푸시·PR 은 사람 결정.

## Common Mistakes

| 실수 | 올바른 방법 |
|------|------------|
| 발급 링크·단계를 스킬 본문에 하드코딩 | `providers/<provider>.json` 의 `token_guide` 를 읽어 안내 |
| 팀 scope 면 도입자 1회로 끝난다고 안내 | v0.1 은 각자 입력 — 팀 scope 도 각 멤버가 자기 토큰 입력 |
| 토큰을 config·세션로그에 기록 | 토큰은 금고(0600)에, 인스턴스 값만 config 에 |
| 금고를 동기화 폴더에 둬도 된다고 안내 | 평문이라 동기화 폴더 금지 — 0600 + git 미추적이 방어선 |
| 빈 슬롯을 에러로 취급 | 빈 슬롯 = 1급 시민. `[info]` 비치명, 연결 후 재배선으로 활성 |
| `handlers/<역할>.py` 있는데 새로 생성 | 재사용 hard 강제 — 교체는 직접 수정 + 팀 PR 합의 |
| 핸들러 코드에 토큰 직접 embed | `credentials.load()` 로만 읽는다 — secret lint 가 탐지 |
| 사람 확인 없이 핸들러 커밋 | 코드 전체 출력 → 사람 수락 → 커밋 순서 반드시 준수 |
| 흡수 경로에서 도구명·서비스명 직표기 | 역할 어휘(issues/chat/docs/calendar)로만 말한다 |

---
> 발견: 이 스킬은 AGENTS.md / CLAUDE.md 의 포인터로 찾는다(tm-onboard 와 동일 방식). 동작·데이터 근거는 `providers/<name>.json`(연결 데이터)·`infra/credentials.py`(금고)·`docs/spec/handlers.md`(핸들러 계약)·SPEC §5.3·§5.4·§7 확인.
