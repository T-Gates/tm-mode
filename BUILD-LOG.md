# teammode 자율 빌드 로그

> dev-cycle (구현 → 적대적 검수 → 반영 루프). 그린필드 기준선 = 0 tests.
> 각 슬라이스: TDD(분석→RED→구현→GREEN) 구현 서브에이전트 → 별도 적대적 검수 서브에이전트 → "수정할 내역 없음"까지 루프.

## 환경 결정

- **언어/테스트**: Python 3.13 + pytest 9. `pyproject.toml`에 `[tool.pytest.ini_options]` (testpaths=tests). 근거: 스펙 §11.5 "Python-first", 훅이 이미 Python 의존이라 추가 의존성 0.
- **pytest 격리**: 시스템에 pytest 부재 → 레포 루트 `.venv/`에 설치(.gitignore 등재됨). 테스트 실행은 `.venv/bin/python -m pytest`.
- **실환경 오염 금지**: 모든 어댑터 테스트는 tmp_path 픽스처로 settings.json/config.toml을 가짜 경로에 둔다. `~/.claude/settings.json` 등 실파일 무접촉.

---

## 슬라이스 1 — 검수 도구 우선 (골든 시나리오 + 러너)

### 결정/근거
- **시나리오 = 선언적 JSON** (`conformance/scenarios/*.json`). 각 시나리오는 `steps[]`, 각 step은 `action`(command/fs_write/noop) + `expect[]`(assertion 배열, AND). 근거: §11.12 "시나리오 = 실행 가능한 스펙" — 데이터로 두면 verify·conform이 한 정의를 공유.
- **엔진 하니스 인터페이스**: `engine.run(argv) -> Result(exit_code, stdout, stderr)` + root 아래 파일 부작용. 스펙 03 §2 "C2 주의"(독립 구현에 파일배치·언어 비강제)와 정합 — `SubprocessEngine`이 임의 `--engine` prefix를 받아 어떤 언어 구현도 검사 가능.
- **noop step의 stdout/exit_code 단언은 직전 command 결과를 상속**. 02·05 시나리오가 "동작 1회 → 결과를 여러 각도로 단언" 패턴이라 필요. (`test_noop_step_inherits_last_command_output`로 고정)
- **Tier 산출(§11.11)**: 결정적 시나리오 전부 통과해야 compliant. advisory 순응률(advisory 시나리오 통과 비율)로 Tier 1(100%)/2(부분)/3(0%) 등급. 04-log-accumulate만 `advisory`(세션로그=§11.11 advisory enforcement 예시), 나머지 4개 deterministic.
- **assertion kind**: exit_code, stdout/stderr_contains, file_exists/contains, session_log_single_file(스펙 01 §3.1 하루1파일·분할금지), session_log_contains, state_off/on(.acme-active 마커). 미지의 kind는 크래시 대신 fail 처리(`test_unknown_assertion_kind_is_failure_not_crash`).
- **lint(정적)**: v0.1은 manifest 정규형(mcp__/Write|Edit/apply_patch 직표기 금지, K4) 1항목으로 시작. 슬라이스 2에서 manifest 생기면 실효 검사. 빈 레포에선 "manifest 없음 — 건너뜀"으로 PASS(크래시 금지).

### 라운드 1 (구현 → 적대적 검수)
- RED: `tests/test_check.py` 작성 → `ModuleNotFoundError: check` 확인.
- 구현: `conformance/check.py` (파싱/실행/Tier/3모드 디스패치) → 17 테스트 GREEN.
- **검수 지적 1건(테스트 결함)**: `test_file_exists_and_file_contains`가 같은 tmp_path를 양성/음성 케이스에 재사용 → 첫 케이스의 side_effect(배너 파일)가 음성 케이스에 잔존해 오탐. **구현 버그 아님**(파일 검사 로직은 정확). 음성 케이스를 `tmp_path/clean` 별도 루트로 격리 수정.
- 재검수: 구현 로직 결함 0건. "수정할 내역 없음".

### 1.4 빈 엔진 verify = 전부 RED (인수 테스트 박힘) — 실행 증거
no-op 엔진(`sys.exit(127)`)에 `verify` 실행 결과:
```
[FAIL] 01-on-banner / 02-context-injection / 03-issue-create / 05-off-persist (deterministic)
[FAIL] 04-log-accumulate (advisory)
RED: 0/5 통과   (exit 1)
```
`conform` 모드 → "비호환: 결정적 시나리오 실패" (Tier 미산정, 의도대로).
`lint`(빈 레포) → manifest 없음 건너뜀 PASS.

### 결과
- 신규 테스트 17개 전부 green (기준선 0 → 17). 슬라이스 1 검수 통과.

---

## 슬라이스 2 — Claude 어댑터 수직 슬라이스

### 결정/근거
- **manifest.json 정규형 샘플**: SessionStart·UserPromptSubmit(mode:on, advisory) / PostToolUse+`{action:file_edit}`(runtime fallback, block) / PreToolUse+`{mcp:{linear,create_issue}}`(runtime, block, strict). enforcement(§11.11)·fallback·strict 필드 포함. mcp__·Write|Edit 같은 에이전트 표기 0 (lint·grep으로 K4 확인).
- **events.json**: 4 정규 이벤트 전부 매핑(claude는 전부 동일명 지원) + `actions.file_edit="Write|Edit"` + `mcp_tool_format="mcp__{server}__{tool}"`. config_file=`~/.claude/settings.json`.
- **adapter.py sync**: 파싱→번역(translate_event/translate_match)→settings.json upsert. 커맨드는 `<python> "<install>/agents/claude/normalize.py" <script> [args]`로 **반드시 normalize 경유**(§5.1-2, 공통 스크립트 직접 등록 금지). 멱등(직렬화 결과 동일 시 무기록)·제거(소유 훅이지만 wanted에 없으면 삭제)·on/off(mode:on 훅 토글) 구현.
- **소유권 마커(§5.1-5)**: 단순 `agents/` 부분문자열 금지. 꼬리 `agents/<name>/normalize.py` 일치로 판정 → 사용자의 `my-agents/cool.py`를 오인 삭제하지 않음(적대 테스트로 확인).
- **mcp 별칭(§5.2-2)**: v0.1 기본 규칙 = 정규 서버명 == 등록 별칭(`resolve_server_alias` 항등). install-mcp 자체는 슬라이스 4+ 범위.
- **install.py 디스패처(§2 불변식 3)**: 분기 로직 0. `--<agent>`를 `agents/<name>/` 디렉토리 존재로 판정 → adapter CLI에 위임만. codex 디렉토리 없으면 `--codex`는 위임 불가(하드코딩 분기 아님).
- **teammode.py(엔진 수직 슬라이스)**: on/off만 실배선(배너 렌더+sync+`.acme-active` 마커). context/issue/log 동사는 미구현(exit 127) → 해당 골든 시나리오 RED 유지(후속 슬라이스 인수 테스트).

### 라운드 1 (구현 → 적대적 검수)
- RED: `tests/test_adapter_claude.py` → `ModuleNotFoundError: adapter` 확인.
- 구현: adapter.py + events.json + manifest.json → 어댑터 9 테스트 + 디스패처 3 테스트 GREEN.
- **검수 지적(실 버그 3건, 전부 수정)**:
  1. `teammode.py` `_adapter()`가 삭제된 `TEAM_ROOT` 전역 참조 → `NameError`로 `on` 크래시(exit 1). 어댑터 team_root는 **설치 위치**(normalize 마커 기준), 메모리 쓰기 팀 루트와 별개 축임을 분리해 수정.
  2. **실환경 오염 사고**: ambient `LEGACY_TOOL_HOME`(실 acme-toolkit 가리킴)이 엔진에 새어들어 verify가 실 toolkit의 `memory/banner.txt`를 건드림. `SubprocessEngine.run`이 subprocess env의 `LEGACY_TOOL_HOME`을 run root로 고정해 격리(스펙 01 §2.4). 실 toolkit은 `git checkout`으로 원복·git status clean 확인 — 영구 오염 없음.
  3. 배너/마커 경로가 설치 위치 고정 → cwd(=검사 대상 팀 루트) 기준으로 변경(`_team_root()` 호출시점 해석).
- **검수 추가 확인(증거 기반)**: K4 정규형 grep 위반 0 / events.json 4이벤트·file_edit 완전 / 소유 마커 false-positive 없음 / fallback 미지원 이벤트 = `[warn]`+drop(무음 스킵 부재, §7) / 멱등 e2e.
- 재검수: 구현 결함 0건. "수정할 내역 없음".

### 2.7 verify 재실행 — on/off 시나리오 GREEN 전환 (실행 증거)
실 엔진(teammode.py)으로 verify (env 격리):
```
[PASS] 01-on-banner (deterministic)
[FAIL] 02-context-injection / 03-issue-create (deterministic — 동사 미구현)
[FAIL] 04-log-accumulate (advisory — 동사 미구현)
[PASS] 05-off-persist (deterministic)
RED: 2/5 통과
```
슬라이스 1에서 0/5 → 슬라이스 2에서 2/5(on·off) GREEN 전환 확인. 나머지 3개는 후속 슬라이스 인수 테스트로 RED 유지.

### 결과
- 신규 테스트 12개(어댑터 9 + 디스패처 3) green. 누적 29 테스트 green. 슬라이스 2 검수 통과.

### 슬라이스 2 범위 밖(후속으로 이월) — 결정 기록
- manifest 중복 (event, script) 금지 lint(§6.2-2 전제) — 현 manifest엔 중복 없음. lint 항목화는 슬라이스 3+(normalize 자가필터와 함께).
- `enforcement` 필드의 실제 분기(block→Stop 훅 게이트) — normalize/Stop 훅 영역(§11.11), 슬라이스 3+.
- `install-mcp`/`install-skills` CLI — 슬라이스 4+ (서비스 슬롯·스킬 오버라이드).

---

## 슬라이스 3 — normalize 런타임 + 공통 훅 1종 (stretch)

### 결정/근거
- **normalize.py(§6 런타임 계약)**: Claude 원어 JSON(stdin) → 정규 스키마(§6.1) 변환 → 공통 스크립트 stdin 전달 → exit/stdout/stderr 전파.
  - **역매핑**: events.json을 역방향으로 읽어 에이전트 이벤트→정규 이벤트, tool_name→action(Write|Edit 분해 후 멤버십), tool_name→mcp(server,tool)(`mcp_tool_format` 템플릿을 정규식화, server는 non-greedy로 첫 `__` 경계 분리 → `google_calendar`·`slack-acme` 등 언더스코어/하이픈 서버명 안전).
  - **자가 필터(§6.2-2)**: `fallback=="runtime"` 엔트리에 한해 (script, 정규이벤트) 조회 → 현재 발동 내용(action 또는 mcp server·tool) 불일치 시 exit 0 무동작. 매처 등록된 훅은 에이전트가 이미 게이트했으므로 필터 생략(올바른 분기).
  - **시맨틱 전파(§6.2-3)**: subprocess의 exit code·stdout·stderr 그대로 — PreToolUse exit 2 차단 + JSON 결정(stdout) 둘 다 보존.
  - **실패 정책(§6.2-4)**: 변환 실패 시 비-strict는 exit 0 + stderr 경고(세션 안 막음), strict는 실패 전파. 파싱 실패로 event를 모르므로 "그 script의 어떤 엔트리든 strict면 strict"로 fail-closed(안전 훅 보수적 처리).
- **공통 훅 이식(3.2)**: `session-log-remind.py`를 정규 스키마 전용으로 재작성. 기존 env-var 의존(CLAUDE_TOOL_INPUT_FILE 등) 제거 → **stdin 정규 JSON만 인지**(에이전트 무지, §6). 출력은 시맨틱 안내문(mcp__·툴명 직표기 0, §8.2). age≥30분 또는 5프롬프트 주기 리마인드(스펙 01 §3.4). auto-commit 대신 이걸 고른 이유: advisory(§11.11 예시)이자 git 부작용 없어 stdin/stdout 계약 검증이 깔끔.

### 라운드 1 (구현 → 적대적 검수)
- RED: `tests/test_normalize.py` → normalize.py 부재로 fixture 복사 실패.
- 구현: normalize.py + session-log-remind.py(이식) → 12 테스트 GREEN(변환3·자가필터3·전파1·실패정책2·공통훅3).
- **검수 지적 1건(테스트 결함)**: fixture가 normalize.py만 복사하고 events.json 누락 → normalize가 events.json 못 찾음. fixture에 events.json 복사 추가(구현 버그 아님).
- **검수 추가 확인(증거)**: mcp 파싱 언더스코어/하이픈 서버명 정확 / builtin tool None / 자가필터 일치=실행·불일치=무동작·mcp서버불일치=무동작 / block exit2 전파 / strict 분기.
- 재검수: 구현 결함 0건. "수정할 내역 없음".

### 결과
- 신규 테스트 12개 green. 누적 41 green. 슬라이스 3 검수 통과.

---

## 슬라이스 4 — Codex 어댑터 + 폴백 (stretch)

### 결정/근거
- **codex/events.json**: `PreToolUse: null`(미지원, §4 키 누락 금지 — 명시적 null) / `actions.file_edit="apply_patch"` / `mcp_tool_format="{server}.{tool}"` / config_file=`~/.codex/config.toml`.
- **codex/adapter.py**: 번역 코어(events.json 기반, 에이전트 무관)는 **Claude Adapter 상속**으로 재사용 — 드리프트 방지. Codex 고유의 TOML 블록 출력 + 폴백·enforcement 축소만 재정의. `if agent=='codex'` 하드코딩 분기 0(§4 규칙 3): PreToolUse skip·apply_patch는 전부 events.json 데이터.
  - **폴백(§7)**: PreToolUse null → drop + `[warn]`(무음 스킵 금지). enforcement=block이 미지원 이벤트에 걸리면 `[warn]`에 "(block 강제 상실)" 명시 — §11.11 advisory 축소의 정직한 표면화.
  - **멱등**: TOML 블록을 `# teammode-hooks-start/end` 마커로 교체. 빈 파일에서 선행 개행 추가하던 버그 수정(블록 앞 사용자 콘텐츠 유무로 prefix 결정).
  - **사용자 config 보존**: 마커 블록만 관리, 나머지 TOML 무접촉.
- **codex/normalize.py**: Claude normalize 함수 재사용 + `importlib`로 모듈 로드해 함수의 `__globals__`에 Codex 경로 상수 재바인딩(events.json·hooks 위치). `apply_patch`→`file_edit` 역매핑 확인. ⚠️ Codex 실 훅 입력 스키마 미확인(스펙 부록 B 이월) — Claude 유사 형태 가정, BUILD-LOG·코드 주석에 명시.

### 라운드 1 (구현 → 적대적 검수)
- RED: `tests/test_adapter_codex.py` → adapter.py 부재.
- 구현: codex events.json + adapter.py(상속) + normalize.py → 7 테스트.
- **검수 지적(실 버그 2건 + 실환경 오염 1건, 전부 수정)**:
  1. **멱등 깨짐**: 빈 config에서 첫 sync는 선행 개행 없이, 둘째 sync(블록 매치 경로)는 선행 `\n` 추가 → 재실행 시 파일 달라짐. 블록 앞 콘텐츠 유무로 prefix 결정하도록 수정.
  2. `codex/normalize.py`가 `runpy.run_path` 반환 dict 변경으로 경로 치환 시도했으나, 함수 `__globals__`는 별개라 무효 → Claude의 events.json을 읽어 FileNotFound. `importlib`로 모듈 객체 로드 후 `_base.EVENTS=...` 속성 재바인딩으로 수정.
  3. **🔴 실환경 오염**: 검수 중 `~/.codex/config.toml`(+`.codex` 디렉토리)에 teammode 블록 누수 발견(중간 상태에서 발생, 현 테스트로는 재현 안 됨). **정리**: 백업 후 어댑터 uninstall로 블록 제거 → 파일/디렉토리 모두 teammode가 생성한 것(누수 전 `.codex` 부재)이라 디렉토리째 원상복구. **재발 방지**: `tests/conftest.py`에 autouse 가드 추가 — 매 테스트 전후 실 설정 경로(~/.claude/settings.json, ~/.codex/config.toml) 스냅샷 비교, 변화 시 즉시 fail. 가드가 의도적 누수를 잡는 것까지 확인.
- **검수 추가 확인(증거)**: 실 manifest 4엔트리 → Codex 3엔트리 축소(PreToolUse drop+warn) e2e / apply_patch 매처 / normalize 경유 / 멱등 md5 동일 / uninstall 클린 / 가드 작동.
- 재검수: 구현 결함 0건. "수정할 내역 없음".

### 4.3 크로스에이전트 (실행 증거)
같은 manifest가 에이전트별로 다르게 표현됨:
- Claude: 4엔트리 전부 등록(PreToolUse 포함, Write|Edit 매처)
- Codex: 3엔트리(PreToolUse/confirm-action drop+`[warn]`, apply_patch 매처)
공통 스크립트는 양쪽에서 동일 정규 입력을 받음 — normalize가 `Write`(claude)/`apply_patch`(codex)를 똑같이 `file_edit` action으로 변환. 크로스에이전트 호환 실증.

### 결과
- 신규 테스트 7개 green + conftest 가드. 누적 48 green. 슬라이스 4 검수 통과.
