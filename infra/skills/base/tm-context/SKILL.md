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

`members[].summary`(한 줄)로 간단 현황을 요약한다. 작업 내역·다음 할 일·막힌 점 등 **상세 맥락이 필요하면 `members[].file`(세션로그 markdown)을 열어 해당 섹션을 직접 파싱한다** — summary 만으로는 3섹션 본문을 얻을 수 없다.

### 갓 셋업(세션로그 0개) 안내

`members` 배열이 비어 있으면:
> "아직 팀 기록이 없습니다. 다음 작업부터 세션로그가 쌓입니다 (`tm off` 시 자동 저장)."

### state 점검

- `state == "on"`: 정상.
- `state == "off"`: 팀모드가 꺼져 있음. 최근 커밋된 세션로그는 보이지만 현재 세션은 반영 안 됨을 안내.

### 출력 형식

```
<이름>
  하고 있는 일: (members[].summary 또는 file 파싱 결과)
  다음 할 일: (file 파싱 "다음 할 일" 섹션, 있으면)
  막힌 것: (file 파싱 "막힌 점" 섹션, 있으면 표시, 없으면 생략)
```

> ℹ️ `members` 배열에 포함된 멤버는 세션로그가 1개 이상인 멤버뿐이다(`_collect_members` 가 로그 0개 디렉토리를 skip). 아직 기록이 없는 팀원은 JSON 에 나타나지 않는다.

`members`가 비어 있으면(전원 기록 없음):
> "아직 팀 기록이 없습니다. 다음 작업부터 세션로그가 쌓입니다 (`tm off` 시 자동 저장)."

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
  `team.config.json`의 `services.issues.provider` 필드가 채워져 있는지 **파일을 직접 읽어** 확인한다.
  > ⚠️ `teammode.py issue` 동사는 MCP 이슈 조회를 수행하지 않는다 — provider/input 스키마 echo 만 한다(빈 슬롯이면 비-JSON 안내 텍스트 출력). 이슈 조회는 연결된 역할 슬롯 MCP 도구를 통해 **직접** 호출해야 한다.

  연결된 MCP 도구가 있을 때만 In Progress 이슈를 조회해 L1 요약 이후 보조로 표시한다.
  미연결이면 조용히 skip(L1 은 항상 먼저·완전).

- `services.calendar` 슬롯: 연결돼 있으면 다가오는 팀 일정을 추가. 미연결이면 skip.

> ⚠️ L2 서비스 데이터는 **보조 정보**다. L2 조회 실패(네트워크·인증 오류)는 비치명 — L1 결과를 먼저 보여주고 "일부 서비스 데이터를 가져오지 못했습니다"로 안내한다.

## 설계 원칙 요약

| 항목 | tm-context 동작 |
|------|----------------|
| 레포 경로 | `--root .` 명시 (env 폴백 없음) |
| 멤버 이모지 | 이름만 표시 (이모지 스키마 미정) |
| issues 슬롯 | config services 슬롯으로만, 미연결 skip |
| calendar 슬롯 | config services 슬롯으로만, 미연결 skip |
| git pull | 불포함 (tm on 절차가 처리) |

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
