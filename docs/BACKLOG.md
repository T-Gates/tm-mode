# 백로그: KB 쓰기 거버넌스 (tm-mode L1 메모리 차별점)

착안: 2026-06-15 (acme-toolkit 실작업 중 체감). 상태: **설계 메모, 미구현 — 다음에 구현**.

## 문제
`memory/` 파일을 직접 `Edit`/`Write`하면 INDEX 갱신·커밋·알림 등 일관 절차가 누락된다. 실제로 acme 작업 중 서브에이전트·메인이 직접 Edit해서 매번 절차가 빠지는 일이 반복됨. → "팀 메모리는 동사(스킬)로만 쓴다"를 **강제**할 필요.

## 해법 (옵션 B = 플래그형, 채택)
`memory/` 직접 쓰기를 막고 **메모리 관리 스킬 경유 쓰기만** 허용.
- **PreToolUse 훅**: `Edit`/`Write` 타겟이 `memory/` 경로 + unlock 플래그 없으면 → `deny` + "관리 스킬을 쓰세요" 안내.
- **메모리 관리 스킬**: 시작 시 unlock 플래그 `touch` → 완료(커밋 후) `rm`.

## 검토한 대안 (접은 이유)
- **A. 경고형** (차단 X, 경고만): soft — 우회 가능, 강제력 없음. 기각.
- **C. 스크립트 단일화** (`memory/` 쓰기를 전용 동사 `kb-write`로만, Edit/Write는 무조건 차단): 가장 견고하나 관리 스킬 대수술 필요. 과함.
- **B 채택**: 의도(스킬로만) 충족 + 구현 가벼움(훅 1개 + 스킬에 touch/rm 2줄).

## ⚠️ 취약점 · 가드
- 핵심 약점: 스킬이 중간에 비정상 종료하면 unlock 플래그가 **잔류 → 영구 unlock**(이후 아무 직접 Edit이나 통과).
- 가드안:
  1. 플래그에 **세션 ID 매칭** 또는 **TTL**(예: 플래그 mtime이 N분 지나면 무효).
  2. (Claude Code 한정) PreToolUse 훅이 직전 `Skill` 호출을 transcript로 확인 — 단 크로스에이전트 이식성 떨어짐. tm-mode는 에이전트 무관이 원칙이라 **플래그+TTL/세션ID가 정답**.

## tm-mode 편입 위치
- L1 메모리 시스템(`spec/01-team-memory.md`)에 "쓰기 거버넌스" 절로 편입.
- 차별점 메시지: **"팀 메모리는 동사로만 쓴다"** = 여러 에이전트·팀원이 만져도 INDEX/커밋/알림 일관성이 코드로 보장됨. L1 delight 후보.

## 구현 시 체크
- [ ] PreToolUse 훅 추가 (`infra/hooks/` + install.sh 등록 + migration 1장)
- [ ] 메모리 관리 스킬에 unlock touch/rm + 플래그 TTL/세션ID 가드
- [ ] 직접 Edit 차단 시 안내문(스킬명·우회금지 사유)
- [ ] conformance 테스트(직접 Edit deny / 스킬경유 allow)


---

## 06 — 메모리 업로드 (노션 → 팀 memory) 설계

> 상태: design (brainstorm 2026-06-16, Jane) / 다음: writing-plans
> 의존: L2(notion provider·install-mcp·docs 슬롯), 엔진 동사 체계(§3), tm-onboard(§5)
> spec_version 영향: 엔진 동사 `memory` 추가 = §3 동사 계약 변경 → **minor bump(0.2→0.3)**

### 1. 정체

**"메모리 업로드" = 연결된 노션 MCP의 페이지를 긁어 주제별로 분류해 팀 memory로 저장하는 기능.**
콜드스타트(세션로그 0인 신규 팀)를 기존 노션 자료로 워밍업한다. tm-onboard에 통합되며, 온보딩이 한 진입점이다.

### 2. 핵심 결정 (brainstorm 확정)

| 항목 | 결정 | 근거 |
|------|------|------|
| 결과물 | 팀 memory 시드(저장) | 일회 요약 아님 — 지속 가치 |
| 저장 위치 | `memory/team/memory/<주제>.md` (신설 영역) | members/sessions로는 "팀 메모리" 담을 곳 없음 |
| scope | **팀 scope** — 도입자 1회 시드 → git 공유, 팀원은 pull로 수령(0회) | memory는 비밀 아니라 git 추적 → credentials가 못 한 팀당1회를 공짜로 |
| 토큰 | **credentials 금고 안 씀** — 에이전트에 이미 연결된 notion MCP를 빌려 읽기만 | tm-mode 토큰 무접촉. 토큰은 MCP 연결 소관(각자) |
| 분류 | AI가 주제별 분류(통째 덤프 X) | "분류는 시킬거" |
| 수집 주체 | **페이지별 fan-out** — 오케스트레이터가 페이지마다 서브에이전트 디스패치(백그라운드 병렬), 오케스트레이터는 취합·정리만 | 노션 트리 양 많음 → 병렬·비차단. L2 빌드 오케스트레이션 패턴 재사용 |
| 트리거 | tm-onboard 통합. 도입자+memory 비어있으면 제안. 나중 재실행으로 추가 | "온보딩 통합", "메모리 업로드 기능으로 추가" |
| 범위 | 준 링크 + 관련 하위 "쭉" 긁기, 도입자에게 "이만큼" 확인 | "정보 쭉 긁어와서" |

### 3. 컴포넌트 (tm-mode 철칙: 엔진=기계, 스킬=판단)

#### 3.1 엔진 동사 `memory` (기계적 — `log` 형제)
```
teammode.py memory --root <팀루트> --topic <주제> --text <내용> [--source <노션URL/제목>]
```
- `memory/team/memory/<주제>.md` 생성/이어쓰기. 주제 파일명은 author 검증과 동형(경로 traversal·footgun 차단).
- frontmatter: `topic`, `source`(출처 노션 링크/페이지), `updated`(작업일 06시컷). 출처 명시 = "외부유래" 표시.
- `memory/team/memory/INDEX.md`(또는 기존 INDEX)에 주제 등재(기계적).
- **요약·분류 안 함**(§3 "엔진은 판단 안 함") — 받은 `--text`를 그대로 저장. 분류·요약은 스킬이 끝낸 결과.
- 멱등: 같은 주제 재호출 = 이어쓰기/갱신(append vs replace는 §6 미결).

#### 3.2 tm-onboard 확장 (오케스트레이터 — 페이지별 fan-out)
- 온보딩 흐름: `L1셋업 → context(첫가치) → docs 슬롯(notion) 연결(tm-connect) → [신규] 메모리 시드 제안`.
- 도입자 + `memory/` 비어있음 → "연결된 노션에서 팀 메모리 가져올까요?" → **오케스트레이션**:
  1. **오케스트레이터(tm-onboard)**: notion MCP로 트리 구조/페이지 목록만 가볍게 파악(본문 X). 도입자에게 "페이지 N개 가져올게요" 확인.
  2. **페이지별 fan-out**: 페이지마다 서브에이전트 **백그라운드 병렬** 디스패치. 각 서브에이전트 = 자기 페이지 1개 본문 읽기 → 요약 + 주제 태깅 → 구조화 결과 반환(파일 직접 안 씀).
  3. **오케스트레이터 취합·정리만**: 서브에이전트 결과들을 주제별로 묶어 → 주제마다 `memory` 동사 1회 호출(저장). 오케스트레이터는 노션 본문을 직접 안 읽음(서브에이전트가 함) — 정리·저장 지휘만.
  4. 온보딩 메인 흐름은 안 막힘(백그라운드), 완료 시 "N개 주제 시드됨" 안내.
- `memory/` 이미 있음(팀원·재온보딩) → 수집 skip + "팀 메모리 N개 있음"(context가 표시).

### 4. 데이터 흐름

```
도입자 온보딩
  └ docs 슬롯 = notion 연결 (install-mcp, 토큰은 그 MCP 소관)
      └ tm-onboard(오케스트레이터): notion MCP로 페이지 목록 파악 → "N개 가져올게요" 확인
          ├ 페이지1 → 서브에이전트A (백그라운드) ─┐ 각자 본문 읽기→요약+주제태깅
          ├ 페이지2 → 서브에이전트B (백그라운드) ─┤ → 구조화 결과 반환
          └ 페이지N → 서브에이전트… (병렬)      ─┘
              └ 오케스트레이터 취합·정리(본문 직접 안 읽음)
                  └ 주제마다 `teammode.py memory --topic X --text ... --source ...`
                      └ memory/team/memory/X.md + INDEX 등재
                          └ git commit + push (도입자)
팀원 온보딩
  └ git pull → memory/ 자동 수령 → 수집 skip, context에 "팀 메모리 N개" 표시
```

### 5. 온보딩 분기 (install.py role 판정 + memory 존재)

| 상황 | 동작 |
|------|------|
| 도입자 + memory 비어있음 | 노션 연결됐으면 "메모리 시드?" 제안 → 서브에이전트 수집 |
| 도입자 + 노션 미연결 | "노션 연결하면 메모리 시드 가능" 안내, skip |
| memory 이미 있음(팀원/재온보딩) | 수집 skip + "팀 메모리 N개" 안내 |
| 온보딩 후 "이 노션 메모리 추가해" | 같은 수집 경로 재실행(서브에이전트) — 새 주제 추가/기존 갱신 |

### 6. 미결 (writing-plans 전 확정)

- **같은 주제 재업로드**: append(누적) vs replace(덮어쓰기) vs 병합. → 멱등성·중복 방지 관점에서 결정.
- **주제 분류 카테고리**: 고정 어휘(제품/로드맵/용어/결정/회의) vs AI 자유 주제명. 자유면 파일명 정규화 규칙.
- **노션 범위 상한**: "쭉" 긁되 페이지 N개/깊이 상한(폭주 방지)? 도입자 확인 UX.
- **INDEX**: 기존 `memory/INDEX.md` 재사용 vs `memory/INDEX.md` 별도. (자가유지 설계의 INDEX 거버넌스와 정합 필요 — INDEX 자동쓰기 merge churn 주의.)
- **권한**: 팀원도 메모리 업로드 가능(git 추적이라 누구나 add 가능)? 도입자 중심? 충돌은 git_ops ff-only.
- **스킬 발견**: tm-onboard 통합이라 별도 스킬 발견 불요. 단 "메모리 추가해" 재실행 트리거를 tm-onboard description에 넣을지.

### 7. 테스트 전략

- 엔진 `memory` 동사: 파일 생성·이어쓰기·INDEX 등재·frontmatter·경로 traversal 차단·멱등 (tmp 격리, log 동사 테스트 패턴).
- conformance 골든: "메모리 시드 → memory/ 파일 존재 → context에 표시" 시나리오 추가(06).
- 온보딩 분기: 도입자/팀원/memory유무 (install golden 패턴, --settings 격리).
- 서브에이전트 수집은 notion MCP 모킹(실 노션 무접촉) — 픽스처 페이지 → 분류 → memory 호출 검증.
- 호스트 무접촉: 실 노션·실 memory 무접촉, tmp+monkeypatch.

### 8. 범위 밖 (이월)
- 지속 자동 동기화(노션 변경 주기 반영) — 변경추적·충돌 복잡, v0.2+.
- 노션 외 docs provider(구글독스 등) 메모리 업로드 — provider팩 확장 시.

---

## 신규 백로그 (2026-06-17 새벽 — Jane 구상 + 윈도우 실셋업 도그푸딩)

### 기능 (Jane 구상, 우선순위 순)
1. **메모리 스켈레톤 + 채우기 유도** — 메모리 폴더 뼈대를 미리 깔고(얇게, acme-toolkit의 `team/`·`product/`·`decisions/` 구조를 제품특정 빼고 범용화), 에이전트가 칸을 채우도록 유도. ⚠️ 빈 깡통 남발 금지(`.gitkeep`·INDEX 안내 위주), 유도는 ②가이드라인 주입으로(강제 X).
2. **팀모드 가이드라인 파일 세션 시작 주입** — 세션 시작에 "팀모드 잘 쓰는 법" 강령을 INDEX·최근활동과 함께 주입(6/16 AI강령 3계층의 ①). 범용 강령=`infra/`(upstream 소유·update로 갱신) vs 팀 커스텀=`memory/`(팀 소유) **분리**, 매 세션이라 **얇게**, AGENTS.md(셋업 진입점)와 **채널 구분**. session-start.py가 ②③ 이미 주입 → ①만 추가.
3. **툴킷 강력 스킬 이식** — acme-toolkit의 검증된 스킬을 tm-mode로. **on/off 토글 스킬 포함**(엔진 `on`/`off` 동사는 있으나 사용자용 스킬 래퍼 부재): on=pull+맥락주입+배너 / off=세션로그+커밋+push. 어느 스킬을 이식할지 선별 필요.
4. **statusline 팀명** — on 시 상태줄에 팀명 표시. ⚠️ 에이전트별 표면 다름(claude=`settings.json` statusLine command, codex=대안/스킵) → **어댑터 레이어가 처리**(크로스에이전트 어댑터의 좋은 쓰임새). 팀명은 `team.config.json`에서, `--yes` 게이트. **③(on 스킬) 이후** 자연스러움.
   - ✅ **배너 picker 구현 완료**(23886a9): `infra/banners/` 6종(ansi_shadow·slant·chunky·cyberlarge·larry3d·speed) 정적 렌더 + 온보딩 personality에서 선택→`cp`로 `memory/banner.txt` 적용. pyfiglet은 빌드타임만(런타임 의존성 0).

### 도그푸딩 발견 (수정 필요)
- **install cp949 잔존 경로** — 190bfca(`_engine_capture`·`_git`)가 일부만 덮음. 윈도우 `install --yes` verify서 `_readerthread` 트레이스백 재발(비치명, state=on). 남은 subprocess decode 경로 추적·수정.
- **윈도우 Git Bash 경로변환 주의** — 에이전트가 `git show upstream/main:.gitignore` 류에서 MSYS 경로변환(`/`→`\`, `:`→`;`)에 막혀 오판. 문서(AGENTS/SKILL)에 윈도우 Git Bash 주의(`MSYS_NO_PATHCONV=1`) 한 줄.
- **`--team-name` 인자** — install이 team.name을 repo명으로 자동. 팀명 직접 지정 인자 부재(현재는 셋업 후 config 수정).
- **직책/직군 분리 스키마** — 현재 `--role`은 단일 자유필드(예 `팀장/개발`). 직책(팀장/팀원)·직군(dev/pm/design) 분리 스키마.

---

## ~~핫픽스 묶음 — push 후 검증 P1 견고성~~ (2026-06-18, BACKLOG③ 출시 후) — ✅ 완료 (2026-06-18 수정, 2026-07-03 검증·마감)

915fa70 push 후 7-시나리오 병렬 검증 + 윈도우 실설치 도그푸딩에서 나온 견고성 결함. **P0 0**(핵심 안전 traversal·symlink·커밋오염·고아청소·실호스트오염·거버넌스 실발동 전부 정상). 아래는 입력검증/예외처리 P1 — 출시 막을 결함 아니라 묶음 hotfix.

**마감 검증 (2026-07-03, codex 교차검수 2라운드)**: 아래 항목 전부 2026-06-18 커밋으로 이미 수정 완료 확인 — 항목별 SHA 매핑:

### memory 동사 입력 견고성 (수렴 P1) — ✅
- ~~**미처리 예외 → 트레이스백 + exit 1**~~ → ✅ 7aaae60 (write/delete try/except → exit 2 + 친화 메시지, atomic write + INDEX 롤백. 테스트 test_knowledge_p1_hotfix.py). 추가(2026-07-03): delete 존재 사전판정의 `is_file()` 이 stat EACCES 를 False 로 접어 실존 파일을 "파일 없음(멱등) exit 0" 으로 거짓 성공 보고하던 잔여 구멍을 os.stat 직접 판정으로 마감(codex 합의 — 부모 디렉토리 탐색권한 없음 계열에서만 발현, FileNotFoundError 만 멱등 0, 그 외 OS 예외 exit 2).
- ~~**유니코드 author/filename 통과**~~ → ✅ b057dc2 + 2672220 (isascii() 강제 — author·filename·folder 세그먼트 전부). ⚠️ 레거시 주의: isascii 이전 버전/수동 편집으로 비ASCII member 가 이미 있는 팀은 `sessions/<이름>` 디렉토리 rename + `members.md` 수정 필요(읽기는 되지만 그 author 로의 log/write 가 exit 2).
- ~~**content 제어문자 미필터 (P2)**~~ → ✅ 2672220 (Cc/Cf/Cs + 고립 surrogate 거부, \n·\r·\t 만 허용).

### 거버넌스(kb-write-guard) 경미 (P1) — ✅
- ~~**상대경로 fail-closed 여부 경미**~~ → ✅ 933ffaa (S2-1: 상대경로는 CWD 무시·팀루트 기준 join, 판별 실패 None → fail-closed).
- ~~**memory 내부 symlink**~~ → ✅ 933ffaa (S2-2: raw normpath / resolved 양쪽 union containment — 내부→외부 symlink 경유 편집 차단. 테스트 test_kb_write_guard.py).

### 윈도우 도그푸딩 미세 갭 (P2)
- **전역 git identity 빈 경우**: 온보딩에 `git config user.name/email` 안내 한 줄 (AI가 로컬로 설정하게 됨). — 미처리(잔존)
- ~~**`install.py --help`가 `--root` 요구 → exit 2**~~ → ✅ 256cb77 (--help/-h 손파싱 우선 처리, root 없이 exit 0. 테스트 test_install_s3_help.py).
- **PowerShell git stderr 빨강 래핑**: 윈도우 특유 비치명 — 문서에 주의 한 줄(선택). — 미처리(잔존)
- (참고) install cp949 잔존 subprocess decode 경로: 190bfca 이후 install.py·install_lib.py·git_ops.py·normalize.py 의 모든 subprocess 호출에 encoding="utf-8", errors="replace" 명시 확인(2026-07-03 전수 grep) — 잔존 없음. 테스트 test_cp949_encoding.py.

### memory 동시 write race (P2, 백로그 — dev-cycle 2차 검수서 codex 적발)
- atomic write 반영 후, 같은 topic 동시 write 중 한 writer 의 INDEX 실패 롤백이 다른 writer 가 방금 쓴 파일을 삭제/과거 내용으로 복원할 수 있음(race, fault injection 확인).
- **접은 이유**: teammode memory 는 단일 CLI 순차 사용 모델 — 동시 same-topic write 비현실적. lock 도입은 P1 핫픽스 범위 과대. 실제 동시성 요구 생기면 topic/folder 단위 lock 또는 롤백 전 "내가 쓴 내용인지" 비교 후 unlink/restore.

> 출처: 2026-06-18 push 후 검증·윈도우 end-to-end 도그푸딩 (세션로그 jane-doe 2026-06-18). 핵심 안전은 전부 통과, 위는 견고성/UX 개선분.

## 스킬 구성 개선 (2026-06-20 Jane 착안, 도그푸딩 중)
- ~~**tm-reset 스킬 삭제**~~ — ✅ 완료(2026-06-20 제거). 되돌리기 기능은 `python infra/install.py --uninstall --root . --yes` 직접 실행으로 유지.
- **tm-manage-utils → 커스터마이즈 스킬로 전환** — 유틸 설치/제거 관리 대신 "팀별 커스터마이즈" 용도 스킬로 재정의.
- (출처: acme→tm-mode 마이그레이션 도그푸딩 중 발견. 상세 설계는 tm-mode 본격 작업 시.)

## personality 완료판정 결함 (2026-06-20 도그푸딩 발견 — Jane)
- tm-onboard 체크표의 "팀 personality = greeting/farewell 기본값과 다름" 판정이 **코드 미구현**. install_lib에 비교 로직 없음 → 스킬 마크다운 기준만 보고 **에이전트가 임의 판정 → 오탐**(마이그레이션 중 팀명만 Acme로 바꿨는데 personality ✅로 잘못 잡힘. 실제론 greeting이 기본 공식 그대로).
- 게다가 기본 greeting이 `install_lib.py:534 f"{team_name} 팀모드 ON"`으로 팀명 포함 → "기본값"이 팀마다 달라 고정문자열 비교 불가.
- **개선**: teammode.py context(또는 doctor)가 `personality_customized` 플래그를 결정적으로 뱉게. 기본 공식 `f"{name} 팀모드 ON"`·`f"수고하셨습니다 — {name}"`과 정확 비교 + `banner.txt` 존재 여부로 판정. 체크표 항목은 코드가 판정, 에이전트 임의판정 금지.

## tm-customize 스킬 신설 + 스킬 재구성 (2026-06-20 Jane, 도그푸딩 중 확정)
tm-mode 스킬 셋 재편 — "커스터마이징"을 한 스킬로 통합:
- **신설 `tm-customize`**: 팀 personality(배너 picker · greeting/farewell 멘트) + 기존 tm-manage-utils(유틸 스킬 관리) 흡수. "팀색 입히기"를 한곳에.
- **tm-onboard에서 personality 커스텀 절차(배너·멘트) 제거** → 온보딩은 L1 가치 + 서비스(L2) 제안에 집중, 길이 단축. 체크표의 personality 항목도 제거하거나 "tm-customize로" 안내만.
- **tm-manage-utils → tm-customize로 전환/흡수** (위 마이그레이션 착안 통합).
- ~~**tm-reset 삭제**~~ — ✅ 완료(2026-06-20 제거, 별도 항목 위).
- 연계: 위 "personality 완료판정 결함"도 tm-customize 쪽에서 결정적 판정으로 해소.

## ~~흡수 경로 탐지 결함 — check-mcp가 _teammode_managed만 인식~~ (2026-06-20 도그푸딩 — Jane) → ✅ 해소(2026-07-03, #3)
- **해소 방식**: `--check-mcp` CLI 는 스킬·스펙 어디서도 호출하지 않는 사문이라 **제거**, "역할 provider와 매칭되는 기존 MCP 감지"는 어댑터 `install-mcp` 내부로 흡수(#3). placeholder 대상 provider(slack/google 등)는 등록 전에 사용자 서버(키 토큰·url·command 매칭)를 감지해 placeholder 대신 `[info] 기존 서버 발견 — tm-<provider> 별칭으로 재사용하려면 …` 안내를 낸다(자동 채택은 소유권 마커 부재로 금지 — 사람 확인).
- (원 기록) tm-connect §1② 흡수 = "팀이 이미 다른 경로로 연결한 서비스 탐지"인데, 구 check-mcp 는 `_teammode_managed` 만 connected 판정 → tm-mode가 등록한 MCP만 인식. 기존 비-tm-mode 연결(acme-toolkit이 등록한 linear/slack/notion MCP)을 흡수 대상으로 탐지 못 함. 그 linear MCP args가 `acme-toolkit/infra/mcp/linear-mcp/dist/server.js` 의존 → acme 정리 시 깨짐.

## 온보딩 발견성 + "왜+다음" 침묵 — tm-mode 전반 불친절 (2026-06-20 도그푸딩, Jane 핵심지적)
"불친절"의 정체 = 자동화 부족이 아니라 **발견성·피드백 부재**:
- **KB 발견성**: 메모리(KB) 개념은 README §기둥②에 있으나 **tm-onboard에 소개 0** → 새 팀원은 KB 존재를 모르고, 첫 마주침이 kb-write-guard 차단("memory 직접편집 금지→memory write 경유")인데 "왜/KB가 뭔지"가 없음. 문서엔 있으나 필요한 순간엔 없음.
- **침묵하는 실패(공통 뿌리)**: ~~check-mcp `{"connected":false}`(왜인지 X)~~(check-mcp 는 2026-07-03 제거 — install-mcp 감지 안내가 "왜+다음 액션"을 냄)·훅 직접실행 무출력·personality 오탐(✅로 거짓보고)·흡수 막다른길. 전부 "왜+다음 액션"을 안 알려줌.
- 구분: 토큰발급·핸들러 커밋확인 같은 **의도된 보안 마찰은 유지**(없애면 위험), 단 "왜 필요한지" 안내로 부드럽게.
- 개선(P0급 UX):
  ① tm-onboard에 "기둥② 메모리(KB)" 한 단락 + tm-memory/tm-manage-memory 존재 안내 (L1·L2처럼 progressive 소개)
  ② kb-write-guard 차단 메시지에 KB 개념·이유 1줄
  ③ **실패/차단/빈슬롯 출력에 "왜 + 다음 액션" 원칙** 전반 적용 (MCP 는 install-mcp 의 기존 서버 감지 안내로 일부 적용됨 — 2026-07-03)
  ④ 상태 보고 정직성 (personality 등 오탐 제거 — 위 결정적 판정 항목과 연결)

## 배너 기본값 부재 + config 비동기 (2026-06-20 도그푸딩, Jane)
- **기본값 초라**: infra/banners/ 에 ASCII 아트 6종(ansi_shadow·slant·speed 등) 있으나, banner.txt 없으면 자동 배너가 `=== <팀> ===` **한 줄 텍스트**. 신규 팀 첫인상이 빈손 — 멋진 배너는 picker로 일부러 골라야만 나옴(발견성 0). "발견성/친절성"과 같은 뿌리.
  - 개선: install scaffold가 기본 폰트(예: ansi_shadow) banner.txt를 박아두거나, 자동 배너 자체를 ASCII 렌더로. personality 커스텀 = "기본에서 바꾸기"여야지 "빈손 채우기"가 아니게.
- **배너↔config 비동기**: banner.txt 있으면 무조건 우선 → team.name 바꿔도 배너 안 따라옴(마이그레이션 시 acme 잔재 배너가 그대로 노출). 위 personality 결정적판정 항목과 연결 — 배너 출처가 banner.txt면 team.name 변경 시 경고하거나, 자동 배너는 team.name 추종.

## UX 포팅 — acme엔 있고 tm-mode가 안 가져온 것들 (2026-06-20 도그푸딩, Jane)
메타: acme→tm-mode 마이그레이션이 엔진(L1)만 가져오고 UX/첫경험을 안 가져옴. 재발명 말고 **포팅**.
- **tm on 출력 포팅**: acme on(acme SKILL.md:39-65)은 [환영 + 📊팀원별 상태(🔧하는일/⏭다음/🚧막힌것, 세션로그+Linear In Progress) + 📋지난성과 3~5줄 + 📅일정(Google Cal 오늘~+3일) + 멤버 이모지 🌙Jane/😛Jonathon/👽Jonathan]. tm-mode tm on은 배너+greeting+summary 한줄 → 빈약.
  - L2 불요: 환영·팀원별 상태·지난성과(세션로그 기반). L2 필요: Linear In Progress·Google Calendar 일정.
  - 구현: tm SKILL.md ON §3-4를 acme식 웰컴 포맷으로. 이모지는 members.md/team.config.
- **statusline 포팅**: acme는 statusline-command.sh:52 `[Acme]`(노란 하드코딩) + `.acme-active` 마커 조건부. tm-mode: ①statusline-command.sh에 `.teammode-active` 체크 추가(개인 statusline 유지·병합) ②team.config team.name 동적 읽기(bash+python3) ③settings.json TEAMMODE_HOME 배선(미구현) ④마커 우선순위 tm-mode>acme. teammode.py 마커는 이미 구현(:43,:1446).
- 이 묶음 전체가 "친절성·가시성 P0 테마". 계획 에이전트 로드맵에 누락됐으니 다음 세션 P0 묶음으로 신설.

## tm-customize 동작 수정 — 오버라이드 레이어 설계 (2026-06-20 Jane+논의)
tm-customize에 "동작/훅 수정" 영역 추가. 단 코어 직접 패치는 upstream pull 충돌 → 오버라이드 레이어로.
- **범위 3층**:
  1. 표면 personality (배너·greeting/farewell) — 값만, 안전
  2. 유틸 스킬 추가/제거 (tm-manage-utils 흡수)
  3. **동작/훅 수정** — 위험, 아래 3중 가드 필수
- **동작 수정 3중 가드**:
  ① **서브에이전트 컨텍스트 풀로딩** (Jane 제안) — 이 영역 선택 시 서브에이전트가 레포 구조·계약을 빠삭히 적재한 뒤 작업(무지성 훅 수정 방지).
  ② **오버라이드 레이어** — `infra/`(코어, upstream 관리)는 불변. 팀 전용 `team-overrides/`(훅 대체·확장, config 동작 플래그)에만 작성 → upstream pull 공존(게임 mods/ 원리). **코어 직접 패치 금지**.
  ③ **테스트/conformance 게이트** — 수정 후 검증, 깨짐 방지.
- **설계 미결**: 오버라이드 레이어 로딩 메커니즘(엔진이 team-overrides/ 훅을 코어보다 우선 로드?), config 동작 플래그 스키마, upstream 충돌 감지. → spec/writing-plans 전 확정 필요.

## ~~tm-customize 팀 페르소나(톤·캐릭터) 영역 추가~~ — 접음 (2026-06-21 Jane)
**결정: persona 영역 제거.** tm-customize에서 페르소나 축을 아예 뺐다(references/persona.md 삭제, 라우터·트리거·테스트 정리).
- **접은 근거**: persona는 SessionStart 상시주입을 안 하던 어정쩡한 축(매 세션 톤 강제 회피 vs 외부메시지만 적용 사이에서 미결)이라 가장 안 쓰일 물건이었다. tm-customize는 배너 + util 스킬로 좁힌다.
- **유지**: 표면 `personality`(배너·greeting/farewell, 엔진 출력)는 그대로 — persona와 별개다.
- 되살릴 일 있으면: 강도/범위(전체 응답 vs 외부메시지만)·멤버별 오버라이드 허용 여부가 당시 미결이었음.

> 후속: 정체성(팀명·greeting·farewell) 커스텀 축 추가 완료 — 아래 "정체성 커스텀 축" 항목 참조.

<details><summary>(접힌 원안 — 2026-06-20)</summary>

tm-customize 범위 4층으로 확장 — persona 추가:
1. 표면 personality (배너·greeting/farewell) — 엔진 **출력**
2. **팀 persona (톤·캐릭터, 예: 토깽이톤)** — 에이전트가 그 톤으로 **말하게**. ↓
3. 유틸 스킬 추가/제거
4. 동작/훅 수정 (오버라이드 레이어 — 위 항목)
- **persona ≠ personality**: personality는 엔진이 출력(배너·멘트), persona는 에이전트 행동 지침(말투·캐릭터).
- 저장: team.config 또는 `memory/team/persona.md`(팀 공유, 도입자 작성). 적용: SessionStart 훅/맥락 주입에 "이 팀 페르소나: …" 포함 → 에이전트가 그 톤으로 응답·메시지.
- L2 연동: chat(슬랙) 등 외부 메시지에서 특히 의미(예: 토깽이톤 슬랙 공유). L1 내부 응답 톤에도 적용 가능.
- 미결: persona 강도/범위(전체 응답 vs 외부메시지만), 멤버별 오버라이드 허용 여부, 토큰 무관(텍스트 지침).

</details>

## ✅ 정체성(팀명·greeting·farewell) 커스텀 축 (2026-06-21 Jane) — 완료
**목표: 팀명·greeting·farewell을 tm-customize에서 언제든 자유롭게 바꿀 수 있게.** → 달성.
- **블로커였던 것**: 크리덴셜 금고가 `team.name`으로 키됨(`credentials/<team>.json`) → 개명하면 토큰 고아.
- **검토했다 접은 안 (team.id)**: config에 불변 `team.id` 도입 후 크리덴셜·MCP를 id 기준으로 전환. **과함** — install·MCP wiring·스키마·마이그레이션·다수 테스트를 건드림. 멀티팀이 고려사항이 아닌데(Jane: "그때 가서 생각") 멀티팀 충돌방지용 식별자 체계를 미리 지는 셈.
- **채택안 (단일 금고)**: `_vault_path`를 팀명 무관 단일 파일 `default.json`으로. team 인자는 시그니처 호환만(파일명에 안 씀). → `team.name`은 순수 표시용 = **파급 0, 언제든 변경.** 훨씬 작음(credentials 1함수 + 마이그레이션 헬퍼 + 테스트). 멀티팀 필요해지면 그때 `_vault_path(team)`로 되살림.
- **한 일**: `credentials._vault_path` 단일화 + `migrate_legacy_vault`(레거시 `<name>.json`→`default.json` 1회 이전, 멱등) + `teammode.cmd_on`에서 비치명 호출 + `references/identity.md` 신설(team.config.json 직접편집, Edit OK) + tm-customize 라우터에 "정체성" 축 + 스펙(skills/internals) 정합 + 테스트(개명안전·마이그레이션 4종, symlink가드 경로 갱신).
- **잔여 참고**: "배너↔config 비동기"(team.name 바꿔도 banner.txt 박힌 ASCII는 자동 추종 안 함) — identity.md에 수동 갱신 안내로 커버. 근본 해소(자동 배너가 team.name 추종)는 별개 항목.

## statusline codex 쪽 누락 — 진짜 발견 (2026-06-20, Jane 끝까지 추적·Jonathan 단서)
초기 조사가 Claude statusLine만 봐서 3번 "없다" 오판. 실제론 **codex** 쪽에 있었음:
- **acme는 codex statusline 건드림**: `infra/hooks/sync.py:196`이 codex `config.toml` hooks 블록에 `statusMessage = "acme hook"` 주입(커밋 `c14e746` "Codex Acme setup support"; 과거엔 "Checking/Saving acme session log"). codex CLI는 훅 실행 시 상태줄에 이 메시지 표시 → **Jonathan(codex 사용자)이 본 게 이것**.
- **tm-mode는 codex statusMessage 안 넣음** — `infra/agents/codex/adapter.py`가 hooks 블록 생성하되 statusMessage 누락. 마이그레이션 시 빠짐. Jonathan이 tm-mode 쓰면 codex statusline 비어있음.
- **할 일 (statusline 두 갈래)**:
  - Claude statusLine = 동적 팀명(오늘 우리 환경에 수동 구현, statusline-command.sh) → install 자동주입 제품화 필요(기존 작업4).
  - **Codex statusMessage = tm-mode codex adapter에 포팅** (acme 원본 있음). 단 "acme hook" 고정 → **동적**(팀명/팀모드 ON, team.config에서)으로 개선.
- 교훈: 에이전트별 statusline 표면이 다름 — claude=`settings.json` statusLine / codex=`config.toml` statusMessage. **둘 다 봐야** 함. 조사 시 한쪽만 보면 놓침.

## statusline 구현 설계 확정 (2026-06-20 크로스OS·에이전트 검토 — Jane 푸시백)
초기 "범용 wrapper 한 방"(settings.json statusLine.command를 tm-mode wrapper로 감싸 개인 원본 호출+팀블록 prepend)은 **크로스OS에서 기각**.
- **기각 근거(셸 미스매치)**: claude statusLine은 윈도우서 Git Bash(있으면)/PowerShell(없으면)로 실행(공식문서 https://code.claude.com/docs/en/statusline.md). wrapper가 개인 원본 command를 subprocess 재실행하면 python `shell=True`=윈도우 cmd.exe ≠ claude의 GitBash/PS → 원본이 `bash xxx.sh`면 깨진다. **원본 재실행을 피하는 경로만** 견고.
- **확정 설계**:
  - **codex**: statusMessage는 config.toml **정적 문자열**(동적렌더 X) → codex adapter sync가 `[<팀명>] 팀모드 ON`을 team.config team.name에서 **동적**으로 박기. wrapper 불요. (acme `statusMessage="acme hook"` 고정 → 동적화)
  - **claude 케이스 분기**: ①개인 statusLine **없음** → tm-mode python statusline 단독설치(`sys.executable`+`io_encoding`, 셸무관 — 전 훅 패턴 일관). ②개인 **있음** → 안 덮고 **정직한 수동안내**(BACKLOG "왜+다음" 원칙). ③멱등+원복(`_teammode_managed` 마커, hooks sync 패턴 모방). 팀명 동적, 하드코딩 금지.
  - **후속 이월**: "개인 bash statusLine에 마커 블록 자동삽입"(우리 현재 수동방식)은 남의 파일 편집·언어의존이라 이번 범위 밖. ②케이스를 수동안내→자동삽입으로 올리는 건 별도 백로그.
- 상태: 코더(sonnet) TDD 구현 착수 — claude adapter + codex adapter + 신규 `teammode_statusline.py` + 테스트. 완료선=기존 1089 + 신규 green.

## statusLine PowerShell call operator 셸분기 (미지원 — 후속)
현재 `_build_status_line_entry` / `_sync_status_line`의 statusLine 자동설치는 **Git Bash 전제**이다.
PowerShell-only 윈도우(Git Bash 미설치)에서는 quoted executable(`'C:/path/python.exe' 'script.py'`)이
PowerShell call operator(`&`) 없이 문자열 평가로 처리돼 실행되지 않는다.

- **재현 조건**: Windows + Git Bash 미설치 + Claude Code가 statusLine을 PowerShell로 실행하는 환경.
- **필요한 수정**: `_build_status_line_entry`에서 PowerShell 여부를 감지(또는 `--shell` 옵션 추가)해
  PowerShell이면 `& 'C:/path/python.exe' 'script.py'` 형태로 생성하는 분기 추가.
- **현재 Git Bash 사용자(= 대부분 팀원)에게는 영향 없음.** PowerShell-only 환경 대응은 별도 릴리스.

---

## L2 재설계 — "표준 인터페이스" → "표준 툴셋" (2026-06-21 Jane 결정)
**결정: L2 = 팀이 합의한 벤더 MCP 툴셋을 각 멤버 에이전트에 크로스에이전트로 등록해주는 "MCP 등록기".** 자체 role_server 프록시 + 역할(issues/chat/docs/calendar) handler 추상화는 **버린다.**

### 왜 (현 L2가 무거운 근원)
- 현재 L2(tm-connect)는 tm-mode 자체 MCP(`role_server`) + 역할별 `handlers/<role>.py` 수제코드로 서비스를 **프록시**한다 = 벤더 공식 MCP(Linear·Notion·GCal)를 **재발명**.
- "표준 인터페이스(역할 추상화)"의 이득 = provider 교체 무관인데, 팀이 Linear→Jira 갈아탈 일은 거의 없음(YAGNI). 그 보험료로 handler 유지비 + 무거운 절차(핸들러 생성·재배선)를 평생 짐.

### 채택 방향 (B = 표준 툴셋)
- "표준화"의 대상을 **인터페이스 → 툴셋**으로 이동. tm-mode가 하는 일:
  1. 팀이 "우리 쓰는 MCP는 이것들"(예: linear, notion, gcal)을 **합의·공유**(git, team.config 또는 별도 목록).
  2. 합류자/멤버 셋업 시 tm-mode가 그 **벤더 MCP를 각 에이전트(claude `.mcp.json`/settings + codex `config.toml`)에 대신 등록**(크로스에이전트 배선 대행).
  3. 토큰은 보안상 여전히 **각자 1회 붙여넣기**(단일 금고 default.json 재사용 — 이미 구현).
- 가치: "MCP 깔고 양쪽 에이전트에 배선하는 귀찮음 대행 + 팀 합의 공유". handler·role_server·재배선 소멸.

### 버릴 것 / 바꿀 것 (설계 패스에서 확정)
- `infra/mcp/role_server.py` + `handlers/` + 역할 추상화 → 제거 또는 대폭 축소.
- `tm-connect`: 역할 슬롯·핸들러 생성·우선순위판정(재사용>흡수>수제) → "팀 MCP 목록에서 골라 등록 + 토큰 입력"으로 단순화.
- `providers/<provider>.json`: 프록시 스펙 → **MCP 등록 스펙**(서버 명령/패키지·토큰 안내·필요 env)으로 재정의.
- `tm-onboard` L2 제안: 등록기 흐름으로 갱신.

### 열린 질문 (브레인스토밍/writing-plans 전 확정)
- 팀 MCP 목록 저장처: `team.config.json` services 재활용 vs 신설.
- 토큰을 단일 금고에 둘지 vs 에이전트 MCP 설정의 네이티브 토큰 처리에 맡길지.
- 멤버별 MCP 차등(누구는 calendar 안 씀) 허용?
- 기존 role_server/handlers 쓰는 팀 마이그레이션(현재 그런 팀 없을 가능성 — WIP).
- role_server `--team` dead-code(검수 MINOR)는 이 재설계서 role_server 제거와 함께 해소.

> 다음: 이 방향으로 brainstorming → spec → writing-plans. 큰 재설계라 별도 집중 세션 권장.
