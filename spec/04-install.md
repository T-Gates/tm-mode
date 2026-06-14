# teammode 스펙 04 — 설치·부트스트랩 (install.py)

| | |
|---|---|
| spec_version | **0.1-draft** |
| 상태 | 초안 (2026-06-14 새벽). 적대적 검수 1회 반영(11건: B1·M1~M6·m1~m4). |
| 범위 | `install.py` = **결정적(deterministic) 부트스트랩**. 팀을 0에서 **L1(세션로그만)** 동작까지 올린다. |
| 범위 외 | 서비스 연결·OAuth·리소스 선택(어느 DB/채널/캘린더) = **onboard 스킬(별도, 보류)**. install.py는 LLM 판단을 하지 않는다. |
| 관련 스펙 | [01 팀 메모리](01-team-memory.md), [02 훅·어댑터](02-hook-manifest.md), [03 호환 선언](03-conformance.md) |

---

## 0. 한 문장

> 사람이 에이전트에 치는 첫 한마디 **"이 레포 셋업해줘"** → 에이전트가 `python install.py`를 대신 실행 → 팀 레포가 **감지·스캐폴딩·에이전트 배선·env·훅** 까지 한 번에 서고, 끝에서 **`context`로 L1 데이터가 읽히는지 확인**한다. (실제 *맥락 주입*은 install이 아니라 **다음 세션의 SessionStart 훅**이 한다 — §4 ⑦.) install.py는 **즉흥이 아니라 고정 스크립트**이며, 똑똑한 판단(서비스 선택)은 하지 않는다.

## 1. 목적과 범위

### 1.1 표기 규약
[스펙 01 §1.1](01-team-memory.md)과 동일: **필수 / 권장 / 예약**.

### 1.2 설계 원칙

| 원칙 | 의미 |
|---|---|
| **코어 ≠ 스킨** | install.py는 *설치 코어*다. 진입 스킨(`"셋업해줘"`·pipx·npx·플러그인·템플릿)은 전부 결국 install.py를 호출하는 얇은 층. 스킨은 나중에 얼마든지 추가, 코어는 하나. |
| **결정적** | 같은 입력→같은 결과. LLM이 산문을 해석해 즉흥 실행하지 않는다. **판단이 필요한 지점(이 이름이 나인가, 어느 DB인가)은 install.py가 처리하지 않고 명시 인자로 받거나 멈춘다(추측 금지).** |
| **L1 자력 도달** | 서비스 연결(L2)·다이제스트(L3) 없이도, install.py 단독으로 **세션로그 디렉토리+훅+맥락 수집이 도는 상태(L1)** 까지 간다. 서비스 슬롯은 비어 있어도 정상(빈 슬롯 1급 시민, [스펙 02 §9](02-hook-manifest.md)). |
| **크로스에이전트** | 머신에 설치된 에이전트(Claude Code/Codex/…)를 감지해 각 어댑터로 배선. 한 번 실행이 설치된 에이전트 전부를 덮으며, **에이전트별 배선은 독립**이다(§8). |
| **판단은 위임** | "어느 Notion DB·어느 캘린더" 같은 선택은 install.py 범위 외 → onboard 스킬(보류). install.py는 그 자리를 *빈 슬롯*으로 두고 끝낸다. |

### 1.3 범위 외 (명시)
- 서비스 OAuth/토큰 발급·리소스 자동조회·리소스 선택 → onboard 스킬.
- 팀 레포 *생성*(`gh repo create --template`) → 에이전트의 cold-start 선행 동작 (install.py는 **이미 받아진 레포 안에서** 실행된다고 가정, §2.2). 공개 `T-Gates/teammode`는 엔진만 추출된 최소 레포라 `skills/`·`memory/`·`team.config.json`이 없다 → install.py ④가 처음부터 scaffold한다.
- 호스팅 서버·대시보드·다이제스트 워크플로 → 별도 트랙.

## 2. 진입과 전제

### 2.1 호출 경로 (전부 같은 install.py로 수렴)
```
A. cold-start(도입자, 레포 없음):
   "T-Gates/teammode 템플릿으로 셋업해줘"
   → 에이전트: gh repo create --template … --private --clone → cd → python install.py
B. 레포 보유(도입자/팀원):
   "이 레포 셋업해줘"  → 에이전트: python install.py
C. 직접:  python install.py [flags]   (스킨·CI·디버그)
```

### 2.2 전제조건
| 전제 | 등급 | 없을 때 install.py 동작 |
|---|---|---|
| Python ≥ (하한 §12-1) | 필수 | preflight 버전 검사, 미달이면 에러 종료(exit 2) + 안내 |
| `git` 바이너리 | 필수 | 없으면 에러 종료(exit 2) — 메모리가 git 기반 |
| GitHub 원격 인증(pull/push) | **협업 필수 / L1 로컬엔 불요** | 없으면 **경고**(로컬 L1은 진행) — push/pull 시점에 막힌다. preflight는 인증 부재로 종료하지 않는다(m3). |
| install.py가 **팀 레포 안에서** 실행됨 | 필수 | cwd/감지 root에 팀 레포 표식 없으면 에러(§10) |
| `gh` CLI | 권장 | 경로 A(템플릿 자동생성)에만 필요. 없으면 "웹 Use this template 후 다시" 안내(fallback). install.py 본체는 gh 불요 |

> **근거**: git 인증은 *팀 협업*(pull/commit/push)의 전제일 뿐, *로컬 L1*(세션로그 누적+맥락 수집)은 push 없이도 성립한다. 그래서 인증 부재 = 종료가 아니라 경고. gh는 타깃층에 사실상 보편 → 경로 A가 디폴트.
> **upstream remote(m2)**: 경로 A(템플릿)는 upstream이 설정된다(드래프트 §11.6). 경로 B(직접 clone)로 upstream이 없는 팀은 `update`/템플릿풀이 **우아하게 축소(스킵)** 된다.

## 3. CLI 계약

```
python install.py [--root PATH] [--agent {auto|claude|codex|...}]
                  [--member-name NAME] [--settings PATH] [--yes]
                  [--update] [--dry-run]
```
| 플래그 | 의미 | 기본 |
|---|---|---|
| `--root PATH` | 팀 루트 명시. **env 신뢰 금지**([스펙 01 §1.2], P1). | 미지정 시 §10 규칙으로 cwd 검증 후 결정 |
| `--agent` | 배선 대상. `auto`=설치 감지(§8). | `auto` |
| `--member-name` | 세션로그 author(영문·소문자·고유, [스펙 01 §3.3]). **대화 모드**=git user.name 제안 후 질의 / **`--yes`**=git user.name 사용, 없으면 exit 3(추측 금지, §12-3 결론). | git config user.name → 제안 |
| `--settings PATH` | **에이전트 설정 쓰기 타깃 오버라이드**(M1). 미지정=실호스트 기본(예: `~/.claude/settings.json`). CI/conformance/격리 테스트는 격리 경로 지정. | 실호스트 기본 |
| `--yes` | 비대화 모드(감지·기본값으로 끝까지). | off |
| `--update` | 이미 설치된 팀 레포 갱신(멱등 재배선, §7). 데이터(memory/) 무접촉. | off |
| `--dry-run` | 변경 없이 계획만 출력. **settings.json·memory·env 무접촉**(I-dry). | off |

**종료 코드**: `0` 성공 / `2` 전제·인자 오류(무변경) / `3` 부분 실패 또는 해소불가 충돌(어디까지 됐는지·무엇이 막혔는지 stderr 명시 — 필수).

## 4. 절차 (핵심)

순서대로, **멱등하게** 수행. 각 단계는 재실행 시 이미 된 부분을 건너뛴다(§7).

```
① preflight   Python 버전·git 바이너리·팀 루트 표식 검사. 원격 인증은 경고만. (실패 시 즉시 종료, 무변경)
② detect      git remote→org/repo, git user.name→이름 제안, 시스템 tz/locale,
              설치된 에이전트(~/.claude·~/.codex 등), team.config.json 존재·유효성(③)
③ role        team.config.json 존재 + 필수키(spec_version·team.name) 유효 → 팀원(§6)
              파일 부재 또는 team.name이 placeholder/미초기화 표식 → 도입자(§5)
              ※ services 채움 여부로 가르지 않는다 — 빈 슬롯은 정상(M3, 스펙02 §9.2)
④ scaffold    memory/ 구조 생성(memory/INDEX.md·memory/team/members.md·
              memory/team/sessions/<이름>/ — 경로는 엔진 실제값[teammode.py]·스펙01 단일소스),
              도입자는 최소 config 작성.
              ※ install.py는 첫 세션로그를 쓰지 않는다(M2, §12-2 결론) — 디렉토리만 만든다.
⑤ wire        감지된 에이전트마다 어댑터 위임(스킬·MCP 등록·훅 sync). 실호스트 설정에 쓴다(M1, §10).
              에이전트별 독립 — 하나 실패가 다른 배선을 막지 않음(M5, §8).
⑥ env         런타임 훅용 팀 루트 env를 셸 프로파일에 멱등 1줄 주입 (§9)
⑦ verify      `teammode on --root <root> --settings <타깃> --install` (배너+훅 활성+active 마커) →
              이어서 `teammode context --root <root> --json` 실행해 **L1 데이터가 읽히는지(수집 가능) 확인**.
              ※ 실제 *맥락 주입*은 여기가 아니라 **다음 에이전트 세션의 SessionStart 훅**이 한다(B1, 스펙02 §3.1).
              ※ context는 기계 수집만 — 요약은 스킬 몫. ⑦은 `--json` 원자료 출력까지만(r2).
```

## 5. 도입자 경로 (config 부재/미초기화)

1. **최소 config 작성** — 감지·플래그로 채운다(LLM 불요):
   - `team.name`(기본=레포명), `locale`/`timezone`(시스템 감지), `admin_contact`(이름), `members_file`.
   - `services`: **전부 빈 슬롯(키 생략)**. 서비스 연결은 onboard 스킬(보류). 빈 슬롯은 정상([스펙 02 §9]).
   - 손편집 0 — install.py가 직접 쓴다.
2. **memory/ 스캐폴딩** — `memory/INDEX.md`, `memory/team/members.md`(본인 이름 등재, §6-2 충돌 정책 적용), `memory/team/sessions/<이름>/` mkdir. **경로는 엔진 실제값(teammode.py: `memory/team/sessions/<author>/`)·스펙01을 단일 소스로** — 약식 `sessions/<이름>/` 표기에 속지 말 것. 배너는 `memory/banner.txt`를 team.name으로 **선기록**(엔진은 파일 있으면 그대로 읽으므로 엔진 무수정 — env `TGATES_TEAM_NAME` 경로 우회). **첫 세션로그는 쓰지 않음**(M2).
3. ⑤~⑦ 공통 골격 수행.
4. 결과: **L1 동작 팀 레포 완성.** 첫 로그는 도입자의 첫 실작업 세션에서 훅 흐름으로 생성된다. commit·push·팀원 초대는 install.py 범위 외(권장 안내).

## 6. 팀원 경로 (config 유효)

> 핵심 레버: **도입자 1회 고생 → 팀원 0.** config가 레포에 있으므로 서비스·슬롯을 *읽기만* 한다 — 토큰·ID 수집 전부 스킵.

1. config **작성 안 함**(읽기만).
2. **members.md 이름 등재 — 결정적 충돌 정책(M4)**:
   - 같은 영문 이름이 이미 있으면 **추가하지 않고 본인 항목으로 간주**(멱등). 동일인 재설치/다른 머신 = 정상.
   - 다른 이름을 원하면 `--member-name`으로 오버라이드.
   - 오버라이드 이름이 *다른 사람*으로 이미 등재돼 있으면 **exit 3 + 안내**(사람이 해소, install.py는 "나인가 남인가"를 추측하지 않음).
3. ⑤~⑦ 공통 골격(배선·env·verify).
4. 결과: **다음 세션** 시작 시 SessionStart 훅이 팀 최근 로그·결정을 주입한다(B1).

## 7. 멱등성·재실행·업데이트

- 모든 단계 **재실행 안전**(필수): 디렉토리/등록/env 라인 중복 생성 금지.
- `--update`: 엔진·스킬·훅 최신 재배선([스펙 `update` 동사] 정합). 데이터(memory/) 무접촉.
- `--dry-run`: 변경 없이 "무엇을 할지"만 출력 — settings.json·memory·env 무접촉(I-dry).

## 8. 에이전트 배선 (어댑터 위임)

- install.py는 에이전트별 표기를 **알지 못한다** — `agents/<name>/` 어댑터에 위임([스펙 02 §0]). 단계: **스킬 등록**(`install-skills`)·**MCP 등록**(`install-mcp`)·**훅 sync**.
- `--agent auto`: `~/.claude`·`~/.codex` 등 존재로 감지, 발견된 에이전트 **전부** 배선.
- **부분 실패 정책(M5)**: 에이전트별 배선은 독립 — 한 에이전트 실패가 다른 에이전트 배선을 막지 않는다(스펙02 §5·§9.2 정신 계승). 하나라도 실패 시 **exit 3 + 어느 에이전트의 어느 단계가 막혔는지 stderr 명시**. 성공한 배선은 롤백하지 않는다(멱등 재실행으로 재시도).

> **현재 갭(비규범, 검수로 코드 확인)**: 공개 레포 install.py는 `--claude sync` 위임만 하는 디스패처(~53줄). `install-skills`·`install-mcp`·스캐폴딩·figlet·verify·`infra/skills/`·`memory/`·`team.config.json`이 **전부 미구현/부재** → 본 스펙이 목표.

## 9. 환경변수 주입

- 런타임 훅은 하니스가 발동하므로 인자 통로가 없다 → **팀 루트 env 필요**([스펙 01 §1.2]).
- **변수명은 스펙01 §1.2 reference 값 `TEAMMODE_HOME`을 따른다**(현행 런타임 훅 코드와 일치). ⚠️ 스펙01 부록A의 `TGATES_HOME` 표기는 자기모순(오기) — 스펙01쪽 정정 필요(M6, 별도 이슈).
- **크로스플랫폼 영구 env 주입**:
  - **POSIX(Linux/macOS)**: 셸 프로파일 감지(bash/zsh/fish)해 **멱등 1줄** 주입(중복 금지).
  - **Windows**: 셸 프로파일 대신 레지스트리(`HKCU\Environment`) — `setx TEAMMODE_HOME "<절대경로>"`(영구 user env). uninstall 은 `reg delete HKCU\Environment /v TEAMMODE_HOME /f`. setx/reg 부재·실패는 비치명. (reference: `install_lib.inject_env_windows`/`remove_injected_env_windows`, `is_windows` 분기. Windows 분기는 setx/reg subprocess 모킹으로 검증 — 실 Windows 동작은 native 환경 권장.)
- ⚠️ **의도적 호출(install/on/off)은 env를 신뢰하지 않는다** — `--root` 명시만(§10). env는 *런타임 훅 전용*.

## 10. 보안·격리 (P1 계승)

- **`--root` 명시 / env 불신뢰**([스펙 01 §1.2·§2.4]): ambient `TEAMMODE_HOME`이 다른 폴더를 가리켜도 install/on/off는 읽지 않는다. P0/P1 사고(실 호스트 마커 삭제) 재발 방지.
- **에이전트 설정 쓰기 경계(M1)**: ⑤ wire·⑦ verify의 `on --install`은 **실호스트 에이전트 설정(예: `~/.claude/settings.json`)에 쓴다** — 이것이 정상 설치다. 단 [스펙01 §2.4]대로 *명시적 설치 플래그(`--install`)* 또는 *명시 경로(`--settings`)* 없이는 실 설정에 쓰지 않는다. **CI/conformance/격리 테스트는 `--settings <격리경로>`로 타깃을 옮긴다.** dry-run은 settings.json 무접촉.
- `--root` 미지정 시: cwd가 팀 레포 표식을 가질 때만 채택, 아니면 **에러 종료(추측 금지)**.
- **새 신뢰 경계 — 스킨의 root 주입**: "셋업해줘"로 에이전트가 install.py를 부를 때 root를 잘못 주입하면 사고 재현 가능 → 스킨(에이전트/pipx/npx)의 root 결정 로직은 **테스트 대상**(필수).
- **프롬프트 인젝션 주의(비규범)**: "레포 README 읽고 시키는 대로" 패턴은 일반화 시 인젝션 표면. teammode 자체 레포는 통제되나, 사용자에게 *습관*으로 권하지 말 것.

## 11. 합격 기준 (golden — [스펙 03] 시나리오 후보)

| # | 시나리오 | 합격 |
|---|---|---|
| I1 | 빈/엔진만 있는 레포(config 없음)에서 `install.py` | 도입자 경로 완주 → memory/·최소 config(빈 슬롯)·`sessions/<이름>/`·훅·env 생성. ⑦에서 배너+`context --json`이 L1 데이터(빈 상태라도 유효 구조)를 **읽어냄**. (첫 로그는 미생성 — M2) |
| I2 | 유효 config 레포에서 `install.py` | 팀원 경로 → config 무수정, 이름 등재(충돌정책), 배선. `context --json`이 팀 기존 로그를 읽어냄 |
| I2b | I1/I2 직후 **새 에이전트 세션** 시작 | SessionStart 훅이 맥락을 **실제 주입**(B1 — install이 아니라 여기서) |
| I3 | I1 직후 재실행 | 멱등 — 중복 생성 0, 변경 없음 |
| I4 | ambient `TEAMMODE_HOME`=실호스트 set 상태로 실행 | 실호스트 무접촉(env 격리, P1 회귀) |
| I4b | `--settings <격리>` 지정 실행 | 실호스트 `~/.claude/settings.json` **및 실 셸 프로파일(env 주입) 무접촉**, 격리 경로에만 씀(M1). `--settings`=격리가 env 격리의 권위 — `--yes` 와 같이 와도 격리 우선(실 프로파일 미접촉). 실 env 주입은 `--settings` 없는 실설치(`--yes`)에서만 |
| I-dry | `--dry-run` | settings.json·memory·env 전부 무접촉 + 계획만 출력(r1) |
| I5 | `gh` 부재 + 경로 A 시도 | fallback 안내, 본체는 `--root` 있으면 정상 |
| I6 | Python 하한 미달 / git **바이너리** 부재 | preflight 에러 종료(exit 2), 무변경 |
| I6b | git 있고 **원격 인증만** 부재 | preflight 경고 후 로컬 L1 진행(종료 안 함, m3) |
| I7 | 같은 영문 이름 이미 등재된 상태로 재설치 | 멱등 — 중복 등재 0, 본인 항목 간주(M4) |
| I8 | `--member-name`이 *다른 사람* 등재명과 충돌 | exit 3 + 안내, members.md 무변경(M4) |

## 12. 미결 (open questions)

1. **(열림)** Python 버전 하한 확정값(3.9? 3.10?) — 타깃 머신 분포 근거 필요.
2. **(닫음 — 검수 M2)** 첫 세션로그를 install.py가 직접 쓰지 **않는다**. `sessions/<이름>/`만 만들고, 첫 로그는 첫 실작업 세션의 훅 흐름으로 생성(author/06시컷/summary 규약 자연 충족).
3. **(닫음 — 검수 m1)** `--member-name`이 `--yes`에서 git user.name도 없으면 **exit 3**(신원 추측 금지).
4. **(닫음 — 검수 m4)** 플러그인 매니페스트(`.claude-plugin/`·`.codex-plugin/`) 생성은 install.py 기본 산출물이 **아니라** 별도 스킨/어댑터 옵션. §8 본체에서 분리.
5. **(열림)** 도입자 commit·push 안내를 어디까지 자동화할지(install.py 범위 경계).
