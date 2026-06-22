# 스킬 시스템

teammode SPEC v0.2 — 스킬 계층(base/core) + acme 이식 로드맵 + 온보딩 스킬 명세(§5)

## 스킬 계층 (base / core)

| 계층 | 설치·활성 시점 | 스킬 |
|---|---|---|
| **base** | 셋업 시 항상(install) | `tm-onboard` · `tm`(on/off 토글) |
| **core** | 팀모드 `on` 시 활성 | `tm-connect` · `tm-context` · `tm-customize` · `tm-knowledge` · `tm-manage-knowledge` |

- **base** = 팀모드를 켜고·끄고·셋업하는 최소 스킬. 항상 설치된다.
- **core** = 팀모드가 켜졌을 때만 활성(맥락·지식 운영). `off` 시 비활성.
- **util**(선택 설치) 계층은 현재 비어 있음. `dev-cycle` 등은 보류 — 범용 개발 메타라 teammode 코어(맥락 공유·서비스 연결) 정체성과 결이 다르다.

## 이식 로드맵 (acme-toolkit → teammode)

acme-toolkit의 검증된 스킬을 범용 teammode로 이식한다. acme 특정 의존(`LEGACY_TOOL_HOME`·하드코딩 채널/DB ID)은 teammode 범용(`--root` 명시·`team.config.json` services 슬롯)으로 번역한다.

### 이번 이식 — L1 4개

| 스킬 | 출처(acme) | L1 코어 (즉시 동작) | L2 graceful (연결 시 추가) | 선행 |
|---|---|---|---|---|
| **tm** (on/off) | `acme` | 엔진 `on`/`off` 동사 래퍼 + 맥락 주입 + 세션로그(`log`/`commit`) | — | 경량 sync |
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
| `acme-onboard` · `credentials` | teammode에 `tm-onboard`·금고 이미 있음 (중복) |
| `3d-modeling` · `acme-browse` | Acme/팀 팀특정 |
| `check-health` · `lint` · `cheer` | 제품/툴킷 특정 · 우선순위 낮음 |
| `dev-cycle` | 범용 개발 메타 — 코어 정체성과 결 다름 (보류) |

---

teammode SPEC v0.2 — tm-onboard·tm-connect (§5)

## §5. 온보딩 스킬 (tm-onboard)

> 이 절의 ground truth는 현재 워킹트리의 `infra/skills/base/tm-onboard/SKILL.md`, `infra/skills/core/tm-connect/SKILL.md`이다. 2026-06-16 현재 워킹트리에는 `install-skills` 관련 미커밋 변경(`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py` 등)이 있으며, 이 절은 커밋 여부와 무관하게 **현재 구현된 스킬 본문**을 반영한다.
>
> 공통 원칙: 사람이 할 판단·동의·권한 부여는 스킬이 대화로 처리하고, 결정적 파일 조작·배선·검증·되돌리기는 `install.py`/엔진/credentials 모듈에 맡긴다. 스킬은 install.py 단계를 손으로 재현하지 않는다.

### 5.1 정체성·트리거

```yaml
name: tm-onboard
description: Use at first contact with teammode — setting up a team repo or joining a team —
  or to register the team memory as an Obsidian vault at any time later.
triggers:
  - "이 레포 셋업해줘"
  - "팀모드 셋업"
  - "팀모드 시작"
  - "온보딩"
  - "팀모드 합류"
  - "teammode setup"
  - "Obsidian 등록"
  - "옵시디언 볼트 만들어줘"
  - when handed a teammode repo to set up
```

`tm-onboard`는 teammode를 처음 켜는 스킬이다. 생애주기상 **팀 셋업(도입자 1회) → 개인 셋업(각 멤버) → 서비스 연결(L2, 나중)** 중 앞의 L1 부트스트랩과 첫 가치 내레이션을 담당한다. L1은 세션로그와 세션 시작 맥락 자동 주입이다.

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

### 5.2 install.py ↔ tm-onboard 분업

| 단계 | 주체 |
|---|---|
| preflight·detect·role 판정·scaffold·wire·env·verify | `install.py`. 스킬은 명령을 호출하고 결과를 사람 말로 옮긴다. |
| 도입자/팀원 자동 판정 설명 | `tm-onboard`. 단, 판정 자체는 `install.py`가 `team.config.json` 유효성으로 한다. |
| 이름 제안·이름 충돌 안내 | `tm-onboard`가 안내, `install.py`가 검증·충돌 판정. |
| 첫 가치(context) 실행과 요약 | `tm-onboard`가 `teammode.py context --json`를 실행하고 사람 말로 요약. |
| personality 커스텀 opt-in | `tm-onboard`. greeting/farewell은 도입자 config, banner는 `memory/banner.txt`. |
| Obsidian 등록 opt-in | `tm-onboard`가 `install.py --register-obsidian`만 호출. |
| L2 서비스 연결 제안 | `tm-onboard`. 실행은 하지 않는다. |
| L2 서비스 연결 실행 | `tm-connect`. provider 데이터 안내, credentials 저장, config 슬롯 기록, install 재배선. |
| 호스트 설치 되돌리기 | `install.py --uninstall` 직접 실행(`python infra/install.py --uninstall --root . --yes`). 파괴적이라 사람 확인 먼저. |

도입자/팀원 분기는 `--member-name`으로 하지 않는다. `install.py`가 `team.config.json`의 유효성을 보고 자동 판정한다.

- config 없음 또는 미초기화: 도입자/팀 셋업. config를 새로 쓴다.
- 유효 config 존재: 팀원/개인 셋업. config는 팀 상태의 근거로 읽으며, 현 구현은 `team.config.json.members`의 **자기 엔트리만** upsert할 수 있다. team/services/admin 등 팀 공통 필드는 보존한다.
- `--member-name`은 양쪽 경로에서 author/member 이름을 정하는 인자일 뿐 role 스위치가 아니다.
- `install.py`는 아직 role을 `--json`으로 출력하지 않는다. 스킬이 실행 전에 굳이 알아야 하면 `team.config.json` 유효성, 즉 `team.name`이 placeholder가 아니고 `spec_version`이 있는지를 직접 확인한다. `--json`이 생기면 그쪽으로 전환한다.

호스트 안전 게이트:

- `--yes`는 단순 동의 플래그가 아니라 실 `~/.claude/settings.json` 등에 훅·스킬·MCP를 배선하는 실설치 의도다.
- 격리 설치는 `--settings <경로>`로 한다.
- 변경 없이 보려면 `--dry-run`을 쓴다.
- `--yes`와 `--settings`가 모두 없으면 install은 wire를 건너뛰고 끝난다. 실호스트를 건드리지 않는다.
- reset의 `--yes`는 실 settings에서 teammode 훅을 제거하는 쓰기 의도다. 격리 되돌리기는 `--settings <경로>`를 쓴다. 둘 다 없으면 uninstall은 실호스트를 건드리지 않고 거부한다.

### 5.3 흐름 (progressive — L1 먼저, L2 당길 때)

```
"이 레포 셋업해줘"
 1. 도입자/팀원은 install.py가 자동 판정한다고 알린다.
 2. python infra/install.py --root . --member-name <영문이름> --yes
 3. 실패(exit != 0)면 사유를 사람 말로 옮기고 멈춘다.
 4. python infra/teammode.py context --root . --json
 5. context 결과를 "지금 팀 상황: ..."으로 요약한다.
 6. 필요하면 personality 커스텀과 Obsidian 등록을 opt-in으로 묻는다.
 7. L2 서비스 연결을 강요 없이 제안한다. 예: "서비스(이슈 트래커·채팅·문서·캘린더) 연결할래요? 나중에 해도 돼요."
```

공통 셋업 명령:

```bash
python infra/install.py --root . --member-name <영문이름> --yes
```

인자와 기본 동작:

- `--root .`는 필수 관행이다. 팀 루트는 환경변수로 추측하지 않고 명시한다.
- `--member-name <영문이름>`은 권장이다. 생략하면 install이 git `user.name` 기반 제안을 쓴다. 팀원은 이름 충돌 회피를 위해 명시하는 편이 안전하다.
- `--yes`는 실호스트 배선까지 포함한 설치다. 격리 검증은 `--settings <경로>`, 무접촉 계획 확인은 `--dry-run`을 쓴다.

install.py가 하는 일:

- preflight
- 팀 상태 감지
- role 자동 판정
- scaffold: `memory/INDEX.md`, `memory/team/members.md`, `memory/team/sessions/<이름>/`, 도입자면 빈 services config 등
- 훅 sync와 실 settings write(`--yes`일 때)
- env 주입
- verify: `context`로 설치를 확인한다(`on` 미사용 — active marker·settings 안 만듦).

멱등성과 분기:

- 재실행은 정상 경로다. scaffold·등록·배선은 중복을 만들지 않는 방향으로 install.py가 처리한다.
- 이름 충돌, 즉 다른 사람이 같은 이름으로 등재된 것으로 판정되면 install.py가 exit 3과 안내를 낸다. 스킬은 추측해 고치지 않고 사람이 `--member-name <다른 영문이름>`으로 재실행하게 한다.
- 어떤 실패든 exit code가 0이 아니면 스킬은 사유를 전달하고 멈춘다. 후속 context·연결을 추측 진행하지 않는다.

첫 가치:

```bash
python infra/teammode.py context --root . --json
```

- 결과를 그대로 덤프하지 않고 사람 말로 요약한다.
- 설치는 팀모드를 자동으로 켜지 않는다 — `--yes`/`--settings`로 wire+verify까지 완주해도 `state=off`가 정상이다(설치 ≠ 활성화). verify는 `context`로 설치를 확인하고 **팀모드는 켜지 않는다**(on 미사용 — on의 auto_update 부작용 회피). `state=on`은 사용자가 `tm on`(또는 tm-onboard 제안 동의)으로 명시했을 때만 — 설치하며 켜는 단축 플래그는 두지 않는다.
- 갓 만든 팀은 세션로그가 0개일 수 있다. 이때는 "구조는 섰고, 다음 작업부터 자동 기록·주입됩니다"라고 설명한다.
- 팀원은 기존 팀 로그가 있으면 context에서 보인다. 다음 세션부터는 `session-start.py` 훅이 팀원별 최근 세션로그를 자동 주입한다.

팀 personality 커스텀은 opt-in이다.

- 먼저 "배너·시작멘트(greeting)·끝맺음말(farewell) 커스텀할래요? 기본값 그대로 둬도 됩니다"라고 묻는다.
- 예라고 하면 시작 멘트와 끝맺음말은 `team.config.json`의 `team.greeting`, `team.farewell`을 교체한다. 도입자 config에는 기본값 `"<팀> 팀모드 ON"`, `"수고하셨습니다 — <팀>"`이 있다.
- 엔진 `on`은 배너 직후 greeting을 출력하고, `off`는 farewell을 출력한다. farewell이 없으면 "상태 저장됨"을 출력한다.
- 배너는 **picker**로 고른다. `infra/banners/`에 ansi_shadow·slant·chunky·cyberlarge·larry3d·speed 6종의 정적 ASCII 아트 후보가 있다. 각 후보를 `cat`으로 보여주고 사용자가 고른 폰트를 `cp infra/banners/<폰트명>.txt memory/banner.txt`로 복사해 적용한다. 배너는 TEAM/MODE 텍스트 고정이며, 팀명은 엔진이 배너 아래 greeting으로 동적 출력한다. 6종 중 원하는 것이 없으면 `memory/banner.txt`를 직접 작성해도 된다(임의 ASCII 아트 자유).
- 아니오면 기본 greeting/farewell과 자동 배너(`=== <팀> ===`)를 그대로 둔다.
- config의 greeting/farewell은 팀 스코프다. 도입자가 바꾸고 커밋하면 팀원에게 공유된다. 팀원은 개인 취향으로 이 값을 바꾸지 않는다.

Obsidian 뷰도 opt-in이다.

```bash
python infra/install.py --root . --register-obsidian
```

- `memory/`가 Markdown이므로 Obsidian 볼트로 볼 수 있다.
- 예라고 하면 위 명령만 실행한다. `.obsidian/` dataview·graph 설정 생성과 `obsidian.json` merge 등록을 install.py가 처리한다.
- 기존 볼트는 보존하고 멱등으로 등록한다.
- Obsidian이 미설치면 우아하게 skip한다. 안 쓰는 사람에게 영향이 없다.
- 아니오 또는 미설치면 수동 대안으로 `<repo>/memory`를 Obsidian의 "Open folder as vault"로 열라고 안내한다. `obsidian://open?path=<memory 절대경로>` 링크도 가능하다.
- 키·토큰은 없다. 다만 `obsidian.json`은 실 호스트 설정이므로 동의 뒤에만 건드린다.
- 이 액션은 독립 실행된다. 온보딩 때 건너뛰었더라도 나중에 "Obsidian 등록해줘"라고 하면 다른 온보딩 단계 없이 이 명령만 실행한다.

L2 연결 제안:

- `tm-onboard`의 L2 책임은 제안과 트리거뿐이다.
- 사용자가 예라고 하면 `tm-connect` 스킬로 넘긴다.
- 아니오 또는 나중에 하겠다고 하면 L1만으로 끝낸다. 빈 슬롯은 정상 상태다.
- `tm-onboard`는 토큰을 받거나 config 서비스 슬롯을 직접 채우지 않는다.

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
| `--member-name`으로 도입자/팀원을 가른다고 봄 | role은 install.py가 config 유효성으로 자동 판정한다. |
| `--yes`를 단순 동의로만 안내 | `--yes`는 실호스트 settings write/remove 의도다. 격리는 `--settings`. |
| tm-onboard가 서비스 연결을 직접 실행 | tm-onboard는 제안과 트리거까지만, 실행은 tm-connect. |
| 발급 링크·단계를 하드코딩 | `providers/<provider>.json`의 `token_guide`와 `auth`를 읽어 안내. |
| 팀 scope면 도입자 1회로 끝난다고 안내 | 0.2는 각자 입력이다. 팀 scope도 각 멤버가 자기 토큰을 저장한다. |
| 토큰을 config·세션로그에 기록 | 토큰은 로컬 credentials 금고에만 둔다. config에는 인스턴스 값만 쓴다. |
| 평문 금고를 동기화 폴더에 둬도 된다고 안내 | 0.2 금고는 평문 JSON이다. 동기화 폴더 금지. |
| 빈 슬롯을 에러로 취급 | 빈 슬롯은 정상이다. 엔진은 `[info]` 비치명 안내를 낸다. |
| uninstall이 memory까지 지운다고 안내 | uninstall은 호스트 흔적만 되돌리고 `memory/`는 보존한다. |
| scratch repo가 uninstall로 사라진다고 봄 | 폴더는 남는다. 통째 정리는 별도 삭제다. |

---

