# 스킬 시스템

teammode SPEC v0.2 — 스킬 계층(base/core) + tgates 이식 로드맵 + 온보딩 스킬 명세(§5)

## 스킬 계층 (base / core)

| 계층 | 설치·활성 시점 | 스킬 |
|---|---|---|
| **base** | 셋업 시 항상(install) | `tm-onboard` · `tm`(on/off 토글) |
| **core** | 팀모드 `on` 시 활성 | `tm-connect` · `tm-context` · `tm-customize` · `tm-knowledge` · `tm-manage-knowledge` |

- **base** = 팀모드를 켜고·끄고·셋업하는 최소 스킬. 항상 설치된다.
- **core** = 팀모드가 켜졌을 때만 활성(맥락·지식 운영). `off` 시 비활성.
- **util**(선택 설치) 계층은 현재 비어 있음. `dev-cycle` 등은 보류 — 범용 개발 메타라 teammode 코어(맥락 공유·서비스 연결) 정체성과 결이 다르다.

## 이식 로드맵 (tgates-toolkit → teammode)

tgates-toolkit의 검증된 스킬을 범용 teammode로 이식한다. tgates 특정 의존(`TGATES_HOME`·하드코딩 채널/DB ID)은 teammode 범용(`--root` 명시·`team.config.json` services 슬롯)으로 번역한다.

### 이번 이식 — L1 4개

| 스킬 | 출처(tgates) | L1 코어 (즉시 동작) | L2 graceful (연결 시 추가) | 선행 |
|---|---|---|---|---|
| **tm** (on/off) | `tgates` | 엔진 `on`/`off` 동사 래퍼 + 맥락 주입 + 세션로그(`log`/`commit`) | — | 경량 sync |
| **tm-load-knowledge** | `load-knowledge` | `memory/` INDEX 계층 로드(읽기 전용) | — | INDEX 구조 정책 |
| **tm-context** | `get-context` | 세션로그·decisions 요약 | Linear In Progress · Calendar | decisions 매니페스트 |
| **tm-manage-knowledge** | `manage-knowledge` | 파일 CRUD·INDEX 갱신·`commit` | Slack 알림 | INDEX 자동갱신(편집일) |

- **L1 코어로 즉시 동작**하고, L2 서비스(Linear/Calendar/Slack)는 연결됐을 때만 graceful 추가(미연결이면 조용히 skip).
- 이식 순서: `tm-load-knowledge`(제일 쉬움) → `tm` → `tm-context` → `tm-manage-knowledge`.

### L2 후속 (provider 연결 후 — 자리만)

| 스킬 | 출처 | 의존 |
|---|---|---|
| `tm-meeting` | `create-meeting` | **Notion(docs) 저장이 본질 → L1 아님** |
| `tm-tasks` · `tm-task` | get/set/create-tasks · start/end-task | Linear(issues) provider |
| `tm-schedule` | `schedule` | Calendar provider |

### 선행 인프라 (이식 전 필요)

1. **경량 sync** — `tm` on 시 훅만 갱신(현재 `install.py` 통째뿐, 부분 실행 모드 없음).
2. **session-start 훅** — 맥락 자동주입(`tm-context`의 자동화 버전).
3. **decisions 매니페스트** — `memory/team/decisions/current.md`(`tm-context`·`tm-meeting`가 의존, 현재 미정).
4. **멤버 이모지** — `team.config.json` 또는 `members.md`(선택).

### 제외 (이식 안 함)

| 스킬 | 이유 |
|---|---|
| `tgates-onboard` · `credentials` | teammode에 `tm-onboard`·금고 이미 있음 (중복) |
| `3d-modeling` · `soma-browse` | 그린고래/소마 팀특정 |
| `check-health` · `lint` · `cheer` | 제품/툴킷 특정 · 우선순위 낮음 |
| `dev-cycle` | 범용 개발 메타 — 코어 정체성과 결 다름 (보류) |

---

teammode SPEC v0.2 — tm-onboard·tm-connect (§5)

## §5. 온보딩 스킬 (tm-onboard)

> 이 절의 ground truth는 현재 워킹트리의 `infra/skills/base/tm-onboard/SKILL.md`, `infra/skills/core/tm-connect/SKILL.md`, `src/teammode/cli.py`이다. 2026-06-16 현재 워킹트리에는 `install-skills` 관련 미커밋 변경(`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py` 등)이 있으며, 이 절은 커밋 여부와 무관하게 **현재 구현된 스킬 본문**을 반영한다.
>
> **핵심 계약 변경(2026-06)**: 설치는 CLI(`teammode init` / `teammode join`)가 끝낸다. 스킬은 설치 후 ① 검증(서브에이전트 위임) ② 가치 전달(value.md)만 한다. 스킬이 `install.py`를 직접 호출하거나 멤버명·org·팀명·역할을 묻는 것은 **구 계약이며 폐기됐다.**

### 5.1 정체성·트리거

```yaml
name: tm-onboard
description: Use right after a teammode install (`teammode init` / `teammode join`) — when
  entering Claude Code/Codex in a freshly set-up team repo. Dispatches a verification
  subagent to confirm the install landed, and meanwhile conveys what teammode does for you.
triggers:
  - "tm-onboard"
  - "팀모드 온보딩"
  - "팀모드 시작"
  - "설치 잘 됐나"
  - "팀모드 셋업 확인"
  - when the CLI tells the user to open an agent and run tm-onboard
```

`tm-onboard`는 **`teammode init` / `teammode join` 설치 직후**, 에이전트로 처음 들어왔을 때 실행하는 스킬이다. 설치·레포 생성·clone은 CLI wizard가 이미 끝냈다. 스킬이 하는 일은 **딱 둘**: ① 설치 검증(검증 서브에이전트에 위임, 메인은 기다리지 않음), ② 팀모드 가치 전달(`value.md` 읽어 사람에게 전달).

같은 절에서 다루는 관련 스킬:

```yaml
name: tm-connect
description: Connect a service slot (issues / chat / docs / calendar) to a teammode team.
triggers:
  - "서비스 연결"
  - "이슈 트래커 연결"
  - "채팅 연결"
  - "문서 연결"
  - "캘린더 연결"
  - "팀모드 서비스 붙여줘"
  - "teammode connect"
  - "connect service"
  - after tm-onboard offers L2
```

`tm-connect`는 `tm-onboard`가 첫 가치 직후 제안한 L2 연결을 실제로 수행한다. 토큰 안내, 로컬 금고 저장, config 슬롯 기록, 재배선은 `tm-connect`의 책임이다.

### 5.2 CLI ↔ tm-onboard ↔ install.py 분업

| 단계 | 주체 |
|---|---|
| 레포 생성(`gh repo create --template`) | `cli.py` `cmd_init` |
| 팀 레포 clone | `cli.py` `cmd_join` (wizard 2단계 후 실행) |
| 멤버명·org·팀명·역할·에이전트·Obsidian 대화 | `cli.py` `_wizard_join` (TTY) / 인자 경로 (비-TTY) |
| preflight·detect·role 판정·scaffold·wire·env·verify | `install.py`. CLI가 subprocess로 위임 호출한다. |
| 설치 완료 안내("에이전트 열고 tm-onboard 입력") | `cli.py` `_done()` |
| 설치 검증(서브에이전트 위임) | `tm-onboard`. 메인은 기다리지 않고 병렬로 가치 전달. |
| 팀모드 가치 전달(`value.md` 읽어 사람에게) | `tm-onboard`. 검증 서브가 도는 동안 메인이 진행. |
| personality 커스텀 opt-in | `tm-customize` 스킬 (tm-onboard 범위 밖 — progressive). |
| Obsidian 등록 opt-in | CLI wizard 5단계에서 이미 묻거나, `install.py --register-obsidian` 직접. |
| L2 서비스 연결 제안 | `tm-onboard`는 다루지 않는다 — 각 스킬(`tm-connect`)이 그때 드러난다. |
| L2 서비스 연결 실행 | `tm-connect`. provider 데이터 안내, credentials 저장, config 슬롯 기록, 재배선. |
| 호스트 설치 되돌리기 | `install.py --uninstall` 직접 실행. 파괴적이라 사람 확인 먼저. |

**스킬이 하지 않는 것 (폐기된 옛 계약):**
- `install.py` 직접 호출 — CLI가 끝냈다. 재설치가 필요하면 `teammode join <url>` 재실행(멱등) 안내.
- 멤버명·org·팀명·역할 대화 — CLI wizard가 이미 받았다.
- 도입자/팀원 판정 설명 — CLI wizard가 처리했다.
- 설치 안 된 사람에게 설치를 시작 → `teammode init` / `teammode join <url>` CLI 안내 후 멈춤.

### 5.3 흐름 (설치 후 첫 진입 — 병렬)

> **전제**: `teammode init` 또는 `teammode join <url>` 이 이미 완료됐다. 에이전트는 clone된 팀 레포 루트에서 실행된다.

```
"tm-onboard" (또는 "팀모드 시작" / "설치 잘 됐나")
 1. 검증 서브에이전트를 즉시 디스패치한다 — 읽기 전용·수정 금지. 메인은 기다리지 않는다.
 2. (서브가 도는 동안) infra/skills/base/tm-onboard/value.md 를 읽고 가치를 사람에게 전달한다.
 3. 검증 결과 도착 → 종합:
    - 전부 ✅ → "설치도 정상 확인됐어요" 한 줄 매듭.
    - ❌ 항목 있음 → 무엇이 안 됐는지 짚고 → `teammode join <팀레포 URL>` 재실행 안내(멱등).
 4. 마무리: "작업 시작할 땐 `tm on` 하세요." 한 걸음 안내로 끝낸다.
```

검증 서브에이전트 확인 항목(SKILL.md §① 기준):
1. `python infra/teammode.py context --root <팀루트> --json` — 에러 없이 state 출력 (`state=off` 정상 — 설치 ≠ 활성화)
2. `memory/team/members.md` 멤버 등재, `memory/INDEX.md` 존재
3. `team.config.json` 존재 + `agents` 기록
4. 스킬 심링크 (claude=`~/.claude/skills`, codex 해당 경로)
5. 훅 배선 (`~/.claude/settings.json` 등)

install.py가 내부적으로 하는 일(참고 — 스킬이 재현하지 않는다):

- preflight, detect, role 자동 판정
- scaffold: `memory/INDEX.md`, `memory/team/members.md`, `memory/team/sessions/<이름>/`, 도입자면 빈 services config 등
- 훅 sync와 실 settings write(`--yes`일 때)
- env 주입
- verify: `context`로 설치 확인 (`on` 미사용 — active marker·settings 안 만듦, 설치 ≠ 활성화)

### 5.4 서비스 연결 스킬 (tm-connect)

#### 5.4.1 tm-connect — 역할 슬롯 연결

`tm-connect`는 역할 슬롯 `issues`, `chat`, `docs`, `calendar` 중 하나에 provider를 연결한다. 제품명을 하드코딩하지 않고 **역할 어휘**로 말한다. 실제 제품은 `team.config.json`의 `services.<역할>.provider`와 `providers/<provider>.json`이 결정한다.

provider 선택:

- 사용자가 연결하려는 역할을 정한다.
- 이미 `team.config.json`의 `services.<역할>.provider`가 있으면 그 provider를 읽는다.
- 슬롯이 비어 있으면 어떤 provider를 쓸지 사람에게 묻는다.
- `providers/<provider>.json`이 실제로 없으면 미지원이다. 추측해서 진행하지 않는다.

provider 팩에서 읽는 필드:

| 필드 | 용도 |
|---|---|
| `token_guide.url` | 토큰 발급 페이지 딥링크. 그대로 제시한다. |
| `token_guide.steps` | 발급 단계 목록. 순서대로 안내한다. |
| `auth` | 연결 방식. `api_key`, `oauth`, `bot_token` 중 값에 맞춰 멘트를 고른다. |
| `default_scope` | `team` 또는 `personal`. credentials namespace와 안내 기본값이다. |
| `resource_fields` | 연결 후 `team.config.json` 슬롯에 채울 인스턴스 필드명 목록이다. |
| `mcp.register_hint` | install-mcp 재배선 안내에 참고한다. |

토큰 발급 안내:

- `token_guide.url`과 `token_guide.steps`를 데이터로 읽어 안내한다. 링크·버튼·단계를 스킬 본문에 하드코딩하지 않는다.
- 막연히 "키 찾아와"라고 하지 않는다.
- 토큰 발급, OAuth 허용, 봇 설치, 공유 토글 같은 권한 부여는 사람이 직접 한다. 보안 경계라 무인 처리하지 않는다.
- 스킬은 "당신 몫은 토큰 N개뿐"으로 기대치를 고정해 토큰 병목을 줄인다.

`auth` 분기:

| `auth` | 안내 |
|---|---|
| `api_key` | 개인/통합 키를 Create → 복사 → 붙여넣기. attribution이 본인으로 남도록 각자 발급한다. |
| `bot_token` | 앱/봇 토큰을 발급하고 워크스페이스에 설치한 뒤 봇 토큰을 복사해 붙여넣는다. |
| `oauth` | localhost OAuth(PKCE). 사람이 동의 화면에서 허용하면 콜백으로 토큰을 받는다. 붙여넣기가 없을 수 있다. |

credentials 저장:

- 0.2에는 팀 토큰 자동공유가 없다.
- 각 멤버가 자기 토큰을 직접 입력한다. `default_scope`가 `team`이어도 도입자 1회로 끝나지 않는다.
- 저장 위치는 로컬 `$XDG_DATA_HOME/teammode/credentials/default.json`이다(단일 금고 — 멀티팀 미지원, 2026-06-21. 팀명에 묶이지 않아 개명 안전).
- 파일 권한은 0600이다.
- git 추적 대상이 아니다.
- 저장은 `infra/credentials.py`가 한다. 스킬은 평문 토큰을 stdout, 로그, 세션로그, config에 출력하거나 기록하지 않는다.

현재 스킬이 제시하는 저장 호출:

```bash
python -c "import sys; sys.path.insert(0,'infra'); import credentials; \
  credentials.store('<team>', '<scope>', '<역할>', input())"
```

- 토큰은 표준입력으로만 들어간다.
- 명령행 인자에 토큰을 싣지 않는다.
- 세션로그에 토큰을 쓰지 않는다.
- 0.2 금고는 평문 JSON이다. Syncthing, Dropbox, iCloud 같은 동기화 폴더에 두지 말라고 반드시 경고한다. 0600 권한, git 미추적, 동기화 폴더 금지가 0.2의 방어선이다. OS 키체인은 후속 영역이다.

config 슬롯 기록:

- 토큰 저장 뒤 실제 사용할 리소스, 예를 들어 문서 DB·채팅 채널·캘린더를 정한다.
- `resource_fields`가 config에 채울 인스턴스 필드 이름을 선언한다. 빈 리스트면 인스턴스 값이 필요 없다.
- `team.config.json`의 `services.<역할>` 슬롯에 `{ provider, scope, <resource_fields 각 필드 = 고른 값> }`을 기록한다.
- 토큰은 config에 적지 않는다. 토큰은 credentials 금고, 비밀이 아닌 인스턴스 값은 config가 소유한다.
- 팀 scope 슬롯의 provider·인스턴스 값을 도입자가 config에 커밋하면 팀원은 그 선언을 읽는다. 단 토큰은 0.2에서 각자 입력한다.

재배선과 첫 가치:

```bash
python infra/install.py --root . --yes
```

- 연결 뒤 install을 재실행해 adapter가 새 슬롯의 MCP를 등록하도록 한다. 빈 슬롯이 채워지면 sync가 해당 매처를 활성화할 수 있다.
- `mcp.register_hint`는 이 안내에 참고한다.
- 첫 가치는 issues 동사로 보여준다.

```bash
python infra/teammode.py issue create --root . --title "<요약>"
```

- 연결된 issues 슬롯이 있으면 정규 입력 스키마가 echo된다.
- 빈 슬롯이면 엔진이 `[info]`로 비치명 안내한다. 빈 슬롯은 에러가 아니다.

`tm-connect`가 하지 않는 것:

- 토큰 발급·동의 클릭을 대신하지 않는다.
- 평문 토큰을 stdout·로그·세션로그·config에 남기지 않는다.
- doctor 수준의 검증·자가수리는 하지 않는다. 연결 직후 유효 ping 정도까지만 범위다.
- 코드 작성, 이슈 본문 생성, 다른 스킬 자동 호출, 푸시, PR을 하지 않는다.

#### 5.4.2 호스트 되돌리기 (install.py --uninstall 직접)

tm-reset 스킬은 제거됐다. 호스트 설치 되돌리기는 `install.py --uninstall` 직접 실행으로 수행한다. 파괴적이므로 반드시 사람 확인을 먼저 받고, 되돌리는 범위를 고지한 뒤 실행한다. 상세 동작은 `docs/spec/internals.md §4.10(cmd_uninstall)`을 참조한다.

```bash
python infra/install.py --uninstall --root . --yes
```

- `install.py`가 off, Claude adapter hook uninstall, env 줄 제거, Obsidian 등록 해제를 처리한다. MCP 등록 제거와 skills 제거는 이 경로에서 처리하지 않는다.
- `--yes`는 실 settings에서 제거하는 쓰기 의도다. 격리 테스트 정리는 `--settings <settings-file-path>`를 쓴다.
- `memory/`는 삭제하지 않는다. 팀 데이터는 그대로 둔다.
- 레포 폴더 자체는 삭제하지 않는다. 통째 정리는 사람이 직접 `rm -rf <repo>`.
- 멱등·비치명: 이미 없으면 무동작.

### 5.5 경계 / 단일 책임

- `tm-onboard`는 온보딩과 L1 첫 가치까지만 직접 수행한다. L2는 제안만 하고 실행은 `tm-connect`로 넘긴다.
- `tm-connect`는 provider 데이터 기반 연결만 수행한다. provider 팩에 없는 제품·필드·발급 절차를 추측하지 않는다.
- 두 스킬 모두 install.py/engine/credentials가 하는 일을 손으로 재현하지 않는다.
- 실패(exit != 0)하면 사유를 전달하고 멈춘다. 추측 수리하지 않는다.
- 빈 서비스 슬롯은 1급 시민이다. 연결 전 L1 사용은 정상이다.
- 푸시·PR은 사람이 결정한다.

Common mistakes:

| 실수 | 올바른 방법 |
|---|---|
| **tm-onboard가 install.py를 직접 호출** | 설치는 CLI가 끝냈다. 스킬은 검증·가치 전달만. |
| **tm-onboard가 멤버명·org·팀명·역할을 다시 묻는다** | CLI wizard가 이미 받았다. 묻지 않는다. |
| **"셋업해줘"에 스킬이 설치를 시작** | `teammode init`(새 팀) / `teammode join <url>`(합류) 터미널 안내 후 멈춘다. |
| 검증을 메인이 동기로 붙잡고 함 | 검증 서브에이전트 디스패치 + 그 동안 메인이 가치 전달(병렬). |
| 검증 건너뛰고 "설치됐겠지" 가정 | 서브에게 실제 파일/명령으로 확인시킨다 — 특히 훅·스킬 심링크. |
| `install.py` 단계를 손으로 재현 | 안 됐으면 `teammode join <url>` 재실행 안내(멱등). |
| L2·Obsidian·personality를 메뉴로 나열 | 다루지 않는다. 각 스킬이 그때 드러난다(progressive). |
| 빈 팀(세션로그 0)을 실패로 말함 | 정상 — "지금부터 쌓인다"로 내레이션. |
| `--member-name`으로 도입자/팀원을 가른다고 봄 | role은 install.py가 config 유효성으로 자동 판정한다. (install.py 내부 참고용) |
| `--yes`를 단순 동의로만 안내 | `--yes`는 실호스트 settings write/remove 의도다. 격리는 `--settings`. |
| tm-connect: 서비스 연결을 tm-onboard가 직접 실행 | tm-onboard는 다루지 않는다. tm-connect 스킬이 그때 드러난다. |
| 발급 링크·단계를 하드코딩 | `providers/<provider>.json`의 `token_guide`와 `auth`를 읽어 안내. |
| 팀 scope면 도입자 1회로 끝난다고 안내 | 0.2는 각자 입력이다. 팀 scope도 각 멤버가 자기 토큰을 저장한다. |
| 토큰을 config·세션로그에 기록 | 토큰은 로컬 credentials 금고에만 둔다. config에는 인스턴스 값만 쓴다. |
| 평문 금고를 동기화 폴더에 둬도 된다고 안내 | 0.2 금고는 평문 JSON이다. 동기화 폴더 금지. |
| 빈 슬롯을 에러로 취급 | 빈 슬롯은 정상이다. 엔진은 `[info]` 비치명 안내를 낸다. |
| uninstall이 memory까지 지운다고 안내 | uninstall은 호스트 흔적만 되돌리고 `memory/`는 보존한다. |

---

