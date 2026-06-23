# 유틸 스킬 커스텀

## util 스킬이 뭔가

팀원이 자기 작업에 쓰는 보조 스킬(리서치·디버그·리뷰 등). 코어 스킬(tm·tm-context …)과 달리 **팀원별로** 골라 설치한다 — 기획자와 개발자가 쓰는 스킬이 다르니까.

- **추천 풀(카탈로그)** = `infra/skills/util/<name>/` 디렉터리들. 여기 있는 것만 설치 가능.
- **멤버 설치목록** = `memory/team/sessions/<member>/util-skills.json`.
- **반영 시점**: 팀모드 `on`이고 `add`/`remove`에 `--install`(또는 `--settings <경로>`)을 주면 즉시 심링크 반영. 안 주면 — `--root .`만 주면 active 상태여도 즉시반영을 skip하고(실호스트 보호) json만 갱신한다 → 다음 `on` 때 반영. `off`면 항상 json만 갱신.
- **원칙**: 직접 파일/심링크를 만지지 않는다 — 엔진 `util` 동사만 경유.

## 동사 3종

```bash
# 조회 — available(카탈로그) + installed(멤버 설치현황)을 JSON으로
python infra/teammode.py util list --root . [--member <이름>]

# 추가 (on 상태면 --install로 즉시 반영)
python infra/teammode.py util add --root . --member <이름> --skill <스킬명> --install

# 제거 (마찬가지)
python infra/teammode.py util remove --root . --member <이름> --skill <스킬명> --install
```

- 없는 스킬(카탈로그 밖) 추가 시 엔진이 **거부**한다. 추측하거나 강행하지 않는다.
- `add`/`remove`는 멱등 — 재실행해도 안전.

## ⭐ 세션로그 기반 추천

"이 사람한테 뭘 깔아주면 좋을까"를 **세션로그에서 끌어내** 추천한다. 메모리를 에이전트가 직접 읽어 판단하는 tm-mode다운 방식.

**절차:**

1. **카탈로그·설치현황 조회**
   ```bash
   python infra/teammode.py util list --root . --member <이름>
   ```
   → `{"available": [{"name":"...","description":"..."}], "installed": ["스킬명", ...]}`
   ⚠️ 형식이 다르다 — `available`은 **오브젝트 배열**(name+description), `installed`는 **문자열 배열**. 매칭할 땐 `available[].name`과 `installed[]` 문자열을 비교한다.

2. **빈 카탈로그 가드 (중요)** — `available`이 비어 있으면 **추천할 게 없다.** 공회전하지 말고 이렇게 안내하고 멈춘다:
   > "추천할 util 스킬 풀이 비어 있어요. 먼저 `infra/skills/util/`에 스킬을 채워야 추천이 됩니다." (채우는 법은 아래 참조)

3. **세션로그에서 작업 패턴 파악** — 해당 멤버의 로그를 읽는다:
   ```bash
   ls memory/team/sessions/<이름>/        # YYYY-MM-DD.md 들
   ```
   최근 로그 몇 개를 Read해서 **반복되는 작업 유형**을 본다(예: 리서치를 자주 한다 / 디버깅이 잦다 / PR 리뷰가 많다).

4. **매칭 추천** — 미설치(`available[].name` 중 `installed`에 없는 것) 중에서, 파악한 패턴에 **실제로 맞는 것만** 고른다. 각 추천엔 **로그 근거 한 줄을 반드시** 붙인다:
   > "로그 보니 디버깅 세션이 잦으시네요(6/18·6/19) → `<디버그스킬>` 추천해요."

5. **동의 후 추가** — 사용자가 받으면 `util add`로 설치. 강행 금지.

**안전장치:**

- 로그 근거 없는 추측 추천 금지 — "개발자니까 이거" 같은 일반론 말고, **그 사람 로그에 실제로 나타난 일** 기반으로만.
- `available` 풀 밖 스킬은 추천하지 않는다(어차피 엔진이 거부).
- 세션로그가 없거나 비면(신규 멤버) 추천을 보류하고, "로그가 쌓이면 추천해드릴게요"로 안내.
- 로그는 있는데 카탈로그와 맞는 패턴이 안 잡히면 억지 추천 금지 — "현재 로그로는 마땅한 추천 근거를 못 찾았어요"로 솔직히 안내하고 보류.

## 카탈로그 채우는 법

추천 풀이 비었으면 `infra/skills/util/<name>/SKILL.md`를 두면 등록된다(다른 스킬과 동일한 SKILL.md 구조). 팀이 공유할 보조 스킬을 여기 모아두면, 이후 멤버별로 골라 설치·추천할 수 있다.

## 흔한 실수

| 실수 | 올바른 방법 |
|---|---|
| 심링크/json 직접 편집 | 엔진 `util` 동사만 경유 |
| 빈 카탈로그에서 추천 시도 | `available` 비면 채우라 안내하고 멈춤 |
| 로그 안 보고 일반론으로 추천 | 세션로그 근거 한 줄 필수 |
| 카탈로그 밖 스킬 추천 | `available` 안에서만 (엔진이 거부함) |
