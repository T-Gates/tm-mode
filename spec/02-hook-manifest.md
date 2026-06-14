# teammode 스펙 02 — 훅·어댑터 표준 (Hook & Adapter)

| | |
|---|---|
| spec_version | **0.1** |
| 상태 | 정식 초판 (2026-06-12) — `teammode-adapter-spec-draft.md` v0.1-draft를 정식 체재로 재구성 |
| 범위 외 | 현행 코드(tgates-toolkit) 마이그레이션·구현 노트는 스펙이 아니다 — 초안 §11.x 참조 |
| 관련 스펙 | [01 팀 메모리 표준](01-team-memory.md), [03 호환 선언 절차](03-conformance.md) |

---

## 0. 한 문장

> 훅·스킬·MCP의 **내용은 1벌**만 유지하고, 에이전트(Claude Code/Codex/Hermes/…)마다 다른 **표기·등록 방식·입력 스키마는 `agents/<name>/` 어댑터가 번역**한다. 새 에이전트 지원 = 어댑터 파일 3개 추가, 기존 코드 무접촉.

## 1. 용어와 표기 규약

### 1.1 용어

| 용어 | 정의 |
|---|---|
| **정규형 (canonical)** | 에이전트 무관하게 teammode가 정의하는 표준 어휘 — 정규 이벤트(§3.1), 행위 클래스(§3.2), 정규 입력 스키마(§6) |
| **어댑터 (adapter)** | `agents/<name>/` 폴더. 설치 시점에 정규 선언을 해당 에이전트의 설정으로 번역·등록한다 |
| **normalize 심 (shim)** | 런타임에 에이전트의 훅 입력 JSON을 정규 스키마로 변환하는 얇은 통역 계층 |
| **행위 클래스 (action)** | 빌트인 툴의 에이전트 무관 추상화. 예: `file_edit` = Claude Code의 `Write\|Edit` = Codex의 `apply_patch` |
| **공통 스크립트** | `infra/hooks/*.py`. 정규 스키마만 인지하며 특정 에이전트를 알지 못한다 |
| **역할 슬롯 (service slot)** | issues/chat/docs/calendar 등 서비스의 도구 중립 추상화 (§9) |

### 1.2 표기 규약

[스펙 01 §1.1](01-team-memory.md)과 동일: **필수 / 권장 / 예약**.

## 2. 디렉토리 구조

```
infra/
├── hooks/                       # 공통 — 1벌
│   ├── manifest.json            #   정규형 선언 (§3)
│   └── *.py                     #   공통 스크립트. 정규 스키마(§6)만 인지, 에이전트 무지
├── skills/                      # 공통 — 1벌 (오버라이드 규칙은 §8)
├── agents/
│   └── <name>/                  # 에이전트별 어댑터 — 파일 3개
│       ├── adapter.py           #   설치 시점 번역기 (§5)
│       ├── events.json          #   번역표 (§4)
│       └── normalize.py         #   런타임 통역사 (§6)
└── install / update / sync      # 얇은 디스패처: --<agent> 플래그 → agents/<name>/ 위임
```

구조 불변식 (필수):

1. 공통 스크립트와 스킬에 에이전트 고유 표기를 쓰지 않는다.
2. 에이전트 고유 지식은 전부 `agents/<name>/` 아래에만 존재한다.
3. 디스패처는 분기 로직을 갖지 않는다 — 어댑터 CLI(§5)에 위임만 한다.

## 3. manifest.json — 정규형 선언

`infra/hooks/manifest.json`은 훅 엔트리의 배열이다. **에이전트 무관 정규형으로만** 선언한다.

```jsonc
[
  {
    "event": "PostToolUse",                 // 필수. 정규 이벤트 (§3.1)
    "match": { "action": "file_edit" },     // 선택. 정규 매처 (§3.2). 생략 = 전체 매칭
    "script": "auto-commit.py",             // 필수. hooks/ 하위 공통 스크립트
    "args": "",                             // 선택. 기본 ""
    "timeout": 5000,                        // 선택. ms. 기본값은 구현 정의
    "mode": "on",                           // 선택. 생략 = base(상시) / "on" = 팀 모드 켜진 동안만
    "fallback": "runtime",                  // 선택. "runtime" | "drop". 기본 "drop" (§7)
    "strict": false                         // 선택. 기본 false. normalize 변환 실패 정책 (§6 의무 4)
  }
]
```

**금지 (필수)**: manifest에 에이전트 고유 표기를 직접 쓰는 것 — `mcp__*` 형식 툴명, `Write|Edit` 같은 에이전트 매처 문자열, 특정 에이전트의 설정 파일 경로. 전부 정규형으로만 선언한다. (lint/conformance 검사 대상)

### 3.1 정규 이벤트 (v0.1)

| 정규 이름 | 의미 | 의미 보존 요건 |
|---|---|---|
| `SessionStart` | 에이전트 세션 시작 | 세션당 1회, 사용자 첫 입력 전에 발화 |
| `UserPromptSubmit` | 사용자 프롬프트 제출 직후 | 에이전트가 응답 생성을 시작하기 전에 발화 |
| `PreToolUse` | 툴 실행 직전 | **차단 가능해야 함** — 훅의 실패(비정상 종료 코드)가 툴 실행을 막을 수 있어야 한다 |
| `PostToolUse` | 툴 실행 직후 | 툴 결과 확정 후 발화 |

- 정규 이름은 **Claude Code 어휘를 기준**으로 한다 (Tier 1 Reference).
- 정규 이벤트 추가는 스펙 minor bump로만 한다 (§10).
- 에이전트가 어떤 정규 이벤트를 표현할 수 없으면 어댑터 번역표에 `null`로 **명시**한다 (§4) — 무음 누락 금지.

### 3.2 정규 매처

두 종류가 있다. `match` 객체에는 정확히 하나의 키만 쓴다 (필수).

```jsonc
// (a) 빌트인 행위 클래스
{ "action": "file_edit" }

// (b) MCP 툴 — server는 "정규 서버명" (등록 별칭이 아니다!)
{ "mcp": { "server": "linear", "tool": "create_issue" } }
{ "mcp": { "server": "slack",  "tool": "post_message" } }
```

**정규 행위 클래스 v0.1**: `file_edit` (파일 생성·수정) 하나만 정의한다. `shell_exec`, `file_read` 등은 필요가 입증될 때 minor bump로 추가한다.

**정규 서버명 (필수)**: MCP 서버의 등록 별칭은 환경마다 다르다(`slack-tgates`, `claude_ai_Google_Calendar` 등). manifest는 `team.config.json`의 서비스 선언(§9)에 등장하는 **정규 서버명**(provider 식별자: `linear`, `slack`, `notion`, `google` 등)만 참조한다. 정규 서버명 → 실제 등록 별칭 매핑을 보장하는 것은 어댑터의 등록 시점 책임이다(§5.2).

## 4. events.json — 어댑터 번역표

각 어댑터는 정규 어휘 → 자기 에이전트 표기의 번역표를 선언한다. 설치 로직(코드)이 아니라 **데이터**로 두는 것이 핵심이다 — 번역 규칙이 코드 분기에 숨지 않게 한다.

```jsonc
// agents/codex/events.json (예시)
{
  "agent": "codex",
  "config_file": "~/.codex/config.toml",     // 훅이 등록되는 에이전트 설정 파일
  "events": {
    "SessionStart": "SessionStart",
    "UserPromptSubmit": "UserPromptSubmit",
    "PreToolUse": null,                       // null = 미지원 명시 (→ §7 폴백 발동)
    "PostToolUse": "PostToolUse"
  },
  "actions": {
    "file_edit": "apply_patch"                // 정규 행위 → 이 에이전트의 매처 문자열
  },
  "mcp_tool_format": "{server}.{tool}"        // 정규 mcp 매처 → 매처 문자열 템플릿
}
```

규칙 (필수):

1. `events`에는 §3.1의 **모든 정규 이벤트 키가 존재**해야 한다. 지원하지 않으면 값을 `null`로 — **키 누락 금지** (lint/conformance 검사 대상).
2. `actions`에는 v0.1의 모든 정규 행위 클래스 키가 존재해야 한다. 미지원이면 `null`.
3. 에이전트별 특수 처리(이벤트 skip, 매처 문자열 변형 등)를 설치 코드에 하드코딩하는 것을 금지한다. 전부 이 파일로 표현한다.
4. `mcp_tool_format`의 치환 변수는 `{server}`, `{tool}` 두 개다. `{server}`에는 어댑터가 해석한 **실제 등록 별칭**이 들어간다 (§3.2 정규 서버명 매핑).

## 5. adapter.py — 설치 시점 계약

어댑터는 다음 CLI를 구현해야 한다 (필수). 디스패처가 호출한다.

```
adapter.py sync [--on|--off]     # manifest → 에이전트 설정 파일 동기화 (멱등)
adapter.py install-skills        # 스킬 설치 (§8 오버라이드 해석 포함)
adapter.py install-mcp           # MCP 서버 등록 (정규 서버명 → 자기 에이전트 방식)
adapter.py uninstall             # 역순 제거
```

- `sync`를 **플래그 없이** 실행하면 마지막으로 적용된 on/off 상태를 유지한 재동기화다. 상태의 영속 방법은 구현 세부이며, 한 번도 `--on`/`--off`가 적용된 적 없으면 off로 간주한다(필수).
- MCP 매처가 있는 manifest를 동기화하려면 `install-mcp`가 선행되어야 한다(§5.2의 별칭 보장이 전제 조건). 전제 위반 시(별칭 미확정 상태의 `sync`) 해당 **MCP 매처 엔트리만** `[warn]` 출력 후 생략하고 나머지는 정상 동기화한다 — 전체 실패 금지(필수).

### 5.1 `sync`의 의무 (필수)

1. manifest 각 엔트리를 events.json으로 번역해 자기 에이전트 설정에 등록한다. `--on`/`--off`는 `mode: "on"` 엔트리의 활성/비활성을 전환한다.
2. **등록되는 훅 커맨드는 반드시 normalize 경유로 배선한다**: `<python> agents/<name>/normalize.py <script> [args]`. 공통 스크립트를 직접 등록하는 것은 금지다. `<python>`은 **크로스플랫폼**으로 설치 시점 `sys.executable`(인터프리터 절대경로)을 쓴다 — Windows 에서 `python3`가 PATH 에 없거나 venv 여도 견고하며, normalize 도 child 실행에 `sys.executable`을 써서 체인 전체가 동일 인터프리터로 일관된다. 공백 든 경로(예 Windows `C:\Program Files\...\python.exe`)는 따옴표로 안전 인용한다. (reference: `Adapter.default_python`/`build_command`.)
3. 미지원 이벤트/매처를 만나면 §7 폴백 정책을 적용한다. **무음 스킵 금지** — 등록을 생략하는 경우 `[warn]` 출력 의무.
4. **멱등성**: 재실행 시 변경이 없으면 설정 파일도 무변경이어야 한다. manifest에서 제거된 훅은 에이전트 설정에서도 제거한다.
5. **소유권**: teammode가 등록한 항목만 추가·수정·삭제한다. 사용자가 직접 등록한 훅은 건드리지 않는다. 식별 마커: 등록 커맨드가 **팀 루트 하위의 `agents/<name>/normalize.py`를 가리키는지** 여부 (단순 부분문자열 `agents/` 포함 판정 금지 — 사용자의 무관한 경로를 오인 삭제할 수 있다).

### 5.2 `install-mcp`의 의무

1. `team.config.json`의 서비스 선언(§9)을 읽어, 연결된 provider의 MCP 서버를 자기 에이전트 방식으로 등록한다 (필수).
2. 정규 서버명 → 실제 등록 별칭의 매핑을 이 시점에 확정·보장한다 (필수, §3.2). **기본 규칙: 별칭을 정규 서버명과 동일하게 등록한다.** 에이전트 제약으로 동일 등록이 불가능한 경우에만 어댑터가 매핑을 자체 영속화하고(위치·형식은 어댑터 내부 사항) `sync`가 그것을 읽어 매처 문자열을 생성한다.
3. 레포 내 MCP 코드는 경로 직접 참조로 등록하는 것을 권장한다 (pull로 코드 자동 갱신). 갱신 시 lock 해시 비교로 변경 시에만 의존성 재설치하는 것을 권장한다.

## 6. normalize — 런타임 계약

**입력**: 에이전트가 주는 원어(原語) JSON (stdin) → **출력**: 공통 스크립트에 정규 JSON (stdin) 전달.

### 6.1 정규 입력 스키마 (canonical input) v0.1

```jsonc
{
  "event": "PostToolUse",            // 필수. 정규 이벤트 (§3.1)
  "action": "file_edit",             // 해당 시. 정규 행위 클래스
  "tool": {                          // 해당 시 (Pre/PostToolUse). MCP면 정규 서버명으로
    "kind": "mcp",                   //   "mcp" | "builtin"
    "server": "linear",              //   kind=mcp일 때. 정규 서버명
    "name": "create_issue"
  },
  "files": ["/abs/path"],            // file_edit일 때. 대상 파일 절대 경로 배열
  "prompt": "사용자 입력 …",          // UserPromptSubmit일 때
  "agent": "codex",                  // 필수. 출처 에이전트명
  "raw": { }                         // 선택. 원어 전문 (탈출구 — 공통 스크립트의 남용 금지). 생략 시 {}
}
```

공통 스크립트는 이 스키마만 신뢰해야 하며, `raw`는 정규 필드로 표현 불가능한 정보에 한해 최후 수단으로만 읽는다 (권장).

### 6.2 normalize의 의무 (필수)

1. **변환**: 원어 → 정규 스키마. 이 파일의 존재 이유.
2. **런타임 자가 필터**: `fallback: "runtime"`으로 무매처 등록된 훅(§7)은, manifest에서 자기 엔트리의 `match`를 조회해 **현재 발동의 내용(행위 클래스 또는 MCP 서버·툴)이 불일치하면** `exit 0`(무동작)으로 끝낸다. 조회 키는 **(script, 현재 정규 이벤트) 쌍**이다 — 이를 위해 manifest에서 같은 (event, script) 조합의 중복 엔트리는 금지한다(필수, lint 검사 대상). 이 경로는 수 ms 안에 끝나야 한다.
3. **시맨틱 전파**: 공통 스크립트의 exit code와 stdout을 그대로 에이전트에 전파한다. 특히 `PreToolUse`의 차단 시맨틱이 보존되어야 한다.
4. **변환 실패 시**: 훅 실패로 세션을 막지 않는다 — `exit 0` + stderr 경고. **예외**: 안전장치 성격의 훅은 manifest에 `"strict": true`로 선언하며, 이 경우 변환 실패도 훅 실패로 전파한다.

## 7. 폴백 정책

manifest 엔트리의 `fallback` 필드. 어댑터가 해당 엔트리를 자기 에이전트로 표현할 수 없을 때의 동작을 선언한다.

| 값 | 발동 조건 | 동작 |
|---|---|---|
| `"drop"` (기본값) | 이벤트 또는 매처를 이 에이전트가 표현 불가 | 등록 생략 + `[warn] <script>: <agent> 미지원 → 비활성` 출력 (필수 — 무음 금지) |
| `"runtime"` | 매처만 표현 불가 (이벤트는 지원) | 무매처로 등록 + normalize 자가 필터(§6.2-2)로 의미 보존 |

- `"runtime"`인데 **이벤트 자체가 미지원**(`events.json`에서 `null`)이면 표현할 방법이 없으므로 `"drop"`과 동일하게 동작한다 + `[warn]` (필수).
- **빈 슬롯 우선 규칙 (필수)**: `mcp` 매처가 참조하는 provider의 역할 슬롯이 미연결(§9.2)이면, `fallback` 값과 무관하게 등록을 생략하고 `[info]`를 출력한다 — 에러가 아니다 (빈 슬롯 = 1급 시민). 슬롯 연결 후 `sync` 재실행으로 활성화한다.
- 선택 가이드: 빠져도 되는 편의 기능 → `drop` / 빠지면 안 되는 안전장치(확인·차단류) → `runtime` (+ 필요시 `strict`).

## 8. 스킬 해석 — 단일 소스 + 오버라이드

스킬 본문은 1벌(`infra/skills/`)이 원칙이다. 어댑터의 `install-skills`는 다음 탐색 순서를 따른다 (필수):

```
1. agents/<name>/skills/<skill>/SKILL.md   ← 있으면 이것 (오버라이드)
2. infra/skills/**/<skill>/SKILL.md        ← 폴백 (공통본)
```

규칙:

1. 오버라이드는 **구조적 분기**(서브에이전트 문법, 에이전트 전용 기능)에만 쓴다 (필수). 표기 차이는 오버라이드 사유가 아니다 — 아래 표기 규약으로 해결한다.
2. **MCP 표기 규약 (필수)**: 스킬 본문은 시맨틱 참조만 쓴다 — "Linear MCP의 list_issues로 조회". `mcp__*` 직표기 금지 (lint/conformance 검사 대상). 에이전트별 실제 툴명 형식은 각 에이전트의 진입 문서(CLAUDE.md/AGENTS.md류)에 1회만 명시한다.
3. **드리프트 경보 (권장)**: 오버라이드 파일이 생기면 lint가 "공통본 변경 시 오버라이드 검토" 목록에 등재한다.

## 9. 서비스 추상화 (크로스서비스)

에이전트 축(§2~§8)과 직교하는 두 번째 축: 같은 역할(이슈 트래커, 채팅, …)을 팀마다 다른 제품으로 채울 수 있어야 한다.

### 9.1 역할 슬롯 선언

`team.config.json`의 `services`는 **역할 → provider** 선언이다. 역할명은 도구 중립 어휘로 고정한다 (필수): `issues`(— kanban이 아니라), `chat`, `docs`, `calendar`.

```jsonc
"services": {
  "issues": { "provider": "linear",  /* provider별 상수 … */ },
  "chat":   { "provider": "slack",   /* … */ },
  "docs":   { "provider": "notion",  /* … */ },
  "calendar": { "provider": "google", /* … */ }
}
```

### 9.2 빈 슬롯 = 1급 시민 (필수)

역할 슬롯 미연결은 **에러가 아니라 선언된 상태**다. `services`에서 키 자체를 생략하면 그 슬롯은 빈 것이다.

- 스킬은 frontmatter에 `requires: [issues]` 형식으로 의존 슬롯을 선언한다.
- 미연결 슬롯에 의존하는 스킬은 **자동 비활성**되고, 나머지는 전부 동작한다 ("Slack만 있어도 시작").
- 자동 비활성의 메커니즘(v0.1): `install-skills`가 미연결 슬롯에 의존하는 스킬의 설치를 생략하고 `[info]`로 출력한다. 슬롯을 나중에 연결하면 `install-skills` 재실행으로 활성화한다 (온보딩 마법사가 연결 직후 수행하는 것을 권장).
- 구현은 빈 슬롯을 이유로 설치·세션 시작을 실패시켜서는 안 된다.

### 9.3 스킬·훅에서의 서비스 참조

- **스킬 본문**: 역할 어휘만 쓴다 — "이슈 트래커 MCP에서 조회". 제품명 직표기 금지 (필수, lint 검사 대상). 역할 → 실제 제품의 번역은 LLM이 런타임에 config를 보고 한다.
- **훅 매처**: 코드는 LLM 번역을 쓸 수 없으므로, 정규 액션 `{service, action}` 선언을 `providers/<name>.json` 매핑표로 컴파일한다 — events.json(§4)과 동일한 "번역표는 데이터" 패턴. v0.1에서 이 매핑표의 상세 스키마는 **예약**이며 v0.2에서 확정한다. v0.1의 MCP 매처는 §3.2의 정규 서버명 방식을 쓴다.

### 9.4 provider 생태계 (권장 사항)

- Tier 1 provider = 독푸딩 조합인 Linear/Slack/Notion/Google Calendar. 그 외는 provider pack 기여 슬롯으로 연다.
- MCP 서버를 직접 제작·유지보수하지 않는 것이 기본 방침 — 기존 생태계(공식 MCP 등)를 활용하고, teammode는 providers/ 슬롯·가이드·기여자 등재만 제공한다.
- 공식 MCP가 없는 사내 도구용으로 `infra/mcp/_template/`(최소 서버 + 시작 스크립트 + 가이드)을 동봉하는 것을 권장한다 — 에이전트가 온보딩 중 즉석 제작을 제안할 수 있는 설계도.
- provider pack에는 `token_guide` 필드(토큰 발급 딥링크 + 단계)를 권장한다 — 온보딩에서 사람 몫(토큰 수령)을 최소화하는 장치.

### 9.5 온보딩 = 설정 마법사 (권장)

config는 손 편집이 아니라 **온보딩 스킬이 대화로 완성**하는 것을 권장한다: 역할 슬롯을 하나씩 돌며 연결/스킵을 묻고("캘린더 쓰세요? → 토큰 주세요 → ✅ / 스킵 → 관련 스킬 💤"), 빈 슬롯은 §9.2에 따라 선언된 상태로 남긴다.

토큰 병목 완화 사다리 (권장):

1. 가능한 provider는 **OAuth remote MCP를 우선 채택** — 토큰 심부름 자체를 없앤다.
2. provider pack의 `token_guide` 필드 — 발급 딥링크와 단계 안내.
3. **팀당 1회 원칙** — 토큰은 리더가 1회 수령해 팀 자격증명 금고(reference 구현의 credentials 패턴 일반화)에 두고, 팀원 온보딩은 금고에서 가져온다.
4. 온보딩 멘트로 기대치 고정 — "당신 몫은 토큰 N개뿐" 식으로 사람 몫의 범위를 시작 시점에 알린다.

## 10. 버저닝

- 본 스펙은 SemVer 0.x를 따른다. 정규 이벤트(§3.1)·행위 클래스(§3.2)·정규 입력 스키마(§6.1)·어댑터 계약(§5)의 변경 = **minor bump + CHANGELOG 기록** (필수).
- 0.x 동안은 깨질 수 있음을 명시한다. 독립 구현이 2개 이상 생기면 1.0 동결 + RFC-lite 절차 도입 ([스펙 01 §5](01-team-memory.md)와 공통).
- 어댑터·독립 구현은 자신이 따르는 spec_version을 명시해야 한다 (필수).

---

## 부록 A. 초안 대비 확정 사항

| 항목 | 초안 | 정식판 결정 |
|---|---|---|
| `fallback` 기본값 | 미정의 | **`"drop"`** — 미선언 훅이 무매처 등록되는 것보다 경고 후 비활성이 안전 |
| `strict` 필드 | §6 본문에서 언급만 | manifest 필드로 정식 등재 (기본 `false`) |
| `match` 키 개수 | 미정의 | 정확히 1개 (`action` 또는 `mcp`) |
| `runtime` + 이벤트 미지원 | 미정의 | `drop`과 동일 동작 + `[warn]` |
| `actions` 키 완전성 | events만 명시 | actions에도 동일 적용 (모든 정규 행위 클래스 키 존재, 미지원 `null`) |
| 정규 서버명의 소스 | `mcp.servers` 키 이름 (초안 §12 미결) | **`services` 선언의 provider 식별자**로 확정. 트레이드오프: 동일 provider 다중 인스턴스·역할 중복 provider는 v0.1에서 표현 불가 (부록 B로 이월) |
| 정규 이벤트의 의미 보존 요건 | `PreToolUse` 차단 가능만 명시 | 4종 전부에 발화 시점 요건 명문화 (§3.1 표) — conformance 판정 기준 |
| 별칭 매핑의 보장 방식 | "어댑터가 등록 시점에 보장" | 기본 규칙 = 정규 서버명과 동일 별칭으로 등록. 불가 시에만 어댑터 자체 영속화 + `install-mcp` → `sync` 순서 의존 명시 |
| `mcp_tool_format` 치환 변수 | 예시만 | `{server}`/`{tool}` 2개로 고정, `{server}` = 실제 등록 별칭 |
| 자가 필터 조회 키 | "manifest에서 자기 match 조회" | (script, 현재 정규 이벤트) 쌍. 같은 (event, script) 중복 엔트리 금지 |
| `sync` 무플래그 동작 | 미정의 | 마지막 적용 상태 유지 재동기화. 최초에는 off 간주 |
| 소유권 마커 | "커맨드 경로에 `agents/` 포함" | 팀 루트 하위 `agents/<name>/normalize.py` 지시 여부로 좁힘 (오인 삭제 방지) |
| canonical input 필드 필수성 | 미구분 | `event`·`agent` 필수, `raw` 선택(생략 시 `{}`), 나머지는 해당 시 |
| `requires` 자동 비활성 메커니즘 | 미정의 | `install-skills`가 설치 생략 + `[info]`, 슬롯 연결 후 재실행으로 활성화 |
| 빈 슬롯 provider를 참조하는 훅 매처 | 미정의 | `fallback` 무관 등록 생략 + `[info]` (§7) — 스킬 `requires`와 대칭 |
| `install-mcp` 선행 위반 시 `sync` | 미정의 | MCP 매처 엔트리만 `[warn]` 생략, 나머지 정상 — 전체 실패 금지 |
| minor bump 대상 | 이벤트·행위·스키마 | 어댑터 계약(§5) 추가. 구현의 spec_version 명시 의무 신설 |
| `providers/<name>.json` 스키마 | §8.5에서 방향만 | v0.1 예약 — v0.2 확정으로 명시 |
| §11.x 구현 노트 | 본문에 혼재 | 스펙에서 제외 (마이그레이션 문서는 초안 참조) |

## 부록 B. 미결 (초안 §12 승계)

스펙 0.2 전에 확정해야 할 것:

- [ ] Codex 훅 입력 JSON 실스키마 확인 (§6 예시는 가정 — Codex 실환경에서 캡처 필요)
- [ ] `PreToolUse` 차단 시맨틱이 Codex/Hermes에서 표현 가능한지 (불가 시 확인·차단류 훅의 폴백 설계)
- [ ] Hermes 이벤트 매핑 실조사 (pre_llm_call≈UserPromptSubmit, on_session_start≈SessionStart — 2026-06-11 조사 기준, 재확인 필요)
- [ ] normalize의 manifest 조회 비용 (매 발동마다 파일 읽기 — 캐시 필요성)
- [ ] 동일 provider 다중 인스턴스·역할 중복 provider의 정규 서버명 표현 (부록 A "정규 서버명의 소스" 결정의 잔여 한계)
- [ ] `team.config.json` `services`의 provider별 상수 스키마 상세
- [ ] `providers/<name>.json` 매핑표 스키마 (§9.3)
