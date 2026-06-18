---
name: tm-manage-utils
description: Use when managing util skill installation — listing, adding, or removing util skills per user. Triggers on "유틸 관리", "유틸 추가", "유틸 제거", "유틸 목록", "스킬 설치", "스킬 제거".
---

# tm-manage-utils — 유틸 스킬 관리

팀원별 util 스킬 목록을 조회·추가·제거한다. 판단만 하고, 직접 파일/심링크를 건드리지 않는다 — 모든 변경은 엔진 `util` 동사 경유.

## 안 하는 것

- 직접 `ln`, 파일 쓰기, JSON 편집 금지 — 엔진 동사만 호출.
- base/core 스킬 관리 금지 — util 전용.
- 다른 멤버 util-skills.json 수정 금지.
- util 스킬 파일 자체 생성/수정 금지.

## 트리거

"유틸 관리", "유틸 추가", "유틸 제거", "유틸 목록", "스킬 설치", "스킬 제거"

## 절차

### 1. 현황 조회

```bash
python infra/teammode.py util list --root . [--member <이름>]
```

반환 JSON:
```json
{
  "available": [{"name": "...", "description": "..."}],
  "installed": ["skill-a", ...]
}
```

`available`: `infra/skills/util/` 에 있는 설치 가능한 util 스킬 전체.
`installed`: `--member` 지정 시 해당 멤버의 현재 등록 목록.

### 2. 추가

사용자가 추가할 스킬을 선택하면:

```bash
python infra/teammode.py util add --root . --member <이름> --skill <스킬명>
```

- `--member`: `_validate_author` 검증됨 (영숫자·`-`·`_` 조합, 영숫자 시작).
- 없는 스킬 추가 시 엔진이 거부(`[error]`). 추측하거나 강행 금지.
- 팀모드 on 상태면 즉시 심링크 반영. off 상태면 json만 갱신(다음 on 시 반영).

### 3. 제거

```bash
python infra/teammode.py util remove --root . --member <이름> --skill <스킬명>
```

- 이미 미등록 스킬을 제거해도 멱등(에러 없음).
- 심링크도 함께 제거됨(on 상태인 경우).

## 설계 원칙

| 항목 | 동작 |
|------|------|
| 레포 경로 | `--root .` 명시 (env 폴백 없음) |
| 직접 편집 | 금지 — `util` 동사만 경유 |
| 멤버 검증 | `_validate_author` 동형 (영숫자 시작, 슬래시·`..` 차단) |
| 없는 스킬 | 엔진이 거부, 추측 금지 |
| 멱등 | add/remove 재실행 무해 |
