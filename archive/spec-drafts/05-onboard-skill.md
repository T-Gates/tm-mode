# teammode 스펙 05 — 온보딩 스킬 (tm-onboard) [설계 초안]

| | |
|---|---|
| spec_version | **0.1-draft** |
| 상태 | 설계 초안 (2026-06-14 새벽, Jane "어떻게 구현할지 생각해봐 / 낼 보러와야지"). **미검수** — 낼 dev-cycle 문서모드로 적대 검수 예정. |
| 범위 | `tm-onboard` 스킬 = 온보딩의 **판단층(LLM)**. install.py(기계층)를 지휘 + 서비스 연결을 대화로 안내. |
| 관련 | [04 install.py](04-install.md), [02 훅·어댑터](02-hook-manifest.md) §9 역할슬롯, prior art: 툴킷 `infra/skills/base/acme-onboard/SKILL.md` |

---

## 0. 한 문장

> install.py가 **기계적으로 할 수 있는 것**(스캐폴드·훅·env·L1)은 다 하고, **사람 판단·대화가 필요한 것**(서비스 연결, 토큰 찾기 안내, 어느 DB/채널/캘린더 고르기, 첫 가치 보여주기)은 `tm-onboard` 스킬이 한다. 스킬은 install.py를 *지휘*할 뿐 그 일을 다시 구현하지 않는다.

## 1. 왜 스킬이 따로 필요한가 (install.py로 안 되나)

install.py는 **결정적(LLM 없음)** 이라 못 하는 게 있다:
- "어느 Notion DB/캘린더를 쓸지" = 사람이 골라야 (판단).
- "Linear 키 어디서 발급?" = Jane가 한참 헤맨 그 마찰 → 에이전트가 정확한 링크·버튼으로 안내해야 (대화).
- OAuth "허용" 클릭 유도 = 대화.
- "켜졌고 이게 팀 상황이에요" 첫 가치 내레이션 = 요약(판단).

→ 이건 전부 **LLM(에이전트)** 의 일. 그래서 스킬. **원칙: 될 일은 코드로(install.py), 판단은 스킬로.** 스킬은 install.py가 기계적으로 한 걸 다시 하지 않는다.

## 2. 정체성

```yaml
name: tm-onboard
description: Use when setting up teammode or connecting team services. Triggers on
  "팀모드 셋업", "팀모드 시작", "이 레포 셋업해줘", "온보딩", "팀모드 합류", "서비스 연결", "teammode setup/onboard".
```
- `tm-` 접두사(자동완성 발견성). 진입 문장 "이 레포 셋업해줘"가 이 스킬을 트리거. (AGENTS.md/CLAUDE.md도 이 스킬을 가리킬 수 있음 — 이중 진입.)

## 3. install.py ↔ tm-onboard 분업 (핵심)

| 단계 | 주체 | 내용 |
|---|---|---|
| preflight·detect·role·scaffold·wire·env·L1 verify | **install.py** (기계) | spec/04. 결정적. 스킬은 이걸 *호출*만. |
| 감지·role 결과 해석·내레이션 | tm-onboard | install.py가 `--json`으로 뱉은 결과를 읽어 분기·설명 |
| L2 서비스 연결 (토큰 안내·OAuth·리소스 선택) | tm-onboard | §5 — 스킬의 본체 |
| config 서비스 슬롯 작성 | tm-onboard | 사람이 고른 값으로 (install.py는 빈 슬롯만 만듦) |
| 첫 가치 (context 실행→팀 상태 요약) | tm-onboard | `teammode context --json` 결과를 사람 말로 |

> **install.py 요구사항(04에 반영 필요)**: 스킬이 분기하려면 install.py가 **구조화된(JSON) 감지·role 결과**를 출력해야 한다. (04 §3 `--json`/요약 출력 추가 검토 — 미결.)

## 4. 흐름 (progressive: L1 느끼고 → L2 연결)

```
트리거 "이 레포 셋업해줘"
 1. install.py 실행 (L1 기계 부트스트랩) → role·감지 JSON 수신
 2. role 분기 내레이션:
    · 도입자(config 없음): "팀 새로 만드는 거네요. L1(세션로그·맥락주입) 켜졌어요."
    · 팀원(config 있음): "팀 합류 — 서비스 설정은 레포에서 다 읽었어요. 본인 개인연결만 하면 돼요."
 3. 첫 가치 즉시: `teammode context` → "지금 팀 상황은 …" (서비스 0이어도 L1 동작 보여줌)
 4. L2 제안(강요 X): "Linear·Slack 같은 거 연결할래요? 나중에 해도 돼요."
    → 원하면 §5 서비스별 안내. 안 원하면 여기서 끝(L1으로 충분).
```
핵심: **리니어 연결 *전에* L1 가치를 먼저 보여준다**(멘토 "온보딩 쉬움"의 답=첫 가치까지 거리). 연결은 당길 때.

## 5. 서비스별 연결 안내 (스킬의 본체)

각 서비스마다 스킬이 **정확한 링크·버튼**으로 사람을 데려간다(막연한 "키 찾아와" 금지 — Jane가 헤맨 마찰). 토큰 받으면 **API로 리소스 ID 자동조회 → 후보 제시 → 사람 1클릭 선택 → config 기록.**

**팀/개인 스코프 구분([스펙 02 §9]에 `scope` 추가 전제):**

| 서비스 | scope | 연결 방식 | 안내 스크립트(요지) |
|---|---|---|---|
| **Linear** | **개인** | 개인 API 키 | "https://linear.app/settings/api → 'Create key' → lin_api_… 붙여줘" → credentials 저장. 팀원 각자 1회(attribution 위해 필수). |
| **Google Calendar** | 개인/혼합 | **localhost OAuth(PKCE)** | "브라우저 뜨면 '허용'" → loopback 플로우(우리 서버 0). 캘린더 ID는 `calendarList`로 자동조회→선택. |
| **Slack** | **팀** | 봇 토큰 or 앱 manifest | 도입자 1회. "api.slack.com → Bot token(xoxb-) 붙여줘" 또는 manifest 붙여넣기. 채널은 `conversations.list`로 자동조회→선택. |
| **Notion** | **팀** | integration 토큰 | 도입자 1회. 토큰 + "이 페이지를 integration에 공유" 토글 안내 + DB는 자동조회→후보 선택. |

- **도입자 1회→팀원 0**: 팀 스코프(Slack·Notion)는 도입자가 한 번 연결→config 커밋→팀원은 레포에서 읽음(연결 0). 개인 스코프(Linear·GCal)는 팀원 각자 1회.
- **저장**: 팀 토큰=팀 금고(credentials), 개인 토큰=로컬. 평문 노출 금지.

## 6. 정직한 경계 (무인 불가, 사람 몫)

스킬은 동의 게이트 **직전까지** 몰고 가고, 그 클릭은 사람이:
- OAuth "허용", 개인키 "Create+붙여넣기", Notion "공유 토글" = 사람이 권한 *부여*(보안 경계, 무인 불가).
- 스킬은 정확한 위치·버튼을 짚어주고 결과를 받아 처리.

## 7. 검증·자가수리는 안 함 (doctor와 분리)

- prior art(acme-onboard Phase 2)는 검증까지 했으나, teammode는 **검증·자가진단을 별도 `doctor`**(나중 슬라이스)로 분리. tm-onboard는 "셋업+연결+첫가치"까지만. (단일책임.)
- 단 연결 직후 "한 번 호출해 토큰 유효 확인"(API ping) 정도는 포함(연결 성공 피드백).

## 8. 크로스에이전트

- SKILL.md는 프롬프트라 CC·Codex 공통. MCP 등록 방식 차이(`~/.claude.json` vs `~/.codex/config.toml`)는 **install.py/어댑터의 install-mcp(L2)** 가 기계적으로 처리하고, 스킬은 "어느 서비스·어느 리소스"만 판단 → 에이전트 무관.

## 9. prior art 대비 개선점 (툴킷 acme-onboard → tm-onboard)

| 툴킷 acme-onboard | teammode tm-onboard |
|---|---|
| install.sh + MCP env 손편집 안내(에이전트별 분기 장황) | install.py(기계) 호출 + install-mcp가 등록 → 스킬은 판단만 |
| 토큰 전부 수동 붙여넣기 | OAuth localhost(Google)·자동 리소스 조회로 마찰↓, 못 줄이는 것만 안내 |
| 검증(Phase 2) 포함 | doctor로 분리(단일책임) |
| 팀/개인 토큰 구분 암묵 | scope: team\|personal 명시 → 도입자1회/팀원각자 자동 분기 |

## 10. 미결 (낼 검수에서 닫을 것)

1. install.py가 스킬에 넘길 **구조화 출력**(role·감지) 형식 — `--json` 스키마 확정 (04 §3 보강).
2. tm-onboard가 L1만 책임지고 L2 연결은 별도 스킬(`tm-connect`?)로 더 쪼갤지 — 단일 스킬 vs 분리.
3. credentials 저장 메커니즘(팀 금고)이 teammode에 아직 없음 — 의존 슬라이스.
4. "이 레포 셋업해줘" 진입을 AGENTS.md가 install.py 직접 실행 vs tm-onboard 트리거 — 이중 진입 시 중복 방지.
5. SKILL.md 실제 작성(이 문서는 설계, 다음은 스킬 본문) — 분량·구조(prior art 324줄 → 더 얇게?).
