# teammode — 단일 권위 스펙 (SPEC)

| | |
|---|---|
| spec_version | **0.2** |
| 상태 | 정식 단일판 (reconciled with build, 2026-06-15; 0.2 — issue 동사 추가) |
| 범위 | 팀 메모리 표준 · 훅/어댑터 표준 · 엔진 동사 · 설치/부트스트랩 · 온보딩 스킬 · 호환 선언 · 서비스 슬롯 |
| 대체 관계 | 본 문서가 흩어진 `spec/01`~`spec/05`를 **통합·대체**한다(부록 D의 repoint 목록 참조). |
| 표기 규약 | **필수 / 권장 / 예약** (§0.3) |

> **reconcile 원칙**: 설계(`spec/`)와 빌드(`infra/`·`conformance/`)가 어긋나면 **빌드(코드)가 진실**이다. 본문은 코드로 닫힌 미결을 closed로 서술하고, 차이는 부록 A에 명시한다. 04/05 draft가 미결로 남겼으나 구현으로 답이 난 것은 본문에서 확정으로 기술한다. 코드보다 앞선(미구현) 설계 항목은 본문에서 "예약/로드맵"으로 명시한다.

---

## §0. 개요 · 용어 · 표기 규약

### 0.1 한 문장

> 팀의 작업 맥락(세션로그·결정·상태)을 **git 레포 하나에 마크다운으로** 모으고, 어떤 AI 코딩 에이전트(Claude Code · Codex · …)로 읽고 쓰든 동일한 팀 메모리를 공유하며, 에이전트가 **세션 시작 시 그 맥락을 자동으로 읽어** 들어오는 크로스에이전트 팀 협업 툴킷.

teammode는 두 직교 축으로 설계된다: **에이전트 축**(§2 — 같은 훅/스킬 내용 1벌, 에이전트별 표기는 어댑터가 번역)과 **서비스 축**(§7 — 같은 역할을 팀마다 다른 제품으로). 데이터 표준(§1)은 두 축의 토대다.

### 0.2 용어

| 용어 | 정의 |
|---|---|
| **팀 레포** | 팀 메모리와 엔진 설정을 담는 git 저장소. 팀당 1개, private. |
| **팀 루트** | 팀 레포의 로컬 클론 경로. |
| **세션로그** | 멤버가 에이전트 세션에서 수행한 팀 작업의 일지(§1.3). |
| **작업일** | 06시 컷 적용 후의 날짜(§1.4). |
| **주입(injection)** | 세션 시작 시 팀 메모리 일부를 에이전트 컨텍스트에 자동 로드(§1.6). |
| **정규형 (canonical)** | 에이전트 무관하게 teammode가 정의하는 표준 어휘 — 정규 이벤트(§2.4)·행위 클래스(§2.5)·정규 입력 스키마(§2.10). |
| **어댑터 (adapter)** | `infra/agents/<name>/` 폴더. 설치 시점에 정규 선언을 해당 에이전트의 설정으로 번역·등록(§2.7). |
| **normalize 심 (shim)** | 런타임에 에이전트의 훅 입력 JSON을 정규 스키마로 변환하는 얇은 통역 계층(§2.10). |
| **행위 클래스 (action)** | 빌트인 툴의 에이전트 무관 추상화. 예: `file_edit` = Claude의 `Write\|Edit` = Codex의 `apply_patch`. |
| **공통 스크립트** | `infra/hooks/*.py`. 정규 스키마만 인지하며 특정 에이전트를 모름. |
| **역할 슬롯 (service slot)** | issues/chat/docs/calendar 등 서비스의 도구 중립 추상화(§7). |
| **reference 구현** | 본 레포의 구현. Tier 1 = Claude Code 기준. 코드: `infra/`·`conformance/`. |
| **독립 구현** | 본 레포 코드와 별개로 작성된, 본 스펙 준수를 목표로 하는 구현(§6). |
| **L1 / L2 / L3** | 도달 단계: L1 = 세션로그+훅+맥락수집(install.py 자력 도달), L2 = 서비스 연결, L3 = 다이제스트 등. |

### 0.3 표기 규약

- **필수** — 따르지 않으면 비준수. conformance 검사 대상.
- **권장** — 따르지 않아도 비준수는 아니나, 정당한 사유가 있어야 한다.
- **예약** — v0.1에서 자리만 정의하고 의미를 확정하지 않은 항목. 임의 사용 금지.

### 0.4 버저닝 (전 영역 공통)

- 본 SPEC의 모든 영역(§1~§7)은 **단일 `spec_version`을 공유**하며, 현재 **0.1**이다. 어느 영역이든 규범 변경이 생기면 버전이 함께 오른다. 팀 레포 `team.config.json`의 최상위 `spec_version` 필드는 그 팀 데이터가 따르는 버전을 선언한다.
- **minor bump 대상(필수 + CHANGELOG 기록)**: 세션로그 포맷 필드의 추가/의미 변경, 폴더 구조 변경, 정규 이벤트(§2.4)·행위 클래스(§2.5)·정규 입력 스키마(§2.10)·어댑터 계약(§2.7)·엔진 동사 계약(§3)·conformance 검사 항목(§6.4)의 변경. 오탈자·설명 보강 등 의미 불변 수정은 버전 불변.
- **0.x 기간에는 하위 호환이 깨질 수 있다.** 독립 구현이 2개 이상 등재되면 1.0으로 동결하고 RFC-lite 변경 절차(제안 이슈 → 구현 영향 검토 → 합의 후 머지)를 도입한다.
- 구현·어댑터는 자신이 지원하는 spec_version을 명시해야 한다(필수, §6).
- CHANGELOG는 스펙을 배포하는 본진 레포에서 관리한다(팀 레포가 아니다).

---

## §1. 팀 메모리 표준 (Team Memory)

팀 메모리의 데이터 표준: 디렉토리 구조, 세션로그 포맷, 컨텍스트 주입 규칙. 어떤 에이전트로 읽고 쓰든 이 표준을 따르면 같은 팀 메모리를 공유한다 — 크로스에이전트 호환의 토대.

### 1.1 디렉토리 구조

팀 메모리는 팀 루트의 `memory/` 아래에 있다. 위치는 **필수**.

```
memory/
├── INDEX.md                      # 메모리 인덱스 — 세션 시작 시 주입되는 단일 진입점
├── banner.txt                    # 팀 배너 캐시 (권장 표준 위치 — 엔진·install이 실사용)
└── team/
    ├── members.md                # 멤버 명부 — 영문 이름(소문자)·역할·연락의 단일 소스
    ├── sessions/<이름>/          # 멤버별 세션로그(§1.3). <이름>=members.md의 영문 이름
    ├── decisions/                # 확정된 결정사항
    │   ├── current.md            #   활성 결정
    │   └── archive/              #   과거 결정
    └── meeting/
        ├── summary/              # 회의록 요약본
        └── raw/                  # 회의 원본 (STT·텍스트)
```

- **INDEX.md (필수)** — 폴더별 "여기에 넣는 것" 설명을 표로 유지. 새 폴더를 만들면 INDEX.md 갱신이 필수. 등재되지 않은 폴더는 주입·탐색 대상에서 누락된다. (install.py가 스캐폴딩하는 기본 INDEX.md 표는 `install_lib._INDEX_MD` 참조.)
- **members.md (필수)** — 멤버 영문 이름은 시스템 계정명(`$USER`)이 아니라 이 파일에 등재된 이름이다. 코드·훅·스킬은 이름을 하드코딩하지 말고 이 파일을 참조해야 한다(필수). 영문 이름은 **소문자·팀 내 고유**(폴더명·frontmatter가 이 이름을 그대로 쓴다). 멤버 항목 라인 포맷(reference 구현, §4.4와 정합): `- <name>  <!-- id: <identity> -->`. `id` 주석은 install.py가 동일인/타인을 결정적으로 가르는 데 쓰며(§4.4 충돌 정책) 없어도 호환된다. 멤버 **역할(role)**은 members.md가 아니라 `team.config.json`의 `members` 배열에 둔다(L2-A2, Jane 결정 2026-06-16): `members: [{name, role?}]` — 각 멤버가 install 시 자기 `name` 엔트리만 upsert(각자 upsert)하며 타인 엔트리는 무접촉. `role`은 권장 어휘(developer/pm/designer/…) 또는 자유문자열, 생략 가능. 빈 배열·`members` 키 없음도 valid(기존 0.1/0.2 config 무회귀). ⚠️ `members` 블록은 role **판정**(`config_is_valid`)과 완전 분리 — 스키마 위반은 `[warn]`만 발화하고 도입자/팀원 판정을 뒤집지 않는다. members.md의 연락 필드 상세 포맷은 v0.2 확정(예약).
- **sessions/<이름>/** — 세션로그(`YYYY-MM-DD.md`) 외 보조 파일을 둘 수 있다(주입·검사 대상 아님). 단 **`YYYY-MM-DD`로 시작하는 `.md` 파일명은 세션로그 네임스페이스로 예약**되어 보조 파일에 쓸 수 없다(`-late` 등 분할 파일은 §1.3 위반 검사 대상). 네임스페이스 판정: stem 길이 ≥10, `stem[:4]` 숫자, `stem[4]=='-'`, `stem[7]=='-'` (reference: `teammode._is_session_log_name`).
- **decisions/** — "확정된" 결정만. 논의 중인 사안은 세션로그·회의록에 머문다.

**권장·예약 항목:**

| 경로 | 분류 | 내용 |
|---|---|---|
| `team/reviews/` | 권장 | 외부 평가·피드백. 파일명 `YYYY-MM-DD-출처-단계.md` |
| `team/ground-rules.md` | 권장 | 팀 운영 그라운드 룰(엔진/다이제스트가 참조할 표준 위치) |
| `banner.txt` | 권장 | 팀 배너 캐시 표준 위치. `team.config.json`의 `banner_file`이 가리킴(reference: `memory/banner.txt`) |

**팀 확장 (자유)**: `memory/` 아래 자유 폴더 추가 가능(예: `product/`, `extras/`). 규칙 두 가지 — ① 기존 폴더로 충분하면 새 폴더 금지(증식 방지, 권장), ② 새 폴더는 INDEX.md에 등재(필수).

### 1.2 쓰기 위치·팀 루트·env 규칙 (필수)

- **쓰기 위치**: 팀 메모리 쓰기는 항상 **팀 루트의 `memory/`** 에 한다. 작업 중인 코드 레포에 우연히 있는 `./memory/`에 쓰는 것은 금지. 구현은 세션 리마인더 등으로 혼동을 방지하길 권장(reference: `session-log-remind.py`).
- **런타임 훅의 팀 루트 = 환경변수**: 런타임 훅은 에이전트 하니스가 발동하므로 `--root` 같은 인자 통로가 없다. 따라서 구현은 런타임 훅이 참조할 **환경변수를 제공해야 한다(필수)**. reference 변수명: **`TEAMMODE_HOME`** (없으면 cwd 폴백; read-only이라 P1 사고 표면이 아님). reference 훅: `session-start.py`·`session-log-remind.py`·`auto_pull.py`.
- **엔진/어댑터/설치의 팀 루트 = 명시 인자만(필수)**: on/off·log·context·pull·commit·update·install 등 **의도적으로 호출되는** 동사는 팀 루트를 **명시 인자(`--root`)로만** 받는다. 환경변수 폴백·cwd 추측을 해서는 안 되며, `--root`가 없으면(설치는 cwd 표식 검증 후 표식이 없으면) **동작하지 말고 에러로 종료**한다(exit 2). 근거: ambient env(예: 호스트 toolkit을 가리키는 `LEGACY_TOOL_HOME`)가 새어들어 격리 하니스를 우회한 직접 호출이 호스트의 상태 마커(`.acme-active` 등)·`memory/banner.txt`를 건드린 실사고(P0/P1). 엔진이 "어느 폴더를 건드릴지 추측하지 않게" 하는 것이 근본 처방.
- **에이전트 설정 경로도 명시 only(필수)**: `~/.claude/settings.json` 등 실 사용자 설정은 **명시 경로(`--settings`) 또는 명시적 설치 플래그(`--install`/`--yes`)** 없이는 건드리지 않는다(§3.1·§4.5).

### 1.3 세션로그 — 위치·단위·상세도 (필수)

- 경로: `memory/team/sessions/<이름>/YYYY-MM-DD.md`. `<이름>`은 members.md 영문 이름.
- **하루 1파일.** 같은 작업일의 로그는 기존 파일에 이어 쓴다. `-late`, `-2` 등 분할 파일 생성 금지.
- `YYYY-MM-DD`는 작업일(§1.4)이며 frontmatter의 `date`와 일치해야 한다.
- **상세도 기준(필수)**: 세션로그는 "한 일 목록"이 아니다. **나중에 읽어도 그때의 맥락을 완전히 복원** — 단일 기준. 각 항목은 한 흐름으로: ① 무엇을 했나 ② 왜 그렇게 결정했나 ③ 접은 대안 ④ 막힌 점 ⑤ 다음 단계.
- **팀 작업만(필수).** 개인 일정·사적 내용 제외.
- 같은 날 여러 세션은 시각 구분선(예: `## 14:30`)으로 이어 쓴다(권장). reference 엔진 `log`는 append 시 `## HH:MM`(KST) 구분선을 자동으로 넣는다.
- 구현은 일정 시간(reference: 30분) 이상 로그 미갱신 시 세션 내 리마인드를 제공하길 권장 — 규율을 사람 습관이 아니라 훅이 들게 하는 것이 핵심 설계(reference: `session-log-remind.py`, age≥1800초 또는 5프롬프트 주기).

### 1.4 06시 컷 (필수)

작업일 경계는 자정이 아니라 **06:00(팀 timezone)** 이다.

- 00:00~05:59에 작성을 시작하는 로그 → **전날** 날짜 파일.
- 06:00 이후 → 당일 파일.
- **판정 시점 = 그 로그 작성을 시작한 시각**. 세션 도중 06:00을 넘겨도 시작 시점의 작업일 파일에 계속 이어 쓴다 — 한 흐름을 두 파일로 찢지 않는다.
- reference 단일 소스: `infra/workday.py` (`workday(now)`·`workday_str(now)`, 순수 함수). reference timezone은 KST 고정. 스펙상 timezone은 `team.config.json`의 `team.timezone`이며 config 주입은 reference의 확장 여지(현 코드는 KST 상수).

### 1.5 frontmatter (필수)

```markdown
---
author: jane-doe
date: 2026-06-11
summary: 훅 어댑터 레이어 설계 확정 — events.json 번역표, 폴백 정책, 서비스 추상화까지
---
```

| 필드 | 필수 | 정의 |
|---|---|---|
| `author` | 필수 | members.md 영문 이름. `$USER` 등 시스템 계정명 금지 |
| `date` | 필수 | 작업일(06시 컷 적용). 파일명과 일치 |
| `summary` | 필수 | 그날 작업의 **한 줄** 요약(권장 100자 이내) |

- 위 3필드는 필수 최소 집합. 팀·구현이 추가 필드를 두는 것은 허용.
- **`summary`** — 스케일 주입(§1.6)·context 수집·대시보드(로드맵)가 공용으로 읽는다. 하루 중 내용이 바뀌면 대표 내용으로 **교체**(append 아님). reference 엔진 `log`는 첫 기록 시 `--text` 첫 줄(100자)로 summary를 **초기화**하며, 갱신(교체) 판단은 하지 않는다 — 갱신은 스킬/사람 몫(엔진 철학: 기계적 재료손질, 요약/판단은 안 함).
- **마이그레이션**: v0.1 이전 `summary` 없는 로그는 비준수로 취급하지 않는다. 신규 작성분부터 필수.

### 1.6 주입 규칙 (스케일)

세션 시작 시 구현은 팀 메모리를 컨텍스트에 주입한다. 전원 전문 주입은 인원에 비례해 컨텍스트가 폭발하므로 팀 크기에 따라 스케일한다.

| 팀 크기 | 세션로그 주입 방식 |
|---|---|
| **~4인** | 전원 최근 로그 **전문** 주입 |
| **5인 이상** | 전원 `summary` 한 줄 + **본인** 로그 전문 + 동료 상세는 **lazy load** |

- **팀 크기** = members.md 등재 멤버 수.
- **주입 기본 단위** = 멤버별 **가장 최근 작업일 파일 1개**(전문이든 summary든). 구현은 더 넓게(최근 N일) 제공할 수 있으나 기본값은 1파일.
- 대상 파일에 `summary`가 없으면(구 로그) 그 멤버의 **summary 주입은 생략** — 전문 폴백 주입 금지(컨텍스트 폭발 방지가 목적).
- reference: `teammode._collect_members`가 멤버별 최근 1파일의 `{author, date, summary, file}`을 수집(요약 안 함). `session-start.py` 훅이 INDEX + 멤버별 summary 라인을 `additionalContext`로 주입. ⚠️ **현 reference는 팀 크기 분기(~4인 전문 / 5인+ summary)를 구현하지 않고 항상 summary 라인 기반 주입**이다 — 부록 A 참조. v0.1에서 스케일 규칙은 conformance 자동검사 대상이 아니며(§6.4 K-list 외), 골든 시나리오 "컨텍스트 조회"로 주입 방식을 확인한다.
- `team.config.json`의 `groups` 키는 **v0.1 예약어**(스쿼드 단위 주입 범위용). 구현은 무시해야 하며 `null` 외 값에 의미를 부여하지 않는다. v0.2 확정.
- **적용 범위**: v0.1 타겟 팀 크기는 **2~5인**. 7인 이상 동작은 본 스펙이 보증하지 않는다.

---

## §2. 훅 · 어댑터 표준 (Hook & Adapter)

> 훅·스킬·MCP의 **내용은 1벌**만 유지하고, 에이전트마다 다른 **표기·등록 방식·입력 스키마는 `agents/<name>/` 어댑터가 번역**한다. 새 에이전트 지원 = 어댑터 파일 3개 추가, 기존 코드 무접촉.

### 2.1 디렉토리 구조

```
infra/
├── hooks/                       # 공통 — 1벌
│   ├── manifest.json            #   정규형 선언 (§2.3)
│   └── *.py                     #   공통 스크립트. 정규 스키마(§2.10)만 인지, 에이전트 무지
├── skills/                      # 공통 — 1벌 (오버라이드 규칙 §2.12)
├── agents/
│   └── <name>/                  # 에이전트별 어댑터 — 파일 3개
│       ├── adapter.py           #   설치 시점 번역기 (§2.7)
│       ├── events.json          #   번역표 (§2.6)
│       └── normalize.py         #   런타임 통역사 (§2.10)
└── install.py                   # 디스패처 겸 부트스트랩: --<agent> 플래그 → agents/<name>/ 위임
```

**구조 불변식 (필수):**
1. 공통 스크립트와 스킬에 에이전트 고유 표기를 쓰지 않는다.
2. 에이전트 고유 지식은 전부 `agents/<name>/` 아래에만.
3. 디스패처는 분기 로직을 갖지 않는다 — 어댑터 CLI에 위임만. (reference: `install._split_agent`/`_dispatch` — `--<agent>`가 `agents/<name>/` 디렉토리와 일치하면 위임.)

### 2.2 매니페스트 엔트리 형식

`infra/hooks/manifest.json`은 훅 엔트리 배열이며 **에이전트 무관 정규형으로만** 선언한다.

```jsonc
{
  "event": "PostToolUse",                 // 필수. 정규 이벤트 (§2.4)
  "match": { "action": "file_edit" },     // 선택. 정규 매처 (§2.5). 생략 = 전체 매칭
  "script": "auto-commit.py",             // 필수. hooks/ 하위 공통 스크립트
  "args": "",                             // 선택. 기본 ""
  "timeout": 5000,                        // 선택. ms. 기본값은 구현 정의
  "mode": "on",                           // 선택. 생략 = base(상시) / "on" = 팀 모드 켜진 동안만
  "fallback": "runtime",                  // 선택. "runtime" | "drop". 기본 "drop" (§2.9)
  "strict": false,                        // 선택. 기본 false. normalize 변환 실패 정책 (§2.10)
  "enforcement": "advisory"               // 선택. "advisory" | "block". 폴백 경고 강화용 (아래)
}
```

- **`enforcement`** (reference 코드의 실필드, 02 draft 미언급 → 본문 확정): `"block"`이면 폴백으로 비활성될 때 "차단 강제 상실"을 `[warn]`에 명시(예: Codex가 PreToolUse 미지원 → block 훅이 무음 누락되지 않게). 기본 `"advisory"`. reference 어댑터(claude/codex sync)가 읽어 경고 문구를 강화한다.
- **금지(필수)**: manifest에 에이전트 고유 표기 직기 — `mcp__*` 형식 툴명, `Write|Edit` 같은 매처 문자열, `apply_patch`, 특정 에이전트 설정 파일 경로. 전부 정규형으로만. (lint/conformance 검사 대상 — reference: `check._lint_manifest_canonical`이 `mcp__`/`Write|Edit`/`apply_patch` grep.)

### 2.3 reference manifest (현 빌드)

reference 빌드는 4개 엔트리를 선언한다. **3개는 공통 스크립트 파일이 실재**(session-start.py·session-log-remind.py·auto_pull.py를 호출하는 부분)하고, **2개는 manifest에만 선언되고 스크립트 파일은 아직 부재**(`auto-commit.py`·`confirm-action.py`)다 — 부록 A 갭 참조.

| event | match | script | mode | fallback | enforcement | strict | 스크립트 실재 |
|---|---|---|---|---|---|---|---|
| `SessionStart` | (없음) | `session-start.py` | on | (drop) | advisory | — | ✅ |
| `UserPromptSubmit` | (없음) | `session-log-remind.py` | on | (drop) | advisory | — | ✅ |
| `PostToolUse` | `action: file_edit` | `auto-commit.py` | (base) | runtime | block | — | ❌ 미구현 |
| `PreToolUse` | `mcp: {linear, create_issue}` | `confirm-action.py` | (base) | runtime | block | true | ❌ 미구현 |

### 2.4 정규 이벤트 (v0.1)

| 정규 이름 | 의미 | 의미 보존 요건 |
|---|---|---|
| `SessionStart` | 세션 시작 | 세션당 1회, 사용자 첫 입력 전 발화 |
| `UserPromptSubmit` | 사용자 프롬프트 제출 직후 | 에이전트가 응답 생성 시작 전 발화 |
| `PreToolUse` | 툴 실행 직전 | **차단 가능해야 함** — 훅 실패(비정상 exit)가 툴 실행을 막을 수 있어야 한다 |
| `PostToolUse` | 툴 실행 직후 | 툴 결과 확정 후 발화 |

- 정규 이름은 **Claude Code 어휘 기준**(Tier 1 Reference).
- 정규 이벤트 추가는 minor bump로만.
- 에이전트가 어떤 정규 이벤트를 표현 못 하면 어댑터 번역표(events.json)에 `null`로 **명시**(무음 누락 금지).

### 2.5 정규 매처

`match` 객체에는 정확히 하나의 키만(필수).

```jsonc
{ "action": "file_edit" }                                  // (a) 빌트인 행위 클래스
{ "mcp": { "server": "linear", "tool": "create_issue" } }  // (b) MCP 툴 — 정규 서버명
```

- **정규 행위 클래스 v0.1**: `file_edit`(파일 생성·수정) 하나만. `shell_exec`·`file_read` 등은 필요 입증 시 minor bump로 추가.
- **정규 서버명(필수)**: MCP 서버 등록 별칭은 환경마다 다르다(`slack-acme`, `claude_ai_Google_Calendar` 등). manifest는 `services` 선언(§7)의 **정규 서버명**(provider 식별자: `linear`·`slack`·`notion`·`google` 등)만 참조한다. 정규 서버명 → 실제 등록 별칭 매핑 보장은 어댑터의 등록 시점 책임(§2.8).

### 2.6 events.json — 어댑터 번역표

각 어댑터는 정규 어휘 → 자기 에이전트 표기 번역표를 **데이터로** 선언한다(번역 규칙이 코드 분기에 숨지 않게).

```jsonc
// agents/claude/events.json (reference)
{
  "agent": "claude",
  "config_file": "~/.claude/settings.json",
  "events": { "SessionStart": "SessionStart", "UserPromptSubmit": "UserPromptSubmit",
              "PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse" },
  "actions": { "file_edit": "Write|Edit" },
  "mcp_tool_format": "mcp__{server}__{tool}"
}
// agents/codex/events.json (reference) — PreToolUse: null(미지원), file_edit: "apply_patch",
//   mcp_tool_format: "{server}.{tool}"
```

규칙(필수):
1. `events`에는 §2.4의 **모든 정규 이벤트 키가 존재**해야 한다. 미지원이면 `null` — 키 누락 금지(lint 대상).
2. `actions`에는 v0.1의 모든 정규 행위 클래스 키 존재. 미지원이면 `null`.
3. 에이전트별 특수 처리(이벤트 skip·매처 변형)를 설치 코드에 하드코딩 금지 — 전부 이 파일로.
4. `mcp_tool_format`의 치환 변수는 `{server}`·`{tool}` 둘. `{server}`에는 어댑터가 해석한 **실제 등록 별칭**이 들어간다.

### 2.7 adapter.py — 설치 시점 계약

어댑터는 다음 CLI를 구현해야 한다(필수). 디스패처가 호출한다.

```
adapter.py sync [--on|--off]     # manifest → 에이전트 설정 동기화 (멱등)
adapter.py uninstall             # teammode 등록 훅 역순 제거
adapter.py install-skills        # 스킬 설치 (§2.12 오버라이드 해석)        [예약 — reference 미구현]
adapter.py install-mcp           # MCP 서버 등록 (정규 서버명 → 자기 방식)   [예약 — reference 미구현]
```

- reference 어댑터(claude·codex)는 **`sync`·`uninstall`만** 구현한다. `install-skills`·`install-mcp`는 계약상 존재하나 reference 빌드 미구현(L2 슬라이스) — 부록 A.
- `sync`를 **플래그 없이** 실행하면 마지막 적용 on/off 상태 유지 재동기화. 한 번도 적용된 적 없으면 **off로 간주**(필수, reference: 무플래그 → base 엔트리만).
- MCP 매처가 있는 manifest를 동기화하려면 `install-mcp`가 선행돼야 한다(§2.8 별칭 보장). 전제 위반 시 해당 **MCP 매처 엔트리만** `[warn]` 후 생략, 나머지 정상 — 전체 실패 금지(필수).

**`sync`의 의무(필수):**
1. manifest 각 엔트리를 events.json으로 번역해 자기 설정에 등록. `--on`/`--off`는 `mode: "on"` 엔트리의 활성/비활성을 전환.
2. **등록 훅 커맨드는 반드시 normalize 경유로 배선**: `<python> agents/<name>/normalize.py <script> [args]`. 공통 스크립트 직접 등록 금지. `<python>`은 **크로스플랫폼**: 설치 시점 `sys.executable`(인터프리터 절대경로 — Windows 에서 `python3` 가 PATH 에 없거나 venv 여도 견고; normalize 도 child 실행에 `sys.executable` 사용 = 체인 일관). 공백 든 경로(Windows `C:\Program Files\...`)는 따옴표로 안전 인용. reference: `Adapter.default_python`/`build_command`.
3. 미지원 이벤트/매처는 §2.9 폴백 적용. **무음 스킵 금지** — 생략 시 `[warn]` 의무.
4. **멱등성**: 재실행 시 변경 없으면 설정 파일도 무변경. manifest에서 제거된 훅은 설정에서도 제거. (reference: settings.json 직렬화 텍스트 동일성 비교; Codex는 `# teammode-hooks-start/end` 블록 단위 멱등 교체.)
5. **소유권**: teammode가 등록한 항목만 추가·수정·삭제. 사용자 직접 등록 훅은 무접촉. 식별 마커 = 등록 커맨드가 **팀 루트 하위 `agents/<name>/normalize.py`를 가리키는지**(단순 `agents/` 부분문자열 판정 금지 — 무관 경로 오인 삭제 방지). reference: `Adapter.is_owned`.

### 2.8 install-mcp의 의무 (계약 — reference L2 미구현)

1. `services` 선언(§7)을 읽어 연결된 provider의 MCP 서버를 자기 방식으로 등록(필수).
2. 정규 서버명 → 실제 등록 별칭 매핑을 이 시점에 확정·보장(필수). **기본 규칙: 별칭을 정규 서버명과 동일하게 등록.** 에이전트 제약으로 불가능할 때만 어댑터가 매핑을 자체 영속화하고 `sync`가 읽어 매처 문자열 생성. reference `resolve_server_alias`는 현재 항등(정규명=별칭).
3. 레포 내 MCP 코드는 경로 직접 참조 등록 권장(pull로 코드 자동 갱신). lock 해시 비교로 변경 시에만 의존성 재설치 권장.

### 2.9 폴백 정책

manifest 엔트리의 `fallback` — 어댑터가 그 엔트리를 자기 에이전트로 표현 못 할 때 동작.

| 값 | 발동 조건 | 동작 |
|---|---|---|
| `"drop"` (기본) | 이벤트 또는 매처를 표현 불가 | 등록 생략 + `[warn] <script>: <agent> 미지원 → 비활성`(필수, 무음 금지) |
| `"runtime"` | 매처만 표현 불가(이벤트는 지원) | 무매처로 등록 + normalize 자가 필터(§2.10-2)로 의미 보존 |

- `"runtime"`인데 **이벤트 자체가 미지원**(events.json `null`)이면 표현 방법이 없어 `"drop"`과 동일 동작 + `[warn]`(필수). reference 어댑터: `event is None`이면 runtime이어도 drop.
- **빈 슬롯 우선 규칙(필수)**: `mcp` 매처가 참조하는 provider 역할 슬롯이 미연결(§7.2)이면 `fallback` 무관 등록 생략 + `[info]` — 에러 아님(빈 슬롯 = 1급 시민). 슬롯 연결 후 `sync` 재실행으로 활성화.
- 선택 가이드: 빠져도 되는 편의 → `drop` / 빠지면 안 되는 안전장치(확인·차단류) → `runtime`(+필요시 `strict`).

### 2.10 normalize — 런타임 계약

**입력**: 에이전트 원어 JSON(stdin) → **출력**: 공통 스크립트에 정규 JSON(stdin) 전달.

**정규 입력 스키마 (canonical input) v0.1:**

```jsonc
{
  "event": "PostToolUse",            // 필수. 정규 이벤트 (§2.4)
  "action": "file_edit",             // 해당 시. 정규 행위 클래스
  "tool": { "kind": "mcp",           //   해당 시(Pre/PostToolUse). "mcp" | "builtin"
            "server": "linear",      //   kind=mcp일 때. 정규 서버명
            "name": "create_issue" },
  "files": ["/abs/path"],            // file_edit일 때. 대상 파일 절대 경로 배열
  "prompt": "사용자 입력 …",          // UserPromptSubmit일 때
  "agent": "codex",                  // 필수. 출처 에이전트명
  "raw": { }                         // 선택. 원어 전문(탈출구). 생략 시 {}
}
```

필드 필수성: `event`·`agent` 필수, `raw` 선택(생략 시 `{}`), 나머지는 해당 시. 공통 스크립트는 이 스키마만 신뢰하고 `raw`는 최후 수단으로만 읽는다(권장).

**normalize의 의무(필수):**
1. **변환**: 원어 → 정규 스키마. (reference: Claude `{hook_event_name, tool_name, tool_input, prompt}` → 정규형. `_parse_mcp`로 `mcp__server__tool` 역파싱, `_reverse_action`으로 행위 클래스 역매핑.)
2. **런타임 자가 필터**: `fallback: "runtime"`으로 무매처 등록된 훅은, manifest에서 자기 엔트리 `match`를 조회해 현재 발동의 내용(행위 클래스 또는 MCP 서버·툴)이 불일치하면 `exit 0`(무동작). 조회 키 = **(script, 현재 정규 이벤트) 쌍**. 이를 위해 **같은 (event, script) 중복 엔트리 금지**(필수, lint 대상). 수 ms 안에 끝나야 한다.
3. **시맨틱 전파**: 공통 스크립트 exit code·stdout을 그대로 전파. 특히 `PreToolUse` 차단 시맨틱 보존. (reference: subprocess returncode·stdout/stderr 그대로 반환.)
4. **변환 실패 시**: 훅 실패로 세션을 막지 않는다 — `exit 0` + stderr 경고. **예외**: `"strict": true` 엔트리는 변환 실패도 훅 실패로 전파(reference: 해당 script의 strict 여부로 exit 1/0 분기).

### 2.11 크로스에이전트 (Claude ↔ Codex)

- **번역 코어 공유(reference)**: Codex 어댑터는 Claude `Adapter`를 상속해 번역 코어(events.json 기반)를 재사용하고, Codex 고유의 **config 포맷(TOML 블록) + 폴백/enforcement 축소**만 재정의. Codex normalize는 Claude normalize의 함수를 import해 경로 상수(events.json·manifest)만 Codex 컨텍스트로 재바인딩.
- **Codex 한계(정직한 표면화)**: Codex는 events.json에서 `PreToolUse: null`이라 PreToolUse 차단 훅이 등록되지 않으며, `enforcement: block`이면 sync 시 `[warn] … (block 강제 상실) → 비활성`을 출력한다. ⚠️ **Codex 실 훅 입력 JSON 스키마는 미확인**(Claude 유사 형태 가정) — 부록 A·B.
- 독립 구현은 `agents/` 디렉토리 구조나 Python을 그대로 쓸 필요 없다. 보존 대상은 **선언 포맷(manifest.json·events.json)과 의미**이지 구현 언어·파일 배치가 아니다(§6 C2).

### 2.12 스킬 해석 — 단일 소스 + 오버라이드

스킬 본문은 1벌(`infra/skills/`)이 원칙. `install-skills`(계약)의 탐색 순서(필수):

```
1. agents/<name>/skills/<skill>/SKILL.md   ← 있으면 이것 (오버라이드)
2. infra/skills/**/<skill>/SKILL.md        ← 폴백 (공통본)
```

규칙:
1. 오버라이드는 **구조적 분기**(서브에이전트 문법 등)에만(필수). 표기 차이는 오버라이드 사유 아님.
2. **MCP 표기 규약(필수)**: 스킬 본문은 역할 어휘 + 시맨틱 참조만 — "이슈 트래커 MCP의 list_issues로 조회"(제품명 "Linear" 직표기 금지 §7.3). `mcp__*` 직표기도 금지(둘 다 K7 lint 대상). 에이전트별 실제 툴명 형식·역할↔제품 매핑은 각 에이전트 진입 문서(CLAUDE.md/AGENTS.md류)·config에 1회만.
3. **드리프트 경보(권장)**: 오버라이드 파일 생성 시 lint가 "공통본 변경 시 오버라이드 검토" 목록에 등재.

---

## §3. 엔진 동사 (teammode.py) — 신규 명문화

엔진 `infra/teammode.py`는 8개 동사를 구현한다. **공통 계약(필수)**: 팀 루트는 `--root`로만 받고(§1.2 P1), env 무신뢰, `--root` 미지정 시 exit 2. 알 수 없는 동사는 exit 127(`[unimplemented]`), 동사 없으면 usage + exit 2. 엔진은 **요약·판단을 하지 않는다**(기계적 재료손질만 — 요약은 스킬/에이전트 몫).

```
teammode.py <verb> --root <팀루트> [동사별 플래그]
verbs: on | off | log | context | pull | commit | update | issue
```

값 받는 플래그 화이트리스트: `--root --settings --author --text --now --message --title --body --assignee --label --priority`. 부울: `--install --json --push`. 화이트리스트 밖 `--flag`의 다음 토큰은 값으로 삼키지 않는다(verb 손실 방지). **positional**: 첫 non-flag = verb, 이후 non-flag = positional(예: `issue create`의 `create` 서브액션) — `--root`가 verb와 서브액션 사이에 끼워져도 정상 파싱(value 플래그가 토큰쌍으로 소비되므로).

### 3.1 on / off (settings 경유 — `--root` + (`--settings`|`--install`) 필수)

- on/off만 어댑터 sync를 호출하므로 `~/.claude`를 건드린다. 따라서 settings 경로도 **명시로만**: `--settings <경로>`(격리) 또는 `--install`(실설치 → `~/.claude/settings.json`) 중 **하나 필수**. 둘 다 없으면 exit 2(실 `~/.claude` 추측 오염 거부, P2).
- **on**: ① 배너 출력(`_render_banner`: `memory/banner.txt` 있으면 그대로, 없으면 `ACME_TEAM_NAME` env 또는 `acme`로 최소 배너 생성·캐시) + **시작 멘트(greeting) 출력**(`team.config.json`의 `team.greeting` 있으면) ② Claude 어댑터 `sync(mode="on")` ③ `.acme-active` 마커 기록 ④ upstream **fetch만** 자동 — behind면 `teammode update` 안내(merge 절대 자동 금지; 미설정·오프라인·git 아님이면 조용히 패스, on을 막지 않음). exit 0.
- **off**: 어댑터 `sync(mode="off")` → `.acme-active` 마커 삭제 → **끝맺음 말(farewell) 출력**(`team.config.json`의 `team.farewell` 있으면, 없으면 "상태 저장됨"). exit 0.
- **active 마커**: 팀 모드 활성 상태 표식 = 팀 루트의 `.acme-active`(빈 파일). 런타임 훅은 이 마커가 있을 때만 동작한다.

### 3.2 log (세션로그 기록 — `--root --author --text` 필수)

- `--author`(필수)·`--text`(필수)·`--now`(선택 ISO8601, 기본 실시각 KST).
- author 검증(필수): 빈 문자열·경로 구분자(`/`,`\`)·`.`/`..`·절대경로·선두 `-`/`_`·비영숫자(영숫자·`-`·`_` 외) 거부 → 위반 시 exit 2. 경로 traversal·플래그 오인(footgun) 차단(`_validate_author`).
- 작업일 = `workday_str(now)`(06시 컷, §1.4). 경로 = `memory/team/sessions/<author>/<작업일>.md`. 이중 방어로 정규화 후 경로가 sessions_dir 밖이면 exit 2.
- 파일 없으면 frontmatter(author/date/summary) + `## HH:MM` 항목 작성, summary = text 첫 줄(100자). 파일 있으면 frontmatter 재작성 없이 `## HH:MM\n\n<text>` append(하루 1파일). exit 0.

### 3.3 context (맥락 수집 — `--root` 필수, `--json` 선택)

- 전원 세션로그·INDEX·active 상태를 긁어 출력. **요약 안 함** — frontmatter의 summary/date를 그대로 옮길 뿐.
- `_collect_members`: 멤버별 최근 1파일(파일명=작업일 사전식 정렬)에서 `{author, date, summary, file}`. 로그 0개 멤버는 건너뜀. summary 없는 구 로그는 summary=""(전문 폴백 금지, §1.6).
- **역할 보강(L2-A2)**: `team.config.json`의 `members` 배열에서 `author`로 매칭한 `role`을 각 멤버에 보강한다(config 미등재·role 생략 시 `role=None`). config 부재·손상이어도 무크래시(role 전부 None).
- **텍스트 모드**: `=== teammode context ===` / `state: on (active)|off` / `--- INDEX ---` / `--- members … ---`(멤버별 summary 라인 + file 경로). role 있으면 멤버 식별자를 `이름(role)`로 표기, 없으면 이름만.
- **`--json` 모드**: `{"state": "on"|"off", "index": <INDEX.md 전문>, "members": [{author,date,summary,file,role}, …]}`(`role`은 string 또는 null). 스킬·install verify가 파싱하는 구조화 출력. exit 0.

### 3.4 pull / commit / update (git 동기화 — `--root` 필수)

git 작업은 공통 안전장치 `infra/git_ops.py` 재사용(자격증명 프롬프트 차단 `GIT_TERMINAL_PROMPT=0`·`--ff-only`·손자 killpg·타임아웃 5초). **실패는 비치명**(우아한 축소) — 작업을 막지 않고 exit 1 + 사유, 크래시 0. 엔진은 워킹트리를 오염시키지 않는다.

- **pull**: `git pull --ff-only --no-rebase --no-edit`. 성공 exit 0, git 아님·오프라인·ff불가·타임아웃 → exit 1 + stderr 안내.
- **commit**: `--message`(필수) → `git add/commit (push)`. `--push`로 push 동반. 변경 없음·git 아님은 비치명 exit 1. push 실패는 로컬 커밋을 되돌리지 않는다(커밋 보존). 성공 exit 0.
- **update**: upstream(템플릿) 명시적 `fetch` + `merge --ff-only`. ff 불가(divergent)·upstream 미설정·오프라인 → 비치명 exit 1(워킹트리 무오염, 사람 판단 유도). 첫 병합(unrelated histories)은 ff가 막으므로 `allow_unrelated`는 라이브러리 옵션(기본 비활성, 자동 merge/conflict 강행 금지). 최신이면 exit 0. (on의 자동 fetch와 분리된 의도적 적용 단계.)

### 3.5 issue (서비스 슬롯 동사 — `--root` 필수, 첫 positional = 서브액션)

**altitude(필수, context 동사와 동일)**: 엔진은 `issues` 슬롯의 연결 provider를 **확인**하고 **정규 입력 스키마를 stdout JSON으로 echo까지만** 한다. **`action_map` 해석·페이로드 변환·실 MCP 호출은 하지 않는다**(그건 어댑터/스킬 몫 — §3 "엔진은 판단 안 함"). 엔진은 사용자 입력을 정규 어휘로 정리해 내보낼 뿐, 무엇을 어떻게 호출할지 판단하지 않는다.

- **입력**: 첫 positional = 서브액션(예: `create`). 정규 입력 필드 플래그: `--title --body --assignee --label --priority`(값 화이트리스트). `--root`가 verb와 서브액션 사이에 끼워져도 정상 파싱(§3 positional 규칙).
- **슬롯 확인**: `team.config.json`의 `services.issues.provider`를 읽고, 그 provider 팩이 `providers/`에 실재할 때만 연결로 인정(미지 provider 추측 금지 — `providers.lookup` None이면 미연결). 슬롯 조회는 비치명(부재·파싱실패·예외 모두 미연결로 흡수, issue 동사를 크래시시키지 않음).
- **빈 슬롯**(미연결·config 부재·미지 provider): `[info]` 안내 + **exit 0**(비치명, 빈 슬롯 = 1급 시민 §7.2). echo 안 함.
- **연결 슬롯**: 정규 입력 스키마 `{"verb":"issue","action":<서브액션|null>,"service":"issues","provider":<이름>,"input":{<설정된 필드만>}}`를 **1줄 JSON으로 echo** + exit 0. 미설정 입력 필드는 생략.
- **인젝션 면역(필수, V.4 회귀락)**: 사용자 텍스트는 `json.dumps`로만 직렬화한다 — 셸/JSON 인젝션 불가(엔진은 페이로드를 셸·다른 JSON 문맥에 보간하지 않음). 원문은 변환 없이 그대로 보존(엔진은 해석 안 함).

---

## §4. 설치 · 부트스트랩 (install.py)

> 사람이 에이전트에 치는 첫 한마디 **"이 레포 셋업해줘"** → 에이전트가 `python install.py`를 대신 실행 → 팀 레포가 감지·스캐폴딩·에이전트 배선·env·훅까지 한 번에 서고, 끝에서 `context`로 **L1 데이터가 읽히는지** 확인한다. install.py는 **결정적 고정 스크립트**이며 LLM 판단(서비스 선택)은 하지 않는다.

### 4.0 설계 원칙

| 원칙 | 의미 |
|---|---|
| **코어 ≠ 스킨** | install.py는 설치 코어. 진입 스킨("셋업해줘"·pipx·npx·플러그인)은 결국 install.py를 호출하는 얇은 층. |
| **결정적** | 같은 입력 → 같은 결과. 판단 지점(이 이름이 나인가, 어느 DB인가)은 명시 인자로 받거나 멈춘다(추측 금지). |
| **L1 자력 도달** | 서비스 연결(L2) 없이도 install.py 단독으로 세션로그 디렉토리+훅+맥락 수집(L1)까지. 빈 서비스 슬롯은 정상(§7.2). |
| **크로스에이전트** | 설치된 에이전트(Claude/Codex/…)를 감지해 각 어댑터로 배선. 에이전트별 배선은 독립(§4.6). |
| **판단은 위임** | "어느 Notion DB·어느 캘린더"는 install.py 범위 외 → onboard 스킬(§5). 그 자리를 빈 슬롯으로 두고 끝낸다. |

**범위 외**: 서비스 OAuth/토큰 발급·리소스 선택 → onboard 스킬(§5). 팀 레포 *생성*(`gh repo create --template`) → 에이전트의 cold-start 선행 동작(install.py는 이미 받아진 레포 안에서 실행 가정). 호스팅·대시보드·다이제스트 → 별도 트랙.

### 4.1 CLI 계약

```
python install.py [--root PATH] [--agent {auto|claude|codex|...}]
                  [--member-name NAME] [--settings PATH] [--yes]
                  [--update] [--dry-run]
                  [--register-obsidian [--obsidian-config PATH]]
# 디스패치 모드(보존): install.py --<agent> sync [--on|--off] / uninstall
```

| 플래그 | 의미 | 기본 |
|---|---|---|
| `--root PATH` | 팀 루트 명시. env 무신뢰(§1.2). | 미지정 시 cwd가 팀 표식 가지면 cwd, 아니면 exit 2 |
| `--agent` | 배선 대상. `auto`=설치 감지(§4.6). | `auto` |
| `--member-name` | 세션로그 author(영문·소문자·고유). git user.name 제안. `--yes`에서 이름 못 정하면 exit 3(추측 금지). | git user.name → 제안 |
| `--settings PATH` | 에이전트 설정 쓰기 타깃 오버라이드(격리 디렉토리). 미지정=실호스트. CI/conformance는 격리 경로. | 실호스트 기본 |
| `--yes` | 비대화 모드 + **실호스트 배선 동의**(§4.5). | off |
| `--update` | (계약상) 이미 설치된 팀 레포 재배선. 데이터 무접촉. **reference 미사용** — 부록 A. | off |
| `--dry-run` | 변경 없이 계획만 출력. settings·memory·env 무접촉. | off |
| `--register-obsidian` | Obsidian 볼트 등록 단독 액션(§4.7, opt-in). | off |
| `--obsidian-config PATH` | obsidian.json 경로 오버라이드. 미지정=플랫폼 기본. | 플랫폼 기본 |

**종료 코드**: `0` 성공 / `2` 전제·인자 오류(무변경) / `3` 부분 실패 또는 해소불가 충돌(어디까지 됐는지·무엇이 막혔는지 stderr 명시 — 필수).

### 4.2 전제조건 (preflight)

| 전제 | 등급 | 없을 때 |
|---|---|---|
| Python ≥ `MIN_PYTHON` (reference **3.9**) | 필수 | exit 2 + 안내 |
| `git` 바이너리 | 필수 | exit 2(메모리가 git 기반) |
| GitHub 원격 인증 | 협업 필수 / L1 로컬엔 불요 | **경고만**(로컬 L1 진행). preflight는 인증 부재로 종료하지 않는다 |
| install.py가 팀 레포 안에서 실행 | 필수 | 팀 표식(`.git`/`team.config.json`/`memory`) 없으면 exit 2 |
| `gh` CLI | 권장 | 경로 A(템플릿 자동생성)에만. 없으면 "웹 Use this template 후 다시" 안내 |

원격 인증은 협업(pull/commit/push)의 전제일 뿐, 로컬 L1은 push 없이 성립 → 인증 부재 = 경고. 경로 A(템플릿)는 upstream remote가 설정되고, 경로 B(직접 clone)로 upstream 없으면 `update`/템플릿풀이 우아하게 축소(스킵).

### 4.3 절차 (멱등하게 순서대로)

```
① preflight   Python 버전·git 바이너리·팀 표식 검사. 원격 인증은 경고만. (실패 즉시 종료, 무변경)
② detect      git remote→org/repo, git user.name→이름 제안, user.email→identity,
              설치 에이전트(~/.claude·~/.codex), 원격 인증, role(③)
③ role        team.config.json 유효(spec_version + team.name 비-placeholder) → 팀원(§4.4)
              부재/미초기화 → 도입자(§4.4). ※ services 채움 여부로 가르지 않음(빈 슬롯 정상)
④ scaffold    memory/ 구조·INDEX·members.md 등재·banner 선기록, 도입자는 최소 config.
              ※ 첫 세션로그는 쓰지 않는다(디렉토리만)
⑤ wire        감지 에이전트마다 어댑터 sync(훅 등록). 실호스트 배선은 --yes/--settings 필요(§4.5).
              에이전트별 독립 — 하나 실패가 다른 배선을 막지 않음(§4.6)
⑥ env         런타임 훅용 TEAMMODE_HOME 영구 주입 — POSIX:셸 프로파일 1줄 / Windows:setx (§4.8)
⑦ verify      teammode on(배너+훅+active 마커) → teammode context --json 으로 L1 데이터 읽힘 확인.
              ※ 실제 *맥락 주입*은 여기가 아니라 다음 세션의 SessionStart 훅(§1.6·§2.4)
```

reference: `install.bootstrap`이 ①~⑦ 전부 오케스트레이션. `install_lib`이 순수/주입 가능 함수(테스트가 호스트 무접촉). ⑦ verify는 엔진을 subprocess로 호출하되 **env 화이트리스트**(`PATH/HOME/LANG/…`)로 ambient `TEAMMODE_HOME`/`LEGACY_TOOL_HOME` 누수 차단(P1 이중 방어).

### 4.4 도입자/팀원 경로 (role)

**role 판정(closed — `config_is_valid`)**: `team.config.json`이 dict이고 `spec_version`(truthy) + `team.name`(str, placeholder 아님)이면 **팀원(member)**, 아니면 **도입자(introducer)**. placeholder = `{"", "changeme", "todo", "your-team-name", "team-name", "tbd", "placeholder"}`. services 채움 여부로 가르지 않는다.

- **도입자**: 최소 config 작성(LLM 불요) — `spec_version`(0.1)·`team{name(기본=레포명),timezone(감지 또는 Asia/Seoul),locale(감지 또는 ko_KR)}`·`admin_contact(이름)`·`members_file`·`banner_file`·**`greeting`(시작 멘트 기본값)·`farewell`(끝맺음 말 기본값)**·`services: {}`(전부 빈 슬롯, 키 생략). 이미 유효 config면 무수정(멱등). memory/ 스캐폴딩 + members.md 본인 등재 + banner 선기록. **첫 세션로그 안 씀**(closed — M2) — 첫 로그는 첫 실작업 세션의 훅 흐름으로 생성.
- **팀원**: config 작성 안 함(읽기만). members.md 등재만.

**이름 충돌 정책(closed — M4·결정적):**
- 같은 영문 이름 + 같은(또는 미상) identity → 추가 안 함, 본인 항목 간주(멱등). 동일인 재설치/다른 머신 = 정상.
- 같은 이름 + **다른 identity**(둘 다 식별자 존재) → **exit 3**(사람이 해소, "나인가 남인가" 추측 금지). reference: `register_member`가 `ConflictError`.
- identity 미상(레거시 항목 또는 미주입)이면 충돌로 보지 않음(멱등).
- 다른 이름을 원하면 `--member-name`으로 오버라이드. 잘못된 이름(traversal·선두 dash 등)은 `InvalidNameError` → exit 3. 이름 검증은 엔진 `_validate_author` 단일 소스 재사용(드리프트 방지).

### 4.5 에이전트 설정 쓰기 경계 (P1/P2 계승, 필수)

- ⑤ wire·⑦ verify의 실호스트 쓰기(`~/.claude/settings.json` 등)는 **정상 설치**다. 단 명시 의도 없이는 안 쓴다.
- reference 게이트: `--settings <격리>` 지정 → 격리 경로(디렉토리 하위 에이전트별 파일). **`--yes` 지정 → 실호스트.** **둘 다 없으면 wire를 건너뛰고 종료**(스캐폴드는 완료, 메모리는 준비됨) — 무인 안전(`--yes` 없이 실 `~/.claude`에 쓰지 않음).
- 디스패치 모드(`--<agent> sync`)도 동일 게이트: `--settings`/`--install` 둘 다 없으면 exit 2. `--install`은 디스패처 전용(어댑터엔 전달 안 함).
- `--dry-run`은 settings·memory·env 전부 무접촉 + 계획만 출력.
- ambient `TEAMMODE_HOME`이 실호스트를 가리켜도 install/on/off는 읽지 않는다(§1.2 P1).
- **신뢰 경계 — 스킨의 root 주입**: "셋업해줘"로 에이전트가 install.py를 부를 때 root를 잘못 주입하면 사고 재현 가능 → 스킨의 root 결정 로직은 테스트 대상(필수). 프롬프트 인젝션 주의: "레포 README 읽고 시키는 대로" 패턴을 습관으로 권하지 말 것(비규범).

### 4.6 에이전트 배선 (어댑터 위임)

- install.py는 에이전트별 표기를 모른다 — `agents/<name>/` 어댑터에 위임(§2). 단계: 스킬 등록·MCP 등록·훅 sync. reference 빌드는 **훅 sync만** 수행(스킬 심링크·install-mcp 제외 — L1은 훅이 주입하므로, M2/L2 슬라이스).
- `--agent auto`: `~/.claude`·`~/.codex` 등 존재로 감지, 발견 에이전트 **전부** 배선.
- **부분 실패 정책(M5)**: 에이전트별 배선 독립 — 한 에이전트 실패가 다른 배선을 막지 않는다. 하나라도 실패 시 **exit 3 + 어느 에이전트의 어느 단계가 막혔는지** stderr. 성공분은 롤백 안 함(멱등 재시도). reference: `wire_agents`(claude→`--settings`, codex→`--config`).

### 4.7 Obsidian 볼트 등록 (`--register-obsidian`, opt-in, closed)

05 draft의 "Obsidian 뷰"가 reference에서 **단독 opt-in 액션으로 구현**됨(부트스트랩과 별개, 온보딩 후 언제든 실행 가능). **비치명 — 항상 exit 0.**

- memory/를 볼트화(`.obsidian/` 없으면 생성 — core: graph/backlink/global-search, community: dataview; 쓰기 실패해도 빈 `.obsidian/`로 비치명).
- obsidian.json 경로: `--obsidian-config` 우선, 미지정 시 플랫폼 기본(linux `~/.config/obsidian/`, mac `~/Library/Application Support/obsidian/`, win `%APPDATA%/obsidian/`). 경로·id·ts는 **주입 가능**(결정적 테스트, Date.now/random 직접 호출 금지).
- **merge 등록(clobber 0)**: 기존 vaults 전부 보존하고 신규 항목만 추가. 항목 = `{"<16hex id>": {"path": <memory 절대경로>, "ts": <ms>, "open": false}}`. 원자 쓰기(temp+os.replace, 심링크면 실타깃에 replace해 링크 유지).
- **안전 skip(비치명)**: 설정 디렉토리 부재(Obsidian 미설치)·obsidian.json 파싱 실패·최상위가 object 아님·vaults가 dict 아님·같은 path 이미 등록(멱등)·vault_id 충돌 → 등록 안 하고 `registered=False`. 어떤 오류도 raise하지 않음.

### 4.8 환경변수 주입 (§9) — 크로스플랫폼

- 런타임 훅용 팀 루트 env(`TEAMMODE_HOME`)를 **플랫폼별 영구 env 메커니즘**으로 주입(§1.2). ⚠️ 의도적 호출(install/on/off)은 env 무신뢰 — env는 런타임 훅 전용.
- **POSIX(Linux/macOS)**: 셸 프로파일에 **멱등 1줄**. 셸 감지(`$SHELL`): bash→`.bashrc`/`export`, zsh→`.zshrc`/`export`, fish→`.config/fish/config.fish`/`set -gx`. 미지원 셸은 경고만(비치명). 멱등 마커(`# teammode (env injection, §9)`)로 중복 판정 — 같으면 무변경, 팀루트 바뀌면 그 줄만 교체.
- **Windows**: 셸 프로파일이 아니라 레지스트리(`HKCU\Environment`)에 산다 → `setx TEAMMODE_HOME "<절대경로>"`(영구 user env, 새 프로세스부터 반영). 제거(uninstall)는 `reg delete HKCU\Environment /v TEAMMODE_HOME /f`. setx 부재·rc≠0 은 비치명(injected=False, 경고). 플랫폼 감지는 `is_windows`(sys.platform `win`/`cygwin`).
- 격리(`--settings`)는 두 플랫폼 모두 실 호스트 env(셸 프로파일/레지스트리)를 건드리지 않는다(install·uninstall 대칭, I4b).
- ⚠️ Windows 분기는 setx/reg subprocess **모킹(runner 주입)** 으로 단위·라운드트립 검증됨. 실 Windows 동작(레지스트리 영속·새 세션 반영)은 native 환경 검증 권장(reference 빌드는 Linux 에서 작성).

### 4.9 합격 기준 (골든 — §6 시나리오 후보)

| # | 시나리오 | 합격 |
|---|---|---|
| I1 | 빈/엔진만 레포(config 없음)에서 install | 도입자 경로 완주 → memory/·최소 config(빈 슬롯)·sessions/·훅·env. ⑦에서 배너+context가 L1 데이터(빈 구조라도) 읽음. 첫 로그 미생성 |
| I2 | 유효 config 레포에서 install | 팀원 경로 → config 무수정, 이름 등재(충돌정책), 배선. context가 기존 로그 읽음 |
| I2b | I1/I2 직후 새 세션 시작 | SessionStart 훅이 맥락을 **실제 주입**(install이 아니라 여기서) |
| I3 | I1 직후 재실행 | 멱등 — 중복 생성 0, 변경 없음 |
| I4 | ambient `TEAMMODE_HOME`=실호스트 set 실행 | 실호스트 무접촉(env 격리) |
| I4b | `--settings <격리>` 지정 | 실 `~/.claude/settings.json` **및 실 호스트 env(POSIX 셸 프로파일 / Windows 레지스트리) 무접촉**, 격리 경로에만. `--settings`=env 격리 권위(`--yes` 와 와도 격리 우선). 실 env 주입은 `--settings` 없는 `--yes` 에서만. uninstall 도 격리면 실 env 제거 스킵(대칭) |
| I-win | nt 모킹(`platform=win32` + setx/reg runner 주입) 라운드트립 | install→on→context→uninstall: env 는 setx/reg delete(셸 프로파일 무접촉), active 마커·memory 정상, 실 setx/reg 미실행. (실 Windows 검증은 native 환경 권장) |
| I-dry | `--dry-run` | settings·memory·env 무접촉 + 계획만 |
| I5 | `gh` 부재 + 경로 A | fallback 안내, 본체는 `--root` 있으면 정상 |
| I6 / I6b | Python 하한 미달/git 바이너리 부재 / 원격 인증만 부재 | I6: exit 2 무변경 / I6b: 경고 후 로컬 L1 진행 |
| I7 / I8 | 같은 이름 재설치 / `--member-name`이 타인 등재명 충돌 | I7: 멱등 본인 항목 / I8: exit 3 + members.md 무변경 |

---

## §5. 온보딩 스킬 (tm-onboard)

> install.py가 **기계적으로 할 수 있는 것**(스캐폴드·훅·env·L1)은 다 하고, **사람 판단·대화가 필요한 것**(서비스 연결, 토큰 찾기, 어느 DB/채널/캘린더, 첫 가치 내레이션)은 `tm-onboard` 스킬이 한다. 스킬은 install.py를 *지휘*할 뿐 그 일을 다시 구현하지 않는다.

reference: `infra/skills/base/tm-onboard/SKILL.md` (실제 작성됨 — 05는 설계 draft였으나 본문으로 닫힘).

### 5.1 정체성·트리거

```yaml
name: tm-onboard
description: Use at first contact with teammode — setting up a team repo or joining a team —
  or to register the team memory as an Obsidian vault at any time later.
  Triggers: "이 레포 셋업해줘", "팀모드 셋업/시작/합류", "온보딩",
            "teammode setup", "Obsidian 등록", "옵시디언 볼트 만들어줘".
```
`tm-` 접두사(자동완성 발견성). AGENTS.md/CLAUDE.md도 이 스킬을 가리킨다(이중 진입 — 중복 방지는 스킬이 install.py를 호출하고 손으로 재현하지 않음으로 해소).

### 5.2 install.py ↔ tm-onboard 분업

| 단계 | 주체 |
|---|---|
| preflight·detect·role·scaffold·wire·env·L1 verify | **install.py**(기계). 스킬은 호출만 |
| 감지·role 결과 해석·내레이션 | tm-onboard |
| L2 서비스 연결(토큰 안내·OAuth·리소스 선택) | tm-onboard (§5.4 — 로드맵) |
| config 서비스 슬롯 작성 | tm-onboard (사람이 고른 값; install.py는 빈 슬롯만) |
| 첫 가치(context → 팀 상태 요약) | tm-onboard (`teammode context --json` 결과를 사람 말로) |

role은 `--member-name`이 아니라 **team.config.json 유효성으로 install.py가 자동 판정**(§4.4). 셋업 명령은 도입자/팀원 공통 하나. (install.py가 아직 role을 `--json`으로 안 뱉으므로, 미리 알아야 하면 스킬이 `config_is_valid`를 직접 확인 — `--json` 생기면 전환. 부록 A 미결.)

### 5.3 흐름 (progressive — L1 먼저, L2 당길 때)

```
"이 레포 셋업해줘"
 1. python infra/install.py --root . --member-name <이름> --yes   # L1 기계 부트스트랩
 2. role 분기 내레이션 (도입자: "팀 새로 만드는 거네요" / 팀원: "서비스는 레포에서 다 읽었어요")
 3. python infra/teammode.py context --root . --json  → "지금 팀 상황은 …" (서비스 0이어도 L1 보여줌)
 4. L2 제안(강요 X): "Linear·Slack 연결할래요? 나중에 해도 돼요"
```
핵심: 서비스 연결 **전에** L1 가치를 먼저 보여준다("온보딩 쉬움"의 답 = 첫 가치까지 거리). 갓 만든 팀은 세션로그 0 → "구조는 섰고 다음 작업부터 자동 기록·주입"으로 내레이션. state=on으로 보이려면 셋업이 `--yes`(또는 `--settings`)로 wire+verify까지 완주했어야 함.

**Obsidian 뷰(opt-in, 키 0)**: 물어보고 → `python infra/install.py --root . --register-obsidian`(§4.7). 미설치면 우아하게 skip. **나중 등록 가능** — 이미 셋업된 레포에서 "Obsidian 등록해줘"라고 하면 이 액션만 독립 실행(다른 단계 없이).

### 5.4 서비스별 연결 안내 (L2 — 로드맵, reference 미구현)

각 서비스마다 스킬이 **정확한 링크·버튼**으로 사람을 데려간다(막연한 "키 찾아와" 금지). 토큰 받으면 API로 리소스 ID 자동조회 → 후보 제시 → 1클릭 선택 → config 기록. 팀/개인 scope 구분(§7.2):

| 서비스 | scope | 연결 방식 |
|---|---|---|
| **Linear** | 개인 | 개인 API 키(팀원 각자 1회, attribution) |
| **Google Calendar** | 개인/혼합 | localhost OAuth(PKCE), 캘린더 ID 자동조회 |
| **Slack** | 팀 | 봇 토큰/앱 manifest(도입자 1회), 채널 자동조회 |
| **Notion** | 팀 | integration 토큰 + 페이지 공유 토글(도입자 1회), DB 자동조회 |

- **도입자 1회 → 팀원 0**: 팀 scope(Slack·Notion)는 도입자 연결 → config 커밋 → 팀원은 읽기. 개인 scope(Linear·GCal)는 각자 1회.
- 저장: 팀 토큰 = 팀 금고(credentials), 개인 토큰 = 로컬. 평문 노출 금지. (credentials 금고는 teammode에 아직 없음 — 의존 슬라이스, 부록 A.)
- **정직한 경계(사람 몫)**: OAuth "허용", 개인키 "Create+붙여넣기", Notion "공유 토글" = 사람이 권한 부여(보안 경계, 무인 불가). 스킬은 동의 게이트 직전까지 몰고 가고 클릭만 사람이.
- reference SKILL.md: "L2 미구현 — 준비 중" 안내까지만.

### 5.5 경계 / 단일 책임

- **검증·자가수리는 안 함** — 별도 `doctor`(로드맵)로 분리. tm-onboard는 "셋업+연결+첫가치"까지. (연결 직후 토큰 유효 ping 정도는 L2에 포함.)
- 코드 작성·이슈 생성·다른 스킬 자동 호출 안 함. 푸시·PR은 사람 결정.
- 크로스에이전트: SKILL.md는 프롬프트라 Claude/Codex 공통. MCP 등록 방식 차이는 install-mcp(L2)가 기계 처리, 스킬은 "어느 서비스·어느 리소스"만 판단.

---

## §6. 호환 선언 (Conformance)

teammode는 reference 구현과 별개로 누구든 같은 표준을 따르는 **독립 구현**을 만들 수 있다. 본 절은 독립 구현이 **"teammode compatible"** 을 선언하는 조건·절차를 정의한다.

호환의 약속: **호환 구현끼리는 같은 팀 레포를 공유할 수 있다.** 한 팀에서 멤버 A가 reference를, B가 독립 구현을 써도 팀 메모리가 깨지지 않는다.

### 6.1 호환 조건 (셋 다 필수)

- **C1 — 팀 메모리 표준 준수(§1)**: 세션로그 포맷(위치·하루 1파일·06시 컷·frontmatter author/date/summary)·코어 디렉토리·INDEX 갱신·주입 스케일(v0.1 자가 점검 + 골든 시나리오 "컨텍스트 조회"로 확인). 양방향: 생산한 파일이 포맷 준수 + 표준 팀 레포를 읽고 정상 동작.
- **C2 — 훅·어댑터 표준의 의미 보존(§2)**: 정규 이벤트 4종 의미(특히 PreToolUse 차단)·정규 입력 스키마로 공통 스크립트 호출·폴백(무음 스킵 금지)·정규형 규약(에이전트 고유 표기 금지, MCP 시맨틱 참조). **주의: 독립 구현이 `agents/` 구조나 Python을 그대로 쓸 필요 없다** — 보존 대상은 선언 포맷(manifest.json·events.json)과 의미이지 언어·배치가 아니다.
- **C3 — conformance kit 통과(§6.4)**: 필수 검사 통과 + 결과 로그를 등재 신청에 첨부.

**범위 한정**: 호환 선언은 특정 spec_version에 대한 선언("teammode 호환 (spec 0.1)"). 버전 없는 선언은 무효. 부분 구현은 "partial" 표기로 등재 가능(예: memory-only는 K1~K2·K8 + 골든 중 컨텍스트 조회·세션로그 작성). 부분집합 적정성은 maintainer 리뷰 승인.

### 6.2 reference 검수 도구 — check.py 3모드

reference는 단일 도구 `conformance/check.py`로 세 모드를 제공한다(03의 conformance kit 구상의 실물):

| 모드 | 성격 | 내용 |
|---|---|---|
| `lint` | 정적(엔진 실행 없음) | manifest 정규형·events.json 완전성 등. reference 현재: `_lint_manifest_canonical`(manifest에 `mcp__`/`Write\|Edit`/`apply_patch` 없는지, K4) — 다른 K 검사는 로드맵 |
| `verify` | 동적 | 골든 시나리오를 **우리 툴킷**에 실행(독푸딩). `--engine` 필요 |
| `conform` | 동적 + Tier | 같은 시나리오를 **임의 구현**에 실행 + advisory 순응률로 Tier 산출 |

- `verify`/`conform`은 같은 골든 시나리오 정의(`conformance/scenarios/*.json`)를 공유 — **시나리오 = 실행 가능한 스펙**. 빈 엔진(no-op)에 돌리면 전부 RED = 엔진의 인수 테스트.
- **하니스 인터페이스(C2 정신, 언어·배치 비강제)**: 엔진은 `engine.run(argv) → Result(exit_code, stdout, stderr)`를 만족하고 root 아래 파일 부작용을 내면 된다. reference `SubprocessEngine`은 임의 `--engine` prefix(예: `python3 infra/teammode.py`)를 받아 어떤 언어 구현도 검사 가능. 팀 루트는 동사 뒤 `--root`로 명시 주입하고 env 화이트리스트로 ambient 팀루트 변수 누수 차단(P1 이중 방어). reference 엔진은 settings 명시(P2)가 필요하므로 CLI가 root 하위 격리 settings를 주입(`--settings`를 모르는 타 구현은 미지 플래그로 무시).

### 6.3 Tier 산출

- **결정적(deterministic) 시나리오가 전부 통과해야 호환.** advisory 순응률로 Tier 등급: Tier 1 = advisory 100% / Tier 2 = advisory 부분 / Tier 3 = advisory 0. 결정적 실패가 하나라도 있으면 `compliant=False`(Tier 미산정).
- reference 시나리오 `tier_signal`: deterministic(01·02·03·05) / advisory(04).

### 6.4 검사 항목 (K1~K8, 필수 통과)

| # | 검사 | 대응 | reference 상태 |
|---|---|---|---|
| K1 | 생산한 세션로그 포맷(필수 3필드·파일명=date·하루 1파일) | §1.3·§1.5 | 시나리오 04 + assertion(`session_log_single_file/contains`) |
| K2 | 06시 컷 경계값(05:59→전날 / 06:00→당일) | §1.4 | `test_workday`(단위) — kit 자동검사 로드맵 |
| K3 | events.json 완전성(모든 정규 이벤트·행위 키 존재) | §2.6 | lint 로드맵 |
| K4 | manifest 정규형(에이전트 고유 표기 grep) | §2.2 | `lint` 구현됨 |
| K5 | normalize golden test(원어 → 정규 스키마 일치) | §2.10 | `test_normalize`(단위) — kit 로드맵 |
| K6 | 폴백 동작(미지원 이벤트 시 `[warn]`, 무음 스킵 부재) | §2.9 | `test_adapter_codex`(단위) — kit 로드맵 |
| K7 | 스킬 본문 정규형(`mcp__`·제품명 직표기 부재) | §2.12·§7.3 | lint 로드맵 |
| K8 | 코어 디렉토리 구조 + 신규 폴더 INDEX 등재 | §1.1 | lint 로드맵 |

추가로 **골든 시나리오 5종**(켜기 → 컨텍스트 조회 → 이슈 생성 → 세션로그 작성 → 끄기)을 실환경 통과(v0.1 수동 체크리스트, 자동화는 kit 후속). reference 시나리오 = `01-on-banner`·`02-context-injection`·`03-issue-create`·`04-log-accumulate`·`05-off-persist`. `03-issue-create`는 `issue` 동사(§3.5)로 GREEN — 시나리오가 연결 issues fixture를 자체 세팅(fs_write)·정리(fs_delete)해 공유 root에서 04/05를 오염시키지 않는다.

### 6.5 등재 절차 · 배지

1. **신청** — 본진 레포에 이슈(`implementation` 라벨): 구현 이름·레포·라이선스, 대상 에이전트/플랫폼·spec_version, kit 결과 로그(kit 공개 전: C1·C2 자가 점검표 + 골든 5종 기록).
2. **리뷰** — maintainer가 kit 결과 확인 + 양방향 상호운용 스팟 체크(독립 구현이 만든 세션로그를 reference가 읽기, 역방향). 사이드 프로젝트 케이던스상 수 주 소요 가능.
3. **등재** — README Implementations 표에 추가(구현·에이전트/플랫폼·spec_version·상태·검증일).
4. **상태 전이** — minor bump 시 등재 구현에 통지. `compatible → stale`: 통지된 minor의 다음 minor 발행 시점까지 재검증 미제출 시 stale(maintainer가 절대 기한 병기 가능, partial도 동일). `stale/partial → compatible`: 기존 이슈에 현행 spec_version 기준 결과 제출 → 표 갱신(신규 신청 불요). 철회는 본인 신청으로 언제든.

배지: `![teammode compatible](…/teammode-compatible%20(spec%200.1)-blue)`. **spec_version 반드시 포함**(버전 없는 선언 무효). `partial`/`stale`은 compatible 배지 불가(상태 배지는 가능). 명예 기반 — 허위 확인 시 제거·공지.

### 6.6 버저닝 연동

kit 검사 항목(K1~K8) 추가·변경은 minor bump. 0.x 호환 선언은 해당 minor에 대한 선언이며 1.0 동결 후 1.x 전체로 완화. 독립 구현 2개 이상 등재가 1.0 동결 + RFC-lite 도입 트리거.

---

## §7. 서비스 슬롯 · provider 팩

에이전트 축(§2)과 직교하는 두 번째 축: 같은 역할(이슈 트래커·채팅·…)을 팀마다 다른 제품으로.

### 7.1 역할 슬롯 선언 + scope (필수)

`team.config.json`의 `services`는 **역할 → provider** 선언. 역할명은 도구 중립 어휘로 고정(필수): `issues`(kanban 아님)·`chat`·`docs`·`calendar`. 각 슬롯에 **`scope: team | personal`**(05에서 §7로 승격)을 둔다 — 팀 scope는 도입자 1회 연결 후 팀원 공유, 개인 scope는 멤버 각자 1회.

```jsonc
"services": {
  "issues":   { "provider": "linear", "scope": "personal" /* provider별 상수 … */ },
  "chat":     { "provider": "slack",  "scope": "team" },
  "docs":     { "provider": "notion", "scope": "team" },
  "calendar": { "provider": "google", "scope": "personal" }
}
```

provider별 상수 스키마 상세는 v0.2 확정(예약).

### 7.2 빈 슬롯 = 1급 시민 (필수)

역할 슬롯 미연결은 **에러가 아니라 선언된 상태**다. `services`에서 키를 생략하면 빈 슬롯. install.py 도입자 경로는 `services: {}`(전부 빈)로 시작.

- 스킬은 frontmatter에 `requires: [issues]` 형식으로 의존 슬롯 선언.
- 미연결 슬롯 의존 스킬은 **자동 비활성**, 나머지는 전부 동작("Slack만 있어도 시작"). 메커니즘(v0.1): `install-skills`가 미연결 슬롯 의존 스킬 설치를 생략 + `[info]`. 슬롯 연결 후 `install-skills` 재실행으로 활성화.
- 빈 슬롯 provider를 참조하는 **훅 매처**는 `fallback` 무관 등록 생략 + `[info]`(§2.9, 스킬 `requires`와 대칭).
- 구현은 빈 슬롯을 이유로 설치·세션 시작을 실패시켜서는 안 된다.

### 7.3 스킬·훅에서의 서비스 참조

- **스킬 본문**: 역할 어휘만 — "이슈 트래커 MCP에서 조회". 제품명 직표기 금지(필수, lint 대상). 역할 → 실제 제품 번역은 LLM이 런타임에 config 보고.
- **훅 매처**: 코드는 LLM 번역 불가 → 정규 액션 `{service, action}` 선언을 `providers/<name>.json` 매핑표로 컴파일(events.json과 동일 "번역표는 데이터"). v0.1에서 이 매핑표 상세 스키마는 **예약**(v0.2 확정). v0.1 MCP 매처는 §2.5 정규 서버명 방식.

### 7.4 provider 생태계 (v1 매트릭스 + 확장)

- **Tier 1 provider = 독푸딩 조합 Linear / Slack / Notion / Google Calendar.** 그 외는 provider pack 기여 슬롯으로 연다.
- MCP 서버를 직접 제작·유지보수하지 않는 것이 기본 — 기존 생태계(공식 MCP 등) 활용, teammode는 `providers/` 슬롯·가이드·기여자 등재만.
- 공식 MCP 없는 사내 도구용 `infra/mcp/_template/`(최소 서버 + 시작 스크립트 + 가이드) 동봉 권장 — 온보딩 중 즉석 제작 제안용 설계도.
- provider pack에 `token_guide` 필드(토큰 발급 딥링크 + 단계) 권장 — 온보딩에서 사람 몫(토큰 수령) 최소화.

### 7.5 토큰 병목 완화 (권장 사다리)

1. 가능한 provider는 **OAuth remote MCP 우선** — 토큰 심부름 제거.
2. provider pack의 `token_guide` — 발급 딥링크·단계.
3. **팀당 1회 원칙** — 토큰은 리더가 1회 수령해 팀 자격증명 금고에 두고 팀원 온보딩은 금고에서.
4. 온보딩 멘트로 기대치 고정 — "당신 몫은 토큰 N개뿐".

---

## 부록 A. 설계 ↔ 빌드 reconcile (코드가 진실 — 닫은 미결 + 잔여 갭)

### A.1 구현으로 닫힌 미결 (draft → closed)

| 원 미결(스펙) | 닫은 결정(코드 기준) | 근거 코드 |
|---|---|---|
| 04 §12-1 Python 하한(3.9? 3.10?) | **3.9** (`MIN_PYTHON`) — 분포 근거 나오면 재조정 가능 | `install_lib.MIN_PYTHON` |
| 04 §12-2 install이 첫 세션로그 쓰나 | **안 씀**(디렉토리만). 첫 로그는 첫 작업 세션 훅 | `scaffold_memory`(로그 미생성) |
| 04 §12-3 `--yes`에서 이름 못 정하면 | **exit 3**(신원 추측 금지) | `bootstrap`(member_name None → 3) |
| 04 §3 role 판정 기준 | spec_version + team.name 비-placeholder = 팀원, 아니면 도입자 | `config_is_valid`·`detect_role` |
| 04 §5/§6 이름 충돌 | identity(git email 주석)로 동일인/타인 결정적 판정 → 타인은 exit 3 | `register_member`(ConflictError) |
| 04 §9/M6 env 변수명(LEGACY_TOOL_HOME vs TEAMMODE_HOME) | **`TEAMMODE_HOME`** 단일화(런타임 훅 코드 일치). 01 부록A의 `LEGACY_TOOL_HOME` 오기 폐기 | `install_lib.ENV_VAR`·훅 3종 |
| 04 §10 실호스트 쓰기 게이트 | `--yes`(실설치) 또는 `--settings`(격리) 없으면 wire 건너뜀 | `bootstrap`(wire gate)·`_dispatch` |
| 05 전체(설계 draft) | tm-onboard SKILL.md **실제 작성됨** | `infra/skills/base/tm-onboard/SKILL.md` |
| 05 Obsidian 등록 메커니즘 | `--register-obsidian` 단독 opt-in 액션·merge·비치명·나중 등록 | `register_obsidian`·`register_obsidian_vault` |
| 02 §5 `sync` 무플래그 | base 엔트리만(최초 off 간주) | 어댑터 `_wanted_entries` |

### A.2 코드에만 있는 동작 (스펙 미기재 → 본문에 명문화)

- **엔진 동사 전체(§3)**: 02/01에 동사 계약이 없었음 — on/off/log/context/pull/commit/update를 §3에 신규 명문화. `--root` 필수·env 무신뢰·on/off의 settings 명시(P2)·06시 컷 log·context state/json·git 동사 비치명 축소.
- **`enforcement` manifest 필드(§2.2)**: 02 draft 미언급, reference 어댑터가 실사용(block 폴백 경고 강화) → 본문 확정.
- **on의 upstream fetch 자동 알림(§3.1)**: merge 금지·fetch만. 02/04 부분 언급을 §3.1로 통합.
- **install 디스패치 모드(§4.1·§2.1)**: `install.py --<agent> sync/uninstall` 보존 인터페이스.

### A.3 잔여 갭 (코드가 스펙 목표에 아직 미달 — 비규범)

- **manifest의 미구현 스크립트 2개**: `auto-commit.py`(PostToolUse/file_edit)·`confirm-action.py`(PreToolUse/linear create_issue)가 manifest.json에 선언됐으나 `infra/hooks/`에 파일 부재. sync 시 등록은 되나 발동 시 normalize가 없는 스크립트를 subprocess 실행 → 런타임 실패 위험. (L2/안전장치 슬라이스 대상.)
- **install-skills·install-mcp 미구현**: 어댑터 계약(§2.7)에 있으나 reference 어댑터는 sync·uninstall만. install.py wire도 훅 sync만(스킬 심링크·MCP 등록 제외). → L2.
- **주입 스케일 분기 미구현(§1.6)**: reference `session-start.py`는 항상 summary 라인 기반 주입, ~4인 전문/5인+ 분기 없음. v0.1 자동검사 대상 아님이라 비준수는 아니나 스펙 목표 대비 갭.
- ~~**conformance 시나리오 03(`issue create`)**: 엔진 미구현 동사 → exit 127(의도된 RED, 서비스 슬롯 L2).~~ → **닫힘(0.2)**: `issue` 동사 구현(§3.5), 03 GREEN.
- **lint 검사 범위**: reference `check.py lint`는 K4(manifest 정규형)만. K3·K5~K8은 단위 테스트로 커버하거나 로드맵.
- **`--update` 플래그 미사용**: install.py CLI에 파싱되나(`Options.update`) bootstrap이 사용하지 않음(멱등 재실행이 사실상 update 역할). §4.1에 명시.
- **`--json` role 출력 미구현**: 05/04가 요구한 install의 구조화 role 출력(`--json`) 미구현 → tm-onboard가 `config_is_valid` 직접 확인으로 우회(§5.2).
- **`config.example` / 서비스 config sanitize**: provider별 상수 스키마(§7.1)·`providers/<name>.json`(§7.3)·예시 config가 reference에 부재(예약/L2).
- **credentials 팀 금고**: §5.4·§7.5가 전제하나 teammode에 미구현(의존 슬라이스).
- **Codex 실 훅 입력 스키마 미확인**: normalize가 Claude 유사 형태 가정(§2.11) — Codex 실환경 캡처 후 v0.2 확정.
- **팀 personality(커스텀 배너·greeting·farewell)**: config `team.greeting`/`team.farewell` 필드와 on/off 출력은 본 스펙이 **정의(규범)**. reference 구현은 자동 배너(`team.name`)만 — greeting/farewell 출력·온보딩(tm-onboard 팀셋업) opt-in 커스텀·custom 배너 입력은 **미구현(빌드 예정)**.
- **workday timezone 고정**: reference는 KST 상수(`workday.KST`), `team.config.json team.timezone` 주입 미연결(확장 여지).
- **Windows 네이티브(env setx·훅 인터프리터·POSIX 감사)**: 구현 완료(§4.8·§2 — `is_windows` 분기, setx/reg env, `sys.executable` 훅 명령, POSIX 가정 제거). **단 reference 빌드는 Linux 에서 작성돼 Windows 분기는 setx/reg subprocess 모킹(runner 주입) + 플랫폼 주입으로만 검증**됨 — 실 Windows 에서의 레지스트리 영속·새 세션 env 반영·경로 해석은 native 환경 실측 권장(코드는 갭이 아니라 검증 환경의 한계).

---

## 부록 B. 미결 (spec 0.2 전 확정 — open)

- [ ] Python 버전 하한 최종 확정(3.9 잠정 → 타깃 머신 분포 근거).
- [ ] 도입자 commit·push 안내 자동화 범위(install.py 경계).
- [ ] Codex 훅 입력 JSON 실스키마 확인(실환경 캡처) + PreToolUse 차단 시맨틱 Codex/Hermes 표현 가능성.
- [ ] Hermes 이벤트 매핑 실조사(pre_llm_call≈UserPromptSubmit, on_session_start≈SessionStart — 재확인).
- [ ] normalize의 manifest 조회 비용(매 발동 파일 읽기 — 캐시 필요성).
- [ ] 동일 provider 다중 인스턴스·역할 중복 provider의 정규 서버명 표현(정규 서버명=provider 식별자 결정의 잔여 한계).
- [ ] `team.config.json services`의 provider별 상수 스키마(§7.1).
- [ ] `providers/<name>.json` 매핑표 스키마(§7.3).
- [x] 멤버 역할 필드 — `team.config.json` `members: [{name, role?}]`로 확정(L2-A2, 각자 upsert). members.md 연락 필드 포맷은 여전히 v0.2 예약.
- [ ] install role의 `--json` 출력 스키마(§5.2 우회 해소).
- [ ] tm-onboard L2를 별도 스킬(`tm-connect`?)로 더 쪼갤지.

---

## 부록 C. 버전 이력 · 01~05 대비 변경점

- **0.2** — **engine 동사 계약 변경(minor bump, §0.4)**: 8번째 엔진 동사 `issue` 추가(§3.5). `issues` 슬롯 provider 확인 후 정규 입력 스키마를 stdout JSON으로 echo까지만(action_map 해석·페이로드 변환 금지 — 어댑터/스킬 몫). 값 화이트리스트에 `--title --body --assignee --label --priority` 추가, positional 서브액션 파싱 명문화(§3). conformance 03 닫힘(RED→GREEN). 하니스 `fs_delete` 액션(시나리오 자체 teardown) 추가.
- **0.1 (이 문서)** — 흩어진 spec 01(정식)·02(정식)·03(정식)·04(0.1-draft)·05(0.1-draft)를 단일 권위 문서로 통합. spec_version·용어·표기 규약 단일화. 04/05 draft를 빌드 기준 closed로 승격(부록 A.1). 코드에만 있던 엔진 동사(§3)·`enforcement` 필드·install 디스패치 모드를 명문화. 잔여 갭을 부록 A.3에 명시.

**01~05 → SPEC 대비 주요 변경점:**
- 04/05의 `0.1-draft` 상태 해소 → 전 영역 단일 `0.1`.
- 04 §9의 `LEGACY_TOOL_HOME` vs `TEAMMODE_HOME` 자기모순 해소 → `TEAMMODE_HOME`으로 단일화(01 부록A 오기 폐기).
- 엔진 동사 챕터(§3) 신설 — 01~05 어디에도 없던 on/off/log/context/pull/commit/update 계약.
- 02의 `enforcement` 필드를 본문 확정(draft 미언급, 코드 실사용).
- 05의 Obsidian 뷰 설계를 `--register-obsidian` 구현 계약으로 확정(§4.7).
- §7에 `scope: team|personal` 승격(05 §5 전제 → 정식)·v1 provider 매트릭스(Linear·Slack·Notion·GCal) 통합.
- 03의 "conformance kit 구상"을 reference `check.py` 3모드 실물과 대조(§6.2).

---

## 부록 D. 이 SPEC이 01~05를 대체한다 — 제거 시 repoint 필요한 참조 목록

> 본 SPEC이 `spec/01`~`spec/05`를 대체한다. **아직 제거하지 말 것** — 아래 참조들이 옛 경로/섹션을 가리키므로, spec/ 파일 제거 전 이 목록을 SPEC.md의 새 섹션 번호로 repoint해야 한다. (목록만 — 본 작업에서 수정하지 않음.)

**문서·진입점:**
- `README.md:77` — "스펙: 설계 폴더 `spec/` — 01 팀메모리 · 02 … 05 onboard." (목록 전체 → SPEC.md)
- `AGENTS.md:36` — "설계 스펙(`spec/04-install.md`·`spec/05-onboard-skill.md`) 확인."
- `infra/skills/base/tm-onboard/SKILL.md:74` (footer) — "`spec/04-install.md`·`spec/05-onboard-skill.md`를 확인."
- `conformance/scenarios/README.md:3,6,7,49` — "스펙 02 §11.12·§11.11 + 스펙 03 §3", "(스펙 02 §11.12)", "(스펙 03 §3)", "스펙 03 §2 C2 주의".

**코드 docstring·주석 (동작 변경 없음, 참조 텍스트만):**
- `infra/install.py:2,11,174` — "(spec/04)", "스펙 02 §2 불변식 3", "(spec/05, opt-in)".
- `infra/install_lib.py:2,32,153,227,239,255,347,491,572,574,658` — "spec/04", "스펙 01 §6", "스펙02 §9.2", "스펙 01 §2.1", "스펙01 §1.2", "spec/05" 등 다수.
- `infra/teammode.py:149,171,258` — "스펙 01 §2.1", "스펙 01 §3.3".
- `infra/workday.py:2,10` — "스펙 01 §3.2".
- `infra/agents/claude/adapter.py:2,11` — "스펙 02 §5", "스펙 02 §2".
- `infra/agents/claude/normalize.py:2` — "스펙 02 §6".
- `infra/agents/codex/adapter.py:2` — "스펙 02 §5".
- `infra/agents/codex/normalize.py:2,8,11` — "스펙 02 §6", "스펙 02 부록 B / 초안 §12".
- `infra/hooks/session-start.py:4,40` — "스펙 02 §3.1·스펙 04 §4⑦·B1", "스펙 01 §1.2".
- `infra/hooks/session-log-remind.py:4,11,34` — "스펙 02 §6", "스펙 01 §3.4", "스펙 01 §1.2".
- `conformance/check.py:2,7,13,143,288,335` — "스펙 02 §11.12", "스펙 03 §3", "스펙 03 §2 C2", "스펙 01 §2.1", "스펙 02 §3, K4", "스펙 01 §2.4".

**테스트 docstring (참조 텍스트만 — 동작 무관):**
- `tests/*.py` 다수 — `test_workday`(스펙 01 §3.2)·`test_context`(스펙 01 §4)·`test_log`(스펙 01 §3)·`test_normalize`(스펙 02 §6)·`test_adapter_claude/codex`(스펙 02 §4·§5·§7)·`test_install_l1a~l1e`/`test_install_golden`/`test_install_l1b`(spec/04 각 절)·`test_register_obsidian`(spec/05).

**참고**: `BUILD-LOG.md`·`CHECKLIST.md`도 다수 spec 참조를 포함하나 이력 문서이므로 repoint 우선순위 낮음(원하면 후속).

> ⚠️ 02 draft의 섹션 번호 중 일부(§11.x: §11.5 배너·§11.11 Tier·§11.12 check 3모드)는 `spec/02-hook-manifest.md` 본문에 존재하지 않는다(초안 §11.x를 가리키는 잔존 참조). SPEC 매핑: §11.5→§3.1(배너), §11.11→§6.3(Tier), §11.12→§6.2(check 3모드). repoint 시 함께 정정.
