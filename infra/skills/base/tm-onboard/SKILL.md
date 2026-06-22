---
name: tm-onboard
description: Use at first contact with teammode — setting up a team repo or joining a team — or to register the team memory as an Obsidian vault at any time later. Triggers on "이 레포 셋업해줘", "팀모드 셋업", "팀모드 시작", "온보딩", "팀모드 합류", "teammode setup", "Obsidian 등록", "옵시디언 볼트 만들어줘", or when handed a teammode repo to set up.
---

# tm-onboard — 팀모드 온보딩

teammode를 처음 켜는 스킬. 팀 생애주기를 따라 자란다:

```
팀 셋업 (도입자 1회)  →  개인 셋업 (각 멤버)  →  서비스 연결 (L2, 나중)
        └─ L1: 세션로그 + 맥락 자동주입 ─┘        └─ 이슈·채팅·… (tm-connect) ┘
```

## 원칙
- **될 일은 `install.py`(결정적 기계)가, 판단·대화는 이 스킬이.** install.py를 호출하고 결과를 사람 말로 옮긴다 — 단계를 손으로 재현하지 않는다.
- **progressive**: L1 가치(맥락 주입)를 *먼저* 보여주고, 서비스 연결은 사람이 원할 때만.
- **배선은 항상 실배선(`--yes`) — 사용자에게 "어느 배선모드?"를 묻지 않는다.** 온보딩은 실제로 팀모드를 쓰려는 의도이므로 실 `~/.claude/settings.json`에 훅을 배선(write)하는 게 기본이다. 격리(`--settings <경로>`)·미리보기(`--dry-run`)는 **개발·테스트에서 명시적으로 요청받았을 때만** 쓴다(평상시 온보딩에선 노출·선택지 제시 금지).

## 진입: 현재 상태 점검 (호출마다 먼저)
이 스킬이 호출되면 **먼저 현재 온보딩 상태를 감지**해 체크표로 보여주고, **안 된 것·다시 할 것만** 진행한다. 상태는 **저장하지 않고 매번 환경에서 감지**한다 — 저장 플래그는 stale 위험(env 무신뢰 원칙과 같은 결).

| 단계 | 됨 판정 (이렇게 감지 — 저장 X) |
|------|------------------------------|
| 설치·훅 배선 | `team.config.json` 존재 (install.py 가 스캐폴드+배선 완료 시 생성). ⚠️ `state` 는 **활성화** 여부지 설치 여부가 아니다 — 설치 직후 정상값은 `off` |
| 팀모드 활성화 *(opt)* | `teammode.py context --json` 의 `state == "on"` (설치는 자동 활성화 안 함 — 사용자가 `tm on` 으로 켬) |
| upstream 추적 | `git -C . remote` 에 `upstream` 있음 |
| 서비스 연결(L2) | `team.config.json` 의 `services` 슬롯이 빈 슬롯이 아님 |
| Obsidian 뷰 | 레포에 `.obsidian/` 존재 |

- 체크표를 사람에게 제시: "여기까진 됐고, 다음/다시 할 것을 고르세요." **이미 된 단계는 기본 건너뛴다**(사람이 "다시"라 하면 재실행 — install 류는 멱등).
- ⚠️ 이 체크표·진행상태를 **파일로 저장하지 않는다**(매번 감지). 캐시가 꼭 필요하면 `.context/`(gitignored)에만 — **`memory/`(팀 공유)엔 절대 두지 않는다**(팀원마다 진행이 달라 충돌·churn).

## 0. 도입자/팀원은 install.py가 자동 판정
**경로는 `--member-name`이 아니라 `team.config.json` 유효성으로 install.py가 자동으로 가른다**:
- config 없음/미초기화 → **팀 셋업**(도입자) — config를 새로 쓴다.
- config 유효 → **개인 셋업**(팀원) — config는 읽기만.

`--member-name`은 *분기 스위치가 아니라* 양쪽에서 author 이름을 정하는 인자다. 그래서 셋업 명령은 사실상 하나다 (아래). 미리 사람에게 "도입자/팀원 자동 판정됨"을 알리고 진행한다.
> (install.py는 아직 role을 `--json`으로 안 뱉으므로, 굳이 미리 알아야 하면 `team.config.json` **유효성**(team.name이 placeholder 아니고 spec_version 존재 = config_is_valid)을 직접 확인한다. `--json` 생기면 그걸로 전환.)

## 셋업 (도입자·팀원 공통 명령, role 자동)

**먼저 확인 (필수) — 멋대로 정하지 말 것:**
- **멤버 이름**: 세션로그에 author로 남는 영문 이름. `git config user.name`을 *제안값*으로 보여주되 **반드시 사용자 확인 후** `--member-name`에 넣는다. ⚠️ git 설정·계정명·이메일(예: `bob`)이 사용자가 원하는 팀 멤버명(예: `jane-doe`)과 다를 수 있다 — 추론값을 임의 확정 금지.
  - **한글·비영문 user.name이면**(예: `장Jane`) 영숫자만 남기는 추론이 비어 install이 exit 3 으로 거부한다. 이 경우 install 을 돌리기 **전에** "영문 멤버명을 뭘로 할까요?(예: jane-doe)"라고 **먼저 물어** `--member-name`을 받는다 — exit 3 를 보고 재실행하게 두지 말 것(첫 도입 흐름이 매끄럽게).
- **전역 git identity**: `git config --global user.name`·`user.email`이 비어 있으면 커밋 단계에서 실패한다 — 비어 있을 경우 `git config --global user.name "이름"` 및 `git config --global user.email "이메일"` 설정을 안내한다.
- **팀명**(도입자만): 기본은 repo명. "팀 이름 이대로 쓸까요?" 확인. (현재 install.py는 team.name 인자가 없어 repo명 자동 → 바꾸려면 셋업 후 `team.config.json`의 `team.name` 수정. 백로그: `--team-name`.)
- **org/레포 위치**(도입자가 아직 레포가 없으면 — **install 보다 먼저**): 레포를 만들기 전에 어느 GitHub org·계정에 만들지 **반드시 묻는다** — 개인 계정 vs 팀 org(예: `Acme`). 임의 선택·자동 진행 금지. 순서: **org 확인 → 레포 생성(template/`gh repo create`) → clone → install.** (AGENTS.md '국면 0'과 동일. 레포가 이미 있으면 건너뜀.)
- **역할·직군**(`team.config.json` members 에 저장): 팀 내 **직책**(팀장/팀원)과 **직군**(developer/pm/designer 등)을 묻고 `--role` 로 저장한다. ⚠️ install 의 **도입자/팀원 자동판정**(config 유효성 = 팀을 셋업했나/합류했나)과는 **다른 축**이다 — 그건 role 판정용이고, 이건 *사람의 직책·직군*이다. 한 문자열로 합쳐도 된다(예: `팀장/개발`, `팀원/디자인`). 초기 단계라 단일 자유필드(직책·직군 분리 스키마는 백로그).

```bash
python infra/install.py --root . --member-name <영문이름> --role <직책/직군> --yes
```
- install.py가 함: preflight → 감지 → role 자동 → scaffold(`memory/INDEX.md`·`memory/team/members.md`·`memory/team/sessions/<이름>/`·도입자면 빈 services config) → 훅 sync(**실 settings.json에 write**) → env 주입 → verify(`context` 로 설치 확인 — **팀모드는 켜지 않는다**, 활성화는 사용자가 `tm on`).
- `--member-name`: 권장. 생략 시 git user.name 제안. **팀원은 이름 충돌 회피 위해 명시 권장.**
- **이름 충돌**(다른 사람이 같은 이름 등재) → install.py가 **exit 3 + 안내**. 사람이 `--member-name <다른 영문이름>`으로 재실행하게 한다 (추측 정정 금지).
- 실패(exit≠0)면 사유를 사람 말로 옮기고 멈춘다.

## 첫 가치 (셋업 직후) — L1 보여주기
```bash
python infra/teammode.py context --root . --json
```
→ 결과를 사람 말로 요약: "지금 팀 상황: …"
- **설치는 팀모드를 자동으로 켜지 않는다 — `--yes`로 wire까지 했어도 state=off가 정상이다**(설치 ≠ 활성화, P: 사용자 동의 전 호스트를 활성 상태로 두지 않음). 활성화는 사용자가 직접 `tm on`(아래 진입점 권유) 하거나 tm-onboard 제안에 동의했을 때만. off라도 context로 팀 맥락은 보여줄 수 있다.
- **갓 만든 팀은 세션로그 0** → 요약할 게 없다. "구조는 섰고, 다음 작업부터 자동 기록·주입됩니다"로 내레이션.
- 팀원은 context로 팀 기존 로그가 보인다. (다음 세션부터는 `session-start.py` 훅이 자동 주입.)
- **KB(지식 베이스)**: 팀 공유 지식은 `tm-knowledge`(조회·검색)·`tm-manage-knowledge`(추가·수정·삭제)로 관리한다. 처음엔 비어 있고 팀이 쌓아가는 구조 — 셋업 직후엔 소개만(강제 진행 X).
- **이제 시작 — 일상 진입점 (여기서 닫는다)**: 셋업·선택을 마치면 마지막으로 **팀모드를 지금 켤지 제안한다**(설치만으론 꺼져 있음 — 동의하면 `tm on`, "나중에"면 안내만): "작업을 시작할 땐 `tm on` 하세요 — 최신화하고 팀 맥락과 함께 엽니다." 셋업 직후의 막막함("이제 뭐하지?")을 막는 **다음 한 걸음**이다. ⚠️ tm off·tm-knowledge·tm-customize 등 나머지는 **여기서 설명하지 않는다** — `tm on` 웰컴과 각 스킬 트리거로 그때그때 자연히 드러난다(progressive, 온보딩 비대화 방지). 작업 스타일은 설명이 아니라 반복으로 밴다.

## 다음 단계 — 미완료 항목을 하나씩 빠짐없이 (중요)
첫 가치를 보여준 뒤, **위 체크표에서 미완료(⬜)인 항목을 순서대로 하나씩** 제안한다. 메뉴처럼 동시에 늘어놓진 말되(산만), ⚠️ **한 항목을 "나중에"라고 미뤘다고 거기서 온보딩을 끝내지 마라** — 그 항목만 건너뛰고 **다음 미완료 항목으로 넘어가 또 묻는다.** 미완료 항목을 한 번씩 다 물어본 뒤에 종료한다.

1. **서비스 연결(L2)** → 원하면 `tm-connect`, "나중에"면 **스킵하고 2로**.
2. **Obsidian 뷰 등록**(아래 섹션) → 원하면 진행, "나중에"면 스킵.

> 💡 **스킬·personality 커스텀**: 배너·인사말·유틸 스킬은 `tm-customize`로 언제든 설정할 수 있습니다. 나중에 합류한 팀원도 마찬가지 — `tm-customize`를 호출하세요.

- 각 항목은 **사용자 응답을 받은 뒤** 다음으로. 한 항목 "나중에"는 *그 항목만* 스킵이다 — **전체 종료가 아니다.**
- 사용자가 **"다 나중에"·"그만"이라고 명시**하면 그때 남은 항목을 일괄 건너뛰고 종료한다.
- 이미 된(✅) 항목은 묻지 않는다(체크표 감지). **미완료(⬜)만 하나씩 빠짐없이.**
- ⚠️ `update`(팀모드 업데이트)는 **갓 셋업한 팀에 제안하지 않는다** — 이미 최신이고, upstream 갱신이 실제로 생겼을 때만 의미 있다.

## 팀 personality 커스텀

팀 personality(배너·인사말)는 `tm-customize`로 입힌다 — 이 스킬에서 직접 진행하지 않는다.

## Obsidian 뷰 (opt-in, 키 0)
memory/가 마크다운이라 Obsidian으로 그래프처럼 볼 수 있다. **물어보고** 진행:
- **예** → `python infra/install.py --root . --register-obsidian`. 이 명령이 `.obsidian/`(dataview·graph) 생성 + `obsidian.json`에 **merge 등록**(기존 볼트 보존·멱등)을 한 번에 한다. **Obsidian 미설치면 둘 다 우아하게 skip**(아무것도 안 만듦) — 안 쓰는 사람 0 영향.
- **아니오/미설치** → 수동: "`<repo>/memory`를 Obsidian 'Open folder as vault'로 여세요" (`obsidian://open?path=<memory 절대경로>`).
- 키·토큰 0(로컬 파일). 단 `obsidian.json`은 실 호스트 설정이라 **동의(opt-in) 후에만.**
- **나중에도 가능**: 온보딩 때 안 했어도 이 액션은 **독립 실행**된다. 사용자가 "Obsidian 등록해줘"라고 하면(이미 셋업된 팀 레포에서) 다른 단계 없이 `python infra/install.py --root . --register-obsidian`만 실행한다. Obsidian 쓰기 시작한 시점에 언제든.

## 서비스 연결 (L2) — 제안+트리거. 실행은 `tm-connect`
이 스킬의 L2 몫은 **제안과 트리거**다(§5.3 4단계): 첫 가치(L1)를 보여준 *직후* "서비스(이슈 트래커·채팅·문서·캘린더) 연결할래요? 나중에 해도 돼요"라고 **강요 없이** 제안한다.

- 사용자가 **예**라고 하면 → **`tm-connect` 스킬**(`infra/skills/core/tm-connect/SKILL.md`)로 넘긴다. 토큰 안내·금고 저장·config 슬롯 기록·재배선은 전부 거기서 한다.
- **아니오/나중에** → L1만으로 끝낸다. 빈 슬롯은 1급 시민이라 정상 — 나중에 "서비스 연결해줘"라고 하면 그때 `tm-connect`.
- 여기서 토큰을 받거나 config 슬롯을 직접 채우지 않는다 — **제안까지가 tm-onboard, 실행은 tm-connect.**

## 안 하는 것 / 경계
- 코드 작성·이슈 생성·다른 스킬 자동 호출 안 함 — 온보딩만.
- 자가진단·검증은 `doctor`(별도, 나중) 몫.
- 푸시·PR은 사람 결정.

## Common Mistakes
| 실수 | 올바른 방법 |
|------|------------|
| `--member-name`으로 도입자/팀원을 가른다고 봄 | role은 install.py가 config 유효성으로 자동 판정. member-name은 이름일 뿐 |
| 사용자에게 "실배선/격리/dry-run 중 뭘로?" 물음 | 묻지 않는다 — 온보딩은 **항상 `--yes` 실배선**. 격리·dry-run은 개발/테스트 명시 요청 시만 |
| install.py 역할을 스킬이 손으로 재현 | install.py 호출하고 결과만 옮긴다 |
| 이름 충돌(exit 3)을 임의 해소 | 사람이 `--member-name <다른이름>` 재실행 |
| 이름을 git/계정/이메일에서 추론해 임의 확정 | git user.name은 *제안값* — **사용자 확인 후** `--member-name` 확정 |
| 서비스 연결을 tm-onboard 가 직접 실행 | 제안+트리거까지만 — 실행은 `tm-connect` 로 넘긴다 |
| 레포 만들 org·계정을 임의 선택(자동 진행) | 레포 새로 만들 때 어느 org·계정인지 **먼저 묻는다**(install 전 국면 0) |

---
> 동작이 예상과 다르면 `spec/04-install.md`(install.py)·`spec/05-onboard-skill.md`(이 스킬 설계)를 확인.
