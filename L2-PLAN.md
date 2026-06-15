# teammode L2 빌드 계획 (서비스 연결 단계) — v2 (검수 3축 반영)

> 착수: 2026-06-16 / 모드: 무인 dev-cycle (구현→적대적 검수→반영, "수정 없음"까지 루프)
> 기준선: **334 passed** (L1 + Windows 분기). git clean, main == origin/main.
> 스펙 소스: `SPEC.md` §2.5·§2.7·§2.8·§2.9·§2.12·§3·§5.3·§5.4·§6·§7 + 부록 A.3·B.
> 워커: 생산 = Codex `exec`(workspace-write), 검수 = Claude 서브에이전트/Codex 교차(생산자≠검수자).
> 푸시 = 사람(Jane) 게이트. 에이전트 단독 push 금지.
> **v2 = 적대적 계획검수 3축(의존성·스키마·안전)의 P0 6건 + B0 선행 슬라이스 + P1 다수 반영본.**

## 검수 반영 요약 (v1 → v2)

- **[P0-1]** 시나리오 03 verb 구조 정정: `teammode.py issue --root <root> create --title ...` — verb=`issue` 하나, `create`는 `--root` 뒤 positional. (F)
- **[P0-2]** 03이 공유 root에 services 채우면 04/05 회귀 → fixture 격리/teardown 명시. (F)
- **[P0-3]** 빈 슬롯 우선 규칙(§2.9/§7.2)을 **sync가 적용** — 어댑터가 config services를 읽어 빈슬롯 MCP 매처 생략. L1 기존 미준수(linear 빈슬롯 매처 등록 중) 동시 교정. (B)
- **[P0-4]** `.gitignore` credentials 패턴 + **팀루트 추적파일 토큰키 거부 린트**(.gitignore는 죽은방어). (B0/A)
- **[P0-5 → 무효화]** install-skills(L2-C) **삭제**(Jane 결정 2026-06-16) → 심링크 traversal 가드 불필요해짐. v0.1 스킬은 AGENTS.md/CLAUDE.md 문서 포인터로 발견(이미 tm-onboard가 그 방식). 자동완성 발견성·오버라이드 해석은 v0.2 이월.
- **[P0-6]** credentials 팀 공유 = **각자 입력**(Jane 결정). 팀당1회 자동공유 = v0.2 이월. SPEC §7.5 문구 하향. (E/H)
- **[B0 신설]** 새 쓰기 표면(MCP 실경로·실 스킬 디렉토리·credentials 실경로) conftest 가드 확장 + **발화 실증**을 L2-B보다 먼저. (L1-0 패턴, 과거 dotfile blind spot 동형)
- **[P1]** install-mcp/skills wire는 문장이 아닌 **구조**(`run_adapter` 다동사 확장 + 동사별 게이트 검사). auto-commit `add -A` 무인빌드 위험. Codex `PreToolUse:null`로 confirm-action 차단불가 → warn 실측. credentials 마스킹 테스트 강제. config 확장가능 object. action_map 예약필드 격하. issue 동사 altitude 경계.

---

## 안전 철칙 (L1/W 계승 — 무인 빌드 비협상)

- **호스트 무오염**: 실 `~/.claude`·`~/.claude.json`(MCP)·`~/.bashrc`·실 git config·실 자격증명·실 스킬 디렉토리 절대 무접촉. 변형 테스트는 `tmp` + fake HOME(`monkeypatch`) + `--settings` 격리.
- **B0 가드 선행**: 새 쓰기 표면을 conftest 가드가 **실제로 덮고 발화하는지** 실증 못 하면 L2-B 이후 무인 빌드 금지(과거 blind spot 교훈).
- **팀 루트 `--root` 명시만**(P1), credentials·심링크 타깃 추측 금지(realpath 검증).
- **빈 슬롯 = 1급 시민**(§7.2): 미연결 슬롯이 설치·세션·sync를 실패시키면 안 됨. 생략 + `[info]`.
- **푸시/PR 금지** — Jane 판단. 슬라이스마다 conventional commit + Co-Authored-By, 푸시 안 함. `do_commit(push=True)` 무인빌드 중 금지(단언 테스트).
- **자기보고 불신**: Codex exit 0 불신 → 산출물 직접 읽기 + pytest 실측. 생산자≠검수자.

## 부록 B 미결 스키마 — v2 확정 결정 (검수 반영)

- **B-1 `providers/<name>.json` 스키마**(검수 P1 반영): `{ provider, token_guide:{url,steps}, default_scope, auth:"api_key|oauth|bot_token", services:[역할], resource_fields:[…], mcp:{register_hint}, action_map:{…(예약)} }`.
  - `provider` 단일 필드 + **"정규 서버명 == provider 항등" 불변식을 스키마 검증으로 강제**(§2.5). `canonical_server` 별도 분리 안 함(v0.2 다중인스턴스 때 도입). 접은 대안: provider/canonical_server 분리(v0.1 검증불가 자유도 누출).
  - `action_map`은 **v0.1 죽은 필드**(소비자 부재 — `adapter._compile_match`는 §2.5 정규서버명 직사용, 역할매처는 §7.3 "예약"). → **예약 필드: 존재 시 shape만 검증, 컴파일 소비 테스트 금지**(가짜 테스트 방지).
  - `resource_fields` 신설: 이 provider가 config에 요구하는 인스턴스 필드 선언(예: notion→`["database_id"]`, slack→`["channel_id"]`). config 검증이 이걸 읽음(B-2 연동).
  - `default_scope`·`auth`: §5.4가 provider마다 scope성향·연결방식(api_key/oauth/bot_token) 고정 → 데이터로 둬야 tm-connect가 하드코딩 안 함(§7.3).
- **B-2 services 상수 스키마**(검수 P1 반영 — 분리선 재조정): config `services.<역할>` = **확장 가능 object** `{ provider, scope, <provider팩 resource_fields가 요구한 인스턴스 값> }`. "최소 {provider,scope}" 폐기 — Notion DB ID·Slack 채널·GCal 캘린더 같은 **인스턴스 값은 config 소관**(§5.3 "config 기록"). provider팩=요구 필드 스키마, config=채운 값. v0.2 무중단 위해 열린 object.
- **B-3 credentials 위치/공유**(Jane 결정 = 각자 입력): 저장 = 멤버 로컬 `$XDG_DATA_HOME/teammode/credentials/<team>.json`(0600). **팀 자동공유 메커니즘 v0.1 미구현 — 각 멤버가 자기 토큰 직접 입력**(팀 scope도 v0.1은 "각자 1회"). §7.5 "팀당 1회 자동공유"는 v0.2 이월(SPEC 문구 하향). 근거 정정: XDG 선례는 `XDG_STATE_HOME`(last-pull)이나 credentials=비밀이므로 `XDG_DATA_HOME` 채택. git 추적 금지 + 평문 토큰 stdout/로그/예외 누출 금지(마스킹 테스트).
- **B-4 issue 동사 altitude**(검수 P1 — 경계 명문화): 엔진 `issue` 동사는 **정규 입력 스키마를 stdout JSON으로 echo까지만**. services에서 issues 슬롯 provider 확인(context 동사와 같은 altitude). **action_map 해석·페이로드 변환 금지**(그건 어댑터/스킬 몫 — §3 "엔진은 판단 안 함"). 빈 슬롯 `[info]`+exit 0. ⚠️ 동사 8번째 추가 = §3 동사목록 변경 = **minor bump**(SPEC §3·§0.4 동시 갱신).
- **B-5 tm-connect 분리**(타당): tm-onboard = "제안+트리거"(§5.3 4단계), tm-connect = "실행". tm-connect는 `requires` 없이 항상 설치(연결안내가 목적). B-3 각자입력 흐름에 맞춤.

---

## 슬라이스 분해 (의존성 순서 — 같은 레포 직렬)

> 공통 합격 게이트: ① 신규 테스트 RED→GREEN ② 기준선 무회귀(직전 누계 전부 pass, **+ 의도된 갱신/회귀 구분**) ③ 적대적 검수 "수정할 내역 없음" ④ 해당 골든 GREEN(있으면) ⑤ conventional commit.

### L2-B0 — 가드 선행 (최선행, P1-6/안전 P0 반영 — L1-0 패턴)
- B0.1 conftest `_GUARDED`/`_CONTENT_GUARDED`에 추가: (a) claude MCP 등록 실경로(`~/.claude.json` 등 실제 등록 파일), (b) 실 스킬 디렉토리(`~/.claude/skills/`·codex 상당), (c) credentials 실경로(`$XDG_DATA_HOME/teammode/credentials`). codex `~/.codex/config.toml` MCP 섹션 변경도 잡히는지 포함.
- B0.2 **가드 발화 실증**: 각 새 경로에 쓰기 시도 → 가드가 실제로 막는다는 격리 테스트(과거 dotfile suffix=="" 처럼 안 도는 blind spot 차단). XDG 격리 autouse.
- B0.3 `.gitignore`에 방어 패턴: `*credentials*.json`·`*.token`·`*secret*`·`team.config.local.json`.

### L2-A — provider 팩 기반 (B-1/B-2 v2 반영)
- A.1 `infra/providers.py`: load·validate·lookup. **항등 불변식 강제**(provider==정규서버명, 위반 reject). action_map 예약(shape만).
- A.2 Tier1 4종 `providers/{linear,slack,notion,google}.json`: token_guide·default_scope·auth·services·resource_fields(notion→database_id 등).
- A.3 config services 확장 object 스키마(B-2) + `team.config.example.json`(빈 슬롯 + 4역할 + **인스턴스 필드 자리** 주석). **토큰키(token/secret/key/password) 팀루트 추적파일 진입 거부 린트**(P0-4 — .gitignore보다 강제력).
- A.4 테스트: 스키마검증(정상/누락/오타/항등위반), 4종 로드, 빈슬롯·부분채움 유효성, **"채운 슬롯 인스턴스필드 누락"**(notion인데 database_id 없음) 케이스, 토큰키 린트 발화. lint 범위 = 스킬본문·manifest(providers/*.json은 데이터라 제품명 허용).

### L2-A2 — 멤버 역할 (Jane 결정 2026-06-16: config.members 배열)
> 부록 B 미결 "members.md 역할 필드(§1.1)"를 config 쪽으로. members.md엔 역할 칸이 선언만 돼있고 실제 미저장(이름+id만)이었음. 의존: A(config 스키마). 착수는 A 안정화 후, 순서 유연(B~H 어디든 config 의존만 충족하면).
- A2.1 config `members: [{name, role}]` 스키마(확장 object — role 권장어휘 or 자유문자열). config_is_valid에 members 블록 검증(있으면; 빈배열·없음 valid). 비-도입자 키 오염 안 생기게.
- A2.2 ⚠️ **충돌 해소(착수 전 Jane 확정 필요)**: config는 "도입자 쓰기·팀원 읽기" 원칙인데 멤버 역할은 멤버 속성 → (a) 도입자 일괄 선언 vs (b) 각 멤버 install 시 자기 엔트리만 config.members upsert(원칙 완화). members.md(identity)와의 역할 단일소스 관계도 정리.
- A2.3 활용(죽은필드 방지): `context` 출력에 멤버별 역할 표시(v0.1 최소가치). 역할별 훅/맞춤주입은 v0.2.
- A2.4 테스트: members 스키마검증·context 역할표시·충돌해소 동작·기존 config 무회귀.

### L2-B — install-mcp 어댑터 + 빈슬롯 sync (§2.8 + P0-3)
- B.1 claude `adapter.py install-mcp`: services 읽어 연결 provider MCP 등록, `resolve_server_alias`(항등). 멱등.
- B.2 **빈슬롯 sync 교정(P0-3)**: 어댑터 sync가 `team.config.json services`를 읽어 **MCP 매처의 provider 슬롯 미연결 시 등록 생략 + `[info]`**(§2.9/§7.2). install-mcp 미선행 시 해당 매처만 `[warn]` 생략(§2.7, info와 구분). **L1 기존 미준수(linear 빈슬롯 PreToolUse 매처 등록) 동시 교정.**
- B.3 codex `install-mcp`: 상속 + Codex MCP 등록(TOML) 재정의. 한계 정직 표면화.
- B.4 테스트: 등록/빈슬롯`[info]`생략/install-mcp미선행`[warn]`생략(두 경로 분리)/멱등/별칭/제거/크로스에이전트. **착수 전 `grep -rn "linear\|PreToolUse\|confirm-action\|create_issue" tests/`로 L1 영향 전수 → 의도된 갱신 vs 회귀 구분**(L1-A 패턴).

### L2-C — install-skills 어댑터 — **삭제(v0.2 이월, Jane 결정 2026-06-16)**
> v0.1 스킬은 AGENTS.md/CLAUDE.md 문서 포인터로 발견(tm-onboard가 이미 그 방식). install-skills 메커니즘(심링크 설치·오버라이드 해석·requires 게이트)은 자동완성 발견성 개선 시점(v0.2)에. → P0-5 traversal 가드도 함께 소멸. MCP 표기 lint(스킬본문 `mcp__*`·제품명 직표기 금지)는 H.3 정합성 검수로 흡수.

### L2-D — install.py wire 통합 (P1 — 구조)
- D.1 **`run_adapter` 다동사 확장**(`(agent, verb, flag, path)`): wire가 install-mcp → sync(--on) 순 호출(install-skills 제외 — 삭제), **동사별 게이트 검사**(`--yes`/`--settings`를 sync용 path 암묵재활용 금지). 빈슬롯·미연결 전부 비치명.
- D.2 에이전트별 독립실패 = exit3+stderr, 성공분 롤백 안 함(L1-C 계승). 멱등.
- D.3 테스트: **`--settings` 격리모드에서 실 MCP 등록경로 0바이트 무접촉 실증**(부재→부재 단언). 골든 갱신(빈→L1→슬롯연결).

### L2-E — credentials 금고 (각자입력, §5.4 v0.1 + 안전 P0/P1)
- E.1 `infra/credentials.py`: 저장·조회·삭제(B-3, 0600). **각 멤버 자기 토큰 입력**(팀 자동공유 없음). git_ops 패턴의 stderr detail 노출 주의 — 토큰 마스킹.
- E.2 conftest 가드(B0에서 선행 추가됨) 재확인 + XDG 격리.
- E.3 테스트: 저장/조회/삭제·**0600 stat 실측**(umask 의존 금지)·**토큰 센티넬이 예외/stdout/로그에 부분문자열로도 안 나옴**(적대 grep)·실경로 무오염.

### L2-F — issue 동사 + 시나리오 03 (B-4 + P0-1/P0-2)
- F.1 엔진 `issue` 동사: **verb=`issue`, 첫 positional=서브액션(`create`), `--root`가 그 사이 삽입돼도 정상 파싱**(P0-1). issues 슬롯 provider 확인 후 **정규 입력스키마 echo까지만**(action_map 해석 금지). 빈슬롯 `[info]`+exit 0. 화이트리스트(§3:366) 준수 + 페이로드 셸/JSON 인젝션 면역(V.4 회귀락 계승).
- F.2 **시나리오 03(P0-2)**: 연결된 issues fixture를 **03 스텝 내에서만** 세팅 후 teardown 원복(또는 03 격리 root) → 04·05 무회귀. deterministic 결정성(§6.3) 유지 위해 fixture를 시나리오 정의에 포함(환경따라 GREEN/skip 갈리지 않게).
- F.3 테스트 + 03 GREEN 전환 + 04/05 무회귀 단언. SPEC §3 동사목록+spec_version minor bump.

### L2-G — 미구현 훅 2개 (부록 A.3 + 안전 P1)
- G.1 `infra/hooks/auto-commit.py`(PostToolUse/file_edit): 정규스키마만, teammode 활성 시만, 실패 비차단. **`add -A` 전체스테이징 위험 — 정규스키마 지목 파일만 스테이징**(do_commit에 path 인자) + 토큰패턴 파일 제외(P0-4 연동). **무인빌드 중 발동 방지**(빌드 격리에서 .acme-active off) 게이트.
- G.2 `infra/hooks/confirm-action.py`(PreToolUse/linear create_issue): strict 차단 시맨틱. **Codex는 `PreToolUse:null`로 차단 불가(§2.11) → `enforcement:block` warn 필수 + 무음누락0 실측**(warn 출력 단언). linear 빈슬롯이면 미등록(P0-3 정합).
- G.3 manifest 정합(선언↔파일) 회복. 테스트(/tmp 격리, 발동·비차단·차단시맨틱·Codex warn·빈슬롯 미등록·push=True 안넘김 단언).

### L2-H — tm-connect 스킬 (§5.4, B-5)
- H.1 `infra/skills/base/tm-connect/SKILL.md`: 연결 안내(progressive, 역할어휘만, token_guide·auth 데이터 읽어 안내, **각자 자기 토큰 입력** 흐름). credentials 금고 경유. **발견 = AGENTS.md/CLAUDE.md 포인터**(install-skills 삭제 — tm-onboard와 동일 방식).
- H.2 tm-onboard = "첫가치 직후 L2 제안+트리거"(§5.3 4단계), 실행은 tm-connect(기존 "준비 중" 교체). 평문금고 경고(Syncthing/동기화폴더 금지). AGENTS.md/CLAUDE.md에 tm-connect 포인터 추가.
- H.3 정합성 검수(스킬 lint·MCP 표기 `mcp__*` 금지·역할어휘 — L2-C에서 흡수).

### 마감 (사람 몫 — 에이전트 금지)
- [ ] 푸시/PR 판단 (Jane)
- [ ] native 윈도우 실테스트 (Jane — L2 독립, L1만으로 가능)
- [ ] SPEC 갱신: 부록 A.3 닫힌 갭→A.1 이동, §3 issue 동사 + spec_version minor bump, §7.5 "팀당1회" v0.2 이월 문구
- [ ] (v0.2 이월) **install-skills(스킬 자동설치·`/tm-` 발견성·오버라이드 해석)**, 팀 토큰 자동공유, OS 키체인, action_map 역할매처 컴파일, 주입 스케일 분기 §1.6, workday timezone 주입

---

## L2 범위 밖 (의도적 이월 — 부록 A.3 중 안 닫는 것)
- 주입 스케일 분기(§1.6 ~4인/5인+) — v0.1 자동검사 대상 아님, L2 비범위.
- lint K3·K5~K8 — L2는 K4(manifest)+K7(스킬정규형)+제품명/토큰 린트만 확장.
- workday timezone 주입(§1.4 team.timezone) — L2 비범위.

## 워커 오케스트레이션
- 직렬 B0→A→B→D→E→F→G→H (C 삭제, 같은 레포 충돌 회피).
- 슬라이스별: Codex 구현 → 메인 pytest 실측 → 적대검수(Claude, "구현자 불신·실행증거") → 반영 → "수정 없음" → commit.
- 이종 교차: E(credentials)·F(issue altitude)·B(빈슬롯 sync)는 Codex 생산 ↔ Claude 검수.
- 추적: 이 파일 체크박스 + BUILD-LOG.md 슬라이스별 기록.
