# 내부 규범

tm-mode SPEC v0.2 — 엔진·표준 규범

## §1. 팀 메모리 표준 (Team Memory)

팀 메모리의 데이터 표준: 디렉토리 구조, 세션로그 포맷, 컨텍스트 주입 규칙. 어떤 에이전트로 읽고 쓰든 이 표준을 따르면 같은 팀 메모리를 공유한다 — 크로스에이전트 호환의 토대.

### 1.1 디렉토리 구조

팀 메모리는 팀 루트의 `memory/` 아래에 있다. 위치는 **필수**.

```
memory/
├── INDEX.md                      # 메모리 인덱스 — 세션 시작 시 주입되는 단일 진입점
├── banner.txt                    # 팀 배너 캐시 (권장 표준 위치 — 엔진·install이 실사용)
└── team/
    ├── members.md                # 멤버 명부 — 이름/identity 등재. 역할은 config.members가 단일 소스
    ├── sessions/<이름>/          # 멤버별 세션로그(§1.3). <이름>=members.md의 영문 이름
    ├── decisions/                # 확정된 결정사항
    │   ├── current.md            #   활성 결정
    │   └── archive/              #   과거 결정
    └── meeting/
        ├── summary/              # 회의록 요약본
        └── raw/                  # 회의 원본 (STT·텍스트)
```

- **INDEX.md (필수)** — 폴더별 "여기에 넣는 것" 설명을 표로 유지. 새 폴더를 만들면 INDEX.md 갱신이 필수. 등재되지 않은 폴더는 주입·탐색 대상에서 누락된다. (install.py가 스캐폴딩하는 기본 INDEX.md 표는 `install_lib._INDEX_MD` 참조.)
- **members.md (필수)** — 멤버 이름은 시스템 계정명(`$USER`)이 아니라 이 파일에 등재된 이름이다. 코드·훅·스킬은 이름을 하드코딩하지 말고 이 파일을 참조해야 한다(필수). reference 검증 단일 소스는 `teammode._validate_author`이며, 실제 허용 규칙은 "빈 문자열 금지, `/`·`\` 금지, `.`·`..` 금지, 절대경로 금지, 첫 글자는 Unicode `isalnum()`, 전체 문자는 Unicode `isalnum()` 또는 `-` 또는 `_`"이다. 즉 대문자·Unicode 영숫자·밑줄도 구현상 허용되며, ASCII 소문자만으로 제한하지 않는다. 멤버 항목 라인 포맷(reference 구현, §4.4와 정합): `- <name>  <!-- id: <identity> -->`. `id` 주석은 install.py가 동일인/타인을 결정적으로 가르는 데 쓰며(§4.4 충돌 정책) 없어도 호환된다. 멤버 **역할(role)**은 members.md가 아니라 `team.config.json`의 `members` 배열에 둔다(L2-A2, 은수 결정 2026-06-16): `members: [{name, role?}]` — 각 멤버가 install 시 자기 `name` 엔트리만 upsert(각자 upsert)하며 타인 엔트리는 무접촉. `role`은 권장 어휘(developer/pm/designer/…) 또는 자유문자열, 생략 가능. 빈 배열·`members` 키 없음도 valid(기존 config 무회귀). ⚠️ `members` 블록은 role **판정**(`config_is_valid`)과 완전 분리 — 스키마 위반은 `[warn]`만 발화하고 도입자/팀원 판정을 뒤집지 않는다. 현재 `install_lib._INDEX_MD`/`_MEMBERS_HEADER` 스캐폴드 문구에는 members.md가 역할·연락의 단일 소스이고 이름은 소문자라는 과거 표현이 남아 있으나, 코드 동작 기준 단일 소스는 위 규칙이다. members.md의 연락 필드 상세 포맷은 0.2에서도 예약이다.
- **sessions/<이름>/** — 세션로그(`YYYY-MM-DD.md`) 외 보조 파일을 둘 수 있다(주입·검사 대상 아님). 단 **`YYYY-MM-DD`로 시작하는 `.md` 파일명은 세션로그 네임스페이스로 예약**되어 보조 파일에 쓸 수 없다(`-late` 등 분할 파일은 §1.3 위반 검사 대상). 네임스페이스 판정: stem 길이 ≥10, `stem[:4]` 숫자, `stem[4]=='-'`, `stem[7]=='-'` (reference: `teammode._is_session_log_name`).
- **decisions/** — "확정된" 결정만. 논의 중인 사안은 세션로그·회의록에 머문다.

**권장·예약 항목:**

| 경로 | 분류 | 내용 |
|---|---|---|
| `team/reviews/` | 권장 | 외부 평가·피드백. 파일명 `YYYY-MM-DD-출처-단계.md` |
| `team/ground-rules.md` | 권장 | 팀 운영 그라운드 룰(엔진/다이제스트가 참조할 표준 위치) |
| `banner.txt` | 권장 | 팀 배너 캐시 표준 위치. `team.config.json`의 `banner_file`이 가리킴(reference: `memory/banner.txt`) |

**팀 확장 (자유)**: `memory/` 아래 자유 폴더 추가 가능(예: `product/`, `soma/`). 규칙 두 가지 — ① 기존 폴더로 충분하면 새 폴더 금지(증식 방지, 권장), ② 새 폴더는 INDEX.md에 등재(필수). 등재/해제는 reference 동사 `teammode.py memory route {upsert|remove}`(`--root --path --desc --author`)가 담당하며, `memory write`는 미등재 최상위 폴더 감지 시 이 동사를 안내하는 `[hint]` 한 줄을 stdout에 출력한다(자동 등재 아님 — 설명 한 줄은 사람이 확정).

### 1.2 쓰기 위치·팀 루트·env 규칙 (필수)

- **쓰기 위치**: 팀 메모리 쓰기는 항상 **팀 루트의 `memory/`** 에 한다. 작업 중인 코드 레포에 우연히 있는 `./memory/`에 쓰는 것은 금지. 구현은 세션 리마인더 등으로 혼동을 방지하길 권장(reference: `session-log-remind.py`).
- **런타임 훅의 팀 루트 = 환경변수**: 런타임 훅은 에이전트 하니스가 발동하므로 `--root` 같은 인자 통로가 없다. 따라서 구현은 런타임 훅이 참조할 **환경변수를 제공해야 한다(필수)**. reference 변수명: **`TEAMMODE_HOME`** (없으면 cwd 폴백). reference에서 직접 env를 읽는 훅은 `session-start.py`·`session-log-remind.py`·`auto-commit.py`·`confirm-action.py`다. `auto_pull.py`는 env를 읽지 않고 호출자가 넘긴 `team_root`를 받는 helper다.
- **엔진/어댑터/설치의 팀 루트 = 명시 인자만(필수)**: on/off·log·context·pull·commit·update·install 등 **의도적으로 호출되는** 동사는 팀 루트를 **명시 인자(`--root`)로만** 받는다. 환경변수 폴백·cwd 추측을 해서는 안 되며, `--root`가 없으면(설치는 cwd 표식 검증 후 표식이 없으면) **동작하지 말고 에러로 종료**한다(exit 2). 근거: ambient env(예: 호스트 toolkit을 가리키는 `TGATES_HOME`)가 새어들어 격리 하니스를 우회한 직접 호출이 호스트의 상태 마커(`.teammode-active` 등)·`memory/banner.txt`를 건드린 실사고(P0/P1). 엔진이 "어느 폴더를 건드릴지 추측하지 않게" 하는 것이 근본 처방.
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

작업일 경계는 자정이 아니라 **KST 06:00** 이다. reference 구현의 단일 소스는 `infra/workday.py`이며, 현재 코드는 `team.config.json`의 timezone을 읽지 않는다. `KST = timezone(timedelta(hours=9))`, `CUT_HOUR = 6`이 상수로 고정되어 있다.

- `workday(now: datetime) -> datetime`: 입력 `now`를 작업일 자정 `datetime(..., tzinfo=KST)`으로 정규화한다.
  - `now.tzinfo is None`이면 입력 시각을 **KST naive 시각**으로 간주해 `now.replace(tzinfo=KST)` 한다.
  - aware datetime이면 `now.astimezone(KST)`로 먼저 변환한다.
  - KST 변환 뒤 `now.hour < 6`이면 `now - timedelta(days=1)`을 적용한다.
  - 반환값은 조정된 날짜의 `00:00:00 KST` datetime이다. 분·초·마이크로초는 반환값에 보존되지 않는다.
- `workday_str(now: datetime) -> str`: `workday(now).strftime("%Y-%m-%d")`만 수행한다. 세션로그 파일명과 frontmatter `date`의 reference 값이다.
- `now_kst() -> datetime`: CLI 기본값용 현재 시각이다. `datetime.now(KST)`를 반환한다. 테스트·재현은 실시각 대신 `teammode.py log --now <ISO8601>`로 주입한다.
- 경계 조건:
  - KST `00:00:00` 이상 `05:59:59.999999` 이하에 시작한 로그는 **전날** 작업일이다.
  - KST `06:00:00`부터는 **당일** 작업일이다. 즉 `05:59`와 `06:00` 사이가 유일한 컷이다.
  - 월·연도 경계도 같은 규칙을 따른다. 예: `2026-07-01T05:59:00+09:00` → `2026-06-30`, `2026-07-01T06:00:00+09:00` → `2026-07-01`.
  - aware 입력은 먼저 KST로 변환한 뒤 판정한다. 예: `2026-06-15T20:59:00+00:00`은 KST `2026-06-16 05:59`이므로 `2026-06-15`, `2026-06-15T21:00:00+00:00`은 KST `2026-06-16 06:00`이므로 `2026-06-16`.
- **판정 시점 = 로그 작성 시작 시각**이다. reference CLI에서는 `cmd_log(..., now)` 호출에 들어온 단일 `now`가 파일명·frontmatter `date`·항목 시각 라벨 계산에 쓰인다. 같은 `teammode.py log` 호출이 실행 중 06:00을 넘겨도 중간에 재판정하지 않는다.
- `teammode.py log --now` 파싱은 `datetime.fromisoformat()`이다. 파싱 실패·미지정은 에러가 아니라 `now_kst()`로 조용히 폴백한다(§3.2). 작업일 계산 자체는 위 `workday_str(now)` 규칙을 따른다.
- 구현상 주의: `workday()`는 naive datetime을 KST로 간주하지만, `teammode.py log`의 항목 라벨(`## HH:MM`)은 현재 코드에서 `now.astimezone(KST)`로 직접 만든다. Python의 naive `astimezone()`은 실행 호스트의 로컬 timezone 해석을 탄다. reference 실행 환경이 KST이면 작업일과 라벨이 일치하지만, 비-KST 호스트에서 naive `--now`를 넣으면 작업일 판정(KST 간주)과 라벨 표시(호스트 로컬 간주 후 KST 변환)가 달라질 수 있다. 재현 가능한 테스트·운영 입력은 offset 포함 ISO8601을 권장한다.

### 1.5 frontmatter (필수)

```markdown
---
author: eunsu
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
- **마이그레이션**: 구버전의 `summary` 없는 로그는 비준수로 취급하지 않는다. 신규 작성분부터 필수.

### 1.6 주입 규칙 (스케일)

세션 시작 시 구현은 팀 메모리를 컨텍스트에 주입한다. 전원 전문 주입은 인원에 비례해 컨텍스트가 폭발하므로 팀 크기에 따라 스케일한다.

| 팀 크기 | 세션로그 주입 방식 |
|---|---|
| **~4인** | 전원 최근 로그 **전문** 주입 |
| **5인 이상** | 전원 `summary` 한 줄 + **본인** 로그 전문 + 동료 상세는 **lazy load** |

- **팀 크기** = members.md 등재 멤버 수.
- **주입 기본 단위** = 멤버별 **가장 최근 작업일 파일 1개**(전문이든 summary든). 구현은 더 넓게(최근 N일) 제공할 수 있으나 기본값은 1파일.
- 대상 파일에 `summary`가 없으면(구 로그) 그 멤버의 **summary 주입은 생략** — 전문 폴백 주입 금지(컨텍스트 폭발 방지가 목적).
- reference: `teammode._collect_members`가 멤버별 최근 1파일의 `{author, date, summary, file}`을 수집(요약 안 함). `session-start.py` 훅이 INDEX + 멤버별 summary 라인을 `additionalContext`로 주입. ⚠️ **현 reference는 팀 크기 분기(~4인 전문 / 5인+ summary)를 구현하지 않고 항상 summary 라인 기반 주입**이다 — 부록 A 참조. 0.2에서 스케일 규칙은 conformance 자동검사 대상이 아니며(§6.4 K-list 외), 골든 시나리오 "컨텍스트 조회"로 주입 방식을 확인한다.
- `team.config.json`의 `groups` 키는 **0.2 예약어**(스쿼드 단위 주입 범위용). 구현은 무시해야 하며 `null` 외 값에 의미를 부여하지 않는다.
- **적용 범위**: 0.2 타겟 팀 크기는 **2~5인**. 7인 이상 동작은 본 스펙이 보증하지 않는다.

---

## §2. 훅 · 어댑터 표준 (Hook & Adapter)

> 훅·스킬·MCP의 **내용은 1벌**만 유지하고, 에이전트마다 다른 **표기·등록 방식·입력 스키마는 `agents/<name>/` 어댑터가 번역**한다. 설계 목표는 어댑터 추가만으로 새 에이전트를 붙이는 것이지만, 현 reference는 감지·배선 경로가 `install_lib._AGENT_HOME_DIRS`와 `_AGENT_WIRE`에 하드코딩되어 있어 새 에이전트 추가 시 `agents/<name>/` 파일뿐 아니라 install_lib 맵 수정도 필요하다.
>
> 이 절의 ground truth는 2026-06-16 현재 워킹트리의 `infra/agents/{claude,codex}/{adapter.py,normalize.py,events.json}`, `infra/hooks/{manifest.json,session-start.py,session-log-remind.py,auto_pull.py,auto-commit.py,confirm-action.py}`, `infra/io_encoding.py`이다. 현재 워킹트리에는 `install-skills` 관련 미커밋 변경(`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py` 등)이 있으며, 이 절은 **커밋 여부와 무관하게 현재 구현**을 반영한다.

### 2.1 디렉토리 구조

```
infra/
├── hooks/                       # 공통 — 1벌
│   ├── manifest.json            #   정규형 선언 (§2.3)
│   ├── session-start.py          #   SessionStart additionalContext 주입 + 세션당 1회 auto_pull
│   ├── session-log-remind.py     #   UserPromptSubmit 리마인더 (pull 안 함 — 2026-06-17 분리)
│   ├── auto_pull.py              #   manifest 엔트리 아님. session-start helper(세션당 1회)
│   ├── auto-commit.py            #   PostToolUse/file_edit 자동 커밋
│   └── confirm-action.py         #   PreToolUse/linear.create_issue 확인 차단
├── skills/
│   └── base/<skill>/SKILL.md     # 공통 스킬 원본. 현 구현은 base만 설치(오버라이드 없음)
├── agents/
│   └── <name>/                  # 에이전트별 어댑터 — 파일 3개
│       ├── adapter.py           #   설치 시점 번역기 (§2.7)
│       ├── events.json          #   번역표 (§2.6)
│       └── normalize.py         #   런타임 통역사 (§2.10)
└── install.py                   # 디스패처 겸 부트스트랩: --<agent> 플래그 → agents/<name>/ 위임
```

**구조 원칙과 현 reference 예외:**
1. 설계 목표는 공통 스크립트와 스킬에 에이전트 고유 표기를 두지 않는 것이다. 단 현 reference 공통 훅 일부는 예외적으로 Claude 출력 스키마(`hookSpecificOutput`/`permissionDecision`)를 직접 알고 있다(`session-start.py`, `session-log-remind.py`; 부록 A.3). `session-log-remind.py`는 2026-06-22 재설계에서 `hookSpecificOutput.additionalContext`+`systemMessage` **JSON stdout**(session-start 와 동형)으로 전환했다 — normalize가 그대로 재방출해 Claude가 수신한다. (`confirm-action.py`의 PreToolUse exit-2 차단은 Claude·Codex 공통이라 에이전트 고유 표기가 아니다.)
2. 에이전트별 settings 렌더링·manifest 번역·normalize 입력 변환 메모리는 `agents/<name>/` 아래에 둔다. 위 예외 때문에 "모든 에이전트 고유 메모리가 전부 `agents/<name>/` 아래에만 있다"는 강한 명제는 현 reference에는 맞지 않는다.
3. 설치 시점 배선은 어댑터 CLI에 위임한다. `install.py`/`install_lib.py`는 에이전트별 settings 경로·격리 경로·동사 호출 순서만 알고, manifest 번역·훅 문자열 생성·MCP/스킬 설치 세부는 어댑터가 한다.
4. 런타임 공통 스크립트는 정규 스키마만 읽는다. 에이전트 원어 JSON은 반드시 `normalize.py`에서 변환한 뒤 공통 스크립트 stdin으로 전달한다.

### 2.2 매니페스트 엔트리 형식

`infra/hooks/manifest.json`은 훅 엔트리 배열이며 **에이전트 무관 정규형으로만** 선언한다.

```jsonc
{
  "event": "PostToolUse",                 // 필수. 정규 이벤트 (§2.4)
  "match": { "action": "file_edit" },     // 선택. 정규 매처 (§2.5). 생략 = 전체 매칭
  "script": "auto-commit.py",             // 필수. hooks/ 하위 공통 스크립트
  "args": "",                             // 선택. 기본 ""
  "timeout": 3,                            // 선택. 초(seconds). 기본값은 구현 정의
  "mode": "on",                           // 선택. 생략 = base(상시) / "on" = 팀 모드 켜진 동안만
  "fallback": "runtime",                  // 선택. "runtime" | "drop". 기본 "drop" (§2.9)
  "strict": false,                        // 선택. 기본 false. normalize 변환 실패 정책 (§2.10)
  "enforcement": "advisory"               // 선택. "advisory" | "block". 폴백 경고 강화용 (아래)
}
```

- `event`·`script`는 필수다. 어댑터는 값 검증기를 따로 두지 않고 접근한다. 키가 없으면 현재 구현은 `KeyError`로 실패할 수 있다.
- `match` 생략 또는 falsy 값은 전체 매칭이다. 지원 키는 현 구현상 `action`, `mcp`뿐이다. 알 수 없는 match 키는 어댑터 번역 단계에서 표현 불가로 취급하고, normalize 런타임 필터에서는 `True`로 통과한다(unknown match를 런타임에서 막지 않음). `match`가 정확히 하나의 키만 갖는지는 현재 lint/conformance에서 검사하지 않는다.
- `args`는 문자열 그대로 커맨드 끝에 붙는다. 리스트 파싱이나 shell escaping 재해석은 하지 않는다. 예: manifest의 `"args": "teammode-linear-create-allow"`는 `confirm-action.py`의 첫 positional 인자로 들어간다.
- `timeout`은 **초(seconds)** 단위 선언이다. Claude settings.json 과 Codex config.toml 모두 초 단위 hook timeout 을 사용하므로 어댑터가 변환 없이 그대로 기록한다(변환 드리프트 원천 차단). 생략 시 양쪽 모두 timeout 필드를 기록하지 않는다.
- `mode` 생략은 base 엔트리다. `"on"`은 `sync --on`일 때만 base와 함께 등록된다. `sync --off` 또는 플래그 없는 `sync`는 base만 등록한다. 현재 구현에는 "마지막 on/off 상태 기억"이 없다.
- `fallback` 기본값은 `"drop"`이다. `"runtime"`은 이벤트는 지원하지만 매처가 표현 불가할 때 무매처로 등록하고 normalize 자가 필터에 맡기는 모드다. 이벤트 자체가 `null`이면 runtime이어도 등록할 수 없어 drop된다.
- `strict`는 normalize 변환 실패 시 종료코드를 결정한다. 같은 `script`를 가진 manifest 엔트리 중 하나라도 `strict: true`면, 해당 script로 호출된 normalize 변환 실패가 exit 1이 된다. 아니면 exit 0이다.
- `enforcement` 기본값은 `"advisory"`다. 현 코드에서 이 필드는 Codex `sync()`의 `event is None` 경로에서만 경고 문구를 강화한다. 즉 어떤 이벤트가 events.json에서 미지원(`null`)으로 선언됐는데 `enforcement: "block"`이면 `[warn] ... (block 강제 상실) → 비활성`을 출력한다. Codex는 현재 4종 이벤트를 모두 지원하므로 reference 매니페스트의 PreToolUse 차단 훅은 이 경로를 타지 않는다. Claude `sync()`는 `enforcement`를 읽지 않는다.
- **금지(필수)**: manifest에 에이전트 고유 표기 직기 — `mcp__*` 형식 툴명, `Write|Edit` 같은 매처 문자열, `apply_patch`, 특정 에이전트 설정 파일 경로. 전부 정규형으로만. (lint/conformance 검사 대상 — reference: `check._lint_manifest_canonical`이 `mcp__`/`Write|Edit`/`apply_patch` grep.)

### 2.3 reference manifest (현 빌드)

reference 빌드는 `infra/hooks/manifest.json`에 4개 엔트리를 선언한다. 선언된 4개 스크립트는 모두 `infra/hooks/`에 존재한다. `auto_pull.py`까지 포함하면 훅 관련 Python 파일은 5개지만, `auto_pull.py`는 manifest 엔트리가 아니라 `session-log-remind.py`가 import해서 호출하는 helper다.

| event | match | script | mode | fallback | enforcement | strict | 스크립트 실재 |
|---|---|---|---|---|---|---|---|
| `SessionStart` | (없음) | `session-start.py` | on | (drop) | advisory | — | ✅ |
| `UserPromptSubmit` | (없음) | `session-log-remind.py` | on | (drop) | advisory | — | ✅ |
| `PostToolUse` | `action: file_edit` | `auto-commit.py` | (base) | runtime | block | — | ✅ |
| `PreToolUse` | `mcp: {server: linear, tool: create_issue}` | `confirm-action.py` | (base) | runtime | block | true | ✅ |

현 빌드의 5개 훅 관련 파일 요약:

| 파일 | manifest 등록 | 입력 | 주요 분기 | 출력·종료 |
|---|---:|---|---|---|
| `session-start.py` | ✅ `SessionStart` | 정규 JSON stdin | event 불일치, JSON 파싱 실패, `.teammode-active` 부재, engine import/수집 실패면 no-op | 활성 시 **세션당 1회 auto-pull**(throttle·실패무해) 후 Claude additionalContext JSON stdout, 항상 exit 0 의도 |
| `session-log-remind.py` | ✅ `UserPromptSubmit` | 정규 JSON stdin | event 불일치, JSON 파싱 실패, `.teammode-active` 부재면 no-op. 활성 시 멤버 식별(TEAMMODE_MEMBER env 1순위→config 단일 fallback→없으면 폴백) + 내 파일 mtime 기반 age·카운터 판정. check_reset: 내 파일 mtime 변화 또는 날짜(06시 컷) 바뀜 → count=0 + return(안 보챔). (**pull 안 함** — 2026-06-17 P0 hook hang 수정으로 매 프롬프트 pull 을 SessionStart 1회로 분리) | 필요 시 **`hookSpecificOutput.additionalContext`+`systemMessage` JSON stdout**(normalize 재방출로 전파). strong(age≥1800 & 30분 throttle) OR weak(count%5==0) 발화, count·offset 이어쓰기 키트(Read 끝 20줄+Edit) 표시. 정상 exit 0. 상태파일 write 실패는 OSError catch(무해). |
| `auto_pull.py` | ❌ helper | 함수 호출 | 상태파일 throttle, git pull 실패, 예외를 결과 객체로 흡수 | CLI main 없음. `AutoPullResult` 반환, 예외 전파 금지 의도. **호출처: session-start.py(세션당 1회)** — 종전 session-log-remind(매 프롬프트)에서 이전 |
| `auto-commit.py` | ✅ `PostToolUse` | 정규 JSON stdin | event/action 불일치, `.teammode-active` 부재, `git_ops` 부재, files 없음, 예외면 no-op | 정규 `files`만 commit 대상으로 넘기고 push 금지, 항상 exit 0 |
| `confirm-action.py` | ✅ `PreToolUse` | 정규 JSON stdin + 첫 argv marker | event 불일치, `.teammode-active` 부재, 대상 MCP 불일치, 사람 allow 신호 있으면 통과 | allow 없으면 deny JSON stdout + stderr, exit 2 |

### 2.4 정규 이벤트 (0.2)

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

`match` 객체의 정규 shape는 다음 둘 중 하나다. 정확히 하나의 키만 두는 것이 의도된 형태지만, 현 reference는 이를 별도 검증하지 않는다.

```jsonc
{ "action": "file_edit" }                                  // (a) 빌트인 행위 클래스
{ "mcp": { "server": "linear", "tool": "create_issue" } }  // (b) MCP 툴 — 정규 서버명
```

- **정규 행위 클래스 0.2**: `file_edit`(파일 생성·수정) 하나만. `shell_exec`·`file_read` 등은 필요 입증 시 minor bump로 추가.
- **정규 서버명(필수)**: MCP 서버 등록 별칭은 환경마다 다르다(`slack-tgates`, `claude_ai_Google_Calendar` 등). manifest는 `services` 선언(§7)의 **정규 서버명**(provider 식별자: `linear`·`slack`·`notion`·`google` 등)만 참조한다. 정규 서버명 → 실제 등록 별칭 매핑 보장은 어댑터의 등록 시점 책임(§2.8).

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
// agents/codex/events.json (reference) — PreToolUse: "PreToolUse"(지원), file_edit: "apply_patch",
//   mcp_tool_format: "mcp__{server}__{tool}"
```

규칙(필수):
1. `events`에는 §2.4의 **모든 정규 이벤트 키가 존재**해야 한다. 미지원이면 `null` — 키 누락 금지. 단 현 `conformance/check.py lint`는 events 완전성을 검사하지 않는다.
2. `actions`에는 0.2의 모든 정규 행위 클래스 키 존재. 미지원이면 `null`. 단 현 lint는 actions 완전성을 검사하지 않는다.
3. 에이전트별 특수 처리(이벤트 skip·매처 변형)를 설치 코드에 하드코딩 금지 — 전부 이 파일로.
4. `mcp_tool_format`의 치환 변수는 `{server}`·`{tool}` 둘. `{server}`에는 어댑터가 해석한 **실제 등록 별칭**이 들어간다.
5. 현 reference 값:
   - Claude: `events` 4종 모두 동일명, `actions.file_edit = "Write|Edit"`, `mcp_tool_format = "mcp__{server}__{tool}"`, settings 기본 파일 `~/.claude/settings.json`.
   - Codex: `SessionStart`·`UserPromptSubmit`·`PreToolUse`·`PostToolUse` 4종 모두 지원, `actions.file_edit = "apply_patch"`, `mcp_tool_format = "mcp__{server}__{tool}"`, config 기본 파일 `~/.codex/config.toml`.

### 2.7 adapter.py — 설치 시점 계약

어댑터는 다음 CLI를 구현해야 한다(필수). 디스패처가 호출한다.

```
adapter.py [global-options] sync [--on|--off]   # manifest → 에이전트 설정 동기화
adapter.py [global-options] uninstall           # tm-mode 훅 제거 + tm-mode 스킬 제거
adapter.py [global-options] install-mcp         # services 연결 provider MCP 별칭 등록
adapter.py [global-options] install-skills      # infra/skills/base/* 설치
```

구현된 global options:

| 어댑터 | settings/config 옵션 | team config 옵션 | MCP 옵션 | provider 옵션 | skills 옵션 | python 옵션 |
|---|---|---|---|---|---|---|
| Claude | `--settings`, 기본 `~/.claude/settings.json` | `--config`, 기본 `<team_root>/team.config.json` | `--mcp-config`, 기본 `~/.claude.json` | `--providers-dir` | `--skills-dir`, 기본 `~/.claude/skills` | `--python`, 기본 `sys.executable` |
| Codex | `--config`, 기본 `~/.codex/config.toml` | `--team-config`, 기본 `<team_root>/team.config.json` | 없음. `--config` 파일 안 `# teammode-mcp-*` 블록 사용 | `--providers-dir` | `--skills-dir`, 기본 `~/.codex/skills` | `--python`, 기본 `sys.executable` |

주의: 위 표는 어댑터 `adapter.py` 자체의 CLI 계약이다. `install.py --<agent> ...` 디스패처 게이트는 별도로 `--settings` 또는 `--install`만 안전 의도로 인정하며, Codex 어댑터의 `--config`는 이 게이트를 통과시키지 않는다(§4.2).

공통 CLI 종료코드:

- 정상 경로의 `main()`은 항상 `0`을 반환한다. 각 동사의 change message는 stdout으로 출력한다.
- argparse 사용법 오류(필수 subcommand 누락, 알 수 없는 옵션 등)는 Python argparse 기본 동작으로 stderr 출력 후 exit 2다.
- 어댑터 내부 예외는 top-level에서 포괄 catch하지 않는다. 예: 잘못된 manifest shape가 `KeyError`를 일으키면 비정상 종료할 수 있다. `install.py` wire는 어댑터 반환 rc가 0이 아니면 해당 에이전트를 실패로 집계한다.
- `uninstall` CLI는 훅 제거 뒤 `uninstall_skills()`도 호출한다. Codex `uninstall()`은 hook 블록과 MCP 블록을 함께 제거하고, 그 뒤 skills 제거가 실행된다.

`sync`의 구현 계약:

1. 대상 엔트리 선택:
   - `mode is None` 또는 `mode == "off"`: manifest에서 `mode`가 없는 base 엔트리만 대상.
   - `mode == "on"`: base 엔트리 + `mode: "on"` 엔트리 대상.
   - 현재 구현은 마지막 sync 상태를 저장하지 않는다. 플래그 없는 `sync`는 항상 off와 같은 base-only 동작이다.
2. 이벤트 번역:
   - `events.json.events[canonical_event]`를 읽는다.
   - 키가 없으면 `None`처럼 미지원 취급한다.
   - 값이 `null`이면 등록 생략 + `[warn] <script>: <agent> 미지원(이벤트 <event>) → 비활성`.
   - Codex에서 `enforcement == "block"`이면 위 경고에 `(block 강제 상실)`이 추가된다.
3. MCP 매처 전처리:
   - `team.config.json`이 없거나, JSON 파싱 실패거나, top-level object가 아니거나, `services`가 object가 아니면 `_load_services()`는 `None`을 반환한다. 이때 빈 슬롯 규칙과 install-mcp 선행 검사는 적용하지 않는다(L1 동작 보존).
   - `services`가 dict이고 match가 `{"mcp": ...}`이면 provider 연결 여부를 먼저 본다. provider 팩의 `services` 역할 목록 중 하나라도 `team.config.json.services[role].provider == canonical_server`이면 연결됨이다. provider 팩을 못 찾으면 fallback으로 services 값 중 provider가 같은 슬롯이 있는지 본다.
   - 연결되지 않았으면 fallback과 무관하게 해당 엔트리 생략 + `[info] <script>: '<provider>' 역할 슬롯 미연결 → MCP 매처 생략(빈 슬롯, 슬롯 연결 후 sync 재실행)`.
   - 연결됐지만 MCP 등록 파일/블록에 `_teammode_managed: true`인 alias가 없으면 install-mcp 미선행으로 보고 해당 엔트리 생략 + `[warn] ... MCP 별칭 미보장(install-mcp 선행 필요) → 이 매처만 생략`.
4. 매처 번역:
   - `match` 없음: `(matcher=None, expressible=True)`.
   - `{"action": "file_edit"}`: `events.json.actions.file_edit` 문자열 사용. Claude는 `Write|Edit`, Codex는 `apply_patch`.
   - `{"mcp": {"server": S, "tool": T}}`: `events.json.mcp_tool_format`에 `server=resolve_server_alias(S)`, `tool=T`를 치환한다. `resolve_server_alias`는 정규 서버명에 `tm-` 접두를 붙인다(`linear`→`tm-linear`) — teammode 가 등록하는 별칭 네임스페이스라 사용자 동명 MCP 와 충돌 없이 공존한다. 매처 문자열은 런타임 실 도구명(`mcp__tm-linear__create_issue`)과 일치하고, normalize 가 그 별칭을 정규 서버명으로 역환원한다(§6.1).
   - 표현 불가이고 `fallback == "runtime"`이면 무매처로 등록한다. 표현 불가이고 fallback이 drop이면 `[warn] ... 매처 표현 불가 → 비활성` 후 생략한다.
5. 커맨드 생성:
   - 반드시 `<python> <agents/<name>/normalize.py> <script> [args]` 형식이다. 공통 스크립트를 직접 등록하지 않는다.
   - `<python>` 기본값은 설치 시점 `sys.executable` 절대경로다. `--python`을 주면 그 문자열을 그대로 쓴다.
   - `_to_slash(s)`가 모든 백슬래시를 `/`로 바꾼 뒤 `_quote_arg(s)`가 공백·탭·큰따옴표 포함 토큰을 큰따옴표로 감싼다. 이미 같은 따옴표로 감싼 토큰은 그대로 둔다. 빈 문자열은 `""`가 된다. 단순 토큰은 인용하지 않는다.
   - `args`는 별도 quote 없이 command string 뒤에 그대로 append된다.
6. 소유권:
   - 훅 소유 판정은 command 문자열이 이 어댑터의 `normalize.py` 절대경로를 포함하거나, `agents/<agent>/normalize.py` 꼬리 경로를 포함하는지로 한다.
   - 비교 전 `_to_slash`를 적용해 과거 백슬래시 등록도 소유로 인식한다.
   - 단순히 `agents/`가 들어있다는 이유로 소유 처리하지 않는다.
   - 사용자 훅은 삭제·수정하지 않는다.
7. 멱등:
   - Claude는 JSON settings를 읽어 `hooks` object를 upsert/delete한 뒤 `json.dumps(indent=2, ensure_ascii=False) + "\n"` 결과가 원문과 다를 때만 쓴다. 깨진 JSON은 `{}`로 취급한다.
   - Claude는 같은 event/matcher에 소유 훅이 있으면 첫 hook command 와 timeout 을 upsert 한다. command 또는 timeout 이 다르면 갱신하고 `[update]`를 반환한다. manifest 에 timeout 이 없으면 기존 timeout 키를 제거한다(기존 5000 잔존 방지).
   - Claude는 manifest 대상 command 집합에 없는 소유 훅을 제거한다. event 배열이 비면 event key도 삭제한다.
   - Codex는 TOML 파서를 쓰지 않고 `# teammode-hooks-start`부터 `# teammode-hooks-end`까지의 관리 블록을 통째로 렌더·교체한다. 블록이 없으면 파일 끝에 append한다.
   - Codex hook 블록은 event마다 `[[hooks.<event>]]`, 선택적 `matcher = "..."`, `[[hooks.<event>.hooks]]`, `type = "command"`, `command = ...`, `timeout = <seconds>`를 쓴다. command 문자열은 작은따옴표 literal을 우선 사용하고, command에 작은따옴표가 있으면 큰따옴표 TOML 문자열로 escape한다.
   - Codex에서 등록 대상이 0개여도 빈 teammode hook 블록은 렌더 대상이다. 기존 파일과 다르면 write된다.
8. 출력:
   - warnings와 infos는 `sync()` 안에서 직접 print한다.
   - 파일 변경이 없고 warnings/infos도 없으면 반환 리스트에 `[ok] 변경 없음`을 넣는다.
   - 파일 변경이 있으면 Claude는 `[add]`, `[update]`, `[remove]` 메시지를 반환하고, Codex는 `[sync] Codex 훅 <n>개 등록`을 반환한다.

`install.py` wire와의 관계:

- 현재 `install_lib.wire_agents()`는 감지된 각 에이전트에 대해 `install-mcp → sync --on → install-skills` 순서로 어댑터를 호출한다.
- 한 에이전트에서 `install-mcp`가 실패하면 그 에이전트의 `sync`와 `install-skills`는 생략되고, 다른 에이전트 배선은 계속된다. 실패 집계의 wire exit code는 3이다.
- `sync` 실패 시 해당 에이전트의 `install-skills`도 생략된다.
- `install-skills` 실패는 해당 에이전트 실패로 집계하지만 다른 에이전트 배선은 계속된다.
- 격리 모드(`--settings`)에서는 어댑터별 settings/config, Claude MCP 파일, skills dir이 모두 격리 하위 경로로 명시 전달된다. 실호스트 모드에서는 `--yes` 게이트를 통과한 뒤 home 기준 기본 경로를 쓴다.

### 2.8 install-mcp의 의무

`install-mcp`는 L2 등록기의 배선 동사다 — 팀이 고른 **공식(없으면 자작) 벤더 MCP를 마련하고 그 정규 서버명 alias를 에이전트 설정에 등록**한다. tm-mode는 여기서 끝이고, 동작(이슈 생성·일정 추가)은 AI가 등록된 `mcp__<alias>__<벤더도구>`를 직접 호출한다 — install-mcp가 동작을 래핑하거나 `role_server`로 중계하지 않는다.

현 구현의 `install-mcp`는 실제 MCP 서버 실행 command를 placeholder에만 담고 직접 제작하지는 않는다(0.2 한계). 계약은 **연결된 provider의 정규 서버명 alias가 에이전트 설정에 teammode 관리 항목으로 존재하도록 보장**하는 데 있다. 실행 가능한 MCP 서버 정의는 provider 팩의 `mcp.register_hint`가 안내하며, 그 보강(공식 MCP를 본 레포로 가져와 두거나 자작)은 L2 등록기/connect 계층(§5.4)이 채운다.

**공식/자작 분기.** install-mcp는 등록만 책임지고 **마련**(공식 가져오기 / 자작)은 connect 계층(§skills 5.4)에서 일어난다 — 단 install-mcp는 두 경로의 산출물을 **동일하게** 다룬다. 공식 MCP든 자작 MCP든 본 레포 `infra/mcp/<provider>/`에 코드+실행 메타로 놓이고, 정규 서버명(`resolve_server_alias(provider)`)으로 같은 alias 등록을 받는다. install-mcp에 "자작이라 다르게" 처리하는 분기는 없다.

**자작 경로 디테일**(공식 MCP 레포가 없을 때만, 공식 우선):

1. provider 공식 API 스펙(REST/GraphQL 문서)을 출처로 삼아 Python MCP SDK로 서버를 작성한다 — **그 슬롯 역할에 필요한 도구만** 노출한다(calendar면 list_events / create_event 수준).
2. `infra/mcp/<provider>/`에 서버 코드와 실행 command를 두고 본 레포에 커밋한다 = 팀 공유 보관소. 다음 멤버는 재사용하고 재자작하지 않는다.
3. 토큰은 공식 MCP와 같은 경로(env / 로컬 금고 0600, §5.4·§7.5)다. 자작이라고 별도 토큰 경로를 만들지 않는다.
4. 첫 자작 직후 적대검수(서브에이전트)로 노출 도구의 실동작을 검증한다.

자작 MCP는 **그 벤더 전용 MCP**다 — provider API를 감싼 도구를 그대로 노출할 뿐, 역할 통일 동사(`issues_create` 같은)를 만들지 않는다. 그건 폐기한 `role_server`/역할 추상화(B안)의 부활이다. tm-mode는 자작 MCP에 대해서도 연결(등록)만 한다(A안). 상세 7단계는 `docs/archive/2026-06-25-L2-redesign.md` "MCP 마련"을 참조한다.

공통 규칙:

1. 입력 소스는 `team.config.json.services`다. `_load_services()`가 dict를 반환할 때만 연결 provider를 계산한다. 파일 부재·깨진 JSON·`services` 누락·`services` 비object는 연결 provider 0개와 같다.
2. 연결 provider는 `services` 값 중 object이고 `provider`가 비어있지 않은 문자열인 항목에서 수집한다. 같은 provider가 여러 역할에 있어도 1회만 등록한다.
3. provider 팩은 `providers.lookup(provider, providers_dir=...)`로 읽는다. 팩이 없거나 lookup 예외가 나면 추측하지 않고 `[info] <provider>: provider 팩 없음 → MCP 등록 생략`.
4. alias는 `resolve_server_alias(provider)` 결과다. 현 구현은 정규 서버명에 `tm-` 접두를 붙인다(`linear`→`tm-linear`, 멱등 — 이미 접두 붙은 입력은 그대로). teammode 소유 네임스페이스라 사용자가 직접 등록한 동명 MCP(`linear`)와 키 충돌 없이 공존한다. 등록 항목의 `_canonical_server`에는 별칭이 아닌 정규 서버명을 담는다.
5. sync의 MCP 매처 보장은 이 alias에 teammode 관리 마커가 있는지를 본다. Claude는 `mcpServers[alias]._teammode_managed is True`, Codex는 teammode MCP 블록 안 `[mcp_servers.<alias>]` 존재를 `_teammode_managed: True`로 파싱한다.

Claude 구현(`~/.claude.json` shape):

1. 기본 MCP 파일은 `~/.claude.json`이고 CLI `--mcp-config`로 바꿀 수 있다. Codex 상속 footgun 방지를 위해 `_SEALED` 센티넬이 있으며, 봉인 상태에서 부모 `install_mcp()`를 호출하면 `NotImplementedError`가 난다.
2. `_read_mcp_config()`는 파일 부재·깨진 JSON·top-level 비object를 `{}`로 취급한다. 정상 object면 전체를 보존하고 `mcpServers`만 수정한다.
3. 등록 entry는 다음 placeholder다.
   ```jsonc
   {
     "_teammode_managed": true,
     "_canonical_server": "<provider>",
     "_register_hint": "<provider pack mcp.register_hint or empty>"
   }
   ```
4. 기존 alias가 있고 `_teammode_managed: true`이며 entry가 같으면 무변경이다.
5. 기존 alias가 있고 teammode 소유가 아니면 `[warn] <alias>: 사용자 등록 MCP 서버 존재 → 무접촉` 후 그 alias는 desired에서 제외한다. 사용자 entry와 다른 top-level 데이터(`projects` 등)는 보존한다.
6. 등록·갱신이 필요한 teammode 소유/부재 alias는 `servers[alias] = entry` 후 `[mcp] <alias> 등록`.
7. 제거는 `mcpServers` 안 teammode 소유 entry 중 현재 desired alias가 아닌 것을 삭제하고 `[remove-mcp] <alias>`를 반환한다.
8. 빈 슬롯 안전:
   - 원본에 `mcpServers` 키가 없고 등록할 서버도 없으면 파일을 생성하거나 touch하지 않는다.
   - 원본에 `mcpServers` 키가 있거나 서버가 남아 있으면 정규 JSON으로 직렬화해 원문과 다를 때만 쓴다.
9. 반환 메시지가 없으면 desired alias 수로 구분한다. desired alias가 있으면 `[ok] 변경 없음 (<n>개 provider 등록됨)`, 없으면 `[info] 연결된 MCP provider 없음 (빈 슬롯)`.

Codex 구현(`~/.codex/config.toml` shape):

1. MCP 등록은 `--config` 파일 안의 `# teammode-mcp-start` / `# teammode-mcp-end` 블록으로만 관리한다. 별도 `--mcp-config`는 없다.
2. `_read_mcp_servers()`는 이 블록 안의 `[mcp_servers.<name>]` header만 regex로 읽어 `{name: {"_teammode_managed": True}}`처럼 반환한다.
3. 등록 블록 entry는 다음 TOML placeholder다. 섹션 키는 별칭(`tm-<provider>`)이고 `_canonical_server`에는 정규 서버명을 담는다.
   ```toml
   [mcp_servers.tm-<provider>]
   _teammode_managed = true
   _canonical_server = '<provider>'
   _register_hint = '<provider pack mcp.register_hint>'
   ```
4. provider가 하나 이상 있으면 전체 teammode MCP 블록을 렌더해 기존 블록을 교체하거나 파일 끝에 append한다. write가 일어나면 `[mcp] <alias> 등록`을 alias마다 반환하고, 바이트 동일이면 `[ok] 변경 없음 (<n>개 provider 등록됨)`을 반환한다.
5. provider가 하나도 없으면 기존 teammode MCP 블록만 제거한다. 블록이 없으면 파일을 touch하지 않고 `[info] 연결된 MCP provider 없음 (빈 슬롯)`을 반환한다.
6. 한계: 이 placeholder에는 `command`/`args`가 없다. Codex 런타임이 실제 MCP 서버로 기동하려 하면 에러가 날 수 있다. 현 계약은 sync가 참조할 alias 슬롯 보장까지다.
7. 한계: Codex 구현은 TOML 전체를 파싱하지 않으므로 teammode 블록 밖의 사용자 `[mcp_servers.<same>]`와의 중복 collision을 검사하지 않는다. teammode가 관리하는 것은 marker 블록뿐이다.

### 2.9 폴백 정책

manifest 엔트리의 `fallback` — 어댑터가 그 엔트리를 자기 에이전트로 표현 못 할 때 동작.

| 값 | 발동 조건 | 동작 |
|---|---|---|
| `"drop"` (기본) | 이벤트 또는 매처를 표현 불가 | 등록 생략 + `[warn] <script>: <agent> 미지원 → 비활성`(필수, 무음 금지) |
| `"runtime"` | 매처만 표현 불가(이벤트는 지원) | 무매처로 등록 + normalize 자가 필터(§2.10-2)로 의미 보존 |

- `"runtime"`인데 **이벤트 자체가 미지원**(events.json `null`)이면 표현 방법이 없어 `"drop"`과 동일 동작 + `[warn]`(필수). reference 어댑터: `event is None`이면 runtime이어도 drop.
- **빈 슬롯 우선 규칙(필수)**: `mcp` 매처가 참조하는 provider 역할 슬롯이 미연결(§7.2)이면 `fallback` 무관 등록 생략 + `[info]` — 에러 아님(빈 슬롯 = 1급 시민). 슬롯 연결 후 `sync` 재실행으로 활성화.
- **config 파일 부재와 빈 services는 다르다**: `team.config.json` 부재/파싱 실패/services 누락은 services 정보 미지(`None`)라 빈 슬롯 규칙을 적용하지 않는다. 이 경우 L1 호환을 위해 MCP match 번역을 그대로 시도한다. 반면 파일이 있고 `services: {}`이면 명시적 빈 슬롯으로 보고 `[info]` 생략한다.
- **install-mcp 미선행은 warn**: services상 연결됐지만 alias 보장이 없으면 빈 슬롯이 아니므로 `[info]`가 아니라 `[warn]`이고, 해당 MCP match 엔트리만 생략한다.
- 선택 가이드: 빠져도 되는 편의 → `drop` / 빠지면 안 되는 안전장치(확인·차단류) → `runtime`(+필요시 `strict`).

### 2.10 normalize — 런타임 계약

**입력**: 에이전트 원어 JSON(stdin) → **출력**: 공통 스크립트에 정규 JSON(stdin) 전달.

**정규 입력 스키마 (canonical input) 0.2:**

```jsonc
{
  "event": "PostToolUse",            // 필수. 정규 이벤트 (§2.4)
  "action": "file_edit",             // 해당 시. 정규 행위 클래스
  "tool": { "kind": "mcp",           //   해당 시(Pre/PostToolUse). "mcp" | "builtin"
            "server": "linear",      //   kind=mcp일 때. 정규 서버명
            "name": "create_issue" },
  "files": ["path"],                 // file_edit일 때. normalize가 받은 file_path 문자열 배열
  "prompt": "사용자 입력 …",          // UserPromptSubmit일 때
  "agent": "codex",                  // 필수. 출처 에이전트명
  "raw": { }                         // 선택. 원어 전문(탈출구). 생략 시 {}
}
```

현 normalize는 `event`·`agent`·`raw`를 항상 출력한다. stdin이 비어 있으면 원어 `{}`로 취급해 `event`는 빈 문자열이 될 수 있다. `raw`는 원어 dict 그대로다. 나머지는 해당 시에만 붙는다. 공통 스크립트는 이 스키마만 신뢰하고, 보안상 `raw.tool_input` 같은 모델 제어 payload를 allow 신호로 쓰면 안 된다(`confirm-action.py`가 이를 지킨다).

**normalize의 의무(필수):**
1. 호출 형식: `normalize.py <script> [args...]`. 현 구현은 `<script>`를 검증하지 않고 `HOOKS_DIR / script`로 결합해 실행한다. argv가 없으면 stderr에 `[normalize] script 인자 필요`를 쓰고 exit 0.
2. 입력 로드: stdin 전체를 읽는다. 공백뿐이면 `{}`로 처리한다. JSON 파싱·변환 중 `JSONDecodeError`, `ValueError`, `KeyError`, `TypeError`가 나면 변환 실패 정책으로 간다.
3. 이벤트 변환:
   - 원어 event는 `raw["hook_event_name"]` 우선, 없으면 `raw["event"]`, 없으면 `""`.
   - `events.json.events`의 역매핑으로 정규 이벤트를 찾는다. 매핑에 없으면 원어 event 문자열을 그대로 쓴다.
   - `agent`는 `events.json.agent`이고 기본값은 Claude normalize 기준 `"claude"`다. Codex normalize는 Claude normalize 모듈의 경로 상수를 Codex로 재바인딩해 Codex events.json을 보게 한다.
4. `UserPromptSubmit`: `prompt = raw.get("prompt", "")`.
5. `PreToolUse`/`PostToolUse`:
   - `tool_name = raw.get("tool_name", "")`, `tool_input = raw.get("tool_input", {}) or {}`.
   - `tool_name`이 비어 있으면 tool/action/files를 붙이지 않는다.
   - `tool_name`이 `mcp_tool_format`에 맞으면 `tool = {"kind": "mcp", "server": <server>, "name": <tool>}`.
   - 아니면 `tool = {"kind": "builtin", "name": tool_name}`.
   - builtin tool명이 `events.json.actions`의 OR 문자열과 동등 매칭되면 `action`을 붙인다. Claude `Write|Edit`은 `|`로 나눠 `Write` 또는 `Edit`과 동등 비교한다. Codex는 `apply_patch`와 동등 비교한다.
   - action이 잡힌 경우 `tool_input.file_path`가 truthy이면 절대경로 변환·경로탈출 검증 없이 `files = [file_path]`, 아니면 `files = []`.
6. MCP 역파싱:
   - `mcp_tool_format` 템플릿을 regex로 바꿔 `{server}`와 `{tool}`을 capture한다.
   - capture한 `{server}`는 런타임 실 도구명의 등록 별칭(`tm-linear`)일 수 있으므로 `_canonical_server`로 `tm-` 접두를 떼어 정규 서버명(`linear`)으로 환원해 출력한다(`resolve_server_alias`의 역). 이래야 self-filter(§6.2)와 `confirm-action.py`가 manifest의 정규 서버명(§2.5)과 일치한다. 접두 없는 사용자 동명 서버(`linear`)는 그대로 보존된다.
7. 런타임 자가 필터:
   - manifest는 매 호출 시 `infra/hooks/manifest.json`에서 읽는다. 파일 부재·깨진 JSON이면 빈 list다.
   - lookup key는 `(script, canonical_event)`이고 첫 번째 일치 엔트리만 쓴다. 같은 `(event, script)` 중복은 현재 lint가 검사하지 않는다.
   - entry가 있고 `fallback == "runtime"`일 때만 필터한다.
   - action match는 `canonical.action == match.action`.
   - mcp match는 `canonical.tool.kind == "mcp"`이고 server/name이 match의 server/tool과 같아야 한다.
   - match가 없으면 통과한다. 알 수 없는 match key도 현재 `_matches_filter()`에서는 `True`를 반환한다.
   - 불일치면 공통 스크립트를 실행하지 않고 exit 0.
8. 공통 스크립트 실행:
   - `subprocess.run([sys.executable, str(HOOKS_DIR / script)] + extra_args, input=json.dumps(canonical, ensure_ascii=False), capture_output=True, text=True)`로 실행한다. 현 구현은 `script`가 파일명인지, `..`를 포함하는지, `infra/hooks/` 밖으로 탈출하는지 별도로 검증하지 않는다.
   - 공통 스크립트 stdout/stderr를 normalize 자신의 stdout/stderr로 그대로 재방출한다.
   - normalize 종료코드는 공통 스크립트 returncode와 같다. 따라서 `confirm-action.py`의 exit 2 차단이 보존된다.
   - script 파일이 없으면 Python subprocess가 보통 exit 2와 stderr를 내며, normalize는 그 returncode를 그대로 반환한다.
9. 변환 실패:
   - 같은 `script`를 가진 manifest 엔트리 중 하나라도 `strict` truthy면 stderr에 `[normalize] 변환 실패: <exc>`를 쓰고 exit 1.
   - strict가 없으면 같은 경고를 stderr에 쓰고 exit 0.
10. UTF-8:
   - Claude normalize `main()`은 시작 시 `_ensure_utf8_io()`를 호출한다.
   - Codex normalize는 Claude normalize `main`을 그대로 재사용하므로 같은 보정 경로를 탄다.

### 2.11 크로스에이전트 (Claude ↔ Codex)

- **번역 코어 공유(reference)**: Codex 어댑터는 Claude `Adapter`를 상속해 번역 코어(events.json 기반)를 재사용하고, Codex 고유의 **config 포맷(TOML 블록) + 폴백 처리**만 재정의. Codex normalize는 Claude normalize의 함수를 import해 경로 상수(events.json·manifest)만 Codex 컨텍스트로 재바인딩.
- **Codex PreToolUse 지원**: Codex는 events.json에서 `PreToolUse: "PreToolUse"`로 4종 이벤트를 모두 지원한다. `confirm-action.py`·`kb-write-guard.py` 같은 `enforcement: block` 차단 훅도 `config.toml`의 `[[hooks.PreToolUse]]`로 등록되어 exit-2 차단이 발효한다. Codex 실 훅 입력은 `tool_name`/`tool_input`(또는 top-level `name`/`input`) 형태이며, apply_patch는 `tool_input.command`에 patch 문자열을 담는다(2026-06-21 캡처) — normalize가 파일 헤더를 정규 `files[]`로 변환한다.
- 독립 구현은 `agents/` 디렉토리 구조나 Python을 그대로 쓸 필요 없다. 보존 대상은 **선언 포맷(manifest.json·events.json)과 의미**이지 구현 언어·파일 배치가 아니다(§6 C2).
- Codex MCP 등록 한계는 정직하게 표면화한다. `install-mcp`는 `config.toml`에 command 없는 placeholder 블록만 쓰며, 실제 기동 가능한 MCP 서버 정의는 사용자가 보강해야 할 수 있다.
- Codex `sync()`는 `PreToolUse`를 지원하므로 `confirm-action.py`를 `[[hooks.PreToolUse]]`로 등록한다. `install-mcp`로 등록한 MCP 도구 호출도 normalize → confirm 게이트 경로로 차단 가능하다.

### 2.12 스킬 해석 — 단일 소스 + 오버라이드

스킬 본문은 현 구현상 `infra/skills/base/<name>/SKILL.md`만 소스로 삼는다. 기존 초안의 `agents/<name>/skills/<skill>/SKILL.md` 오버라이드 탐색은 **현재 구현되어 있지 않다**. 오버라이드 해석·requires 게이트·traversal guard는 0.2에서도 미구현이다.

현 워킹트리의 base 스킬은 `tm-onboard`이다. `tm-connect`는 `infra/skills/core/tm-connect/`에 있다.

`install-skills` 구현 계약:

1. source discovery는 `self.skills_src_dir = <team_root>/infra/skills/base` 아래 child 중 디렉토리이고 `SKILL.md` 파일이 있는 것만 대상으로 한다. 정렬된 `iterdir()` 순서다.
2. target은 `<skills_dir>/<name>`이다. `skills_dir` 기본값은 Claude `~/.claude/skills`, Codex `~/.codex/skills`다. CLI `--skills-dir`로 격리/명시할 수 있다.
3. target이 없으면 parent를 만들고 우선 `os.symlink(src, target, target_is_directory=True)`를 시도한다. 성공 메시지는 `[skill] <name> 심링크`.
4. symlink가 `OSError`로 실패하면(주로 윈도우 — 심링크는 개발자모드/관리자 권한 필요): **윈도우(`os.name=='nt'`)는 정션을 먼저 시도**한다 — `subprocess`로 `cmd /c mklink /J <target> <src>`(py3.9라 `_winapi.CreateJunction`(3.12+) 대신). 정션은 권한 불필요 + 링크라 pull 시 소스 갱신이 반영된다(복사와 달리 stale 없음). 성공 메시지 `[skill] <name> 정션`. ⚠️ `cmd /c mklink`는 `cmd.exe`가 `/c` 뒤를 재파싱하므로 경로에 cmd 메타문자(`& | < > ^ " %`)가 있으면 명령 주입 위험이다 — 그런 경로는 정션을 건너뛰고 곧장 복사 폴백한다. 정션도 실패하거나 비윈도우면 `shutil.copytree(src, target)` + `_teammode_skill` 마커(`[skill] <name> 복사(폴백)`) — 무겁고 갱신 안 되는 최후 수단.
5. 소유 판정:
   - symlink면 `os.readlink(target)`을 절대화한 realpath가 src realpath와 같을 때 소유다.
   - directory면: 윈도우 정션은 `is_symlink`=False라, `os.path.realpath(target)`이 src realpath로 resolve되면 소유다. 그 외엔 `_teammode_skill` marker 파일이 있으면 소유다.
   - 그 외 또는 OSError면 비소유다.
6. target이 이미 있고 소유면 멱등 무변경이다. 심링크·정션은 링크라 source 변경이 자동 반영된다. 단 복사 폴백본은 source가 바뀌어도 현재 0.2 구현은 marker 존재만 보고 재복사하지 않는다(stale — 정션 도입으로 복사는 최후 수단이라 영향이 줄었다).
7. target이 이미 있고 비소유면 `[skip] <name>: 사용자 스킬 존재 → 무접촉`을 반환하고 덮어쓰지 않는다.
8. orphan cleanup: `skills_dir`가 존재하면 그 안 child 중 현재 source name 집합에 없는 항목을 본다. `is_owned_skill(child, skills_src_dir / child.name)`이 True면 symlink는 unlink, directory는 제거한다 — **윈도우는 `os.rmdir` 먼저**(정션이면 링크만 떼고 원본 무접촉, 복사 실디렉이면 비어있지 않아 실패→`shutil.rmtree`), 비윈도우는 `shutil.rmtree`. 그 후 `[remove-skill] <name>`을 반환한다.
9. 변경 메시지가 하나도 없으면 `[ok] 변경 없음`.
10. `uninstall_skills()`는 `skills_dir`가 없으면 `[ok] 제거할 스킬 없음`을 반환한다. 있으면 child 중 소유 스킬만 제거하고 `[remove-skill] <name>`을 반환한다. 제거할 것이 없으면 `[ok] 제거할 스킬 없음`.
11. `adapter.py uninstall` CLI는 훅 제거와 스킬 제거를 모두 실행한다. Claude 훅 uninstall은 소유 훅 제거 메시지가 없으면 빈 list를 반환할 수 있고, skills uninstall 메시지는 별도로 출력된다. Codex 훅 uninstall은 block 제거 여부에 따라 `[remove] tm-mode 훅 블록` 또는 `[ok] 제거할 블록 없음`을 반환한 뒤 skills uninstall을 실행한다.

`io_encoding.ensure_utf8_io()` 구현 계약:

1. 목적은 Windows native cp949 등 비UTF-8 stdout/stderr에서 한글 JSON·경고 출력이 `UnicodeEncodeError`로 죽는 것을 막는 것이다.
2. `_is_utf8(enc)`는 `codecs.lookup(enc).name == "utf-8"`이면 True다. `enc`가 falsy거나 lookup 실패면 False다.
3. `_reconfigure_stream(stream)`은 stream이 None이거나 `reconfigure` 속성이 없으면 무동작이다. 이미 UTF-8이면 무동작이다. 필요하면 `stream.reconfigure(encoding="utf-8")`를 호출하고, `ValueError`·`OSError`·`AttributeError`는 조용히 무시한다. `errors` 정책은 지정하지 않아 기존 정책을 유지한다.
4. `ensure_utf8_io()`는 stdout과 stderr만 보정한다. stdin은 보정하지 않는다.
5. 호출 지점은 adapter `main()`, normalize `main()`, `session-start.py`, `session-log-remind.py`, `confirm-action.py`, 그리고 다른 엔진 진입점이다. `auto-commit.py`와 `auto_pull.py`는 현 코드에서 이 함수를 호출하지 않는다.

---

## §3. 엔진 동사 (teammode.py) — 신규 명문화

엔진 `infra/teammode.py`는 현재 8개 동사만 known verb로 인정한다.

```
python infra/teammode.py <verb> --root <팀루트> [동사별 플래그]
verbs: on | off | log | context | pull | commit | update | issue
```

이 섹션의 ground truth는 현재 워킹트리의 `infra/teammode.py`, `infra/workday.py`, `infra/git_ops.py`이다. 2026-06-16 현재 이 레포에는 `install-skills` 관련 미커밋 변경(`infra/agents/*/adapter.py`, `infra/install*.py`, `tests/test_install_skills_l2c.py` 등)이 있으나, `teammode.py` 엔진 동사 자체는 위 8개가 전부다.

공통 불변식:

- 진입 시 `ensure_utf8_io()`를 호출해 stdout/stderr를 UTF-8로 보장하려 한다. stdin은 보정하지 않는다. 한글 오류 메시지·JSON 출력이 native 인코딩 때문에 깨지거나 크래시하는 것을 줄이기 위한 방어다.
- 팀 루트는 **명시 `--root` 값만** 쓴다. `TEAMMODE_HOME`, cwd, 설치 위치를 엔진 동사의 대상 루트로 추측하지 않는다.
- `--root`가 없거나 값 플래그만 있고 값이 없으면 `root is None`으로 취급한다. 모든 known verb에서 다른 동사별 필수 옵션보다 먼저 `[error] --root <팀루트> ...`를 stderr에 쓰고 exit 2.
- `team_root = Path(root).resolve()`만 수행한다. 존재 여부·디렉토리 여부·`team.config.json` 존재 여부는 공통 단계에서 검증하지 않는다. 이후 동사별 파일 IO나 git 검사에서 자연스럽게 처리된다.
- known verb가 아니면 두 갈래다. 동사 자체가 없으면 usage를 stderr에 쓰고 exit 2. 첫 non-flag가 있으나 known verb가 아니면 `[unimplemented] <verb>`를 stderr에 쓰고 exit 127.
- 엔진은 요약·판단을 하지 않는다. `log`는 받은 텍스트를 보존하고, `context`는 frontmatter 값을 옮기며, `issue`는 정규 스키마 echo까지만 한다.
- Python 레벨에서 잡지 않는 예외가 있다. 예를 들어 `on/off`의 어댑터 실행 실패, 배너 파일 읽기/쓰기 실패, 로그 파일 쓰기 실패 등은 별도 exit code로 변환되지 않고 일반 Python 예외로 종료될 수 있다(통상 traceback + exit 1). 명시적으로 처리한 입력 오류만 exit 2, git 비치명 실패만 exit 1, 미구현 동사만 exit 127이다.

argv 파서(`_parse_args`)는 `argparse`가 아니라 손파서다.

- 값 받는 플래그 화이트리스트: `--root`, `--settings`, `--author`, `--text`, `--now`, `--message`, `--title`, `--body`, `--assignee`, `--label`, `--priority`.
- 위 플래그를 만나면 다음 토큰을 값으로 소비한다. 다음 토큰이 `--다른플래그`처럼 생겼어도 값으로 소비한다. 다음 토큰이 없으면 값은 `None`이다. 같은 플래그가 반복되면 마지막 값이 남는다.
- 부울 플래그: `--install`, `--json`, `--push`. 기본값은 모두 `False`다.
- 화이트리스트 밖 `--flag`는 무시한다. 이때 다음 토큰을 값으로 소비하지 않는다. 그래서 미지 부울 플래그 뒤의 non-flag 토큰은 verb 또는 positional이 될 수 있다.
- 첫 non-flag 토큰이 `verb`가 되고, 그 뒤 non-flag 토큰들은 `positionals`에 순서대로 쌓인다. `issue --root <root> create`처럼 verb와 서브액션 사이에 값 플래그가 있어도 `create`는 positional로 남는다.
- extra positional은 `issue`의 첫 positional 외에는 현재 어떤 known verb에서도 사용하지 않는다.

### 3.1 on / off (settings 경유 — `--root` + (`--settings` 또는 `--install`) 필수)

`on/off`만 Claude 어댑터 `sync()`를 호출하므로 settings 경로 게이트가 있다. `log/context/pull/commit/update/issue`는 settings를 요구하지 않고, `--settings`가 와도 무시한다.

- settings 해석:
  - `--settings <경로>`가 있으면 그 문자열을 그대로 adapter settings path로 쓴다.
  - `--settings`가 없고 `--install`이 있으면 `os.path.expanduser("~/.claude/settings.json")`을 쓴다.
  - 둘 다 있으면 `--settings`가 우선한다. `--install`과의 충돌 오류는 없다.
  - 둘 다 없으면 `[error] --settings <경로> ... 또는 --install ...`을 stderr에 쓰고 exit 2.
- adapter 생성:
  - `infra/agents/claude/adapter.py`를 `runpy.run_path(..., run_name="__teammode_engine__")`로 로드한다.
  - `Adapter(agent_dir=<infra>/agents/claude, manifest_path=<infra>/hooks/manifest.json, settings_path=<resolved>, team_root=<INFRA.parent>)`를 만든다.
  - 여기서 adapter의 `team_root`는 `--root`로 받은 메모리 쓰기 대상이 아니라 엔진 설치 위치(`infra/..`)다. 현재 레포 실행에서는 둘이 보통 같지만 코드상 별개 축이다.
- 배너·personality config:
  - `_render_banner(team_root)`는 `<team_root>/memory/banner.txt`가 파일이면 UTF-8로 그대로 읽어 stdout에 출력한다.
  - 배너 파일이 없으면 `TEAMMODE_TEAM_NAME` 환경변수 값 또는 기본 `"teammode"`로 `=== <team_name> ===\n`을 만들고, `memory/` 디렉토리를 생성한 뒤 `memory/banner.txt`에 캐시한다. 이 env는 팀 루트 결정에는 쓰지 않는다.
  - `_read_team_field(team_root, field)`는 `<team_root>/team.config.json`의 `team.<field>`가 비어있지 않은 문자열일 때만 반환한다. config 부재·JSON 파싱 실패·타입 불일치·예외는 모두 `None`으로 흡수한다. `on/off`를 막지 않는다.
- `on` 실행 순서:
  - 배너를 stdout에 **동적 길이 펜스(fenced code block)로 감싸** 출력한다. 구체적으로: `_render_banner` 반환값을 `rstrip("\n")`한 뒤, 내용 안에 등장하는 가장 긴 연속 백틱 run보다 최소 1 더 긴(그리고 최소 3) 길이의 백틱 문자열을 펜스로 사용한다. 이를 통해 배너 내에 `` ``` `` 줄이 있어도 조기 종료 없이 배너 전체가 단일 코드블록 안에 담긴다. 출력 형식: `<fence>\n<banner_content>\n<fence>`.
  - `team.greeting`이 있으면 그 다음 줄에 출력한다.
  - adapter `sync(mode="on")`를 호출한다.
  - `<team_root>/.teammode-active`에 빈 문자열을 UTF-8로 쓴다. 부모 디렉토리 생성은 하지 않는다. 기존 파일은 덮어써진다.
  - `auto_update_on_start(team_root)`를 호출한다. 이는 upstream 엔진(infra/)을 자동 sync + 자동 커밋한다(push 절대 금지). dirty 가드·fetch 실패 등은 조용히 skip — on 을 막지 않는다.
  - 정상 완료 시 exit 0.
- `on`의 upstream 자동 업데이트(작업 D):
  - `auto_update_on_start`가 `git_ops.sync_from_upstream`을 호출한다. fetch 실패·remote 없음·오프라인 → on 막지 않고 조용히 skip.
  - 대상 경로(infra/, NOTICE.md)에 커밋 안 된 변경이 있으면 blocked=True 로 skip + 사람 알림만 출력.
  - 변경이 있으면 `do_commit(paths=res.paths, push=False)`로 paths 한정 자동 커밋. push 자동 절대 금지.
  - 커밋 성공 시 NOTICE 첫 불릿을 "엔진 업데이트됨: <내용>" 형식으로 출력.
  - 이 함수는 모든 예외를 삼킨다. `on`의 exit code에 영향을 주지 않는다.
- `off` 실행 순서:
  - adapter `sync(mode="off")`를 호출한다.
  - `<team_root>/.teammode-active`가 존재하면 삭제한다. 없으면 그대로 진행한다.
  - `team.farewell`이 있으면 그 문자열을 출력하고, 없으면 `tm-mode off — 상태 저장됨`을 출력한다.
  - 정상 완료 시 exit 0.
- 멱등성:
  - `on`은 반복 실행해도 같은 `.teammode-active` 빈 파일을 다시 쓰고 adapter sync를 다시 수행한다. 배너 파일이 이미 있으면 재생성하지 않는다. upstream은 매번 fetch를 시도할 수 있다.
  - `off`는 마커가 없어도 성공 경로로 진행한다. adapter sync는 매번 수행한다.

### 3.2 log (세션로그 기록 — `--root --author --text` 필수)

CLI:

```
python infra/teammode.py log --root <팀루트> --author <이름> --text <내용> [--now <ISO8601>]
```

- 필수 옵션:
  - `--author`가 없거나 값이 없으면 `[error] log: --author <이름> 가 필요합니다.`를 stderr에 쓰고 exit 2.
  - `--text`가 없거나 값이 없으면 `[error] log: --text <내용> 가 필요합니다.`를 stderr에 쓰고 exit 2.
  - 빈 문자열 값은 셸에서 전달 가능하다면 `text is None`은 아니므로 허용된다. `author=""`는 아래 author 검증에서 거부된다.
- `--now`:
  - 있으면 `datetime.fromisoformat(now_str)`로 파싱한다.
  - `ValueError`가 나면 오류가 아니라 `workday.now_kst()`로 폴백한다.
  - 없으면 `workday.now_kst()`를 쓴다.
  - 06시 컷과 naive/aware 처리의 실제 규칙은 §1.4를 따른다.
- author 검증(`_validate_author`):
  - 빈 문자열 거부: `author 가 비어 있습니다.`
  - `/` 또는 `\` 포함 거부: `author 에 경로 구분자가 포함될 수 없습니다: ...`
  - `"."`, `".."` 거부: `author 로 '.' 는 허용되지 않습니다.` 등.
  - 절대경로 거부: `author 는 절대 경로일 수 없습니다: ...`
  - 첫 글자가 `-` 또는 `_`이면 거부: `author 는 영숫자로 시작해야 합니다: ...`
  - 모든 문자가 `str.isalnum()` 또는 `-` 또는 `_` 중 하나가 아니면 거부: `author 에 허용되지 않는 문자가 있습니다: ...`
  - 실패 시 `[error] <메시지>`를 stderr에 쓰고 exit 2.
  - 코드 주석은 members.md 영문 이름 규약을 말하지만, 실제 검증은 Unicode `isalnum()`을 허용한다. ASCII 소문자만으로 제한하지 않는다.
- 경로·파일명:
  - `date_str = workday.workday_str(now)`.
  - `sessions_dir = <team_root>/memory/team/sessions/<author>`.
  - `log_path = <sessions_dir>/<date_str>.md`.
  - `log_path.resolve()` 문자열이 `sessions_dir.resolve()` 문자열로 시작하지 않으면 `[error] 로그 경로가 세션 디렉토리를 벗어납니다.`를 stderr에 쓰고 exit 2. author 검증 뒤의 이중 방어다.
  - 정상 경로면 `sessions_dir.mkdir(parents=True, exist_ok=True)`를 수행한다.
- 항목 라벨과 본문:
  - `time_label = now.astimezone(KST).strftime("%H:%M")`.
  - entry는 정확히 `\n## <HH:MM>\n\n<text>\n` 형식이다. 텍스트를 요약·escape·trim하지 않는다.
  - §1.4의 구현상 주의처럼 naive `now`의 라벨 변환은 Python 로컬 timezone 해석을 탄다.
- 새 파일 생성:
  - 파일이 없으면 frontmatter를 먼저 쓴다.
  - frontmatter는 정확히 `---\nauthor: <author>\ndate: <date_str>\nsummary: <summary>\n---\n`이다.
  - `summary`는 `text.strip().splitlines()[0]`가 있으면 그 첫 줄의 앞 100자이고, `text.strip()`이 비면 빈 문자열이다.
  - 그 뒤 entry를 쓴다.
- 기존 파일 append:
  - 파일이 있으면 frontmatter를 읽거나 검증하거나 고치지 않는다.
  - UTF-8 append 모드로 entry만 붙인다.
  - 같은 작업일 하루 1파일 불변식은 파일명으로만 적용된다. 이미 잘못된 frontmatter가 있어도 엔진은 수정하지 않는다.
- 성공 출력: `tm-mode log — <author>/<date_str>.md 기록됨`을 stdout에 쓰고 exit 0.

### 3.3 context (맥락 수집 — `--root` 필수, `--json` 선택)

CLI:

```
python infra/teammode.py context --root <팀루트> [--json]
```

`context`는 파일을 생성하거나 수정하지 않는다. INDEX, 세션로그 frontmatter, active 마커, config role을 읽어 구조화한다. 요약 생성은 하지 않는다.

- INDEX:
  - 읽는 경로는 `<team_root>/memory/INDEX.md`.
  - 파일이면 UTF-8로 읽는다. `OSError`는 빈 문자열로 흡수한다.
  - 파일이 아니면 빈 문자열이다.
- active 상태:
  - `<team_root>/.teammode-active` 존재 여부만 본다.
  - JSON에서는 `"state": "on"` 또는 `"off"`.
  - 텍스트에서는 `state: on (active)` 또는 `state: off`.
- 세션로그 수집(`_collect_members`):
  - 루트는 `<team_root>/memory/team/sessions`.
  - 이 디렉토리가 없으면 멤버 목록은 빈 배열이다.
  - 바로 아래 자식 중 디렉토리만 `member_dir.name` 기준 정렬 순회한다.
  - 각 멤버 디렉토리에서 `*.md` 중 `_is_session_log_name(p.stem)`이 true인 파일만 후보로 본다.
  - `_is_session_log_name`의 실제 판정은 느슨하다: stem 길이 ≥ 10, 앞 4글자가 숫자, 5번째 글자(`stem[4]`)가 `-`, 8번째 글자(`stem[7]`)가 `-`이면 true다. 월·일 자리 숫자 여부와 stem 뒤 추가 문자열은 검사하지 않는다.
  - 후보가 없으면 그 멤버 디렉토리는 출력에서 건너뛴다.
  - 후보가 있으면 `max(logs, key=lambda p: p.stem)`로 stem 사전식 최대 파일 1개를 고른다. 날짜 파싱은 하지 않는다.
  - 파일을 UTF-8로 읽고, `OSError`면 빈 문자열로 처리한다.
- frontmatter 파싱(`_parse_frontmatter`):
  - 텍스트가 `"---"`로 시작하지 않으면 빈 dict.
  - 첫 줄이 정확히 `---`가 아니면 빈 dict.
  - 이후 줄을 닫는 `---`까지 순회한다. 닫는 줄이 없으면 파일 끝까지 순회한다.
  - `:`가 있는 줄만 첫 콜론 기준으로 `key.strip()` / `value.strip()`을 저장한다. YAML 파서는 아니다. quoting, multiline, list는 해석하지 않는다.
  - 수집 객체는 `{author: <member_dir.name>, date: fm["date"] or latest.stem, summary: fm["summary"] or "", file: <team_root 상대경로>}`이다.
  - 구로그처럼 summary가 없으면 빈 문자열이다. 전문 fallback은 하지 않는다.
- role 보강(`_member_roles`):
  - `<team_root>/team.config.json`을 읽는다.
  - JSON object의 `members`가 list일 때만 본다.
  - 각 entry가 dict이고 `name`이 string, `role`이 non-empty string이면 `roles[name] = role`.
  - config 부재·파싱 실패·타입 불일치·예외는 모두 빈 dict로 흡수한다.
  - 수집된 각 member dict에 `role = roles.get(author)`를 추가한다. 없으면 Python `None`, JSON에서는 `null`.
- 텍스트 출력:
  - 첫 줄은 `=== tm-mode context ===`.
  - INDEX 섹션은 `--- INDEX ---` 뒤에 `index_text.rstrip()`를 넣는다. 빈 문자열이면 `(INDEX.md 없음)`.
  - 멤버 섹션 제목은 `--- members (멤버별 최근 작업일 1파일 summary) ---`.
  - 멤버가 없으면 `(세션로그 없음 — summary 수집 대상 0)`.
  - 멤버가 있으면 summary가 non-empty일 때 `- <who> [<date>] summary: <summary>`, 다음 줄에 `    file: <file>`.
  - summary가 빈 문자열이면 `(summary 없음 — 구로그)`를 출력한다.
  - role이 있으면 `<author>(<role>)`, 없으면 `<author>`다. 텍스트 출력의 role만 `_sanitize_line()`을 거쳐 DEL 또는 U+0000~U+001F 제어문자를 공백으로 바꾼다. JSON 출력의 role은 원값을 `json.dumps`가 escape한다.
- JSON 출력:
  - `--json`이면 `json.dumps({"state": ..., "index": ..., "members": ...}, ensure_ascii=False)`를 한 줄로 stdout에 쓴다.
  - 멤버 객체는 현재 구현상 `author`, `date`, `summary`, `file`, `role` 키를 가진다.
- 성공 exit 0. 읽기 실패는 가능한 한 빈 값으로 축소하며 exit code를 바꾸지 않는다.

### 3.4 pull / commit / update (git 동기화 — `--root` 필수)

세 동사는 `infra/git_ops.py`를 공통 safety layer로 쓴다.

공통 git 안전장치:

- 기본 timeout은 `DEFAULT_TIMEOUT = 2`초다(pull/fetch 2초 초과 시 비치명 실패, 로컬 commit/checkout 도 충분).
- `git_env()`는 현재 환경을 복사한 뒤 `GIT_TERMINAL_PROMPT=0`을 강제하고, `GIT_SSH_COMMAND`가 없으면 `ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new -oConnectTimeout=5`를 넣으며, `GIT_ASKPASS`가 없으면 `true`를 넣는다. HTTPS/SSH credential prompt로 멈추지 않게 하는 목적이다.
- 네트워크성 git 호출에는 `-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=<timeout>`을 붙인다. 적용 대상은 pull, fetch, push다. merge에는 붙지 않는다.
- `run_git(args, timeout)`은 `git <args>`를 stdout/stderr pipe, stdin DEVNULL, text mode로 실행한다. POSIX에서 `start_new_session=True`로 새 프로세스 그룹을 만들고, `subprocess.TimeoutExpired`가 나면 `kill_group()`으로 프로세스 그룹 전체를 SIGKILL한다. 실패해도 kill 예외는 흡수한다.
- `is_git_worktree(team_root)`는 `git -C <team_root> rev-parse --is-inside-work-tree`가 rc 0이고 stdout이 `true`일 때만 true다. 예외는 false.
- 외부 함수들은 예외를 전파하지 않는 것을 목표로 한다. 실패는 dataclass 결과의 `ok=False`와 `detail` 문자열로 표현한다.

#### 3.4.1 pull

CLI:

```
python infra/teammode.py pull --root <팀루트>
```

- 실행 전 git worktree가 아니면 `PullResult(ok=False, detail="not a git work tree")`.
- 실제 명령은 `git -C <team_root> -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 pull --ff-only --no-rebase --no-edit`.
- timeout이면 detail은 `timeout`.
- 실행 예외면 detail은 `exec error: <exc>`.
- rc 0이면 `ok=True`, detail은 stdout strip 앞 200자다. stdout이 비면 engine 출력에서 `up-to-date`로 대체한다.
- rc non-zero면 `ok=False`, detail은 stderr 또는 stdout strip 앞 200자다.
- engine 성공 출력: `tm-mode pull — 최신화됨: <detail-or-up-to-date>` stdout, exit 0.
- engine 실패 출력: `tm-mode pull — 건너뜀(비치명): <detail>` stderr, exit 1.
- `--ff-only`라서 non-ff merge나 conflict를 자동 생성하지 않는다.

#### 3.4.2 commit

CLI:

```
python infra/teammode.py commit --root <팀루트> --message <메시지> [--push]
```

- `--message`가 없거나 값이 falsy이면 `[error] commit: --message <메시지> 가 필요합니다.`를 stderr에 쓰고 exit 2. 빈 문자열 메시지는 거부된다.
- engine CLI는 path 제한 옵션을 노출하지 않는다. 따라서 `git_ops.do_commit(..., paths=None)`가 호출되고 stage 범위는 전체 워킹트리 `git add -A`다.
- git worktree가 아니면 `CommitResult(ok=False, detail="not a git work tree")`.
- stage:
  - 명령은 `git -C <team_root> add -A`.
  - timeout detail은 `add timeout`.
  - 실행 예외 detail은 `add exec error: <exc>`.
  - rc non-zero detail은 `add failed: <stderr 앞 200자>`.
- 변경 확인:
  - `git -C <team_root> diff --cached --quiet`를 실행한다.
  - rc != 0이면 staged changes 있음으로 본다.
  - rc == 0 또는 예외면 변경 없음으로 본다.
  - 변경 없음이면 `CommitResult(ok=False, committed=False, detail="nothing to commit")`; 빈 커밋은 만들지 않는다.
- commit:
  - 명령은 `git -C <team_root> commit -m <message>`.
  - timeout detail은 `commit timeout`.
  - 실행 예외 detail은 `commit exec error: <exc>`.
  - rc non-zero detail은 `commit failed: <stderr-or-stdout 앞 200자>`.
  - 성공하고 `--push`가 없으면 `ok=True, committed=True, pushed=False`, detail은 commit stdout 앞 200자.
- push:
  - `--push`가 있을 때만 `git -C <team_root> -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 push`.
  - push 성공이면 `ok=True, committed=True, pushed=True, detail="committed and pushed"`.
  - push timeout·실행 예외·rc non-zero는 **로컬 커밋을 되돌리지 않는다**. 결과는 `ok=True, committed=True, pushed=False`, detail은 `committed; push timeout` / `committed; push exec error: ...` / `committed; push failed: ...`.
- engine 성공 출력:
  - push 성공: `tm-mode commit — 커밋됨 (pushed): committed and pushed`
  - push 요청했으나 push 실패: `tm-mode commit — 커밋됨 (push 실패·커밋은 보존): <detail>`
  - push 미요청: `tm-mode commit — 커밋됨: <detail>`
  - 모두 exit 0이다. push 실패는 exit 1이 아니다.
- engine 실패 출력: 변경 없음·git 아님·add/commit 실패 등은 `tm-mode commit — 건너뜀(비치명): <detail>` stderr, exit 1.

#### 3.4.3 update

CLI:

```
python infra/teammode.py update --root <팀루트>
```

`update`는 template upstream 적용 동사다. **merge 가 아니라 파일 동기화**로 동작한다. 상수는 `UPSTREAM_REMOTE = "upstream"`이고, 동기화 대상은 모듈 상수 `git_ops.SYNC_PATHS = ["infra"]`(엔진 경로)다. CLI로 remote/branch/paths를 바꿀 수 없다. 플래그는 `--dry-run`(미리보기) 하나를 받는다.

**왜 merge 가 아닌가**: 도입 레포는 GitHub *template* 으로 생성돼 upstream(`T-Gates/tm-mode`)과 공통 조상이 0인 **unrelated histories**다. 그래서 `git merge`/`pull --ff-only` 는 영원히 `fatal: refusing to merge unrelated histories` 로 막힌다. 따라서 merge 를 버리고 upstream 의 **엔진 경로만** `git checkout` 으로 working tree 에 덮어쓰는 파일 동기화로 구현한다. 히스토리 관계(공통 조상)와 무관하게 동작한다.

**동기화 대상·보호 대상**: `SYNC_PATHS`(`infra/`)만 덮어쓴다. ⚠️ `memory/`·`team.config.json`·`.git`·기타 팀 소유 파일은 **절대** 건드리지 않는다(checkout 의 pathspec 이 `infra/` 로 한정됨). 새 엔진 디렉토리를 추가할 땐 `SYNC_PATHS` 만 확장한다.

엔진 `cmd_update(team_root, dry_run)`는 `git_ops.sync_from_upstream(team_root, remote="upstream", dry_run=...)` 한 번에 위임한다. `sync_from_upstream` 의 단계:

- 1단계 fetch:
  - `git_ops.fetch_upstream(team_root, remote="upstream")`를 재사용한다(자격증명 차단·killpg·http 타임아웃 안전장치 공유).
  - git worktree가 아니면 `not a git work tree`로 `ok=False`.
  - `git remote` 목록에 `upstream`이 없으면 `no 'upstream' remote`로 `ok=False`.
  - 실제 fetch 명령은 `git -C <team_root> -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 fetch --quiet upstream`.
  - fetch 실패 시 `SyncResult(ok=False, detail="fetch 실패: <detail>")`. engine 은 stderr 에 `tm-mode update — 건너뜀(비치명): ...` + 수동 `git remote add upstream <UPSTREAM_URL>` 안내를 쓰고 exit 1.
- 2단계 기본 브랜치 감지:
  - `detect_default_branch(team_root, remote="upstream")` — 전부 로컬 ref만 본다(네트워크·hang 없음). `git remote show`는 쓰지 않는다.
  - 순서: ① `git symbolic-ref refs/remotes/upstream/HEAD` 의 끝 세그먼트 → ② `refs/remotes/upstream/main` 존재 시 `main` → ③ 폴백 `main`.
  - `ref = "upstream/<branch>"`. ref 가 실재하지 않으면 `ok=False, detail="upstream 브랜치를 찾을 수 없습니다: <ref>"`.
- 3단계 변경 유무(멱등):
  - `git diff --name-status <ref> -- infra` 가 비어 있으면 `ok=True, changed=False, detail="이미 최신"`. engine 은 `tm-mode update — 이미 최신입니다.` stdout, exit 0.
- 4단계 dirty 가드(필수):
  - `git status --porcelain -- infra` 가 비어 있지 않으면(대상 경로에 커밋 안 된 staged/unstaged/untracked 변경) **중단**한다: `ok=False, blocked=True`. 덮어쓰기로 유실되기 때문이다.
  - engine 은 stderr 에 "중단: ... 먼저 변경을 커밋하거나 되돌린 뒤 다시 실행하세요(사람 판단 필요)" + diff 를 쓰고 exit 1. tm-mode 원칙(막히면 추측 수리 금지).
  - status 조회가 실패/예외면 보수적으로 dirty 로 보고 중단한다.
- 5단계 dry-run:
  - `--dry-run` 이면 4단계까지만 하고 `git diff --name-status <ref> -- infra` 결과를 `SyncResult.diff` 로 채워 `ok=True, changed=False` 로 반환한다. **실제 변경 0.** engine 은 `tm-mode update [dry-run] — 동기화하면 바뀔 파일(infra):` + diff 를 출력하고 exit 0.
- 6단계 적용(checkout):
  - 실제 명령은 `git -C <team_root> checkout <ref> -- infra`. working tree 를 덮어쓰고 변경은 **staged** 된다.
  - timeout detail은 `checkout timeout`, 실행 예외는 `checkout exec error: ...`, rc non-zero는 `checkout 실패: <앞 200자>`.
  - 성공 시 `ok=True, changed=True, detail="동기화 완료(staged)"`. engine 은 `tm-mode update — 엔진 파일 동기화 완료(infra, staged). 바뀐 파일:` + diff + 사람이 직접 커밋하라는 안내를 stdout 에 쓰고 exit 0.
- **자동 commit·push 절대 안 함**: checkout 은 staged 상태까지만 만든다. engine 도 commit/push 를 하지 않는다 — 무엇이 바뀌었는지 사람이 검토 후 직접 커밋한다. update 는 conflict 해결·rebase·merge 를 하지 않으므로 unrelated histories 와 무관하다.

### 3.5 issue (서비스 슬롯 동사 — `--root` 필수, 첫 positional = 서브액션)

> **L1 프로토타입 — 폐기 예정.** 이 동사는 L1 시절의 echo 프로토타입이다. 정규 입력 스키마를 stdout JSON으로 **echo만** 하고 실제 이슈를 만들지 않는다. L2 재설계(A안, 2026-06-25)에서 이슈 생성 같은 동작은 **AI가 등록된 벤더 MCP 도구(`mcp__<alias>__create_issue` 등)를 직접 호출**하는 것으로 확정됐으므로, 이 엔진 동사는 동작 경로가 아니다. 현재 conformance 시나리오 03(`03-issue-create`)이 이 echo 계약을 검사하므로 0.2에서는 남겨 두며, 시나리오 정리와 함께 제거 예정이다. **이 동사를 동작 CLI로 키우지 말 것**(`--title` 등으로 실제 생성·전송하는 명령을 추가하는 것은 폐기한 추상화의 부활이다).

CLI:

```
python infra/teammode.py issue --root <팀루트> [<action>] [--title <t>] [--body <b>] [--assignee <a>] [--label <l>] [--priority <p>]
```

`issue`의 altitude는 `context`와 같다. 엔진은 issues 슬롯 연결 여부를 확인하고, 연결돼 있으면 정규 입력 스키마를 stdout JSON으로 echo한다. `providers/<provider>.json`의 `action_map` 해석, provider별 payload 변환, MCP 호출, 실제 이슈 생성은 하지 않는다.

- positional:
  - 첫 positional만 `action`이다. 예: `create`.
  - positional이 없으면 `action`은 `null`이다.
  - 두 번째 이후 positional은 현재 무시된다.
- 입력 필드:
  - 정규 필드 플래그는 `--title`, `--body`, `--assignee`, `--label`, `--priority`.
  - 값이 `None`이 아닌 필드만 `input` object에 들어간다. 값이 빈 문자열이면 `None`은 아니므로 들어갈 수 있다.
  - 같은 필드를 반복하면 마지막 값만 남는다.
  - 사용자 텍스트는 해석하지 않고 `json.dumps(..., ensure_ascii=False)`로만 직렬화한다. 셸 명령이나 다른 JSON 문자열에 보간하지 않는다.
- provider 확인(`_resolve_issue_provider`):
  - `<team_root>/team.config.json`이 파일이 아니면 미연결.
  - JSON root가 object가 아니면 미연결.
  - `services`가 object가 아니면 미연결.
  - `services.issues`가 object가 아니면 미연결.
  - `services.issues.provider`가 non-empty string이 아니면 미연결.
  - `_providers.lookup(provider)`가 `None`이 아니어야 연결로 인정한다. lookup은 기본적으로 레포 루트 `providers/<provider>.json` 파일을 찾고 provider pack validation까지 통과해야 한다. provider 파일 부재·검증 실패·예외는 모두 미연결로 흡수된다.
  - 엔진은 provider 이름을 추측하거나 fallback provider를 고르지 않는다.
- 빈 슬롯:
  - config 부재, 슬롯 부재, provider 미지, provider pack invalid 등은 모두 빈 슬롯이다.
  - stdout에 `[info] issues 슬롯이 연결돼 있지 않습니다. team.config.json 의 services.issues 를 연결하세요(tm-connect).`를 출력하고 exit 0.
  - 빈 슬롯에서는 schema JSON을 echo하지 않는다.
- 연결 슬롯:
  - stdout에 한 줄 JSON을 출력하고 exit 0.
  - shape는 정확히 `{"verb":"issue","action":<action|null>,"service":"issues","provider":<provider>,"input":{...}}`.
  - `input`에는 값이 주어진 정규 필드만 들어간다.
  - `provider`는 config의 provider 문자열이며, providers lookup을 통과한 이름이다.

---

## §6. 호환 선언 (Conformance)

tm-mode는 reference 구현과 별개로 누구든 같은 표준을 따르는 **독립 구현**을 만들 수 있다. 본 절은 독립 구현이 **"tm-mode compatible"** 을 선언하는 조건·절차를 정의한다.

호환의 약속: **호환 구현끼리는 같은 팀 레포를 공유할 수 있다.** 한 팀에서 멤버 A가 reference를, B가 독립 구현을 써도 팀 메모리가 깨지지 않는다.

### 6.1 호환 조건 (셋 다 필수)

- **C1 — 팀 메모리 표준 준수(§1)**: 세션로그 포맷(위치·하루 1파일·06시 컷·frontmatter author/date/summary)·코어 디렉토리·INDEX 갱신·주입 스케일(0.2 자가 점검 + 골든 시나리오 "컨텍스트 조회"로 확인). 양방향: 생산한 파일이 포맷 준수 + 표준 팀 레포를 읽고 정상 동작.
- **C2 — 훅·어댑터 표준의 의미 보존(§2)**: 정규 이벤트 4종 의미(특히 PreToolUse 차단)·정규 입력 스키마로 공통 스크립트 호출·폴백(무음 스킵 금지)·정규형 규약(에이전트 고유 표기 금지, MCP 시맨틱 참조). **주의: 독립 구현이 `agents/` 구조나 Python을 그대로 쓸 필요 없다** — 보존 대상은 선언 포맷(manifest.json·events.json)과 의미이지 언어·배치가 아니다.
- **C3 — conformance kit 통과(§6.4)**: 필수 검사 통과 + 결과 로그를 등재 신청에 첨부.

**범위 한정**: 호환 선언은 특정 spec_version에 대한 선언("tm-mode 호환 (spec 0.2)"). 버전 없는 선언은 무효. 부분 구현은 "partial" 표기로 등재 가능(예: memory-only는 K1~K2·K8 + 골든 중 컨텍스트 조회·세션로그 작성). 부분집합 적정성은 maintainer 리뷰 승인.

### 6.2 reference 검수 도구 — check.py 3모드

reference는 단일 도구 `conformance/check.py`로 세 모드를 제공한다(03의 conformance kit 구상의 실물):

| 모드 | 성격 | 내용 |
|---|---|---|
| `lint` | 정적(엔진 실행 없음) | 현 reference는 3개 정적 검사만 실행한다: `_lint_manifest_canonical`(K4 일부: manifest에 `mcp__`/`Write\|Edit`/`apply_patch` 없는지), `lint_no_tracked_secrets`(config/credentials 데이터 파일의 토큰키 차단), `lint_skill_canonical`(K7: 스킬 본문 `mcp__`·provider 제품명 직표기 차단). events/actions 완전성·duplicate `(event, script)`·match 단일 키는 검사하지 않는다. |
| `verify` | 동적 | 골든 시나리오를 **우리 툴킷**에 실행(독푸딩). `--engine` 필요 |
| `conform` | 동적 + Tier | 같은 시나리오를 **임의 구현**에 실행 + advisory 순응률로 Tier 산출 |

- `verify`/`conform`은 같은 골든 시나리오 정의(`conformance/scenarios/*.json`)를 공유 — **시나리오 = 실행 가능한 스펙**. 빈 엔진(no-op)에 돌리면 전부 RED = 엔진의 인수 테스트.
- **하니스 인터페이스(C2 정신, 언어·배치 비강제)**: 엔진은 `engine.run(argv) → Result(exit_code, stdout, stderr)`를 만족하고 root 아래 파일 부작용을 내면 된다. reference `SubprocessEngine`은 임의 `--engine` prefix(예: `python3 infra/teammode.py`)를 받아 어떤 언어 구현도 검사 가능. 팀 루트는 동사 뒤 `--root`로 명시 주입하고 env 화이트리스트로 ambient 팀루트 변수 누수 차단(P1 이중 방어). reference 엔진은 settings 명시(P2)가 필요하므로 CLI가 root 하위 격리 settings를 주입(`--settings`를 모르는 타 구현은 미지 플래그로 무시).

### 6.3 Tier 산출

- **결정적(deterministic) 시나리오가 전부 통과해야 호환.** advisory 순응률로 Tier 등급: Tier 1 = advisory 100% / Tier 2 = advisory 부분 / Tier 3 = advisory 0. 결정적 실패가 하나라도 있으면 `compliant=False`(Tier 미산정).
- reference 시나리오 `tier_signal`: deterministic(01·02·03·05) / advisory(04).

### 6.4 검사 항목 (K1~K8, reference 상태)

| # | 검사 | 대응 | reference 상태 |
|---|---|---|---|
| K1 | 생산한 세션로그 누적(하루 1파일 + 내용 포함) | §1.3·§1.5 | 시나리오 04 + assertion(`session_log_single_file/contains`). frontmatter 3필드 존재나 파일명=date 일치는 현재 check.py가 검사하지 않는다. |
| K2 | 06시 컷 경계값(05:59→전날 / 06:00→당일) | §1.4 | `test_workday`(단위) — kit 자동검사 로드맵 |
| K3 | events.json 완전성(모든 정규 이벤트·행위 키 존재) | §2.6 | 미구현. 현재 lint는 검사하지 않는다. |
| K4 | manifest 정규형(에이전트 고유 표기 grep) | §2.2 | `lint` 구현됨 |
| K5 | normalize golden test(원어 → 정규 스키마 일치) | §2.10 | `test_normalize`(단위) — kit 로드맵 |
| K6 | 폴백 동작(미지원 이벤트 시 `[warn]`, 무음 스킵 부재) | §2.9 | `test_adapter_codex`(단위) — kit 로드맵 |
| K7 | 스킬 본문 정규형(`mcp__`·제품명 직표기 부재) | §2.12·§7.3 | `lint_skill_canonical`로 lint 구현됨 |
| K8 | 코어 디렉토리 구조 + 신규 폴더 INDEX 등재 | §1.1 | lint 로드맵 |

추가로 **골든 시나리오 5종**(켜기 → 컨텍스트 조회 → 이슈 생성 → 세션로그 작성 → 끄기)을 실행한다. reference 시나리오 = `01-on-banner`·`02-context-injection`·`03-issue-create`·`04-log-accumulate`·`05-off-persist`. `03-issue-create`는 `issue` 동사(§3.5)로 GREEN — 시나리오가 연결 issues fixture를 자체 세팅(fs_write)·정리(fs_delete)해 공유 root에서 04/05를 오염시키지 않는다. 현재 `03-issue-create.json`의 fixture content에는 `"spec_version":"0.1"`이 남아 있지만, 구현의 `config_is_valid()`는 truthy 여부만 보며 reference 구현 버전은 `install_lib.SPEC_VERSION == "0.2"`다.

### 6.5 등재 절차 · 배지

1. **신청** — 본진 레포에 이슈(`implementation` 라벨): 구현 이름·레포·라이선스, 대상 에이전트/플랫폼·spec_version, kit 결과 로그(kit 공개 전: C1·C2 자가 점검표 + 골든 5종 기록).
2. **리뷰** — maintainer가 kit 결과 확인 + 양방향 상호운용 스팟 체크(독립 구현이 만든 세션로그를 reference가 읽기, 역방향). 사이드 프로젝트 케이던스상 수 주 소요 가능.
3. **등재** — README Implementations 표에 추가(구현·에이전트/플랫폼·spec_version·상태·검증일).
4. **상태 전이** — minor bump 시 등재 구현에 통지. `compatible → stale`: 통지된 minor의 다음 minor 발행 시점까지 재검증 미제출 시 stale(maintainer가 절대 기한 병기 가능, partial도 동일). `stale/partial → compatible`: 기존 이슈에 현행 spec_version 기준 결과 제출 → 표 갱신(신규 신청 불요). 철회는 본인 신청으로 언제든.

배지: `![tm-mode compatible](…/tm-mode-compatible%20(spec%200.2)-blue)`. **spec_version 반드시 포함**(버전 없는 선언 무효). `partial`/`stale`은 compatible 배지 불가(상태 배지는 가능). 명예 기반 — 허위 확인 시 제거·공지.

### 6.6 버저닝 연동

kit 검사 항목(K1~K8) 추가·변경은 minor bump. 0.x 호환 선언은 해당 minor에 대한 선언이며 1.0 동결 후 1.x 전체로 완화. 독립 구현 2개 이상 등재가 1.0 동결 + RFC-lite 도입 트리거.

---

## §7. 서비스 슬롯 · provider 팩

에이전트 축(§2)과 직교하는 두 번째 축: 같은 역할(이슈 트래커·채팅·…)을 팀마다 다른 제품으로.

> **L2 = MCP 등록기 (A안, 2026-06-25 확정).** 슬롯에는 팀이 고른 **공식 벤더 MCP를 *연결(등록)*** 한다. tm-mode는 연결만 하고, 이슈 생성·일정 추가 같은 **동작은 AI가 `mcp__<alias>__<벤더도구>`를 직접 호출**한다. tm-mode가 역할을 도구 중립 함수 계약(`issues_create` 등)으로 한 겹 감싸거나 `role_server` 프록시로 중계하지 않는다. provider 팩은 슬롯에 어떤 MCP를 어떻게 등록할지(`mcp.register_hint`)와 발급 안내(`token_guide`)를 담는 **등록용 메타데이터**다 — 동작 변환표가 아니다. 진실 소스: `docs/archive/2026-06-25-L2-redesign.md`.

현재 구현의 ground truth는 `providers/*.json`, `infra/providers.py`, `infra/install_lib.py::services_are_valid`, `infra/credentials.py`다. 이 영역에는 독립 CLI가 없다. 실패는 CLI exit code가 아니라 `ProviderValidationError`/`ValueError`/`OSError` 예외 또는 boolean/`None` 반환으로 표현된다.

### 7.1 역할 슬롯 선언 + scope (필수)

`team.config.json`의 `services`는 **역할 슬롯 → 등록할 provider(공식 벤더 MCP)** 선언이다. 역할명은 제품 중립 어휘로 고정한다: `issues`·`chat`·`docs`·`calendar`(슬롯 *이름*만 중립일 뿐, 슬롯 *내용물*은 그 provider의 실제 벤더 MCP다 — tm-mode가 도구 중립 함수로 추상화하지 않는다). 구현상 `services`는 `None` 또는 `{}`이면 전부 빈 슬롯으로 유효하다. 부분 채움도 유효하다.

채운 슬롯의 shape는 object `{ "provider": <정규 provider>, "scope": <team|personal optional>, <resource_fields...> }`다. `scope`는 슬롯에 있으면 반드시 `team` 또는 `personal`이어야 한다. 현재 `services_are_valid()`는 `scope` 누락을 invalid로 만들지 않는다. 누락 시 연결 스킬/소비자가 provider pack의 `default_scope`를 기본값으로 사용할 수 있게 남겨둔 상태다.

```jsonc
"services": {
  "issues":   { "provider": "linear", "scope": "personal" },
  "chat":     { "provider": "slack",  "scope": "team", "channel_id": "C0123..." },
  "docs":     { "provider": "notion", "scope": "team", "database_id": "..." },
  "calendar": { "provider": "google", "scope": "personal", "calendar_id": "primary" }
}
```

검증 함수는 `infra/install_lib.py::services_are_valid(services, *, providers_dir=None) -> bool`이다. 단 "bool 반환"은 provider 파일이 없거나 일반 shape 검증에서 탈락하는 경우의 계약이다. provider 파일이 존재하지만 JSON 파싱이나 provider pack schema 검증에 실패하면 `providers.lookup()`의 `ProviderValidationError`가 전파되고 `False`로 흡수되지 않는다.

- `services is None` → `True`.
- `services == {}` → `True`.
- `services`가 dict가 아니면 `False`.
- 각 role key는 `issues`, `chat`, `docs`, `calendar` 중 하나여야 한다. `tickets` 같은 오타는 `False`.
- 각 slot은 dict여야 한다.
- `slot.provider`는 non-empty string이어야 한다.
- `providers.lookup(provider, providers_dir=providers_dir)`가 `None`이면 `False`. provider 이름을 추측하거나 fallback provider를 고르지 않는다. 파일이 존재하는 provider의 JSON/schema 오류는 `ProviderValidationError`로 전파된다.
- `slot.scope`가 존재하면 `team|personal` 중 하나여야 한다. 없으면 이 함수에서는 허용한다.
- provider pack의 `resource_fields`에 든 모든 필드는 slot에 non-empty string으로 있어야 한다. `None`, 누락, 공백 문자열은 `False`.
- provider pack이 요구하지 않는 추가 키는 허용한다. 후속 확장을 막지 않기 위해 unknown slot key를 거부하지 않는다.
- 이 검증은 role 판정의 파괴적 분기에 쓰이지 않는다. `config_is_valid()`/`detect_role()`은 services 스키마나 provider pack 존재 여부로 기존 멤버 config를 도입자 config로 강등하지 않는다.

현재 provider pack 기준 resource field는 `linear=[]`, `slack=["channel_id"]`, `notion=["database_id"]`, `google=["calendar_id"]`다.

### 7.2 빈 슬롯 = 1급 시민 (필수)

역할 슬롯 미연결은 **에러가 아니라 선언된 상태**다. `services`가 없거나 `services: {}`이거나 특정 role key가 없으면 그 role은 빈 슬롯이다. install.py 도입자 경로는 `services: {}`로 시작한다.

- adapter가 `team.config.json`을 읽어 `services`가 dict임을 확인한 경우에만 빈 슬롯 우선 규칙을 적용한다. config 파일 부재·깨진 JSON·`services` 누락·`services` 비object는 adapter에서 `None`으로 취급되어 L1 동작 보존 경로가 된다(§2.8/§2.9).
- 빈 슬롯 provider를 참조하는 MCP 훅 매처는 `fallback` 무관 등록 생략 + `[info]`다. provider pack을 못 찾으면 adapter는 추측하지 않고 fallback으로 `services` 값 중 provider가 같은 슬롯이 있는지만 보는 제한적 판정을 한다.
- `install-mcp`는 연결된 provider만 정규 서버명 alias로 등록한다. 연결 provider가 없으면 기존 teammode MCP 블록 제거 또는 no-op 후 `[info] 연결된 MCP provider 없음 (빈 슬롯)`을 반환한다.
- `issue` 엔진 동사는 `services.issues.provider`가 non-empty string이고 `providers.lookup()`을 통과할 때만 연결로 본다. config 부재, 슬롯 부재, provider 미지, provider pack invalid는 모두 빈 슬롯으로 흡수된다.
- 구현은 빈 슬롯을 이유로 설치·세션 시작을 실패시켜서는 안 된다.

### 7.3 스킬·훅에서의 서비스 참조

- **스킬 본문**: 역할 어휘(`issues`, `chat`, `docs`, `calendar`)를 기준으로 말한다. 실제 제품명·발급 링크·발급 단계는 provider pack 데이터에서 읽는다.
- **훅 매처**: 0.2 구현은 `providers/<name>.json.action_map`을 컴파일 소비하지 않는다. MCP 매처(manifest)는 §2.5의 정규 서버명 방식이며, 정규 서버명은 provider 이름과 항등이다. 단 **등록 별칭**은 정규 서버명이 아니라 `resolve_server_alias(provider)`=`tm-<provider>`다(§2.8). 런타임 도구명은 이 별칭으로 잡히고, normalize가 정규 서버명으로 역환원해 manifest 매처와 맞춘다(§6.1).
- `action_map`은 현재 예약 필드다. provider pack에 있으면 object인지 shape만 검증하고 `ProviderPack.action_map`에 보존한다. 없으면 `None`이다. list, string 등 object가 아닌 값은 provider validation 실패다.

### 7.4 provider pack 스키마와 loader 계약

provider pack 파일은 기본적으로 레포 루트 `providers/<name>.json`에 둔다. `infra/providers.py`의 `DEFAULT_PROVIDERS_DIR`는 `infra/`의 부모인 레포 루트 아래 `providers/`다. `<name>`은 정규 서버명이며 `provider` 필드와 정확히 같아야 한다. 0.2에는 별도 `canonical_server` 필드가 없다.

필수 키:

| 키 | 구현된 검증 |
| --- | --- |
| `provider` | non-empty string. `load_pack()` 호출 시 파일 stem과 정확히 같아야 한다. |
| `token_guide` | object. |
| `token_guide.url` | non-empty string. |
| `token_guide.steps` | list. 원소 타입은 현재 검증하지 않는다. |
| `default_scope` | `team` 또는 `personal`. |
| `auth` | `api_key`, `oauth`, `bot_token` 중 하나. |
| `services` | non-empty list이며 모든 원소가 non-empty string. |
| `resource_fields` | list이며 모든 원소가 non-empty string. 빈 list 허용. |
| `mcp` | object. |
| `mcp.register_hint` | non-empty string. |

선택 키:

| 키 | 구현된 검증 |
| --- | --- |
| `action_map` | 있으면 object여야 한다. 0.2에서는 예약 필드로 보존만 한다. |

unknown top-level key는 모두 reject한다. 예를 들어 `resorce_fields` 같은 오타는 `ProviderValidationError`다.

`ProviderPack` dataclass 필드는 `provider`, `token_guide`, `default_scope`, `auth`, `services`, `resource_fields`, `mcp`, `action_map=None`, `raw=None`다. `canonical_server` property는 항상 `provider`를 반환한다. `services`와 `resource_fields`는 새 list로 복사해 저장하고, `token_guide`, `mcp`, `raw`는 로드한 dict 객체를 보존한다.

함수 계약:

- `validate_pack(data, *, expected_name=None) -> ProviderPack`
  - `data`가 dict가 아니면 `ProviderValidationError("provider 팩은 object 여야 합니다.")`.
  - 필수 키 누락은 sorted list를 포함한 `ProviderValidationError("필수 키 누락: ...")`.
  - unknown key는 sorted list를 포함한 `ProviderValidationError("알 수 없는 키(오타 의심): ...")`.
  - `expected_name`이 `None`이 아니면 `data["provider"] == expected_name`을 강제한다. 위반 메시지는 "항등 불변식 위반"을 포함한다.
  - 성공 시 `ProviderPack`을 반환한다. 파일이나 디스크는 읽지 않는다.
- `load_pack(path) -> ProviderPack`
  - `path`가 파일이 아니면 `ProviderValidationError("provider 팩 파일이 없습니다: <path>")`.
  - UTF-8 text로 읽고 JSON parse한다.
  - JSON parse 실패는 `ProviderValidationError("provider 팩 JSON 파싱 실패(<path>): <parser error>")`.
  - 파일 stem을 `expected_name`으로 넣어 `validate_pack()`을 호출하므로 `providers/slack.json` 안 `provider: "notion"`은 reject된다.
- `load_all(providers_dir=None) -> dict`
  - `providers_dir`가 `None`이면 `DEFAULT_PROVIDERS_DIR`.
  - 디렉토리가 없으면 `{}`를 반환한다. provider pack 부재는 빈 슬롯과 양립하는 정상 상태다.
  - `*.json` 파일을 sorted glob 순서로 `load_pack()`한다.
  - validation 예외는 잡지 않고 호출자에게 전파한다.
  - 반환은 `{pack.provider: pack}` dict다.
- `lookup(provider: str, providers_dir=None) -> ProviderPack | None`
  - `<providers_dir>/<provider>.json` 파일이 없으면 `None`.
  - 파일이 있으면 `load_pack()` 결과를 반환한다.
  - provider 문자열 자체의 whitelist 검증은 하지 않는다. 존재하는 파일의 JSON/스키마 오류는 `ProviderValidationError`로 전파된다.

이 모듈에는 CLI가 없으므로 별도 exit code도 없다.

### 7.5 현재 provider 팩 4종

현재 레포에는 `providers/google.json`, `providers/linear.json`, `providers/notion.json`, `providers/slack.json`만 있다. `providers.load_all()` 기준 provider set은 `{ "google", "linear", "notion", "slack" }`다.

| provider | 역할(`services`) | `default_scope` | `auth` | `resource_fields` | `mcp.register_hint` |
| --- | --- | --- | --- | --- | --- |
| `linear` | `["issues"]` | `personal` | `api_key` | `[]` | Linear 공식 MCP 서버를 정규 서버명 `linear`로 등록(개인 API 키 사용). |
| `slack` | `["chat"]` | `team` | `bot_token` | `["channel_id"]` | Slack MCP 서버를 정규 서버명 `slack`로 등록(봇 토큰 사용, 도입자 1회). |
| `notion` | `["docs"]` | `team` | `api_key` | `["database_id"]` | Notion MCP 서버를 정규 서버명 `notion`로 등록(integration 토큰 사용, 도입자 1회). |
| `google` | `["calendar"]` | `personal` | `oauth` | `["calendar_id"]` | Google Calendar MCP 서버를 정규 서버명 `google`로 등록(localhost OAuth/PKCE). |

발급 안내 데이터는 모두 provider pack의 `token_guide`에 들어 있다. 연결 스킬은 이 값을 하드코딩하지 않고 그대로 읽어 안내해야 한다.

| provider | `token_guide.url` | `token_guide.steps` |
| --- | --- | --- |
| `linear` | `https://linear.app/settings/api` | Linear 설정 → Security & access → Personal API keys 이동; `Create key` 클릭 후 라벨 입력; 생성된 키를 복사해 각자 1회 붙여넣기. |
| `slack` | `https://api.slack.com/apps` | Create New App → From scratch; OAuth & Permissions에서 봇 스코프 부여 후 워크스페이스 설치; Bot User OAuth Token(`xoxb-…`) 복사 후 팀 config에 채널 지정. |
| `notion` | `https://www.notion.so/my-integrations` | New integration 생성; Internal Integration Token(`secret_…`) 복사; 대상 페이지/DB의 Connections에 integration 공유 후 DB를 팀 config에 지정. |
| `google` | `https://console.cloud.google.com/apis/credentials` | OAuth client ID 생성; localhost redirect(PKCE)로 동의 화면 Allow; 연결 후 캘린더 ID 자동조회 후 사용할 캘린더를 config에 지정. |

각 provider pack의 `action_map`은 현재 `{}`다. `{}`라는 사실은 예약 필드가 비어 있음을 뜻할 뿐, issue/chat/docs/calendar action 변환이 구현됐다는 뜻이 아니다.

### 7.6 토큰 금고 위치·스코프·보안 불변식

`infra/credentials.py`는 0.2 평문 JSON 금고다. OS keychain, 암호화, 팀 자동 공유, remote sync, publish/fetch/share 동사는 없다. 팀 scope 토큰도 0.2에서는 각 멤버가 자기 로컬 금고에 직접 입력한다. `team`과 `personal`은 전송 정책이 아니라 같은 파일 안의 namespace다.

저장 위치:

- `credentials_dir() -> Path`는 `$XDG_DATA_HOME/teammode/credentials`를 반환한다.
- `XDG_DATA_HOME`이 없으면 `~/.local/share/teammode/credentials`를 쓴다.
- 함수 자체는 디렉토리를 만들지 않는다. 쓰기 시 `_secure_dir()`가 만든다.
- 금고 파일은 단일 `<credentials_dir>/default.json`이다(멀티팀 미지원, 2026-06-21). `_vault_path(team=None)`는 `team` 인자와 무관하게 이 파일을 반환한다 — 팀명에 묶이지 않아 `team.name`을 바꿔도 금고 키가 안 변한다. `migrate_legacy_vault(old_team)`는 단일파일 전환 전 팀명-키 금고(`<old_team>.json`)를 `default.json`으로 1회 이전하며, `default.json`이 이미 있으면 no-op(멱등)이다. 멀티팀이 필요해지면 그때 `team`별 파일명으로 되살린다.
- 디렉토리는 만들거나 보정할 때 `0700`을 시도한다. `chmod` 실패는 비차단이다.
- 파일은 저장 후 `0600`을 재단언한다. 기존 파일 mode가 넓어져 있어도 다음 `store()`가 `0600`으로 되돌린다.
- 평문 JSON이므로 동기화 폴더(Syncthing/Dropbox 등)에 두면 안 된다. `store()`는 흔한 동기화 폴더 경로 패턴(`dropbox`·`onedrive`·`mobile documents`·`icloud`·`/sync/` 등)을 휴리스틱으로 감지해 **경고**한다(SEC-4). 거부는 하지 않는다 — Syncthing 은 임의 경로라 완전 감지가 불가하고 오탐 차단은 작업을 막으므로, 경고로 방어선을 둔다. git 추적 여부는 검사하지 않으며 `.gitignore`가 별도 방어선이다.

식별자와 scope:

- public scope 상수는 `SCOPE_TEAM = "team"`, `SCOPE_PERSONAL = "personal"`이다.
- 허용 scope는 정확히 `team`, `personal` 두 개다. 다른 값은 `ValueError("invalid scope (allowed: team, personal)")`.
- `key` 식별자는 정규식 `^[A-Za-z0-9_.\-]+$`를 통과해야 한다. `team`은 더 이상 경로 구성요소가 아니라(단일 금고) 일반 CRUD 경로에서 검증·거부되지 않는다 — `migrate_legacy_vault(old_team)`의 `old_team` 인자만 같은 규칙으로 검증한다(레거시 파일명 안전).
- `/`, 공백, 빈 문자열, NUL, `;`, 기타 문자는 거부된다.
- `"."`, `".."`, `"..."`처럼 전체가 dot으로만 된 식별자는 정규식을 통과해도 별도로 거부된다.
- 식별자 오류 메시지는 입력값 자체를 echo하지 않는다. 메시지는 `invalid <team|key> identifier (allowed: [A-Za-z0-9_.-])` 형태다.

마스킹 불변식:

- 토큰 평문은 stdout, stderr, 로그, 예외 메시지, 반환값에 출력하지 않는다.
- `store()`의 반환값은 금고 파일 `Path`이며 토큰이 아니다.
- `list_keys()`는 key 이름만 반환하고 value는 반환하지 않는다.
- 파손 JSON, symlink read, IO 오류를 읽기 경로에서 만났을 때 파일 내용이나 토큰을 예외로 노출하지 않고 빈 금고처럼 취급한다.

심링크/파일 처리:

- 읽기는 `os.open(path, O_RDONLY | O_NOFOLLOW)`를 사용한다. 파일 부재, symlink, OS 오류, UTF-8 decode 실패, JSON parse 실패, top-level non-object는 모두 `{}`로 처리한다.
- 쓰기는 parent를 준비한 뒤 `os.open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0o600)`를 사용한다. symlink 등으로 안전 open에 실패하면 `OSError("vault path is not a regular file (refusing to write)")`를 raise한다.
- 저장 JSON은 `json.dumps(data, ensure_ascii=False, indent=2)` 결과다.
- 현재 쓰기는 temp file rename 방식의 원자적 교체가 아니라 대상 파일을 직접 truncate/write한다. 따라서 "0600 평문 금고"는 구현되어 있지만 crash-safe atomic write는 구현되어 있지 않다.

### 7.7 credentials 함수 계약

이 모듈에도 CLI가 없으므로 exit code가 없다. public 함수의 계약은 다음과 같다.

- `credentials_dir() -> Path`
  - 환경변수 `XDG_DATA_HOME`을 읽어 금고 디렉토리 경로를 계산한다.
  - 부작용 없음. 디렉토리 생성 없음.
- `store(team: str, scope: str, key: str, token: str) -> Path`
  - `scope`와 `key`를 먼저 검증한다. `team`은 금고 path 계산 시 검증된다.
  - `token`이 string이 아니면 `ValueError("token must be a string")`.
  - 기존 금고를 읽는다. 부재·파손·읽기 오류는 빈 dict로 취급한다.
  - `data[scope]`가 없으면 `{}`를 만든다. 기존 `data[scope]`가 dict가 아니면 `{}`로 교체한다.
  - 같은 `(scope, key)`가 있으면 덮어쓴다. 멱등성은 "마지막 store 값이 load 값"이다.
  - 저장 성공 시 금고 파일 path를 반환한다.
  - symlink 등 쓰기 거부는 토큰 없는 `OSError`로 전파한다.
- `load(team: str, scope: str, key: str) -> Optional[str]`
  - `scope`와 `key`를 검증한다.
  - 금고 부재, 읽기 오류, 파손 JSON, section 부재, section 비object, key 부재는 모두 `None`.
  - 저장된 value가 string일 때만 그 문자열을 반환한다. string이 아니면 `None`.
  - 파일을 수정하지 않는다.
- `delete(team: str, scope: str, key: str) -> bool`
  - `scope`와 `key`를 검증한다.
  - section이 없거나 key가 없으면 `False`.
  - key가 있으면 삭제하고 파일을 다시 쓴 뒤 `True`.
  - 삭제 후 section이 비면 scope key 자체를 top-level JSON에서 제거한다.
  - 없는 값을 지우는 것은 예외가 아니라 no-op false다.
- `list_keys(team: str, scope: str) -> list`
  - `scope`를 검증한다. key 인자는 없다.
  - section이 없거나 section이 dict가 아니면 `[]`.
  - section key 이름을 정렬해 반환한다. 토큰 value는 반환하지 않는다.
- `file_mode(team: str) -> Optional[int]`
  - 금고 파일의 `stat.S_IMODE(os.stat(path).st_mode)`를 반환한다.
  - 파일 부재나 stat 오류는 `None`.
  - 테스트/검증 보조 함수이며 파일을 수정하지 않는다.

남은 구현 갭:

- 팀 scope 토큰 자동 배포/공유/동기화는 미구현이다. 팀 scope provider·인스턴스 값은 config에 커밋될 수 있지만 토큰은 각자 로컬 금고에 입력해야 한다.
- credentials 금고는 평문 JSON이다. OS keychain/암호화는 미구현이다.
- provider `action_map` 해석, provider별 payload 변환, 실제 MCP 호출은 이 섹션의 구현 범위에 없다.
- provider pack top-level unknown key를 거부하므로 새 top-level 필드를 추가하려면 `infra/providers.py`의 `_KNOWN_KEYS`를 먼저 확장해야 한다.

---

## 부록 A. 설계 ↔ 빌드 reconcile (코드가 진실 — 닫은 미결 + 잔여 갭)

### A.1 구현으로 닫힌 미결 (draft → closed)

| 원 미결(스펙) | 닫은 결정(코드 기준) | 근거 코드 |
|---|---|---|
| 04 §12-1 Python 하한(3.9? 3.10?) | **3.9** (`MIN_PYTHON`) | `install_lib.MIN_PYTHON` |
| 04 §12-2 install이 첫 세션로그 쓰나 | **안 씀**(디렉토리만). 첫 로그는 첫 작업 세션 훅 | `scaffold_memory`(로그 미생성) |
| 04 §12-3 `--yes`에서 이름 못 정하면 | **exit 3**(신원 추측 금지) | `bootstrap`(member_name None → 3) |
| 04 §3 role 판정 기준 | spec_version + team.name 비-placeholder = 팀원, 아니면 도입자 | `config_is_valid`·`detect_role` |
| 04 §5/§6 이름 충돌 | identity(git email 주석)로 동일인/타인 결정적 판정 → 타인은 exit 3 | `register_member`(ConflictError) |
| 04 §9/M6 env 변수명(TGATES_HOME vs TEAMMODE_HOME) | **`TEAMMODE_HOME`** 단일화(런타임 훅 코드 일치). 01 부록A의 `TGATES_HOME` 오기 폐기 | `install_lib.ENV_VAR`·훅 3종 |
| 04 §10 실호스트 쓰기 게이트 | `--yes`(실설치) 또는 `--settings`(격리) 없으면 wire 건너뜀 | `bootstrap`(wire gate)·`_dispatch` |
| 05 전체(설계 draft) | tm-onboard SKILL.md **실제 작성됨** | `infra/skills/base/tm-onboard/SKILL.md` |
| 05 Obsidian 등록 메커니즘 | `--register-obsidian` 단독 opt-in 액션·merge·비치명·나중 등록 | `register_obsidian`·`register_obsidian_vault` |
| 02 §5 `sync` 무플래그 | base 엔트리만(최초 off 간주) | 어댑터 `_wanted_entries` |
| L2 안전 훅 2종 | `auto-commit.py`·`confirm-action.py` 파일 실재 + manifest 등록 | `infra/hooks/manifest.json`·`infra/hooks/auto-commit.py`·`infra/hooks/confirm-action.py` |
| L2 MCP 배선 | Claude/Codex 어댑터 `install-mcp` + install wire 선행 호출 구현 | `infra/agents/claude/adapter.py`·`infra/agents/codex/adapter.py`·`infra/install_lib.py` |
| L2 스킬 설치 | Claude/Codex 어댑터 `install-skills` + install wire 후행 호출 구현 | `infra/agents/claude/adapter.py`·`infra/agents/codex/adapter.py`·`infra/install_lib.py` |
| L2 provider 팩 | Linear/Slack/Notion/Google provider JSON 4종 실재 | `providers/{linear,slack,notion,google}.json` |
| L2 `team.config.json services` 스키마 | 역할 슬롯(`issues/chat/docs/calendar`) → `{provider, scope?, <resource_fields...>}` object. 빈 슬롯 허용, provider pack 기반 검증 | `install_lib.services_are_valid`·`infra/providers.py` |
| L2 provider pack 스키마 | `provider`, `token_guide`, `default_scope`, `auth`, `services`, `resource_fields`, `mcp`, optional `action_map`으로 확정 | `infra/providers.py::validate_pack`·`providers/*.json` |
| L2 credentials 금고 | 로컬 평문 JSON 금고 store/load/delete/list/file_mode 구현 | `infra/credentials.py` |
| L2 연결 스킬 분리 | tm-onboard는 L2 제안만, 실제 연결은 tm-connect가 수행 | `infra/skills/base/tm-onboard/SKILL.md`·`infra/skills/core/tm-connect/SKILL.md` |
| 멤버 역할 필드 | `team.config.json` `members: [{name, role?}]`로 확정, install 시 자기 엔트리만 upsert | `install_lib.upsert_member_role`·`scaffold_memory` |
| 팀 personality 기본 출력 | 도입자 config 기본 `greeting`/`farewell`, 엔진 on/off 출력 구현 | `install_lib.write_introducer_config`·`teammode.cmd_on/off` |

### A.2 코드에만 있는 동작 (스펙 미기재 → 본문에 명문화)

- **엔진 동사 전체(§3)**: 02/01에 동사 계약이 없었음 — on/off/log/context/pull/commit/update를 §3에 신규 명문화. `--root` 필수·env 무신뢰·on/off의 settings 명시(P2)·06시 컷 log·context state/json·git 동사 비치명 축소.
- **`enforcement` manifest 필드(§2.2)**: 02 draft 미언급, reference 어댑터가 실사용(block 폴백 경고 강화) → 본문 확정.
- **on의 upstream fetch 자동 알림(§3.1)**: merge 금지·fetch만. 02/04 부분 언급을 §3.1로 통합.
- **install 디스패치 모드(§4.1·§2.1)**: `install.py --<agent> sync/uninstall` 보존 인터페이스.
- **conformance 03 fixture의 `spec_version` 문자열**: `03-issue-create.json`의 fixture content에는 `"spec_version":"0.1"`이 남아 있다. 이는 지원 버전 선언이 아니라 scenario용 유효 config fixture이며, reference 지원 버전 단일 소스는 `install_lib.SPEC_VERSION == "0.2"`다.

### A.3 잔여 갭 (코드가 스펙 목표에 아직 미달 — 비규범)

- **공통 훅의 에이전트 메모리 누출**: 설계 목표는 에이전트 고유 메모리를 `agents/<name>/` 아래로 격리하는 것이나, 현 `session-start.py`/`confirm-action.py`는 Claude 출력 스키마와 Codex 한계를 직접 알고 있다. `session-log-remind.py`는 2026-06-21 재설계에서 평문 stdout 출력으로 전환해 이 갭을 해소했다.
- ~~**bootstrap verify의 Claude 고정**: wire는 감지 에이전트별 어댑터를 호출하지만 verify는 감지 결과와 무관하게 `teammode.py on`을 실행하고, 엔진 `on`은 항상 Claude 어댑터만 로드한다.~~ → **닫힘**: verify가 `on` 호출을 제거하고 `context`만 쓰게 되어(`auto_update_on_start` 자동 커밋 부작용 회피, 이종 적대검수 B1) 어댑터 로드 자체가 사라졌다.
- **Codex 디스패치 게이트 불일치**: Codex 어댑터 설정 옵션은 `--config`지만 `install.py --<agent>` 디스패처 게이트는 `--settings`/`--install`만 인정한다. `--codex --config <path> sync`는 어댑터 도달 전 exit 2다.
- **주입 스케일 분기 미구현(§1.6)**: reference `session-start.py`는 항상 summary 라인 기반 주입, ~4인 전문/5인+ 분기 없음. 0.2 자동검사 대상 아님이라 비준수는 아니나 스펙 목표 대비 갭.
- ~~**conformance 시나리오 03(`issue create`)**: 엔진 미구현 동사 → exit 127(의도된 RED, 서비스 슬롯 L2).~~ → **닫힘(0.2)**: `issue` 동사 구현(§3.5), 03 GREEN.
- **lint 검사 범위**: reference `check.py lint`는 K4 일부(manifest 정규형 grep), 토큰키 데이터 파일 린트, K7(스킬 본문 정규형)을 실행한다. K3 events/actions 완전성, duplicate `(event, script)`, match 단일 키, K8 INDEX 등재는 미구현이다.
- **`--update` 플래그 미사용**: install.py CLI에 파싱되나(`Options.update`) bootstrap이 사용하지 않음(멱등 재실행이 사실상 update 역할). §4.1에 명시.
- **`--json` role 출력 미구현**: 05/04가 요구한 install의 구조화 role 출력(`--json`) 미구현 → tm-onboard가 `config_is_valid` 직접 확인으로 우회(§5.2).
- **Codex 실 훅 입력 스키마 미확인**: normalize가 Claude 유사 형태 가정(§2.11) — Codex 실환경 캡처 후 확정.
- **install.py uninstall의 MCP/skills 회수 누락**: install wire는 `install-mcp`·`install-skills`를 호출하지만 `cmd_uninstall()`은 Claude `Adapter.uninstall()`만 호출해 MCP 등록과 skills 설치 흔적을 제거하지 않는다.
- **`--settings` 의미 불일치**: bootstrap은 `--settings`를 격리 디렉토리로 해석하지만 uninstall은 같은 값을 settings 파일 경로로 그대로 사용한다.
- **normalize 경로 검증 부재**: `tool_input.file_path`는 절대경로화/경로탈출 검증 없이 `files`로 복사되고, `<script>`도 파일명·경로탈출 검증 없이 `HOOKS_DIR / script`로 실행된다.
- **팀 personality 커스텀 배너 입력**: 기본 `greeting`/`farewell` 생성과 on/off 출력은 구현됐지만, 온보딩에서 custom banner 내용을 입력받아 `memory/banner.txt`를 교체하는 UX는 별도 확장 여지다.
- **workday timezone 고정**: reference는 KST 상수(`workday.KST`), `team.config.json team.timezone` 주입 미연결(확장 여지).
- **Windows 네이티브(env setx·훅 인터프리터·POSIX 감사)**: 구현 완료(§4.8·§2 — `is_windows` 분기, setx/reg env, `sys.executable` 훅 명령, POSIX 가정 제거). **단 reference 빌드는 Linux 에서 작성돼 Windows 분기는 setx/reg subprocess 모킹(runner 주입) + 플랫폼 주입으로만 검증**됨 — 실 Windows 에서의 레지스트리 영속·새 세션 env 반영·경로 해석은 native 환경 실측 권장(코드는 갭이 아니라 검증 환경의 한계).

---

## 부록 B. 미결 (open)

- [ ] 도입자 commit·push 안내 자동화 범위(install.py 경계).
- [x] Codex 훅 입력 JSON 실스키마 확인(2026-06-21 실환경 캡처: apply_patch=`tool_input.command`) + PreToolUse 차단 시맨틱 Codex 표현 — events.json `PreToolUse: "PreToolUse"`로 확정. (Hermes 표현 가능성은 별도 이월.)
- [ ] Hermes 이벤트 매핑 실조사(pre_llm_call≈UserPromptSubmit, on_session_start≈SessionStart — 재확인).
- [ ] normalize의 manifest 조회 비용(매 발동 파일 읽기 — 캐시 필요성).
- [ ] 동일 provider 다중 인스턴스·역할 중복 provider의 정규 서버명 표현(정규 서버명=provider 식별자 결정의 잔여 한계).
- [ ] install role의 `--json` 출력 스키마(§5.2 우회 해소).

---

## 부록 C. 버전 이력 · 01~05 대비 변경점

- **2026-06-16** — 구현 재정합 — 코드 ground truth를 §2~§5,§7에 상세 반영(codex dev-cycle)
- **0.2** — **engine 동사 계약 변경(minor bump, §0.4)**: 8번째 엔진 동사 `issue` 추가(§3.5). `issues` 슬롯 provider 확인 후 정규 입력 스키마를 stdout JSON으로 echo까지만(action_map 해석·페이로드 변환 금지 — 어댑터/스킬 몫). 값 화이트리스트에 `--title --body --assignee --label --priority` 추가, positional 서브액션 파싱 명문화(§3). conformance 03 닫힘(RED→GREEN). 하니스 `fs_delete` 액션(시나리오 자체 teardown) 추가.
  - **L2(서비스 연결) 빌드**: provider 팩(`providers/{linear,slack,notion,google}.json` — token_guide·auth·default_scope·resource_fields, §7) + config `services` 확장 object 스키마(§7.1) + 어댑터 `install-mcp`(§2.8, 빈슬롯 sync 교정 §2.9) + 어댑터 `install-skills`(§2.12) + `install.py` wire 다동사 통합(§4) + credentials 금고(`infra/credentials.py`, 각자입력 0600, §5.4·§7.5) + 안전 훅 2개(auto-commit·confirm-action) + `tm-connect` 스킬(§5.4) + K7 스킬 lint(§2.12).
  - **config.members 멤버 역할(§1.1, L2-A2)**: `members:[{name, role?}]`, 각자 upsert(타인 무접촉). `context` 동사 출력에 `role` 필드 추가(§3.3 — additive, 미등재/생략 시 null·하위호환). members 블록은 role 판정(`config_is_valid`)과 분리. role 개행/제어문자 거부(어휘는 자유).
- **0.1 (이전 단일판)** — 흩어진 spec 01(정식)·02(정식)·03(정식)·04(0.1-draft)·05(0.1-draft)를 단일 권위 문서로 통합. spec_version·용어·표기 규약 단일화. 04/05 draft를 빌드 기준 closed로 승격(부록 A.1). 코드에만 있던 엔진 동사(§3)·`enforcement` 필드·install 디스패치 모드를 명문화. 잔여 갭을 부록 A.3에 명시.

**01~05 → SPEC 대비 주요 변경점:**
- 04/05의 `0.1-draft` 상태 해소 후, 현재 문서는 issue 동사와 L2 서비스 연결 구현을 포함한 전 영역 단일 `0.2`.
- 04 §9의 `TGATES_HOME` vs `TEAMMODE_HOME` 자기모순 해소 → `TEAMMODE_HOME`으로 단일화(01 부록A 오기 폐기).
- 엔진 동사 챕터(§3) 신설 — 01~05 어디에도 없던 on/off/log/context/pull/commit/update 계약.
- 02의 `enforcement` 필드를 본문 확정(draft 미언급, 코드 실사용).
- 05의 Obsidian 뷰 설계를 `--register-obsidian` 구현 계약으로 확정(§4.7).
- §7에 `scope: team|personal` 승격(05 §5 전제 → 정식)·v1 provider 매트릭스(Linear·Slack·Notion·GCal) 통합.
- 03의 "conformance kit 구상"을 reference `check.py` 3모드 실물과 대조(§6.2).

---

## 부록 D. 이 SPEC이 01~05를 대체한다 — 제거 시 repoint 필요한 참조 목록

> 본 SPEC이 `spec/01`~`spec/05`를 대체한다. **아직 제거하지 말 것** — 아래 참조들이 옛 경로/섹션을 가리키므로, spec/ 파일 제거 전 이 목록을 `docs/spec/`의 새 섹션 번호로 repoint해야 한다. (목록만 — 본 작업에서 수정하지 않음.)

**문서·진입점:**
- `README.md` — "스펙: 설계 폴더 `spec/` — 01 팀메모리 · 02 … 05 onboard." (목록 전체 → `docs/spec/README.md`)
- `AGENTS.md` — "설계 스펙(`spec/04-install.md`·`spec/05-onboard-skill.md`) 확인."
- `infra/skills/base/tm-onboard/SKILL.md` footer — "`spec/04-install.md`·`spec/05-onboard-skill.md`를 확인."
- `conformance/scenarios/README.md` — 상단 설명, 모드 설명, 시나리오 표, 하니스 인터페이스 설명의 spec 02/03 참조.

**코드 docstring·주석 (동작 변경 없음, 참조 텍스트만):**
- `infra/install.py` 상단 docstring — spec/04, 스펙 02 불변식 참조.
- `infra/install.py` `register_obsidian()` docstring — spec/05 opt-in 참조.
- `infra/install_lib.py` 상단 docstring과 주요 섹션 주석 — spec/04, 스펙 01/02/05 참조.
- `infra/install_lib.py` `_INDEX_MD`·`_MEMBERS_HEADER` scaffold 문자열 — 스펙 01 §2.1 및 과거 members.md 역할/소문자 문구.
- `infra/install_lib.py` env/Obsidian 관련 주석·docstring — 스펙 01/05 참조.
- `infra/teammode.py` `_render_banner()` docstring — 과거 §11.5 배너 참조.
- `infra/teammode.py` `_validate_author()`, `_frontmatter()`, `_is_session_log_name()`, `_parse_frontmatter()`, `_collect_members()`, `_read_index()` docstring — 스펙 01 및 과거 섹션 참조.
- `infra/workday.py` 상단 docstring과 timezone 주석 — 스펙 01 §3.2 참조.
- `infra/agents/claude/adapter.py` 상단 docstring·설계 불변식·번역/배선/MCP/skill 주석 — 스펙 02 및 과거 섹션 참조.
- `infra/agents/claude/normalize.py` 상단 docstring과 normalize/filter/lookup docstring — 스펙 02 §6 및 과거 normalize 섹션 참조.
- `infra/agents/codex/adapter.py` 상단 docstring·Codex 특성·fallback/MCP 주석 — 스펙 02 및 과거 §11.11/§7 참조.
- `infra/agents/codex/normalize.py` 상단 docstring — 스펙 02 §6 및 "부록 B / 초안 §12 미결" 참조.
- `infra/hooks/session-start.py` 상단 docstring과 `_team_root()` docstring — 스펙 02/04/01 참조.
- `infra/hooks/session-log-remind.py` 상단 docstring과 `_team_root()` docstring — 스펙 02/01 참조.
- `conformance/check.py` 상단 docstring, `_session_log_files()`, Tier 산출 주석, `_lint_manifest_canonical()`, `SubprocessEngine.run()` 주석 — 스펙 01/02/03 및 과거 §11.x 참조.

**테스트 docstring (참조 텍스트만 — 동작 무관):**
- `tests/*.py` 다수 — `test_workday`(스펙 01 §3.2)·`test_context`(스펙 01 §4)·`test_log`(스펙 01 §3)·`test_normalize`(스펙 02 §6)·`test_adapter_claude/codex`(스펙 02 §4·§5·§7)·`test_install_l1a~l1e`/`test_install_golden`/`test_install_l1b`(spec/04 각 절)·`test_register_obsidian`(spec/05).

**참고**: `BUILD-LOG.md`·`CHECKLIST.md`도 다수 spec 참조를 포함하나 이력 문서이므로 repoint 우선순위 낮음(원하면 후속).

> ⚠️ 02 draft의 섹션 번호 중 일부(§11.x: §11.5 배너·§11.11 Tier·§11.12 check 3모드)는 `spec/02-hook-manifest.md` 본문에 존재하지 않는다(초안 §11.x를 가리키는 잔존 참조). SPEC 매핑: §11.5→§3.1(배너), §11.11→§6.3(Tier), §11.12→§6.2(check 3모드). repoint 시 함께 정정.
