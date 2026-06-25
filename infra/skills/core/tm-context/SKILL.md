---
name: tm-context
description: Use when the user asks about team status, current situation, or needs context loaded. Triggers on "팀 현황", "지금 팀 상황", "맥락 알려줘", "context", "팀원 뭐해".
---

# tm-context — 팀 현황 조회

현재 팀 현황을 조회해 사람 말로 요약한다. 순수 읽기 전용 — 파일/상태를 건드리지 않는다.

> 절차: L1(세션로그)을 **먼저·항상 완전히** 채운 뒤, 연결된 L2 서비스(이슈/캘린더)를 보조로 덧붙인다.
> L2는 없어도 tm-context는 완전히 동작한다(graceful).

## L1 코어 (항상 동작)

### 1. 멤버·인덱스 수집

```bash
python infra/teammode.py context --root . --json
```

반환 JSON 스키마:

```json
{
  "state": "on" | "off",
  "index": "<INDEX.md 전문 또는 빈 문자열>",
  "members": [
    {
      "author": "<영문 이름>",
      "date": "<작업일 YYYY-MM-DD>",
      "summary": "<세션로그 frontmatter 한 줄 요약>",
      "file": "<세션로그 상대 경로>",
      "role": "<team.config.json role 또는 null>"
    }
  ]
}
```

> ℹ️ `members` 에는 세션로그가 1개 이상인 멤버만 들어온다(`_collect_members` 가 로그 0개 디렉토리를 skip). 로그가 없는 팀원은 JSON 에 안 나타난다 — 전체 명부가 필요하면 `team.config.json` 의 `members_file`(기본 `memory/team/members.md`) 을 읽어 보완한다.

### 2. 세션로그 심층 읽기 (핵심 — summary 로 끝내지 말 것)

`members[].summary` 는 frontmatter 한 줄이라 현황의 절반도 못 담는다. **각 멤버의 `members[].file` 을 실제로 열어** 본문에서 아래 3가지를 의미 기반으로 추출한다.

| 항목 | 추출 대상 |
|------|-----------|
| **하고 있는 일** | 최근 작업 내용 요약 (2~3줄) |
| **다음에 할 일** | 다음 단계·TODO·"다음" 류 서술 |
| **막힌 것 / 결정 필요** | 블로커·미결·"막힌"·결정 대기 항목 |

> ⚠️ 세션로그는 **자유 형식**이다 — `## 작업 내역` 같은 고정 템플릿 헤더가 보장되지 않는다. 헤더 문자열을 키로 파싱하지 말고 **본문을 읽어 의미로 추출**한다. 같은 멤버 디렉토리(`memory/team/sessions/<author>/`)에 최근 로그가 여러 개면 가장 최근 1~3일을 함께 읽어 흐름을 잡는다.

### state 점검

- `state == "on"`: 정상.
- `state == "off"`: 팀모드가 꺼져 있음. 최근 커밋된 세션로그는 보이지만 현재 세션은 반영 안 됨을 안내.

### 갓 셋업(세션로그 0개) 안내

`members` 배열이 비어 있으면(전원 기록 없음):
> "아직 팀 기록이 없습니다. 다음 작업부터 세션로그가 쌓입니다 (`tm off` 시 자동 저장)."

## L2 보조 정보 (연결된 슬롯만, 없으면 조용히 skip)

`team.config.json` 의 `services` 슬롯을 읽어, **연결된 서비스가 있을 때만** 데이터를 덧붙인다.
미연결이면 조용히 skip — L2 조회 실패(네트워크·인증)도 비치명이다. L1 을 먼저 보여주고 "일부 서비스 데이터를 가져오지 못했습니다"로만 안내한다.

### 3. decisions

`memory/team/decisions/current.md` 가 있으면 최근 결정사항 몇 개를 추가 출력한다. 없으면 조용히 skip.

### 4. 이슈 (services.issues 슬롯)

`team.config.json` 의 `services.issues.provider` 가 채워져 있는지 **파일을 직접 읽어** 확인한다.

연결돼 있으면, 그 트래커는 **`tm-<provider>` 규약으로 MCP 서버에 등록돼 있다** (예: `linear` → `tm-linear`). 따라서 provider 를 하드코딩하지 말고:

1. `services.issues.provider` 값을 읽는다 (예: `linear`).
2. 사용 가능한 `mcp__tm-<provider>__*` 도구 중 **이슈 목록/검색** 도구를 골라 호출한다(도구명은 런타임에 발견 — provider 마다 다르므로 스킬에 박지 않는다).
3. 멤버별 **In Progress** 이슈만 assignee 로 매칭해 식별자+제목만 보조 표시한다.

> ⚠️ Backlog/Todo 는 표시하지 않는다(현황이 아니라 계획 — 별도 스킬 소관). `teammode.py issue` 동사는 MCP 조회를 하지 않고 provider/스키마 echo 만 하므로, 실제 조회는 위처럼 MCP 도구로 **직접** 한다.
> `tm-<provider>` 서버가 미등록이거나 이슈 도구가 없으면 조용히 skip.

### 5. 캘린더 (services.calendar 슬롯)

`services.calendar.provider` 가 채워져 있으면, 같은 `tm-<provider>` 규약의 MCP 서버에서 **다가오는 7일 팀 일정**을 조회해 덧붙인다. 캘린더 식별자는 슬롯의 리소스 필드(`calendar_id` 등 `providers/<provider>.json` 의 `resource_fields`)에서 읽는다.

> 팀이 어떤 캘린더를 팀 일정용으로 지정했는지는 **인스턴스 설정 소관**이다 — 특정 색상/카테고리 필터를 스킬에 하드코딩하지 않는다. 미연결이면 조용히 skip.

## 출력 형식

이름 앞 이모지는 **`members_file`(members.md)에 멤버별 이모지가 정의돼 있으면** 붙이고, 없으면 이름만 쓴다(members.md 가 단일 소스).

```
<이모지?> <이름> (<role?>)
  하고 있는 일: (세션로그 심층 추출, 2~3줄)
  다음 할 일: (세션로그의 다음 단계, 있으면)
  막힌 것: (블로커·미결, 있으면 표시 / 없으면 생략)
  📌 이슈: (In Progress 식별자+제목, L2 연결 시 / 없으면 줄 생략)

📋 최근 결정
  ...

🗓 다가오는 팀 일정 (L2 캘린더 연결 시)
  ...
```

로그가 없어 JSON 에 안 잡힌 팀원을 명부로 보완해 표시할 땐 "세션 로그 없음 — 최근 활동 불명"으로 적는다.

## 설계 원칙 요약

| 항목 | tm-context 동작 |
|------|----------------|
| 레포 경로 | `--root .` 명시 (env 폴백 없음) |
| 세션로그 | summary 가 아니라 `file` 본문을 의미 기반으로 심층 추출 (자유 형식) |
| 멤버 이모지 | members.md 에 정의돼 있으면 렌더, 없으면 이름만 |
| issues 슬롯 | `tm-<provider>` MCP 도구로 직접 조회(In Progress 만), 미연결 skip |
| calendar 슬롯 | `tm-<provider>` MCP 도구로 직접 조회, 색상필터 등은 인스턴스 소관, 미연결 skip |
| git pull | 불포함 (tm on 절차가 처리) |

## 안 하는 것

- 상태 변경, 이슈 생성, 파일 수정 — 읽기 전용.
- 리스크 판단 → `tm-check-health` (별도 스킬, 로드맵).
- Backlog/Todo·계획 나열 (현황만 — 계획은 별도 스킬).
- L2가 미연결이라고 오류 표시하거나 연결을 강요하지 않는다.

## 엔진 동사 호출 요약

| 항목 | 명령 |
|------|------|
| 팀 맥락 수집 | `teammode.py context --root . --json` |

---

> 동작이 예상과 다르면 `infra/teammode.py`의 `cmd_context` 함수를 확인하세요.
> `tm-<provider>` MCP 등록 규약은 `infra/install.py`(linear → tm-linear) 참조.
