# 정체성 커스텀 (팀명·greeting·farewell)

## 정체성이 뭔가

팀의 표시용 문구 3종. 전부 `team.config.json`(팀 루트)의 `team` 블록에 산다.

| 필드 | 어디에 쓰이나 | 기본 공식 |
|---|---|---|
| `name` | statusline 팀명 · greeting/farewell 기본 공식 · 배너 기본값 | (install 시 git 레포명) |
| `greeting` | `tm on` 배너 직후 출력 | `{name} 팀모드 ON` |
| `farewell` | `tm off` 종료 시 출력 | `수고하셨습니다 — {name}` |

## ✅ 언제든 자유롭게 바꿀 수 있다 (파급 0)

팀명·인사말은 순수 표시용이다. **언제 바꿔도 아무것도 안 깨진다** — 크리덴셜 금고가 단일 파일(`default.json`)이라 팀명에 묶이지 않기 때문(2026-06-21 단일 금고 전환). L2 토큰·세션로그·멤버 어느 것도 영향 없음.

> (멀티팀은 현재 미지원이라 이 단순함이 성립. 멀티팀이 필요해지면 그때 금고 키 전략을 다시 본다.)

## 방법 — team.config.json 직접 편집

⚠️ 배너와 결정적 차이: `team.config.json`은 **팀 루트**라 `kb-write-guard`(memory/ 전용) 대상이 **아니다** → **Edit/Write 도구로 편집해도 된다**(JSON이라 sed보다 Edit가 안전). 정체성 변경용 엔진 동사는 없다.

1. `team.config.json`을 열어 `team.name` / `team.greeting` / `team.farewell`을 고친다. **유효한 JSON 유지**(쉼표·따옴표).
   ```jsonc
   "team": {
     "name": "tgates",
     "greeting": "tgates 팀모드 켜짐 🐳",
     "farewell": "오늘도 고생했어요 — tgates"
   }
   ```
2. greeting/farewell을 기본 공식과 다르게 두면 `personality_customized`가 `true`가 된다(엔진이 런타임에 기본 공식과 비교해 판정 — 배너의 그 플래그와 동일).
3. **확인**: `python3 infra/teammode.py context --root . --json`으로 반영 확인하거나, 다음 `tm on`에서 새 greeting이 뜨는지 본다.

## 팀명만 바꿀 때 참고

- `name`을 바꿔도 greeting/farewell을 기본 공식으로 두면 자동으로 새 이름이 반영된다(공식이 `{name}`을 쓰므로). greeting/farewell을 이미 커스텀 문구로 박아놨다면 거기 든 옛 팀명은 **수동으로** 같이 고친다.
- 배너가 `memory/banner.txt`에 팀명을 박아둔 ASCII면 팀명 변경이 배너엔 자동 반영 안 된다 → 배너는 `references/banner.md`로 따로 갱신.

## 흔한 실수

| 실수 | 올바른 방법 |
|---|---|
| 정체성도 Bash로만 고쳐야 한다고 오해 | team.config.json은 루트라 Edit/Write OK (banner만 Bash 전용) |
| JSON 깨뜨림(쉼표 누락 등) | 편집 후 유효성 확인 — `tm context`가 읽히는지 |
| greeting 커스텀 후 팀명 바꿨는데 옛 이름 잔존 | 커스텀 문구 속 팀명은 수동 갱신(공식 `{name}`만 자동) |
| "팀명 바꾸면 토큰 깨진다"고 걱정 | 단일 금고라 안 깨짐 — 자유 변경 |
