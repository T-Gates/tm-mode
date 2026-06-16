# acme-toolkit 오픈소스 분리 계획 (Plan 에이전트 보고서)

작성: 2026-06-10 / 상태: 검토 대기 (팀 합의 전 — 툴킷 레포에 커밋하지 않음)

---

## 결론 먼저

**가능성: 상.** 엔진(infra/)과 데이터(memory/)가 이미 디렉토리 수준에서 분리돼 있고, 코드→데이터 결합은 경로 참조 ~10곳 + 내용 결합 2개 파일(members.md, persona.md)뿐. 공수 **약 2~3일(13–18h)**.

**⚠️ 유일한 하드 블로커: git 히스토리 324개 커밋에 실 Notion 토큰(`ntn_16271224400...`)이 박혀 있음.**
→ 토큰 즉시 회전(revoke+재발급) 필요. public 레포는 fresh start로 가면 자연 해소.

추천 경로: **private 내부에 `team.config.json` 설정 레이어 먼저 도입(무중단) → 엔진만 fresh-start public 레포 추출 → Acme는 upstream remote merge로 엔진 업데이트 수신.**

---

## 1. 팀 전용 하드코딩 인벤토리 (전수)

이동처 범례: **[C]** `team.config.json`, **[P]** private 전용(공개 안 함), **[E]** public 예시(placeholder)로 치환.

### 1-1. Slack (채널 ID·봇)

| 파일:라인 | 내용 | 이동처 |
|---|---|---|
| `CLAUDE.md:114`, `AGENTS.md:105` | `C0B70642P3L`(#공지사항), `C0B71DQ6K8U`(#alerts), `C0B6GBBC3HV`(#ai-agent) | [C] `slack.channels` |
| `CLAUDE.md:111`, `AGENTS.md:102` | "Bot: Acme Bot" | [C] `slack.bot_name` |
| `infra/cron/daily-digest.sh:16` | `DAILY_CHANNEL:-C0B71DG9E76` 기본값 | [C] (env 폴백 유지) |
| `infra/skills/base/acme-schedule/SKILL.md:67` | #공지사항 ID 자동 발송 | [C] 참조로 치환 |
| `infra/skills/core/acme-manage-knowledge/SKILL.md:111,117,136,155` | #지식베이스 `C0B91G9NN0K` ×4 | [C] `slack.channels.knowledge` |
| `infra/mcp/slack-acme/server.mjs:43` | 설명 텍스트의 예시 채널 ID | [E] |
| `infra/persona.md` 전체 | 토깽이 페르소나 | [P] 유지 / public엔 generic 예시 persona |

### 1-2. Notion (DB/페이지 ID)

| 파일:라인 | 내용 | 이동처 |
|---|---|---|
| `CLAUDE.md:92-95`, `AGENTS.md:86-88` | 문서 DB `33c3c9d7-...`, Key DB `3563c9d7-...` | [C] `notion.docs_db`, `notion.key_db` |
| `README.md:13` | Key DB 링크 | [P] |
| `infra/skills/util/acme-credentials/SKILL.md:12,16,22` | Secret 페이지·계정 DB·Key DB ID | [C] |
| `infra/skills/core/acme-create-meeting/SKILL.md:12` | 문서 DB ID | [C] |
| `infra/skills/base/acme-onboard/SKILL.md:213` | Key DB ID | [C] |
| `infra/docs/SPEC.md:338,466`, `infra/cron/daily-digest.sh:91` | 문서 DB·Key DB ID | [C] |

### 1-3. Linear

| 파일:라인 | 내용 | 이동처 |
|---|---|---|
| `CLAUDE.md:87-88`, `AGENTS.md:81-84` | `linear.app/acme/team/T/active`, prefix `TG` | [C] |
| `infra/skills/core/acme-create-tasks/SKILL.md:28-30` | 워크플로 상태 UUID 3개 | [C] `linear.states` |
| `.github/workflows/linear-sync.yml:29,32` | `TG-[0-9]+` 추출 | [E] `vars.ISSUE_PREFIX` 주입 |
| start-task/end-task/get-context/create-tasks 곳곳 | `TG-N` 예시 | [C]+[E] |
| `infra/skills/util/acme-lint/SKILL.md:53,103` | prefix 통일 검사 | 설정값 대조로 |
| `memory/team/members.md:44,63,83` | 멤버 Linear UUID | [P] (자동 해결) |

### 1-4. 팀원 이름·이메일·이모지

| 파일:라인 | 내용 | 이동처 |
|---|---|---|
| `CLAUDE.md:5`, `AGENTS.md:5`, `infra/docs/INDEX.md:5` | 실명 3인 | [P] → members.md 참조 문구로 |
| `infra/hooks/session-log-remind.py:40,53` | `jane-doe/alice/jonathan` 하드코딩 (members.md 단일 참조 원칙 위반) | members.md 동적 로드 또는 일반 문구 |
| `infra/skills/base/acme/SKILL.md:55,114` | 멤버 이모지, "Jane daily 로그 밀도가 기준" | members.md 참조로 |
| `infra/skills/core/acme-get-context/SKILL.md:34-48` | 이모지·이름 출력 예시 | [E] |
| `infra/skills/base/acme-onboard/SKILL.md:50` | "CTO(Jane)에게 받거나" | [C] `admin_contact` |
| `infra/cron/daily-digest.sh:7`, `SPEC.md:601` | "Jane 라즈베리파이" | [E] "운영 호스트" |
| `memory/team/members.md` | 개인 이메일 3건 등 | [P] |

### 1-5. 제품명·팀

| 항목 | 처리 |
|---|---|
| "Acme/acme", "팀 17기" (CLAUDE.md, INDEX.md, SPEC.md 등) | [C] `product_name`, `team_description` |
| demo-product 예시들 | [E] |
| `infra/skills/util/acme-acme-browse/` 전체 | [P] public 제외 |
| 캘린더 카테고리(팀/팀/개인) + colorId 매핑 | [C] `calendar.categories` |

### 1-6. 환경변수·기타

| 항목 | 처리 |
|---|---|
| `LEGACY_TOOL_HOME` (156곳) | **유지 권장** — rename은 전 팀원 재설치 유발, 이득 없음 |
| `ACME_GCAL_ID` (8곳) | [C] `gcal.key_name` |
| `acme on/off`, `acme-` 스킬 접두사 | v1 유지 (rename은 메이저 버전 과제) |
| Acme ASCII 아트 배너 | [C] 또는 persona 파일로 |

---

## 2. 코드↔데이터 결합점 (infra → memory)

| 코드 | 참조 |
|---|---|
| `infra/install.sh:30,309-329,333` | members.md append, sessions/ mkdir |
| `infra/update.sh:198` | sessions/<이름>/util-skills.json |
| `infra/hooks/auto-commit.py:21,28` | `memory/team/sessions/` 매칭, `git add memory/` |
| `infra/hooks/session-log-remind.py:14,40,53` | sessions glob + 멤버명 하드코딩 (유일한 원칙 위반) |
| `infra/hooks/session-start.py:19` | context-sources.json → memory/INDEX.md (이미 선언적 — 좋은 패턴) |
| `infra/hooks/confirm-action.py:45` | persona.md 로드 |
| `infra/cron/daily-digest.sh` | sessions/, members.md, ground-rules.md, persona.md, INDEX 자동갱신 |
| `infra/skills/util/acme-lint` | decisions/current.md 등 구조 검사 |

**결합은 전부 "경로" 수준. memory/ 스켈레톤만 public에 유지하면 엔진 그대로 동작.**

## 3. 스킬 분류

- **범용**: base 3종, core 10종, util(check-health, cheer, lint, manage-utils, credentials*) — 팀 상수만 설정화하면 됨 (*credentials는 Notion ID 설정화)
- **팀 전용 (public 제외)**: `acme-acme-browse`

## 4. 분리 아키텍처 — A안 추천

```
public:  team-agent-toolkit (fresh start — 엔진 + example config + memory 스켈레톤)
private: acme-toolkit (현재 레포 그대로)
         └ git remote add upstream <public>
         └ 엔진 업데이트: git fetch upstream && git merge upstream/main
```

- Acme는 현재 레포·히스토리·워크플로 무변경 = 무중단
- 첫 merge만 `--allow-unrelated-histories`, 이후 일반 merge
- 운영 규칙 하나: **"infra/는 public에서 고치고 private은 merge로 받는다"** (memory/·config는 public에 없어 충돌 0)
- 기각: B안(2-레포 overlay — 결합점 대수술 + 전원 재설치 = 무중단 위반), C안(template — 업데이트 수신 경로 없음. 단 A안 public에 template 버튼 겸용은 OK)

## 5. 설정 레이어

```
team.config.json          ← 팀 상수 단일 소스 (private 커밋 대상 — 시크릿 아님. public엔 example)
infra/persona.md          ← 마스코트 (팀별 교체 포인트 = 킬러 기능)
memory/team/members.md    ← 기존 유지
infra/mcp/*/.env          ← 토큰 (이미 gitignore)
```

스키마 핵심: team_name, product_name, locale/timezone, linear{url, team_key, issue_prefix, states}, notion{docs_db, key_db, secret_page, account_db}, slack{bot_name, channels{announce, alerts, daily, knowledge}}, calendar{key_name, categories[]}, admin_contact.

구현 포인트:
- SKILL.md들은 ID를 `{config...}` 참조로 치환 + "팀 상수는 team.config.json" 한 줄 (에이전트가 읽는 문서라 런타임 코드 불필요)
- 코드 수정은 daily-digest.sh + session-log-remind.py 2곳만
- **context-sources.json에 team.config.json 추가** → 세션마다 자동 주입 = 기존 메커니즘 재사용

## 6. 마이그레이션 단계 (무중단)

| 단계 | 작업 | 검증 |
|---|---|---|
| 0 | **Notion 토큰 회전 (즉시)** | Key DB 갱신, MCP 재연결 |
| 1 | team.config.json 도입 + [C] 치환(~40개소) + session-log-remind 이름 하드코딩 제거 | acme-lint 확장 + on/off 왕복 + digest 수동 1회 + 스킬 3종 스모크 |
| 2 | 경계 정리 (acme-browse 표시, SPEC 분리, CLAUDE.md 재구성) | lint + 마이그레이션 0006 추가 |
| 3 | public 레포 fresh start (엔진 + example + README 한/영 + 라이선스) | gitleaks 스캔 + 클린 머신 install 풀사이클 |
| 4 | upstream 연결 (`-s ours --allow-unrelated-histories`로 기준점) | merge no-op 확인 |
| 5 | 운영 규칙 문서화 + 시험 라운드트립 | 엔진 변경 1건 public→private 흘려 전원 동기화 확인 |

## 7. 보안 점검

| 항목 | 발견 | 조치 |
|---|---|---|
| **실 Notion 토큰** | **히스토리 324/740 커밋에 존재** | 즉시 회전. public은 fresh start로 해소 |
| .env | 히스토리에 커밋된 적 없음 (확인) | 복사 시 제외만 주의 |
| 개인 이메일 | members.md + 커밋 author | memory 미포함 + fresh start로 author 소거, public 커밋은 noreply 권장 |
| 마지막 게이트 | — | `gitleaks detect` + 수동 grep(`C0B`, `c9d7-`, 이메일, 실명) |

## 8. 공수: 합계 약 2~3일 (13–18h)

0.5h(토큰) + 4–6h(설정 레이어) + 2–3h(경계) + 4–6h(public 생성) + 1h(upstream) + 2h(검증)

## 9. 리스크·미결

- 팀원이 infra/를 private에서 직접 수정 → upstream 충돌. lint에 검사 추가로 완화
- SKILL.md에서 ID 지우고 config 로드 빼먹으면 기능 저하 → session-start 주입으로 완화
- `acme-` 네이밍: v1 유지, rename은 v2
- 미결: 한/영 수준·linear-mcp 패치본(@hatcloud) 라이선스 확인
- ~~레포 이름~~ → **`teammode` 확정 방향 (2026-06-11, Jane 발안 "팀모드 툴킷")** — 이름=사용법(`teammode on`), 태그라인 "Turn your team mode on." 약점: 일반명사 SEO 불리(직링크 유통이라 수용). GitHub 선점 확인 필요
- ~~라이선스~~ → **MIT 방향 확정 (2026-06-10)**

## 10. 사용자(도입 팀) 플로우 — 2026-06-10 논의 추가

| 단계 | 행위자 | 소요 | 내용 |
|---|---|---|---|
| 0. 발견 | — | 5분 | 긱뉴스/X 데모 GIF → README "지금 팀 상황" 한 방 데모 + 실사용 스토리 |
| 1. 포크 | 리더 | 1분 | Use this template / fork → 팀 private 레포 |
| 2. 팀 설정 | 리더 1회 | 30분~1h | team.config.json + persona.md + MCP .env. **이탈 최다 지점** |
| 3. 팀원 합류 | 각자 | 5분 | `git clone && ./infra/install.sh <이름>` 한 줄 |
| 4. 일상 사용 | 전원 | — | acme on → 훅이 세션로그 반강제 누적 → 2주차 복리 시작 |
| 5. 엔진 업데이트 | 리더 | 월 1회 | `git fetch upstream && git merge upstream/main` |
| 6. 커스텀 | 팀 | — | 팀 전용 util 스킬 추가, 잘 만든 건 upstream PR 역기여 |

설계 포인트:
- **⚠️ 도입 경로 이원화 (2026-06-11 발견)** — 도입 팀은 **fork 금지, Use this template(→ org private) 또는 clone+private push**로. public 레포의 포크는 무조건 public이라 팀 데이터(회의록·세션로그)가 공개되는 함정. upstream 업데이트는 GitHub 포크 관계 없이도 `git remote add upstream`+merge로 수신 가능(Acme 자신이 이 방식). fork는 기여자(PR) 전용. README 온보딩에 경고 박스 1순위
- **성공 지표 — "스타 100 + 살아있는 팀 5"** (런칭 1~2달): ① 도입 팀 3~5 ② 2주 생존율(세션로그 지속) 절반↑ ③ 외부 기여자 2~3 ④ 스타 50~100. 활성 팀 > 스타. 확산 동선: 1팀(친구) 검증 → 저변 확대 → 웹엑스(팀 전체) 공유
- **부분 설정 허용** — Linear/Notion/Slack 중 일부만 연결해도 해당 스킬만 비활성, 나머지 동작. "Slack만 있어도 시작"
- **onboard 스킬 = 차별점** — 에이전트가 대화로 설정을 도와주는 온보딩 (스크린샷 가이드 병행)
- 1일차 가치 앞당기기 — 설치 직후 get-context가 뭐라도 보여주도록 온보딩에서 첫 로그 생성

## 11. 가치 전달 전략 — 2026-06-10 논의 추가

핵심 문제: **비용은 1일차, 가치(세션로그 복리)는 2주차.** 이 갭 공략이 전부.

1. **마법의 순간을 팔기** — README 최상단 30초 데모 GIF: "지금 팀 상황" → 팀원 3명 작업·블로커·일정 좌르륵. 실화 소재: Jane의 질문(센서 1:1)과 Jonathan의 플래그(nodes.* stale)가 같은 지점임을 시스템이 잡아낸 장면
2. **고통으로 열기** — "그 결정, 회의 때 했는데 왜 그랬는지 기억하는 사람이 없다" → 가치 제안: "맥락이 휘발되지 않는 팀"
3. **규율을 훅이 든다** — "습관을 요구하지 않습니다. 훅이 대신 듭니다" (리마인더+auto-commit 반강제 구조가 도입 마찰의 해독제)
4. **정직한 기대치** — "우리도 첫 주는 귀찮았다. 2주차부터 시스템이 우리보다 팀을 잘 기억했다" (Acme fixture = 1호 고객 후기)

## 12. 릴리즈 타임라인·전략 결정 — 2026-06-10 논의

- **제품화 기각** — 플랫폼 리스크(Anthropic 네이티브 팀 기능 가능성), 해자 없음(md+py, 복제 1주), 3인 팀 초점 분산. 오픈소스 → 반응 좋으면 open core(호스팅 버전) 재검토
- **라이선스: MIT 방향** — 포크-커스텀 목적에 부합, AS-IS 면책, 기여 수용 가능
- **프레이밍: "우리가 실전에서 쓴 협업 깃"** — 실사용 서사가 최강 카드
- **2-웨이브 릴리즈**: ① 7월 초중순 1차 발사("팀 3인 팀이 6주째 실전 사용") — 선점 리스크(유사 툴킷·네이티브 기능) 때문에 팀 종료까지 안 기다림 ② 팀 종료(11~12월) 시 "풀시즌 데이터 회고" 글로 2차 점화
- **홍보 채널**: 긱뉴스·디스콰이엇·기술블로그(1군) → X·Reddit r/ClaudeAI·awesome 리스트(2군) → 팀 내부는 격차 가치 줄어든 뒤(3군)
- **선행 조건**: ~~팀 합의~~ ✅ **완료 (2026-06-11 — Jonathon·Jonathan 긍정적)**, Notion 토큰 회전(보안 7절)만 남음
- **해자 분석 (2026-06-11)**: 코드는 해자 아님(포크 가능). OSS 해자 = 표준 지위 방어 — ① dogfooding 갱신 속도(매일 실사용 피드백 루프, 카피캣엔 없음) ② 크로스에이전트 매트릭스 유지("귀찮음의 해자", Anthropic 네이티브가 와도 타 에이전트 구역은 생존) ③ 어휘 선점(팀 모드·세션로그·06시 컷) ④ 생태계 레지스트리(v2, 도입 수십 팀 이후). 셋 다 기존 활동의 부산물이라 추가 투자 불요. 단 우리 목적(평판·도구)상 카피당해도 실손실 거의 없음 — 해자는 "표준이 되고 싶을 때" 필요
- **YC 각도 (2026-06-11, Jane)**: teammode 평판 → 추후 YC 지원 시 founder signal로 활용 구상. 위치: 본업(Acme) 지원서의 보조 증거 — "자체 협업 인프라를 만들어 오픈소스화, N팀 사용"은 빌딩 속도·distribution 능력 증명. teammode 자체로 YC는 비추(제품화 기각 논리 동일 — 플랫폼 리스크)
- **레포 소유·권한 (2026-06-11)**: 새 org 안 팜. Acme org 아래 public 레포(평판이 팀에 쌓임) + 친구는 **그 레포만 outside collaborator(maintain)** — org 멤버 초대 금지(private 레포 노출 위험). org base permission "No permission" 확인. 프로젝트가 커지면 그때 전용 org로 transfer(스타·리다이렉트 보존됨)
- ~~외부 협업자: 친구 팀 2호 도입~~ → **방향 전환 (2026-06-11 친구와 합의)**: 친구도 자기 팀 하네스를 직접 만들기로 — **1달간 각자 개발 후 합쳐보기**. 친구는 Hermes 기반, 우리는 Claude Code 기반.
  - 장점: 친구 오너십 극대화, 독립 설계 2개의 수렴점 비교(설계 검증), 동일 문제를 두 팀이 각자 만들 만큼의 시장 신호
  - ⚠️ 관리할 것: ① **"합치기" 정의 사전 합의** — 코드 머지가 아니라 "설계 비교 → 베이스 선택 + 아이디어 이식"임을 미리 공유 ② **2주차 중간 체크인 30분** (어휘·큰 발견 공유, 발산 방지) ③ **릴리즈 시점 재조정** — 단독 7월 공개가 합치기 약속과 충돌 가능. 단독 공개 가능 여부를 친구와 합의하거나 1차 발사를 합친 후로 이동
  - 기존 원칙 유지: private 레포 접근 금지, 합칠 때도 public 엔진에서만 협업

## 13. 운영·크로스에이전트 전략 — 2026-06-11 새벽 논의

**운영 (정기회의 없음, 비동기 우선)**
- GitHub Issues/Discussions가 본진. PR = 진행 보고. CONTRIBUTING.md가 규칙 전달
- 케이던스 = 월 1회 릴리즈 태깅(정기회의 대체). 타임박스 주 1~2시간, README에 "사이드 프로젝트, 응답 느릴 수 있음" 명시
- 역할: Jane lead maintainer(머지 권한), 협업자는 contributor→maintainer 승급. 에이전트별 담당 메인테이너 체제
- **teammode 운영을 teammode로** (dogfooding = 살아있는 데모, README 한 줄: "이 프로젝트는 자기 자신으로 운영됩니다")

**크로스에이전트 (Claude/Codex/Hermes 프롬프트 이식성 문제)**
- 티어 선언: Tier 1 Reference = Claude Code (100% 검증) / Tier 2 Best effort = Codex·Hermes (코어 플로우 보장)
- 스킬 작성 규약(짧은 명령문·번호 단계·명시적 출력 포맷·에이전트 전용 문법 금지) + acme-lint에 이식성 검사 추가
- 갈라질 땐 어댑터: 공통 본문 + 에이전트별 분기 블록 (전체 복제본 2개 금지 — 드리프트 지옥)
- 골든 시나리오 5개(켜기→컨텍스트→이슈→로그→끄기) 릴리즈 전 에이전트별 수동 체크
- **훅 어댑터 레이어 (Jane 발안)**: manifest.json에 `agents` 필드(지원 에이전트) + `event` 에이전트별 매핑 추가 → sync.py가 플래그(--claude/--codex/--hermes) 보고 자기 것만·자기 이벤트명으로 등록. 대응 이벤트 없으면 해당 훅만 우아한 축소. Hermes 타겟 = ~/.hermes/plugins (pre_llm_call≈UserPromptSubmit, on_session_start≈SessionStart)

**Hermes Agent 조사 결과 (2026-06-11)**
- NousResearch, 2026-02 공개 오픈소스. MCP 네이티브 ✅ / 스킬 시스템 ✅ / 플러그인 훅(pre_llm_call·on_session_start/end·shell hooks) ✅ → 포트 가능성 높음, 난이도 중 (Codex 지원 추가와 같은 패턴, install.sh --hermes)
- **협업자 기여 1호 후보 = Hermes 포트** ("너네 팀이 쓰려면 어차피 필요" — 자기 필요+기여 영역+초기 멤버 티켓 결합)
- 포지셔닝 충돌 없음: Hermes는 개인 기억, teammode는 팀 기억. 설득 문구: "Hermes가 너를 기억해주잖아? teammode는 너네 팀을 기억해줘"
- 참고: github.com/nousresearch/hermes-agent, hermes-agent.nousresearch.com/docs

## 구현 핵심 파일

- `CLAUDE.md` — 팀 상수 최다 밀집
- `infra/install.sh` — 설치 파이프라인 결합점
- `infra/hooks/sync.py` — 엔진 핵심
- `infra/cron/daily-digest.sh` — 코드에 박힌 하드코딩 최대 지점
- `infra/skills/util/acme-lint/SKILL.md` — 각 단계 검증 게이트
