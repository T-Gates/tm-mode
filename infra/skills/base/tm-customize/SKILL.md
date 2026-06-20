---
name: tm-customize
description: Use when the user wants to customize team banner, persona, or util skills. Triggers on "팀색 입히기", "커스텀", "배너 바꿔", "페르소나 설정", "스킬 추가", "스킬 제거", "tm-customize".
---

# tm-customize — 팀색 입히기

팀 identity(배너·페르소나·유틸 스킬)를 입히는 라우터 스킬.

## 진입

먼저 사용자에게 묻는다:

> "배너 / 페르소나 / 스킬 — 어느 영역을 커스텀할까요?"

선택에 따라 해당 `references/` 문서를 읽고 그 지침대로 작업한다.

## 영역별 참조

| 선택 | 참조 문서 | 요약 |
|------|----------|------|
| 배너 | `references/banner.md` | tm on 시 출력되는 ASCII 아트 배너 교체 |
| 페르소나 | `references/persona.md` | 팀 톤·캐릭터 지침 작성 |
| 스킬 | `references/skills.md` | 팀원별 util 스킬 추가·제거·조회 |

라우터는 가볍게. 판단과 절차는 각 references 문서에 위임한다.
