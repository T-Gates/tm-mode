# 백로그: KB 쓰기 거버넌스 (teammode L1 메모리 차별점)

착안: 2026-06-15 (tgates-toolkit 실작업 중 체감). 상태: **설계 메모, 미구현 — 다음에 구현**.

## 문제
`memory/` 파일을 직접 `Edit`/`Write`하면 INDEX 갱신·커밋·알림 등 일관 절차가 누락된다. 실제로 tgates 작업 중 서브에이전트·메인이 직접 Edit해서 매번 절차가 빠지는 일이 반복됨. → "팀 메모리는 동사(스킬)로만 쓴다"를 **강제**할 필요.

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
  2. (Claude Code 한정) PreToolUse 훅이 직전 `Skill` 호출을 transcript로 확인 — 단 크로스에이전트 이식성 떨어짐. teammode는 에이전트 무관이 원칙이라 **플래그+TTL/세션ID가 정답**.

## teammode 편입 위치
- L1 메모리 시스템(`spec/01-team-memory.md`)에 "쓰기 거버넌스" 절로 편입.
- 차별점 메시지: **"팀 메모리는 동사로만 쓴다"** = 여러 에이전트·팀원이 만져도 INDEX/커밋/알림 일관성이 코드로 보장됨. L1 delight 후보.

## 구현 시 체크
- [ ] PreToolUse 훅 추가 (`infra/hooks/` + install.sh 등록 + migration 1장)
- [ ] 메모리 관리 스킬에 unlock touch/rm + 플래그 TTL/세션ID 가드
- [ ] 직접 Edit 차단 시 안내문(스킬명·우회금지 사유)
- [ ] conformance 테스트(직접 Edit deny / 스킬경유 allow)


---

## 06 — 지식 업로드 (노션 → 팀 knowledge) 설계

> 상태: design (brainstorm 2026-06-16, 은수) / 다음: writing-plans
> 의존: L2(notion provider·install-mcp·docs 슬롯), 엔진 동사 체계(§3), tm-onboard(§5)
> spec_version 영향: 엔진 동사 `knowledge` 추가 = §3 동사 계약 변경 → **minor bump(0.2→0.3)**

### 1. 정체

**"지식 업로드" = 연결된 노션 MCP의 페이지를 긁어 주제별로 분류해 팀 knowledge로 저장하는 기능.**
콜드스타트(세션로그 0인 신규 팀)를 기존 노션 자료로 워밍업한다. tm-onboard에 통합되며, 온보딩이 한 진입점이다.

### 2. 핵심 결정 (brainstorm 확정)

| 항목 | 결정 | 근거 |
|------|------|------|
| 결과물 | 팀 memory 시드(저장) | 일회 요약 아님 — 지속 가치 |
| 저장 위치 | `memory/team/knowledge/<주제>.md` (신설 영역) | members/sessions로는 "팀 지식" 담을 곳 없음 |
| scope | **팀 scope** — 도입자 1회 시드 → git 공유, 팀원은 pull로 수령(0회) | knowledge는 비밀 아니라 git 추적 → credentials가 못 한 팀당1회를 공짜로 |
| 토큰 | **credentials 금고 안 씀** — 에이전트에 이미 연결된 notion MCP를 빌려 읽기만 | teammode 토큰 무접촉. 토큰은 MCP 연결 소관(각자) |
| 분류 | AI가 주제별 분류(통째 덤프 X) | "분류는 시킬거" |
| 수집 주체 | **페이지별 fan-out** — 오케스트레이터가 페이지마다 서브에이전트 디스패치(백그라운드 병렬), 오케스트레이터는 취합·정리만 | 노션 트리 양 많음 → 병렬·비차단. L2 빌드 오케스트레이션 패턴 재사용 |
| 트리거 | tm-onboard 통합. 도입자+knowledge 비어있으면 제안. 나중 재실행으로 추가 | "온보딩 통합", "지식 업로드 기능으로 추가" |
| 범위 | 준 링크 + 관련 하위 "쭉" 긁기, 도입자에게 "이만큼" 확인 | "정보 쭉 긁어와서" |

### 3. 컴포넌트 (teammode 철칙: 엔진=기계, 스킬=판단)

#### 3.1 엔진 동사 `knowledge` (기계적 — `log` 형제)
```
teammode.py knowledge --root <팀루트> --topic <주제> --text <내용> [--source <노션URL/제목>]
```
- `memory/team/knowledge/<주제>.md` 생성/이어쓰기. 주제 파일명은 author 검증과 동형(경로 traversal·footgun 차단).
- frontmatter: `topic`, `source`(출처 노션 링크/페이지), `updated`(작업일 06시컷). 출처 명시 = "외부유래" 표시.
- `memory/team/knowledge/INDEX.md`(또는 기존 INDEX)에 주제 등재(기계적).
- **요약·분류 안 함**(§3 "엔진은 판단 안 함") — 받은 `--text`를 그대로 저장. 분류·요약은 스킬이 끝낸 결과.
- 멱등: 같은 주제 재호출 = 이어쓰기/갱신(append vs replace는 §6 미결).

#### 3.2 tm-onboard 확장 (오케스트레이터 — 페이지별 fan-out)
- 온보딩 흐름: `L1셋업 → context(첫가치) → docs 슬롯(notion) 연결(tm-connect) → [신규] 지식 시드 제안`.
- 도입자 + `knowledge/` 비어있음 → "연결된 노션에서 팀 지식 가져올까요?" → **오케스트레이션**:
  1. **오케스트레이터(tm-onboard)**: notion MCP로 트리 구조/페이지 목록만 가볍게 파악(본문 X). 도입자에게 "페이지 N개 가져올게요" 확인.
  2. **페이지별 fan-out**: 페이지마다 서브에이전트 **백그라운드 병렬** 디스패치. 각 서브에이전트 = 자기 페이지 1개 본문 읽기 → 요약 + 주제 태깅 → 구조화 결과 반환(파일 직접 안 씀).
  3. **오케스트레이터 취합·정리만**: 서브에이전트 결과들을 주제별로 묶어 → 주제마다 `knowledge` 동사 1회 호출(저장). 오케스트레이터는 노션 본문을 직접 안 읽음(서브에이전트가 함) — 정리·저장 지휘만.
  4. 온보딩 메인 흐름은 안 막힘(백그라운드), 완료 시 "N개 주제 시드됨" 안내.
- `knowledge/` 이미 있음(팀원·재온보딩) → 수집 skip + "팀 지식 N개 있음"(context가 표시).

### 4. 데이터 흐름

```
도입자 온보딩
  └ docs 슬롯 = notion 연결 (install-mcp, 토큰은 그 MCP 소관)
      └ tm-onboard(오케스트레이터): notion MCP로 페이지 목록 파악 → "N개 가져올게요" 확인
          ├ 페이지1 → 서브에이전트A (백그라운드) ─┐ 각자 본문 읽기→요약+주제태깅
          ├ 페이지2 → 서브에이전트B (백그라운드) ─┤ → 구조화 결과 반환
          └ 페이지N → 서브에이전트… (병렬)      ─┘
              └ 오케스트레이터 취합·정리(본문 직접 안 읽음)
                  └ 주제마다 `teammode.py knowledge --topic X --text ... --source ...`
                      └ memory/team/knowledge/X.md + INDEX 등재
                          └ git commit + push (도입자)
팀원 온보딩
  └ git pull → knowledge/ 자동 수령 → 수집 skip, context에 "팀 지식 N개" 표시
```

### 5. 온보딩 분기 (install.py role 판정 + knowledge 존재)

| 상황 | 동작 |
|------|------|
| 도입자 + knowledge 비어있음 | 노션 연결됐으면 "지식 시드?" 제안 → 서브에이전트 수집 |
| 도입자 + 노션 미연결 | "노션 연결하면 지식 시드 가능" 안내, skip |
| knowledge 이미 있음(팀원/재온보딩) | 수집 skip + "팀 지식 N개" 안내 |
| 온보딩 후 "이 노션 지식 추가해" | 같은 수집 경로 재실행(서브에이전트) — 새 주제 추가/기존 갱신 |

### 6. 미결 (writing-plans 전 확정)

- **같은 주제 재업로드**: append(누적) vs replace(덮어쓰기) vs 병합. → 멱등성·중복 방지 관점에서 결정.
- **주제 분류 카테고리**: 고정 어휘(제품/로드맵/용어/결정/회의) vs AI 자유 주제명. 자유면 파일명 정규화 규칙.
- **노션 범위 상한**: "쭉" 긁되 페이지 N개/깊이 상한(폭주 방지)? 도입자 확인 UX.
- **INDEX**: 기존 `memory/INDEX.md` 재사용 vs `knowledge/INDEX.md` 별도. (자가유지 설계의 INDEX 거버넌스와 정합 필요 — INDEX 자동쓰기 merge churn 주의.)
- **권한**: 팀원도 지식 업로드 가능(git 추적이라 누구나 add 가능)? 도입자 중심? 충돌은 git_ops ff-only.
- **스킬 발견**: tm-onboard 통합이라 별도 스킬 발견 불요. 단 "지식 추가해" 재실행 트리거를 tm-onboard description에 넣을지.

### 7. 테스트 전략

- 엔진 `knowledge` 동사: 파일 생성·이어쓰기·INDEX 등재·frontmatter·경로 traversal 차단·멱등 (tmp 격리, log 동사 테스트 패턴).
- conformance 골든: "지식 시드 → knowledge/ 파일 존재 → context에 표시" 시나리오 추가(06).
- 온보딩 분기: 도입자/팀원/knowledge유무 (install golden 패턴, --settings 격리).
- 서브에이전트 수집은 notion MCP 모킹(실 노션 무접촉) — 픽스처 페이지 → 분류 → knowledge 호출 검증.
- 호스트 무접촉: 실 노션·실 memory 무접촉, tmp+monkeypatch.

### 8. 범위 밖 (이월)
- 지속 자동 동기화(노션 변경 주기 반영) — 변경추적·충돌 복잡, v0.2+.
- 노션 외 docs provider(구글독스 등) 지식 업로드 — provider팩 확장 시.

---

## 신규 백로그 (2026-06-17 새벽 — 은수 구상 + 윈도우 실셋업 도그푸딩)

### 기능 (은수 구상, 우선순위 순)
1. **메모리 스켈레톤 + 채우기 유도** — 메모리 폴더 뼈대를 미리 깔고(얇게, tgates-toolkit의 `team/`·`product/`·`decisions/` 구조를 제품특정 빼고 범용화), 에이전트가 칸을 채우도록 유도. ⚠️ 빈 깡통 남발 금지(`.gitkeep`·INDEX 안내 위주), 유도는 ②가이드라인 주입으로(강제 X).
2. **팀모드 가이드라인 파일 세션 시작 주입** — 세션 시작에 "팀모드 잘 쓰는 법" 강령을 INDEX·최근활동과 함께 주입(6/16 AI강령 3계층의 ①). 범용 강령=`infra/`(upstream 소유·update로 갱신) vs 팀 커스텀=`memory/`(팀 소유) **분리**, 매 세션이라 **얇게**, AGENTS.md(셋업 진입점)와 **채널 구분**. session-start.py가 ②③ 이미 주입 → ①만 추가.
3. **툴킷 강력 스킬 이식** — tgates-toolkit의 검증된 스킬을 teammode로. **on/off 토글 스킬 포함**(엔진 `on`/`off` 동사는 있으나 사용자용 스킬 래퍼 부재): on=pull+맥락주입+배너 / off=세션로그+커밋+push. 어느 스킬을 이식할지 선별 필요.
4. **statusline 팀명** — on 시 상태줄에 팀명 표시. ⚠️ 에이전트별 표면 다름(claude=`settings.json` statusLine command, codex=대안/스킵) → **어댑터 레이어가 처리**(크로스에이전트 어댑터의 좋은 쓰임새). 팀명은 `team.config.json`에서, `--yes` 게이트. **③(on 스킬) 이후** 자연스러움.
   - ✅ **배너 picker 구현 완료**(23886a9): `infra/banners/` 6종(ansi_shadow·slant·chunky·cyberlarge·larry3d·speed) 정적 렌더 + 온보딩 personality에서 선택→`cp`로 `memory/banner.txt` 적용. pyfiglet은 빌드타임만(런타임 의존성 0).

### 도그푸딩 발견 (수정 필요)
- **install cp949 잔존 경로** — 190bfca(`_engine_capture`·`_git`)가 일부만 덮음. 윈도우 `install --yes` verify서 `_readerthread` 트레이스백 재발(비치명, state=on). 남은 subprocess decode 경로 추적·수정.
- **윈도우 Git Bash 경로변환 주의** — 에이전트가 `git show upstream/main:.gitignore` 류에서 MSYS 경로변환(`/`→`\`, `:`→`;`)에 막혀 오판. 문서(AGENTS/SKILL)에 윈도우 Git Bash 주의(`MSYS_NO_PATHCONV=1`) 한 줄.
- **`--team-name` 인자** — install이 team.name을 repo명으로 자동. 팀명 직접 지정 인자 부재(현재는 셋업 후 config 수정).
- **직책/직군 분리 스키마** — 현재 `--role`은 단일 자유필드(예 `팀장/개발`). 직책(팀장/팀원)·직군(dev/pm/design) 분리 스키마.

---

## 핫픽스 묶음 — push 후 검증 P1 견고성 (2026-06-18, BACKLOG③ 출시 후)

915fa70 push 후 7-시나리오 병렬 검증 + 윈도우 실설치 도그푸딩에서 나온 견고성 결함. **P0 0**(핵심 안전 traversal·symlink·커밋오염·고아청소·실호스트오염·거버넌스 실발동 전부 정상). 아래는 입력검증/예외처리 P1 — 출시 막을 결함 아니라 묶음 hotfix.

### knowledge 동사 입력 견고성 (수렴 P1)
- **미처리 예외 → 트레이스백 + exit 1** (친화 메시지 X): 긴 파일명(255자↑ → `OSError`), 권한 문제(`chmod`된 INDEX → `PermissionError`). → write/delete를 try/except로 감싸 **exit 2 + 친화 메시지**로.
- **유니코드 author/filename 통과**: `_validate_author`가 `isalnum()` 쓰는데 파이썬 `isalnum()`은 유니코드라 한글 author·filename이 통과(예: author "햄버거"). → **`isascii()` 강제** 추가(영문/숫자/제한기호만).
- **content 제어문자 미필터 (P2)**: knowledge content에 제어문자 들어가도 그대로 저장. → 정규화 or 거부.

### 거버넌스(kb-write-guard) 경미 (P1)
- **상대경로 fail-closed 여부 경미**: file_path가 상대경로일 때 containment 판정이 CWD 의존(경미 — 실제 훅 입력은 보통 절대경로). → 명시적 처리.
- **memory 내부 symlink**: memory/ *안에서* 밖을 가리키는 symlink 경유 편집 경계(경미).

### 윈도우 도그푸딩 미세 갭 (P2)
- **전역 git identity 빈 경우**: 온보딩에 `git config user.name/email` 안내 한 줄 (AI가 로컬로 설정하게 됨).
- **`install.py --help`가 `--root` 요구 → exit 2**: help는 root 없이 출력되게.
- **PowerShell git stderr 빨강 래핑**: 윈도우 특유 비치명 — 문서에 주의 한 줄(선택).

### knowledge 동시 write race (P2, 백로그 — dev-cycle 2차 검수서 codex 적발)
- atomic write 반영 후, 같은 topic 동시 write 중 한 writer 의 INDEX 실패 롤백이 다른 writer 가 방금 쓴 파일을 삭제/과거 내용으로 복원할 수 있음(race, fault injection 확인).
- **접은 이유**: teammode knowledge 는 단일 CLI 순차 사용 모델 — 동시 same-topic write 비현실적. lock 도입은 P1 핫픽스 범위 과대. 실제 동시성 요구 생기면 topic/folder 단위 lock 또는 롤백 전 "내가 쓴 내용인지" 비교 후 unlink/restore.

> 출처: 2026-06-18 push 후 검증·윈도우 end-to-end 도그푸딩 (세션로그 eunsu 2026-06-18). 핵심 안전은 전부 통과, 위는 견고성/UX 개선분.
