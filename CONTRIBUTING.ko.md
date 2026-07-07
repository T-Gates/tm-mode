[English](CONTRIBUTING.md) | **한국어**

# tm-mode 기여 가이드

**tm-mode**(AI 코딩 에이전트를 위한 크로스에이전트 팀 협업 툴킷)에 기여를 고려해주셔서 감사합니다. 프로젝트가 무엇을 하는지·어떻게 구성됐는지는 [README.md](README.md)를 먼저 참고하세요. 이 문서는 "어떻게 기여하는지"를 다룹니다.

## 1. 기여 경로 두 가지

- **팀 인스턴스 안에서 작업 중이라면** (이 템플릿에서 생성된 레포, 예: `tm-mode init`/`join`으로 만든 팀 레포): 버그·개선 제안의 가장 빠른 길은 **`tm-contribute` 스킬**(`infra/skills/core/tm-contribute/SKILL.md`)입니다. 에이전트에게 "본레포에 올려줘"라고 하면, 실제 `tm-mode` 버그인지(`upstream/main`에서 재현되는지) 아니면 인스턴스 로컬 문제인지 먼저 진단하고, 초안을 승인받은 뒤 GitHub 이슈를 대신 올려줍니다. 이 스킬은 PR을 열지 않습니다 — 이슈만 올립니다. 팀 인스턴스는 이 템플릿 위에 자체 파일·디렉터리(추가 스크립트, 연동 서버, 드래프트 등)를 얹기도 하는데, 그런 것들은 그 팀의 소유이며 업스트림 PR의 대상이 절대 아닙니다.
- **코드로 직접 기여하고 싶다면**: 제품 레포 `T-Gates/tm-mode`를 fork 하세요(팀 인스턴스 자신의 `origin`이 아닙니다 — 팀 인스턴스는 보통 이 레포를 `upstream`이라는 이름의 git remote로 추적합니다). 아래 흐름을 따르세요.

## 2. 이슈 먼저 (권장)

코드를 쓰기 전에 [T-Gates/tm-mode 이슈](https://github.com/T-Gates/tm-mode/issues)를 먼저 올려 방향을 맞추세요 — PR이 헛돌지 않습니다. 이슈 폼을 사용하세요:

- **버그 신고** (`.github/ISSUE_TEMPLATE/bug_report.yml`) — `tm-mode` 자체(`infra/`) 범위입니다. 팀 자신의 `memory/` 데이터 문제는 대상이 아닙니다. 환경·현상·재현 절차·기대/실제 동작·진단(파악 못 했으면 "미파악"이라고 정직하게)을 요구합니다.
- **기능 제안** (`.github/ISSUE_TEMPLATE/feature_request.yml`) — 문제/제안/대안 + stdlib-only로 구현 가능한지 체크박스.

**이슈 양식은 `.github/ISSUE_TEMPLATE/`이 단일 소스**입니다 — 제목 접두(`[Bug]`/`[Feature]`), 라벨(`bug`/`enhancement`), 필드 구조 모두. 웹에서는 폼이 자동 적용되지만, `gh` CLI·에이전트로 올릴 때는 yml 템플릿이 적용되지 않으므로(`gh issue create --body`는 폼을 렌더링하지 않음) 템플릿 파일을 먼저 읽고 같은 제목 접두·라벨·필드 구조로 직접 작성해주세요.

## 3. 개발 환경

```bash
git clone https://github.com/<you>/tm-mode && cd tm-mode
pip install pytest          # pytest는 stdlib이 아니고, 이 레포에 lockfile로 고정돼 있지도 않음
python -m pytest -q         # 전체 테스트 — PR 전 반드시 통과
```

- Python **3.9+**. 제품 자체는 런타임 의존성이 0입니다(`pyproject.toml`의 `dependencies = []`) — git/gh는 호스트 필수도구지 pip 패키지가 아닙니다.
- 개발·테스트에 패키지를 먼저 설치할 필요가 없습니다: 각 테스트 파일이 스스로 `sys.path`를 관리합니다(예: `infra/`를, 훅 모듈을 테스트하는 파일은 `infra/hooks/`를 추가) — 따라서 `pip install -e .`·`uv sync`·가상환경 단계가 필요 없습니다.
- 위 명령은 bare `python`을 씁니다 — 이 레포 자체 CI(`.github/workflows/test.yml`)와 PR 템플릿이 쓰는 것과 동일합니다. macOS의 순정 Homebrew/python.org 설치처럼 `python` alias가 없는 환경이라면 `python3`를 쓰세요.
- `gh`(GitHub CLI)는 레포·이슈를 생성하는 흐름(`tm-mode init`, `tm-contribute`)을 같이 테스트할 때만 필요합니다.

## 4. 레포 구조

**제품 코드** — 업스트림 PR이 건드리는 영역:

| 경로 | 정체 |
|---|---|
| `src/teammode/cli.py` | 런처 — pip/curl/npx로 배포되는 얇은 stdlib 진입점(`tm-mode init`/`join`) |
| `infra/teammode.py` | 엔진 — 동사(verb) 디스패처(`on`/`off`/`log`/`context`/`pull`/`commit`/`update`/`issue`/`memory`/`util`) |
| `infra/install.py` + `infra/install_lib.py` | 부트스트랩 — 훅 배선·스킬 배포·env 주입. `--dry-run`/`--yes` 게이트 |
| `infra/git_ops.py` | git 공통 작업 + 동기화 판정 |
| `infra/agents/<name>/` | 에이전트별 어댑터(Claude `settings.json`, Codex `config.toml`) |
| `infra/hooks/` | 공통 훅 — session-start·auto-commit·push-worker·kb-write-guard 등 |
| `infra/skills/{base,core,util}/` | 3계층 스킬 — 활성화 규칙은 §6 참고 |
| `infra/mcp/` | L2 서비스 연결을 지원하는 MCP OAuth 헬퍼 코드 |
| `infra/credentials.py`, `infra/i18n.py`, `infra/io_encoding.py`, `infra/providers.py`, `infra/workday.py` | 엔진 보조 모듈 |
| `infra/guidelines.md`, `infra/guidelines.en.md` | 에이전트 세션에 주입되는 "팀모드 운영 지침" 텍스트(한국어/영어) |
| `infra/banners/`, `infra/migrations/`, `infra/scaffolds/` | 배너 아트, 마이그레이션 노트, 새 팀 레포 셋업 시 쓰이는 scaffold 템플릿 |
| `tests/` | pytest 스위트 — 기능/수정 하나당 `test_*.py` 하나 |
| `conformance/check.py` | `lint` / `verify` / `conform` 검수 도구 |
| `conformance/scenarios/*.json` | 5개 골든 시나리오 — `verify`/`conform`이 실행하는 실행 가능한 스펙 |
| `docs/spec/` | 동작의 **단일 권위본**(SPEC v0.3, 영어) |
| `docs/BACKLOG.md`, `docs/archive/`, `docs/scenarios/` | 설계 백로그, 아카이브된 설계 노트, 온보딩 시나리오 문서 |
| `providers/*.json` | L2 provider 팩(issues/chat/docs/calendar) — 데이터만, 코드 변경 없이 추가 가능 |
| `npm/` | npm 배포 shim(`npx tm-mode`) — 핀된 `cli.py` 위 얇은 스킨 |
| `.github/` | `CODEOWNERS`, CI 워크플로, PR 템플릿, 이슈 템플릿 |
| `LICENSE`, `NOTICE.md` | Apache-2.0 라이선스. `NOTICE.md`는 메인테이너의 최신 공지 피드로, `tm on` 시 인스턴스와 자동 대조됨 |
| `install.sh`, `INSTALL.md` | 사람용 curl 설치 스크립트와 설치 레퍼런스 |
| `team.config.example.json` | 인스턴스별 `team.config.json`의 템플릿 — 실제 `team.config.json`은 셋업 시 생성되며 그 자체는 인스턴스 로컬 |

**인스턴스 로컬** — 업스트림 PR 대상이 절대 아닌, 각 팀의 데이터: `memory/`(그 팀의 세션로그·결정 — 어떤 제품 코드 경로도 동기화·삭제하지 않음), `team.config.json`, `.teammode-active`. 팀 인스턴스는 이 템플릿 위에 자체 목적의 파일·디렉터리를 더 얹기도 하는데, 그것들 역시 업스트림 코드가 아닙니다.

전체 아키텍처 지도(컴포넌트 표·세션 데이터 흐름·설계 철칙)는 [README.md](README.md)의 "아키텍처" 절 참고.

## 5. 테스트 · conformance 검사 실행

```bash
python -m pytest -q                                                          # 전체 테스트 스위트
python conformance/check.py lint    --root .                                 # 정적: manifest/events.json 형태 검사, 엔진 실행 없음
python conformance/check.py verify  --root . --engine "python infra/teammode.py"   # 동적: 골든 시나리오 5개를 우리 엔진에 실행
python conformance/check.py conform --root . --engine "<다른 구현>"           # 같은 시나리오를 제3의 구현에 실행, advisory Tier 산출용
```

각 플래그는 `python conformance/check.py --help`로 확인 가능하고, 골든 시나리오 형식은 `conformance/scenarios/README.md`에 문서화돼 있습니다. 새 동작에는 테스트를 함께 추가하고, 버그 수정이면 재현 테스트를 먼저 red로 작성한 뒤 고치세요.

전체 스위트는 수 초가 아니라 **수 분** 걸립니다 — 멈춘 것으로 오해하지 말고 그렇게 예상하세요. CI는 Python 3.9와 3.12에서 테스트합니다(`.github/workflows/test.yml`) — 훨씬 최신 인터프리터를 쓰고 있고 관련 없어 보이는 실패가 무더기로 나온다면, 제품 버그로 단정하기 전에 이 버전들 중 하나로 먼저 시도해보세요. `tests/test_install_l1b.py::test_bootstrap_exit3_when_no_name_resolvable` **하나만** 실패한다면, 전역 `git config user.name`이 설정된 머신에서는 정상적으로 나타나는 현상입니다(`.github/workflows/test.yml` 맨 위의 경고 주석 참고) — 환경 전제조건 문제이지 제품 버그가 아닙니다.

## 6. 코드 스타일과 컨벤션

- **stdlib-only는 철칙**: `pyproject.toml`의 `dependencies = []`는 기본값이 아니라 규칙입니다 — 런타임 pip 의존성을 추가하지 마세요. `git`/`gh`는 호스트 필수 도구지 pip 의존성이 아닙니다. 외부 라이브러리가 꼭 필요해 보이면 코드 대신 이슈로 먼저 논의하세요.
- **설계 철칙**: 엔진은 판단하지 않는다 — 동사는 멱등한 기계 작업만, 요약·분류는 스킬(에이전트)의 몫; 훅은 절대 세션을 죽이지 않는다 — 무raise, 손자 프로세스까지 `killpg`로 종료하는 타임아웃, 실패는 비치명으로 나중에 표면화; 테스트는 실 호스트에 손대지 않는다 — 실 `~/.claude`·실 원격 금지, tmp + `--settings` 격리와 가짜 원격만; 인스턴스 데이터는 불가침이다 — `memory/`·`team.config.json`을 동기화·삭제하는 제품 코드 경로는 없다; 배포 아티팩트(`install.sh`·`cli.py`·npx shim)는 릴리스 태그에 핀 — `main`은 자유롭게 움직인다.
- **스킬 계층**(`infra/skills/{base,core,util}/`): `base`는 항상 설치됩니다; `core`는 팀모드가 켜져 있는 동안 자동으로 활성화되고 `off`면 비활성입니다 — 팀이 선택하는 게 아닙니다; 실제로 팀원별 선택(opt-in) 대상인 계층은 `util`입니다.
- Python 3.9+ 문법만 사용하세요(`match`/`case` 등 3.10+ 전용 문법을 쓰기 전에 `pyproject.toml`의 `requires-python`을 확인).
- 참고: 팀 인스턴스에 `memory/team/code-conventions.md`가 있다면, 이건 *그 팀 자신의 제품*(예: 그 팀이 만드는 별도 백엔드)의 컨벤션입니다 — tm-mode 자체 코드와는 무관하니 여기 적용하지 마세요.

## 7. 코드 언어 — 코드 내용은 영어로

**신규·수정 코드의 내용물(주석·docstring·테스트 코드·식별자·assert 메시지)은 영어로 작성해주세요.** 기존 한국어 주석은 해당 코드를 고칠 때 함께 영어로 옮기면 좋습니다(일괄 번역 강제 아님).

- **사용자에게 보이는 런타임 문구**(설치 출력·훅 주입물·CLI 메시지)는 이 정책이 아니라 **i18n(locale) 체계**를 따릅니다 — 하드코딩 언어 전환 금지.
- 문서도 영어 기본: front-door 문서는 영어 기본 + 한국어(문서에 따라 같은 파일의 절이거나 자매 파일)이고, `docs/`(spec 포함)도 영어로 작성·유지합니다. 코드블록 안 한국어(예시 config·에러 문자열)와 인용된 런타임 문자열은 그대로 둡니다.

## 8. 공개 위생 — 실환경 식별자 금지 (필수)

이 레포는 공개 제품입니다. **어떤 파일에도(테스트 fixture·문서 예시·주석 포함) 실제 사람·팀·머신의 식별자를 쓰지 마세요.** 2026-07-07 공개 전 감사에서 실멤버명·홈경로·팀 인스턴스 레포가 fixture 로 다수 유입돼 전수 익명화 + 히스토리 처리를 치렀습니다 — 같은 일을 반복하지 않습니다.

**금지**: 실명·실멤버 핸들, 실 이메일, 하드코딩 홈경로(`/Users/<실계정>/…`), 팀 인스턴스 레포/조직 참조(제품 레포 `T-Gates/tm-mode` 제외), 실제 팀·제품·사업 도메인 명칭.

**허용 어휘(이것만 쓰세요)** — 사람: `alice`·`bob`·`jane-doe`, 편집거리 쌍: `jonathan`↔`jonathon`, 팀/조직: `acme`/`Acme`/`ACME`, 레포: `acme/acme-team`, 이메일: `user@example.com`(개인메일 판정 fixture 는 `me@gmail.com`), 경로: `tmp_path`(pytest)·`~/…`·`/Users/alice/…`.

CI 가드 `tests/test_no_identity_leaks.py` 가 일반 패턴(하드코딩 홈경로·이메일·비제품 org 레포)을 기계적으로 차단합니다. 가드에 걸리면 실값을 허용 어휘로 바꾸는 게 정답이지, 가드에 예외를 추가하는 게 아닙니다(예외는 위 허용 어휘 확장에 한함).

**리뷰 체크**: 도그푸딩에서 가져온 실측 값(경로·명령어·해시·로그)을 커밋하기 전, 식별자를 허용 어휘로 치환했는지 확인하세요. 실측 golden(해시 벡터 등)은 치환 후 동일 알고리즘으로 재유도합니다.

## 9. 커밋 스타일

기존 히스토리를 따릅니다: `<타입>(<스코프>): <설명>`. 스코프는 선택입니다. 쓰이는 타입: `feat` / `fix` / `docs` / `chore`. 설명은 한국어·영어 모두 좋습니다.

```
feat(memory): add `memory route {upsert|remove}` verb
fix(codex): pass TEAMMODE_MEMBER to Codex hooks
chore: switch license to Apache 2.0
```

## 10. Pull Request

1. Fork → 브랜치(`fix/...`, `feat/...`) → `T-Gates/tm-mode`의 `main`으로 PR.
2. `.github/PULL_REQUEST_TEMPLATE.md`를 채웁니다: 무엇/왜, 변경사항, 테스트 근거(`python -m pytest -q` 출력 붙이기), 체크리스트(stdlib-only 유지, 전체 테스트 통과). 웹 UI는 이 템플릿을 자동 적용하지만 `gh pr create --body`는 그렇지 않으니, 그렇게 올릴 경우 같은 구조로 직접 작성하세요.
3. `.github/CODEOWNERS`에 따라 메인테이너가 리뷰·머지합니다. 리뷰 코멘트에는 후속 커밋으로 응답하세요 — 리뷰 도중 히스토리를 force-push로 덮어쓰지 마세요.

## 11. 문서 · i18n

- front-door 문서(`README.md`, `CONTRIBUTING.md`)는 **영어가 기본(canonical)** 입니다. `README.md`는 한국어 번역을 같은 파일 안의 한 절로(상단에 앵커 링크) 담고, `CONTRIBUTING.md`는 같은 구조의 자매 파일 `CONTRIBUTING.ko.md`로 담습니다(각 파일 상단에서 서로 링크) — 이 파일에 한해 README와는 의도적으로 다르게 고른 i18n 레이아웃입니다.
- **동기화 규칙:** `CONTRIBUTING.md`를 바꾸는 PR은 같은 PR에서 `CONTRIBUTING.ko.md`도 갱신해야 합니다(반대 방향도 동일).
- 내부 명세(`docs/spec/`)와 시나리오 문서(`docs/scenarios/`)는 **영어가 기본**입니다 — 이관이 이미 완료됐습니다. 팀 인스턴스의 로컬 사본은 한국어로 뒤처져 있을 수 있는데, 이는 그 인스턴스의 번역 부채이지 업스트림 정책이 아닙니다.

## 라이선스

tm-mode는 Apache License 2.0으로 배포됩니다 — 자세한 내용은 [LICENSE](LICENSE) 참조. 기여를 제출하면 같은 라이선스로 배포되는 데 동의하는 것입니다(표준 inbound=outbound — 이 레포에는 별도 CLA가 없습니다). `NOTICE.md`는 법적 고지 문서라기보다 메인테이너의 최신 공지 로그로, `tm on` 실행 시 인스턴스와 자동으로 대조되어 보여집니다.
