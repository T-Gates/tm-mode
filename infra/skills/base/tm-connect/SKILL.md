---
name: tm-connect
description: Use to connect a service slot (issues / chat / docs / calendar) to a teammode team — guiding token issuance, storing it in the local credentials vault, and recording the resource in team config. Triggers on "서비스 연결", "이슈 트래커 연결", "채팅 연결", "문서 연결", "캘린더 연결", "팀모드 서비스 붙여줘", "teammode connect", "connect service", or after tm-onboard offers L2.
---

# tm-connect — 서비스 슬롯 연결 (L2)

teammode 의 **역할 슬롯**(issues / chat / docs / calendar)에 서비스를 붙이는 스킬. tm-onboard 가 첫 가치(L1) 직후 "연결할래요?" 하고 제안하면, **실행은 이 스킬**이 한다.

```
tm-onboard (제안+트리거)  →  tm-connect (실행)
   "연결할래요?"              토큰 안내 → 금고 저장 → config 기록 → 재배선
```

## 원칙
- **될 일은 데이터·엔진이, 판단·동의 게이트는 이 스킬이.** 발급 링크·단계·연결방식은 **`providers/<역할의 provider>.json` 에서 읽어** 안내한다 — 스킬 본문에 링크·단계를 하드코딩하지 않는다.
- **역할 어휘만.** 이 스킬은 issues / chat / docs / calendar 라는 **역할**로만 말한다. 실제 어느 제품인지는 `team.config.json` 의 `services.<역할>.provider` 와 `providers/<provider>.json` 이 답한다 — 런타임에 데이터를 보고 옮긴다.
- **각자 입력(v0.1).** 각 멤버가 **자기 토큰을 직접 입력**한다. 팀 토큰 자동공유는 v0.1 에 없다 — 팀 scope 슬롯도 각 멤버가 "각자 1회" 입력한다.
- **정직한 경계(사람 몫).** 토큰 발급·동의(OAuth "허용", 개인키 "Create+붙여넣기", 공유 토글)는 **사람이 권한을 부여**하는 보안 경계라 무인 불가. 스킬은 동의 게이트 직전까지 데려가고 클릭·붙여넣기만 사람이.

## 0. 어느 역할을 연결하나 — provider 데이터를 먼저 읽는다
사용자가 붙이려는 역할(issues / chat / docs / calendar)을 정한다. 그 역할의 provider 와 연결 데이터는 두 곳에서 읽는다 (**추측 금지 — 데이터가 진실**):

1. **이미 config 에 선언된 provider**: `team.config.json` 의 `services.<역할>.provider` 를 읽는다.
2. **아직 빈 슬롯이면**: 어떤 provider 를 쓸지 사람에게 묻고, `providers/` 에 그 이름의 팩이 실재하는지 확인한다(`providers/<provider>.json`). 없으면 미지원 — 추측해서 진행하지 않는다.

provider 가 정해지면 그 팩(`providers/<provider>.json`)에서 **다음 필드를 데이터로 읽어** 안내를 구성한다:

| 팩 필드 | 용도 (스킬이 읽어서 안내) |
|---|---|
| `token_guide.url` | 토큰 발급 페이지로 가는 **딥링크** — 그대로 사람에게 제시 |
| `token_guide.steps` | 발급까지의 **단계 목록** — 순서대로 안내 |
| `auth` | 연결방식(`api_key` / `oauth` / `bot_token`) — 멘트를 이 값에 맞춰 고른다(§2) |
| `default_scope` | `team` / `personal` — 누가 입력하나의 기본값(§3) |
| `resource_fields` | 연결 후 config 에 채울 **인스턴스 필드** 이름 목록(§4) |
| `mcp.register_hint` | MCP 등록 힌트 — 재배선 안내에 참고 |

> 같은 안내 문구를 스킬에 박지 않는 이유: provider 마다 발급 경로·연결방식·필요 필드가 다르고, 새 provider 가 팩만 추가돼도 이 스킬이 그대로 안내할 수 있어야 한다(데이터 = 진실).

## 1. 토큰 발급 안내 (사람 몫 — 동의 게이트까지)
팩의 `token_guide.url` 로 데려가고 `token_guide.steps` 를 순서대로 옮긴다. 막연한 "키 찾아와" 금지 — **정확한 링크·버튼**으로.

- 사람이 발급 페이지에서 토큰을 만든다(이 단계는 사람이 권한을 부여하는 보안 경계 — 무인 불가).
- "당신 몫은 토큰 N개뿐" 으로 기대치를 고정한다(토큰 병목 완화).

## 2. 연결방식(auth)별 멘트 — 팩의 `auth` 값으로 분기
팩의 `auth` 값을 읽어 안내를 고른다(스킬이 제품을 모름 — 값으로만 분기):

| `auth` | 안내 |
|---|---|
| `api_key` | 발급 페이지에서 **개인/통합 키를 Create → 복사 → 붙여넣기**. attribution 이 본인으로 남도록 각자 발급. |
| `bot_token` | 앱/봇 토큰을 발급해 워크스페이스에 설치 후 **봇 토큰 복사 → 붙여넣기**. |
| `oauth` | **localhost OAuth(PKCE)** — 동의 화면에서 사람이 "허용". 콜백으로 토큰 수령(붙여넣기 불필요할 수 있음). |

## 3. 각자 입력 → credentials 금고에 저장 (B-3)
**팀 자동공유 없음(v0.1).** 각 멤버가 자기 토큰을 직접 입력하고, **로컬 금고**(`infra/credentials.py`)에 저장한다.

- 저장 위치: 멤버 로컬 `$XDG_DATA_HOME/teammode/credentials/<team>.json`(파일 권한 0600). git 추적 안 됨.
- 팩의 `default_scope` 로 네임스페이스를 고른다 — `team` 이든 `personal` 이든 **v0.1 은 각자 1회 입력**이다(팀 scope 라고 도입자 1회로 끝나지 않는다 — 자동공유 미구현).
- 저장은 엔진/모듈이 한다(스킬이 평문 토큰을 stdout·로그·세션로그에 절대 출력하지 않는다):
  ```bash
  python -c "import sys; sys.path.insert(0,'infra'); import credentials; \
    credentials.store('<team>', '<scope>', '<역할>', input())"
  ```
  토큰은 표준입력으로만 흘리고, 명령행 인자·로그·세션로그 어디에도 평문으로 남기지 않는다.

> ⚠️ **평문 금고 경고(필수 안내).** v0.1 금고는 **평문 JSON** 이다. Syncthing·Dropbox·iCloud 등 **동기화 폴더에 절대 두지 말 것** — 평문 토큰이 동기화되어 다른 기기·서비스로 새면 곧 유출이다. 0600 권한 + git 미추적 + 동기화 폴더 금지가 v0.1 의 방어선이다(OS 키체인은 v0.2).

## 4. config 슬롯 기록 — 인스턴스 값(resource_fields)
토큰을 받으면 그 서비스에서 **실제 어느 리소스**(문서 DB·채팅 채널·캘린더 등)를 쓸지 정한다. 팩의 `resource_fields` 가 config 에 채울 **인스턴스 필드 이름**을 선언한다(빈 리스트면 인스턴스 값 불필요).

- `team.config.json` 의 `services.<역할>` 슬롯에 `{ provider, scope, <resource_fields 각 필드 = 고른 값> }` 를 기록한다.
- **인스턴스 값(리소스 ID·채널·캘린더 등)은 config 소관**이다 — 토큰(비밀)은 금고에, 인스턴스 값(비밀 아님)은 config 에. 토큰을 config 에 적지 않는다(토큰키 추적 거부 린트가 막는다).
- 팀 scope 슬롯의 provider·인스턴스 값을 **도입자가 config 에 커밋**하면 팀원은 그 선언을 읽기만 한다(단, 토큰은 v0.1 에서 각자 입력 — §3).

## 5. 재배선 (install-mcp 재실행) → 첫 가치
연결을 마치면 어댑터가 새 슬롯의 MCP 를 등록하도록 **install-mcp 를 재실행**한다(빈 슬롯이 채워졌으니 sync 가 그 매처를 활성화). 팩의 `mcp.register_hint` 를 참고로 옮긴다.

```bash
python infra/install.py --root . --yes        # 재배선(설치된 어댑터에 install-mcp + sync)
```

첫 가치는 **issues 동사**로 보여준다 — 연결된 issues 슬롯이 있으면 정규 입력 스키마가 echo 된다:
```bash
python infra/teammode.py issue create --root . --title "<요약>"
```
빈 슬롯이면 엔진이 `[info]` 로 비치명 안내한다(빈 슬롯 = 1급 시민, 에러 아님).

## 안 하는 것 / 경계
- 토큰 발급·동의 클릭을 대신하지 않는다 — 보안 경계(사람 몫).
- 평문 토큰을 stdout·로그·세션로그·config 어디에도 출력하지 않는다.
- 검증·자가수리는 `doctor`(로드맵) 몫. 연결 직후 유효 ping 정도까지만.
- 코드 작성·이슈 본문 생성·다른 스킬 자동 호출 안 함. 푸시·PR 은 사람 결정.

## Common Mistakes
| 실수 | 올바른 방법 |
|------|------------|
| 발급 링크·단계를 스킬 본문에 하드코딩 | `providers/<provider>.json` 의 `token_guide` 를 읽어 안내 |
| 팀 scope 면 도입자 1회로 끝난다고 안내 | v0.1 은 각자 입력 — 팀 scope 도 각 멤버가 자기 토큰 입력 |
| 토큰을 config·세션로그에 기록 | 토큰은 금고(0600)에, 인스턴스 값만 config 에 |
| 금고를 동기화 폴더에 둬도 된다고 안내 | 평문이라 Syncthing/Dropbox 금지 — 0600 + git 미추적이 방어선 |
| 빈 슬롯을 에러로 취급 | 빈 슬롯 = 1급 시민. `[info]` 비치명, 연결 후 재배선으로 활성 |

---
> 발견: 이 스킬은 AGENTS.md / CLAUDE.md 의 포인터로 찾는다(tm-onboard 와 동일 방식). 동작·데이터 근거는 `providers/<name>.json`(연결 데이터)·`infra/credentials.py`(금고)·SPEC §5.3·§5.4·§7 확인.
