# Contributing to tm-mode

> **EN:** Fork → branch → PR to `main`; run `python -m pytest -q` before submitting; stdlib-only (`dependencies = []` — no new runtime deps); commit prefixes `feat/fix/docs/chore`.

tm-mode 기여를 환영합니다. 코드 PR은 누구나 보낼 수 있고, tm-mode를 쓰는 팀 인스턴스는 보통 `tm-contribute` 스킬로 이슈부터 올립니다 — 둘 다 좋은 기여입니다.

## 기여 흐름

1. **이슈 먼저 (권장)** — 버그·개선안은 [GitHub Issues](https://github.com/T-Gates/tm-mode/issues)에 먼저 올려주세요. 방향을 미리 맞추면 PR이 헛돌지 않습니다. 팀 인스턴스에서는 `tm-contribute` 스킬이 "진짜 업스트림 버그인지 / 로컬 설정 문제인지" 진단 후 이슈를 올려줍니다.
   - **이슈 양식은 [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/)이 단일 소스** — 버그는 `[Bug] ` 제목 접두 + `bug` 라벨 + 환경/현상/재현 절차/기대·실제 동작/원인·진단 근거, 제안은 `[Feature] ` + `enhancement`. 웹에서는 폼이 자동 적용되지만, **`gh` CLI·에이전트로 올릴 때는 yml 템플릿이 적용되지 않으므로** 템플릿 파일을 먼저 읽고 같은 제목 접두·라벨·필드 구조로 본문을 작성해주세요.
2. **Fork → 브랜치** — 레포를 fork하고 작업 브랜치를 만듭니다 (`fix/...`, `feat/...` 등).
3. **PR to `main`** — 본문은 [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) 양식(무엇/왜 · 변경사항 · 테스트 · 체크리스트)을 따릅니다. 웹 PR에는 자동으로 뜨지만 **`gh pr create --body` 는 템플릿을 채워주지 않으므로** 같은 구조로 직접 작성해주세요.
4. **리뷰·머지** — 메인테이너가 리뷰 후 머지합니다. 리뷰 코멘트에는 수정 커밋으로 응답해주세요.

## 코드 지도

어디를 보면 되는지는 [README.md](README.md) 의 Architecture 절(영/한) — 3계층 구조·컴포넌트 지도·세션 데이터 흐름·설계 철칙·기여 시나리오별 진입점.

## 개발 루프

```bash
git clone https://github.com/<you>/tm-mode && cd tm-mode
python -m pytest -q        # 전체 테스트 — PR 전 반드시 통과
```

- Python **3.9+**, 외부 실행 의존성 없음.
- 테스트는 `tests/`에 있고, 새 동작에는 테스트를 함께 넣어주세요 (버그 수정이면 재현 테스트 먼저).

## 철칙: stdlib-only

`pyproject.toml`의 `dependencies = []`는 규칙입니다 — **런타임 pip 의존성을 추가하지 마세요.** git/gh는 호스트 필수도구(prerequisite)이지 pip 의존성이 아닙니다. 외부 라이브러리가 꼭 필요해 보이면 코드 대신 이슈로 먼저 논의해주세요.

## 코드 언어 — 코드 내용은 영어로

**신규·수정 코드의 내용물(주석·docstring·테스트 코드·식별자·assert 메시지)은 영어로 작성해주세요.** 기존 한국어 주석은 해당 코드를 고칠 때 함께 영어로 옮기면 좋습니다(일괄 번역 강제 아님).

- **사용자에게 보이는 런타임 문구**(설치 출력·훅 주입물·CLI 메시지)는 이 정책이 아니라 **i18n(locale) 체계**를 따릅니다 — 하드코딩 언어 전환 금지.
- 문서는 기존 정책 유지: front-door(README 등) 영어 기본 + 한국어 절, `docs/spec/` 한국어 우선.

## 공개 위생 — 실환경 식별자 금지 (필수)

이 레포는 공개 제품입니다. **어떤 파일에도(테스트 fixture·문서 예시·주석 포함) 실제 사람·팀·머신의 식별자를 쓰지 마세요.** 2026-07-07 공개 전 감사에서 실멤버명·홈경로·팀 인스턴스 레포가 fixture 로 다수 유입돼 전수 익명화 + 히스토리 처리를 치렀습니다 — 같은 일을 반복하지 않습니다.

**금지**: 실명·실멤버 핸들, 실 이메일, 하드코딩 홈경로(`/Users/<실계정>/…`), 팀 인스턴스 레포/조직 참조(제품 레포 `T-Gates/tm-mode` 제외), 실제 팀·제품·사업 도메인 명칭.

**허용 어휘(이것만 쓰세요)** — 사람: `alice`·`bob`·`jane-doe`, 편집거리 쌍: `jonathan`↔`jonathon`, 팀/조직: `acme`/`Acme`/`ACME`, 레포: `acme/acme-team`, 이메일: `user@example.com`(개인메일 판정 fixture 는 `me@gmail.com`), 경로: `tmp_path`(pytest)·`~/…`·`/Users/alice/…`.

CI 가드 `tests/test_no_identity_leaks.py` 가 일반 패턴(하드코딩 홈경로·이메일·비제품 org 레포)을 기계적으로 차단합니다. 가드에 걸리면 실값을 허용 어휘로 바꾸는 게 정답이지, 가드에 예외를 추가하는 게 아닙니다(예외는 위 허용 어휘 확장에 한함).

**리뷰 체크**: 도그푸딩에서 가져온 실측 값(경로·명령어·해시·로그)을 커밋하기 전, 식별자를 허용 어휘로 치환했는지 확인하세요. 실측 golden(해시 벡터 등)은 치환 후 동일 알고리즘으로 재유도합니다.

## 커밋 스타일

히스토리 관례를 따릅니다 — `<type>(<scope>): <설명>` 형식, 타입은 `feat` / `fix` / `docs` / `chore`. 설명은 한국어·영어 모두 좋습니다.

```
feat(memory): add `memory route {upsert|remove}` verb
fix(codex): pass TEAMMODE_MEMBER to Codex hooks
chore: switch license to Apache 2.0
```

## 문서

front-door 문서는 **영어 기본**이며 한국어는 같은 [README.md](README.md) 하단 '한국어' 절입니다(2026-07-06 — 홈에서 앵커 점프, 파일 이동 없음). 내부 명세(docs/spec/)는 당분간 한국어 우선 — 영어 번역 기여를 환영합니다.
