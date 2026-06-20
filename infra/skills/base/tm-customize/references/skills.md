# 유틸 스킬 커스텀

팀원별 util 스킬을 조회·추가·제거한다. 직접 파일/심링크 편집 금지 — 엔진 `util` 동사만 경유.

## 조회

```bash
python infra/teammode.py util list --root . [--member <이름>]
```

## 추가

```bash
python infra/teammode.py util add --root . --member <이름> --skill <스킬명>
```

## 제거

```bash
python infra/teammode.py util remove --root . --member <이름> --skill <스킬명>
```

- 팀모드 on 상태면 즉시 심링크 반영. off 상태면 json만 갱신(다음 on 시 반영).
- 없는 스킬 추가 시 엔진이 거부. 추측하거나 강행 금지.
- add/remove 재실행 멱등.
