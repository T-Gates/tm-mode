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
- **assertion kind**: exit_code, stdout/stderr_contains, file_exists/contains, session_log_single_file(스펙 01 §3.1 하루1파일·분할금지), session_log_contains, state_off/on(.tgates-active 마커). 미지의 kind는 크래시 대신 fail 처리(`test_unknown_assertion_kind_is_failure_not_crash`).
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
- **teammode.py(엔진 수직 슬라이스)**: on/off만 실배선(배너 렌더+sync+`.tgates-active` 마커). context/issue/log 동사는 미구현(exit 127) → 해당 골든 시나리오 RED 유지(후속 슬라이스 인수 테스트).

### 라운드 1 (구현 → 적대적 검수)
- RED: `tests/test_adapter_claude.py` → `ModuleNotFoundError: adapter` 확인.
- 구현: adapter.py + events.json + manifest.json → 어댑터 9 테스트 + 디스패처 3 테스트 GREEN.
- **검수 지적(실 버그 3건, 전부 수정)**:
  1. `teammode.py` `_adapter()`가 삭제된 `TEAM_ROOT` 전역 참조 → `NameError`로 `on` 크래시(exit 1). 어댑터 team_root는 **설치 위치**(normalize 마커 기준), 메모리 쓰기 팀 루트와 별개 축임을 분리해 수정.
  2. **실환경 오염 사고**: ambient `TGATES_HOME`(실 tgates-toolkit 가리킴)이 엔진에 새어들어 verify가 실 toolkit의 `memory/banner.txt`를 건드림. `SubprocessEngine.run`이 subprocess env의 `TGATES_HOME`을 run root로 고정해 격리(스펙 01 §2.4). 실 toolkit은 `git checkout`으로 원복·git status clean 확인 — 영구 오염 없음.
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
  - **역매핑**: events.json을 역방향으로 읽어 에이전트 이벤트→정규 이벤트, tool_name→action(Write|Edit 분해 후 멤버십), tool_name→mcp(server,tool)(`mcp_tool_format` 템플릿을 정규식화, server는 non-greedy로 첫 `__` 경계 분리 → `google_calendar`·`slack-tgates` 등 언더스코어/하이픈 서버명 안전).
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

---

## 사후 정리 — 실환경 오염 사고 전말 및 복구

은수의 세션로그(tgates-toolkit, 2026-06-12, 본인 작성분 — 무수정)에서 확인된 사고:
- **원인**: 슬라이스 2 초기 `verify` 실행 시(env 격리 패치 *이전*), 빌드 에이전트 셸에 상속된 ambient `TGATES_HOME`(=실 tgates-toolkit)이 conformance 러너로 새어들어, `05-off-persist` 시나리오의 `off` 동작이 **실 toolkit의 `.tgates-active` 플래그를 실삭제**. banner.txt 도 같은 경로로 잠시 건드림(읽기만, 무수정·복구됨).
- **이미 적용된 수정**(슬라이스 2 커밋 a81b6b0): `SubprocessEngine.run`이 subprocess env의 `TGATES_HOME`을 run root로 명시 주입 → ambient 무시. 검증: ambient `TGATES_HOME=실toolkit` set 상태로 verify 돌려도 실 `.tgates-active` 생존·banner는 격리 cwd 생성 확인.
- **복구 조치**: 내 초기 verify가 삭제한 실 toolkit `.tgates-active`(런타임 마커, gitignored)를 `touch`로 재생성 — 사고 전 상태 복원. 은수 세션로그 파일은 본인 작성분이라 손대지 않음.
- **교훈/백로그(teammode 설계)**: conformance 격리를 더 강하게 — `env -i` 류로 ambient 전부 차단 후 명시 변수만 주입하는 방식 검토(현 구현은 `dict(os.environ)` 복사 후 override라 다른 누수 변수 가능성 잔존). 또한 무인 빌드 에이전트 디스패치 자체도 깨끗한 env로 띄우는 것을 권고.

---

## 슬라이스 P1 — 엔진 env 비신뢰 + settings 오염 가드 (2026-06-13)

> 슬라이스 0.4 적대적 검수가 잡은 근본: 변수명 rename(0.1)은 반쪽. 엔진(teammode.py)이 ambient `TEAMMODE_HOME`을 무조건 신뢰 → SubprocessEngine 격리를 **우회한 직접 CLI 호출** 시 동일 호스트 오염 재현 가능.

### 결정/근거
- **P1-a — `--root` 정책 (A) 채택**: `_team_root()` 자체를 삭제하고, 팀 루트는 `--root <경로>` 명시 인자로만 받는다. `--root` 미지정 시 **에러 종료(exit 2)** — (B) cwd 폴백 대비 (A)를 택한 이유: 엔진이 "어느 폴더를 건드릴지 추측"하는 표면을 0으로 만드는 것이 사고의 근본 처방. env 폴백 완전 제거. 어댑터가 이미 `settings_path`를 명시로 받는 철학과 일치.
- **P2 — settings 명시 필수 가드**: `--settings <경로>`(격리 모드) 또는 `--install`(실설치 → `~/.claude/settings.json`) 중 하나가 없으면 거부(exit 2). 이유: P1-a로 `--root`는 막혔지만 `--settings` 기본값이 실 `~/.claude`라 별도 오염 표면이 남아 있었음(실제로 RED 단계에서 `~/.claude/settings.json`에 teammode 훅이 누수됨 → 확인 즉시 제거). `--install`은 실설치 정상 동작 보장(가짜 HOME e2e: 4훅 등록 확인). 과방어로 설치를 깨지 않음.
- **호출처 동기화**:
  - `conformance/check.py` `SubprocessEngine.run` — env로 `TEAMMODE_HOME` 주입하던 것을 제거(env 화이트리스트에 팀루트 변수 없음). 대신 동사 뒤에 `--root <run root>`를 명시 삽입. CLI `main()`은 `--settings`가 없으면 run root 하위 `.teammode-settings.json`을 자동 주입(타 구현은 미지 플래그로 무시, §2 C2).
  - `infra/hooks/session-log-remind.py` — **env 유지**. 런타임 훅은 에이전트 하니스가 발동해 `--root`를 받을 통로가 없으므로 스펙 01 §1.2의 "팀 루트 환경변수(필수)"를 read-only로 참조하는 게 정당. 엔진과의 구분을 docstring에 명시(엔진=의도적 호출=명시 인자, 훅=수동 발동=env).
  - 스펙 01 §1.2(팀 루트 정의)·§2.4(쓰기 위치)에 "엔진/어댑터는 명시 인자로만, env 비신뢰, 미지정 시 에러" 반영. reference 변수명도 `TGATES_HOME`→`TEAMMODE_HOME` 정정.
  - 기존 `test_isolation` 의 `_isolated_env` 기대값 수정(팀루트 변수가 env에 **없음**을 단언으로 전환) — 인터페이스 변경에 따른 정당한 기대값 갱신.

### 라운드 1 (TDD → 구현 → 적대적 자기검수)
- RED: `tests/test_isolation.py`에 P1-b/P2 회귀 5종 추가 → 현 엔진이 env를 읽어 전부 실패 확인. **RED 실행 자체가 `~/.claude/settings.json`을 오염**시킴(conftest 가드가 적발) = P2 버그의 실증. 즉시 수동 제거·복구.
- GREEN: `teammode.py` argparse 손파싱 재작성(verb/--root/--settings/--install) + `_resolve_settings` 가드 + check.py 동기화 → 전 테스트 통과.
- **적대적 자기검수(별도 시각 — "ambient env로 호스트 오염 재현 시도", /tmp 피해자만 사용)**: 4종 공격 — ① ambient `TEAMMODE_HOME=피해자`+`off --root 격리` → 피해자 마커 생존 ② `--root` 없는 `off` → 에러 종료·cwd 무접촉 ③ `--settings`/`--install` 없는 `on` → 거부·마커 미생성 ④ 플래그 순서 뒤섞기(`--settings ... --root ...`) → 정상 바인딩·피해자 무변. **전부 차단**. 실 `tgates-toolkit` 무접촉(피해자는 전부 `mktemp -d`). 실 `~/.claude/settings.json` 누수 0 + conftest 가드 통과.
- 재검수: 구현 결함 0건. "수정할 내역 없음".

### 2.7' verify 재실행 — 격리 경로로 (실행 증거, 적대 ambient env 하에서)
```
ROOT=/tmp/tmp.XXXX  (ambient TEAMMODE_HOME=/tmp/SHOULD_NOT_BE_TOUCHED 무시됨)
[PASS] 01-on-banner / [PASS] 05-off-persist
[FAIL] 02·03·04 (동사 미구현, 후속 슬라이스 RED 유지)
RED: 2/5 통과  — settings는 root 하위 .teammode-settings.json에만, 피해자 경로 미생성
```

### 결과
- 신규 테스트 5개(직접호출 off/on×env무시·--root필수, P2 거부) green. 기존 `_isolated_env` 단언 갱신. **누적 56 passed** (기준선 51 → 56). 슬라이스 P1 검수 통과("수정할 내역 없음").

---

## 슬라이스 U — 상시 레포 최신화 훅 (throttled auto-pull) (2026-06-13)

> 설계(은수 새벽 합의): UserPromptSubmit 마다 팀 레포를 최신화하되 스로틀로 과부하 방지, **실패는 절대 작업 차단 금지(철칙)**. 슬라이스 T(on 시 upstream fetch)와 별개 축 — 이건 팀 레포 자체의 상시 ff-pull.

### 결정/근거
- **순수 함수 분리(`infra/hooks/auto_pull.py`)**: `should_pull`/`do_pull`/`auto_pull`. 시각·경로·스로틀초를 전부 인자 주입(P1 교훈: env 무조건 신뢰 금지) → /tmp fake remote + 시각 주입으로 결정적 테스트.
- **훅 통합 방식 = session-log-remind 인라인 호출(별도 manifest 엔트리 아님)**. 근거: ① 설계가 "pull 이 리마인드보다 **먼저**"를 요구 — 한 프로세스 안에서 호출하면 순서가 결정적(별도 settings.json 엔트리는 등록 순서 의존이라 취약). ② normalize subprocess 1회 절약. ③ 기존 UserPromptSubmit 훅이 이미 `.tgates-active` 게이트·TEAMMODE_HOME 해석을 하므로 재사용. `_maybe_auto_pull` 는 `.tgates-active` 활성 시 리마인드 age 계산 **직전**에 호출되고, 어떤 예외도 삼킨다(철칙).
- **상태 위치**: 팀 루트 밖 `$XDG_STATE_HOME/teammode/last-pull`(없으면 `~/.local/state`). 팀 메모리(memory/) 오염 회피. 런타임 훅이라 env 참조 정당(엔진과 달리 read-only·상태격리 목적).
- **스로틀 = 시도(attempt) 단위 기록**(설계 미세 보강): 원래 "성공 시에만 시각 기록" 안이었으나, 그러면 원격 장애 시 매 프롬프트가 최대 timeout 초 pull 을 재시도해 작업에 세금. `do_pull` 직전에 시각을 박아 throttle 창당 1회만 비용 → "실패 무해" 철칙을 latency 측면에서도 강화.
- **ff-only**: 충돌·divergent 시 merge 안 하고 실패(워킹트리 무오염, 설계 §3). `--no-rebase --no-edit` 로 에디터·rebase 부작용 차단.
- **hang 차단 다층**: `GIT_TERMINAL_PROMPT=0`+`GIT_ASKPASS=true`+SSH `BatchMode/ConnectTimeout`(자격증명 프롬프트) / subprocess 타임아웃 5s / git `http.lowSpeedLimit=1000`·`lowSpeedTime`(저속 자가 중단) / **프로세스 그룹**(아래 검수 버그).

### 라운드 1 (TDD → 구현 → 적대적 자기검수)
- RED: `tests/test_auto_pull.py` → `ModuleNotFoundError: auto_pull`.
- GREEN: auto_pull.py + session-log-remind 통합 → 19 테스트(스로틀4·do_pull4·auto_pull7·훅통합4).
- **적대적 자기검수(새 관점 — "이 훅이 작업을 막을 수 있는 경로"를 집요하게, /tmp 피해자만)** — 실측 버그 2건 수정:
  1. **🔴 손자 프로세스 고아 누수**: `subprocess.run(timeout=)` 은 직접 자식(git)만 SIGKILL → git 이 fork 한 `git-remote-https` 손자가 비라우팅/stall 호스트에 매달려 고아로 생존(pgrep 으로 5개 실측). 작업을 *직접* 막진 않지만 누적·네트워크 점유. **수정**: `Popen(start_new_session=True)` 로 자체 PGID → 타임아웃 시 `os.killpg(SIGKILL)` 로 손자까지 일괄 종료. 재검: 비라우팅·connect-then-stall 원격 둘 다 3s 에 끊기고 고아 0.
  2. **🔴 실 `~/.local/state` 오염**: 신규 `_maybe_auto_pull` 가 기존 `test_normalize.py` 의 hook 호출 테스트(XDG 미격리·non-git tmp 루트)에서 실 `~/.local/state/teammode/last-pull` 에 시도 시각을 기록(attempt-record 가 do_pull 의 non-git early-return 보다 앞이라). 슬라이스 4 conftest 가드가 suffix 없는 경로라 못 잡음. **수정**: ① conftest 에 autouse `XDG_STATE_HOME` 격리 픽스처(모든 subprocess 상속) ② 가드에 `~/.local/state/teammode` 부재→존재 전이 검사 추가(suffix 무관). 누수 파일 제거·가드가 강제 leak 을 실제로 fail 시키는 것까지 실증.
- 추가 적대 점검(전부 무raise 확인): state_path=디렉토리 / nonexistent 경로 / 손상 상태 파일 / 기록불가 상태 디렉토리 / throttle=0 강제 시도. worst-case latency = 죽은 원격이라도 throttle(5분)당 1회 ≤5s.
- 재검수: 구현 결함 0건. "수정할 내역 없음".

### 결과
- 신규 테스트 19개 green + conftest 가드 강화. **누적 75 passed** (기준선 56 → 75). 실 `~/.claude/settings.json`(md5 불변)·실 toolkit·실 `~/.local/state` 무접촉. 슬라이스 U 검수 통과("수정할 내역 없음").

---

## 슬라이스 V — 엔진 핵심 동사 (2026-06-13)

> 원칙: 엔진=기계적 재료손질, 스킬=판단(요약·정리). 동사는 재료만 모으고 요약은 에이전트.
> git 동사는 신규 작성 금지 — auto_pull.py 를 `infra/git_ops.py` 공통 모듈로 키워 재사용(드리프트 방지).

### V.1 `log` — 세션로그 생성/append (골든 04)

**결정/근거**
- **06시컷 단일소스 `infra/workday.py`**: `workday(now)`/`workday_str(now)` 순수 함수. 시각을 인자로 받아(P1 정신) 05:59(전날)/06:00(당일)/자정·월·연 경계를 결정적 검증. session-log-remind 안내문과 엔진 log 가 같은 컷을 써야 하므로 계산을 한 곳에 둠(drift 방지). naive→KST 간주, 타 tz→KST 변환.
- **author 검증 화이트리스트**: members.md 영문 이름은 소문자 단일 세그먼트(스펙 01 §2.1). 슬래시·`\`·`.`·`..`·절대경로·빈문자열·비영숫자(널바이트·탭·공백 포함)를 거부 → 팀 루트 밖 쓰기 차단. 이중 방어로 정규화 후 경로가 sessions_dir 밖이면 추가 거부.
- **하루1파일 append**: 파일 존재 시 frontmatter 재작성 없이 `## HH:MM` 구분선으로 이어 씀(스펙 §3.1 -late 분할 금지). 첫 기록만 frontmatter(author/date/summary). **엔진은 요약 안 함** — summary 는 text 첫 줄(100자)로 초기화하고 이후 갱신은 스킬/사람 몫(엔진은 교체 판단 안 함).
- **--now 주입**: ISO8601 명시 주입으로 06시컷 경계 결정적 테스트. 미지정 시 실시각(KST).
- **`_parse_args` 일반화**: (verb, settings, install) 튜플 → opts dict 로 확장해 동사별 플래그(--author/--text/--now) 수용. `_VALUE_FLAGS` 화이트리스트로 알 수 없는 플래그가 다음 토큰(verb 등)을 값으로 삼키는 사고 방지. log 는 settings 불요(메모리 동사라 ~/.claude 무접촉).

**라운드 1 (TDD → 구현 → 적대적 검수)**
- RED: `tests/test_log.py`(20) + `tests/test_workday.py`(8) → log verb 127 unimplemented.
- GREEN: workday.py + teammode.py cmd_log → 28 신규 테스트. 골든 04-log-accumulate GREEN(verify /tmp).
- **적대적 검수(/tmp 피해자, 구현자 불신, 실행 증거)** — 실측 1버그 수정:
  - 🔴 **선두 dash author footgun**: `--author "-rf"`·`"--root"` 가 화이트리스트(`-` 허용) 통과 → `-rf` 디렉토리/파일 생성. 다운스트림 git/rm/glob 에서 플래그로 오인되는 footgun. **수정**: 선두 `-`/`_` 거부(영숫자 시작 강제). 회귀 테스트 추가.
  - ✅ 무해 확인: 널바이트 author(subprocess argv OS 차단 + 검증기 거부 이중방어), `...`(거부), multiline text(summary 는 첫 줄만 frontmatter, 본문 전체 보존 — frontmatter 누수 0).
- 재검수: "수정할 내역 없음".

**결과**: 신규 28 테스트 green(누적 75→103). 골든 04 GREEN. 실 ~/.claude(mtime 불변)·toolkit·~/.local/state 무접촉(conftest 가드 + 수동 확인).

### V.2 `context` — 전원 세션로그·INDEX·상태 구조화 출력 (골든 02)

**결정/근거**
- **기계적 수집만, 요약 0**: 멤버별 가장 최근 작업일 1파일(스펙 §4.1 기본 단위)을 골라 frontmatter 의 summary/date 를 **그대로** 옮긴다. 요약·정리는 스킬 몫. `_collect_members` 가 sessions/<멤버>/ 를 순회하며 YYYY-MM-DD.md 중 사전식 max(=최신 작업일) 1개 선택.
- **구로그(summary 없음) 처리**: §4.1 "summary 없으면 주입 생략, 전문 폴백 금지". summary 를 빈 문자열로 두고 멤버는 노출하되 본문을 끌어오지 않는다.
- **두 출력 모드**: 텍스트(섹션 라벨 `--- INDEX ---`·`summary:` 상존 → 골든 02 의 "INDEX"·"summary" 토큰을 빈 memory 에서도 만족) + `--json`(스킬 파싱용 구조화: state/index/members[]).
- **frontmatter 파서**: `---`로 시작하는 블록의 `key: value` 라인만 dict 로. `partition(":")` 로 첫 콜론만 분리(summary 값의 콜론 보존). 출력은 알려진 3필드(author/date/summary)만 방출 → 임의 키 누수 없음.
- settings 불요(메모리 조회 동사). `--json` 부울 플래그 추가.

**라운드 1 (TDD → 구현 → 적대적 검수)**
- RED: `tests/test_context.py`(14) → context verb 127.
- GREEN: cmd_context + 헬퍼 → 골든 02-context-injection GREEN(verify /tmp). 신규 14.
- **적대적 검수(/tmp, 구현자 불신, 실행 증거)** — 실측 버그 **0건**. probe 후 안전 확인:
  - 심링크(/etc/passwd)·passwd류 콜론 라인을 담은 세션로그 → 알려진 3필드만 방출, 'SECRETLEAK' 등 임의키 내용 stdout/JSON 누수 0(실측 grep -c=0).
  - sessions/ 직하 파일(디렉토리 아님) 멤버 오인 없음, 빈 멤버 디렉토리/빈 root 무크래시 exit0, --json 항상 valid, summary 콜론값 보존, 보조파일(notes.md) 무시.
  - 회귀 락 3종 추가(누수·stray파일·콜론). 재검수 "수정할 내역 없음".

**결과**: 신규 17 테스트 green(누적 103→120). 골든 02·04 GREEN(03-issue 는 슬라이스 V 범위 밖 — 별도 동사). 실 ~/.claude(mtime 불변)·toolkit·~/.local/state 무접촉.

### V.3 `pull` — git_ops 공통 모듈 + auto_pull 재사용 리팩토링

**결정/근거**
- **신규 git 코드 작성 금지(드리프트 방지)**: 어젯밤 auto_pull.py 의 do_pull 안전장치(손자 git-remote-https killpg·`--ff-only`·subprocess+git 양쪽 타임아웃·자격증명/SSH 프롬프트 차단)를 `infra/git_ops.py` **단일 소스**로 이관. pull 동사·상시 auto-pull 이 같은 do_pull 을 호출 → 같은 버그를 두 곳에서 따로 고치는 사고 봉쇄.
- **auto_pull.py 리팩토링**: git 머신러리 전부 제거하고 `import git_ops` + `do_pull/PullResult/DEFAULT_TIMEOUT` re-export(기존 호출부·19 테스트가 `ap.do_pull`/`ap.PullResult` 를 그대로 씀 → 호환 유지). 스로틀(should_pull)·시각 기록·조립(auto_pull)만 보유. `test_auto_pull_reuses_git_ops` 가 `ap.do_pull is go.do_pull` 동일 객체를 단언해 드리프트를 테스트로 잠금.
- **hooks/ ↔ infra/ import**: auto_pull 은 hooks/ 에 있고 git_ops 는 infra/ 에 있다. auto_pull 이 `os.path.dirname(dirname(__file__))`(=infra)를 sys.path 에 보강 후 import — 훅이 hooks/ 에서 직접 실행되든 infra/ 가 path 인 채 import 되든 양쪽 동작(ADV5 실증).
- **pull 동사 = 비치명 실패**: do_pull 성공 → exit0+요약, 실패(git아님·오프라인·ff불가·타임아웃) → exit1+stderr 안내, **크래시 0**. ff-only 라 워킹트리 무오염.

**라운드 1 (TDD → 구현 → 적대적 검수)**
- RED: `tests/test_git_ops.py`(13) → `ModuleNotFoundError: git_ops`.
- GREEN: git_ops.py 추출 + auto_pull 리팩토링 + pull 동사 → 신규 13. auto_pull 19 회귀 그대로 green.
- **적대적 검수(/tmp, 실행 증거)** — 실측 버그 **0건**(리팩토링이 안전장치 보존 확인):
  - 🟢 손자 고아 누수(역사적 버그): do_pull(timeout=2) → 비라우팅 원격, pgrep git-remote-http before/after 동일(고아 0). killpg 가 git_ops 이관 후에도 동작 → **회귀 락 테스트 추가**.
  - 🟢 ff-impossible(divergence): pull 이 ff-only 로 거부, HEAD 불변, worktree clean, 상대 커밋(b.txt) 안 끌어옴, exit1 비치명.
  - 🟢 env 비신뢰: 적대 `TEAMMODE_HOME=실토ولكit` set + `pull`(--root 없음) → P1 정책A 에러, cwd 무접촉.
  - 🟢 분기 무raise: do_pull(nonexistent)·do_pull(/dev/null) → ok=False, 예외 0.
  - 재검수: "수정할 내역 없음".

**결과**: 신규 13 테스트(누적 120→129). auto_pull 19 유지. 골든 02·04 GREEN(불변). 실 ~/.claude(mtime 불변)·toolkit·~/.local/state 무접촉.

### V.4 `commit` — git add/commit/push 묶음 (git_ops 확장)

**결정/근거**
- **git_ops.do_commit**: auto_pull/do_pull 과 같은 안전장치 재사용(git_env 자격증명·SSH 프롬프트 차단, killpg 타임아웃, 무raise). 흐름: `add -A` → `diff --cached --quiet`(변경 유무) → `commit -m` → 선택 `push`. CommitResult(ok/committed/pushed/detail).
- **push 실패 ≠ 커밋 손실**: push 는 commit 이후 별도 단계. push 실패(원격없음·오프라인·자격증명·타임아웃)는 ok 을 commit 성공 기준으로 유지하고 pushed=False 만 표시 — **로컬 커밋을 절대 되돌리지 않는다**(rev-list count before+1 로 실증). "여러 스킬이 매번 하던 것 흡수"의 안전 기본값.
- **변경 없음 = 비치명**: 빈 커밋을 만들지 않고 committed=False/ok=False 로 우아하게 축소. git 아님도 동일.
- **arg 주입 면역**: 메시지를 `commit -m <msg>` 의 값 위치(list-form argv)로 넘겨 셸/옵션 해석 0. `--amend`·`--author=` 류 메시지가 옵션으로 오인되지 않음(실증).
- commit 동사: `--message` 필수(빈 문자열 거부), `--push` 선택. 실패 비치명(exit1).

**라운드 1 (TDD → 구현 → 적대적 검수)**
- RED: `tests/test_commit.py`(13) → `do_commit` 없음/verb unimplemented.
- GREEN: git_ops.do_commit + commit 동사 → 신규 13. **impl 중 테스트버그 1건**(bare 원격 HEAD=master 인데 push 는 main → `git log` ref 없이 unborn 오류) → log 에 `main` 명시로 수정(구현 버그 아님).
- **적대적 검수(/tmp, 실행 증거)** — 실측 버그 **0건**:
  - 🟢 메시지 '--amend evil'/'normal --author=hacker' → 메시지로만 보존, amend/author 변조 0(list-form 면역).
  - 🟢 push timeout(비라우팅 원격) → 로컬 커밋 survive(count 1→2), exit0 "커밋은 보존".
  - 🟢 빈 메시지 → exit2 거부, 공백 메시지 → git 비치명 abort(커밋 0).
  - 🟢 host 무접촉: ~/.claude/settings.json md5 불변, 실 toolkit .git 무접촉.
  - 회귀 락 4종(arg주입×2·롤백없음·빈메시지) 추가. 재검수 "수정할 내역 없음".

**결과**: 신규 17 테스트(누적 129→146). 골든 02·04 GREEN 유지. 실 ~/.claude·toolkit·~/.local/state 무접촉.

### V.5 / 슬라이스 T — 템플릿 풀 (on 시 upstream fetch + update 동사)

**결정/근거**
- **fetch 만 자동, merge 절대 금지(은수 합의의 핵심 안전)**: `on` 은 banner+sync+marker 후 `_maybe_notify_upstream` 호출 — upstream fetch(조용·실패무시·타임아웃) → count_behind → behind 면 upstream_changes(git log 원본) 알림. **merge 는 전혀 안 한다**. 적용은 명시적 `update` 동사로 분리(자동 적용이 워킹트리를 건드리는 사고 방지).
- **git_ops 빌딩블록**(do_pull 안전장치 재사용): `fetch_upstream`(remote 가드 + fetch --quiet), `count_behind`(rev-list --count HEAD..upstream/main, 모르면 0 보수적), `upstream_changes`(log --oneline 원본, 요약 안 함), `update_from_upstream`(merge --ff-only --no-edit). 전부 무raise·git_env 자격증명차단·killpg 타임아웃.
- **우아한 축소**: upstream remote 미설정(`_has_remote` 가드)·오프라인·git 아님 → fetch ok=False → on 은 조용히 패스(알림 없음, **on 무차단**). `_maybe_notify_upstream` 은 모든 예외를 삼켜 on 핵심 경로를 절대 막지 않는다.
- **update 동사 = ff-only 안전 기본**: divergent(ff 불가)면 merge 강행 없이 비치명 거부(워킹트리 무오염, 사람 판단 유도 메시지). 이미 최신/upstream 없음도 우아 처리. `allow_unrelated`(첫 병합 unrelated histories)는 라이브러리 옵션으로 두되 기본은 ff-only — 자동 충돌해결 강행 금지.
- **자기 레포 pull 분리**: T.1 초안의 "① 자기 레포 pull"은 별도 pull 동사(V.3)로 분리 — on 의 핵심 경로(배너·마커)를 네트워크가 막지 않게.

**라운드 1 (TDD → 구현 → 적대적 검수)**
- RED: `tests/test_update.py`(18) → git_ops 함수 없음/update verb unimplemented.
- GREEN: git_ops 4함수 + cmd_on 확장(_maybe_notify_upstream) + cmd_update → 신규 18. 골든 01-on 여전히 PASS(verify root 에 upstream 없음 → 조용히 패스).
- **적대적 검수(/tmp, 실행 증거)** — 실측 버그 **0건**(핵심 안전 보장 실증):
  - 🟢 on + **divergent** history → rc0, behind 알림 출력, **HEAD 불변**, t.txt v1 유지(v2 자동 merge 0).
  - 🟢 update + divergent → ff-only 거부, rc1 비치명, HEAD 불변, `git status --porcelain` 빈 상태(merge debris/conflict 잔해 0), t.txt 강제 덮어쓰기 0.
  - 🟢 offline upstream(비라우팅) → on 은 타임아웃으로 끊고 rc0(hang 0).
  - 🟢 host 무접촉: ~/.claude/settings.json md5 불변.
  - 회귀 락 2종(on 무자동merge·update divergent 무오염) 추가. 재검수 "수정할 내역 없음".
- **Gstack 교훈 반영**: fetch 만 자동(매 호출 merge 강행 안 함)·실패는 작업 무차단·타임아웃으로 hang 차단. 캐시/throttle 은 fetch 가 on 시 1회뿐이라 과하면 생략(설계 지침대로).

**결과**: 신규 20 테스트(누적 146→166). 골든 01-on·02·04·05 PASS(03-issue 만 슬라이스 V 범위 밖). 실 ~/.claude·toolkit·~/.local/state 무접촉.

---

## 슬라이스 V 종합 (2026-06-13 완료)

- **완료 동사 5종**: log(V.1)·context(V.2)·pull(V.3)·commit(V.4)·update(V.5, 슬라이스 T 편입).
- **검수**: 각 동사 적대적 검수 1라운드씩 → V.1 실측버그 1건(author 선두dash footgun) 수정, V.2~V.5 실측버그 0건(probe 후 핵심 보장 실증 + 회귀 락). 전 동사 "수정할 내역 없음" 도달.
- **테스트**: 기준선 75 green → **166 green**(신규 91: workday 8·log 20·context 17·git_ops 14·commit 17·update 20 — 각 회귀락 포함). auto_pull 19 회귀 유지.
- **골든 시나리오**: 02-context-injection·04-log-accumulate **GREEN**(슬라이스 V 직접 인수). 01-on-banner·05-off-persist PASS 유지. 03-issue-create 만 RED(issue 동사는 슬라이스 V 범위 밖).
- **드리프트 방지**: git 동사(pull/commit/update) 전부 `infra/git_ops.py` 공통 모듈의 do_pull/do_commit/fetch·merge 재사용. auto_pull 도 git_ops 재사용으로 리팩토링(`ap.do_pull is go.do_pull` 단언으로 잠금).
- **호스트 안전**: 전 동사·전 검수 /tmp 격리. 실 ~/.claude(md5 불변)·실 toolkit·실 ~/.local/state 무접촉(conftest 가드 + 수동 실증).
- **푸시 안 함**(사람 몫). 남은 항목: 03-issue 동사(별도 슬라이스), doctor·symlink(나중 슬라이스).

---

## 🟢 L1 — install.py 부트스트랩 (2026-06-14 새벽 착수)

> 스펙 단일소스: `spec/04-install.md` (+ 01,02). 기준선 166 passed. 무인 dev-cycle.
> ⚠️ 본 세션 환경에 Task/Agent 서브에이전트 툴 부재 → 오케스트레이터가 단일 세션 내에서
> 구현→적대적 자기검수(회귀재현·diff전수·호스트오염 재현시도)→반영 루프로 진행. 별도
> 서브에이전트 프로세스는 못 띄우나, 게이트(166+신규 green)·철칙(호스트 무접촉·--root/--settings 명시)은 동일 적용.

### L1-0 — conftest 가드 강화 (선행) [완료]

**결정/근거**
- 셸 프로파일 5종(`~/.bashrc·.zshrc·.profile·.bash_profile·.config/fish/config.fish`)을 `_GUARDED`에 추가. install.py ⑥(env 주입, §9)이 실 호스트 프로파일을 건드리면 이후 슬라이스 테스트가 즉시 잡게.
- install.py 디스패처에 P2 가드 계승: `--settings`(격리)도 `--install`(실설치)도 없으면 거부(exit 2). 어댑터의 `--settings` 기본값이 실 `~/.claude/settings.json`이라 디스패처 단계에서 막지 않으면 실 호스트 오염. `--install`은 디스패처 전용 플래그로 흡수(어댑터엔 미전달).

**구현 중 실측 버그 1건 (자기검수 전 발견·수정)**
- ⚠️ **dotfile suffix 함정**: `Path("~/.bashrc").suffix == ""` (pathlib 은 선두 dot 을 stem 으로 본다). 기존 conftest 가드는 `if p.suffix and b != a` 로만 내용 검사 → `.bashrc·.zshrc·.profile·.bash_profile` 4종은 **가드에 추가해도 절대 안 잡히는** blind spot. `config.fish`(suffix `.fish`)만 잡혔을 것.
- **수정**: `_CONTENT_GUARDED` 집합 신설 — suffix 무관하게 `b != a`(부재→존재 포함) 변화를 오염으로 fail. 셸 프로파일 5종 + `~/.claude/settings.json`·`~/.codex/config.toml` 포함.

**적대적 자기검수 (회귀재현·호스트오염 재현시도)**
- 🟢 **가드 발화 실증**: 격리 스크립트로 conftest `_snapshot`+teardown 비교식을 fake `.bashrc` 에 대해 구동 → `GUARD FIRED (before=absent after=file)` 확인. dotfile suffix 무관 탐지 동작.
- 🟢 autouse 가드가 매 테스트 실제 실행됨 실증(throwaway 가 monkeypatch 된 _GUARDED 로 KeyError → 비교 루프 작동 증명).
- 🟢 install.py 가드 우회 시도: `--settings`/`--install` 둘 다 없음 → exit 2. `--install` 단독 → fake HOME 의 `.claude/settings.json` 에만 씀(실 호스트 무접촉, e2e). `--install`+`--settings` → 격리 경로 우선.
- 🟢 호스트 무접촉 확인: 실 `~/.claude/settings.json`(mtime Jun 13, 이전 작업)·`~/.bashrc`(mtime May 30) 둘 다 본 세션 무변경. 171 테스트 전부 통과(가드 자체가 오염 0 증명).
- **판정: 수정할 내역 없음** (구현 중 발견한 dotfile 버그는 검수 전 수정 완료).

**테스트**: 신규 5 (test_guard 3 + test_install_dispatch 2). 누적 166→**171 green**.

### L1-A — CLI·preflight·detect·role [완료]

**결정/근거**
- **디스패처 흡수 결정**: `grep -rln install tests/ conformance/` → 의존처는 test_install_dispatch.py(디스패처)·test_isolation.py(엔진, install.py 본체 무관)뿐. 엔진은 어댑터를 직접 부르고 install.py 디스패처 형(`--<agent> sync`)을 쓰지 않음. → install.py 를 **부트스트랩 CLI + 디스패처 양쪽 흡수**로 재작성. 첫 인자에 `--<agent>`(agents/<name>/ 존재) 있으면 디스패치, 아니면 부트스트랩. 기존 디스패치 테스트 3개 전부 green 유지.
- **순수 코어 분리**: 판정·계산을 `infra/install_lib.py`(parse_args·preflight·detect_role·config_is_valid·detect_agents·suggest_member_name·repo_name_from_remote)로 분리 — 값 주입(team_root·python_version·home·git값)으로 단위 테스트가 호스트 무접촉.
- **env 불신뢰(§10, P1)**: `_resolve_root` 은 --root 명시 우선, 미지정 시 cwd 가 팀표식(.git/team.config.json/memory) 가질 때만 cwd. ambient TEAMMODE_HOME/TGATES_HOME 절대 안 읽음.
- **role(M3)**: config 존재 + spec_version + team.name 유효성. team.name 이 placeholder(changeme/todo/your-team-name 등)면 도입자. services 무관(빈 슬롯 정상). 깨진 JSON → 안전하게 도입자(크래시 금지).
- **preflight(§4①)**: Python 하한(MIN_PYTHON=3.9, §12-1 미결이라 보수적)·git 바이너리·팀표식 → exit 2. 원격 인증만 부재 → 경고(로컬 L1 진행, m3·I6b).
- L1-A 는 ①preflight ②detect ③role + 계획 출력까지(무변경). ④scaffold~⑦verify 는 L1-B..F.

**적대적 자기검수 (라우팅·env·호스트오염 재현)**
- 🟢 E2E 5종(subprocess 실호출): ①부트스트랩(introducer, member_name=git user.name 정규화) rc0 ②--dry-run "변경 없음" rc0 ③디스패치(--claude --settings 격리 sync) rc0·격리경로에만 씀 ④bare `sync`(에이전트 미지정) rc2 ⑤--root 없고 cwd 표식 없음 rc2.
- 🟢 **env 불신뢰 실증**: `TEAMMODE_HOME=/tmp/VICTIM` set 상태로 `--root` 사용 → VICTIM **생성 안 됨**(ambient 무시).
- 🟢 호스트 무접촉: 실 `~/.claude/settings.json` mtime 불변(Jun 13). plan-only 모드에서 probe 레포에 memory/ 미생성.
- 관찰(비버그): 오케스트레이터는 preflight 의 remote 경고를 잠정 True 로 건너뛰고 detect 후 실제 인증값으로 경고 — preflight remote 경고 분기는 라이브러리 단위테스트로만 커버(callers 용). 의도된 분리.
- **판정: 수정할 내역 없음.**

**테스트**: 신규 22 (install_lib 17 + bootstrap 5). 누적 171→**193 green**.

### L1-B — scaffold [완료]

**결정/근거**
- **세션 경로 = 엔진 단일소스** `memory/team/sessions/<author>/` (teammode.py:191) — `memory/sessions/` 아님(M1). 테스트로 못박음(`test_scaffold_session_dir_matches_engine_path`).
- **memory/ 코어 구조**(스펙 01 §2.1): INDEX.md(폴더 설명 표)·team/members.md·team/decisions/{current.md,archive/}·team/meeting/{summary,raw}/·sessions/<이름>/.
- **도입자 최소 config(§5-1)**: spec_version 0.1·team{name,timezone,locale}·admin_contact·members_file·banner_file·**services:{}**(빈 슬롯, 스펙02 §9.2). 멱등(유효 config 있으면 무수정 → 팀원 경로도 안전).
- **banner 선기록(M4)**: memory/banner.txt 를 team.name 으로 — 엔진은 파일 있으면 그대로 읽어 무수정(env TGATES_TEAM_NAME 우회 불요).
- **이름 검증 = 엔진 _validate_author 재사용(m1)**: traversal·선두dash·슬래시 거부. install_lib 가 teammode import.
- **첫 세션로그 안 씀(M2)**: 세션 디렉토리만 mkdir, 파일 0(실증).

**구현 중 보강 1건 (검수 전, I8 충족)**
- ⚠️ **I8 충돌정책이 이름-only 식별로는 불가능**: M4 "오버라이드 이름이 *다른 사람*으로 등재 → exit 3" 인데 v0.1 members.md 는 이름 문자열뿐 → 동일인/타인 구별 불가. ConflictError 가 죽은 코드가 될 뻔.
- **보강**: members 항목에 git user.email 식별자를 주석(`- name <!-- id: email -->`)으로 부착. 같은 이름+**다른 식별자** → ConflictError(exit3, members.md 무변경). 같은/미상 식별자 → 멱등(M4 "동일인=본인 간주" 유지). 레거시(식별자 없는) 항목과도 호환.

**적대적 자기검수 (멱등·충돌·traversal·호스트오염)**
- 🟢 E2E: introducer 스캐폴드 → 전체 memory/ 트리·유효 config(빈 services)·members.md(식별자 태그)·**빈 세션 디렉토리(M2)** rc0. 재실행 → role 이 member 로 자기일관 전환, boblee 등재 1회 유지(멱등).
- 🟢 I8: 다른 git email(mallory) 이 `--member-name alice` 점유 → exit 3 "충돌", members.md 무변경(실증).
- 🟢 traversal: `--member-name ../escape` → exit 3, `sessions/..` 미생성.
- 🟢 env 불신뢰: `TEAMMODE_HOME=/tmp/VICTIM2` set·`--root` 사용 → VICTIM2 미생성. 실 ~/.claude mtime 불변.
- **판정: 수정할 내역 없음** (I8 보강은 검수 전 완료).

**테스트**: 신규 23 (scaffold/충돌/멱등 19 + 통합 bootstrap 4 — I8 e2e 포함). 누적 193→**213 green**.

### L1-C — wire (훅 sync만) [완료]

**결정/근거**
- **에이전트별 sync 위임(§8)**: wire_agents 가 감지된 에이전트마다 어댑터 sync 호출. run_adapter(agent,flag,path) 콜러블 주입으로 부작용 추상화(테스트가 호스트·subprocess 무접촉).
- **플래그 차이 흡수**: claude→`--settings ~/.claude/settings.json`, codex→`--config ~/.codex/config.toml`. agent_settings_path 가 격리(--settings override 하위 에이전트별 파일)/실호스트 기본을 해석.
- **스킬 심링크 제외(M2)**: L1 은 훅이 맥락 주입. infra/skills 부재 → wire 는 훅 sync 만.
- **부분 실패 정책(M5)**: 에이전트별 독립 — try/except 로 한 에이전트 실패가 다른 배선을 안 막음. 하나라도 실패 시 exit 3 + 어느 에이전트가 막혔는지 stderr. 성공분 롤백 안 함(멱등 재시도).
- **무인 안전 추가 가드**: 실호스트 배선은 `--yes`(실설치) 또는 `--settings`(격리) 명시에서만. 둘 다 없으면 wire 건너뛰고 "스캐폴드 완료"로 rc0(실 ~/.claude 무단 쓰기 방지 — 엔진 P2 정신 계승).

**적대적 자기검수 (M5·격리·플래그·호스트오염)**
- 🟢 M5 단위: codex 어댑터가 예외를 던져도 claude 는 wired 유지, exit 3, codex 는 failed 집계(성공분 무롤백). rc!=0·미지원 에이전트도 동일.
- 🟢 E2E 격리: `--settings /tmp/iso` → claude/codex 둘 다 격리 경로(`iso/claude/settings.json`·`iso/codex/config.toml`)에만 씀. fake-HOME `.claude/settings.json` 미생성(격리 wire 가 실호스트 안 건드림). normalize 경유·manifest 훅 등록 확인.
- 🟢 E2E 실설치: `--yes` → fake-HOME `.claude/settings.json` 에 씀(실호스트 타깃 정상). 실 ~/.claude mtime 불변.
- 🟢 wire-skip: `--settings`/`--yes` 없으면 "건너뜀" rc0.
- 🟢 codex block 훅 축소(confirm-action 미지원→비활성)는 어댑터 자체 advisory(rc0) — wire 실패 아님.
- **판정: 수정할 내역 없음.**

**테스트**: 신규 10 (wire_agents 8 + bootstrap 통합 2). 누적 213→**223 green**.

### L1-D — env 주입 [완료]

**결정/근거**
- **변수명 TEAMMODE_HOME(m2)**: 스펙01 §1.2 reference·런타임 훅 코드와 일치. ⚠️ 의도적 호출(install/on/off)은 env 불신뢰(§10) — env 는 *런타임 훅 전용*. 주석으로 명시.
- **셸별 프로파일·문법**: bash→.bashrc·export, zsh→.zshrc·export, fish→.config/fish/config.fish·set -gx. detect_shell($SHELL).
- **멱등 1줄(§9)**: 라인 끝 마커(`# teammode (env injection, §9)`)로 중복 판정. 동일=무변경, 팀루트 변경=마커 라인만 교체(중복 0), 마커 2개 이상=1개로 정리(방어). 기존 프로파일 내용 보존(끝에 append).
- **미감지/미지원 셸**: 경고만(비치명) + 수동 설정 안내 — L1 핵심은 메모리+훅이라 env 부재가 install 을 안 막음.
- bootstrap shell 파라미터 기본 "__env__" → $SHELL 감지(테스트는 monkeypatch + home=tmp).

**적대적 자기검수 (멱등·셸별·호스트오염·가드)**
- 🟢 단위: bash export/fish set -gx/idempotent(count1)/기존내용보존/팀루트변경 교체(team1→team2, 1줄)/미지원셸 비치명/중복마커 정리.
- 🟢 E2E: SHELL=zsh·fake HOME → fake `.zshrc` 에만 주입. 재실행 "이미 최신(멱등)" count 1.
- 🟢 호스트 무접촉: 실 `~/.bashrc` md5 불변, 실 `~/.zshrc`(기존) 무접촉(fake home 에 씀). conftest _CONTENT_GUARDED 가 실 프로파일 보호(L1-0 에서 발화 실증).
- **판정: 수정할 내역 없음.**

**테스트**: 신규 11 (셸감지·주입 9 + bootstrap 통합 2). 누적 223→**234 green**.

### L1-E — session-start.py 훅 (신규) [완료]

**결정/근거**
- **L1 진짜 payoff(B1)**: manifest 에 SessionStart→session-start.py 등록됐으나 파일 부재였던 갭 해소. install 이 아니라 *이 훅이* 다음 세션에 팀 맥락을 실제 주입(스펙04 §4⑦·스펙02 §3.1).
- **맥락 수집 = 엔진 단일소스 재사용**: teammode._collect_members(멤버별 최근 1파일 summary/date)·_read_index(INDEX). 드리프트 방지 + 요약 안 함(엔진 철학). summary 만 옮기므로 본문 누수 0.
- **활성 게이트**: .tgates-active 있을 때만 주입. 팀 루트 = TEAMMODE_HOME(런타임 훅이라 env 정당, session-log-remind 동일 근거).
- **advisory 안전**: 깨진 stdin·수집 실패·엔진 부재 → 조용히 exit 0(세션 무차단).
- **빈 팀(I1)**: 로그 0이어도 활성이면 유효 구조 안내 주입("아직 세션로그 없음").

**적대적 자기검수 (주입·게이트·누수·normalize)**
- 🟢 활성→summary 주입(hookEventName=SessionStart), 비활성→무동작 빈 stdout, non-SessionStart→무동작.
- 🟢 빈 팀→유효 구조 안내. 깨진 stdin→exit0 무출력(advisory). 다중 멤버 전원 수집.
- 🟢 **본문 누수 0 실증**: summary `공개 요약만`만 주입, 본문 `SECRET_BODY` 미포함(`_collect_members` frontmatter-only).
- 🟢 normalize 경유: Claude 원어 `hook_event_name:SessionStart` → 정규화 → 훅 호출 → additionalContext 전파(안 깨짐). SessionStart 엔트리는 fallback!=runtime 이라 자가필터 미적용(무조건 실행) — 정상.
- 🟢 호스트 무접촉: TEAMMODE_HOME=tmp 주입, 전부 tmp.
- **판정: 수정할 내역 없음.**

**테스트**: 신규 8 (직접 6 + normalize 경유·manifest 실재 2). 누적 234→**242 green**.

### L1-F — verify + 골든 [완료]

**결정/근거**
- **⑦ verify(§4⑦·B1)**: bootstrap 이 env 후 `teammode on`(배너+훅+active 마커) → `teammode context --json`(L1 데이터 읽힘 확인) 호출. _engine_capture/_engine_call 이 subprocess 로 엔진 호출(env 화이트리스트로 ambient TEAMMODE_HOME 누수 차단, P1 이중방어). 격리(--settings 디렉토리)면 그 하위 verify-settings.json, 실설치(--yes)면 --install.
- context --json 파싱 실패·on/context rc!=0 → exit 3(어디서 막혔는지 stderr). 실제 맥락 *주입*은 여기 아니라 다음 세션 SessionStart(L1-E).
- **골든은 실행 가능 인수 테스트**(test_install_golden.py): conformance 시나리오 JSON 러너는 엔진(teammode.py) 전용이라, install 부트스트랩 시나리오(I1·I2·I2b·I3·I4·I4b·I-dry)는 install.py 를 subprocess 로 끝까지 돌리는 e2e 로 박았다.

**골든 시나리오 7종 (전부 GREEN)**
- I1 빈/엔진만 레포 → 도입자 완주: memory/·빈 services config·sessions/<이름>/·배너·verify(context 읽힘)·active 마커. 첫 로그 미생성(M2).
- I2 유효 config → 팀원: config 무수정, 본인 이름 등재, verify 읽힘.
- I2b install 직후 새 세션 → SessionStart 훅이 맥락 실제 주입(install 아님, B1).
- I3 재실행 → 멱등(config·members 무변경, 중복 0).
- I4 ambient TEAMMODE_HOME=victim → victim 무접촉(P1 회귀), 작업은 --root 에만.
- I4b --settings 격리 → fake-home ~/.claude/settings.json 무생성, 격리 경로에만 배선.
- I-dry --dry-run → memory·config·marker·iso·프로파일 전부 무생성, 계획만.

**적대적 자기검수 (전체 e2e·호스트오염)**
- 🟢 풀 부트스트랩 e2e: preflight→detect→role→scaffold→wire(claude+codex)→env→verify(state=on·members=0·active·배너) rc0. ambient TEAMMODE_HOME=victim 무시(victim empty).
- 🟢 호스트 무접촉: 실 ~/.claude/settings.json·~/.bashrc md5 불변(풀 런 후).
- test_bootstrap_no_agents_still_ok 기대값 갱신(verify 가 iso/verify-settings.json 쓰므로 — 에이전트 디렉토리 부재만 단언). 회귀 아님(verify 추가에 따른 정당한 갱신).
- **판정: 수정할 내역 없음.**

**테스트**: 신규 7 (골든 I1·I2·I2b·I3·I4·I4b·I-dry). 누적 242→**249 green**.

---

## 🟢 L1 종합 (2026-06-14 완료)

- **완료 슬라이스 7**: L1-0(가드강화)·L1-A(CLI/preflight/detect/role)·L1-B(scaffold)·L1-C(wire)·L1-D(env)·L1-E(session-start 훅)·L1-F(verify+골든).
- **테스트**: 기준선 166 → **249 green**(신규 83: guard 3·dispatch 2·l1a 22·l1b 23·l1c 10·l1d 11·l1e 8·golden 7 — 일부 통합 중복 포함). 0 회귀.
- **검수**: 각 슬라이스 적대적 자기검수 1라운드(서브에이전트 툴 부재로 단일 세션 내 구현↔검수 분리). 실측 버그 2건 검수 전 수정: ① L1-0 dotfile `.suffix==""` 가드 blind spot(_CONTENT_GUARDED 신설) ② L1-B I8 충돌이 이름-only 로 불가 → git email 식별자 부착. 그 외 슬라이스 0버그.
- **무인 안전 철칙 준수**: 실 ~/.bashrc·~/.zshrc·~/.claude·실 git config 무접촉(전 슬라이스 e2e md5 불변 실증). env 불신뢰(ambient TEAMMODE_HOME/TGATES_HOME 무시, I4 회귀락). 실호스트 배선은 --yes/--settings 명시 게이트. 직렬 진행. 푸시 안 함(사람 몫).
- **L1 도달**: install.py 단독으로 빈 레포→메모리 스캐폴드+훅 배선+env+SessionStart 맥락주입까지(서비스 연결 L2 제외). 골든 7종으로 인수.
- **남은 항목(사람)**: 푸시/PR 은수 판단, 세션로그·계획서 반영.

---

## 🪟 W — Windows 네이티브 지원 (2026-06-15 새벽 착수)

> 기준선 301. 파이=Linux, 실 윈도우 없음 → Windows 분기는 **sys.platform/os.name 모킹 + subprocess(setx/reg) 모킹(runner 주입)** 으로 단위 테스트. 실 setx/reg 절대 미실행. 실 윈도우 검증은 은수 내일(범위 밖).

### W-A env 주입 Windows (setx) — 완료

**설계/근거**
- POSIX env 주입은 셸 프로파일(.bashrc 등) 1줄. **Windows 영구 user env 는 셸 프로파일이 아니라 레지스트리(HKCU\Environment)** 에 산다 → `setx TEAMMODE_HOME "<abs>"`(새 프로세스부터 반영). 제거는 `reg delete HKCU\Environment /v TEAMMODE_HOME /f`.
- `is_windows(platform=None)` — 값 주입(미지정 시 sys.platform). win32/cygwin→True. 테스트가 nt 분기 모킹.
- `inject_env_windows(team_root, *, runner=None)` / `remove_injected_env_windows(*, runner=None)` — **runner 주입**으로 실 setx/reg 실행 대체(모킹). 명령·인자 정확성으로 합격 판정. 둘 다 비치명(rc!=0·바이너리 부재 raise 흡수 → injected False/False).
- 팀루트는 `Path.resolve()` 로 **절대경로** 정규화(레지스트리 값은 절대라야 의미).
- 기존 `inject_env`/`remove_injected_env` 에 `platform`/`runner` kwarg 추가 → is_windows 면 윈도우 변형으로 라우팅, 아니면 기존 셸 프로파일 경로(시그니처 호환·무회귀).
- `install.py bootstrap`: `platform` 파라미터 추가(기본 sys.platform). ⑥env 단계에 윈도우 분기(setx, 셸 무관). **격리(--settings)는 윈도우에서도 우선** — setx 미실행(실 호스트 env 무접촉, §10 I4b 정신). `cmd_uninstall` ③: is_windows() 면 reg delete 라우팅.

**TDD**
- RED: tests/test_install_windows.py 12케이스 작성 → is_windows/platform kwarg 부재로 전부 실패 확인.
- GREEN: install_lib 구현 후 11 → bootstrap 통합 2 추가 = 13 green.

**적대적 자기검수** (새 관점: 모킹이 진짜 윈도우 분기를 타나·Linux 무회귀·실 setx/reg 미실행·호스트 무접촉)
- 🟢 nt 분기 실타격: is_windows("win32")→setx/reg 명령 생성(argv[0]=="setx"/"reg", /v TEAMMODE_HOME /f). recorder 가 호출 기록.
- 🟢 실 setx/reg 미실행: 모든 윈도우 테스트가 runner(recorder/boom) 주입 또는 il._default_runner monkeypatch. 파이는 sys.platform=linux 라 _default_runner 자체가 윈도우 분기에 안 걸림.
- 🟢 posix 무회귀: platform="linux" 명시 시 셸 프로파일 경로(.bashrc 작성), runner.calls==[]. 전체 스위트 301→314.
- 🟢 호스트 무접촉: fake HOME + 윈도우 테스트는 setx 모킹 → 실 셸 프로파일/레지스트리/env 무변경. 격리 모드 setx 차단 단언.
- 🟢 비치명: setx rc!=0·바이너리 부재(FileNotFoundError) → injected False, raise 안 함(L1 핵심은 메모리+훅이라 env 실패가 install 안 막음).
- **판정: 수정할 내역 없음**(실측 버그 0).

**테스트**: 신규 13 (is_windows 2·inject setx 4·posix 무회귀 1·remove reg 3·posix remove 1·bootstrap 통합 2). 누적 301→**314 green**. 0 회귀.

> ⚠️ **실 윈도우 검증 미수행(모킹만)** — 실제 setx/reg 동작·새 프로세스 env 반영은 은수 내일 native 윈도우에서 확인.

### W-B 훅 명령 크로스플랫폼 — 완료

**설계/근거**
- 발견: `normalize.py` 는 이미 child 스크립트를 `sys.executable` 로 실행(line 170). 하드코딩 `python3` 은 **어댑터 build_command 의 바깥 명령**(Claude Code/Codex 가 normalize.py 를 부르는 prefix) 한 군데뿐.
- `default_python()` = **sys.executable(절대경로)** — 권장안. 근거: 윈도우는 'python3' 가 PATH 에 없을 수 있으나 절대경로는 항상 유효; venv/conda 도 정확한 인터프리터로 실행(드리프트 0); normalize 와 prefix 가 같은 인터프리터 = 체인 일관. sys.executable 비면 폴백(os.name=='nt'→python / posix→python3).
- 생성자 `python` 기본 None → 해석. argparse `--python` 기본 None(claude+codex). run_adapter 가 --python 안 넘기므로 설치 시점 sys.executable 이 박힘.
- `_quote_arg`: 공백/따옴표 든 토큰만 인용(이미 인용된 건 그대로). 윈도우 `C:\Program Files\...\python.exe` 안전. 공백 없는 'python3'·tmp 경로는 비인용(기존 동작·테스트 보존).
- codex 어댑터는 build_command/is_owned/생성자를 BaseAdapter 에서 상속 → 자동 반영. argparse 기본만 codex 에서도 None 으로.
- is_owned 무파손: 소유 판정은 normalize.py 경로 substring 기반(python prefix 무관). 기존 `python3 ...` 명령도 여전히 소유 인식 → 다음 sync 가 sys.executable 명령으로 교체(멱등 마이그레이션).

**TDD**: RED 7케이스(default_python·python=None 해석·sys.executable 사용·argparse 기본·윈도우 인용·is_owned 일관·codex 상속) 실패 확인 → 구현 → GREEN.

**적대적 자기검수**
- 🟢 is_owned 일관: python=None/python3/`C:\Program Files\...` 셋 다 build_command 출력을 is_owned True.
- 🟢 윈도우 공백 경로 인용: `"C:\Program Files\Python\python.exe"` 따옴표 보호.
- 🟢 마이그레이션 안전: 기존 python3-prefix 훅도 is_owned True → 재sync 시 sys.executable 로 교체(고아 잔존 0).
- 🟢 Linux 무회귀: 기존 어댑터 테스트(claude 6+codex) 전부 green. 전체 314→323.
- **판정: 수정할 내역 없음**(실측 버그 0).

**테스트**: 신규 9. 누적 314→**323 green**. 0 회귀.

> ⚠️ **실 윈도우 검증 미수행(모킹만)** — 실제 윈도우 셸에서 절대경로 python + 따옴표 명령 실행은 은수 내일 확인.

### W-C POSIX 종속 감사 — 완료

**전수 grep 색출 → 분류**
- 교정 필요(POSIX 가정):
  - `install.py _default_profile()` — `~/.bashrc` 무조건 반환 → **윈도우는 None**(env 가 셸 프로파일이 아니라 레지스트리/setx 에 살아서 대상 파일 없음). platform 주입(기본 sys.platform).
  - `install.py bootstrap` shell 정규화 `"/" in str(shell)` → **`\\` 도 경로 구분자로 인식**(윈도우식 셸 경로 `C:\msys64\...\bash.exe` 정규화).
  - `install.py _default_obsidian_config()` — Linux XDG 고정 → **윈도우 AppData\Roaming·mac Library 위임**(il.obsidian_config_path 재사용, 단일 소스).
- 검증 후 무교정(이미 안전):
  - `os.path.expanduser("~/.claude/...")` (teammode·adapter·codex·install) — 크로스플랫폼(윈도우는 C:\Users\x, 내부 / 는 윈도우 Python 수용).
  - `teammode.py _validate_author` — `"/" in author or "\\" in author` 이미 양쪽 구분자 거부.
  - 런타임 훅(session-log-remind·session-start) — 경로 조립 전부 os.path.join, glob 패턴 윈도우 호환.
  - `python3` 리터럴 — W-B 에서 제거됨(default_python=sys.executable).

**TDD**: RED 2케이스(_default_profile platform kwarg 부재) → 구현 → GREEN. 나머지는 소스 가드·플랫폼 주입 테스트.

**적대적 자기검수**
- 🟢 윈도우 None 프로파일: cmd_uninstall 이 None 받아도 remove_injected_env 가 윈도우면 reg delete 라우팅(Path(None) 도달 전). Linux 는 bashrc 반환·무회귀.
- 🟢 소스 절대경로 0: teammode·adapter·codex·install 에 `/home/`·`/Users/` 하드코딩 없음(가드 테스트).
- 🟢 훅 리터럴 / 결합 0: `f"{root}/..."`·`+ "/"` 안티패턴 없음(가드 테스트).
- 🟢 백슬래시 셸 경로 정규화: bootstrap 에 윈도우 bash 경로 주입 → bash 로 정규화·env 주입(platform=linux 로 정규화만 검증).
- **판정: 수정할 내역 없음**(실측 버그 0).

**테스트**: 신규 11. 누적 323→**332 green**. 0 회귀.

> ⚠️ **실 윈도우 검증 미수행(모킹/플랫폼주입만)** — 실제 윈도우 경로 해석은 은수 내일 확인.

### W-D verify + SPEC — 완료

**라운드트립 e2e (nt 모킹)**
- `tests/test_windows_roundtrip.py` 2케이스: ① 실설치 라운드트립 install(bootstrap, platform=win32)→setx env→on(active·memory)→context(state=on)→uninstall(reg delete)→마커 제거·memory 보존. ② 격리(--settings) 라운드트립 — setx/reg delete **둘 다 미실행**(실 호스트 env 무접촉).
- ⚠️ 설계 결정: **전역 sys.platform 모킹 금지** — 그러면 stdlib subprocess(shutil `_win_path_needs_curdir` 등)가 윈도우 분기로 들어가 Linux 에서 `_winapi` 없어 깨짐(실측 실패). 대신 `platform="win32"` 를 bootstrap/cmd_uninstall 에 **명시 주입**하고 `il._default_runner` 만 레코더로 교체 → 윈도우 분기만 모킹, 엔진 git/subprocess 는 실 Linux 동작.

**cmd_uninstall platform 주입 + 격리 env 스킵(실측 버그 1건 수정)**
- cmd_uninstall 에 `platform` 파라미터 추가(기본 sys.platform). step3 가 `is_windows(platform)`·`_default_profile(platform)`·`remove_injected_env(profile, platform=platform)` 로 일관.
- **실측 버그**: 격리(--settings) uninstall 인데 step3 가 항상 `remove_injected_env` 호출 → POSIX 는 `~/.bashrc`(실 호스트!), Windows 는 reg delete(실 레지스트리!) 를 건드림. install 은 격리 시 env 주입을 스킵하는데 uninstall 은 안 함 = **비대칭 호스트 누수**. → 격리(--settings, --profile 없을 때) 면 실 env 제거 스킵하도록 수정(install·uninstall 대칭). 기존 POSIX 누수도 동시 차단(테스트는 --profile 명시라 잠재였음).

**SPEC/spec 반영**
- `SPEC.md`: §4.8 "환경변수 주입 — 크로스플랫폼"(POSIX 셸 프로파일 / Windows setx·reg, is_windows, 격리 대칭, 모킹검증 주석). §2.x-2 어댑터 훅 커맨드 `<python>`=sys.executable 크로스플랫폼·따옴표 인용. ⑥env 단계 설명. §4.9 I4b(레지스트리 포함·uninstall 대칭)·I-win(nt 라운드트립) 골든 행 추가. A.3 에 "Windows 네이티브 = 구현 완료, 모킹/플랫폼주입 검증, 실 Windows 실측 권장" 명시.
- `spec/04-install.md` §9: 크로스플랫폼 env(POSIX 프로파일 / Windows setx·reg) 정정.
- `spec/02-hook-manifest.md` §6-2: `<python>`=sys.executable 크로스플랫폼 정정.

**적대적 자기검수**
- 🟢 모킹이 진짜 윈도우 분기를 타나: 라운드트립이 setx(install)·reg delete(uninstall) 호출을 레코더로 포착(argv 단언).
- 🟢 Linux 무회귀: 전역 sys.platform 안 건드림 → 엔진 subprocess 정상. 전체 332→334. 기존 uninstall e2e(--profile) green 유지.
- 🟢 실 setx/reg 미실행: runner 레코더 주입.
- 🟢 호스트 무접촉 + 누수 수정: 격리 uninstall 이 실 env(프로파일/레지스트리) 안 건드림(신규 수정·테스트 락).
- **판정: 수정할 내역 없음**(라운드트립 작성 중 발견한 격리 누수 1건은 W-D 범위 내 즉시 수정·재검 통과).

**테스트**: 신규 2(라운드트립). 누적 332→**334 green**. 0 회귀.

> ⚠️ **실 윈도우 검증 미수행(모킹/플랫폼주입만)** — 실 setx 레지스트리 영속·새 세션 env 반영·경로 해석은 은수 내일 native 윈도우에서 확인.
