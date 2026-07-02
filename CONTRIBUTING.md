# Contributing to tm-mode

> **EN:** Fork → branch → PR to `main`; run `python -m pytest -q` before submitting; stdlib-only (`dependencies = []` — no new runtime deps); commit prefixes `feat/fix/docs/chore`.

tm-mode 기여를 환영합니다. 코드 PR은 누구나 보낼 수 있고, tm-mode를 쓰는 팀 인스턴스는 보통 `tm-contribute` 스킬로 이슈부터 올립니다 — 둘 다 좋은 기여입니다.

## 기여 흐름

1. **이슈 먼저 (권장)** — 버그·개선안은 [GitHub Issues](https://github.com/T-Gates/tm-mode/issues)에 먼저 올려주세요. 방향을 미리 맞추면 PR이 헛돌지 않습니다. 팀 인스턴스에서는 `tm-contribute` 스킬이 "진짜 업스트림 버그인지 / 로컬 설정 문제인지" 진단 후 이슈를 올려줍니다.
2. **Fork → 브랜치** — 레포를 fork하고 작업 브랜치를 만듭니다 (`fix/...`, `feat/...` 등).
3. **PR to `main`** — 변경 이유·동작 변화를 본문에 짧게 적어 `main`으로 PR을 엽니다.
4. **리뷰·머지** — 메인테이너가 리뷰 후 머지합니다. 리뷰 코멘트에는 수정 커밋으로 응답해주세요.

## 개발 루프

```bash
git clone https://github.com/<you>/tm-mode && cd tm-mode
python -m pytest -q        # 전체 테스트 — PR 전 반드시 통과
```

- Python **3.9+**, 외부 실행 의존성 없음.
- 테스트는 `tests/`에 있고, 새 동작에는 테스트를 함께 넣어주세요 (버그 수정이면 재현 테스트 먼저).

## 철칙: stdlib-only

`pyproject.toml`의 `dependencies = []`는 규칙입니다 — **런타임 pip 의존성을 추가하지 마세요.** git/gh는 호스트 필수도구(prerequisite)이지 pip 의존성이 아닙니다. 외부 라이브러리가 꼭 필요해 보이면 코드 대신 이슈로 먼저 논의해주세요.

## 커밋 스타일

히스토리 관례를 따릅니다 — `<type>(<scope>): <설명>` 형식, 타입은 `feat` / `fix` / `docs` / `chore`. 설명은 한국어·영어 모두 좋습니다.

```
feat(memory): add `memory route {upsert|remove}` verb
fix(codex): pass TEAMMODE_MEMBER to Codex hooks
chore: switch license to Apache 2.0
```

## 문서

문서는 한국어 우선(Korean-first)입니다. v1에서 이중언어(ko 기본 + en 선택)를 계획 중이므로, 영어 번역 기여도 환영합니다.
