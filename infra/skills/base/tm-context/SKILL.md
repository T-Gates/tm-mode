---
name: tm-context
description: Use when the user asks about team status, current situation, or needs context loaded. Triggers on "팀 현황", "지금 팀 상황", "맥락 알려줘", "context", "팀원 뭐해".
---

# tm-context — 팀 현황 조회

<!-- 설계상 core(on시 활성) — core 설치 메커니즘 구현되면 infra/skills/core/로 이동. 잠정 base에 둠(동작 우선). -->

현재 팀 현황을 조회해 사람 말로 요약한다. 순수 읽기 전용 — 파일/상태를 건드리지 않는다.

## L1 코어 (항상 동작)

```bash
python infra/teammode.py context --root . --json
```

반환된 JSON을 파싱해 팀원별로 아래 3가지를 사람 말로 요약한다.

| 항목 | 세션로그 섹션 | 출력 예시 |
|------|--------------|-----------|
| 하고 있는 일 | `## 작업 내역` 최근 내용 | "허브 펌웨어 MQTT 연동 중" |
| 다음 할 일 | `## 다음 할 일` 섹션 | "- SCD40 연결 테스트" |
| 막힌 것 | `## 막힌 점 / 시도` 섹션 | "AWS 계정 미정" |

> ℹ️ JSON의 `summary` 필드는 세션로그 첫 줄 요약이다. 상세 맥락이 필요하면 `members[].file` 경로의 세션로그 전문을 읽는다.

### 갓 셋업(세션로그 0개) 안내

`members` 배열이 비어 있으면:
> "아직 팀 기록이 없습니다. 다음 작업부터 세션로그가 쌓입니다 (`tm off` 시 자동 저장)."

### state 점검

- `state == "on"`: 정상.
- `state == "off"`: 팀모드가 꺼져 있음. 최근 커밋된 세션로그는 보이지만 현재 세션은 반영 안 됨을 안내.

### 출력 형식

```
<이름>
  하고 있는 일: (세션로그 summary 또는 작업 내역 요약)
  다음 할 일: (세션로그 "다음 할 일" 섹션)
  막힌 것: (있으면 표시, 없으면 생략)

<이름2>
  세션로그 없음 — 다음 작업부터 기록됩니다.
```

이름 앞 이모지는 팀모드에 이모지 스키마가 미정이므로 이름만 표시한다 (이모지는 후속 인프라).

## graceful 추가 정보 (있을 때만, 없으면 조용히 skip)

### decisions.md
`memory/team/decisions/current.md` 파일이 있으면 최근 결정사항을 추가 출력한다.
없으면 조용히 skip (teammode에 decisions 매니페스트 미구현 — graceful).

```python
decisions_path = Path(".") / "memory" / "team" / "decisions" / "current.md"
if decisions_path.is_file():
    # 파일 읽어 "최근 결정사항" 섹션으로 출력
```

### L2 서비스 (team.config.json services 슬롯 기반)

`team.config.json`을 읽어 연결된 서비스가 있을 때만 해당 데이터를 추가한다.
**미연결이면 조용히 skip** — tm-context는 L2 없어도 완전히 동작한다.

- `services.issues` 슬롯 연결 확인:
  ```bash
  python infra/teammode.py issue --root . --json
  ```
  연결돼 있으면 In Progress 이슈를 MCP로 조회해 참고 표시.
  미연결이면 skip.

- `services.calendar` 슬롯: 연결돼 있으면 다가오는 팀 일정을 추가. 미연결이면 skip.

> ⚠️ L2 서비스 데이터는 **보조 정보**다. L2 조회 실패(네트워크·인증 오류)는 비치명 — L1 결과를 먼저 보여주고 "일부 서비스 데이터를 가져오지 못했습니다"로 안내한다.

## 범용화 차이점 (tgates-get-context 대비)

| 항목 | tgates-get-context | tm-context |
|------|-------------------|------------|
| 레포 경로 | `$TGATES_HOME` 환경변수 | `--root .` 명시 |
| 멤버 이모지 | members.md에서 읽어 표시 | 이름만 표시 (이모지 스키마 미정) |
| issues 슬롯 하드코딩 | 특정 팀 ID·colorId 필터 | config services 슬롯으로만, 미연결 skip |
| calendar 슬롯 | 특정 캘린더 ID 하드코딩 | config services 슬롯으로만, 미연결 skip |
| git pull | 절차에 포함 | 불포함 (tm on 절차가 처리) |

## 안 하는 것

- 상태 변경, 이슈 생성, 파일 수정 — 읽기 전용.
- 리스크 판단 → `tm-check-health` (별도 스킬, 로드맵).
- L2가 미연결이라고 오류 표시하거나 연결을 강요하지 않는다.

## 엔진 동사 호출 요약

| 항목 | 명령 |
|------|------|
| 팀 맥락 수집 | `teammode.py context --root . --json` |

---

> 동작이 예상과 다르면 `infra/teammode.py`의 `cmd_context` 함수를 확인하세요.
